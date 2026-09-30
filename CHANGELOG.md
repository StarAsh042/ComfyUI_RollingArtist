# 变更记录

本文件记录 RollingArtist 的重要变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [4.1.0] - 2026-10-01

**破坏性版本**：删除穷举模式（画师输出 4 → 3、输入 18 → 17）；角色节点删除手输作品过滤与全部去重参数、
作品下拉改为全量、`include_core_tags` 更名为 `describe_character`（角色输入 13 → 11）。

### 新增

- **`RollingCharacter.copyright_pick` 作品下拉**：列出 CSV 里的全部作品（默认 3,460 个），
  按**作品热度**降序（该作品下所有角色的 `count` 之和）；首项 `(不限)`，可打字筛选、忽略大小写
- **角色节点的「最近 10 条输出记录内不重复」**：用一个内存窗口（每条 = 一次生成）替换原来的 4 种去重模式；
  窗口把候选全部吃掉时退回「允许重复」并打 WARNING 日志。`node.reset_recent()` / `node.recent_names()`
  可手动清空与查看

### 变更

- **删除穷举模式**：`mode` 参数、`remaining_count` 输出与组合池一并移除
  （`ra_core/exact.py`、`exact_pool` / `pool_meta` 表、`GENERATION_MODES` / `weight_grid` / `EXACT_*` 常量）。
  `artist_count = 1` 现在就是随机抽 1 个，不会再返回 `ALL_COMBINATIONS_TESTED`；
  打开旧数据库时会自动删掉遗留的两张组合池表
- **画师提示词末尾总是补一个逗号**（即使只抽到 1 个艺术家），方便直接拼接角色节点的多行输出
- **`tested_count` 输出由 `INT` 改为 `STRING`**（两个节点），可直接接到文本节点查看
- **角色节点**：删除 `copyright_filter` / `dedup_mode` / `max_attempts`；
  `include_core_tags` 更名为 `describe_character`（中文「开启角色描述」/ 英文 Enable Character Description）；
  多角色输出改为「逗号 + 换行」分隔（行尾补逗号，最后一行不补）
- **`dedup_mode` 补齐文档**：帮助页新增「去重模式详解」，README 同步

### 兼容性

- **输出从 4 个减为 3 个**（`prompt` / `artists_json` / `tested_count`）：
  旧工作流里接在 `remaining_count` 上的连线会消失
- **输入参数增删**：画师输入 18 → 17（删 `mode`），角色输入 13 → 11
  （删 `copyright_filter` / `dedup_mode` / `max_attempts`，新增 `copyright_pick`，`include_core_tags` 改名）。
  参数顺序有变化，旧工作流载入后请检查两个高级字段（`custom_csv_path` / `tested_db_path`）是否仍是期望的文件
- 旧工作流里「拼入外观标签」的值会掉回默认（开启）：键名改了，保存的 `false` 不再被识别
- 外部脚本需去掉 `generate_artists(..., mode=...)` 与
  `generate_characters(..., dedup_mode=..., max_attempts=..., copyright_filter=..., include_core_tags=...)`

## [4.0.0] - 2026-09-30

**破坏性版本**：记录存储从 CSV 迁移到 SQLite，新增 RollingCharacter（滚动角色）节点与权重分配曲线，
并精简了参数面（删除 4 个输入与 1 个输出）。

### 新增

- **`weight_curve` 参数**（`flat` / `dominant` / `ramp`）：控制权重的分配倾向，三种曲线都严格满足
  权重的上下限与总和约束
- **`tested_count` 输出**与 **`tested_db_path` 参数**：记录数据库（SQLite）路径
- **新增节点 `RollingCharacter`（滚动角色）**：处理角色数据（`danbooru_character_001.csv`），
  输出不带权重、每个角色一行 `角色标签,作品标签,core_tags`（外观标签在行内去重）；
  支持作品过滤 `copyright_filter` 与 `include_core_tags` 开关；读取列固定为 `trigger`
  （该列经 34,416 行全量校验是恒定的「角色标签,作品标签」两段结构）
- **新增共享模块** `ra_core/characters.py`（角色解析）与 `ra_core/params.py`（入参容错转换）
- **记录类型隔离**：`tested` 表新增 `kind` 列，两个节点指向同一个数据库也不会互相冒充
- **数据加工脚本重写**：`modify_danbooru_art.py` 与新增的 `modify_danbooru_character.py` 把 danbooru
  导出文件裁成 `artist,trigger,count` / `character,copyright,trigger,core_tags,count`，
  共用 `danbooru_csv_tool.py` 里的筛选 / 列裁剪 / 转义 / 备份 / 原子写回实现
- **测试**：新增 `tests/`（pytest），覆盖纯逻辑、节点层冒烟、加工脚本，以及用两个真实子进程
  验证的跨进程去重

### 变更

- **记录存储改为 SQLite**：一个数据库文件承载「已测记录」与「穷举剩余组合池」，
  取代 `tested_combinations.csv` 与 `*_remaining.csv`。判重变成索引查询、不再把全部历史读进内存；
  「判重 → 生成 → 记录」跑在同一个 `BEGIN IMMEDIATE` 事务里，多开 ComfyUI 也不会重复出图
- **启动清理口径**：改为「删默认数据库（含 `-wal` / `-shm`）+ 兼容删除旧版两份 CSV」，
  仍然只清理默认路径
- **删除 `RollingArtist.csv_column`**：取列**固定为 `trigger`**（已转义、可直接进提示词），
  没有该列时回退首列并记 WARNING
- **删除 `RollingArtist.tested_csv_path`**：旧记录的**界面导入入口取消**；导入能力保留在
  `RollingArtistDB.import_legacy_csv()`，需要时手动执行一次：

  ```bash
  python -c "from ra_core.db import RollingArtistDB; print(RollingArtistDB('rollingartist.sqlite').import_legacy_csv('tested_combinations.csv'))"
  ```

- **删除 `RollingCharacter.csv_column`**：角色读取列固定为 `trigger`
- **删除 `RollingCharacter.core_tags` 输出**：输出从 4 个减为 3 个；`core_tags` 仍保留在
  `characters_json` 里，`include_core_tags` 开关照旧控制是否拼进 prompt
- **`RollingCharacter.character_count` 默认值 3 → 1**
- **默认 CSV 重新生成**：`danbooru_art_001.csv` 从「1,725 行单列」变为
  「`count >= 30` 的 50,018 行，`artist,trigger,count` 三列带表头」

### 修复

- **加工脚本的转义规则改为「按逗号分段」**：原来会把逗号后的空格也变成下划线，
  产出 `hakurei_reimu,_touhou` 这类脏标签（角色文件 34,416 行全部中招）；
  顺带修掉 `95---`、`iwashi dorobou -r-`、`kezune (i- -i)`、`grs-` 等源数据脏写法
- 旧记录导入的「新增条数」统计：被 `INSERT OR IGNORE` 忽略时不再误报为新增

### 兼容性

- **输出从 2 个增加到 4 个**（`prompt` / `artists_json` / `tested_count` / `remaining_count`），
  新增的两个接在原有两个之后，旧工作流已有的连线不受影响
- **输入参数增删**：画师节点新增 `mode` / `weight_curve` / `tested_db_path`，删除 `csv_column` /
  `tested_csv_path`；角色节点删除 `csv_column`。参数顺序随之变化，**旧工作流载入后请检查两个
  高级字段（`custom_csv_path` / `tested_db_path`）是否为空**，必要时清空重设一次
- **同 seed 的可复现性**（以 3.2.0 为基准）：常规模式输出与 3.2.0 完全一致；
  穷举模式因组合池的排列来源改变而与 3.2.0 不同
- **默认数据源换血**：默认 CSV 从 1,725 个名字变成 50,018 个，**同一 seed 的输出会与旧版完全不同**；
  想回到原来的列表，可从 `danbooru_art_001.bak` 或 git 历史中取回
- **取列固定为 `trigger`**：实测在默认数据源下与之前的 `auto` 结果完全一致（50,018 个名字逐项相同）
- **Python 接口**：`generate_artists` 删除 `csv_column` / `tested_csv_path` 形参、返回值 2 → 4；
  `generate_characters` 删除 `csv_column` 形参、返回值 4 → 3（去掉 `core_tags`）。
  按位置解包的外部脚本需要相应调整
- **不再写出 CSV 记录**：旧版 CSV 记录仍可被读取（仅用于导入），但节点不再产生新的 CSV

## [3.2.0] - 2026-09-29

参数说明、界面本地化、节点帮助页与启动期清理。

### 新增

- 参数说明改用 `tooltip`：悬停可见，右侧「信息」面板的“描述”列同步显示
  （界面不读旧版的 `description`）
- 中英文界面：节点名、参数名与参数说明跟随 ComfyUI 界面语言自动切换（`locales/zh`、`locales/en`）
- 节点帮助页：`web/docs/RollingArtist/<语言>.md`（缺失时回退到英文版）
- 启动期清理：每次启动删除节点目录下的 `tested_combinations.csv` 与 `*_remaining.csv`

### 变更

- 参数顺序调整为「抽样 → 权重 → 前缀/种子 → 偏好 → 名单 → 去重 → CSV/路径」，
  很少改动的参数折叠进“高级输入”

### 兼容性

- 所有原有输入参数、默认值、输出类型与语义不变（`prompt` / `artists_json`）
- 提示词格式不变：权重为 `1.0` 时输出 `artist:名称`，否则输出 `(artist:名称:权重)`
- `tested_combinations.csv` 的行格式不变，旧文件可继续使用
- `generate_artists` 的形参顺序与默认值不变，外部脚本按位置调用不受影响
- 仅「只含位置数组」的旧版工作流需要核对一次参数值

## [3.1.0] - 2026-09-26

一次以内核重构为主的版本，由提交 `c4164cc` 发布（连同此前的 `5051f9f`、`8b6161c`）。

### 新增

- **`ra_core/` 包**：把原先集中在 `RollingArtist.py` 里的逻辑拆成
  `artists.py`（CSV 加载与抽样）、`weights.py`（权重分配与提示词拼接）、
  `exact.py`（穷举剩余组合池）、`storage.py`（已测记录）、`constants.py`（路径与阈值）。
  该包**不依赖 ComfyUI**，可独立导入与测试，节点文件只剩编排层
- **角色数据源**：引入 `danbooru_character.csv`（244,932 行）
- **许可证改为 MIT**：`LICENSE` 由 AGPL-3.0 全文换成 MIT（`5051f9f`）
- 发布工作流移动到 `.github/workflows/publish.yml`

### 变更

- `RollingArtist.py`（584 行改动）、`README.md`（121 行）、`modify_danbooru_csv.py`（99 行）
  同步重写；`danbooru_art_full.csv` 更新（452,848 行改动）
- 随 `ra_core` 拆分一并修掉的旧问题：
  - 多列 CSV 默认只取一列（旧版本会把表头、`count`、`url` 也当成艺术家名）
  - 权重不再越界（旧版本为凑 `weight_total` 可能让某个权重超过 `weight_max`）
  - 失败时抛出可读异常，不再静默返回空提示词
  - 清空 `custom_csv_path` 会正确回到默认 CSV
  - 改用 `logging`（logger 名 `RollingArtist`），不再直接 `print`
  - 大 CSV 下抽样由 `O(n²)` 降为 `O(k)`，CSV 与去重记录不再重复解析

## [3.0.0] - 2025-12-13

- 新增 `artists_json` 结构化输出（艺术家 / 权重 / 是否 Top / 状态）
- 权重总和精确匹配目标值；提示词格式与原有参数保持不变
- `pyproject.toml` 补充 `requires-python`、`dynamic = ["dependencies"]` 与 `Documentation`
  链接，并把 `PublisherId` 大小写修正为 `StarAsh042`（提交 `08f5d4f`）

## [2.0.0] - 2025-02-09

**没有功能变更**：该版本号提交（`9847a2d`）只改了 `pyproject.toml` 里的一行。

## [1.0.0] - 2025-02-09

首个发布版本：

- **RollingArtist 节点上线**：从艺术家 CSV 随机抽人、分配随机权重，
  输出 `(artist:名称:权重)` 形式的提示词（`7850897`）
- 许可证选用 **AGPL-3.0**（`1a55213`；3.1.0 时改为 MIT）
- 补齐 `pyproject.toml` 与 ComfyUI Registry 发布工作流 `publish.yml`（`1fff5fa`）

[4.1.0]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/9d9d5ba...7041444
[4.0.0]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/aa58ad1...9d9d5ba
[3.2.0]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/c4164cc...aa58ad1
[3.1.0]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/08f5d4f...c4164cc
[3.0.0]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/9847a2d...08f5d4f
[2.0.0]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/1fff5fa...9847a2d
[1.0.0]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/9e37afd...1fff5fa

<!-- 比较链接一律使用 commit SHA（不依赖 tag，避免 tag 未推送时变成死链）。
     发新版时：把新发布提交的 SHA 填进最新一版与上一版本的右端，并新增一行版本链接。 -->
