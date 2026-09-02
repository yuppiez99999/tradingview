"""console_encoding 单元测试 — UTF-8 控制台设置全分支覆盖

覆盖:
  - stdout/stderr reconfigure 成功路径
  - reconfigure 抛 ValueError/TypeError/OSError → TextIOWrapper 兜底
  - reconfigure 属性为 None → TextIOWrapper 兜底
  - 无 buffer 属性 → 跳过兜底
  - TextIOWrapper 兜底失败 → 静默跳过
  - stream 为 None → 跳过
  - Windows 平台 → 执行 chcp 65001
  - 非 Windows 平台 → 不执行 chcp
  - chcp 抛 OSError/SubprocessError → 捕获
  - PYTHONIOENCODING 已存在 → 不覆盖
  - PYTHONIOENCODING 不存在 → 设置为 utf-8
"""

from __future__ import annotations

import io
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils import console_encoding as ce  # noqa: E402


def _mock_stream():
    return MagicMock()


class TestReconfigureSuccess:
    """reconfigure 可调用且成功 → 直接调用 reconfigure, 不走兜底"""

    def test_reconfigure_called_for_both_streams(self, monkeypatch):
        mock_out = _mock_stream()
        mock_err = _mock_stream()
        monkeypatch.setattr(sys, "stdout", mock_out)
        monkeypatch.setattr(sys, "stderr", mock_err)
        monkeypatch.setattr(sys, "platform", "linux")
        ce.setup_utf8_console()
        mock_out.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
        mock_err.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")

    def test_reconfigure_success_skips_textiowrapper_fallback(self, monkeypatch):
        mock_out = _mock_stream()
        mock_out.buffer = _mock_stream()
        monkeypatch.setattr(sys, "stdout", mock_out)
        monkeypatch.setattr(sys, "stderr", _mock_stream())
        monkeypatch.setattr(sys, "platform", "linux")
        ce.setup_utf8_console()
        assert sys.stdout is mock_out


class TestReconfigureFailureFallback:
    """reconfigure 抛异常 → 走 TextIOWrapper 兜底"""

    @pytest.mark.parametrize("exc", [ValueError("b"), TypeError("b"), OSError("b")])
    def test_reconfigure_exception_falls_back_to_textiowrapper(self, exc, monkeypatch):
        real_buffer = io.BytesIO()
        mock_out = _mock_stream()
        mock_out.reconfigure.side_effect = exc
        mock_out.buffer = real_buffer
        monkeypatch.setattr(sys, "stdout", mock_out)
        monkeypatch.setattr(sys, "stderr", _mock_stream())
        monkeypatch.setattr(sys, "platform", "linux")
        ce.setup_utf8_console()
        assert isinstance(sys.stdout, io.TextIOWrapper)
        assert sys.stdout.encoding == "utf-8"


class TestNoReconfigureAttr:
    """reconfigure 为 None → 走 TextIOWrapper 兜底"""

    def test_stream_with_reconfigure_none_uses_buffer(self, monkeypatch):
        stream = types.SimpleNamespace(reconfigure=None, buffer=io.BytesIO())
        monkeypatch.setattr(sys, "stdout", stream)
        monkeypatch.setattr(sys, "stderr", _mock_stream())
        monkeypatch.setattr(sys, "platform", "linux")
        ce.setup_utf8_console()
        assert isinstance(sys.stdout, io.TextIOWrapper)
        assert sys.stdout.encoding == "utf-8"

    def test_stream_without_buffer_skipped(self, monkeypatch):
        stream = types.SimpleNamespace(reconfigure=None)
        monkeypatch.setattr(sys, "stdout", stream)
        monkeypatch.setattr(sys, "stderr", _mock_stream())
        monkeypatch.setattr(sys, "platform", "linux")
        ce.setup_utf8_console()
        assert sys.stdout is stream


class TestTextIOWrapperFallbackFailure:
    """TextIOWrapper 兜底失败 → 静默跳过, sys.stdout 不变"""

    def test_textiowrapper_raises_attribute_error_swallowed(self, monkeypatch):
        mock_out = _mock_stream()
        mock_out.reconfigure.side_effect = ValueError("boom")
        mock_out.buffer = io.BytesIO()
        monkeypatch.setattr(sys, "stdout", mock_out)
        monkeypatch.setattr(sys, "stderr", _mock_stream())
        monkeypatch.setattr(sys, "platform", "linux")
        with patch.object(ce.io, "TextIOWrapper", side_effect=AttributeError("boom")):
            ce.setup_utf8_console()
        assert sys.stdout is mock_out

    def test_textiowrapper_raises_value_error_swallowed(self, monkeypatch):
        mock_out = _mock_stream()
        mock_out.reconfigure.side_effect = ValueError("boom")
        mock_out.buffer = io.BytesIO()
        monkeypatch.setattr(sys, "stdout", mock_out)
        monkeypatch.setattr(sys, "stderr", _mock_stream())
        monkeypatch.setattr(sys, "platform", "linux")
        with patch.object(ce.io, "TextIOWrapper", side_effect=ValueError("boom")):
            ce.setup_utf8_console()
        assert sys.stdout is mock_out


class TestStreamNone:
    """stream 为 None → 跳过该流"""

    def test_stdout_none_skipped(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(sys, "stderr", _mock_stream())
        monkeypatch.setattr(sys, "platform", "linux")
        ce.setup_utf8_console()
        assert sys.stdout is None

    def test_both_streams_none(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(sys, "stderr", None)
        monkeypatch.setattr(sys, "platform", "linux")
        ce.setup_utf8_console()
        assert sys.stdout is None
        assert sys.stderr is None


class TestWindowsChcp:
    """Windows 平台 → 执行 chcp 65001"""

    def test_windows_calls_chcp_65001(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", _mock_stream())
        monkeypatch.setattr(sys, "stderr", _mock_stream())
        monkeypatch.setattr(sys, "platform", "win32")
        with patch("subprocess.run") as mock_run:
            ce.setup_utf8_console()
        # shell=False: 列表参数直接执行, 避免 shell 注入 (S602 修复后的安全行为)
        mock_run.assert_called_once_with(
            ["chcp", "65001"], capture_output=True, shell=False, check=False
        )

    def test_windows_chcp_oserror_swallowed(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", _mock_stream())
        monkeypatch.setattr(sys, "stderr", _mock_stream())
        monkeypatch.setattr(sys, "platform", "win32")
        with patch("subprocess.run", side_effect=OSError("boom")):
            ce.setup_utf8_console()

    def test_windows_chcp_subprocesserror_swallowed(self, monkeypatch):
        import subprocess

        monkeypatch.setattr(sys, "stdout", _mock_stream())
        monkeypatch.setattr(sys, "stderr", _mock_stream())
        monkeypatch.setattr(sys, "platform", "win32")
        with patch("subprocess.run", side_effect=subprocess.SubprocessError("boom")):
            ce.setup_utf8_console()


class TestNonWindowsChcp:
    """非 Windows 平台 → 不执行 chcp"""

    def test_non_windows_no_chcp(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", _mock_stream())
        monkeypatch.setattr(sys, "stderr", _mock_stream())
        monkeypatch.setattr(sys, "platform", "linux")
        with patch("subprocess.run") as mock_run:
            ce.setup_utf8_console()
        mock_run.assert_not_called()

    def test_darwin_no_chcp(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", _mock_stream())
        monkeypatch.setattr(sys, "stderr", _mock_stream())
        monkeypatch.setattr(sys, "platform", "darwin")
        with patch("subprocess.run") as mock_run:
            ce.setup_utf8_console()
        mock_run.assert_not_called()


class TestPythonIoEncoding:
    """PYTHONIOENCODING 环境变量兜底"""

    def test_pythonioencoding_set_when_absent(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", _mock_stream())
        monkeypatch.setattr(sys, "stderr", _mock_stream())
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        ce.setup_utf8_console()
        assert os.environ["PYTHONIOENCODING"] == "utf-8"

    def test_pythonioencoding_preserved_when_present(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", _mock_stream())
        monkeypatch.setattr(sys, "stderr", _mock_stream())
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("PYTHONIOENCODING", "latin-1")
        ce.setup_utf8_console()
        assert os.environ["PYTHONIOENCODING"] == "latin-1"
