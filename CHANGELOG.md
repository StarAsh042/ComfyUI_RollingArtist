# 变更记录

本文件记录 RollingArtist 的重要变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.0] - 2026-10-10

首个版本：内核与画师节点。

### 新增

- **`ra_core` 内核包**：常量、权重分配、CSV 读取、参数归一化、SQLite 记录库、
  路径防护、启动期清理
- **RollingArtist 节点**：随机抽画师 + 随机权重，输出 `prompt` / `artists_json` / `tested_count`
- **SQLite 记录**：判重、生成、记录跑在同一事务里，多开 ComfyUI 也不会重复出图
- **路径防护**：自定义 CSV / 数据库路径命中系统目录、凭据目录或他人节点时直接拒绝
- **艺术家数据集** `danbooru_art_001.csv`
