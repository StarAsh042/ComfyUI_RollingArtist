# 性能基线（BENCHMARKS）

> 测量方法、环境与实测数据，供优化前后对比与回归参照。
> **防回归原则：测试只断言行为（如索引复用同一性），不断言绝对耗时**——绝对计时在慢机器上会 flaky（项目既有纪律，见 `docs/iteration/ITERATION_PLAN.md` T-11）。

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

## 行为级防回归测试（不依赖绝对计时）

| 测试 | 断言 | 守护的行为 |
|------|------|------------|
| `tests/test_character_index.py::test_index_is_cached_between_calls` | 两次 `load_index` 返回**同一对象**（`first is second`） | 索引复用（5.1.0 优化的核心，防回归） |
| `tests/test_character_index.py::test_index_rebuilds_when_csv_changes` | CSV 变化后索引重建 | 缓存键含文件签名，换数据必刷新 |
| `tests/test_character_index.py::test_copyright_menu_rebuilds_when_csv_changes` | 作品下拉随签名重建 | TD-08：换 CSV 后下拉无需重启即刷新 |
| `tests/test_node_cache.py`（13 项） | 指纹随文件变化 / 不变时稳定 / 防护拒绝返回哨兵 | IS_CHANGED 缓存键行为 |
