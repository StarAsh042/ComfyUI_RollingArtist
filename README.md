# ComfyUI RollingArtist

**RollingArtist** 是一个 ComfyUI 节点，用于生成包含随机权重的艺术家提示文本。通过动态调整 Top 艺术家比例，实现可控的随机组合。

![ComfyUI_RollingArtist](https://github.com/user-attachments/assets/157de400-e4e9-479b-ac8e-8698d2661112)


## 核心功能

- **动态控制**：定义Top艺术家范围，控制实际输出数量
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
| artist_count       | INT     | 1-10         | 选择生成的艺术家人数          |
| artist_top_count   | INT     | 1-10         | 输出中包含的Top艺术家数量     |
| artist_top_ratio   | FLOAT   | 0.1-1.0      | 提取CSV中Top艺术家占前百分比  |
| artists_prefix     | BOOLEAN | -            | 添加"artist:"前缀             |
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
