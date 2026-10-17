# ComfyUI RollingArtist

从 danbooru 数据里随机抽人并生成提示词：画师带权重，角色不带权重。

## 节点

| 节点 | 数据源 | 输出 |
|---|---|---|
| **RollingArtist** | 画师 CSV | 带权重的 `(artist:名字:权重)` |
| **RollingCharacter** | 角色 CSV | 不带权重的角色标签 + 作品标签 |

## 功能

- 画师：随机抽取、权重曲线（flat / dominant / ramp）、Top 池、前缀、种子、名单过滤
- 角色：按作品筛选、外观标签合并开关、最近 10 条输出内不重复
- 两个节点共用 SQLite 记录库，判重与写入在同一事务里完成

## 安装

把本目录放进 `ComfyUI/custom_nodes/` 下，重启 ComfyUI，
节点列表的 `RollingArtist` 分类下会出现两个节点。

## 数据

- 画师：`danbooru_art_001.csv`（`name,count,url`）
- 角色：`danbooru_character_001.csv`（`name,count,url`，`trigger` 列拆成「角色,作品」）

也可以在节点参数里指定自己的 CSV。

## 输出

| 节点 | 输出 |
|---|---|
| RollingArtist | `prompt` / `artists_json` / `tested_count` |
| RollingCharacter | `prompt` / `characters_json` / `tested_count` |

## 许可证

MIT
