"""启动期清理：删除节点目录下的默认运行期记录文件。

ComfyUI 在导入任何自定义节点之前，会执行 ``custom_nodes/<节点>/prestartup_script.py``
（见 ComfyUI ``main.py`` 的 ``execute_prestartup_script``），本模块提供该脚本调用的实现，
保证每次启动 ComfyUI 都不带着上次运行留下的记录。

清理范围**只限默认路径**（``DEFAULT_TESTED_CSV`` 与由它派生的 ``*_remaining.csv``）：
用户显式指定的 ``tested_csv_path`` 一律不动，那代表用户主动选择要跨会话保留的记录文件。
"""

import os
from typing import List

from .constants import DEFAULT_TESTED_CSV, LOGGER
from .exact import remaining_path_for

__all__ = ["default_runtime_files", "cleanup_default_runtime_files"]


def default_runtime_files() -> List[str]:
    """默认路径下的运行期文件：已测组合记录 + Exact 模式的剩余组合池。"""
    return [DEFAULT_TESTED_CSV, remaining_path_for(DEFAULT_TESTED_CSV)]


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
