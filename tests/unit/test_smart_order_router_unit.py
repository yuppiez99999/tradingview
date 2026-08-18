"""test_smart_order_router_unit.py — 智能订单路由器单元测试

覆盖要点:
    - Venue / OrderBookSnapshot / VenueScore / RoutingDecision dataclass
    - SmartOrderRouter 构造 (默认/自定义参数/权重归一化)
    - register_venue / update_venue_status
    - route (SMART/LIQUIDITY_FIRST/ICEBERG/DARK_FIRST 策略)
    - _score_venues (盘口数据/无盘口/不可用场所)
    - _allocate_proportional / _allocate_iceberg
    - _detect_gaming (无盘口/不平衡/价差异常)
    - summarize_decision
"""
from __future__ import annotations

import pytest

from utils.smart_order_router import (
    OrderBookSnapshot,
    RoutingDecision,
    SmartOrderRouter,
    Venue,
    VenueScore,
)


# ============================================================
# dataclass
# ============================================================


class TestVenue:
    @pytest.mark.unit
    def test_defaults(self):
        v = Venue(name="SSE", venue_type="EXCHANGE")
        assert v.liquidity_score == 0.5
        assert v.cost_score == 0.5
        assert v.available is True
        assert v.commission_bps == 2.5


class TestOrderBookSnapshot:
    @pytest.mark.unit
    def test_defaults(self):
        b = OrderBookSnapshot(venue_name="SSE", timestamp="2026-08-18")
        assert b.bid_prices == []
        assert b.last_price == 0.0


class TestRoutingDecision:
    @pytest.mark.unit
    def test_defaults(self):
        d = RoutingDecision(symbol="600519", side="BUY", total_shares=1000)
        assert d.allocations == []
        assert d.strategy == "SMART"
        assert d.gaming_detected is False


# ============================================================
# SmartOrderRouter 构造
# ============================================================


class TestConstruction:
    @pytest.mark.unit
    def test_default_venues(self):
        r = SmartOrderRouter()
        assert "SSE_MAIN" in r.venues
        assert "SZSE_MAIN" in r.venues
        assert "BLOCK_TRADE" in r.venues
        assert "DARK_POOL" in r.venues

    @pytest.mark.unit
    def test_custom_venues(self):
        v = Venue(name="CUSTOM", venue_type="EXCHANGE")
        r = SmartOrderRouter(venues=[v])
        assert "CUSTOM" in r.venues
        assert "SSE_MAIN" not in r.venues

    @pytest.mark.unit
    def test_weight_normalization(self):
        r = SmartOrderRouter(w_liquidity=10, w_cost=10, w_speed=10, w_anonymity=10)
        assert abs(r.w_liq + r.w_cost + r.w_speed + r.w_anon - 1.0) < 1e-6


# ============================================================
# 场所管理
# ============================================================


class TestVenueManagement:
    @pytest.mark.unit
    def test_register_venue(self):
        r = SmartOrderRouter(venues=[])
        v = Venue(name="NEW", venue_type="EXCHANGE")
        r.register_venue(v)
        assert "NEW" in r.venues

    @pytest.mark.unit
    def test_update_status(self):
        r = SmartOrderRouter()
        r.update_venue_status("SSE_MAIN", False)
        assert r.venues["SSE_MAIN"].available is False
        r.update_venue_status("SSE_MAIN", True)
        assert r.venues["SSE_MAIN"].available is True

    @pytest.mark.unit
    def test_update_unknown_venue(self):
        r = SmartOrderRouter()
        r.update_venue_status("UNKNOWN", False)
        assert "UNKNOWN" not in r.venues


# ============================================================
# route
# ============================================================


class TestRoute:
    @pytest.mark.unit
    def test_smart_basic(self):
        r = SmartOrderRouter()
        decision = r.route(symbol="600519", side="BUY", total_shares=10000)
        assert decision.symbol == "600519"
        assert decision.side == "BUY"
        assert decision.strategy == "SMART"
        assert len(decision.allocations) > 0
        assert decision.primary_venue != ""

    @pytest.mark.unit
    def test_liquidity_first(self):
        r = SmartOrderRouter()
        decision = r.route(symbol="600519", side="BUY", total_shares=10000, strategy="LIQUIDITY_FIRST")
        assert decision.strategy == "LIQUIDITY_FIRST"
        # SSE_MAIN 流动性最高
        assert decision.primary_venue == "SSE_MAIN"

    @pytest.mark.unit
    def test_iceberg(self):
        r = SmartOrderRouter()
        decision = r.route(symbol="600519", side="BUY", total_shares=10000, strategy="ICEBERG")
        assert decision.strategy == "ICEBERG"
        # 主场所可见部分应小于总量
        if decision.allocations:
            assert decision.allocations[0].recommended_shares < 10000

    @pytest.mark.unit
    def test_dark_first(self):
        r = SmartOrderRouter()
        decision = r.route(symbol="600519", side="BUY", total_shares=10000, strategy="DARK_FIRST")
        # DARK_FIRST 只用暗池/大宗
        assert len(decision.allocations) > 0
        for a in decision.allocations:
            assert r.venues[a.venue_name].venue_type in ("DARK_POOL", "BLOCK_TRADE")

    @pytest.mark.unit
    def test_max_venues_limit(self):
        r = SmartOrderRouter()
        decision = r.route(symbol="600519", side="BUY", total_shares=10000, max_venues=2)
        assert len(decision.allocations) <= 2

    @pytest.mark.unit
    def test_with_order_books(self):
        r = SmartOrderRouter()
        book = OrderBookSnapshot(
            venue_name="SSE_MAIN", timestamp="2026-08-18",
            bid_prices=[10.0, 9.99, 9.98], bid_sizes=[5000, 3000, 2000],
            ask_prices=[10.01, 10.02, 10.03], ask_sizes=[4000, 2000, 1000],
            last_price=10.005,
        )
        decision = r.route(
            symbol="600519", side="BUY", total_shares=1000,
            order_books={"SSE_MAIN": book},
        )
        assert len(decision.allocations) > 0

    @pytest.mark.unit
    def test_unavailable_venue_excluded(self):
        r = SmartOrderRouter()
        r.update_venue_status("SSE_MAIN", False)
        decision = r.route(symbol="600519", side="BUY", total_shares=10000)
        for a in decision.allocations:
            assert a.venue_name != "SSE_MAIN"

    @pytest.mark.unit
    def test_total_shares_conserved(self):
        r = SmartOrderRouter()
        decision = r.route(symbol="600519", side="BUY", total_shares=10000, max_venues=2)
        total = sum(a.recommended_shares for a in decision.allocations)
        assert abs(total - 10000) < len(decision.allocations)


# ============================================================
# _detect_gaming
# ============================================================


class TestDetectGaming:
    @pytest.mark.unit
    def test_no_order_books(self):
        r = SmartOrderRouter()
        assert r._detect_gaming({}, "BUY") == (False, 0.0)

    @pytest.mark.unit
    def test_balanced_book(self):
        r = SmartOrderRouter()
        book = OrderBookSnapshot(
            venue_name="SSE", timestamp="t",
            bid_prices=[10.0], bid_sizes=[5000],
            ask_prices=[10.01], ask_sizes=[5000],
            last_price=10.005,
        )
        detected, risk = r._detect_gaming({"SSE": book}, "BUY")
        # 平衡 → 低风险
        assert risk < 0.7

    @pytest.mark.unit
    def test_imbalanced_book(self):
        r = SmartOrderRouter(gaming_threshold=0.3)
        book = OrderBookSnapshot(
            venue_name="SSE", timestamp="t",
            bid_prices=[10.0], bid_sizes=[10000],
            ask_prices=[10.001], ask_sizes=[100],
            last_price=10.0,
        )
        detected, risk = r._detect_gaming({"SSE": book}, "BUY")
        # 高不平衡 + 小价差 → 高风险
        assert risk > 0.3


# ============================================================
# summarize_decision
# ============================================================


class TestSummarize:
    @pytest.mark.unit
    def test_summary(self):
        r = SmartOrderRouter()
        decision = r.route(symbol="600519", side="BUY", total_shares=10000, max_venues=2)
        s = r.summarize_decision(decision)
        assert s["symbol"] == "600519"
        assert s["num_venues"] == len(decision.allocations)
        assert "allocations" in s
        assert "gaming_detected" in s