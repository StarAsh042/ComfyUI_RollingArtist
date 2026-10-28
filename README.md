# ComfyUI RollingArtist

两个 ComfyUI 节点，从 CSV 随机抽人生成提示文本：

- **RollingArtist（滚动艺术家）**：抽艺术家 + 分配随机权重
- **RollingCharacter（滚动角色）**：抽角色（**不带权重**），带作品标签与角色描述（源数据列 `core_tags`），支持按作品筛选

两者共用抽样、去重与记录机制：4 种去重模式下「判重 → 生成 → 记录」在同一 SQLite 事务内，多线程与多开 ComfyUI 都不重复出图。中英文界面自动切换。

## 安装

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/StarAsh042/ComfyUI_RollingArtist.git
```

仓库自带 `danbooru_art_001.csv`（`artist,trigger,count`）与 `danbooru_character_001.csv`（`character,copyright,trigger,core_tags,count`），克隆即用。换数据见[数据预处理](#数据预处理)。

`ra_core/`、`locales/`、`web/docs/` 由 ComfyUI 按目录约定自动加载；后两者缺失只影响文案。节点名、参数名与 tooltip 跟随 `Comfy.Locale`（文案在 `locales/zh/`、`locales/en/`），「信息」面板取 `web/docs/RollingArtist/<语言>.md`，缺失时回退 `web/docs/RollingArtist.md`。**新增语言**：复制 `locales/en/` 为 `locales/<语言代码>/` 并翻译 `nodeDefs.json`，帮助页同理。

`danbooru_art_full.csv` / `danbooru_character.csv`（加工脚本的输入）用 Git LFS 存储，节点运行**不需要**它们——只有重跑加工脚本才需 `git lfs install` + `git lfs pull`。

## RollingArtist（画师）

下列顺序即界面顺序；`advanced` 参数折叠在“高级输入”中。

| 参数 | 类型 | 范围 | 说明 |
|------|------|------|------|
| `artist_count` | INT | 1-10 | 生成人数（默认 3） |
| `artist_top_ratio` | FLOAT | 0.01-1.0 | Top 池占前百分比（默认 0.01） |
| `artist_top_count` | INT | 1-10 | 输出中的 Top 人数（默认 1） |
| `weight_min` / `weight_max` | FLOAT | 0.1-2.0 | 单个权重的最小 / 最大值 |
| `weight_total` | FLOAT | 0.0-20.0 | 权重总和（默认 2.0） |
| `weight_curve` | ENUM | flat / dominant / ramp | 分配曲线（默认 flat） |
| `artists_prefix` | BOOLEAN | - | 添加 `artist:` 前缀 |
| `seed` | INT | 0-4294967295 | 随机种子 |

**可选参数**

- `use_top_priority`（默认开）：启用 Top 优先池；关闭后从完整 CSV 均匀抽取
- `sort_by_weight`（默认开）：按权重降序排序
- `exclude_artists` / `force_include`：逗号分隔的名单；`force_include` 必定出现，优先级高于排除名单
- `dedup_mode`（默认 `none`）：`none` 不去重（仍记录）；`full_prompt` 顺序与权重全同才算重复；`artist_set` 只看集合；`artist_list` 比较排序后名字。完整对比见 [`web/docs/RollingArtist/zh.md`](web/docs/RollingArtist/zh.md)
- `max_attempts`（1-100，默认 10）：重试上限；仍全命中时接受最后一次并打 WARNING
- `custom_csv_path` / `tested_db_path`：留空用默认 CSV / `rollingartist.sqlite`；**换数据库路径等于重置去重进度**
- `csv_max_rows`（默认 0 = 不限制）：一次读取最多消费多少条 CSV 记录（**含表头行**）。超过上限的记录不参与加载，候选池与 Top 池会相应变小；表很大且只想用前一部分时设正整数，可降低读取耗时与内存。**默认 0 与旧版行为完全一致**

固定读 `trigger` 列（已转义，如 `hammer_\(sunset_beach\)`）。没有该列时**回退首列**并记 WARNING；若输出里出现数字或网址（把 `count`、`url` 当成了人名），就是这种情况。

## RollingCharacter（角色）

从 `danbooru_character_001.csv` 抽取，按 `角色标签,作品标签,角色描述` **逐行输出**（多角色间用「逗号+换行」分隔），**不带权重**。读取列固定 `trigger`，经 34,416 行全量校验是恒定两段：第 1 段 = 转义后的 `character`（角色名），第 2 段 = 转义后的 `copyright`（作品标签）。

```
hakurei_reimu,touhou,1girl,brown eyes,long hair,
ganyu_\(genshin_impact\),genshin_impact,1girl,horns
```

关闭「开启角色描述」后为 `hakurei_reimu,touhou`。空的部分自动省略；角色描述（`core_tags`）**行内去重**；多角色行尾补逗号（最后一行不补），顺序即抽样顺序。

| 参数 | 类型 / 范围 | 说明 |
|------|-------------|------|
| `character_count` | INT 1-10 | 每次生成数（默认 1），每个角色一行 |
| `character_top_ratio` | FLOAT 0.01-1.0 | 候选数 = 作品筛选之后的角色数 |
| `character_top_count` | INT 1-10 | 每次至少抽几个人气池角色 |
| `seed` | INT | 随机种子 |
| `use_top_priority` | BOOLEAN | 优先从人气池抽 |
| `exclude_characters` / `force_include` | STRING | 名单；强制包含允许写不在 CSV 里的名字 |
| `copyright_pick` | COMBO | 列出全部作品（默认约 3,460 个，按 count 总和降序、并列按名字升序），首项 `(不限)`；可打字筛选、忽略大小写。**下拉项来自默认 CSV**：填了 `custom_csv_path` 时下拉不会跟着变，但筛选在该 CSV 上生效——其作品名请直接填写（原始写法 `fate_(series)` 亦可）；选了个该 CSV 里没有的作品时，报错会列出该 CSV 的作品总数与热门示例 |
| `describe_character` | BOOLEAN | 每行末尾拼上角色描述；默认开启 |
| `custom_csv_path` / `tested_db_path` | STRING | 留空用 `danbooru_character_001.csv` / `rollingcharacter.sqlite`（与画师分开）；作品筛选在 `custom_csv_path` 指向的 CSV 上生效 |
| `csv_max_rows` | INT | 一次读取最多消费多少条 CSV 记录（**含表头行**；默认 0 = 不限制）。超过上限的记录不参与加载，候选池、作品菜单与热度池会相应变小；**默认 0 与旧版行为完全一致** |

角色节点**不做权重分配**（故无 `weight_*` / `sort_by_weight`）；输出**不接受前缀包装**（没有画师那样的 `artists_prefix` 开关）；**没有去重模式参数**，改用固定规则的窗口。

**重复回避**：窗口存最近 10 条输出记录（每条 = 一次生成，可含多个角色），抽取前把窗口内角色名从候选排除，因此 `character_count=3` 相当于「最近 30 个角色内不重复」。窗口在**节点实例内存**里（重启清零、多实例各自独立——同一张图放两个角色节点，各避各的）；外部脚本可用 `node.reset_recent()` 清空、`node.recent_names()` 查看。窗口吃掉全部候选时退回「允许重复」并打 WARNING。

输出三个：`prompt`、`characters_json`（`[{"name","copyright","top","count","core_tags"}]`）、`tested_count`。

**注意**：约 22% 的角色没有描述，这些行只有角色标签与作品标签，角色多时输出会明显变长。角色名唯一（34,416 行无重名）。记录带 `kind=character` 标记、权重列按 `1.0` 占位，与画师共用数据库也不会互相冒充。

**性能**：派生结构（名字列表 / 名字→记录 / 按作品筛选下标）随记录缓存，重复执行不重复解析 CSV（实测单次执行 62.6ms → 26.7ms）；作品下拉按默认 CSV 文件签名缓存，替换数据后自动重建，无需重启 ComfyUI。

## 记录与去重（SQLite）

单个 SQLite 文件（WAL 模式）存**已测记录**；判重键写入时算好并建索引，判重是索引查询，历史再大也不占内存。「判重 → 生成 → 记录」整段在同一 `BEGIN IMMEDIATE` 事务：同进程靠可重入锁，多进程靠 SQLite 文件锁。艺术家名与权重以 JSON 存列，名字含 `|`、`:`、逗号都不破坏解析。

记录不自动裁剪；**默认路径的数据库每次启动会被清理**（`prestartup_script.py` 删 `rollingartist.sqlite` / `rollingcharacter.sqlite` 及 `-wal` / `-shm`）。想跨会话保留，把 `tested_db_path` 指到自己的文件——**只清理默认路径**。
## 数据安全与路径防护

`custom_csv_path` / `tested_db_path` **不再原样透传**：命中受保护目标时直接拒绝并抛出 `PathNotAllowedError`。

- **拦截**：系统关键目录（Windows 的 `Windows` / `Program Files` / `ProgramData`，Linux/macOS 的 `/etc` `/usr` `/bin` `/sbin` `/lib` `/boot` `/proc` `/sys` `/dev`）、凭据目录（`~/.ssh` `~/.aws` `~/.gnupg` `~/.kube` `~/.docker`）、ComfyUI 自身代码（`comfy`、`.git`、`custom_nodes`，本节点目录下的数据文件除外）、本节点目录内的源码文件
- **数据库写入**额外校验：目标已存在且不是 SQLite 文件时拒绝覆写
- **规范化**：先剥离 Windows 扩展长度 / 设备前缀（`\\?\`、`\\.\`）与备用数据流后缀（`data.csv:evil` 按 `data.csv` 判定）；本机管理共享（`\\localhost\C$\Windows`）**还原为盘符路径**再比较；指向**其它主机**的共享一律拒绝

**已知限制**：这是**纵深防御**，不是沙箱，**不能替代访问控制**——暴露到公网且无鉴权时仍可读取未列入名单的普通文件；不解决 TOCTOU；盘符黑名单只覆盖本机盘符。**若 ComfyUI 对局域网或公网开放，请加访问控制**。

**逃生开关**：单机自用可设 `ROLLINGARTIST_ALLOW_ANY_PATH=1` 完全关闭防护——**仅在确认环境可信时使用**。同一记录数据库**不要**被多个实例共用同一个网络盘（WAL 要求本地文件系统）。

## 使用示例

添加 **RollingArtist** 节点 → 连接文本编码器 → 调整参数。Prompt 输出形如：

```
artist:Jane_Smith,(artist:John_Doe:0.8),(artist:FooBar:0.2),
```

权重为 `1.0` 时直接输出 `artist:名称`（无括号与权重）；末尾**总是**带逗号，方便拼接角色节点的多行输出。同时输出 `artists_json`（各项 `name` / `weight` / `top`，`status` 恒为 `OK`）与 `tested_count`。

## 调参与注意事项

- 权重以 0.1 为整数单位运算，因此**任一权重严格落在 `[weight_min, weight_max]` 内**，总和精确等于 `weight_total`
- `artist_count` 用 3-5 更容易聚焦风格；想要「一个主风格 + 若干弱参考」把 `weight_curve` 设为 `dominant`
- `artist_top_ratio` 是相对比例：默认数据 50,018 人配 0.01 约等于 500 人 Top 池，数据更大时调小
- 降级必有信号：可用艺术家不足、重试耗尽、记录写入失败均输出 WARNING / ERROR，不静默返回空提示词
- **缓存**：ComfyUI 默认按输入参数缓存节点输出；两个节点已把被读取 CSV 的「路径 + 修改时间 + 大小」纳入缓存键（`IS_CHANGED`），替换 CSV 后会正常重算；文件与参数都不变时行为与 5.0.0 一致，不会退化为每次执行都重算

## 数据预处理

两个脚本把 danbooru 导出裁成节点可读的精简 CSV，共用 `danbooru_csv_tool.py` 的筛选 / 转义 / 备份 / 原子写回：

| 脚本 | 默认输入（行数） | 默认输出（行数） | 输出列 |
|------|----------|----------|--------|
| `modify_danbooru_art.py` | `danbooru_art_full.csv`（419,789） | `danbooru_art_001.csv`（50,018） | `artist,trigger,count` |
| `modify_danbooru_character.py` | `danbooru_character.csv`（244,932） | `danbooru_character_001.csv`（34,416） | `character,copyright,trigger,core_tags,count` |

```bash
python modify_danbooru_art.py            # 默认：count >= 30，只转义 trigger 列
python modify_danbooru_art.py -n 100     # 换成 count >= 100
```

规则（两脚本一致）：只保留 `count` ≥ 阈值（默认 30）并按 count 降序；只输出上表列；**只转义 `trigger` 列**（按逗号分段，段内空白 / 短横线折叠为下划线、丢弃空段、补转义括号），其余列原样；写回前备份为 `<输出名>.bak`，写回用临时文件 + 原子替换。

- **角色文件的 `character` 列没有转义**（只处理 trigger），带括号的仍有 16,380 条（如 `ganyu_(genshin_impact)`）；这些括号进提示词会被权重解析器当分组语法吃掉，需精确匹配请自行加工该列
- `trigger` 段内连字符会变成下划线（`itomugi-kun` → `itomugi_kun`），想保留原始连字符改 `danbooru_csv_tool.transform_text` 一处

> 更新 / 更换数据的完整流程（取数、LFS 纪律、校验清单、回滚、提交约定）见仓库数据 SOP；该 SOP 与项目文档仅维护在本地工作树，不随远端仓库分发。

## 参与开发

> **给外部贡献者的重要说明**
>
> `main` 分支受保护，且仅维护者可直接推送。
> **请向 `dev` 分支提交 Pull Request；直接向 `main` 提交的 PR 大概率不会受理。**

推荐流程：

1. Fork 本仓库到你自己账号
2. 从 `dev` 分支切出特性分支（`git checkout -b feature/xxx`）
3. 提交改动并推送到你的 Fork
4. 向本仓库的 **`dev` 分支**开 Pull Request，等待 CI 通过与合并

分支流向：`feature/xxx` → `dev`（日常集成）→ `main`（发布版本）

本地开发与自检（维护者本地工作树执行——`tests/` 与项目文档仅保留在本地，不进远端仓库）：

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest -q
```

外部贡献者只需 ruff 两项静态检查（克隆仓库不含测试套件）。

## 测试

测试套件覆盖纯逻辑、两个节点的接口与行为（含**回避窗口**）、加工脚本与跨进程去重，**不需要 ComfyUI** 即可运行。规模口径（2026-10-25 实测）：**254 个测试函数**，参数化展开共 **338 个用例**；CHANGELOG 中 5.0.0 记载的「316 项」为当时的展开用例口径，差额来自 5.1.0 新增的节点缓存与角色索引两组测试（13 + 9 项）。

测试**仅维护在维护者本地工作树**（`tests/`，自 5.1.6 起不进入远端仓库与发布包）：克隆仓库不含测试，质量门禁（pytest / ruff / mypy）由维护者在本地全量执行，CI 对推送的代码运行 ruff 静态检查。

## 变更记录

见 [CHANGELOG.md](CHANGELOG.md)。

## 许可证

基于 [MIT License](LICENSE) 发布，Copyright (c) 2026 StarAsh042。可自由使用、修改、分发与商用，仅需保留版权声明与许可声明。
