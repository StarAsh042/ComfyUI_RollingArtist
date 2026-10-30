# 性能基线（BENCHMARKS）

> 测量方法、环境与实测数据，供优化前后对比与回归参照。
> **防回归原则：测试只断言行为（如索引复用同一性），不断言绝对耗时**——绝对计时在慢机器上会 flaky（项目既有纪律）。
> 本文引用的测试文件位于维护者本地 `tests/` 目录——测试与项目文档自 5.1.6 起仅保留在本地工作树，不随远端仓库分发。

## 测量环境

| 项 | 值 |
|----|----|
| 机器 | Windows 11 x64，16GB RAM |
| Python | 3.13.14（managed venv） |
| 默认数据 | `danbooru_art_001.csv`（50,018 行）/ `danbooru_character_001.csv`（34,416 行） |
| 复测日期 | 2026-10-25（代码：v5.1.0） |

## 复测方法（角色节点）

不走 ComfyUI，直接实例化节点（测试同理，不依赖 ComfyUI）；`tested_db_path` 指向临时文件，避免打扰运行中的实例：

```python
import sys, tempfile, time
from pathlib import Path

sys.path.insert(0, r"<仓库父目录>/custom_nodes")
from ComfyUI_RollingArtist.RollingCharacter import RollingCharacter

node = RollingCharacter()
params = dict(
    character_count=3,
    character_top_ratio=0.01,
    character_top_count=1,
    seed=42,
    tested_db_path=str(Path(tempfile.mkdtemp()) / "bench.sqlite"),
)
# 冷一次（构建索引 + 建库），再温跑 N 次换 seed，取 perf_counter 差值
```

## 数据

| 指标 | 5.0.0（作者实测） | v5.1.0（2026-10-25 复测） | 说明 |
|------|------------------|---------------------------|------|
| 角色节点单次执行（温） | 62.6 ms | 16.0 ~ 23.9 ms（p50 18.1 ms） | 5.1.0 起派生索引（名字列表 / 名字→记录 / 作品筛选下标）随记录缓存，不再每次在 34,416 条上重建 |
| 角色节点首次执行（冷） | — | 71.9 ms | 复测口径含临时库初始化；5.0.0 侧无同口径数据 |
| 画师节点缓存命中 | 0.23 ms | — | 未复测；文件签名缓存命中路径 |
| 画师节点冷加载 CSV | 9.6 s | — | 50,018 行整表解析（首节点执行） |
| 角色节点冷加载 CSV | 1.8 s | — | 34,416 行整表解析 |
| `generation_lock` 单次持锁 | ~9 ms | — | 3 个艺术家、默认参数；重试 10 次约 90 ms（持锁范围见 `ra_core/db.py` docstring） |

口径说明：5.0.0 列来自 `perf/character-derived` 分支提交信息的作者实测；v5.1.0 列为本次复测，机器与方法不同，绝对值不可直接对比，量级与方向（温跑从 60ms 级降到 20ms 级）一致。

## v5.2.0 流式读取改造：同机新旧对照（2026-10-28 实测）

**测量方法**：同一台机器、同一 Python（3.11.6 venv）、同一份数据文件，新旧代码**交替**各跑一遍。
旧版用 `git show HEAD:<path>` 取出 `8a1b62c`（tag `v5.1.7`）的 `ra_core/` 与节点文件放到
`%TEMP%/ra-old/ComfyUI_RollingArtist/`，通过 `sys.path` 切换导入来源；两版读**完全相同**的
`danbooru_art_001.csv`（50,018 行）/ `danbooru_character_001.csv`（34,416 行）。
`tracemalloc` 计量 Python 堆，冷加载各跑一次（含解析与对象构造）。

| 指标 | 旧版 v5.1.7 | v5.2.0 | 变化 |
|------|------------|--------|------|
| 画师冷加载 **峰值** | 13.30 MB | **3.39 MB** | **−74.5%** |
| 画师冷加载 **常驻** | 3.36 MB | 3.35 MB | 持平（终态 artists 列表本就必须常驻） |
| 画师冷加载耗时 | 1523.2 ms | 1545.4 ms | +1.5%（在噪声内；终态解析量未变） |
| 画师暖缓存 p50 | 0.93 ms | 0.83 ms | 持平 |
| 角色冷加载 **峰值** | 27.26 MB | **16.46 MB** | **−39.6%** |
| 角色冷加载 **常驻** | 26.86 MB | **16.07 MB** | **−40.2%** |
| 角色冷加载耗时 | 3129.7 ms | **2703.0 ms** | **−13.6%** |
| 角色暖缓存 | 2.62 ms | 2.08 ms | 持平 |
| 角色路径 `_rows_cache` 条目数 | 1 | **0** | AC-C06 达成 |

**解读**：

- 画师峰值降 74.5% 来自消除「整份原始行列表」这一中间层——改造前 `_read` 先 `_csv_rows(path)`
  把 50,018 行读成 `List[List[str]]`（约 10 MB），再交给解析器产出 artists；改造后记录直接流进
  解析器，中间层不再存在。**常驻**持平是预期的：终态 artists 列表本身就是 O(N)，本版不改变它。
- 角色峰值/常驻降约 40% 来自两点：① 不再经 `load_rows()` 把原始行写进 `_rows_cache`（少一份
  `List[List[str]]`）；② `load_index` 不再为拿 stat 而先 `load_rows()`。**常驻**也降了，因为
  `_rows_cache` 是长期驻留的（不像画师的中间列表用完即弃）。
- 角色冷加载耗时降 13.6% 是附带收益：少解析一遍原始行结构。
- **不宣称**「内存占用降低 74%」这种笼统说法——上面每一行都对应具体指标与具体原因。

**上限的效果**（同一台机器，画师 CSV 只读前 1000 条记录）：

| 指标 | 全量（max_rows=0） | max_rows=1000 |
|------|-------------------|---------------|
| 冷加载耗时 | 1545 ms | **32.6 ms**（−97.9%） |
| 峰值内存 | 3.39 MB | **0.10 MB** |
| 解析出艺术家数 | 50,018 | 999 |

## 行为级防回归测试（不依赖绝对计时）

| 测试 | 断言 | 守护的行为 |
|------|------|------------|
| `tests/test_character_index.py::test_index_is_cached_between_calls` | 两次 `load_index` 返回**同一对象**（`first is second`） | 索引复用（5.1.0 优化的核心，防回归） |
| `tests/test_character_index.py::test_index_rebuilds_when_csv_changes` | CSV 变化后索引重建 | 缓存键含文件签名，换数据必刷新 |
| `tests/test_character_index.py::test_copyright_menu_rebuilds_when_csv_changes` | 作品下拉随签名重建 | TD-08：换 CSV 后下拉无需重启即刷新 |
| `tests/test_artists.py::test_read_stops_early_on_load_path` | `load()` 路径实际消费记录数 ≤ N+1 | v5.2.0：上限在两条路径都真生效（改造前 `load()` 读满全文） |
| `tests/test_artists.py::test_max_rows_enters_cache_key` | 改上限必须重建、同上限必须命中 | v5.2.0：上限进入 5 层缓存键 |
| `tests/test_artists.py::test_exact_n_does_not_claim_truncation` | 恰好 N 条不告警、超 N 才告警 | v5.2.0：告警准确性（改造前用 `>=` 会误报） |
| `tests/test_characters.py::test_build_records_single_pass_handles_generator` | 生成器输入与列表输入结果完全一致 | v5.2.0：`build_records` 单次遍历（改造前喂生成器会静默丢首条） |
| `tests/test_characters.py::test_character_load_does_not_cache_raw_rows` | 角色正常加载后 `_rows_cache == {}` | v5.2.0：AC-C06 原始行不常驻 |
| `tests/test_node_cache.py`（13 项） | 指纹随文件变化 / 不变时稳定 / 防护拒绝返回哨兵 | IS_CHANGED 缓存键行为 |
