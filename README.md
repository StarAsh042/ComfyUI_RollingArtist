# ComfyUI RollingArtist

**RollingArtist** 是一个 ComfyUI 节点，用于生成包含随机权重的艺术家提示文本，适配文本到图像生成模型。节点从 CSV 文件中读取艺术家列表，并根据参数生成组合提示。

## 概述

- **功能**：根据设定的艺术家数量、权重范围及统一的种子，随机生成格式化的提示字符串。
- **数据源**：使用 `danbooru_art_001.csv` 作为艺术家数据源。若 CSV 格式存在问题，可运行附带的 `modify_danbooru_csv.py` 进行预处理。

## 安装

1. 将本仓库克隆到 ComfyUI 的 `custom_nodes` 目录下：
   ```bash
   git clone https://github.com/StarAsh042/ComfyUI_RollingArtist.git
   ```
2. 确保 `danbooru_art_001.csv` 文件与节点代码在同一目录内。

## 使用方法

1. 在 ComfyUI 工作流中添加 "RollingArtist" 节点。
2. 配置主要参数：
   - **artist_count**：选择生成的艺术家数量。
   - **artists_prefix**：是否为艺术家名称添加前缀。
   - **weight_min / weight_max / weight_total**：定义各艺术家的权重范围和总权重。
   - **seed**：统一的随机种子，确保生成结果可重复。
3. 将节点输出的提示文本连接至文本到图像生成模型进行使用。

## 附加工具

- 如遇 CSV 格式问题，可运行 `modify_danbooru_csv.py` 脚本对 CSV 文件进行预处理。

## 许可证

本项目采用 [GNU Affero General Public License v3](https://www.gnu.org/licenses/agpl-3.0.html) 许可协议。详情请参阅 [LICENSE](./LICENSE) 文件。