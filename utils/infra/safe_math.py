"""安全数学运算工具 — 防止除零等常见数值错误.

模块整合 8.4 — CODE_REVIEW_AUDIT_20260803 P2-4 修复

设计目标:
    1. 封装常见除零风险 (len/max/abs/sum 等可能为 0 的表达式)
    2. 提供 safe_div / safe_mean / safe_max / safe_pct 等工具
    3. 默认返回 0.0 而非抛出异常 (适合量化因子计算)
    4. 可选 log_warning 参数记录被触发的降级

使用示例:
    from utils.infra.safe_math import safe_div, safe_mean

    # 替代: mean = total / len(values)
    mean = safe_mean(values)

    # 替代: ratio = a / max(b, c)
    ratio = safe_div(a, max(b, c))

    # 替代: pct = part / total * 100
    pct = safe_pct(part, total)
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Optional, Union

logger = logging.getLogger("safe_math")

Number = Union[int, float, complex]
NumberOrNone = Optional[Number]

# 数值安全阈值: 小于此值视为 0 (避免浮点精度问题)
_EPSILON = 1e-10


def safe_div(
    numerator: Number,
    denominator: Number,
    default: float = 0.0,
    log_warning: bool = False,
    context: str = "",
) -> float:
    """安全除法, 避免除零异常.

    Args:
        numerator: 分子
        denominator: 分母
        default: 分母为 0 时返回的默认值
        log_warning: 是否记录降级警告日志
        context: 日志上下文 (如调用位置/变量名)

    Returns:
        除法结果, 或 default (当分母接近 0 时)

    Examples:
        >>> safe_div(10, 2)
        5.0
        >>> safe_div(10, 0)
        0.0
        >>> safe_div(10, 0, default=float('nan'))
        nan
    """
    try:
        denom = float(denominator)
    except (TypeError, ValueError):
        if log_warning:
            logger.warning("safe_div: 分母类型异常 (%r) — %s", denominator, context)
        return default

    if abs(denom) < _EPSILON:
        if log_warning:
            logger.warning("safe_div: 除零风险已避免 (denom=%.6g) — %s", denom, context)
        return default

    try:
        return float(numerator) / denom
    except (TypeError, ValueError):
        if log_warning:
            logger.warning("safe_div: 分子类型异常 (%r) — %s", numerator, context)
        return default


def safe_mean(
    values: Iterable[Number],
    default: float = 0.0,
    log_warning: bool = False,
    context: str = "",
) -> float:
    """安全平均值, 处理空列表.

    Args:
        values: 数值可迭代对象
        default: 空列表时返回的默认值
        log_warning: 是否记录降级警告
        context: 日志上下文

    Returns:
        平均值, 或 default (当列表为空或非数值时)
    """
    try:
        nums = list(values)
    except TypeError:
        if log_warning:
            logger.warning("safe_mean: 输入不可迭代 — %s", context)
        return default

    if not nums:
        if log_warning:
            logger.warning("safe_mean: 空列表 — %s", context)
        return default

    try:
        total = sum(float(x) for x in nums)
        return safe_div(
            total, len(nums), default=default, context=f"safe_mean({context})"
        )
    except (TypeError, ValueError) as e:
        if log_warning:
            logger.warning("safe_mean: 元素非数值 (%s) — %s", e, context)
        return default


def safe_max(
    values: Iterable[Number],
    default: float = 0.0,
    log_warning: bool = False,
    context: str = "",
) -> float:
    """安全最大值, 处理空列表.

    Args:
        values: 数值可迭代对象
        default: 空列表时返回的默认值
        log_warning: 是否记录降级警告
        context: 日志上下文

    Returns:
        最大值, 或 default
    """
    try:
        nums = [float(x) for x in values]
    except (TypeError, ValueError) as e:
        if log_warning:
            logger.warning("safe_max: 元素非数值 (%s) — %s", e, context)
        return default

    if not nums:
        if log_warning:
            logger.warning("safe_max: 空列表 — %s", context)
        return default

    return max(nums)


def safe_min(
    values: Iterable[Number],
    default: float = 0.0,
    log_warning: bool = False,
    context: str = "",
) -> float:
    """安全最小值, 处理空列表."""
    try:
        nums = [float(x) for x in values]
    except (TypeError, ValueError) as e:
        if log_warning:
            logger.warning("safe_min: 元素非数值 (%s) — %s", e, context)
        return default

    if not nums:
        if log_warning:
            logger.warning("safe_min: 空列表 — %s", context)
        return default

    return min(nums)


def safe_pct(
    part: Number,
    total: Number,
    default: float = 0.0,
    log_warning: bool = False,
    context: str = "",
) -> float:
    """安全百分比计算 (part / total * 100).

    Args:
        part: 部分值
        total: 总值
        default: 总值为 0 时返回的默认百分比
        log_warning: 是否记录降级警告
        context: 日志上下文

    Returns:
        百分比 (0-100), 或 default
    """
    ratio = safe_div(
        part,
        total,
        default=None,
        log_warning=log_warning,
        context=f"safe_pct({context})",
    )
    if ratio is None:
        return default
    return ratio * 100.0


def safe_abs_ratio(
    numerator: Number,
    denominator: Number,
    default: float = 0.0,
    log_warning: bool = False,
    context: str = "",
) -> float:
    """安全绝对值比率 (|numerator| / |denominator|).

    常用于: normalized = x / abs(y) 场景
    """
    try:
        abs_num = abs(float(numerator))
        abs_denom = abs(float(denominator))
    except (TypeError, ValueError) as e:
        if log_warning:
            logger.warning("safe_abs_ratio: 类型异常 (%s) — %s", e, context)
        return default

    return safe_div(
        abs_num, abs_denom, default=default, log_warning=log_warning, context=context
    )


def safe_len(values: Iterable) -> int:
    """安全 len, 处理 None 或不可迭代对象."""
    if values is None:
        return 0
    try:
        return len(values)
    except TypeError:
        try:
            return sum(1 for _ in values)
        except TypeError:
            return 0


__all__ = [
    "safe_div",
    "safe_mean",
    "safe_max",
    "safe_min",
    "safe_pct",
    "safe_abs_ratio",
    "safe_len",
]
