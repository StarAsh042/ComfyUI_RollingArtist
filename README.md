# ComfyUI RollingArtist

本包提供两个 ComfyUI 节点，用于生成包含随机权重的提示文本，通过动态调整 Top 比例实现可控的随机组合：

- **RollingArtist（滚动艺术家）**：从艺术家 CSV 随机抽人 + 分配随机权重
- **RollingCharacter（滚动角色）**：从角色 CSV 随机抽人（**不带权重**），
  额外带**作品标签**与**角色描述**（源数据列 `core_tags`），并支持按作品筛选

两者共用同一套抽样、权重分配、去重与记录机制；下面先讲画师节点，
角色节点的差异集中在「[RollingCharacter（角色）](#rollingcharacter角色)」一节。

## 核心功能

- **动态控制**：定义 Top 艺术家范围，控制实际输出数量
- **权重分配**：精确控制每个艺术家的权重范围和总和，并可选择**分配曲线**（平坦 / 主次分明 / 阶梯）
- **去重**（画师节点）：4 种去重模式跳过已生成过的组合；「判重 → 生成 → 记录」跑在同一个 SQLite 事务里，
  同进程多线程、多开 ComfyUI 都不会重复出图
- **作品下拉**（角色节点）：列出 CSV 里的全部作品（默认约 3,460 个），可打字筛选、忽略大小写
- **重复回避**（角色节点）：最近 10 条输出记录内角色不重复，无需配置去重模式
- **进度可见**：直接输出「已测数量」（**字符串**，可接到文本节点查看）
- **种子控制**：统一随机种子确保结果可复现
- **界面**：中英文自动切换，每个参数带 tooltip，右侧「信息」面板提供随语言切换的帮助页

## 代码结构

核心逻辑按职责拆分为 `ra_core/` 子包（**不依赖 ComfyUI**，可单独导入与测试），
两个节点文件只保留接口定义与单次生成的编排：

```
ComfyUI_RollingArtist/
├── RollingArtist.py · RollingCharacter.py   # 两个节点：输入输出定义 + 生成编排
├── ra_core/                                 # 核心实现（纯逻辑）
│   ├── artists.py · characters.py           # CSV 解析与加载缓存、抽样；角色数据与作品筛选
│   ├── weights.py · db.py                   # 权重分配与提示词拼接；SQLite 记录库
│   └── params.py · constants.py · storage.py · startup.py
├── danbooru_csv_tool.py · modify_danbooru_*.py   # 数据加工（共享实现 + 两个脚本）
├── locales/ · web/docs/                     # 界面文案与节点帮助页（中英）
├── prestartup_script.py                     # 启动钩子：清理上次运行留下的默认记录文件
└── tests/                                   # pytest：纯逻辑 + 节点层冒烟 + 加工脚本 + 跨进程
```

## 安装指南

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/StarAsh042/ComfyUI_RollingArtist.git
```

仓库自带两个数据文件，克隆下来即可用：`danbooru_art_001.csv`（画师，`artist,trigger,count`）与
`danbooru_character_001.csv`（角色，`character,copyright,trigger,core_tags,count`）。
要换成自己的数据，用 `modify_danbooru_art.py` / `modify_danbooru_character.py` 加工（见[数据预处理](#数据预处理)）。

`ra_core/`（相对导入）、`locales/`、`web/docs/` 都由 ComfyUI 按目录约定自动加载，无需注册；
`locales/` 或 `web/` 缺失只影响界面文案，不影响功能。

运行测试（可选）：

```bash
pip install -e ".[dev]"
pytest
```

## 界面语言与节点帮助页

- **中英文自动切换**：节点显示名、参数名与参数说明（tooltip）跟随 ComfyUI 的界面语言
  （设置里的 `Comfy.Locale`）。中文来自 `locales/zh/`，英文来自 `locales/en/`，
  ComfyUI 启动时按目录约定读取（`GET /i18n`），代码里无需注册。
- **节点帮助页**：右侧「信息」面板的内容取自 `web/docs/`，前端按
  `/extensions/ComfyUI_RollingArtist/docs/RollingArtist/<语言>.md` 请求，该语言缺失时回退到
  `web/docs/RollingArtist.md`。面板里同时会给出各参数的名称与说明。
- **新增语言**：复制 `locales/en/` 为 `locales/<语言代码>/` 并翻译 `nodeDefs.json`
  （语言代码用官方集合：`zh` / `zh-TW` / `ja` / `ko` / `fr` / `ru` / `es` / `ar` …）；
  帮助页同理新增 `web/docs/RollingArtist/<语言代码>.md`。
- 默认文案（无 i18n 时显示的内容）写在 `RollingArtist.py` 的 `INPUT_TYPES`（`tooltip` 键）里。

## RollingArtist（画师）

下列顺序即界面上参数的出现顺序；`advanced` 参数在界面里折叠在“高级输入”中。

| 参数 | 类型 | 范围 | 说明 |
|------|------|------|------|
| 艺术家数量（`artist_count`） | INT | 1-10 | 选择生成的艺术家人数（默认 3） |
| Top 池占比（`artist_top_ratio`） | FLOAT | 0.01-1.0 | 提取 CSV 中 Top 艺术家占前百分比（默认 0.01） |
| Top 抽取数量（`artist_top_count`） | INT | 1-10 | 输出中包含的 Top 艺术家数量（默认 1） |
| 权重下限（`weight_min`） | FLOAT | 0.1-2.0 | 单个艺术家最小权重值 |
| 权重上限（`weight_max`） | FLOAT | 0.1-2.0 | 单个艺术家最大权重值 |
| 权重总和（`weight_total`） | FLOAT | 0.0-20.0 | 所有权重总和（默认 2.0） |
| 权重分配曲线（`weight_curve`） | ENUM | flat / dominant / ramp | 权重分配曲线（默认 flat） |
| artist: 前缀（`artists_prefix`） | BOOLEAN | - | 添加 "artist:" 前缀 |
| 随机种子（`seed`） | INT | 0-4294967295 | 控制随机性的种子值 |

### 可选参数

- Top 优先抽样（`use_top_priority`）：BOOLEAN，是否启用 Top 优先池（默认开启）。关闭后从完整 CSV 中均匀抽取，不再强制包含 Top 艺术家
- 按权重排序（`sort_by_weight`）：BOOLEAN，是否按权重降序排序（默认开启）
- 排除艺术家（`exclude_artists`）：STRING，逗号分隔的排除名单（精确匹配）
- 强制包含（`force_include`）：STRING，逗号分隔的强制包含名单。这些艺术家一定出现在输出中，优先级高于 `exclude_artists`；数量超过 `artist_count` 时从中随机抽取
- 去重模式（`dedup_mode`）：ENUM（`none` / `full_prompt` / `artist_set` / `artist_list`，默认 `none`），跳过已生成过的组合
  - `none`：不去重（仍然会记录），想要纯随机就留这个
  - `full_prompt`：艺术家**顺序与权重**完全一致才算重复（最严格）
  - `artist_set`：仅比较艺术家**集合**，忽略顺序与权重
  - `artist_list`：比较**排序后**的艺术家名，忽略权重（与 `artist_set` 基本等价，
    区别只在名字重复时是否折叠）
  - 各模式的完整对比（比较内容 / 什么算重复 / 适用场景）见
    [`web/docs/RollingArtist/zh.md`](web/docs/RollingArtist/zh.md) 的「去重模式详解」
- 最大重试次数（`max_attempts`）：INT（1-100，默认10），去重时的最大重试次数；超过后接受最后一次结果，避免死循环

### 高级参数（界面默认折叠）

- 自定义 CSV 路径（`custom_csv_path`）：STRING，自定义 CSV 路径（留空使用默认）
- 记录数据库路径（`tested_db_path`）：STRING，记录数据库（SQLite）路径。留空使用节点目录下 `rollingartist.sqlite`；
  已测记录存在这个文件里，**更换该路径相当于重置去重进度**

### CSV 取列规则（固定读 `trigger` 列，不可配置）

画师节点**固定读取 `trigger` 列**（已转义，可直接进提示词，如 `hammer_\(sunset_beach\)`），
默认的 `danbooru_art_001.csv` 表头是 `artist,trigger,count`。

- CSV 里没有 `trigger` 列时会**回退到首列**并记一条 WARNING（此时可能是未转义的原始标签）。
  如果输出里出现数字或网址（把 `count`、`url` 当成了艺术家名），就是这种情况，检查表头拼写
- 需要读别的列请改数据文件（用加工脚本重新生成一份带 `trigger` 的 CSV）

## RollingCharacter（角色）

从角色 CSV（默认 `danbooru_character_001.csv`）抽取角色，按
`角色标签,作品标签,角色描述` **逐行输出**（多个角色之间用「逗号+换行」分隔），**不带权重**。

### 数据来源与 trigger 拆解

读取的列固定为 `trigger`（不可配置）。该列经 34,416 行全量校验是**恒定的两段结构**：
第 1 段 = 转义后的 `character` 列（角色名，可直接进提示词），
第 2 段 = 转义后的 `copyright` 列（作品标签），两段均 **34,416/34,416** 完全吻合。

### 输出格式

**不带权重**，每个角色一行，字段用英文逗号连接：

```
角色标签,作品标签,角色描述
```

```
hakurei_reimu,touhou,1girl,brown eyes,long hair,
ganyu_\(genshin_impact\),genshin_impact,1girl,horns
```

关闭「开启角色描述」后变成 `角色标签,作品标签`：

```
hakurei_reimu,touhou
```

- 空的部分自动省略：没有作品标签或没有角色描述时不会留下多余逗号
- 角色描述（`core_tags`）在**行内去重**（源数据里同一角色可能重复列同一个标签）
- 多个角色用**「逗号 + 换行」**分隔：行尾补逗号（最后一行不补），
  拼成一行时前后两个角色的标签不会粘在一起；单个角色不带尾逗号。行的顺序就是抽样顺序

### 参数

与画师节点同名同义的部分不重复列举，差异如下（`character_*` 对应 `artist_*`）：

| 参数 | 类型 / 范围 | 说明 |
|------|-------------|------|
| 角色数量（`character_count`） | INT 1-10 | 每次生成的角色数量（默认 1），每个角色一行 |
| 人气池占比（`character_top_ratio`） | FLOAT 0.01-1.0 | 人气池占比；候选数 = 作品筛选之后的角色数 |
| 人气池抽取数量（`character_top_count`） | INT 1-10 | 每次至少抽几个人气池角色 |
| 随机种子（`seed`） | INT | 随机种子 |
| Top 优先抽样（`use_top_priority`） | BOOLEAN | 优先从人气池抽角色 |
| 排除角色（`exclude_characters`） / 强制包含（`force_include`） | STRING | 名单；强制包含允许写不在 CSV 里的名字 |
| 作品（`copyright_pick`） | COMBO | 作品下拉：列出 CSV 里的**全部作品**（默认数据约 3,460 个，按作品热度即 count 总和降序，并列按名字升序），首项 `(不限)`；下拉可打字筛选、忽略大小写；同时接受 `fate_(series)` 与 `fate_\(series\)`。下拉项来自默认 CSV，换 `custom_csv_path` 不会跟着变 |
| 开启角色描述（`describe_character`） | BOOLEAN | 开启时每行末尾拼上**角色描述**（源数据列 `core_tags`）；默认开启 |
| 自定义 CSV 路径（`custom_csv_path`） | STRING | 留空使用 `danbooru_character_001.csv` |
| 记录数据库路径（`tested_db_path`） | STRING | 留空使用 `rollingcharacter.sqlite`（与画师节点分开）；只存历史记录，换路径不会重置回避窗口 |

角色节点**不做权重分配**，因此没有 `weight_min` / `weight_max` / `weight_total` / `weight_curve` /
`sort_by_weight`；输出格式固定，因此也没有 `characters_prefix`，只保留 `describe_character`
这一个内容开关；**没有去重模式参数**，重复回避改用一个固定规则的窗口（见下）。

### 重复回避：最近 10 条输出记录内不重复

- 窗口保存**最近 10 条输出记录**，每条 = 一次生成（可能含多个角色）；
  抽取前把窗口里的角色名从候选里排除。`character_count=1` 连抽 10 次会得到 10 个不同角色；
  `character_count=3` 相当于「最近 30 个角色内不重复」
- 窗口放在**节点实例的内存**里：重启 ComfyUI 清零，同一工作流里的多个角色节点各算各的；
  外部脚本可用 `node.reset_recent()` 清空、`node.recent_names()` 查看
- 窗口把候选全部吃掉时（例如只选了个很小的作品），退回「允许重复」并打 WARNING 日志，不报错

### 输出

| 输出 | 说明 |
|------|------|
| 角色列表（`prompt`） | 每个角色一行：`角色标签,作品标签,角色描述`（不带权重；关掉开关后为 `角色标签,作品标签`） |
| 角色 JSON（`characters_json`） | `[{"name","copyright","top","count","core_tags"}]` |
| 已测数量（`tested_count`） | 当前数据库里已记录的角色组合数量（字符串，可接到文本节点查看） |

### 注意事项

- **约 22% 的角色没有角色描述**（源数据 `core_tags` 为空），这些行里只有角色标签与作品标签；
  每个角色平均约 10 个标签，角色多时输出会明显变长
- 角色名唯一（34,416 行无重名），因此名字可直接对应到记录
- 记录带 `kind=character` 标记，且权重列统一按 `1.0` 占位：与画师节点共用一个数据库也不会互相冒充

## 记录与去重（SQLite）

- 记录库是单个 SQLite 文件（WAL 模式），存放**已测记录**；判重键在写入时一次算好并建索引，
  因此判重是索引查询，历史再大也不占内存
- 「判重 → 生成 → 记录」整段在同一个 `BEGIN IMMEDIATE` 事务里执行：同进程多线程靠可重入锁，
  多开 ComfyUI / 多进程靠 SQLite 文件锁（等待上限 10 秒），都不会生成重复组合
- 艺术家名与权重以 JSON 存列，名字里含 `|`、`:`、逗号都不会破坏解析
- 记录条数没有上限也不会自动裁剪；**默认路径的记录文件每次启动会被清理**
  （`prestartup_script.py` 会删除节点目录下的 `rollingartist.sqlite` / `rollingcharacter.sqlite`
  及 `-wal` / `-shm`，外加旧版残留的 `tested_combinations.csv` 等）。想跨会话保留去重进度，
  把 `tested_db_path` 指到自己的文件——**只清理默认路径**，显式指定的路径不会被碰

## 数据安全与路径提醒

`custom_csv_path` 与 `tested_db_path` 是**原样读写**的路径，节点不做任何目录限制：

- 自己单机使用没有问题
- 但如果这台机器的 ComfyUI 对局域网或公网开放，任何人都可以通过 API 让节点读写你硬盘上的任意文件
  （读 CSV、写数据库与临时文件）

如果属于后者，建议：给 ComfyUI 加上访问控制（如反向代理鉴权），或至少不要在公网暴露带工作流执行权限的接口。
另外：同一个记录数据库**不要**同时被多个 ComfyUI 实例共用同一个网络盘（WAL 模式要求本地文件系统）。

## 去重的注意事项

- 去重依赖 `tested_db_path` 的历史记录，更换该路径相当于重置去重状态
- 重试耗尽时（连续 `max_attempts` 次都命中已测组合）会接受最后一次结果并打一条 WARNING 日志，
  便于排查「反复出同一张图」

## 技术特性

- **权重算法**：以 0.1 为整数单位运算，先按分配曲线给出随机倾向值，再用最大余数法补齐，
  因此**任一权重都严格落在 `[weight_min, weight_max]` 内**，且总和精确等于 `weight_total`
- **降级必有信号**：可用艺术家不足、重试耗尽后返回重复组合、记录写入失败，
  均会输出 WARNING / ERROR 日志，不静默返回空提示词

## 使用示例

典型工作流配置：
1. 添加 **RollingArtist** 节点
2. 连接至文本编码器
3. 调整参数以获得理想的艺术家组合
4. 输出示例：
   - Prompt：
     ```
     artist:Jane_Smith,(artist:John_Doe:0.8),(artist:FooBar:0.2),
     ```
     当权重为 `1.0` 时，直接输出 `artist:名称`，不包含括号与权重；
     末尾**总是**带一个逗号（即使只抽到 1 个艺术家），方便直接拼接角色节点的多行输出。
   - JSON：
     ```json
     {
       "artists": [
         {"name": "Jane_Smith", "weight": 1.0, "top": true},
         {"name": "John_Doe", "weight": 0.8, "top": false},
         {"name": "FooBar", "weight": 0.2, "top": false}
       ],
       "status": "OK"
     }
     ```
     `status` 目前恒为 `OK`。
   - `tested_count`：当前数据库里已记录的组合数量（字符串，可接到文本节点查看）

## 最佳实践

- `artist_count` 用 3-5 更容易获得聚焦的风格
- 想要「一个主风格 + 若干弱参考」，把 `weight_curve` 设为 `dominant`；
  只调 `weight_min` / `weight_max` 的差值只能控制波动范围，做不出明显主次
- `artist_top_ratio` 是相对比例：默认数据 50,018 位艺术家配默认的 0.01 约等于 500 人的 Top 池，
  数据量更大时把比例调小
- 想跨会话积累去重进度，把 `tested_db_path` 指到固定文件，再接下 `tested_count` 就能看到已记录多少组
- 直接用**原始导出文件**（`danbooru_art_full.csv` / `danbooru_character.csv`）也能跑，
  但 `trigger` 列尚未转义、文件大得多，建议先加工（见下节）

## 数据预处理

两个脚本把 danbooru 导出文件裁成节点可直接读取的精简 CSV；筛选 / 列裁剪 / 转义 / 备份 / 原子写回
的共同逻辑都在 `danbooru_csv_tool.py` 里，**转义规则只有一份实现**：

| 脚本 | 默认输入 | 默认输出 | 输出列 |
|------|----------|----------|--------|
| `modify_danbooru_art.py` | `danbooru_art_full.csv`（419,789 行） | `danbooru_art_001.csv`（50,018 行） | `artist,trigger,count` |
| `modify_danbooru_character.py` | `danbooru_character.csv`（244,932 行） | `danbooru_character_001.csv`（34,416 行） | `character,copyright,trigger,core_tags,count` |

```bash
python modify_danbooru_art.py            # 默认：count >= 30，只转义 trigger 列
python modify_danbooru_art.py -n 100     # 换成 count >= 100
python modify_danbooru_art.py -i a.csv -o b.csv --no-backup
```

加工规则（两个脚本一致）：只保留 `count` ≥ 阈值（默认 30）的行并按 count 降序；只输出上表的列；
**只转义 `trigger` 列**（按逗号分段，段内空白 / 短横线折叠为下划线、丢弃空段、补转义括号），
其余列原样保留；写回前把原输出文件备份为 `<输出名>.bak`（已有备份不覆盖），
写回用临时文件 + 原子替换。

三点注意：

- **角色文件的 `character` 列没有转义**（只处理 trigger），而它带括号的仍有 16,380 条
  （如 `ganyu_(genshin_impact)`）。这些括号进提示词后会被权重解析器当成分组语法吃掉，
  需要精确匹配时请自行加工该列
- `trigger` 段内的连字符会变成下划线（`itomugi-kun` → `itomugi_kun`），沿用旧脚本惯例；
  想保留原始连字符，改 `danbooru_csv_tool.transform_text` 一处即可
- 输入与输出同一路径、缺列、文件不存在、编码错误都会明确报错并以非 0 退出码结束；
  输出文件被编辑器 / 网盘 / 杀软占用时会先重试，仍失败则退化为直接覆盖写入（并在末尾打印实际方式）

## 测试

```bash
pip install -e ".[dev]"
pytest
```

测试**不需要 ComfyUI**：`ra_core/` 是纯逻辑，节点文件只 import 标准库与 `ra_core`，可以直接实例化。
覆盖范围：`ra_core` 的纯逻辑（权重、CSV 解析、角色与作品筛选、记录库）、两个节点的接口与行为
（含**最近 10 条输出记录的回避窗口**）、两个加工脚本，以及用**两个真实子进程**验证的跨进程去重。

`tests/conftest.py` 会把 `custom_nodes` 加进 `sys.path` 并按包路径导入（`ComfyUI_RollingArtist.ra_core...`）；
每个测试文件对应一个模块（`test_weights.py` / `test_artists.py` / `test_characters.py` / `test_db.py` /
`test_node.py` / `test_node_character.py` / `test_modify_*.py`）。

## 变更记录

见 [CHANGELOG.md](CHANGELOG.md)。

## 许可证

本项目基于 [MIT License](LICENSE) 发布，Copyright (c) 2026 StarAsh042。

允许自由使用、修改、分发与商用，仅需保留版权声明与许可声明。
