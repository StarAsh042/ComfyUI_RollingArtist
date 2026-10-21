"""节点层冒烟测试。

节点文件只依赖标准库与 ``ra_core``，不 import ComfyUI，因此这里可以直接
实例化节点并调用 ``generate_artists``，无需启动 ComfyUI 或做桩。

覆盖：接口定义（输入/输出/说明）、三种生成模式、去重、旧记录导入、
进度输出、以及各种参数非法时的报错。
"""

import json
import os

import pytest
from ComfyUI_RollingArtist.RollingArtist import (
    NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS,
    RollingArtist,
)

BASE = dict(
    artist_count=3,
    artist_top_count=1,
    artist_top_ratio=0.5,
    artists_prefix=True,
    weight_min=0.2,
    weight_max=1.0,
    weight_total=2.0,
    seed=42,
)


def run(node, csv_path, db_path, **overrides):
    params = dict(BASE, custom_csv_path=csv_path, tested_db_path=db_path)
    params.update(overrides)
    return node.generate_artists(**params)


@pytest.fixture
def node():
    return RollingArtist()


@pytest.fixture
def small_csv(artists_csv):
    return artists_csv([f"artist_{index}" for index in range(30)])


@pytest.fixture
def tiny_csv(artists_csv):
    return artists_csv(["a", "b", "c"])


# ----------------------------------------------------------------------
# 接口定义
# ----------------------------------------------------------------------
def test_node_is_registered():
    assert "RollingArtist" in NODE_CLASS_MAPPINGS
    assert "RollingArtist" in NODE_DISPLAY_NAME_MAPPINGS


def test_return_contract():
    assert RollingArtist.RETURN_TYPES == ("STRING", "STRING", "STRING")
    assert RollingArtist.RETURN_NAMES == ("prompt", "artists_json", "tested_count")
    assert len(RollingArtist.OUTPUT_TOOLTIPS) == 3


def test_all_inputs_have_tooltips():
    """每个输入都必须带 tooltip，否则界面上看不到说明。"""
    inputs = RollingArtist.INPUT_TYPES()
    for group in ("required", "optional"):
        for name, spec in inputs[group].items():
            assert spec[1].get("tooltip"), f"{name} 缺少 tooltip"
    assert set(inputs["required"]) == {
        "artist_count",
        "artist_top_ratio",
        "artist_top_count",
        "weight_min",
        "weight_max",
        "weight_total",
        "weight_curve",
        "artists_prefix",
        "seed",
    }
    assert set(inputs["optional"]) == {
        "use_top_priority",
        "sort_by_weight",
        "exclude_artists",
        "force_include",
        "dedup_mode",
        "max_attempts",
        "custom_csv_path",
        "tested_db_path",
    }


def test_still_exposed_params():
    inputs = RollingArtist.INPUT_TYPES()
    assert inputs["optional"]["tested_db_path"][1]["default"] == ""
    assert inputs["optional"]["tested_db_path"][1]["advanced"] is True
    assert inputs["optional"]["custom_csv_path"][1]["advanced"] is True


def test_removed_params_are_gone():
    """列参数、旧记录导入参数与生成模式（穷举）已删除，不应再出现在界面上。"""
    inputs = RollingArtist.INPUT_TYPES()
    names = set(inputs["required"]) | set(inputs["optional"])
    for removed in ("csv_column", "tested_csv_path", "mode"):
        assert removed not in names, f"{removed} 已不再生效，不应保留"


def test_generate_artists_has_no_mode_parameter():
    import inspect

    signature = inspect.signature(RollingArtist.generate_artists)
    assert "mode" not in signature.parameters


# ----------------------------------------------------------------------
# 常规模式
# ----------------------------------------------------------------------
def test_normal_mode_outputs(node, small_csv, tmp_path):
    prompt, payload_json, tested = run(node, small_csv, str(tmp_path / "n.sqlite"))
    payload = json.loads(payload_json)
    assert payload["status"] == "OK"
    assert len(payload["artists"]) == 3
    assert all("name" in item and "weight" in item and "top" in item for item in payload["artists"])
    assert all(f"artist:{item['name']}" in prompt for item in payload["artists"])
    # 已测数量以字符串输出，便于直接接到文本节点查看
    assert tested == "1"


def test_same_seed_is_reproducible(node, small_csv, tmp_path):
    first = run(node, small_csv, str(tmp_path / "a.sqlite"))[0]
    second = run(node, small_csv, str(tmp_path / "b.sqlite"))[0]
    assert first == second


def test_sort_by_weight_orders_prompt(node, small_csv, tmp_path):
    _, payload_json, _ = run(node, small_csv, str(tmp_path / "n.sqlite"), sort_by_weight=True)
    weights = [item["weight"] for item in json.loads(payload_json)["artists"]]
    assert weights == sorted(weights, reverse=True)


def test_prefix_can_be_disabled(node, small_csv, tmp_path):
    prompt, _, _ = run(node, small_csv, str(tmp_path / "n.sqlite"), artists_prefix=False)
    assert "artist:" not in prompt


@pytest.mark.parametrize("curve", ["flat", "dominant", "ramp"])
def test_weight_curves_are_accepted(node, small_csv, tmp_path, curve):
    prompt, payload_json, _ = run(node, small_csv, str(tmp_path / "n.sqlite"), weight_curve=curve)
    assert prompt
    assert len(json.loads(payload_json)["artists"]) == 3


def test_dedup_avoids_repeating_a_recorded_combo(node, small_csv, tmp_path):
    db_path = str(tmp_path / "n.sqlite")
    first = run(node, small_csv, db_path, dedup_mode="artist_list")[0]
    second = run(node, small_csv, db_path, dedup_mode="artist_list")[0]
    assert first != second


def test_exclude_and_force_include(node, small_csv, tmp_path):
    prompt, payload_json, _ = run(
        node,
        small_csv,
        str(tmp_path / "n.sqlite"),
        exclude_artists="artist_0,artist_1",
        force_include="artist_29",
    )
    names = [item["name"] for item in json.loads(payload_json)["artists"]]
    assert "artist_29" in names
    assert "artist_0" not in names
    assert "artist_1" not in names


def test_single_artist_is_plain_random_now(node, tiny_csv, tmp_path):
    """穷举模式删除后，artist_count=1 就是普通的随机抽 1 个。"""
    prompt, payload_json, tested = run(
        node, tiny_csv, str(tmp_path / "n.sqlite"), artist_count=1, weight_min=0.5, weight_max=0.7
    )
    assert prompt != "ALL_COMBINATIONS_TESTED"
    assert json.loads(payload_json)["status"] == "OK"
    assert len(json.loads(payload_json)["artists"]) == 1
    assert tested == "1"
    # 同一个库里重复执行不会翻出哨兵字符串，只会当作新组合记录
    for _ in range(4):
        again = run(
            node, tiny_csv, str(tmp_path / "n.sqlite"), artist_count=1, weight_min=0.5, weight_max=0.7
        )[0]
        assert again != "ALL_COMBINATIONS_TESTED"


def test_progress_count_is_a_cumulative_string(node, small_csv, tmp_path):
    db_path = str(tmp_path / "n.sqlite")
    seen = [
        run(
            node, small_csv, db_path, artist_count=1, dedup_mode="full_prompt", weight_min=0.5, weight_max=0.7
        )[2]
        for _ in range(3)
    ]
    assert all(value.isdigit() for value in seen)
    assert seen[0] == "1"
    counts = [int(value) for value in seen]
    assert counts == sorted(counts), "已测数量不应倒退"
    assert counts[-1] >= 2, "开去重后应当记录到不止一条组合"


# ----------------------------------------------------------------------
# 错误处理
# ----------------------------------------------------------------------
def test_missing_csv_raises(node, tmp_path):
    with pytest.raises(ValueError, match="未能从 CSV 加载艺术家列表"):
        run(node, str(tmp_path / "missing.csv"), str(tmp_path / "n.sqlite"))


def test_excluding_everything_raises(node, tiny_csv, tmp_path):
    with pytest.raises(ValueError, match="排除了全部艺术家"):
        run(node, tiny_csv, str(tmp_path / "n.sqlite"), exclude_artists="a,b,c")


def test_default_db_path_is_used_when_blank():
    """留空数据库路径时应落到节点目录下的默认数据库。"""
    from ComfyUI_RollingArtist.ra_core.constants import DEFAULT_DB, NODE_DIR
    from ComfyUI_RollingArtist.ra_core.db import resolve_db_path

    assert resolve_db_path("") == DEFAULT_DB
    assert resolve_db_path("   ") == DEFAULT_DB
    assert os.path.dirname(DEFAULT_DB) == NODE_DIR
