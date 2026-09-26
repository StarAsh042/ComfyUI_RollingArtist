"""CSV 读写与已测组合存储（线程安全）。

- 写入使用临时文件 + ``os.replace`` 原子替换，避免中途失败留下半截文件
- 已测记录采用“增量解析”缓存：文件被追加时只解析新增部分，
  避免每次执行都重新读取整个历史文件
"""

import csv
import io
import os
import threading
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .constants import CSV_READ_ENCODING, CSV_WRITE_ENCODING, LOGGER
from .weights import dedup_key, normalize_dedup_mode, parse_prompt, parse_weight_list

__all__ = [
    "ensure_parent_dir",
    "read_rows",
    "append_row",
    "write_rows",
    "parse_tested_row",
    "TestedStore",
    "get_tested_store",
]

# 头部快照长度：用于判断文件是否被整体替换（追加写入不会改变头部内容）
_HEAD_BYTES = 128


# ----------------------------------------------------------------------
# 基础 IO
# ----------------------------------------------------------------------
def ensure_parent_dir(path: str) -> None:
    """确保父目录存在（路径没有目录部分时忽略）。"""
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)


def read_rows(path: str) -> List[List[str]]:
    """读取 CSV，返回非空行列表；文件不存在或读取失败时返回空列表。"""
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding=CSV_READ_ENCODING, newline="") as handle:
            return [row for row in csv.reader(handle) if row]
    except (OSError, csv.Error, UnicodeDecodeError) as error:
        LOGGER.error("[RollingArtist] CSV 读取失败: %s -> %s", path, error)
        return []


def append_row(path: str, row: Sequence[str]) -> bool:
    """向 CSV 追加一行；失败时记录日志并返回 False。"""
    try:
        ensure_parent_dir(path)
        with open(path, "a", encoding=CSV_WRITE_ENCODING, newline="") as handle:
            csv.writer(handle).writerow(row)
        return True
    except OSError as error:
        LOGGER.error("[RollingArtist] CSV 写入失败: %s -> %s", path, error)
        return False


def write_rows(path: str, rows: Sequence[Sequence[str]]) -> bool:
    """整体覆写 CSV（原子替换）；失败时记录日志并返回 False。"""
    temp_path = f"{path}.tmp"
    try:
        ensure_parent_dir(path)
        with open(temp_path, "w", encoding=CSV_WRITE_ENCODING, newline="") as handle:
            csv.writer(handle).writerows(rows)
            handle.flush()
        os.replace(temp_path, path)
        return True
    except OSError as error:
        LOGGER.error("[RollingArtist] CSV 写入失败: %s -> %s", path, error)
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        return False


def parse_tested_row(row: Sequence[str]) -> Tuple[List[str], List[float]]:
    """解析已测 CSV 的一行 -> (艺术家列表, 权重列表)。

    行格式为 ``[艺术家(| 分隔), 权重(, 分隔), prompt]``；
    兼容旧版“仅含提示词”的单列格式。
    """
    artists: List[str] = []
    weights: List[float] = []
    if not row:
        return artists, weights

    if len(row) >= 2:
        artists = [name.strip() for name in str(row[0]).split("|") if name.strip()]
        weights = parse_weight_list(row[1])
    elif str(row[0]).strip():
        artists, weights = parse_prompt(row[0])

    if artists and len(weights) != len(artists):
        weights = [weights[index] if index < len(weights) else 1.0 for index in range(len(artists))]
    return artists, weights


# ----------------------------------------------------------------------
# 已测记录存储
# ----------------------------------------------------------------------
class _ModeState:
    """单个去重模式的增量解析状态。"""

    __slots__ = ("offset", "keys", "head")

    def __init__(self) -> None:
        self.offset = 0          # 已解析到的字节位置
        self.keys: Set[str] = set()
        self.head: Optional[bytes] = None   # 文件头部快照，用于识别“被整体替换”


class TestedStore:
    """已生成组合的记录库（按路径共享，线程安全）。

    - ``keys(mode)`` 返回该模式下的已测组合键集合（增量更新，返回值只读）
    - ``record()`` 追加一条记录
    - ``generation_lock()`` 返回跨实例共享的可重入锁，
      用于把“读取已测 -> 生成 -> 记录”串行化，避免多节点实例生成重复组合
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._states: Dict[str, _ModeState] = {}

    # ------------------------------------------------------------------
    def generation_lock(self) -> threading.RLock:
        """返回用于保护完整生成流程的锁（与文件 IO 共用同一把可重入锁）。"""
        return self._lock

    def keys(self, mode: Optional[str]) -> Set[str]:
        """返回指定去重模式的已测组合键集合（内部增量维护，调用方不要修改）。"""
        mode = normalize_dedup_mode(mode)
        if mode == "none":
            return set()
        with self._lock:
            state = self._states.get(mode)
            if state is None:
                state = self._states[mode] = _ModeState()
            self._refresh(state, mode)
            return state.keys

    def record(self, artists: Sequence[str], weights: Sequence[float], prompt: str) -> bool:
        """记录一次生成结果。"""
        row = [
            "|".join(artists),
            ",".join(f"{round(float(weight), 1)}" for weight in weights),
            prompt,
        ]
        with self._lock:
            return append_row(self.path, row)

    def reset(self) -> None:
        """清空增量缓存（文件被外部替换/删除后使用）。"""
        with self._lock:
            self._states.clear()

    # ------------------------------------------------------------------
    def _refresh(self, state: _ModeState, mode: str) -> None:
        """把文件中新增的记录解析进 state.keys。

        用“头部快照 + 文件长度”双重校验判断文件是否仍是之前那份：
        追加写入不会改变头部内容，因此可安全地只解析新增部分；
        一旦头部不再是旧的头部（例如用户用备份整体替换了该文件），
        就从 0 重新解析，避免从旧偏移继续读导致键集合掺杂、漏判已测组合。
        """
        try:
            size = os.path.getsize(self.path)
        except OSError:
            # 文件不存在或不可访问：重置状态，等待下次重建
            state.offset = 0
            state.keys.clear()
            state.head = None
            return

        try:
            with open(self.path, "rb") as handle:
                head = handle.read(_HEAD_BYTES)
                if state.head is not None and not head.startswith(state.head):
                    # 头部不匹配 = 文件被整体替换，旧偏移与旧键都不可靠
                    state.offset = 0
                    state.keys.clear()
                state.head = head

                if size < state.offset:
                    # 文件被截断（例如用户删除了部分记录），需要整体重读
                    state.offset = 0
                    state.keys.clear()
                if size == state.offset:
                    return

                handle.seek(state.offset)
                raw = handle.read()
        except OSError as error:
            LOGGER.error("[RollingArtist] 已测记录读取失败: %s -> %s", self.path, error)
            return
        if not raw:
            return

        if b'"' in raw:
            # 出现引号说明可能存在跨行字段，增量切分会破坏解析，退化为整体重读
            state.offset = 0
            state.keys.clear()
            try:
                with open(self.path, "rb") as handle:
                    raw = handle.read()
            except OSError as error:
                LOGGER.error("[RollingArtist] 已测记录读取失败: %s -> %s", self.path, error)
                return

        # 只消费到最后一个完整行，避免把写了一半的记录解析进来
        cut = raw.rfind(b"\n")
        if cut < 0:
            return
        chunk = raw[:cut + 1]
        state.offset += len(chunk)

        for row in csv.reader(io.StringIO(chunk.decode(CSV_READ_ENCODING, errors="replace"))):
            if not row:
                continue
            artists, weights = parse_tested_row(row)
            if not artists:
                continue
            key = dedup_key(mode, artists, weights)
            if key:
                state.keys.add(key)


_STORES: Dict[str, TestedStore] = {}
_STORES_LOCK = threading.RLock()


def get_tested_store(path: str) -> TestedStore:
    """按路径获取进程级共享的已测记录库。"""
    key = os.path.normcase(os.path.abspath(path))
    with _STORES_LOCK:
        store = _STORES.get(key)
        if store is None:
            store = TestedStore(path)
            _STORES[key] = store
        return store
