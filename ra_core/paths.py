r"""用户自定义路径的安全防护。

背景（为什么需要这个模块）
--------------------------
``custom_csv_path`` 与 ``tested_db_path`` 是节点界面上的自由文本路径参数。
修复前它们是**原样透传**的：节点会把任意路径交给文件读取与 SQLite 连接调用，
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

可选的管理员白名单（配置文件通道）
----------------------------------
黑名单只能拦截"已知危险目录"，无法阻止把文件写到任意其它位置。需要更强约束的
部署方（例如对公网开放、多用户共用）可以在节点目录下放一个 ``ra_config.json``::

    {"csv_allowed_roots": ["D:\\datasets"], "db_allowed_roots": ["D:\\datasets"]}

配置存在时，用户自定义的 CSV / 数据库路径必须落在对应根目录内；配置**只收窄**，
通过白名单后仍要经过 UNC 拒绝与黑名单。配置不存在 = 不启用白名单（保持现有行为）。
配置无效时一律 fail closed（宁拒绝不放行）。

没有"关闭防护"的开关
--------------------
5.3.0 移除了 ``ROLLINGARTIST_ALLOW_ANY_PATH`` 环境变量。它曾用于完全关闭防护，
但那是个一键关停的开关——任何能写进程环境的人都能让防护失效，与"防任意文件读写"
的初衷相悖。需要更强约束的部署方改用上面的白名单配置；需要放宽的少数场景
（CSV / 数据库放在系统目录下）请换目录，或把根目录写进白名单。

本模块不依赖 ComfyUI，可独立导入与测试。
"""

import json
import os
import re
import threading
from typing import Any, Iterable, List, Optional, Tuple

from .constants import LOGGER, NODE_DIR

__all__ = [
    "PathNotAllowedError",
    "forbidden_roots",
    "clear_forbidden_cache",
    "clear_allowlist_cache",
    "CONFIG_FILE_NAME",
    "check_path",
    "check_csv_path",
    "check_db_path",
    "SQLITE_MAGIC",
]

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


# Windows 系统目录的注册表来源：(配置单元路径, 值名, 标准默认值)。
#
# 为什么读注册表而不是环境变量：``SystemRoot`` / ``ProgramFiles`` / ``ProgramData``
# 这些环境变量是**登录时从注册表派生的副本**，注册表才是权威来源；且环境变量可被
# 启动脚本改写，注册表不能。改读注册表因此不是降级，而是更准。
#
# 注意 ``ProgramData`` 不在 ``CurrentVersion`` 下（QueryValueEx 返回 WinError 2），
# 必须走 ``Explorer\Shell Folders`` 的 ``Common AppData``——本机实测确认。
_WINDOWS_SYSTEM_DIR_SOURCES: Tuple[Tuple[str, str, str], ...] = (
    (
        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
        "SystemRoot",
        r"C:\Windows",
    ),
    (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion",
        "ProgramFilesDir",
        r"C:\Program Files",
    ),
    (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion",
        "ProgramFilesDir (x86)",
        r"C:\Program Files (x86)",
    ),
    (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        "Common AppData",
        r"C:\ProgramData",
    ),
)


def _read_registry_string(key_path: str, value_name: str) -> Optional[str]:
    """读取 ``HKLM`` 下 ``key_path`` 的字符串值；读不到返回 ``None``。

    ``winreg`` 只在 Windows 上存在，因此**延迟导入**——本模块在 POSIX 上也要能
    正常导入（模块 docstring 承诺可独立导入与测试）。

    任何异常（无此键、无此值、权限、非字符串类型）都返回 ``None``，由调用方回退到
    标准默认值：探测系统目录时「读不到」是正常情况，不该让路径校验直接崩掉。
    """
    try:
        import winreg  # noqa: PLC0415  （仅 Windows 存在，必须延迟导入）
    except ImportError:
        return None

    # ``winreg`` 是 Windows 专用模块：在 Linux/macOS 上 mypy 找不到它的成员，
    # 直接调用会报 attr-defined。绑定到 Any 让两个平台的类型检查都成立。
    winreg_api: Any = winreg

    try:
        with winreg_api.OpenKey(winreg_api.HKEY_LOCAL_MACHINE, key_path) as key:
            value, _kind = winreg_api.QueryValueEx(key, value_name)
    except OSError:
        return None

    if isinstance(value, str) and value.strip():
        return value
    return None


def _windows_system_dirs() -> List[str]:
    """返回 Windows 受保护系统目录的候选列表（标准默认值 + 注册表实测值的并集）。

    返回的是**候选集**，允许包含不存在的路径——:func:`_compute_forbidden_roots`
    本来就用 ``os.path.isdir`` 过滤候选，多给几个是安全的。

    两种情形都覆盖：

    - 标准系统：注册表值与默认值重复，去重后结果不变（行为与 5.2.0 等价）
    - 非标准系统（例如 Program Files 被挪到 ``D:\\Program Files``）：注册表值补上
      真实路径，防护不丢——这正是「硬编码默认值」做不到的。
    """
    resolved: List[str] = []
    for key_path, value_name, fallback in _WINDOWS_SYSTEM_DIR_SOURCES:
        resolved.append(_read_registry_string(key_path, value_name) or fallback)

    # 并集 + 保序去重（大小写不敏感：Windows 路径同一目录可能有多种写法）
    seen = set()
    result: List[str] = []
    for path in list(resolved) + [fallback for _k, _v, fallback in _WINDOWS_SYSTEM_DIR_SOURCES]:
        marker = os.path.normcase(path)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(path)
    return result


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
        # 系统目录改读注册表：注册表是这些值的权威来源（环境变量是登录时派生的副本，
        # 可被启动脚本改写而注册表不能），同时不含 os.getenv / os.environ 字面量，
        # 不再触发 Registry 的环境变量扫描规则。见 ITERATION_PLAN_V5.3.0 第 3 节。
        candidates.extend(_windows_system_dirs())
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

    比较前两侧都过 ``os.path.normcase``：Windows 路径大小写不敏感，而
    ``commonpath`` 返回的是**输入的大小写**（实测 ``commonpath('c:\\data\\x', 'C:\\Data')``
    为 ``'c:\\data'``）。根目录尚不存在时（白名单首次部署的常见情形）``realpath``
    不会统一大小写，缺这一步会把合法路径误判成「不在范围内」。
    """
    if os.path.normcase(target) == os.path.normcase(root):
        return True
    try:
        common = os.path.commonpath([target, root])
    except ValueError:
        # 不同盘符（Windows）或 UNC 与盘符路径混用会抛 ValueError，视为不在范围内
        return False
    return os.path.normcase(common) == os.path.normcase(root)


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


# D-05 决策（用户已拍板）：管理共享**只**还原这些本机主机形态。
# 其它主机（``127.0.0.2``、``localhost.``、本机名、FQDN、任意 IP）一律保持 UNC，
# 交由 ``check_path`` 的 UNC 拒绝分支处理——而不是被静默降级成本机同名盘符路径。
# 主机名在 Windows 上大小写不敏感，因此匹配时用 re.IGNORECASE。
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")

_UNC_ADMIN_SHARE_RE = re.compile(
    r"^\\\\(" + "|".join(re.escape(host) for host in _LOOPBACK_HOSTS) + r")\\([A-Za-z])\$(?=[\\/]|$)",
    re.IGNORECASE,
)


def _resolve_unc_to_drive(text: str) -> str:
    r"""把**本机** UNC 管理共享路径还原为盘符路径，便于与黑名单比较。

    ``os.path.realpath`` **不会**把 ``\\localhost\C$\Windows`` 规范化成
    ``C:\Windows``，因此基于盘符的黑名单对它完全失效（安全复核实测确认）。
    ``C$`` / ``D$`` 这类管理共享在 ComfyUI 以管理员身份运行时是可读的，
    与之前修掉的 ``\\?\`` 绕过属于同一类风险。

    - ``\\localhost\C$\Windows\x``  -> ``C:\Windows\x``
    - ``\\127.0.0.1\D$\data``      -> ``D:\data``
    - ``\\[::1]\C$\x`` / ``\\::1\C$\x`` -> ``C:\x``
    - ``\\?\UNC\localhost\C$\x``   -> ``C:\x``（在剥离扩展前缀之后就已是 UNC 形式）

    **只**还原 :data:`_LOOPBACK_HOSTS` 里的本机主机形态。其它主机的 ``X$`` 共享
    （``\\evil-host\C$\...``、``\\attacker.example.com\C$\...``、``\\192.168.1.50\C$\...``）
    **原样返回**，由 ``check_path`` 按"非本机路径"直接拒绝；不还原是为了避免把
    「访问远程共享」的意图静默降级成「读写本机同名盘符路径」。
    """
    if os.name != "nt":
        return text
    match = _UNC_ADMIN_SHARE_RE.match(text)
    if not match:
        return text
    drive = match.group(2).upper()
    rest = text[match.end() :].lstrip("\\/")
    return f"{drive}:\\{rest}" if rest else f"{drive}:\\"


def _canonicalize(user_path: str) -> str:
    """把用户路径规范化为可用于安全比较的绝对 realpath。

    依次：去引号 -> 剥离 Windows 扩展前缀 / UNC 前缀 -> 还原本机盘符 ->
    剥离 ADS -> abspath -> realpath（解析 ``..`` 与符号链接）。

    ``realpath`` 对**不可达的网络位置**会抛 ``OSError``（实测 ``\\\\127.0.0.2\\C$\\...``
    -> WinError 1231）。此时退回 ``abspath`` 结果，让 :func:`check_path` 的 UNC
    拒绝分支给出可操作的 ``PathNotAllowedError``，而不是把裸 ``OSError`` 抛给用户。
    退回分支不影响安全性：只有 UNC 路径会走退回，而 UNC 路径随后必被拒绝；
    本机路径的 ``OSError`` 照常上抛，不会被静默放过。
    """
    text = str(user_path).strip().strip('"').strip("'")
    text = _normalize_windows_path(text)
    text = _resolve_unc_to_drive(text)
    text = _strip_ads(text)
    absolute = os.path.abspath(text)
    try:
        return os.path.realpath(absolute)
    except OSError:
        if absolute.replace("/", "\\").startswith("\\\\"):
            return absolute
        raise


# ----------------------------------------------------------------------
# 管理员白名单（配置文件通道，D-01 = A：不新增任何环境变量读取）
# ----------------------------------------------------------------------
# 配置文件放在节点目录下（与 ra_core/ 同级，随节点分发但默认不存在）。
CONFIG_FILE_NAME = "ra_config.json"

# 路径类别 -> 配置键。白名单只约束**用户可填写的**自定义 CSV / DB 路径，
# 包内默认 CSV / DB 由调用方在空路径时直接返回，根本不会走到这里。
_KIND_CSV = "csv"
_KIND_DB = "db"
_CONFIG_KEYS = {_KIND_CSV: "csv_allowed_roots", _KIND_DB: "db_allowed_roots"}
_KIND_LABELS = {_KIND_CSV: "自定义 CSV 路径", _KIND_DB: "记录数据库路径"}

# 白名单的进程级缓存：``None`` = 尚未读取过；``()`` = 明确未启用白名单。
#
# 只缓存「允许根列表」而不是每次重读配置，理由与 ``_FORBIDDEN_CACHE`` 相同：
# 它位于每次数据加载的必经路径上（``resolve_path`` -> ``check_csv_path``）。
# 配置在进程生命周期内不会变，改配置后重启 ComfyUI 即可；测试用
# :func:`clear_allowlist_cache` 强制重读。
_ALLOWED_ROOTS: Optional[Tuple[Tuple[str, ...], Tuple[str, ...]]] = None
_ALLOWLIST_LOCK = threading.RLock()


def _config_path() -> str:
    """白名单配置文件路径（节点目录下 ``ra_config.json``）。"""
    return os.path.join(NODE_DIR, CONFIG_FILE_NAME)


def clear_allowlist_cache() -> None:
    """清除白名单缓存，强制下次校验重新读取 ``ra_config.json``。

    测试、热重载或管理员手改配置后使用。
    """
    global _ALLOWED_ROOTS
    with _ALLOWLIST_LOCK:
        _ALLOWED_ROOTS = None


def _load_allowlist() -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """读取并缓存白名单配置，返回 ``(csv 允许根, db 允许根)``。

    语义（计划 4.2 规则 1/2/3/5，以及陷阱 2/3/4/5）：

    - 文件不存在 -> ``((), ())``，表示**未启用**白名单（保持现有黑名单行为）。
    - 文件存在但 JSON 非法 / 顶层不是对象 / 两个键都没有 / 某个列表为空 /
      根目录不是绝对路径或无法规范化 -> 抛 :class:`PathNotAllowedError`（fail closed），
      并给出可操作错误（写到哪个文件、哪个键、期望什么）。
    - 根目录逐个走**与 target 相同的** :func:`_canonicalize`（含 UNC 映射、剥尾分隔符），
      再 ``normcase`` 后缓存；比较时用 :func:`_is_within`（两侧均已 normcase）。
    - 裸盘符 ``D:`` 会被 ``abspath`` 解析成进程 CWD（Windows 盘符相对语义），
      与管理员「整个 D 盘」的直觉不符，因此显式拒绝并要求写 ``D:\\\\``。
    """
    global _ALLOWED_ROOTS
    cached = _ALLOWED_ROOTS
    if cached is not None:
        return cached
    with _ALLOWLIST_LOCK:
        if _ALLOWED_ROOTS is None:
            _ALLOWED_ROOTS = _read_allowlist()
        return _ALLOWED_ROOTS


def _read_allowlist() -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """实际读取 ``ra_config.json``（开销只发生一次，由 :func:`_load_allowlist` 缓存）。"""
    path = _config_path()
    if not os.path.exists(path):
        # 文件不存在 = 不启用白名单：保持 5.1.7 的黑名单行为，不新增任何 stat/open 热路径开销
        return ((), ())

    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as error:
        LOGGER.error("[RollingArtist] 白名单配置读取失败: %s -> %s", path, error)
        raise PathNotAllowedError(
            f"[RollingArtist] 路径白名单配置无法读取，已按 fail-closed 拒绝所有自定义路径：\n"
            f"  配置文件：{path}\n"
            f"  错误：{error}\n"
            f'请修正该 JSON 文件（结构：{{"csv_allowed_roots": [...], "db_allowed_roots": [...]}}），'
            f"或删除它以恢复默认的黑名单行为"
        ) from error

    if not isinstance(data, dict):
        raise _allowlist_invalid(path, "顶层必须是 JSON 对象")

    present = [key for key in _CONFIG_KEYS.values() if key in data]
    if not present:
        raise _allowlist_invalid(
            path,
            f'未配置任何白名单键（需要 "{_CONFIG_KEYS[_KIND_CSV]}" 或 "{_CONFIG_KEYS[_KIND_DB]}" 之一）；'
            "若不需要白名单请删除该文件",
        )

    roots: List[Tuple[str, ...]] = []
    for kind in (_KIND_CSV, _KIND_DB):
        key = _CONFIG_KEYS[kind]
        if key not in data:
            # 只配置了一类：另一类保持不启用（不因单类配置影响另一类）
            roots.append(())
            continue
        raw = data[key]
        if not isinstance(raw, list) or not raw:
            raise _allowlist_invalid(path, f'"{key}" 必须是非空数组')
        normalized: List[str] = []
        seen = set()
        for item in raw:
            if not isinstance(item, str) or not item.strip():
                raise _allowlist_invalid(path, f'"{key}" 的每一项都必须是非空字符串')
            text = item.strip()
            # 裸盘符（"D:"）在 Windows 上表示「D 盘当前目录」而不是 D 盘根，容易造成
            # 「以为放行了整个 D 盘、实际只放行了进程 CWD」的误解 -> 显式拒绝
            drive, tail = os.path.splitdrive(text)
            if drive and not tail:
                raise _allowlist_invalid(
                    path,
                    f'"{key}" 中的 "{text}" 是裸盘符，请写成 "{drive}\\\\" 表示整个盘',
                )
            if not os.path.isabs(text):
                raise _allowlist_invalid(
                    path,
                    f'"{key}" 中的 "{text}" 不是绝对路径（相对路径会按进程 CWD 解析，结果不可预期）',
                )
            try:
                root = _canonicalize(text)
            except OSError as error:  # pragma: no cover - realpath 已兜底 OSError
                raise _allowlist_invalid(path, f'"{key}" 中的 "{text}" 无法规范化：{error}') from error
            if root.replace("/", "\\").startswith("\\\\"):
                # 节点自身拒绝一切 UNC 路径，UNC 根永远匹配不到任何合法目标，
                # 静默接受只会让管理员以为配置生效 -> 明确报错
                raise _allowlist_invalid(
                    path,
                    f'"{key}" 中的 "{text}" 是网络共享（UNC）路径；白名单根必须是本机盘符路径',
                )
            # 与 target 同源规范化后再 normcase，避免「根 / 目标」两侧大小写不一致
            key_norm = os.path.normcase(root)
            if key_norm not in seen:
                seen.add(key_norm)
                normalized.append(root)
        roots.append(tuple(normalized))
    return (roots[0], roots[1])


def _allowlist_invalid(path: str, reason: str) -> PathNotAllowedError:
    """构造「白名单配置无效」的 fail-closed 错误（文案统一，便于用户照做）。"""
    return PathNotAllowedError(
        f"[RollingArtist] 路径白名单配置无效，已按 fail-closed 拒绝所有自定义路径：\n"
        f"  配置文件：{path}\n"
        f"  问题：{reason}\n"
        f'请修正该 JSON 文件（结构：{{"csv_allowed_roots": [...], "db_allowed_roots": [...]}}，'
        f'每项为绝对目录路径，例如 "D:\\\\datasets"），或删除它以恢复默认的黑名单行为'
    )


def _check_allowlist(target: str, kind: str) -> None:
    """白名单校验：target 必须落在 ``kind`` 对应的某个允许根内。

    调用点固定在 :func:`check_path` 里 ``_canonicalize`` 之后、黑名单循环**之前**：
    这里也早于「节点目录早退」，因此不会被本节点目录分支绕过。

    本函数**只拒绝不放行**：通过后仍须继续走 UNC 拒绝与黑名单循环（规则 4）。

    **节点目录豁免**（计划 4.2 规则 3）：包内默认 CSV / DB 是受信任的运行资源，
    节点目录内的路径不参与白名单校验。理由：① 默认路径由 ``resolve_path`` 在
    空路径时直接返回，本来就不经过这里；但用户可能把默认 CSV 的完整路径**显式**
    写进 ``custom_csv_path``（旧工作流里并不少见），若不放行会在启用白名单后
    突然报错；② 该目录的内容由维护者随节点分发，管理员配置白名单不是为了
    限制它；③ 现有代码在 :func:`check_path` 里本就对本节点目录单独处理
    （只拦源码覆写、数据文件放行），这里保持一致。
    """
    if _is_within(target, _node_real()):
        return

    csv_roots, db_roots = _load_allowlist()
    if not csv_roots and not db_roots:
        # 配置文件不存在（或两类都未配置）-> 白名单未启用，保持现有黑名单行为
        return

    allowed = csv_roots if kind == _KIND_CSV else db_roots
    if not allowed:
        # 只配置了另一类：本类不启用白名单（互不影响）
        return

    if any(_is_within(target, root) for root in allowed):
        return

    label = _KIND_LABELS[kind]
    roots_text = "\n".join(f"  - {root}" for root in allowed)
    LOGGER.error("[RollingArtist] 拒绝白名单外的%s：%s", label, target)
    raise PathNotAllowedError(
        f"[RollingArtist] {label}不在管理员配置的允许目录内，已拒绝：\n"
        f"  请求路径：{target}\n"
        f"  允许目录：\n{roots_text}\n"
        f"请把文件放到上述目录之一，或由管理员修改 {_config_path()} 中的白名单配置"
    )


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
        for_write: 写入场景。决定白名单用 ``db_allowed_roots`` 还是
            ``csv_allowed_roots``（``check_db_path`` 传 ``True``，CSV 侧保持默认）
        extra_forbidden: 调用方额外指定的禁止目录

    返回:
        已 ``realpath`` 规范化的绝对路径（文件可以尚不存在）

    异常:
        PathNotAllowedError: 路径命中受保护目标、落在管理员白名单之外，
            或白名单配置本身无效（fail closed）
    """
    if user_path is None or not str(user_path).strip():
        raise PathNotAllowedError(f"{what}为空")

    target = _canonicalize(user_path)

    # 【白名单插入点】固定在 ``_canonicalize`` 之后、黑名单循环之前：
    # 这里也早于「节点目录早退」，因此不会被本节点目录分支绕过。
    # 白名单只收窄不放行：通过后仍须走下面的 UNC 拒绝与黑名单循环（规则 4）。
    _check_allowlist(target, _KIND_DB if for_write else _KIND_CSV)

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
                f"系统目录与凭据目录不可放开，{CONFIG_FILE_NAME} 的白名单只收窄不放宽"
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
