"""test_tca_engine_unit.py — 交易后成本分析引擎单元测试

覆盖要点:
    - FillRecord / BenchmarkPrices / TCAReport dataclass
    - TCAManager 构造 (默认/自定义参数)
    - analyze (买入/卖出/多笔/空/零量/VWAP偏离/收盘偏离/机会成本/成交率/参与率/择时能力/成本细项/评级/诊断)
    - analyze_batch (多标的/空跳过/缺基准跳过)
    - summarize (空/多报告/评级分布/最优最差)
    - _signed_return (买/卖/零基准)
    - _grade (A+/A/B/C/D/F)
    - _diagnose (各种问题)
    - save_report
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.tca_engine import (
    BenchmarkPrices,
    FillRecord,
    TCAManager,
    TCAReport,
)


# ============================================================
# Dataclass
# ============================================================


class TestFillRecord:
    @pytest.mark.unit
    def test_construction(self):
        r = FillRecord(symbol="000001", side="BUY", shares=1000, price=10.5, timestamp="2026-01-01T09:30:00")
        assert r.symbol == "000001"
        assert r.side == "BUY"
        assert r.shares == 1000
        assert r.price == 10.5
        assert r.broker == ""
        assert r.venue == ""
        assert r.order_id == ""


class TestBenchmarkPrices:
    @pytest.mark.unit
    def test_defaults(self):
        b = BenchmarkPrices(decision_price=10.0, arrival_price=10.02)
        assert b.decision_price == 10.0
        assert b.arrival_price == 10.02
        assert b.vwap == 0.0
        assert b.close_price == 0.0
        assert b.open_price == 0.0
        assert b.twap == 0.0


class TestTCAReport:
    @pytest.mark.unit
    def test_defaults(self):
        r = TCAReport(
            symbol="000001", side="BUY", total_shares=1000, avg_exec_price=10.0, vwap=10.0,
            is_cost_bps=5.0, arrival_cost_bps=3.0, vwap_deviation_bps=2.0, close_deviation_bps=1.0,
            market_impact_bps=2.0, timing_cost_bps=3.0, opportunity_cost_bps=0.0, slippage_bps=2.0,
            fill_rate=1.0, participation_rate=0.01, timing_skill_score=-0.1,
            commission=5.0, fees=0.67, total_cost=10.67, quality_grade="A",
        )
        assert r.symbol == "000001"
        assert r.issues == []


# ============================================================
# TCAManager 构造
# ============================================================


class TestInit:
    @pytest.mark.unit
    def test_defaults(self):
        tca = TCAManager()
        assert tca.commission_rate == 0.0003
        assert tca.min_commission == 5.0
        assert tca.fee_rate == 0.000067
        assert tca.stamp_duty_rate == 0.0005

    @pytest.mark.unit
    def test_custom(self):
        tca = TCAManager(commission_rate=0.0005, min_commission=10.0, fee_rate=0.0001, stamp_duty_rate=0.001)
        assert tca.commission_rate == 0.0005
        assert tca.min_commission == 10.0


# ============================================================
# _signed_return
# ============================================================


class TestSignedReturn:
    @pytest.mark.unit
    def test_buy_cost(self):
        tca = TCAManager()
        # 买入: 执行价 > 基准 → 正 (成本)
        assert tca._signed_return(10.1, 10.0, "BUY") == pytest.approx(0.01)

    @pytest.mark.unit
    def test_buy_gain(self):
        tca = TCAManager()
        # 买入: 执行价 < 基准 → 负 (收益)
        assert tca._signed_return(9.9, 10.0, "BUY") == pytest.approx(-0.01)

    @pytest.mark.unit
    def test_sell_cost(self):
        tca = TCAManager()
        # 卖出: 执行价 < 基准 → 正 (成本)
        assert tca._signed_return(9.9, 10.0, "SELL") == pytest.approx(0.01)

    @pytest.mark.unit
    def test_sell_gain(self):
        tca = TCAManager()
        # 卖出: 执行价 > 基准 → 负 (收益)
        assert tca._signed_return(10.1, 10.0, "SELL") == pytest.approx(-0.01)

    @pytest.mark.unit
    def test_zero_ref(self):
        tca = TCAManager()
        assert tca._signed_return(10.0, 0.0, "BUY") == 0.0


# ============================================================
# _grade
# ============================================================


class TestGrade:
    @pytest.mark.unit
    def test_a_plus(self):
        tca = TCAManager()
        assert tca._grade(1.0) == "A+"
        assert tca._grade(-1.5) == "A+"

    @pytest.mark.unit
    def test_a(self):
        tca = TCAManager()
        assert tca._grade(3.0) == "A"

    @pytest.mark.unit
    def test_b(self):
        tca = TCAManager()
        assert tca._grade(7.0) == "B"

    @pytest.mark.unit
    def test_c(self):
        tca = TCAManager()
        assert tca._grade(15.0) == "C"

    @pytest.mark.unit
    def test_d(self):
        tca = TCAManager()
        assert tca._grade(30.0) == "D"

    @pytest.mark.unit
    def test_f(self):
        tca = TCAManager()
        assert tca._grade(60.0) == "F"


# ============================================================
# _diagnose
# ============================================================


class TestDiagnose:
    @pytest.mark.unit
    def test_no_issues(self):
        tca = TCAManager()
        issues = tca._diagnose(
            is_cost_bps=5.0, vwap_deviation_bps=3.0, market_impact_bps=5.0,
            timing_cost_bps=3.0, fill_rate=1.0, participation_rate=0.05,
        )
        assert issues == []

    @pytest.mark.unit
    def test_high_is_cost(self):
        tca = TCAManager()
        issues = tca._diagnose(
            is_cost_bps=25.0, vwap_deviation_bps=3.0, market_impact_bps=5.0,
            timing_cost_bps=3.0, fill_rate=1.0, participation_rate=0.05,
        )
        assert any("IS 成本过高" in i for i in issues)

    @pytest.mark.unit
    def test_low_fill_rate(self):
        tca = TCAManager()
        issues = tca._diagnose(
            is_cost_bps=5.0, vwap_deviation_bps=3.0, market_impact_bps=5.0,
            timing_cost_bps=3.0, fill_rate=0.80, participation_rate=0.05,
        )
        assert any("成交率低" in i for i in issues)

    @pytest.mark.unit
    def test_high_participation(self):
        tca = TCAManager()
        issues = tca._diagnose(
            is_cost_bps=5.0, vwap_deviation_bps=3.0, market_impact_bps=5.0,
            timing_cost_bps=3.0, fill_rate=1.0, participation_rate=0.25,
        )
        assert any("参与率过高" in i for i in issues)

    @pytest.mark.unit
    def test_low_participation(self):
        tca = TCAManager()
        issues = tca._diagnose(
            is_cost_bps=5.0, vwap_deviation_bps=3.0, market_impact_bps=5.0,
            timing_cost_bps=3.0, fill_rate=1.0, participation_rate=0.005,
        )
        assert any("参与率过低" in i for i in issues)


# ============================================================
# analyze
# ============================================================


class TestAnalyze:
    @pytest.fixture
    def tca(self):
        return TCAManager()

    @pytest.mark.unit
    def test_empty_fills_raises(self, tca):
        with pytest.raises(ValueError):
            tca.analyze([], BenchmarkPrices(10.0, 10.0))

    @pytest.mark.unit
    def test_zero_shares_raises(self, tca):
        fills = [FillRecord("000001", "BUY", 0, 10.0, "2026-01-01")]
        with pytest.raises(ValueError):
            tca.analyze(fills, BenchmarkPrices(10.0, 10.0))

    @pytest.mark.unit
    def test_buy_basic(self, tca):
        fills = [FillRecord("000001", "BUY", 1000, 10.05, "2026-01-01")]
        bench = BenchmarkPrices(decision_price=10.0, arrival_price=10.02, vwap=10.04, close_price=10.10)
        r = tca.analyze(fills, bench)
        assert r.symbol == "000001"
        assert r.side == "BUY"
        assert r.total_shares == 1000
        assert r.avg_exec_price == 10.05
        # IS = (10.05 - 10.0) / 10.0 * 10000 = 50 bps (浮点精度可能略超 50)
        assert r.is_cost_bps == pytest.approx(50.0)
        assert r.quality_grade in ("D", "F")  # 边界值, 浮点精度决定

    @pytest.mark.unit
    def test_sell_basic(self, tca):
        fills = [FillRecord("000001", "SELL", 1000, 9.95, "2026-01-01")]
        bench = BenchmarkPrices(decision_price=10.0, arrival_price=9.98, vwap=9.96, close_price=9.90)
        r = tca.analyze(fills, bench)
        assert r.side == "SELL"
        # IS (sell) = (10.0 - 9.95) / 10.0 * 10000 = 50 bps
        assert r.is_cost_bps == pytest.approx(50.0)

    @pytest.mark.unit
    def test_multiple_fills(self, tca):
        fills = [
            FillRecord("000001", "BUY", 500, 10.0, "2026-01-01"),
            FillRecord("000001", "BUY", 500, 10.1, "2026-01-01"),
        ]
        bench = BenchmarkPrices(10.0, 10.0)
        r = tca.analyze(fills, bench)
        assert r.total_shares == 1000
        assert r.avg_exec_price == pytest.approx(10.05)

    @pytest.mark.unit
    def test_fill_rate(self, tca):
        fills = [FillRecord("000001", "BUY", 800, 10.0, "2026-01-01")]
        bench = BenchmarkPrices(10.0, 10.0)
        r = tca.analyze(fills, bench, order_shares=1000)
        assert r.fill_rate == pytest.approx(0.8)

    @pytest.mark.unit
    def test_participation_rate(self, tca):
        fills = [FillRecord("000001", "BUY", 1000, 10.0, "2026-01-01")]
        bench = BenchmarkPrices(10.0, 10.0)
        r = tca.analyze(fills, bench, interval_volume=10000)
        assert r.participation_rate == pytest.approx(0.1)

    @pytest.mark.unit
    def test_opportunity_cost(self, tca):
        fills = [FillRecord("000001", "BUY", 500, 10.0, "2026-01-01")]
        bench = BenchmarkPrices(10.0, 10.0, close_price=10.20)
        r = tca.analyze(fills, bench, order_shares=1000)
        assert r.opportunity_cost_bps > 0

    @pytest.mark.unit
    def test_no_vwap(self, tca):
        fills = [FillRecord("000001", "BUY", 1000, 10.0, "2026-01-01")]
        bench = BenchmarkPrices(10.0, 10.0)  # vwap=0
        r = tca.analyze(fills, bench)
        assert r.vwap_deviation_bps == 0.0

    @pytest.mark.unit
    def test_timing_skill(self, tca):
        fills = [FillRecord("000001", "BUY", 1000, 10.0, "2026-01-01")]
        bench = BenchmarkPrices(10.0, 10.0)
        r = tca.analyze(fills, bench)
        # IS=0 → timing_skill=0
        assert r.timing_skill_score == 0.0

    @pytest.mark.unit
    def test_commission_min(self, tca):
        """小额交易佣金取最小值"""
        fills = [FillRecord("000001", "BUY", 10, 10.0, "2026-01-01")]
        bench = BenchmarkPrices(10.0, 10.0)
        r = tca.analyze(fills, bench)
        # notional=100, commission_rate=0.0003 → 0.03 < min 5.0
        assert r.commission == 5.0

    @pytest.mark.unit
    def test_sell_stamp_duty(self, tca):
        fills = [FillRecord("000001", "SELL", 1000, 10.0, "2026-01-01")]
        bench = BenchmarkPrices(10.0, 10.0)
        r = tca.analyze(fills, bench)
        # fees 应包含印花税
        notional = 10000
        expected_fees = notional * 0.000067 + notional * 0.0005
        assert r.fees == pytest.approx(expected_fees)


# ============================================================
# analyze_batch
# ============================================================


class TestAnalyzeBatch:
    @pytest.mark.unit
    def test_multi_symbol(self):
        tca = TCAManager()
        fills_by_sym = {
            "000001": [FillRecord("000001", "BUY", 1000, 10.0, "2026-01-01")],
            "000002": [FillRecord("000002", "BUY", 500, 20.0, "2026-01-01")],
        }
        benchmarks = {
            "000001": BenchmarkPrices(10.0, 10.0),
            "000002": BenchmarkPrices(20.0, 20.0),
        }
        reports = tca.analyze_batch(fills_by_sym, benchmarks)
        assert "000001" in reports
        assert "000002" in reports

    @pytest.mark.unit
    def test_empty_fills_skipped(self):
        tca = TCAManager()
        fills_by_sym = {"000001": [], "000002": [FillRecord("000002", "BUY", 500, 20.0, "2026-01-01")]}
        benchmarks = {"000001": BenchmarkPrices(10.0, 10.0), "000002": BenchmarkPrices(20.0, 20.0)}
        reports = tca.analyze_batch(fills_by_sym, benchmarks)
        assert "000001" not in reports
        assert "000002" in reports

    @pytest.mark.unit
    def test_missing_benchmark_skipped(self):
        tca = TCAManager()
        fills_by_sym = {"000001": [FillRecord("000001", "BUY", 1000, 10.0, "2026-01-01")]}
        benchmarks = {}
        reports = tca.analyze_batch(fills_by_sym, benchmarks)
        assert "000001" not in reports


# ============================================================
# summarize
# ============================================================


class TestSummarize:
    @pytest.mark.unit
    def test_empty(self):
        tca = TCAManager()
        s = tca.summarize({})
        assert s["total_notional"] == 0
        assert s["total_cost"] == 0

    @pytest.mark.unit
    def test_multi(self):
        tca = TCAManager()
        r1 = tca.analyze(
            [FillRecord("000001", "BUY", 1000, 10.0, "2026-01-01")],
            BenchmarkPrices(10.0, 10.0),
        )
        r2 = tca.analyze(
            [FillRecord("000002", "BUY", 500, 20.0, "2026-01-01")],
            BenchmarkPrices(20.0, 20.0),
        )
        s = tca.summarize({"000001": r1, "000002": r2})
        assert s["n_orders"] == 2
        assert s["total_notional"] == pytest.approx(20000)
        assert "grade_distribution" in s
        assert "worst_symbol" in s
        assert "best_symbol" in s


# ============================================================
# save_report
# ============================================================


class TestSaveReport:
    @pytest.mark.unit
    def test_save(self, tmp_path):
        tca = TCAManager()
        r = tca.analyze(
            [FillRecord("000001", "BUY", 1000, 10.0, "2026-01-01")],
            BenchmarkPrices(10.0, 10.0),
        )
        path = tca.save_report(r, tmp_path / "tca" / "report.json")
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["symbol"] == "000001"