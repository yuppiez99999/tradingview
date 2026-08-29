"""G7 覆盖率冲刺 — utils/smart_order_router.py 单元测试.

目标: 覆盖率从 33.16% → ≥70%

测试范围:
    1. dataclass: Venue / OrderBookSnapshot / VenueScore / RoutingDecision
    2. SmartOrderRouter.__init__ + 权重归一化
    3. register_venue / update_venue_status
    4. route: SMART / DARK_FIRST / LIQUIDITY_FIRST / ICEBERG / 无盘口
    5. _score_venues: 有/无盘口 / 不可用场所过滤
    6. _allocate_proportional: 空 / 正常 / min_shares 过滤 / 残差再分配
    7. _allocate_iceberg: 空 / 单场所 / 多场所
    8. _detect_gaming: 无盘口 / 不平衡 / 价差异常
    9. summarize_decision

运行:
    python -m pytest tests/unit/test_g7_smart_order_router_boost.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.smart_order_router import (  # noqa: E402
    OrderBookSnapshot,
    RoutingDecision,
    SmartOrderRouter,
    Venue,
    VenueScore,
)

# ============================================================
# 1. dataclass
# ============================================================


class TestDataclasses:
    def test_venue_defaults(self) -> None:
        v = Venue(name="V1", venue_type="EXCHANGE")
        assert v.liquidity_score == 0.5
        assert v.available is True
        assert v.commission_bps == 2.5

    def test_order_book_defaults(self) -> None:
        ob = OrderBookSnapshot(venue_name="V1", timestamp="t")
        assert ob.bid_prices == []
        assert ob.last_price == 0.0

    def test_venue_score_required_fields(self) -> None:
        s = VenueScore(
            venue_name="V1",
            total_score=0.8,
            liquidity_score=0.9,
            cost_score=0.8,
            speed_score=0.7,
            anonymity_score=0.1,
            expected_fill_price=100.0,
            expected_fill_ratio=0.95,
            expected_cost_bps=3.0,
        )
        assert s.recommended_shares == 0.0

    def test_routing_decision_defaults(self) -> None:
        d = RoutingDecision(symbol="600519", side="BUY", total_shares=1000)
        assert d.allocations == []
        assert d.strategy == "SMART"
        assert d.gaming_detected is False


# ============================================================
# 2. SmartOrderRouter.__init__
# ============================================================


class TestRouterInit:
    def test_default_venues_loaded(self) -> None:
        r = SmartOrderRouter()
        assert "SSE_MAIN" in r.venues
        assert "SZSE_MAIN" in r.venues
        assert "BLOCK_TRADE" in r.venues
        assert "DARK_POOL" in r.venues

    def test_weight_normalization(self) -> None:
        r = SmartOrderRouter(w_liquidity=1, w_cost=1, w_speed=1, w_anonymity=1)
        total = r.w_liq + r.w_cost + r.w_speed + r.w_anon
        assert total == pytest.approx(1.0)

    def test_custom_venues(self) -> None:
        v = Venue(name="CUSTOM", venue_type="EXCHANGE")
        r = SmartOrderRouter(venues=[v])
        assert "CUSTOM" in r.venues
        assert "SSE_MAIN" not in r.venues


# ============================================================
# 3. 场所管理
# ============================================================


class TestVenueManagement:
    def test_register_venue(self) -> None:
        r = SmartOrderRouter(venues=[])
        v = Venue(name="NEW", venue_type="EXCHANGE")
        r.register_venue(v)
        assert "NEW" in r.venues

    def test_update_status_existing(self) -> None:
        # 用自定义 venues 避免污染类级 DEFAULT_VENUES
        r = SmartOrderRouter(venues=[Venue(name="V1", venue_type="EXCHANGE")])
        r.update_venue_status("V1", False)
        assert r.venues["V1"].available is False

    def test_update_status_nonexistent_no_error(self) -> None:
        r = SmartOrderRouter()
        # 不存在 → 静默无操作
        r.update_venue_status("NOT_EXIST", False)
        assert "NOT_EXIST" not in r.venues


# ============================================================
# 4. route (主入口)
# ============================================================


class TestRoute:
    def test_smart_no_order_books(self) -> None:
        r = SmartOrderRouter()
        d = r.route("600519", "BUY", 10000)
        assert d.symbol == "600519"
        assert d.side == "BUY"
        assert d.strategy == "SMART"
        assert len(d.allocations) >= 1

    def test_smart_with_order_books(self) -> None:
        r = SmartOrderRouter()
        ob = OrderBookSnapshot(
            venue_name="SSE_MAIN",
            timestamp="t",
            bid_prices=[99.0, 98.0],
            bid_sizes=[5000, 3000],
            ask_prices=[101.0, 102.0],
            ask_sizes=[4000, 2000],
            last_price=100.0,
        )
        d = r.route("600519", "BUY", 10000, order_books={"SSE_MAIN": ob})
        assert d.primary_venue != ""

    def test_dark_first_fallback_to_smart(self) -> None:
        # DARK_FIRST 但无暗池场所可用 → 回退 SMART
        r = SmartOrderRouter(venues=[Venue(name="ONLY_EXCH", venue_type="EXCHANGE")])
        d = r.route("600519", "BUY", 1000, strategy="DARK_FIRST")
        assert d.strategy == "SMART"

    def test_dark_first_with_dark_pool(self) -> None:
        r = SmartOrderRouter(
            venues=[
                Venue(name="EXCH", venue_type="EXCHANGE", liquidity_score=0.9),
                Venue(name="DARK", venue_type="DARK_POOL", liquidity_score=0.5),
            ]
        )
        d = r.route("600519", "BUY", 1000, strategy="DARK_FIRST")
        # 仅暗池场所被选
        for a in d.allocations:
            assert a.venue_name == "DARK"

    def test_liquidity_first_sorts_by_liquidity(self) -> None:
        r = SmartOrderRouter(
            venues=[
                Venue(name="LOW_LIQ", venue_type="EXCHANGE", liquidity_score=0.3),
                Venue(name="HIGH_LIQ", venue_type="EXCHANGE", liquidity_score=0.9),
            ]
        )
        d = r.route("600519", "BUY", 1000, strategy="LIQUIDITY_FIRST", max_venues=2)
        # 高流动性应排前
        assert d.allocations[0].venue_name == "HIGH_LIQ"

    def test_iceberg_strategy(self) -> None:
        r = SmartOrderRouter()
        d = r.route("600519", "BUY", 10000, strategy="ICEBERG", max_venues=3)
        assert d.strategy == "ICEBERG"
        # 冰山: 主场所可见部分小
        if d.allocations:
            assert d.allocations[0].recommended_shares > 0

    def test_max_venues_limit(self) -> None:
        r = SmartOrderRouter()
        d = r.route("600519", "BUY", 10000, max_venues=2)
        assert len(d.allocations) <= 2

    def test_unavailable_venue_skipped(self) -> None:
        # 用自定义 venues 避免污染类级 DEFAULT_VENUES
        r = SmartOrderRouter(
            venues=[
                Venue(
                    name="V_OFF",
                    venue_type="EXCHANGE",
                    liquidity_score=0.9,
                    available=False,
                ),
                Venue(name="V_ON", venue_type="EXCHANGE", liquidity_score=0.5),
            ]
        )
        d = r.route("600519", "BUY", 10000)
        for a in d.allocations:
            assert a.venue_name != "V_OFF"


# ============================================================
# 5. _score_venues
# ============================================================


class TestScoreVenues:
    def test_no_order_books(self) -> None:
        r = SmartOrderRouter()
        scores = r._score_venues("BUY", 10000, {})
        assert len(scores) == len(r.venues)  # 所有默认场所均评分

    def test_buy_uses_ask_depth(self) -> None:
        r = SmartOrderRouter(
            venues=[Venue(name="V", venue_type="EXCHANGE", liquidity_score=0.5)]
        )
        ob = OrderBookSnapshot(
            venue_name="V",
            timestamp="t",
            bid_prices=[99.0],
            bid_sizes=[1000],
            ask_prices=[101.0],
            ask_sizes=[5000],
            last_price=100.0,
        )
        scores = r._score_venues("BUY", 10000, {"V": ob})
        assert len(scores) == 1
        # BUY → 用 ask 深度
        assert scores[0].expected_fill_price == 101.0

    def test_sell_uses_bid_depth(self) -> None:
        r = SmartOrderRouter(
            venues=[Venue(name="V", venue_type="EXCHANGE", liquidity_score=0.5)]
        )
        ob = OrderBookSnapshot(
            venue_name="V",
            timestamp="t",
            bid_prices=[99.0],
            bid_sizes=[5000],
            ask_prices=[101.0],
            ask_sizes=[1000],
            last_price=100.0,
        )
        scores = r._score_venues("SELL", 10000, {"V": ob})
        assert scores[0].expected_fill_price == 99.0

    def test_sorted_by_total_score(self) -> None:
        r = SmartOrderRouter()
        scores = r._score_venues("BUY", 10000, {})
        for i in range(len(scores) - 1):
            assert scores[i].total_score >= scores[i + 1].total_score


# ============================================================
# 6. _allocate_proportional
# ============================================================


class TestAllocateProportional:
    def test_empty_scores(self) -> None:
        r = SmartOrderRouter()
        assert r._allocate_proportional([], 1000, 100) == []

    def test_normal_allocation(self) -> None:
        r = SmartOrderRouter()
        s1 = VenueScore("V1", 0.9, 0.9, 0.9, 0.9, 0.1, 100.0, 0.9, 3.0)
        s2 = VenueScore("V2", 0.5, 0.5, 0.5, 0.5, 0.1, 100.0, 0.5, 3.0)
        result = r._allocate_proportional([s1, s2], 10000, 100)
        assert len(result) == 2
        total = sum(s.recommended_shares for s in result)
        assert total == pytest.approx(10000)

    def test_min_shares_filter(self) -> None:
        r = SmartOrderRouter()
        s1 = VenueScore("V1", 0.9, 0.9, 0.9, 0.9, 0.1, 100.0, 0.9, 3.0)
        s2 = VenueScore("V2", 0.01, 0.01, 0.01, 0.01, 0.1, 100.0, 0.01, 3.0)
        # min_shares=5000 → V2 分配 < 5000 被过滤
        result = r._allocate_proportional([s1, s2], 10000, 5000)
        assert len(result) == 1
        assert result[0].venue_name == "V1"
        # 残差再分配给主场所
        assert result[0].recommended_shares == pytest.approx(10000)


# ============================================================
# 7. _allocate_iceberg
# ============================================================


class TestAllocateIceberg:
    def test_empty_scores(self) -> None:
        r = SmartOrderRouter()
        assert r._allocate_iceberg([], 1000, 100) == []

    def test_single_venue(self) -> None:
        r = SmartOrderRouter()
        s1 = VenueScore("V1", 0.9, 0.9, 0.9, 0.9, 0.1, 100.0, 0.9, 3.0)
        result = r._allocate_iceberg([s1], 10000, 100)
        assert len(result) == 1
        # 单场所: 可见部分 = max(10000*0.1, 100) = 1000
        assert result[0].recommended_shares == 1000

    def test_multi_venue(self) -> None:
        r = SmartOrderRouter()
        s1 = VenueScore("V1", 0.9, 0.9, 0.9, 0.9, 0.1, 100.0, 0.9, 3.0)
        s2 = VenueScore("V2", 0.5, 0.5, 0.5, 0.5, 0.1, 100.0, 0.5, 3.0)
        result = r._allocate_iceberg([s1, s2], 10000, 100)
        # 主场所可见 1000, 其余 9000 分到 V2
        assert result[0].recommended_shares == 1000
        assert result[1].recommended_shares == 9000

    def test_multi_venue_small_remaining_skipped(self) -> None:
        r = SmartOrderRouter()
        s1 = VenueScore("V1", 0.9, 0.9, 0.9, 0.9, 0.1, 100.0, 0.9, 3.0)
        s2 = VenueScore("V2", 0.5, 0.5, 0.5, 0.5, 0.1, 100.0, 0.5, 3.0)
        # min_shares=8000 → remaining=9000, per_venue=9000 >= 8000 → 保留
        result = r._allocate_iceberg([s1, s2], 10000, 8000)
        assert len(result) == 2
        # min_shares=9500 → per_venue=9000 < 9500 → V2 跳过
        result2 = r._allocate_iceberg([s1, s2], 10000, 9500)
        assert result2[1].recommended_shares == 0.0


# ============================================================
# 8. _detect_gaming
# ============================================================


class TestDetectGaming:
    def test_no_order_books(self) -> None:
        r = SmartOrderRouter()
        assert r._detect_gaming({}, "BUY") == (False, 0.0)

    def test_balanced_book_no_gaming(self) -> None:
        r = SmartOrderRouter()
        ob = OrderBookSnapshot(
            venue_name="V",
            timestamp="t",
            bid_prices=[99.0],
            bid_sizes=[5000],
            ask_prices=[101.0],
            ask_sizes=[5000],
            last_price=100.0,
        )
        detected, risk = r._detect_gaming({"V": ob}, "BUY")
        # 平衡 + 价差 200bps → 不触发
        assert detected is False

    def test_imbalanced_book(self) -> None:
        r = SmartOrderRouter(gaming_threshold=0.5)
        ob = OrderBookSnapshot(
            venue_name="V",
            timestamp="t",
            bid_prices=[99.0],
            bid_sizes=[10000],
            ask_prices=[99.01],
            ask_sizes=[100],
            last_price=99.0,
        )
        detected, risk = r._detect_gaming({"V": ob}, "BUY")
        # 极度不平衡 + 价差 < 1bp → 高风险
        assert risk > 0.5

    def test_empty_sizes_skipped(self) -> None:
        r = SmartOrderRouter()
        ob = OrderBookSnapshot(venue_name="V", timestamp="t")
        detected, risk = r._detect_gaming({"V": ob}, "BUY")
        assert detected is False
        assert risk == 0.0


# ============================================================
# 9. summarize_decision
# ============================================================


class TestSummarizeDecision:
    def test_summary_structure(self) -> None:
        r = SmartOrderRouter()
        d = r.route("600519", "BUY", 10000)
        s = r.summarize_decision(d)
        assert s["symbol"] == "600519"
        assert s["side"] == "BUY"
        assert s["total_shares"] == 10000
        assert "allocations" in s
        assert "gaming_detected" in s
        assert isinstance(s["allocations"], list)
