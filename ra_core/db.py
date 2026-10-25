"""SQLite 化的运行期存储：已测记录。

4.0.0 起取代原来的 CSV 记录文件（``tested_combinations.csv``）。带来的直接变化：

- **判重不再是全量读文件**：去重键在写入时一次算好并建索引，
  判重是 ``O(log n)`` 的索引查询，不再把全部历史读进内存 ``set``
  （旧实现在记录里含引号时会退化成每次整体重读，增量优化形同虚设）
- **跨进程安全**：用 ``BEGIN IMMEDIATE`` 事务把「判重 -> 生成 -> 记录」串行化，
  SQLite 自带的文件锁让多个进程（多个 ComfyUI 实例）也不会生成同一组合
- **艺术家名不再怕分隔符**：艺术家与权重列以 JSON 存储，
  名字里含 ``|`` / ``:`` / 逗号都不会破坏解析
- **穷举组合池已废弃**：4.1.0 删除了穷举模式，打开旧库时会顺手删掉遗留的
  ``exact_pool`` / ``pool_meta`` 两张表

本模块不依赖 ComfyUI，可独立导入与测试。
"""

import csv
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Callable, Dict, Iterator, Optional, Sequence, Tuple

from .constants import (
    CSV_READ_ENCODING,
    DEFAULT_DB,
    KIND_ARTIST,
    LOGGER,
    SCHEMA_VERSION,
)
from .paths import check_db_path as _guard_db_path
from .storage import ensure_parent_dir, parse_tested_row
from .weights import build_prompt, dedup_key, normalize_dedup_mode

__all__ = ["RollingArtistDB", "get_db", "close_all_dbs", "resolve_db_path", "DEDUP_KEY_COLUMNS"]

# 去重模式 -> 预先算好并建索引的键列（列名为白名单常量，不进 SQL 拼接用户输入）
DEDUP_KEY_COLUMNS: Dict[str, str] = {
    "full_prompt": "key_full",
    "artist_set": "key_set",
    "artist_list": "key_list",
}

# 建表与建索引分两步：老库需要先补 kind 列，才能建带该列的复合索引
_TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS tested (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    key_full    TEXT NOT NULL,
    key_set     TEXT NOT NULL,
    key_list    TEXT NOT NULL,
    artist_list TEXT NOT NULL,
    weight_list TEXT NOT NULL,
    prompt      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_INDEX_SCHEMA = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_tested_kind_full ON tested(kind, key_full);
CREATE INDEX IF NOT EXISTS ix_tested_kind_set ON tested(kind, key_set);
CREATE INDEX IF NOT EXISTS ix_tested_kind_list ON tested(kind, key_list);
"""

# 4.1.0 删除穷举模式后遗留的两张表：打开旧库时顺手删掉，避免白占空间
# （这两张表里的数据都是「按当前配置可重建」的派生数据，删掉不影响已测记录）
_LEGACY_POOL_SCHEMA = """
DROP TABLE IF EXISTS exact_pool;
DROP TABLE IF EXISTS pool_meta;
"""


def _migrate_schema(conn: sqlite3.Connection, path: str) -> None:
    """把数据库升级到 :data:`SCHEMA_VERSION`，按版本号顺序执行迁移。

    早期实现靠 ``PRAGMA table_info`` 探测列是否存在来决定是否迁移，
    只能表达「一个开关」；随着结构演进会退化成无法维护的分支树。
    现在改为：

    - 版本号存在 ``meta`` 表的 ``schema_version`` 键
    - 每次结构变更在 :data:`_MIGRATIONS` 末尾追加 ``(目标版本, 迁移函数)``
    - 打开库时从当前版本逐级升到最新，每级迁移成功即写回新版本号

    旧库（没有 meta 表 / 没有版本号）按 v1 起算——v1 就是「带 kind 列」的结构，
    因此 v1 -> v2 的迁移负责把缺 kind 列的老库补齐。

    全新库（首次创建、``tested`` 表为空且无任何历史行）直接写入当前版本号，
    不跑迁移、也不打"升级"日志——否则用户首次运行会看到莫名其妙的版本升级提示。
    """
    recorded = _read_raw_schema_version(conn)

    if recorded is None:
        if _is_brand_new(conn):
            _write_schema_version(conn, SCHEMA_VERSION)
            return
        # 有历史数据但没有版本记录：是旧库，按 v1 起算
        recorded = 1
        _write_schema_version(conn, 1)

    if recorded >= SCHEMA_VERSION:
        return

    original = recorded
    current = recorded
    for target, migrate in _MIGRATIONS:
        if current < target:
            migrate(conn, path)
            current = target
            _write_schema_version(conn, current)

    LOGGER.warning(
        "[RollingArtist] 数据库已从 v%d 升级到 v%d: %s",
        original,
        SCHEMA_VERSION,
        path,
    )


def _is_brand_new(conn: sqlite3.Connection) -> bool:
    """判断是否为全新库：``tested`` 表没有历史行，也没有旧版遗留表。"""
    try:
        row = conn.execute("SELECT COUNT(*) FROM tested").fetchone()
    except sqlite3.Error:
        return True
    if row and int(row[0]) > 0:
        return False
    # 旧版遗留的组合池表存在 => 是升级上来的老库，而非全新库
    for legacy in ("exact_pool", "pool_meta"):
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (legacy,)
        ).fetchone()
        if exists:
            return False
    return True


def _read_raw_schema_version(conn: sqlite3.Connection) -> Optional[int]:
    """读取已记录的 schema 版本；未记录（无 meta 表 / 无该键 / 值非法）时返回 None。"""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def _read_schema_version(conn: sqlite3.Connection) -> int:
    """读取当前 schema 版本；未记录时返回 1（视为最早的合法结构）。"""
    recorded = _read_raw_schema_version(conn)
    return 1 if recorded is None else recorded


def _write_schema_version(conn: sqlite3.Connection, version: int) -> None:
    """写入 schema 版本号（meta 表由 _TABLE_SCHEMA 保证存在）。"""
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(version),),
    )


def _migrate_v1_to_v2(conn: sqlite3.Connection, path: str) -> None:
    """v1 -> v2：给缺少 ``kind`` 列的老库补列，并把旧单列索引换成复合索引。

    没有这一步，旧的 ``rollingartist.sqlite`` 会在查询时报「no such column: kind」。
    只补列 + 换索引，不动已有数据。
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tested)")}
    if not columns or "kind" in columns:
        return
    conn.execute(f"ALTER TABLE tested ADD COLUMN kind TEXT NOT NULL DEFAULT '{KIND_ARTIST}'")
    for name in ("ux_tested_key_full", "ix_tested_key_set", "ix_tested_key_list"):
        conn.execute(f"DROP INDEX IF EXISTS {name}")
    LOGGER.warning("[RollingArtist] 旧数据库已升级到新表结构（补 kind 列）: %s", path)


# 迁移链：按 (目标版本, 迁移函数) 升序排列。新增结构变更时在末尾追加。
_MIGRATIONS: Tuple[Tuple[int, "Callable[[sqlite3.Connection, str], None]"], ...] = ((2, _migrate_v1_to_v2),)


class RollingArtistDB:
    """按数据库路径共享的记录库（线程安全、跨进程安全）。

    典型用法::

        db = get_db("")                      # 空路径 -> 默认数据库
        with db.generation_lock():           # BEGIN IMMEDIATE 事务
            if not db.is_tested("artist_set", artists, weights):
                ...
                db.record(artists, weights, prompt)
    """

    def __init__(self, path: str) -> None:
        self.path = path
        # _write_lock：保护"写事务本身"（BEGIN IMMEDIATE ... COMMIT）。
        # 注意它只约束"谁在跑事务"，不约束"事务里放什么"——持锁时长由调用方决定：
        # 只做 check+record 的调用方持锁极短；把整个重试循环放进来（RollingArtist）
        # 则持锁等于一次完整生成，同库并发会被串行化（详见 generation_lock）。
        self._write_lock = threading.RLock()
        # _local：每线程一个连接。sqlite3 的 Connection 默认不允许跨线程使用
        # （check_same_thread=True），早期实现用一把锁共享单连接，
        # 结果是同一库上的所有生成任务被强制排队，且事务里混入了纯 CPU 工作。
        self._local = threading.local()
        # 主线程先建一次连接：既完成建表/迁移，也让"连接建立失败"在构造期暴露
        self._connect(path)

    # ------------------------------------------------------------------
    # 连接与事务
    # ------------------------------------------------------------------
    @property
    def _conn(self) -> sqlite3.Connection:
        """取当前线程的连接；没有就按需创建（线程安全）。"""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect(self.path)
            self._local.conn = conn
        return conn

    @staticmethod
    def _connect(path: str) -> sqlite3.Connection:
        ensure_parent_dir(path)
        # isolation_level=None -> 自动提交，事务由 generation_lock() 显式控制
        # 直接构造 sqlite3.Connection（sqlite3.connect 的等价官方形式，参数一致）
        conn = sqlite3.Connection(path, timeout=10.0, isolation_level=None)
        # 默认 check_same_thread=True：连接只在本线程使用，违反时立即报错
        conn.execute("PRAGMA journal_mode=WAL")
        # 多进程同时写时不要立刻失败，等一会儿再报错
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA synchronous=NORMAL")
        # 顺序不能颠倒：老库要先补列（迁移），才能建带该列的索引
        conn.executescript(_TABLE_SCHEMA)
        _migrate_schema(conn, path)
        conn.executescript(_INDEX_SCHEMA)
        conn.executescript(_LEGACY_POOL_SCHEMA)
        return conn

    @contextmanager
    def generation_lock(self) -> Iterator["RollingArtistDB"]:
        """把「判重 -> 记录」放进同一把写锁与同一个事务。

        - 进程内：``_write_lock`` 保证同一时刻只有一个写事务在跑
        - 跨进程：``BEGIN IMMEDIATE`` 抢占写锁，另一个进程会阻塞到本事务结束，
          因此不会出现「两边都判定未测过」而生成同一组合的情况

        **持锁时长 = 调用方在上下文里做的事情**，这一点没有隐藏机制：

        - 只做 ``is_tested`` + ``record`` 的调用方，持锁时间是一次索引查询加一条写入
        - 需要「判重 -> 生成 -> 记录」整段原子的调用方（``RollingArtist`` 的重试循环：
          命中已测组合就要重新抽样），必须把抽样也放进来，此时持锁等于一次完整生成，
          同一数据库上的并发生成会被串行化

        后者是跨进程不重复的代价，不是实现缺陷：把抽样挪到锁外，两个进程仍可能在
        锁外各抽到同一组合，只是把冲突推迟到写入时才被 ``INSERT OR IGNORE`` 吞掉。
        实测单次持锁约 9ms（3 个艺术家、默认参数），重试 10 次约 90ms。

        若事务期间抛异常则回滚，保证不会留下半截记录。
        """
        with self._write_lock:
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield self
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def close(self) -> None:
        """关闭**当前线程**的连接（测试与热重载时使用）。

        注意：关闭后本对象**并未失效**——下一次访问 :attr:`_conn`
        （如 ``count()`` / ``is_tested()``）会按需**重新建立连接**。
        这是有意设计（连接是每线程懒创建的），但也意味着「close 之后
        再用」不会报错，而是静默重连。

        若要表达「彻底废弃本对象」，请配合 :func:`close_all_dbs()` 一起使用
        （它会从进程级缓存里移除本对象，之后 :func:`get_db` 会新建实例）。
        """
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None

    def close_current_thread(self) -> None:
        """关闭当前线程持有的连接（**只能关当前线程**，故如此命名）。

        ``sqlite3`` 的连接不能跨线程关闭，因此不存在「一次关掉所有线程连接」
        的方法——其它线程的连接只能在它们自己的线程里关，否则随线程结束由 GC 回收。

        命名说明：本方法原名 ``close_all_threads``，但实测它关不掉别的线程，
        名字与行为不符会误导调用方（以为调用后所有连接都已释放）。现改为此名，
        并把旧名保留为**已废弃别名**，避免破坏既有外部调用。
        """
        self.close()

    def close_all_threads(self) -> None:
        """已废弃别名，等价于 :meth:`close_current_thread`。

        .. deprecated::
            名字暗示"关闭全部线程的连接"，但 sqlite3 不允许跨线程关闭，
            实际只能关当前线程。请改用 :meth:`close_current_thread`。
        """
        self.close_current_thread()

    # ------------------------------------------------------------------
    # 已测记录
    # ------------------------------------------------------------------
    def is_tested(
        self, mode: Optional[str], artists: Sequence[str], weights: Sequence[float], kind: str = KIND_ARTIST
    ) -> bool:
        """按去重模式判断某组合是否已记录过（按 ``kind`` 隔离画师与角色）。"""
        mode = normalize_dedup_mode(mode)
        if mode == "none":
            return False
        key = dedup_key(mode, artists, weights)
        if not key:
            return False
        column = DEDUP_KEY_COLUMNS[mode]
        row = self._conn.execute(
            f"SELECT 1 FROM tested WHERE kind = ? AND {column} = ? LIMIT 1",
            (kind, key),
        ).fetchone()
        return row is not None

    def record(
        self, artists: Sequence[str], weights: Sequence[float], prompt: str, kind: str = KIND_ARTIST
    ) -> bool:
        """记录一次生成结果；组合键重复时静默忽略（不算失败）。

        返回 ``True`` 表示**写入成功或组合已存在**（两种情况都非错误），
        ``False`` 仅在**写入失败**时返回（会额外记 ERROR 日志）。

        注意：``True`` **不表示"新增了一行"**——重复组合因 ``INSERT OR IGNORE``
        影响行数为 0，同样返回 ``True``。需要区分"新增 / 已存在"请用
        :meth:`record_ex`。

        这个方法被节点层用来判断"是否需要记 ERROR 日志"，因此语义是
        「非失败即成功」，而不是"是否真的插入了"。

        **事务契约（两种调用语义不同，务必分清）**

        本方法自己加 ``self._write_lock``，但它对「事务边界」的影响取决于调用位置：

        - **在 :meth:`generation_lock` 内调用** —— 此时已有外层
          ``BEGIN IMMEDIATE`` 事务，本方法只执行一条 ``INSERT OR IGNORE``，
          写入**加入外层事务**。外层抛异常会连同这条记录一起 ``ROLLBACK``。
          （实测：lock 内 record 后抛异常，``count()`` 回到 0。）
        - **独立调用（不在 lock 内）** —— ``isolation_level=None`` 下单条
          ``INSERT`` 是**隐式自动提交事务**，语句执行完即落盘，
          **不受任何外层事务保护**，也无法被后续 ``ROLLBACK`` 撤销。
          （实测：独立 record 后抛异常，记录仍留存。）

        因此需要「判重 → 生成 → 记录」原子性的调用方**必须**用
        :meth:`generation_lock` 包住整段；本方法只是其中的写入动作，
        **不要**把它的自动提交误当成事务保护。
        """
        return self.record_ex(artists, weights, prompt, kind) is not None

    def record_ex(
        self, artists: Sequence[str], weights: Sequence[float], prompt: str, kind: str = KIND_ARTIST
    ) -> Optional[bool]:
        """同 :meth:`record`，但如实返回三态，供需要区分"新增/已存在"的调用方使用。

        返回:

        - ``True``：本次**新增**了一行
        - ``False``：组合**已存在**，被 ``INSERT OR IGNORE`` 跳过
        - ``None``：**写入失败**（已记 ERROR 日志）

        ``record()`` 把本方法的 ``True`` / ``False`` 都映射为 ``True``（非失败即成功），
        使节点层的 ``if not db.record(...)`` 只对真正的写入失败报警。
        需要区分"新增 / 已存在"时用本方法。
        """
        with self._write_lock:
            inserted = self._insert(artists, weights, prompt, kind)
        if inserted is None:
            return None
        return inserted > 0

    def count(self, kind: Optional[str] = None) -> int:
        """已测记录条数；``kind`` 为空时统计全部。

        读取走当前线程的连接，不需要写锁（WAL 模式下读不阻塞写）。
        """
        if kind is None:
            row = self._conn.execute("SELECT COUNT(*) FROM tested").fetchone()
        else:
            row = self._conn.execute("SELECT COUNT(*) FROM tested WHERE kind = ?", (kind,)).fetchone()
        return int(row[0])

    def _insert(
        self, artists: Sequence[str], weights: Sequence[float], prompt: str, kind: str = KIND_ARTIST
    ) -> Optional[int]:
        """写入一条记录，返回实际新增的行数（组合已存在时为 0），失败返回 None。"""
        artists = [str(name) for name in artists]
        values = [round(float(weight), 1) for weight in weights]
        key_full = dedup_key("full_prompt", artists, values)
        key_set = dedup_key("artist_set", artists, values)
        key_list = dedup_key("artist_list", artists, values)
        try:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO tested"
                "(kind, key_full, key_set, key_list, artist_list, weight_list, prompt)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    kind,
                    key_full,
                    key_set,
                    key_list,
                    json.dumps(artists, ensure_ascii=False),
                    json.dumps(values),
                    str(prompt),
                ),
            )
        except sqlite3.Error as error:
            LOGGER.error("[RollingArtist] 记录写入数据库失败: %s -> %s", self.path, error)
            return None
        return int(cursor.rowcount)

    # ------------------------------------------------------------------
    # 旧版 CSV 记录导入
    # ------------------------------------------------------------------
    def import_legacy_csv(self, path: str, kind: str = KIND_ARTIST) -> Optional[Tuple[int, int]]:
        """把旧版 CSV 记录导入数据库，返回 ``(新增条数, 读取条数)``。

        使用 ``INSERT OR IGNORE``，因此可以重复调用：已导入过的组合会被跳过，
        重复导入不会产生重复记录（幂等）。读取失败时返回 None。

        行格式沿用旧版：``[艺术家(| 分隔), 权重(, 分隔), prompt]``，
        也兼容更早的「仅含提示词」单列格式。
        """
        if not os.path.isfile(path):
            return None
        added = 0
        total = 0
        try:
            with open(path, encoding=CSV_READ_ENCODING, newline="") as handle:
                with self._write_lock:
                    for row in csv.reader(handle):
                        if not row:
                            continue
                        total += 1
                        artists, weights = parse_tested_row(row)
                        if not artists:
                            continue
                        prompt = str(row[2]).strip() if len(row) >= 3 else ""
                        prompt = prompt or build_prompt(artists, weights, "")
                        # 只统计真正新增的行：重复导入同一份文件时新增数应为 0
                        if self._insert(artists, weights, prompt, kind):
                            added += 1
        except (OSError, csv.Error, UnicodeDecodeError) as error:
            LOGGER.error("[RollingArtist] 旧记录导入失败: %s -> %s", path, error)
            return None
        return added, total


_DBS: Dict[str, RollingArtistDB] = {}
_DBS_LOCK = threading.RLock()


def resolve_db_path(custom_path: Optional[str], default_path: str = DEFAULT_DB) -> str:
    """空路径回退到默认数据库；否则校验用户路径并规范化。

    画师节点与角色节点各自传自己的默认库（``DEFAULT_DB`` / ``DEFAULT_CHARACTER_DB``）。
    用户路径会经过 :func:`ra_core.paths.check_db_path` 防护：
    命中受保护目录、或目标已存在但不是 SQLite 文件时抛出 ``PathNotAllowedError``
    （继承 ``ValueError``），避免节点被用作任意文件写入的跳板。
    """
    path = str(custom_path or "").strip().strip('"')
    if not path:
        return default_path
    return _guard_db_path(path)


def get_db(custom_path: Optional[str] = None, default_path: str = DEFAULT_DB) -> RollingArtistDB:
    """按路径获取进程级共享的数据库对象。"""
    path = resolve_db_path(custom_path, default_path)
    key = os.path.normcase(os.path.abspath(path))
    with _DBS_LOCK:
        db = _DBS.get(key)
        if db is None:
            db = RollingArtistDB(path)
            _DBS[key] = db
        return db


def close_all_dbs() -> None:
    """清空进程级数据库缓存，并关闭**当前线程**持有的连接（测试或热重载时使用）。

    两个效果需要分开看，别把名字理解成"关掉所有线程的所有连接"：

    - **清空缓存**：``_DBS`` 被清空，之后 :func:`get_db` 会重新构造实例。
      这一步是彻底的、对所有线程都生效的（缓存是进程级共享的）。
    - **关闭连接**：每线程连接无法跨线程关闭，因此只能关当前线程的；
      其它线程的连接随其线程结束由 GC 回收。若某个工作线程仍持有旧连接，
      它不会在本次调用中被释放——但它所属的实例已从缓存里摘除，
      不会再被 :func:`get_db` 命中。
    """
    with _DBS_LOCK:
        for db in _DBS.values():
            try:
                db.close_current_thread()
            except sqlite3.Error as error:  # pragma: no cover - 关闭失败的兜底
                LOGGER.warning("[RollingArtist] 关闭数据库失败: %s", error)
        _DBS.clear()
