# ComfyUI RollingArtist

从 danbooru 画师数据里随机抽人并分配随机权重，直接产出可用于文生图的提示词。

## 功能

- 随机抽取若干画师，按权重曲线分配权重，输出 `(artist:name:weight)` 形式
- 支持 Top 池、前缀、随机种子、抽样偏好与名单过滤
- 已测记录写入 SQLite，同一组合不会重复出现
- 输出 `prompt` / `artists_json` / `tested_count`

## 安装

把本目录放进 `ComfyUI/custom_nodes/` 下，重启 ComfyUI，
在节点列表的 `RollingArtist` 分类里即可看到。

## 数据

默认读取节点目录下的 `danbooru_art_001.csv`（`name,count,url` 三列），
也可以在节点参数里指定自己的 CSV。

## 输出

| 输出 | 类型 | 说明 |
|---|---|---|
| `prompt` | STRING | `(artist:名字:权重)` 拼接串 |
| `artists_json` | STRING | 结构化结果，含名字、权重、是否 Top |
| `tested_count` | STRING | 已记录的组合数 |

## 许可证

MIT
