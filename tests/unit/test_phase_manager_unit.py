"""test_phase_manager_unit.py — 十五五规划年度阶段管理器单元测试

覆盖要点:
    - ANNUAL_PHASES / LIQUIDATION_QUARTERLY_ACTIONS 常量
    - PhaseInfo / QuarterlyReviewResult dataclass
    - PhaseManager 构造
    - get_current_phase (pre_plan/post_plan/2026-2030/未定义年)
    - is_quarter_end (季度末/非季度末/最后几天)
    - get_current_quarter (Q1-Q4)
    - trigger_quarterly_review (季度末/非季度末/有持仓/无持仓/2030清仓)
    - is_liquidation_phase / get_liquidation_actions / get_liquidation_order
    - check_early_exit_trigger (15%/12%/8%/5%/<5%)
    - summary
"""
from __future__ import annotations

from datetime import date

import pytest

from utils.phase_manager import (
    ANNUAL_PHASES,
    LIQUIDATION_QUARTERLY_ACTIONS,
    PhaseInfo,
    PhaseManager,
    QuarterlyReviewResult,
)

# ============================================================
# 常量
# ============================================================


class TestConstants:
    @pytest.mark.unit
    def test_annual_phases_has_5_years(self):
        assert "2026" in ANNUAL_PHASES
        assert "2027" in ANNUAL_PHASES
        assert "2028" in ANNUAL_PHASES
        assert "2029" in ANNUAL_PHASES
        assert "2030" in ANNUAL_PHASES

    @pytest.mark.unit
    def test_liquidation_has_4_quarters(self):
        assert "Q1" in LIQUIDATION_QUARTERLY_ACTIONS
        assert "Q2" in LIQUIDATION_QUARTERLY_ACTIONS
        assert "Q3" in LIQUIDATION_QUARTERLY_ACTIONS
        assert "Q4" in LIQUIDATION_QUARTERLY_ACTIONS

    @pytest.mark.unit
    def test_2026_target_return(self):
        assert ANNUAL_PHASES["2026"]["target_return"] == 0.08

    @pytest.mark.unit
    def test_2030_is_exit(self):
        assert ANNUAL_PHASES["2030"]["name"] == "退出期"


# ============================================================
# Dataclass
# ============================================================


class TestPhaseInfo:
    @pytest.mark.unit
    def test_defaults(self):
        p = PhaseInfo(year="2026", phase_name="建仓期", period="2026", target_return=0.08,
                      max_drawdown=0.08, leverage_target=1.28)
        assert p.actions == {}
        assert p.is_liquidation_year is False
        assert p.liquidation_actions is None


class TestQuarterlyReviewResult:
    @pytest.mark.unit
    def test_defaults(self):
        r = QuarterlyReviewResult()
        assert r.is_quarter_end is False
        assert r.actions == []


# ============================================================
# PhaseManager 构造
# ============================================================


class TestInit:
    @pytest.mark.unit
    def test_init(self):
        pm = PhaseManager()
        assert "2026" in pm.phases


# ============================================================
# get_current_phase
# ============================================================


class TestGetCurrentPhase:
    @pytest.mark.unit
    def test_pre_plan(self):
        pm = PhaseManager()
        phase = pm.get_current_phase(date(2025, 1, 1))
        assert phase.year == "pre_plan"
        assert phase.target_return == 0.0

    @pytest.mark.unit
    def test_post_plan(self):
        pm = PhaseManager()
        phase = pm.get_current_phase(date(2031, 6, 1))
        assert phase.year == "post_plan"

    @pytest.mark.unit
    def test_2026(self):
        pm = PhaseManager()
        phase = pm.get_current_phase(date(2026, 8, 15))
        assert phase.year == "2026"
        assert phase.phase_name == "建仓期"
        assert phase.target_return == 0.08
        assert phase.current_quarter == "Q3"

    @pytest.mark.unit
    def test_2027(self):
        pm = PhaseManager()
        phase = pm.get_current_phase(date(2027, 3, 1))
        assert phase.year == "2027"
        assert phase.phase_name == "主线兑现期"

    @pytest.mark.unit
    def test_2028(self):
        pm = PhaseManager()
        phase = pm.get_current_phase(date(2028, 6, 1))
        assert phase.year == "2028"
        assert phase.phase_name == "分化期"

    @pytest.mark.unit
    def test_2029(self):
        pm = PhaseManager()
        phase = pm.get_current_phase(date(2029, 1, 1))
        assert phase.year == "2029"
        assert phase.phase_name == "去杠杆期"

    @pytest.mark.unit
    def test_2030_liquidation(self):
        pm = PhaseManager()
        phase = pm.get_current_phase(date(2030, 2, 15))
        assert phase.year == "2030"
        assert phase.is_liquidation_year is True
        assert phase.current_quarter == "Q1"
        assert phase.liquidation_actions is not None

    @pytest.mark.unit
    def test_2030_q2(self):
        pm = PhaseManager()
        phase = pm.get_current_phase(date(2030, 5, 1))
        assert phase.current_quarter == "Q2"
        assert phase.liquidation_actions is not None

    @pytest.mark.unit
    def test_undefined_year(self):
        pm = PhaseManager()
        phase = pm.get_current_phase(date(2025, 12, 31))
        # 2025 < PLAN_START_DATE → pre_plan
        assert phase.year == "pre_plan"


# ============================================================
# is_quarter_end
# ============================================================


class TestIsQuarterEnd:
    @pytest.mark.unit
    def test_march_last_day(self):
        pm = PhaseManager()
        assert pm.is_quarter_end(date(2026, 3, 31)) is True

    @pytest.mark.unit
    def test_june_last_day(self):
        pm = PhaseManager()
        assert pm.is_quarter_end(date(2026, 6, 30)) is True

    @pytest.mark.unit
    def test_september_last_day(self):
        pm = PhaseManager()
        assert pm.is_quarter_end(date(2026, 9, 30)) is True

    @pytest.mark.unit
    def test_december_last_day(self):
        pm = PhaseManager()
        assert pm.is_quarter_end(date(2026, 12, 31)) is True

    @pytest.mark.unit
    def test_non_quarter_month(self):
        pm = PhaseManager()
        assert pm.is_quarter_end(date(2026, 2, 28)) is False

    @pytest.mark.unit
    def test_mid_quarter_month(self):
        pm = PhaseManager()
        assert pm.is_quarter_end(date(2026, 3, 15)) is False

    @pytest.mark.unit
    def test_near_end(self):
        """倒数第2天也算季度末"""
        pm = PhaseManager()
        assert pm.is_quarter_end(date(2026, 3, 30)) is True


# ============================================================
# get_current_quarter
# ============================================================


class TestGetCurrentQuarter:
    @pytest.mark.unit
    def test_q1(self):
        pm = PhaseManager()
        assert pm.get_current_quarter(date(2026, 1, 15)) == "Q1"
        assert pm.get_current_quarter(date(2026, 3, 15)) == "Q1"

    @pytest.mark.unit
    def test_q2(self):
        pm = PhaseManager()
        assert pm.get_current_quarter(date(2026, 4, 15)) == "Q2"
        assert pm.get_current_quarter(date(2026, 6, 15)) == "Q2"

    @pytest.mark.unit
    def test_q3(self):
        pm = PhaseManager()
        assert pm.get_current_quarter(date(2026, 7, 15)) == "Q3"
        assert pm.get_current_quarter(date(2026, 9, 15)) == "Q3"

    @pytest.mark.unit
    def test_q4(self):
        pm = PhaseManager()
        assert pm.get_current_quarter(date(2026, 10, 15)) == "Q4"
        assert pm.get_current_quarter(date(2026, 12, 15)) == "Q4"


# ============================================================
# trigger_quarterly_review
# ============================================================


class TestQuarterlyReview:
    @pytest.mark.unit
    def test_not_quarter_end(self):
        pm = PhaseManager()
        result = pm.trigger_quarterly_review(today=date(2026, 8, 15))
        assert result.is_quarter_end is False
        assert result.stress_test_triggered is False

    @pytest.mark.unit
    def test_quarter_end(self):
        pm = PhaseManager()
        result = pm.trigger_quarterly_review(today=date(2026, 9, 30))
        assert result.is_quarter_end is True
        assert result.quarter == "Q3"

    @pytest.mark.unit
    def test_with_positions_rebalance(self):
        pm = PhaseManager()
        positions = [
            {"sector": "半导体", "weight": 0.20},
            {"sector": "半导体", "weight": 0.05},
        ]
        result = pm.trigger_quarterly_review(positions=positions, today=date(2026, 9, 30))
        assert result.rebalance_needed is True

    @pytest.mark.unit
    def test_with_positions_ok(self):
        pm = PhaseManager()
        positions = [{"sector": "半导体", "weight": 0.10}, {"sector": "医药", "weight": 0.10}]
        result = pm.trigger_quarterly_review(positions=positions, today=date(2026, 9, 30))
        assert result.rebalance_needed is False

    @pytest.mark.unit
    def test_2030_liquidation_actions(self):
        pm = PhaseManager()
        result = pm.trigger_quarterly_review(today=date(2030, 2, 28))
        assert any("2030 清仓" in a for a in result.actions)


# ============================================================
# 清仓流程
# ============================================================


class TestLiquidation:
    @pytest.mark.unit
    def test_is_liquidation_2030(self):
        pm = PhaseManager()
        assert pm.is_liquidation_phase(date(2030, 6, 1)) is True

    @pytest.mark.unit
    def test_not_liquidation_2029(self):
        pm = PhaseManager()
        assert pm.is_liquidation_phase(date(2029, 6, 1)) is False

    @pytest.mark.unit
    def test_get_liquidation_actions_q1(self):
        pm = PhaseManager()
        actions = pm.get_liquidation_actions(date(2030, 2, 1))
        assert actions is not None
        assert actions["name"] == "保留核心+方向性清零"

    @pytest.mark.unit
    def test_get_liquidation_actions_q4(self):
        pm = PhaseManager()
        actions = pm.get_liquidation_actions(date(2030, 11, 1))
        assert actions is not None
        assert actions["name"] == "纯现金+清算完成"

    @pytest.mark.unit
    def test_get_liquidation_actions_none(self):
        pm = PhaseManager()
        assert pm.get_liquidation_actions(date(2029, 6, 1)) is None

    @pytest.mark.unit
    def test_liquidation_order(self):
        pm = PhaseManager()
        order = pm.get_liquidation_order()
        assert len(order) == 6
        assert order[0] == "1_illiquid_small_cap"
        assert order[-1] == "6_futures_hedge_close"


# ============================================================
# check_early_exit_trigger
# ============================================================


class TestEarlyExit:
    @pytest.mark.unit
    def test_15pct(self):
        pm = PhaseManager()
        r = pm.check_early_exit_trigger(0.15)
        assert r["trigger"] == "drawdown_15pct"
        assert r["action"] == "defensive_mode"

    @pytest.mark.unit
    def test_12pct(self):
        pm = PhaseManager()
        r = pm.check_early_exit_trigger(0.12)
        assert r["trigger"] == "drawdown_12pct"

    @pytest.mark.unit
    def test_8pct(self):
        pm = PhaseManager()
        r = pm.check_early_exit_trigger(0.08)
        assert r["trigger"] == "drawdown_8pct"

    @pytest.mark.unit
    def test_5pct(self):
        pm = PhaseManager()
        r = pm.check_early_exit_trigger(0.05)
        assert r["trigger"] == "drawdown_5pct"

    @pytest.mark.unit
    def test_below_5pct(self):
        pm = PhaseManager()
        r = pm.check_early_exit_trigger(0.03)
        assert r is None

    @pytest.mark.unit
    def test_above_15pct(self):
        pm = PhaseManager()
        r = pm.check_early_exit_trigger(0.20)
        assert r["trigger"] == "drawdown_15pct"


# ============================================================
# summary
# ============================================================


class TestSummary:
    @pytest.mark.unit
    def test_summary_2026(self):
        pm = PhaseManager()
        s = pm.summary(date(2026, 8, 15))
        assert "建仓期" in s
        assert "2026" in s

    @pytest.mark.unit
    def test_summary_2030(self):
        pm = PhaseManager()
        s = pm.summary(date(2030, 2, 15))
        assert "2030" in s
        assert "清仓" in s
