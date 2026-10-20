"""``ra_core.db`` 的 schema 版本迁移测试。

对应改造：把「用 ``PRAGMA table_info`` 探测列」的迁移方式换成
「meta 表记录 schema_version + 按版本号顺序执行迁移链」。
这里覆盖三条路径：全新库、v1 老库、已是当前版本的库。
"""

import sqlite3

import pytest

from ComfyUI_RollingArtist.ra_core.constants import KIND_ARTIST, SCHEMA_VERSION
from ComfyUI_RollingArtist.ra_core.db import (
    RollingArtistDB,
    _read_raw_schema_version,
    _read_schema_version,
)

_V1_TABLE = """
CREATE TABLE tested (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key_full    TEXT NOT NULL,
    key_set     TEXT NOT NULL,
    key_list    TEXT NOT NULL,
    artist_list TEXT NOT NULL,
    weight_list TEXT NOT NULL,
    prompt      TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_tested_key_full ON tested(key_full);
"""


def _make_v1_db(path, *, rows=1, with_legacy_pool=False):
    """构造一个 v1 结构的库（无 kind 列、无 meta 表）。"""
    conn = sqlite3.connect(str(path))
    conn.executescript(_V1_TABLE)
    for index in range(rows):
        conn.execute(
            "INSERT INTO tested(key_full,key_set,key_list,artist_list,weight_list,prompt)"
            " VALUES(?,?,?,?,?,?)",
            (f"k{index}", f"s{index}", f"l{index}", "[]", "[]", f"p{index}"),
        )
    if with_legacy_pool:
        conn.execute("CREATE TABLE exact_pool (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE pool_meta (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()


def _version(db):
    return _read_raw_schema_version(db._conn)


def test_fresh_db_is_stamped_with_current_version(tmp_path):
    """全新库直接写入当前版本号，不留下"待迁移"状态。"""
    db = RollingArtistDB(str(tmp_path / "fresh.sqlite"))
    assert _version(db) == SCHEMA_VERSION
    db.close()


def test_fresh_db_does_not_log_upgrade(tmp_path, caplog):
    """全新库不应打"升级"日志（否则用户首次运行会困惑）。"""
    with caplog.at_level("WARNING", logger="RollingArtist"):
        db = RollingArtistDB(str(tmp_path / "fresh2.sqlite"))
    assert "升级到 v" not in caplog.text
    db.close()


def test_v1_db_is_migrated_and_data_preserved(tmp_path):
    """v1 老库应自动迁移：补 kind 列、写入版本号、保留原有记录。"""
    path = tmp_path / "v1.sqlite"
    _make_v1_db(path, rows=2)

    db = RollingArtistDB(str(path))
    columns = {row[1] for row in db._conn.execute("PRAGMA table_info(tested)")}
    assert "kind" in columns
    assert _version(db) == SCHEMA_VERSION
    # 老记录保留，且迁移时按默认值标记为 artist
    assert db.count() == 2
    assert db.count(KIND_ARTIST) == 2
    db.close()


def test_v1_db_migration_logs_upgrade(tmp_path, caplog):
    """真正的升级必须留下日志（与全新库区分开）。"""
    path = tmp_path / "v1_log.sqlite"
    _make_v1_db(path, rows=1)

    with caplog.at_level("WARNING", logger="RollingArtist"):
        db = RollingArtistDB(str(path))
    assert "升级到 v" in caplog.text
    db.close()


def test_legacy_pool_tables_do_not_look_brand_new(tmp_path, caplog):
    """有旧版组合池表的库即便 tested 为空，也应被识别为老库并迁移。"""
    path = tmp_path / "legacy_pool.sqlite"
    _make_v1_db(path, rows=0, with_legacy_pool=True)

    with caplog.at_level("WARNING", logger="RollingArtist"):
        db = RollingArtistDB(str(path))
    assert "升级到 v" in caplog.text
    assert _version(db) == SCHEMA_VERSION
    db.close()


def test_reopen_does_not_remigrate(tmp_path, caplog):
    """已是当前版本的库再次打开不应重复迁移、也不应再打日志。"""
    path = tmp_path / "reopen.sqlite"
    db = RollingArtistDB(str(path))
    db.close()

    with caplog.at_level("WARNING", logger="RollingArtist"):
        db2 = RollingArtistDB(str(path))
    assert "升级到 v" not in caplog.text
    assert _version(db2) == SCHEMA_VERSION
    db2.close()


def test_read_schema_version_defaults_to_one_without_record(tmp_path):
    """未记录版本号时，``_read_schema_version`` 回退为 1。"""
    conn = sqlite3.connect(str(tmp_path / "raw.sqlite"))
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    assert _read_schema_version(conn) == 1
    conn.close()


def test_meta_table_exists_after_open(tmp_path):
    db = RollingArtistDB(str(tmp_path / "meta.sqlite"))
    exists = db._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    assert exists is not None
    db.close()
