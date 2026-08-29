"""CostBenefitFilter 单元测试 — 覆盖通过/拒绝/CALM/期权/边界值。

测试策略:
    - 覆盖率目标 ≥ 95%
    - 边界值测试精确到小数点后 6 位
"""

from __future__ import annotations

import pytest

from utils.auto_hedge_rebalance.cost_benefit_filter import (
    CostBenefitFilter,
    PortfolioRisk,
)
from utils.auto_hedge_rebalance.models import (
    HedgeToolType,
    OptionsStrategy,
    ToolSelection,
)


@pytest.fixture
def cost_filter() -> CostBenefitFilter:
    return CostBenefitFilter(threshold=1.5)


@pytest.fixture
def normal_risk() -> PortfolioRisk:
    return PortfolioRisk(
        volatility=0.18,
        max_drawdown_60d=0.10,
        equity_exposure=0.80,
        nav=1000.0,
        beta=1.0,
    )


class TestCostBenefitFilterPass:
    """成本效益通过测试。"""

    def test_high_benefit_passes(
        self, cost_filter: CostBenefitFilter, normal_risk: PortfolioRisk
    ) -> None:
        # Arrange — 高回撤 + 高对冲比例 → 高收益
        selection = ToolSelection(
            tool_type=HedgeToolType.INDEX_FUTURES,
            instruments=["IF"],
            hedge_ratio=0.25,
        )
        risk = PortfolioRisk(max_drawdown_60d=0.20, equity_exposure=0.90)
        # Act
        result = cost_filter.filter(selection, risk)
        # Assert
        assert result.passed is True
        assert result.cost_estimate > 0
        assert result.expected_benefit > 0

    def test_calm_state_zero_cost_passes(
        self, cost_filter: CostBenefitFilter, normal_risk: PortfolioRisk
    ) -> None:
        # Arrange — CALM 状态 (对冲比例 0%)
        selection = ToolSelection(tool_type=HedgeToolType.NONE, hedge_ratio=0.0)
        # Act
        result = cost_filter.filter(selection, normal_risk)
        # Assert
        assert result.passed is True
        assert result.cost_estimate == 0.0
        assert result.expected_benefit == 0.0


class TestCostBenefitFilterReject:
    """成本效益拒绝测试。"""

    def test_low_benefit_rejected(
        self, cost_filter: CostBenefitFilter, normal_risk: PortfolioRisk
    ) -> None:
        # Arrange — 低回撤 + 低对冲比例 → 低收益
        selection = ToolSelection(
            tool_type=HedgeToolType.INDEX_FUTURES,
            instruments=["IF"],
            hedge_ratio=0.05,
        )
        risk = PortfolioRisk(max_drawdown_60d=0.02, equity_exposure=0.50)
        # Act
        result = cost_filter.filter(selection, risk)
        # Assert
        assert result.passed is False
        assert "成本效益不足" in result.reject_reason

    def test_zero_drawdown_rejected(self, cost_filter: CostBenefitFilter) -> None:
        # Arrange — 零回撤 → 零收益
        selection = ToolSelection(
            tool_type=HedgeToolType.INDEX_FUTURES,
            instruments=["IF"],
            hedge_ratio=0.20,
        )
        risk = PortfolioRisk(max_drawdown_60d=0.0, equity_exposure=0.80)
        # Act
        result = cost_filter.filter(selection, risk)
        # Assert
        assert result.passed is False
        assert result.expected_benefit == 0.0


class TestCostBenefitFilterOptions:
    """期权成本计算测试。"""

    def test_options_premium_included(self, cost_filter: CostBenefitFilter) -> None:
        # Arrange — ETF 期权含权利金
        selection = ToolSelection(
            tool_type=HedgeToolType.ETF_OPTIONS,
            instruments=["510300"],
            hedge_ratio=0.15,
            options_strategy=OptionsStrategy.PROTECTIVE_PUT,
        )
        risk = PortfolioRisk(max_drawdown_60d=0.15, equity_exposure=0.80)
        # Act
        result = cost_filter.filter(selection, risk)
        # Assert — 期权成本应高于期货成本 (含权利金)
        futures_selection = ToolSelection(
            tool_type=HedgeToolType.INDEX_FUTURES,
            instruments=["IF"],
            hedge_ratio=0.15,
        )
        futures_result = cost_filter.filter(futures_selection, risk)
        assert result.cost_estimate > futures_result.cost_estimate


class TestCostBenefitFilterBoundary:
    """边界值测试。"""

    def test_exact_threshold_boundary(self, normal_risk: PortfolioRisk) -> None:
        # Arrange — 调整阈值使收益恰好等于成本 × 阈值
        selection = ToolSelection(
            tool_type=HedgeToolType.INDEX_FUTURES,
            instruments=["IF"],
            hedge_ratio=0.20,
        )
        risk = PortfolioRisk(max_drawdown_60d=0.10, equity_exposure=0.80)
        # Act — 先计算实际收益和成本
        cost_filter = CostBenefitFilter(threshold=1.5)
        cost = cost_filter._compute_cost(selection, {})
        benefit = cost_filter._compute_expected_benefit(risk, 0.20)
        # 调整阈值使 benefit = cost × threshold
        exact_threshold = benefit / cost if cost > 0 else 999
        boundary_filter = CostBenefitFilter(threshold=exact_threshold)
        result = boundary_filter.filter(selection, risk)
        # Assert — 恰好等于阈值时应拒绝 (严格大于才通过)
        assert result.passed is False

    def test_zero_hedge_ratio_passes(
        self, cost_filter: CostBenefitFilter, normal_risk: PortfolioRisk
    ) -> None:
        # Arrange
        selection = ToolSelection(
            tool_type=HedgeToolType.INDEX_FUTURES,
            hedge_ratio=0.0,
        )
        # Act
        result = cost_filter.filter(selection, normal_risk)
        # Assert
        assert result.passed is True
        assert result.cost_estimate == pytest.approx(0.0, abs=1e-6)
