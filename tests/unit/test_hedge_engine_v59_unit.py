# -*- coding: utf-8 -*-
"""test_hedge_engine_v59_unit.py — HedgeEngine v5.9 门面类单元测试

C-1.4 测试任务 (2026-08-01): 为 hedge_engine_v59.py 补单元测试覆盖。

背景:
    hedge_engine_v59.py 经 B3.3 重构后, 大部分方法已委托到:
      - risk/portfolio_risk_assessor.py (风险评估层)
      - hedging/hedge_strategy_executor.py (对冲策略执行层)
    本测试聚焦门面层职责:
      1. 委托完整性 — 验证每个委托方法正确转发参数与返回值
      2. 纯方法 — format_report / get_hedge_signal_for_fusion / _compute_portfolio_vol
      3. 常量校验 — 模块常量与类常量回归保护
      4. 便捷函数 — get_hedge_engine / calculate_portfolio_beta
      5. 端到端流程 — 真实 positions → 完整对冲方案 → 报告格式化

覆盖目标: 40%+ (门面层方法大多为单行委托, 端到端流程覆盖核心链路)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ── 路径设置 (与 test_correlation_matrix_unit.py 保持一致) ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_ROOT = PROJECT_ROOT / "v8.3_institutional" / "src"
for _p in (str(PROJECT_ROOT), str(SRC_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hedging.hedge_engine_v59 import (  # noqa: E402
    COST_BENEFIT_THRESHOLD,
    HEDGE_MARGIN_OPP_COST,
    HEDGE_ROLL_COST_ANNUAL,
    INDEX_ALLOCATION_ORDER,
    PORTFOLIO_TAIL_HEDGE_TRIGGERS,
    VOLATILITY_TARGET_ANNUAL,
    HedgeEngine,
    calculate_portfolio_beta,
    get_hedge_engine,
)
from hedging.hedge_types import (  # noqa: E402
    HedgeRecommendation,
    HedgeSignalStrength,
    HedgeType,
)
from risk.portfolio_risk_assessor import PortfolioRisk  # noqa: E402

# ============================================================
# Fixture: 真实持仓数据 (端到端流程使用)
# ============================================================

@pytest.fixture
def sample_positions():
    """构造真实持仓 dict (覆盖多板块)"""
    return {
        "300308": {"shares": 100, "avg_cost": 100.0, "style": "高端制造"},
        "601088": {"shares": 200, "avg_cost": 40.0, "style": "顺周期"},
        "600276": {"shares": 100, "avg_cost": 55.0, "style": "防御"},
    }


@pytest.fixture
def sample_prices():
    """与 sample_positions 对应的最新价格"""
    return {
        "300308": 105.0,
        "601088": 42.0,
        "600276": 56.0,
    }


@pytest.fixture
def engine():
    """默认 HedgeEngine 实例 (100万组合)"""
    return HedgeEngine(portfolio_value=1_000_000)


def _make_recommendation(**overrides) -> HedgeRecommendation:
    """构造 HedgeRecommendation 测试数据 (format_report 专用)"""
    defaults = dict(
        hedge_type=HedgeType.FUTURES_SHORT,
        strength=HedgeSignalStrength.MODERATE,
        urgency_score=0.55,
        futures_instruments=["IF2401", "IC2401"],
        futures_contracts={"IF2401": 2, "IC2401": 1},
        futures_notional={"IF2401": 800000.0, "IC2401": 400000.0},
        futures_margin={"IF2401": 96000.0, "IC2401": 52000.0},
        hedge_ratio=0.50,
        effective_hedge_pct=0.48,
        expected_beta_after=0.55,
        expected_drawdown_reduce=0.35,
        reasoning="组合Beta偏高 + 波动率上升, 建议多指数对冲",
        timestamp="2026-08-01T14:30:00",
    )
    defaults.update(overrides)
    return HedgeRecommendation(**defaults)


# ============================================================
# 1. TestHedgeEngineConstants — 常量回归保护
# ============================================================

class TestHedgeEngineConstants:
    """验证模块级与类级常量不被意外修改"""

    def test_module_constants_values(self):
        """模块常量值回归保护"""
        assert VOLATILITY_TARGET_ANNUAL == 0.18
        assert HEDGE_ROLL_COST_ANNUAL == 0.025
        assert HEDGE_MARGIN_OPP_COST == 0.020
        assert COST_BENEFIT_THRESHOLD == 1.5

    def test_index_allocation_order(self):
        """v5.9: IC/IM 优先于 IF (匹配中小盘成长组合)"""
        assert INDEX_ALLOCATION_ORDER == ["IC", "IM", "IF"]
        assert "IC" in INDEX_ALLOCATION_ORDER
        assert INDEX_ALLOCATION_ORDER[0] == "IC"

    def test_tail_hedge_triggers_keys(self):
        """组合自触发尾部对冲阈值字段完整性"""
        required_keys = {"vol_trigger", "dd_trigger", "min_hedge_ratio", "max_hedge_ratio"}
        assert required_keys.issubset(PORTFOLIO_TAIL_HEDGE_TRIGGERS.keys())

    def test_tail_hedge_triggers_value_ranges(self):
        """触发阈值在合理区间"""
        t = PORTFOLIO_TAIL_HEDGE_TRIGGERS
        assert 0.0 < t["vol_trigger"] < 1.0
        assert 0.0 < t["dd_trigger"] < 1.0
        assert 0.0 < t["min_hedge_ratio"] <= t["max_hedge_ratio"] <= 1.0

    def test_class_default_betas_structure(self):
        """DEFAULT_BETAS: 每个标的映射 4 元组 (csi300, csi500, csi1000, sse50)"""
        assert len(HedgeEngine.DEFAULT_BETAS) >= 10
        for code, betas in HedgeEngine.DEFAULT_BETAS.items():
            assert isinstance(betas, tuple)
            assert len(betas) == 4
            for b in betas:
                assert isinstance(b, (int, float))

    def test_class_sector_map_covers_core_styles(self):
        """SECTOR_MAP 覆盖核心板块"""
        sectors = set(HedgeEngine.SECTOR_MAP.values())
        assert "高端制造" in sectors
        assert "顺周期" in sectors
        assert "防御" in sectors

    def test_class_fixed_income_types(self):
        """固收类型集合 (不计入股票板块集中度)"""
        assert "国债ETF" in HedgeEngine.FIXED_INCOME_TYPES

    def test_class_risk_limits(self):
        """风控阈值回归保护"""
        assert HedgeEngine.SECTOR_LIMIT == 0.35
        assert HedgeEngine.MRC_LIMIT == 0.25
        assert HedgeEngine.CORRELATION_WARN == 0.70

    def test_class_stress_scenarios_count(self):
        """历史压力测试情景数量 (至少6个)"""
        assert len(HedgeEngine.HISTORICAL_STRESS_SCENARIOS) >= 6
        # 每个情景包含关键指数跌幅字段
        for name, scenario in HedgeEngine.HISTORICAL_STRESS_SCENARIOS.items():
            assert "csi300" in scenario
            assert "sector" in scenario


# ============================================================
# 2. TestHedgeEngineInit — 初始化与缓存
# ============================================================

class TestHedgeEngineInit:

    def test_default_initialization(self):
        eng = HedgeEngine()
        assert eng.portfolio_value == 1_000_000
        assert eng._price_cache == {}
        assert eng._beta_cache == {}

    def test_custom_portfolio_value(self):
        eng = HedgeEngine(portfolio_value=5_000_000)
        assert eng.portfolio_value == 5_000_000

    def test_engine_instances_independent_cache(self):
        """两个实例的缓存相互独立"""
        eng1 = HedgeEngine()
        eng2 = HedgeEngine()
        eng1._price_cache["600519"] = 1800.0
        assert "600519" not in eng2._price_cache


# ============================================================
# 3. TestDelegation — 委托方法转发验证
# ============================================================

class TestDelegation:
    """验证 B3.3 委托方法正确转发参数到底层 assessor/executor 函数"""

    def test_assess_portfolio_risk_delegates(self, engine, sample_positions, sample_prices):
        """assess_portfolio_risk 委托到 _assessor_assess_portfolio_risk"""
        with patch(
            "hedging.hedge_engine_v59._assessor_assess_portfolio_risk"
        ) as mock_fn:
            fake_risk = PortfolioRisk(total_value=1_000_000)
            mock_fn.return_value = fake_risk

            result = engine.assess_portfolio_risk(
                sample_positions, sample_prices, cash=100_000
            )

            mock_fn.assert_called_once_with(
                positions=sample_positions,
                prices=sample_prices,
                historical_returns=None,
                cash=100_000,
            )
            assert result is fake_risk

    def test_compute_weighted_beta_delegates(self, engine):
        """_compute_weighted_beta 委托到 assessor"""
        with patch(
            "hedging.hedge_engine_v59._assessor_compute_weighted_beta"
        ) as mock_fn:
            mock_fn.return_value = 1.15
            weights = {"300308": 0.5, "601088": 0.5}
            result = engine._compute_weighted_beta(weights, "csi300")
            mock_fn.assert_called_once_with(weights, "csi300")
            assert result == 1.15

    def test_estimate_default_price_delegates(self, engine):
        """_estimate_default_price 委托到 assessor"""
        with patch(
            "hedging.hedge_engine_v59._assessor_estimate_default_price"
        ) as mock_fn:
            mock_fn.return_value = 100.0
            result = engine._estimate_default_price("300308")
            mock_fn.assert_called_once_with("300308")
            assert result == 100.0

    def test_compute_correlation_matrix_delegates(self, engine):
        """compute_correlation_matrix 委托到 assessor"""
        with patch(
            "hedging.hedge_engine_v59._assessor_compute_correlation_matrix"
        ) as mock_fn:
            mock_fn.return_value = {"A": {"A": 1.0}}
            returns = {"A": [0.01, 0.02]}
            result = engine.compute_correlation_matrix(returns, ["A"], lookback_days=30)
            mock_fn.assert_called_once_with(returns, ["A"], 30)
            assert "A" in result

    def test_monitor_daily_correlation_delegates(self, engine, sample_positions):
        """monitor_daily_correlation 委托到 assessor"""
        with patch(
            "hedging.hedge_engine_v59._assessor_monitor_daily_correlation"
        ) as mock_fn:
            mock_fn.return_value = {"alert": False}
            result = engine.monitor_daily_correlation(
                sample_positions, {}, alert_threshold=0.8, lookback_days=60
            )
            mock_fn.assert_called_once_with(sample_positions, {}, 0.8, 60)
            assert result == {"alert": False}

    def test_check_sector_concentration_delegates(self, engine, sample_positions):
        """check_sector_concentration 委托到 assessor"""
        with patch(
            "hedging.hedge_engine_v59._assessor_check_sector_concentration"
        ) as mock_fn:
            mock_fn.return_value = {"warning": None}
            result = engine.check_sector_concentration(sample_positions, {}, lookback_days=45)
            mock_fn.assert_called_once_with(sample_positions, {}, 45)
            assert result == {"warning": None}

    def test_run_historical_stress_tests_delegates(self, engine, sample_positions, sample_prices):
        """run_historical_stress_tests 委托到 assessor"""
        with patch(
            "hedging.hedge_engine_v59._assessor_run_historical_stress_tests"
        ) as mock_fn:
            mock_fn.return_value = {"scenario1": {}}
            result = engine.run_historical_stress_tests(sample_positions, sample_prices)
            mock_fn.assert_called_once_with(sample_positions, sample_prices)
            assert "scenario1" in result

    def test_determine_hedge_signal_strength_delegates(self, engine):
        """determine_hedge_signal_strength 委托到 executor"""
        with patch(
            "hedging.hedge_engine_v59._executor_determine_hedge_signal_strength"
        ) as mock_fn:
            mock_fn.return_value = (HedgeSignalStrength.MODERATE, 0.55)
            risk = PortfolioRisk()
            result = engine.determine_hedge_signal_strength(
                risk, portfolio_volatility=0.25, portfolio_drawdown_60d=0.08
            )
            mock_fn.assert_called_once_with(risk, None, 0.25, 0.08)
            assert result[0] == HedgeSignalStrength.MODERATE

    def test_compute_optimal_hedge_ratio_delegates(self, engine):
        """compute_optimal_hedge_ratio 委托到 executor"""
        with patch(
            "hedging.hedge_engine_v59._executor_compute_optimal_hedge_ratio"
        ) as mock_fn:
            mock_fn.return_value = 0.45
            risk = PortfolioRisk()
            result = engine.compute_optimal_hedge_ratio(
                risk, HedgeSignalStrength.MODERATE, method="min_variance",
                portfolio_volatility=0.25,
            )
            mock_fn.assert_called_once_with(
                risk, HedgeSignalStrength.MODERATE, "min_variance", 0.25, None
            )
            assert result == 0.45

    def test_generate_futures_hedge_delegates(self, engine):
        """generate_futures_hedge 委托到 executor"""
        with patch(
            "hedging.hedge_engine_v59._executor_generate_futures_hedge"
        ) as mock_fn:
            mock_fn.return_value = {"contracts": {"IF2401": 2}}
            risk = PortfolioRisk()
            result = engine.generate_futures_hedge(risk, 0.5, {"IF2401": 3800.0})
            mock_fn.assert_called_once_with(risk, 0.5, {"IF2401": 3800.0})
            assert "contracts" in result

    def test_generate_options_hedge_delegates(self, engine):
        """generate_options_hedge 委托到 executor"""
        with patch(
            "hedging.hedge_engine_v59._executor_generate_options_hedge"
        ) as mock_fn:
            mock_fn.return_value = {"strategy": "protective_put"}
            risk = PortfolioRisk()
            result = engine.generate_options_hedge(risk, 0.5, strategy="collar")
            mock_fn.assert_called_once_with(risk, 0.5, None, "collar")
            assert result["strategy"] == "protective_put"

    def test_generate_hedge_plan_delegates(self, engine, sample_positions, sample_prices):
        """generate_hedge_plan 委托到 executor (完整参数转发)"""
        with patch(
            "hedging.hedge_engine_v59._executor_generate_hedge_plan"
        ) as mock_fn:
            fake_rec = _make_recommendation()
            mock_fn.return_value = fake_rec
            risk = PortfolioRisk()
            result = engine.generate_hedge_plan(
                risk=risk,
                market_signals={"vix": 25},
                futures_prices={"IF2401": 3800.0},
                prefer_options=False,
                portfolio_volatility=0.25,
                portfolio_drawdown_60d=0.08,
                positions=sample_positions,
                prices=sample_prices,
            )
            mock_fn.assert_called_once_with(
                risk=risk,
                market_signals={"vix": 25},
                futures_prices={"IF2401": 3800.0},
                prefer_options=False,
                portfolio_volatility=0.25,
                portfolio_drawdown_60d=0.08,
                positions=sample_positions,
                prices=sample_prices,
            )
            assert result is fake_rec

    def test_generate_hedge_reason_delegates(self, engine):
        """_generate_hedge_reason 委托到 executor"""
        with patch(
            "hedging.hedge_engine_v59._executor_generate_hedge_reason"
        ) as mock_fn:
            mock_fn.return_value = "对冲理由文本"
            risk = PortfolioRisk()
            result = engine._generate_hedge_reason(risk, 0.5, {"IF2401": 2})
            mock_fn.assert_called_once_with(risk, 0.5, {"IF2401": 2})
            assert result == "对冲理由文本"

    def test_compute_portfolio_vol_cov_delegates(self, engine):
        """_compute_portfolio_vol_cov 委托到 assessor"""
        with patch(
            "hedging.hedge_engine_v59._assessor_compute_portfolio_vol_cov"
        ) as mock_fn:
            mock_fn.return_value = 0.018
            weights = {"300308": 1.0}
            returns = {"300308": [0.01, 0.02]}
            result = engine._compute_portfolio_vol_cov(weights, returns, ["300308"])
            mock_fn.assert_called_once_with(weights, returns, ["300308"])
            assert result == 0.018

    def test_compute_expected_shortfall_delegates(self, engine):
        """_compute_expected_shortfall 委托到 assessor"""
        with patch(
            "hedging.hedge_engine_v59._assessor_compute_expected_shortfall"
        ) as mock_fn:
            mock_fn.return_value = 25000.0
            weights = {"300308": 1.0}
            returns = {"300308": [0.01, 0.02]}
            result = engine._compute_expected_shortfall(
                weights, returns, ["300308"], 1_000_000, confidence=0.99
            )
            mock_fn.assert_called_once_with(
                weights, returns, ["300308"], 1_000_000, 0.99
            )
            assert result == 25000.0

    def test_compute_mrc_delegates(self, engine):
        """_compute_mrc 委托到 assessor"""
        with patch(
            "hedging.hedge_engine_v59._assessor_compute_mrc"
        ) as mock_fn:
            mock_fn.return_value = {"300308": 0.45}
            weights = {"300308": 1.0}
            returns = {"300308": [0.01, 0.02]}
            result = engine._compute_mrc(weights, returns, ["300308"], 0.018)
            mock_fn.assert_called_once_with(weights, returns, ["300308"], 0.018)
            assert result == {"300308": 0.45}


# ============================================================
# 4. TestFormatReport — 纯格式化方法 (核心覆盖区)
# ============================================================

class TestFormatReport:
    """format_report 是门面层最大纯方法, 覆盖各分支输出"""

    def test_basic_futures_report(self, engine):
        """期货对冲方案报告基础结构"""
        rec = _make_recommendation()
        report = engine.format_report(rec)

        assert "Hedge Engine v5.9" in report
        assert "对冲策略报告" in report
        assert "futures_short" in report
        assert "MODERATE" in report
        assert "50%" in report  # hedge_ratio * 100
        # 期货合约信息
        assert "IF2401" in report
        assert "IC2401" in report
        assert "做空" in report
        assert "总保证金需求" in report

    def test_report_includes_reasoning(self, engine):
        """报告包含对冲理由"""
        rec = _make_recommendation(reasoning="测试理由XYZ")
        report = engine.format_report(rec)
        assert "测试理由XYZ" in report
        assert "对冲逻辑" in report

    def test_report_without_futures(self, engine):
        """无期货合约时不输出期货段"""
        rec = _make_recommendation(
            hedge_type=HedgeType.NONE,
            futures_instruments=[],
            futures_contracts={},
            futures_notional={},
            futures_margin={},
        )
        report = engine.format_report(rec)
        assert "期货对冲方案" not in report

    def test_report_with_options(self, engine):
        """期权对冲方案输出"""
        rec = _make_recommendation(
            hedge_type=HedgeType.PUT_PROTECTIVE,
            options_contracts=[
                {
                    "underlying": "510050",
                    "strategy": "protective_put",
                    "contracts": 10,
                    "cost": 5000.0,
                }
            ],
        )
        report = engine.format_report(rec)
        assert "期权对冲方案" in report
        assert "510050" in report
        assert "protective_put" in report
        assert "10" in report  # contracts

    def test_report_with_sector_warnings(self, engine):
        """P0-6: 板块集中度预警输出"""
        rec = _make_recommendation(sector_warnings=["高端制造板块占比40%超限"])
        report = engine.format_report(rec)
        assert "集中度风险监控" in report
        assert "P0-6" in report
        assert "高端制造板块占比40%超限" in report

    def test_report_with_mrc_warnings(self, engine):
        """P0-6: 单标的边际风险贡献预警"""
        rec = _make_recommendation(mrc_warnings=["300308 MRC=0.32超25%上限"])
        report = engine.format_report(rec)
        assert "集中度风险监控" in report
        assert "300308 MRC=0.32超25%上限" in report

    def test_report_no_warnings_shows_safe(self, engine):
        """无集中度预警时显示安全提示"""
        rec = _make_recommendation(sector_warnings=[], mrc_warnings=[])
        report = engine.format_report(rec)
        # 当 sector_warnings 和 mrc_warnings 均为空时, 不输出集中度段
        # (源码 L478: if recommendation.sector_warnings or recommendation.mrc_warnings)
        assert "集中度风险监控" not in report

    def test_report_with_correlation_warning(self, engine):
        """P0-7: 相关性预警输出"""
        rec = _make_recommendation(correlation_warning="平均相关系数0.82超0.7阈值")
        report = engine.format_report(rec)
        assert "相关性风险监控" in report
        assert "P0-7" in report
        assert "0.82" in report

    def test_report_with_stress_tests(self, engine):
        """P0-8: 历史压力测试输出"""
        rec = _make_recommendation(
            stress_tests={
                "2015股灾": {
                    "drawdown_pct": 18.5,
                    "estimated_loss": 185000.0,
                    "breaches_limit": True,
                    "sector_impact": "全面崩盘",
                },
                "2020疫情": {
                    "drawdown_pct": 8.0,
                    "estimated_loss": 80000.0,
                    "breaches_limit": False,
                    "sector_impact": "闪崩",
                },
            }
        )
        report = engine.format_report(rec)
        assert "历史极端压力测试" in report
        assert "P0-8" in report
        assert "2015股灾" in report
        assert "2020疫情" in report
        assert "突破15%回撤上限" in report  # breaches_limit=True
        assert "1/6" in report  # breach_count

    def test_report_stress_tests_all_safe(self, engine):
        """压力测试全部未突破"""
        rec = _make_recommendation(
            stress_tests={
                "情景A": {
                    "drawdown_pct": 5.0,
                    "estimated_loss": 50000.0,
                    "breaches_limit": False,
                    "sector_impact": "温和",
                },
            }
        )
        report = engine.format_report(rec)
        assert "未触发" in report
        assert "全部历史情景均未突破15%回撤上限" in report

    def test_report_disclaimer(self, engine):
        """报告末尾免责声明"""
        rec = _make_recommendation()
        report = engine.format_report(rec)
        assert "以上分析仅供参考" in report
        assert "不构成投资建议" in report

    def test_report_timestamp_truncated(self, engine):
        """timestamp 截取前19字符 (去除微秒)"""
        rec = _make_recommendation(timestamp="2026-08-01T14:30:00.123456")
        report = engine.format_report(rec)
        assert "2026-08-01T14:30:00" in report
        # 完整微秒不应出现
        assert "14:30:00.123456" not in report


# ============================================================
# 5. TestGetHedgeSignalForFusion — 信号融合便捷函数
# ============================================================

class TestGetHedgeSignalForFusion:

    def test_returns_dict_with_required_fields(self, engine):
        """返回字典包含融合层所需字段"""
        signal = engine.get_hedge_signal_for_fusion("portfolio_001")
        assert signal["code"] == "portfolio_001"
        assert signal["source"] == "hedge_engine_v59"
        assert signal["action"] == "HOLD"
        assert signal["score"] == 0.5
        assert signal["confidence"] == 0.3
        assert "reason" in signal
        assert "timestamp" in signal

    def test_default_portfolio_code(self, engine):
        """默认 portfolio_code = 'portfolio'"""
        signal = engine.get_hedge_signal_for_fusion()
        assert signal["code"] == "portfolio"

    def test_timestamp_is_iso_format(self, engine):
        """timestamp 为 ISO 格式字符串"""
        signal = engine.get_hedge_signal_for_fusion()
        ts = signal["timestamp"]
        # ISO 格式: 包含 'T'
        assert "T" in ts
        # 可被 datetime.fromisoformat 解析
        from datetime import datetime
        datetime.fromisoformat(ts)


# ============================================================
# 6. TestComputePortfolioVol — 旧版兼容方法
# ============================================================

class TestComputePortfolioVol:

    def test_returns_fixed_value(self, engine):
        """旧版单资产波动率估算返回固定 0.015 (向后兼容)"""
        result = engine._compute_portfolio_vol({}, {})
        assert result == 0.015


# ============================================================
# 7. TestConvenienceFunctions — 便捷函数
# ============================================================

class TestConvenienceFunctions:

    def test_get_hedge_engine_default(self):
        """默认创建 100万组合的引擎"""
        eng = get_hedge_engine()
        assert isinstance(eng, HedgeEngine)
        assert eng.portfolio_value == 1_000_000

    def test_get_hedge_engine_custom_value(self):
        """自定义组合金额"""
        eng = get_hedge_engine(5_000_000)
        assert eng.portfolio_value == 5_000_000

    def test_get_hedge_engine_none_falls_back(self):
        """portfolio_value=None 回退到默认 100万"""
        eng = get_hedge_engine(None)
        assert eng.portfolio_value == 1_000_000

    def test_calculate_portfolio_beta_returns_keys(self, sample_positions, sample_prices):
        """calculate_portfolio_beta 返回完整字段"""
        result = calculate_portfolio_beta(sample_positions, sample_prices)
        required_keys = {
            "beta_csi300", "beta_csi500", "beta_csi1000",
            "beta_sse50", "concentration_hhi", "var_95_daily",
        }
        assert required_keys.issubset(result.keys())

    def test_calculate_portfolio_beta_empty_positions(self):
        """空持仓不崩溃, 返回零值"""
        result = calculate_portfolio_beta({}, {})
        assert result["beta_csi300"] == 0.0
        assert result["var_95_daily"] == 0.0


# ============================================================
# 8. TestEndToEndFlow — 端到端流程 (覆盖委托链路)
# ============================================================

class TestEndToEndFlow:
    """真实 positions → 风险评估 → 对冲方案 → 报告格式化"""

    def test_full_hedge_flow_with_real_data(self, sample_positions, sample_prices):
        """完整对冲流程不崩溃且产出合理结构"""
        eng = HedgeEngine(portfolio_value=1_000_000)

        # 1. 风险评估
        risk = eng.assess_portfolio_risk(sample_positions, sample_prices)
        assert isinstance(risk, PortfolioRisk)
        assert risk.total_value > 0

        # 2. 信号强度评估
        strength, urgency = eng.determine_hedge_signal_strength(
            risk, portfolio_volatility=0.22, portfolio_drawdown_60d=0.05
        )
        assert isinstance(strength, HedgeSignalStrength)
        assert 0.0 <= urgency <= 1.0

        # 3. 最优对冲比率
        ratio = eng.compute_optimal_hedge_ratio(
            risk, strength, portfolio_volatility=0.22, portfolio_drawdown_60d=0.05
        )
        assert 0.0 <= ratio <= 1.0

        # 4. 完整对冲方案
        rec = eng.generate_hedge_plan(
            risk=risk,
            portfolio_volatility=0.22,
            portfolio_drawdown_60d=0.05,
            positions=sample_positions,
            prices=sample_prices,
        )
        assert isinstance(rec, HedgeRecommendation)

        # 5. 报告格式化
        report = eng.format_report(rec)
        assert isinstance(report, str)
        assert "Hedge Engine v5.9" in report

    def test_stress_tests_with_real_positions(self, sample_positions, sample_prices):
        """历史压力测试产出合理结构"""
        eng = HedgeEngine()
        results = eng.run_historical_stress_tests(sample_positions, sample_prices)

        assert isinstance(results, dict)
        assert len(results) >= 6  # 至少6个历史情景
        for scenario_name, result in results.items():
            assert "drawdown_pct" in result or "estimated_loss" in result

    def test_calculate_portfolio_beta_with_real_data(self, sample_positions, sample_prices):
        """组合 Beta 计算产出合理值"""
        result = calculate_portfolio_beta(sample_positions, sample_prices)
        # Beta 值应在合理区间 (非极端值)
        for key in ["beta_csi300", "beta_csi500", "beta_csi1000", "beta_sse50"]:
            assert -1.0 <= result[key] <= 3.0, f"{key}={result[key]} 超出合理区间"
        # 集中度 HHI 在 [0, 1]
        assert 0.0 <= result["concentration_hhi"] <= 1.0
