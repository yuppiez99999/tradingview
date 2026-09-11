"""cash_manager 单元测试 — 现金管理器全覆盖.

被测模块: utils/cash_manager.py
覆盖目标: >=90%
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from unittest.mock import patch

from utils.cash_manager import (  # noqa: E402
    MIN_REPO_AMOUNT,
    CashAllocation,
    CashManager,
)

# ============================================================
# __init__
# ============================================================


class TestInit:
    def test_default(self):
        cm = CashManager()
        assert cm.total_cash == 1_300_000
        assert cm.yield_target > 0

    def test_custom(self):
        # P0 修复回归 (2026-09-09): 显式参数必须优先于 v10.0 全局配置。
        # 此前全局配置无条件覆盖显式参数 (参数优先级反转, 资金链路隐患),
        # 修复后显式传入的 total_cash / yield_target 应原样生效。
        cm = CashManager(total_cash=500_000, yield_target=0.03)
        assert cm.total_cash == 500_000
        assert cm.yield_target == 0.03

    def test_explicit_args_win_over_v10_config(self):
        """显式参数 > v10.0 配置: 即使配置文件存在也不覆盖显式传入值."""
        fake_cfg = {
            "capital": 9_999_999,
            "yield_target": 0.09,
            "allocation": {"futures_margin": 1.0},
            "instruments": {"reverse_repo": "FAKE"},
        }
        with patch("utils.cash_manager.V10ConfigLoader") as mock_loader:
            mock_loader.return_value.get_cash_config.return_value = fake_cfg
            cm = CashManager(total_cash=500_000, yield_target=0.03)
            assert cm.total_cash == 500_000
            assert cm.yield_target == 0.03
            # 未显式传入的字段仍由配置补齐
            assert cm.allocation == {"futures_margin": 1.0}
            assert cm.instruments["reverse_repo"] == "FAKE"

    def test_v10_config_fills_unset_fields(self):
        """未显式传入的字段由 v10.0 配置补齐 (向后兼容配置驱动行为)."""
        fake_cfg = {"capital": 2_000_000, "yield_target": 0.04}
        with patch("utils.cash_manager.V10ConfigLoader") as mock_loader:
            mock_loader.return_value.get_cash_config.return_value = fake_cfg
            cm = CashManager()
            assert cm.total_cash == 2_000_000
            assert cm.yield_target == 0.04


# ============================================================
# allocate_idle_cash
# ============================================================


class TestAllocateIdleCash:
    def test_basic(self):
        cm = CashManager()
        result = cm.allocate_idle_cash(
            total_cash=1_300_000,
            futures_margin_used=480_000,
            options_collateral_used=10_000,
            current_repo_rate=0.025,
        )
        assert isinstance(result, CashAllocation)
        assert result.total_cash == 1_300_000
        assert result.futures_margin > 0

    def test_high_rate(self):
        cm = CashManager()
        result = cm.allocate_idle_cash(
            total_cash=1_300_000,
            futures_margin_used=480_000,
            current_repo_rate=0.06,
        )
        assert "高利率" in result.reason or "加大" in result.reason

    def test_emergency_used(self):
        cm = CashManager()
        result = cm.allocate_idle_cash(
            total_cash=1_300_000,
            futures_margin_used=480_000,
            emergency_used=50_000,
        )
        assert result.emergency_replenish_needed is True

    def test_month_end(self):
        cm = CashManager()
        result = cm.allocate_idle_cash(
            total_cash=1_300_000,
            futures_margin_used=480_000,
            trade_date=date(2026, 8, 28),
        )
        assert result.is_month_end is True

    def test_quarter_end(self):
        cm = CashManager()
        result = cm.allocate_idle_cash(
            total_cash=1_300_000,
            futures_margin_used=480_000,
            trade_date=date(2026, 9, 28),
        )
        assert result.is_quarter_end is True
        assert result.is_month_end is True

    def test_low_idle_skip(self):
        cm = CashManager(total_cash=600_000)
        result = cm.allocate_idle_cash(
            total_cash=600_000,
            futures_margin_used=500_000,
            options_collateral_used=100_000,
        )
        assert result.idle_cash >= 0

    def test_repo_order_generated(self):
        cm = CashManager()
        result = cm.allocate_idle_cash(
            total_cash=1_300_000,
            futures_margin_used=480_000,
            current_repo_rate=0.025,
        )
        if result.reverse_repo >= MIN_REPO_AMOUNT:
            assert result.repo_order.get("action") == "place_repo_order"

    def test_income_positive(self):
        cm = CashManager()
        result = cm.allocate_idle_cash(
            total_cash=1_300_000,
            futures_margin_used=480_000,
            current_repo_rate=0.025,
        )
        assert result.estimated_daily_income >= 0

    def test_trade_date(self):
        cm = CashManager()
        result = cm.allocate_idle_cash(
            total_cash=1_300_000,
            futures_margin_used=480_000,
            trade_date=date(2026, 8, 17),
        )
        assert result.trade_date == "2026-08-17"


# ============================================================
# check_emergency_replenish
# ============================================================


class TestEmergencyReplenish:
    def test_no_action(self):
        cm = CashManager()
        r = cm.check_emergency_replenish(0)
        assert r["action"] == "no_action"

    def test_schedule(self):
        cm = CashManager()
        r = cm.check_emergency_replenish(50_000, last_used_date=date.today())
        assert r["action"] == "schedule_replenish"

    def test_overdue(self):
        cm = CashManager()
        from datetime import timedelta

        old_date = date.today() - timedelta(days=5)
        r = cm.check_emergency_replenish(50_000, last_used_date=old_date)
        assert r["action"] == "replenish_now"


# ============================================================
# check_margin_call
# ============================================================


class TestMarginCall:
    def test_normal(self):
        cm = CashManager()
        r = cm.check_margin_call(1_000_000, 400_000)
        assert r["action"] == "no_action"

    def test_margin_call(self):
        cm = CashManager()
        r = cm.check_margin_call(100_000, 80_000)
        assert "margin_call" in r["action"]

    def test_zero_account(self):
        cm = CashManager()
        r = cm.check_margin_call(0, 50_000)
        assert r["action"] == "no_action"


# ============================================================
# get_allocation_summary
# ============================================================


class TestAllocationSummary:
    def test_basic(self):
        cm = CashManager()
        s = cm.get_allocation_summary()
        assert "total_cash" in s
        assert "allocation" in s
        assert "allocation_pct" in s


# ============================================================
# summary
# ============================================================


class TestSummary:
    def test_basic(self):
        cm = CashManager()
        result = cm.allocate_idle_cash(
            total_cash=1_300_000,
            futures_margin_used=480_000,
        )
        s = cm.summary(result)
        assert "现金管理报告" in s


# ============================================================
# _is_month_end / _is_quarter_end
# ============================================================


class TestDateHelpers:
    def test_month_end(self):
        cm = CashManager()
        assert cm._is_month_end(date(2026, 8, 28)) is True
        assert cm._is_month_end(date(2026, 8, 10)) is False

    def test_quarter_end(self):
        cm = CashManager()
        assert cm._is_quarter_end(date(2026, 9, 28)) is True
        assert cm._is_quarter_end(date(2026, 8, 28)) is False
