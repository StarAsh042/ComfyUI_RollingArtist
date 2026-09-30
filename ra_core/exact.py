"""Exact 模式（artist_count == 1）的 (艺术家, 权重) 组合池。

语义与原实现保持一致：
1. 组合池可从任意中断处继续（现在持久化在 SQLite 里）
2. 每次随机取一条并移除，在穷举完之前绝不重复
3. 池为空时按当前配置重建（已记录的组合会被剔除）
4. 全部穷举后返回 None（节点据此输出 ALL_COMBINATIONS_TESTED）

相对旧版（``*_remaining.csv``）的改进：
- 抽取组合只删一行，不再像旧实现那样每次把剩余组合整体重写回磁盘
- 组合池在数据库里，不再有「遗留文件超过 64MB 就报错」的问题
- 构建时的「全集减去已测」在 SQL 里完成，不再把上百万条组合物化成 Python 列表
- 池键由「艺术家池 + 权重网格」派生：配置一变自动换新池，
  等价于旧版「删掉 *_remaining.csv 会按当前配置重建」，且不再重复构建空池
- 组合总量超过 ``EXACT_COMBOS_LIMIT`` 时仍按老规则降级为「随机采样 + 去重」
"""

import os
import random
from typing import Optional, Sequence, Tuple

from .constants import EXACT_COMBOS_LIMIT, EXACT_COMBOS_WARN, KIND_ARTIST, LOGGER
from .db import RollingArtistDB, pool_key_for
from .weights import escape_key_part

__all__ = ["take_exact", "remaining_count", "remaining_path_for"]

Combo = Tuple[str, float]


def remaining_path_for(tested_path: str) -> str:
    """由旧版已测 CSV 路径派生 ``*_remaining.csv`` 路径（仅用于旧文件备份/清理）。"""
    base, extension = os.path.splitext(tested_path)
    return f"{base}_remaining{extension or '.csv'}"


def remaining_count(db: RollingArtistDB, artist_pool: Sequence[str],
                    grid: Sequence[float], kind: str = KIND_ARTIST) -> int:
    """组合池里还剩多少条组合。

    降级采样模式（组合总量过大）下没有池，返回 0 表示「不适用」。
    """
    if len(artist_pool) * len(grid) > EXACT_COMBOS_LIMIT:
        return 0
    return db.pool_count(pool_key_for(artist_pool, grid))


def take_exact(db: RollingArtistDB, rng: random.Random, artist_pool: Sequence[str],
               grid: Sequence[float], max_attempts: int = 10,
               kind: str = KIND_ARTIST) -> Optional[Combo]:
    """返回一个未测过的 ``(艺术家, 权重)`` 组合。

    返回 None 表示所有组合都已被测试（或采样降级后判定为已穷举）。
    调用方需在 ``db.generation_lock()`` 事务内调用，保证「取组合 + 记录」原子。
    """
    universe = len(artist_pool) * len(grid)
    if universe <= 0:
        return None

    if universe > EXACT_COMBOS_LIMIT:
        LOGGER.warning(
            "[RollingArtist] Exact 组合总数 %d 超过物化上限 %d，改用随机采样去重（不建立组合池）",
            universe, EXACT_COMBOS_LIMIT,
        )
        return _sample_untested(db, rng, artist_pool, grid, max_attempts, kind)

    if universe > EXACT_COMBOS_WARN:
        LOGGER.info("[RollingArtist] Exact 模式组合数为 %d，组合池较大", universe)

    pool_key = pool_key_for(artist_pool, grid)
    remaining = db.pool_count(pool_key)
    if remaining == 0:
        if db.pool_built(pool_key):
            # 池建过且已空 = 组合全部测过：直接返回，不再重算一遍全集
            return None
        escaped = [escape_key_part(name) for name in artist_pool]
        remaining = db.pool_build(pool_key, universe, artist_pool, escaped, grid, kind)
        if remaining == 0:
            return None

    return db.pool_take(pool_key, rng.randrange(remaining))


def _sample_untested(db: RollingArtistDB, rng: random.Random,
                     artist_pool: Sequence[str], grid: Sequence[float],
                     max_attempts: int, kind: str = KIND_ARTIST) -> Optional[Combo]:
    """组合总量过大时的降级方案：随机采样并跳过已测组合。"""
    attempts = max(1, int(max_attempts or 1))
    for _ in range(attempts):
        artist = artist_pool[rng.randrange(len(artist_pool))]
        weight = grid[rng.randrange(len(grid))]
        if not db.is_tested("full_prompt", [artist], [weight], kind):
            return artist, weight
    LOGGER.warning(
        "[RollingArtist] Exact 模式采样 %d 次均命中已测组合（组合总量过大，无法精确判定是否穷举），按已穷举处理",
        attempts,
    )
    return None
