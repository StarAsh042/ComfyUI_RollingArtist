# 变更记录

本文件记录 RollingArtist 的重要变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

暂无。

## [4.0.0] - 2026-09-30

**破坏性版本**：记录存储从 CSV 迁移到 SQLite，新增 RollingCharacter（滚动角色）节点、
生成模式与权重分配曲线，并精简了参数面（删除了 4 个输入与 1 个输出）。
（本版本开发期间曾用 3.3.0 编号，正式发布统一为 4.0.0。）

### 新增

- **`mode` 参数（自动 / 普通 / 穷举）**：穷举模式不再只能靠「艺术家数量 = 1」隐式触发。
  `auto` 保持旧行为，`random` 永不穷举（想只抽 1 个艺术家时用），
  `exact` 强制穷举——条件不满足时直接报错，不会静默降级成普通模式
- **`weight_curve` 参数（flat / dominant / ramp）**：控制权重的分配倾向。
  `flat` 是原来的平均分配；`dominant` 让随机一位艺术家明显占主导（主风格 + 弱参考）；
  `ramp` 按随机名次递减。三种曲线都仍严格满足权重的上下限与总和约束
- **两个进度输出**：`tested_count`（已测组合数）与 `remaining_count`（穷举池剩余数），
  穷举模式下不用再翻日志确认还剩多少
- **`tested_db_path` 参数**：记录数据库（SQLite）路径
- **新增节点 `RollingCharacter`（滚动角色）**：与画师节点共用同一套抽样、去重与记录机制，
  处理角色数据（`danbooru_character_001.csv`）：
  - **输出不带权重**，每个角色一行：`角色标签,作品标签,core_tags`，多个角色换行分隔，
    空的部分自动省略，外观标签在行内去重
  - `include_core_tags` 开关控制是否把外观标签拼进每一行（关闭后为 `角色标签,作品标签`）
  - **trigger 列拆解**：该列经 34,416 行全量校验是恒定的「角色标签,作品标签」两段结构
    （第 1 段 == 转义后的 `character` 列、第 2 段 == 转义后的 `copyright` 列，均 100%）
  - **作品过滤** `copyright_filter`：只从指定作品抽角色，同时接受原始写法与转义写法
  - `character_count` 默认 1；3 个输出：`prompt` / `characters_json` / `tested_count`
  - 不做权重分配（因此没有权重与曲线参数、也没有排序参数）、不做穷举、读取列固定为 `trigger`、
    输出格式固定（因此没有前缀与列参数），共 12 个参数
- **新增共享模块** `ra_core/characters.py`（角色解析）与 `ra_core/params.py`（入参容错转换）
- **记录类型隔离**：`tested` 表新增 `kind` 列（复合唯一索引），
  两个节点即使指向同一个数据库也不会互相冒充；没有 `kind` 列的旧库在打开时会自动补列升级
- **数据加工脚本重写**：`modify_danbooru_art.py` 从「就地规范化任意 CSV」改为
  「把 danbooru 导出文件加工成节点可直接使用的精简 CSV」——按 `count` 阈值筛选、
  只保留 `artist,trigger,count` 三列、只对 `trigger` 列做转义、
  默认 `danbooru_art_full.csv` → `danbooru_art_001.csv` 并自动备份为 `danbooru_art_001.bak`
- **新增角色数据加工脚本** `modify_danbooru_character.py`：
  `danbooru_character.csv` → `danbooru_character_001.csv`，只保留
  `character,copyright,trigger,core_tags,count` 五列，只对 `trigger` 列转义，源文件不被覆盖
- **新增共享实现模块** `danbooru_csv_tool.py`：两个加工脚本共用同一份
  筛选 / 列裁剪 / 转义 / 备份 / 原子写回实现，避免转义规则出现两份拷贝；
  重构后画师脚本的产出经 MD5 比对**逐字节未变**
- **测试**：新增 `tests/`（pytest），覆盖纯逻辑、节点层冒烟、加工脚本、
  以及用两个真实子进程验证的跨进程去重

### 变更

- **记录存储改为 SQLite**：一个数据库文件同时承载「已测记录」与「穷举剩余组合池」，
  取代 `tested_combinations.csv` 与 `*_remaining.csv`。带来四点直接变化：
  - 判重是索引查询（`O(log n)`），不再把全部历史读进内存；
    旧实现还会因为记录里含引号而被迫**每次整体重读**，增量优化形同虚设
  - 「判重 → 生成 → 记录」跑在同一个 `BEGIN IMMEDIATE` 事务里，**多开 ComfyUI 也不会重复出图**
    （旧实现只有进程内的锁）
  - 穷举模式每抽一条只删一行；旧实现会把剩余组合**整体重写**回磁盘
  - 艺术家名与权重按 JSON 存列，名字里含 `|`、`:`、逗号时不再串行
- **启动清理口径**：从「删两份 CSV」改为「删默认数据库（含 `-wal` / `-shm`）+ 兼容删除旧版两份 CSV」。
  仍然只清理默认路径，显式指定 `tested_db_path` 的文件不会被碰
- **删除 `RollingArtist.csv_column`**：CSV 取列**固定为 `trigger`**（已转义的列，可直接进提示词）；
  CSV 里没有该列时回退到首列并记 WARNING。
  3.0.0 的「展平所有列」（`all`）与 `auto` / 列序号 / 列名等写法随之不可用
  （`auto` 的识别能力仍保留在 `ra_core.artists` 层，供外部脚本与测试使用）
- **删除 `RollingArtist.tested_csv_path`**：旧版 CSV 记录的**界面导入入口取消**。
  导入能力保留在 `ra_core.db.RollingArtistDB.import_legacy_csv()`，
  需要保留历史时按 README「从 3.2.x 及更早版本升级」一节手动执行一次
- **删除 `RollingCharacter.csv_column`**：角色读取列固定为 `trigger`
- **删除 `RollingCharacter.core_tags` 输出**：输出从 4 个减为 3 个。
  每个角色的 `core_tags` 仍保留在 `characters_json` 里，`include_core_tags` 开关照旧控制是否拼进 prompt
- **删除 `ra_core.characters.format_core_tags()` / `collect_core_tags()`**：
  随上面的输出口一起移除（已无调用方）；`unique_tags()` 仍保留，`build_character_prompt` 与
  `build_payload` 都在用它
- **`RollingCharacter.character_count` 默认值 3 → 1**
- **穷举组合池键**由「艺术家池 + 权重网格」派生：配置一改自动换新池，
  不必再手动删除 `*_remaining.csv`；池最多保留 4 套，按最近构建时间淘汰
- **不再删除 `EXACT_FILE_LIMIT_BYTES` 的 64MB 报错**：组合池进了数据库后，
  原先「遗留清单超过 64MB 就直接报错让人手删」的问题不复存在
- 组合池构建的差集计算改在 SQL 里完成，不再把上百万条组合物化成 Python 列表
- **`auto` 取列的优先级调整**（`ra_core` 层能力）为 `character` → `trigger` → `artist` → `name`/`tag`：
  `trigger` 是「提示词可直接使用」的列（空格与括号已转义）故优先于原始 `artist` 列；
  `character` 仍排最前，因为它的 `trigger` 是「角色 + 作品」多标签串。
  节点本身已改为固定读 `trigger`，不再经过这套识别
- **默认 CSV 重新生成**：`danbooru_art_001.csv` 从「1,725 行单列」变为
  「`count >= 30` 的 50,018 行，`artist,trigger,count` 三列带表头」
- 参数说明与帮助文档同步更新；README 增加「CSV 取列规则」与「数据安全与路径提醒」两节
- 变更记录（原 README 的「升级说明」）移入本文件

### 修复

- **加工脚本的转义规则改为「按逗号分段」**：原来对整串做替换，会把逗号后的空格也变成下划线，
  产出 `hakurei_reimu,_touhou` 这类脏标签（角色文件 34,416 行**全部**中招）。
  现在逐段整理：段内空白 / 短横线折叠为单个下划线、去掉首尾空白与短横线、丢弃空段。
  同时修掉源数据里的同类脏写法：`95---` → `95`、`iwashi dorobou -r-` → `iwashi_dorobou_r`、
  `kezune (i- -i)` → `kezune_\(i_i\)`、`grs-` → `grs`（画师文件 25 行）
- 两个产出文件重新生成；「角色 / 画师列」等非转义列一字未动，行数与筛选结果完全一致
- 旧记录导入的「新增条数」统计：`INSERT OR IGNORE` 被忽略时不再误报为新增
- 穷举池已空且确认建过之后，不再每次执行都重算一遍全集

### 兼容性

- **输出从 2 个增加到 4 个**（`prompt` / `artists_json` / `tested_count` / `remaining_count`），
  新增的两个接在原有两个之后，旧工作流已有的连线不受影响
- **输入参数增删**：画师节点新增 `mode`、`weight_curve`、`tested_db_path`，
  删除 `csv_column`、`tested_csv_path`；角色节点删除 `csv_column`，共 12 个参数。
  参数顺序随之变化，**旧工作流载入后请检查两个高级字段（`custom_csv_path` / `tested_db_path`）
  是否为空**——按位置恢复时后面的值会顶到前面的参数上，必要时清空重设一次
- **同 seed 的可复现性实测结果**（以 3.2.0 为基准）：
  - 常规模式（`artist_count != 1`）：默认参数下同 seed 输出与 3.2.0 **完全一致**
  - 穷举模式（`artist_count = 1`）：同 seed 取到的组合**与 3.2.0 不同**——
    组合池的排列来源从「文件行序」改成了「按艺术家名 + 权重排序」，属于预期变化
- **默认数据源换血**：默认 CSV 从 1,725 个名字变成 50,018 个 `count >= 30` 的名字，
  因此**同一 seed 在默认配置下的输出会与旧版完全不同**；
  想回到原来的列表，可从 `danbooru_art_001.bak`（脚本生成的备份）或 git 历史中取回
- **取列固定为 `trigger`**：带 `artist` 与 `trigger` 两列的文件读 `trigger` 列（转义后的写法，
  可直接进提示词）。实测在默认数据源下与之前的 `auto` 结果**完全一致**（50,018 个名字逐项相同，
  同 seed 输出不变）；旧版单列 CSV 因为没有 `trigger` 表头会退回首列并记 WARNING
- **`generate_artists` 的 Python 接口**：新增参数追加在末尾，删除了 `csv_column` 与 `tested_csv_path`
  两个形参；返回值从 2 个变为 4 个，按位置解包的外部脚本需要相应调整
- **`generate_characters` 的 Python 接口**：删除了 `csv_column` 形参；
  返回值从 4 个变为 3 个（去掉 `core_tags`），按位置解包的外部脚本需要相应调整
- **不再写出 CSV 记录**：旧版 CSV 记录仍可被读取（仅用于导入），但节点不再产生新的 CSV
- **有意未做的改动**（评估过但按需保留现状）：记录条数上限与自动裁剪、
  采样不足时的 JSON 降级标记、参数非法时的严格模式、CSV 重名去重、
  按热度加权抽样、同作品约束、输出模板、冷却期、一次生成多组、CSV 列下拉框

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
本文件是 3.2.0 才建立的，以下内容依据 git 提交历史事后补写。

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

**没有功能变更**：该版本号提交（`9847a2d`）只改了 `pyproject.toml` 里的一行，
没有配套的功能提交，也没留下变更说明（事后依据 git 历史补写）。

## [1.0.0] - 2025-02-09

首个发布版本（事后依据 git 历史补写）：

- **RollingArtist 节点上线**：从艺术家 CSV 随机抽人、分配随机权重，
  输出 `(artist:名称:权重)` 形式的提示词（`7850897`）
- 许可证选用 **AGPL-3.0**（`1a55213`；3.1.0 时改为 MIT）
- 补齐 `pyproject.toml` 与 ComfyUI Registry 发布工作流 `publish.yml`（`1fff5fa`）

[未发布]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/main...HEAD
[4.0.0]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/aa58ad1...main
[3.2.0]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/c4164cc...aa58ad1
[3.1.0]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/08f5d4f...c4164cc
[3.0.0]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/9847a2d...08f5d4f
[2.0.0]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/1fff5fa...9847a2d
[1.0.0]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/9e37afd...1fff5fa

<!-- 比较链接一律使用 commit SHA（不依赖 tag，避免 tag 未推送时变成死链）。
     发新版时：把新发布提交的 SHA 填进“未发布”与上一版本的右端，并新增一行版本链接。 -->
