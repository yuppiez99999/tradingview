"""stop_loss_monitor 单元测试 — 多头/空头/flat/无规则 (S2 修复验证)

覆盖:
  - 多头止损/止盈/移动止损 (回归)
  - 空头止损/止盈 (S2 新增)
  - flat → None, 无规则 → None
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import stop_loss_monitor as slm  # noqa: E402
from stop_loss_monitor import TriggerType  # noqa: E402


@pytest.fixture()
def monitor():
    """构造一个带测试规则的 StopLossMonitor, broker=None (MOCK)."""
    with patch.object(slm.StopLossMonitor, "__init__", lambda self: None):
        m = slm.StopLossMonitor()
    m.rules = {
        "600519": {
            "code": "600519.SH",
            "name": "贵州茅台",
            "entry_price": 100.0,
            "stop_loss_pct": -12.0,
            "take_profit_pct": 25.0,
            "trailing_stop": False,
            "atr_stop_loss_price": 0,
        }
    }
    m._high_water_mark = {}
    m._low_water_mark = {}
    m.trigger_history = []
    return m


# ==================== 多头 (回归) ====================


class TestLongPosition:
    """多头持仓: 价格下跌止损, 价格上涨止盈"""

    def test_long_stop_loss(self, monitor):
        r = monitor.check_position(
            "600519",
            {
                "shares": 100,
                "avg_cost": 100.0,
                "name": "MT",
                "current_price": 88.0,
            },
        )
        assert r is not None
        assert r.trigger_type == TriggerType.STOP_LOSS
        assert r.action == "SELL"
        assert r.shares == 100

    def test_long_take_profit(self, monitor):
        r = monitor.check_position(
            "600519",
            {
                "shares": 100,
                "avg_cost": 100.0,
                "name": "MT",
                "current_price": 126.0,
            },
        )
        assert r is not None
        assert r.trigger_type == TriggerType.TAKE_PROFIT
        assert r.action == "SELL"

    def test_long_no_trigger(self, monitor):
        r = monitor.check_position(
            "600519",
            {
                "shares": 100,
                "avg_cost": 100.0,
                "name": "MT",
                "current_price": 105.0,
            },
        )
        assert r is None

    def test_long_trailing_stop(self, monitor):
        monitor.rules["600519"]["trailing_stop"] = True
        # 涨到 110 (低于止盈线 125) → HWM=110, trailing stop = 110*0.88 = 96.8
        r1 = monitor.check_position(
            "600519",
            {
                "shares": 100,
                "avg_cost": 100.0,
                "name": "MT",
                "current_price": 110.0,
            },
        )
        assert r1 is None
        # 回落到 95 → 低于 trailing stop 96.8; 因 95 < entry (100), 标记为 STOP_LOSS
        r2 = monitor.check_position(
            "600519",
            {
                "shares": 100,
                "avg_cost": 100.0,
                "name": "MT",
                "current_price": 95.0,
            },
        )
        assert r2 is not None
        assert r2.trigger_type == TriggerType.STOP_LOSS
        assert r2.action == "SELL"


# ==================== 空头 (S2 新增) ====================


class TestShortPosition:
    """空头持仓: 价格上涨止损, 价格下跌止盈 (S2 修复)"""

    def test_short_stop_loss_on_rise(self, monitor):
        """空头: stop_loss_pct=-12% → 止损线=100*(1-(-0.12))=112.
        价格涨到 113 → 触发止损, action=BUY (买回)"""
        r = monitor.check_position(
            "600519",
            {
                "shares": -100,
                "avg_cost": 100.0,
                "name": "MT",
                "current_price": 113.0,
            },
        )
        assert r is not None
        assert r.trigger_type == TriggerType.STOP_LOSS
        assert r.action == "BUY"
        assert r.shares == 100  # absolute value

    def test_short_take_profit_on_fall(self, monitor):
        """空头: take_profit_pct=25% → 止盈线=100*(1-0.25)=75.
        价格跌到 74 → 触发止盈, action=BUY"""
        r = monitor.check_position(
            "600519",
            {
                "shares": -100,
                "avg_cost": 100.0,
                "name": "MT",
                "current_price": 74.0,
            },
        )
        assert r is not None
        assert r.trigger_type == TriggerType.TAKE_PROFIT
        assert r.action == "BUY"

    def test_short_no_trigger(self, monitor):
        """空头: 100~112 之间, 不触发"""
        r = monitor.check_position(
            "600519",
            {
                "shares": -100,
                "avg_cost": 100.0,
                "name": "MT",
                "current_price": 105.0,
            },
        )
        assert r is None

    def test_short_trailing_stop(self, monitor):
        """空头移动止损: 先跌 (设 LWM) → 价格反弹到 trailing stop"""
        monitor.rules["600519"]["trailing_stop"] = True
        # Drop to 80 → LWM=80, trailing stop = 80 * 1.12 = 89.6
        r1 = monitor.check_position(
            "600519",
            {
                "shares": -100,
                "avg_cost": 100.0,
                "name": "MT",
                "current_price": 80.0,
            },
        )
        assert r1 is None  # 80 > 75, no TP
        # Rise to 90 → above trailing stop 89.6
        r2 = monitor.check_position(
            "600519",
            {
                "shares": -100,
                "avg_cost": 100.0,
                "name": "MT",
                "current_price": 90.0,
            },
        )
        assert r2 is not None
        assert r2.trigger_type == TriggerType.TRAILING_STOP
        assert r2.action == "BUY"


# ==================== Flat / 无规则 ====================


class TestEdgeCases:
    """flat 持仓、无规则 → None"""

    def test_flat_returns_none(self, monitor):
        r = monitor.check_position(
            "600519",
            {
                "shares": 0,
                "avg_cost": 100.0,
                "name": "MT",
                "current_price": 88.0,
            },
        )
        assert r is None

    def test_no_rule_returns_none(self, monitor):
        r = monitor.check_position(
            "000001",
            {
                "shares": 100,
                "avg_cost": 10.0,
                "name": "平安银行",
                "current_price": 5.0,
            },
        )
        assert r is None

    def test_short_then_flat_clears_marks(self, monitor):
        """空头后清仓: 高低水位线均清除"""
        monitor.rules["600519"]["trailing_stop"] = True
        # Short position triggers a trailing mark
        monitor.check_position(
            "600519",
            {
                "shares": -100,
                "avg_cost": 100.0,
                "name": "MT",
                "current_price": 80.0,
            },
        )
        assert "600519" in monitor._low_water_mark
        # Flat: clears both marks
        monitor.check_position(
            "600519",
            {
                "shares": 0,
                "avg_cost": 100.0,
                "name": "MT",
                "current_price": 100.0,
            },
        )
        assert "600519" not in monitor._low_water_mark
        assert "600519" not in monitor._high_water_mark
