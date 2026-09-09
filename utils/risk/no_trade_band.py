"""
No-Trade Band — 波动率调制的再平衡触发容忍带
=============================================

设计依据 (2026-09-07 T2, 对冲基金审计六项升级之二):
    固定偏离阈值 (如 ±20%) 在高波动期过度交易、低波动期反应迟钝。
    业界标准做法是 no-trade band: 偏离小于带宽时不调仓, 带宽随目标权重
    与标的波动率调制 — band = max(abs_tol, k * target_weight * sigma)。

    k 越大越保守 (交易越少); sigma 高 → band 宽 → 减少高波动期的交易成本;
    国债等低波动资产 sigma 小 → band 收窄到 abs_tol → 及时跟踪目标。

失败语义 (决策路径 fail-close, 观测路径 fail-open):
    本模块属于"要不要交易"的决策辅助, 但 sigma 缺失仅导致带宽退化到
    abs_tol (更接近旧行为), 不会阻断交易 — 故按 fail-open 处理并标注原因,
    调用方可通过 BandDecision.reason 审计。current_weight 无效则保守触发
    调仓 (fail-close), 数据坏了不应静默躺平。

用法:
    from utils.risk.no_trade_band import should_rebalance

    d = should_rebalance(current_weight=0.13, target_weight=0.15, sigma=0.25)
    if d.triggered:
        generate_rebalance_orders(...)  # 偏离超出容忍带, 执行调仓
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 带宽乘数: band = k * target_weight * sigma (业界常用 0.5)
DEFAULT_K = 0.5
# 绝对容忍带下限: 防止低权重/低波动资产 band 过窄导致微调交易
DEFAULT_ABS_TOL = 0.02


@dataclass(frozen=True)
class BandDecision:
    """no-trade band 判断结果 (审计可追溯)."""

    deviation: float  # current_weight - target_weight (有符号)
    band: float  # 实际生效带宽 (非负)
    triggered: bool  # True = 偏离超出容忍带, 应调仓
    reason: str  # 触发/不触发原因 (审计字段)


def _sigma_valid(sigma: float | None) -> bool:
    return isinstance(sigma, (int, float)) and not isinstance(sigma, bool) and math.isfinite(sigma) and sigma > 0


def band_width(
    target_weight: float,
    sigma: float | None,
    k: float = DEFAULT_K,
    abs_tol: float = DEFAULT_ABS_TOL,
) -> tuple[float, str]:
    """计算带宽. 返回 (band, reason).

    sigma 无效 (None/NaN/<=0) 时 fail-open 退化为 abs_tol, reason 标注来源.
    """
    if (
        not isinstance(target_weight, (int, float))
        or isinstance(target_weight, bool)
        or not math.isfinite(target_weight)
        or target_weight < 0
    ):
        # 目标权重无效 → 保守退化 abs_tol (与旧行为等价的固定带宽)
        return abs_tol, "invalid_target_weight_abs_tol"
    if not _sigma_valid(sigma):
        return abs_tol, "sigma_unavailable_abs_tol"
    # _sigma_valid 是自定义守卫函数, mypy 无法据此收窄 Optional;
    # 其返回 True 时 sigma 必非 None (isinstance + isfinite 检查), 断言零成本
    assert sigma is not None
    band = max(abs_tol, k * target_weight * sigma)
    return band, "volatility_modulated"


def should_rebalance(
    current_weight: float,
    target_weight: float,
    sigma: float | None = None,
    k: float = DEFAULT_K,
    abs_tol: float = DEFAULT_ABS_TOL,
) -> BandDecision:
    """判断权重偏离是否超出容忍带.

    Args:
        current_weight: 当前权重 (0~1).
        target_weight: 目标权重 (0~1).
        sigma: 年化波动率 (如 0.25 = 25%). None/无效时退化为固定 abs_tol.
        k: 波动率调制乘数.
        abs_tol: 绝对容忍带下限.

    Returns:
        BandDecision — triggered=True 表示应执行再平衡.
    """
    if (
        not isinstance(current_weight, (int, float))
        or isinstance(current_weight, bool)
        or not math.isfinite(current_weight)
    ):
        # 当前权重无效 → 保守触发调仓 (数据坏了不应静默躺平, fail-close)
        logger.warning("[NoTradeBand] current_weight 无效: %r, 保守触发调仓", current_weight)
        return BandDecision(
            deviation=float("nan"),
            band=abs_tol,
            triggered=True,
            reason="invalid_current_weight_fail_close",
        )

    band, reason = band_width(target_weight, sigma, k=k, abs_tol=abs_tol)
    deviation = current_weight - target_weight
    triggered = abs(deviation) > band

    if not triggered:
        logger.debug(
            "[NoTradeBand] 偏离 %.4f 在容忍带 %.4f 内, 跳过 (%s)",
            deviation,
            band,
            reason,
        )
    return BandDecision(deviation=deviation, band=band, triggered=triggered, reason=reason)
