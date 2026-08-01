# -*- coding: utf-8 -*-
"""decision_theories 单元测试 — T2.2.

验证以下方面:
    1. TheoryDecision dataclass 结构与序列化
    2. SorosReflexivityEngine: 反身性得分 + 盛衰周期阶段分类
    3. DalioEconomicMachine: 经济四象限 + 债务周期 + 风险平价
    4. FirstPrinciplesAnalyzer: 价值驱动分解 + DCF + 共识挑战
    5. BuffettMungerFramework: 护城河 + 安全边际 + 能力圈 + 质量评分
    6. TheoryFusionEngine: 加权融合 + 一致性 + 冲突检测
    7. run_full_theory_analysis: 一键全流程
    8. Re-export 兼容层: v8.3 旧路径可正常导入
"""
from __future__ import annotations

import sys
from pathlib import Path


# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

# 导入被测模块 (新路径)
from utils.alpha.decision_theories import (
    BuffettMungerFramework,
    DalioEconomicMachine,
    FirstPrinciplesAnalyzer,
    SorosReflexivityEngine,
    TheoryDecision,
    TheoryFusionEngine,
    run_full_theory_analysis,
)


# ============================================================
# 1. TheoryDecision dataclass 测试
# ============================================================

class TestTheoryDecision:
    """TheoryDecision 数据类测试."""

    def test_basic_construction(self):
        """基本构造."""
        d = TheoryDecision(
            theory="测试理论",
            signal="BUY",
            score=0.75,
            conviction="HIGH",
            summary="测试摘要",
        )
        assert d.theory == "测试理论"
        assert d.signal == "BUY"
        assert d.score == 0.75
        assert d.conviction == "HIGH"
        assert d.summary == "测试摘要"
        assert d.details == {}
        assert d.timestamp  # 自动填充

    def test_timestamp_auto_filled(self):
        """timestamp 自动填充 ISO 格式."""
        d = TheoryDecision(
            theory="t", signal="HOLD", score=0.5, conviction="LOW", summary="",
        )
        assert "T" in d.timestamp  # ISO 格式

    def test_explicit_timestamp_respected(self):
        """显式指定 timestamp 不被覆盖."""
        d = TheoryDecision(
            theory="t", signal="HOLD", score=0.5, conviction="LOW",
            summary="", timestamp="2026-01-01T00:00:00",
        )
        assert d.timestamp == "2026-01-01T00:00:00"

    def test_to_dict_serialization(self):
        """to_dict 序列化正确."""
        d = TheoryDecision(
            theory="t", signal="BUY", score=0.8, conviction="HIGH",
            summary="s", details={"k": "v"},
        )
        result = d.to_dict()
        assert result["theory"] == "t"
        assert result["signal"] == "BUY"
        assert result["score"] == 0.8
        assert result["details"] == {"k": "v"}
        assert "timestamp" in result


# ============================================================
# 2. SorosReflexivityEngine 测试
# ============================================================

class TestSorosReflexivityEngine:
    """索罗斯反身性引擎测试."""

    def test_empty_price_data(self):
        """空价格数据返回空结果."""
        engine = SorosReflexivityEngine()
        result = engine.compute_reflexivity_score({})
        assert result == {}

    def test_invalid_price_skipped(self):
        """price <= 0 的标的被跳过."""
        engine = SorosReflexivityEngine()
        result = engine.compute_reflexivity_score({
            "BAD": {"price": 0, "change_20d": 0.1, "z_score": 0, "volatility": 0.2},
            "GOOD": {"price": 100, "change_20d": 0.1, "z_score": 0, "volatility": 0.2},
        })
        assert "BAD" not in result
        assert "GOOD" in result

    def test_basic_score_computation(self):
        """基本得分计算."""
        engine = SorosReflexivityEngine()
        result = engine.compute_reflexivity_score({
            "TEST": {
                "price": 100, "change_20d": 0.15, "z_score": 1.5, "volatility": 0.25,
            },
        })
        assert "TEST" in result
        r = result["TEST"]
        assert 0 <= r["score"] <= 1
        assert "phase" in r
        assert "phase_desc" in r
        assert "far_from_equilibrium" in r
        assert "signal" in r
        assert r["signal"] in ("BUY", "SELL", "HOLD")

    def test_high_reflexivity_sell_signal(self):
        """高反身性 + 高 z_score → SELL.

        源码阈值: signal = "SELL" if (reflexivity > 0.7 and z_score > 0)
        reflexivity = momentum*0.30 + vol*0.25 + val*0.25 + sent*0.20
        仅 price_data 时 vol=val=sent=0.5, reflexivity=0.65 不足触发 SELL
        必须补充 volume/valuation/sentiment 数据推高 reflexivity > 0.7
        """
        engine = SorosReflexivityEngine()
        result = engine.compute_reflexivity_score(
            price_data={
                "TEST": {
                    "price": 100, "change_20d": 0.30, "z_score": 2.5, "volatility": 0.35,
                },
            },
            volume_data={"TEST": {"current_vol": 10000, "avg_vol_20d": 3000}},  # vol_ratio=3.33 → vol_score=1.0
            valuation_data={"TEST": {"pe_ttm": 60, "pe_historical_median": 30}},  # pe_dev=1.0 → val_score=1.0
            sentiment_data={"TEST": {"sentiment_score": 0.9, "narrative_strength": 0.9}},  # sent_score=0.9
        )
        # reflexivity = 1.0*0.30 + 1.0*0.25 + 1.0*0.25 + 0.9*0.20 = 0.98 > 0.7
        # z_score = 2.5 > 0 → SELL
        assert result["TEST"]["signal"] == "SELL"
        assert result["TEST"]["score"] > 0.7

    def test_low_reflexivity_buy_signal(self):
        """低 z_score + 中等反身性 → BUY."""
        engine = SorosReflexivityEngine()
        result = engine.compute_reflexivity_score({
            "TEST": {
                "price": 100, "change_20d": 0.20, "z_score": -2.0, "volatility": 0.30,
            },
            "TEST2": {
                "price": 100, "change_20d": 0.25, "z_score": -2.0, "volatility": 0.30,
            },
        })
        # reflexivity > 0.5 + z_score < -1.5 → BUY
        assert result["TEST"]["signal"] == "BUY"

    def test_cycle_phase_classification(self):
        """盛衰周期阶段分类."""
        engine = SorosReflexivityEngine()
        # 低反身性 → 均衡期
        assert engine._classify_cycle_phase(0.2, 0.1, 0.5, 0.0) == "EQUILIBRIUM"
        # 高反身性 + 高 z_score → TESTING/REVERSAL
        assert engine._classify_cycle_phase(0.8, 0.6, 3.0, 0.1) == "REVERSAL"
        assert engine._classify_cycle_phase(0.8, 0.3, 3.0, 0.1) == "TESTING"
        # 高反身性 + 中等 z_score → ACCELERATING
        assert engine._classify_cycle_phase(0.8, 0.6, 1.0, 0.1) == "ACCELERATING"

    def test_generate_decision_empty(self):
        """空结果生成 NEUTRAL."""
        engine = SorosReflexivityEngine()
        d = engine.generate_decision({})
        assert d.signal == "NEUTRAL"
        assert d.score == 0.0
        assert d.conviction == "LOW"

    def test_generate_decision_with_data(self):
        """有数据生成有效决策."""
        engine = SorosReflexivityEngine()
        stock_results = {
            "A": {"score": 0.8, "phase": "ACCELERATING", "far_from_equilibrium": True},
            "B": {"score": 0.7, "phase": "ACCELERATING", "far_from_equilibrium": True},
            "C": {"score": 0.6, "phase": "GERMINAL", "far_from_equilibrium": False},
        }
        d = engine.generate_decision(stock_results)
        assert d.theory == "索罗斯反身性"
        assert d.signal in ("BUY", "SELL", "HOLD")
        assert 0 <= d.score <= 1


# ============================================================
# 3. DalioEconomicMachine 测试
# ============================================================

class TestDalioEconomicMachine:
    """达利奥经济机器引擎测试."""

    def test_prosperity_regime(self):
        """繁荣期: 增长↑ 通胀↑."""
        engine = DalioEconomicMachine()
        result = engine.classify_economic_regime(
            growth_data={"pmi": 52, "gdp_growth": 5.5},
            inflation_data={"cpi": 3.0, "ppi": 2.0},
        )
        assert result["regime"] == "PROSPERITY"
        assert result["growth_trend"] == "上升"
        assert result["inflation_trend"] == "上升"
        assert "stocks" in result["recommended_allocation"]

    def test_recession_regime(self):
        """衰退期: 增长↓ 通胀↓."""
        engine = DalioEconomicMachine()
        result = engine.classify_economic_regime(
            growth_data={"pmi": 48, "gdp_growth": 3.0},
            inflation_data={"cpi": 0.5, "ppi": -2.0},
        )
        assert result["regime"] == "RECESSION"
        assert result["growth_trend"] == "下降"
        assert result["inflation_trend"] == "下降"

    def test_overheat_regime(self):
        """过热期: 增长↓ 通胀↑."""
        engine = DalioEconomicMachine()
        result = engine.classify_economic_regime(
            growth_data={"pmi": 48, "gdp_growth": 3.5},
            inflation_data={"cpi": 3.0, "ppi": 2.0},
        )
        assert result["regime"] == "OVERHEAT"

    def test_reflexivity_regime(self):
        """再通胀期: 增长↑ 通胀↓."""
        engine = DalioEconomicMachine()
        result = engine.classify_economic_regime(
            growth_data={"pmi": 52, "gdp_growth": 5.5},
            inflation_data={"cpi": 0.5, "ppi": -2.0},
        )
        assert result["regime"] == "REFLEXIVITY"

    def test_debt_cycle_high_risk(self):
        """高风险债务周期."""
        engine = DalioEconomicMachine()
        result = engine.assess_debt_cycle({
            "debt_to_gdp": 3.2,  # > 3.0 → 长期顶部
            "credit_growth_yoy": 0.15,  # > 0.12 → 信贷扩张
            "policy_rate": 0.03,
            "real_rate": 0.01,
        })
        assert result["combined_debt_score"] > 0.5
        assert result["signal"] in ("SELL", "HOLD", "BUY")

    def test_debt_cycle_low_risk(self):
        """低风险债务周期."""
        engine = DalioEconomicMachine()
        result = engine.assess_debt_cycle({
            "debt_to_gdp": 1.5,
            "credit_growth_yoy": 0.08,
            "policy_rate": 0.02,
            "real_rate": 0.01,
        })
        assert result["combined_debt_score"] < 0.7

    def test_risk_parity_weights(self):
        """风险平价权重计算."""
        engine = DalioEconomicMachine()
        weights = engine.compute_risk_parity_weights({
            "stocks": 0.20,
            "bonds": 0.05,
            "gold": 0.15,
        })
        # 权重和应为 1
        assert abs(sum(weights.values()) - 1.0) < 0.01
        # 低波动资产权重应更高
        assert weights["bonds"] > weights["stocks"]

    def test_risk_parity_empty(self):
        """空输入使用默认值."""
        engine = DalioEconomicMachine()
        weights = engine.compute_risk_parity_weights()
        assert len(weights) > 0
        assert abs(sum(weights.values()) - 1.0) < 0.01

    def test_generate_decision(self):
        """生成综合决策."""
        engine = DalioEconomicMachine()
        regime = engine.classify_economic_regime(
            {"pmi": 52, "gdp_growth": 5.5},
            {"cpi": 0.5, "ppi": -2.0},
        )
        debt = engine.assess_debt_cycle({
            "debt_to_gdp": 1.5, "credit_growth_yoy": 0.08,
        })
        d = engine.generate_decision(regime, debt)
        assert d.theory == "达利奥经济机器"
        assert d.signal in ("BUY", "SELL", "HOLD")


# ============================================================
# 4. FirstPrinciplesAnalyzer 测试
# ============================================================

class TestFirstPrinciplesAnalyzer:
    """第一性原理分析器测试."""

    def test_decompose_value_drivers(self):
        """价值驱动分解."""
        analyzer = FirstPrinciplesAnalyzer()
        result = analyzer.decompose_value_drivers({
            "601088": {"sector": "能源", "price": 41.26, "pe": 10.5, "roe": 0.15},
        })
        assert "601088" in result
        r = result["601088"]
        assert r["sector"] == "能源"
        assert "key_drivers" in r
        assert "signal" in r

    def test_undervalued_detection(self):
        """ROE 显著高于隐含增长率 → BUY."""
        analyzer = FirstPrinciplesAnalyzer()
        result = analyzer.decompose_value_drivers({
            "A": {"sector": "能源", "pe": 10, "roe": 0.25, "price": 100},
        })
        # ROE=0.25 vs implied_growth=0.10 → 差 0.15 > 0.05 → BUY
        assert result["A"]["signal"] == "BUY"

    def test_overvalued_detection(self):
        """ROE 低于隐含增长率 → SELL."""
        analyzer = FirstPrinciplesAnalyzer()
        result = analyzer.decompose_value_drivers({
            "A": {"sector": "能源", "pe": 50, "roe": 0.10, "price": 100},
        })
        # ROE=0.10 vs implied_growth=0.50 → 差 -0.40 < -0.05 → SELL
        assert result["A"]["signal"] == "SELL"

    def test_unknown_sector_defaults_manufacturing(self):
        """未知行业默认为制造."""
        analyzer = FirstPrinciplesAnalyzer()
        result = analyzer.decompose_value_drivers({
            "A": {"sector": "未知行业", "pe": 15, "roe": 0.12, "price": 100},
        })
        assert result["A"]["sector"] == "制造"

    def test_dcf_intrinsic_value(self):
        """DCF 内在价值计算."""
        analyzer = FirstPrinciplesAnalyzer()
        result = analyzer.compute_intrinsic_value_range(
            eps=2.0, growth_rate=0.10, discount_rate=0.10, terminal_growth=0.03, years=5,
        )
        assert result["low"] > 0
        assert result["mid"] > result["low"]
        assert result["high"] > result["mid"]

    def test_dcf_zero_eps(self):
        """EPS=0 返回零价值."""
        analyzer = FirstPrinciplesAnalyzer()
        result = analyzer.compute_intrinsic_value_range(
            eps=0, growth_rate=0.10,
        )
        assert result["low"] == 0
        assert result["mid"] == 0

    def test_challenge_market_narrative_flaws(self):
        """挑战市场共识 — 检测叙事漏洞."""
        analyzer = FirstPrinciplesAnalyzer()
        result = analyzer.challenge_market_narrative(
            "TEST", "高增长科技股, 这次不一样",
            {"pe": 50, "roe": 0.10, "revenue_growth": 0.03},
        )
        assert result["code"] == "TEST"
        assert len(result["flaws"]) > 0
        assert len(result["counter_points"]) > 0

    def test_challenge_narrative_no_flaws(self):
        """合理估值无漏洞."""
        analyzer = FirstPrinciplesAnalyzer()
        result = analyzer.challenge_market_narrative(
            "TEST", "稳健增长",
            {"pe": 15, "roe": 0.18, "revenue_growth": 0.12},
        )
        assert len(result["flaws"]) == 0

    def test_generate_decision_empty(self):
        """空结果生成 NEUTRAL."""
        analyzer = FirstPrinciplesAnalyzer()
        d = analyzer.generate_decision({})
        assert d.signal == "NEUTRAL"
        assert d.theory == "第一性原理"

    def test_generate_decision_with_buy_signals(self):
        """多个 BUY 信号 → 整体 BUY."""
        analyzer = FirstPrinciplesAnalyzer()
        d = analyzer.generate_decision({
            "A": {"signal": "BUY"}, "B": {"signal": "BUY"}, "C": {"signal": "HOLD"},
        })
        assert d.signal == "BUY"


# ============================================================
# 5. BuffettMungerFramework 测试
# ============================================================

class TestBuffettMungerFramework:
    """巴菲特芒格框架测试."""

    def test_evaluate_moat_wide(self):
        """宽阔护城河评分."""
        bm = BuffettMungerFramework()
        result = bm.evaluate_moat({
            "A": {
                "sector": "医药", "roic": 0.20, "gross_margin": 0.85,
                "market_share": 0.20, "revenue_10y_cagr": 0.12,
                "brand_strength": 0.9, "patent_count": 600,
                "switching_cost": 0.7, "network_effect": 0.5,
            },
        })
        assert result["A"]["moat_score"] >= 70
        assert result["A"]["moat_level"] == "wide"
        assert result["A"]["signal"] == "BUY"

    def test_evaluate_moat_fragile(self):
        """脆弱护城河."""
        bm = BuffettMungerFramework()
        result = bm.evaluate_moat({
            "A": {
                "sector": "商品", "roic": 0.05, "gross_margin": 0.15,
                "market_share": 0.02, "revenue_10y_cagr": 0.02,
                "brand_strength": 0.1, "patent_count": 0,
                "switching_cost": 0.1, "network_effect": 0.1,
            },
        })
        assert result["A"]["moat_score"] < 30
        assert result["A"]["moat_level"] == "fragile"

    def test_margin_of_safety_deep_value(self):
        """深度价值 — 安全边际 > 30%."""
        bm = BuffettMungerFramework()
        result = bm.compute_margin_of_safety(intrinsic_value=100, market_price=60)
        assert result["level"] == "deep_value"
        assert result["signal"] == "BUY"
        assert result["margin_pct"] > 30

    def test_margin_of_safety_overvalued(self):
        """显著高估."""
        bm = BuffettMungerFramework()
        result = bm.compute_margin_of_safety(intrinsic_value=100, market_price=150)
        assert result["level"] == "significantly_overvalued"
        assert result["signal"] == "SELL"

    def test_margin_of_safety_zero_inputs(self):
        """无效输入返回 unknown."""
        bm = BuffettMungerFramework()
        result = bm.compute_margin_of_safety(intrinsic_value=0, market_price=100)
        assert result["level"] == "unknown"
        assert result["signal"] == "HOLD"

    def test_circle_of_competence_core(self):
        """核心能力圈.

        源码阈值: confidence = user_knowledge_score / (user_knowledge_score + difficulty)
                  confidence > 0.7 → "核心能力圈"
        "消费" 行业 difficulty=0.4:
          - user_knowledge_score=0.9 → confidence=0.6923 (未达 0.7, 落入"扩展能力圈")
          - user_knowledge_score=0.95 → confidence=0.7037 (>0.7, 触发"核心能力圈")
        """
        bm = BuffettMungerFramework()
        result = bm.assess_circle_of_competence("消费", user_knowledge_score=0.95)
        assert result["confidence"] > 0.7
        assert result["level"] == "核心能力圈"

    def test_circle_of_competence_outside(self):
        """能力圈外."""
        bm = BuffettMungerFramework()
        result = bm.assess_circle_of_competence("医药", user_knowledge_score=0.1)
        assert result["level"] == "能力圈外"

    def test_quality_score_grade_a(self):
        """A 级优质企业."""
        bm = BuffettMungerFramework()
        result = bm.get_quality_score({
            "roe": 0.20, "debt_to_equity": 0.2, "fcf_margin": 0.15,
            "capex_ratio": 0.05, "profit_stability": 0.95,
        })
        assert result["quality_score"] >= 70
        assert result["grade"].startswith("A")

    def test_quality_score_grade_d(self):
        """D 级不推荐."""
        bm = BuffettMungerFramework()
        result = bm.get_quality_score({
            "roe": 0.05, "debt_to_equity": 0.9, "fcf_margin": 0.02,
            "capex_ratio": 0.20, "profit_stability": 0.4,
        })
        assert result["quality_score"] < 30
        assert result["grade"].startswith("D")

    def test_generate_decision_empty(self):
        """空结果生成 NEUTRAL."""
        bm = BuffettMungerFramework()
        d = bm.generate_decision({})
        assert d.signal == "NEUTRAL"
        assert d.theory == "巴菲特芒格模型"

    def test_generate_decision_with_wide_moat(self):
        """有宽阔护城河 + 安全边际 → BUY."""
        bm = BuffettMungerFramework()
        moat = {
            "A": {"moat_level": "wide"}, "B": {"moat_level": "narrow"},
        }
        margin = {"A": {"level": "deep_value"}}
        d = bm.generate_decision(moat, margin)
        assert d.signal == "BUY"
        assert d.conviction == "HIGH"


# ============================================================
# 6. TheoryFusionEngine 测试
# ============================================================

class TestTheoryFusionEngine:
    """理论融合引擎测试."""

    def test_empty_decisions(self):
        """空决策列表."""
        engine = TheoryFusionEngine()
        result = engine.fuse_decisions([])
        assert result["fused_signal"] == "NEUTRAL"
        assert result["fused_score"] == 0.5

    def test_all_buy_signals(self):
        """全部 BUY → 融合 BUY."""
        engine = TheoryFusionEngine()
        decisions = [
            TheoryDecision(theory="索罗斯反身性", signal="BUY", score=0.8, conviction="HIGH", summary="A"),
            TheoryDecision(theory="达利奥经济机器", signal="BUY", score=0.7, conviction="HIGH", summary="B"),
            TheoryDecision(theory="第一性原理", signal="BUY", score=0.75, conviction="HIGH", summary="C"),
            TheoryDecision(theory="巴菲特芒格模型", signal="BUY", score=0.85, conviction="HIGH", summary="D"),
        ]
        result = engine.fuse_decisions(decisions)
        assert result["fused_signal"] == "BUY"
        assert result["fused_score"] > 0.65
        assert result["agreement"] > 0.8

    def test_all_sell_signals(self):
        """全部 SELL → 融合 SELL."""
        engine = TheoryFusionEngine()
        decisions = [
            TheoryDecision(theory="索罗斯反身性", signal="SELL", score=0.2, conviction="HIGH", summary="A"),
            TheoryDecision(theory="达利奥经济机器", signal="SELL", score=0.1, conviction="HIGH", summary="B"),
            TheoryDecision(theory="第一性原理", signal="SELL", score=0.15, conviction="HIGH", summary="C"),
            TheoryDecision(theory="巴菲特芒格模型", signal="SELL", score=0.05, conviction="HIGH", summary="D"),
        ]
        result = engine.fuse_decisions(decisions)
        assert result["fused_signal"] == "SELL"
        assert result["fused_score"] < 0.35

    def test_conflict_detection(self):
        """BUY 与 SELL 冲突检测."""
        engine = TheoryFusionEngine()
        decisions = [
            TheoryDecision(theory="索罗斯反身性", signal="BUY", score=0.8, conviction="HIGH", summary="A"),
            TheoryDecision(theory="达利奥经济机器", signal="SELL", score=0.2, conviction="HIGH", summary="B"),
        ]
        result = engine.fuse_decisions(decisions)
        assert len(result["conflicts"]) > 0
        assert "冲突" in result["conflicts"][0]

    def test_hold_signal_threshold(self):
        """HOLD 信号阈值."""
        engine = TheoryFusionEngine()
        decisions = [
            TheoryDecision(theory="t1", signal="HOLD", score=0.5, conviction="MEDIUM", summary="A"),
            TheoryDecision(theory="t2", signal="HOLD", score=0.5, conviction="MEDIUM", summary="B"),
        ]
        result = engine.fuse_decisions(decisions)
        assert result["fused_signal"] == "HOLD"

    def test_custom_weights(self):
        """自定义权重."""
        weights = {
            "索罗斯反身性": 0.5,
            "达利奥经济机器": 0.5,
        }
        engine = TheoryFusionEngine(weights=weights)
        decisions = [
            TheoryDecision(theory="索罗斯反身性", signal="BUY", score=0.8, conviction="HIGH", summary="A"),
            TheoryDecision(theory="达利奥经济机器", signal="SELL", score=0.2, conviction="HIGH", summary="B"),
        ]
        result = engine.fuse_decisions(decisions)
        # BUY(1.0)*0.5 + SELL(0.0)*0.5 = 0.5 → HOLD
        assert result["fused_signal"] == "HOLD"

    def test_generate_fusion_report(self):
        """生成 Markdown 报告."""
        engine = TheoryFusionEngine()
        decisions = [
            TheoryDecision(theory="索罗斯反身性", signal="BUY", score=0.8, conviction="HIGH", summary="A"),
        ]
        result = engine.fuse_decisions(decisions)
        report = engine.generate_fusion_report(result)
        assert "四大理论融合决策" in report
        assert "BUY" in report


# ============================================================
# 7. run_full_theory_analysis 测试
# ============================================================

class TestRunFullTheoryAnalysis:
    """一键全流程分析测试."""

    def test_full_analysis_basic(self):
        """完整分析流程."""
        price_data = {
            "601088": {"price": 41.26, "change_20d": -0.05, "z_score": -1.2, "volatility": 0.22},
            "600276": {"price": 50.47, "change_20d": 0.12, "z_score": 1.8, "volatility": 0.26},
        }
        financial_data = {
            "601088": {"sector": "能源", "pe": 10.5, "roe": 0.15},
            "600276": {"sector": "医药", "pe": 45.0, "roe": 0.22},
        }
        macro_data = {
            "pmi": 50.2, "cpi": 0.3, "ppi": -2.1,
            "debt_to_gdp": 2.8, "credit_growth": 0.082, "policy_rate": 0.03,
        }

        result = run_full_theory_analysis(
            price_data=price_data,
            macro_data=macro_data,
            financial_data=financial_data,
        )

        assert "fusion" in result
        assert "individual_decisions" in result
        assert "timestamp" in result
        assert len(result["individual_decisions"]) == 4
        assert result["fusion"]["fused_signal"] in ("BUY", "SELL", "HOLD")

    def test_full_analysis_empty_data(self):
        """空数据不报错."""
        result = run_full_theory_analysis(
            price_data={},
            macro_data={},
            financial_data={},
        )
        # 应该有 fusion 字段 (即使所有引擎都失败)
        assert "fusion" in result


# ============================================================
# 8. Re-export 兼容层测试
# ============================================================

class TestReexportCompat:
    """v8.3 旧路径 re-export 兼容测试 (HC-1)."""

    def test_v83_path_import(self):
        """v8.3 旧路径可正常导入.

        原文件路径: v8.3_institutional/src/factors/decision_theories.py
        必须将 v8.3_institutional/src/factors 加入 sys.path,
        使 `import decision_theories` 能定位到该文件.
        """
        # 清理已加载的模块
        for mod_name in list(sys.modules.keys()):
            if mod_name == "decision_theories" or "decision_theories" in mod_name:
                del sys.modules[mod_name]

        # v8.3 路径必须指向 factors 目录 (decision_theories.py 所在位置)
        v83_path = str(_PROJECT_ROOT / "v8.3_institutional" / "src" / "factors")
        if v83_path not in sys.path:
            sys.path.insert(0, v83_path)

        try:
            import decision_theories as v83_dt
            assert hasattr(v83_dt, "TheoryDecision")
            assert hasattr(v83_dt, "SorosReflexivityEngine")
            assert hasattr(v83_dt, "DalioEconomicMachine")
            assert hasattr(v83_dt, "FirstPrinciplesAnalyzer")
            assert hasattr(v83_dt, "BuffettMungerFramework")
            assert hasattr(v83_dt, "TheoryFusionEngine")
            assert hasattr(v83_dt, "run_full_theory_analysis")
            # 历史兼容别名
            assert hasattr(v83_dt, "DecisionTheoryEngine")
        finally:
            if v83_path in sys.path:
                sys.path.remove(v83_path)
            # 清理: 避免污染后续测试
            for mod_name in list(sys.modules.keys()):
                if mod_name == "decision_theories":
                    del sys.modules[mod_name]

    def test_v83_path_functional(self):
        """v8.3 旧路径功能正常."""
        for mod_name in list(sys.modules.keys()):
            if mod_name == "decision_theories" or "decision_theories" in mod_name:
                del sys.modules[mod_name]

        v83_path = str(_PROJECT_ROOT / "v8.3_institutional" / "src" / "factors")
        if v83_path not in sys.path:
            sys.path.insert(0, v83_path)

        try:
            import decision_theories as v83_dt
            # 实例化 + 调用
            engine = v83_dt.SorosReflexivityEngine()
            result = engine.compute_reflexivity_score({
                "TEST": {"price": 100, "change_20d": 0.1, "z_score": 1.0, "volatility": 0.2},
            })
            assert "TEST" in result

            # DecisionTheoryEngine 别名应可用 (历史兼容)
            fusion = v83_dt.DecisionTheoryEngine()
            assert fusion is not None
        finally:
            if v83_path in sys.path:
                sys.path.remove(v83_path)
            for mod_name in list(sys.modules.keys()):
                if mod_name == "decision_theories":
                    del sys.modules[mod_name]

    def test_new_path_identity(self):
        """新路径是生产唯一事实源."""
        from utils.alpha.decision_theories import SorosReflexivityEngine as NewEngine
        # 再次导入应该返回同一类对象
        from utils.alpha.decision_theories import SorosReflexivityEngine as NewEngine2
        assert NewEngine is NewEngine2
