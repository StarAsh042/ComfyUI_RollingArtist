# ComfyUI RollingArtist

**RollingArtist** 是一个 ComfyUI 节点，用于生成包含随机权重的艺术家提示文本。通过动态调整 Top 艺术家比例，实现可控的随机组合。

## 核心功能

- **动态控制**：定义Top艺术家范围，控制实际输出数量
- **权重分配**：精确控制每个艺术家的权重范围和总和
- **种子控制**：统一随机种子确保结果可复现
- **线程安全**：支持多线程环境下的稳定运行
- **类型注解**：完整的类型提示，提高代码可维护性
- **结构化输出**：`artists_json` 包含艺术家、权重、是否Top 等信息
- **性能优化**：CSV / 已测记录的进程级缓存，多列 CSV 自动识别
- **格式规则**：权重为 `1.0` 时不输出权重，直接输出名称
- **参数说明**：17 个输入都带 `tooltip`，悬停可见，右侧「信息」面板的“描述”列同步显示
- **中英文界面**：节点名、参数名与参数说明跟随 ComfyUI 界面语言自动切换（`locales/zh`、`locales/en`）
- **节点帮助页**：「信息」面板展示随语言切换的文档（`web/docs/RollingArtist/<语言>.md`）
- **启动即清场**：每次启动自动删除节点目录下默认的 `tested_combinations.csv` 与 `*_remaining.csv`

## 代码结构

核心逻辑按职责拆分，便于阅读、复用与单独测试（均不依赖 ComfyUI）：

```
ComfyUI_RollingArtist/
├── RollingArtist.py       # 节点定义（INPUT_TYPES / tooltip / 输出）与单次生成编排
├── ra_core/
│   ├── constants.py       # 路径、权重步长、规模阈值、日志器
│   ├── artists.py         # 艺术家 CSV 解析 / 加载缓存 / 索引化抽样
│   ├── weights.py         # 权重分配、提示词构建解析、去重键
│   ├── storage.py         # CSV 原子读写、已测记录增量存储
│   ├── exact.py           # Exact 模式组合池
│   └── startup.py         # 启动期清理默认记录文件（由 prestartup_script.py 调用）
├── prestartup_script.py   # ComfyUI 启动钩子：清理上次运行留下的默认记录文件
├── locales/               # 界面文案：<语言>/nodeDefs.json（节点与参数）、main.json（节点分类）
│   ├── zh/
│   └── en/
├── web/docs/              # 节点帮助页：RollingArtist/<语言>.md + 英文回退 RollingArtist.md
└── __init__.py            # 节点注册 + WEB_DIRECTORY
```

关键性能设计：

| 场景 | 旧实现 | 现实现 |
|------|--------|--------|
| 艺术家抽样（n 个艺术家） | 每个元素重复构建 set，复杂度 O(n²)（5000 个艺术家约 3 秒） | 直接对索引抽样，O(k)，与总量无关（同等条件 <1 ms） |
| 每次执行加载 CSV | 每次重新解析整个文件 | 按 `路径+列+修改时间+大小` 缓存，文件未变不重复解析 |
| 已测记录去重 | 每次执行重新读取并解析整个历史文件 | 增量解析，只读取新增字节 |
| Exact 模式取组合 | 每次读取并整体重写剩余组合文件（大 CSV 下会产生数百 MB 文件） | 组合池进程内缓存 + swap-pop 删除 + 临时文件原子替换 |

## 安装指南

1. 克隆仓库到 ComfyUI 的 `custom_nodes` 目录：
   ```bash
   git clone https://github.com/StarAsh042/ComfyUI_RollingArtist.git
   ```
2. 确保以下文件/目录存在：
   - `RollingArtist.py`（主节点文件）
   - `ra_core/`（核心实现子包，包含 constants / artists / weights / storage / exact / startup）
   - `prestartup_script.py`（启动钩子，用于清理默认记录文件）
   - `locales/`、`web/`（界面中英文与节点帮助页；缺失只影响界面文案，不影响功能）
   - `danbooru_art_001.csv`（默认艺术家数据源）
   - `modify_danbooru_csv.py`（可选预处理工具）
   说明：`ra_core` 使用相对导入，由 ComfyUI 以包的形式加载，无需额外配置；
   `locales/` 与 `web/docs/` 由 ComfyUI 按目录约定自动加载，同样无需注册。

## 界面语言与节点帮助页

- **中英文自动切换**：节点显示名、参数名与参数说明（tooltip）跟随 ComfyUI 的界面语言
  （设置里的 `Comfy.Locale`）。中文来自 `locales/zh/`，英文来自 `locales/en/`，
  ComfyUI 启动时按目录约定读取（`GET /i18n`），代码里无需注册。
- **节点帮助页**：右侧「信息」面板的内容取自 `web/docs/`，前端按
  `/extensions/ComfyUI_RollingArtist/docs/RollingArtist/<语言>.md` 请求，该语言缺失时回退到
  `web/docs/RollingArtist.md`。面板里同时会给出各参数的名称与说明。
- **新增语言**：复制 `locales/en/` 为 `locales/<语言代码>/` 并翻译 `nodeDefs.json`
  （语言代码用官方集合：`zh` / `zh-TW` / `ja` / `ko` / `fr` / `ru` / `es` / `ar` …）；
  帮助页同理新增 `web/docs/RollingArtist/<语言代码>.md`。
- 默认文案（无 i18n 时显示的内容）写在 `RollingArtist.py` 的 `INPUT_TYPES`（`tooltip` 键）里。

## 参数说明

下列顺序即界面上参数的出现顺序；`advanced` 参数在界面里折叠在“高级输入”中。

| 参数名称           | 类型    | 范围         | 说明                          |
|--------------------|---------|--------------|-------------------------------|
| artist_count       | INT     | 1-10         | 选择生成的艺术家人数（默认3） |
| artist_top_ratio   | FLOAT   | 0.01-1.0     | 提取CSV中Top艺术家占前百分比（默认0.01） |
| artist_top_count   | INT     | 1-10         | 输出中包含的Top艺术家数量（默认1） |
| weight_min         | FLOAT   | 0.1-2.0      | 单个艺术家最小权重值          |
| weight_max         | FLOAT   | 0.1-2.0      | 单个艺术家最大权重值          |
| weight_total       | FLOAT   | 0.0-20.0     | 所有权重总和（默认2.0）       |
| artists_prefix     | BOOLEAN | -            | 添加"artist:"前缀             |
| seed               | INT     | 0-4294967295 | 控制随机性的种子值            |

### 可选参数

- `use_top_priority`：BOOLEAN，是否启用 Top 优先池（默认开启）。关闭后从完整 CSV 中均匀抽取，不再强制包含 Top 艺术家
- `sort_by_weight`：BOOLEAN，是否按权重降序排序（默认开启）
- `exclude_artists`：STRING，逗号分隔的排除名单（精确匹配）
- `force_include`：STRING，逗号分隔的强制包含名单。这些艺术家一定出现在输出中，优先级高于 `exclude_artists`；数量超过 `artist_count` 时从中随机抽取
- `dedup_mode`：ENUM（`none` / `full_prompt` / `artist_set` / `artist_list`，默认 `none`），跳过已生成过的组合
  - `none`：不去重
  - `full_prompt`：艺术家顺序与权重完全一致才算重复
  - `artist_set`：仅比较艺术家集合，忽略顺序与权重
  - `artist_list`：比较排序后的艺术家名，忽略权重
- `max_attempts`：INT（1-100，默认10），去重时的最大重试次数；超过后接受最后一次结果，避免死循环

### 高级参数（界面默认折叠）

- `csv_column`：STRING（默认 `auto`），CSV 列的取法
  - `auto`：自动识别表头并优先取 `artist` / `character` / `name` / `tag` 列；无表头时取首列
  - `all`：展平所有列（与 3.0.0 的行为一致）
  - `0` / `1` / …：按列序号取值
  - `artist` / `character` / `core_tags` 等：按表头列名取值
  - 说明：仓库内的 `danbooru_art_full.csv`、`danbooru_character.csv` 为多列文件，
    旧版本会把表头、count、url 等也当作艺术家名；现在默认配置即可正确使用
- `custom_csv_path`：STRING，自定义 CSV 路径（留空使用默认）
- `tested_csv_path`：STRING，已生成组合的记录 CSV 路径（留空使用节点目录下 `tested_combinations.csv`）。每行格式为 `艺术家(|分隔), 权重(,分隔), prompt`

### Exact 模式（穷举）

当 `artist_count = 1` 且未设置 `force_include` 时进入 Exact 模式：

1. 按 `weight_min`~`weight_max`（步长 0.1）枚举出所有 `(艺术家, 权重)` 排列，写入 `<tested_csv_path>` 对应的 `*_remaining.csv`
2. 每次运行从组合池中随机取一条并移除（swap-pop），保证在穷举完之前**绝不重复**
3. 已记录在 `tested_csv_path` 中的组合会被自动剔除
4. 全部穷举后，`prompt` 返回特殊字符串 `ALL_COMBINATIONS_TESTED`（`artists_json` 中 `status` 同步为该值），下游需自行处理

组合池的实现要点：

- 组合池在进程内缓存（按文件签名失效），并用临时文件 + 原子替换落盘，中途失败不会留下半截文件
- 组合总数超过 `EXACT_COMBOS_LIMIT`（100 万）时**不再生成** `*_remaining.csv`，
  改为“随机采样 + 去重”：在最大重试次数内取未测过的组合，避免大型 CSV 生成数百 MB 的中间文件
- 检测到单个体积超过 64 MB 的遗留 `*_remaining.csv` 时会直接报错并提示删除，避免内存被撑爆

删除 `*_remaining.csv` 即可按当前配置重新生成排列；删除 `tested_csv_path` 相当于清空历史。

**默认记录文件每次启动都会清理**：ComfyUI 启动时 `prestartup_script.py` 会删除节点目录下的
`tested_combinations.csv` 与 `tested_combinations_remaining.csv`，让每次运行都从干净状态开始。
需要跨会话接着去重/穷举，请把 `tested_csv_path` 指到你自己的文件——**只清理默认路径**，
显式指定的路径不会被碰。

## Exact 模式与去重的注意事项

- Exact 模式下 `artists_json` 的 `top` 字段来自当前 Top 池，若中途替换了艺术家 CSV，该标记可能与实际不符
- Exact 模式下 `dedup_mode` 与 `weight_total` **不生效**：去重固定按 `full_prompt` 判定，
  权重按 `weight_min`~`weight_max` 网格取单个值；以某组配置首次进入该模式时会输出一条 INFO 提示
- 去重依赖 `tested_csv_path` 的历史记录，更换该路径相当于重置去重状态；
  默认路径的记录文件在每次启动时会被清理（见上文），要跨会话保留历史请显式指定 `tested_csv_path`
- 同一 `tested_csv_path` 上的“读取已测 → 生成 → 记录”由进程内共享锁保护，
  多个 RollingArtist 实例（或并发线程）不会生成重复组合
- 降级采样模式下，若连续 `max_attempts` 次都命中已测组合，会按“已穷举”返回
  `ALL_COMBINATIONS_TESTED`（组合总量过大时无法精确判定，仅作近似判断）

## 技术特性

- **权重算法**：以 0.1 为整数单位运算，先按随机倾向值分配再用最大余数法补齐，
  因此**任一权重都严格落在 `[weight_min, weight_max]` 内**，且总和精确等于 `weight_total`
- **完善的类型注解**：使用Python类型提示增强代码可读性和IDE支持
- **线程安全设计**：进程级共享缓存 + 可重入锁；文件写入使用原子替换
- **明确的错误处理**：统一使用 `logging`，CSV 缺失 / 解析失败 / 参数非法时抛出可读异常，
  不再静默返回空提示词
- **降级必有信号**：可用艺术家不足、重试耗尽后返回重复组合、已测记录写入失败、
  组合已穷举完毕，均会输出 WARNING / ERROR 日志（批量执行时不会刷屏）

## 使用示例

典型工作流配置：
1. 添加 **RollingArtist** 节点
2. 连接至文本编码器
3. 调整参数以获得理想的艺术家组合
4. 输出示例：
   - Prompt：
     ```
     artist:Jane_Smith,(artist:John_Doe:0.8),(artist:FooBar:0.2)
     ```
     当权重为 `1.0` 时，直接输出 `artist:名称`，不包含括号与权重。
   - JSON：
     ```json
     {
       "artists": [
         {"name": "Jane_Smith", "weight": 1.0, "top": true},
         {"name": "John_Doe", "weight": 0.8, "top": false},
         {"name": "FooBar", "weight": 0.2, "top": false}
       ],
       "status": "OK"
     }
     ```
     `status` 为 `OK`（正常）或 `ALL_COMBINATIONS_TESTED`（Exact 模式已穷举）。

## 最佳实践

- 使用较小的`artist_count`（3-5）获得更聚焦的风格（默认3）
- 调整`weight_min`和`weight_max`的差值来控制艺术家影响力的变化范围
- 使用固定的`seed`值以便在多次生成中保持一致的艺术家组合
- 大型CSV（>10MB）建议将 `artist_top_ratio` 设为较小值（如 0.01-0.1）
  并保持 `csv_column=auto`；仓库自带的 `danbooru_art_full.csv`（42 万行）与
  `danbooru_character.csv`（24 万行）现在可以直接作为 `custom_csv_path` 使用
- 自定义CSV路径时，节点会检测文件是否变更，仅在变更时重载，避免重复解析

## 数据预处理
当 CSV 文件出现格式问题时，运行：
```bash
python modify_danbooru_csv.py            # 默认处理 danbooru_art_001.csv
python modify_danbooru_csv.py 其它.csv    # 也可指定文件
```
该脚本会把空格/短横线转为下划线、转义括号，并在写回前自动生成 `.bak` 备份。

## 升级说明

### 3.2.0 的变化

| 变化 | 说明 |
|------|------|
| 参数顺序调整 | 参数按“抽样 → 权重 → 前缀/种子 → 偏好 → 名单 → 去重 → CSV/路径”重排，`advanced` 参数折叠进“高级输入”。当前前端保存工作流时会同时写按名字的 `widgets_values_named` 与按位置的 `widgets_values`，读取时优先按名字恢复，因此不受影响；仅“只含位置数组”的旧版工作流需核对一次参数值 |
| 默认记录文件启动时清理 | 每次启动删除节点目录下的 `tested_combinations.csv` 与 `*_remaining.csv`；要跨会话保留历史请显式设置 `tested_csv_path` |
| 参数说明改用 `tooltip` | 界面不读 `description`；现在悬停与右侧「信息」面板都能看到参数说明（并随语言切换） |
| 新增中英文界面与节点帮助页 | 见上文「界面语言与节点帮助页」 |
| Python 接口不变 | `generate_artists` 的形参顺序与默认值保持不变，外部脚本按位置调用不受影响 |

### 相对 3.0.0 的行为变更

保持兼容的部分：

- 所有原有输入参数、默认值、输出类型与语义不变（`prompt` / `artists_json`）
- 提示词格式不变：权重为 `1.0` 时输出 `artist:名称`，否则输出 `(artist:名称:权重)`
- `tested_combinations.csv` 的行格式不变（`艺术家(| 分隔), 权重(, 分隔), prompt`），旧文件可继续使用
- 旧的 `*_remaining.csv` 会被直接复用；删除后按当前配置重建
- JSON 字段不变：`artists[{name, weight, top}]`、`status`

需要留意的变化：

| 变化 | 说明 |
|------|------|
| 多列 CSV 默认只取一列 | 旧版本会展平所有列（含表头/`count`/`url`）；如需旧行为请设 `csv_column=all` |
| 权重不再越界 | 旧版本为凑 `weight_total` 可能让某个权重超过 `weight_max`，现已被约束在范围内 |
| 同一 `seed` 的结果不同 | 权重分配与抽样实现改进后，随机数消费顺序变化；跨版本不再保证同一 `seed` 输出一致 |
| 失败时抛出异常 | CSV 不存在 / 解析不到艺术家 / 排除掉全部艺术家时，节点会报错而不是返回空字符串 |
| 清空 `custom_csv_path` 会回到默认 CSV | 旧版本会一直沿用上次加载的自定义列表，需要重启才恢复 |
| 日志 | 改用 `logging`（logger 名 `RollingArtist`），不再直接 `print` |
| 大幅性能提升 | 大 CSV 下抽样由 O(n²) 变为 O(k)，CSV 与去重记录不再重复解析 |

## 许可证
本项目基于 [MIT License](LICENSE) 发布，Copyright (c) 2026 StarAsh042。

允许自由使用、修改、分发与商用，仅需保留版权声明与许可声明。
