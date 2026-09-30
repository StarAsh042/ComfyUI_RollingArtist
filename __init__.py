"""ComfyUI RollingArtist 节点包。

ComfyUI 以“目录即包”的方式加载本文件（spec_from_file_location + __path__），
RollingArtist.py / RollingCharacter.py 与 ra_core 之间的相对导入因此成立，
请勿改成顶层绝对导入。

本包提供两个节点：

- ``RollingArtist``：从艺术家 CSV 抽人 + 随机权重
- ``RollingCharacter``：从角色 CSV 抽人 + 随机权重，额外带作品标签与外观标签
"""

from .RollingArtist import (
    NODE_CLASS_MAPPINGS as _ARTIST_NODES,
    NODE_DISPLAY_NAME_MAPPINGS as _ARTIST_NAMES,
)
from .RollingCharacter import (
    NODE_CLASS_MAPPINGS as _CHARACTER_NODES,
    NODE_DISPLAY_NAME_MAPPINGS as _CHARACTER_NAMES,
)

NODE_CLASS_MAPPINGS = {**_ARTIST_NODES, **_CHARACTER_NODES}
NODE_DISPLAY_NAME_MAPPINGS = {**_ARTIST_NAMES, **_CHARACTER_NAMES}

# 前端静态资源目录：ComfyUI 会把它挂到 /extensions/ComfyUI_RollingArtist/ 下。
# 按官方文档放 web/docs/<节点类名>/<语言>.md，即为节点“信息”面板的帮助文档，
# 前端按界面语言请求（缺失该语言时回退到 web/docs/<节点类名>.md）。
WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
