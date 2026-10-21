"""``ra_core.artists`` 的测试：CSV 列识别、Top 数量计算、抽样选择、加载缓存。"""

import csv
import os
import random
import time

import pytest
from ComfyUI_RollingArtist.ra_core.artists import (
    ArtistRepository,
    compute_top_count,
    parse_artist_rows,
    select_artists,
    split_names,
)


def parse(text, column="auto"):
    return parse_artist_rows(csv.reader(text.splitlines()), column)


def test_single_column_without_header():
    assert parse("a\nb\nc\n") == ["a", "b", "c"]


def test_auto_prefers_trigger_over_artist():
    """trigger 是「提示词可直接使用」的列（空格与括号已转义），必须优先于原始 artist 列。"""
    text = "artist,trigger,count,url\nhammer_(sunset_beach),hammer_\\(sunset_beach\\),5387,http://x\n"
    assert parse(text) == ["hammer_\\(sunset_beach\\)"]


def test_auto_prefers_character_over_trigger():
    """character 必须优先：character CSV 的 trigger 是「角色 + 作品」多标签串，会破坏提示词。"""
    text = 'character,copyright,trigger,count\nhatsune_miku,vocaloid,"hatsune miku, vocaloid",103500\n'
    assert parse(text) == ["hatsune_miku"]


def test_auto_falls_back_to_artist_without_trigger_column():
    text = "artist,count\nfoo,10\nbar,5\n"
    assert parse(text) == ["foo", "bar"]


def test_auto_reads_default_danbooru_art_001_csv():
    """仓库自带的默认 CSV：三列带表头，默认配置应取到 trigger 列。"""
    from ComfyUI_RollingArtist.ra_core.constants import DEFAULT_ARTIST_CSV

    artists, _ = ArtistRepository().load("", "auto")
    assert artists, "默认 CSV 未能解析出艺术家"
    assert DEFAULT_ARTIST_CSV.endswith("danbooru_art_001.csv")
    assert all(" " not in name for name in artists), "trigger 列不应包含空格"
    assert not any("(" in name.replace("\\(", "") for name in artists), "trigger 列不应包含未转义括号"


def test_auto_skips_unknown_header_columns():
    """首行含未识别列名时，仍要按优先级命中 artist 列（旧实现会整列错位）。"""
    text = "title,artist,count\nhello,foo,10\nworld,bar,5\n"
    assert parse(text) == ["foo", "bar"]


def test_all_columns_flattens_everything():
    text = "artist,count\nfoo,10\n"
    assert parse(text, "all") == ["foo", "10"]


def test_column_by_index_and_by_name():
    text = "character,copyright,count\nmiku,vocaloid,100\n"
    assert parse(text, "0") == ["miku"]
    assert parse(text, "copyright") == ["vocaloid"]


def test_column_index_out_of_range_falls_back_to_first():
    text = "a,b\nc,d\n"
    assert parse(text, "9") == ["a", "c"]


def test_empty_and_blank_lines_are_ignored():
    text = "\n\nfoo\n\nbar\n"
    assert parse(text) == ["foo", "bar"]


def test_bom_and_surrounding_whitespace_are_stripped():
    assert parse("\ufefffoo\n  bar  \n") == ["foo", "bar"]


@pytest.mark.parametrize(
    "total,ratio,expected",
    [
        (0, 0.5, 0),
        (10, 0.01, 1),
        (10, 1.0, 10),
        (10, 0.55, 6),
        (3, 0.0, 1),
        (5, -1.0, 1),
        (5, 5.0, 5),
        (5, "bad", 1),
    ],
)
def test_compute_top_count(total, ratio, expected):
    assert compute_top_count(total, ratio) == expected


def test_split_names_dedupes_and_keeps_order():
    assert split_names(" a , b ,a, ,c ") == ["a", "b", "c"]
    assert split_names(None) == []


def test_select_artists_force_include_takes_priority():
    chosen = select_artists(random.Random(0), 2, 1, ["a", "b", "c"], ["a", "b", "c", "d"], 0, set())
    assert len(chosen) == 2
    assert set(chosen).issubset({"a", "b", "c"})


def test_select_artists_force_include_overrides_exclude():
    chosen = select_artists(random.Random(0), 1, 1, ["a"], ["a", "b"], 0, {"a"})
    assert chosen == ["a"]


def test_select_artists_deduplicates_force_list():
    """force_list 中的重复项不得产出重复艺术家。

    回归：修复前 `['a1','a1','a2']` 会返回含 'a1' 两次的结果，
    且 `len(force) >= artist_count` 分支会用 rng.sample 抽出同名两次。
    """
    artists = [f"a{i}" for i in range(50)]
    chosen = select_artists(random.Random(1), 3, 3, ["a1", "a1", "a2"], artists, 25, set())
    assert len(chosen) == len(set(chosen))
    assert {"a1", "a2"} <= set(chosen)


def test_select_artists_force_dedup_in_sample_branch():
    """force 数量 >= count 且含重复时，抽出的名单仍须唯一。"""
    artists = [f"a{i}" for i in range(50)]
    for seed in range(50):
        chosen = select_artists(random.Random(seed), 2, 2, ["a1", "a1", "a2"], artists, 25, set())
        assert len(chosen) == len(set(chosen))


def test_select_artists_respects_exclude_list():
    artists = ["a", "b", "c", "d"]
    for seed in range(20):
        chosen = select_artists(random.Random(seed), 3, 1, [], artists, 0, {"a"})
        assert "a" not in chosen


def test_select_artists_includes_top_when_priority_enabled():
    artists = [f"artist_{index}" for index in range(20)]
    top_pool = set(artists[:5])
    for seed in range(20):
        chosen = select_artists(random.Random(seed), 4, 2, [], artists, 5, set())
        assert len(chosen) == 4
        assert len(top_pool.intersection(chosen)) >= 1


def test_select_artists_returns_all_available_when_pool_too_small():
    chosen = select_artists(random.Random(0), 5, 1, [], ["a", "b"], 1, set())
    assert sorted(chosen) == ["a", "b"]


def test_select_artists_never_returns_duplicates():
    artists = [f"a{index}" for index in range(10)]
    for seed in range(30):
        chosen = select_artists(random.Random(seed), 5, 2, ["a0"], artists, 3, set())
        assert len(chosen) == len(set(chosen))


def test_repository_caches_and_reloads_on_change(tmp_path):
    path = tmp_path / "artists.csv"
    path.write_text("a\nb\n", encoding="utf-8")
    repo = ArtistRepository()
    first, _ = repo.load(str(path), "auto")
    assert first == ["a", "b"]

    # 内容与签名都没变 -> 命中缓存（返回同一对象）
    again, _ = repo.load(str(path), "auto")
    assert again is first

    # 文件变大后应重新解析
    time.sleep(0.01)
    path.write_text("a\nb\nc\n", encoding="utf-8")
    third, _ = repo.load(str(path), "auto")
    assert third == ["a", "b", "c"]


def test_repository_reports_missing_file():
    repo = ArtistRepository()
    artists, stat = repo.load("不存在.csv", "auto")
    assert artists == []
    assert stat is None


def test_repository_resolve_path_falls_back_to_default():
    repo = ArtistRepository(default_path="/tmp/default.csv")
    assert repo.resolve_path("") == "/tmp/default.csv"
    assert repo.resolve_path("  ") == "/tmp/default.csv"
    # 非空路径会做安全校验并规范化成绝对路径（供 realpath 比较用）
    resolved = repo.resolve_path(' "x.csv" ')
    assert os.path.isabs(resolved)
    assert os.path.basename(resolved) == "x.csv"


def test_repository_rejects_path_in_protected_dir(tmp_path):
    """受保护目录（系统 / 凭据 / 源码）下的路径必须被拒绝。"""
    from ComfyUI_RollingArtist.ra_core.paths import (
        PathNotAllowedError,
        forbidden_roots,
    )

    repo = ArtistRepository()
    roots = forbidden_roots()
    if not roots:
        pytest.skip("当前环境下没有可用的受保护目录")
    probe = os.path.join(roots[0], "evil.csv")
    with pytest.raises(PathNotAllowedError):
        repo.resolve_path(probe)


def test_csv_max_rows_caps_both_load_and_load_rows(tmp_path, monkeypatch):
    """CSV_MAX_ROWS 必须同时约束 load() 与 load_rows() 两条读取路径。

    回归：修复前只有 _read_rows() 遵守上限，load()（画师节点走这条）完全不设防。
    """
    import ComfyUI_RollingArtist.ra_core.artists as artists_mod

    path = tmp_path / "big.csv"
    lines = ["artist,trigger"] + [f"a{i},a{i}" for i in range(200)]
    path.write_text(chr(10).join(lines), encoding="utf-8")

    monkeypatch.setattr(artists_mod, "CSV_MAX_ROWS", 50)
    repo = ArtistRepository()
    assert len(repo.load(str(path))[0]) == 50
    rows, _ = repo.load_rows(str(path))
    assert len(rows) == 50


def test_empty_result_is_not_cached(tmp_path):
    """读取失败/空结果不得进缓存。

    回归防线：若把空列表写进缓存，文件随后恢复可读时仍会一直返回空，
    直到文件签名（mtime/size）变化——表现为"文件明明修好了却读不出来"。
    """
    repo = ArtistRepository()
    # 路径不存在 -> stat_key 为 None -> 直接走未缓存分支，返回空
    artists, stat = repo.load(str(tmp_path / "nope.csv"))
    assert artists == [] and stat is None
    rows, stat_rows = repo.load_rows(str(tmp_path / "nope.csv"))
    assert rows == [] and stat_rows is None

    # 空文件：有 stat_key，但解析结果为空 -> 不写缓存
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    repo.load(str(empty))
    assert repo._cache == {}, "空结果不应写入 artists 缓存"
    repo.load_rows(str(empty))
    assert repo._rows_cache == {}, "空结果不应写入 rows 缓存"


def test_both_read_paths_share_the_single_open_helper(tmp_path):
    """两条读取路径必须共用 `_open_csv`：异常处理只应存在一份。

    这里是"结构性"断言——统计 `_open_csv` 的调用次数，确认 `load` 与
    `load_rows` 都经过它，而不是各自 `open()`。此前两处各写了一份逐字相同的
    体积告警 + 四个 except 分支，合并后任何读取都必须走同一入口。
    """
    path = tmp_path / "a.csv"
    path.write_text("artist,trigger\nfoo,foo\n", encoding="utf-8")

    repo = ArtistRepository()
    calls = {"n": 0}
    original = repo._open_csv

    def counting_open(p):
        calls["n"] += 1
        return original(p)

    repo._open_csv = counting_open
    repo.load(str(path))
    assert calls["n"] == 1, "load() 未经过 _open_csv"
    repo.load_rows(str(path))
    assert calls["n"] == 2, "load_rows() 未经过 _open_csv"


def test_lru_eviction_keeps_cache_bounded(tmp_path):
    """超过 _MAX_CACHE 个不同签名时按 LRU 淘汰，缓存长度始终有界。"""
    repo = ArtistRepository()
    for index in range(ArtistRepository._MAX_CACHE + 3):
        path = tmp_path / f"f{index}.csv"
        path.write_text(f"a{index}\n", encoding="utf-8")
        repo.load(str(path))
    assert len(repo._cache) <= ArtistRepository._MAX_CACHE

    for index in range(ArtistRepository._MAX_CACHE + 3):
        path = tmp_path / f"r{index}.csv"
        path.write_text(f"a{index}\n", encoding="utf-8")
        repo.load_rows(str(path))
    assert len(repo._rows_cache) <= ArtistRepository._MAX_CACHE


def test_csv_max_rows_stops_reading_early(tmp_path, monkeypatch):
    """CSV_MAX_ROWS 必须**读满即停**，而不是读完再切片。

    回归防线：护栏的意义是在文件异常巨大时止损。若实现改成"全读再切片"，
    结果行数看起来一样（100 行），但代价从读 100 行变成读 40 万行——
    实测 6 MB / 40 万行时 0.019 s 变 0.68 s（约 36 倍）。
    这里用"实际被消费的行数"来断言，不依赖计时（避免环境抖动）。
    """
    import ComfyUI_RollingArtist.ra_core.artists as artists_mod

    path = tmp_path / "huge.csv"
    path.write_text("\n".join(f"a{i},a{i}" for i in range(5000)), encoding="utf-8")

    consumed = {"n": 0}
    real_reader = artists_mod.csv.reader

    def counting_reader(source, *args, **kwargs):
        for row in real_reader(source, *args, **kwargs):
            consumed["n"] += 1
            yield row

    monkeypatch.setattr(artists_mod.csv, "reader", counting_reader)
    monkeypatch.setattr(artists_mod, "CSV_MAX_ROWS", 50)
    repo = ArtistRepository()
    assert len(repo.load_rows(str(path))[0]) == 50
    assert consumed["n"] <= 60, f"截断时读了 {consumed['n']} 行，说明没有提前停止（全量 5000 行）"


def test_invalid_encoding_degrades_gracefully_on_both_paths(tmp_path):
    """非法 UTF-8 文件在两条路径上都应记日志并返回空，而不是抛异常。

    回归防线：解码错误只在**迭代行**时抛出（``open()`` 不会），
    因此异常兜底必须包住整个消费过程。早期实现把 ``except UnicodeDecodeError``
    放在 ``open()`` 周围（死代码），一旦读取期真的抛错就会直接冒到节点层。
    """
    path = tmp_path / "bad.csv"
    path.write_bytes(b"artist,trigger\nfoo\xff\xfe,bar\n")

    repo = ArtistRepository()
    assert repo.load(str(path))[0] == []
    assert repo.load_rows(str(path))[0] == []


def test_missing_and_directory_paths_return_empty(tmp_path):
    """不存在路径与目录路径都必须优雅降级为空结果（两条路径一致）。"""
    repo = ArtistRepository()
    for bad in (tmp_path / "none.csv", tmp_path):
        assert repo.load(str(bad))[0] == []
        assert repo.load_rows(str(bad))[0] == []
