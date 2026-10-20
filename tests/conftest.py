"""pytest 公共配置与 fixture。

两点约定：

1. 把 ``custom_nodes`` 目录加进 ``sys.path``，这样测试可以按包路径导入
   （``ComfyUI_RollingArtist.ra_core...``），与 ComfyUI 加载节点的方式一致。
2. **只使用包路径导入**：如果同时再用顶层 ``ra_core`` 导入，Python 会加载出第二份
   同名模块，连带出现第二份进程级单例（两个数据库连接指向同一文件），
   测试之间会互相抢锁。``tests/test_no_double_import.py`` 对此有断言。

本套测试不依赖 ComfyUI：节点文件只 import 标准库与 ``ra_core``，
因此节点层冒烟测试可以直接跑，不需要启动 ComfyUI 或做桩。
"""

import os
import sys

import pytest

NODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENT_DIR = os.path.dirname(NODE_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)


@pytest.fixture(autouse=True)
def _reset_db_registry():
    """每个测试结束后关闭并清空数据库连接缓存。

    Windows 上被占用的文件无法删除，不关闭会让 pytest 清理临时目录时报错。
    """
    yield
    from ComfyUI_RollingArtist.ra_core.db import close_all_dbs

    close_all_dbs()


@pytest.fixture
def artists_csv(tmp_path):
    """写一个内容可控的小 CSV，返回其路径。"""
    def _make(names, header=None, delimiter="\n"):
        path = tmp_path / "artists.csv"
        lines = ([header] if header else []) + [str(name) for name in names]
        path.write_text(delimiter.join(lines) + delimiter, encoding="utf-8")
        return str(path)

    return _make
