"""A 股 T+1 交易规则单测 (G15 回测规则层)。

覆盖点:
  - PositionLot 不可变性与字段
  - T1PositionTracker 建仓/可卖量/总持仓/FIFO 消费
  - AShareTradingRules T+1/涨跌停检查
  - bar_to_date 日期解析
  - filter_order_t1 订单过滤
"""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch

import pytest

from utils.backtest.a_share_rules import (
    AShareTradingRules,
    PositionLot,
    T1PositionTracker,
    bar_to_date,
    filter_order_t1,
)

# ============================================================
# 1. PositionLot
# ============================================================


class TestPositionLot:
    def test_frozen_fields(self):
        lot = PositionLot(code="600519.SH", volume=100.0, acquisition_date=date(2026, 8, 1), avg_price=1500.0)
        assert lot.code == "600519.SH"
        assert lot.volume == 100.0
        assert lot.acquisition_date == date(2026, 8, 1)
        assert lot.avg_price == 1500.0

    def test_frozen_cannot_mutate(self):
        lot = PositionLot(code="600519.SH", volume=100.0, acquisition_date=date(2026, 8, 1), avg_price=1500.0)
        with pytest.raises((AttributeError, TypeError)):
            lot.volume = 200.0


# ============================================================
# 2. T1PositionTracker
# ============================================================


class TestT1PositionTracker:
    def test_empty_tracker(self):
        tracker = T1PositionTracker()
        assert tracker.available_volume("600519.SH", date(2026, 8, 3)) == 0.0
        assert tracker.total_volume("600519.SH") == 0.0
        assert tracker.get_lots("600519.SH") == []

    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=False)
    def test_add_lot_and_available(self, _mock_t0):
        tracker = T1PositionTracker()
        tracker.add_lot("600519.SH", 100.0, date(2026, 8, 1), 1500.0)
        assert tracker.total_volume("600519.SH") == 100.0
        assert tracker.available_volume("600519.SH", date(2026, 8, 3)) == 100.0

    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=False)
    def test_t1_block_today_sell(self, _mock_t0):
        tracker = T1PositionTracker()
        tracker.add_lot("600519.SH", 100.0, date(2026, 8, 3), 1500.0)
        assert tracker.available_volume("600519.SH", date(2026, 8, 3)) == 0.0

    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=True)
    def test_t0_skip_t1(self, _mock_t0):
        tracker = T1PositionTracker()
        tracker.add_lot("000001.SZ", 100.0, date(2026, 8, 3), 12.0)
        assert tracker.available_volume("000001.SZ", date(2026, 8, 3)) == 100.0

    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=False)
    def test_consume_fifo(self, _mock_t0):
        tracker = T1PositionTracker()
        tracker.add_lot("600519.SH", 100.0, date(2026, 8, 1), 1500.0)
        tracker.add_lot("600519.SH", 200.0, date(2026, 8, 2), 1550.0)
        consumed_vol, consumed_val = tracker.consume("600519.SH", 150.0, date(2026, 8, 3))
        assert consumed_vol == 150.0
        assert consumed_val == pytest.approx(100.0 * 1500.0 + 50.0 * 1550.0)
        assert tracker.total_volume("600519.SH") == 150.0

    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=False)
    def test_consume_insufficient(self, _mock_t0):
        tracker = T1PositionTracker()
        tracker.add_lot("600519.SH", 100.0, date(2026, 8, 1), 1500.0)
        consumed_vol, consumed_val = tracker.consume("600519.SH", 200.0, date(2026, 8, 3))
        assert consumed_vol == 100.0
        assert consumed_val == pytest.approx(100.0 * 1500.0)
        assert tracker.total_volume("600519.SH") == 0.0

    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=False)
    def test_get_lots_returns_copy(self, _mock_t0):
        tracker = T1PositionTracker()
        tracker.add_lot("600519.SH", 100.0, date(2026, 8, 1), 1500.0)
        lots = tracker.get_lots("600519.SH")
        assert len(lots) == 1
        lots.append(PositionLot(code="600519.SH", volume=50.0, acquisition_date=date(2026, 8, 1), avg_price=1500.0))
        assert tracker.total_volume("600519.SH") == 100.0

    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=False)
    def test_add_lot_zero_volume_skips(self, _mock_t0):
        tracker = T1PositionTracker()
        tracker.add_lot("600519.SH", 0.0, date(2026, 8, 1), 1500.0)
        assert tracker.total_volume("600519.SH") == 0.0


# ============================================================
# 3. AShareTradingRules
# ============================================================


class TestAShareTradingRules:
    def test_defaults(self):
        rules = AShareTradingRules()
        assert rules.enable_t1 is True
        assert rules.enable_price_limit is True
        assert isinstance(rules.t1_tracker, T1PositionTracker)

    def test_disable_t1(self):
        rules = AShareTradingRules(enable_t1=False)
        assert rules.check_sell_t1("600519.SH", 100.0, date(2026, 8, 3)) == (True, "")

    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=True)
    def test_t0_skip_t1_check(self, _mock_t0):
        rules = AShareTradingRules()
        ok, reason = rules.check_sell_t1("000001.SZ", 100.0, date(2026, 8, 3))
        assert ok is True
        assert reason == ""

    def test_price_limit_disabled(self):
        rules = AShareTradingRules(enable_price_limit=False)
        assert rules.check_price_limit("600519.SH", 2000.0, 1500.0) == (True, "")
        assert rules.get_price_limit_pct("600519.SH") == pytest.approx(1.0)

    @patch("utils.backtest.a_share_rules.is_20cm_symbol", return_value=True)
    def test_20cm_limit(self, _mock_20cm):
        rules = AShareTradingRules()
        assert rules.get_price_limit_pct("688001.SH") == pytest.approx(0.20)

    @patch("utils.backtest.a_share_rules.is_20cm_symbol", return_value=False)
    def test_10cm_limit(self, _mock_20cm):
        rules = AShareTradingRules()
        assert rules.get_price_limit_pct("600519.SH") == pytest.approx(0.10)

    def test_price_limit_upper(self):
        rules = AShareTradingRules()
        ok, reason = rules.check_price_limit("600519.SH", 1700.0, 1500.0)
        assert ok is False
        assert "涨停限制" in reason

    def test_price_limit_lower(self):
        rules = AShareTradingRules()
        ok, reason = rules.check_price_limit("600519.SH", 1300.0, 1500.0)
        assert ok is False
        assert "跌停限制" in reason

    def test_price_limit_zero_pre_close(self):
        rules = AShareTradingRules()
        ok, reason = rules.check_price_limit("600519.SH", 100.0, 0.0)
        assert ok is True
        assert reason == ""

    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=False)
    def test_on_buy_fill_adds_lot(self, _mock_t0):
        rules = AShareTradingRules()
        rules.on_buy_fill("600519.SH", 100.0, 1500.0, date(2026, 8, 1))
        assert rules.t1_tracker.total_volume("600519.SH") == 100.0

    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=False)
    def test_on_sell_fill_consumes(self, _mock_t0):
        rules = AShareTradingRules()
        rules.on_buy_fill("600519.SH", 100.0, 1500.0, date(2026, 8, 1))
        consumed_vol, consumed_val = rules.on_sell_fill("600519.SH", 100.0, date(2026, 8, 3))
        assert consumed_vol == 100.0
        assert consumed_val == pytest.approx(100.0 * 1500.0)


# ============================================================
# 4. bar_to_date
# ============================================================


class TestBarToDate:
    def test_int_date(self):
        bar = type("BarData", (), {"date": 20260803, "ts_event": 0})()
        assert bar_to_date(bar) == date(2026, 8, 3)

    def test_zero_date_fallback_ts_event(self):
        bar = type("BarData", (), {"date": 0, "ts_event": int(datetime(2026, 8, 3, 9, 30).timestamp() * 1e9)})()
        assert bar_to_date(bar) == date(2026, 8, 3)

    def test_zero_date_and_zero_ts_fallback_today(self):
        today = date.today()
        bar = type("BarData", (), {"date": 0, "ts_event": 0})()
        assert bar_to_date(bar) == today

    def test_missing_date_attribute(self):
        bar = type("BarData", (), {"ts_event": 0})()
        assert bar_to_date(bar) == date.today()


# ============================================================
# 5. filter_order_t1
# ============================================================


class TestFilterOrderT1:
    def _make_order(self, direction: str = "BUY", volume: float = 100.0, code: str = "600519.SH"):
        return type("OrderData", (), {"direction": direction, "volume": volume, "code": code})()

    def test_buy_pass_always(self):
        rules = AShareTradingRules()
        order = self._make_order("BUY", 100.0)
        result = filter_order_t1(rules, order, date(2026, 8, 3))
        assert result.passed is True
        assert result.adjusted_volume == 100.0

    def test_sell_t1_blocked(self):
        rules = AShareTradingRules()
        order = self._make_order("SELL", 100.0)
        result = filter_order_t1(rules, order, date(2026, 8, 3))
        assert result.passed is False
        assert "可卖量不足" in result.reason or "T+1" in result.reason

    def test_sell_t1_allowed_when_sufficient(self):
        rules = AShareTradingRules()
        rules.on_buy_fill("600519.SH", 200.0, 1500.0, date(2026, 8, 1))
        order = self._make_order("SELL", 100.0)
        result = filter_order_t1(rules, order, date(2026, 8, 3))
        assert result.passed is True
        assert result.adjusted_volume == 100.0


# ============================================================
# 6. 补充: consume 边界分支 (G7 覆盖率冲刺)
# ============================================================


class TestConsumeBranches:
    """覆盖 T1PositionTracker.consume 的未覆盖分支。"""

    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=False)
    def test_consume_less_than_first_lot_keeps_remainder(self, _mock_t0):
        # consume 量 < 第一个 lot volume → 第一个 lot 部分消费, remaining=0 → 后续 lot 保留
        tracker = T1PositionTracker()
        tracker.add_lot("600519.SH", 100.0, date(2026, 8, 1), 1500.0)
        tracker.add_lot("600519.SH", 200.0, date(2026, 8, 2), 1550.0)
        consumed_vol, consumed_val = tracker.consume("600519.SH", 30.0, date(2026, 8, 3))
        assert consumed_vol == 30.0
        assert consumed_val == pytest.approx(30.0 * 1500.0)
        # 第一个 lot 剩余 70, 第二个 lot 保留 200
        assert tracker.total_volume("600519.SH") == pytest.approx(270.0)

    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=False)
    def test_consume_t1_blocks_today_lot(self, _mock_t0):
        # lot.acquisition_date >= current_date → T+1 阻断, lot 保留
        tracker = T1PositionTracker()
        tracker.add_lot("600519.SH", 100.0, date(2026, 8, 3), 1500.0)  # 当日买入
        consumed_vol, consumed_val = tracker.consume("600519.SH", 50.0, date(2026, 8, 3))
        # 当日买入不可卖 → consume 0
        assert consumed_vol == 0.0
        assert consumed_val == 0.0
        assert tracker.total_volume("600519.SH") == 100.0

    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=False)
    def test_consume_mixed_t1_eligible_and_blocked(self, _mock_t0):
        # 混合: lot1 (8/1, 可卖), lot2 (8/3 当日, 不可卖) → 仅消费 lot1
        tracker = T1PositionTracker()
        tracker.add_lot("600519.SH", 100.0, date(2026, 8, 1), 1500.0)
        tracker.add_lot("600519.SH", 200.0, date(2026, 8, 3), 1550.0)
        consumed_vol, consumed_val = tracker.consume("600519.SH", 150.0, date(2026, 8, 3))
        # 仅 lot1 可卖 100, 请求 150 → 消费 100
        assert consumed_vol == 100.0
        assert consumed_val == pytest.approx(100.0 * 1500.0)
        # lot2 保留
        assert tracker.total_volume("600519.SH") == 200.0

    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=True)
    def test_consume_t0_ignores_t1_rule(self, _mock_t0):
        # T+0 标的: 即使 acquisition_date >= current_date 仍可消费
        tracker = T1PositionTracker()
        tracker.add_lot("000001.SZ", 100.0, date(2026, 8, 3), 12.0)  # 当日买入
        consumed_vol, consumed_val = tracker.consume("000001.SZ", 50.0, date(2026, 8, 3))
        assert consumed_vol == 50.0
        assert consumed_val == pytest.approx(50.0 * 12.0)

    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=False)
    def test_consume_empty_lots_returns_zero(self, _mock_t0):
        tracker = T1PositionTracker()
        consumed_vol, consumed_val = tracker.consume("600519.SH", 100.0, date(2026,8, 3))
        assert consumed_vol == 0.0
        assert consumed_val == 0.0

    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=False)
    def test_consume_tiny_remainder_drops_lot(self, _mock_t0):
        # lot.volume - consume <= 0.0001 → 不保留 (lot 完全消费)
        tracker = T1PositionTracker()
        tracker.add_lot("600519.SH", 100.0, date(2026, 8, 1), 1500.0)
        # consume 99.99999 → 剩余 0.00001 <= 0.0001 → 不保留
        tracker.consume("600519.SH", 99.99999, date(2026, 8, 3))
        assert tracker.total_volume("600519.SH") == 0.0


# ============================================================
# 7. 补充: check_sell_t1 / check_price_limit 通过分支
# ============================================================


class TestCheckSellT1PassBranch:
    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=False)
    def test_sufficient_available_passes(self, _mock_t0):
        # available >= sell_volume → (True, "")
        rules = AShareTradingRules()
        rules.on_buy_fill("600519.SH", 200.0, 1500.0, date(2026, 8, 1))
        ok, reason = rules.check_sell_t1("600519.SH", 100.0, date(2026, 8, 3))
        assert ok is True
        assert reason == ""

    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=False)
    def test_insufficient_available_fails(self, _mock_t0):
        # available < sell_volume → (False, "T+1限制...")
        rules = AShareTradingRules()
        rules.on_buy_fill("600519.SH", 50.0, 1500.0, date(2026, 8, 1))
        ok, reason = rules.check_sell_t1("600519.SH", 100.0, date(2026, 8, 3))
        assert ok is False
        assert "T+1限制" in reason
        assert "不足" in reason


class TestCheckPriceLimitPassBranch:
    def test_price_within_range_passes(self):
        # 委托价在涨跌停范围内 → (True, "")
        rules = AShareTradingRules()
        # pre_close=100, 10cm → 上下限 110/90
        ok, reason = rules.check_price_limit("600519.SH", 105.0, 100.0)
        assert ok is True
        assert reason == ""

    def test_price_at_upper_limit_passes(self):
        # 委托价 == 涨停价 → 通过 (order_price > upper 才拒)
        rules = AShareTradingRules()
        ok, _ = rules.check_price_limit("600519.SH", 110.0, 100.0)
        assert ok is True

    def test_price_at_lower_limit_passes(self):
        # 委托价 == 跌停价 → 通过
        rules = AShareTradingRules()
        ok, _ = rules.check_price_limit("600519.SH", 90.0, 100.0)
        assert ok is True


# ============================================================
# 8. 补充: filter_order_t1 部分放行分支
# ============================================================


class TestFilterOrderT1Partial:
    def _make_order(self, direction: str = "BUY", volume: float = 100.0, code: str = "600519.SH"):
        return type("OrderData", (), {"direction": direction, "volume": volume, "code": code})()

    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=False)
    def test_partial_pass_when_some_available(self, _mock_t0):
        # 0 < available < order.volume → 部分放行
        rules = AShareTradingRules()
        rules.on_buy_fill("600519.SH", 30.0, 1500.0, date(2026, 8, 1))
        order = self._make_order("SELL", 100.0)
        result = filter_order_t1(rules, order, date(2026, 8, 3))
        assert result.passed is True
        assert result.adjusted_volume == 30.0
        assert "部分放行" in result.reason

    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=False)
    def test_full_block_when_zero_available(self, _mock_t0):
        # available == 0 → 完全拒绝
        rules = AShareTradingRules()
        rules.on_buy_fill("600519.SH", 100.0, 1500.0, date(2026, 8, 3))  # 当日买入, 不可卖
        order = self._make_order("SELL", 50.0)
        result = filter_order_t1(rules, order, date(2026, 8, 3))
        assert result.passed is False
        assert result.adjusted_volume == 0.0


# ============================================================
# 9. 补充: on_sell_fill 部分消费
# ============================================================


class TestOnSellFillPartial:
    @patch("utils.backtest.a_share_rules.is_t0_eligible", return_value=False)
    def test_partial_consume(self, _mock_t0):
        rules = AShareTradingRules()
        rules.on_buy_fill("600519.SH", 100.0, 1500.0, date(2026, 8, 1))
        # 卖出 30, 仅消费 30
        consumed_vol, consumed_val = rules.on_sell_fill("600519.SH", 30.0, date(2026, 8, 3))
        assert consumed_vol == 30.0
        assert consumed_val == pytest.approx(30.0 * 1500.0)
        # 剩余 70
        assert rules.t1_tracker.total_volume("600519.SH") == pytest.approx(70.0)
