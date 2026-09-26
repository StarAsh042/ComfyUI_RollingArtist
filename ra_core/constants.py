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
DEFAULT_TESTED_CSV: Final[str] = os.path.join(NODE_DIR, "tested_combinations.csv")

# 权重枚举步长（Exact 模式与界面 step 保持一致）
WEIGHT_STEP: Final[float] = 0.1
# 权重保留的小数位数 / 整数化因子，用整数单位运算避免浮点累计误差
WEIGHT_DECIMALS: Final[int] = 1
WEIGHT_SCALE: Final[int] = 10 ** WEIGHT_DECIMALS

# Exact 模式：组合数超过该值时给出提示（不阻断）
EXACT_COMBOS_WARN: Final[int] = 100000
# Exact 模式：组合数超过该值时不再物化组合池，改为随机采样 + 去重
EXACT_COMBOS_LIMIT: Final[int] = 1000000
# Exact 模式：遗留 remaining 文件超过该体积时拒绝加载（避免 OOM）
EXACT_FILE_LIMIT_BYTES: Final[int] = 64 * 1024 * 1024

# CSV 读取使用 utf-8-sig，自动兼容带 BOM 的文件；写入统一使用 utf-8
CSV_READ_ENCODING: Final[str] = "utf-8-sig"
CSV_WRITE_ENCODING: Final[str] = "utf-8"

# 统一日志器：消息中自带 [RollingArtist] 前缀，兼容 ComfyUI 的日志配置
LOGGER: Final[logging.Logger] = logging.getLogger("RollingArtist")
