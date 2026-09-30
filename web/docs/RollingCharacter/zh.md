# 滚动角色（Rolling Character）

从角色 CSV（默认 `danbooru_character_001.csv`）中随机抽取角色，按
`角色标签,作品标签,core_tags` **逐行输出**（多个角色换行分隔），并输出结构化 JSON 与已测数量。
输出未被下游使用时节点仍会执行。

- **输出 1 `prompt`**：每个角色一行，例如
  ```
  hakurei_reimu,touhou,1girl,brown eyes,long hair
  ganyu_\(genshin_impact\),genshin_impact,1girl,horns
  ```
  **不带权重**，不会出现 `(名称:权重)` 这种写法
- **输出 2 `characters_json`**：`{"characters": [{"name","copyright","top","count","core_tags"}], "status": "OK"}`
- **输出 3 `tested_count`**：当前数据库里已记录的角色组合数量

## 数据来源与 trigger 拆解

默认读 `trigger` 列。该列经 34,416 行全量校验是**恒定的两段结构**：

| 段 | 内容 | 校验结果 |
|---|---|---|
| 第 1 段 | 角色标签（已转义，可直接使用） | == 转义后的 `character` 列：34,416/34,416 |
| 第 2 段 | 作品标签（如 `touhou`） | == 转义后的 `copyright` 列：34,416/34,416 |

读取的列固定为 `trigger`（不可配置）：该列恒为上述两段结构，因此不需要选列参数。

## 输出格式

每个角色一行，字段之间用英文逗号连接：

```
角色标签,作品标签,core_tags
```

关闭「拼入外观标签」开关后变成：

```
角色标签,作品标签
```

- **空的部分自动省略**：没有作品标签或没有外观标签时不会留下多余逗号
- 外观标签在**行内去重**（源数据里存在同一角色重复列同一标签的情况）
- 多个角色用**换行**分隔，行的顺序就是抽样顺序

## 参数

| 参数 | 类型 / 范围 | 说明 |
|---|---|---|
| `character_count` | INT 1-10 | 每次生成的角色数量（1-10），每个角色一行 |
| `character_top_ratio` | FLOAT 0.01-1.0 | 人气池占比：按 CSV 行序取前 `ceil(候选数×比例)` 个；候选数 = 作品过滤之后的角色数 |
| `character_top_count` | INT 1-10 | 每次至少抽取几个人气池角色 |
| `seed` | INT | 随机种子 |
| `use_top_priority` | BOOLEAN | 优先从人气池抽角色 |
| `exclude_characters` | STRING | 排除名单，英文逗号分隔 |
| `force_include` | STRING | 必定出现的角色；不在 CSV 里的名字也能用，只是没有作品与外观标签 |
| `copyright_filter` | STRING | 只从这些作品抽（如 `touhou,vocaloid`）。同时接受 `fate_(series)` 与 `fate_\(series\)`，忽略大小写；过滤后为空会报错 |
| `include_core_tags` | BOOLEAN | 是否把角色的固定外观标签拼进每一行（默认开启） |
| `dedup_mode` | ENUM | `none` / `full_prompt` / `artist_set` / `artist_list` |
| `max_attempts` | INT 1-100 | 去重模式下的最大重试次数 |
| `custom_csv_path` | STRING | 自定义角色 CSV 路径；留空使用 `danbooru_character_001.csv` |
| `tested_db_path` | STRING | 记录数据库路径；留空使用 `rollingcharacter.sqlite`（与画师节点分开） |

角色节点**不做权重分配**（因此没有权重相关参数）、**不做穷举**（角色是单词条），
输出格式固定，只保留「拼入外观标签」这一个格式开关。

## 注意事项

- **约 22% 的角色没有外观标签**（`core_tags` 为空），这些角色的行里只有角色标签与作品标签
- 每个角色平均约 10 个外观标签，角色多时输出行会明显变长
- 角色名唯一（34,416 行无重名），因此名字可直接对应到记录
- 去重键按「名字 + 权重」计算，但角色不带权重，所以键里统一按 `1.0` 计算
  （对 `artist_set` / `artist_list` 无影响；`full_prompt` 等价于「顺序完全相同」）
- 记录带 `kind=character` 标记，与画师节点的记录互不冒充
- 默认数据库每次启动 ComfyUI 会被清理；需要跨会话保留去重进度，请显式设置 `tested_db_path`
