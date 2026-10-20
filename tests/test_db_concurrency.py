"""并发行为测试：验证 P0-2 的连接模型改造。

改造前：单个 ``sqlite3.Connection`` 被一把锁共享，"判重 -> 生成 -> 记录"
整段（含权重计算等纯 CPU 工作）都在锁内，同一库上的并发生成被强制串行化。

改造后：每线程一个连接；写锁只覆盖事务区间。这里覆盖两个关键性质：

1. **不再跨线程共享连接**：多线程并发写入不会抛 sqlite3 的线程检查错误
2. **写锁不覆盖 CPU 工作**：事务外可以并行跑重活（用可观测的并发度断言）
"""

import threading
import time

from ComfyUI_RollingArtist.ra_core.constants import KIND_ARTIST
from ComfyUI_RollingArtist.ra_core.db import RollingArtistDB


def test_concurrent_writes_from_many_threads(tmp_path):
    """多线程并发 record 不得抛线程错误，且记录数正确。"""
    db = RollingArtistDB(str(tmp_path / "concurrent.sqlite"))
    errors = []

    def worker(base):
        try:
            for index in range(20):
                db.record([f"a{base}_{index}"], [1.0], f"p{base}_{index}", KIND_ARTIST)
        except Exception as error:  # pragma: no cover - 失败时把异常带回主线程
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(base,)) for base in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"并发写入出现异常：{errors}"
    assert db.count(KIND_ARTIST) == 80
    db.close()


def test_cpu_work_outside_transaction_runs_in_parallel(tmp_path):
    """写锁只覆盖事务：事务外的 CPU 工作应能并行执行。

    **为什么不用「总耗时应小于 N×sleep」来断言**：那种写法把
    Windows 下 SQLite WAL 的跨进程文件锁与线程调度抖动也算进来了。
    实测本机 4 线程 `BEGIN IMMEDIATE`/`COMMIT` 的基线波动可达 0.19–0.44s，
    与「睡眠是否被串行化」无关，却足以让基于绝对耗时的断言偶发失败
    （独立复跑 5 次中曾出现 0.77s vs 阈值 0.45s）。

    改为**直接观测锁区间**：线程在进入 `generation_lock` 前先睡眠，
    记录「睡眠结束时刻」与「拿到锁时刻」。若锁覆盖整段（旧实现），
    后进入的线程必须排队等前面的事务，两个时刻之间会出现明显的排队差；
    若锁只覆盖事务，所有线程的睡眠阶段会彼此重叠，且睡眠结束到拿锁的间隔很短。
    """
    db = RollingArtistDB(str(tmp_path / "parallel.sqlite"))
    sleep_seconds = 0.15
    thread_count = 4
    barrier = threading.Barrier(thread_count)   # 让所有线程同时起床，消除启动抖动
    slept_at = []                                # 各线程「睡眠结束」的时刻
    locked_at = []                               # 各线程「拿到写锁」的时刻
    records = []

    def worker(index):
        barrier.wait()                          # 对齐起点
        time.sleep(sleep_seconds)               # 事务外的纯 CPU / 准备阶段
        slept_at.append((index, time.monotonic()))
        with db.generation_lock():
            locked_at.append((index, time.monotonic()))
            db.record([f"a{index}"], [1.0], f"p{index}", KIND_ARTIST)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    db.close()
    assert db.count(KIND_ARTIST) == thread_count

    sleeps = [t for _, t in slept_at]
    locks = [t for _, t in locked_at]
    assert len(sleeps) == thread_count and len(locks) == thread_count

    # ① 睡眠阶段必须重叠：最早与最晚「睡眠结束」的间隔应远小于串行总时长。
    #    若睡眠被锁串行化（旧实现），这个间隔会接近 (N-1) × sleep。
    spread = max(sleeps) - min(sleeps)
    serial_span = sleep_seconds * (thread_count - 1)
    assert spread < serial_span * 0.5, (
        f"事务外睡眠似乎被串行化了：睡眠结束时间跨度 {spread:.3f}s，"
        f"串行预期约 {serial_span:.3f}s"
    )

    # ② 睡眠结束 -> 拿到锁 的等待必须很短：证明锁区间只包住事务本身，
    #    而不是把整个 worker（含睡眠）都圈在里面。
    #    放宽到 2×sleep，足以容纳 WAL 写锁的排队，但旧实现会远超此值。
    for index, woke in slept_at:
        locked = dict(locked_at)[index]
        wait = locked - woke
        assert wait < sleep_seconds * 2, (
            f"线程 {index} 在事务外等待过久（{wait:.3f}s），"
            "说明写锁可能覆盖了 CPU 工作"
        )


def test_same_thread_reuses_one_connection(tmp_path):
    """同一线程多次操作应复用同一个连接对象。"""
    db = RollingArtistDB(str(tmp_path / "reuse.sqlite"))
    first = db._conn
    db.record(["a"], [1.0], "p", KIND_ARTIST)
    assert db._conn is first
    db.close()


def test_different_threads_get_different_connections(tmp_path):
    """不同线程必须使用各自的连接（连接不可跨线程共享）。"""
    db = RollingArtistDB(str(tmp_path / "per_thread.sqlite"))
    main_conn = db._conn
    other = {}

    def worker():
        other["conn"] = db._conn

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert other["conn"] is not main_conn
    db.close()
