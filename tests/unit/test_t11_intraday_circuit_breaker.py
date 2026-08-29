"""T11 单元测试 — IntradayCircuitBreaker 盘中断路器."""

from __future__ import annotations

import time

import pytest

from utils.risk.intraday_circuit_breaker import (
    CBState,
    IntradayCircuitBreaker,
)


class TestConfigValidation:
    def test_invalid_threshold_raises(self):
        with pytest.raises(ValueError):
            IntradayCircuitBreaker(consecutive_fail_threshold=0)

    def test_invalid_vol_window_raises(self):
        with pytest.raises(ValueError):
            IntradayCircuitBreaker(vol_window=1)


class TestConsecutiveFailTrip:
    def test_below_threshold_stays_closed(self):
        cb = IntradayCircuitBreaker(consecutive_fail_threshold=5)
        for _ in range(4):
            cb.record_failure("timeout")
        assert cb.state == CBState.CLOSED
        assert cb.allow_trading

    def test_above_threshold_opens(self):
        cb = IntradayCircuitBreaker(consecutive_fail_threshold=3)
        cb.record_failure("a")
        cb.record_failure("b")
        assert cb.state == CBState.CLOSED
        cb.record_failure("c")
        assert cb.state == CBState.OPEN
        assert not cb.allow_trading
        m = cb.metrics
        assert m.trip_count == 1
        assert "CONSECUTIVE_FAIL" in m.last_trip_reasons[0]

    def test_success_resets_consecutive(self):
        cb = IntradayCircuitBreaker(consecutive_fail_threshold=3)
        cb.record_failure("a")
        cb.record_failure("b")
        cb.record_success()  # reset
        cb.record_failure("c")  # 1 次, 不会触发
        assert cb.state == CBState.CLOSED


class TestIntradayDrawdownTrip:
    def test_drawdown_breach_trips(self):
        cb = IntradayCircuitBreaker(intraday_dd_pct=0.03)
        cb.update_intraday_pnl(0.05)  # 峰值 5%
        cb.update_intraday_pnl(0.019)  # 回撤 3.1% > 3%
        assert cb.state == CBState.OPEN
        assert any("DRAWDOWN" in r for r in cb.metrics.last_trip_reasons)
        assert cb.metrics.intraday_max_drawdown_pct >= 0.031

    def test_drawdown_within_safe(self):
        cb = IntradayCircuitBreaker(intraday_dd_pct=0.05)
        cb.update_intraday_pnl(0.02)
        cb.update_intraday_pnl(-0.01)  # 3% 回撤 < 5%
        assert cb.state == CBState.CLOSED


class TestVolatilityBurstTrip:
    def test_vol_spike_trips(self):
        # 送一组方差极大的回报序列
        cb = IntradayCircuitBreaker(
            realized_vol_annual_pct=0.05,  # 5% 极严阈值
            vol_window=5,
        )
        # 回报序列: 交替 ±10% → 年化 vol 必然爆炸
        for pnl in [0.0, 0.10, -0.10, 0.10, -0.10, 0.10]:
            cb.update_intraday_pnl(pnl)
        assert cb.state == CBState.OPEN
        assert any("VOL_BURST" in r for r in cb.metrics.last_trip_reasons)


class TestStateMachineCooloffAndHalfOpen:
    def test_cooloff_transitions_to_half_open(self):
        cb = IntradayCircuitBreaker(
            consecutive_fail_threshold=2,
            cooloff_seconds=1,
        )
        cb.record_failure("a")
        cb.record_failure("b")
        assert cb.state == CBState.OPEN
        time.sleep(1.1)
        # 冷却到期 → HALF_OPEN
        assert cb.state == CBState.HALF_OPEN
        assert cb.allow_trading

    def test_half_open_success_recovers_to_closed(self):
        cb = IntradayCircuitBreaker(consecutive_fail_threshold=1, cooloff_seconds=1)
        cb.record_failure("a")
        time.sleep(1.1)
        assert cb.state == CBState.HALF_OPEN
        cb.record_success()
        assert cb.state == CBState.CLOSED

    def test_half_open_failure_reopens(self):
        cb = IntradayCircuitBreaker(
            consecutive_fail_threshold=1,
            cooloff_seconds=1,
            half_open_max_failures=1,
        )
        cb.record_failure("a")
        time.sleep(1.1)
        assert cb.state == CBState.HALF_OPEN
        cb.record_failure("b")  # HALF_OPEN 下失败 → 立即 OPEN
        assert cb.state == CBState.OPEN


class TestManualControl:
    def test_manual_trip(self):
        cb = IntradayCircuitBreaker(consecutive_fail_threshold=999)
        cb.trip(reason="emergency")
        assert cb.state == CBState.OPEN
        assert "MANUAL" in cb.metrics.last_trip_reasons[0]

    def test_manual_close_reset(self):
        cb = IntradayCircuitBreaker(consecutive_fail_threshold=1)
        cb.record_failure("x")
        assert cb.state == CBState.OPEN
        cb.close()
        assert cb.state == CBState.CLOSED
        assert cb.metrics.total_failures == 0


class TestSnapshot:
    def test_snapshot_has_expected_keys(self):
        cb = IntradayCircuitBreaker(consecutive_fail_threshold=3)
        s = cb.snapshot()
        for k in (
            "state",
            "consecutive_failures",
            "consecutive_fail_threshold",
            "total_failures",
            "total_successes",
            "trip_count",
            "drawdown_threshold_pct",
            "vol_threshold_annualized",
        ):
            assert k in s, f"缺少 {k}"
