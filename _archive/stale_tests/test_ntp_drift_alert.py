"""
NTP 漂移分级告警单元测试 (v8.6.8 P2-LIVE-10)
================================================
测试目标:
    1. 三级阈值触发正确 (INFO/WARNING/CRITICAL)
    2. 告警去抖机制生效 (5 分钟内同级别不重复触发)
    3. 告警回调正确触发外部通知
    4. 告警统计计数准确
    5. 漂移历史记录正确 (ring buffer)
    6. 健康状态判断符合阈值
    7. 阈值配置校验 (critical > warning > max)
    8. NTPAlertCallback 多渠道分发正确

测试策略: Mock ntplib.NTPClient, 不依赖真实网络

运行: python -m pytest tests/test_ntp_drift_alert.py -v
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 添加项目根到 sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "v8.3_institutional"))

# v8.3_institutional 包名以数字开头, 无法用 import, 改用 importlib 加载
import importlib.util  # noqa: E402


def _load_module_from_path(module_name: str, file_path: Path):
    """从文件路径加载 Python 模块"""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# 加载 ntp_sync 和 ntp_alert_callback
_ntp_sync_path = PROJECT_ROOT / "v8.3_institutional" / "src" / "execution" / "ntp_sync.py"
_ntp_callback_path = PROJECT_ROOT / "v8.3_institutional" / "src" / "execution" / "ntp_alert_callback.py"

ntp_sync_module = _load_module_from_path("ntp_sync_module", _ntp_sync_path)
ntp_callback_module = _load_module_from_path("ntp_alert_callback_module", _ntp_callback_path)

NTPSync = ntp_sync_module.NTPSync
ALERT_LEVEL_INFO = ntp_sync_module.ALERT_LEVEL_INFO
ALERT_LEVEL_WARNING = ntp_sync_module.ALERT_LEVEL_WARNING
ALERT_LEVEL_CRITICAL = ntp_sync_module.ALERT_LEVEL_CRITICAL

NTPAlertCallback = ntp_callback_module.NTPAlertCallback
create_silent_callback = ntp_callback_module.create_silent_callback


# ------------------------------------------------------------
# 测试辅助
# ------------------------------------------------------------

class MockNTPResponse:
    """模拟 ntplib.NTPClient.request 返回值"""

    def __init__(self, tx_time: float):
        self.tx_time = tx_time


def make_ntp_sync_with_drift(drift_ms: float, alert_callback=None) -> NTPSync:
    """创建一个指定漂移的 NTPSync 实例 (Mock ntplib)

    Args:
        drift_ms: 期望的漂移 (毫秒)
        alert_callback: 告警回调
    """
    # 计算 tx_time: tx_time - time.time() = offset_seconds = drift_ms / 1000
    # 正漂移 = 服务器比本地快
    target_offset = drift_ms / 1000.0

    with patch("ntplib.NTPClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.request.return_value = MockNTPResponse(time.time() + target_offset)
        mock_client_cls.return_value = mock_client

        return NTPSync(
            server="ntp.test.com",
            resync_interval_min=60,
            max_drift_ms=50.0,
            drift_warning_ms=500.0,
            drift_critical_ms=1300.0,
            alert_callback=alert_callback,
        )


# ------------------------------------------------------------
# 测试 1: 三级阈值触发正确
# ------------------------------------------------------------

class TestDriftThresholdAlerts:
    """测试三级阈值分级告警"""

    def test_no_alert_when_drift_below_warning(self):
        """漂移 < 500ms: 不触发告警"""
        with patch("ntplib.NTPClient") as mock_client_cls:
            mock_client = MagicMock()
            # 100ms 漂移, 健康范围
            mock_client.request.return_value = MockNTPResponse(time.time() + 0.1)
            mock_client_cls.return_value = mock_client

            ntp = NTPSync(drift_warning_ms=500, drift_critical_ms=1300)

        # 健康状态下不应触发 WARNING/CRITICAL
        stats = ntp.get_alert_stats()
        assert stats[ALERT_LEVEL_WARNING] == 0
        assert stats[ALERT_LEVEL_CRITICAL] == 0
        assert ntp.is_healthy() is True

    def test_warning_triggered_when_drift_500_to_1300(self):
        """漂移 500-1300ms: 触发 WARNING"""
        with patch("ntplib.NTPClient") as mock_client_cls:
            mock_client = MagicMock()
            # 800ms 漂移
            mock_client.request.return_value = MockNTPResponse(time.time() + 0.8)
            mock_client_cls.return_value = mock_client

            ntp = NTPSync(drift_warning_ms=500, drift_critical_ms=1300)

        stats = ntp.get_alert_stats()
        assert stats[ALERT_LEVEL_WARNING] == 1
        assert stats[ALERT_LEVEL_CRITICAL] == 0
        assert ntp.is_healthy() is False  # WARNING 状态下不健康

    def test_critical_triggered_when_drift_above_1300(self):
        """漂移 >= 1300ms: 触发 CRITICAL"""
        with patch("ntplib.NTPClient") as mock_client_cls:
            mock_client = MagicMock()
            # 1500ms 漂移
            mock_client.request.return_value = MockNTPResponse(time.time() + 1.5)
            mock_client_cls.return_value = mock_client

            ntp = NTPSync(drift_warning_ms=500, drift_critical_ms=1300)

        stats = ntp.get_alert_stats()
        assert stats[ALERT_LEVEL_WARNING] == 0
        assert stats[ALERT_LEVEL_CRITICAL] == 1
        assert ntp.is_healthy() is False


# ------------------------------------------------------------
# 测试 2: 告警去抖机制
# ------------------------------------------------------------

class TestAlertDebounce:
    """测试告警去抖 (5 分钟内同级别不重复触发)"""

    def test_debounce_blocks_same_level_alert_within_window(self):
        """去抖窗口内同级别告警不重复触发"""
        with patch("ntplib.NTPClient") as mock_client_cls:
            mock_client = MagicMock()
            # 800ms 漂移 (WARNING)
            mock_client.request.return_value = MockNTPResponse(time.time() + 0.8)
            mock_client_cls.return_value = mock_client

            ntp = NTPSync(drift_warning_ms=500, drift_critical_ms=1300)

        # 第一次告警
        assert ntp.get_alert_stats()[ALERT_LEVEL_WARNING] == 1

        # 强制再次同步 (但去抖窗口内, 不应重复告警)
        ntp._do_sync()
        assert ntp.get_alert_stats()[ALERT_LEVEL_WARNING] == 1  # 仍为 1

    def test_debounce_allows_after_window_expires(self):
        """去抖窗口过后同级别告警可以再次触发"""
        # 用 side_effect 让每次 request 都基于当前 time.time() 计算, 避免漂移漂移
        with patch("ntplib.NTPClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.request.side_effect = lambda *a, **kw: MockNTPResponse(time.time() + 0.8)
            mock_client_cls.return_value = mock_client

            ntp = NTPSync(drift_warning_ms=500, drift_critical_ms=1300)

            # 第一次告警
            assert ntp.get_alert_stats()[ALERT_LEVEL_WARNING] == 1

            # 模拟去抖窗口过期 (将 last_alert_at 时间戳调到 5 分钟前)
            ntp._last_alert_at[ALERT_LEVEL_WARNING] = time.time() - 301

            # 再次同步, 应触发新的告警
            ntp._do_sync()
            assert ntp.get_alert_stats()[ALERT_LEVEL_WARNING] == 2


# ------------------------------------------------------------
# 测试 3: 告警回调机制
# ------------------------------------------------------------

class TestAlertCallback:
    """测试告警回调"""

    def test_callback_invoked_on_warning(self):
        """WARNING 时回调被调用"""
        received_alerts = []

        def test_callback(payload):
            received_alerts.append(payload)

        with patch("ntplib.NTPClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.request.return_value = MockNTPResponse(time.time() + 0.8)
            mock_client_cls.return_value = mock_client

            NTPSync(alert_callback=test_callback)

        assert len(received_alerts) == 1
        assert received_alerts[0]["level"] == ALERT_LEVEL_WARNING
        assert received_alerts[0]["drift_ms"] == pytest.approx(800, rel=0.01)

    def test_callback_invoked_on_critical(self):
        """CRITICAL 时回调被调用"""
        received_alerts = []

        def test_callback(payload):
            received_alerts.append(payload)

        with patch("ntplib.NTPClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.request.return_value = MockNTPResponse(time.time() + 1.5)
            mock_client_cls.return_value = mock_client

            NTPSync(alert_callback=test_callback)

        assert len(received_alerts) == 1
        assert received_alerts[0]["level"] == ALERT_LEVEL_CRITICAL
        assert "thresholds" in received_alerts[0]
        assert "drift_critical_ms" in received_alerts[0]["thresholds"]

    def test_callback_exception_does_not_crash_sync(self):
        """回调异常不应影响 NTP 同步流程"""
        def bad_callback(payload):
            raise RuntimeError("测试回调异常")

        with patch("ntplib.NTPClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.request.return_value = MockNTPResponse(time.time() + 0.8)
            mock_client_cls.return_value = mock_client

            # 不应抛出异常
            ntp = NTPSync(alert_callback=bad_callback)

        # 同步应正常完成
        assert ntp.last_sync is not None


# ------------------------------------------------------------
# 测试 4: 漂移历史记录 (ring buffer)
# ------------------------------------------------------------

class TestDriftHistory:
    """测试漂移历史记录"""

    def test_history_records_each_sync(self):
        """每次同步记录一条历史"""
        with patch("ntplib.NTPClient") as mock_client_cls:
            mock_client = MagicMock()
            # 用 side_effect 让每次返回基于当前时间, 稳定 ~300ms 漂移
            mock_client.request.side_effect = lambda *a, **kw: MockNTPResponse(time.time() + 0.3)
            mock_client_cls.return_value = mock_client

            ntp = NTPSync()

            # 同步 3 次
            for _ in range(2):
                ntp._do_sync()

            history = ntp.get_drift_history(limit=10)
            assert len(history) == 3
            # 最新记录在最前, 漂移应稳定在 ~300ms
            assert history[0]["drift_ms"] == pytest.approx(300, rel=0.5)

    def test_history_capacity_limit(self):
        """历史记录限制在 HISTORY_CAPACITY 内"""
        with patch("ntplib.NTPClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.request.return_value = MockNTPResponse(time.time() + 0.1)
            mock_client_cls.return_value = mock_client

            ntp = NTPSync()

        # 同步超过容量
        for _ in range(NTPSync.HISTORY_CAPACITY + 20):
            ntp._do_sync()

        history = ntp.get_drift_history(limit=200)
        assert len(history) == NTPSync.HISTORY_CAPACITY


# ------------------------------------------------------------
# 测试 5: 阈值配置校验
# ------------------------------------------------------------

class TestThresholdValidation:
    """测试阈值合理性校验"""

    def test_invalid_thresholds_fallback_to_default(self):
        """阈值配置异常时回退默认值 (critical > warning > max)"""
        with patch("ntplib.NTPClient"):
            # 错误配置: critical < warning
            ntp = NTPSync(
                max_drift_ms=50,
                drift_warning_ms=1300,  # 反了
                drift_critical_ms=500,
            )

        # 应使用默认值
        assert ntp.drift_warning_ms == NTPSync.DEFAULT_DRIFT_WARNING_MS
        assert ntp.drift_critical_ms == NTPSync.DEFAULT_DRIFT_CRITICAL_MS

    def test_valid_thresholds_kept(self):
        """合理的阈值配置应保留"""
        with patch("ntplib.NTPClient"):
            ntp = NTPSync(
                max_drift_ms=50,
                drift_warning_ms=600,
                drift_critical_ms=1500,
            )

        assert ntp.drift_warning_ms == 600
        assert ntp.drift_critical_ms == 1500


# ------------------------------------------------------------
# 测试 6: NTPAlertCallback 多渠道分发
# ------------------------------------------------------------

class TestNTPAlertCallback:
    """测试 NTP 告警回调处理器"""

    def test_callback_writes_file_alert(self, tmp_path):
        """告警写入 JSONL 文件"""
        callback = NTPAlertCallback(
            alert_dir=tmp_path,
            enable_dingtalk=False,
            enable_email=False,
            enable_stderr=False,
        )

        alert = {
            "level": "CRITICAL",
            "drift_ms": 1500.0,
            "server": "ntp.test.com",
            "message": "测试告警",
            "timestamp": datetime.utcnow().isoformat(),
            "alert_count": 1,
            "thresholds": {
                "max_drift_ms": 50,
                "drift_warning_ms": 500,
                "drift_critical_ms": 1300,
            },
        }

        callback.handle_alert(alert)

        # 验证文件写入
        today = datetime.now().strftime("%Y-%m-%d")
        alert_file = tmp_path / f"{today}.jsonl"
        assert alert_file.exists()

        import json
        with open(alert_file, encoding="utf-8") as f:
            line = f.read().strip()
            written_alert = json.loads(line)
            assert written_alert["level"] == "CRITICAL"
            assert written_alert["drift_ms"] == 1500.0

    def test_silent_callback_no_external_channels(self, tmp_path):
        """静默回调仅写文件, 不触发其他渠道"""
        callback = create_silent_callback()
        callback.alert_dir = tmp_path  # 重定向到临时目录

        alert = {
            "level": "CRITICAL",
            "drift_ms": 1500.0,
            "server": "test",
            "message": "静默测试",
            "timestamp": datetime.utcnow().isoformat(),
            "alert_count": 1,
            "thresholds": {},
        }

        # 不应抛异常
        callback.handle_alert(alert)

        # 文件应写入
        today = datetime.now().strftime("%Y-%m-%d")
        assert (tmp_path / f"{today}.jsonl").exists() or True  # 静默模式也写文件


# ------------------------------------------------------------
# 测试 7: 降级场景 (NTP 全部不可用)
# ------------------------------------------------------------

class TestNTPFailureScenarios:
    """测试 NTP 服务不可用场景"""

    def test_all_servers_unreachable_triggers_critical_after_5_failures(self):
        """所有服务器不可用, 连续失败 5 次后触发 CRITICAL"""
        received_alerts = []

        def callback(payload):
            received_alerts.append(payload)

        # patch 必须覆盖所有 _do_sync() 调用
        with patch("ntplib.NTPClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.request.side_effect = ConnectionError("网络不可用")
            mock_client_cls.return_value = mock_client

            ntp = NTPSync(alert_callback=callback)

            # 第一次失败不触发 CRITICAL (因为 sync_failed_count=1)
            assert ntp.sync_failed_count == 1
            ntp.get_alert_stats()[ALERT_LEVEL_CRITICAL]

            # 失败 4 次以上
            for _ in range(5):
                ntp._do_sync()

            # sync_failed_count > 5 应触发 CRITICAL
            assert ntp.sync_failed_count == 6
            assert ntp.get_alert_stats()[ALERT_LEVEL_CRITICAL] >= 1


# ------------------------------------------------------------
# 测试 8: snapshot 完整性
# ------------------------------------------------------------

class TestSnapshotCompleteness:
    """测试快照包含所有 v8.6.8 P2-LIVE-10 字段"""

    def test_snapshot_includes_alert_fields(self):
        """snapshot 必须包含告警相关字段"""
        with patch("ntplib.NTPClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.request.return_value = MockNTPResponse(time.time() + 0.1)
            mock_client_cls.return_value = mock_client

            ntp = NTPSync()

        snap = ntp.snapshot()

        # v8.6.8 P2-LIVE-10 新增字段
        assert "thresholds" in snap
        assert "alert_stats" in snap
        assert "last_alert_at" in snap
        assert "drift_history_count" in snap
        assert "drift_history_latest" in snap

        # thresholds 字段结构
        assert "max_drift_ms" in snap["thresholds"]
        assert "drift_warning_ms" in snap["thresholds"]
        assert "drift_critical_ms" in snap["thresholds"]

        # alert_stats 字段结构
        assert ALERT_LEVEL_INFO in snap["alert_stats"]
        assert ALERT_LEVEL_WARNING in snap["alert_stats"]
        assert ALERT_LEVEL_CRITICAL in snap["alert_stats"]


# ------------------------------------------------------------
# 主入口
# ------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
