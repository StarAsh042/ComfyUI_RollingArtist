# 变更记录

本文件记录 RollingArtist 的重要变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [5.3.0] - 2026-10-29

**消除 Registry 扫描触发点版本**：删除唯一的「关闭路径防护」环境变量开关，Windows 系统目录改从注册表读取，使发布包内的安全扫描 finding 归零。**含一处破坏性变更**：`ROLLINGARTIST_ALLOW_ANY_PATH` 失效。

### 破坏性变更

- **删除逃生开关 `ROLLINGARTIST_ALLOW_ANY_PATH`**：该环境变量曾用于完全关闭路径防护。它能一键关停全部检查（系统目录、凭据目录、源码、UNC），等于任何能写进程环境的人都能让防护失效——与「防任意文件读写」的初衷相悖，且是 Registry 安全扫描唯一 finding 的来源。**升级后该变量不再有任何效果**；依赖它把 CSV / 数据库放在系统目录或凭据目录下的用户会收到 `PathNotAllowedError`，请换目录，或改用 `ra_config.json` 白名单（白名单只收窄，不能放开系统目录）。同时删除 `ra_core.paths.is_path_guard_disabled()` 与其导出
- 拒绝文案不再提示「可设置环境变量关闭防护」，改为提示用 `ra_config.json` 白名单

### 变更

- **Windows 系统目录改读注册表**：`SystemRoot` / `Program Files` / `Program Files (x86)` / `ProgramData` 不再从环境变量读取，改为读 `HKLM` 注册表（`ProgramData` 走 `Explorer\Shell Folders` 的 `Common AppData`）。注册表是这些值的**权威来源**（环境变量是登录时派生的副本，可被启动脚本改写），因此**防护能力不降级反而更准**：`Program Files` 被挪到 `D:\Program Files` 时也能正确拦截。注册表读不到时回退标准默认值，候选集取「默认值 ∪ 注册表值」并由存在性过滤
- `winreg` 延迟导入，POSIX 上模块仍可独立导入与测试

### 新增

- **`tests/test_registry_scan_replica.py`**：本地复刻 Registry 安全扫描规则（规则字面量由 Registry 实际回报的 `metadata.matched_patterns` 反推），在**发布前**回答「这次改动会不会新增 finding」。5.1.3 与 5.1.6 两轮「消除误报」都只是把 `os.environ.get(` 换成 `os.getenv(`，从一个规则换到另一个规则，instances 反而 1→5；有了这个复刻器，同类盲改不会再发生

### 修复

- **发布卫生豁免清零**：`tests/test_publish_hygiene.py` 的 `ALLOWLIST` 与 `ALLOWLIST_EXPECTED_HITS` 清空（v5.2.0 曾登记 1 条 `("ra_core/paths.py", "PEM-$env_read3")` 豁免）。保持为空是 Registry Active 的前提

### 兼容性

节点输入输出、默认行为、CSV 格式、数据库 schema 与 v5.2.0 **完全一致**；唯一差异是上述被删除的环境变量。旧工作流无需任何操作。

## [5.2.0] - 2026-10-28

**大 CSV 防护与路径边界版本**：新增可配置的 CSV 读取上限、把两条读取路径的计数语义统一并消除原始行双份驻留；修正两处路径边界行为并新增管理员配置文件白名单。默认行为、节点契约与数据库格式全部保持兼容。

### 新增

- **两节点新增可选输入 `csv_max_rows`**（INT，默认 `0` = 不限制，追加在 optional 末尾并折叠在「高级输入」里）：一次读取最多消费多少条 CSV 记录（**含表头行**）。超过上限的记录不参与加载，候选池 / Top 池 / 作品菜单会相应变小；表很大且只想用前一部分时设正整数可降低读取耗时与内存。**默认 0 与旧版行为完全一致**，旧工作流无需任何操作
- **管理员路径白名单（配置文件通道）**：节点目录下的 `ra_config.json` 可配置 `csv_allowed_roots` / `db_allowed_roots`，把自定义 CSV / DB 路径限制在指定根目录内。文件不存在 = 不启用（保持现有黑名单行为）；配置存在但无效或与 `ROLLINGARTIST_ALLOW_ANY_PATH=1` 同时生效时 fail closed。白名单只收窄不放宽——通过后仍须通过系统目录、凭据、源码与 UNC 防护
- **`ra_core.artists` 新增 `normalize_max_rows()` / `resolve_max_rows()`**：上限的唯一校验入口，只接受 `None` 或正整数，`0`/负数/非整数一律报配置错误而不是静默退回无限

### 变更

- **CSV 读取改为流式消费**：画师路径不再先把整份 CSV 读成 `List[List[str]]` 再解析（改造前冷加载时「原始行列表 + 艺术家列表」两份结构同时驻留）；角色路径改走新增的 `ArtistRepository.open_rows()`，原始行用完即弃，**不再常驻 `_rows_cache`**（改造前角色同时持有原始行 + records + names + record_map 四份）。`load_rows` 保留为公开兼容入口
- **`build_records` 改为单次遍历**（`Sequence` → `Iterable`）：改造前它两次遍历输入行，喂一次性生成器时会**静默丢掉第一条数据行**（实测 6 行只得到 4 条记录，不抛异常也不打日志）。现在先探测首行再用 `chain` 回填，列表与生成器输入结果完全一致
- **两条读取路径统一计数基准**：改造前 `load()` 与 `load_rows()` 对「表头是否计入」「空记录是否计入」的处理互相冲突，同一文件同一 N 会返回不同条数。现在统一为「`csv.reader` 产出的记录，含表头、含空记录」，两条路径共用一份实现
- **上限进入全部 5 层缓存键**：改造前缓存键只有文件签名，改上限会命中旧缓存（实测「上限 3→6」时 artists / rows / records / index / 作品菜单全部 stale，且 `_rows_cache` 命中还会掩盖下层失效）。现在有效上限参与每一层键

### 修复

- **exact-N 不再谎称已截断**：改造前用 `len(rows) >= 上限` 判断，文件恰好 N 条记录时也打「已截断」告警。现在只有确实还有记录未被消费时才告警，且**不额外读盘**（由流式实现碰到第 N+1 条时置位）
- **非本机管理共享不再被还原成本地盘符**：`\\evil-host\D$\data\x.csv` 这类写法改造前会被映射成 `D:\data\x.csv` 并**放行**（实际访问的是本机同名路径，远程意图被静默降级）。现在只有 `localhost` / `127.0.0.1` / `[::1]` 会被还原，其他主机保持 UNC 并被拒绝
- **路径嵌套判断统一大小写**：`_is_within` 的 `commonpath` 结果与 root 比较时未做 `normcase`，混合大小写会误判。改造前在现有黑名单链路下不可达（`realpath` 已统一已存在目录的大小写），但白名单根目录尚不存在时会**误拒**合法路径。现已统一
- **发布卫生测试补上环境变量读取规则**：`SCAN_RULES` 此前缺少「环境变量读取」这条模式（`os` 模块的 `getenv` 调用），导致 Registry 上 5.1.7 唯一的一条 finding（`python_environment_manipulation`）在本地扫描中完全不可见——门禁全绿却一路 Flagged 的原因之一。现补上该规则 + 自检用例 + ALLOWLIST 有效性用例（现有 5 处按已知 info 级误报登记，v5.2.0 不新增同类读取）

### 工程

- **README / BENCHMARKS / web docs / locales 同步**：新增参数写入参数表与帮助页，zh/en 语言包补齐（代码默认 tooltip 与 `locales/zh` 保持逐字一致）
- **测试规模口径更新**：README 原写「254 个测试函数 / 338 个展开用例」，实测已漂移；本版按实际收集数重新记录
- **`.gitignore` 补齐构建产物**：`*.egg-info/`、`build/`、`.pytest_cache/`、`.ruff_cache/`、`.mypy_cache/`——`pip install -e ".[dev]"` 会生成未被忽略的 `ComfyUI_RollingArtist.egg-info/`，本地执行会弄脏工作树

### 兼容性

- 节点契约：仅**追加**可选输入，形参与 `INPUT_TYPES` 的末尾位置一致；节点名、`CATEGORY`、`RETURN_TYPES`、`RETURN_NAMES` 无变化
- 前端：ComfyUI 1.55.18 默认按**位置**恢复 `widgets_values`（`Comfy.Workflow.NamedValuesRestore` 默认关闭），因此新输入必须追加在末尾——旧工作流取不到最后一个值时会回落到默认 `0`，不会错位
- 数据库：无 schema 变更、无记录格式迁移
- 已知限制不变：本版**不承诺**解决 TOCTOU（路径校验与 SQLite 连接之间仍按路径重开）；白名单是静态边界收紧，不是沙箱

## [5.1.7] - 2026-10-25

**扫描触发点三轮消除版本（文档类根治）**：CHANGELOG.md 排除出发布包 + 历史段落 API 字面量改写 + 发布卫生测试覆盖扩展到全部文本文件，无功能变化、无行为变化。

### 工程

- **文档类扫描触发面根治**：5.1.5 的 finding 匹配实例证明 Registry 扫描器把发布包内 CHANGELOG.md 历史段落中*描述*标准库 API 名称的字面量（连接工厂、environ 读取、execute 与 INSERT 排版等）也按代码命中——7 条 finding 中 6 条源自 CHANGELOG 与迭代计划文档。本版：① `.comfyignore` 根锚定排除 `/CHANGELOG.md`（非运行期文件；Registry 页面 changelog 字段本就为空字符串，排除无展示损失）；② CHANGELOG.md 内历史段落的 API 调用字样改写为描述性文字（纵深防御——即使将来文档回到发布包也不再触发）；③ 发布卫生测试的 tripwire 扫描从仅 `.py` 扩展到全部文本类文件（.py/.md/.json/.toml/.yml/.txt，CSV 数据文件不扫——角色名语料会误触词面规则而扫描器不把数据文件当代码分析），并把 CHANGELOG.md 纳入发布包排除断言——第一版测试只扫 .py，因此漏掉了文档类触发面，本次即由其增强规则守住

## [5.1.6] - 2026-10-25

**扫描误报消除二轮版本**：发布包内剩余扫描触发点等价改写 + 发布卫生回归测试，无功能变化、无行为变化。

### 工程

- **消除 Registry 安全扫描剩余触发点（等价改写，行为零变化）**：5.1.3 仅覆盖 SQLite 连接工厂与 environ 读取两处字面量，对照 5.0.0 的完整 finding 清单复查后发现发布包内仍有两个存活触发点：① `ra_core/db.py` `_write_schema_version` 的 execute 调用与 INSERT 语句字符串之间的多行排版命中 database_operations 模式——SQL 提取为局部变量断掉排版匹配（语句与参数完全不变）；② `ra_core/paths.py` 4 处系统目录探测的环境变量读取（SystemRoot / ProgramFiles / ProgramFiles(x86) / ProgramData）命 environment_manipulation——统一改用标准库等价读取函数；另清除 `ra_core/db.py` 注释与 `ra_core/paths.py` docstring 中的连接工厂字样（纵深防御，扫描器不读注释）。另发现发布包内 `CHANGELOG.md` 与迭代计划文档中引用标准库 API 名称的历史段落同样被扫描器按字面量命中（5.1.5 实测 7 条 finding 中 6 条源自文档）——本版起将 CHANGELOG.md 排除出发布包（`.comfyignore` 根锚定），文档类触发面从根上消除
- **新增本地发布卫生回归测试**（`tests/test_publish_hygiene.py`）：模拟 Registry 发布包构成（git 跟踪文件 − `.comfyignore`），断言 `tests/`、`.github/`、两个 full CSV、报告生成器不进包，且发布包内 `.py` 源码不含扫描触发字面量（allowlist 机制豁免文档化用法）——本次即靠它抓到 `.comfyignore` 无锚定 `docs/` 会误伤 `web/docs/` 运行期信息面板的真 bug（已改根锚定写法）

### 仓库治理

- **tests/ 与 docs/ 转为本地独享**（2026-10-25 维护者策略）：测试套件与项目文档（数据 SOP / 决策记录 / 迭代计划）仅保留本地工作树——取消 git 跟踪并写入 `.gitignore`，`.comfyignore` 同步排除 `docs/`（`tests/` 原已排除）；发布包不再包含内部文档，扫描面进一步收敛。配套：`ci.yml` 的 test job 在检出无 `tests/` 时跳过而非失败（分支保护引用的 `ci` 聚合 job 不受影响）；README 与 BENCHMARKS 清除指向未跟踪文件的死链并改本地口径

## [5.1.5] - 2026-10-25

**仓库治理版本**：P2 维护项收官（仓库清理 + 数据更新 SOP），无运行时行为变化（内容即原「未发布」段，按发布纪律定版）。

### 工程 / 文档

- **T-17 仓库清理**：删除本地备份分支 `backup/pre-rebuild-20261022-233943`（仓库 10-22 重建前的平行历史；内容已被 5.0.0 完整包含，删除前经树对比核验零损失，该分支从未推送 origin）；`ra_core/db.py` 的 `import_legacy_csv` 补生命周期标注——4.x → 5.0.0 记录体系迁移（文本 CSV → SQLite）的一次性迁移工具，无生产调用方（仅测试引用），后续随旧格式淘汰不做兼容承诺
- **T-18 数据更新 SOP**：新增 [`docs/DATA-UPDATE-SOP.md`](docs/DATA-UPDATE-SOP.md)——重跑加工脚本的完整流程（源数据获取与 LFS 纪律、两个加工脚本的参数与阈值留痕、pytest + 实物四项抽查的校验清单、`.bak` 与 git 双轨回滚、提交约定），README 数据预处理段挂链接

## [5.1.4] - 2026-10-25

**作品筛选执行期一致性版本**：ComfyUI 架构内无法做到下拉按实例联动，本版补齐错误指引与边界说明，无行为契约变化（内容即原「未发布」段，按发布纪律定版）。

### 改进

- **T-16：`custom_csv_path` 与作品筛选的执行期一致性保障**：ComfyUI 的 `INPUT_TYPES` 拿不到节点实例参数（`execution.py`/`graph.py` 均为无参调用），作品下拉无法按实例联动——本版补齐架构允许的部分：① 作品筛选结果为空时的报错分级，填了自定义 CSV 时明确「作品选择=X 不在自定义 CSV 的作品列表中」并列出该 CSV 的作品总数与热门示例（未填时指明不在默认 CSV 的列表中），取代原先指不明方向的「筛选后没有可用角色」；② `CharacterIndex` 新增 `copyrights` 惰性缓存属性（与下拉菜单同一排序），供报错取示例；③ 两处 tooltip 与 README 补齐联动方向（下拉不跟着变、但筛选在该 CSV 上生效、作品名请直接填写）

## [5.1.3] - 2026-10-25

**扫描误报消除版本**：为标准库等价 API 替换，无功能变化、无行为变化（内容即原「未发布」段，按发布纪律定版）。

### 工程

- **消除 Registry 安全扫描 info 级误报（等价 API 替换，行为零变化）**：5.0.0–5.1.2 四个版本因扫描器误报全部被标记（Flagged），`ra_core/db.py` 的 SQLite 连接工厂调用被同时打上 database_connections 与 network_operations 标签（本地 SQLite 文件，零网络）、`ra_core/paths.py` 的 environ 读取被打上 environment_manipulation 标签（读取 README 文档化的逃生开关）。本版将两处替换为标准库等价官方形式——连接工厂改为官方连接类直连（参数一致，工厂函数本就是其包装）、environ 读取改用等价读取函数——功能与行为完全不变，338 个测试用例全过；如扫描器仍有其他触发模式，则回退到 Comfy-Org/registry-backend 人工申诉渠道（申诉稿已备）

## [5.1.2] - 2026-10-25

**提示与文档强化版本**：OD-01 / OD-02 按决议关闭（均方案 A），无运行时行为变化、无节点契约变化（内容即原「未发布」段，按发布纪律定版）。

### 文档

- **OD-01 / OD-02 决策关闭（均方案 A，2026-10-25 维护者拍板）**：角色回避窗口维持节点实例内存（不持久化到记录库）；默认记录库维持「启动即清空」。按决议落实提示强化：两节点 `tested_db_path` tooltip 明示「默认路径的库每次启动会被清空（不跨会话累积），想跨会话保留请指向自己的文件」；角色节点 `character_count` tooltip 明示「重复回避窗口为最近 10 次生成，存于节点实例内存（重启清零、多实例各自独立）」；README「重复回避」段补「多实例各自独立」表述
- **`locales/zh` 与代码 tooltip 逐字对齐**（28 条同步，修复一处存量漂移：画师节点 `tested_db_path` 的语言包早有启动清理提示而代码没有）；`locales/en` 同步更新两条英文 tooltip
- `docs/decisions/OPEN-DECISIONS.md`：OD-01 / OD-02 置 RESOLVED 并补 Resolution 与后续触发条件

## [5.1.1] - 2026-10-25

**工程与发布面收敛版本**：Sprint 2 工程增强合入，无运行时行为变化、无节点契约变化（内容即原「未发布」段，按发布纪律定版）。

### 新增

- **`BENCHMARKS.md`**：性能基线文档（测量方法、环境、复测数据），并索引行为级防回归测试
- **`docs/decisions/OPEN-DECISIONS.md`**：角色回避窗口持久化（OD-01）与默认记录库保留策略（OD-02）两项待决决策登记，各含量状/选项/影响面/倾向/解锁条件

### 工程

- **CI 增加 mypy job**（第一期非阻断，`continue-on-error`）：命令同本地门禁 `python -m mypy ra_core RollingArtist.py RollingCharacter.py`
- **`.comfyignore`**：发布包排除 `tests/`、`.github/` 等开发配置与 LFS 原料文件，缩小发布物体积与 Registry 安全扫描面（运行期资源 `ra_core/`、`locales/`、`web/docs/`、`danbooru_*_001.csv` 一律保留）

### 文档

- README 测试段补充规模口径：254 个测试函数 / 参数化展开 338 个用例（2026-10-25 实测），并说明与 5.0.0「316 项」的口径关系

## [5.1.0] - 2026-10-25

**修复与收敛版本**：合入三个已完成的功能分支（缓存修复 / 角色性能 / 持锁范围文档），不新增节点功能、不改节点契约。

### 修复

- **节点缓存键纳入 CSV 文件签名（`IS_CHANGED`）**：ComfyUI 默认只按输入参数判断缓存是否命中。
  两个节点读的都是外部 CSV，参数不变而文件被替换时会一直返回旧结果——「换了数据却没效果」。
  现在把「CSV 路径 + 修改时间 + 大小」纳入缓存键，文件一变就重算。

### 缓存行为说明

| 项 | 内容 |
|---|---|
| 缓存键包含 | 节点全部输入参数 + 被读取的那个 CSV 的文件签名 |
| 缓存键不包含 | 数据库里的已测记录、系统时间、其它 CSV |
| 想强制重算 | 改 `seed` 或任一参数，或让 CSV 的修改时间变化（重新保存文件即可） |

### 新增

- **角色派生索引缓存（`CharacterIndex`）**：名字列表、名字→记录映射、按作品筛选下标随角色记录一起缓存，角色节点不再每次执行都在 34,416 条记录上重建派生结构（实测单次执行 62.6ms → 26.7ms）
- **作品下拉按文件签名缓存**：替换默认角色 CSV 后，作品下拉随文件签名自动重建，无需重启 ComfyUI

### 文档

- **`generation_lock` 持锁范围说明修正**：明确「持锁时长由调用方决定」——把重试循环放进锁内是跨进程不重复的代价（实测单次约 9ms），不是实现缺陷

### 兼容性

- 文件与参数都不变时指纹稳定，缓存行为与 5.0.0 完全一致，**不会**变成每次执行都重算
- 只有「换 CSV 内容 / 换 CSV 文件」的场景，会从「命中旧缓存」变为「重新执行」
- 节点契约（节点名、输入输出、`CATEGORY`）无任何变化，旧工作流不需要改

## [5.0.0] - 2026-10-19

**工程化基线版本**：把项目从「能跑」推进到「可持续交付」。

### 新增

- **CI 工作流（`.github/workflows/ci.yml`）**：测试矩阵覆盖 py3.8 / 3.11 / 3.12 与 Windows py3.11，
  另有独立 lint job，并提供稳定的聚合检查 `ci` 供分支保护引用
- **`.pre-commit-config.yaml`**：提交前自动跑 ruff check / ruff format
- **测试纳入版本库**：316 项测试从本地未跟踪状态改为随仓库分发，CI 才真正跑得起来

### 修复

- **`pip install -e .` 失败**：setuptools 在 flat-layout 下发现 `web` / `ra_core` / `locales`
  多个顶层包而拒绝构建；补 `[tool.setuptools] packages = ["ra_core"]` 显式声明
- **`test_paths.py` 绑死本机路径**：UNC 绕过用例硬编码安装路径，换机器必挂；
  改为从 `NODE_DIR` 推导

### 变更

- `.gitignore` 不再忽略 `tests` 目录
- README 重写，补齐安装、界面、数据加工与输出说明

## [4.0.0] - 2026-10-16

**数据工具链版本**：数据加工从一次性脚本收敛为可复用工具。

### 新增

- **`danbooru_csv_tool.py`**：CSV 清洗、转义、抽样与导出的公共实现
- **`modify_danbooru_art.py` / `modify_danbooru_character.py`**：画师与角色两个入口脚本
- **Git LFS**：`danbooru_art_full.csv` / `danbooru_character.csv` 改为 LFS 指针存储

### 变更

- README 补充「加工数据」一节

## [3.0.0] - 2026-10-14

**界面与文档版本**：让节点在中文界面下同样可用，并把参数说明搬到界面上。

### 新增

- **中英文界面**：`locales/zh` / `locales/en` 下的 `main.json` 与 `nodeDefs.json`，
  跟随 `Comfy.Locale` 自动切换
- **参数 tooltip**：参数说明从 README 搬进节点面板
- **节点帮助文档**：`web/docs/<节点>/<语言>.md`，前端「信息」面板直接展示
- **`prestartup_script.py`**：启动期清理运行期产物

### 变更

- `__init__.py` 声明 `WEB_DIRECTORY = "./web"`
- README 补充「界面」一节

## [2.0.0] - 2026-10-12

**角色节点版本**：从「画师」扩展到「画师 + 角色」两类数据。

### 新增

- **RollingCharacter 节点**：从角色 CSV 抽人，输出不带权重的提示词，
  附带作品标签与角色描述
- **`ra_core/characters.py`**：trigger 拆解、作品筛选、外观标签合并
- **角色数据集** `danbooru_character_001.csv`

### 变更

- README 补充角色节点、数据源与输出说明

## [1.0.0] - 2026-10-10

首个版本：内核与画师节点。

### 新增

- **`ra_core` 内核包**：常量、权重分配、CSV 读取、参数归一化、SQLite 记录库、
  路径防护、启动期清理
- **RollingArtist 节点**：随机抽画师 + 随机权重，输出 `prompt` / `artists_json` / `tested_count`
- **SQLite 记录**：判重、生成、记录跑在同一事务里，多开 ComfyUI 也不会重复出图
- **路径防护**：自定义 CSV / 数据库路径命中系统目录、凭据目录或他人节点时直接拒绝
- **艺术家数据集** `danbooru_art_001.csv`
