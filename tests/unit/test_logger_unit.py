"""test_logger_unit.py — 日志工具单元测试

覆盖要点:
    - RelativePathFormatter (相对路径转换/ValueError 回退)
    - Logger (构造/各级别方法/handler 设置)
    - _resolve_log_level (None/有效/无效)
    - get_logger (返回 Logger 实例)
    - _apply_litellm_log_level (环境变量)
"""

from __future__ import annotations

import logging
import os

import pytest

from utils.logger import (
    Logger,
    RelativePathFormatter,
    _resolve_log_level,
    get_logger,
)

# ============================================================
# RelativePathFormatter
# ============================================================


class TestRelativePathFormatter:
    @pytest.mark.unit
    def test_relative_path(self, tmp_path):
        fmt = RelativePathFormatter("%(pathname)s", relative_to=tmp_path)
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=str(tmp_path / "sub" / "file.py"),
            lineno=10,
            msg="test",
            args=(),
            exc_info=None,
        )
        result = fmt.format(record)
        assert result == "sub" + os.sep + "file.py" or result == "sub/file.py"

    @pytest.mark.unit
    def test_value_error_fallback(self, tmp_path):
        """路径不在 relative_to 下 → 保留绝对路径"""
        fmt = RelativePathFormatter("%(pathname)s", relative_to=tmp_path)
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/other/path/file.py",
            lineno=10,
            msg="test",
            args=(),
            exc_info=None,
        )
        result = fmt.format(record)
        assert "file.py" in result


# ============================================================
# Logger
# ============================================================


class TestLogger:
    @pytest.mark.unit
    def test_init(self, tmp_path):
        log_file = str(tmp_path / "test.log")
        logger = Logger(
            "test_logger", level="DEBUG", log_file=log_file, console_output=False
        )
        assert logger.name == "test_logger"
        assert logger.level == logging.DEBUG

    @pytest.mark.unit
    def test_log_methods(self, tmp_path):
        log_file = str(tmp_path / "test.log")
        logger = Logger(
            "test_methods", level="DEBUG", log_file=log_file, console_output=False
        )
        logger.debug("debug msg")
        logger.info("info msg")
        logger.warning("warning msg")
        logger.error("error msg")
        logger.critical("critical msg")
        # 不抛异常即通过

    @pytest.mark.unit
    def test_invalid_level_defaults_info(self, tmp_path):
        logger = Logger(
            "test_invalid", level="INVALID", log_file=None, console_output=False
        )
        assert logger.level == logging.INFO

    @pytest.mark.unit
    def test_no_handlers_duplicate(self, tmp_path):
        """同名 Logger 不重复添加 handler"""
        log_file = str(tmp_path / "dup.log")
        l1 = Logger("dup_logger", level="INFO", log_file=log_file, console_output=False)
        initial_handlers = len(l1.logger.handlers)
        l2 = Logger("dup_logger", level="INFO", log_file=log_file, console_output=False)
        assert len(l2.logger.handlers) == initial_handlers


# ============================================================
# _resolve_log_level
# ============================================================


class TestResolveLogLevel:
    @pytest.mark.unit
    def test_none(self):
        assert _resolve_log_level(None) == logging.INFO

    @pytest.mark.unit
    def test_valid(self):
        assert _resolve_log_level("DEBUG") == logging.DEBUG
        assert _resolve_log_level("WARNING") == logging.WARNING
        assert _resolve_log_level("ERROR") == logging.ERROR

    @pytest.mark.unit
    def test_invalid(self):
        assert _resolve_log_level("INVALID") == logging.INFO

    @pytest.mark.unit
    def test_lowercase(self):
        assert _resolve_log_level("debug") == logging.DEBUG

    @pytest.mark.unit
    def test_custom_default(self):
        assert _resolve_log_level(None, default=logging.WARNING) == logging.WARNING


# ============================================================
# get_logger
# ============================================================


class TestGetLogger:
    @pytest.mark.unit
    def test_returns_logger(self, tmp_path):
        logger = get_logger("test_get_logger", log_dir=str(tmp_path))
        assert isinstance(logger, Logger)
        assert logger.name == "test_get_logger"

    @pytest.mark.unit
    def test_creates_log_dir(self, tmp_path):
        log_dir = str(tmp_path / "sub" / "logs")
        get_logger("test_mkdir", log_dir=log_dir)
        assert os.path.exists(log_dir)


# ============================================================
# _apply_litellm_log_level
# ============================================================


class TestLitellmLogLevel:
    @pytest.mark.unit
    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("LITELLM_LOG_LEVEL", "DEBUG")
        from utils.logger import _apply_litellm_log_level

        _apply_litellm_log_level()
        assert logging.getLogger("LiteLLM").level == logging.DEBUG

    @pytest.mark.unit
    def test_default_warning(self, monkeypatch):
        monkeypatch.delenv("LITELLM_LOG_LEVEL", raising=False)
        from utils.logger import _apply_litellm_log_level

        _apply_litellm_log_level()
        assert logging.getLogger("LiteLLM").level == logging.WARNING


# ============================================================
# _apply_quiet_loggers
# ============================================================


class TestQuietLoggers:
    @pytest.mark.unit
    def test_quiet_loggers_set_to_warning(self):
        from utils.logger import _apply_quiet_loggers

        _apply_quiet_loggers()
        assert logging.getLogger("urllib3").level == logging.WARNING
        assert logging.getLogger("httpx").level == logging.WARNING


# ============================================================
# _init_root_logging
# ============================================================


class TestInitRootLogging:
    @pytest.mark.unit
    def test_creates_log_files(self, tmp_path):
        from utils.logger import _init_root_logging

        _init_root_logging(log_prefix="test_init", log_dir=str(tmp_path))
        # 应创建 .log 文件 (info + debug)
        log_files = list(tmp_path.glob("*.log"))
        assert len(log_files) >= 2

    @pytest.mark.unit
    def test_root_logger_has_handlers(self, tmp_path):
        from utils.logger import _init_root_logging

        _init_root_logging(log_dir=str(tmp_path))
        root = logging.getLogger()
        assert len(root.handlers) >= 3  # console + info + debug


# ============================================================
# setup_loggers
# ============================================================


class TestSetupLoggers:
    @pytest.mark.unit
    def test_returns_system_and_modules(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from utils.logger import setup_loggers

        result = setup_loggers()
        assert "system" in result
        assert "modules" in result
        assert isinstance(result["system"], Logger)
        assert isinstance(result["modules"], dict)


# ============================================================
# Logger makedirs (line 101)
# ============================================================


class TestLoggerMakedirs:
    @pytest.mark.unit
    def test_creates_nested_log_dir(self, tmp_path):
        """log_file 在不存在的嵌套目录中 → 自动创建"""
        log_file = str(tmp_path / "nested" / "deep" / "test.log")
        Logger(
            "test_mkdir_nested", level="INFO", log_file=log_file, console_output=False
        )
        assert os.path.exists(tmp_path / "nested" / "deep")
