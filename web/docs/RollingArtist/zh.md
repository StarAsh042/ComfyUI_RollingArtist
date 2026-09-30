# 滚动艺术家（Rolling Artist）

从艺术家 CSV 中随机抽取艺术家、为每人分配随机权重，输出带权重的提示词与结构化 JSON。
输出未被下游使用时节点仍会执行（输出节点）。

- **输出 1 `prompt`**：提示词，例如 `(artist:xxx:0.8),(artist:yyy:0.6)`
- **输出 2 `artists_json`**：`{"artists": [{"name", "weight", "top"}], "status"}`，`status` 为 `OK` 或 `ALL_COMBINATIONS_TESTED`
- **输出 3 `tested_count`**：当前数据库里已记录的组合数量
- **输出 4 `remaining_count`**：穷举模式下组合池剩余数量；非穷举模式恒为 `0`

## 数据源与取列

默认读取节点目录下的 `danbooru_art_001.csv`（表头 `artist,trigger,count`），
**固定读 `trigger` 列**：该列已转义，可直接进提示词（如 `hammer_\(sunset_beach\)`）。
CSV 里没有 `trigger` 列时退回首列并记 WARNING（此时可能是未转义的原始标签）。

## 抽样与权重

| 参数 | 类型 / 范围 | 说明 |
|---|---|---|
| `artist_count` | INT 1-10 | 每次生成的艺术家数量。`模式=自动` 时，设为 1 且“强制包含”为空会进入 **穷举模式** |
| `artist_top_ratio` | FLOAT 0.01-1.0 | Top 池占比：按 CSV 行序取前 `ceil(总数×比例)` 个（至少 1 个），行序越靠前通常作品越多 |
| `artist_top_count` | INT 1-10 | 每次至少抽取几个 Top 池艺术家（仅在“Top 优先抽样”开启且 Top 池非空时生效） |
| `weight_min` | FLOAT 0.1-2.0 | 单个艺术家的权重下限；与上限写反会自动交换，负数夹到 0 |
| `weight_max` | FLOAT 0.1-2.0 | 单个艺术家的权重上限 |
| `weight_total` | FLOAT 0-20 | 所有权重之和，按总和精确分配，超出可行区间夹到边界（穷举模式不生效） |
| `weight_curve` | ENUM | 分配曲线：`flat` 平坦（默认）/ `dominant` 主次分明 / `ramp` 阶梯。只影响谁分得多，不影响上下限与总和 |
| `artists_prefix` | BOOLEAN | 开启输出 `artist:名称`，关闭只输出名称本身 |
| `seed` | INT | 随机种子；同一种子在同一组参数下结果可复现 |

## 生成模式

| `mode` | 是否穷举 | 条件 |
|---|---|---|
| `auto`（默认） | `artist_count = 1` 且“强制包含”为空时穷举 | 与 3.2.x 行为一致 |
| `random` | 从不穷举 | 只想抽 1 个艺术家时用这个 |
| `exact` | 总是穷举 | 要求 `artist_count = 1` 且“强制包含”为空，否则**直接报错** |

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
| `custom_csv_path` | STRING | 自定义艺术家 CSV 路径；留空使用节点目录下的 `danbooru_art_001.csv` |
| `tested_db_path` | STRING | 记录数据库（SQLite）路径；留空使用节点目录下的 `rollingartist.sqlite`。已测记录与穷举组合池都在这个文件里，更换路径相当于重置去重进度 |

## 两种工作模式

- **常规模式**：按参数随机抽取并分配权重，命中已测组合时重试（最多 `max_attempts` 次）。
- **穷举模式**（`artist_count=1` 且“强制包含”为空，或 `模式=穷举`）：按 `(艺术家, 权重)` 组合逐个不重复输出，
  组合池持久化在数据库里；全部穷举后返回 `ALL_COMBINATIONS_TESTED`。
  该模式下 `dedup_mode` 与 `weight_total` **不生效**（固定按 `full_prompt` 判定，权重按 0.1 网格枚举）。
  组合总量超过 100 万时不建立组合池，改为「随机采样 + 去重」（此时 `remaining_count` 恒为 0）。

## 记录数据库（SQLite）

- 默认数据库位于节点目录：`rollingartist.sqlite`（含 `-wal` / `-shm` 附带文件）。
- **每次启动 ComfyUI 会自动清理默认数据库**（由 `prestartup_script.py` 完成），保持每次运行干净；
  想要跨会话接着去重 / 穷举，请把 `tested_db_path` 指到自己的文件（该路径不会被清理）。
- 「判重 → 生成 → 记录」处于同一个数据库事务中，进程内多线程、**多开 ComfyUI 或多进程**都不会产生重复组合。
- 判重走索引查询，历史再大也不占内存；艺术家名里含 `|`、`:`、逗号也不会串行。
- 记录条数没有上限，也不会自动裁剪；默认路径每次启动会被清空。
- `custom_csv_path` 与 `tested_db_path` 是原样读写的路径：自己单机使用没问题，
  但如果 ComfyUI 对局域网或公网开放，任何人都可以借此读写你硬盘上的任意文件，请自行加访问控制。
