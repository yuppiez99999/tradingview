"""PnL Attribution Engine 单元测试.

被测模块: utils/pnl_attribution_engine.py
覆盖目标: >=90%

测试覆盖:
    1. dataclass: FactorContribution / AttributionResult
    2. 构造函数: 默认/自定义 risk_free_rate
    3. attribute() 主入口: 完整流程/空输入/仅 portfolio_returns
    4. Beta 计算: _calc_beta_pnl / _calc_beta (正常/短序列/零方差)
    5. Alpha 计算: _calc_alpha_pnl (正常/空基准/不等长)
    6. 风格因子归因: _calc_style_attribution (正常/空/归一化)
    7. 行业归因: _calc_sector_attribution (正常/空/未知行业)
    8. 风险指标: tracking_error / information_ratio / sharpe_ratio
    9. 异常检测: _detect_anomalies (8 类规则)
    10. 摘要生成: _build_summary
    11. save_report: 文件写入/异常
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.pnl_attribution_engine import (  # noqa: E402
    AttributionResult,
    FactorContribution,
    PnLAttributionEngine,
)


class TestFactorContribution:
    """FactorContribution dataclass 测试."""

    def test_creation_minimal(self):
        fc = FactorContribution(
            factor_name="momentum", contribution=100.0, contribution_pct=0.05
        )
        assert fc.factor_name == "momentum"
        assert fc.contribution == 100.0
        assert fc.contribution_pct == 0.05
        assert fc.exposure == 0.0
        assert fc.factor_return == 0.0
        assert fc.is_significant is False

    def test_creation_full(self):
        fc = FactorContribution(
            factor_name="tech",
            contribution=-500.0,
            contribution_pct=-0.2,
            exposure=0.3,
            factor_return=0.01,
            is_significant=True,
        )
        assert fc.exposure == 0.3
        assert fc.factor_return == 0.01
        assert fc.is_significant is True


class TestAttributionResult:
    """AttributionResult dataclass 测试."""

    def test_default_values(self):
        r = AttributionResult()
        assert r.total_pnl == 0.0
        assert r.alpha_pnl == 0.0
        assert r.style_factors == []
        assert r.sector_factors == []
        assert r.anomalies == []

    def test_to_dict(self):
        r = AttributionResult(total_pnl=1000.0, alpha_pnl=300.0)
        d = r.to_dict()
        assert d["total_pnl"] == 1000.0
        assert d["alpha_pnl"] == 300.0
        assert "style_factors" in d

    def test_to_dict_with_factors(self):
        fc = FactorContribution("momentum", 100.0, 0.1, is_significant=True)
        r = AttributionResult(style_factors=[fc])
        d = r.to_dict()
        assert len(d["style_factors"]) == 1
        assert d["style_factors"][0]["factor_name"] == "momentum"


class TestPnLAttributionEngineInit:
    """构造函数测试."""

    def test_default_init(self):
        engine = PnLAttributionEngine()
        assert engine.risk_free_rate == 0.025

    def test_custom_risk_free_rate(self):
        engine = PnLAttributionEngine(risk_free_rate=0.03)
        assert engine.risk_free_rate == 0.03

    def test_style_factors_constant(self):
        engine = PnLAttributionEngine()
        assert len(engine.STYLE_FACTORS) == 7
        assert "momentum" in engine.STYLE_FACTORS
        assert "valuation" in engine.STYLE_FACTORS

    def test_sectors_constant(self):
        engine = PnLAttributionEngine()
        assert len(engine.SECTORS) == 8
        assert "tech" in engine.SECTORS
        assert "healthcare" in engine.SECTORS


class TestCalcBeta:
    """Beta 计算测试."""

    def test_beta_normal(self):
        engine = PnLAttributionEngine()
        asset = [0.01, 0.02, -0.01, 0.005, 0.015]
        market = [0.008, 0.015, -0.005, 0.003, 0.012]
        beta = engine._calc_beta(asset, market)
        assert 0.5 < beta < 2.0

    def test_beta_short_sequence(self):
        engine = PnLAttributionEngine()
        beta = engine._calc_beta([0.01], [0.02])
        assert beta == 1.0

    def test_beta_empty(self):
        engine = PnLAttributionEngine()
        beta = engine._calc_beta([], [])
        assert beta == 1.0

    def test_beta_zero_variance(self):
        engine = PnLAttributionEngine()
        market = [0.01, 0.01, 0.01]
        asset = [0.02, 0.03, 0.01]
        beta = engine._calc_beta(asset, market)
        assert beta == 1.0

    def test_beta_pnl_no_market_returns(self):
        engine = PnLAttributionEngine()
        result = engine._calc_beta_pnl([], [0.01, 0.02], None, 1_000_000)
        assert result == 0.0

    def test_beta_pnl_no_portfolio_returns(self):
        engine = PnLAttributionEngine()
        result = engine._calc_beta_pnl([], [], [0.01, 0.02], 1_000_000)
        assert result == 0.0

    def test_beta_pnl_normal(self):
        engine = PnLAttributionEngine()
        portfolio = [0.01, 0.02, -0.005, 0.015, 0.008]
        market = [0.008, 0.015, -0.003, 0.012, 0.006]
        result = engine._calc_beta_pnl([], portfolio, market, 1_000_000)
        assert isinstance(result, float)


class TestCalcAlphaPnl:
    """Alpha PnL 计算测试."""

    def test_no_benchmark(self):
        engine = PnLAttributionEngine()
        result = engine._calc_alpha_pnl([], [0.01, 0.02], None, 1_000_000)
        assert result == 0.0

    def test_no_portfolio_returns(self):
        engine = PnLAttributionEngine()
        result = engine._calc_alpha_pnl([], [], [0.01, 0.02], 1_000_000)
        assert result == 0.0

    def test_equal_returns(self):
        engine = PnLAttributionEngine()
        returns = [0.01, 0.02, -0.005]
        result = engine._calc_alpha_pnl([], returns, returns, 1_000_000)
        assert abs(result) < 1e-6

    def test_outperformance(self):
        engine = PnLAttributionEngine()
        portfolio = [0.02, 0.03, 0.01]
        benchmark = [0.01, 0.01, 0.01]
        result = engine._calc_alpha_pnl([], portfolio, benchmark, 1_000_000)
        assert result > 0

    def test_underperformance(self):
        engine = PnLAttributionEngine()
        portfolio = [0.01, 0.01, 0.01]
        benchmark = [0.02, 0.03, 0.01]
        result = engine._calc_alpha_pnl([], portfolio, benchmark, 1_000_000)
        assert result < 0

    def test_unequal_length(self):
        engine = PnLAttributionEngine()
        portfolio = [0.02, 0.03, 0.01, 0.005]
        benchmark = [0.01, 0.01]
        result = engine._calc_alpha_pnl([], portfolio, benchmark, 1_000_000)
        assert isinstance(result, float)


class TestCalcStyleAttribution:
    """风格因子归因测试."""

    def test_no_factor_returns(self):
        engine = PnLAttributionEngine()
        pnl, factors = engine._calc_style_attribution([], None, 1_000_000)
        assert pnl == 0.0
        assert factors == []

    def test_empty_positions(self):
        engine = PnLAttributionEngine()
        factor_returns = {"momentum": [0.01, 0.02]}
        pnl, factors = engine._calc_style_attribution([], factor_returns, 1_000_000)
        assert pnl == 0.0
        assert len(factors) == 7

    def test_normal_attribution(self):
        engine = PnLAttributionEngine()
        positions = [
            {"weight": 0.5, "style_exposures": {"momentum": 0.8, "growth": 0.6}},
            {"weight": 0.5, "style_exposures": {"momentum": 0.4, "growth": 0.2}},
        ]
        factor_returns = {"momentum": [0.01, 0.02], "growth": [0.005, 0.01]}
        pnl, factors = engine._calc_style_attribution(
            positions, factor_returns, 1_000_000
        )
        assert len(factors) == 7
        momentum_fc = next(f for f in factors if f.factor_name == "momentum")
        assert momentum_fc.exposure > 0
        assert momentum_fc.contribution > 0

    def test_normalization(self):
        engine = PnLAttributionEngine()
        positions = [
            {"weight": 1.0, "style_exposures": {"momentum": 0.8}},
            {"weight": 1.0, "style_exposures": {"momentum": 0.4}},
        ]
        factor_returns = {"momentum": [0.01]}
        pnl, factors = engine._calc_style_attribution(
            positions, factor_returns, 1_000_000
        )
        momentum_fc = next(f for f in factors if f.factor_name == "momentum")
        assert abs(momentum_fc.exposure - 0.6) < 1e-6

    def test_significant_flag(self):
        engine = PnLAttributionEngine()
        positions = [{"weight": 1.0, "style_exposures": {"momentum": 1.0}}]
        factor_returns = {"momentum": [0.1]}
        pnl, factors = engine._calc_style_attribution(
            positions, factor_returns, 1_000_000
        )
        momentum_fc = next(f for f in factors if f.factor_name == "momentum")
        assert momentum_fc.is_significant is True


class TestCalcSectorAttribution:
    """行业归因测试."""

    def test_no_sector_returns(self):
        engine = PnLAttributionEngine()
        pnl, factors = engine._calc_sector_attribution([], None, 1_000_000)
        assert pnl == 0.0
        assert factors == []

    def test_normal_attribution(self):
        engine = PnLAttributionEngine()
        positions = [
            {"weight": 0.6, "sector": "tech"},
            {"weight": 0.4, "sector": "consumer"},
        ]
        sector_returns = {"tech": [0.02, 0.01], "consumer": [0.005, 0.003]}
        pnl, factors = engine._calc_sector_attribution(
            positions, sector_returns, 1_000_000
        )
        assert len(factors) == 2
        tech_fc = next(f for f in factors if f.factor_name == "tech")
        assert tech_fc.exposure == pytest.approx(0.6)
        assert tech_fc.contribution > 0

    def test_unknown_sector(self):
        engine = PnLAttributionEngine()
        positions = [{"weight": 1.0, "sector": "custom_sector"}]
        sector_returns = {"custom_sector": [0.01]}
        pnl, factors = engine._calc_sector_attribution(
            positions, sector_returns, 1_000_000
        )
        assert len(factors) == 1
        assert factors[0].factor_name == "custom_sector"

    def test_zero_weight_skipped(self):
        engine = PnLAttributionEngine()
        positions = [{"weight": 0.0, "sector": "tech"}]
        sector_returns = {"tech": [0.01]}
        pnl, factors = engine._calc_sector_attribution(
            positions, sector_returns, 1_000_000
        )
        assert factors == []

    def test_normalization(self):
        engine = PnLAttributionEngine()
        positions = [
            {"weight": 2.0, "sector": "tech"},
            {"weight": 2.0, "sector": "consumer"},
        ]
        sector_returns = {"tech": [0.01], "consumer": [0.01]}
        pnl, factors = engine._calc_sector_attribution(
            positions, sector_returns, 1_000_000
        )
        tech_fc = next(f for f in factors if f.factor_name == "tech")
        assert tech_fc.exposure == pytest.approx(0.5)


class TestRiskMetrics:
    """风险指标测试."""

    def test_tracking_error_no_benchmark(self):
        engine = PnLAttributionEngine()
        assert engine._calc_tracking_error([0.01, 0.02], None) == 0.0

    def test_tracking_error_short_sequence(self):
        engine = PnLAttributionEngine()
        assert engine._calc_tracking_error([0.01], [0.02]) == 0.0

    def test_tracking_error_normal(self):
        engine = PnLAttributionEngine()
        portfolio = [0.01, 0.02, -0.005, 0.015, 0.008, -0.003]
        benchmark = [0.008, 0.015, -0.003, 0.012, 0.006, -0.001]
        te = engine._calc_tracking_error(portfolio, benchmark)
        assert te > 0

    def test_information_ratio_zero_te(self):
        engine = PnLAttributionEngine()
        assert engine._calc_information_ratio([0.01], [0.02]) == 0.0

    def test_information_ratio_normal(self):
        engine = PnLAttributionEngine()
        portfolio = [0.02, 0.03, 0.01, 0.025, 0.015, 0.02]
        benchmark = [0.01, 0.01, 0.01, 0.01, 0.01, 0.01]
        ir = engine._calc_information_ratio(portfolio, benchmark)
        assert ir > 0

    def test_sharpe_ratio_short(self):
        engine = PnLAttributionEngine()
        assert engine._calc_sharpe_ratio([0.01]) == 0.0

    def test_sharpe_ratio_zero_variance(self):
        engine = PnLAttributionEngine()
        assert engine._calc_sharpe_ratio([0.01, 0.01, 0.01]) == 0.0

    def test_sharpe_ratio_normal(self):
        engine = PnLAttributionEngine()
        returns = [0.01, 0.02, -0.005, 0.015, 0.008, -0.003, 0.012, -0.002]
        sharpe = engine._calc_sharpe_ratio(returns)
        assert isinstance(sharpe, float)


class TestDetectAnomalies:
    """异常检测测试."""

    def test_no_anomalies(self):
        engine = PnLAttributionEngine()
        result = AttributionResult(total_pnl=10000.0, alpha_pnl=3000.0, beta_pnl=5000.0)
        anomalies = engine._detect_anomalies(result)
        assert isinstance(anomalies, list)

    def test_alpha_negative_anomaly(self):
        engine = PnLAttributionEngine()
        result = AttributionResult(total_pnl=10000.0, alpha_pnl=-500.0)
        anomalies = engine._detect_anomalies(result)
        assert any("Alpha" in a for a in anomalies)

    def test_beta_too_high_anomaly(self):
        engine = PnLAttributionEngine()
        result = AttributionResult(total_pnl=10000.0, alpha_pnl=1000.0, beta_pnl=8000.0)
        anomalies = engine._detect_anomalies(result)
        assert any("Beta" in a for a in anomalies)

    def test_style_factor_too_large(self):
        engine = PnLAttributionEngine()
        fc = FactorContribution("momentum", 5000.0, 0.5, is_significant=True)
        result = AttributionResult(total_pnl=10000.0, style_factors=[fc])
        anomalies = engine._detect_anomalies(result)
        assert any("momentum" in a for a in anomalies)

    def test_sector_too_large(self):
        engine = PnLAttributionEngine()
        sc = FactorContribution("tech", 5000.0, 0.5, is_significant=True)
        result = AttributionResult(total_pnl=10000.0, sector_factors=[sc])
        anomalies = engine._detect_anomalies(result)
        assert any("tech" in a for a in anomalies)

    def test_trading_cost_too_high(self):
        engine = PnLAttributionEngine()
        result = AttributionResult(total_pnl=10000.0, trading_cost=-3000.0)
        anomalies = engine._detect_anomalies(result)
        assert any("交易成本" in a for a in anomalies)

    def test_timing_anomaly(self):
        engine = PnLAttributionEngine()
        result = AttributionResult(total_pnl=10000.0, timing_pnl=5000.0)
        anomalies = engine._detect_anomalies(result)
        assert any("择时" in a for a in anomalies)

    def test_ir_negative_anomaly(self):
        engine = PnLAttributionEngine()
        result = AttributionResult(information_ratio=-0.8)
        anomalies = engine._detect_anomalies(result)
        assert any("信息比率" in a for a in anomalies)

    def test_tracking_error_too_high(self):
        engine = PnLAttributionEngine()
        result = AttributionResult(tracking_error=0.2)
        anomalies = engine._detect_anomalies(result)
        assert any("跟踪误差" in a for a in anomalies)

    def test_zero_total_pnl(self):
        engine = PnLAttributionEngine()
        result = AttributionResult(total_pnl=0.0)
        anomalies = engine._detect_anomalies(result)
        assert isinstance(anomalies, list)


class TestBuildSummary:
    """摘要生成测试."""

    def test_basic_summary(self):
        engine = PnLAttributionEngine()
        result = AttributionResult(total_pnl=10000.0, total_return_pct=0.05)
        summary = engine._build_summary(result)
        assert "P&L 归因摘要" in summary
        assert "10,000" in summary

    def test_summary_with_factors(self):
        engine = PnLAttributionEngine()
        fc = FactorContribution("momentum", 2000.0, 0.2, is_significant=True)
        result = AttributionResult(total_pnl=10000.0, style_factors=[fc])
        summary = engine._build_summary(result)
        assert "风格因子明细" in summary
        assert "momentum" in summary

    def test_summary_with_anomalies(self):
        engine = PnLAttributionEngine()
        result = AttributionResult(total_pnl=10000.0, anomalies=["测试异常"])
        summary = engine._build_summary(result)
        assert "异常预警" in summary
        assert "测试异常" in summary


class TestSaveReport:
    """保存报告测试."""

    def test_save_report_success(self, tmp_path):
        engine = PnLAttributionEngine()
        result = AttributionResult(attribution_date="2026-08-14", total_pnl=1000.0)
        with patch("utils.pnl_attribution_engine.REPORT_DIR", tmp_path):
            path = engine.save_report(result)
            assert path.exists()
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["total_pnl"] == 1000.0

    def test_save_report_with_factors(self, tmp_path):
        engine = PnLAttributionEngine()
        fc = FactorContribution("momentum", 100.0, 0.1)
        result = AttributionResult(attribution_date="2026-08-14", style_factors=[fc])
        with patch("utils.pnl_attribution_engine.REPORT_DIR", tmp_path):
            path = engine.save_report(result)
            data = json.loads(path.read_text(encoding="utf-8"))
            assert len(data["style_factors"]) == 1


class TestAttributeMain:
    """attribute() 主入口测试."""

    def test_empty_portfolio_returns(self):
        engine = PnLAttributionEngine()
        result = engine.attribute(positions=[], portfolio_returns=[])
        assert result.total_pnl == 0.0
        assert result.total_return_pct == 0.0

    def test_simple_returns(self):
        engine = PnLAttributionEngine()
        positions = [{"weight": 1.0, "market_value": 1_000_000, "sector": "tech"}]
        result = engine.attribute(
            positions=positions, portfolio_returns=[0.01, 0.02, -0.005]
        )
        assert result.total_pnl > 0
        assert result.total_return_pct > 0

    def test_full_attribution(self):
        engine = PnLAttributionEngine()
        positions = [
            {
                "code": "300308",
                "weight": 0.15,
                "sector": "tech",
                "style_exposures": {"momentum": 0.8, "growth": 0.7},
                "market_value": 150_000,
            },
            {
                "code": "600519",
                "weight": 0.10,
                "sector": "consumer",
                "style_exposures": {"earnings_quality": 0.9},
                "market_value": 100_000,
            },
        ]
        portfolio_returns = [0.01, 0.02, -0.005, 0.015, 0.008]
        benchmark_returns = [0.008, 0.015, -0.003, 0.012, 0.006]
        market_returns = [0.009, 0.014, -0.004, 0.013, 0.007]
        factor_returns = {"momentum": [0.005, 0.008, -0.002, 0.006, 0.003]}
        sector_returns = {"tech": [0.012, 0.018, -0.005, 0.016, 0.009]}

        result = engine.attribute(
            positions=positions,
            portfolio_returns=portfolio_returns,
            benchmark_returns=benchmark_returns,
            market_returns=market_returns,
            factor_returns=factor_returns,
            sector_returns=sector_returns,
            trading_costs=1000.0,
            funding_cost=-50.0,
            hedge_pnl=-200.0,
        )

        assert result.total_pnl > 0
        assert result.trading_cost == -1000.0
        assert result.funding_cost == -50.0
        assert result.hedge_pnl == -200.0
        assert len(result.style_factors) == 7
        assert isinstance(result.summary, str)
        assert isinstance(result.anomalies, list)

    def test_with_attribution_date(self):
        engine = PnLAttributionEngine()
        result = engine.attribute(
            positions=[],
            portfolio_returns=[0.01],
            attribution_date="2026-01-01",
        )
        assert result.attribution_date == "2026-01-01"

    def test_portfolio_value_from_amount(self):
        engine = PnLAttributionEngine()
        positions = [{"amount": 500_000}]
        result = engine.attribute(positions=positions, portfolio_returns=[0.01])
        assert result.total_pnl == pytest.approx(500_000 * 0.01)

    def test_portfolio_value_default(self):
        engine = PnLAttributionEngine()
        result = engine.attribute(positions=[], portfolio_returns=[0.01])
        assert result.total_pnl == pytest.approx(1_000_000 * 0.01)

    def test_timing_pnl_residual(self):
        engine = PnLAttributionEngine()
        positions = [{"weight": 1.0, "market_value": 1_000_000, "sector": "tech"}]
        result = engine.attribute(
            positions=positions,
            portfolio_returns=[0.01, 0.02],
            trading_costs=500.0,
            hedge_pnl=-100.0,
            funding_cost=-50.0,
        )
        explained = (
            result.alpha_pnl
            + result.beta_pnl
            + result.style_pnl
            + result.sector_pnl
            + result.hedge_pnl
            + result.trading_cost
            + result.funding_cost
            + result.timing_pnl
        )
        assert abs(explained - result.total_pnl) < 1e-6
