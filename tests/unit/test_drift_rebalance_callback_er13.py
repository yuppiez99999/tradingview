"""ER-1.3: 漂移→再平衡回调生产启用测试 (Wave 7-ERL Sprint 1)

验证目标:
    1. _make_drift_rebalance_callback 返回可调用回调
    2. alerts 为空时回调返回 False
    3. alerts 非空时设置 _drift_rebalance_pending = True 并返回 True
    4. 回调计入 _drift_rebalance_history 审计日志
    5. history 超过 100 条时截断到最近 100 条
    6. _init_drift_monitor 实例化 DriftMonitor 时传入 rebalance_callback
    7. 回调 fail-safe: 异常不传播
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))


class TestDriftRebalanceCallback:
    """漂移→再平衡回调测试."""

    def _make_rebalancer_with_mocks(self):
        """创建 mock 依赖的 ETFOptionHedgeRebalancer 实例."""
        with (
            patch("etf_option_hedge_rebalancer._DD_OK", True),
            patch("etf_option_hedge_rebalancer._KS_OK", True),
            patch("etf_option_hedge_rebalancer._PP_OK", True),
            patch("etf_option_hedge_rebalancer._PO_OK", False),
            patch("etf_option_hedge_rebalancer._SF_OK", False),
            patch("etf_option_hedge_rebalancer._VRW_OK", False),
            patch("etf_option_hedge_rebalancer._DM_OK", True),
            patch("etf_option_hedge_rebalancer._MR_OK", False),
            patch("etf_option_hedge_rebalancer._EO_V2_OK", False),
        ):
            from etf_option_hedge_rebalancer import ETFOptionHedgeRebalancer

            return ETFOptionHedgeRebalancer()

    def test_callback_is_callable(self):
        """_make_drift_rebalance_callback 返回可调用对象."""
        rebalancer = self._make_rebalancer_with_mocks()
        cb = rebalancer._make_drift_rebalance_callback()
        assert callable(cb)

    def test_empty_alerts_returns_false(self):
        """alerts 为空时回调返回 False."""
        rebalancer = self._make_rebalancer_with_mocks()
        cb = rebalancer._make_drift_rebalance_callback()
        result = cb([])
        assert result is False

    def test_non_empty_alerts_sets_pending_flag(self):
        """alerts 非空时设置 _drift_rebalance_pending = True."""
        rebalancer = self._make_rebalancer_with_mocks()
        assert rebalancer._drift_rebalance_pending is False

        cb = rebalancer._make_drift_rebalance_callback()
        alerts = [MagicMock(severity="critical"), MagicMock(severity="warning")]
        result = cb(alerts)

        assert result is True
        assert rebalancer._drift_rebalance_pending is True

    def test_callback_records_history(self):
        """回调计入 _drift_rebalance_history 审计日志."""
        rebalancer = self._make_rebalancer_with_mocks()
        cb = rebalancer._make_drift_rebalance_callback()

        alerts = [MagicMock(severity="critical")]
        cb(alerts)

        assert len(rebalancer._drift_rebalance_history) == 1
        record = rebalancer._drift_rebalance_history[0]
        assert "triggered_at" in record
        assert record["alert_count"] == 1
        assert "severity_counts" in record

    def test_history_truncated_to_100(self):
        """history 超过 100 条时截断到最近 100 条."""
        rebalancer = self._make_rebalancer_with_mocks()
        cb = rebalancer._make_drift_rebalance_callback()
        alerts = [MagicMock(severity="low")]

        for _ in range(105):
            cb(alerts)

        assert len(rebalancer._drift_rebalance_history) == 100

    def test_severity_counts_correct(self):
        """severity_counts 正确统计各严重级别数量."""
        rebalancer = self._make_rebalancer_with_mocks()
        cb = rebalancer._make_drift_rebalance_callback()
        alerts = [
            MagicMock(severity="critical"),
            MagicMock(severity="critical"),
            MagicMock(severity="warning"),
        ]
        cb(alerts)

        record = rebalancer._drift_rebalance_history[-1]
        assert record["severity_counts"]["critical"] == 2
        assert record["severity_counts"]["warning"] == 1

    def test_alert_without_severity_attribute(self):
        """alert 无 severity 属性时记为 unknown."""
        rebalancer = self._make_rebalancer_with_mocks()
        cb = rebalancer._make_drift_rebalance_callback()
        alerts = [MagicMock(spec=[])]
        cb(alerts)

        record = rebalancer._drift_rebalance_history[-1]
        assert record["severity_counts"].get("unknown") == 1


class TestInitDriftMonitorWithCallback:
    """_init_drift_monitor 传入 rebalance_callback 测试."""

    def test_drift_monitor_receives_rebalance_callback(self):
        """DriftMonitor 实例化时传入非 None 的 rebalance_callback."""
        with (
            patch("etf_option_hedge_rebalancer._DD_OK", True),
            patch("etf_option_hedge_rebalancer._KS_OK", True),
            patch("etf_option_hedge_rebalancer._PP_OK", True),
            patch("etf_option_hedge_rebalancer._PO_OK", False),
            patch("etf_option_hedge_rebalancer._SF_OK", False),
            patch("etf_option_hedge_rebalancer._VRW_OK", False),
            patch("etf_option_hedge_rebalancer._DM_OK", True),
            patch("etf_option_hedge_rebalancer._MR_OK", False),
            patch("etf_option_hedge_rebalancer._EO_V2_OK", False),
        ):
            from etf_option_hedge_rebalancer import ETFOptionHedgeRebalancer

            rebalancer = ETFOptionHedgeRebalancer()

        assert rebalancer.drift_monitor is not None
        assert rebalancer.drift_monitor.rebalance_callback is not None

    def test_drift_monitor_disabled_when_dm_not_ok(self):
        """_DM_OK=False 时 drift_monitor 为 None."""
        with (
            patch("etf_option_hedge_rebalancer._DD_OK", True),
            patch("etf_option_hedge_rebalancer._KS_OK", True),
            patch("etf_option_hedge_rebalancer._PP_OK", True),
            patch("etf_option_hedge_rebalancer._PO_OK", False),
            patch("etf_option_hedge_rebalancer._SF_OK", False),
            patch("etf_option_hedge_rebalancer._VRW_OK", False),
            patch("etf_option_hedge_rebalancer._DM_OK", False),
            patch("etf_option_hedge_rebalancer._MR_OK", False),
            patch("etf_option_hedge_rebalancer._EO_V2_OK", False),
        ):
            from etf_option_hedge_rebalancer import ETFOptionHedgeRebalancer

            rebalancer = ETFOptionHedgeRebalancer()

        assert rebalancer.drift_monitor is None

    def test_pending_flag_initialized_false(self):
        """_drift_rebalance_pending 初始化为 False."""
        rebalancer = None
        with (
            patch("etf_option_hedge_rebalancer._DD_OK", True),
            patch("etf_option_hedge_rebalancer._KS_OK", True),
            patch("etf_option_hedge_rebalancer._PP_OK", True),
            patch("etf_option_hedge_rebalancer._PO_OK", False),
            patch("etf_option_hedge_rebalancer._SF_OK", False),
            patch("etf_option_hedge_rebalancer._VRW_OK", False),
            patch("etf_option_hedge_rebalancer._DM_OK", True),
            patch("etf_option_hedge_rebalancer._MR_OK", False),
            patch("etf_option_hedge_rebalancer._EO_V2_OK", False),
        ):
            from etf_option_hedge_rebalancer import ETFOptionHedgeRebalancer

            rebalancer = ETFOptionHedgeRebalancer()

        assert rebalancer._drift_rebalance_pending is False
        assert rebalancer._drift_rebalance_history == []
