"""ComfyUI RollingArtist 节点。

从艺术家 CSV 中随机抽取艺术家并分配随机权重，生成可直接使用的提示词，
同时输出结构化 JSON（艺术家 / 权重 / 是否 Top / 状态）。

核心逻辑已拆分到 ``ra_core`` 子包，本文件仅保留：
- 节点接口定义（INPUT_TYPES / RETURN_TYPES 等）
- 单次生成的编排（参数规范化、锁与记录、输出组装）
"""

import json
import random
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from .ra_core.artists import (
    ArtistRepository,
    compute_top_count,
    get_artist_repository,
    select_artists,
    split_names,
)
from .ra_core.constants import (
    EXACT_COMBOS_WARN,
    KIND_ARTIST,
    LOGGER,
    WEIGHT_STEP,
)
from .ra_core.db import RollingArtistDB, get_db
from .ra_core.exact import remaining_count, take_exact
from .ra_core.params import as_bool, as_float, as_int, as_text
from .ra_core.weights import (
    build_prompt,
    generate_weights,
    normalize_dedup_mode,
    normalize_generation_mode,
    weight_grid,
)

# 兼容旧版本从本模块导入常量的用法
__all__ = [
    "RollingArtist",
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEIGHT_STEP",
    "EXACT_COMBOS_WARN",
]

_STATUS_OK = "OK"
_STATUS_ALL_TESTED = "ALL_COMBINATIONS_TESTED"


# 参数容错转换与 RollingCharacter 共用一份实现（见 ra_core/params.py）
_as_text = as_text
_as_int = as_int
_as_float = as_float
_as_bool = as_bool
_text = _as_text


# Exact 模式“忽略 dedup_mode / weight_total”的提示按配置去重，避免批量执行时刷屏
_exact_notices: Set[str] = set()


def _resolve_exact_mode(mode: Any, artist_count: int, force_list: List[str]) -> bool:
    """判断本次是否进入穷举模式。

    - ``auto``：保持 3.2.x 行为（艺术家数量为 1 且未填强制包含时穷举）
    - ``random``：永不穷举
    - ``exact``：强制穷举，条件不满足时**直接报错**（不静默降级，避免用户以为在穷举）
    """
    resolved = normalize_generation_mode(mode)
    if resolved == "random":
        return False
    if resolved == "exact":
        if artist_count != 1:
            raise ValueError(
                "[RollingArtist] mode=exact（穷举）要求 artist_count = 1，"
                f"当前 artist_count = {artist_count}。穷举池按「单个艺术家 × 权重档位」枚举，"
                "多艺术家组合数量会爆炸，因此不支持。"
            )
        if force_list:
            raise ValueError(
                "[RollingArtist] mode=exact（穷举）与 force_include（强制包含）不能同时使用："
                "强制包含会固定组合，无法穷举。请清空强制包含，或把模式改回 auto/random。"
            )
        return True
    return artist_count == 1 and not force_list


def _notify_exact_mode(mode: str, raw_dedup_mode: Any, weight_total: float) -> None:
    """提示 Exact 模式固定按 full_prompt 去重且不使用 weight_total（同配置只提示一次）。"""
    key = f"{mode}|{weight_total!r}"
    if key in _exact_notices:
        return
    _exact_notices.add(key)
    if mode != "full_prompt" or weight_total != 0.0:
        LOGGER.info(
            "[RollingArtist] artist_count=1 进入 Exact 穷举模式：dedup_mode=%r 与 weight_total=%r "
            "不生效（固定按 full_prompt 去重，权重按 weight_min~weight_max 网格枚举）",
            raw_dedup_mode, weight_total,
        )


class RollingArtist:
    """RollingArtist 节点类。

    功能特性：
    - 支持从 CSV 加载艺术家列表，自动识别表头与列，可自定义路径
    - 支持 Top 艺术家优先池、排除名单、强制包含名单
    - 权重范围与总和双约束（步长 0.1，总和精确且不会越界）
    - 支持 4 种去重模式，以及 artist_count=1 时的 Exact 穷举模式
    - 结构化输出 artists_json，便于下游处理

    线程安全：
    - 艺术家 CSV 解析结果按文件签名缓存，多个实例共享
    - 去重记录与 Exact 组合池都存在 SQLite（``tested_db_path``）里，
      “判重 -> 生成 -> 记录”整段运行在同一个 ``BEGIN IMMEDIATE`` 事务中：
      进程内（可重入锁）与跨进程（SQLite 文件锁）都不会生成重复组合
    """

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Dict[str, Any]]:
        """定义节点的输入参数类型和界面显示。

        界面顺序即下方字典顺序，按“抽样 → Top 池 → 权重 → 前缀/种子 → 抽样偏好 → 名单 →
        去重 → 高级（CSV 数据源、数据库与旧记录路径）”归类；很少改动的 CSV / 路径标记为
        ``advanced``，由前端折叠到“高级输入”。

        关于重排的兼容性：当前前端保存工作流时同时写 ``widgets_values_named``（按名字）与
        ``widgets_values``（按位置），读取时优先按名字恢复（settingStore 的 LiteGraph
        ``serialize`` / ``getNamedValues``），因此本版本前端存出的工作流重排后依然正确；
        只有“仅含位置数组”的旧版工作流会整体错位，必要时可用官方 ``io.NodeReplace``
        （``old_widget_ids`` 把位置索引映射回输入 id）在换节点类名时兜底。

        注意：``generate_artists`` 的形参顺序是给外部脚本按位置调用的接口，保持不变；
        ComfyUI 本身按关键字传参，与这里的顺序无关。

        参数说明使用 ``tooltip`` 键（V1 的 ``description`` 界面不读；V3 schema 的通用输入
        参数即 ``tooltip`` / ``advanced`` / ``display_name``）。这里是默认文案（中文）；
        中英文切换由 ``locales/<语言>/nodeDefs.json`` 提供，
        节点帮助页（信息面板）由 ``web/docs/RollingArtist/<语言>.md`` 提供。
        """
        return {
            "required": {
                # ---------------- 抽样：抽几个人、从哪里抽 ----------------
                "artist_count": ("INT", {
                    "default": 3,
                    "min": 1,
                    "max": 10,
                    "step": 1,
                    "display": "slider",
                    "tooltip": "每次生成的艺术家数量（1-10）。在“模式=自动”下，设为 1 且“强制包含”为空"
                               "会进入穷举模式：按 (艺术家, 权重) 组合逐个不重复输出，"
                               "穷举完返回 ALL_COMBINATIONS_TESTED。想只抽 1 个而不穷举请把“模式”改为普通"
                }),
                "artist_top_ratio": ("FLOAT", {
                    "default": 0.01,
                    "min": 0.01,
                    "max": 1.0,
                    "step": 0.01,
                    "display": "slider",
                    "tooltip": "Top 池占比（0.01-1.0）：按 CSV 行序取前 ceil(总数×比例) 个（至少 1 个）作为 Top 池，"
                               "行序越靠前的艺术家通常作品越多"
                }),
                "artist_top_count": ("INT", {
                    "default": 1,
                    "min": 1,
                    "max": 10,
                    "step": 1,
                    "display": "slider",
                    "tooltip": "每次至少抽取几个 Top 池艺术家（1-10）；"
                               "仅在“Top 优先抽样”开启且 Top 池非空时生效"
                }),
                # ---------------- 权重：范围与总和 ----------------
                "weight_min": ("FLOAT", {
                    "default": 0.2,
                    "min": 0.1,
                    "max": 2.0,
                    "step": 0.1,
                    "display": "slider",
                    "tooltip": "单个艺术家的权重下限（步长 0.1）；与上限写反时自动交换，负数会被夹到 0"
                }),
                "weight_max": ("FLOAT", {
                    "default": 1.0,
                    "min": 0.1,
                    "max": 2.0,
                    "step": 0.1,
                    "display": "slider",
                    "tooltip": "单个艺术家的权重上限（步长 0.1）；每个权重都落在上限与下限之间"
                }),
                "weight_total": ("FLOAT", {
                    "default": 2.0,
                    "min": 0.0,
                    "max": 20.0,
                    "step": 0.5,
                    "display": "slider",
                    "tooltip": "所有权重之和（步长 0.5），按总和精确分配，超出可行区间时夹到边界；"
                               "artist_count=1 的 Exact 模式不生效（改为按 0.1 网格枚举单个权重）"
                }),
                "weight_curve": (["flat", "dominant", "ramp"], {
                    "default": "flat",
                    "tooltip": "权重分配曲线，只影响“谁分得多”，不影响上下限与总和。"
                               "flat=平坦（现状，各位权重接近，风格平均混合）；"
                               "dominant=主次分明（随机一位当主风格，权重明显更高）；"
                               "ramp=阶梯（按随机名次递减）。穷举模式按权重网格枚举，本项不生效"
                }),
                # ---------------- 输出形式与随机性 ----------------
                "artists_prefix": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "开启时输出 artist:名称（例如 (artist:xxx:0.8)），关闭时只输出名称本身"
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
                    "tooltip": "开启时优先从 Top 池抽人，并保证含 artist_top_count 个 Top 艺术家；"
                               "关闭时从完整 CSV 均匀抽样"
                }),
                "sort_by_weight": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "开启时按权重从高到低重排提示词里的艺术家顺序；"
                               "顺序会影响 full_prompt 去重键"
                }),
                # ---------------- 名单 ----------------
                "exclude_artists": ("STRING", {
                    "default": "",
                    "tooltip": "要排除的艺术家名，英文逗号分隔（例如 a,b,c）；被排除的名字不会出现在结果中"
                }),
                "force_include": ("STRING", {
                    "default": "",
                    "tooltip": "必须出现的艺术家名，英文逗号分隔；优先级高于“排除艺术家”，"
                               "数量达到 artist_count 时直接从中随机抽取"
                }),
                # ---------------- 生成模式与去重 ----------------
                "mode": (["auto", "random", "exact"], {
                    "default": "auto",
                    "tooltip": "生成模式。auto=保持旧行为（艺术家数量=1 且未填“强制包含”时进入穷举）；"
                               "random=永远随机抽取；exact=强制穷举（要求艺术家数量=1 且未填“强制包含”，"
                               "不满足时直接报错）。穷举模式下 dedup_mode 与 weight_total 不生效"
                }),
                "dedup_mode": (["none", "full_prompt", "artist_set", "artist_list"], {
                    "default": "none",
                    "tooltip": "去重依据：none=只记录不判重；full_prompt=艺术家顺序与权重完全一致；"
                               "artist_set=艺术家集合一致（忽略顺序与权重）；"
                               "artist_list=排序后的名字一致（忽略权重）。Exact 模式固定按 full_prompt 判定"
                }),
                "max_attempts": ("INT", {
                    "default": 10,
                    "min": 1,
                    "max": 100,
                    "step": 1,
                    "tooltip": "去重模式下的最大重试次数（1-100）；重试后仍全部命中已测组合时接受最后一次结果，"
                               "并输出 WARNING 日志"
                }),
                # ---------------- 高级：CSV 与数据库（界面默认折叠） ----------------
                "custom_csv_path": ("STRING", {
                    "default": "",
                    "advanced": True,
                    "tooltip": "自定义艺术家 CSV 的路径；留空使用节点目录下的 danbooru_art_001.csv"
                }),
                "tested_db_path": ("STRING", {
                    "default": "",
                    "advanced": True,
                    "tooltip": "记录数据库（SQLite）路径；留空使用节点目录下的 rollingartist.sqlite。"
                               "已测记录与穷举组合池都存这里，更换该路径相当于重置去重进度。"
                               "默认路径的数据库每次启动会被清理，要跨会话保留请显式指定"
                }),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "INT", "INT")
    RETURN_NAMES = ("prompt", "artists_json", "tested_count", "remaining_count")
    FUNCTION = "generate_artists"
    CATEGORY = "RollingArtist"
    # 与原实现保持一致：作为输出节点，即使输出未被下游使用也会执行
    OUTPUT_NODE = True
    DESCRIPTION = "从艺术家 CSV 中随机抽取艺术家并分配随机权重，生成带权重的提示词，并输出结构化 JSON。"
    OUTPUT_TOOLTIPS = (
        "生成的提示词",
        "结构化结果 JSON（艺术家 / 权重 / 是否 Top / 状态）",
        "当前数据库里已记录的组合数量",
        "穷举模式下组合池里还剩多少条组合；非穷举模式（含总量过大而降级采样时）恒为 0",
    )

    def __init__(self) -> None:
        """初始化节点实例：准备好仓库与状态容器。

        注意：真正的加载发生在 ``generate_artists`` 中（按文件签名缓存），
        这里只做一次默认 CSV 的预热，且任何异常都不会影响节点注册。
        """
        self._repo: ArtistRepository = get_artist_repository()
        self._lock = threading.RLock()
        self.artists: List[str] = []
        self.top_count = 0
        try:
            # 文件签名缓存由 ArtistRepository 统一维护，实例不再保存签名
            self.artists, _ = self._repo.load("", "trigger")
            self.top_count = compute_top_count(len(self.artists), 0.01)
        except Exception as error:  # pragma: no cover - 初始化必须保持健壮
            LOGGER.error("[RollingArtist] 初始化加载默认 CSV 失败: %s", error)

    # ------------------------------------------------------------------
    # 兼容接口（旧版本曾暴露的方法，保留以避免破坏外部调用）
    # ------------------------------------------------------------------
    @property
    def top_pool(self) -> List[str]:
        """Top 艺术家列表（按当前 Top 数量惰性切片）。"""
        with self._lock:
            return self.artists[:self.top_count]

    @property
    def non_top_pool(self) -> List[str]:
        """非 Top 艺术家列表（按当前 Top 数量惰性切片）。"""
        with self._lock:
            return self.artists[self.top_count:]

    def load_artists(self, csv_path: Optional[str] = None, column: str = "trigger") -> List[str]:
        """加载（并按文件修改时间缓存）艺术家列表。"""
        artists, _ = self._repo.load(csv_path or "", column)
        if artists:
            with self._lock:
                self.artists = artists
        return artists

    def update_top_pool(self, top_ratio: float) -> int:
        """按比例刷新 Top 艺术家数量，返回该数量。"""
        with self._lock:
            self.top_count = compute_top_count(len(self.artists), top_ratio)
            return self.top_count

    def generate_fixed_weights(self, count: int, weight_min: float, weight_max: float,
                               weight_total: float, rng: random.Random) -> List[float]:
        """（兼容保留）生成符合约束的权重列表。"""
        return generate_weights(count, weight_min, weight_max, weight_total, rng)

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def generate_artists(self, artist_count: int, artist_top_count: int,
                         artist_top_ratio: float, artists_prefix: bool,
                         weight_min: float, weight_max: float,
                         weight_total: float, seed: int,
                         custom_csv_path: str = "", exclude_artists: str = "",
                         sort_by_weight: bool = True, use_top_priority: bool = True,
                         force_include: str = "", dedup_mode: str = "none",
                         max_attempts: int = 10,
                         mode: str = "auto",
                         weight_curve: str = "flat",
                         tested_db_path: str = "") -> Tuple[str, str, int, int]:
        """生成艺术家提示词。

        形参顺序是给外部脚本按位置调用的接口（与 ``INPUT_TYPES`` 的界面顺序无关，
        ComfyUI 按关键字传参）。

        参数:
            mode: 生成模式。``auto`` = 艺术家数量为 1 且未填强制包含时穷举（旧行为）；
                  ``random`` = 永不穷举；``exact`` = 强制穷举（条件不满足时抛错）。
                  判定逻辑见 ``_resolve_exact_mode``
            tested_db_path: 记录数据库路径（空 = 节点目录下的 rollingartist.sqlite）

        读取的 CSV 列固定为 ``trigger``（提示词可直接使用的列：空格与括号已转义），不可配置；
        CSV 里没有该列时回退到首列并记 WARNING。

        返回:
            ``(prompt, artists_json, tested_count, remaining_count)``
        """
        count = _as_int(artist_count, 1)
        if count < 1:
            raise ValueError(f"[RollingArtist] artist_count 必须 >= 1，当前值: {artist_count!r}")

        # 1) 加载艺术家列表（内容未变化时直接命中缓存，不重复解析 CSV）
        #    列固定为 trigger：该列已转义，可直接进提示词
        artists, _ = self._repo.load(custom_csv_path, "trigger")
        if not artists:
            raise ValueError(
                "[RollingArtist] 未能从 CSV 加载艺术家列表，请检查文件是否存在、"
                f"编码是否为 UTF-8、是否含有 trigger 列（没有该列时会退回首列）: "
                f"{self._repo.resolve_path(_text(custom_csv_path))}"
                "（若本节点是从旧工作流载入的，请检查“自定义 CSV 路径”是否已清空）"
            )

        # 2) 参数规范化（兼容 API 直接传入 None / 字符串的情况）
        exclude_set = set(split_names(exclude_artists))
        force_list = split_names(force_include)
        candidates = artists if not exclude_set else [
            name for name in artists if name not in exclude_set
        ]
        if not candidates and not force_list:
            raise ValueError("[RollingArtist] exclude_artists 排除了全部艺术家，无可用艺术家")

        top_count = compute_top_count(len(artists), artist_top_ratio)
        with self._lock:
            self.artists, self.top_count = artists, top_count

        prefix = "artist:" if _as_bool(artists_prefix, True) else ""
        dedup = normalize_dedup_mode(dedup_mode)
        attempts = max(1, _as_int(max_attempts, 10))
        rng_seed = _as_int(seed, 0)
        low = _as_float(weight_min, 0.2)
        high = _as_float(weight_max, 1.0)
        total = _as_float(weight_total, 2.0)
        grid = weight_grid(low, high)
        use_exact = _resolve_exact_mode(mode, count, force_list)

        # 3) 记录库：SQLite（已测记录 + 穷举组合池）
        db = get_db(tested_db_path)

        # 4) 穷举模式：穷举 (艺术家, 权重)，穷举完之前绝不重复
        #    该模式固定使用 full_prompt 去重键，不参与 weight_total 分配（按权重网格枚举单权重）
        if use_exact:
            _notify_exact_mode(dedup, dedup_mode, total)
            prompt, payload_json = self._generate_exact(
                db, candidates, artists, top_count,
                rng_seed, grid, prefix, attempts,
            )
            return (prompt, payload_json, db.count(KIND_ARTIST),
                    remaining_count(db, candidates, grid, KIND_ARTIST))

        # 5) 常规模式：整段生成处于同一个事务内，进程内 / 跨进程都不会产生重复组合
        with db.generation_lock():
            last_result: Optional[Tuple[List[str], List[float], str]] = None

            for attempt in range(attempts):
                rng = random.Random(rng_seed + attempt)
                selected = select_artists(
                    rng, count, _as_int(artist_top_count, 1), force_list,
                    artists, top_count if _as_bool(use_top_priority, True) else 0,
                    exclude_set,
                )
                if not selected:
                    continue
                if len(selected) < count:
                    LOGGER.warning(
                        "[RollingArtist] 可用艺术家不足：请求 %d 个，实际选中 %d 个"
                        "（请检查 artist_count、排除名单或 CSV 规模）",
                        count, len(selected),
                    )

                weights = generate_weights(len(selected), low, high, total, rng, weight_curve)
                if _as_bool(sort_by_weight, True):
                    pairs = sorted(zip(selected, weights), key=lambda item: item[1], reverse=True)
                    selected = [item[0] for item in pairs]
                    weights = [item[1] for item in pairs]

                prompt = build_prompt(selected, weights, prefix)
                last_result = (selected, weights, prompt)

                if dedup == "none" or not db.is_tested(dedup, selected, weights, KIND_ARTIST):
                    break

            if last_result is None:
                raise ValueError("[RollingArtist] 未选出任何艺术家，请检查 artist_count 与排除名单")

            selected, weights, prompt = last_result
            # 循环耗尽说明末次结果仍命中已测组合（dedup != none 时才可能），
            # 保留原有“接受并记录”语义，仅补上信号便于排查“反复出同一图”
            if dedup != "none" and db.is_tested(dedup, selected, weights, KIND_ARTIST):
                LOGGER.warning(
                    "[RollingArtist] 已试 %d 次均命中已测组合（dedup_mode=%s），返回重复结果: %s",
                    attempts, dedup, prompt,
                )
            if not db.record(selected, weights, prompt, KIND_ARTIST):
                LOGGER.error(
                    "[RollingArtist] 已测记录写入数据库失败(%s)，本次组合未落盘，"
                    "后续可能重复生成: %s",
                    db.path, prompt,
                )

        payload = self._build_payload(selected, weights, artists, top_count)
        # 常规模式没有组合池，「剩余」不适用，固定输出 0
        return prompt, json.dumps(payload, ensure_ascii=False), db.count(KIND_ARTIST), 0

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _generate_exact(self, db: RollingArtistDB, candidates: List[str], artists: List[str],
                        top_count: int, rng_seed: int, grid: List[float],
                        prefix: str, attempts: int) -> Tuple[str, str]:
        """穷举模式：每次返回一个未测过的 ``(艺术家, 权重)`` 组合。

        全部穷举后返回 ``("ALL_COMBINATIONS_TESTED", json)``。
        """
        with db.generation_lock():
            rng = random.Random(rng_seed)
            combo = take_exact(db, rng, candidates, grid, attempts, KIND_ARTIST)
            if combo is None:
                LOGGER.warning(
                    "[RollingArtist] 组合已穷举完(%s)，prompt 返回 %s",
                    db.path, _STATUS_ALL_TESTED,
                )
                payload = {"status": _STATUS_ALL_TESTED, "artists": []}
                return _STATUS_ALL_TESTED, json.dumps(payload, ensure_ascii=False)

            artist, weight = combo
            prompt = build_prompt([artist], [weight], prefix)
            if not db.record([artist], [weight], prompt, KIND_ARTIST):
                LOGGER.error(
                    "[RollingArtist] 已测记录写入数据库失败(%s)，该组合可能被再次发出: %s",
                    db.path, prompt,
                )

        payload = self._build_payload([artist], [weight], artists, top_count)
        return prompt, json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _build_payload(selected: List[str], weights: List[float],
                       artists: List[str], top_count: int) -> Dict[str, Any]:
        """组装结构化输出：艺术家 / 权重 / 是否 Top / 状态。"""
        # top_count >= 总数时全员都是 Top，无需构建集合（大 CSV 下可省下可观内存）
        top_names: Optional[Set[str]] = None
        if top_count < len(artists):
            top_names = set(artists[:top_count])
        return {
            "artists": [
                {
                    "name": name,
                    "weight": weight,
                    "top": top_names is None or name in top_names,
                }
                for name, weight in zip(selected, weights)
            ],
            "status": _STATUS_OK,
        }


# 注册节点类
NODE_CLASS_MAPPINGS = {"RollingArtist": RollingArtist}
# 设置节点显示名称
NODE_DISPLAY_NAME_MAPPINGS = {"RollingArtist": "Rolling Artist"}
