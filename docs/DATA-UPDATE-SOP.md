# 数据更新 SOP（重跑加工脚本的流程与校验）

本文件是「换 / 更新抽卡数据」的标准作业流程：从拿到新的 danbooru 导出文件，到加工、校验、提交上线的完整步骤。**只涉及仓库根目录的三个加工脚本与四个 CSV 数据文件，不碰 `ra_core/` 运行时代码。**

## 1. 什么时候需要走本流程

- danbooru 导出文件更新，想刷新节点可抽的画师 / 角色池
- 想调整热度阈值（`count` 下限）改变池子大小
- 修复数据质量问题（转义、脏行）后重新产出

日常使用**不需要**走本流程——仓库自带的 `danbooru_art_001.csv` / `danbooru_character_001.csv` 克隆即用。

## 2. 数据文件全景

| 文件 | 角色 | 大小量级 | 存储 | 随 Registry 包发布 |
|------|------|---------|------|------------------|
| `danbooru_art_full.csv` | 画师加工**输入**（danbooru 原始导出） | ~32MB / 42 万行 | Git LFS | 否（`.comfyignore` 排除） |
| `danbooru_character.csv` | 角色加工**输入**（danbooru 原始导出） | ~37MB / 24 万行 | Git LFS | 否（同上） |
| `danbooru_art_001.csv` | 画师**运行期数据**（节点实际读取） | ~1.5MB / 5 万行 | 普通 git | 是 |
| `danbooru_character_001.csv` | 角色**运行期数据**（节点实际读取） | ~6.5MB / 3.4 万行 | 普通 git | 是 |

**要点**：节点运行只需要两个 `*_001.csv`；两个 full CSV 只有重跑加工时才需要（`git lfs install` + `git lfs pull` 后才有实体）。

## 3. 标准流程

### S1. 准备输入（如需刷新源数据）

1. 从 danbooru 导出新的画师 / 角色 CSV，分别覆盖 `danbooru_art_full.csv` / `danbooru_character.csv`
2. LFS 纪律：覆盖后 `git add` 时确认走的是 LFS 过滤器（`.gitattributes` 已为这两个文件配置 `filter=lfs`，正常 `git add` 即可，**不要**绕过）
3. 列名自检：画师输入应为 `artist,trigger,count,url`；角色输入应为 `character,copyright,trigger,core_tags,count,solo_count,url`（加工脚本按列名定位，列序无所谓）

### S2. 跑加工脚本

在**仓库根目录**执行（两个入口共用 `danbooru_csv_tool.py` 的共享实现，转义规则只有一份）：

```bash
python modify_danbooru_art.py        # danbooru_art_full.csv -> danbooru_art_001.csv
python modify_danbooru_character.py  # danbooru_character.csv  -> danbooru_character_001.csv
```

常用参数（两个脚本一致）：

| 参数 | 默认 | 说明 |
|------|------|------|
| `-i / --input` | 各自 full 文件 | 输入 CSV |
| `-o / --output` | 各自 `_001` 文件 | 输出 CSV（写回前自动备份为 `<输出名>.bak`） |
| `-n / --min-count` | 30 | 只保留 `count` 不小于该值的行 |
| `--no-backup` | 关 | 不生成 `.bak`（日常别用） |

**阈值决策要留痕**：改 `-n` 会在 CHANGELOG 里记一句「画师池阈值 30 → 50，约 X 行」。脚本加工逻辑：按 count 降序保留、裁列、只对 trigger 列做转义（括号转义 + 逗号分段整理），画师脚本就地覆盖输出、角色脚本写到独立文件。

### S3. 自动化校验（必做）

```bash
python -m pytest -q            # 全量测试（含三个数据加工测试文件）
```

数据相关的三个测试文件：`tests/test_modify_art.py`、`tests/test_modify_character.py`（加工逻辑：转义 / 裁列 / 备份 / 原子写回）、`tests/test_no_double_import.py`（重复导入不产生重复记录）。**全绿才继续。**

### S4. 实物校验（必做， tests 覆盖不到的）

```bash
wc -l danbooru_art_001.csv danbooru_character_001.csv   # 行数合理性（与上一版对比，突变要能解释）
head -3 danbooru_character_001.csv                      # 表头应为 character,copyright,trigger,core_tags,count
```

抽查项：

- [ ] 输出表头与 §2 表格中的列一致
- [ ] `trigger` 列括号已转义（`ganyu_\(genshin_impact\)` 形态），`character` 列保持 danbooru 原始写法
- [ ] 行数变化能对上新阈值 / 新源数据（无异常截断——比如恰好卡在某个整数附近要确认不是写一半）
- [ ] 无空行、无列数不符的行（可用 `awk -F, 'NF!=5'` 对角色文件抽查）

### S5. 文档同步

- README 的数据口径段（行数、作品数等引用具体数字的地方）同步更新
- `CHANGELOG.md` 「未发布」段记一条：更新了什么数据、阈值有无变化、行数前后对比

### S6. 提交

```bash
git add danbooru_art_001.csv danbooru_character_001.csv   # 运行期数据，普通 git
git add danbooru_art_full.csv danbooru_character.csv      # 若刷新了源数据，走 LFS
```

提交信息建议带 `data:` 前缀（如 `data: 刷新 danbooru 导出（2026-11），阈值维持 30`）。合并后**不需要**为纯数据更新单独发版——数据文件随下一个版本发布进 Registry 包即可；但如果数据变化影响了 README 口径或测试预期，按正常 PR 流程走。

## 4. 回滚

- 加工脚本写回前自动备份：`danbooru_art_001.csv.bak` / `danbooru_character_001.csv.bak`，改坏了直接拷回
- 已提交的改动用 git 回滚：`git checkout <上一个提交> -- danbooru_*_001.csv`
- LFS 文件回滚同理，注意 `git lfs checkout` 重新拉取实体

## 5. 已知取舍（改数据前必读）

- **`character` 列不做转义**，而它正是角色节点默认读取的列。danbooru 角色名约 45% 带括号，进入提示词后会被 A1111 / ComfyUI 的权重解析器当成分组语法。这是有意取舍（保原始写法便于对照源数据），需要精确匹配的场景自行加工
- **`trigger` 列是「角色 + 作品」多标签串**（`hakurei reimu, touhou`），不当单个名字用
- **转义规则只实现一份**（`danbooru_csv_tool.py` 的 `escape_paren` / `transform_text`），历史上从「处理整行」改成「只处理 trigger 列」过一次；如再改，先改共享模块，两个入口自动跟随，别各改一份

## 6. 一页纸清单

```
□ S1 源数据就位（列名自检通过，LFS add 正常）
□ S2 两个加工脚本跑完（阈值如变更先记原因）
□ S3 pytest 全绿
□ S4 行数 / 表头 / 转义 / 无截断 四项抽查通过
□ S5 README 口径 + CHANGELOG 已同步
□ S6 数据文件与源文件分别 add 后提交
```
