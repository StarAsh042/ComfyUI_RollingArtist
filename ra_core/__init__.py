"""RollingArtist 核心实现（不依赖 ComfyUI，可独立导入与测试）。

模块划分：
- ``constants``: 路径、步长、规模阈值与日志器
- ``artists``:   艺术家 CSV 解析 / 加载缓存 / 索引化抽样
- ``weights``:   权重分配、提示词构建解析、去重键
- ``storage``:   CSV 原子读写、已测组合的增量存储
- ``exact``:     Exact 模式组合池
"""

from .artists import (
    ArtistRepository,
    compute_top_count,
    get_artist_repository,
    parse_artist_rows,
    select_artists,
    split_names,
)
from .constants import (
    DEFAULT_ARTIST_CSV,
    DEFAULT_TESTED_CSV,
    EXACT_COMBOS_LIMIT,
    EXACT_COMBOS_WARN,
    LOGGER,
    NODE_DIR,
    WEIGHT_STEP,
)
from .exact import ExactPool, get_exact_pool, remaining_path_for, take_exact
from .storage import TestedStore, get_tested_store, parse_tested_row
from .weights import (
    DEDUP_MODES,
    build_prompt,
    dedup_key,
    generate_weights,
    normalize_dedup_mode,
    parse_prompt,
    weight_grid,
)

__all__ = [
    "ArtistRepository",
    "compute_top_count",
    "get_artist_repository",
    "parse_artist_rows",
    "select_artists",
    "split_names",
    "DEFAULT_ARTIST_CSV",
    "DEFAULT_TESTED_CSV",
    "EXACT_COMBOS_LIMIT",
    "EXACT_COMBOS_WARN",
    "LOGGER",
    "NODE_DIR",
    "WEIGHT_STEP",
    "ExactPool",
    "get_exact_pool",
    "remaining_path_for",
    "take_exact",
    "TestedStore",
    "get_tested_store",
    "parse_tested_row",
    "DEDUP_MODES",
    "build_prompt",
    "dedup_key",
    "generate_weights",
    "normalize_dedup_mode",
    "parse_prompt",
    "weight_grid",
]
