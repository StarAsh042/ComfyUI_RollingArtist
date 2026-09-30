"""RollingArtist 全局常量与日志。

该模块集中管理路径、权重步长、规模保护阈值等常量，避免散落在业务代码中。
注意：本模块不依赖 ComfyUI，可独立导入与测试。
"""

import logging
import os
from typing import Final

# 节点目录（ra_core 的上一级），所有默认文件均相对于该目录
NODE_DIR: Final[str] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ARTIST_CSV: Final[str] = os.path.join(NODE_DIR, "danbooru_art_001.csv")
DEFAULT_CHARACTER_CSV: Final[str] = os.path.join(NODE_DIR, "danbooru_character_001.csv")
# 4.0.0 起记录统一存进 SQLite；该常量保留用于兼容旧版 CSV 记录文件
DEFAULT_TESTED_CSV: Final[str] = os.path.join(NODE_DIR, "tested_combinations.csv")
# 默认数据库：承载已测记录
DEFAULT_DB: Final[str] = os.path.join(NODE_DIR, "rollingartist.sqlite")
# 角色节点的默认数据库：与画师分开，互不干扰
DEFAULT_CHARACTER_DB: Final[str] = os.path.join(NODE_DIR, "rollingcharacter.sqlite")

# 记录类型标记：两个节点即使被指向同一个数据库，同名的画师与角色也不会互相冒充
KIND_ARTIST: Final[str] = "artist"
KIND_CHARACTER: Final[str] = "character"
# 旧版 CSV 记录文件在导入数据库后改名留底用的后缀
LEGACY_BACKUP_SUFFIX: Final[str] = ".bak"

# 权重步长（与节点界面的 step 保持一致）
WEIGHT_STEP: Final[float] = 0.1
# 权重保留的小数位数 / 整数化因子，用整数单位运算避免浮点累计误差
WEIGHT_DECIMALS: Final[int] = 1
WEIGHT_SCALE: Final[int] = 10 ** WEIGHT_DECIMALS

# CSV 读取使用 utf-8-sig，自动兼容带 BOM 的文件；写入统一使用 utf-8
CSV_READ_ENCODING: Final[str] = "utf-8-sig"
CSV_WRITE_ENCODING: Final[str] = "utf-8"

# 统一日志器：消息中自带 [RollingArtist] 前缀，兼容 ComfyUI 的日志配置
LOGGER: Final[logging.Logger] = logging.getLogger("RollingArtist")
