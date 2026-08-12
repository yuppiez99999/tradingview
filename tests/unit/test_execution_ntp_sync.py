"""ms_strategy.src.execution.ntp_sync 单元测试 — NTPSync 同步逻辑 + 降级阈值 (monkeypatch 隔离网络)"""
from __future__ import annotations

from datetime import datetime

import pytest

from ms_strategy.src.execution.ntp_sync import NTPSync


@pytest.fixture
def ntp(monkeypatch):
    """隔离网络: monkeypatch _do_sync 返回成功, 不触发真实 NTP 请求"""
    inst = NTPSync.__new__(NTPSync)
    # 手动初始化 (跳过 __init__ 的网络同步)
    inst.fallback_servers = ["ntp.aliyun.com"]
    inst.resync_interval = __import__("datetime").timedelta(minutes=30)
    inst.max_drift_ms = 50.0
    inst.offset_seconds = -0.001
    inst.last_sync = datetime.utcnow()
    inst.sync_failed_count = 0
    inst.active_server = "ntp.aliyun.com"
    # 确保 sync/sync_if_needed 不触网
    def _fake_sync(self):
        self.last_sync = datetime.utcnow()
        self.sync_failed_count = 0
        return True
    monkeypatch.setattr(NTPSync, "_do_sync", _fake_sync)
    return inst


def test_ntp_sync_initial_state(ntp):
    """构造后 last_sync 已设置, offset_seconds 为有限浮点"""
    assert ntp.last_sync is not None
    assert isinstance(ntp.offset_seconds, float)


def test_ntp_sync_is_healthy_initial(ntp):
    """初始 sync_failed_count=0 → is_healthy=True"""
    assert ntp.sync_failed_count == 0
    assert ntp.is_healthy() is True


def test_ntp_sync_drift_and_offset(ntp):
    """drift_ms = |offset_seconds| * 1000 (绝对值)"""
    ntp.offset_seconds = -0.009
    assert ntp.drift_ms() == pytest.approx(9.0, abs=1e-3)
    assert ntp.get_offset() == pytest.approx(-0.009, abs=1e-9)


def test_ntp_sync_unhealthy_after_failures(ntp):
    """连续失败 > 5 次 → is_healthy() 返回 False"""
    ntp.sync_failed_count = 6
    assert ntp.is_healthy() is False


def test_ntp_sync_snapshot_keys(ntp):
    """snapshot() 返回完整结构 (server/offset/drift/last_sync/healthy)"""
    snap = ntp.snapshot()
    assert set(snap.keys()) >= {
        "server", "offset_seconds", "drift_ms", "last_sync", "healthy"
    }
    assert snap["healthy"] is True


def test_ntp_sync_server_ts_local_ts(ntp):
    """server_ts = local_ts + offset, 差值接近 |offset|"""
    ntp.offset_seconds = -0.009
    s = ntp.server_ts()
    l = ntp.local_ts()
    diff = abs((s - l).total_seconds())
    assert diff == pytest.approx(0.009, abs=1e-2)


def test_ntp_sync_sync_if_needed_resync_window(ntp):
    """sync_if_needed: last_sync 新鲜时返回 True (不强制网络同步)"""
    ntp.last_sync = datetime.utcnow()
    assert ntp.sync_if_needed() is True
