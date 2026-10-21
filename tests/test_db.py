"""``ra_core.db`` 的测试：记录、判重、旧数据导入、跨进程去重。

其中 ``test_cross_process_*`` 用两个真实子进程验证跨进程不会产生重复组合
（旧实现的锁只在进程内有效）。
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from ComfyUI_RollingArtist.ra_core.constants import KIND_ARTIST, KIND_CHARACTER
from ComfyUI_RollingArtist.ra_core.db import RollingArtistDB

GRID = [0.5, 0.6, 0.7]
ARTISTS = ["a", "b", "c"]


def make_db(tmp_path, name="test.sqlite"):
    return RollingArtistDB(str(tmp_path / name))


# ----------------------------------------------------------------------
# 记录与判重
# ----------------------------------------------------------------------
def test_record_and_is_tested_per_mode(tmp_path):
    db = make_db(tmp_path)
    assert db.count() == 0
    assert db.record(["a", "b"], [0.8, 0.7], "p") is True
    assert db.count() == 1

    assert db.is_tested("full_prompt", ["a", "b"], [0.8, 0.7]) is True
    assert db.is_tested("full_prompt", ["b", "a"], [0.8, 0.7]) is False
    # artist_set / artist_list 忽略顺序与权重，只比名字
    assert db.is_tested("artist_set", ["b", "a"], [0.1, 0.2]) is True
    assert db.is_tested("artist_list", ["b", "a"], [0.1, 0.2]) is True
    assert db.is_tested("artist_set", ["a", "c"], [0.1, 0.2]) is False
    assert db.is_tested("artist_list", ["a", "c"], [0.1, 0.2]) is False
    assert db.is_tested("none", ["a", "b"], [0.8, 0.7]) is False
    assert db.is_tested("full_prompt", ["a"], [0.8]) is False


def test_duplicate_record_is_ignored(tmp_path):
    db = make_db(tmp_path)
    db.record(["a"], [0.5], "p")
    db.record(["a"], [0.5], "p")
    assert db.count() == 1


def test_weights_are_stored_with_one_decimal(tmp_path):
    db = make_db(tmp_path)
    db.record(["a"], [0.66666], "p")
    assert db.is_tested("full_prompt", ["a"], [0.7]) is True


def test_special_characters_do_not_break_parsing(tmp_path):
    """名字含分隔符时，旧版 CSV 会串行；数据库用 JSON 存列，必须精确往返。"""
    db = make_db(tmp_path)
    db.record(["x|y"], [1.0], "p1")
    db.record(["x", "y"], [1.0, 1.0], "p2")
    assert db.count() == 2
    assert db.is_tested("full_prompt", ["x|y"], [1.0]) is True
    assert db.is_tested("full_prompt", ["x", "y"], [1.0, 1.0]) is True
    # full_prompt 键不同；artist_set / artist_list 也不应互相覆盖
    assert db.is_tested("artist_list", ["x|y"], [1.0]) is True
    assert db.is_tested("artist_list", ["x", "y"], [1.0, 1.0]) is True


def test_generation_lock_rolls_back_on_error(tmp_path):
    db = make_db(tmp_path)
    with pytest.raises(RuntimeError):
        with db.generation_lock():
            db.record(["a"], [0.5], "p")
            raise RuntimeError("boom")
    assert db.count() == 0


def test_existing_database_is_reused_across_instances(tmp_path):
    path = str(tmp_path / "shared.sqlite")
    first = RollingArtistDB(path)
    first.record(["a"], [0.5], "p")
    second = RollingArtistDB(path)
    assert second.count() == 1
    assert second.is_tested("full_prompt", ["a"], [0.5]) is True


# ----------------------------------------------------------------------
# 记录类型隔离（画师 vs 角色）
# ----------------------------------------------------------------------
def test_records_are_isolated_by_kind(tmp_path):
    """同名同权重的画师与角色各占一条，互不冒充。"""
    db = make_db(tmp_path)
    assert db.record(["a"], [0.5], "artist", KIND_ARTIST) is True
    assert db.record(["a"], [0.5], "character", KIND_CHARACTER) is True

    assert db.count() == 2
    assert db.count(KIND_ARTIST) == 1
    assert db.count(KIND_CHARACTER) == 1
    for mode in ("full_prompt", "artist_set", "artist_list"):
        assert db.is_tested(mode, ["a"], [0.5], KIND_ARTIST) is True
        assert db.is_tested(mode, ["a"], [0.5], KIND_CHARACTER) is True
    assert db.is_tested("full_prompt", ["b"], [0.5], KIND_CHARACTER) is False


def test_same_key_under_two_kinds_is_not_treated_as_duplicate(tmp_path):
    """若唯一索引没带上 kind，第二条会被 INSERT OR IGNORE 静默丢掉。"""
    db = make_db(tmp_path)
    db.record(["x"], [1.0], "p1", KIND_ARTIST)
    assert db.record(["x"], [1.0], "p2", KIND_CHARACTER) is True
    assert db.count() == 2


def test_schema_migration_from_early_3_3_0_database(tmp_path):
    """早期版本建的库没有 kind 列，打开时应自动补列而不是报错。"""
    path = str(tmp_path / "legacy.sqlite")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE tested (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_full TEXT NOT NULL, key_set TEXT NOT NULL, key_list TEXT NOT NULL,
            artist_list TEXT NOT NULL, weight_list TEXT NOT NULL, prompt TEXT NOT NULL
        );
        CREATE UNIQUE INDEX ux_tested_key_full ON tested(key_full);
        CREATE INDEX ix_tested_key_set ON tested(key_set);
        CREATE INDEX ix_tested_key_list ON tested(key_list);
        INSERT INTO tested(key_full, key_set, key_list, artist_list, weight_list, prompt)
        VALUES ('a:0.5', 'a', 'a', '["a"]', '[0.5]', 'artist:a:0.5');
        """
    )
    conn.commit()
    conn.close()

    db = RollingArtistDB(path)
    assert db.count() == 1
    assert db.count(KIND_ARTIST) == 1  # 老数据默认归为画师
    assert db.count(KIND_CHARACTER) == 0
    assert db.is_tested("full_prompt", ["a"], [0.5], KIND_ARTIST) is True
    assert db.is_tested("full_prompt", ["a"], [0.5], KIND_CHARACTER) is False
    # 升级后仍可继续写入
    assert db.record(["b"], [0.5], "p", KIND_CHARACTER) is True
    assert db.count() == 2


def test_legacy_pool_tables_are_dropped(tmp_path):
    """4.1.0 删除了穷举模式：旧库里遗留的组合池表应在打开时被清掉。"""
    path = str(tmp_path / "old_pool.sqlite")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE exact_pool (pool_key TEXT, artist TEXT, weight REAL);
        CREATE TABLE pool_meta (pool_key TEXT, universe INTEGER, built_at REAL);
        INSERT INTO exact_pool VALUES ('k', 'a', 0.5);
        INSERT INTO pool_meta VALUES ('k', 9, 0.0);
        """
    )
    conn.commit()
    conn.close()

    RollingArtistDB(path).close()

    check = sqlite3.connect(path)
    names = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    check.close()
    assert "exact_pool" not in names
    assert "pool_meta" not in names


# ----------------------------------------------------------------------
# 旧版 CSV 导入
# ----------------------------------------------------------------------
def test_legacy_csv_import_is_idempotent(tmp_path):
    db = make_db(tmp_path)
    legacy = tmp_path / "old.csv"
    legacy.write_text(
        "a|b,0.8,0.7,(artist:a:0.8),(artist:b:0.7)\nc,0.5,artist:c:0.5\nartist:d:0.3\n",
        encoding="utf-8",
    )
    assert db.import_legacy_csv(str(legacy)) == (3, 3)
    assert db.count() == 3
    # 再导入一次：键都重复，不会新增
    assert db.import_legacy_csv(str(legacy)) == (0, 3)
    assert db.count() == 3
    assert db.is_tested("artist_list", ["a", "b"], [0.8, 0.7]) is True
    assert db.is_tested("full_prompt", ["d"], [0.3]) is True


def test_legacy_csv_import_missing_file(tmp_path):
    db = make_db(tmp_path)
    assert db.import_legacy_csv(str(tmp_path / "nope.csv")) is None
    assert db.count() == 0


# ----------------------------------------------------------------------
# 跨进程：两个真实子进程抢同一批组合
# ----------------------------------------------------------------------
_CHILD_SCRIPT = """
import random, sys
sys.path.insert(0, {parent!r})
from ComfyUI_RollingArtist.ra_core.db import RollingArtistDB

db = RollingArtistDB({db_path!r})
combos = [(artist, weight) for artist in {artists!r} for weight in {grid!r}]
rng = random.Random({offset})
rng.shuffle(combos)

got = []
for artist, weight in combos:
    # 「判重 -> 记录」必须在同一个事务里：否则两个进程可能同时判定未测过
    with db.generation_lock():
        if db.is_tested("full_prompt", [artist], [weight]):
            continue
        db.record([artist], [weight], "p")
    got.append(f"{{artist}}:{{weight}}")
print(";".join(got))
"""


def test_cross_process_generates_no_duplicate_combos(tmp_path):
    parent = str(Path(__file__).resolve().parents[2])
    db_path = str(tmp_path / "shared.sqlite")
    RollingArtistDB(db_path)  # 先建库，子进程再打开同一个文件

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    processes = []
    for offset in (0, 1000):
        script = _CHILD_SCRIPT.format(
            parent=parent, db_path=db_path, artists=ARTISTS, grid=GRID, offset=offset
        )
        processes.append(
            subprocess.Popen(
                [sys.executable, "-c", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
            )
        )

    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=120)
        assert process.returncode == 0, stderr
        results.extend(part for part in stdout.strip().split(";") if part)

    assert len(results) == len(set(results)), "跨进程出现了重复组合"
    assert len(results) == len(ARTISTS) * len(GRID)
    assert RollingArtistDB(db_path).count() == len(ARTISTS) * len(GRID)


def test_legacy_import_stays_idempotent_across_processes(tmp_path):
    """先后在两个子进程里导入同一份旧 CSV，记录数不应翻倍。"""
    parent = str(Path(__file__).resolve().parents[2])
    db_path = str(tmp_path / "shared.sqlite")
    legacy = tmp_path / "old.csv"
    legacy.write_text("a,0.5,artist:a:0.5\nb,0.6,artist:b:0.6\n", encoding="utf-8")

    script = f"""
import sys
sys.path.insert(0, {parent!r})
from ComfyUI_RollingArtist.ra_core.db import RollingArtistDB
db = RollingArtistDB({db_path!r})
print(db.import_legacy_csv({str(legacy)!r}))
"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    for _ in range(2):
        done = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=120,
        )
        assert done.returncode == 0, done.stderr
    assert RollingArtistDB(db_path).count() == 2


# ----------------------------------------------------------------------
# 事务语义契约（问题 5、6 的回归防线）
# ----------------------------------------------------------------------
def test_record_inside_lock_participates_in_outer_transaction(tmp_path):
    """在 generation_lock 内 record，外层抛异常必须连同记录一起回滚。

    这是「判重 -> 生成 -> 记录」原子性的基础：若 record 自己另开事务提交，
    回滚就撤不掉已写入的记录，去重进度会被污染。
    """
    db = make_db(tmp_path)
    with pytest.raises(RuntimeError):
        with db.generation_lock():
            db.record(["a"], [0.5], "p")
            raise RuntimeError("模拟生成中途失败")
    assert db.count() == 0, "lock 内写入的记录未随外层事务回滚"
    db.close()


def test_record_standalone_autocommits(tmp_path):
    """独立调用 record（不在 lock 内）是隐式自动提交，不受外层事务保护。

    这个语义**是有意的**（供 import_legacy_csv 这类批量场景使用），
    但必须显式钉住，避免有人误以为"record 总是在事务里"。
    """
    db = make_db(tmp_path)
    assert db.record(["a"], [0.5], "p") is True
    assert db.count() == 1, "独立 record 应立即落盘"

    # 抛异常也撤不掉——它已经提交了
    with pytest.raises(RuntimeError):
        db.record(["b"], [0.5], "p")
        raise RuntimeError("外层失败")
    assert db.count() == 2, "独立 record 不应受外层异常影响"
    db.close()


def test_record_returns_true_for_duplicate_but_record_ex_distinguishes(tmp_path):
    """``record()`` 对重复组合返回 True（"非失败即成功"），``record_ex()`` 才能区分。

    这是两条互补的契约，都要钉住：

    - ``record()``：``True`` = 写入成功**或**已存在；``False`` = 写入失败。
      节点层用它判断"要不要记 ERROR 日志"，所以重复**不能**被当成失败。
    - ``record_ex()``：``True`` = 新增；``False`` = 已存在；``None`` = 写入失败。

    回归防线：早期 ``record()`` 的 docstring 说"重复时静默忽略"却又标 ``-> bool``，
    实现是 ``_insert(...) is not None``——重复（影响行数 0）也返回 ``True``。
    文档与实现存在字面矛盾，故补 ``record_ex()`` 把三态显式化。
    """
    db = make_db(tmp_path)
    assert db.record(["a"], [0.5], "p") is True
    assert db.record(["a"], [0.5], "p") is True, "重复组合不算失败，record() 应返回 True"
    assert db.count() == 1, "重复组合不应产生新行"

    # record_ex 如实三态
    assert db.record_ex(["a"], [0.5], "p") is False, "已存在应为 False"
    assert db.record_ex(["b"], [0.5], "p") is True, "新增应为 True"
    assert db.record_ex(["b"], [0.5], "p") is False
    db.close()


def test_close_then_reuse_silently_reconnects(tmp_path):
    """close() 之后再用会静默重连——这是有意行为，钉住以防被当成 bug 改掉。

    连接是每线程懒创建的，close() 只清掉当前线程的连接引用；
    后续 count() / is_tested() 会按需重建。因此 "close 后再用" 不报错。
    """
    from ComfyUI_RollingArtist.ra_core.db import close_all_dbs

    db = make_db(tmp_path)
    db.record(["a"], [0.5], "p")
    db.close()
    assert getattr(db._local, "conn", None) is None, "close() 后连接引用应被清空"

    # 静默重连：结果仍正确
    assert db.count() == 1
    assert getattr(db._local, "conn", None) is not None, "再次访问应重建连接"
    db.close()
    close_all_dbs()


def test_close_current_thread_is_the_canonical_name(tmp_path):
    """close_current_thread() 是新名字；close_all_threads() 保留为废弃别名。"""
    db = make_db(tmp_path)
    db.record(["a"], [0.5], "p")

    assert hasattr(db, "close_current_thread"), "缺少语义准确的新方法名"
    assert hasattr(db, "close_all_threads"), "旧名应保留为别名，避免破坏外部调用"

    # 新名与别名行为一致：都只关当前线程
    for method in ("close_current_thread", "close_all_threads"):
        db.close()
        db.record(["a"], [0.5], "p")  # 触发重连
        getattr(db, method)()
        assert getattr(db._local, "conn", None) is None, f"{method}() 未关闭当前线程连接"
    db.close()


def test_close_all_dbs_clears_process_cache(tmp_path):
    """close_all_dbs() 必须清空进程级缓存，使 get_db 重新构造实例。"""
    from ComfyUI_RollingArtist.ra_core.db import close_all_dbs, get_db

    path = str(tmp_path / "cached.sqlite")
    first = get_db(path)
    assert get_db(path) is first, "同一路径应命中缓存"

    close_all_dbs()
    second = get_db(path)
    assert second is not first, "close_all_dbs() 后应重新构造实例"
    close_all_dbs()
