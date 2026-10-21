"""``ra_core.weights`` 的纯逻辑测试：权重分配、提示词构建解析、去重键。"""

import random

import pytest
from ComfyUI_RollingArtist.ra_core.weights import (
    DEDUP_MODES,
    WEIGHT_CURVES,
    build_prompt,
    dedup_key,
    generate_weights,
    normalize_dedup_mode,
    normalize_weight_curve,
    parse_prompt,
    parse_weight_list,
)

BOUND_CASES = [
    (0.2, 1.0, 2.0, 3),
    (0.1, 2.0, 20.0, 5),
    (0.5, 0.5, 1.5, 4),  # min == max
    (0.1, 0.3, 0.1, 4),  # 总和小于下界 -> 夹到边界
    (0.1, 2.0, 0.0, 2),  # 总和为 0 -> 全部取下界
    (0.9, 0.2, 1.2, 2),  # 上下限写反
    (-0.5, 1.0, 1.0, 2),  # 负数下界
]


@pytest.mark.parametrize("curve", WEIGHT_CURVES + ("非法值",))
@pytest.mark.parametrize("low,high,total,count", BOUND_CASES)
def test_generate_weights_respects_bounds_and_total(low, high, total, count, curve):
    """三种曲线（含非法值回退）都必须满足：不越界 + 总和精确。"""
    for seed in range(50):
        values = generate_weights(count, low, high, total, random.Random(seed), curve)
        assert len(values) == count
        floor, ceil = max(0.0, min(low, high)), max(0.0, max(low, high))
        assert all(floor - 1e-9 <= value <= ceil + 1e-9 for value in values)
        target = max(count * floor, min(round(total, 1), count * ceil))
        assert round(sum(values), 1) == pytest.approx(target, abs=0.05)


def test_generate_weights_zero_count():
    assert generate_weights(0, 0.2, 1.0, 2.0, random.Random(0)) == []


def test_generate_weights_keeps_one_decimal():
    values = generate_weights(5, 0.1, 2.0, 5.0, random.Random(7), "ramp")
    assert all(abs(round(value, 1) - value) < 1e-9 for value in values)


def test_dominant_curve_is_more_concentrated_than_flat():
    """主次分明应当让最重/最轻比明显大于平坦曲线。"""

    def average_ratio(curve):
        ratios = []
        for seed in range(200):
            values = generate_weights(4, 0.2, 1.0, 2.0, random.Random(seed), curve)
            ratios.append(max(values) / min(values))
        return sum(ratios) / len(ratios)

    assert average_ratio("dominant") > average_ratio("flat") + 0.5


def test_ramp_curve_produces_descending_ladder():
    """阶梯曲线：权重的不同取值数量应多于平坦曲线（存在名次差）。"""
    ladder = generate_weights(6, 0.1, 2.0, 6.0, random.Random(3), "ramp")
    assert max(ladder) > min(ladder)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("flat", "flat"),
        ("DOMINANT", "dominant"),
        (" ramp ", "ramp"),
        ("", "flat"),
        (None, "flat"),
        ("不存在的值", "flat"),
    ],
)
def test_normalize_weight_curve(value, expected):
    assert normalize_weight_curve(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("none", "none"),
        ("FULL_PROMPT", "full_prompt"),
        ("", "none"),
        (None, "none"),
        ("bogus", "none"),
    ],
)
def test_normalize_dedup_mode(value, expected):
    assert normalize_dedup_mode(value) == expected
    assert expected in DEDUP_MODES


def test_build_prompt_omits_weight_when_one():
    # 末尾总是补一个逗号，方便跟角色节点的多行输出直接拼接
    assert build_prompt(["a", "b"], [1.0, 0.7], "artist:") == "artist:a,(artist:b:0.7),"
    assert build_prompt(["a"], [1.0], "") == "a,"


def test_build_prompt_always_ends_with_comma():
    assert build_prompt(["a"], [0.7], "artist:").endswith(",")
    assert build_prompt(["a", "b", "c"], [0.5, 0.6, 0.7], "").endswith(",")
    # 没有艺术家时不能只剩一个孤零零的逗号
    assert build_prompt([], [], "artist:") == ""


def test_build_prompt_trailing_comma_is_parseable():
    prompt = build_prompt(["foo_bar", "baz_(x)"], [1.0, 0.4], "artist:")
    assert prompt.endswith(",")
    assert parse_prompt(prompt) == (["foo_bar", "baz_(x)"], [1.0, 0.4])


def test_build_prompt_parse_prompt_roundtrip():
    artists = ["foo_bar", "baz_(x)"]
    weights = [1.0, 0.4]
    prompt = build_prompt(artists, weights, "artist:")
    parsed_artists, parsed_weights = parse_prompt(prompt)
    assert parsed_artists == artists
    assert parsed_weights == weights


def test_parse_prompt_handles_empty_and_bare_names():
    assert parse_prompt("") == ([], [])
    assert parse_prompt("a, b") == (["a", "b"], [1.0, 1.0])


def test_parse_weight_list_skips_garbage():
    assert parse_weight_list("0.8, x, 1.0, ") == [0.8, 1.0]


def test_dedup_key_none_mode_is_empty():
    assert dedup_key("none", ["a"], [0.5]) == ""
    assert dedup_key("full_prompt", ["a"], [0.5]) != ""


def test_dedup_key_mode_semantics():
    # full_prompt：顺序与权重都参与比较
    assert dedup_key("full_prompt", ["a", "b"], [0.8, 0.7]) != dedup_key(
        "full_prompt", ["b", "a"], [0.8, 0.7]
    )
    assert dedup_key("full_prompt", ["a", "b"], [0.8, 0.7]) != dedup_key(
        "full_prompt", ["a", "b"], [0.7, 0.8]
    )

    # artist_set / artist_list：忽略顺序与权重，只比名字
    for mode in ("artist_set", "artist_list"):
        assert dedup_key(mode, ["a", "b"], [0.8, 0.7]) == dedup_key(mode, ["b", "a"], [0.1, 0.2])
        assert dedup_key(mode, ["a", "b"], [0.8, 0.7]) != dedup_key(mode, ["a", "c"], [0.8, 0.7])

    # artist_set 会折叠重复名字，artist_list 不会
    assert dedup_key("artist_set", ["a", "a", "b"], [1.0, 0.5, 0.5]) == dedup_key(
        "artist_set", ["a", "b"], [1.0, 0.5]
    )
    assert dedup_key("artist_list", ["a", "a", "b"], [1.0, 0.5, 0.5]) != dedup_key(
        "artist_list", ["a", "b"], [1.0, 0.5]
    )


def test_dedup_key_escapes_separators():
    """名字里带分隔符时不能与「多个名字」撞键，否则组合会被误判成已测过。"""
    assert dedup_key("artist_set", ["a|b"], [1.0]) != dedup_key("artist_set", ["a", "b"], [1.0])
    assert dedup_key("artist_list", ["a:b"], [1.0]) != dedup_key("artist_list", ["a", "b"], [1.0])


def test_generation_mode_helpers_are_gone():
    """穷举模式已整体删除，生成模式相关的常量与函数不应再存在。"""
    from ComfyUI_RollingArtist.ra_core import weights

    for removed in ("GENERATION_MODES", "normalize_generation_mode", "weight_grid"):
        assert not hasattr(weights, removed), f"{removed} 已随穷举模式一起删除"
