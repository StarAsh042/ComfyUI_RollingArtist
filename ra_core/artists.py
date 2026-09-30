"""艺术家 CSV 的解析、加载缓存与抽样选择。

- 表头/列自动识别，兼容 danbooru 导出的多列 CSV（artist,trigger,count,url …）
- 按 “文件 + 列布局 + 修改时间 + 大小” 做进程级缓存，避免每次执行重复解析
- 抽样基于索引进行，避免为每次执行构建大列表
"""

import csv
import math
import os
import random
import threading
from collections import OrderedDict
from itertools import chain
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .constants import CSV_READ_ENCODING, DEFAULT_ARTIST_CSV, LOGGER

__all__ = [
    "ArtistRepository",
    "get_repository",
    "get_artist_repository",
    "clear_repositories",
    "parse_artist_rows",
    "normalize_column_spec",
    "resolve_layout",
    "compute_top_count",
    "select_artists",
    "split_names",
]

# 被识别为表头的单元格（小写、空格/短横线归一为下划线后比较）
HEADER_TOKENS = frozenset({
    "artist", "artists", "character", "characters", "name", "names",
    "tag", "tags", "token", "tokens", "trigger", "triggers",
    "count", "post_count", "solo_count", "url", "id", "copyright",
    "core_tags", "category", "source", "notes", "description", "sample",
})

# auto 模式下按优先级挑选的列名。
# 顺序说明：
# - ``character`` 系列最优先：danbooru_character.csv 的 trigger 是「角色 + 作品」多标签串
#   （如 "hatsune miku, vocaloid"），当成单个艺术家名会破坏提示词，因此必须让 character 胜出
# - ``trigger`` 排在 ``artist`` 之前：trigger 列是「提示词可直接使用」的写法
#   （空格与括号已转义，见 modify_danbooru_art.py），而 artist 列是 danbooru 原始标签，
#   可能带未转义括号（会破坏权重语法）
COLUMN_PRIORITY = (
    "character", "characters", "trigger",
    "artist", "artists", "name", "names", "tag", "tags",
)

COLUMN_ALL = "all"
COLUMN_AUTO = "auto"


def normalize_cell(value: Optional[str]) -> str:
    """归一化单元格文本：去空白/BOM、转小写、空格与短横线转下划线。"""
    text = str(value or "").strip().lstrip("\ufeff")
    return text.lower().replace("-", "_").replace(" ", "_")


def is_header_row(row: Sequence[str]) -> bool:
    """判断某行是否为表头：所有非空单元格都是已知的表头关键字。"""
    cells = [normalize_cell(cell) for cell in row if str(cell or "").strip()]
    if not cells:
        return False
    return all(cell in HEADER_TOKENS for cell in cells)


def normalize_column_spec(column: Optional[str]) -> str:
    """归一化 csv_column 参数（auto / all / 列序号 / 列名）。"""
    spec = str(column or COLUMN_AUTO).strip().lower()
    return spec.replace("-", "_").replace(" ", "_") or COLUMN_AUTO


def resolve_layout(sample_row: Sequence[str], spec: str, has_header: bool):
    """根据首行与 csv_column 配置决定取哪些列。

    返回 ``"all"``（展平所有列）或列索引 int。
    """
    cells = [normalize_cell(cell) for cell in sample_row]
    if spec == COLUMN_ALL:
        return COLUMN_ALL
    if spec in ("", COLUMN_AUTO):
        # 无论表头是否被完整识别，都先按优先级查找列名：
        # 这样 "title,artist,count" 这类含有未知表头列的 CSV 也能取到正确的列。
        # 旧实现只在整行都被识别为表头时才查找，否则回退首列，
        # 会把 title / 计数列当成艺术家名（整列错位）。
        for name in COLUMN_PRIORITY:
            if name in cells:
                return cells.index(name)
        return 0
    if spec.isdigit():
        index = int(spec)
        if 0 <= index < len(sample_row):
            return index
        LOGGER.warning(
            "[RollingArtist] csv_column=%s 超出 CSV 列数(%d)，回退到首列",
            spec, len(sample_row),
        )
        return 0
    if has_header and spec in cells:
        return cells.index(spec)
    LOGGER.warning("[RollingArtist] csv_column=%s 未匹配到表头列，回退到首列", spec)
    return 0


def parse_artist_rows(rows: Iterable[Sequence[str]],
                      column: Optional[str] = COLUMN_AUTO) -> List[str]:
    """从 CSV 行迭代器中解析艺术家名列表。

    参数:
        rows: 行迭代器（``csv.reader`` 即可，无需预先读入内存）
        column: ``auto``（默认，自动识别表头/列）、``all``（展平所有列）、
                列索引（``"0"``）或表头列名（``"artist"``）
    """
    spec = normalize_column_spec(column)
    iterator = iter(rows)

    first: Optional[Sequence[str]] = None
    for row in iterator:
        if row and any(str(cell or "").strip() for cell in row):
            first = row
            break
    if first is None:
        return []

    has_header = is_header_row(first)
    layout = resolve_layout(first, spec, has_header)
    if not has_header and isinstance(layout, int) and layout > 0:
        # auto 模式在“非首列”命中列名，说明首行就是表头（即使含有未识别的列名），
        # 否则会把表头里的 artist 之类的字面量当成一个艺术家名
        has_header = True

    artists: List[str] = []
    # 首行不是表头时它本身就是数据行，需要一并处理
    body = iterator if has_header else chain((first,), iterator)
    for row in body:
        if not row:
            continue
        if layout == COLUMN_ALL:
            cells: Sequence[str] = row
        elif 0 <= int(layout) < len(row):
            cells = (row[int(layout)],)
        else:
            continue
        for cell in cells:
            name = str(cell or "").strip().lstrip("\ufeff")
            if name:
                artists.append(name)
    return artists


class ArtistRepository:
    """艺术家列表的加载与缓存（线程安全）。

    同一个 CSV 在内容未变化时只会被解析一次，多个节点实例共享同一份缓存。
    """

    _MAX_CACHE = 4

    def __init__(self, default_path: str = DEFAULT_ARTIST_CSV) -> None:
        self.default_path = default_path
        self._lock = threading.RLock()
        self._cache: "OrderedDict[Tuple[str, str, int, int], List[str]]" = OrderedDict()
        self._rows_cache: "OrderedDict[Tuple[str, int, int], List[List[str]]]" = OrderedDict()

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------
    def resolve_path(self, custom_path: Optional[str]) -> str:
        """空路径回退到默认 CSV；否则使用用户指定的路径。"""
        path = str(custom_path or "").strip().strip('"')
        return path or self.default_path

    def stat_key(self, path: str) -> Optional[Tuple[str, int, int]]:
        """返回 (规范化绝对路径, 修改时间, 大小)；文件不可用时返回 None。"""
        try:
            info = os.stat(path)
        except OSError:
            return None
        return (os.path.normcase(os.path.abspath(path)), info.st_mtime_ns, info.st_size)

    def load(self, custom_path: Optional[str] = None,
             column: Optional[str] = COLUMN_AUTO) -> Tuple[List[str], Optional[Tuple[str, int, int]]]:
        """加载艺术家列表，返回 ``(artists, stat_key)``。

        失败时返回空列表并记录日志，不抛出异常，由调用方决定如何报错。
        """
        path = self.resolve_path(custom_path)
        spec = normalize_column_spec(column)
        stat_key = self.stat_key(path)
        cache_key = (stat_key[0], spec, stat_key[1], stat_key[2]) if stat_key else None

        if cache_key is not None:
            with self._lock:
                cached = self._cache.get(cache_key)
                if cached is not None:
                    self._cache.move_to_end(cache_key)
                    return cached, stat_key

        artists = self._read(path, spec)

        if cache_key is not None and artists:
            with self._lock:
                self._cache[cache_key] = artists
                self._cache.move_to_end(cache_key)
                while len(self._cache) > self._MAX_CACHE:
                    self._cache.popitem(last=False)
        return artists, stat_key

    def _read(self, path: str, spec: str) -> List[str]:
        try:
            with open(path, "r", encoding=CSV_READ_ENCODING, newline="") as handle:
                artists = parse_artist_rows(csv.reader(handle), spec)
        except FileNotFoundError:
            LOGGER.error("[RollingArtist] CSV 文件不存在: %s", path)
            return []
        except IsADirectoryError:
            LOGGER.error("[RollingArtist] CSV 路径是目录: %s", path)
            return []
        except UnicodeDecodeError as error:
            LOGGER.error("[RollingArtist] CSV 编码无法解析（请使用 UTF-8）: %s -> %s", path, error)
            return []
        except (OSError, csv.Error) as error:
            LOGGER.error("[RollingArtist] CSV 读取失败: %s -> %s", path, error)
            return []
        if not artists:
            LOGGER.warning("[RollingArtist] CSV 中未解析到任何艺术家: %s", path)
        return artists

    def load_rows(self, custom_path: Optional[str] = None
                  ) -> Tuple[List[List[str]], Optional[Tuple[str, int, int]]]:
        """读取并缓存整份 CSV 行，返回 ``(rows, stat_key)``。

        与 :meth:`load` 共用同一套「文件签名」缓存策略，供需要多列的节点使用
        （例如 RollingCharacter 要同时用 trigger / copyright / core_tags / count）。
        行内保持原始顺序，表头行也在其中，由调用方自行判断。
        """
        path = self.resolve_path(custom_path)
        stat_key = self.stat_key(path)
        if stat_key is not None:
            with self._lock:
                cached = self._rows_cache.get(stat_key)
                if cached is not None:
                    self._rows_cache.move_to_end(stat_key)
                    return cached, stat_key

        rows = self._read_rows(path)

        if stat_key is not None and rows:
            with self._lock:
                self._rows_cache[stat_key] = rows
                self._rows_cache.move_to_end(stat_key)
                while len(self._rows_cache) > self._MAX_CACHE:
                    self._rows_cache.popitem(last=False)
        return rows, stat_key

    def _read_rows(self, path: str) -> List[List[str]]:
        try:
            with open(path, "r", encoding=CSV_READ_ENCODING, newline="") as handle:
                return [row for row in csv.reader(handle) if row]
        except FileNotFoundError:
            LOGGER.error("[RollingArtist] CSV 文件不存在: %s", path)
            return []
        except IsADirectoryError:
            LOGGER.error("[RollingArtist] CSV 路径是目录: %s", path)
            return []
        except UnicodeDecodeError as error:
            LOGGER.error("[RollingArtist] CSV 编码无法解析（请使用 UTF-8）: %s -> %s", path, error)
            return []
        except (OSError, csv.Error) as error:
            LOGGER.error("[RollingArtist] CSV 读取失败: %s -> %s", path, error)
            return []

    def clear_cache(self) -> None:
        """清空加载缓存（测试或手动热重载时使用）。"""
        with self._lock:
            self._cache.clear()
            self._rows_cache.clear()


_REPOSITORY_LOCK = threading.RLock()
_REPOSITORIES: Dict[str, ArtistRepository] = {}


def get_repository(default_path: str = DEFAULT_ARTIST_CSV) -> ArtistRepository:
    """按默认路径获取进程级共享的仓库。

    不同默认文件各自一个实例（画师 CSV / 角色 CSV 互不干扰），
    同一默认文件下的多个节点实例共享解析结果。
    """
    key = os.path.normcase(os.path.abspath(default_path))
    with _REPOSITORY_LOCK:
        repository = _REPOSITORIES.get(key)
        if repository is None:
            repository = ArtistRepository(default_path)
            _REPOSITORIES[key] = repository
        return repository


def get_artist_repository() -> ArtistRepository:
    """获取默认画师 CSV 的进程级共享仓库。"""
    return get_repository(DEFAULT_ARTIST_CSV)


def clear_repositories() -> None:
    """清空全部共享仓库（测试或手动热重载时使用）。"""
    with _REPOSITORY_LOCK:
        for repository in _REPOSITORIES.values():
            repository.clear_cache()
        _REPOSITORIES.clear()


# ----------------------------------------------------------------------
# 抽样
# ----------------------------------------------------------------------
def compute_top_count(total: int, top_ratio: float) -> int:
    """按比例计算 Top 艺术家数量：向上取整，至少 1 个，且不超过总数。"""
    if total <= 0:
        return 0
    try:
        ratio = float(top_ratio)
    except (TypeError, ValueError):
        ratio = 0.0
    ratio = min(1.0, max(0.0, ratio))
    return max(1, min(int(math.ceil(total * ratio)), total))


def split_names(text: Optional[str]) -> List[str]:
    """把逗号分隔的名单解析为去重且保持顺序的列表。"""
    names: List[str] = []
    seen: Set[str] = set()
    for item in str(text or "").split(","):
        name = item.strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def select_artists(rng: random.Random, artist_count: int, artist_top_count: int,
                   force_list: Sequence[str], artists: Sequence[str],
                   top_count: int, exclude_set: Set[str]) -> List[str]:
    """按原有语义选择艺术家。

    - ``force_list`` 中的艺术家一定出现（优先级高于 ``exclude_set``）
    - ``top_count`` > 0 时保证至少抽取 1 个 Top 艺术家
    - Top 池不足时回退到其余艺术家补齐（边界处理）

    返回艺术家名列表（顺序已随机打乱）。

    实现上使用索引抽样：无排除条件时直接对 ``range`` 抽样，
    因此运行时间与艺术家总量无关（旧实现对每个元素重复构建 set，复杂度 O(n²)）。
    """
    total = len(artists)
    force = [name for name in force_list if name]
    # 强制名单数量不少于需求时，从中随机抽取（保持原有语义）
    if len(force) >= artist_count:
        return list(rng.sample(force, artist_count))

    need = artist_count - len(force)
    force_set = set(force)
    top_count = max(0, min(int(top_count), total))

    top_idx: Sequence[int]
    rest_idx: Sequence[int]
    if exclude_set or force_set:
        def allowed(index: int) -> bool:
            name = artists[index]
            return name not in force_set and name not in exclude_set

        top_idx = [index for index in range(top_count) if allowed(index)]
        rest_idx = [index for index in range(top_count, total) if allowed(index)]
    else:
        top_idx = range(top_count)
        rest_idx = range(top_count, total)

    chosen: List[int] = []
    if len(top_idx) > 0:
        n_top = max(1, min(int(artist_top_count), need, len(top_idx)))
        chosen = list(rng.sample(top_idx, n_top))

    if len(chosen) < need:
        missing = need - len(chosen)
        take = min(missing, len(rest_idx))
        if take > 0:
            chosen += rng.sample(rest_idx, take)
            missing -= take
        if missing > 0:
            # 非 Top 池不足（例如 top_ratio=1.0 或艺术家过少）时回退到其余 Top 艺术家
            chosen_set = set(chosen)
            extra = [index for index in top_idx if index not in chosen_set]
            if extra:
                chosen += rng.sample(extra, min(missing, len(extra)))

    final = force + [artists[index] for index in chosen]
    rng.shuffle(final)
    return final
