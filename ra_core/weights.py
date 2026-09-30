"""权重分配、提示词构建/解析与去重键。

纯逻辑模块，不涉及文件 IO，便于单独测试。
"""

import random
import re
from typing import Iterable, List, Optional, Sequence, Tuple

from .constants import WEIGHT_SCALE, WEIGHT_STEP

__all__ = [
    "DEDUP_MODES",
    "GENERATION_MODES",
    "WEIGHT_CURVES",
    "generate_weights",
    "build_prompt",
    "parse_prompt",
    "parse_weight_list",
    "normalize_dedup_mode",
    "normalize_generation_mode",
    "normalize_weight_curve",
    "escape_key_part",
    "dedup_key",
    "weight_grid",
]

# 去重模式：只有 none 之外的模式才需要读取已测记录
DEDUP_MODES = ("none", "full_prompt", "artist_set", "artist_list")

# 生成模式：
# - auto   保持 3.2.x 行为（artist_count == 1 且未设置 force_include 时进入穷举）
# - random 永不穷举
# - exact  强制穷举（要求 artist_count == 1 且未设置 force_include）
GENERATION_MODES = ("auto", "random", "exact")

# 权重分配曲线（决定各艺术家权重是平均分配还是主次分明）：
# - flat     现状：每位艺术家的随机倾向接近（0.5~1.5），风格平均混合
# - dominant 主次分明：随机挑一位当主风格，权重明显高于其余
# - ramp     阶梯：按随机名次递减（第一个最多，依次递减）
WEIGHT_CURVES = ("flat", "dominant", "ramp")

# 解析 "artist:name:0.8" / "(artist:name:0.8)" / "name" 这类片段
_PROMPT_PART_RE = re.compile(
    r"^\(*\s*(?:artist:)?\s*(.*?)\s*(?::\s*([0-9]*\.?[0-9]+))?\s*\)*$"
)
_KEY_SEP = "|"


def _to_units(value: float) -> int:
    """把权重值换算成步长为 WEIGHT_STEP 的整数单位（四舍五入到 0.1）。"""
    return int(round(float(value) * WEIGHT_SCALE))


def _from_units(units: int) -> float:
    """把整数单位还原为保留一位小数的权重值。"""
    return round(units / WEIGHT_SCALE, 1)


def _curve_desires(count: int, curve: str, rng: random.Random) -> List[float]:
    """按分配曲线生成每个槽位的“倾向值”（相对份额，只有比例有意义）。

    - ``flat``：全部在 0.5~1.5 之间随机，分配结果接近平均
    - ``dominant``：随机挑一位给高倾向，其余给低倾向，形成明显主次
    - ``ramp``：把槽位随机打乱后按名次递减（1, 1/2^0.7, 1/3^0.7 …），形成阶梯
    """
    if count <= 0:
        return []
    if curve == "dominant":
        main = rng.randrange(count)
        return [
            rng.uniform(2.2, 3.2) if index == main else rng.uniform(0.15, 0.6)
            for index in range(count)
        ]
    if curve == "ramp":
        order = list(range(count))
        rng.shuffle(order)
        desires = [0.0] * count
        for rank, index in enumerate(order):
            desires[index] = 1.0 / (rank + 1) ** 0.7
        return desires
    return [rng.uniform(0.5, 1.5) for _ in range(count)]


def generate_weights(count: int, weight_min: float, weight_max: float,
                     weight_total: float, rng: random.Random,
                     curve: Optional[str] = None) -> List[float]:
    """生成满足约束的随机权重列表。

    约束（与节点界面描述一致）：
    1. 每个权重 ∈ [weight_min, weight_max]（不会越界；weight_max < weight_min 时自动交换）
    2. 权重总和精确等于 weight_total（超出可行区间时夹到边界）
    3. 每个权重保留一位小数，且分配结果带随机性
    4. 权重恒为非负：传入负数（API 直传可能出现）会被夹到 0

    参数:
        curve: 分配曲线，见 :data:`WEIGHT_CURVES`（非法值回退为 flat）。
               它只影响“谁分得多”，不会破坏上面 4 条约束。

    实现说明：所有运算都在“0.1 的整数倍”单位上进行，避免浮点累计误差；
    先按随机倾向值做近似平均的分配，再用最大余数法与容量回填保证总和精确，
    因此不会再出现旧实现中“为凑总和而越过 weight_max”的问题。
    """
    n = int(count)
    if n <= 0:
        return []

    low_units = _to_units(weight_min)
    high_units = _to_units(weight_max)
    if high_units < low_units:  # 容忍参数写反的情况
        low_units, high_units = high_units, low_units
    # 边界保护：负数权重在提示词里无意义，会污染总和与条件编码
    low_units = max(0, low_units)
    high_units = max(0, high_units)

    total_units = _to_units(weight_total)
    # 夹到可行区间：n * min <= total <= n * max
    total_units = max(n * low_units, min(total_units, n * high_units))
    extra = total_units - n * low_units
    if extra <= 0:
        return [_from_units(low_units)] * n

    caps = [high_units - low_units] * n  # 每个槽位在最小值之上还能分配多少
    if sum(caps) <= 0:  # min == max，所有槽位只能取同一个值
        return [_from_units(low_units)] * n

    # 1) 倾向值决定各槽位的份额；曲线决定分布是平均还是主次分明
    desire = _curve_desires(n, normalize_weight_curve(curve), rng)
    desire_sum = sum(desire)
    shares = [extra * value / desire_sum for value in desire]

    allocation = [min(int(share), cap) for share, cap in zip(shares, caps)]
    remain = extra - sum(allocation)

    # 2) 余数按小数部分从大到小补齐（最大余数法），保证总和精确
    order = sorted(
        range(n),
        key=lambda index: (shares[index] - int(shares[index]), rng.random()),
        reverse=True,
    )
    for index in order:
        if remain <= 0:
            break
        if allocation[index] < caps[index]:
            allocation[index] += 1
            remain -= 1

    # 3) 仍有余量时逐个回填（sum(caps) >= extra 恒成立，一定能填满）
    while remain > 0:
        progressed = False
        for index in range(n):
            if remain <= 0:
                break
            if allocation[index] < caps[index]:
                allocation[index] += 1
                remain -= 1
                progressed = True
        if not progressed:
            break

    return [_from_units(low_units + value) for value in allocation]


def build_prompt(artists: Iterable[str], weights: Iterable[float], prefix: str = "") -> str:
    """把艺术家名与权重构建为提示词字符串。

    权重为 1.0 时直接输出名称（不含括号与权重），例如 ``artist:a,(artist:b:0.7)``。
    """
    parts: List[str] = []
    for artist, weight in zip(artists, weights):
        value = round(float(weight), 1)
        name = f"{prefix}{artist}"
        parts.append(name if value == 1.0 else f"({name}:{value})")
    return ",".join(parts)


def parse_prompt(prompt: str) -> Tuple[List[str], List[float]]:
    """从提示词字符串反向解析出艺术家名与权重列表。

    支持 ``artist:a,(artist:b:0.7)`` 或 ``a,(b:0.7)`` 两种形式。
    """
    artists: List[str] = []
    weights: List[float] = []
    if not prompt:
        return artists, weights
    for part in str(prompt).split(","):
        part = part.strip()
        if not part:
            continue
        match = _PROMPT_PART_RE.match(part)
        if match is None:
            name, weight = part.strip("() "), 1.0
        else:
            name = (match.group(1) or "").strip()
            weight = float(match.group(2)) if match.group(2) else 1.0
        if name:
            artists.append(name)
            weights.append(round(weight, 1))
    return artists, weights


def parse_weight_list(text: str) -> List[float]:
    """解析逗号分隔的权重字符串（已测 CSV 第二列），无法解析的项会被跳过。"""
    weights: List[float] = []
    for item in str(text or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            weights.append(round(float(item), 1))
        except ValueError:
            continue
    return weights


def normalize_dedup_mode(mode: Optional[str]) -> str:
    """规范化去重模式，非法值回退为 none。"""
    value = str(mode or "none").strip().lower()
    return value if value in DEDUP_MODES else "none"


def normalize_generation_mode(mode: Optional[str]) -> str:
    """规范化生成模式，非法值回退为 auto（保持历史行为）。"""
    value = str(mode or "auto").strip().lower()
    return value if value in GENERATION_MODES else "auto"


def normalize_weight_curve(curve: Optional[str]) -> str:
    """规范化权重分配曲线，非法值回退为 flat（保持历史行为）。"""
    value = str(curve or "flat").strip().lower()
    return value if value in WEIGHT_CURVES else "flat"


def escape_key_part(value: str) -> str:
    """转义键分隔符：把 | 与 : 变成重复字符（可逆的重复表示法）。

    组合池构建时会在 SQL 里拼同样的字符串，因此本函数必须保持纯字符串变换。
    """
    return str(value).replace(_KEY_SEP, _KEY_SEP * 2).replace(":", "::")


def dedup_key(mode: str, artists: Sequence[str], weights: Sequence[float]) -> str:
    """按去重模式计算组合的唯一键；mode 为 none 时返回空字符串。

    名称中的分隔符会被转义，因此 ``["a:b"]`` 与 ``["a", "b"]`` 不会拼出同一个键。
    旧实现直接拼接，艺术家名含 ``:`` / ``|`` 时会把不同组合判成同一个，
    导致这些组合永远不被输出（去重假阳性）。
    """
    if mode == "full_prompt":
        return _KEY_SEP.join(
            f"{escape_key_part(artist)}:{round(float(weight), 1)}"
            for artist, weight in zip(artists, weights)
        )
    if mode == "artist_set":
        return _KEY_SEP.join(sorted(escape_key_part(name) for name in set(artists)))
    if mode == "artist_list":
        return _KEY_SEP.join(sorted(escape_key_part(name) for name in artists))
    return ""


def weight_grid(weight_min: float, weight_max: float) -> List[float]:
    """按 WEIGHT_STEP 枚举 [weight_min, weight_max] 内的所有权重（保留一位小数）。"""
    low, high = round(float(weight_min), 1), round(float(weight_max), 1)
    if high < low:
        low, high = high, low
    steps = int(round((high - low) / WEIGHT_STEP))
    return [round(low + index * WEIGHT_STEP, 1) for index in range(max(0, steps) + 1)]
