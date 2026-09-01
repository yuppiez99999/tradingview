"""成本效益过滤器 — 对冲方案准入判定。

本组件计算对冲成本与预期收益，按 1.5 倍阈值过滤，
保证仅执行成本效益合理的对冲方案。

成本计算:
    对冲成本 = 展期成本 (年化展期率 × 名义敞口)
             + 保证金机会成本 (保证金率 × 保证金)
             + 权利金 (期权时)

收益计算:
    预期对冲收益 = 回撤减少量 × 对冲比例 × 组合股票敞口

准入判定:
    expected_benefit > cost × threshold (默认 1.5)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from utils.auto_hedge_rebalance.models import FilterResult, HedgeToolType, ToolSelection

logger = logging.getLogger(__name__)


# ============================================================================
# 复用存量常量 (避免硬编码)
# ============================================================================

try:
    from utils.hedge_engine import (
        COST_BENEFIT_THRESHOLD,
        HEDGE_MARGIN_OPP_COST,
        HEDGE_ROLL_COST_ANNUAL,
    )
except ImportError:
    HEDGE_ROLL_COST_ANNUAL = 0.025
    HEDGE_MARGIN_OPP_COST = 0.020
    COST_BENEFIT_THRESHOLD = 1.5


# ============================================================================
# PortfolioRisk 数据结构
# ============================================================================


@dataclass(frozen=True)
class PortfolioRisk:
    """组合风险评估结果。

    Attributes:
        volatility: 组合年化波动率 (0.0-1.0)。
        max_drawdown_60d: 60 日最大回撤 (0.0-1.0)。
        equity_exposure: 组合股票敞口比例 (0.0-1.0)。
        nav: 组合净值 (万元)。
        beta: 组合相对大盘的 Beta 系数。
    """

    volatility: float = 0.18
    max_drawdown_60d: float = 0.0
    equity_exposure: float = 0.8
    nav: float = 1000.0
    beta: float = 1.0


# ============================================================================
# CostBenefitFilter
# ============================================================================


class CostBenefitFilter:
    """成本效益过滤器。

    计算对冲成本与预期收益，按阈值过滤，保证仅执行成本效益合理的对冲方案。

    Attributes:
        threshold: 成本效益阈值 (预期收益 > 成本 × 阈值)。
        roll_cost_annual: 年化展期成本率。
        margin_opp_cost: 保证金机会成本率。
    """

    def __init__(
        self,
        threshold: float = COST_BENEFIT_THRESHOLD,
        roll_cost_annual: float = HEDGE_ROLL_COST_ANNUAL,
        margin_opp_cost: float = HEDGE_MARGIN_OPP_COST,
    ) -> None:
        """初始化成本效益过滤器。

        Args:
            threshold: 成本效益阈值 (默认 1.5，复用 HedgeEngine.COST_BENEFIT_THRESHOLD)。
            roll_cost_annual: 年化展期成本率 (默认 2.5%)。
            margin_opp_cost: 保证金机会成本率 (默认 2%)。
        """
        self.threshold = threshold
        self.roll_cost_annual = roll_cost_annual
        self.margin_opp_cost = margin_opp_cost

    def filter(
        self,
        selection: ToolSelection,
        risk: PortfolioRisk,
        prices: dict[str, float] | None = None,
    ) -> FilterResult:
        """执行成本效益过滤。

        Args:
            selection: 对冲工具选择结果。
            risk: 组合风险评估。
            prices: 工具行情价格字典 (可选)。

        Returns:
            过滤结果 FilterResult。
        """
        # CALM 状态 (对冲比例 0%) 直接返回通过且成本为 0
        if selection.tool_type == HedgeToolType.NONE or selection.hedge_ratio <= 0:
            return FilterResult(
                passed=True,
                cost_estimate=0.0,
                expected_benefit=0.0,
                reject_reason="",
            )

        cost = self._compute_cost(selection, prices or {})
        benefit = self._compute_expected_benefit(risk, selection.hedge_ratio)

        if benefit > cost * self.threshold:
            return FilterResult(
                passed=True,
                cost_estimate=cost,
                expected_benefit=benefit,
                reject_reason="",
            )

        reject_reason = f"成本效益不足: 预期收益{benefit:.4f} ≤ 成本{cost:.4f} × 阈值{self.threshold}"
        logger.info("[过滤] %s", reject_reason)
        return FilterResult(
            passed=False,
            cost_estimate=cost,
            expected_benefit=benefit,
            reject_reason=reject_reason,
        )

    def _compute_cost(
        self,
        selection: ToolSelection,
        prices: dict[str, float],
    ) -> float:
        """计算对冲成本。

        对冲成本 = 展期成本 + 保证金机会成本 + 权利金 (期权时)

        Args:
            selection: 对冲工具选择结果。
            prices: 工具行情价格字典。

        Returns:
            对冲成本 (占组合净值比例)。
        """
        hedge_ratio = selection.hedge_ratio

        # 展期成本 = 年化展期率 × 名义敞口比例
        roll_cost = self.roll_cost_annual * hedge_ratio

        # 保证金机会成本 = 保证金率 × 保证金占用比例
        # 期货保证金约 10-15%，取 12%
        margin_ratio = (
            0.12 if selection.tool_type == HedgeToolType.INDEX_FUTURES else 0.0
        )
        margin_cost = self.margin_opp_cost * margin_ratio * hedge_ratio

        # 权利金 (期权时) — 期权权利金约标的价格的 3%
        premium_cost = 0.0
        if selection.tool_type == HedgeToolType.ETF_OPTIONS:
            premium_cost = 0.03 * hedge_ratio

        total_cost = roll_cost + margin_cost + premium_cost
        return total_cost

    def _compute_expected_benefit(
        self,
        risk: PortfolioRisk,
        hedge_ratio: float,
    ) -> float:
        """计算预期对冲收益。

        预期对冲收益 = 回撤减少量 × 对冲比例 × 组合股票敞口

        Args:
            risk: 组合风险评估。
            hedge_ratio: 对冲比例。

        Returns:
            预期对冲收益 (占组合净值比例)。
        """
        # 回撤减少量: 假设对冲可减少 60% 的回撤
        drawdown_reduction = risk.max_drawdown_60d * 0.6
        benefit = drawdown_reduction * hedge_ratio * risk.equity_exposure
        return benefit


__all__ = ["CostBenefitFilter", "PortfolioRisk"]
