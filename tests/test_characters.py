"""``ra_core.characters`` 的测试：trigger 拆解、作品筛选、标签去重、提示词组装。"""

import csv

import pytest
from ComfyUI_RollingArtist.ra_core.artists import ArtistRepository
from ComfyUI_RollingArtist.ra_core.characters import (
    CharacterRecord,
    build_character_prompt,
    build_payload,
    build_records,
    clear_records_cache,
    copyright_options,
    filter_by_copyright,
    load_records,
    parse_copyright_filter,
    split_core_tags,
    split_tags,
    unique_tags,
)

HEADER = ["character", "copyright", "trigger", "core_tags", "count"]
ROWS = [
    HEADER,
    ["hatsune_miku", "vocaloid", "hatsune_miku,vocaloid", "1girl, aqua eyes", "103500"],
    ["hakurei_reimu", "touhou", "hakurei_reimu,touhou", "1girl, brown eyes", "78109"],
    [
        "ganyu_(genshin_impact)",
        "genshin_impact",
        "ganyu_\\(genshin_impact\\),genshin_impact",
        "1girl, horns, horns",
        "50000",
    ],
    ["obscure_char", "touhou", "obscure_char,touhou", "", "5"],
]

RECORD = CharacterRecord("name", "touhou", "Touhou", "1girl, twintails", 10)


def records_of(rows=None, column="trigger"):
    return build_records(rows if rows is not None else ROWS, column)


def records_of_csv(path, column="trigger"):
    """直接读一个真实 CSV 文件（用于针对仓库自带数据的测试）。"""
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return build_records(list(csv.reader(handle)), column)


# ----------------------------------------------------------------------
# 基础解析
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a,b", ["a", "b"]),
        (" a , b ", ["a", "b"]),
        ("a,,b,", ["a", "b"]),
        ("", []),
        (None, []),
        ("1girl, aqua eyes, twintails", ["1girl", "aqua eyes", "twintails"]),
    ],
)
def test_split_tags(raw, expected):
    assert split_tags(raw) == expected
    assert split_core_tags(raw) == expected


def test_unique_tags_keeps_first_occurrence():
    assert unique_tags(["a", "b", "a", "", "c", "b"]) == ["a", "b", "c"]


def test_build_records_splits_trigger_into_name_and_copyright_tag():
    records = records_of()
    assert [record.name for record in records] == [
        "hatsune_miku",
        "hakurei_reimu",
        "ganyu_\\(genshin_impact\\)",
        "obscure_char",
    ]
    assert [record.extra_tags for record in records] == [
        "vocaloid",
        "touhou",
        "genshin_impact",
        "touhou",
    ]
    assert [record.copyright for record in records] == [
        "vocaloid",
        "touhou",
        "genshin_impact",
        "touhou",
    ]
    assert [record.post_count for record in records] == [103500, 78109, 50000, 5]
    assert records[0].core_tags == "1girl, aqua eyes"


def test_build_records_single_segment_trigger_has_no_extra_tags():
    rows = [HEADER, ["solo", "some_work", "solo", "1girl", "100"]]
    record = records_of(rows)[0]
    assert record.name == "solo"
    assert record.extra_tags == ""


def test_build_records_by_column_name_and_index():
    assert records_of(column="character")[0].name == "hatsune_miku"
    assert records_of(column="2")[0].name == "hatsune_miku"
    assert records_of(column="character")[2].extra_tags == ""
    # auto 会命中 character 列（优先级最高），因此括号不会转义 —— 这正是默认列设为 trigger 的原因
    auto_records = records_of(column="auto")
    assert auto_records[0].name == "hatsune_miku"
    assert auto_records[2].name == "ganyu_(genshin_impact)"
    assert auto_records[2].extra_tags == ""


def test_build_records_without_header():
    rows = [["foo,work", "1girl", "20"], ["bar,work2", "", "10"]]
    records = build_records(rows, "0")
    assert [record.name for record in records] == ["foo", "bar"]
    assert [record.extra_tags for record in records] == ["work", "work2"]
    # 没有表头时辅助列不可用
    assert records[0].core_tags == ""
    assert records[0].post_count == 0


def test_build_records_skips_empty_and_short_rows():
    rows = [HEADER, [], ["", "", "", "", ""], ["ok,work", "", "ok,work", "tags", "3"], ["short"]]
    records = records_of(rows)
    assert [record.name for record in records] == ["ok"]
    assert records[0].extra_tags == "work"
    assert records[0].core_tags == "tags"
    assert records[0].post_count == 3


def test_build_records_rejects_all_columns():
    with pytest.raises(ValueError, match="all"):
        build_records(ROWS, "all")


def test_build_records_on_empty_input():
    assert build_records([]) == []


def test_build_records_tolerates_bad_count():
    rows = [HEADER, ["a,work", "tags", "abc"]]
    assert records_of(rows)[0].post_count == 0


# ----------------------------------------------------------------------
# 作品筛选
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("touhou", ["touhou"]),
        (" touhou , vocaloid ", ["touhou", "vocaloid"]),
        ("touhou,TOUHOU", ["touhou"]),
        ("", []),
        (None, []),
    ],
)
def test_parse_copyright_filter(raw, expected):
    assert parse_copyright_filter(raw) == expected


def test_filter_by_copyright_empty_returns_all():
    records = records_of()
    assert filter_by_copyright(records, "") == list(range(len(records)))


def test_filter_by_copyright_matches_case_insensitively():
    records = records_of()
    indexes = filter_by_copyright(records, "TOUHOU")
    assert [records[index].name for index in indexes] == ["hakurei_reimu", "obscure_char"]


def test_filter_by_copyright_accepts_raw_and_escaped_forms():
    """作品名里带括号时，两种写法都要能匹配上。"""
    rows = [
        HEADER,
        [
            "merlin_(fate/prototype)",
            "fate_(series)",
            "merlin_\\(fate/prototype\\),fate_\\(series\\)",
            "1girl, pointy ears",
            "1102",
        ],
    ]
    records = records_of(rows)
    assert [records[i].name for i in filter_by_copyright(records, "genshin_impact")] == []
    assert filter_by_copyright(records, "fate_(series)") == [0]  # copyright 列的原始写法
    assert filter_by_copyright(records, r"fate_\(series\)") == [0]  # trigger 里转义后的写法
    assert filter_by_copyright(records, "FATE_(SERIES)") == [0]  # 忽略大小写


def test_filter_by_copyright_without_match():
    assert filter_by_copyright(records_of(), "不存在的作品") == []


# ----------------------------------------------------------------------
# 作品下拉菜单
# ----------------------------------------------------------------------
def test_copyright_options_keeps_every_work_and_breaks_ties_by_name():
    """下拉列出全部作品；热度相同时按名字升序，长尾作品也在里面。"""
    rows = (
        [HEADER]
        + [[f"c{index}", "pokemon", f"c{index},pokemon", "", "1"] for index in range(5)]
        + [[f"t{index}", "touhou", f"c{index},touhou", "", "1"] for index in range(3)]
        + [
            ["solo", "rare_work", "solo,rare_work", "", "1"],
        ]
    )
    assert copyright_options(records_of(rows)) == ["pokemon", "touhou", "rare_work"]


def test_copyright_options_sorts_by_total_count_not_character_amount():
    """排序依据是 count 总和，不是「作品里有多少个角色」。"""
    rows = (
        [HEADER]
        + [
            # 2 个角色但每个都很热 -> 总和 2000
            [f"hot{index}", "few_but_hot", f"hot{index},few_but_hot", "", "1000"]
            for index in range(2)
        ]
        + [
            # 5 个角色但都很冷 -> 总和 5
            [f"cold{index}", "many_but_cold", f"cold{index},many_but_cold", "", "1"]
            for index in range(5)
        ]
    )
    assert copyright_options(records_of(rows)) == ["few_but_hot", "many_but_cold"]


def test_copyright_options_ignores_blank_and_deduplicates():
    rows = [HEADER] + [[f"c{index}", "touhou", f"c{index},touhou", "", "1"] for index in range(3)]
    assert copyright_options(records_of(rows)) == ["touhou"]


def test_copyright_options_on_default_csv_is_complete_and_sorted():
    """仓库自带的角色 CSV（若存在）：每个作品都要进下拉，且按 count 总和降序。"""
    import os

    from ComfyUI_RollingArtist.ra_core.constants import DEFAULT_CHARACTER_CSV

    if not os.path.isfile(DEFAULT_CHARACTER_CSV):
        pytest.skip("尚未生成 danbooru_character_001.csv")

    records = records_of_csv(DEFAULT_CHARACTER_CSV)
    options = copyright_options(records)
    assert len(options) == len({record.copyright for record in records if record.copyright})
    assert len(options) > 3000, "默认数据约有 3460 个作品"

    totals = {}
    for record in records:
        if record.copyright:
            totals[record.copyright] = totals.get(record.copyright, 0) + record.post_count
    values = [totals[name] for name in options]
    assert values == sorted(values, reverse=True), "应按 count 总和降序"


# ----------------------------------------------------------------------
# 提示词组装
# ----------------------------------------------------------------------
def test_build_character_prompt_one_line_per_character():
    records = [
        CharacterRecord("a", "work1", "work1", "1girl, twintails", 1),
        CharacterRecord("b", "work2", "work2", "1girl, horns", 1),
    ]
    # 行尾补逗号：拼成一行时前后两个角色的标签不会粘在一起
    assert build_character_prompt(records) == "a,work1,1girl,twintails,\nb,work2,1girl,horns"


def test_build_character_prompt_has_no_trailing_comma_for_single_character():
    prompt = build_character_prompt([CharacterRecord("a", "work1", "work1", "", 1)])
    assert prompt == "a,work1"
    assert not prompt.endswith(",")


@pytest.mark.parametrize(
    "record,expected",
    [
        (CharacterRecord("a", "", "", "", 1), "a"),
        (CharacterRecord("a", "touhou", "touhou", "", 1), "a,touhou"),
        (CharacterRecord("a", "", "", "1girl", 1), "a,1girl"),
        (CharacterRecord("a", "touhou", "touhou", "1girl", 1), "a,touhou,1girl"),
    ],
)
def test_build_character_prompt_omits_missing_parts(record, expected):
    assert build_character_prompt([record]) == expected


def test_build_character_prompt_has_no_weights():
    prompt = build_character_prompt([CharacterRecord("a", "w", "w", "1girl", 1)])
    assert "(" not in prompt and ":" not in prompt


def test_build_character_prompt_deduplicates_tags_within_a_line():
    records = [CharacterRecord("a", "w", "w", "horns, horns, wings", 1)]
    assert build_character_prompt(records) == "a,w,horns,wings"


def test_build_character_prompt_can_drop_core_tags():
    records = [
        CharacterRecord("a", "w", "w", "1girl, horns", 1),
        CharacterRecord("b", "w2", "w2", "", 1),
    ]
    assert build_character_prompt(records, include_core_tags=False) == "a,w,\nb,w2"
    assert build_character_prompt(records, include_core_tags=True) == "a,w,1girl,horns,\nb,w2"


def test_build_character_prompt_falls_back_to_copyright_column():
    """名字列不是 trigger 时没有额外段，作品标签应退回 copyright 列并转义括号。"""
    record = CharacterRecord("a", "", "fate_(series)", "1girl", 1)
    assert build_character_prompt([record]) == "a,fate_\\(series\\),1girl"
    # 已转义的不重复加反斜杠
    already = CharacterRecord("a", "", "fate_\\(series\\)", "", 1)
    assert build_character_prompt([already]) == "a,fate_\\(series\\)"


def test_build_character_prompt_without_records():
    assert build_character_prompt([]) == ""


def test_build_payload_fields():
    records = [CharacterRecord("a", "touhou", "touhou", "1girl, wings", 42)]
    payload = build_payload(records, {"a"})
    assert payload["status"] == "OK"
    assert payload["characters"][0] == {
        "name": "a",
        "copyright": "touhou",
        "top": True,
        "count": 42,
        "core_tags": ["1girl", "wings"],
    }


def test_build_payload_top_none_marks_everyone():
    payload = build_payload([RECORD], None)
    assert payload["characters"][0]["top"] is True


# ----------------------------------------------------------------------
# 记录缓存
# ----------------------------------------------------------------------
def test_load_records_caches_by_file_signature(tmp_path):
    path = tmp_path / "characters.csv"
    with open(path, "w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(ROWS)

    repository = ArtistRepository()
    clear_records_cache()
    first = load_records(repository, str(path), "trigger")
    second = load_records(repository, str(path), "trigger")
    assert first is second, "内容未变化时应命中缓存（返回同一对象）"

    # 换一列就是另一份缓存
    other = load_records(repository, str(path), "character")
    assert other is not first
    assert other[0].name == "hatsune_miku"

    clear_records_cache()
    third = load_records(repository, str(path), "trigger")
    assert third is not first


def test_load_records_missing_file_returns_empty(tmp_path):
    clear_records_cache()
    assert load_records(ArtistRepository(), str(tmp_path / "nope.csv"), "trigger") == []
