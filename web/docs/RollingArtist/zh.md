# 滚动艺术家（Rolling Artist）

从艺术家 CSV 中随机抽取艺术家、为每人分配随机权重，输出带权重的提示词与结构化 JSON。
输出未被下游使用时节点仍会执行（输出节点）。

- **输出 1 `prompt`**：提示词，例如 `(artist:xxx:0.8),(artist:yyy:0.6)`
- **输出 2 `artists_json`**：`{"artists": [{"name", "weight", "top"}], "status"}`，`status` 为 `OK` 或 `ALL_COMBINATIONS_TESTED`

## 抽样与权重

| 参数 | 类型 / 范围 | 说明 |
|---|---|---|
| `artist_count` | INT 1-10 | 每次生成的艺术家数量。设为 1 且“强制包含”为空时进入 **Exact 穷举模式** |
| `artist_top_ratio` | FLOAT 0.01-1.0 | Top 池占比：按 CSV 行序取前 `ceil(总数×比例)` 个（至少 1 个），行序越靠前通常作品越多 |
| `artist_top_count` | INT 1-10 | 每次至少抽取几个 Top 池艺术家（仅在“Top 优先抽样”开启且 Top 池非空时生效） |
| `weight_min` | FLOAT 0.1-2.0 | 单个艺术家的权重下限；与上限写反会自动交换，负数夹到 0 |
| `weight_max` | FLOAT 0.1-2.0 | 单个艺术家的权重上限 |
| `weight_total` | FLOAT 0-20 | 所有权重之和，按总和精确分配，超出可行区间夹到边界（Exact 模式不生效） |
| `artists_prefix` | BOOLEAN | 开启输出 `artist:名称`，关闭只输出名称本身 |
| `seed` | INT | 随机种子；同一种子在同一组参数下结果可复现 |

## 抽样偏好、名单与去重

| 参数 | 类型 / 范围 | 说明 |
|---|---|---|
| `use_top_priority` | BOOLEAN | 优先从 Top 池抽人；关闭后从完整 CSV 均匀抽样 |
| `sort_by_weight` | BOOLEAN | 按权重从高到低重排提示词中的艺术家顺序（影响 `full_prompt` 去重键） |
| `exclude_artists` | STRING | 排除名单，英文逗号分隔 |
| `force_include` | STRING | 必定出现的艺术家，英文逗号分隔；优先级高于排除名单 |
| `dedup_mode` | ENUM | 去重依据：`none` / `full_prompt` / `artist_set` / `artist_list` |
| `max_attempts` | INT 1-100 | 去重模式下的最大重试次数；仍全部命中已测组合时接受最后一次结果并输出 WARNING |

## 高级参数（界面默认折叠）

| 参数 | 类型 | 说明 |
|---|---|---|
| `csv_column` | STRING | 取列方式：`auto`（自动识别表头，优先 artist/character/name/tag）、`all`（展平所有列）、列序号或列名 |
| `custom_csv_path` | STRING | 自定义艺术家 CSV 路径；留空使用节点目录下的 `danbooru_art_001.csv` |
| `tested_csv_path` | STRING | 已测记录文件路径；留空使用节点目录下的 `tested_combinations.csv`，更换路径相当于重置去重进度 |

## 两种工作模式

- **常规模式**：按参数随机抽取并分配权重，命中已测组合时重试（最多 `max_attempts` 次）。
- **Exact 穷举模式**（`artist_count=1` 且“强制包含”为空）：按 `(艺术家, 权重)` 组合逐个不重复输出，
  组合池持久化在 `*_remaining.csv`；全部穷举后返回 `ALL_COMBINATIONS_TESTED`。
  该模式下 `dedup_mode` 与 `weight_total` **不生效**（固定按 `full_prompt` 判定，权重按 0.1 网格枚举）。

## 记录文件

- 默认记录文件位于节点目录：`tested_combinations.csv` 与 `tested_combinations_remaining.csv`。
- **每次启动 ComfyUI 会自动清理这两个默认文件**（由 `prestartup_script.py` 完成），保持每次运行干净；
  想要跨会话接着去重，请把 `tested_csv_path` 指到自己的文件（该路径不会被清理）。
- 同一 `tested_csv_path` 上的“读取已测 → 生成 → 记录”处于同一把进程内共享锁中，
  多个实例/并发线程不会产生重复组合。
