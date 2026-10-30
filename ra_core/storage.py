"""文件指纹与旧版 CSV 记录行的解析工具。

4.0.0 起运行期数据（已测记录）改由 :mod:`ra_core.db` 管理，本模块只保留
三件仍在使用的功能：

- :func:`file_fingerprint` —— 供节点的 ``IS_CHANGED`` 把 CSV 文件签名纳入缓存键
- :func:`ensure_parent_dir` —— 建库前确保父目录存在
- :func:`parse_tested_row` —— 解析旧版 CSV 记录行，供
  :meth:`ra_core.db.RollingArtistDB.import_legacy_csv` 使用

刻意不再提供 CSV 写入能力：写入只有一个入口（数据库），避免出现两套记录来源。
"""

import os
from typing import List, Sequence, Tuple

from .weights import parse_prompt, parse_weight_list

__all__ = [
    "ensure_parent_dir",
    "file_fingerprint",
    "parse_tested_row",
]


# ----------------------------------------------------------------------
# 基础 IO
# ----------------------------------------------------------------------
def ensure_parent_dir(path: str) -> None:
    """确保父目录存在（路径没有目录部分时忽略）。"""
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)


def file_fingerprint(path: str) -> str:
    """文件指纹：``规范化路径|mtime_ns|size``；文件不存在时返回 ``missing:<路径>``。

    供节点的 ``IS_CHANGED`` 使用：ComfyUI 只按输入参数判断缓存是否命中，
    节点读的是外部 CSV，参数不变而文件被替换时会命中旧结果。把文件签名
    纳入指纹后，CSV 内容一变就重算。

    只读 ``os.stat``，不打开文件，因此可以放在每次执行前的缓存判断里。
    """
    normalized = os.path.normcase(os.path.abspath(path))
    try:
        info = os.stat(normalized)
    except OSError:
        return f"missing:{normalized}"
    return f"{normalized}|{info.st_mtime_ns}|{info.st_size}"


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
