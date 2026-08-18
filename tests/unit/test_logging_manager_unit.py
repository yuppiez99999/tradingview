"""
单元测试: utils/logging_manager.py
覆盖 ColoredFormatter / StructuredFormatter / QuantSystemLogger / 全局单例函数
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from utils import logging_manager as lm
from utils.logging_manager import (
    ColoredFormatter,
    QuantSystemLogger,
    StructuredFormatter,
    get_logger,
    get_logger_manager,
    setup_logging,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """每个测试前后重置全局单例，避免互相污染"""
    lm._logger_manager = None
    yield
    lm._logger_manager = None


@pytest.fixture(autouse=True)
def _clean_env():
    """清理可能影响测试的环境变量"""
    keys = ["QUANT_LOG_LEVEL", "QUANT_LOG_DIR"]
    saved = {k: os.environ.pop(k, None) for k in keys}
    yield
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


def _make_record(msg="hello", level=logging.INFO, name="test_logger"):
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=42,
        msg=msg,
        args=(),
        exc_info=None,
    )


class TestColoredFormatter:
    def test_format_adds_color(self):
        fmt = ColoredFormatter("%(levelname)s | %(message)s")
        record = _make_record(level=logging.ERROR)
        result = fmt.format(record)
        assert "\033[31m" in result
        assert "ERROR" in result
        assert "\033[0m" in result

    def test_format_info_level(self):
        fmt = ColoredFormatter("%(levelname)s | %(message)s")
        record = _make_record(level=logging.INFO)
        result = fmt.format(record)
        assert "\033[32m" in result

    def test_format_unknown_level_no_color(self):
        fmt = ColoredFormatter("%(levelname)s | %(message)s")
        record = _make_record(level=999)
        record.levelname = "WEIRD"
        result = fmt.format(record)
        assert "WEIRD" in result


class TestStructuredFormatter:
    def test_basic_json_output(self):
        fmt = StructuredFormatter()
        record = _make_record(msg="test message")
        result = fmt.format(record)
        data = json.loads(result)
        assert data["message"] == "test message"
        assert data["level"] == "INFO"
        assert data["logger"] == "test_logger"
        assert data["line"] == 42

    def test_extra_attrs(self):
        fmt = StructuredFormatter()
        record = _make_record(msg="op")
        record.duration_ms = 123.4
        record.operation = "fetch"
        result = fmt.format(record)
        data = json.loads(result)
        assert data["duration_ms"] == 123.4
        assert data["operation"] == "fetch"

    def test_no_extra_attrs(self):
        fmt = StructuredFormatter()
        record = _make_record(msg="plain")
        result = fmt.format(record)
        data = json.loads(result)
        assert "duration_ms" not in data


class TestParseSize:
    def test_plain_number(self):
        mgr = QuantSystemLogger.__new__(QuantSystemLogger)
        assert mgr._parse_size("1024") == 1024

    def test_kb(self):
        mgr = QuantSystemLogger.__new__(QuantSystemLogger)
        assert mgr._parse_size("10KB") == 10 * 1024

    def test_mb(self):
        mgr = QuantSystemLogger.__new__(QuantSystemLogger)
        assert mgr._parse_size("10MB") == 10 * 1024 * 1024

    def test_gb(self):
        mgr = QuantSystemLogger.__new__(QuantSystemLogger)
        assert mgr._parse_size("2GB") == 2 * 1024 * 1024 * 1024

    def test_lowercase(self):
        mgr = QuantSystemLogger.__new__(QuantSystemLogger)
        assert mgr._parse_size("5mb") == 5 * 1024 * 1024


class TestLoadDefaultConfig:
    def test_defaults(self):
        mgr = QuantSystemLogger.__new__(QuantSystemLogger)
        cfg = mgr._load_default_config()
        assert cfg["level"] == "INFO"
        assert cfg["handlers"]["console"]["enabled"] is True
        assert cfg["handlers"]["file"]["enabled"] is True
        assert cfg["handlers"]["structured"]["enabled"] is False

    def test_env_var_level(self):
        with patch.dict(os.environ, {"QUANT_LOG_LEVEL": "DEBUG"}):
            mgr = QuantSystemLogger.__new__(QuantSystemLogger)
            cfg = mgr._load_default_config()
            assert cfg["level"] == "DEBUG"

    def test_env_var_dir(self):
        with patch.dict(os.environ, {"QUANT_LOG_DIR": "/tmp/custom_logs"}):
            mgr = QuantSystemLogger.__new__(QuantSystemLogger)
            cfg = mgr._load_default_config()
            assert cfg["handlers"]["file"]["directory"] == "/tmp/custom_logs"


class TestQuantSystemLoggerInit:
    def test_default_init(self, tmp_path):
        with patch.dict(os.environ, {"QUANT_LOG_DIR": str(tmp_path)}):
            mgr = QuantSystemLogger()
        assert mgr.config["level"] == "INFO"
        root = logging.getLogger()
        assert len(root.handlers) >= 1

    def test_custom_config(self, tmp_path):
        cfg = {
            "level": "DEBUG",
            "format": {
                "console": "%(message)s",
                "file": "%(message)s",
            },
            "handlers": {
                "console": {"enabled": False, "colored": False, "level": "DEBUG"},
                "file": {"enabled": True, "level": "DEBUG", "max_size": "1MB", "backup_count": 2, "directory": str(tmp_path)},
                "structured": {"enabled": False, "level": "INFO", "directory": str(tmp_path)},
            },
            "loggers": {},
        }
        mgr = QuantSystemLogger(config=cfg)
        assert mgr.config["level"] == "DEBUG"
        root = logging.getLogger()
        has_file = any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers)
        assert has_file

    def test_console_disabled(self, tmp_path):
        cfg = {
            "level": "INFO",
            "format": {"console": "%(message)s", "file": "%(message)s"},
            "handlers": {
                "console": {"enabled": False, "colored": False, "level": "INFO"},
                "file": {"enabled": False, "level": "DEBUG", "max_size": "1MB", "backup_count": 2, "directory": str(tmp_path)},
                "structured": {"enabled": False, "level": "INFO", "directory": str(tmp_path)},
            },
            "loggers": {},
        }
        QuantSystemLogger(config=cfg)
        root = logging.getLogger()
        has_stream = any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers)
        assert not has_stream

    def test_structured_enabled(self, tmp_path):
        cfg = {
            "level": "INFO",
            "format": {"console": "%(message)s", "file": "%(message)s"},
            "handlers": {
                "console": {"enabled": False, "colored": False, "level": "INFO"},
                "file": {"enabled": False, "level": "DEBUG", "max_size": "1MB", "backup_count": 2, "directory": str(tmp_path)},
                "structured": {"enabled": True, "level": "INFO", "directory": str(tmp_path)},
            },
            "loggers": {},
        }
        QuantSystemLogger(config=cfg)
        root = logging.getLogger()
        has_structured = any(
            isinstance(h, logging.handlers.RotatingFileHandler) and isinstance(h.formatter, StructuredFormatter)
            for h in root.handlers
        )
        assert has_structured

    def test_file_disabled(self, tmp_path):
        cfg = {
            "level": "INFO",
            "format": {"console": "%(message)s", "file": "%(message)s"},
            "handlers": {
                "console": {"enabled": False, "colored": False, "level": "INFO"},
                "file": {"enabled": False, "level": "DEBUG", "max_size": "1MB", "backup_count": 2, "directory": str(tmp_path)},
                "structured": {"enabled": False, "level": "INFO", "directory": str(tmp_path)},
            },
            "loggers": {},
        }
        QuantSystemLogger(config=cfg)
        root = logging.getLogger()
        has_file = any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers)
        assert not has_file


class TestConfigureSpecificLoggers:
    def test_specific_loggers_configured(self, tmp_path):
        cfg = {
            "level": "INFO",
            "format": {"console": "%(message)s", "file": "%(message)s"},
            "handlers": {
                "console": {"enabled": False, "colored": False, "level": "INFO"},
                "file": {"enabled": False, "level": "DEBUG", "max_size": "1MB", "backup_count": 2, "directory": str(tmp_path)},
                "structured": {"enabled": False, "level": "INFO", "directory": str(tmp_path)},
            },
            "loggers": {
                "quant": {"level": "DEBUG"},
                "urllib3": {"level": "WARNING"},
            },
        }
        QuantSystemLogger(config=cfg)
        assert logging.getLogger("quant").level == logging.DEBUG
        assert logging.getLogger("urllib3").level == logging.WARNING


class TestGetLogger:
    def test_returns_logger(self, tmp_path):
        with patch.dict(os.environ, {"QUANT_LOG_DIR": str(tmp_path)}):
            mgr = QuantSystemLogger()
        lg = mgr.get_logger("my_module")
        assert isinstance(lg, logging.Logger)
        assert lg.name == "my_module"

    def test_caches_logger(self, tmp_path):
        with patch.dict(os.environ, {"QUANT_LOG_DIR": str(tmp_path)}):
            mgr = QuantSystemLogger()
        lg1 = mgr.get_logger("cached")
        lg2 = mgr.get_logger("cached")
        assert lg1 is lg2


class TestGlobalFunctions:
    def test_get_logger_manager_singleton(self, tmp_path):
        with patch.dict(os.environ, {"QUANT_LOG_DIR": str(tmp_path)}):
            m1 = get_logger_manager()
            m2 = get_logger_manager()
        assert m1 is m2

    def test_get_logger_global(self, tmp_path):
        with patch.dict(os.environ, {"QUANT_LOG_DIR": str(tmp_path)}):
            lg = get_logger("global_test")
        assert isinstance(lg, logging.Logger)
        assert lg.name == "global_test"

    def test_setup_logging_returns_manager(self, tmp_path):
        cfg = {
            "level": "WARNING",
            "format": {"console": "%(message)s", "file": "%(message)s"},
            "handlers": {
                "console": {"enabled": True, "colored": False, "level": "WARNING"},
                "file": {"enabled": False, "level": "DEBUG", "max_size": "1MB", "backup_count": 2, "directory": str(tmp_path)},
                "structured": {"enabled": False, "level": "INFO", "directory": str(tmp_path)},
            },
            "loggers": {},
        }
        mgr = setup_logging(cfg)
        assert isinstance(mgr, QuantSystemLogger)
        assert mgr.config["level"] == "WARNING"

    def test_setup_logging_replaces_singleton(self, tmp_path):
        with patch.dict(os.environ, {"QUANT_LOG_DIR": str(tmp_path)}):
            m1 = get_logger_manager()
        cfg = {
            "level": "ERROR",
            "format": {"console": "%(message)s", "file": "%(message)s"},
            "handlers": {
                "console": {"enabled": True, "colored": False, "level": "ERROR"},
                "file": {"enabled": False, "level": "DEBUG", "max_size": "1MB", "backup_count": 2, "directory": str(tmp_path)},
                "structured": {"enabled": False, "level": "INFO", "directory": str(tmp_path)},
            },
            "loggers": {},
        }
        m2 = setup_logging(cfg)
        assert m1 is not m2
        assert get_logger_manager() is m2