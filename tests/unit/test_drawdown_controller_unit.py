"""drawdown_controller 单元测试 — 组合回撤四级响应控制器全覆盖.

被测模块: utils/drawdown_controller.py
覆盖目标: >=95%
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.drawdown_controller import DrawdownController  # noqa: E402

# ============================================================
# check_drawdown — 各级别
# ============================================================


class TestCheckDrawdown:
    def setup_method(self):
        self.dc = DrawdownController()

    def test_level_0_normal(self):
        r = self.dc.check_drawdown(5_000_000, 4_900_000)
        assert r["level"] == 0
        assert r["level_name"] == "正常"
        assert r["build_allowed"] is True

    def test_level_1_warning(self):
        r = self.dc.check_drawdown(5_000_000, 4_700_000)
        assert r["level"] == 1
        assert r["level_name"] == "预警审查"
        assert r["build_allowed"] is True
        assert r["hedge_ratio_target"] == 0.50

    def test_level_2_first_defense(self):
        r = self.dc.check_drawdown(5_000_000, 4_600_000)
        assert r["level"] == 2
        assert r["level_name"] == "一级防御"
        assert r["build_allowed"] is False
        assert r["spot_reduce_pct"] == 0.20
        assert r["hedge_ratio_target"] == 0.60
        assert r["options_selling_allowed"] is False

    def test_level_3_second_defense(self):
        r = self.dc.check_drawdown(5_000_000, 4_300_000)
        assert r["level"] == 3
        assert r["level_name"] == "二级防御"
        assert r["spot_reduce_pct"] == 0.40
        assert r["hedge_ratio_target"] == 0.80
        assert r["quant_neutral_scale"] == 0.5

    def test_level_4_extreme(self):
        r = self.dc.check_drawdown(5_000_000, 4_000_000)
        assert r["level"] == 4
        assert r["level_name"] == "极限防御"
        assert r["spot_reduce_pct"] == 0.60
        assert r["cash_target_pct"] == 0.60
        assert r["quant_neutral_scale"] == 0.0

    def test_drawdown_pct_correct(self):
        r = self.dc.check_drawdown(5_000_000, 4_600_000)
        assert r["drawdown_pct"] == pytest.approx(-0.08)

    def test_drawdown_amount_correct(self):
        r = self.dc.check_drawdown(5_000_000, 4_600_000)
        assert r["drawdown_amount"] == -400_000


# ============================================================
# check_drawdown — high_water_mark
# ============================================================


class TestHighWaterMark:
    def test_hwm_takes_priority(self):
        dc = DrawdownController()
        r = dc.check_drawdown(4_000_000, 4_600_000, high_water_mark=5_000_000)
        assert r["level"] == 2
        assert r["peak_value"] == 5_000_000

    def test_hwm_none_uses_peak(self):
        dc = DrawdownController()
        r = dc.check_drawdown(5_000_000, 4_600_000, high_water_mark=None)
        assert r["peak_value"] == 5_000_000


# ============================================================
# check_drawdown — fail-closed
# ============================================================


class TestFailClosed:
    def test_zero_peak(self):
        dc = DrawdownController()
        r = dc.check_drawdown(0, 4_600_000)
        assert r["level"] == 4
        assert r["build_allowed"] is False

    def test_negative_peak(self):
        dc = DrawdownController()
        r = dc.check_drawdown(-100, 4_600_000)
        assert r["level"] == 4

    def test_zero_hwm(self):
        dc = DrawdownController()
        r = dc.check_drawdown(5_000_000, 4_600_000, high_water_mark=0)
        assert r["level"] == 4


# ============================================================
# execute_response
# ============================================================


class TestExecuteResponse:
    def setup_method(self):
        self.dc = DrawdownController()

    def test_level_1(self):
        r = self.dc.execute_response(1)
        assert r["executed"] is True
        assert r["level"] == 1
        assert len(r["actions_taken"]) >= 1

    def test_level_2(self):
        r = self.dc.execute_response(2)
        assert r["executed"] is True
        assert any(a["action"] == "reduce_stock_position" for a in r["actions_taken"])

    def test_level_3(self):
        r = self.dc.execute_response(3)
        assert r["executed"] is True
        assert any(a["action"] == "halve_quant_neutral" for a in r["actions_taken"])

    def test_level_4(self):
        r = self.dc.execute_response(4)
        assert r["executed"] is True
        assert any(a["action"] == "launch_exit_assessment" for a in r["actions_taken"])

    def test_invalid_level(self):
        r = self.dc.execute_response(0)
        assert r["executed"] is False
        assert r["reason"] == "invalid_level"

    def test_invalid_level_5(self):
        r = self.dc.execute_response(5)
        assert r["executed"] is False


# ============================================================
# get_event_history
# ============================================================


class TestEventHistory:
    def test_empty_history(self, tmp_path, monkeypatch):
        dc = DrawdownController()
        monkeypatch.setattr(
            "utils.drawdown_controller.LOG_FILE", tmp_path / "events.jsonl"
        )
        assert dc.get_event_history(30) == []

    def test_history_after_event(self, tmp_path, monkeypatch):
        log_file = tmp_path / "events.jsonl"
        monkeypatch.setattr("utils.drawdown_controller.LOG_FILE", log_file)
        dc = DrawdownController()
        dc.check_drawdown(5_000_000, 4_600_000)
        history = dc.get_event_history(30)
        assert len(history) >= 1
        assert history[0]["level"] == 2
