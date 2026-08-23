"""HedgeToolSelector 单元测试 — 覆盖决策表6种组合+降级链。

测试策略:
    - 覆盖率目标 ≥ 90%
    - 决策表 6 种组合均有测试用例
    - 使用 mock 模拟 HedgeEngine 与 DataFetcher
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from utils.auto_hedge_rebalance.cost_benefit_filter import PortfolioRisk
from utils.auto_hedge_rebalance.models import (
    HedgeToolType,
    OptionsStrategy,
    ToolSelection,
)
from utils.auto_hedge_rebalance.tool_selector import (
    HedgeToolSelector,
    MarketRegime,
)


@pytest.fixture
def selector() -> HedgeToolSelector:
    mock_engine = MagicMock()
    mock_engine.generate_futures_hedge.return_value = {"IF": 2, "IC": 1}
    mock_engine.generate_options_hedge.return_value = [{"code": "510300", "strategy": "protective_put"}]
    return HedgeToolSelector(hedge_engine=mock_engine, data_fetcher=MagicMock(), config={})


@pytest.fixture
def large_cap_risk() -> PortfolioRisk:
    return PortfolioRisk(volatility=0.18, max_drawdown_60d=0.10, equity_exposure=0.80, nav=1000.0, beta=1.2)


@pytest.fixture
def small_cap_risk() -> PortfolioRisk:
    return PortfolioRisk(volatility=0.25, max_drawdown_60d=0.15, equity_exposure=0.80, nav=1000.0, beta=0.5)


class TestCalmState:
    """CALM 状态测试。"""

    def test_calm_returns_none(self, selector: HedgeToolSelector, large_cap_risk: PortfolioRisk) -> None:
        # Arrange & Act
        result = selector.select_tools(MarketRegime.CALM, large_cap_risk, 0.0)
        # Assert
        assert result.tool_type == HedgeToolType.NONE
        assert result.hedge_ratio == 0.0

    def test_zero_ratio_returns_none(self, selector: HedgeToolSelector, large_cap_risk: PortfolioRisk) -> None:
        # Arrange & Act
        result = selector.select_tools(MarketRegime.MILD, large_cap_risk, 0.0)
        # Assert
        assert result.tool_type == HedgeToolType.NONE


class TestMildState:
    """MILD 状态测试。"""

    def test_mild_large_cap_selects_if_ih(self, selector: HedgeToolSelector, large_cap_risk: PortfolioRisk) -> None:
        # Arrange & Act
        result = selector.select_tools(MarketRegime.MILD, large_cap_risk, 0.15)
        # Assert
        assert result.tool_type == HedgeToolType.INDEX_FUTURES
        assert "IF" in result.instruments
        assert "IH" in result.instruments

    def test_mild_small_cap_selects_ic_im(self, selector: HedgeToolSelector, small_cap_risk: PortfolioRisk) -> None:
        # Arrange & Act
        result = selector.select_tools(MarketRegime.MILD, small_cap_risk, 0.15)
        # Assert
        assert result.tool_type == HedgeToolType.INDEX_FUTURES
        assert "IC" in result.instruments
        assert "IM" in result.instruments


class TestHighState:
    """HIGH 状态测试。"""

    def test_high_large_cap(self, selector: HedgeToolSelector, large_cap_risk: PortfolioRisk) -> None:
        # Arrange & Act
        result = selector.select_tools(MarketRegime.HIGH, large_cap_risk, 0.25)
        # Assert
        assert result.tool_type == HedgeToolType.INDEX_FUTURES
        assert result.hedge_ratio == 0.25

    def test_high_small_cap(self, selector: HedgeToolSelector, small_cap_risk: PortfolioRisk) -> None:
        # Arrange & Act
        result = selector.select_tools(MarketRegime.HIGH, small_cap_risk, 0.25)
        # Assert
        assert result.tool_type == HedgeToolType.INDEX_FUTURES
        assert "IC" in result.instruments


class TestTailEventState:
    """TAIL_EVENT 状态测试。"""

    def test_tail_event_large_cap_selects_etf_options(self, selector: HedgeToolSelector, large_cap_risk: PortfolioRisk) -> None:
        # Arrange & Act
        result = selector.select_tools(MarketRegime.TAIL_EVENT, large_cap_risk, 0.40)
        # Assert
        assert result.tool_type == HedgeToolType.ETF_OPTIONS
        assert "510300" in result.instruments
        assert "510050" in result.instruments
        assert result.options_strategy == OptionsStrategy.PROTECTIVE_PUT

    def test_tail_event_small_cap_selects_mixed(self, selector: HedgeToolSelector, small_cap_risk: PortfolioRisk) -> None:
        # Arrange & Act
        result = selector.select_tools(MarketRegime.TAIL_EVENT, small_cap_risk, 0.40)
        # Assert
        assert result.tool_type == HedgeToolType.MIXED
        assert result.options_strategy == OptionsStrategy.COLLAR


class TestFallback:
    """降级测试。"""

    def test_options_fallback_to_futures(self, large_cap_risk: PortfolioRisk) -> None:
        # Arrange — 期权生成失败 (使用大盘Beta触发ETF_OPTIONS)
        mock_engine = MagicMock()
        mock_engine.generate_options_hedge.side_effect = Exception("期权不可用")
        mock_engine.generate_futures_hedge.return_value = {"IF": 1}
        selector = HedgeToolSelector(hedge_engine=mock_engine, config={})
        # Act
        result = selector.select_tools(MarketRegime.TAIL_EVENT, large_cap_risk, 0.40)
        # Assert
        assert result.tool_type == HedgeToolType.INDEX_FUTURES
        assert any("降级至期货" in f for f in result.fallback_flags)

    def test_no_engine_returns_empty_contracts(self, small_cap_risk: PortfolioRisk) -> None:
        # Arrange — 无对冲引擎
        selector = HedgeToolSelector(hedge_engine=None, config={})
        # Act
        result = selector.select_tools(MarketRegime.MILD, small_cap_risk, 0.15)
        # Assert
        assert result.tool_type == HedgeToolType.INDEX_FUTURES
        assert result.futures_contracts == {}


class TestReasoning:
    """决策推理测试。"""

    def test_reasoning_contains_regime_and_beta(self, selector: HedgeToolSelector, large_cap_risk: PortfolioRisk) -> None:
        # Arrange & Act
        result = selector.select_tools(MarketRegime.HIGH, large_cap_risk, 0.25)
        # Assert
        assert "HIGH" in result.reasoning
        assert "Beta" in result.reasoning