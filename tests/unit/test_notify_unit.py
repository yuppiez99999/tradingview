"""test_notify_unit.py — 统一监控告警模块单元测试

覆盖要点:
    - _send_dingtalk (无 URL/有 URL mock)
    - _send_feishu (同上)
    - _log_alert (各级别)
    - send_alert (无配置/有配置/NOTIFY_ENABLED=false)
    - send_sms_alert (向后兼容)
    - send_async_alert (异步线程)
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest


def _reload_notify():
    """重新加载 notify 模块以读取最新环境变量"""
    if "utils.notify" in sys.modules:
        return importlib.reload(sys.modules["utils.notify"])
    return importlib.import_module("utils.notify")


# ============================================================
# _log_alert
# ============================================================


class TestLogAlert:
    @pytest.mark.unit
    def test_log_critical(self, caplog):
        notify = _reload_notify()
        with caplog.at_level("CRITICAL"):
            notify._log_alert("title", "content", level="critical")
        assert any(
            "CRITICAL" in r.message or "title" in r.message for r in caplog.records
        )

    @pytest.mark.unit
    def test_log_warning(self, caplog):
        notify = _reload_notify()
        with caplog.at_level("WARNING"):
            notify._log_alert("title", "content", level="warning")
        assert any("title" in r.message for r in caplog.records)

    @pytest.mark.unit
    def test_log_info(self, caplog):
        notify = _reload_notify()
        with caplog.at_level("INFO"):
            notify._log_alert("title", "content", level="info")
        assert any("title" in r.message for r in caplog.records)

    @pytest.mark.unit
    def test_log_unknown_level_defaults_info(self, caplog):
        notify = _reload_notify()
        with caplog.at_level("INFO"):
            notify._log_alert("title", "content", level="unknown")
        assert any("title" in r.message for r in caplog.records)


# ============================================================
# send_alert (无配置)
# ============================================================


class TestSendAlertNoConfig:
    @pytest.mark.unit
    def test_no_webhook_returns_log_only(self, monkeypatch):
        """无 webhook 配置 → 只返回 {"log": True}"""
        monkeypatch.delenv("DINGTALK_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)
        monkeypatch.setenv("NOTIFY_ENABLED", "true")
        notify = _reload_notify()
        result = notify.send_alert("title", "content")
        assert result == {"log": True}

    @pytest.mark.unit
    def test_disabled_returns_log_only(self, monkeypatch):
        """NOTIFY_ENABLED=false → 只返回 {"log": True}"""
        monkeypatch.setenv("DINGTALK_WEBHOOK_URL", "http://example.com/ding")
        monkeypatch.setenv("NOTIFY_ENABLED", "false")
        notify = _reload_notify()
        result = notify.send_alert("title", "content")
        assert result == {"log": True}


# ============================================================
# send_alert (有配置, mock urllib)
# ============================================================


class TestSendAlertWithConfig:
    @pytest.mark.unit
    def test_dingtalk_success(self, monkeypatch):
        monkeypatch.setenv("DINGTALK_WEBHOOK_URL", "http://example.com/ding")
        monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)
        monkeypatch.setenv("NOTIFY_ENABLED", "true")
        notify = _reload_notify()

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"errcode":0}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = notify.send_alert("title", "content", level="critical")

        assert result["log"] is True
        assert result["dingtalk"] is True

    @pytest.mark.unit
    def test_dingtalk_failure(self, monkeypatch):
        """urlopen 抛异常 → dingtalk=False (fail-open)"""
        monkeypatch.setenv("DINGTALK_WEBHOOK_URL", "http://example.com/ding")
        monkeypatch.setenv("NOTIFY_ENABLED", "true")
        notify = _reload_notify()

        with patch("urllib.request.urlopen", side_effect=OSError("network down")):
            result = notify.send_alert("title", "content")

        assert result["dingtalk"] is False

    @pytest.mark.unit
    def test_feishu_success(self, monkeypatch):
        monkeypatch.delenv("DINGTALK_WEBHOOK_URL", raising=False)
        monkeypatch.setenv("FEISHU_WEBHOOK_URL", "http://example.com/feishu")
        monkeypatch.setenv("NOTIFY_ENABLED", "true")
        notify = _reload_notify()

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"StatusCode":0,"code":0}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = notify.send_alert("title", "content")

        assert result["feishu"] is True

    @pytest.mark.unit
    def test_explicit_channels(self, monkeypatch):
        """显式指定 channels=['dingtalk'] 但无 URL → False"""
        monkeypatch.delenv("DINGTALK_WEBHOOK_URL", raising=False)
        monkeypatch.setenv("NOTIFY_ENABLED", "true")
        notify = _reload_notify()
        result = notify.send_alert("title", "content", channels=["dingtalk"])
        assert result["dingtalk"] is False


# ============================================================
# send_sms_alert
# ============================================================


class TestSendSmsAlert:
    @pytest.mark.unit
    def test_no_config_returns_false(self, monkeypatch):
        """无外部通道 → False"""
        monkeypatch.delenv("DINGTALK_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)
        monkeypatch.setenv("NOTIFY_ENABLED", "true")
        notify = _reload_notify()
        assert notify.send_sms_alert("message") is False

    @pytest.mark.unit
    def test_with_dingtalk_success(self, monkeypatch):
        monkeypatch.setenv("DINGTALK_WEBHOOK_URL", "http://example.com/ding")
        monkeypatch.setenv("NOTIFY_ENABLED", "true")
        notify = _reload_notify()

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"errcode":0}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert notify.send_sms_alert("message") is True


# ============================================================
# send_async_alert
# ============================================================


class TestSendAsyncAlert:
    @pytest.mark.unit
    def test_returns_thread(self, monkeypatch):
        monkeypatch.delenv("DINGTALK_WEBHOOK_URL", raising=False)
        monkeypatch.setenv("NOTIFY_ENABLED", "true")
        notify = _reload_notify()
        t = notify.send_async_alert("title", "content")
        t.join(timeout=2)
        assert t.is_alive() is False  # 已完成
