# ComfyUI RollingArtist

**RollingArtist** 是一个 ComfyUI 节点，用于生成包含随机权重的艺术家提示文本。通过动态调整 Top 艺术家比例，实现可控的随机组合。

![节点界面示例](https://github.com/user-attachments/assets/e6590c66-8050-4dfd-8416-489a8ec4e429)


## 核心功能

- **动态 Top 控制**：通过 `artist_top_ratio` 参数定义前 X% 艺术家为 Top 级
- **权重分配**：精确控制每个艺术家的权重范围和总和
- **种子控制**：统一随机种子确保结果可复现
- **线程安全**：支持多线程环境下的稳定运行

## 安装指南

1. 克隆仓库到 ComfyUI 的 `custom_nodes` 目录：
   ```bash
   git clone https://github.com/StarAsh042/ComfyUI_RollingArtist.git
   ```
2. 确保以下文件存在：
   - `RollingArtist.py`（主节点文件）
   - `danbooru_art_001.csv`（艺术家数据源）
   - `modify_danbooru_csv.py`（可选预处理工具）

## 参数说明

| 参数名称           | 类型    | 范围         | 说明                          |
|--------------------|---------|--------------|-------------------------------|
| artist_top_count   | INT     | 1-10         | 生成的艺术家人数              |
| artist_top_ratio   | FLOAT   | 0.1-1.0      | 定义前 X% 艺术家为 Top 级     |
| artists_prefix     | BOOLEAN | -            | 添加 "artist:" 前缀           |
| weight_min         | FLOAT   | 0.1-1.0      | 单个艺术家最小权重            |
| weight_max         | FLOAT   | 0.5-2.0      | 单个艺术家最大权重            |
| weight_total       | FLOAT   | 1.0-20.0     | 所有权重总和                  |
| seed               | INT     | 0-4294967295 | 控制随机性的种子值            |

## 使用示例

典型工作流配置：
1. 添加 **RollingArtist** 节点
2. 连接至文本编码器
3. 输出示例：
   ```
   (artist:John_Doe:0.5),(artist:Jane_Smith:1.2),...
   ```

## 数据预处理
当 CSV 文件出现格式问题时，运行：
```bash
python modify_danbooru_csv.py
```

## 许可证
[GNU Affero General Public License v3](LICENSE)
