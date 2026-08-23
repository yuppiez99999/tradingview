"""
TradingGroup 自反思机制 — 单元测试
====================================

测试覆盖:
- ErrorType / ReflectionGrade 枚举
- ReflectionRecord 数据结构
- SyntheticSample 数据结构
- DynamicStops 数据结构
- DataSynthesizer (正/负/困难样本合成)
- DynamicStopLossManager (ATR/趋势/时间衰减/仓位)
- TradingGroupReflector (自反思/数据合成/动态止盈止损/摘要/建议)

文献: #16 TradingGroup: Self-Reflection + Data-Synthesis (2025.08)
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.trading_group_reflector import (
    DataSynthesizer,
    DynamicStopLossManager,
    DynamicStops,
    ErrorType,
    ReflectionGrade,
    ReflectionRecord,
    SyntheticSample,
    TradingGroupReflector,
)

# ============================================================
# 枚举测试
# ============================================================

class TestErrorType:
    """错误类型枚举测试。"""

    def test_five_types(self):
        assert len(ErrorType) == 5

    def test_values(self):
        assert ErrorType.SIGNAL_ERROR.value == "signal_error"
        assert ErrorType.NO_ERROR.value == "no_error"


class TestReflectionGrade:
    """反思评级枚举测试。"""

    def test_five_grades(self):
        assert len(ReflectionGrade) == 5

    def test_values(self):
        assert ReflectionGrade.EXCELLENT.value == "excellent"
        assert ReflectionGrade.BAD.value == "bad"


# ============================================================
# ReflectionRecord 测试
# ============================================================

class TestReflectionRecord:
    """自反思记录测试。"""

    def test_default_values(self):
        record = ReflectionRecord(
            decision_id="d1", ticker="000001.SZ", action="buy", entry_price=10.0,
        )
        assert record.exit_price is None
        assert record.outcome_return is None
        assert record.error_type == ErrorType.NO_ERROR
        assert record.grade == ReflectionGrade.NEUTRAL
        assert record.market_state == {}

    def test_is_correct(self):
        record = ReflectionRecord(
            decision_id="d1", ticker="A", action="buy", entry_price=10.0,
            error_type=ErrorType.NO_ERROR,
        )
        assert record.is_correct

    def test_is_not_correct(self):
        record = ReflectionRecord(
            decision_id="d1", ticker="A", action="buy", entry_price=10.0,
            error_type=ErrorType.SIGNAL_ERROR,
        )
        assert not record.is_correct

    def test_is_profitable(self):
        record = ReflectionRecord(
            decision_id="d1", ticker="A", action="buy", entry_price=10.0,
            outcome_return=0.05,
        )
        assert record.is_profitable

    def test_is_not_profitable(self):
        record = ReflectionRecord(
            decision_id="d1", ticker="A", action="buy", entry_price=10.0,
            outcome_return=-0.03,
        )
        assert not record.is_profitable

    def test_is_profitable_none(self):
        record = ReflectionRecord(
            decision_id="d1", ticker="A", action="buy", entry_price=10.0,
        )
        assert not record.is_profitable

    def test_to_dict(self):
        record = ReflectionRecord(
            decision_id="d1", ticker="000001.SZ", action="buy", entry_price=10.0,
            exit_price=10.5, outcome_return=0.05,
        )
        d = record.to_dict()
        assert d["decision_id"] == "d1"
        assert d["outcome_return"] == 0.05
        assert d["error_type"] == "no_error"


# ============================================================
# SyntheticSample 测试
# ============================================================

class TestSyntheticSample:
    """合成样本测试。"""

    def test_default_values(self):
        sample = SyntheticSample(features={"x": 1}, label=1)
        assert sample.weight == 1.0
        assert sample.source == "positive"
        assert sample.ticker == ""

    def test_to_dict(self):
        sample = SyntheticSample(features={"x": 1}, label=0, weight=2.0, source="hard")
        d = sample.to_dict()
        assert d["label"] == 0
        assert d["weight"] == 2.0
        assert d["source"] == "hard"


# ============================================================
# DynamicStops 测试
# ============================================================

class TestDynamicStops:
    """动态止盈止损结果测试。"""

    def test_default_values(self):
        stops = DynamicStops(stop_loss=9.0, take_profit=11.0)
        assert stops.trailing_stop is None
        assert stops.position_size == 1.0
        assert stops.risk_score == 0.0

    def test_to_dict(self):
        stops = DynamicStops(stop_loss=9.0, take_profit=11.0, risk_score=0.3)
        d = stops.to_dict()
        assert d["stop_loss"] == 9.0
        assert d["risk_score"] == 0.3


# ============================================================
# DataSynthesizer 测试
# ============================================================

class TestDataSynthesizer:
    """数据合成器测试。"""

    def _make_record(self, ret: float, correct: bool = True) -> ReflectionRecord:
        return ReflectionRecord(
            decision_id="d", ticker="A", action="buy", entry_price=10.0,
            outcome_return=ret,
            error_type=ErrorType.NO_ERROR if correct else ErrorType.SIGNAL_ERROR,
        )

    def test_positive_sample(self):
        """正确且盈利 → 正样本。"""
        synth = DataSynthesizer()
        records = [self._make_record(0.05, correct=True)]
        samples = synth.synthesize(records)
        assert len(samples) == 1
        assert samples[0].label == 1
        assert samples[0].source == "positive"
        assert samples[0].weight == 1.0

    def test_negative_sample(self):
        """错误且亏损 → 负样本。"""
        synth = DataSynthesizer()
        records = [self._make_record(-0.05, correct=False)]
        samples = synth.synthesize(records)
        assert len(samples) == 1
        assert samples[0].label == 0
        assert samples[0].source == "negative"

    def test_hard_sample(self):
        """收益接近0 → 困难样本 (weight=2.0)。"""
        synth = DataSynthesizer(hard_sample_threshold=0.02)
        records = [self._make_record(0.01, correct=True)]
        samples = synth.synthesize(records)
        assert len(samples) == 1
        assert samples[0].source == "hard"
        assert samples[0].weight == 2.0

    def test_skip_none_return(self):
        """outcome_return=None 的记录跳过。"""
        synth = DataSynthesizer()
        records = [ReflectionRecord(
            decision_id="d", ticker="A", action="buy", entry_price=10.0,
        )]
        samples = synth.synthesize(records)
        assert len(samples) == 0

    def test_mixed_records(self):
        """混合记录合成。"""
        synth = DataSynthesizer()
        records = [
            self._make_record(0.05, correct=True),
            self._make_record(-0.03, correct=False),
            self._make_record(0.01, correct=True),
        ]
        samples = synth.synthesize(records)
        assert len(samples) == 3
        sources = {s.source for s in samples}
        assert "positive" in sources
        assert "negative" in sources
        assert "hard" in sources

    def test_get_summary(self):
        """合成样本摘要。"""
        synth = DataSynthesizer()
        records = [
            self._make_record(0.05, correct=True),
            self._make_record(-0.03, correct=False),
        ]
        samples = synth.synthesize(records)
        summary = synth.get_summary(samples)
        assert summary["n_samples"] == 2
        assert summary["n_positive"] == 1
        assert summary["n_negative"] == 1

    def test_get_summary_empty(self):
        """空样本摘要。"""
        synth = DataSynthesizer()
        summary = synth.get_summary([])
        assert summary["n_samples"] == 0
        assert summary["avg_weight"] == 0


# ============================================================
# DynamicStopLossManager 测试
# ============================================================

class TestDynamicStopLossManager:
    """动态止盈止损管理器测试。"""

    def test_long_position(self):
        """多头止盈止损。"""
        mgr = DynamicStopLossManager()
        stops = mgr.compute(entry_price=10.0, atr=0.3, action="buy")
        assert stops.stop_loss < 10.0
        assert stops.take_profit > 10.0

    def test_short_position(self):
        """空头止盈止损。"""
        mgr = DynamicStopLossManager()
        stops = mgr.compute(entry_price=10.0, atr=0.3, action="sell")
        assert stops.stop_loss > 10.0
        assert stops.take_profit < 10.0

    def test_stop_loss_respects_max_loss(self):
        """止损不超过最大亏损限制。"""
        mgr = DynamicStopLossManager(max_loss_pct=0.02)
        stops = mgr.compute(entry_price=10.0, atr=1.0, action="buy")
        # 止损距离不超过 10.0 * 0.02 = 0.2
        assert 10.0 - stops.stop_loss <= 0.2 + 1e-6

    def test_time_decay_tightens_stop(self):
        """持仓时间衰减收紧止损。"""
        mgr = DynamicStopLossManager()
        early = mgr.compute(entry_price=10.0, atr=0.3, holding_days=0, action="buy")
        late = mgr.compute(entry_price=10.0, atr=0.3, holding_days=15, action="buy")
        # 持仓越久, 止损越紧 (距离越小)
        early_distance = 10.0 - early.stop_loss
        late_distance = 10.0 - late.stop_loss
        assert late_distance < early_distance

    def test_trend_strength_widens_profit(self):
        """强趋势放宽止盈。"""
        mgr = DynamicStopLossManager()
        weak = mgr.compute(entry_price=10.0, atr=0.3, trend_strength=0.0, action="buy")
        strong = mgr.compute(entry_price=10.0, atr=0.3, trend_strength=0.8, action="buy")
        assert strong.take_profit > weak.take_profit

    def test_position_size_decreases_with_risk(self):
        """风险越高仓位越小。"""
        mgr = DynamicStopLossManager()
        low_risk = mgr.compute(entry_price=10.0, atr=0.1, action="buy")
        high_risk = mgr.compute(entry_price=10.0, atr=0.5, action="buy")
        assert high_risk.position_size < low_risk.position_size

    def test_risk_score_bounded(self):
        """风险评分在 0-1 之间。"""
        mgr = DynamicStopLossManager()
        for atr in [0.01, 0.1, 0.5, 1.0, 5.0]:
            stops = mgr.compute(entry_price=10.0, atr=atr, action="buy")
            assert 0 <= stops.risk_score <= 1

    def test_invalid_price_raises(self):
        """无效价格抛出 ValueError。"""
        mgr = DynamicStopLossManager()
        try:
            mgr.compute(entry_price=0, atr=0.3, action="buy")
            raise AssertionError("应抛出 ValueError")
        except ValueError:
            pass

    def test_negative_atr_raises(self):
        """负 ATR 抛出 ValueError。"""
        mgr = DynamicStopLossManager()
        try:
            mgr.compute(entry_price=10.0, atr=-0.3, action="buy")
            raise AssertionError("应抛出 ValueError")
        except ValueError:
            pass

    def test_trailing_stop_set(self):
        """移动止损被设置。"""
        mgr = DynamicStopLossManager()
        stops = mgr.compute(entry_price=10.0, atr=0.3, action="buy")
        assert stops.trailing_stop is not None

    def test_reason_present(self):
        """调整理由非空。"""
        mgr = DynamicStopLossManager()
        stops = mgr.compute(entry_price=10.0, atr=0.3, action="buy")
        assert len(stops.reason) > 0


# ============================================================
# TradingGroupReflector 测试
# ============================================================

class TestTradingGroupReflector:
    """TradingGroup 自反思引擎测试。"""

    def test_reflect_correct_profitable(self):
        """正确且盈利的决策 → EXCELLENT。"""
        reflector = TradingGroupReflector()
        record = reflector.reflect(
            decision={"decision_id": "d1", "ticker": "A", "action": "buy", "entry_price": 10.0},
            outcome={"exit_price": 10.5, "return": 0.05, "timestamp": "2026-08-23"},
        )
        assert record.grade == ReflectionGrade.EXCELLENT
        assert record.error_type == ErrorType.NO_ERROR
        assert record.is_correct

    def test_reflect_wrong_direction_loss(self):
        """方向错误导致亏损 → SIGNAL_ERROR。"""
        reflector = TradingGroupReflector()
        record = reflector.reflect(
            decision={"decision_id": "d1", "ticker": "A", "action": "buy", "entry_price": 10.0},
            outcome={"exit_price": 9.5, "return": -0.05, "timestamp": "2026-08-23"},
        )
        assert record.error_type == ErrorType.SIGNAL_ERROR
        assert record.grade == ReflectionGrade.POOR

    def test_reflect_large_loss_risk_error(self):
        """大幅亏损 → RISK_ERROR。"""
        reflector = TradingGroupReflector()
        record = reflector.reflect(
            decision={"decision_id": "d1", "ticker": "A", "action": "buy", "entry_price": 10.0},
            outcome={"exit_price": 9.0, "return": -0.10, "timestamp": "2026-08-23"},
        )
        assert record.error_type == ErrorType.RISK_ERROR
        assert record.grade == ReflectionGrade.BAD

    def test_reflect_short_profitable(self):
        """空头盈利。"""
        reflector = TradingGroupReflector()
        record = reflector.reflect(
            decision={"decision_id": "d1", "ticker": "A", "action": "sell", "entry_price": 10.0},
            outcome={"exit_price": 9.5, "return": 0.05, "timestamp": "2026-08-23"},
        )
        assert record.grade == ReflectionGrade.EXCELLENT

    def test_reflect_neutral_return(self):
        """中性收益。"""
        reflector = TradingGroupReflector()
        record = reflector.reflect(
            decision={"decision_id": "d1", "ticker": "A", "action": "buy", "entry_price": 10.0},
            outcome={"exit_price": 10.005, "return": 0.0005, "timestamp": "2026-08-23"},
        )
        assert record.grade == ReflectionGrade.NEUTRAL

    def test_reflect_none_return(self):
        """无结果数据。"""
        reflector = TradingGroupReflector()
        record = reflector.reflect(
            decision={"decision_id": "d1", "ticker": "A", "action": "buy", "entry_price": 10.0},
            outcome={},
        )
        assert record.grade == ReflectionGrade.NEUTRAL
        assert record.outcome_return is None

    def test_reflect_stores_history(self):
        """反思记录存入历史。"""
        reflector = TradingGroupReflector()
        for i in range(3):
            reflector.reflect(
                decision={"decision_id": f"d{i}", "ticker": "A", "action": "buy", "entry_price": 10.0},
                outcome={"return": 0.05},
            )
        summary = reflector.get_reflection_summary()
        assert summary["n_records"] == 3

    def test_synthesize_data_from_history(self):
        """从历史合成数据。"""
        reflector = TradingGroupReflector()
        reflector.reflect(
            decision={"decision_id": "d1", "ticker": "A", "action": "buy", "entry_price": 10.0},
            outcome={"return": 0.05},
        )
        reflector.reflect(
            decision={"decision_id": "d2", "ticker": "A", "action": "buy", "entry_price": 10.0},
            outcome={"return": -0.05},
        )
        samples = reflector.synthesize_data()
        assert len(samples) == 2

    def test_compute_dynamic_stops(self):
        """计算动态止盈止损。"""
        reflector = TradingGroupReflector()
        stops = reflector.compute_dynamic_stops(
            entry_price=10.0, atr=0.3, action="buy",
        )
        assert stops.stop_loss < 10.0
        assert stops.take_profit > 10.0

    def test_reflection_summary_empty(self):
        """空历史摘要。"""
        reflector = TradingGroupReflector()
        summary = reflector.get_reflection_summary()
        assert summary["n_records"] == 0

    def test_reflection_summary_with_records(self):
        """有记录的摘要。"""
        reflector = TradingGroupReflector()
        reflector.reflect(
            decision={"decision_id": "d1", "ticker": "A", "action": "buy", "entry_price": 10.0},
            outcome={"return": 0.05},
        )
        reflector.reflect(
            decision={"decision_id": "d2", "ticker": "A", "action": "buy", "entry_price": 10.0},
            outcome={"return": -0.03},
        )
        summary = reflector.get_reflection_summary()
        assert summary["n_records"] == 2
        assert "grade_counts" in summary
        assert "error_counts" in summary
        assert 0 <= summary["win_rate"] <= 1

    def test_improvement_suggestions_empty(self):
        """空历史无建议。"""
        reflector = TradingGroupReflector()
        assert reflector.get_improvement_suggestions() == []

    def test_improvement_suggestions_good(self):
        """良好决策的建议。"""
        reflector = TradingGroupReflector()
        reflector.reflect(
            decision={"decision_id": "d1", "ticker": "A", "action": "buy", "entry_price": 10.0},
            outcome={"return": 0.05},
        )
        suggestions = reflector.get_improvement_suggestions()
        assert len(suggestions) > 0
        assert "良好" in suggestions[0]

    def test_improvement_suggestions_with_errors(self):
        """多次错误生成改进建议。"""
        reflector = TradingGroupReflector()
        for i in range(4):
            reflector.reflect(
                decision={"decision_id": f"d{i}", "ticker": "A", "action": "buy", "entry_price": 10.0},
                outcome={"return": -0.05},
            )
        suggestions = reflector.get_improvement_suggestions()
        assert any("信号" in s for s in suggestions)

    def test_market_state_stored(self):
        """市场状态被存储。"""
        reflector = TradingGroupReflector()
        record = reflector.reflect(
            decision={"decision_id": "d1", "ticker": "A", "action": "buy", "entry_price": 10.0},
            outcome={"return": 0.05},
            market_state={"volatility": 0.02, "trend": "up"},
        )
        assert record.market_state["volatility"] == 0.02
        assert record.market_state["trend"] == "up"


# ============================================================
# 端到端集成测试
# ============================================================

class TestEndToEnd:
    """端到端集成测试。"""

    def test_full_reflection_cycle(self):
        """完整自反思周期: 决策 → 反思 → 合成 → 止损。"""
        reflector = TradingGroupReflector()

        decisions = [
            ({"decision_id": "d1", "ticker": "000001.SZ", "action": "buy", "entry_price": 10.0},
             {"exit_price": 10.5, "return": 0.05, "timestamp": "2026-08-23"}),
            ({"decision_id": "d2", "ticker": "600519.SH", "action": "buy", "entry_price": 1500.0},
             {"exit_price": 1450.0, "return": -0.033, "timestamp": "2026-08-23"}),
            ({"decision_id": "d3", "ticker": "000858.SZ", "action": "sell", "entry_price": 200.0},
             {"exit_price": 195.0, "return": 0.025, "timestamp": "2026-08-23"}),
        ]

        for decision, outcome in decisions:
            reflector.reflect(decision, outcome, {"volatility": 0.02})

        summary = reflector.get_reflection_summary()
        assert summary["n_records"] == 3

        samples = reflector.synthesize_data()
        assert len(samples) == 3

        stops = reflector.compute_dynamic_stops(
            entry_price=10.0, atr=0.3, trend_strength=0.6, holding_days=5, action="buy",
        )
        assert stops.stop_loss < 10.0
        assert stops.take_profit > 10.0

        suggestions = reflector.get_improvement_suggestions()
        assert len(suggestions) > 0

    def test_synthesize_with_external_records(self):
        """使用外部记录合成数据。"""
        reflector = TradingGroupReflector()
        external_records = [
            ReflectionRecord(
                decision_id="e1", ticker="A", action="buy", entry_price=10.0,
                outcome_return=0.05, error_type=ErrorType.NO_ERROR,
            ),
            ReflectionRecord(
                decision_id="e2", ticker="A", action="buy", entry_price=10.0,
                outcome_return=-0.05, error_type=ErrorType.SIGNAL_ERROR,
            ),
        ]
        samples = reflector.synthesize_data(external_records)
        assert len(samples) == 2
