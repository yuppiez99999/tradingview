"""2030-Q4 清仓日历 shadow 只读渲染器单测 — v9.5 缺口④

覆盖:
    T01 未激活（2030-09-30, 首步前一天）
    T02 阶梯一激活（2030-10-01, 权益上限 30%）
    T03 阶梯二激活（2030-11-15, 权益上限 15%）
    T04 阶梯三强制清算（2030-12-15, 权益 0%, force=True）
    T05 阶梯三后（2030-12-31, 仍强制清算）
    T06 边界：09-30 → 未激活, 10-01 → 阶梯一
    T07 days_to_next_step 计算
    T08 超限告警（当前权益 > 上限）
    T09 自定义日历参数
    T10 to_dict 可序列化
    T11 与 batch_state_machine final_stage 衔接
"""

from __future__ import annotations

import json

from utils.etf_option_combo.clearance_schedule import (
    ClearanceSchedule,
    evaluate_clearance,
)


class TestClearanceSchedule:
    def test_t01_not_active_before_first_step(self):
        d = evaluate_clearance("2030-09-30")
        assert d.active is False
        assert d.step is None
        assert d.equity_cap_pct == 1.0
        assert d.force_liquidation is False

    def test_t02_step1_active(self):
        d = evaluate_clearance("2030-10-01")
        assert d.active is True
        assert d.step == "阶梯一"
        assert d.equity_cap_pct == 0.30
        assert d.force_liquidation is False

    def test_t03_step2_active(self):
        d = evaluate_clearance("2030-11-15")
        assert d.active is True
        assert d.step == "阶梯二"
        assert d.equity_cap_pct == 0.15

    def test_t04_step3_force_liquidation(self):
        d = evaluate_clearance("2030-12-15")
        assert d.active is True
        assert d.step == "阶梯三"
        assert d.equity_cap_pct == 0.0
        assert d.force_liquidation is True

    def test_t05_after_step3_still_forced(self):
        d = evaluate_clearance("2030-12-31")
        assert d.step == "阶梯三"
        assert d.force_liquidation is True


class TestBoundaries:
    def test_t06_step1_boundary(self):
        assert evaluate_clearance("2030-09-30").active is False
        assert evaluate_clearance("2030-10-01").active is True

    def test_t07_days_to_next_step(self):
        d = evaluate_clearance("2030-09-30")
        assert d.days_to_next_step == 1
        assert d.next_step_date == "2030-10-01"

        d2 = evaluate_clearance("2030-10-15")
        assert d2.days_to_next_step == 31
        assert d2.next_step_date == "2030-11-15"

        d3 = evaluate_clearance("2030-12-15")
        assert d3.days_to_next_step is None
        assert d3.next_step_date is None


class TestOverLimit:
    def test_t08_over_limit_warning(self):
        d = evaluate_clearance("2030-10-01", current_equity_pct=0.45)
        assert "超限告警" in d.note
        assert "45.0%" in d.note

        d2 = evaluate_clearance("2030-10-01", current_equity_pct=0.25)
        assert "超限告警" not in d2.note


class TestCustomAndInterop:
    def test_t09_custom_schedule(self):
        custom = ClearanceSchedule(
            step1_date="2029-10-01",
            step1_equity_cap=0.25,
            step2_date="2029-11-15",
            step2_equity_cap=0.10,
            step3_date="2029-12-15",
            step3_equity_cap=0.0,
        )
        d = evaluate_clearance("2029-10-01", schedule=custom)
        assert d.step == "阶梯一"
        assert d.equity_cap_pct == 0.25

    def test_t10_to_dict_serializable(self):
        d = evaluate_clearance("2030-10-01")
        s = json.dumps(d.to_dict(), ensure_ascii=False)
        assert json.loads(s)["step"] == "阶梯一"

    def test_t11_final_stage_handoff(self):
        """batch_state_machine final_stage_start=2029-10-01 → 清仓日历 2030-10-01。"""
        sch = ClearanceSchedule()
        assert sch.final_stage_start == "2029-10-01"
        assert sch.step1_date == "2030-10-01"
        d = evaluate_clearance("2029-10-01")
        assert d.active is False, "末期收官 2029Q4 ≠ 清仓激活, 清仓 2030Q4"
