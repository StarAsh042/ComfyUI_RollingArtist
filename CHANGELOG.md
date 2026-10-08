# 变更记录

本文件记录 RollingArtist 的重要变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，  
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [4.2.0] - 2026-10-03

**安全性版本**：修复「任意路径读写」高危问题，改造数据库连接模型，并消除路径防护的性能回归。

### 新增

- **路径防护（`ra_core/paths.py`）**：`custom_csv_path` / `tested_db_path` 不再原样透传，命中受保护目标时抛出 `PathNotAllowedError`（继承 `ValueError`，界面上可见）。覆盖系统关键目录、凭据目录、ComfyUI 自身代码与本节点源码文件；数据库写入额外校验（已存在且非 SQLite 文件时拒绝覆写）；规范化 Windows `\\?\` 前缀与 ADS 写法；**本机管理共享**（`\\localhost\C$\Windows`）先还原为盘符路径再比较（`os.path.realpath` 不做这一步，此前可绕过黑名单），指向其它主机的一律拒绝；逃生开关 `ROLLINGARTIST_ALLOW_ANY_PATH=1` 完全关闭防护（详见 [README](README.md#数据安全与路径防护)）
- **数据库 schema 版本管理**：新增 `meta` 表记录 `schema_version`，用迁移链取代 `PRAGMA table_info` 列探测。全新库直接写入当前版本，v1 老库自动补 `kind` 列并保留原数据
- **CSV 规模保护**：读取前检查体积，超 100 MB 给出 WARNING；新增 `CSV_MAX_ROWS` 常量作为紧急截断护栏
- **工程配套**：`pyproject.toml` 的 ruff / mypy 配置、`.pre-commit-config.yaml`、`.github/workflows/ci.yml`

### 变更

- **数据库连接模型**：由「单连接 + 一把锁包住整段生成」改为**每线程一个连接**（`threading.local`），写锁只覆盖事务区间；事务内的纯 CPU 工作不再占锁，并发生成不再被强制串行化。`close()` 只关当前线程的连接
- **路径防护性能**：`forbidden_roots()` 按进程缓存（新增 `clear_forbidden_cache()`），`NODE_DIR` 的 realpath 同样缓存
- **`tested_count` 在事务内取**：与本次写入同一快照，避免显示值不一致
- **内部重构**：`ArtistRepository` 抽出 `_open_csv` / `_csv_rows` / `_cached_load` 三处公共实现，消除重复逻辑；`record_ex()` 新增三态接口，`record()` 改为其薄封装

### 修复

- **路径防护热路径性能回归**：修复前画师节点缓存命中单次 **7.88 ms**（角色节点的 44 倍）——`load()` 在**查询缓存之前**就调用路径校验，「缓存命中」也白付开销。修复后 **7.88 ms → 0.74 ms**；拦截范围不变
- **`select_artists` 对重复的 `force_list` 未去重**：传入 `['a1','a1','a2']` 会产出含重复艺术家的提示词，现做保序去重
- **`ruff format` 未通过**：11 个源文件不符合格式（CI 的 lint job 首跑即红），已全量格式化
- **并发测试断言环境敏感**：原先断言「总耗时 < N×sleep」，把 Windows 下 WAL 写锁排队与调度抖动也算进来，曾偶发失败，改为用 `Barrier` 观测锁区间
- **P2 级清理**（详见 `web/docs/评估报告_第二轮_4.2.0.md`）：`INPUT_TYPES` 默认 tooltip 与 `locales/zh` 的漂移已对齐并加逐字校验；CSV 读取路径去重并删除死代码；`CSV_MAX_ROWS` 提前停止语义被破坏后修复（实测 36 倍性能差）并加测试守住；`record()` 返回值语义明确为「非失败即成功」；`close_all_threads()` 更名（见下）

### 兼容性

- **行为变更**：`custom_csv_path` / `tested_db_path` 传入受保护目录会从「静默读写失败」变为「明确报错并拒绝」。数据放在常规目录的用户不受影响；确需跨目录可设 `ROLLINGARTIST_ALLOW_ANY_PATH=1`
- 数据库文件格式向后兼容：老库首次打开自动迁移，记录不丢失
- `forbidden_roots()` 现在返回缓存对象；测试若改动 `SystemRoot` 等环境变量需先 `clear_forbidden_cache()`
- `close_all_threads()` 更名为 `close_current_thread()`（旧名保留为废弃别名，既有调用不中断）
- 测试从 254 项增加到 **316 项**

## [4.1.0] - 2026-10-01

**破坏性版本**：删除穷举模式（画师输出 4 → 3、输入 18 → 17）；角色节点删除手输作品过滤与全部去重参数、作品下拉改为全量、`include_core_tags` 更名为 `describe_character`（角色输入 13 → 11）。

- **新增**：`copyright_pick` 作品下拉（列出 CSV 全部作品，默认 3,460 个，按作品热度即角色 `count` 之和降序，首项 `(不限)`，可打字筛选）；角色节点的「最近 10 条输出记录内不重复」内存窗口，替换原来的 4 种去重模式（`node.reset_recent()` / `node.recent_names()` 可清空与查看）
- **变更**：**删除穷举模式**——`mode` 参数、`remaining_count` 输出与组合池一并移除（`ra_core/exact.py`、`exact_pool` / `pool_meta` 表、`GENERATION_MODES` / `weight_grid` / `EXACT_*` 常量），`artist_count = 1` 现在就是随机抽 1 个，打开旧库时自动删掉遗留的组合池表；画师提示词末尾总是补一个逗号（方便拼接角色节点输出）；`tested_count` 由 `INT` 改为 `STRING`；角色节点删除 `copyright_filter` / `dedup_mode` / `max_attempts`，多角色输出改为「逗号 + 换行」分隔
- **兼容性**：输出 4 → 3 个，旧工作流接在 `remaining_count` 上的连线会消失；参数顺序有变化，载入后请检查 `custom_csv_path` / `tested_db_path` 是否仍是期望的文件；旧工作流「拼入外观标签」的值会掉回默认（键名改了，保存的 `false` 不再被识别）；外部脚本需去掉 `generate_artists(..., mode=...)` 与 `generate_characters(..., dedup_mode=..., max_attempts=..., copyright_filter=..., include_core_tags=...)`

## [4.0.0] - 2026-09-30

**破坏性版本**：记录存储从 CSV 迁移到 SQLite，新增 RollingCharacter 节点与权重分配曲线，并精简参数面（删除 4 个输入与 1 个输出）。

- **新增**：`weight_curve` 参数（`flat` / `dominant` / `ramp`，三种曲线都严格满足上下限与总和约束）；`tested_count` 输出与 `tested_db_path` 参数；**RollingCharacter 节点**（处理角色数据，输出不带权重，支持作品过滤 `copyright_filter` 与 `include_core_tags` 开关，读取列固定 `trigger`）；共享模块 `ra_core/characters.py` 与 `ra_core/params.py`；`tested` 表新增 `kind` 列做记录类型隔离；数据加工脚本重写并共用 `danbooru_csv_tool.py`；新增 `tests/`（覆盖纯逻辑、节点冒烟、加工脚本与跨进程去重）
- **变更**：**记录存储改为 SQLite**，取代 `tested_combinations.csv` 与 `*_remaining.csv`——判重变成索引查询，「判重 → 生成 → 记录」跑在同一 `BEGIN IMMEDIATE` 事务里，多开 ComfyUI 也不会重复出图；启动清理口径改为「删默认数据库（含 `-wal` / `-shm`）+ 兼容删除旧版两份 CSV」；**删除 `csv_column`**（取列固定 `trigger`，无该列时回退首列并记 WARNING）；**删除 `RollingArtist.tested_csv_path`**（界面导入入口取消，导入能力保留在 `RollingArtistDB.import_legacy_csv()`）；**删除 `RollingCharacter.core_tags` 输出**（仍保留在 `characters_json` 里）；`character_count` 默认值 3 → 1；默认 CSV 重新生成为 `count >= 30` 的 50,018 行三列带表头
- **修复**：加工脚本转义规则改为「按逗号分段」（原来会把逗号后的空格也变成下划线，产出 `hakurei_reimu,_touhou` 这类脏标签，角色文件 34,416 行全部中招）；旧记录导入的「新增条数」统计不再把 `INSERT OR IGNORE` 忽略的行误报为新增
- **兼容性**：输出 2 → 4 个（新增接在原有两个之后，已有连线不受影响）；**默认数据源换血**（1,725 → 50,018 个名字，**同一 seed 的输出与旧版完全不同**）；`generate_artists` 返回值 2 → 4、`generate_characters` 返回值 4 → 3；不再写出 CSV 记录（旧 CSV 仍可读，仅用于导入）

## [3.2.0] - 2026-09-29

界面本地化与启动期清理：参数说明改用 `tooltip`；中英文界面跟随 `Comfy.Locale` 自动切换；新增节点帮助页；启动时删除节点目录下的旧记录 CSV；少改动的参数折叠进“高级输入”。**兼容性**：原有参数、默认值、输出类型与语义不变，提示词与 CSV 格式不变。

## [3.1.0] - 2026-09-26

以内核重构为主（提交 `c4164cc`，连同 `5051f9f`、`8b6161c`）。**新增**：`ra_core/` 包（把逻辑从 `RollingArtist.py` 拆出，**不依赖 ComfyUI**）；引入角色数据源；许可证由 AGPL-3.0 改为 MIT。**顺带修掉的旧问题**：多列 CSV 只取一列（旧版会把表头、`count`、`url` 当艺术家名）；权重越界；失败时静默返回空提示词；清空 `custom_csv_path` 不回到默认 CSV；大 CSV 下抽样由 `O(n²)` 降为 `O(k)`。

## [3.0.0] - 2025-12-13

新增 `artists_json` 结构化输出；权重总和精确匹配目标值；`pyproject.toml` 补齐 `requires-python` 与 `Documentation` 链接。

## [2.0.0] - 2025-02-09

**没有功能变更**：该版本号提交（`9847a2d`）只改了 `pyproject.toml` 里的一行。

## [1.0.0] - 2025-02-09

首个发布版本：**RollingArtist 节点上线**（从艺术家 CSV 随机抽人、分配随机权重，输出 `(artist:名称:权重)`）；许可证选用 **AGPL-3.0**（3.1.0 时改为 MIT）。

[4.1.0]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/9d9d5ba...7041444
[4.0.0]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/aa58ad1...9d9d5ba
[3.2.0]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/c4164cc...aa58ad1
[3.1.0]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/08f5d4f...c4164cc
[3.0.0]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/9847a2d...08f5d4f
[2.0.0]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/1fff5fa...9847a2d
[1.0.0]: https://github.com/StarAsh042/ComfyUI_RollingArtist/compare/9e37afd...1fff5fa

<!-- 比较链接一律使用 commit SHA（不依赖 tag，避免 tag 未推送时变成死链）。
     发新版时：把新发布提交的 SHA 填进最新一版与上一版本的右端，并新增一行版本链接。 -->