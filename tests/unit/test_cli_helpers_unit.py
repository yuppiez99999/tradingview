"""
单元测试: utils/cli_helpers.py
覆盖 write_report_file / archive_report / get_stock_name / log_execution_summary / get_ml_signal_section / get_etf_flow_data / get_portfolio_quotes / get_archive_dir
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from utils.cli_helpers import (
    archive_report,
    get_archive_dir,
    get_etf_flow_data,
    get_ml_signal_section,
    get_portfolio_quotes,
    get_stock_name,
    log_execution_summary,
    write_report_file,
)


class TestWriteReportFile:
    def test_basic_write(self, tmp_path):
        with patch("utils.cli_helpers._BASE_DIR", tmp_path):
            result = write_report_file("test content", "report.txt")
        assert Path(result).exists()
        assert Path(result).read_text(encoding="utf-8") == "test content"

    def test_custom_subdir(self, tmp_path):
        with patch("utils.cli_helpers._BASE_DIR", tmp_path):
            result = write_report_file("data", "f.json", subdir="custom")
        assert "custom" in result
        assert Path(result).exists()

    def test_overwrite_existing(self, tmp_path):
        with patch("utils.cli_helpers._BASE_DIR", tmp_path):
            write_report_file("old", "r.txt")
            result = write_report_file("new", "r.txt")
        assert Path(result).read_text(encoding="utf-8") == "new"


class TestArchiveReport:
    def test_archive_existing(self, tmp_path):
        with patch("utils.cli_helpers._BASE_DIR", tmp_path):
            src = write_report_file("content", "to_archive.txt")
            archived = archive_report(src)
        assert Path(archived).exists()
        assert Path(archived).read_text(encoding="utf-8") == "content"
        assert "archive" in archived

    def test_archive_nonexistent(self):
        result = archive_report("/nonexistent/path/file.txt")
        assert result == "/nonexistent/path/file.txt"

    def test_custom_archive_subdir(self, tmp_path):
        with patch("utils.cli_helpers._BASE_DIR", tmp_path):
            src = write_report_file("content", "f.txt")
            archived = archive_report(src, archive_subdir="old")
        assert Path(archived).exists()
        assert "old" in archived


class TestGetStockName:
    def test_no_positions_file(self, tmp_path):
        with patch("utils.cli_helpers._BASE_DIR", tmp_path):
            assert get_stock_name("600519") == "600519"

    def test_with_positions_file(self, tmp_path):
        import json

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "positions.json").write_text(
            json.dumps({"positions": {"600519": {"name": "贵州茅台"}}}),
            encoding="utf-8",
        )
        with patch("utils.cli_helpers._BASE_DIR", tmp_path):
            assert get_stock_name("600519") == "贵州茅台"

    def test_code_not_in_positions(self, tmp_path):
        import json

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "positions.json").write_text(
            json.dumps({"positions": {"000001": {"name": "平安银行"}}}),
            encoding="utf-8",
        )
        with patch("utils.cli_helpers._BASE_DIR", tmp_path):
            assert get_stock_name("600519") == "600519"

    def test_invalid_json(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "positions.json").write_text("{bad json", encoding="utf-8")
        with patch("utils.cli_helpers._BASE_DIR", tmp_path):
            assert get_stock_name("600519") == "600519"


class TestLogExecutionSummary:
    def test_basic_output(self, capsys):
        log_execution_summary("test_mode", {"key1": "val1", "key2": 42})
        captured = capsys.readouterr()
        assert "test_mode" in captured.out
        assert "key1: val1" in captured.out
        assert "key2: 42" in captured.out

    def test_empty_dict(self, capsys):
        log_execution_summary("empty", {})
        captured = capsys.readouterr()
        assert "empty" in captured.out


class TestGetMlSignalSection:
    def test_default_returns_empty(self):
        assert get_ml_signal_section() == ""

    def test_with_code(self):
        assert get_ml_signal_section("600519") == ""

    def test_return_raw(self):
        assert get_ml_signal_section(return_raw=True) is None

    def test_return_raw_with_code(self):
        assert get_ml_signal_section("600519", return_raw=True) is None


class TestGetEtfFlowData:
    def test_returns_empty_dict(self):
        assert get_etf_flow_data() == {}


class TestGetPortfolioQuotes:
    def test_returns_empty_dict(self):
        assert get_portfolio_quotes() == {}


class TestGetArchiveDir:
    def test_returns_path(self, tmp_path):
        with patch("utils.cli_helpers._BASE_DIR", tmp_path):
            result = get_archive_dir()
        assert isinstance(result, Path)
        assert result.exists()
        assert "archive" in str(result)
