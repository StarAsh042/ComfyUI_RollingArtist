# ComfyUI_RollingArtist 后续迭代计划

> 文档类型：迭代计划（本轮为只读分析产出，未改动任何既有代码、配置或数据文件；本文件为唯一新增文件）
> 归档：2026-10-25 随 v5.1.0 发布入库至 `docs/iteration/`（原为仓库根目录下的未跟踪文件，后续版本计划统一归档于此）
> 分析基线：`dev` 分支 == `main` == `origin/dev` == v5.0.0，工作树干净（2026-10-25 实测）
> 分析方法：通读全部 12 个源文件、14 个测试文件、CI / 发布配置、locales 与 web/docs，并核对 git 全部分支与提交历史
> 状态：待确认。确认后本计划锁定为 v5.1.0 执行依据；执行中变更须走第 8.4 节变更流程

---

## 1. 现状分析结论

### 1.1 架构总览

三层结构 + 工程化配套，依赖方向自上而下，`ra_core` 不依赖 ComfyUI（因此测试无需 ComfyUI）：

| 层 | 模块 | 职责 | 状态 |
|----|------|------|------|
| 节点层 | `RollingArtist.py`（17 个参数，507 行） | 节点接口定义 + 单次生成编排 | 稳定 |
| 节点层 | `RollingCharacter.py`（11 个参数，443 行） | 节点接口定义 + 单次生成编排 | 稳定 |
| 内核层 | `ra_core/artists.py` | CSV 表头/列识别、文件签名缓存、索引抽样 | 稳定 |
| 内核层 | `ra_core/characters.py` | 角色记录解析、作品筛选、输出组装 | 稳定 |
| 内核层 | `ra_core/weights.py` | 权重分配（flat/dominant/ramp）、提示词构建与解析、去重键 | 稳定 |
| 内核层 | `ra_core/db.py` | SQLite 记录库：WAL、`BEGIN IMMEDIATE`、每线程连接、schema 版本迁移（当前 v2） | 稳定 |
| 内核层 | `ra_core/paths.py` | 自定义路径黑名单防护（含 Windows 扩展前缀 / ADS / UNC 还原） | 稳定，有已知边界 |
| 内核层 | `ra_core/params.py` `storage.py` `startup.py` `constants.py` | 参数容错、旧 CSV 兼容、启动清理、全局常量 | 稳定 |
| 数据层 | `danbooru_art_001.csv`（50,018 行）/ `danbooru_character_001.csv`（34,416 行）/ `rollingartist.sqlite` / `rollingcharacter.sqlite` | 数据源与运行期已测记录 | 稳定 |
| 前端资源 | `locales/{zh,en}/`、`web/docs/{RollingArtist,RollingCharacter}/` | 界面文案与节点帮助 | 稳定 |
| 工程化 | `.github/workflows/{ci,publish}.yml`、`.pre-commit-config.yaml`、`tests/`（14 文件、233 个测试函数） | 质量门禁与发布 | 5.0.0 建立 |

已验证的核心能力（作为迭代基座，本轮不动其语义）：

- RollingArtist：Top 池优先抽样、权重范围+总和双约束（0.1 整数单位运算）、4 种去重模式、排除/强制名单、路径防护、事务内判重。
- RollingCharacter：逐行输出（角色标签,作品标签,角色描述）、作品下拉全量列出（约 3,460 项）、最近 10 条内存回避窗口、`kind` 标记与画师共库不混淆。
- 记录库：「判重 → 生成 → 记录」同事务，进程内可重入锁 + 跨进程 SQLite 文件锁。
- 安全：路径黑名单防护 + 三个已修复绕过手法（B1 扩展前缀 / B2 ADS / B3 UNC 管理共享）均有回归测试。
- 工程化：CI 测试矩阵（py3.8/3.11/3.12 + Windows py3.11）+ ruff 双检查 + 聚合 `ci` job；pre-commit；推 `main` 且 `pyproject.toml` 变更时自动发布 Comfy Registry。

### 1.2 技术债务与未决问题清单

| 编号 | 问题 | 用户影响 | 证据 / 位置 |
|------|------|----------|-------------|
| TD-01 | ComfyUI 按输入参数缓存节点输出；CSV 文件被替换但参数不变时返回旧结果（"换了数据没效果"） | 高（正确性） | 两节点均无 `IS_CHANGED`；`fix/is-changed` 分支已修复、未合并 |
| TD-02 | 角色节点每次执行在 34,416 条记录上重建派生结构（名字列表/名字→记录/作品筛选，实测约 19ms，占单次执行约三成）；作品下拉仅在节点注册时构建一次 | 中（性能/体验） | `RollingCharacter.generate_characters`；`perf/character-derived` 分支已优化、未合并 |
| TD-03 | 角色去重窗口仅在节点实例内存：重启清零、多实例各算各、不与记录库联动 | 中（设计取舍） | `RollingCharacter._recent`（`deque(maxlen=10)`） |
| TD-04 | 默认记录库每次启动被清空，`tested_count` 不跨会话累积 | 中（易误解） | `prestartup_script.py`；README 已说明但无界面提示 |
| TD-05 | `generation_lock` 持锁覆盖整个重试循环，同一数据库的并发生成被串行化（单次约 9ms；10 次重试约 90ms） | 中（批量/队列场景吞吐） | `RollingArtist.generate_artists`；`docs/lock-scope` 分支已补充文档、未合并 |
| TD-06 | CSV 整表载入内存，`CSV_MAX_ROWS=None` 无硬上限，仅 100MB 体积告警 | 低-中（超大自定义 CSV） | `ra_core/artists.py` |
| TD-07 | 路径防护为黑名单：不解决 TOCTOU、非本机盘符/junction 转向；不是沙箱 | 低（公网无鉴权暴露时） | `ra_core/paths.py`；README「已知限制」 |
| TD-08 | 作品下拉只来自默认 CSV，`custom_csv_path` 不联动 | 低（已文档化） | `RollingCharacter._copyright_menu` |
| TD-09 | mypy 不在 CI 执行（仅本地约定命令） | 低 | `.github/workflows/ci.yml` |
| TD-10 | 参数文案三处手工同步（代码默认/zh/en）+ README + web/docs，新增参数成本高、已发生过漂移 | 低（有 `test_locales` 守 code==zh） | `tests/test_locales.py` |
| TD-11 | 测试数口径不一致：CHANGELOG 称 316 项；当前 233 个测试函数（AST 展开约 285 例） | 低 | 本轮实测 |
| TD-12 | 无针对真实 ComfyUI 运行时的契约验证（测试刻意不依赖 ComfyUI，节点注册/前端 schema 兼容性未验证） | 中（ComfyUI 升级时） | `tests/conftest.py` |
| TD-13 | 仓库存在重建前平行历史备份分支；`import_legacy_csv` 无生产调用方（仅测试引用） | 低 | `git branch`、`grep` 实测 |
| TD-14 | 性能数据均为手工实测，无常驻基线文档、无防回归测试 | 低 | 无 benchmark 资产 |

### 1.3 仓库状态（关键事实，排期前必读）

- 本地有 3 个**已完成但未合并**的功能分支，且**均未推送到 origin**（origin 仅有 `dev` / `main`）：
  - `fix/is-changed`（2026-10-23）：`IS_CHANGED` 缓存指纹，改动 7 文件、新增 `tests/test_node_cache.py`（159 行）。
  - `perf/character-derived`（2026-10-23）：`CharacterIndex` 派生索引缓存，单次执行 62.6ms → 26.7ms，改动 4 文件、新增 `tests/test_character_index.py`（133 行）。
  - `docs/lock-scope`（2026-10-23）：`generation_lock` 持锁范围文档修正，仅注释/docstring，无行为变化。
- 另有 `backup/pre-rebuild-20261022-233943`（2026-10-09，仓库重建前的平行历史）——清理候选，不影响功能。
- 两分支改动存在文件重叠：`RollingCharacter.py` 与 `ra_core/__init__.py` 同时被 `fix/is-changed` 和 `perf/character-derived` 修改——**合并时必然需要解冲突，必须串行合并、每步跑门禁**。
- 本机两个 Python（managed 3.13.12 / system 3.13.15）均未安装 pytest，本轮只读约束下未运行测试（见假设 A-02）。

---

## 2. 迭代目标与范围

### 2.1 v5.1.0 迭代目标（一句话）

把三个已完成但未合并的分支安全合入 `dev` 并发布 5.1.0：消除「换 CSV 不生效」的正确性缺陷（TD-01）与角色节点每次执行的性能浪费（TD-02），**不新增任何节点功能**。

### 2.2 范围内（In Scope）

1. 合并 `fix/is-changed`、`perf/character-derived`、`docs/lock-scope`（含冲突解决、测试、文档同步）。
2. 发布纪律执行：CHANGELOG、版本号、tag、Registry 发布验证。

### 2.3 范围外（Out of Scope — 锁定，防范围蔓延）

| 不做的功能 | 原因 | 何时考虑 |
|------------|------|----------|
| 角色去重持久化（把内存窗口改为 SQLite 记录） | 改变角色节点核心语义与输出分布，需产品决策（T-07） | v5.2.0，T-07 决议之后 |
| 默认记录库「启动即清空」策略变更 | 影响现有语义、测试与用户预期 | v5.2.0，随 T-07 一并决策 |
| 路径防护重写（白名单/沙箱） | 黑名单方案已有 3 个绕过手法的回归测试，重写风险高于收益 | 出现明确公网部署需求时 |
| CSV 流式读取 / 硬行数上限 | 默认数据 5 万 / 3.4 万行内存可控（实测峰值约 24MB） | 用户报告超大 CSV 问题后（T-13） |
| 新节点、新数据源、新语言 | 与「修复与收敛」目标无关 | v5.2+ 按用户反馈 |
| CI 增加 mypy 阻断 | 先保证本地全绿再进 CI（T-09 分两步走） | v5.1.x |

---

## 3. 需求优先级总表

优先级定义：**必须（P0）**= 不完成则 v5.1.0 失去意义；**应当（P1）**= 显著降低维护风险或解锁下一版决策；**可延后（P2）**= 有价值但当前无痛点驱动。
工作量定义：S ≤ 0.5 天；M = 0.5~2 天；L ≥ 2 天（人力投入，非日历时间）。

| ID | 任务 | 优先级 | 工作量 | 关联债务 |
|----|------|--------|--------|----------|
| T-01 | 合并 `fix/is-changed`（IS_CHANGED 缓存指纹） | P0 必须 | S | TD-01 |
| T-02 | 合并 `perf/character-derived`（CharacterIndex + 菜单签名缓存） | P0 必须 | M | TD-02、TD-08 |
| T-03 | 合并 `docs/lock-scope`（持锁范围文档修正） | P0 必须 | S | TD-05 |
| T-04 | 合并后一致性校验与文档同步（CHANGELOG/README/locales/web docs/CRLF） | P0 必须 | M | TD-10 |
| T-05 | 全量质量门禁（pytest + ruff×2 + mypy） | P0 必须 | S | — |
| T-06 | 发布 5.1.0（版本号/tag/合并 main/Registry 验证） | P0 必须 | S | — |
| T-07 | 角色去重与记录保留策略决策（OPEN-DECISIONS 落条） | P1 应当 | S | TD-03、TD-04 |
| T-08 | 按 T-07 结论落地（文档增强或持久化实现，范围随决策） | P1 应当 | M/L | TD-03、TD-04 |
| T-09 | CI 增加 mypy job（先非阻断，稳定后转阻断） | P1 应当 | S | TD-09 |
| T-10 | 真实 ComfyUI 运行时契约冒烟（节点注册/schema/帮助面板/i18n） | P1 应当 | M | TD-12 |
| T-11 | 性能基线固化（BENCHMARKS 文档 + 索引复用断言测试） | P1 应当 | S | TD-14、TD-02 |
| T-12 | 测试数口径核对并修正 CHANGELOG 表述 | P1 应当 | S | TD-11 |
| T-13 | CSV 规模硬护栏评估（`CSV_MAX_ROWS` 默认值/流式方案） | P2 可延后 | M | TD-06 |
| T-14 | 路径防护增强评估（白名单目录配置/TOCTOU 缓解） | P2 可延后 | L | TD-07 |
| T-15 | 新语言 i18n（按需，如 ja） | P2 可延后 | M | — |
| T-16 | 作品下拉联动 `custom_csv_path` | P2 可延后 | M | TD-08 |
| T-17 | 仓库清理：删除 backup 分支；`import_legacy_csv` 标注生命周期 | P2 可延后 | S | TD-13 |
| T-18 | 数据更新 SOP 文档化（重跑加工脚本的流程与校验） | P2 可延后 | S | — |

---

## 4. 阶段与任务拆分

### Phase A：三分支合并与解冲突（Sprint 1，第 1 周）

> 合并顺序依据依赖与冲突面确定：先合改动面最小的 `docs/lock-scope`（仅注释），再合 `fix/is-changed`，最后合 `perf/character-derived`（它与前者在 `RollingCharacter.py`、`ra_core/__init__.py` 上重叠）。**每合并一个分支，立即跑一次质量门禁**（T-05 的命令），全绿才合下一个。

**T-01 合并 `fix/is-changed`**
- 内容：`RollingArtist.IS_CHANGED` / `RollingCharacter.IS_CHANGED`、`ArtistRepository.cache_fingerprint`、`storage.file_fingerprint`、新测试 `tests/test_node_cache.py`。
- 交付物：dev 上的合并提交；`IS_CHANGED` 生效。
- 验收标准（EARS）：
  - AC-01.1 When 同一组参数下被读取的 CSV 文件内容发生变化（mtime/size 变化），系统**必须**重新执行节点并产出新结果。
  - AC-01.2 While 文件与参数均不变，系统**必须**保持缓存命中行为，**不得**退化为每次执行都重算。
  - AC-01.3 If 自定义路径被防护拒绝或无法解析，`IS_CHANGED` **必须**不抛异常（返回稳定哨兵），错误留待节点执行时抛出并可向用户展示原因。
  - AC-01.4 合并后既有 233 个测试函数零回归，新增缓存测试全绿。

**T-02 合并 `perf/character-derived`**
- 内容：`CharacterIndex` / `load_index`（名字列表、名字→记录、按作品筛选下标随记录缓存）、作品下拉按默认 CSV 文件签名缓存、新测试 `tests/test_character_index.py`。
- 交付物：dev 上的合并提交；角色节点单次执行派生结构零重建。
- 验收标准（EARS）：
  - AC-02.1 While CSV 文件签名不变，角色节点多次执行**必须**复用同一 `CharacterIndex`（重复加载返回同一对象）。
  - AC-02.2 When 默认角色 CSV 被替换，作品下拉菜单**必须**随文件签名变化重建，无需重启 ComfyUI。
  - AC-02.3 筛选、输出格式与 5.0.0 逐字节一致（既有 `test_node_character.py` 全绿 + 新增索引测试）。
  - AC-02.4 If 并发首次构建索引，系统**应该**允许至多一次重复构建，且结果幂等、不产生错误数据。

**T-03 合并 `docs/lock-scope`**
- 内容：`generation_lock` 与 `RollingArtist` 重试循环的持锁范围文档修正（仅注释/docstring）。
- 验收标准：AC-03.1 合并后 `git diff` 仅含注释与 docstring；AC-03.2 文档准确表述「持锁时长由调用方决定、重试循环在锁内是跨进程不重复的代价」。

### Phase B：文档同步与发布（Sprint 1，第 2 周）

**T-04 合并后一致性校验与文档同步**
- 内容：CHANGELOG 增补 5.1.0 段（吸收三分支变更条目，Keep a Changelog 新版本在上）；README 补充 `IS_CHANGED` 行为与角色性能说明；核对 locales 与 web/docs 无残留；执行参数覆盖校验；保持既有文件 CRLF 行尾（README/CHANGELOG 历史上为纯 CRLF）。
- 验收标准（EARS）：
  - AC-04.1 CHANGELOG **必须**包含 5.1.0 段且位于 5.0.0 之上，条目与三个分支的 `[未发布]` 段一致。
  - AC-04.2 README **必须**覆盖全部 17 + 11 个参数名（参数覆盖校验通过）。
  - AC-04.3 已删参数（`characters_prefix` / `include_core_tags` / `csv_column` / `copyright_filter` 等）**不得**在 README 作为现役功能出现。
  - AC-04.4 改动文件的行尾风格与改动前一致（CRLF 校验通过）。

**T-05 全量质量门禁**（合并每个分支后及发布前各跑一次）
- 命令（与 README「参与开发」一致）：
  ```bash
  pip install -e ".[dev]"
  pytest -q
  ruff check . && ruff format --check .
  python -m mypy ra_core RollingArtist.py RollingCharacter.py   # 注意：不用 mypy .
  ```
- 验收标准：AC-05.1 四条命令全部退出码 0；AC-05.2 CI（GitHub Actions）在 PR 上全绿。

**T-06 发布 5.1.0**
- 步骤：① dev 上 `pyproject.toml` version 改为 `5.1.0`（独立提交）；② PR 合并 dev → main；③ push main 触发 `publish.yml`（paths 过滤 `pyproject.toml`）；④ 打 tag `v5.1.0`；⑤ 验证 Registry 页面显示 5.1.0。
- 验收标准（EARS）：
  - AC-06.1 When PR 合并到 main，publish workflow **必须**成功（Registry 显示 5.1.0）。
  - AC-06.2 tag `v5.1.0` **必须**指向含全部三个分支改动的提交。
  - AC-06.3 版本号三处一致：`pyproject.toml` / CHANGELOG / tag（全局 grep 无残留旧版本号）。

### Phase C：决策与工程增强（Sprint 2）

**T-07 角色去重与记录保留策略决策**
- 内容：在 `docs/decisions/OPEN-DECISIONS.md`（新建，纳入版本库）落条两项决策：① 角色回避窗口是否持久化；② 默认记录库是否引入保留策略。每项含现状、选项、影响面、当前倾向、解锁条件。
- 验收标准：AC-07.1 两条 OPEN 条目落盘且格式完整；AC-07.2 每项在 Sprint 2 内给出结论（OPEN → RESOLVED 并补 Resolution），可升格为 ADR。

**T-08 按 T-07 结论落地**
- 方案 A（结论=维持现状）：强化 tooltip 与 README 表述，明确「窗口在内存、重启清零」「默认库启动即清空」。
- 方案 B（结论=持久化）：角色节点支持基于记录库的去重模式——**此为范围扩大项，须先更新本计划并重新确认优先级后再动工**（参考第 8.4 节变更流程）。
- 验收标准：按选定方案的 AC 在动工前书面固化。

**T-09 CI 增加 mypy job**
- 内容：ci.yml 增加 mypy job，命令同 T-05；第一期设为非阻断（`continue-on-error` 或仅报告），本地连续 2 周全绿后转阻断。
- 验收标准：AC-09.1 PR 上可见 mypy 结果；AC-09.2 转阻断前零新增错误。

**T-10 真实 ComfyUI 运行时契约冒烟**
- 内容：在本地真实 ComfyUI 环境中验证：节点注册与 CATEGORY、`INPUT_TYPES` schema（tooltip / advanced / display 被前端正确读取）、「信息」面板帮助文档、中英文切换、两节点输出拼接、`IS_CHANGED` 实际生效、作品下拉刷新。产出检查单与结果记录（落入 `.workbuddy-ai/memory/`）。
- 验收标准：AC-10.1 检查单全部通过并留痕；AC-10.2 失败项形成缺陷单并回流 T-01~T-06 或 v5.2。

**T-11 性能基线固化**
- 内容：新增 `BENCHMARKS.md`（记录测量方法、环境、命令与既有实测数据：角色节点 62.6ms→26.7ms、画师缓存命中 0.23ms、冷加载 9.6s/1.8s、持锁约 9ms）；新增一个**不依赖绝对计时**的回归测试（断言索引复用：第二次 `load_index` 与首次为同一对象）。
- 验收标准：AC-11.1 文档与测试入库且 CI 通过；AC-11.2 测试在慢机器上不 flaky（沿用项目既有的「观测行为而非绝对耗时」原则）。

**T-12 测试数口径核对**
- 内容：安装 dev 依赖后 `pytest --collect-only -q` 取得真实用例数，修正 CHANGELOG/README 中「316 项测试」表述为准确口径（区分测试函数数与参数化展开数）。
- 验收标准：AC-12.1 文档数字与实测一致；AC-12.2 若与 316 差异大，补查是否有测试在历史重构中丢失。

### Phase D：v5.2.0 立项（Sprint 3+，条件触发）

- 触发条件：T-07 决议为「持久化」或用户提出新需求。
- 内容：按决议范围重新走「目标 → 范围 → 任务拆分 → 验收」计划流程，本文件升版或另立 v5.2 计划。

---

## 5. 模块依赖关系

### 5.1 任务依赖图

```
T-01 ─┐
T-02 ─┼──> T-04 文档同步 ──> T-05 质量门禁 ──> T-06 发布 5.1.0 ──> T-10 运行时冒烟
T-03 ─┘                                                          │
                                                                  ├──> v5.2.0 立项（Phase D）
T-02 ──> T-11 性能基线                                            │
T-07 决策 ──> T-08 落地（范围随决策，可能升级为独立版本）───────────┘
T-05 ──> T-09 CI+mypy
T-12 测试口径核对（独立，建议并入 T-04 一并做）
```

### 5.2 代码模块影响面（合并顺序的依据）

| 分支 | 改动文件 | 与其它分支重叠 |
|------|----------|----------------|
| docs/lock-scope | RollingArtist.py、ra_core/db.py | 无 |
| fix/is-changed | CHANGELOG.md、RollingArtist.py、RollingCharacter.py、ra_core/__init__.py、ra_core/artists.py、ra_core/storage.py、tests/test_node_cache.py(新) | RollingArtist.py（仅注释区，冲突可忽略）；RollingCharacter.py 与 ra_core/__init__.py 与 perf 分支重叠 |
| perf/character-derived | RollingCharacter.py、ra_core/__init__.py、ra_core/characters.py、tests/test_character_index.py(新) | 同上 |

数据与兼容性说明：三个分支均**不改变**数据库 schema（`SCHEMA_VERSION` 保持 2）、不改变输出格式、不改变节点契约（节点名/输入输出/CATEGORY）——旧工作流与旧记录库无需迁移。

---

## 6. 测试与质量保障

### 6.1 现有测试资产（保持并扩展）

- 14 个测试文件、233 个测试函数（AST 展开约 285 例），覆盖：纯逻辑（artists/characters/weights）、两节点行为、记录库与迁移、跨进程去重、路径防护（含 Windows 特有分支）、locales 覆盖、加工脚本、双导入防护。
- 设计约束保持：测试**不依赖 ComfyUI**；conftest 的 `_reset_db_registry` 夹具保持（Windows 占用文件可删）。
- flaky 治理原则（既有教训，继续执行）：不使用绝对耗时断言；并发测试用 Barrier 对齐 + 观测锁区间。

### 6.2 新增测试要求

- 每个合并分支必须自带回归测试（T-01/T-02 已具备；若合并中发现覆盖缺口，先补测试再合）。
- T-08 若走持久化方案：必须覆盖「重启后窗口仍生效」「与画师 kind 隔离」「旧库无该表时的迁移」三类用例。
- T-10 冒烟发现的前端 schema 问题：固化 ComfyUI 版本号与期望行为的契约测试（允许在测试内做版本探测跳过）。

### 6.3 质量门禁检查单（每次合并 / 发布前逐项打勾）

```text
[ ] pytest -q 全绿
[ ] ruff check . 零错误
[ ] ruff format --check . 零 diff
[ ] python -m mypy ra_core RollingArtist.py RollingCharacter.py 零错误（不用 mypy .）
[ ] CHANGELOG / pyproject 版本号三处一致
[ ] 参数覆盖校验通过（README 覆盖 17 + 11 参数）
[ ] locales zh/en key 覆盖且非空（test_locales 已守）
[ ] CRLF 行尾一致性（README/CHANGELOG）
[ ] 已删参数无残留（characters_prefix / include_core_tags / csv_column / copyright_filter）
[ ] CI 在 PR 上全绿（test 矩阵 + lint + 聚合 ci）
```

---

## 7. 风险登记与应对

| 编号 | 风险 | 概率 | 影响 | 应对措施 |
|------|------|------|------|----------|
| R-01 | 三分支合并且在 `RollingCharacter.py` / `ra_core/__init__.py` 产生冲突，解冲突引入缺陷 | 中 | 中 | 按 Phase A 顺序串行合并；每步跑门禁；冲突以测试为准，不靠感觉；冲突解决后人工 diff 复核两分支意图是否都保留 |
| R-02 | `IS_CHANGED` 导致缓存命中率下降、执行变慢 | 低 | 中 | 指纹仅含文件签名（不读文件内容）；AC-01.2 测试守住「不变则命中」；发布说明中告知行为变化 |
| R-03 | `CharacterIndex` 惰性缓存并发重复构建或缓存失效导致旧数据 | 低 | 低 | 缓存键含文件签名（路径/mtime/size）；AC-02.1/02.4 覆盖；`clear_records_cache` 同步清索引缓存 |
| R-04 | 角色去重持久化范围蔓延进 v5.1.0 | 中 | 高 | v5.1.0 范围外锁定（第 2.3 节）；T-07 先决策后动工；若决意实施，走 8.4 变更流程重排 |
| R-05 | 5.1.0 发布后发现缺陷 | 中 | 高 | 无 schema/输出格式变更，数据零风险；代码回滚见 8.3；优先 fix-forward 发 5.1.1 |
| R-06 | Registry 已发布版本不可撤回（待确认 Q-03） | 中 | 中 | 以新 patch 版本覆盖为回滚手段；发布窗口选择低使用时段 |
| R-07 | 路径防护被新绕过手法突破 | 中 | 高 | 合并后做一轮安全复核（对照 B1/B2/B3 方法论）；新绕过必须补回归测试；README 已知限制保持如实 |
| R-08 | 超大自定义 CSV 导致内存问题 | 低 | 中 | 现状有 100MB 告警；T-13 评估硬上限；发布说明引导用加工脚本裁剪 |
| R-09 | 文案三处漂移复发 | 中 | 低 | `test_locales`（code==zh 逐字）+ 参数覆盖校验守住；新增参数按检查单执行 |
| R-10 | 本轮只读分析未运行测试，分支质量未经运行验证 | 中 | 中 | T-05 门禁前置；合并第一个分支时即全量跑测试，失败先修再继续 |
| R-11 | 本地三分支未推送 origin，误删/丢失 | 低 | 高 | 合并前先 `git push -u origin <branch>` 备份再合；合并在本地 dev 完成后推送 dev |

---

## 8. 发布与回滚安排

### 8.1 分支与发布流（沿用仓库既有约定）

```text
feature/xxx ──> dev（日常集成）──> main（发布版本）
```

- 外部贡献者向 `dev` 提 PR（README 已声明）；`main` 受保护。
- 发布触发：push 到 `main` 且 `pyproject.toml` 变更 → `publish.yml` 自动发布 Registry；或 Actions 页手动 `workflow_dispatch`。

### 8.2 发布检查单（T-06 执行时逐项确认）

```text
[ ] dev 上 pytest / ruff×2 / mypy 全绿
[ ] CI 在 dev→main 的 PR 上全绿
[ ] pyproject.toml version = 5.1.0；CHANGELOG 5.1.0 段在位
[ ] 三份分支意图均已体现在 CHANGELOG（缓存修复 / 角色性能 / 文档修正）
[ ] tag v5.1.0 已打并指向发布提交
[ ] publish.yml 运行成功，Registry 显示 5.1.0
[ ] 本地真实 ComfyUI 冒烟通过（T-10，若时序在发布后则作为发布后验证）
```

### 8.3 回滚安排

| 层级 | 回滚方式 | 说明 |
|------|----------|------|
| 代码（仓库） | main 上对问题提交做 revert PR（不用 force push，main 受保护） | 保留历史可追溯 |
| 用户侧 | ComfyUI Manager 安装 5.0.0；或 `git checkout v5.0.0` | 旧版记录库直接兼容（schema 未变） |
| 数据 | 无需回滚：v5.1.0 无 schema 变更、无记录格式变更；记录库损坏时默认路径下次启动自动清理自愈，自定义路径换路径即重置 | 迁移链保持「只追加」 |
| Registry | 以 5.1.1 patch 修复覆盖（是否可撤回已发布版本待确认 Q-03） | 不在 README 承诺「可撤回」 |

### 8.4 计划变更流程

执行中如出现下列任一情况，本计划须先修订再动工：新增/删除 P0 任务；T-07 决策导致范围扩大（如角色去重持久化）；发布形式变化（如拆成两个版本）。修订须更新第 3 节总表与第 13 行以内的变更记录，并重新过第 6.3 节门禁。

---

## 9. 假设与待确认事项

### 9.1 假设（分析期作出，未经确认）

- A-01：以 `dev == main == origin/dev == v5.0.0`、工作树干净为分析基线（已实测）。
- A-02：三个功能分支的提交内容即待交付内容，作者已完成本地验证；本轮只读约束 + 本机无 pytest，**未运行任何测试验证分支质量**（风险 R-10）。
- A-03：CHANGELOG「316 项测试」为含参数化展开的口径；当前 233 个测试函数、AST 展开约 285 例，差额待 T-12 核对，不排除历史重构中有丢失。
- A-04：本项目定位为个人/小规模使用的 ComfyUI 插件（单机或可信内网），非多租户服务——影响路径防护与记录库策略的取舍。
- A-05：优先级排序原则为「影响正确性 > 性能 > 工程纪律 > 新功能」。
- A-06：`publish.yml` 所需 `REGISTRY_ACCESS_TOKEN` 仍在有效状态（未验证）。
- A-07：工作量估算为单人投入的粗略量级，未计联调与等待评审时间。

### 9.2 待确认事项（需用户/维护者回答，阻塞对应任务）

| 编号 | 问题 | 阻塞 |
|------|------|------|
| Q-01 | 角色去重是否要持久化（内存窗口 vs 记录库）？ | T-07 → T-08，影响 v5.2 范围 |
| Q-02 | 默认记录库「启动即清空」是否加保留策略（如保留 N 天/容量上限）？ | T-07 → T-08 |
| Q-03 | Comfy Registry 已发布版本是否可撤回/替换？ | T-06 回滚策略 |
| Q-04 | mypy 是否进 CI 阻断（当前仅手动）？ | T-09 |
| Q-05 | 是否需要第三种界面语言（如 ja）？ | T-15 |
| Q-06 | 真实 ComfyUI 环境的版本与验证执行人/时间？ | T-10 |
| Q-07 | 「316 项测试」的权威口径（函数数 or 展开例数）？ | T-12 |
| Q-08 | `backup/pre-rebuild-20261022-233943` 分支是否可删除？ | T-17 |
| Q-09 | 本计划的排期节奏是否可行（Sprint 1 发布 / Sprint 2 增强）？ | 全局 |

---

## 10. 排期与分工建议

### 10.1 建议节奏（双周增量）

| 阶段 | 时间 | 任务 | 出口条件 |
|------|------|------|----------|
| Sprint 1 | 第 1-2 周 | T-01 → T-02 → T-03 → T-04 → T-05 → T-06 | v5.1.0 发布，Registry 可见 |
| Sprint 2 | 第 3-4 周 | T-07（决策）+ T-09 + T-10 + T-11 + T-12；T-08 视决策结论启动 | 决策闭环、CI/基线/口径补齐 |
| Sprint 3+ | 按决策 | Phase D：v5.2.0 立项（含 T-08 方案 B 或 P2 项） | 新计划确认 |

### 10.2 分工建议

- 维护者（StarAsh042）：分支合并与冲突裁决、发布操作（T-01~T-06）、T-07 决策。
- 可委托/辅助：T-04 文档同步、T-11 基线撰写、T-12 口径核对、T-18 SOP——均可由 AI 辅助完成后由维护者审阅。
- T-10 运行时冒烟需要本机 ComfyUI 环境，建议维护者执行或明确授权环境信息后由 AI 辅助执行。

### 10.3 变更记录

| 日期 | 变更内容 | 原因 | 影响范围 |
|------|----------|------|----------|
| 2026-10-25 | 首版：只读分析 + v5.1.0 迭代计划 | 用户要求制定后续迭代计划 | 全文 |

---

## 执行记录（2026-10-25，v5.1.0 发布后追加）

> 本节为发布后的执行回填；上方计划原文未改动。Sprint 1（T-01~T-06）已全部完成。

| 任务 | 状态 | 证据 |
|------|------|------|
| T-01 合并 `fix/is-changed` | 完成 | `ef94f75`（--no-ff，自动合并无冲突）；`tests/test_node_cache.py` 13 项 |
| T-02 合并 `perf/character-derived` | 完成 | `c23cf26`（--no-ff，自动合并无冲突）；`tests/test_character_index.py` 9 项 |
| T-03 合并 `docs/lock-scope` | 完成 | `f1c1fb8`（仅注释/docstring，AC-03 满足） |
| T-04 文档同步与一致性校验 | 完成 | `29f969a`；CHANGELOG 5.1.0 段 + README 缓存/性能说明；参数覆盖 28/28、已删参数零残留、CRLF 纯净 |
| T-05 全量门禁 + QA 独立验收 | 完成 | pytest 338 例 / ruff check / ruff format --check / mypy（12 文件）全绿；QA RoleVerdict: **pass**（12 条 AC 全过；测试零删除零弱化；两处定向变异均被测试击杀） |
| T-06 发布 5.1.0 | 完成 | PR #4 squash 合并 → main `dcb98bc`；publish.yml 运行成功（Registry 版本历史已列 5.1.0，CDN 产物在库）；tag `v5.1.0` → `dcb98bc` |

执行备注（供后续迭代参考）：

- 本机代理对 github.com 的 HTTP/2 通道不稳定，git 远程操作需 `-c http.version=HTTP/1.1`（已固化到仓库本地配置，不影响仓库内容）
- 测试口径实测：338 个收集用例 = 316（5.0.0 时点口径）+ 22（两个新测试文件）——CHANGELOG「316 项测试」在 5.0.0 时点准确；T-12 需把口径更新为当前值并区分「测试函数数（254）/ 参数化展开用例数（338）」
- Registry 观察：5.0.0 版本状态为 Flagged（10-22 发布时即存在，早于本轮迭代，非本次引入）；5.1.0 为 Pending（Registry 审核/索引异步，latest_version 指针尚未翻转）
- Sprint 2（T-07~T-12）待启；Q-01/Q-02（角色去重与记录保留策略）待维护者决策，决策前不动 T-08
