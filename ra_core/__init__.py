"""RollingArtist / RollingCharacter 核心实现（不依赖 ComfyUI，可独立导入与测试）。

模块划分：
- ``constants``:  路径、权重步长、规模阈值、记录类型标记与日志器
- ``params``:     节点入参的容错转换（两个节点共用）
- ``artists``:    艺术家 CSV 解析 / 加载缓存 / 索引化抽样
- ``characters``: 角色数据解析（trigger 拆解）、作品筛选、外观标签合并
- ``weights``:    权重分配、提示词构建解析、去重键
- ``db``:         SQLite 记录库（已测记录 + 穷举组合池）
- ``storage``:    旧版 CSV 读取与备份（供导入与留底使用）
- ``exact``:      Exact 模式组合池的抽取逻辑
- ``startup``:    启动期清理默认运行期文件（由 prestartup_script.py 调用）
"""

from .artists import (
    ArtistRepository,
    clear_repositories,
    compute_top_count,
    get_artist_repository,
    get_repository,
    normalize_column_spec,
    parse_artist_rows,
    resolve_layout,
    select_artists,
    split_names,
)
from .characters import (
    CharacterRecord,
    build_character_prompt,
    build_payload,
    build_records,
    clear_records_cache,
    filter_by_copyright,
    load_records,
    parse_copyright_filter,
    split_core_tags,
    split_tags,
    unique_tags,
)
from .constants import (
    DEFAULT_ARTIST_CSV,
    DEFAULT_CHARACTER_CSV,
    DEFAULT_CHARACTER_DB,
    DEFAULT_DB,
    DEFAULT_TESTED_CSV,
    EXACT_COMBOS_LIMIT,
    EXACT_COMBOS_WARN,
    KIND_ARTIST,
    KIND_CHARACTER,
    LEGACY_BACKUP_SUFFIX,
    LOGGER,
    NODE_DIR,
    WEIGHT_STEP,
)
from .db import RollingArtistDB, close_all_dbs, get_db, pool_key_for, resolve_db_path
from .exact import remaining_count, remaining_path_for, take_exact
from .params import as_bool, as_float, as_int, as_text
from .startup import cleanup_default_runtime_files, default_runtime_files
from .storage import backup_file, parse_tested_row, read_rows
from .weights import (
    DEDUP_MODES,
    GENERATION_MODES,
    WEIGHT_CURVES,
    build_prompt,
    dedup_key,
    escape_key_part,
    generate_weights,
    normalize_dedup_mode,
    normalize_generation_mode,
    normalize_weight_curve,
    parse_prompt,
    weight_grid,
)

__all__ = [
    # artists
    "ArtistRepository",
    "clear_repositories",
    "compute_top_count",
    "get_artist_repository",
    "get_repository",
    "normalize_column_spec",
    "parse_artist_rows",
    "resolve_layout",
    "select_artists",
    "split_names",
    # characters
    "CharacterRecord",
    "build_character_prompt",
    "build_payload",
    "build_records",
    "clear_records_cache",
    "filter_by_copyright",
    "load_records",
    "parse_copyright_filter",
    "split_core_tags",
    "split_tags",
    "unique_tags",
    # constants
    "DEFAULT_ARTIST_CSV",
    "DEFAULT_CHARACTER_CSV",
    "DEFAULT_CHARACTER_DB",
    "DEFAULT_DB",
    "DEFAULT_TESTED_CSV",
    "EXACT_COMBOS_LIMIT",
    "EXACT_COMBOS_WARN",
    "KIND_ARTIST",
    "KIND_CHARACTER",
    "LEGACY_BACKUP_SUFFIX",
    "LOGGER",
    "NODE_DIR",
    "WEIGHT_STEP",
    # db / exact
    "RollingArtistDB",
    "close_all_dbs",
    "get_db",
    "pool_key_for",
    "resolve_db_path",
    "remaining_count",
    "remaining_path_for",
    "take_exact",
    # params / startup
    "as_bool",
    "as_float",
    "as_int",
    "as_text",
    "cleanup_default_runtime_files",
    "default_runtime_files",
    "backup_file",
    "parse_tested_row",
    "read_rows",
    # weights
    "DEDUP_MODES",
    "GENERATION_MODES",
    "WEIGHT_CURVES",
    "build_prompt",
    "dedup_key",
    "escape_key_part",
    "generate_weights",
    "normalize_dedup_mode",
    "normalize_generation_mode",
    "normalize_weight_curve",
    "parse_prompt",
    "weight_grid",
]
