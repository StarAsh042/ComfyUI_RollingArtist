"""守卫测试：``ra_core`` 只能以包路径导入一次。

如果测试里同时写了 ``import ra_core``（顶层）与 ``ComfyUI_RollingArtist.ra_core``，
Python 会把同一份源码加载成两个不同模块，连带出现两份进程级单例：
两个数据库连接指向同一个文件，测试之间会互相抢写锁、出现难以排查的超时。
这里把它钉死，避免以后有人顺手改导入方式。
"""

import sys

import ComfyUI_RollingArtist.ra_core.db as packaged_db


def test_ra_core_is_not_imported_as_top_level_module():
    assert "ra_core" not in sys.modules, (
        "检测到顶层导入 ra_core；请统一改用 ComfyUI_RollingArtist.ra_core，"
        "否则会出现两份单例（数据库连接互相抢锁）"
    )
    assert "ra_core.db" not in sys.modules


def test_node_and_core_share_the_same_module_instance():
    from ComfyUI_RollingArtist import RollingArtist as node_module

    assert node_module.get_db is packaged_db.get_db
