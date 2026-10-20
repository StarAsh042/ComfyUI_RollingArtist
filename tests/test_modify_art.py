"""``modify_danbooru_art.py`` 的回归测试：筛选、列裁剪、只转义 trigger、备份命名。"""

import csv

import pytest

from ComfyUI_RollingArtist.modify_danbooru_art import (
    DEFAULT_MIN_COUNT,
    OUTPUT_COLUMNS,
    backup_output,
    column_indexes,
    convert,
    main,
    normalize_header,
    transform_trigger,
    write_rows,
)

HEADER = ["artist", "trigger", "count", "url"]
ROWS = [
    HEADER,
    ["hammer_(sunset_beach)", "hammer (sunset beach)", "5387", "http://x"],
    ["itomugi-kun", "itomugi-kun", "4512", "http://x"],
    ["low_count_artist", "low count artist", "29", "http://x"],
    ["exactly_thirty", "exactly thirty", "30", "http://x"],
    ["\\konata", "\\konata", "31", "http://x"],
    ["already_escaped", "already_\\(escaped\\)", "40", "http://x"],
]


def test_default_threshold_is_thirty():
    assert DEFAULT_MIN_COUNT == 30
    assert OUTPUT_COLUMNS == ("artist", "trigger", "count")


@pytest.mark.parametrize("value,expected", [
    (" Artist ", "artist"), ("\ufeffTRIGGER", "trigger"), ("core-tags", "core_tags"),
    (None, ""), ("with space", "with_space"),
])
def test_normalize_header(value, expected):
    assert normalize_header(value) == expected


@pytest.mark.parametrize("raw,expected", [
    ("hammer (sunset beach)", "hammer_\\(sunset_beach\\)"),
    ("banned artist", "banned_artist"),
    ("itomugi-kun", "itomugi_kun"),
    ("  padded name  ", "padded_name"),
    ("", ""),
    # 逗号分隔的多标签串：逗号后的空格不能变成下划线
    ("hakurei reimu, touhou", "hakurei_reimu,touhou"),
    ("hatsune miku, vocaloid", "hatsune_miku,vocaloid"),
    ("a , b", "a,b"),
    ("a,b", "a,b"),
    # 空段 / 首尾逗号 / 纯空白都收敛掉
    ("touhou,", "touhou"),
    (",touhou", "touhou"),
    ("a,,b", "a,b"),
    ("   ", ""),
    (",", ""),
    # 连续空白 / 短横线折叠为单个下划线
    ("a  b", "a_b"),
    ("95---", "95"),
    ("hi-ho-", "hi_ho"),
    ("iwashi dorobou -r-", "iwashi_dorobou_r"),
    ("kezune (i- -i)", "kezune_\\(i_i\\)"),
    ("-x-", "x"),
])
def test_transform_trigger(raw, expected):
    assert transform_trigger(raw) == expected


def test_transform_trigger_never_leaves_dirty_underscores():
    """产出的标签里不应出现 _touhou / a__b / _x 这类脏写法。"""
    samples = [
        "hakurei reimu, touhou", "vladilena millize, 86 -eightysix-",
        "agumon -yuki no kizuna-, digimon", "95---", "a  b", "a , b", "-x-",
    ]
    for raw in samples:
        result = transform_trigger(raw)
        assert "  " not in result
        assert ",_" not in result and "_," not in result
        assert "__" not in result
        assert not result.startswith("_") and not result.endswith("_")
        for segment in result.split(","):
            assert segment and not segment.startswith("_") and not segment.endswith("_")


@pytest.mark.parametrize("raw", [
    "already_\\(escaped\\)",      # 已转义（奇数个反斜杠）：不再重复加
    "double_\\\\(escaped\\\\)",   # 偶数个反斜杠：视为未转义，补一个后稳定
])
def test_transform_trigger_is_idempotent_after_one_pass(raw):
    once = transform_trigger(raw)
    assert transform_trigger(once) == once


def test_column_indexes_reports_missing_column():
    with pytest.raises(ValueError, match="trigger"):
        column_indexes(["artist", "count"])


def test_convert_keeps_only_three_columns_and_filters_by_count():
    output, stats = convert(ROWS, 30)
    assert output[0] == ["artist", "trigger", "count"]
    assert stats == {"total": 6, "kept": 5, "skipped": 0}

    names = [row[0] for row in output[1:]]
    assert "low_count_artist" not in names          # count 29 被过滤
    assert "exactly_thirty" in names                # 边界值保留
    assert all(len(row) == 3 for row in output)


def test_convert_sorts_by_count_descending():
    output, _ = convert(ROWS, 30)
    counts = [int(row[2]) for row in output[1:]]
    assert counts == sorted(counts, reverse=True)


def test_convert_only_escapes_trigger_column():
    output, _ = convert(ROWS, 30)
    rows = {row[0]: row for row in output[1:]}

    # trigger 已转义：空格与连字符变下划线、括号被转义
    assert rows["hammer_(sunset_beach)"][1] == "hammer_\\(sunset_beach\\)"
    assert rows["itomugi-kun"][1] == "itomugi_kun"
    assert rows["\\konata"][1] == "\\konata"

    # artist 原样保留：仍然是原始标签（含连字符、未转义括号、反斜杠）
    assert rows["hammer_(sunset_beach)"][0] == "hammer_(sunset_beach)"
    assert rows["itomugi-kun"][0] == "itomugi-kun"
    assert rows["\\konata"][0] == "\\konata"


def test_convert_skips_bad_rows():
    rows = [
        HEADER,
        ["ok", "ok", "50", "http://x"],
        ["bad_count", "bad_count", "abc", "http://x"],
        ["short_row", "short_row"],
        [],
        ["", "empty_artist", "60", "http://x"],
        ["empty_trigger", "", "70", "http://x"],
    ]
    output, stats = convert(rows, 30)
    assert [row[0] for row in output[1:]] == ["ok"]
    assert stats["skipped"] == 4


def test_convert_rejects_file_without_header():
    with pytest.raises(ValueError, match="空"):
        convert([], 30)


def test_convert_accepts_custom_threshold():
    output, stats = convert(ROWS, 0)
    assert stats["kept"] == 6


def test_backup_naming_and_no_overwrite(tmp_path):
    target = tmp_path / "danbooru_art_001.csv"
    target.write_text("old-content\n", encoding="utf-8")

    backup = backup_output(str(target))
    assert backup == str(tmp_path / "danbooru_art_001.bak")
    assert (tmp_path / "danbooru_art_001.bak").read_text(encoding="utf-8") == "old-content\n"
    assert target.read_text(encoding="utf-8") == "old-content\n"   # 原文件仍在

    # 已有备份时不覆盖：先改内容再备份，备份内容应保持第一次那份
    target.write_text("newer-content\n", encoding="utf-8")
    assert backup_output(str(target)) is None
    assert (tmp_path / "danbooru_art_001.bak").read_text(encoding="utf-8") == "old-content\n"


def test_backup_skipped_when_target_missing(tmp_path):
    assert backup_output(str(tmp_path / "nope.csv")) is None


def test_write_rows_roundtrip(tmp_path):
    target = tmp_path / "out.csv"
    write_rows(str(target), [["artist", "trigger", "count"], ["a", "b", "3"]])
    with open(target, encoding="utf-8", newline="") as handle:
        assert list(csv.reader(handle)) == [["artist", "trigger", "count"], ["a", "b", "3"]]
    assert not (tmp_path / "out.csv.tmp").exists()


def test_main_end_to_end(tmp_path, capsys):
    source = tmp_path / "full.csv"
    output = tmp_path / "art.csv"
    with open(source, "w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(ROWS)
    output.write_text("placeholder\n", encoding="utf-8")

    assert main(["-i", str(source), "-o", str(output), "-n", "30"]) == 0
    assert (tmp_path / "art.bak").exists()          # 覆盖前自动备份
    with open(output, encoding="utf-8", newline="") as handle:
        body = list(csv.reader(handle))
    assert body[0] == ["artist", "trigger", "count"]
    assert len(body) == 6
    assert "保留 5 行" in capsys.readouterr().out


def test_main_refuses_same_input_and_output(tmp_path, capsys):
    same = tmp_path / "same.csv"
    same.write_text("x\n", encoding="utf-8")
    assert main(["-i", str(same), "-o", str(same)]) == 1
    assert "同一个文件" in capsys.readouterr().out


def test_main_reports_missing_input(tmp_path, capsys):
    assert main(["-i", str(tmp_path / "nope.csv")]) == 1
    assert "输入文件不存在" in capsys.readouterr().out


def test_main_reports_missing_column(tmp_path, capsys):
    source = tmp_path / "bad.csv"
    source.write_text("artist,count\na,10\n", encoding="utf-8")
    assert main(["-i", str(source), "-o", str(tmp_path / "out.csv")]) == 1
    assert "trigger" in capsys.readouterr().out
