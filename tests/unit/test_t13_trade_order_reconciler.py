"""T13 单元测试 — TradeOrderReconciler 盘后对账."""
from __future__ import annotations

import pytest

from utils.risk.trade_order_reconciler import (
    FillRecord,
    PlannedOrder,
    ReconciliationReport,
    TradeOrderReconciler,
)


def _fill(fid, oid, sym, side, qty, px):
    return FillRecord(fill_id=fid, order_id=oid, symbol=sym, side=side, filled_qty=qty, avg_price=px)


class TestReconcileCoverage:
    def test_zero_fill_coverage_issue(self):
        rec = TradeOrderReconciler()
        planned = [PlannedOrder("o1", "sh1", "buy", 1000, limit_price=10.0)]
        fills: list[FillRecord] = []
        r = rec.reconcile("2026-08-12", planned, fills)
        assert not r.all_pass
        assert any("ORDER_COVERAGE" in s for s in r.issues_summary)
        assert r.total_planned == 1
        assert r.total_filled_orders == 0

    def test_full_coverage_no_issues(self):
        rec = TradeOrderReconciler()
        planned = [PlannedOrder("o1", "sh1", "buy", 1000, limit_price=10.0)]
        fills = [_fill("f1", "o1", "sh1", "buy", 1000, 10.0)]
        r = rec.reconcile("2026-08-12", planned, fills)
        assert r.all_pass
        assert r.total_filled_orders == 1


class TestQtyDeviation:
    def test_within_tolerance_pass(self):
        rec = TradeOrderReconciler(qty_deviation_pct=0.10)
        planned = [PlannedOrder("o1", "sh1", "buy", 1000)]
        fills = [_fill("f1", "o1", "sh1", "buy", 950, 10.0)]  # 950/1000 = 偏差 -5% OK
        r = rec.reconcile("d", planned, fills)
        assert r.all_pass

    def test_beyond_tolerance_fail(self):
        rec = TradeOrderReconciler(qty_deviation_pct=0.05)
        planned = [PlannedOrder("o1", "sh1", "buy", 1000)]
        # 成交 1100 → 多成交 10%, 超阈值 5%
        fills = [_fill("f1", "o1", "sh1", "buy", 1100, 10.0)]
        r = rec.reconcile("d", planned, fills)
        assert not r.all_pass
        assert any("QTY_DEVIATION" in s for s in r.issues_summary)


class TestPriceDeviation:
    def test_within_bps_pass(self):
        rec = TradeOrderReconciler(price_deviation_bps=50)
        planned = [PlannedOrder("o1", "sh1", "buy", 100, limit_price=100.0)]
        fills = [_fill("f1", "o1", "sh1", "buy", 100, 100.20)]  # 20 bps
        r = rec.reconcile("d", planned, fills)
        assert r.all_pass

    def test_beyond_bps_fail(self):
        rec = TradeOrderReconciler(price_deviation_bps=10)
        planned = [PlannedOrder("o1", "sh1", "buy", 100, limit_price=100.0)]
        fills = [_fill("f1", "o1", "sh1", "buy", 100, 101.00)]  # 100 bps
        r = rec.reconcile("d", planned, fills)
        assert not r.all_pass
        assert any("PRICE_DEVIATION" in s for s in r.issues_summary)

    def test_no_limit_price_skips_price_check(self):
        rec = TradeOrderReconciler(price_deviation_bps=1)  # 极严阈值
        planned = [PlannedOrder("o1", "sh1", "buy", 100)]  # 无限价
        fills = [_fill("f1", "o1", "sh1", "buy", 100, 999.0)]  # 任何价都应通过
        r = rec.reconcile("d", planned, fills)
        assert r.all_pass


class TestUnexpectedFills:
    def test_orphan_fill_detected(self):
        rec = TradeOrderReconciler()
        planned = [PlannedOrder("o1", "sh1", "buy", 100)]
        # fill 对应 order_id="o999" (不存在) + 另一笔 o1 正常成交
        fills = [
            _fill("f1", "o999", "sh666", "buy", 999, 1.0),  # 未映射
            _fill("f2", "o1", "sh1", "buy", 100, 10.0),
        ]
        r = rec.reconcile("d", planned, fills)
        assert not r.all_pass
        assert len(r.unexpected_fills) == 1
        assert r.unexpected_fills[0].order_id == "o999"
        assert any("UNEXPECTED_FILLS" in s for s in r.issues_summary)


class TestAggregationOfMultipleFillsForSameOrder:
    def test_multiple_fills_aggregated(self):
        rec = TradeOrderReconciler()
        planned = [PlannedOrder("o1", "sh1", "buy", 1000, limit_price=10.0)]
        fills = [
            _fill("f1", "o1", "sh1", "buy", 400, 10.0),
            _fill("f2", "o1", "sh1", "buy", 600, 10.0),
        ]
        r = rec.reconcile("d", planned, fills)
        assert r.all_pass
        item = r.items["o1"]
        assert item.filled_qty == 1000
        assert abs(item.avg_fill_price - 10.0) < 1e-9

    def test_weighted_avg_price_calculation(self):
        rec = TradeOrderReconciler(price_deviation_bps=100)
        planned = [PlannedOrder("o1", "sh1", "buy", 300, limit_price=10.0)]
        fills = [
            _fill("f1", "o1", "sh1", "buy", 100, 9.0),   # 加权 (900+2200)/300 = 10.333
            _fill("f2", "o1", "sh1", "buy", 200, 11.0),
        ]
        r = rec.reconcile("d", planned, fills)
        assert r.items["o1"].avg_fill_price == pytest.approx(3100 / 300)  # type: ignore[pytest-approx]


class TestReportSummaryText:
    def test_summary_non_empty(self):
        rec = TradeOrderReconciler()
        r = rec.reconcile("d", [], [])
        txt = r.summary_text()
        assert "对账报告" in txt
        assert "计划单数" in txt
        assert "总问题数" in txt


