r"""用户自定义路径的安全防护。

背景（为什么需要这个模块）
--------------------------
``custom_csv_path`` 与 ``tested_db_path`` 是节点界面上的自由文本路径参数。
修复前它们是**原样透传**的：节点会把任意路径交给 ``open()`` / ``sqlite3.connect()``，
而数据库路径还会被自动创建父目录。只要 ComfyUI 对局域网或公网开放（且未做鉴权），
任何能提交 ``/prompt`` 的人都可以读取服务器上的任意文件，或在任意可写位置创建文件。

设计取舍：黑名单防护，而非白名单封闭
------------------------------------
最初的方案是把路径**限制在少数允许目录内**（白名单）。实测发现这会误伤正常用户：
把 CSV 放在 ``D:\我的数据集\`` 或别的盘符是很常见的用法，一刀切拒绝等于破坏工作流。

因此改为**黑名单式防护**——默认放行，只拦截明确的危险目标：

1. **系统关键目录**：Windows 的 ``C:\Windows``、``C:\Program Files`` 等；
   POSIX 的 ``/etc``、``/usr``、``/proc``、``/sys`` 等
2. **凭据与密钥目录**：``~/.ssh``、``~/.aws``、``~/.gnupg`` 等
3. **ComfyUI 自身的代码目录**：``custom_nodes`` 下其它节点的源码、
   ``comfy/`` 核心源码、``.git`` 目录（防止覆写源码或读取仓库凭据）
4. **节点自己的源码目录**：防止把数据库写到源码旁边造成污染

同时保留写入侧的额外校验：数据库文件若**已存在且不是 SQLite 文件**，拒绝写入，
避免"往任意文件里灌 SQLite 页"这种破坏。

逃生开关：环境变量 ``ROLLINGARTIST_ALLOW_ANY_PATH=1`` 完全关闭防护
（仅在单机自用、不对局域网/公网开放时使用）。

本模块不依赖 ComfyUI，可独立导入与测试。
"""

import os
import re
import threading
from typing import Iterable, List, Optional

from .constants import LOGGER, NODE_DIR

__all__ = [
    "PathNotAllowedError",
    "is_path_guard_disabled",
    "forbidden_roots",
    "clear_forbidden_cache",
    "check_path",
    "check_csv_path",
    "check_db_path",
    "SQLITE_MAGIC",
]

# 关闭路径防护的逃生开关（默认不设置 = 保持防护）
ALLOW_ANY_PATH_ENV = "ROLLINGARTIST_ALLOW_ANY_PATH"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

# SQLite 数据库文件头（前 16 字节），用于"已存在文件是否可当作数据库"的判断
SQLITE_MAGIC = b"SQLite format 3\x00"

# 受保护目录的进程级缓存。
#
# 为什么需要缓存：``forbidden_roots()`` 每次调用都要对 13 个候选路径做
# ``realpath`` + ``isdir``，实测单次约 5.3 ms。而它位于**每次数据加载的必经路径**上
# （``ArtistRepository.load()`` -> ``resolve_path()`` -> ``check_csv_path()``），
# 缓存命中时也会被调用，实测把画师缓存命中的开销从亚毫秒抬到 7.9 ms。
# 这些目录在进程生命周期内不会变化，因此缓存一次即可。
_FORBIDDEN_CACHE: Optional[List[str]] = None
_FORBIDDEN_LOCK = threading.RLock()

# 本节点目录 realpath 的缓存（同样位于热路径上）
_NODE_REAL_CACHE: Optional[str] = None


def clear_forbidden_cache() -> None:
    """清除受保护目录缓存（测试、热重载或环境变量变化后使用）。

    缓存键不含环境变量，因此若测试中改动 ``SystemRoot`` / ``ProgramFiles`` 等，
    需要先调用本函数，否则会读到上一次算出的结果。
    """
    global _FORBIDDEN_CACHE, _NODE_REAL_CACHE
    with _FORBIDDEN_LOCK:
        _FORBIDDEN_CACHE = None
        _NODE_REAL_CACHE = None


class PathNotAllowedError(ValueError):
    """用户提供的路径指向受保护目标，已被拒绝。

    继承 ``ValueError``，与节点现有的参数校验失败保持同一异常类型，
    ComfyUI 会把消息原样展示给用户。
    """


def is_path_guard_disabled() -> bool:
    """是否通过环境变量显式关闭了路径防护。"""
    # os.getenv 与 os.environ.get 等价（官方文档互为别名）
    return str(os.getenv(ALLOW_ANY_PATH_ENV, "")).strip().lower() in _TRUTHY


def _comfy_root() -> str:
    """推断 ComfyUI 根目录：``<root>/custom_nodes/<节点>`` 往回退两级。

    推断失败（目录结构非标准）时返回节点目录的父目录。
    """
    parent = os.path.dirname(NODE_DIR)  # custom_nodes
    root = os.path.dirname(parent)  # ComfyUI 根
    return root if root and os.path.isdir(root) else parent


def forbidden_roots() -> List[str]:
    """返回受保护的目录列表（已 realpath 规范化、去重、只保留存在的）。

    这些目录下的任何读写都会被拒绝，因为它们是系统关键区、
    凭据存放区，或 ComfyUI 自身的代码区。

    结果按进程缓存：本函数位于每次数据加载的必经路径上，
    而候选目录在进程生命周期内不会变化，重复计算会显著拖慢热路径
    （详见 :data:`_FORBIDDEN_CACHE`）。
    """
    global _FORBIDDEN_CACHE
    cached = _FORBIDDEN_CACHE
    if cached is not None:
        return cached
    with _FORBIDDEN_LOCK:
        # 双重检查：并发首次调用时只算一次
        if _FORBIDDEN_CACHE is None:
            _FORBIDDEN_CACHE = _compute_forbidden_roots()
        return _FORBIDDEN_CACHE


def _compute_forbidden_roots() -> List[str]:
    """实际计算受保护目录列表（开销较大，仅由 :func:`forbidden_roots` 调用一次）。"""
    candidates: List[str] = []
    comfy_root = _comfy_root()
    parent = os.path.dirname(NODE_DIR)

    home = os.path.expanduser("~")
    if home and home != "~":
        # 凭据 / 密钥目录
        for name in (".ssh", ".aws", ".gnupg", ".config", ".kube", ".docker"):
            candidates.append(os.path.join(home, name))

    if os.name == "nt":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        program_data = os.environ.get("ProgramData", r"C:\ProgramData")
        candidates.extend(
            [
                system_root,
                program_files,
                program_files_x86,
                program_data,
            ]
        )
    else:
        candidates.extend(
            [
                "/etc",
                "/usr",
                "/bin",
                "/sbin",
                "/lib",
                "/boot",
                "/proc",
                "/sys",
                "/dev",
            ]
        )

    # ComfyUI 核心源码与其它自定义节点源码（但**不含**本节点自身）
    candidates.append(os.path.join(comfy_root, "comfy"))
    candidates.append(os.path.join(comfy_root, ".git"))
    candidates.append(parent)  # custom_nodes 整体

    resolved: List[str] = []
    seen = set()
    for item in candidates:
        try:
            real = os.path.realpath(item)
        except OSError:
            continue
        if not os.path.isdir(real):
            continue
        key = os.path.normcase(real)
        if key not in seen:
            seen.add(key)
            resolved.append(real)

    # 本节点目录被 custom_nodes 覆盖，显式从保护名单里剔除：
    # 节点的默认 CSV 与数据库就在这个目录下，必须放行
    node_real = os.path.normcase(os.path.realpath(NODE_DIR))
    return [root for root in resolved if os.path.normcase(root) != node_real]


def _is_within(target: str, root: str) -> bool:
    """判断 target 是否等于 root 或位于 root 之下（均已规范化）。

    用 ``os.path.commonpath`` 而非字符串前缀，避免 ``/data`` 误判 ``/database``。
    """
    if os.path.normcase(target) == os.path.normcase(root):
        return True
    try:
        return os.path.commonpath([target, root]) == root
    except ValueError:
        # 不同盘符（Windows）会抛 ValueError，视为不在范围内
        return False


def _node_real() -> str:
    """本节点目录的 realpath（进程级缓存：每次调用都 realpath 会拖慢热路径）。"""
    global _NODE_REAL_CACHE
    if _NODE_REAL_CACHE is None:
        _NODE_REAL_CACHE = os.path.realpath(NODE_DIR)
    return _NODE_REAL_CACHE


def _is_node_source(path: str, *, real: Optional[str] = None) -> bool:
    """是否命中本节点的源码文件（``.py`` / ``.toml`` 等，防止覆写自身代码）。

    允许本节点目录下的 ``.csv`` / ``.sqlite`` / ``.json`` 等数据文件，
    但拒绝把路径指向 ``.py`` 源码，避免节点被用来改写自己的实现。

    本函数**自带**规范化（剥离扩展前缀 / UNC 前缀 / ADS），因此即便被直接调用
    （不经过 ``check_path`` 的 ``_canonicalize``）也不会被 ``paths.py:evil``
    这类 ADS 写法绕过——安全复核曾据「只读本函数」质疑该点，实测经调用链已拦下，
    这里补齐自足性以免未来被单独复用。

    参数:
        real: 若调用方已算过该路径的 realpath（``check_path`` 就是），
              传入即可省掉一次重复的 ``realpath`` 系统调用。
    """
    if real is None:
        text = _strip_ads(_normalize_windows_path(str(path)))
        real = os.path.realpath(text)
    if not _is_within(real, _node_real()):
        return False
    name = os.path.basename(real).lower()
    return name.endswith((".py", ".pyc", ".pyo", ".pyd", ".toml", ".cfg", ".ini"))


# Windows 扩展长度 / 设备路径前缀。``os.path.realpath`` 不会解析它们，
# 导致 ``\\?\C:\Windows\..`` 这类写法绕过字符串比较（安全复核实测确认）。
_WINDOWS_EXTENDED_PREFIXES = ("\\\\?\\", "\\\\.\\")
# 允许的 UNC 形式：``\\?\UNC\server\share`` 去掉前缀后等价于 ``\\server\share``
_UNC_EXTENDED = "\\\\?\\UNC\\"


def _normalize_windows_path(text: str) -> str:
    """剥离 Windows 扩展长度 / 设备前缀，让 realpath 与比较都能正常处理。

    - ``\\\\?\\C:\\Windows\\x`` -> ``C:\\Windows\\x``
    - ``\\\\.\\C:\\Windows``    -> ``C:\\Windows``
    - ``\\\\?\\UNC\\server\\share`` -> ``\\\\server\\share``
    """
    if text.startswith(_UNC_EXTENDED):
        return "\\\\" + text[len(_UNC_EXTENDED) :]
    for prefix in _WINDOWS_EXTENDED_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix) :]
    return text


# Windows 备用数据流（ADS）分隔符：``name.csv:stream``。
# 真实路径的盘符之后不会再出现冒号（``C:`` 已被盘符占用），
# 因此"字符串中第二个冒号之后还有内容"即可判定为 ADS。
def _strip_ads(text: str) -> str:
    """去掉 Windows 备用数据流（ADS）后缀，返回主文件路径。

    ``C:\\data\\x.csv:evil`` -> ``C:\\data\\x.csv``；
    非 Windows 或没有 ADS 时原样返回。这样 ``paths.py:evil`` 会按 ``paths.py``
    判定，命中源码保护；``data.csv:evil`` 也会被识别为访问 ``data.csv``。
    """
    if os.name != "nt":
        return text
    # 盘符形如 "C:"，从第 2 个字符之后开始找冒号
    drive = text[2:] if len(text) >= 2 and text[1] == ":" else text
    index = drive.find(":")
    if index < 0:
        return text
    return text[: 2 + index] if text[1:2] == ":" else text[:index]


def _resolve_unc_to_drive(text: str) -> str:
    r"""把本机 UNC 管理共享路径还原为盘符路径，便于与黑名单比较。

    ``os.path.realpath`` **不会**把 ``\\localhost\C$\Windows`` 规范化成
    ``C:\Windows``，因此基于盘符的黑名单对它完全失效（安全复核实测确认）。
    ``C$`` / ``D$`` 这类管理共享在 ComfyUI 以管理员身份运行时是可读的，
    与之前修掉的 ``\\?\`` 绕过属于同一类风险。

    - ``\\localhost\C$\Windows\x``  -> ``C:\Windows\x``
    - ``\\127.0.0.1\D$\data``      -> ``D:\data``
    - ``\\?\UNC\localhost\C$\x``   -> ``C:\x``（在剥离扩展前缀之后就已是 UNC 形式）
    - 其它主机/共享（``\\server\share``）无法映射到本地盘符，原样返回，
      交由调用方按"非本机路径"处理。
    """
    if os.name != "nt":
        return text
    match = re.match(r"^\\\\[^\\/]+\\([A-Za-z])\$(?=[\\/]|$)", text)
    if not match:
        return text
    drive = match.group(1).upper()
    rest = text[match.end() :].lstrip("\\/")
    return f"{drive}:\\{rest}" if rest else f"{drive}:\\"


def _canonicalize(user_path: str) -> str:
    """把用户路径规范化为可用于安全比较的绝对 realpath。

    依次：去引号 -> 剥离 Windows 扩展前缀 / UNC 前缀 -> 还原本机盘符 ->
    剥离 ADS -> abspath -> realpath（解析 ``..`` 与符号链接）。
    """
    text = str(user_path).strip().strip('"').strip("'")
    text = _normalize_windows_path(text)
    text = _resolve_unc_to_drive(text)
    text = _strip_ads(text)
    return os.path.realpath(os.path.abspath(text))


def check_path(
    user_path: Optional[str],
    *,
    what: str = "路径",
    for_write: bool = False,
    extra_forbidden: Optional[Iterable[str]] = None,
) -> str:
    """校验用户提供的路径，返回规范化后的绝对路径。

    参数:
        user_path: 用户输入的路径
        what: 出错信息里的名称（如 "自定义 CSV 路径"）
        for_write: 写入场景下额外校验（已存在的非 SQLite 文件不允许当数据库写）
        extra_forbidden: 调用方额外指定的禁止目录

    返回:
        已 ``realpath`` 规范化的绝对路径（文件可以尚不存在）

    异常:
        PathNotAllowedError: 路径命中受保护目标且未关闭防护
    """
    if user_path is None or not str(user_path).strip():
        raise PathNotAllowedError(f"{what}为空")

    target = _canonicalize(user_path)

    if is_path_guard_disabled():
        return target

    forbidden: List[str] = list(forbidden_roots())
    if extra_forbidden:
        for item in extra_forbidden:
            try:
                forbidden.append(os.path.realpath(str(item)))
            except OSError:
                continue

    # 本节点目录是 custom_nodes 的子目录。custom_nodes 被整体保护后，
    # 需要对「本节点目录内的路径」开一个口子（默认 CSV / 数据库就在这里），
    # 因此命中保护目录时还要再确认一次：目标是否位于本节点目录内。
    # target 已是 realpath，直接传入可省掉 _is_node_source 里的重复计算。
    if _is_within(target, _node_real()):
        # 本节点目录内：只拦源码，数据文件放行
        if _is_node_source(target, real=target):
            LOGGER.error("[RollingArtist] 拒绝把%s指向节点源码：%s", what, target)
            raise PathNotAllowedError(
                f"[RollingArtist] {what}指向本节点的源码文件，已拒绝以避免覆写节点实现：\n"
                f"  请求路径：{target}\n"
                f"数据文件（CSV / SQLite）请使用其它文件名，或放到 ComfyUI 的 input / output 目录"
            )
        return target

    # 经过还原本机盘符后仍是 UNC 形式（``\\server\share``）的，指向的是其它主机，
    # 无法用盘符黑名单判断，直接拒绝——节点没有理由读写局域网/远程共享上的文件。
    if target.replace("/", "\\").startswith("\\\\"):
        LOGGER.error("[RollingArtist] 拒绝把%s指向远程/UNC 共享：%s", what, target)
        raise PathNotAllowedError(
            f"[RollingArtist] {what}指向网络共享（UNC）路径，已拒绝：\n"
            f"  请求路径：{target}\n"
            f"请使用本机盘符路径（例如 ComfyUI 的 input / output 目录）"
        )

    for root in forbidden:
        if _is_within(target, root):
            LOGGER.error(
                "[RollingArtist] 拒绝访问受保护目录下的%s：%s（命中：%s）",
                what,
                target,
                root,
            )
            raise PathNotAllowedError(
                f"[RollingArtist] {what}指向受保护的目录，已拒绝以避免任意文件读写：\n"
                f"  请求路径：{target}\n"
                f"  受保护目录：{root}\n"
                f"请把文件放到数据目录（例如 ComfyUI 的 input / output 目录）；"
                f"若确认环境可信、需要关闭该防护，可设置环境变量 "
                f"{ALLOW_ANY_PATH_ENV}=1（仅在单机自用、不对局域网/公网开放时使用）"
            )

    return target


def check_csv_path(user_path: Optional[str], *, extra_forbidden: Optional[Iterable[str]] = None) -> str:
    """校验 CSV 路径（语义化封装，便于统一错误文案）。"""
    return check_path(user_path, what="自定义 CSV 路径", extra_forbidden=extra_forbidden)


def check_db_path(user_path: Optional[str], *, extra_forbidden: Optional[Iterable[str]] = None) -> str:
    """校验数据库路径：除目录防护外，拒绝写入"存在但不是 SQLite"的文件。

    这样即便目标目录不在保护名单内，也不会出现"把 SQLite 页覆写进
    一个普通文件"这种破坏性写入。
    """
    target = check_path(user_path, what="记录数据库路径", for_write=True, extra_forbidden=extra_forbidden)

    if os.path.exists(target) and os.path.isfile(target):
        try:
            with open(target, "rb") as handle:
                head = handle.read(len(SQLITE_MAGIC))
        except OSError as error:
            LOGGER.error("[RollingArtist] 无法读取记录数据库以校验格式: %s -> %s", target, error)
            raise PathNotAllowedError(
                f"[RollingArtist] 记录数据库路径无法读取，已拒绝写入：{target}"
            ) from error
        if head and head != SQLITE_MAGIC:
            # 空文件（head 为空）允许——首次创建时 sqlite 会自行写入文件头
            LOGGER.error(
                "[RollingArtist] 记录数据库路径已存在但不是 SQLite 文件，拒绝覆写：%s",
                target,
            )
            raise PathNotAllowedError(
                f"[RollingArtist] 记录数据库路径已存在，且不是 SQLite 数据库文件，"
                f"已拒绝覆写以免破坏原文件：\n  请求路径：{target}\n"
                f"请改用一个不存在的文件名（或先移走该文件）"
            )
    return target
