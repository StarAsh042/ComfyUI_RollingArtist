"""ComfyUI RollingArtist 节点包。

ComfyUI 以“目录即包”的方式加载本文件（spec_from_file_location + __path__），
RollingArtist.py 与 ra_core 之间的相对导入因此成立，请勿改成顶层绝对导入。
"""

from .RollingArtist import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

# 前端静态资源目录：ComfyUI 会把它挂到 /extensions/ComfyUI_RollingArtist/ 下。
# 按官方文档放 web/docs/RollingArtist/<语言>.md，即为节点“信息”面板的帮助文档，
# 前端按界面语言请求（缺失该语言时回退到 web/docs/RollingArtist.md）。
WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]