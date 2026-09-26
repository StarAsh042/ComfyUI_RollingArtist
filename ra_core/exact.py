"""Exact 模式（artist_count == 1）的 (艺术家, 权重) 组合池。

语义与原实现保持一致：
1. 组合池持久化在 ``*_remaining.csv``，可从任意中断处继续
2. 每次随机取一条并移除，在穷举完之前绝不重复
3. 池为空时按当前配置重建（已记录的组合会被剔除）
4. 全部穷举后返回 None（节点据此输出 ALL_COMBINATIONS_TESTED）

性能改进：
- 组合池在进程内缓存（按文件签名失效），不再每次执行都全量读取
- 落盘使用 swap-pop 删除 + 临时文件原子替换，避免半截文件
- 组合总数超过 ``EXACT_COMBOS_LIMIT`` 时不物化文件，改为“随机采样 + 去重”，
  避免大型 CSV（如 danbooru_art_full.csv）生成数百 MB 的中间文件
"""

import os
import random
import threading
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from .constants import (
    EXACT_COMBOS_LIMIT,
    EXACT_COMBOS_WARN,
    EXACT_FILE_LIMIT_BYTES,
    LOGGER,
)
from .storage import read_rows, write_rows
from .weights import dedup_key

__all__ = ["ExactPool", "get_exact_pool", "remaining_path_for", "take_exact"]

Combo = Tuple[str, float]


def remaining_path_for(tested_path: str) -> str:
    """由已测 CSV 路径派生剩余组合 CSV 路径。"""
    base, extension = os.path.splitext(tested_path)
    return f"{base}_remaining{extension or '.csv'}"


class ExactPool:
    """``*_remaining.csv`` 组合池：进程内共享、惰性缓存、原子落盘。"""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._combos: Optional[List[Combo]] = None
        self._signature: Optional[Tuple[int, int]] = None

    # ------------------------------------------------------------------
    def take(self, rng: random.Random, build: Callable[[], List[Combo]],
             exhausted: bool = False) -> Optional[Combo]:
        """随机取一条组合并移除。

        参数:
            rng: 随机数生成器
            build: 池为空时用于重建组合池的回调（已剔除已测组合）
            exhausted: 已知全部组合都测过时直接返回 None，跳过重建

        返回:
            ``(artist, weight)``；没有任何可用组合时返回 None
        """
        with self._lock:
            combos = self._load_locked()
            if not combos:
                if combos == [] and exhausted:
                    return None
                combos = list(build())
                if not combos:
                    if self._signature is not None:
                        self._save_locked([])
                    return None

            index = rng.randrange(len(combos))
            combo = combos[index]
            combos[index] = combos[-1]  # swap-pop：O(1) 删除，无需整体重建
            combos.pop()
            self._save_locked(combos)
            return combo

    def reset(self) -> None:
        """丢弃内存缓存（文件被外部修改后使用）。"""
        with self._lock:
            self._combos = None
            self._signature = None

    # ------------------------------------------------------------------
    def _current_signature(self) -> Optional[Tuple[int, int]]:
        try:
            info = os.stat(self.path)
        except OSError:
            return None
        return (info.st_mtime_ns, info.st_size)

    def _load_locked(self) -> Optional[List[Combo]]:
        """读取组合池；文件不存在返回 None，存在但为空返回 []。"""
        signature = self._current_signature()
        if signature is None:
            self._combos = None
            self._signature = None
            return None
        if self._combos is not None and signature == self._signature:
            return self._combos
        if signature[1] > EXACT_FILE_LIMIT_BYTES:
            raise ValueError(
                f"[RollingArtist] 剩余组合文件过大（{signature[1] / 1048576:.1f} MB）: {self.path}\n"
                "请删除该文件（会自动按当前配置重建），或缩小艺术家人数 / 权重范围。"
            )

        combos: List[Combo] = []
        for row in read_rows(self.path):
            if len(row) < 2:
                continue
            try:
                combos.append((str(row[0]).strip(), round(float(row[1]), 1)))
            except ValueError:
                continue
        self._combos = combos
        self._signature = signature
        return combos

    def _save_locked(self, combos: List[Combo]) -> None:
        rows = [[artist, f"{weight}"] for artist, weight in combos]
        if write_rows(self.path, rows):
            # 内存状态与磁盘保持一致，后续执行无需重新读取
            self._combos = combos
            self._signature = self._current_signature()
        # 写入失败时保留内存状态：磁盘仍是上一次的完整内容，不会损坏


_POOLS: Dict[str, ExactPool] = {}
_POOLS_LOCK = threading.RLock()


def get_exact_pool(path: str) -> ExactPool:
    """按路径获取进程级共享的组合池。"""
    key = os.path.normcase(os.path.abspath(path))
    with _POOLS_LOCK:
        pool = _POOLS.get(key)
        if pool is None:
            pool = ExactPool(path)
            _POOLS[key] = pool
        return pool


def _sample_untested(rng: random.Random, artist_pool: Sequence[str], grid: Sequence[float],
                     tested_keys: Set[str], max_attempts: int) -> Optional[Combo]:
    """组合总量过大时的降级方案：随机采样并跳过已测组合。"""
    attempts = max(1, int(max_attempts or 1))
    for _ in range(attempts):
        artist = artist_pool[rng.randrange(len(artist_pool))]
        weight = grid[rng.randrange(len(grid))]
        if dedup_key("full_prompt", [artist], [weight]) not in tested_keys:
            return artist, weight
    LOGGER.warning(
        "[RollingArtist] Exact 模式采样 %d 次均命中已测组合（组合总量过大，无法精确判定是否穷举），按已穷举处理",
        attempts,
    )
    return None


def take_exact(remaining_path: str, rng: random.Random, artist_pool: Sequence[str],
               grid: Sequence[float], tested_keys: Set[str],
               max_attempts: int = 10) -> Optional[Combo]:
    """Exact 模式主入口：返回一个未测过的 (艺术家, 权重) 组合。

    返回 None 表示所有组合都已被测试（或采样降级后判定为已穷举）。
    """
    universe = len(artist_pool) * len(grid)
    if universe <= 0:
        return None

    if universe > EXACT_COMBOS_LIMIT:
        LOGGER.warning(
            "[RollingArtist] Exact 组合总数 %d 超过物化上限 %d，改用随机采样去重（不再生成剩余组合文件）",
            universe, EXACT_COMBOS_LIMIT,
        )
        return _sample_untested(rng, artist_pool, grid, tested_keys, max_attempts)

    if universe > EXACT_COMBOS_WARN:
        LOGGER.info("[RollingArtist] Exact 模式组合数为 %d，剩余组合文件可能较大", universe)

    pool = get_exact_pool(remaining_path)

    def build() -> List[Combo]:
        return [
            (artist, weight)
            for artist in artist_pool
            for weight in grid
            if dedup_key("full_prompt", [artist], [weight]) not in tested_keys
        ]

    return pool.take(rng, build, exhausted=len(tested_keys) >= universe)
