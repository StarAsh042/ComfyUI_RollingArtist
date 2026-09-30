"""ComfyUI RollingCharacter 节点。

从角色 CSV（默认 ``danbooru_character_001.csv``）中随机抽取角色，
按 ``角色标签,作品标签,core_tags`` 逐行输出（多个角色换行分隔），
同时输出结构化 JSON 与已测数量。

与 RollingArtist 的关系：抽样、去重、进度输出的做法一致；差异来自数据与产物：

- **不做权重分配**：角色标签直接输出，不套 ``(名称:权重)``，因此没有权重相关参数
- **名字取自 ``trigger`` 列的第 1 段**：该列经 34,416 行全量校验是恒定的
  「角色标签,作品标签」两段结构，第 1 段已转义、可直接使用，第 2 段是作品标签
- **每个角色一行**：``角色标签,作品标签,core_tags``（空的部分自动省略）
- **可按作品过滤**（``copyright_filter``），只从指定作品里抽角色
- **不做穷举**：角色是单词条，穷举意义不大，因此没有 ``mode`` 参数
"""

import json
import random
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from .ra_core.artists import (
    compute_top_count,
    get_repository,
    select_artists,
    split_names,
)
from .ra_core.characters import (
    CharacterRecord,
    build_character_prompt,
    build_payload,
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
from .ra_core.weights import normalize_dedup_mode

__all__ = ["RollingCharacter", "NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]


def _key_weights(count: int) -> List[float]:
    """去重键用的权重占位值：角色输出不带权重，键里统一按 1.0 计算。"""
    return [1.0] * count


class RollingCharacter:
    """RollingCharacter 节点类。

    功能特性：
    - 从角色 CSV 加载角色（默认 ``trigger`` 列，已转义、可直接使用）
    - 支持 Top 池优先、排除/强制包含名单、按作品过滤
    - 逐行输出 ``角色标签,作品标签,core_tags``（不带权重；外观标签可用开关关掉）
    - 结构化输出 characters_json，便于下游处理
    - 4 种去重模式与已测数量输出

    线程安全：
    - 角色 CSV 解析结果按文件签名缓存，多个实例共享
    - 去重记录存在角色专用数据库（``rollingcharacter.sqlite``）里，
      「判重 -> 生成 -> 记录」整段处于同一事务中，进程内与跨进程都不会重复
    - 记录带 ``kind=character`` 标记：即使被指向画师节点的数据库也不会互相冒充
    """

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Dict[str, Any]]:
        """定义节点的输入参数类型和界面显示。

        界面顺序即下方字典顺序，按「抽样 → Top 池 → 前缀/种子 → 抽样偏好 → 名单 →
        作品 → 去重 → 高级（CSV 与数据库路径）」归类；
        很少改动的 CSV / 路径标记为 ``advanced``，由前端折叠到“高级输入”。

        角色节点不做权重分配、不做穷举；输出格式固定为 ``角色标签,作品标签,core_tags``，
        只提供「拼入外观标签」这一个格式开关。
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
                               "候选数指“作品过滤之后”剩下的角色数"
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
                               "去重模式与重试次数会改变实际的抽样序列"
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
                               "不在 CSV 里的名字也能用，只是没有作品与外观标签信息"
                }),
                # ---------------- 作品 ----------------
                "copyright_filter": ("STRING", {
                    "default": "",
                    "tooltip": "只从这些作品里抽角色，英文逗号分隔（例如 touhou,vocaloid）；"
                               "留空表示不限。同时接受 copyright 列的原始写法 fate_(series) "
                               "与 trigger 里转义后的写法 fate_\\(series\\)，比较忽略大小写。"
                               "过滤后没有候选时会报错"
                }),
                "include_core_tags": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "是否把角色的固定外观标签（core_tags）拼进每一行。"
                               "开启时格式为「角色标签,作品标签,core_tags」；"
                               "关闭时格式为「角色标签,作品标签」"
                }),
                # ---------------- 去重 ----------------
                "dedup_mode": (["none", "full_prompt", "artist_set", "artist_list"], {
                    "default": "none",
                    "tooltip": "去重依据：none=只记录不判重；full_prompt=角色顺序完全一致；"
                               "artist_set=角色集合一致（忽略顺序）；"
                               "artist_list=排序后的名字一致"
                }),
                "max_attempts": ("INT", {
                    "default": 10,
                    "min": 1,
                    "max": 100,
                    "step": 1,
                    "tooltip": "去重模式下的最大重试次数（1-100）；重试后仍全部命中已测组合时"
                               "接受最后一次结果，并输出 WARNING 日志"
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
                               "更换该路径相当于重置去重进度"
                }),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "INT")
    RETURN_NAMES = ("prompt", "characters_json", "tested_count")
    FUNCTION = "generate_characters"
    CATEGORY = "RollingArtist"
    # 作为输出节点，即使输出未被下游使用也会执行
    OUTPUT_NODE = True
    DESCRIPTION = ("从角色 CSV 中随机抽取角色，按「角色标签,作品标签,core_tags」逐行输出"
                   "（多个角色换行分隔、不带权重），并输出结构化 JSON 与已测数量。")
    OUTPUT_TOOLTIPS = (
        "角色列表：每个角色一行，格式为「角色标签,作品标签,core_tags」，不带权重；"
        "关闭“拼入外观标签”后为「角色标签,作品标签」",
        "结构化结果 JSON（角色 / 作品 / 是否 Top / 热度 / 外观标签）",
        "当前数据库里已记录的角色组合数量",
    )

    def __init__(self) -> None:
        """初始化节点实例：准备角色仓库与状态容器。

        真正的加载发生在 ``generate_characters`` 中（按文件签名缓存），
        这里只做一次默认 CSV 的预热，且任何异常都不会影响节点注册。
        """
        self._repo = get_repository(DEFAULT_CHARACTER_CSV)
        self._lock = threading.RLock()
        self.characters: List[CharacterRecord] = []
        try:
            self.characters = load_records(self._repo, "", "trigger")
        except Exception as error:  # pragma: no cover - 初始化必须保持健壮
            LOGGER.error("[RollingCharacter] 初始化加载默认角色 CSV 失败: %s", error)

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def generate_characters(self, character_count: int, character_top_ratio: float,
                            character_top_count: int, seed: int,
                            use_top_priority: bool = True,
                            exclude_characters: str = "", force_include: str = "",
                            copyright_filter: str = "", include_core_tags: bool = True,
                            dedup_mode: str = "none",
                            max_attempts: int = 10,
                            custom_csv_path: str = "",
                            tested_db_path: str = "") -> Tuple[str, str, int]:
        """生成角色列表。

        形参顺序与界面参数顺序一致（ComfyUI 按关键字传参，顺序只影响外部脚本按位置调用）。
        读取的 CSV 列固定为 ``trigger``（「角色标签,作品标签」两段结构），不可配置。

        返回:
            ``(prompt, characters_json, tested_count)``
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

        # 2) 作品过滤 + 名单
        force_list = split_names(force_include)
        exclude_set = set(split_names(exclude_characters))
        allowed = filter_by_copyright(records, copyright_filter)
        if not allowed and not force_list:
            raise ValueError(
                f"[RollingCharacter] copyright_filter={copyright_filter!r} 过滤后没有可用角色"
                "（请检查作品名是否写对，或清空该参数）"
            )

        # 名字唯一（默认数据 34,416 行实测无重名），因此可以按名字直接取回记录
        record_map = {record.name: record for record in records}
        names = [records[index].name for index in allowed]
        top_count = compute_top_count(len(names), character_top_ratio)

        dedup = normalize_dedup_mode(dedup_mode)
        attempts = max(1, _as_int(max_attempts, 10))
        rng_seed = _as_int(seed, 0)
        top_priority = top_count if _as_bool(use_top_priority, True) else 0
        with_core = _as_bool(include_core_tags, True)

        # 3) 记录库：角色专用 SQLite（记录带 kind 标记，与画师互不干扰）
        db = get_db(tested_db_path, DEFAULT_CHARACTER_DB)

        # 4) 整段生成处于同一个事务内，进程内 / 跨进程都不会产生重复组合
        with db.generation_lock():
            last_result: Optional[Tuple[List[str], str]] = None

            for attempt in range(attempts):
                rng = random.Random(rng_seed + attempt)
                selected = select_artists(
                    rng, count, _as_int(character_top_count, 1), force_list,
                    names, top_priority, exclude_set,
                )
                if not selected:
                    continue
                if len(selected) < count:
                    LOGGER.warning(
                        "[RollingCharacter] 可用角色不足：请求 %d 个，实际选中 %d 个"
                        "（请检查 character_count、作品过滤、排除名单或 CSV 规模）",
                        count, len(selected),
                    )

                selected_records = [
                    record_map.get(name) or CharacterRecord(name, "", "", "", 0)
                    for name in selected
                ]
                prompt = build_character_prompt(selected_records, with_core)
                last_result = (selected, prompt)

                # 输出不带权重，但去重键是按「名字 + 权重」算的，因此统一用等长的 1.0 占位
                # （否则 full_prompt 会算出空键，导致所有组合撞成同一条记录）
                if dedup == "none" or not db.is_tested(
                        dedup, selected, _key_weights(len(selected)), KIND_CHARACTER):
                    break

            if last_result is None:
                raise ValueError("[RollingCharacter] 未选出任何角色，请检查 character_count 与名单")

            selected, prompt = last_result
            key_weights = _key_weights(len(selected))
            if dedup != "none" and db.is_tested(dedup, selected, key_weights, KIND_CHARACTER):
                LOGGER.warning(
                    "[RollingCharacter] 已试 %d 次均命中已测组合（dedup_mode=%s），返回重复结果:\n%s",
                    attempts, dedup, prompt,
                )
            if not db.record(selected, key_weights, prompt, KIND_CHARACTER):
                LOGGER.error(
                    "[RollingCharacter] 已测记录写入数据库失败(%s)，本次组合未落盘，"
                    "后续可能重复生成:\n%s",
                    db.path, prompt,
                )

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
            db.count(KIND_CHARACTER),
        )


# 注册节点类
NODE_CLASS_MAPPINGS = {"RollingCharacter": RollingCharacter}
# 设置节点显示名称
NODE_DISPLAY_NAME_MAPPINGS = {"RollingCharacter": "Rolling Character"}
