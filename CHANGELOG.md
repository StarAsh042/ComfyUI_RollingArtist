# 变更记录

本文件记录 RollingArtist 的重要变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
