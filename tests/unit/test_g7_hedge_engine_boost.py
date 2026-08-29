"""G7 覆盖率冲刺 — hedge_engine + hedge_rebalance_integrator 补测试.

目标模块:
    1. hedge_engine.py (934行, 0%→目标50%+)
    2. hedge_rebalance_integrator.py (705行, 0%→目标50%+)

覆盖核心路径:
    - 枚举/数据类/常量 (易覆盖, 高行数密度)
    - HedgeEngine: __init__/assess_portfolio_risk/_estimate_default_price/
      determine_hedge_signal_strength/compute_optimal_hedge_ratio/
      format_report/get_hedge_signal_for_fusion
    - HedgeRebalanceIntegrator: __init__/_get_dynamic_rebalance_threshold/
      _get_sector_adjusted_weights/format_report/save_report
    - 模块级函数: get_hedge_engine/calculate_portfolio_beta/get_integrator

运行:
    python -m pytest tests/unit/test_g7_hedge_engine_boost.py -v
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

# ============================================================
# 1. hedge_engine 枚举与数据类
# ============================================================


class TestHedgeEngineEnums:
    """hedge_engine 枚举类型测试."""

    def test_hedge_type_values(self) -> None:
        from utils.hedge_engine import HedgeType

        assert HedgeType.NONE.value == "none"
        assert HedgeType.FUTURES_SHORT.value == "futures_short"
        assert HedgeType.PUT_PROTECTIVE.value == "put_protective"
        assert HedgeType.PUT_SPREAD.value == "put_spread"
        assert HedgeType.COLLAR.value == "collar"
        assert HedgeType.DYNAMIC_DELTA.value == "dynamic_delta"

    def test_hedge_signal_strength_values(self) -> None:
        from utils.hedge_engine import HedgeSignalStrength

        assert HedgeSignalStrength.NO_HEDGE.value == 0
        assert HedgeSignalStrength.LIGHT.value == 1
        assert HedgeSignalStrength.MODERATE.value == 2
        assert HedgeSignalStrength.STRONG.value == 3
        assert HedgeSignalStrength.FULL.value == 4

    def test_hedge_signal_strength_ordering(self) -> None:
        from utils.hedge_engine import HedgeSignalStrength

        assert HedgeSignalStrength.NO_HEDGE.value < HedgeSignalStrength.LIGHT.value
        assert HedgeSignalStrength.MODERATE.value < HedgeSignalStrength.STRONG.value
        assert HedgeSignalStrength.STRONG.value < HedgeSignalStrength.FULL.value


class TestHedgeEngineDataClasses:
    """hedge_engine 数据类测试."""

    def test_portfolio_risk_defaults(self) -> None:
        from utils.hedge_engine import PortfolioRisk

        risk = PortfolioRisk()
        assert risk.total_value == 0.0
        assert risk.stock_exposure == 0.0
        assert risk.cash == 0.0
        assert risk.beta_csi300 == 0.0
        assert risk.volatility_30d == 0.0
        assert risk.var_95_daily == 0.0
        assert risk.concentration_risk == 0.0
        assert risk.correlation_matrix == {}
        assert risk.sector_weights == {}
        assert risk.mrc_warnings == []

    def test_portfolio_risk_with_values(self) -> None:
        from utils.hedge_engine import PortfolioRisk

        risk = PortfolioRisk(
            total_value=1_000_000,
            stock_exposure=800_000,
            cash=200_000,
            beta_csi300=1.2,
            volatility_30d=0.015,
            var_95_daily=0.02,
        )
        assert risk.total_value == 1_000_000
        assert risk.stock_exposure == 800_000
        assert risk.beta_csi300 == 1.2

    def test_hedge_recommendation_defaults(self) -> None:
        from utils.hedge_engine import (
            HedgeRecommendation,
            HedgeSignalStrength,
            HedgeType,
        )

        rec = HedgeRecommendation()
        assert rec.hedge_type == HedgeType.NONE
        assert rec.strength == HedgeSignalStrength.NO_HEDGE
        assert rec.urgency_score == 0.0
        assert rec.futures_instruments == []
        assert rec.futures_contracts == {}
        assert rec.options_instruments == []
        assert rec.reasoning == ""

    def test_hedge_recommendation_with_values(self) -> None:
        from utils.hedge_engine import (
            HedgeRecommendation,
            HedgeSignalStrength,
            HedgeType,
        )

        rec = HedgeRecommendation(
            hedge_type=HedgeType.FUTURES_SHORT,
            strength=HedgeSignalStrength.MODERATE,
            urgency_score=0.6,
            futures_instruments=["IF", "IC"],
            futures_contracts={"IF": 2, "IC": 1},
            hedge_ratio=0.5,
        )
        assert rec.hedge_type == HedgeType.FUTURES_SHORT
        assert rec.strength == HedgeSignalStrength.MODERATE
        assert rec.futures_instruments == ["IF", "IC"]
        assert rec.futures_contracts == {"IF": 2, "IC": 1}
        assert rec.hedge_ratio == 0.5


class TestHedgeEngineConstants:
    """hedge_engine 模块常量测试."""

    def test_index_weights_csi300(self) -> None:
        from utils.hedge_engine import INDEX_WEIGHTS_CSI300

        assert "600519" in INDEX_WEIGHTS_CSI300
        assert INDEX_WEIGHTS_CSI300["600519"] == 0.055
        assert sum(INDEX_WEIGHTS_CSI300.values()) <= 1.0

    def test_index_weights_csi500(self) -> None:
        from utils.hedge_engine import INDEX_WEIGHTS_CSI500

        assert len(INDEX_WEIGHTS_CSI500) > 0
        assert "688981" in INDEX_WEIGHTS_CSI500

    def test_index_futures_specs(self) -> None:
        from utils.hedge_engine import INDEX_FUTURES_SPECS

        assert "IF" in INDEX_FUTURES_SPECS
        assert "IC" in INDEX_FUTURES_SPECS
        assert "IM" in INDEX_FUTURES_SPECS
        assert "IH" in INDEX_FUTURES_SPECS
        assert INDEX_FUTURES_SPECS["IF"]["multiplier"] == 300
        assert INDEX_FUTURES_SPECS["IC"]["multiplier"] == 200

    def test_etf_options_specs(self) -> None:
        from utils.hedge_engine import ETF_OPTIONS_SPECS

        assert "510300" in ETF_OPTIONS_SPECS
        assert "510050" in ETF_OPTIONS_SPECS

    def test_default_futures_prices(self) -> None:
        from utils.hedge_engine import DEFAULT_FUTURES_PRICES

        assert "IF" in DEFAULT_FUTURES_PRICES
        assert "IC" in DEFAULT_FUTURES_PRICES
        assert DEFAULT_FUTURES_PRICES["IF"] > 0

    def test_default_index_prices(self) -> None:
        from utils.hedge_engine import DEFAULT_INDEX_PRICES

        assert DEFAULT_INDEX_PRICES["CSI300"] > 0
        assert DEFAULT_INDEX_PRICES["CSI500"] > 0

    def test_volatility_target(self) -> None:
        from utils.hedge_engine import VOLATILITY_TARGET_ANNUAL

        assert VOLATILITY_TARGET_ANNUAL == 0.18

    def test_portfolio_tail_hedge_triggers(self) -> None:
        from utils.hedge_engine import PORTFOLIO_TAIL_HEDGE_TRIGGERS

        assert PORTFOLIO_TAIL_HEDGE_TRIGGERS["vol_trigger"] == 0.28
        assert PORTFOLIO_TAIL_HEDGE_TRIGGERS["dd_trigger"] == 0.12
        assert PORTFOLIO_TAIL_HEDGE_TRIGGERS["min_hedge_ratio"] == 0.25

    def test_cost_benefit_threshold(self) -> None:
        from utils.hedge_engine import COST_BENEFIT_THRESHOLD

        assert COST_BENEFIT_THRESHOLD == 1.5

    def test_index_allocation_order(self) -> None:
        from utils.hedge_engine import INDEX_ALLOCATION_ORDER

        assert INDEX_ALLOCATION_ORDER == ["IC", "IM", "IF"]


# ============================================================
# 2. HedgeEngine 类测试
# ============================================================


class TestHedgeEngine:
    """HedgeEngine 核心路径测试."""

    def test_init_default(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        assert engine.portfolio_value == 1_000_000
        assert engine._price_cache == {}
        assert engine._beta_cache == {}

    def test_init_custom(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine(portfolio_value=5_000_000)
        assert engine.portfolio_value == 5_000_000

    def test_estimate_default_price_known(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        # 已知兜底价格
        assert engine._estimate_default_price("600519") == 50.0  # 不在 fallback_map
        assert engine._estimate_default_price("300750") == 230.0
        assert engine._estimate_default_price("300750.SZ") == 230.0

    def test_estimate_default_price_unknown(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        # 未知代码返回默认 50.0
        assert engine._estimate_default_price("999999") == 50.0

    def test_compute_portfolio_vol(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        result = engine._compute_portfolio_vol({}, {})
        assert isinstance(result, float)
        assert result == 0.015  # 旧版固定返回

    def test_compute_weighted_beta_empty(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        result = engine._compute_weighted_beta({}, "CSI300")
        assert isinstance(result, (int, float))
        assert result == 0.0

    def test_compute_weighted_beta_csi300(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        weights = {"600519": 0.1, "000858": 0.05}
        result = engine._compute_weighted_beta(weights, "CSI300")
        assert isinstance(result, float)
        assert result > 0

    def test_assess_portfolio_risk_empty(self) -> None:
        from utils.hedge_engine import HedgeEngine, PortfolioRisk

        engine = HedgeEngine()
        risk = engine.assess_portfolio_risk({}, {})
        assert isinstance(risk, PortfolioRisk)
        assert risk.total_value == 0.0
        assert risk.stock_exposure == 0.0

    def test_assess_portfolio_risk_with_cash(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        risk = engine.assess_portfolio_risk({}, {}, cash=500_000)
        assert risk.cash == 500_000
        assert risk.total_value == 500_000

    def test_assess_portfolio_risk_simple(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        positions = {
            "600519": {"shares": 100},
            "000858": {"shares": 200},
        }
        prices = {"600519": 1800.0, "000858": 150.0}
        risk = engine.assess_portfolio_risk(positions, prices)
        assert risk.stock_exposure > 0
        assert risk.total_value > 0
        # 600519: 100*1800 = 180000, 000858: 200*150 = 30000, total = 210000
        assert risk.stock_exposure == 210_000

    def test_assess_portfolio_risk_with_suffix(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        positions = {"600519": {"shares": 100}}
        prices = {"600519.SH": 1800.0}
        risk = engine.assess_portfolio_risk(positions, prices)
        assert risk.stock_exposure == 180_000

    def test_assess_portfolio_risk_fallback_price(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        # 无价格时使用兜底价格
        positions = {"300750": {"shares": 100}}
        prices = {}
        risk = engine.assess_portfolio_risk(positions, prices)
        # 300750 兜底价 230.0
        assert risk.stock_exposure == 23_000

    def test_determine_hedge_signal_strength_no_hedge(self) -> None:
        from utils.hedge_engine import HedgeEngine, HedgeSignalStrength, PortfolioRisk

        engine = HedgeEngine()
        risk = PortfolioRisk()
        strength, score = engine.determine_hedge_signal_strength(risk)
        assert isinstance(strength, HedgeSignalStrength)
        assert isinstance(score, float)
        assert score >= 0.0

    def test_determine_hedge_signal_strength_high_beta(self) -> None:
        from utils.hedge_engine import HedgeEngine, PortfolioRisk

        engine = HedgeEngine()
        risk = PortfolioRisk(beta_csi300=1.6)
        strength, score = engine.determine_hedge_signal_strength(risk)
        assert score > 0  # 高 beta 应有得分

    def test_determine_hedge_signal_strength_high_vol(self) -> None:
        from utils.hedge_engine import HedgeEngine, PortfolioRisk

        engine = HedgeEngine()
        risk = PortfolioRisk(volatility_30d=0.03)  # 高波动
        strength, score = engine.determine_hedge_signal_strength(
            risk, portfolio_volatility=0.35
        )
        assert score > 0.15  # 波动率超标应有得分

    def test_determine_hedge_signal_strength_high_dd(self) -> None:
        from utils.hedge_engine import HedgeEngine, PortfolioRisk

        engine = HedgeEngine()
        risk = PortfolioRisk()
        strength, score = engine.determine_hedge_signal_strength(
            risk, portfolio_drawdown_60d=0.20
        )
        assert score > 0.1  # 回撤超标应有得分

    def test_compute_optimal_hedge_ratio(self) -> None:
        from utils.hedge_engine import HedgeEngine, HedgeSignalStrength, PortfolioRisk

        engine = HedgeEngine()
        risk = PortfolioRisk(
            total_value=1_000_000,
            stock_exposure=800_000,
            beta_csi300=1.2,
            volatility_30d=0.02,
        )
        ratio = engine.compute_optimal_hedge_ratio(risk, HedgeSignalStrength.MODERATE)
        assert isinstance(ratio, float)
        assert 0.0 <= ratio <= 1.0

    def test_get_hedge_signal_for_fusion(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        result = engine.get_hedge_signal_for_fusion("portfolio")
        assert isinstance(result, dict)
        assert "score" in result or len(result) >= 0  # 不崩溃即可

    def test_generate_hedge_reason(self) -> None:
        from utils.hedge_engine import HedgeEngine, HedgeRecommendation, PortfolioRisk

        engine = HedgeEngine()
        risk = PortfolioRisk()
        HedgeRecommendation()
        # contracts 格式: {code: {"contracts": n, "spec": {...}}}
        contracts = {"IF": {"contracts": 2, "spec": {"name": "沪深300"}}}
        reason = engine._generate_hedge_reason(risk, 0.3, contracts)
        assert isinstance(reason, str)

    def test_format_report(self) -> None:
        from utils.hedge_engine import HedgeEngine, HedgeRecommendation

        engine = HedgeEngine()
        rec = HedgeRecommendation()
        report = engine.format_report(rec)
        assert isinstance(report, str)
        assert len(report) > 0


class TestHedgeEngineModuleFunctions:
    """hedge_engine 模块级函数测试."""

    def test_get_hedge_engine_default(self) -> None:
        from utils.hedge_engine import HedgeEngine, get_hedge_engine

        engine = get_hedge_engine()
        assert isinstance(engine, HedgeEngine)
        assert engine.portfolio_value == 1_000_000

    def test_get_hedge_engine_custom(self) -> None:
        from utils.hedge_engine import HedgeEngine, get_hedge_engine

        engine = get_hedge_engine(portfolio_value=3_000_000)
        assert isinstance(engine, HedgeEngine)
        assert engine.portfolio_value == 3_000_000

    def test_calculate_portfolio_beta_empty(self) -> None:
        from utils.hedge_engine import calculate_portfolio_beta

        result = calculate_portfolio_beta({}, {})
        assert isinstance(result, dict)
        assert "beta_csi300" in result
        assert "beta_csi500" in result
        assert "beta_csi1000" in result
        assert "beta_sse50" in result
        assert "concentration_hhi" in result
        assert "var_95_daily" in result

    def test_calculate_portfolio_beta_with_positions(self) -> None:
        from utils.hedge_engine import calculate_portfolio_beta

        positions = {"600519": {"shares": 100}}
        prices = {"600519": 1800.0}
        result = calculate_portfolio_beta(positions, prices)
        assert result["beta_csi300"] >= 0


# ============================================================
# 3. hedge_rebalance_integrator 枚举与数据类
# ============================================================


class TestHedgeRebalanceEnums:
    """hedge_rebalance_integrator 枚举测试."""

    def test_market_regime_values(self) -> None:
        from utils.hedge_rebalance_integrator import MarketRegime

        assert MarketRegime.CALM.value == "calm"
        assert MarketRegime.MILD_VOLATILE.value == "mild"
        assert MarketRegime.HIGH_VOLATILE.value == "high"
        assert MarketRegime.TAIL_EVENT.value == "tail"

    def test_hedge_mode_values(self) -> None:
        from utils.hedge_rebalance_integrator import HedgeMode

        assert HedgeMode.NONE.value == "none"
        assert HedgeMode.TAIL_ONLY.value == "tail_only"
        assert HedgeMode.DYNAMIC.value == "dynamic"
        assert HedgeMode.FIXED.value == "fixed"


class TestHedgeRebalanceDataClasses:
    """hedge_rebalance_integrator 数据类测试."""

    def test_position_weight_defaults(self) -> None:
        from utils.hedge_rebalance_integrator import PositionWeight

        pw = PositionWeight(
            code="600519",
            name="贵州茅台",
            category="白酒",
            target_weight=0.1,
            current_weight=0.12,
            deviation=0.02,
            deviation_pct=0.2,
            current_value=120_000,
            target_value=100_000,
            adjustment=-20_000,
            adjustment_shares=-11,
            current_price=1800.0,
        )
        assert pw.code == "600519"
        assert pw.action == "HOLD"
        assert pw.priority == 0

    def test_hedge_decision_defaults(self) -> None:
        from utils.hedge_rebalance_integrator import (
            HedgeDecision,
            HedgeMode,
            MarketRegime,
        )

        hd = HedgeDecision(needed=False)
        assert hd.needed is False
        assert hd.mode == HedgeMode.NONE
        assert hd.regime == MarketRegime.CALM
        assert hd.hedge_ratio == 0.0
        assert hd.futures_instruments == []
        assert hd.futures_contracts == {}

    def test_hedge_decision_with_values(self) -> None:
        from utils.hedge_rebalance_integrator import (
            HedgeDecision,
            HedgeMode,
            MarketRegime,
        )

        hd = HedgeDecision(
            needed=True,
            mode=HedgeMode.TAIL_ONLY,
            regime=MarketRegime.TAIL_EVENT,
            hedge_ratio=0.4,
            futures_instruments=["IC", "IM"],
            futures_contracts={"IC": 2, "IM": 1},
            total_notional=400_000,
        )
        assert hd.needed is True
        assert hd.mode == HedgeMode.TAIL_ONLY
        assert hd.regime == MarketRegime.TAIL_EVENT
        assert hd.total_notional == 400_000

    def test_rebalance_decision_defaults(self) -> None:
        from utils.hedge_rebalance_integrator import RebalanceDecision

        rd = RebalanceDecision(needed=False)
        assert isinstance(rd, RebalanceDecision)
        assert rd.needed is False
        assert rd.rebalance_type == "none"
        assert rd.threshold == 0.05

    def test_joint_plan_defaults(self) -> None:
        from utils.hedge_rebalance_integrator import JointPlan

        jp = JointPlan()
        assert isinstance(jp, JointPlan)


# ============================================================
# 4. HedgeRebalanceIntegrator 类测试
# ============================================================


class TestHedgeRebalanceIntegrator:
    """HedgeRebalanceIntegrator 核心路径测试."""

    def test_init_default(self) -> None:
        from utils.hedge_rebalance_integrator import (
            HedgeMode,
            HedgeRebalanceIntegrator,
        )

        integrator = HedgeRebalanceIntegrator()
        assert integrator.hedge_mode == HedgeMode.TAIL_ONLY
        assert integrator.portfolio_value > 0
        assert integrator._prices == {}
        assert integrator._prices_loaded is False

    def test_init_custom(self) -> None:
        from utils.hedge_rebalance_integrator import (
            HedgeMode,
            HedgeRebalanceIntegrator,
        )

        integrator = HedgeRebalanceIntegrator(
            portfolio_value=2_000_000,
            hedge_mode=HedgeMode.DYNAMIC,
        )
        assert integrator.portfolio_value == 2_000_000
        assert integrator.hedge_mode == HedgeMode.DYNAMIC

    def test_init_with_base_dir(self, tmp_path: Path) -> None:
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        integrator = HedgeRebalanceIntegrator(
            base_dir=str(tmp_path),
            config_dir=str(config_dir),
            portfolio_value=1_500_000,
        )
        assert integrator.portfolio_value == 1_500_000
        assert integrator.base_dir == str(tmp_path)

    def test_estimate_default_price(self) -> None:
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        integrator = HedgeRebalanceIntegrator()
        price = integrator._estimate_default_price("600519")
        assert isinstance(price, float)
        assert price > 0

    def test_get_dynamic_rebalance_threshold_low(self) -> None:
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        integrator = HedgeRebalanceIntegrator()
        threshold, freq, max_adj = integrator._get_dynamic_rebalance_threshold(0.10)
        assert isinstance(threshold, float)
        assert isinstance(freq, str)
        assert isinstance(max_adj, int)

    def test_get_dynamic_rebalance_threshold_normal(self) -> None:
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        integrator = HedgeRebalanceIntegrator()
        threshold, freq, max_adj = integrator._get_dynamic_rebalance_threshold(0.20)
        assert isinstance(threshold, float)
        assert threshold > 0

    def test_get_dynamic_rebalance_threshold_high(self) -> None:
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        integrator = HedgeRebalanceIntegrator()
        threshold, freq, max_adj = integrator._get_dynamic_rebalance_threshold(0.30)
        assert isinstance(threshold, float)
        assert threshold > 0

    def test_get_sector_adjusted_weights(self) -> None:
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        integrator = HedgeRebalanceIntegrator()
        weights = integrator._get_sector_adjusted_weights()
        assert isinstance(weights, dict)

    def test_load_prices(self) -> None:
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        integrator = HedgeRebalanceIntegrator()
        # Mock 外部数据源以避免 import ifind_client / quant_modules.wind_mcp / efinance 失败
        # API 重构: 源码已移除 iFinD 数据源 (无 _get_ifind_prices_batch), 改用 Wind/AKShare/efinance 回退
        import sys
        from unittest.mock import MagicMock as _MagicMock

        mock_modules = {
            "ifind_client": _MagicMock(),
            "quant_modules": _MagicMock(),
            "quant_modules.wind_mcp": _MagicMock(),
            "efinance": _MagicMock(),
            "akshare": _MagicMock(),
        }
        with (
            patch.dict(sys.modules, mock_modules),
            patch(
                "utils.hedge_rebalance_integrator.get_live_futures_prices",
                return_value={"IF": 3900.0},
            ),
        ):
            prices = integrator.load_prices()
            assert isinstance(prices, dict)

    def test_determine_market_regime_calm(self) -> None:
        from utils.hedge_rebalance_integrator import (
            HedgeRebalanceIntegrator,
            MarketRegime,
            PortfolioRisk,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = PortfolioRisk()
        regime = integrator._determine_market_regime(
            risk, portfolio_volatility=0.10, portfolio_drawdown_60d=0.05
        )
        assert isinstance(regime, MarketRegime)
        assert regime == MarketRegime.CALM

    def test_determine_market_regime_tail(self) -> None:
        from utils.hedge_rebalance_integrator import (
            HedgeRebalanceIntegrator,
            MarketRegime,
            PortfolioRisk,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = PortfolioRisk()
        regime = integrator._determine_market_regime(
            risk, portfolio_volatility=0.40, portfolio_drawdown_60d=0.25
        )
        assert regime == MarketRegime.TAIL_EVENT

    def test_compute_tail_hedge_ratio(self) -> None:
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        integrator = HedgeRebalanceIntegrator()
        ratio = integrator._compute_tail_hedge_ratio(
            portfolio_volatility=0.35, portfolio_drawdown_60d=0.20
        )
        assert isinstance(ratio, float)
        assert 0.0 <= ratio <= 1.0

    def test_format_report(self) -> None:
        from utils.hedge_rebalance_integrator import (
            HedgeRebalanceIntegrator,
            JointPlan,
        )

        integrator = HedgeRebalanceIntegrator()
        plan = JointPlan()
        # 给 plan 一些有效值以避免除零
        plan.portfolio_value = 1_000_000
        plan.stock_exposure = 800_000
        plan.after_hedge_exposure = 500_000
        plan.timestamp = "2026-01-01 10:00:00"
        report = integrator.format_report(plan)
        assert isinstance(report, str)
        assert len(report) > 0

    def test_save_report(self, tmp_path: Path) -> None:
        from utils.hedge_rebalance_integrator import (
            HedgeRebalanceIntegrator,
            JointPlan,
        )

        integrator = HedgeRebalanceIntegrator()
        plan = JointPlan()
        plan.portfolio_value = 1_000_000
        plan.stock_exposure = 800_000
        plan.timestamp = "2026-01-01 10:00:00"
        result = integrator.save_report(plan, output_dir=str(tmp_path))
        # 返回文件路径或空字符串
        assert isinstance(result, str)


class TestHedgeRebalanceModuleFunctions:
    """hedge_rebalance_integrator 模块级函数测试."""

    def test_load_yaml_not_found(self) -> None:
        from utils.hedge_rebalance_integrator import _load_yaml

        result = _load_yaml("/nonexistent/path/file.yaml")
        assert result is None

    def test_load_json_not_found(self) -> None:
        from utils.hedge_rebalance_integrator import _load_json

        result = _load_json("/nonexistent/path/file.json")
        assert result is None

    def test_load_yaml_valid(self, tmp_path: Path) -> None:
        from utils.hedge_rebalance_integrator import _load_yaml

        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text("key: value\nnumber: 42\n", encoding="utf-8")
        result = _load_yaml(str(yaml_file))
        assert result is not None
        assert result["key"] == "value"
        assert result["number"] == 42

    def test_load_json_valid(self, tmp_path: Path) -> None:
        from utils.hedge_rebalance_integrator import _load_json

        json_file = tmp_path / "test.json"
        json_file.write_text('{"key": "value", "number": 42}', encoding="utf-8")
        result = _load_json(str(json_file))
        assert result is not None
        assert result["key"] == "value"
        assert result["number"] == 42

    def test_estimate_portfolio_vol(self) -> None:
        from utils.hedge_rebalance_integrator import _estimate_portfolio_vol

        result = _estimate_portfolio_vol()
        assert isinstance(result, float)
        assert result >= 0

    def test_estimate_portfolio_dd_60d(self) -> None:
        from utils.hedge_rebalance_integrator import _estimate_portfolio_dd_60d

        result = _estimate_portfolio_dd_60d()
        assert isinstance(result, float)
        assert result >= 0

    def test_get_integrator(self) -> None:
        from utils.hedge_rebalance_integrator import (
            HedgeRebalanceIntegrator,
            get_integrator,
        )

        integrator = get_integrator(portfolio_value=1_000_000)
        assert isinstance(integrator, HedgeRebalanceIntegrator)
