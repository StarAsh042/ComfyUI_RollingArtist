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
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .constants import CSV_READ_ENCODING, DEFAULT_ARTIST_CSV, LOGGER
from .paths import check_csv_path

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
    "available_names",
    "split_names",
]

# CSV 文件大小保护：超过该阈值时给出 WARNING（整表载入内存可能拖垮进程）
CSV_SIZE_WARN_BYTES = 100 * 1024 * 1024

# 单次读取的最大行数（None = 不限制）。留作紧急护栏，默认不启用
CSV_MAX_ROWS: Optional[int] = None

# 被识别为表头的单元格（小写、空格/短横线归一为下划线后比较）
HEADER_TOKENS = frozenset(
    {
        "artist",
        "artists",
        "character",
        "characters",
        "name",
        "names",
        "tag",
        "tags",
        "token",
        "tokens",
        "trigger",
        "triggers",
        "count",
        "post_count",
        "solo_count",
        "url",
        "id",
        "copyright",
        "core_tags",
        "category",
        "source",
        "notes",
        "description",
        "sample",
    }
)

# auto 模式下按优先级挑选的列名。
# 顺序说明：
# - ``character`` 系列最优先：danbooru_character.csv 的 trigger 是「角色 + 作品」多标签串
#   （如 "hatsune miku, vocaloid"），当成单个艺术家名会破坏提示词，因此必须让 character 胜出
# - ``trigger`` 排在 ``artist`` 之前：trigger 列是「提示词可直接使用」的写法
#   （空格与括号已转义，见 modify_danbooru_art.py），而 artist 列是 danbooru 原始标签，
#   可能带未转义括号（会破坏权重语法）
COLUMN_PRIORITY = (
    "character",
    "characters",
    "trigger",
    "artist",
    "artists",
    "name",
    "names",
    "tag",
    "tags",
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
            spec,
            len(sample_row),
        )
        return 0
    if has_header and spec in cells:
        return cells.index(spec)
    LOGGER.warning("[RollingArtist] csv_column=%s 未匹配到表头列，回退到首列", spec)
    return 0


def parse_artist_rows(rows: Iterable[Sequence[str]], column: Optional[str] = COLUMN_AUTO) -> List[str]:
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
    consumed = 0
    for row in body:
        if CSV_MAX_ROWS is not None and consumed >= CSV_MAX_ROWS:
            LOGGER.warning(
                "[RollingArtist] CSV 行数达到上限 %d 已截断（parse_artist_rows）"
                "（如需读取全部内容，请调大 CSV_MAX_ROWS）",
                CSV_MAX_ROWS,
            )
            break
        consumed += 1
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
        self._cache: OrderedDict[Tuple[str, str, int, int], List[str]] = OrderedDict()
        self._rows_cache: OrderedDict[Tuple[str, int, int], List[List[str]]] = OrderedDict()

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------
    def resolve_path(self, custom_path: Optional[str]) -> str:
        """空路径回退到默认 CSV；否则校验用户路径并规范化。

        用户路径会经过 :func:`ra_core.paths.check_csv_path` 防护：
        命中系统 / 凭据 / 源码等受保护目录时抛出 ``PathNotAllowedError``
        （继承 ``ValueError``），避免节点被用作任意文件读取的跳板。
        """
        path = str(custom_path or "").strip().strip('"')
        if not path:
            return self.default_path
        return check_csv_path(path)

    def stat_key(self, path: str) -> Optional[Tuple[str, int, int]]:
        """返回 (规范化绝对路径, 修改时间, 大小)；文件不可用时返回 None。"""
        try:
            info = os.stat(path)
        except OSError:
            return None
        return (os.path.normcase(os.path.abspath(path)), info.st_mtime_ns, info.st_size)

    @staticmethod
    def _warn_if_huge(path: str, size: int) -> None:
        """整表载入前检查文件体积，过大时给出可操作的告警（不阻断）。"""
        if size > CSV_SIZE_WARN_BYTES:
            LOGGER.warning(
                "[RollingArtist] CSV 体积较大（%.1f MB）：%s。"
                "整表载入内存可能导致占用过高，建议先用数据加工脚本裁剪，"
                "或确认机器内存充足",
                size / 1048576,
                path,
            )

    def _open_csv(self, path: str):
        """打开 CSV 供逐行消费；体积告警与**打开期**异常集中在这里处理。

        返回文件句柄（调用方负责 ``with`` 关闭）；文件不存在、是目录或其它 IO
        异常时记日志并返回 ``None``，由调用方决定"空结果"的语义。

        这里**不**捕获 ``UnicodeDecodeError``：``open()`` 只拿文件描述符、
        不读内容，因此不可能抛解码错误。实测确认——写入 ``\\xff\\xfe`` 非法字节后
        ``open(encoding='utf-8')`` 正常返回，错误要到迭代行时才抛。
        读取期的解码/CSV 错误由 :meth:`_csv_rows` 统一兜底。
        """
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if size:
            self._warn_if_huge(path, size)
        try:
            return open(path, encoding=CSV_READ_ENCODING, newline="")
        except FileNotFoundError:
            LOGGER.error("[RollingArtist] CSV 文件不存在: %s", path)
        except IsADirectoryError:
            LOGGER.error("[RollingArtist] CSV 路径是目录: %s", path)
        except OSError as error:
            LOGGER.error("[RollingArtist] CSV 打开失败: %s -> %s", path, error)
        return None

    def _cached_load(self, cache: "OrderedDict", cache_key, reader: Callable[[], List]):
        """「查缓存 -> 未命中则读 -> 写回缓存 -> LRU 淘汰」的通用骨架。

        :meth:`load`（键含列规格）与 :meth:`load_rows`（键为文件签名）此前各写了一份
        逐字相同的缓存骨架；差异只在缓存容器与键，因此抽到这里，用一个 ``reader``
        回调承接"怎么读"。行为完全不变：

        - 命中时把键移到 LRU 末尾（最近使用）
        - **读取结果为空时不写缓存**（空结果常来自瞬时 IO 失败，缓存它会让
          文件恢复后仍然返回空，直到签名变化）
        - 超过 :data:`_MAX_CACHE` 时淘汰最久未使用的键
        """
        with self._lock:
            cached = cache.get(cache_key)
            if cached is not None:
                cache.move_to_end(cache_key)
                return cached

        value = reader()

        if value and cache_key is not None:
            with self._lock:
                cache[cache_key] = value
                cache.move_to_end(cache_key)
                while len(cache) > self._MAX_CACHE:
                    cache.popitem(last=False)
        return value

    def load(
        self, custom_path: Optional[str] = None, column: Optional[str] = COLUMN_AUTO
    ) -> Tuple[List[str], Optional[Tuple[str, int, int]]]:
        """加载艺术家列表，返回 ``(artists, stat_key)``。

        失败时返回空列表并记录日志，不抛出异常，由调用方决定如何报错。
        """
        path = self.resolve_path(custom_path)
        spec = normalize_column_spec(column)
        stat_key = self.stat_key(path)
        cache_key = (stat_key[0], spec, stat_key[1], stat_key[2]) if stat_key else None

        if cache_key is None:
            return self._read(path, spec), stat_key
        artists = self._cached_load(self._cache, cache_key, lambda: self._read(path, spec))
        return artists, stat_key

    def _csv_rows(self, path: str, max_rows: Optional[int] = None) -> List[List[str]]:
        """读取整份 CSV 为行列表（丢弃空行）；任何失败都记日志并返回空列表。

        这是两条读取路径（:meth:`_read` / :meth:`_read_rows`）的公共入口，
        把「体积告警 + 打开失败 + 读取期解码/CSV 错误」三类异常收敛到一处。

        参数:
            max_rows: 非 None 时读满这么多行即**停止读取**（不是读完再切片）。
                这是护栏的关键语义：``CSV_MAX_ROWS`` 存在的意义就是在文件
                异常巨大时止损，读满即 break 才能避免"只想取 100 行却付了
                读完 40 万行的代价"（实测 6 MB / 40 万行需 0.68 s）。

        **为什么返回列表而不是上下文管理器/生成器**：``UnicodeDecodeError``
        与 ``csv.Error`` 是在**迭代行**时抛出的，而不是 ``open()`` 时。
        若用 ``@contextmanager`` 写成 "yield 行迭代器，except 里再 yield None"，
        异常实际上是在调用方的 ``with`` 块内（消费迭代器时）抛出，
        此时生成器已 yield 过一次，无法二次 yield —— 会触发
        ``RuntimeError: generator didn't stop after throw()``（已实测复现）。
        因此在**一个**生成器内部完成读取与异常兜底，把结果一次交付。
        """
        handle = self._open_csv(path)
        if handle is None:
            return []
        rows: List[List[str]] = []
        try:
            with handle:
                for row in csv.reader(handle):
                    if not row:
                        continue
                    rows.append(row)
                    if max_rows is not None and len(rows) >= max_rows:
                        break
        except UnicodeDecodeError as error:
            LOGGER.error("[RollingArtist] CSV 编码无法解析（请使用 UTF-8）: %s -> %s", path, error)
            return []
        except (OSError, csv.Error) as error:
            LOGGER.error("[RollingArtist] CSV 读取失败: %s -> %s", path, error)
            return []
        return rows

    def _read(self, path: str, spec: str) -> List[str]:
        # 截断由下游 parse_artist_rows 内的 CSV_MAX_ROWS 负责（它按「消费到的行数」计数）
        artists = parse_artist_rows(self._csv_rows(path), spec)
        if not artists:
            LOGGER.warning("[RollingArtist] CSV 中未解析到任何艺术家: %s", path)
        return artists

    def load_rows(
        self, custom_path: Optional[str] = None
    ) -> Tuple[List[List[str]], Optional[Tuple[str, int, int]]]:
        """读取并缓存整份 CSV 行，返回 ``(rows, stat_key)``。

        与 :meth:`load` 共用同一套「文件签名」缓存策略，供需要多列的节点使用
        （例如 RollingCharacter 要同时用 trigger / copyright / core_tags / count）。
        行内保持原始顺序，表头行也在其中，由调用方自行判断。
        """
        path = self.resolve_path(custom_path)
        stat_key = self.stat_key(path)
        if stat_key is None:
            return self._read_rows(path), stat_key
        rows = self._cached_load(self._rows_cache, stat_key, lambda: self._read_rows(path))
        return rows, stat_key

    def _read_rows(self, path: str) -> List[List[str]]:
        """读整份 CSV 为行列表；超过 :data:`CSV_MAX_ROWS` 时**读满即停**并告警。"""
        if CSV_MAX_ROWS is None:
            return self._csv_rows(path)
        rows = self._csv_rows(path, max_rows=CSV_MAX_ROWS)
        if len(rows) >= CSV_MAX_ROWS:
            LOGGER.warning(
                "[RollingArtist] CSV 行数达到上限 %d，已截断读取: %s"
                "（如需读取全部内容，请调大 CSV_MAX_ROWS）",
                CSV_MAX_ROWS,
                path,
            )
        return rows

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


def available_names(names: Sequence[str], blocked: Optional[Set[str]]) -> List[str]:
    """从候选名列表中剔除被屏蔽的名字，返回可用名单。

    这是「候选池计算」的独立一步，供调用方在抽样前明确地知道自己有多少可用项。
    典型用途：角色节点先用最近窗口算屏蔽集，再用本函数得到可用候选，
    当结果为空（窗口把候选吃光）时可以显式决定"退回允许重复"。

    参数:
        names: 完整候选名列表（保持原顺序）
        blocked: 需要屏蔽的名字集合；``None`` 或空集合表示不屏蔽
    """
    if not blocked:
        return list(names)
    return [name for name in names if name not in blocked]


def select_artists(
    rng: random.Random,
    artist_count: int,
    artist_top_count: int,
    force_list: Sequence[str],
    artists: Sequence[str],
    top_count: int,
    exclude_set: Set[str],
) -> List[str]:
    """从候选池中抽取指定数量的名字。

    - ``force_list`` 中的名字一定出现（优先级高于 ``exclude_set``）
    - ``top_count`` > 0 时保证至少抽取 1 个 Top 成员
    - Top 池不足时回退到其余成员补齐（边界处理）

    返回名字列表（顺序已随机打乱）。

    实现上使用索引抽样：无排除条件时直接对 ``range`` 抽样，
    因此运行时间与总量无关（旧实现对每个元素重复构建 set，复杂度 O(n²)）。

    注意：本函数只负责"抽"，不负责"判断候选是否够用"。调用方若需要
    在候选被屏蔽光时采取降级策略，应先用 :func:`available_names` 计算可用候选。
    """
    total = len(artists)
    # force 去重（保持首次出现顺序）：重复项会让 len(force) 虚高，
    # 进而在 `len(force) >= artist_count` 分支里用 rng.sample 抽出同名两次，
    # 产出 ['a1','a2','a1'] 这种权重被打散、语义重复的提示词且不报错。
    force: List[str] = []
    _seen_force: Set[str] = set()
    for name in force_list:
        if name and name not in _seen_force:
            _seen_force.add(name)
            force.append(name)
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
