"""T17 单元测试 — LiveReconciliationLoop 实盘对账循环."""
from __future__ import annotations

import pytest

from utils.risk.live_reconciliation_loop import (
    LiveReconciliationLoop,
    PositionDrift,
)
from utils.risk.risk_audit_logger import RiskAuditLogger
from utils.risk.trade_order_reconciler import (
    FillRecord,
    PlannedOrder,
    TradeOrderReconciler,
)

# ============================================================
# 测试夹具
# ============================================================

class MockBroker:
    """模拟 broker 持仓查询."""

    def __init__(self, positions: dict[str, int] | None = None):
        self._positions = positions or {}
        self.get_positions_called = 0

    def get_positions(self) -> dict[str, int]:
        self.get_positions_called += 1
        return dict(self._positions)


class FailingBroker(MockBroker):
    def get_positions(self) -> dict[str, int]:
        raise RuntimeError("broker connection lost")


def _make_loop(
    broker: MockBroker | None = None,
    local_book: dict[str, int] | None = None,
    reconciler: TradeOrderReconciler | None = None,
    audit: RiskAuditLogger | None = None,
    **kwargs,
) -> LiveReconciliationLoop:
    import tempfile
    from pathlib import Path
    if audit is None:
        audit = RiskAuditLogger(project_root=Path(tempfile.mkdtemp()), audit_dir="audit")
    return LiveReconciliationLoop(
        reconciler=reconciler or TradeOrderReconciler(),
        broker=broker or MockBroker({}),
        local_position_book=local_book or {},
        audit_logger=audit,
        **kwargs,
    )


# ============================================================
# 配置校验
# ============================================================

class TestConfigValidation:
    def test_invalid_interval_raises(self):
        with pytest.raises(ValueError, match="intraday_interval_sec"):
            _make_loop(intraday_interval_sec=5)

    def test_invalid_drift_thresholds(self):
        with pytest.raises(ValueError, match="drift_alert_pct"):
            _make_loop(drift_alert_pct=0.15, drift_halt_pct=0.10)

    def test_drift_alert_equals_halt_raises(self):
        with pytest.raises(ValueError, match="drift_alert_pct"):
            _make_loop(drift_alert_pct=0.05, drift_halt_pct=0.05)


# ============================================================
# 持仓 drift 检测
# ============================================================

class TestPositionDrift:
    def test_no_drift_when_matching(self):
        broker = MockBroker({"sh600000": 1000})
        loop = _make_loop(broker=broker, local_book={"sh600000": 1000})
        drifts = loop.detect_position_drift()
        assert len(drifts) == 0

    def test_drift_detected(self):
        broker = MockBroker({"sh600000": 1200})
        loop = _make_loop(broker=broker, local_book={"sh600000": 1000})
        drifts = loop.detect_position_drift()
        assert len(drifts) == 1
        assert drifts[0].symbol == "sh600000"
        assert drifts[0].drift_qty == 200
        assert drifts[0].drift_pct > 0

    def test_small_drift_ignored(self):
        """2% 以下 drift → ignore."""
        broker = MockBroker({"sh600000": 1001})
        loop = _make_loop(broker=broker, local_book={"sh600000": 1000},
                          drift_alert_pct=0.02, drift_halt_pct=0.10)
        drifts = loop.detect_position_drift()
        assert len(drifts) == 1
        assert drifts[0].suggested_action == "ignore"

    def test_medium_drift_alert(self):
        """2-10% drift → alert."""
        broker = MockBroker({"sh600000": 1050})
        loop = _make_loop(broker=broker, local_book={"sh600000": 1000},
                          drift_alert_pct=0.02, drift_halt_pct=0.10)
        drifts = loop.detect_position_drift()
        assert drifts[0].suggested_action == "alert"

    def test_large_drift_halt(self):
        """≥10% drift → halt."""
        broker = MockBroker({"sh600000": 1200})
        loop = _make_loop(broker=broker, local_book={"sh600000": 1000},
                          drift_alert_pct=0.02, drift_halt_pct=0.10)
        drifts = loop.detect_position_drift()
        assert drifts[0].suggested_action == "halt"

    def test_missing_in_local(self):
        """broker 有但本地没有的标的."""
        broker = MockBroker({"sh600000": 500, "sz000001": 300})
        loop = _make_loop(broker=broker, local_book={"sh600000": 500})
        drifts = loop.detect_position_drift()
        assert len(drifts) == 1
        assert drifts[0].symbol == "sz000001"

    def test_missing_in_broker(self):
        """本地有但 broker 没有."""
        broker = MockBroker({"sh600000": 500})
        loop = _make_loop(broker=broker, local_book={"sh600000": 500, "sz000001": 300})
        drifts = loop.detect_position_drift()
        assert len(drifts) == 1
        assert drifts[0].symbol == "sz000001"
        assert drifts[0].drift_qty == -300

    def test_broker_exception_returns_empty(self):
        broker = FailingBroker()
        loop = _make_loop(broker=broker)
        drifts = loop.detect_position_drift()
        assert len(drifts) == 0


# ============================================================
# 盘中对账
# ============================================================

class TestIntradayTick:
    def test_pass_when_no_drift_no_issues(self):
        broker = MockBroker({"sh600000": 1000})
        loop = _make_loop(broker=broker, local_book={"sh600000": 1000})
        report = loop.run_intraday_tick()
        assert report.verdict == "pass"
        assert report.drift_count == 0
        assert report.issues_count == 0

    def test_warn_when_drift_alert(self):
        broker = MockBroker({"sh600000": 1050})
        loop = _make_loop(broker=broker, local_book={"sh600000": 1000})
        report = loop.run_intraday_tick()
        assert report.verdict == "warn"
        assert report.drift_count == 1

    def test_halt_when_drift_halt(self):
        broker = MockBroker({"sh600000": 1200})
        loop = _make_loop(broker=broker, local_book={"sh600000": 1000})
        report = loop.run_intraday_tick()
        assert report.verdict == "halt"
        assert report.halt_count == 1

    def test_with_t13_reconciliation(self):
        broker = MockBroker({"sh600000": 1000})
        loop = _make_loop(broker=broker, local_book={"sh600000": 1000})
        planned = [PlannedOrder("o1", "sh600000", "buy", 1000, 10.0)]
        fills = [FillRecord("f1", "o1", "sh600000", "buy", 1000, 10.0)]
        report = loop.run_intraday_tick(planned_orders=planned, fills=fills)
        assert report.base_reconciliation is not None
        assert report.base_reconciliation.all_pass


# ============================================================
# 盘后全量对账
# ============================================================

class TestEodFinal:
    def test_eod_pass(self):
        broker = MockBroker({"sh600000": 1000})
        loop = _make_loop(broker=broker, local_book={"sh600000": 1000})
        planned = [PlannedOrder("o1", "sh600000", "buy", 1000, 10.0)]
        fills = [FillRecord("f1", "o1", "sh600000", "buy", 1000, 10.0)]
        report = loop.run_eod_final(planned, fills)
        assert report.verdict == "pass"
        assert report.mode == "eod"

    def test_eod_warn_with_coverage_issue(self):
        broker = MockBroker({"sh600000": 1000})
        loop = _make_loop(broker=broker, local_book={"sh600000": 1000})
        planned = [PlannedOrder("o1", "sh600000", "buy", 1000, 10.0)]
        fills: list[FillRecord] = []  # 没有成交
        report = loop.run_eod_final(planned, fills)
        assert report.verdict == "warn"
        assert report.issues_count > 0

    def test_eod_halt_with_large_drift(self):
        broker = MockBroker({"sh600000": 2000})
        loop = _make_loop(broker=broker, local_book={"sh600000": 1000})
        planned = [PlannedOrder("o1", "sh600000", "buy", 1000, 10.0)]
        fills = [FillRecord("f1", "o1", "sh600000", "buy", 1000, 10.0)]
        report = loop.run_eod_final(planned, fills)
        assert report.verdict == "halt"


# ============================================================
# 工具方法
# ============================================================

class TestUtilities:
    def test_update_local_book(self):
        loop = _make_loop(local_book={"sh": 100})
        loop.update_local_book("sh", 200)
        assert loop.local_book["sh"] == 200

    def test_should_run_intraday_first_time(self):
        loop = _make_loop()
        assert loop.should_run_intraday() is True

    def test_should_run_intraday_after_tick(self):
        loop = _make_loop(intraday_interval_sec=300)
        loop.run_intraday_tick()
        assert loop.should_run_intraday() is False

    def test_summary_text_non_empty(self):
        broker = MockBroker({"sh600000": 1050})
        loop = _make_loop(broker=broker, local_book={"sh600000": 1000})
        report = loop.run_intraday_tick()
        text = report.summary_text()
        assert "实盘对账报告" in text
        assert "verdict" in text


# ============================================================
# PositionDrift 属性
# ============================================================

class TestPositionDriftProperties:
    def test_has_drift_true(self):
        d = PositionDrift("sh", 100, 110, 10, 0.1)
        assert d.has_drift

    def test_has_drift_false(self):
        d = PositionDrift("sh", 100, 100, 0, 0.0)
        assert not d.has_drift
