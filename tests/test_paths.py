"""``ra_core.paths`` 的测试：路径防护、绕过回归、逃生开关。

本文件的 ``test_bypass_*`` 是把**安全官实测确认的绕过手法**固化成回归测试：
这些写法曾经能通过 ``realpath`` 字符串比较，导致防护失效。
现在每次改动都必须保证它们仍然被拦下。
"""

import os

import pytest
from ComfyUI_RollingArtist.ra_core import paths
from ComfyUI_RollingArtist.ra_core.paths import (
    PathNotAllowedError,
    check_csv_path,
    check_db_path,
    forbidden_roots,
    is_path_guard_disabled,
)


# ----------------------------------------------------------------------
# 基本行为
# ----------------------------------------------------------------------
def test_guard_is_on_by_default():
    assert is_path_guard_disabled() is False


def test_node_dir_data_file_is_allowed():
    """节点目录下的数据文件（默认 CSV / 数据库）必须放行。"""
    target = os.path.join(paths.NODE_DIR, "danbooru_art_001.csv")
    assert check_csv_path(target) == os.path.realpath(target)


def test_empty_path_is_rejected_here():
    """空路径由调用方处理（回退默认值），本函数视为非法。"""
    with pytest.raises(PathNotAllowedError):
        check_csv_path("")


def test_forbidden_roots_are_non_empty():
    """正常环境下应当至少识别出一批受保护目录。"""
    roots = forbidden_roots()
    assert roots, "未识别出任何受保护目录，防护形同虚设"
    assert all(os.path.isabs(root) for root in roots)


@pytest.mark.skipif(not forbidden_roots(), reason="无可用受保护目录")
def test_blocked_path_in_protected_dir():
    probe = os.path.join(forbidden_roots()[0], "evil.csv")
    with pytest.raises(PathNotAllowedError):
        check_csv_path(probe)


@pytest.mark.skipif(not forbidden_roots(), reason="无可用受保护目录")
def test_parent_traversal_is_blocked():
    """``..`` 穿越必须被 realpath 解析后拦下。"""
    probe = os.path.join(forbidden_roots()[0], "sub", "..", "evil.csv")
    with pytest.raises(PathNotAllowedError):
        check_csv_path(probe)


def test_node_source_file_is_blocked():
    """写向本节点源码（.py）必须被拒绝。"""
    probe = os.path.join(paths.NODE_DIR, "ra_core", "paths.py")
    with pytest.raises(PathNotAllowedError):
        check_csv_path(probe)


# ----------------------------------------------------------------------
# 绕过回归（安全复核实测确认过的手法）
# ----------------------------------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="Windows 特有写法")
def test_bypass_windows_extended_prefix():
    """``\\\\?\\`` 扩展长度前缀曾绕过 realpath 字符串比较。"""
    probe = "\\\\?\\" + os.path.join(paths.NODE_DIR, "ra_core", "paths.py")
    with pytest.raises(PathNotAllowedError):
        check_csv_path(probe)


@pytest.mark.skipif(os.name != "nt", reason="Windows 特有写法")
def test_bypass_windows_device_prefix():
    """``\\\\.\\`` 设备前缀同样不得绕过。"""
    probe = "\\\\.\\" + os.path.join(paths.NODE_DIR, "ra_core", "paths.py")
    with pytest.raises(PathNotAllowedError):
        check_csv_path(probe)


@pytest.mark.skipif(os.name != "nt", reason="Windows 特有写法")
def test_bypass_ads_on_source_file():
    """``paths.py:evil`` 备用数据流写法必须按主文件判定（源码 -> 拒绝）。"""
    probe = os.path.join(paths.NODE_DIR, "ra_core", "paths.py") + ":evil"
    with pytest.raises(PathNotAllowedError):
        check_csv_path(probe)


@pytest.mark.skipif(os.name != "nt", reason="Windows 特有写法")
def test_ads_on_data_file_still_allowed():
    """数据文件带 ADS 后缀时应按主文件放行（不是拒绝，而是识别为主文件）。"""
    probe = os.path.join(paths.NODE_DIR, "danbooru_art_001.csv") + ":evil"
    assert check_csv_path(probe) == os.path.realpath(os.path.join(paths.NODE_DIR, "danbooru_art_001.csv"))


def test_bypass_other_custom_node_dir():
    """custom_nodes 下**其它节点**的目录（可能含凭据）必须被拒绝。"""
    probe = os.path.join(os.path.dirname(paths.NODE_DIR), "other_node", "secret.csv")
    with pytest.raises(PathNotAllowedError):
        check_csv_path(probe)


# ----------------------------------------------------------------------
# UNC / 管理共享绕过（安全复核实测确认的 P0）
# ----------------------------------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="UNC 语义仅 Windows")
def test_bypass_unc_admin_share_to_protected_dir():
    r"""``\\localhost\C$\Windows\...`` 必须被拦下。

    ``os.path.realpath`` 不会把 UNC 规范化为盘符，此前基于盘符的黑名单对它完全失效。
    修复后 ``_resolve_unc_to_drive`` 会先还原成 ``C:\Windows\...`` 再做比较。
    """
    # 用「本节点父目录」构造 UNC 路径：该目录必然存在且在保护名单内，
    # 不依赖 ComfyUI 的安装位置，换机器 / 换 CI 环境都能复现
    parent = os.path.realpath(os.path.dirname(paths.NODE_DIR))
    drive, tail = os.path.splitdrive(parent)
    comfy_probe = (
        "\\\\localhost\\" + drive[0] + "$\\" + os.path.join(tail.lstrip("\\"), "other_node", "secret.csv")
    )
    for probe in (
        r"\\localhost\C$\Windows\win.ini",
        comfy_probe,
        r"\\127.0.0.1\C$\Windows\System32\config\SAM",
    ):
        with pytest.raises(PathNotAllowedError):
            check_csv_path(probe)


@pytest.mark.skipif(os.name != "nt", reason="UNC 语义仅 Windows")
def test_bypass_unc_extended_prefix():
    r"""``\\?\UNC\localhost\C$\Windows\...`` 同样必须被拦下（前缀 + UNC 双重绕过）。"""
    with pytest.raises(PathNotAllowedError):
        check_csv_path(r"\\?\UNC\localhost\C$\Windows\win.ini")


@pytest.mark.skipif(os.name != "nt", reason="UNC 语义仅 Windows")
def test_unc_remote_host_is_rejected():
    r"""指向**其它主机**的共享无法用盘符黑名单判断，直接拒绝。"""
    with pytest.raises(PathNotAllowedError):
        check_csv_path(r"\\evil-host\share\payload.csv")


@pytest.mark.skipif(os.name != "nt", reason="UNC 语义仅 Windows")
def test_unc_admin_share_to_safe_dir_is_allowed():
    r"""本机管理共享指向**安全目录**时应放行——证明是「还原后判定」而非「一刀切禁 UNC」。"""
    probe = r"\\localhost\C$\Users\Public\rolling.csv"
    assert check_csv_path(probe) == os.path.realpath(r"C:\Users\Public\rolling.csv")


# ----------------------------------------------------------------------
# 数据库写入侧校验
# ----------------------------------------------------------------------
def test_db_path_rejects_non_sqlite_existing_file(tmp_path):
    """已存在但不是 SQLite 的文件不允许当作数据库覆写。"""
    target = tmp_path / "not_a_db.csv"
    target.write_text("artist,trigger\nfoo,foo\n", encoding="utf-8")
    with pytest.raises(PathNotAllowedError):
        check_db_path(str(target))


def test_db_path_allows_new_file(tmp_path):
    """不存在的路径允许创建（首次建库）。"""
    target = tmp_path / "fresh.sqlite"
    assert check_db_path(str(target)) == os.path.realpath(str(target))


def test_db_path_allows_existing_sqlite(tmp_path):
    """已是 SQLite 的文件允许继续使用。"""
    import sqlite3

    target = tmp_path / "real.sqlite"
    conn = sqlite3.connect(str(target))
    conn.execute("CREATE TABLE t(x)")
    conn.commit()
    conn.close()
    assert check_db_path(str(target)) == os.path.realpath(str(target))


def test_db_path_allows_empty_file(tmp_path):
    """空文件允许（SQLite 首次写入才会写文件头）。"""
    target = tmp_path / "empty.sqlite"
    target.touch()
    assert check_db_path(str(target)) == os.path.realpath(str(target))


# ----------------------------------------------------------------------
# 逃生开关
# ----------------------------------------------------------------------
def test_escape_hatch_disables_guard(monkeypatch):
    """设了环境变量后防护完全关闭（受保护目录也能通过）。"""
    monkeypatch.setenv(paths.ALLOW_ANY_PATH_ENV, "1")
    assert is_path_guard_disabled() is True
    roots = forbidden_roots()
    if roots:
        probe = os.path.join(roots[0], "evil.csv")
        assert check_csv_path(probe) == os.path.realpath(probe)


def test_escape_hatch_off_by_default(monkeypatch):
    monkeypatch.delenv(paths.ALLOW_ANY_PATH_ENV, raising=False)
    assert is_path_guard_disabled() is False


# ----------------------------------------------------------------------
# 受保护目录缓存（热路径性能回归）
# ----------------------------------------------------------------------
def test_forbidden_roots_is_cached_across_calls():
    """forbidden_roots() 必须缓存结果。

    它以 13 次 realpath+isdir 为代价，且位于每次数据加载的必经路径上；
    不缓存会把画师「缓存命中」的开销从亚毫秒抬到约 8ms（实测）。
    """
    paths.clear_forbidden_cache()
    first = paths.forbidden_roots()
    second = paths.forbidden_roots()
    # 两次返回同一对象 => 命中缓存而非重新计算
    assert first is second


def test_forbidden_roots_cache_does_not_go_stale():
    """缓存可被清除，清除后重新计算结果正确。"""
    paths.clear_forbidden_cache()
    first = paths.forbidden_roots()
    paths.clear_forbidden_cache()
    second = paths.forbidden_roots()
    # 内容一致，但不是同一对象（确实重算了）
    assert first == second
    assert first is not second


def test_cache_does_not_weaken_protection():
    """加缓存后防护依然有效：受保护目录仍被拦下。"""
    paths.clear_forbidden_cache()
    roots = paths.forbidden_roots()
    if not roots:
        pytest.skip("当前环境下没有可用的受保护目录")
    probe = os.path.join(roots[0], "evil.csv")
    with pytest.raises(PathNotAllowedError):
        check_csv_path(probe)


def test_node_source_check_still_blocks_with_cache():
    """传入预计算的 realpath 时，源码保护仍生效（优化不能削弱安全性）。"""
    probe = os.path.join(paths.NODE_DIR, "ra_core", "paths.py")
    real = os.path.realpath(probe)
    assert paths._is_node_source(probe, real=real) is True
    assert paths._is_node_source(probe) is True
