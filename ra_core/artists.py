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
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from .constants import CSV_READ_ENCODING, DEFAULT_ARTIST_CSV, LOGGER
from .paths import check_csv_path
from .storage import file_fingerprint

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

# 单次读取的最大记录数（None = 不限制）。这是**进程级默认值**，生产代码从不赋值
# （只有测试用 monkeypatch 改它），真正的上限由调用方按参数传入（见 normalize_max_rows）。
# 保留它是因为：① 它是节点 csv_max_rows 未设置时的兜底；② 既有测试以它作为全局开关。
CSV_MAX_ROWS: Optional[int] = None


def normalize_max_rows(value: Optional[int]) -> Optional[int]:
    """校验并归一化「读取上限」：只接受 ``None`` 或**正整数**。

    这是内部读取 API 的唯一入口校验，不做静默容错——把 ``0`` 当成「不限制」、
    把负数当成「不限」、把字符串当数字，都会让用户以为限流生效而实际没有，
    或反过来把「不限制」误读成「读 0 条」。因此非法值一律报配置错误。

    参数:
        value: ``None``（不限制）或正整数（最多消费的记录条数）

    异常:
        ValueError: 传入了 ``0``、负数、``bool`` 或非整数类型
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"[RollingArtist] 读取上限必须是 None 或正整数，当前值: {value!r}")
    if not isinstance(value, int):
        raise ValueError(f"[RollingArtist] 读取上限必须是 None 或正整数，当前值: {value!r}")
    if value <= 0:
        raise ValueError(
            f"[RollingArtist] 读取上限必须是 None 或正整数（0 表示不限制请传 None），当前值: {value!r}"
        )
    return value


def resolve_max_rows(max_rows: Optional[int]) -> Optional[int]:
    """解析「本次读取的有效上限」：显式参数优先，否则回落到模块级 :data:`CSV_MAX_ROWS`。

    两条来源都走 :func:`normalize_max_rows` 校验，因此非法值不会被静默吞掉。
    有效上限必须进入缓存键（见 :meth:`ArtistRepository.load` / ``load_rows``），
    否则「改上限」会命中旧缓存——实测改造前 5 层缓存会全部 stale。
    """
    explicit = normalize_max_rows(max_rows)
    if explicit is not None:
        return explicit
    return normalize_max_rows(CSV_MAX_ROWS)


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


def parse_artist_rows(
    rows: Iterable[Sequence[str]],
    column: Optional[str] = COLUMN_AUTO,
    max_rows: Optional[int] = None,
) -> List[str]:
    """从 CSV 行迭代器中解析艺术家名列表。

    参数:
        rows: 行迭代器（``csv.reader`` 即可，无需预先读入内存）
        column: ``auto``（默认，自动识别表头/列）、``all``（展平所有列）、
                列索引（``"0"``）或表头列名（``"artist"``）
        max_rows: 最多消费的记录条数（``None`` = 不限制）。

    **计数基准**：``max_rows`` 数的是「``csv.reader`` 产出的记录」，
    **含表头行、含空记录**。这是 v5.2.0 统一后的唯一定义，``load_rows`` 路径也按
    同一基准计数（见 :meth:`ArtistRepository._iter_csv_rows`）。

    历史坑（改造前）：本函数与 ``_csv_rows`` 的计数对象不同——这里不计表头但计空记录，
    那里计表头但丢弃空记录，同一文件同一 N 会返回不同条数。现在统一到「含表头、
    含空记录」，两条路径对同一 CSV 同一 N 必须返回一致的记录集。

    **exact-N 不谎称截断**：只有「确实还有记录没被消费」时才告警。改造前用
    ``len(rows) >= CSV_MAX_ROWS`` 判断，恰好 N 条的文件也会打「已截断」，是误报。
    """
    limit = normalize_max_rows(max_rows)
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
    truncated = False
    consumed = 0
    for row in body:
        if limit is not None and consumed >= limit:
            truncated = True
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

    if truncated:
        LOGGER.warning(
            "[RollingArtist] 已达到读取上限 %d，后续记录未参与加载"
            "（如需读取全部内容，请把 csv_max_rows 设为 0 或调大该值）",
            limit,
        )
    return artists


class ArtistRepository:
    """艺术家列表的加载与缓存（线程安全）。

    同一个 CSV 在内容未变化时只会被解析一次，多个节点实例共享同一份缓存。
    """

    _MAX_CACHE = 4

    def __init__(self, default_path: str = DEFAULT_ARTIST_CSV) -> None:
        self.default_path = default_path
        self._lock = threading.RLock()
        self._cache: OrderedDict[Tuple[str, str, int, int, int], List[str]] = OrderedDict()
        self._rows_cache: OrderedDict[Tuple[str, int, int, int], List[List[str]]] = OrderedDict()

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

    def cache_fingerprint(self, custom_path: Optional[str]) -> str:
        """CSV 缓存指纹，供节点的 ``IS_CHANGED`` 使用。

        与 :meth:`load` 走同一条路径解析（空路径 -> 默认 CSV，用户路径 -> 路径防护），
        因此指纹与真正被读取的文件严格对应。

        路径被防护拒绝或无法解析时**不抛异常**：``IS_CHANGED`` 抛异常会打断
        ComfyUI 的缓存判断；这里返回一个稳定哨兵，让节点照常执行并在执行时
        抛出原本的错误（用户能看到明确原因）。
        """
        try:
            resolved = self.resolve_path(custom_path)
        except (OSError, ValueError) as error:
            return f"invalid:{type(error).__name__}:{str(custom_path or '').strip()}"
        return file_fingerprint(resolved)

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
        self,
        custom_path: Optional[str] = None,
        column: Optional[str] = COLUMN_AUTO,
        max_rows: Optional[int] = None,
    ) -> Tuple[List[str], Optional[Tuple[str, int, int]]]:
        """加载艺术家列表，返回 ``(artists, stat_key)``。

        **有效上限进缓存键**：键从 ``(路径, 列, mtime, size)`` 扩成
        ``(路径, 列, mtime, size, limit)``，改上限必然重建，不会命中旧结果。
        """
        path = self.resolve_path(custom_path)
        spec = normalize_column_spec(column)
        limit = resolve_max_rows(max_rows)
        stat_key = self.stat_key(path)
        cache_key = (
            (stat_key[0], spec, stat_key[1], stat_key[2], limit if limit is not None else 0)
            if stat_key
            else None
        )

        if cache_key is None:
            return self._read(path, spec, limit), stat_key
        artists = self._cached_load(self._cache, cache_key, lambda: self._read(path, spec, limit))
        return artists, stat_key

    def _iter_csv_rows(
        self,
        path: str,
        state: Optional[Dict[str, bool]] = None,
        max_rows: Optional[int] = None,
    ) -> Iterator[List[str]]:
        """**流式**产出 CSV 的非空行，不把整份文件读成列表。

        这是 :meth:`_read`（画师解析）与 :meth:`open_rows`（角色解析）的数据源，
        与 :meth:`_csv_rows` 的区别只有一个：**逐条产出而不是先攒成 List**。
        因此「整份原始行列表 + 最终对象」的双份驻留被消除，解析器拿到一条就处理一条。

        参数:
            state: 可选的失败标记字典。读取期发生解码 / CSV / IO 错误时置
                ``state["failed"] = True``，由调用方决定「整份作废」（见 :meth:`_read`）。
                为什么用外部标记而不是抛异常：错误发生在**迭代过程中**，而调用方
                已经拿到前几条记录，无法再回退；用一个可查的标记让调用方在迭代
                结束后统一作废，才能保持「任一读错误则空结果」的既有语义。
            max_rows: 最多消费的**原始记录**条数（``None`` = 不限制）。

        **计数基准**：计的是 ``csv.reader`` 产出的每一条记录，
        **含空记录**（空记录会被跳过不产出，但**计入**上限）。这是两条读取路径的
        唯一计数实现——``_csv_rows`` 也委托到这里，因此 ``load`` 与 ``load_rows``
        对同一 CSV 同一 N 消费的记录数必然一致。

        达到上限即 ``return``（不是读完再切），因此「只想取前 N 条」不会付出读完
        整个大文件的代价（实测 6 MB / 40 万行全读需 0.68 s）。

        **异常兜底必须写在生成器内部**（历史教训）：``UnicodeDecodeError`` 与
        ``csv.Error`` 是在**迭代行**时抛出的，不是 ``open()`` 时。若用
        ``@contextmanager`` 写成 "yield 迭代器，except 里再 yield None"，异常会在
        调用方 ``with`` 块内（消费迭代器时）抛出，此时生成器已 yield 过一次、
        无法二次 yield —— 触发 ``RuntimeError: generator didn't stop after throw()``
        （已实测复现）。所以读取与兜底放在**同一个**生成器里。
        """
        limit = normalize_max_rows(max_rows)
        handle = self._open_csv(path)
        if handle is None:
            if state is not None:
                state["failed"] = True
            return
        consumed = 0
        try:
            with handle:
                for row in csv.reader(handle):
                    if limit is not None and consumed >= limit:
                        # 走到这里说明「第 limit+1 条」已经被产出了 —— 它正是判断
                        # 「是否真有余量」所需的证据，**零额外读盘**。文件恰好 N 条时
                        # 循环会自然结束、走不到这里，因此 exact-N 不会误报截断。
                        if state is not None:
                            state["truncated"] = True
                        return
                    consumed += 1
                    if not row:
                        continue
                    yield row
        except UnicodeDecodeError as error:
            LOGGER.error("[RollingArtist] CSV 编码无法解析（请使用 UTF-8）: %s -> %s", path, error)
            if state is not None:
                state["failed"] = True
        except (OSError, csv.Error) as error:
            LOGGER.error("[RollingArtist] CSV 读取失败: %s -> %s", path, error)
            if state is not None:
                state["failed"] = True

    def open_rows(
        self, custom_path: Optional[str] = None, max_rows: Optional[int] = None
    ) -> Tuple[Iterator[List[str]], Dict[str, bool], Optional[Tuple[str, int, int]]]:
        """流式打开 CSV，返回 ``(行迭代器, 失败标记, stat_key)``。

        供需要多列数据的调用方（RollingCharacter 要同时用 trigger / copyright /
        core_tags / count）使用，**不写 ``_rows_cache``**：角色正常加载不应常驻
        一份原始行列表。

        与 :meth:`load_rows` 的关系：``load_rows`` 是保留的兼容入口（返回 List 并缓存），
        本方法是新增的流式入口，两者读同一份文件、同一套路径解析与体积告警，
        并按同一基准（含表头、含空记录）应用 ``max_rows``。

        调用方必须：① 消费完迭代器后检查失败标记，为真则整份作废；
        ② 在不再消费时 ``close()`` 迭代器，避免文件句柄悬空。
        """
        path = self.resolve_path(custom_path)
        stat_key = self.stat_key(path)
        state: Dict[str, bool] = {"failed": False}
        return self._iter_csv_rows(path, state, max_rows), state, stat_key

    def _csv_rows(self, path: str, max_rows: Optional[int] = None) -> List[List[str]]:
        """把流式行迭代器收集成列表（丢弃空行）；任何失败都记日志并返回空列表。

        改造后本方法只是 :meth:`_iter_csv_rows` 的**收集壳**：计数、提前停止与
        异常兜底全部收敛到流式实现里，两条路径不再各写一份逻辑——这正是
        v5.2.0 之前「同一文件同一 N 两条路径返回不同条数」的根因。

        保留它是因为 ``load_rows`` 需要返回 ``List``（保留的兼容入口）。
        """
        state: Dict[str, bool] = {"failed": False}
        rows = list(self._iter_csv_rows(path, state, max_rows))
        if state["failed"]:
            return []
        return rows

    def _read_rows_with_flag(self, path: str, max_rows: Optional[int]) -> Tuple[List[List[str]], bool]:
        """读整份行并同时返回「是否确实还有余量未被消费」（供告警准确性用）。

        为什么不在外层再探一次：多读一遍会把「读满即停」变成「读满再读一遍」，
        实测会让截断场景的读取行数翻倍（50 条上限时读到 103 行），直接违背
        护栏的初衷。这里让流式实现自己在**碰到第 limit+1 条时**置位，零额外开销。
        """
        state: Dict[str, bool] = {"failed": False, "truncated": False}
        rows = list(self._iter_csv_rows(path, state, max_rows))
        if state["failed"]:
            return [], False
        return rows, state["truncated"]

    def _read(self, path: str, spec: str, max_rows: Optional[int] = None) -> List[str]:
        """把 CSV 记录**流式**交给解析器，不再先攒一份原始行列表。

        改造前这里是 ``parse_artist_rows(self._csv_rows(path), spec)``：先把整份
        CSV 读成 ``List[List[str]]``，再交给解析器产出艺术家列表，冷加载时两份
        结构同时驻留。解析器本来就接受 ``Iterable``（见 :func:`parse_artist_rows`
        签名与实现：只迭代一次、不依赖 ``len``/切片），因此直接喂迭代器即可。

        **上限只在流式层应用一次**：``_iter_csv_rows`` 负责计数与提前停止，
        这里不再把 ``max_rows`` 传给解析器——否则会二次计数（实测会少取一条）。

        错误语义保持不变：读取期任一解码 / CSV / IO 错误会让整份作废并返回空列表
        （``state["failed"]``），而不是返回「出错前已解析出的前缀」——后者会让
        用户在文件损坏时拿到看似正常的残缺候选池，比直接报错更难排查。
        """
        limit = normalize_max_rows(max_rows)
        state: Dict[str, bool] = {"failed": False, "truncated": False}
        artists = parse_artist_rows(self._iter_csv_rows(path, state, limit), spec)
        if state["failed"]:
            return []
        if state["truncated"]:
            LOGGER.warning(
                "[RollingArtist] 已达到读取上限 %d，后续记录未参与加载: %s"
                "（如需读取全部内容，请把 csv_max_rows 设为 0 或调大该值）",
                limit,
                path,
            )
        if not artists:
            LOGGER.warning("[RollingArtist] CSV 中未解析到任何艺术家: %s", path)
        return artists

    def load_rows(
        self, custom_path: Optional[str] = None, max_rows: Optional[int] = None
    ) -> Tuple[List[List[str]], Optional[Tuple[str, int, int]]]:
        """读取并缓存整份 CSV 行，返回 ``(rows, stat_key)``。

        与 :meth:`load` 共用同一套「文件签名」缓存策略，供需要多列的节点使用
        （例如 RollingCharacter 要同时用 trigger / copyright / core_tags / count）。
        行内保持原始顺序，表头行也在其中，由调用方自行判断。

        **有效上限进缓存键**：键从 ``stat_key`` 扩成 ``stat_key + (limit,)``。
        改造前键只有文件签名，改上限会命中旧缓存——实测「上限 3→6」时 5 层缓存
        全部 stale，而 ``_rows_cache`` 命中还会掩盖下层 records/index 的失效。
        """
        path = self.resolve_path(custom_path)
        limit = resolve_max_rows(max_rows)
        stat_key = self.stat_key(path)
        if stat_key is None:
            return self._read_rows(path, limit), stat_key
        cache_key = stat_key + (limit if limit is not None else 0,)
        rows = self._cached_load(self._rows_cache, cache_key, lambda: self._read_rows(path, limit))
        return rows, stat_key

    def _read_rows(self, path: str, max_rows: Optional[int] = None) -> List[List[str]]:
        """读整份 CSV 为行列表；超过有效上限时**读满即停**并告警。

        **exact-N 不谎称截断**：只有「确实还有记录未被消费」时才告警。
        改造前用 ``len(rows) >= CSV_MAX_ROWS`` 判断，恰好 N 条的文件也报「已截断」，
        是误报。现在由流式实现在碰到第 limit+1 条时置位，**不额外读盘**。
        """
        limit = normalize_max_rows(max_rows)
        if limit is None:
            return self._csv_rows(path)
        rows, truncated = self._read_rows_with_flag(path, limit)
        if truncated:
            LOGGER.warning(
                "[RollingArtist] 已达到读取上限 %d，后续记录未参与加载: %s"
                "（如需读取全部内容，请把 csv_max_rows 设为 0 或调大该值）",
                limit,
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
