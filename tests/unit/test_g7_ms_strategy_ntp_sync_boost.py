"""G7 boost: ms_strategy/src/execution/ntp_sync.py 单元测试.

覆盖 NTPSync 同步/降级/健康检查/快照全部公开接口,
包括 ntplib 不可用、主备切换、全失败、连续失败降级等异常分支.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from ms_strategy.src.execution.ntp_sync import NTPSync  # noqa: E402


def _make_resp(tx_time: float) -> SimpleNamespace:
    """构造 mock NTP 响应 (带 tx_time 属性)."""
    return SimpleNamespace(tx_time=tx_time)


@pytest.fixture
def ntp_client():
    """patch ntplib.NTPClient, yield mock 实例 (默认 request 成功)."""
    with patch("ntplib.NTPClient") as mock_client_cls:
        mock_inst = mock_client_cls.return_value
        mock_inst.request.return_value = _make_resp(time.time())
        yield mock_inst


# ============================================================
# 1. 构造与首次同步
# ============================================================


class TestNTPSyncInit:
    def test_main_server_success(self, ntp_client):
        """主服务器同步成功: offset/last_sync/active_server 正确."""
        ntp_client.request.return_value = _make_resp(time.time() + 0.05)
        ntp = NTPSync(server="ntp.tencent.com")
        # resp.tx_time - time.time() 有微秒级调用间隔, 用 1e-2 容差
        assert ntp.offset_seconds == pytest.approx(0.05, abs=1e-2)
        assert ntp.last_sync is not None
        assert ntp.active_server == "ntp.tencent.com"
        assert ntp.sync_failed_count == 0

    def test_fallback_to_secondary(self, ntp_client):
        """主服务器失败, 切换备用成功."""
        ntp_client.request.side_effect = [
            OSError("main down"),
            _make_resp(time.time() - 0.01),
        ]
        ntp = NTPSync(server="ntp.tencent.com", fallback_servers=["ntp.aliyun.com"])
        assert ntp.active_server == "ntp.aliyun.com"
        assert ntp.sync_failed_count == 0

    def test_all_servers_fail(self, ntp_client):
        """所有服务器失败: sync_failed_count=1, last_sync=None."""
        ntp_client.request.side_effect = OSError("all down")
        ntp = NTPSync(server="ntp.tencent.com", fallback_servers=["ntp.aliyun.com"])
        assert ntp.sync_failed_count == 1
        assert ntp.offset_seconds == 0.0
        assert ntp.last_sync is None

    def test_ntplib_missing_degraded(self):
        """ntplib 未安装: 降级到本地时间."""
        with patch.dict(sys.modules, {"ntplib": None}):
            ntp = NTPSync(server="ntp.tencent.com")
            assert ntp.offset_seconds == 0.0
            assert ntp.last_sync is not None
            assert ntp.sync_failed_count == 0

    def test_drift_exceeds_threshold_warning(self, ntp_client):
        """offset 漂移超过阈值: 走 logger.warning 分支 (仍成功)."""
        ntp_client.request.return_value = _make_resp(time.time() + 0.1)
        ntp = NTPSync(server="ntp.tencent.com", max_drift_ms=50.0)
        assert ntp.offset_seconds == pytest.approx(0.1, abs=1e-2)


# ============================================================
# 2. _do_sync 异常类型
# ============================================================


class TestDoSyncExceptions:
    def test_various_exceptions_caught(self, ntp_client):
        """request 抛各种异常都应被捕获并切换备用."""
        ntp_client.request.side_effect = [
            ValueError("bad"), TypeError("bad"), KeyError("bad"),
            AttributeError("bad"), RuntimeError("bad"), OSError("bad"),
            TimeoutError("bad"), ConnectionError("bad"),
        ]
        ntp = NTPSync(
            server="s0",
            fallback_servers=["s1", "s2", "s3", "s4", "s5", "s6", "s7"],
        )
        assert ntp.sync_failed_count == 1

    def test_consecutive_failures_degraded(self, ntp_client):
        """连续失败 > 5 次进入降级模式."""
        ntp_client.request.side_effect = OSError("down")
        ntp = NTPSync(server="s0", fallback_servers=[])
        for _ in range(6):
            ntp._do_sync()
        assert ntp.sync_failed_count > 5

    def test_sync_returns_true_on_success(self, ntp_client):
        ntp = NTPSync(server="ntp.tencent.com")
        assert ntp._do_sync() is True

    def test_sync_returns_false_on_all_fail(self, ntp_client):
        ntp_client.request.side_effect = OSError("down")
        ntp = NTPSync(server="s0", fallback_servers=[])
        result = ntp._do_sync()
        assert result is False
        assert ntp.sync_failed_count == 2


# ============================================================
# 3. sync_if_needed
# ============================================================


class TestSyncIfNeeded:
    def test_last_sync_none_triggers_sync(self, ntp_client):
        ntp = NTPSync(server="ntp.tencent.com")
        ntp.last_sync = None
        assert ntp.sync_if_needed() is True
        assert ntp.last_sync is not None

    def test_within_interval_no_resync(self, ntp_client):
        ntp = NTPSync(server="ntp.tencent.com", resync_interval_min=30)
        ntp.last_sync = datetime.utcnow()
        ntp_client.request.reset_mock()
        assert ntp.sync_if_needed() is True
        ntp_client.request.assert_not_called()

    def test_past_interval_triggers_resync(self, ntp_client):
        ntp = NTPSync(server="ntp.tencent.com", resync_interval_min=30)
        ntp.last_sync = datetime.utcnow() - timedelta(hours=1)
        assert ntp.sync_if_needed() is True


# ============================================================
# 4. 访问器方法
# ============================================================


class TestAccessors:
    def test_server_ts_local_ts_offset(self, ntp_client):
        ntp = NTPSync(server="ntp.tencent.com")
        ntp.offset_seconds = 0.1
        s = ntp.server_ts()
        local = ntp.local_ts()
        assert (s - local).total_seconds() == pytest.approx(0.1, abs=1e-2)

    def test_get_offset(self, ntp_client):
        ntp = NTPSync(server="ntp.tencent.com")
        ntp.offset_seconds = -0.005
        assert ntp.get_offset() == pytest.approx(-0.005)

    def test_drift_ms(self, ntp_client):
        ntp = NTPSync(server="ntp.tencent.com")
        ntp.offset_seconds = -0.003
        assert ntp.drift_ms() == pytest.approx(3.0)

    def test_is_healthy_true(self, ntp_client):
        ntp = NTPSync(server="ntp.tencent.com", max_drift_ms=50.0)
        assert ntp.is_healthy() is True

    def test_is_healthy_false_no_sync(self, ntp_client):
        ntp_client.request.side_effect = OSError("down")
        ntp = NTPSync(server="ntp.tencent.com")
        ntp.last_sync = None
        assert ntp.is_healthy() is False

    def test_is_healthy_false_too_many_failures(self, ntp_client):
        ntp = NTPSync(server="ntp.tencent.com")
        ntp.sync_failed_count = 6
        assert ntp.is_healthy() is False

    def test_is_healthy_false_drift_too_large(self, ntp_client):
        ntp = NTPSync(server="ntp.tencent.com", max_drift_ms=10.0)
        ntp.offset_seconds = 0.05
        assert ntp.is_healthy() is False

    def test_snapshot_structure(self, ntp_client):
        ntp = NTPSync(server="ntp.tencent.com")
        snap = ntp.snapshot()
        assert set(snap.keys()) == {
            "server", "offset_seconds", "drift_ms",
            "last_sync", "failed_count", "healthy",
        }
        assert snap["server"] == "ntp.tencent.com"
        assert snap["failed_count"] == 0

    def test_sync_compat(self, ntp_client):
        """sync() 兼容旧 API, 等价 _do_sync."""
        ntp = NTPSync(server="ntp.tencent.com")
        assert ntp.sync() is True
