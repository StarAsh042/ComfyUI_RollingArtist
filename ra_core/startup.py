"""启动期清理：删除节点目录下的默认运行期文件。

ComfyUI 在导入任何自定义节点之前，会执行 ``custom_nodes/<节点>/prestartup_script.py``
（见 ComfyUI ``main.py`` 的 ``execute_prestartup_script``），本模块提供该脚本调用的实现，
保证每次启动 ComfyUI 都不带着上次运行留下的记录。

清理范围**只限默认路径**：

- 默认数据库 ``rollingartist.sqlite`` 与 ``rollingcharacter.sqlite``
  （各含 WAL 模式的 ``-wal`` / ``-shm`` 附带文件）
- 旧版 CSV 记录 ``tested_combinations.csv`` 与 ``tested_combinations_remaining.csv``
  （兼容从 3.2.x 升级上来的残留文件）

用户显式指定的数据库路径 / 旧记录路径一律不动，那代表用户主动选择要跨会话保留的数据。
"""

import os
from typing import List

from .constants import DEFAULT_CHARACTER_DB, DEFAULT_DB, DEFAULT_TESTED_CSV, LOGGER

__all__ = ["default_runtime_files", "cleanup_default_runtime_files"]


def _legacy_remaining_path(tested_path: str) -> str:
    """由旧版已测 CSV 路径派生 ``*_remaining.csv`` 路径（仅用于清理历史残留）。"""
    base, extension = os.path.splitext(tested_path)
    return f"{base}_remaining{extension or '.csv'}"


def default_runtime_files() -> List[str]:
    """默认路径下的运行期文件：两份数据库 + WAL 附带文件 + 旧版两份 CSV。"""
    files: List[str] = []
    for db_path in (DEFAULT_DB, DEFAULT_CHARACTER_DB):
        files.extend([db_path, f"{db_path}-wal", f"{db_path}-shm"])
    files.extend([DEFAULT_TESTED_CSV, _legacy_remaining_path(DEFAULT_TESTED_CSV)])
    return files


def cleanup_default_runtime_files() -> List[str]:
    """删除默认路径下的运行期文件，返回实际删除的路径列表。

    - 文件不存在：跳过（不算失败）
    - 删除失败（占用、权限等）：只记 WARNING，绝不抛异常影响 ComfyUI 启动
    """
    removed: List[str] = []
    for path in default_runtime_files():
        try:
            os.remove(path)
        except FileNotFoundError:
            continue
        except OSError as error:
            LOGGER.warning("[RollingArtist] 启动清理失败，未能删除 %s: %s", path, error)
            continue
        removed.append(path)
    return removed
