"""节点入参的容错转换。

ComfyUI 本身会按 ``INPUT_TYPES`` 校验类型与范围（见 ``execution.py`` 的
``validate_inputs``），因此这些兜底主要服务于「外部脚本直接调用节点类」的场景：
传进来的可能是 ``None``、字符串或其它可转换的值，这里统一转换并在失败时回退默认值。

各节点共用一份实现，避免两个节点对「参数不是合法整数」的处理出现分歧。
"""

from typing import Any

from .constants import LOGGER

__all__ = ["as_text", "as_int", "as_float", "as_bool"]


def as_text(value: Any) -> str:
    """把可能为 None 的字符串输入安全地转成 str。"""
    return "" if value is None else str(value)


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        LOGGER.warning("[RollingArtist] 参数 %r 不是合法整数，回退为默认值 %r", value, default)
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        LOGGER.warning("[RollingArtist] 参数 %r 不是合法数值，回退为默认值 %r", value, default)
        return default


def as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "off")
    return bool(value)
