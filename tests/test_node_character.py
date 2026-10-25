"""RollingCharacter 节点层冒烟测试。

与 RollingArtist 一样，节点文件只依赖标准库与 ``ra_core``，不 import ComfyUI，
因此可以直接实例化并调用 ``generate_characters``。

输出约定（本文件重点守住）：**不带权重**，每个角色一行，格式为
``角色标签,作品标签,角色描述``，多个角色用「逗号 + 换行」分隔；
``tested_count`` 以字符串输出；重复回避靠「最近 10 条输出记录」的内存窗口。
"""

import csv
import json
import os

import pytest
from ComfyUI_RollingArtist import NODE_CLASS_MAPPINGS
from ComfyUI_RollingArtist.RollingCharacter import RollingCharacter

CHARACTER_ROWS = [
    ["character", "copyright", "trigger", "core_tags", "count"],
    ["hatsune_miku", "vocaloid", "hatsune_miku,vocaloid", "1girl, aqua eyes, twintails", "103500"],
    ["hakurei_reimu", "touhou", "hakurei_reimu,touhou", "1girl, brown eyes", "78109"],
    [
        "ganyu_(genshin_impact)",
        "genshin_impact",
        "ganyu_\\(genshin_impact\\),genshin_impact",
        "1girl, horns",
        "50000",
    ],
    ["obscure_char", "touhou", "obscure_char,touhou", "", "5"],
]

BASE = dict(character_count=3, character_top_ratio=0.01, character_top_count=1, seed=42)


@pytest.fixture
def node():
    return RollingCharacter()


@pytest.fixture
def character_csv(tmp_path):
    path = tmp_path / "characters.csv"
    with open(path, "w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(CHARACTER_ROWS)
    return str(path)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "characters.sqlite")


def run(node, csv_path, db, **overrides):
    params = dict(BASE, custom_csv_path=csv_path, tested_db_path=db)
    params.update(overrides)
    return node.generate_characters(**params)


def line_names(prompt):
    """把多行输出还原成角色名列表（去掉行尾逗号）。"""
    return [line.strip().rstrip(",").split(",")[0] for line in prompt.splitlines()]


# ----------------------------------------------------------------------
# 接口定义
# ----------------------------------------------------------------------
def test_node_is_registered_and_artist_node_still_there():
    assert set(NODE_CLASS_MAPPINGS) == {"RollingArtist", "RollingCharacter"}


def test_return_contract():
    assert RollingCharacter.RETURN_TYPES == ("STRING", "STRING", "STRING")
    assert RollingCharacter.RETURN_NAMES == ("prompt", "characters_json", "tested_count")
    assert len(RollingCharacter.OUTPUT_TOOLTIPS) == 3
    assert RollingCharacter.CATEGORY == "RollingArtist"


def test_all_inputs_have_tooltips():
    inputs = RollingCharacter.INPUT_TYPES()
    for group in ("required", "optional"):
        for name, spec in inputs[group].items():
            assert spec[1].get("tooltip"), f"{name} 缺少 tooltip"


def test_required_and_optional_parameters():
    inputs = RollingCharacter.INPUT_TYPES()
    assert set(inputs["required"]) == {
        "character_count",
        "character_top_ratio",
        "character_top_count",
        "seed",
    }
    assert set(inputs["optional"]) == {
        "use_top_priority",
        "exclude_characters",
        "force_include",
        "copyright_pick",
        "describe_character",
        "custom_csv_path",
        "tested_db_path",
    }


def test_removed_parameters_are_gone():
    """去重参数、手输作品过滤、旧的开关名与权重参数都不应再出现。"""
    inputs = RollingCharacter.INPUT_TYPES()
    names = set(inputs["required"]) | set(inputs["optional"])
    for removed in (
        "dedup_mode",
        "max_attempts",
        "copyright_filter",
        "include_core_tags",
        "weight_min",
        "weight_max",
        "weight_total",
        "weight_curve",
        "sort_by_weight",
        "characters_prefix",
        "include_copyright_tag",
        "mode",
        "csv_column",
    ):
        assert removed not in names, f"{removed} 已不再生效，不应保留"


def test_generate_characters_signature():
    import inspect

    params = inspect.signature(RollingCharacter.generate_characters).parameters
    assert "describe_character" in params
    for removed in ("dedup_mode", "max_attempts", "copyright_filter", "include_core_tags"):
        assert removed not in params


def test_defaults():
    inputs = RollingCharacter.INPUT_TYPES()
    assert inputs["required"]["character_count"][1]["default"] == 1
    assert inputs["optional"]["describe_character"][1]["default"] is True
    assert inputs["optional"]["tested_db_path"][1]["advanced"] is True
    assert inputs["optional"]["custom_csv_path"][1]["advanced"] is True


def test_default_db_and_csv_paths():
    from ComfyUI_RollingArtist.ra_core.constants import (
        DEFAULT_CHARACTER_CSV,
        DEFAULT_CHARACTER_DB,
        NODE_DIR,
    )
    from ComfyUI_RollingArtist.ra_core.db import resolve_db_path

    assert DEFAULT_CHARACTER_CSV.endswith("danbooru_character_001.csv")
    assert DEFAULT_CHARACTER_DB.endswith("rollingcharacter.sqlite")
    assert os.path.dirname(DEFAULT_CHARACTER_DB) == NODE_DIR
    # 留空时角色节点落到自己的默认库，而不是画师的
    assert resolve_db_path("", DEFAULT_CHARACTER_DB) == DEFAULT_CHARACTER_DB


# ----------------------------------------------------------------------
# 作品下拉
# ----------------------------------------------------------------------
def test_copyright_pick_is_a_searchable_dropdown_with_every_work():
    """下拉以「不限」打头，后面是 CSV 里的全部作品（默认数据 3460 个）。"""
    from ComfyUI_RollingArtist.ra_core.characters import COPYRIGHT_ANY

    options = RollingCharacter.INPUT_TYPES()["optional"]["copyright_pick"][0]
    assert options[0] == COPYRIGHT_ANY
    assert len(set(options)) == len(options), "下拉项不应重复"
    assert all(isinstance(name, str) and name for name in options)

    spec = RollingCharacter.INPUT_TYPES()["optional"]["copyright_pick"][1]
    assert spec["default"] == COPYRIGHT_ANY

    from ComfyUI_RollingArtist.ra_core.constants import DEFAULT_CHARACTER_CSV

    if not os.path.isfile(DEFAULT_CHARACTER_CSV):
        pytest.skip("尚未生成 danbooru_character_001.csv")
    assert len(options) > 3000, "默认数据约有 3460 个作品，下拉不应做阈值裁剪"


# ----------------------------------------------------------------------
# 输出格式
# ----------------------------------------------------------------------
def test_prompt_is_one_line_per_character(node, character_csv, db_path):
    prompt, payload_json, tested = run(node, character_csv, db_path)
    payload = json.loads(payload_json)
    lines = prompt.splitlines()

    assert payload["status"] == "OK"
    assert len(payload["characters"]) == 3
    assert len(lines) == 3
    # 已测数量以字符串输出，便于直接接到文本节点查看
    assert tested == "1"
    # 行尾补逗号（最后一行不补），拼成一行时标签不会粘在一起
    assert not prompt.endswith(",")
    for line in lines[:-1]:
        assert line.endswith(",")

    # 每行以该角色的标签开头，并包含它的作品标签
    for line, item in zip(lines, payload["characters"]):
        parts = line.rstrip(",").split(",")
        assert parts[0] == item["name"]
        assert item["copyright"] in parts


def test_prompt_has_no_weights(node, character_csv, db_path):
    prompt, _, _ = run(node, character_csv, db_path)
    assert ":" not in prompt, "不应出现权重分隔符"
    # 角色标签里会有转义括号（如 ganyu_\\(genshin_impact\\)），但不应出现未转义的权重分组
    assert "(" not in prompt.replace("\\(", "")
    assert ")" not in prompt.replace("\\)", "")


def test_prompt_line_format_is_name_copyright_core_tags(node, character_csv, db_path):
    """定死单行格式：角色标签,作品标签,角色描述。"""
    prompt, _, _ = run(
        node, character_csv, db_path, force_include="ganyu_\\(genshin_impact\\)", character_count=1
    )
    assert prompt == "ganyu_\\(genshin_impact\\),genshin_impact,1girl,horns"


def test_prompt_omits_missing_copyright_or_core_tags(node, character_csv, db_path):
    prompt, _, _ = run(node, character_csv, db_path, force_include="obscure_char", character_count=1)
    # obscure_char 有作品标签但没有角色描述
    assert prompt == "obscure_char,touhou"


def test_describe_character_switch(node, character_csv, tmp_path):
    """关掉开关后每行只输出「角色标签,作品标签」。"""
    with_tags, _, _ = run(
        node, character_csv, str(tmp_path / "a.sqlite"), force_include="hatsune_miku", character_count=1
    )
    without, payload_json, _ = run(
        RollingCharacter(),
        character_csv,
        str(tmp_path / "b.sqlite"),
        force_include="hatsune_miku",
        character_count=1,
        describe_character=False,
    )

    assert with_tags == "hatsune_miku,vocaloid,1girl,aqua eyes,twintails"
    assert without == "hatsune_miku,vocaloid"
    # JSON 里的 core_tags 不受开关影响
    assert json.loads(payload_json)["characters"][0]["core_tags"] == ["1girl", "aqua eyes", "twintails"]


def test_json_fields_without_weight(node, character_csv, db_path):
    _, payload_json, _ = run(node, character_csv, db_path)
    for item in json.loads(payload_json)["characters"]:
        assert set(item) == {"name", "copyright", "top", "count", "core_tags"}
        assert "weight" not in item


def test_same_seed_is_reproducible_on_fresh_nodes(character_csv, tmp_path):
    """同一份参数 + 新实例（空窗口）应给出相同结果。"""
    first = run(RollingCharacter(), character_csv, str(tmp_path / "a.sqlite"))[0]
    second = run(RollingCharacter(), character_csv, str(tmp_path / "b.sqlite"))[0]
    assert first == second


# ----------------------------------------------------------------------
# 作品筛选与名单
# ----------------------------------------------------------------------
def test_copyright_pick_limits_candidates(node, character_csv, db_path):
    _, payload_json, _ = run(node, character_csv, db_path, copyright_pick="touhou", character_count=1)
    assert {item["copyright"] for item in json.loads(payload_json)["characters"]} == {"touhou"}


def test_copyright_pick_is_case_insensitive(node, character_csv, db_path):
    _, payload_json, _ = run(node, character_csv, db_path, copyright_pick="GENSHIN_IMPACT")
    assert {item["copyright"] for item in json.loads(payload_json)["characters"]} == {"genshin_impact"}


def test_copyright_pick_without_match_names_custom_csv(node, character_csv, db_path):
    """填了自定义 CSV 时，报错必须指明「不在你的自定义 CSV 里」并给出该 CSV 的热门示例。

    最常见的困惑：作品下拉只列默认 CSV 的作品，用户选了其中的名字、又填了自定义
    CSV 路径 -> 在自定义 CSV 里筛不出角色。笼统的「没有可用角色」指不明方向。
    """
    with pytest.raises(ValueError, match="不在自定义 CSV 的作品列表中") as info:
        run(node, character_csv, db_path, copyright_pick="fate_grand_order")
    message = str(info.value)
    assert "vocaloid" in message, "热门示例应含 count 最高的作品"
    assert "作品下拉仅列出默认 CSV" in message


def test_copyright_pick_without_match_names_default_csv(node, db_path):
    """未填自定义 CSV 时，报错指明「不在默认 CSV 的作品列表中」。"""
    from ComfyUI_RollingArtist.ra_core.constants import DEFAULT_CHARACTER_CSV

    if not os.path.isfile(DEFAULT_CHARACTER_CSV):
        pytest.skip("尚未生成 danbooru_character_001.csv")

    with pytest.raises(ValueError, match="不在默认 CSV 的作品列表中"):
        run(node, "", db_path, copyright_pick="不存在的作品")


def test_copyright_any_means_no_limit(node, character_csv, db_path):
    from ComfyUI_RollingArtist.ra_core.characters import COPYRIGHT_ANY

    _, payload_json, _ = run(node, character_csv, db_path, copyright_pick=COPYRIGHT_ANY)
    assert len(json.loads(payload_json)["characters"]) == 3


def test_copyright_pick_keeps_force_include_working(node, character_csv, db_path):
    prompt, _, _ = run(
        node, character_csv, db_path, copyright_pick="不存在的作品", force_include="hatsune_miku"
    )
    assert prompt.startswith("hatsune_miku")


def test_exclude_and_force_include(node, character_csv, db_path):
    prompt, payload_json, _ = run(
        node,
        character_csv,
        db_path,
        exclude_characters="hakurei_reimu",
        force_include="ganyu_\\(genshin_impact\\)",
    )
    names = [item["name"] for item in json.loads(payload_json)["characters"]]
    assert "ganyu_\\(genshin_impact\\)" in names
    assert "hakurei_reimu" not in names
    assert "ganyu_\\(genshin_impact\\),genshin_impact" in prompt


def test_force_include_with_unknown_name(node, character_csv, db_path):
    prompt, payload_json, _ = run(node, character_csv, db_path, force_include="not_in_csv", character_count=1)
    payload = json.loads(payload_json)
    assert payload["characters"][0]["name"] == "not_in_csv"
    assert payload["characters"][0]["core_tags"] == []
    assert prompt == "not_in_csv"


# ----------------------------------------------------------------------
# 最近 10 条输出记录内的重复回避（内存窗口）
# ----------------------------------------------------------------------
def test_recent_window_avoids_repeats(node, character_csv, db_path):
    """4 个候选连抽 4 次，每次 1 个：窗口内不应出现重复。"""
    seen = [
        line_names(run(node, character_csv, db_path, character_count=1, use_top_priority=False)[0])[0]
        for _ in range(4)
    ]
    assert len(set(seen)) == 4, f"最近记录窗口内出现了重复: {seen}"


def test_recent_window_spans_whole_generation(node, character_csv, db_path):
    """窗口按「条」记：每次生成 2 个角色，两条记录之间也不该撞。"""
    first = line_names(run(node, character_csv, db_path, character_count=2, use_top_priority=False)[0])
    second = line_names(run(node, character_csv, db_path, character_count=2, use_top_priority=False)[0])
    assert len(set(first)) == 2
    assert len(set(second)) == 2
    assert not (set(first) & set(second)), f"窗口内不应重复: {first} / {second}"


def test_recent_names_tracks_outputs_and_resets(node, character_csv, db_path):
    assert node.recent_names() == set()
    run(node, character_csv, db_path, character_count=2, use_top_priority=False)
    assert len(node.recent_names()) == 2
    node.reset_recent()
    assert node.recent_names() == set()


def test_window_keeps_at_most_ten_records(node, character_csv, db_path):
    from ComfyUI_RollingArtist.RollingCharacter import _RECENT_RECORDS

    assert _RECENT_RECORDS == 10
    for _ in range(_RECENT_RECORDS + 3):
        run(node, character_csv, db_path, character_count=1, use_top_priority=False)
    assert len(node._recent) == _RECENT_RECORDS


def test_window_covering_all_candidates_falls_back_to_repeats(node, character_csv, db_path):
    """窗口把候选吃光时退回「允许重复」，而不是报错。"""
    first = run(node, character_csv, db_path, character_count=4, use_top_priority=False)[0]
    assert len(first.splitlines()) == 4  # 4 个候选一次被抽走
    second, _, _ = run(node, character_csv, db_path, character_count=1, use_top_priority=False)
    assert line_names(second)[0]  # 仍有输出，没有抛异常


# ----------------------------------------------------------------------
# 记录与错误处理
# ----------------------------------------------------------------------
def test_tested_count_is_a_cumulative_string(node, character_csv, db_path):
    assert run(RollingCharacter(), character_csv, db_path)[2] == "1"
    assert (
        run(RollingCharacter(), character_csv, db_path, exclude_characters="hatsune_miku,hakurei_reimu")[2]
        == "2"
    )


def test_records_without_weights_still_get_distinct_keys(tmp_path):
    """不带权重时记录键必须仍然互不相同，否则所有组合会撞成一条记录。"""
    from ComfyUI_RollingArtist.ra_core.constants import KIND_CHARACTER
    from ComfyUI_RollingArtist.ra_core.db import get_db

    db = get_db(str(tmp_path / "k.sqlite"), "")
    assert db.record(["a", "b"], [1.0, 1.0], "p1", KIND_CHARACTER) is True
    assert db.record(["a", "c"], [1.0, 1.0], "p2", KIND_CHARACTER) is True
    assert db.count(KIND_CHARACTER) == 2
    assert db.is_tested("full_prompt", ["a", "b"], [1.0, 1.0], KIND_CHARACTER) is True
    assert db.is_tested("full_prompt", ["a", "c"], [1.0, 1.0], KIND_CHARACTER) is True
    assert db.is_tested("full_prompt", ["a", "z"], [1.0, 1.0], KIND_CHARACTER) is False


def test_records_are_isolated_from_artist_node(node, character_csv, tmp_path):
    """两个节点指向同一个数据库时，同名的画师与角色不会互相冒充。"""
    from ComfyUI_RollingArtist.ra_core.constants import KIND_ARTIST, KIND_CHARACTER
    from ComfyUI_RollingArtist.ra_core.db import get_db

    shared = str(tmp_path / "shared.sqlite")
    names = ["hatsune_miku"]
    weights = [1.0]

    db = get_db(shared, "")
    db.record(names, weights, "character prompt", KIND_CHARACTER)
    db.record(names, weights, "artist prompt", KIND_ARTIST)

    assert db.count() == 2
    assert db.count(KIND_ARTIST) == 1
    assert db.count(KIND_CHARACTER) == 1
    assert db.is_tested("full_prompt", names, weights, KIND_CHARACTER) is True
    assert db.is_tested("full_prompt", names, weights, KIND_ARTIST) is True
    # 角色节点只统计自己的记录：手工 1 条 + 本次生成 1 条
    # （本次生成的角色名与手工记录不同，不会撞键）
    assert run(node, character_csv, shared, force_include="obscure_char", character_count=1)[2] == "2"


def test_missing_csv_raises(node, tmp_path):
    with pytest.raises(ValueError, match="未能从 CSV 加载角色列表"):
        run(node, str(tmp_path / "missing.csv"), str(tmp_path / "n.sqlite"))


def test_zero_count_raises(node, character_csv, db_path):
    with pytest.raises(ValueError, match="character_count"):
        run(node, character_csv, db_path, character_count=0)


def test_default_character_csv_shape():
    """仓库自带的角色 CSV（若存在）应能直接喂给默认配置。"""
    from ComfyUI_RollingArtist.ra_core.characters import clear_records_cache, load_records
    from ComfyUI_RollingArtist.ra_core.constants import DEFAULT_CHARACTER_CSV

    if not os.path.isfile(DEFAULT_CHARACTER_CSV):
        pytest.skip("尚未生成 danbooru_character_001.csv")

    from ComfyUI_RollingArtist.ra_core.artists import ArtistRepository

    clear_records_cache()
    records = load_records(ArtistRepository(DEFAULT_CHARACTER_CSV), "", "trigger")
    assert len(records) > 30000
    names = [record.name for record in records]
    assert len(set(names)) == len(names), "角色名应唯一"
    assert not any(" " in name for name in names), "trigger 第 1 段不应含空格"
    assert all(record.extra_tags for record in records), "trigger 第 2 段应存在"
