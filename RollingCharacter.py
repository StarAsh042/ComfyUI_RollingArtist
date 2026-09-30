"""ComfyUI RollingCharacter 节点。

从角色 CSV（默认 ``danbooru_character_001.csv``）中随机抽取角色，
按 ``角色标签,作品标签,core_tags`` 逐行输出（多个角色用「逗号 + 换行」分隔），
同时输出结构化 JSON 与已测数量。

与 RollingArtist 的关系：抽样与进度输出的做法一致；差异来自数据与产物：

- **不做权重分配**：角色标签直接输出，不套 ``(名称:权重)``，因此没有权重相关参数
- **名字取自 ``trigger`` 列的第 1 段**：该列经 34,416 行全量校验是恒定的
  「角色标签,作品标签」两段结构，第 1 段已转义、可直接使用，第 2 段是作品标签
- **每个角色一行**：``角色标签,作品标签,core_tags``（空的部分自动省略）；
  多个角色之间用「逗号 + 换行」分隔（行尾补逗号，拼成一行时标签不会粘在一起）
- **可按作品过滤**：``copyright_pick`` 下拉列出**全部作品**，可打字筛选
- **不重复靠一个简单的内存窗口**：最近 10 条输出记录里出现过的角色名会被回避
  （见 :data:`_RECENT_RECORDS`），没有去重模式参数
- **不做穷举**：角色是单词条，穷举意义不大，因此没有 ``mode`` 参数
"""

import json
import random
import threading
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Sequence, Set, Tuple

from .ra_core.artists import (
    compute_top_count,
    get_repository,
    select_artists,
    split_names,
)
from .ra_core.characters import (
    COPYRIGHT_ANY,
    CharacterRecord,
    build_character_prompt,
    build_payload,
    copyright_options,
    filter_by_copyright,
    load_records,
)
from .ra_core.constants import (
    DEFAULT_CHARACTER_CSV,
    DEFAULT_CHARACTER_DB,
    KIND_CHARACTER,
    LOGGER,
)
from .ra_core.db import get_db
from .ra_core.params import as_bool as _as_bool
from .ra_core.params import as_int as _as_int
from .ra_core.params import as_text as _text

__all__ = ["RollingCharacter", "NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]

# 「最近多少条输出记录内角色不重复」：这里的一条 = 一次生成（可能含多个角色）。
# 窗口放在节点实例的内存里，因此重启 ComfyUI 会清零，多个实例也各算各的。
_RECENT_RECORDS = 10


def _key_weights(count: int) -> List[float]:
    """记录用的权重占位值：角色输出不带权重，键里统一按 1.0 计算。"""
    return [1.0] * count


# 作品下拉菜单的选项：注册节点时解析一次默认 CSV（3.4 万行，约 0.5 秒），
# 之后所有实例共用；用户填的 ``custom_csv_path`` 拿不到，因此不参与构建
_COPYRIGHT_MENU: Optional[List[str]] = None


def _copyright_menu() -> List[str]:
    """作品下拉菜单选项：``[COPYRIGHT_ANY, 全部作品名...]``（按 count 总和降序）。

    默认 CSV 缺失或解析失败时退化为只有「不限」一项，绝不让菜单构建失败影响节点注册。
    """
    global _COPYRIGHT_MENU
    if _COPYRIGHT_MENU is None:
        options: List[str] = [COPYRIGHT_ANY]
        try:
            repository = get_repository(DEFAULT_CHARACTER_CSV)
            options.extend(copyright_options(load_records(repository, "", "trigger")))
        except Exception as error:  # pragma: no cover - 菜单构建必须保持健壮
            LOGGER.error("[RollingCharacter] 构建作品下拉菜单失败: %s", error)
        if len(options) == 1:
            LOGGER.warning(
                "[RollingCharacter] 作品下拉菜单只有“不限”一项（默认 CSV 缺失或没有读到作品列），"
                "请检查 danbooru_character_001.csv 是否就位"
            )
        _COPYRIGHT_MENU = options
    return _COPYRIGHT_MENU


class RollingCharacter:
    """RollingCharacter 节点类。

    功能特性：
    - 从角色 CSV 加载角色（默认 ``trigger`` 列，已转义、可直接使用）
    - 支持 Top 池优先、排除/强制包含名单、按作品筛选（下拉列出全部作品）
    - 逐行输出 ``角色标签,作品标签,core_tags``（不带权重；角色描述可开关）；
      多个角色之间用「逗号 + 换行」分隔
    - 结构化输出 characters_json，便于下游处理
    - 最近 10 条输出记录内不重复（内存窗口，无去重模式参数）

    线程安全：
    - 角色 CSV 解析结果按文件签名缓存，多个实例共享
    - 「选角色 -> 记录」整段处于数据库事务中；不重复判定用的是节点实例自己的内存窗口
    - 记录带 ``kind=character`` 标记：即使被指向画师节点的数据库也不会互相冒充
    """

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Dict[str, Any]]:
        """定义节点的输入参数类型和界面显示。

        界面顺序即下方字典顺序，按「抽样 → Top 池 → 种子 → 抽样偏好 → 名单 →
        作品 → 输出内容 → 高级（CSV 与数据库路径）」归类；
        很少改动的 CSV / 路径标记为 ``advanced``，由前端折叠到“高级输入”。

        角色节点不做权重分配、不做穷举；输出格式固定为 ``角色标签,作品标签,core_tags``，
        只提供「开启角色描述」这一个内容开关。
        """
        return {
            "required": {
                # ---------------- 抽样：抽几个角色、从哪里抽 ----------------
                "character_count": ("INT", {
                    "default": 1,
                    "min": 1,
                    "max": 10,
                    "step": 1,
                    "display": "slider",
                    "tooltip": "每次生成的角色数量（1-10），每个角色输出一行。"
                               "角色是单词条，没有穷举模式"
                }),
                "character_top_ratio": ("FLOAT", {
                    "default": 0.01,
                    "min": 0.01,
                    "max": 1.0,
                    "step": 0.01,
                    "display": "slider",
                    "tooltip": "人气池占比（0.01-1.0）：按 CSV 行序取前 ceil(候选数×比例) 个"
                               "（至少 1 个）作为 Top 池，行序越靠前的角色作品数越多。"
                               "候选数指“作品筛选之后”剩下的角色数"
                }),
                "character_top_count": ("INT", {
                    "default": 1,
                    "min": 1,
                    "max": 10,
                    "step": 1,
                    "display": "slider",
                    "tooltip": "每次至少抽取几个人气池角色（1-10）；"
                               "仅在“Top 优先抽样”开启且人气池非空时生效"
                }),
                "seed": ("INT", {
                    "default": 1234,
                    "min": 0,
                    "max": 4294967295,
                    "tooltip": "随机种子。同一种子在同一组参数下结果可复现；"
                               "最近 10 条输出记录的回避窗口也会影响实际结果"
                }),
            },
            "optional": {
                # ---------------- 抽样偏好 ----------------
                "use_top_priority": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "开启时优先从人气池抽角色，并保证含 character_top_count 个人气角色；"
                               "关闭时从完整候选列表均匀抽样"
                }),
                # ---------------- 名单 ----------------
                "exclude_characters": ("STRING", {
                    "default": "",
                    "tooltip": "要排除的角色名，英文逗号分隔；被排除的名字不会出现在结果中"
                }),
                "force_include": ("STRING", {
                    "default": "",
                    "tooltip": "必须出现的角色名，英文逗号分隔；优先级高于“排除角色”，"
                               "数量达到角色数量时直接从中随机抽取。"
                               "不在 CSV 里的名字也能用，只是没有作品与角色描述信息"
                }),
                # ---------------- 作品 ----------------
                "copyright_pick": (_copyright_menu(), {
                    "default": COPYRIGHT_ANY,
                    "tooltip": "从下拉里挑一个作品。下拉列出 CSV 里的全部作品"
                               "（按作品热度降序：该作品下所有角色的 count 之和），"
                               "并且可以直接打字筛选、忽略大小写（输入 a 只留 a 开头的，"
                               "继续输入 ab 会进一步收窄）。“(不限)”表示不筛选。"
                               "同时接受 copyright 列的原始写法 fate_(series) 与 trigger 里"
                               "转义后的写法 fate_\\(series\\)。注意：下拉项来自默认 CSV，"
                               "换“自定义 CSV 路径”时下拉不会跟着变"
                }),
                # ---------------- 输出内容 ----------------
                "describe_character": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "开启时在每行末尾拼上角色的固定外观标签（core_tags，即角色描述），"
                               "格式为「角色标签,作品标签,角色描述」；"
                               "关闭时只输出「角色标签,作品标签」"
                }),
                # ---------------- 高级：CSV 与数据库（界面默认折叠） ----------------
                "custom_csv_path": ("STRING", {
                    "default": "",
                    "advanced": True,
                    "tooltip": "自定义角色 CSV 的路径；"
                               "留空使用节点目录下的 danbooru_character_001.csv"
                }),
                "tested_db_path": ("STRING", {
                    "default": "",
                    "advanced": True,
                    "tooltip": "记录数据库（SQLite）路径；留空使用节点目录下的 "
                               "rollingcharacter.sqlite（与画师节点分开）。"
                               "这里只存历史输出记录，更换该路径不会重置“最近 10 条不重复”的窗口"
                               "（那个窗口在内存里，重启即清零）"
                }),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("prompt", "characters_json", "tested_count")
    FUNCTION = "generate_characters"
    CATEGORY = "RollingArtist"
    # 作为输出节点，即使输出未被下游使用也会执行
    OUTPUT_NODE = True
    DESCRIPTION = ("从角色 CSV 中随机抽取角色，按「角色标签,作品标签,角色描述」逐行输出"
                   "（多个角色用「逗号+换行」分隔、不带权重），并输出结构化 JSON 与已测数量。")
    OUTPUT_TOOLTIPS = (
        "角色列表：每个角色一行，格式为「角色标签,作品标签,角色描述」，不带权重；"
        "关闭“开启角色描述”后为「角色标签,作品标签」",
        "结构化结果 JSON（角色 / 作品 / 是否 Top / 热度 / 角色描述）",
        "当前数据库里已记录的角色组合数量（字符串形式，可直接接到文本节点查看）",
    )

    def __init__(self) -> None:
        """初始化节点实例：准备角色仓库、状态容器与“最近记录”窗口。

        真正的加载发生在 ``generate_characters`` 中（按文件签名缓存），
        这里只做一次默认 CSV 的预热，且任何异常都不会影响节点注册。
        """
        self._repo = get_repository(DEFAULT_CHARACTER_CSV)
        self._lock = threading.RLock()
        self.characters: List[CharacterRecord] = []
        # 最近若干条输出记录（每条 = 一次生成的角色名），用于回避重复
        self._recent: Deque[Tuple[str, ...]] = deque(maxlen=_RECENT_RECORDS)
        try:
            self.characters = load_records(self._repo, "", "trigger")
        except Exception as error:  # pragma: no cover - 初始化必须保持健壮
            LOGGER.error("[RollingCharacter] 初始化加载默认角色 CSV 失败: %s", error)

    # ------------------------------------------------------------------
    # 最近记录窗口
    # ------------------------------------------------------------------
    def recent_names(self) -> Set[str]:
        """最近 :data:`_RECENT_RECORDS` 条输出记录里出现过的角色名（并集）。"""
        with self._lock:
            names: Set[str] = set()
            for record_names in self._recent:
                names.update(record_names)
            return names

    def _remember(self, names: Sequence[str]) -> None:
        """把本次输出的角色名记进窗口。"""
        with self._lock:
            self._recent.append(tuple(names))

    def reset_recent(self) -> None:
        """清空“最近记录”窗口（手动重置重复回避时使用）。"""
        with self._lock:
            self._recent.clear()

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def generate_characters(self, character_count: int, character_top_ratio: float,
                            character_top_count: int, seed: int,
                            use_top_priority: bool = True,
                            exclude_characters: str = "", force_include: str = "",
                            copyright_pick: str = COPYRIGHT_ANY,
                            describe_character: bool = True,
                            custom_csv_path: str = "",
                            tested_db_path: str = "") -> Tuple[str, str, str]:
        """生成角色列表。

        形参顺序与界面参数顺序一致（ComfyUI 按关键字传参，顺序只影响外部脚本按位置调用）。
        读取的 CSV 列固定为 ``trigger``（「角色标签,作品标签」两段结构），不可配置。

        不重复的规则很简单：把最近 :data:`_RECENT_RECORDS` 条输出记录里出现过的角色名
        从候选里排除；只有当窗口把候选全部吃掉时才退回「允许重复」并输出 WARNING。

        返回:
            ``(prompt, characters_json, tested_count)``；``tested_count`` 是字符串，
            方便直接接到文本节点查看
        """
        count = _as_int(character_count, 1)
        if count < 1:
            raise ValueError(f"[RollingCharacter] character_count 必须 >= 1，当前值: {character_count!r}")

        # 1) 加载角色记录（内容未变化时直接命中缓存，不重复解析 CSV）
        #    列固定为 trigger：该列恒为「角色标签,作品标签」两段结构
        records = load_records(self._repo, custom_csv_path, "trigger")
        if not records:
            raise ValueError(
                "[RollingCharacter] 未能从 CSV 加载角色列表，请检查文件是否存在、"
                f"编码是否为 UTF-8、是否含有 trigger 列: "
                f"{self._repo.resolve_path(_text(custom_csv_path))}"
                "（若本节点是从旧工作流载入的，请检查“自定义 CSV 路径”是否已清空）"
            )
        with self._lock:
            self.characters = records

        # 2) 作品筛选（下拉单选，“(不限)”表示不筛选）+ 名单
        force_list = split_names(force_include)
        exclude_set = set(split_names(exclude_characters))
        pick = _text(copyright_pick).strip()
        allowed = filter_by_copyright(records, "" if pick == COPYRIGHT_ANY else pick)
        if not allowed and not force_list:
            raise ValueError(
                f"[RollingCharacter] 作品选择={copyright_pick!r} 筛选后没有可用角色"
                "（请改成“(不限)”或换一个作品）"
            )

        # 名字唯一（默认数据 34,416 行实测无重名），因此可以按名字直接取回记录
        record_map = {record.name: record for record in records}
        names = [records[index].name for index in allowed]
        top_count = compute_top_count(len(names), character_top_ratio)

        rng_seed = _as_int(seed, 0)
        top_priority = top_count if _as_bool(use_top_priority, True) else 0
        with_core = _as_bool(describe_character, True)

        # 3) 回避最近 _RECENT_RECORDS 条输出记录里出现过的角色
        recent = self.recent_names()
        blocked = exclude_set | recent
        rest = [name for name in names if name not in exclude_set]
        if recent and rest and not [name for name in rest if name not in recent] \
                and not force_list:
            # 窗口把候选全部吃掉（可用角色本来就少）：退回允许重复，并明确告警
            LOGGER.warning(
                "[RollingCharacter] 最近 %d 条输出记录已覆盖全部 %d 个候选角色，"
                "本轮不再回避重复（可用角色太少时属于正常现象）",
                _RECENT_RECORDS, len(rest),
            )
            blocked = exclude_set

        # 4) 记录库：角色专用 SQLite（记录带 kind 标记，与画师互不干扰）
        db = get_db(tested_db_path, DEFAULT_CHARACTER_DB)

        # 5) 「选角色 -> 记录」处于同一个事务内，进程内 / 跨进程都不会重复落盘
        with db.generation_lock():
            rng = random.Random(rng_seed)
            selected = select_artists(
                rng, count, _as_int(character_top_count, 1), force_list,
                names, top_priority, blocked,
            )
            if not selected:
                raise ValueError(
                    "[RollingCharacter] 未选出任何角色，请检查 character_count、"
                    "排除名单与作品选择"
                )
            if len(selected) < count:
                LOGGER.warning(
                    "[RollingCharacter] 可用角色不足：请求 %d 个，实际选中 %d 个"
                    "（请检查 character_count、作品选择、排除名单或 CSV 规模）",
                    count, len(selected),
                )

            selected_records = [
                record_map.get(name) or CharacterRecord(name, "", "", "", 0)
                for name in selected
            ]
            prompt = build_character_prompt(selected_records, with_core)

            # 记录里的权重列统一用等长的 1.0 占位（角色不带权重）
            if not db.record(selected, _key_weights(len(selected)), prompt, KIND_CHARACTER):
                LOGGER.error(
                    "[RollingCharacter] 记录写入数据库失败(%s)，本次组合未落盘:\n%s",
                    db.path, prompt,
                )

        # 记进“最近记录”窗口（放在事务之后：只有真正输出的组合才占用窗口）
        self._remember(selected)

        selected_records = [
            record_map.get(name) or CharacterRecord(name, "", "", "", 0)
            for name in selected
        ]
        # 人气池覆盖全部候选时，所有人都是 Top，无需构建集合
        top_names: Optional[Set[str]] = None
        if top_count < len(names):
            top_names = set(names[:top_count])

        payload = build_payload(selected_records, top_names)
        return (
            prompt,
            json.dumps(payload, ensure_ascii=False),
            str(db.count(KIND_CHARACTER)),
        )


# 注册节点类
NODE_CLASS_MAPPINGS = {"RollingCharacter": RollingCharacter}
# 设置节点显示名称
NODE_DISPLAY_NAME_MAPPINGS = {"RollingCharacter": "Rolling Character"}
