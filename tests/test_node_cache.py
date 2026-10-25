"""节点缓存指纹（``IS_CHANGED``）测试。

背景：ComfyUI 默认只按节点输入参数判断缓存是否命中。两个节点读的都是外部 CSV，
参数不变而文件被替换时，缓存会一直返回旧结果——用户「换了数据却没效果」。

这里的断言只覆盖指纹本身（纯文件 IO），不涉及 ComfyUI 运行时；
运行时是否真的重算由用户在 ComfyUI 中验证。
"""

import os

import pytest
from ComfyUI_RollingArtist.ra_core.storage import file_fingerprint
from ComfyUI_RollingArtist.RollingArtist import RollingArtist
from ComfyUI_RollingArtist.RollingCharacter import RollingCharacter

ARTIST_INPUTS = dict(
    artist_count=3,
    artist_top_count=1,
    artist_top_ratio=0.5,
    artists_prefix=True,
    weight_min=0.2,
    weight_max=1.0,
    weight_total=2.0,
    seed=42,
    use_top_priority=True,
    sort_by_weight=True,
    exclude_artists="",
    force_include="",
    dedup_mode="none",
    max_attempts=10,
    weight_curve="flat",
    tested_db_path="",
)

CHARACTER_INPUTS = dict(
    character_count=2,
    character_top_ratio=0.5,
    character_top_count=1,
    seed=42,
    use_top_priority=True,
    exclude_characters="",
    force_include="",
    copyright_pick="(不限)",
    describe_character=True,
    tested_db_path="",
)


def _write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write("name,trigger,count\n")
        for index, name in enumerate(rows):
            handle.write(f"{name},{name},{index + 1}\n")


# ----------------------------------------------------------------------
# file_fingerprint
# ----------------------------------------------------------------------
def test_fingerprint_is_stable_without_change(tmp_path):
    csv_path = tmp_path / "a.csv"
    _write_csv(csv_path, ["alpha", "beta"])
    assert file_fingerprint(str(csv_path)) == file_fingerprint(str(csv_path))


def test_fingerprint_changes_when_content_changes(tmp_path):
    csv_path = tmp_path / "a.csv"
    _write_csv(csv_path, ["alpha"])
    before = file_fingerprint(str(csv_path))
    _write_csv(csv_path, ["alpha", "beta", "gamma"])
    assert file_fingerprint(str(csv_path)) != before


def test_fingerprint_changes_when_mtime_changes(tmp_path):
    """内容长度不变、只改修改时间时也要变（覆盖「同名同大小、内容被换」）。"""
    csv_path = tmp_path / "a.csv"
    _write_csv(csv_path, ["alpha"])
    before = file_fingerprint(str(csv_path))
    stat = os.stat(csv_path)
    os.utime(csv_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert file_fingerprint(str(csv_path)) != before


def test_fingerprint_marks_missing_file(tmp_path):
    missing = tmp_path / "nope.csv"
    value = file_fingerprint(str(missing))
    assert value.startswith("missing:")
    assert file_fingerprint(str(missing)) == value


# ----------------------------------------------------------------------
# RollingArtist.IS_CHANGED
# ----------------------------------------------------------------------
def test_artist_is_changed_is_stable(tmp_path):
    csv_path = tmp_path / "artists.csv"
    _write_csv(csv_path, ["alpha", "beta"])
    first = RollingArtist.IS_CHANGED(**ARTIST_INPUTS, custom_csv_path=str(csv_path))
    second = RollingArtist.IS_CHANGED(**ARTIST_INPUTS, custom_csv_path=str(csv_path))
    assert first == second, "文件与参数都没变时指纹必须稳定，否则每次执行都会重算"


def test_artist_is_changed_tracks_csv_replacement(tmp_path):
    csv_path = tmp_path / "artists.csv"
    _write_csv(csv_path, ["alpha"])
    before = RollingArtist.IS_CHANGED(**ARTIST_INPUTS, custom_csv_path=str(csv_path))
    _write_csv(csv_path, ["gamma", "delta"])
    after = RollingArtist.IS_CHANGED(**ARTIST_INPUTS, custom_csv_path=str(csv_path))
    assert before != after, "CSV 被替换后指纹必须变化，否则会命中旧缓存"


def test_artist_is_changed_returns_str(tmp_path):
    """ComfyUI 会把返回值放进缓存键，必须是可哈希且可比较的稳定值。"""
    csv_path = tmp_path / "artists.csv"
    _write_csv(csv_path, ["alpha"])
    value = RollingArtist.IS_CHANGED(**ARTIST_INPUTS, custom_csv_path=str(csv_path))
    assert isinstance(value, str)
    assert hash(value) == hash(str(value))


def test_artist_is_changed_survives_rejected_path(tmp_path):
    """路径被防护拒绝时不能抛异常，否则会打断 ComfyUI 的缓存判断。"""
    protected = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "win.ini")
    value = RollingArtist.IS_CHANGED(**ARTIST_INPUTS, custom_csv_path=protected)
    assert isinstance(value, str)
    assert value.startswith("invalid:")


# ----------------------------------------------------------------------
# RollingCharacter.IS_CHANGED
# ----------------------------------------------------------------------
def test_character_is_changed_is_stable(tmp_path):
    csv_path = tmp_path / "characters.csv"
    _write_csv(csv_path, ["alpha", "beta"])
    first = RollingCharacter.IS_CHANGED(**CHARACTER_INPUTS, custom_csv_path=str(csv_path))
    second = RollingCharacter.IS_CHANGED(**CHARACTER_INPUTS, custom_csv_path=str(csv_path))
    assert first == second


def test_character_is_changed_tracks_csv_replacement(tmp_path):
    csv_path = tmp_path / "characters.csv"
    _write_csv(csv_path, ["alpha"])
    before = RollingCharacter.IS_CHANGED(**CHARACTER_INPUTS, custom_csv_path=str(csv_path))
    _write_csv(csv_path, ["gamma", "delta"])
    after = RollingCharacter.IS_CHANGED(**CHARACTER_INPUTS, custom_csv_path=str(csv_path))
    assert before != after


def test_character_is_changed_default_path_is_resolved():
    """留空走默认 CSV：指纹应指向节点目录下的默认文件，而不是空串。"""
    value = RollingCharacter.IS_CHANGED(**CHARACTER_INPUTS)
    assert value.startswith("missing:") or value.count("|") == 2
    assert "danbooru_character_001.csv" in value


@pytest.mark.parametrize("node", [RollingArtist, RollingCharacter])
def test_is_changed_accepts_unknown_inputs(node):
    """ComfyUI 会把节点全部输入当关键字传进来，签名必须能吞掉未知键。"""
    value = node.IS_CHANGED(custom_csv_path="", future_input="ignored")
    assert isinstance(value, str)
