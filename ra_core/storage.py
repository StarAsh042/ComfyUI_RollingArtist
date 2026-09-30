"""旧版 CSV 文件的读取与备份工具。

4.0.0 起运行期数据（已测记录 / 穷举组合池）改由 :mod:`ra_core.db` 管理，
本模块只保留两类仍在使用的功能：

- 读取旧版 CSV 记录，供一次性导入数据库（``read_rows`` / ``parse_tested_row``）
- 导入完成后把旧文件改名留底（``backup_file``）

刻意不再提供 CSV 写入能力：写入只有一个入口（数据库），避免出现两套记录来源。
"""

import csv
import os
from typing import List, Optional, Sequence, Tuple

from .constants import CSV_READ_ENCODING, LEGACY_BACKUP_SUFFIX, LOGGER
from .weights import parse_prompt, parse_weight_list

__all__ = [
    "ensure_parent_dir",
    "read_rows",
    "parse_tested_row",
    "backup_file",
]


# ----------------------------------------------------------------------
# 基础 IO
# ----------------------------------------------------------------------
def ensure_parent_dir(path: str) -> None:
    """确保父目录存在（路径没有目录部分时忽略）。"""
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)


def read_rows(path: str) -> List[List[str]]:
    """读取 CSV，返回非空行列表；文件不存在或读取失败时返回空列表。"""
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding=CSV_READ_ENCODING, newline="") as handle:
            return [row for row in csv.reader(handle) if row]
    except (OSError, csv.Error, UnicodeDecodeError) as error:
        LOGGER.error("[RollingArtist] CSV 读取失败: %s -> %s", path, error)
        return []


def backup_file(path: str, suffix: str = LEGACY_BACKUP_SUFFIX) -> Optional[str]:
    """把文件改名留底（``xxx.csv`` -> ``xxx.csv.bak``），返回新路径。

    - 目标名已存在时不覆盖，自动追加序号（``.bak1``、``.bak2`` …）
    - 失败只记日志并返回 None，绝不打断生成流程
    """
    if not os.path.isfile(path):
        return None
    candidate = f"{path}{suffix}"
    index = 1
    while os.path.exists(candidate):
        candidate = f"{path}{suffix}{index}"
        index += 1
    try:
        os.replace(path, candidate)
    except OSError as error:
        LOGGER.warning("[RollingArtist] 旧文件改名失败（已跳过）: %s -> %s", path, error)
        return None
    return candidate


def parse_tested_row(row: Sequence[str]) -> Tuple[List[str], List[float]]:
    """解析旧版已测 CSV 的一行 -> (艺术家列表, 权重列表)。

    行格式为 ``[艺术家(| 分隔), 权重(, 分隔), prompt]``；
    兼容更早的「仅含提示词」单列格式。
    """
    artists: List[str] = []
    weights: List[float] = []
    if not row:
        return artists, weights

    if len(row) >= 2:
        artists = [name.strip() for name in str(row[0]).split("|") if name.strip()]
        weights = parse_weight_list(row[1])
    elif str(row[0]).strip():
        artists, weights = parse_prompt(row[0])

    if artists and len(weights) != len(artists):
        weights = [weights[index] if index < len(weights) else 1.0 for index in range(len(artists))]
    return artists, weights
