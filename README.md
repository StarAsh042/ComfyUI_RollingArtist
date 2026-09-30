# ComfyUI RollingArtist

本包提供两个 ComfyUI 节点，用于生成包含随机权重的提示文本，通过动态调整 Top 比例实现可控的随机组合：

- **RollingArtist（滚动艺术家）**：从艺术家 CSV 随机抽人 + 分配随机权重
- **RollingCharacter（滚动角色）**：从角色 CSV 随机抽人（**不带权重**），
  额外带**作品标签**与角色的**固定外观标签**（`core_tags`），并支持按作品过滤

两者共用同一套抽样、权重分配、去重与记录机制；下面先讲画师节点，
角色节点的差异集中在「[RollingCharacter（角色）](#rollingcharacter角色)」一节。

## 核心功能

- **动态控制**：定义Top艺术家范围，控制实际输出数量
- **权重分配**：精确控制每个艺术家的权重范围和总和，并可选择**分配曲线**（平坦 / 主次分明 / 阶梯）
- **生成模式**：`自动`（保持历史行为）/ `普通`（永不穷举）/ `穷举`（强制逐个不重复输出）三选一
- **进度可见**：直接输出「已测数量」与「剩余组合数」，穷举模式下不用再去翻日志
- **种子控制**：统一随机种子确保结果可复现
- **线程与跨进程安全**：记录存在 SQLite 里，「判重 → 生成 → 记录」跑在同一个事务中，
  同进程多线程、多开 ComfyUI 实例都不会生成重复组合
- **类型注解**：完整的类型提示，提高代码可维护性
- **结构化输出**：`artists_json` 包含艺术家、权重、是否Top 等信息
- **性能优化**：艺术家 CSV 解析结果的进程级缓存；判重走数据库索引（不再每次全量读文件）；多列 CSV 自动识别
- **格式规则**：权重为 `1.0` 时不输出权重，直接输出名称
- **参数说明**：每个输入都带 `tooltip`（画师 18 个、角色 13 个），悬停可见，右侧「信息」面板的“描述”列同步显示
- **中英文界面**：节点名、参数名与参数说明跟随 ComfyUI 界面语言自动切换（`locales/zh`、`locales/en`）
- **节点帮助页**：「信息」面板展示随语言切换的文档（`web/docs/RollingArtist/<语言>.md`）
- **启动即清场**：每次启动自动删除节点目录下默认的记录数据库（含 `-wal` / `-shm`）与旧版 CSV 记录
- **双节点共用内核**：画师与角色共用抽样 / 权重 / 去重 / 记录机制；记录带类型标记，
  两个节点即使指向同一个数据库也不会互相冒充

## 代码结构

核心逻辑按职责拆分，便于阅读、复用与单独测试（均不依赖 ComfyUI）：

```
ComfyUI_RollingArtist/
├── RollingArtist.py       # 画师节点：输入输出定义与单次生成编排
├── RollingCharacter.py    # 角色节点：额外支持作品标签、外观标签与作品过滤
├── ra_core/
│   ├── constants.py       # 路径、权重步长、规模阈值、记录类型标记、日志器
│   ├── params.py          # 节点入参的容错转换（两个节点共用）
│   ├── artists.py         # CSV 解析 / 加载缓存（名字与整行）/ 索引化抽样
│   ├── characters.py      # 角色数据：trigger 拆解、作品筛选、外观标签合并
│   ├── weights.py         # 权重分配（含分配曲线）、提示词构建解析、去重键
│   ├── db.py              # SQLite 记录库：已测记录 + 穷举组合池 + 旧数据导入
│   ├── storage.py         # 旧版 CSV 的读取与留底（仅供导入 / 备份使用）
│   ├── exact.py           # 穷举模式的组合池抽取逻辑
│   └── startup.py         # 启动期清理默认运行期文件（由 prestartup_script.py 调用）
├── danbooru_csv_tool.py   # 两个加工脚本的共享实现（筛选 / 列裁剪 / 转义 / 备份 / 原子写回）
├── modify_danbooru_art.py # 画师数据加工：danbooru_art_full.csv -> danbooru_art_001.csv
├── modify_danbooru_character.py  # 角色数据加工：danbooru_character.csv -> danbooru_character_001.csv
├── prestartup_script.py   # ComfyUI 启动钩子：清理上次运行留下的默认记录文件
├── tests/                 # pytest：纯逻辑测试 + 节点层冒烟测试 + 加工脚本 + 跨进程测试
├── locales/               # 界面文案：<语言>/nodeDefs.json（节点与参数）、main.json（节点分类）
│   ├── zh/
│   └── en/
├── web/docs/              # 节点帮助页：RollingArtist/<语言>.md + 英文回退 RollingArtist.md
└── __init__.py            # 节点注册 + WEB_DIRECTORY
```

关键性能设计：

| 场景 | 旧实现 | 现实现 |
|------|--------|--------|
| 艺术家抽样（n 个艺术家） | 每个元素重复构建 set，复杂度 O(n²)（5000 个艺术家约 3 秒） | 直接对索引抽样，O(k)，与总量无关（同等条件 <1 ms） |
| 每次执行加载 CSV | 每次重新解析整个文件 | 按 `路径+列+修改时间+大小` 缓存，文件未变不重复解析 |
| 已测记录判重 | 每次执行重新读取并解析整个历史文件（且记录里含引号时会被迫整体重读） | SQLite 索引查询，O(log n)，历史再大也不占内存 |
| 穷举模式取组合 | 每抽一条都要把剩下的组合**整体重写**回磁盘 | 只删一行；组合池构建的差集计算也在 SQL 里完成 |
| 并发 | 只有进程内的锁，多开会产生重复组合 | `BEGIN IMMEDIATE` 事务 + WAL，跨进程也不会重复 |

## 安装指南

1. 克隆仓库到 ComfyUI 的 `custom_nodes` 目录：
   ```bash
   git clone https://github.com/StarAsh042/ComfyUI_RollingArtist.git
   ```
2. 确保以下文件/目录存在：
   - `RollingArtist.py`、`RollingCharacter.py`（两个节点文件）
   - `ra_core/`（核心实现子包）
   - `prestartup_script.py`（启动钩子，用于清理默认记录文件）
   - `locales/`、`web/`（界面中英文与节点帮助页；缺失只影响界面文案，不影响功能）
   - `danbooru_art_001.csv`（默认艺术家数据源：`artist,trigger,count` 三列，由下面的脚本生成）
   - `danbooru_character_001.csv`（可选的角色数据源：`character,copyright,trigger,core_tags,count` 五列）
   - `danbooru_csv_tool.py`（两个加工脚本的共享实现）
   - `modify_danbooru_art.py`（`danbooru_art_full.csv` → `danbooru_art_001.csv`）
   - `modify_danbooru_character.py`（`danbooru_character.csv` → `danbooru_character_001.csv`）
   说明：`ra_core` 使用相对导入，由 ComfyUI 以包的形式加载，无需额外配置；
   `locales/` 与 `web/docs/` 由 ComfyUI 按目录约定自动加载，同样无需注册。
3. 运行测试（可选）：
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

| 参数名称           | 类型    | 范围         | 说明                          |
|--------------------|---------|--------------|-------------------------------|
| artist_count       | INT     | 1-10         | 选择生成的艺术家人数（默认3） |
| artist_top_ratio   | FLOAT   | 0.01-1.0     | 提取CSV中Top艺术家占前百分比（默认0.01） |
| artist_top_count   | INT     | 1-10         | 输出中包含的Top艺术家数量（默认1） |
| weight_min         | FLOAT   | 0.1-2.0      | 单个艺术家最小权重值          |
| weight_max         | FLOAT   | 0.1-2.0      | 单个艺术家最大权重值          |
| weight_total       | FLOAT   | 0.0-20.0     | 所有权重总和（默认2.0）       |
| weight_curve       | ENUM    | flat / dominant / ramp | 权重分配曲线（默认 flat） |
| artists_prefix     | BOOLEAN | -            | 添加"artist:"前缀             |
| seed               | INT     | 0-4294967295 | 控制随机性的种子值            |

### 可选参数

- `use_top_priority`：BOOLEAN，是否启用 Top 优先池（默认开启）。关闭后从完整 CSV 中均匀抽取，不再强制包含 Top 艺术家
- `sort_by_weight`：BOOLEAN，是否按权重降序排序（默认开启）
- `exclude_artists`：STRING，逗号分隔的排除名单（精确匹配）
- `force_include`：STRING，逗号分隔的强制包含名单。这些艺术家一定出现在输出中，优先级高于 `exclude_artists`；数量超过 `artist_count` 时从中随机抽取
- `mode`：ENUM（`auto` / `random` / `exact`，默认 `auto`），生成模式
  - `auto`：保持 3.2.x 的行为——`artist_count = 1` 且未填 `force_include` 时进入穷举
  - `random`：永远随机抽取，不会穷举（只想抽 1 个艺术家时用这个）
  - `exact`：强制穷举。要求 `artist_count = 1` 且未填 `force_include`，**不满足时直接报错**（不静默降级，避免你以为在穷举其实没穷举）
- `dedup_mode`：ENUM（`none` / `full_prompt` / `artist_set` / `artist_list`，默认 `none`），跳过已生成过的组合
  - `none`：不去重（仍然会记录）
  - `full_prompt`：艺术家顺序与权重完全一致才算重复
  - `artist_set`：仅比较艺术家集合，忽略顺序与权重
  - `artist_list`：比较排序后的艺术家名，忽略权重
- `max_attempts`：INT（1-100，默认10），去重时的最大重试次数；超过后接受最后一次结果，避免死循环

### 高级参数（界面默认折叠）

- `custom_csv_path`：STRING，自定义 CSV 路径（留空使用默认）
- `tested_db_path`：STRING，记录数据库（SQLite）路径。留空使用节点目录下 `rollingartist.sqlite`；
  已测记录与穷举组合池都存在这个文件里，**更换该路径相当于重置去重进度**

### CSV 取列规则（固定读 `trigger` 列，不可配置）

画师节点**固定读取 `trigger` 列**：该列已由加工脚本转义（空格与短横线折叠为下划线、括号转义），
可以直接放进提示词。默认的 `danbooru_art_001.csv` 表头是 `artist,trigger,count`，
因此读到的名字是转义后的写法（如 `hammer_\(sunset_beach\)`）。

- CSV 里**没有 `trigger` 列**时（例如旧版单列文件、自己准备的其它格式）会**回退到首列**并记一条 WARNING；
  此时读到的可能是未转义的原始标签
- 以前可填 `auto` / `all` / 列序号 / 列名来换列，现已固定、不可配置；
  需要别的列请改数据文件（用加工脚本重新生成一份带 `trigger` 的 CSV）
- 如果输出里出现数字或网址（例如把 `count`、`url` 当成艺术家名），说明这份 CSV 没有 `trigger` 表头、
  回退到了首列——检查一下表头拼写

## RollingCharacter（角色）

从角色 CSV（默认 `danbooru_character_001.csv`）抽取角色，按
`角色标签,作品标签,core_tags` **逐行输出**（多个角色换行分隔），**不带权重**。

### 数据来源与 trigger 拆解

默认读 `trigger` 列。该列经 34,416 行全量校验是**恒定的两段结构**：

| 段 | 内容 | 校验结果 |
|---|---|---|
| 第 1 段 | 角色名（已转义，可直接进提示词） | == 转义后的 `character` 列：34,416/34,416 |
| 第 2 段 | 作品标签（如 `touhou`） | == 转义后的 `copyright` 列：34,416/34,416 |

读取的列固定为 `trigger`（不可配置）：该列恒为上述两段结构，因此不需要选列参数。

### 输出格式

**不带权重**，每个角色一行，字段用英文逗号连接：

```
角色标签,作品标签,core_tags
```

```
hakurei_reimu,touhou,1girl,brown eyes,long hair
ganyu_\(genshin_impact\),genshin_impact,1girl,horns
```

关闭「拼入外观标签」后变成 `角色标签,作品标签`：

```
hakurei_reimu,touhou
```

- 空的部分自动省略：没有作品标签或没有外观标签时不会留下多余逗号
- 外观标签在**行内去重**（源数据里同一角色可能重复列同一个标签）
- 多个角色用**换行**分隔，行的顺序就是抽样顺序

### 参数

与画师节点同名同义的部分不重复列举，差异如下（`character_*` 对应 `artist_*`）：

| 参数 | 类型 / 范围 | 说明 |
|------|-------------|------|
| character_count | INT 1-10 | 每次生成的角色数量（默认 1；**没有穷举模式**），每个角色一行 |
| character_top_ratio | FLOAT 0.01-1.0 | 人气池占比；候选数 = 作品过滤之后的角色数 |
| character_top_count | INT 1-10 | 每次至少抽几个人气池角色 |
| use_top_priority | BOOLEAN | 优先从人气池抽角色 |
| exclude_characters / force_include | STRING | 名单；强制包含允许写不在 CSV 里的名字 |
| copyright_filter | STRING | 只从这些作品抽（如 `touhou,vocaloid`）；同时接受 `fate_(series)` 与 `fate_\(series\)`，忽略大小写；过滤后为空会报错 |
| include_core_tags | BOOLEAN | 是否把角色的固定外观标签拼进每一行（默认开启） |
| dedup_mode / max_attempts | - | 与画师节点一致 |
| custom_csv_path | STRING | 留空使用 `danbooru_character_001.csv` |
| tested_db_path | STRING | 留空使用 `rollingcharacter.sqlite`（与画师节点分开） |

角色节点**不做权重分配**，因此没有 `weight_min` / `weight_max` / `weight_total` / `weight_curve` /
`sort_by_weight`；输出格式固定，因此也没有 `characters_prefix`，只保留 `include_core_tags`
这一个格式开关。

### 输出

| 输出 | 说明 |
|------|------|
| `prompt` | 每个角色一行：`角色标签,作品标签,core_tags`（不带权重；关掉开关后为 `角色标签,作品标签`） |
| `characters_json` | `[{"name","copyright","top","count","core_tags"}]` |
| `tested_count` | 当前数据库里已记录的角色组合数量 |

### 注意事项

- **约 22% 的角色没有外观标签**，这些角色的行里只有角色标签与作品标签；
  每个角色平均约 10 个标签，角色多时输出会明显变长
- 角色名唯一（34,416 行无重名），因此名字可直接对应到记录
- 去重键按「名字 + 权重」计算，但角色不带权重，所以键里统一按 `1.0` 计算
  （`artist_set` / `artist_list` 不受影响；`full_prompt` 等价于「顺序完全相同」）
- 记录带 `kind=character` 标记：即使与画师节点共用一个数据库，同名的画师与角色也不会互相冒充

## 生成模式与穷举

### 触发关系

| mode | 是否穷举 | 条件 |
|------|----------|------|
| `auto`（默认） | `artist_count = 1` 且 `force_include` 为空时穷举 | 与 3.2.x 一致 |
| `random` | 从不穷举 | — |
| `exact` | 总是穷举 | 要求 `artist_count = 1` 且 `force_include` 为空，否则报错 |

### 穷举模式的语义

1. 按 `weight_min`~`weight_max`（步长 0.1）枚举出所有 `(艺术家, 权重)` 组合，作为**组合池**存进数据库
2. 每次运行从组合池中随机取一条并删除，保证在穷举完之前**绝不重复**
3. 已经记录在数据库里的组合会被排除
4. 全部穷举后，`prompt` 返回特殊字符串 `ALL_COMBINATIONS_TESTED`（`artists_json` 中 `status` 同步为该值），下游需自行处理

组合池的实现要点：

- 组合池键由「艺术家池 + 权重网格」派生（sha1）：**配置一改就自动换成新池**，
  等价于旧版「删掉 `*_remaining.csv` 会按当前配置重建」，且池子空掉后不会每次重算
- 组合池最多保留 4 套（`EXACT_POOL_KEEP`），按最近构建时间淘汰，避免数据库无限增长
- 组合总数超过 `EXACT_COMBOS_LIMIT`（100 万）时**不建立组合池**，改为「随机采样 + 去重」：
  在最大重试次数内取未测过的组合（此时「剩余组合数」输出恒为 0）
- 想强制重建组合池：删掉记录数据库文件即可（默认路径的库每次启动本来就会被清理）

## 记录与去重（SQLite）

- 记录库是单个 SQLite 文件，同时存放**已测记录**与**穷举组合池**；使用 WAL 模式
- 判重键在写入时一次算好并建索引（`key_full` 唯一索引 + `key_set` / `key_list` 普通索引），
  因此判重是索引查询，不需要把全部历史读进内存
- 「判重 → 生成 → 记录」整段在同一个 `BEGIN IMMEDIATE` 事务里执行：
  - 同进程多线程：可重入锁
  - 多开 ComfyUI / 多进程：SQLite 文件锁（等待上限 10 秒）
- 艺术家名与权重以 JSON 存列，名字里含 `|`、`:`、逗号都不会破坏解析（旧 CSV 会串行）
- 记录条数**没有上限**，也不会自动裁剪；默认路径的记录文件每次启动会被清理，
  需要跨会话保留请显式设置 `tested_db_path`

**默认记录文件每次启动都会清理**：ComfyUI 启动时 `prestartup_script.py` 会删除节点目录下的
`rollingartist.sqlite`（含 `-wal` / `-shm`）以及旧版残留的 `tested_combinations.csv` 与
`tested_combinations_remaining.csv`，让每次运行都从干净状态开始。
需要跨会话接着去重/穷举，请把 `tested_db_path` 指到你自己的文件——**只清理默认路径**，
显式指定的路径不会被碰。

### 从 3.2.x 及更早版本升级

- 旧版 CSV 记录**不会自动生效**，界面上也**不再有导入参数**（`tested_csv_path` 已删除）。
  要保留历史，在节点目录下执行一次（路径按需替换）：

  ```bash
  python -c "from ra_core.db import RollingArtistDB; print(RollingArtistDB('rollingartist.sqlite').import_legacy_csv('tested_combinations.csv'))"
  ```

  返回 `(新增条数, 读取条数)`；导入是**幂等**的，同一条组合重复导入会被忽略。旧 CSV 不会被自动改名，
  建议自己留底后删掉。
- 旧版 `*_remaining.csv`（穷举剩余清单）不做导入；新实现会按当前配置在数据库里重建组合池
- 直接用默认路径的用户无需任何操作：旧记录文件本来每次启动就会被删掉

## 数据安全与路径提醒

`custom_csv_path` 与 `tested_db_path` 是**原样读写**的路径，节点不做任何目录限制：

- 自己单机使用没有问题
- 但如果这台机器的 ComfyUI 对局域网或公网开放，任何人都可以通过 API 让节点读写你硬盘上的任意文件
  （读 CSV、写数据库与临时文件）

如果属于后者，建议：给 ComfyUI 加上访问控制（如反向代理鉴权），或至少不要在公网暴露带工作流执行权限的接口。
另外：同一个记录数据库**不要**同时被多个 ComfyUI 实例共用同一个网络盘（WAL 模式要求本地文件系统）。

## Exact 模式与去重的注意事项

- 穷举模式下 `artists_json` 的 `top` 字段来自当前 Top 池，若中途替换了艺术家 CSV，该标记可能与实际不符
- 穷举模式下 `dedup_mode` 与 `weight_total` **不生效**：去重固定按 `full_prompt` 判定，
  权重按 `weight_min`~`weight_max` 网格取单个值；以某组配置首次进入该模式时会输出一条 INFO 提示
- 去重依赖 `tested_db_path` 的历史记录，更换该路径相当于重置去重状态
- 同一数据库上的「判重 → 生成 → 记录」由共享事务保护，多个 RollingArtist 实例
  （或并发线程、甚至多个进程）不会生成重复组合
- 降级采样模式下，若连续 `max_attempts` 次都命中已测组合，会按“已穷举”返回
  `ALL_COMBINATIONS_TESTED`（组合总量过大时无法精确判定，仅作近似判断）
- `ALL_COMBINATIONS_TESTED` 会被当作提示词输出，直接接到文本编码器会真的去画一张图，
  下游需要自己判断（可接 `artists_json` 的 `status` 或另加判断节点）
- 旧版 CSV 记录文件仍可被读取（导入用），但节点不再写出 CSV 记录

## 技术特性

- **权重算法**：以 0.1 为整数单位运算，先按分配曲线给出随机倾向值，再用最大余数法补齐，
  因此**任一权重都严格落在 `[weight_min, weight_max]` 内**，且总和精确等于 `weight_total`
- **分配曲线**：`flat` 接近平均；`dominant` 让随机一位明显占主导；`ramp` 按随机名次递减。
  三种曲线都满足上面的边界与总和约束
- **完善的类型注解**：使用Python类型提示增强代码可读性和IDE支持
- **线程安全设计**：进程级共享缓存 + 可重入锁；跨进程靠 SQLite 事务；文件写入原子替换
- **明确的错误处理**：统一使用 `logging`，CSV 缺失 / 解析失败 / 参数非法时抛出可读异常，
  不再静默返回空提示词
- **降级必有信号**：可用艺术家不足、重试耗尽后返回重复组合、记录写入失败、
  组合已穷举完毕、组合总量过大而降级采样，均会输出 WARNING / ERROR 日志
- **有测试兜底**：`ra_core` 为纯逻辑（不依赖 ComfyUI），节点层也能直接实例化，
  因此 `pytest` 一套即可覆盖；跨进程行为用两个真实子进程验证

## 使用示例

典型工作流配置：
1. 添加 **RollingArtist** 节点
2. 连接至文本编码器
3. 调整参数以获得理想的艺术家组合
4. 输出示例：
   - Prompt：
     ```
     artist:Jane_Smith,(artist:John_Doe:0.8),(artist:FooBar:0.2)
     ```
     当权重为 `1.0` 时，直接输出 `artist:名称`，不包含括号与权重。
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
     `status` 为 `OK`（正常）或 `ALL_COMBINATIONS_TESTED`（穷举模式已穷举）。
   - `tested_count`：当前数据库里已记录的组合数量（整数）
   - `remaining_count`：穷举模式下组合池剩余数量；其他情况恒为 `0`

## 最佳实践

- 使用较小的`artist_count`（3-5）获得更聚焦的风格（默认3）
- 想要「一个主风格 + 若干弱参考」，把 `weight_curve` 设为 `dominant`；
  只调 `weight_min` / `weight_max` 的差值只能控制波动范围，做不出明显主次
- 使用固定的`seed`值以便在多次生成中保持一致的艺术家组合
- 默认的 `danbooru_art_001.csv` 有 50,018 位艺术家，`artist_top_ratio` 用默认的 0.01
  约等于 500 人的 Top 池；列表更大时把该比例调小
- 角色数据建议用加工后的 `danbooru_character_001.csv`（34,416 行）；直接用原始的
  `danbooru_character.csv` 也行，但文件大得多，且 `trigger` 列尚未转义
- `danbooru_art_full.csv` 是**原始导出文件**（`trigger` 列尚未转义、含空格），
  建议先用 `modify_danbooru_art.py` 加工再交给节点
- 自定义CSV路径时，节点会检测文件是否变更，仅在变更时重载，避免重复解析
- 穷举模式（`artist_count=1`）下，把 `tested_db_path` 指到固定文件并接上
  `remaining_count` 输出，就能一眼看到还剩多少组合没跑

## 数据预处理

两个加工脚本把 danbooru 导出文件变成节点可直接使用的精简 CSV。共同逻辑
（筛选 / 列裁剪 / 转义 / 备份 / 原子写回）在 `danbooru_csv_tool.py` 里，
**转义规则只有一份实现**，不会出现两个脚本各写一套的情况：

| 脚本 | 默认输入 | 默认输出 | 输出列 | 转义列 |
|------|----------|----------|--------|--------|
| `modify_danbooru_art.py` | `danbooru_art_full.csv`（419,789 行） | `danbooru_art_001.csv`（50,018 行，1.40 MB） | `artist,trigger,count` | `trigger` |
| `modify_danbooru_character.py` | `danbooru_character.csv`（244,932 行） | `danbooru_character_001.csv`（34,416 行，6.47 MB） | `character,copyright,trigger,core_tags,count` | `trigger` |

```bash
python modify_danbooru_art.py                  # 画师：full -> 001，count >= 30
python modify_danbooru_character.py            # 角色：character -> character_001，count >= 30
python modify_danbooru_art.py -n 100           # 只保留 count >= 100 的行
python modify_danbooru_art.py -i a.csv -o b.csv
python modify_danbooru_art.py --no-backup      # 不生成 .bak 备份
```

加工规则（两个脚本一致）：

| 规则 | 说明 |
|------|------|
| 按热度保留 | 只保留 `count` ≥ 阈值（默认 30）的行，并按 count 从大到小排序 |
| 只留指定列 | 只输出上表列出的列，保留首行表头 |
| 只转义 trigger | trigger 列按**逗号分段**逐段整理：段内空白 / 短横线折叠为单个下划线、去掉首尾空白与短横线、丢弃空段，再转义未转义的括号；其余列原样保留 |
| 自动备份 | 写回前把原输出文件备份为 `<输出名>.bak`（如 `danbooru_art_001.bak`）；已有备份则不覆盖 |

注意事项：

- **角色文件的 `character` 列没有转义**（按当前约定只处理 trigger），而节点默认读的正是这一列；
  过滤后仍有 16,380 条带括号（`ganyu_(genshin_impact)`、`saber_(fate)` …）。
  这些括号进入提示词后会被 A1111 / ComfyUI 的权重解析器当成分组语法吃掉，标签会变形；
  需要精确匹配时请自行加工该列
- `trigger` 是逗号分隔的**多标签串**，所以按段处理：`hakurei reimu, touhou` → `hakurei_reimu,touhou`
  （逗号后的空格不会变成下划线，否则会写出 `hakurei_reimu,_touhou` 这种脏标签）。
  顺带修掉源数据里的脏写法：`95---` → `95`、`iwashi dorobou -r-` → `iwashi_dorobou_r`、
  `a  b` → `a_b`、`kezune (i- -i)` → `kezune_\(i_i\)`
- `trigger` 段内的连字符会变成下划线（`itomugi-kun` → `itomugi_kun`），这是沿用旧脚本的惯例；
  想保留原始连字符，改 `danbooru_csv_tool.transform_text` 一处即可（两个脚本共用它）
- 角色文件的 `trigger` 是「角色 + 作品」多标签串，不适合当作单个名字使用；
  `core_tags` 是角色的固定外观标签，原样保留备用
- 输出文件被编辑器 / 网盘同步 / 杀软占用时，脚本会先重试几次；仍失败则退化为直接覆盖写入
  （不具备原子性），并在末尾打印实际使用的写入方式
- 写回使用临时文件 + 原子替换，中途失败不会损坏输出文件；
  输入与输出同一路径、缺列、文件不存在、编码错误都会明确报错并以非 0 退出码结束

## 测试

```bash
pip install -e ".[dev]"
pytest
```

目录约定：

- `tests/conftest.py` 把 `custom_nodes` 加进 `sys.path`，并按包路径导入
  （`ComfyUI_RollingArtist.ra_core...`）
- `tests/test_weights.py`：权重分配（三种曲线 × 各种边界）、提示词构建解析、去重键
- `tests/test_artists.py`：CSV 列识别、Top 数量、抽样选择、加载缓存
- `tests/test_db.py`：判重语义、重复写入、特殊字符、旧 CSV 导入幂等、组合池穷举、
  **跨进程不产生重复组合**（用两个真实子进程验证）、**画师/角色记录类型隔离**、
  **早期版本表结构自动升级**
- `tests/test_characters.py`：trigger 拆解、作品筛选（含转义写法）、外观标签合并去重、
  提示词组装、记录缓存
- `tests/test_node.py`：画师节点接口（输入/输出/说明）、三种模式、去重、旧记录导入、进度输出、报错
- `tests/test_node_character.py`：角色节点接口、作品过滤、标签开关、去重、名单、
  与画师节点共用数据库时的类型隔离
- `tests/test_modify_art.py` / `tests/test_modify_character.py`：两个加工脚本
  （筛选、列裁剪、只转义 trigger、备份命名、命令行报错），并断言两者共用同一份转义实现
- `tests/test_no_double_import.py`：守卫测试，防止 `ra_core` 被同时以顶层与包路径导入
  （那会产生两份单例，两个数据库连接互相抢锁）

测试**不需要 ComfyUI**：节点文件只 import 标准库与 `ra_core`，可以直接实例化并调用。

## 变更记录

见 [CHANGELOG.md](CHANGELOG.md)。

## 许可证

本项目基于 [MIT License](LICENSE) 发布，Copyright (c) 2026 StarAsh042。

允许自由使用、修改、分发与商用，仅需保留版权声明与许可声明。
