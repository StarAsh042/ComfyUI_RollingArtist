"""SQLite 化的运行期存储：已测记录 + 穷举剩余组合池。

4.0.0 起取代原来的两份 CSV（``tested_combinations.csv`` / ``*_remaining.csv``），
一个数据库文件同时承载两类数据。带来的直接变化：

- **判重不再是全量读文件**：去重键在写入时一次算好并建索引，
  判重是 ``O(log n)`` 的索引查询，不再把全部历史读进内存 ``set``
  （旧实现在记录里含引号时会退化成每次整体重读，增量优化形同虚设）
- **跨进程安全**：用 ``BEGIN IMMEDIATE`` 事务把「判重 -> 生成 -> 记录」串行化，
  SQLite 自带的文件锁让多个进程（多个 ComfyUI 实例）也不会生成同一组合
- **组合池抽取不再重写整份清单**：旧实现每抽一条就把剩余组合整体重写回磁盘，
  现在只删一行
- **艺术家名不再怕分隔符**：艺术家与权重列以 JSON 存储，
  名字里含 ``|`` / ``:`` / 逗号都不会破坏解析
- **组合池构建放在 SQL 里**：候选与已测的差集由 ``CROSS JOIN + LEFT JOIN`` 完成，
  不再需要把上百万条组合物化成 Python 列表

本模块不依赖 ComfyUI，可独立导入与测试。
"""

import csv
import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Dict, Iterator, Optional, Sequence, Tuple

from .constants import (
    CSV_READ_ENCODING,
    DEFAULT_DB,
    EXACT_POOL_KEEP,
    KIND_ARTIST,
    LOGGER,
)
from .storage import ensure_parent_dir, backup_file, parse_tested_row
from .weights import build_prompt, dedup_key, normalize_dedup_mode

__all__ = ["RollingArtistDB", "get_db", "close_all_dbs", "resolve_db_path",
           "pool_key_for", "DEDUP_KEY_COLUMNS"]

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

CREATE TABLE IF NOT EXISTS exact_pool (
    pool_key TEXT NOT NULL,
    artist   TEXT NOT NULL,
    weight   REAL NOT NULL,
    PRIMARY KEY (pool_key, artist, weight)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS pool_meta (
    pool_key TEXT PRIMARY KEY,
    universe INTEGER NOT NULL,
    built_at REAL NOT NULL
);
"""

_INDEX_SCHEMA = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_tested_kind_full ON tested(kind, key_full);
CREATE INDEX IF NOT EXISTS ix_tested_kind_set ON tested(kind, key_set);
CREATE INDEX IF NOT EXISTS ix_tested_kind_list ON tested(kind, key_list);
"""

# 组合池构建时用的临时表（CROSS JOIN 出候选全集，再排除已测）
_TMP_ARTISTS = "ra_tmp_artists"
_TMP_WEIGHTS = "ra_tmp_weights"


def _migrate_schema(conn: sqlite3.Connection, path: str) -> None:
    """把早期版本的库升级到带 ``kind`` 列的表结构。

    没有这一步，旧的 ``rollingartist.sqlite`` 会在查询时报「no such column: kind」。
    只补列 + 换索引，不动已有数据。
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tested)")}
    if not columns or "kind" in columns:
        return
    conn.execute(f"ALTER TABLE tested ADD COLUMN kind TEXT NOT NULL DEFAULT '{KIND_ARTIST}'")
    # 旧的单列索引与新键语义不符（缺少 kind 维度），换成复合索引
    for name in ("ux_tested_key_full", "ix_tested_key_set", "ix_tested_key_list"):
        conn.execute(f"DROP INDEX IF EXISTS {name}")
    LOGGER.warning("[RollingArtist] 旧数据库已升级到新表结构（补 kind 列）: %s", path)

# 组合池构建分批写入的行数：避免一次性构造超长 SQL 参数列表
_BUILD_CHUNK = 20000


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
        self._lock = threading.RLock()
        self._conn = self._connect(path)

    # ------------------------------------------------------------------
    # 连接与事务
    # ------------------------------------------------------------------
    @staticmethod
    def _connect(path: str) -> sqlite3.Connection:
        ensure_parent_dir(path)
        # isolation_level=None -> 自动提交，事务由 generation_lock() 显式控制
        conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        # 多进程同时写时不要立刻失败，等一会儿再报错
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA synchronous=NORMAL")
        # 顺序不能颠倒：老库要先补列，才能建带该列的索引
        conn.executescript(_TABLE_SCHEMA)
        _migrate_schema(conn, path)
        conn.executescript(_INDEX_SCHEMA)
        # 组合池构建用的临时表（连接级，进程退出自动消失）
        conn.execute(
            f"CREATE TEMP TABLE IF NOT EXISTS {_TMP_ARTISTS}"
            "(name TEXT PRIMARY KEY, key_part TEXT NOT NULL)"
        )
        conn.execute(
            f"CREATE TEMP TABLE IF NOT EXISTS {_TMP_WEIGHTS}(weight REAL PRIMARY KEY)"
        )
        return conn

    @contextmanager
    def generation_lock(self) -> Iterator["RollingArtistDB"]:
        """把「判重 -> 生成 -> 记录」放进同一把锁与同一个事务。

        - 进程内：可重入锁，避免同进程多线程穿插
        - 跨进程：``BEGIN IMMEDIATE`` 抢占写锁，另一个进程会阻塞到本事务结束，
          因此不会出现「两边都判定未测过」而生成同一组合的情况
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def close(self) -> None:
        """关闭连接（测试与热重载时使用）。"""
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # 已测记录
    # ------------------------------------------------------------------
    def is_tested(self, mode: Optional[str], artists: Sequence[str],
                  weights: Sequence[float], kind: str = KIND_ARTIST) -> bool:
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

    def record(self, artists: Sequence[str], weights: Sequence[float],
               prompt: str, kind: str = KIND_ARTIST) -> bool:
        """记录一次生成结果；组合键重复时静默忽略（不算失败）。"""
        with self._lock:
            return self._insert(artists, weights, prompt, kind) is not None

    def count(self, kind: Optional[str] = None) -> int:
        """已测记录条数；``kind`` 为空时统计全部。"""
        with self._lock:
            if kind is None:
                row = self._conn.execute("SELECT COUNT(*) FROM tested").fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM tested WHERE kind = ?", (kind,)
                ).fetchone()
            return int(row[0])

    def _insert(self, artists: Sequence[str], weights: Sequence[float],
                prompt: str, kind: str = KIND_ARTIST) -> Optional[int]:
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
    def import_legacy_csv(self, path: str,
                          kind: str = KIND_ARTIST) -> Optional[Tuple[int, int]]:
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
            with open(path, "r", encoding=CSV_READ_ENCODING, newline="") as handle:
                with self._lock:
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

    # ------------------------------------------------------------------
    # 穷举组合池
    # ------------------------------------------------------------------
    def pool_count(self, pool_key: str) -> int:
        """返回该组合池中剩余的组合数。"""
        with self._lock:
            return int(self._conn.execute(
                "SELECT COUNT(*) FROM exact_pool WHERE pool_key = ?", (pool_key,)
            ).fetchone()[0])

    def pool_built(self, pool_key: str) -> bool:
        """该组合池是否已经构建过（用于区分「没建过」与「建完是空的」）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM pool_meta WHERE pool_key = ? LIMIT 1", (pool_key,)
            ).fetchone()
        return row is not None

    def pool_build(self, pool_key: str, universe: int,
                   artists: Sequence[str], escaped: Sequence[str],
                   grid: Sequence[float], kind: str = KIND_ARTIST) -> int:
        """构建组合池：候选全集减去已测组合，返回实际写入的组合数。

        差集完全在 SQL 里完成（``CROSS JOIN`` + ``LEFT JOIN tested``），
        因此不需要把上百万条组合读进 Python 内存。
        """
        with self._lock:
            conn = self._conn
            conn.execute(f"DELETE FROM {_TMP_ARTISTS}")
            conn.execute(f"DELETE FROM {_TMP_WEIGHTS}")
            conn.executemany(
                f"INSERT OR IGNORE INTO {_TMP_ARTISTS}(name, key_part) VALUES (?, ?)",
                list(zip(artists, escaped)),
            )
            conn.executemany(
                f"INSERT OR IGNORE INTO {_TMP_WEIGHTS}(weight) VALUES (?)",
                [(round(float(w), 1),) for w in grid],
            )
            conn.execute(f"DELETE FROM exact_pool WHERE pool_key = ?", (pool_key,))
            # 键格式与 weights.dedup_key("full_prompt", ...) 保持一致：
            # 单个艺术家时 = 转义后的名字 + ":" + 一位小数权重
            conn.execute(
                f"INSERT OR IGNORE INTO exact_pool(pool_key, artist, weight)"
                f" SELECT ?, a.name, w.weight"
                f" FROM {_TMP_ARTISTS} a CROSS JOIN {_TMP_WEIGHTS} w"
                f" LEFT JOIN tested t"
                f"   ON t.kind = ?"
                f"  AND t.key_full = (a.key_part || ':' || printf('%.1f', w.weight))"
                f" WHERE t.id IS NULL",
                (pool_key, kind),
            )
            written = int(conn.execute(
                "SELECT COUNT(*) FROM exact_pool WHERE pool_key = ?", (pool_key,)
            ).fetchone()[0])
            # 用「先删后插」而不是 UPSERT：UPSERT 需要 SQLite 3.24+，
            # 老系统（如 Ubuntu 18.04 的 Python 3.8）可能仍在 3.22 上
            conn.execute("DELETE FROM pool_meta WHERE pool_key = ?", (pool_key,))
            conn.execute(
                "INSERT INTO pool_meta(pool_key, universe, built_at) VALUES (?, ?, ?)",
                (pool_key, int(universe), time.time()),
            )
            self._pool_evict_locked(pool_key)
        return written

    def pool_take(self, pool_key: str, index: int) -> Optional[Tuple[str, float]]:
        """取出第 ``index`` 条组合并从池中删除（``index`` 由调用方随机给出）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT artist, weight FROM exact_pool WHERE pool_key = ?"
                " ORDER BY artist, weight LIMIT 1 OFFSET ?",
                (pool_key, int(index)),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "DELETE FROM exact_pool WHERE pool_key = ? AND artist = ? AND weight = ?",
                (pool_key, row[0], row[1]),
            )
        return str(row[0]), round(float(row[1]), 1)

    def pool_drop(self, pool_key: str) -> None:
        """删除指定组合池（含构建标记）。"""
        with self._lock:
            self._conn.execute("DELETE FROM exact_pool WHERE pool_key = ?", (pool_key,))
            self._conn.execute("DELETE FROM pool_meta WHERE pool_key = ?", (pool_key,))

    def _pool_evict_locked(self, keep_key: str) -> None:
        """按构建先后保留最近使用的若干套组合池，避免数据库无限增长。

        排序用 ``rowid``（插入顺序）而不是 ``built_at``：``time.time()`` 在 Windows 上
        分辨率约 15ms，连续构建的多套池会拿到相同时间戳，排序结果不确定。
        重建某个池走的是「先删后插」，rowid 会变大，因此仍然算「最近构建」。
        """
        rows = self._conn.execute(
            "SELECT pool_key FROM pool_meta WHERE pool_key != ?"
            " ORDER BY rowid DESC",
            (keep_key,),
        ).fetchall()
        for (stale,) in rows[max(0, EXACT_POOL_KEEP - 1):]:
            self._conn.execute("DELETE FROM exact_pool WHERE pool_key = ?", (stale,))
            self._conn.execute("DELETE FROM pool_meta WHERE pool_key = ?", (stale,))


def pool_key_for(artists: Sequence[str], grid: Sequence[float]) -> str:
    """由「艺术家池 + 权重网格」派生组合池标识。

    配置一变就换成新池（等价于旧版「删掉 *_remaining.csv 会按当前配置重建」），
    且用 sha1 而非内建 hash，保证跨进程、跨启动稳定。
    """
    digest = hashlib.sha1()
    digest.update("|".join(str(name) for name in artists).encode("utf-8"))
    digest.update(b"\x00")
    digest.update(",".join(f"{round(float(w), 1):.1f}" for w in grid).encode("utf-8"))
    return digest.hexdigest()


_DBS: Dict[str, RollingArtistDB] = {}
_DBS_LOCK = threading.RLock()


def resolve_db_path(custom_path: Optional[str],
                    default_path: str = DEFAULT_DB) -> str:
    """空路径回退到默认数据库；否则使用用户指定的路径。

    画师节点与角色节点各自传自己的默认库（``DEFAULT_DB`` / ``DEFAULT_CHARACTER_DB``）。
    """
    path = str(custom_path or "").strip().strip('"')
    return path or default_path


def get_db(custom_path: Optional[str] = None,
           default_path: str = DEFAULT_DB) -> RollingArtistDB:
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
    """关闭并清空所有缓存的数据库连接（测试或热重载时使用）。"""
    with _DBS_LOCK:
        for db in _DBS.values():
            try:
                db.close()
            except sqlite3.Error as error:  # pragma: no cover - 关闭失败的兜底
                LOGGER.warning("[RollingArtist] 关闭数据库失败: %s", error)
        _DBS.clear()
