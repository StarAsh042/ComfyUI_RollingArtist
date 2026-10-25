# 变更记录

本文件记录 RollingArtist 的重要变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
