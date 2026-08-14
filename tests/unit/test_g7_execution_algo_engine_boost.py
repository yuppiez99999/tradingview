"""G7 覆盖率冲刺 — utils/execution_algo_engine.py 单元测试.

目标: 覆盖率从 24.75% → ≥70%

测试范围:
    1. AlgoType enum / ExecutionSlice / ExecutionPlan dataclass
    2. ExecutionAlgoEngine.__init__
    3. plan_order: total_shares<=0 / 6 种算法 / current_price 预估滑点
    4. select_algo: 极小单 / high / low / medium 各分支
    5. _time_to_minute_idx: 5 个时段分支
    6. _estimate_slippage_bps: 正常 / avg<=0
    7. _algo_notes: 有/无 avg_daily_volume
    8. save_plan: 正常 / 异常

运行:
    python -m pytest tests/unit/test_g7_execution_algo_engine_boost.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import time
from unittest.mock import patch

import pytest

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.execution_algo_engine import (  # noqa: E402
    AFTERNOON_START,
    MORNING_END,
    MORNING_START,
    AlgoType,
    ExecutionAlgoEngine,
    ExecutionPlan,
    ExecutionSlice,
)

# ============================================================
# 1. dataclass / enum
# ============================================================


class TestAlgoType:
    def test_enum_values(self) -> None:
        assert AlgoType.TWAP.value == "TWAP"
        assert AlgoType.VWAP.value == "VWAP"
        assert AlgoType.POV.value == "POV"
        assert AlgoType.IS.value == "IS"
        assert AlgoType.AC.value == "AC"
        assert AlgoType.DARK.value == "DARK"

    def test_enum_is_str(self) -> None:
        assert isinstance(AlgoType.TWAP, str)


class TestExecutionSlice:
    def test_defaults(self) -> None:
        s = ExecutionSlice(
            slice_idx=0, start_time="09:30", end_time="09:35",
            target_shares=100, accumulated_shares=0, remaining_shares=100,
        )
        assert s.participation_rate == 0.0
        assert s.limit_price is None


class TestExecutionPlan:
    def test_defaults(self) -> None:
        p = ExecutionPlan()
        assert p.plan_id == ""
        assert p.slices == []
        assert p.expected_vwap is None


# ============================================================
# 2. ExecutionAlgoEngine.__init__
# ============================================================


class TestEngineInit:
    def test_defaults(self) -> None:
        eng = ExecutionAlgoEngine()
        assert eng.default_slice_minutes == 5
        assert eng.max_participation_rate == 0.15
        assert eng.min_slice_shares == 100

    def test_custom(self) -> None:
        eng = ExecutionAlgoEngine(default_slice_minutes=10, max_participation_rate=0.2, min_slice_shares=50)
        assert eng.default_slice_minutes == 10
        assert eng.max_participation_rate == 0.2
        assert eng.min_slice_shares == 50


# ============================================================
# 3. plan_order
# ============================================================


class TestPlanOrder:
    def test_zero_shares_returns_empty(self) -> None:
        eng = ExecutionAlgoEngine()
        plan = eng.plan_order(AlgoType.TWAP, "600519", "buy", 0)
        assert plan.total_shares == 0
        assert plan.slices == []

    def test_negative_shares_returns_empty(self) -> None:
        eng = ExecutionAlgoEngine()
        plan = eng.plan_order(AlgoType.TWAP, "600519", "buy", -100)
        assert plan.total_shares == 0

    @pytest.mark.parametrize("algo", [
        AlgoType.TWAP, AlgoType.VWAP, AlgoType.POV, AlgoType.IS, AlgoType.AC,
    ])
    def test_each_algo_produces_slices(self, algo: AlgoType) -> None:
        eng = ExecutionAlgoEngine()
        plan = eng.plan_order(algo, "600519", "buy", 10000, duration_minutes=60, slice_minutes=5)
        assert plan.algo == algo.value
        assert plan.symbol == "600519"
        assert plan.side == "buy"
        assert plan.total_shares == 10000
        assert len(plan.slices) >= 1
        # 累计下单 = 总数 (允许最后一片修正)
        total = sum(s.target_shares for s in plan.slices)
        assert total == 10000

    def test_dark_iceberg_produces_slices(self) -> None:
        # DARK 暗池: 每片固定 100 股, 受交易时段限制总下单量可能 < total
        eng = ExecutionAlgoEngine()
        plan = eng.plan_order(AlgoType.DARK, "600519", "buy", 10000, duration_minutes=60, slice_minutes=5)
        assert plan.algo == "DARK"
        assert len(plan.slices) >= 1
        # 每片 (除最后一片) 应为固定 100 股
        for s in plan.slices[:-1]:
            assert s.target_shares == 100

    def test_current_price_estimates_slippage(self) -> None:
        eng = ExecutionAlgoEngine()
        plan = eng.plan_order(
            AlgoType.TWAP, "600519", "buy", 10000,
            duration_minutes=60, slice_minutes=5,
            current_price=100.0, avg_daily_volume=1_000_000,
        )
        assert plan.expected_slippage_bps > 0
        assert plan.expected_cost > 0

    def test_plan_id_format(self) -> None:
        eng = ExecutionAlgoEngine()
        plan = eng.plan_order(AlgoType.TWAP, "600519", "sell", 1000)
        assert plan.plan_id.startswith("TWAP_600519_sell_")

    def test_unsupported_algo_raises(self) -> None:
        eng = ExecutionAlgoEngine()
        # "UNKNOWN" 是 str, algo.value 在 plan_id 构造时抛 AttributeError
        with pytest.raises((ValueError, AttributeError)):
            eng.plan_order("UNKNOWN", "600519", "buy", 1000)  # type: ignore[arg-type]

    def test_vwap_fallback_to_twap_when_zero_weight(self) -> None:
        # 全零 volume_curve → total_weight=0 → 回退 TWAP
        eng = ExecutionAlgoEngine()
        plan = eng.plan_order(
            AlgoType.VWAP, "600519", "buy", 1000,
            duration_minutes=30, slice_minutes=5,
            volume_curve=[0.0] * 240,
        )
        assert len(plan.slices) >= 1

    def test_ac_zero_risk_aversion_falls_back_twap(self) -> None:
        # risk_aversion=0 → kappa=0 → 退化为 TWAP
        eng = ExecutionAlgoEngine()
        plan = eng.plan_order(
            AlgoType.AC, "600519", "buy", 1000,
            duration_minutes=30, slice_minutes=5,
            risk_aversion=0.0,
        )
        assert len(plan.slices) >= 1


# ============================================================
# 4. select_algo
# ============================================================


class TestSelectAlgo:
    def test_tiny_order_twap(self) -> None:
        eng = ExecutionAlgoEngine()
        # participation <= 0.001 → TWAP
        assert eng.select_algo(100, 1_000_000) == AlgoType.TWAP

    def test_high_urgency_small_is(self) -> None:
        eng = ExecutionAlgoEngine()
        # high + participation < 0.05 → IS
        assert eng.select_algo(40_000, 1_000_000, urgency="high") == AlgoType.IS

    def test_high_urgency_large_ac(self) -> None:
        eng = ExecutionAlgoEngine()
        # high + participation >= 0.05 → AC
        assert eng.select_algo(60_000, 1_000_000, urgency="high") == AlgoType.AC

    def test_low_urgency_large_pov(self) -> None:
        eng = ExecutionAlgoEngine()
        # low + participation > 0.10 → POV
        assert eng.select_algo(150_000, 1_000_000, urgency="low") == AlgoType.POV

    def test_low_urgency_small_vwap(self) -> None:
        eng = ExecutionAlgoEngine()
        # low + participation <= 0.10 → VWAP
        assert eng.select_algo(50_000, 1_000_000, urgency="low") == AlgoType.VWAP

    def test_medium_urgency_large_pov(self) -> None:
        eng = ExecutionAlgoEngine()
        # medium + participation > 0.15 → POV
        assert eng.select_algo(200_000, 1_000_000, urgency="medium") == AlgoType.POV

    def test_medium_urgency_mid_ac(self) -> None:
        eng = ExecutionAlgoEngine()
        # medium + 0.05 < participation <= 0.15 → AC
        assert eng.select_algo(80_000, 1_000_000, urgency="medium") == AlgoType.AC

    def test_medium_urgency_small_vwap(self) -> None:
        eng = ExecutionAlgoEngine()
        # medium + participation <= 0.05 → VWAP
        assert eng.select_algo(30_000, 1_000_000, urgency="medium") == AlgoType.VWAP


# ============================================================
# 5. _time_to_minute_idx
# ============================================================


class TestTimeToMinuteIdx:
    def test_before_open(self) -> None:
        eng = ExecutionAlgoEngine()
        # t < MORNING_START (9:30) → 0
        assert eng._time_to_minute_idx(time(9, 0)) == 0

    def test_morning_session(self) -> None:
        eng = ExecutionAlgoEngine()
        # 9:30 → 0, 10:00 → 30
        assert eng._time_to_minute_idx(time(9, 30)) == 0
        assert eng._time_to_minute_idx(time(10, 0)) == 30

    def test_lunch_break(self) -> None:
        eng = ExecutionAlgoEngine()
        # 11:30-13:00 → 120 (跳到下午开始)
        assert eng._time_to_minute_idx(time(12, 0)) == 120

    def test_afternoon_session(self) -> None:
        eng = ExecutionAlgoEngine()
        # 13:00 → 120, 14:00 → 180
        assert eng._time_to_minute_idx(time(13, 0)) == 120
        assert eng._time_to_minute_idx(time(14, 0)) == 180

    def test_after_close(self) -> None:
        eng = ExecutionAlgoEngine()
        # t > 15:00 → 239
        assert eng._time_to_minute_idx(time(16, 0)) == 239


# ============================================================
# 6. _estimate_slippage_bps
# ============================================================


class TestEstimateSlippage:
    def test_normal(self) -> None:
        eng = ExecutionAlgoEngine()
        # participation = 10000/1000000 = 0.01 → 10*sqrt(0.01) = 1.0
        bps = eng._estimate_slippage_bps(10_000, 1_000_000)
        assert bps == pytest.approx(1.0)

    def test_zero_adv_returns_50(self) -> None:
        eng = ExecutionAlgoEngine()
        assert eng._estimate_slippage_bps(10_000, 0) == 50.0

    def test_negative_adv_returns_50(self) -> None:
        eng = ExecutionAlgoEngine()
        assert eng._estimate_slippage_bps(10_000, -100) == 50.0


# ============================================================
# 7. _algo_notes
# ============================================================


class TestAlgoNotes:
    def test_with_adv(self) -> None:
        eng = ExecutionAlgoEngine()
        notes = eng._algo_notes(AlgoType.TWAP, 10_000, 1_000_000)
        assert "时间加权" in notes
        assert "参与率" in notes

    def test_without_adv(self) -> None:
        eng = ExecutionAlgoEngine()
        notes = eng._algo_notes(AlgoType.TWAP, 10_000, None)
        assert "时间加权" in notes
        assert "参与率" not in notes

    def test_zero_adv_no_participation(self) -> None:
        eng = ExecutionAlgoEngine()
        notes = eng._algo_notes(AlgoType.VWAP, 10_000, 0)
        assert "按日内成交量" in notes
        assert "参与率" not in notes

    def test_unknown_algo_empty_base(self) -> None:
        eng = ExecutionAlgoEngine()
        notes = eng._algo_notes("UNKNOWN", 10_000, None)  # type: ignore[arg-type]
        assert notes == ""


# ============================================================
# 8. save_plan
# ============================================================


class TestSavePlan:
    def test_save_normal(self, tmp_path) -> None:
        eng = ExecutionAlgoEngine()
        plan = eng.plan_order(AlgoType.TWAP, "600519", "buy", 1000, duration_minutes=30)
        path = eng.save_plan(plan)
        assert path.exists()
        assert path.suffix == ".json"

    def test_save_failure_logged(self) -> None:
        eng = ExecutionAlgoEngine()
        plan = ExecutionPlan(plan_id="test_fail", symbol="600519")
        # patch open 抛 OSError → 走 except 分支
        with patch("builtins.open", side_effect=OSError("disk full")):
            path = eng.save_plan(plan)
        # 返回路径对象但不抛异常 (fail-safe)
        assert path.name == "test_fail.json"


# ============================================================
# 9. 内部规划器 (覆盖各算法核心逻辑)
# ============================================================


class TestInternalPlanners:
    def test_twap_accumulation(self) -> None:
        eng = ExecutionAlgoEngine()
        slices = eng._plan_twap(1000, 60, 5, MORNING_START)
        assert len(slices) >= 1
        assert sum(s.target_shares for s in slices) == 1000

    def test_vwap_with_curve(self) -> None:
        eng = ExecutionAlgoEngine()
        from utils.execution_algo_engine import DEFAULT_INTRADAY_VOLUME_CURVE
        slices = eng._plan_vwap(1000, 60, 5, MORNING_START, DEFAULT_INTRADAY_VOLUME_CURVE)
        assert len(slices) >= 1
        assert sum(s.target_shares for s in slices) == 1000

    def test_pov_participation_rate(self) -> None:
        eng = ExecutionAlgoEngine()
        slices = eng._plan_pov(1000, 60, 5, MORNING_START, 1_000_000)
        assert len(slices) >= 1
        # 每片都带 participation_rate
        for s in slices:
            assert s.participation_rate >= 0

    def test_implementation_shortfall_front_loaded(self) -> None:
        eng = ExecutionAlgoEngine()
        slices = eng._plan_implementation_shortfall(1000, 60, 5, MORNING_START, 100.0, 0.25, 2.0)
        assert len(slices) >= 1
        # front-loaded: 第一片应 >= 最后一片
        assert slices[0].target_shares >= slices[-1].target_shares

    def test_almgren_chriss_normal(self) -> None:
        eng = ExecutionAlgoEngine()
        slices = eng._plan_almgren_chriss(1000, 60, 5, MORNING_START, 100.0, 0.25, 1.0)
        assert len(slices) >= 1
        assert sum(s.target_shares for s in slices) == 1000

    def test_dark_iceberg_fixed_size(self) -> None:
        eng = ExecutionAlgoEngine()
        slices = eng._plan_dark_iceberg(1000, 60, 5, MORNING_START)
        assert len(slices) >= 1
        # 暗池: 每片固定 100 股 (除最后一片)
        for s in slices[:-1]:
            assert s.target_shares == 100

    def test_twap_skips_lunch_break(self) -> None:
        eng = ExecutionAlgoEngine()
        # 从 11:00 开始, 30 分钟, 5 分钟片 → 应跳过 11:30-13:00 午休
        slices = eng._plan_twap(1000, 30, 5, time(11, 0))
        # 至少有一片, 且不应有 11:30-13:00 之间的 start_time
        for s in slices:
            assert not (MORNING_END <= time.fromisoformat(s.start_time) < AFTERNOON_START)
