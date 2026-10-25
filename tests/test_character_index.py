"""角色派生索引（``load_index``）与作品菜单缓存测试。

背景：角色节点每次执行都要用到「名字列表 / 名字->记录 / 作品筛选下标」，
过去这些结构在 3.4 万条记录上每执行一次重建一遍（实测约 19 ms）。
``load_index`` 把它们与记录一起按文件签名缓存；这里验证缓存命中、
按作品惰性缓存、以及 CSV 变化后确实重建（不会读到旧数据）。
"""

import csv

from ComfyUI_RollingArtist import RollingCharacter as node_module
from ComfyUI_RollingArtist.ra_core.artists import get_repository
from ComfyUI_RollingArtist.ra_core.characters import (
    clear_records_cache,
    load_index,
    load_records,
)

HEADER = ["character", "copyright", "trigger", "core_tags", "count"]
ROWS = [
    HEADER,
    ["hatsune_miku", "vocaloid", "hatsune_miku,vocaloid", "1girl", "100"],
    ["kagamine_rin", "vocaloid", "kagamine_rin,vocaloid", "1girl", "90"],
    ["hakurei_reimu", "touhou", "hakurei_reimu,touhou", "1girl", "80"],
]


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)


def test_index_is_cached_between_calls(tmp_path):
    clear_records_cache()
    path = tmp_path / "c.csv"
    write_csv(path, ROWS)
    repo = get_repository(str(path))
    first = load_index(repo, "", "trigger")
    second = load_index(repo, "", "trigger")
    assert first is second, "文件没变时应复用同一个索引对象"


def test_index_shares_records_with_load_records(tmp_path):
    clear_records_cache()
    path = tmp_path / "c.csv"
    write_csv(path, ROWS)
    repo = get_repository(str(path))
    index = load_index(repo, "", "trigger")
    assert index.records == load_records(repo, "", "trigger")


def test_names_for_without_filter_returns_prebuilt_list(tmp_path):
    clear_records_cache()
    path = tmp_path / "c.csv"
    write_csv(path, ROWS)
    index = load_index(get_repository(str(path)), "", "trigger")
    assert index.names_for(None) is index.names
    assert index.names == ["hatsune_miku", "kagamine_rin", "hakurei_reimu"]


def test_indices_for_without_filter_is_range(tmp_path):
    """不筛选时返回 range，避免每次执行都分配一个 3.4 万元素的列表。"""
    clear_records_cache()
    path = tmp_path / "c.csv"
    write_csv(path, ROWS)
    index = load_index(get_repository(str(path)), "", "trigger")
    allowed = index.indices_for(None)
    assert isinstance(allowed, range)
    assert list(allowed) == [0, 1, 2]


def test_copyright_filter_is_cached_per_name(tmp_path):
    clear_records_cache()
    path = tmp_path / "c.csv"
    write_csv(path, ROWS)
    index = load_index(get_repository(str(path)), "", "trigger")
    first = index.indices_for("vocaloid")
    second = index.indices_for("vocaloid")
    assert first is second, "同一作品的筛选结果应被缓存"
    assert index.names_for("vocaloid") == ["hatsune_miku", "kagamine_rin"]
    assert index.names_for("touhou") == ["hakurei_reimu"]


def test_index_rebuilds_when_csv_changes(tmp_path):
    clear_records_cache()
    path = tmp_path / "c.csv"
    write_csv(path, ROWS)
    repo = get_repository(str(path))
    before = load_index(repo, "", "trigger")
    write_csv(path, ROWS + [["marisa", "touhou", "marisa,touhou", "", "70"]])
    after = load_index(repo, "", "trigger")
    assert after is not before, "CSV 变化后必须重建索引，否则会读到旧角色"
    assert "marisa" in after.names


def test_clear_cache_drops_index(tmp_path):
    clear_records_cache()
    path = tmp_path / "c.csv"
    write_csv(path, ROWS)
    repo = get_repository(str(path))
    before = load_index(repo, "", "trigger")
    clear_records_cache()
    assert load_index(repo, "", "trigger") is not before


def test_copyright_menu_rebuilds_when_csv_changes(tmp_path, monkeypatch):
    """作品下拉菜单按文件签名缓存：换 CSV 后菜单要跟着变，不能等重启。"""
    path = tmp_path / "menu.csv"
    write_csv(path, ROWS)
    repo = get_repository(str(path))
    monkeypatch.setattr(node_module, "get_repository", lambda _path: repo)
    monkeypatch.setattr(node_module, "_COPYRIGHT_MENU", None)
    monkeypatch.setattr(node_module, "_COPYRIGHT_MENU_KEY", None)
    clear_records_cache()

    first = list(node_module._copyright_menu())
    assert "vocaloid" in first and "touhou" in first

    write_csv(path, ROWS + [["marisa", "new_series", "marisa,new_series", "", "70"]])
    second = list(node_module._copyright_menu())
    assert "new_series" in second, "CSV 新增作品后菜单应重建"
    assert second != first


def test_copyright_menu_is_stable_without_change(tmp_path, monkeypatch):
    path = tmp_path / "menu.csv"
    write_csv(path, ROWS)
    repo = get_repository(str(path))
    monkeypatch.setattr(node_module, "get_repository", lambda _path: repo)
    monkeypatch.setattr(node_module, "_COPYRIGHT_MENU", None)
    monkeypatch.setattr(node_module, "_COPYRIGHT_MENU_KEY", None)
    clear_records_cache()
    assert node_module._copyright_menu() is node_module._copyright_menu()
