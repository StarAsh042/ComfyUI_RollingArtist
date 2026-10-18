"""ComfyUI 启动钩子：清理上次运行留下的默认记录文件，保证每次启动都是干净的。

ComfyUI 在加载自定义节点之前，以“单文件模块”的方式执行本文件
（``main.py`` 的 ``execute_prestartup_script``，用 ``spec_from_file_location`` 加载），
此时节点目录还不在 ``sys.path`` 上，因此先把节点目录加进去，
再按包的形式导入 ``ra_core`` 复用清理实现（``ra_core/startup.py``）。
"""

import os
import sys

NODE_DIR = os.path.dirname(os.path.abspath(__file__))
if NODE_DIR not in sys.path:
    sys.path.insert(0, NODE_DIR)

try:
    from ra_core.startup import cleanup_default_runtime_files

    _removed = cleanup_default_runtime_files()
except Exception as error:  # 启动清理绝不能影响 ComfyUI 启动
    print(f"[RollingArtist] 启动清理失败（已忽略）: {error}")
else:
    if _removed:
        print("[RollingArtist] 已清理上次运行的记录文件: " + ", ".join(_removed))
