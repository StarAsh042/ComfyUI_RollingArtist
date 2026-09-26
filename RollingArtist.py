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
    DEFAULT_TESTED_CSV,
    EXACT_COMBOS_WARN,
    LOGGER,
    WEIGHT_STEP,
)
from .ra_core.exact import remaining_path_for, take_exact
from .ra_core.storage import TestedStore, get_tested_store
from .ra_core.weights import (
    build_prompt,
    dedup_key,
    generate_weights,
    normalize_dedup_mode,
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


def _text(value: Any) -> str:
    """把可能为 None 的字符串输入安全地转成 str。"""
    return "" if value is None else str(value)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "off")
    return bool(value)


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
    - 同一 ``tested_csv_path`` 上的去重记录与 Exact 组合池在进程内共享并加锁，
      “读取已测 -> 生成 -> 记录”整段处于同一把可重入锁内，避免重复组合
    """

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Dict[str, Any]]:
        """定义节点的输入参数类型和界面显示。"""
        return {
            "required": {
                "artist_count": ("INT", {
                    "default": 3,
                    "min": 1,
                    "max": 10,
                    "step": 1,
                    "display": "slider",
                    "description": "选择生成的艺术家人数（1-10）；为 1 时进入 Exact 穷举模式"
                }),
                "artist_top_count": ("INT", {
                    "default": 1,
                    "min": 1,
                    "max": 10,
                    "step": 1,
                    "display": "slider",
                    "description": "输出中包含的Top艺术家数量（至少1个）"
                }),
                "artist_top_ratio": ("FLOAT", {
                    "default": 0.01,
                    "min": 0.01,
                    "max": 1.0,
                    "step": 0.01,
                    "display": "slider",
                    "description": "提取CSV中Top艺术家占前百分比,已知CSV越靠前艺术家作品越多"
                }),
                "artists_prefix": ("BOOLEAN", {
                    "default": True,
                    "description": "是否为艺术家名称添加'artist:'前缀"
                }),
                "weight_min": ("FLOAT", {
                    "default": 0.2,
                    "min": 0.1,
                    "max": 2.0,
                    "step": 0.1,
                    "display": "slider",
                    "description": "单个艺术家最小权重值"
                }),
                "weight_max": ("FLOAT", {
                    "default": 1.0,
                    "min": 0.1,
                    "max": 2.0,
                    "step": 0.1,
                    "display": "slider",
                    "description": "单个艺术家最大权重值"
                }),
                "weight_total": ("FLOAT", {
                    "default": 2.0,
                    "min": 0.0,
                    "max": 20.0,
                    "step": 0.5,
                    "display": "slider",
                    "description": "所有权重值的总和"
                }),
                "seed": ("INT", {
                    "default": 1234,
                    "min": 0,
                    "max": 4294967295,
                    "description": "控制随机性的种子值"
                }),
            },
            "optional": {
                "custom_csv_path": ("STRING", {
                    "default": "",
                    "description": "自定义CSV路径，留空使用默认"
                }),
                "csv_column": ("STRING", {
                    "default": "auto",
                    "description": "CSV列选择：auto=自动识别表头与列（推荐）；all=展平所有列（旧行为）；也支持列序号(0/1/…)或表头列名(如 artist)"
                }),
                "exclude_artists": ("STRING", {
                    "default": "",
                    "description": "排除的艺术家，逗号分隔"
                }),
                "sort_by_weight": ("BOOLEAN", {
                    "default": True,
                    "description": "是否按权重从高到低排序"
                }),
                "use_top_priority": ("BOOLEAN", {
                    "default": True,
                    "description": "是否启用Top艺术家优先池；关闭后从完整CSV中均匀抽取"
                }),
                "force_include": ("STRING", {
                    "default": "",
                    "description": "强制包含的艺术家，逗号分隔（优先级高于 exclude_artists）"
                }),
                "dedup_mode": (["none", "full_prompt", "artist_set", "artist_list"], {
                    "default": "none",
                    "description": "去重模式：none=不去重；full_prompt=顺序+权重完全一致；artist_set=艺术家集合（忽略顺序与权重）；artist_list=排序后的艺术家名（忽略权重）"
                }),
                "tested_csv_path": ("STRING", {
                    "default": "",
                    "description": "记录已生成组合的CSV路径，留空使用节点目录下 tested_combinations.csv"
                }),
                "max_attempts": ("INT", {
                    "default": 10,
                    "min": 1,
                    "max": 100,
                    "step": 1,
                    "description": "去重时的最大重试次数，超过后接受最后一次结果"
                }),
            }
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("prompt", "artists_json")
    FUNCTION = "generate_artists"
    CATEGORY = "RollingArtist"
    # 与原实现保持一致：作为输出节点，即使输出未被下游使用也会执行
    OUTPUT_NODE = True
    DESCRIPTION = "从艺术家 CSV 中随机抽取艺术家并分配随机权重，生成带权重的提示词，并输出结构化 JSON。"
    OUTPUT_TOOLTIPS = ("生成的提示词", "结构化结果 JSON（艺术家 / 权重 / 是否 Top / 状态）")

    def __init__(self) -> None:
        """初始化节点实例：准备好仓库与状态容器。

        注意：真正的加载发生在 ``generate_artists`` 中（按文件签名缓存），
        这里只做一次默认 CSV 的预热，且任何异常都不会影响节点注册。
        """
        self._repo: ArtistRepository = get_artist_repository()
        self._lock = threading.RLock()
        self.artists: List[str] = []
        self._file_key: Optional[Tuple[str, int, int]] = None
        self.top_count = 0
        try:
            self.artists, self._file_key = self._repo.load("", "auto")
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

    def load_artists(self, csv_path: Optional[str] = None, column: str = "auto") -> List[str]:
        """加载（并按文件修改时间缓存）艺术家列表。"""
        artists, file_key = self._repo.load(csv_path or "", column)
        if artists:
            with self._lock:
                self.artists, self._file_key = artists, file_key
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
                         tested_csv_path: str = "", max_attempts: int = 10,
                         csv_column: str = "auto") -> Tuple[str, str]:
        """生成艺术家提示词。

        参数与界面一致；``artist_count == 1`` 且未设置 ``force_include`` 时进入 Exact 模式。

        返回:
            ``(prompt, artists_json)``
        """
        count = _as_int(artist_count, 1)
        if count < 1:
            raise ValueError(f"[RollingArtist] artist_count 必须 >= 1，当前值: {artist_count!r}")

        # 1) 加载艺术家列表（内容未变化时直接命中缓存，不重复解析 CSV）
        artists, file_key = self._repo.load(custom_csv_path, csv_column)
        if not artists:
            raise ValueError(
                "[RollingArtist] 未能从 CSV 加载艺术家列表，请检查文件是否存在、"
                f"编码是否为 UTF-8、csv_column 设置是否正确: "
                f"{self._repo.resolve_path(_text(custom_csv_path))}"
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
            self.artists, self._file_key, self.top_count = artists, file_key, top_count

        prefix = "artist:" if _as_bool(artists_prefix, True) else ""
        mode = normalize_dedup_mode(dedup_mode)
        attempts = max(1, _as_int(max_attempts, 10))
        rng_seed = _as_int(seed, 0)
        low = _as_float(weight_min, 0.2)
        high = _as_float(weight_max, 1.0)
        total = _as_float(weight_total, 2.0)
        tested_path = _text(tested_csv_path).strip() or DEFAULT_TESTED_CSV
        store = get_tested_store(tested_path)

        # 3) Exact 模式：穷举 (艺术家, 权重)，穷举完之前绝不重复
        if count == 1 and not force_list:
            return self._generate_exact(
                candidates, artists, top_count, store, tested_path,
                rng_seed, low, high, prefix, attempts,
            )

        # 4) 常规模式：整段生成处于“按路径共享”的锁内，避免多实例产生重复组合
        with store.generation_lock():
            tested_keys = store.keys(mode)
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

                weights = generate_weights(len(selected), low, high, total, rng)
                if _as_bool(sort_by_weight, True):
                    pairs = sorted(zip(selected, weights), key=lambda item: item[1], reverse=True)
                    selected = [item[0] for item in pairs]
                    weights = [item[1] for item in pairs]

                prompt = build_prompt(selected, weights, prefix)
                last_result = (selected, weights, prompt)

                if mode == "none" or dedup_key(mode, selected, weights) not in tested_keys:
                    break

            if last_result is None:
                raise ValueError("[RollingArtist] 未选出任何艺术家，请检查 artist_count 与排除名单")

            selected, weights, prompt = last_result
            store.record(selected, weights, prompt)

        payload = self._build_payload(selected, weights, artists, top_count)
        return prompt, json.dumps(payload, ensure_ascii=False)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _generate_exact(self, candidates: List[str], artists: List[str], top_count: int,
                        store: TestedStore, tested_path: str, rng_seed: int,
                        weight_min: float, weight_max: float, prefix: str,
                        attempts: int) -> Tuple[str, str]:
        """Exact 模式：每次返回一个未测过的 ``(艺术家, 权重)`` 组合。

        全部穷举后返回 ``("ALL_COMBINATIONS_TESTED", json)``。
        """
        remaining_path = remaining_path_for(tested_path)
        with store.generation_lock():
            tested_keys = store.keys("full_prompt")
            rng = random.Random(rng_seed)
            combo = take_exact(
                remaining_path, rng, candidates,
                weight_grid(weight_min, weight_max), tested_keys, attempts,
            )
            if combo is None:
                payload = {"status": _STATUS_ALL_TESTED, "artists": []}
                return _STATUS_ALL_TESTED, json.dumps(payload, ensure_ascii=False)

            artist, weight = combo
            prompt = build_prompt([artist], [weight], prefix)
            store.record([artist], [weight], prompt)

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
