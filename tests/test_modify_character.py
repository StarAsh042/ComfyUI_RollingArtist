"""``modify_danbooru_character.py`` 的回归测试。

同时守住一条约定：两个加工脚本必须共用同一份转义实现（``danbooru_csv_tool``），
避免「改了一个忘了另一个」导致两边产出的提示词规则不一致。
"""

import csv
import os

import pytest

from ComfyUI_RollingArtist import modify_danbooru_art as art_script
from ComfyUI_RollingArtist import modify_danbooru_character as character_script
from ComfyUI_RollingArtist.danbooru_csv_tool import BACKUP_SUFFIX, convert as generic_convert

HEADER = ["character", "copyright", "trigger", "core_tags", "count", "solo_count", "url"]
ROWS = [
    HEADER,
    ["hatsune_miku", "vocaloid", "hatsune miku, vocaloid", "1girl, aqua eyes, twintails", "103500", "75493", "http://x"],
    ["admiral_(kancolle)", "kantai_collection", "admiral (kancolle), kantai collection", "1girl, ponytail", "4000", "3000", "http://x"],
    ["low_count", "some_copyright", "low count", "1girl", "29", "10", "http://x"],
    ["exactly_thirty", "", "exactly thirty", "", "30", "10", "http://x"],
    ["", "no_character", "no character", "1girl", "99", "10", "http://x"],
    ["empty_trigger", "some_copyright", "", "1girl", "98", "10", "http://x"],
]


def build(path, rows=ROWS):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)
    return str(path)


# ----------------------------------------------------------------------
# 接口约定
# ----------------------------------------------------------------------
def test_output_columns_and_escape_columns():
    assert character_script.OUTPUT_COLUMNS == (
        "character", "copyright", "trigger", "core_tags", "count")
    assert character_script.ESCAPE_COLUMNS == ("trigger",)
    assert character_script.DEFAULT_MIN_COUNT == 30
    assert character_script.DEFAULT_INPUT.endswith("danbooru_character.csv")
    assert character_script.DEFAULT_OUTPUT.endswith("danbooru_character_001.csv")


def test_both_scripts_share_one_escape_implementation():
    """两个脚本必须共用同一份转义函数，否则规则会漂移。"""
    assert art_script.transform_trigger is character_script.transform_trigger
    assert character_script.transform_text is art_script.transform_text


def test_escape_columns_are_identical_across_scripts():
    assert art_script.ESCAPE_COLUMNS == character_script.ESCAPE_COLUMNS == ("trigger",)


# ----------------------------------------------------------------------
# 转换逻辑
# ----------------------------------------------------------------------
def test_convert_keeps_five_columns_with_header():
    output, stats = character_script.convert(ROWS, 30)
    assert output[0] == ["character", "copyright", "trigger", "core_tags", "count"]
    assert all(len(row) == 5 for row in output)
    # 6 行：low_count(29) 被阈值过滤，空 character 与空 trigger 各跳过 1 行
    assert stats == {"total": 6, "kept": 3, "skipped": 2}


def test_convert_filters_by_count_and_sorts_descending():
    output, _ = character_script.convert(ROWS, 30)
    names = [row[0] for row in output[1:]]
    counts = [int(row[4]) for row in output[1:]]

    assert "low_count" not in names            # count 29 被过滤
    assert "exactly_thirty" in names           # 边界值保留
    assert counts == sorted(counts, reverse=True)


def test_convert_escapes_only_trigger():
    output, _ = character_script.convert(ROWS, 0)
    rows = {row[0]: row for row in output[1:]}

    # trigger：逐段处理（逗号后的空格不会变成下划线），括号也被转义
    assert rows["hatsune_miku"][2] == "hatsune_miku,vocaloid"
    assert rows["admiral_(kancolle)"][2] == "admiral_\\(kancolle\\),kantai_collection"

    # character：原样保留（含未转义括号）
    assert rows["admiral_(kancolle)"][0] == "admiral_(kancolle)"

    # copyright / core_tags：原样保留
    assert rows["hatsune_miku"][1] == "vocaloid"
    assert rows["hatsune_miku"][3] == "1girl, aqua eyes, twintails"


def test_convert_keeps_empty_copyright_but_skips_empty_character_or_trigger():
    output, stats = character_script.convert(ROWS, 0)
    names = [row[0] for row in output[1:]]
    assert "exactly_thirty" in names           # copyright 为空不影响保留
    assert "empty_trigger" not in names        # trigger 为空 -> 跳过
    assert "" not in names                     # character 为空 -> 跳过
    assert stats["skipped"] == 2


def test_convert_skips_short_and_bad_rows():
    rows = [
        HEADER,
        ["ok", "c", "ok trigger", "tags", "50", "1", "http://x"],
        ["short", "c", "trigger"],
        ["bad_count", "c", "bad count trigger", "tags", "abc", "1", "http://x"],
        [],
    ]
    output, stats = generic_convert(rows, 30, character_script.OUTPUT_COLUMNS,
                                    character_script.ESCAPE_COLUMNS)
    assert [row[0] for row in output[1:]] == ["ok"]
    assert stats == {"total": 3, "kept": 1, "skipped": 2}


def test_convert_rejects_missing_column():
    with pytest.raises(ValueError, match="core_tags"):
        character_script.column_indexes(["character", "copyright", "trigger", "count"])


def test_convert_rejects_empty_file():
    with pytest.raises(ValueError, match="空"):
        character_script.convert([], 30)


# ----------------------------------------------------------------------
# 命令行
# ----------------------------------------------------------------------
def test_main_end_to_end(tmp_path, capsys):
    source = build(tmp_path / "character.csv")
    output = tmp_path / "character_001.csv"

    assert character_script.main(["-i", source, "-o", str(output), "-n", "30"]) == 0
    with open(output, encoding="utf-8", newline="") as handle:
        body = list(csv.reader(handle))
    assert body[0] == ["character", "copyright", "trigger", "core_tags", "count"]
    assert len(body) == 4
    text = capsys.readouterr().out
    assert "保留 3 行" in text
    assert "转义列：trigger" in text


def test_main_backs_up_existing_output(tmp_path):
    source = build(tmp_path / "character.csv")
    output = tmp_path / "character_001.csv"
    output.write_text("placeholder\n", encoding="utf-8")

    assert character_script.main(["-i", source, "-o", str(output)]) == 0
    backup = tmp_path / f"character_001{BACKUP_SUFFIX}"
    assert backup.read_text(encoding="utf-8") == "placeholder\n"


def test_main_no_backup_flag(tmp_path):
    source = build(tmp_path / "character.csv")
    output = tmp_path / "character_001.csv"
    output.write_text("placeholder\n", encoding="utf-8")

    assert character_script.main(["-i", source, "-o", str(output), "--no-backup"]) == 0
    assert not (tmp_path / f"character_001{BACKUP_SUFFIX}").exists()


def test_main_refuses_same_path_and_missing_input(tmp_path, capsys):
    same = build(tmp_path / "same.csv")
    assert character_script.main(["-i", same, "-o", same]) == 1
    assert "同一个文件" in capsys.readouterr().out
    assert character_script.main(["-i", str(tmp_path / "nope.csv")]) == 1
    assert "输入文件不存在" in capsys.readouterr().out


def test_main_reports_missing_column(tmp_path, capsys):
    source = build(tmp_path / "bad.csv", [["character", "count"], ["a", "50"]])
    assert character_script.main(["-i", source, "-o", str(tmp_path / "o.csv")]) == 1
    assert "trigger" in capsys.readouterr().out


# ----------------------------------------------------------------------
# 仓库里已生成的处理结果
# ----------------------------------------------------------------------
def test_processed_character_csv_shape():
    """处理后的角色 CSV（若已生成）必须符合：五列、count>=30、trigger 已转义。"""
    path = character_script.DEFAULT_OUTPUT
    if not os.path.isfile(path):
        pytest.skip("尚未生成 danbooru_character_001.csv")

    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    header, body = rows[0], rows[1:]
    assert header == ["character", "copyright", "trigger", "core_tags", "count"]
    assert body, "处理结果为空"
    assert all(len(row) == 5 for row in body)
    assert min(int(row[4]) for row in body) >= 30
    assert not any(" " in row[2] for row in body), "trigger 列不应包含空格"
    assert not any("(" in row[2].replace("\\(", "") for row in body), \
        "trigger 列不应包含未转义括号"
    # 脏下划线守卫：,_ / _, / __ / 首尾下划线
    assert not any(",_" in row[2] or "_," in row[2] for row in body), \
        "trigger 列不应出现逗号紧邻的下划线"
    assert not any("__" in row[2] for row in body), "trigger 列不应出现连续下划线"
    assert not any(row[2].startswith("_") or row[2].endswith("_") for row in body), \
        "trigger 列不应以短横线/空格转来的下划线开头或结尾"
