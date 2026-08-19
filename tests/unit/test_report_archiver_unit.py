"""report_archiver 单元测试 — 报告归档全分支覆盖

覆盖:
  - get_archive_dir 显式日期
  - get_archive_dir 默认日期 (今天)
  - archive_report 写入文件 (显式日期)
  - archive_report 写入文件 (默认日期)
  - archive_report 覆盖已存在文件
  - archive_report 创建嵌套目录
  - archive_report 写入 OSError → 记录日志并返回路径
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils import report_archiver as ra  # noqa: E402


class TestGetArchiveDir:
    """get_archive_dir 路径构造"""

    def test_explicit_date(self, tmp_path):
        d = date(2026, 1, 15)
        result = ra.get_archive_dir(str(tmp_path), d)
        assert result == os.path.join(str(tmp_path), "每日报告归档", "2026-01-15")

    def test_none_date_uses_today(self, tmp_path):
        result = ra.get_archive_dir(str(tmp_path))
        today_str = date.today().strftime("%Y-%m-%d")
        assert result == os.path.join(str(tmp_path), "每日报告归档", today_str)

    def test_date_format_zero_padded(self, tmp_path):
        d = date(2026, 1, 5)
        result = ra.get_archive_dir(str(tmp_path), d)
        assert result.endswith(os.path.join("每日报告归档", "2026-01-05"))


class TestArchiveReportWrite:
    """archive_report 文件写入"""

    def test_writes_file_with_explicit_date(self, tmp_path):
        d = date(2026, 3, 20)
        path = ra.archive_report(str(tmp_path), "report.txt", "hello", d)
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            assert f.read() == "hello"
        assert path == os.path.join(
            str(tmp_path), "每日报告归档", "2026-03-20", "report.txt"
        )

    def test_writes_file_with_none_date(self, tmp_path):
        path = ra.archive_report(str(tmp_path), "daily.txt", "content")
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            assert f.read() == "content"
        today_str = date.today().strftime("%Y-%m-%d")
        assert today_str in path

    def test_overwrites_existing_file(self, tmp_path):
        d = date(2026, 5, 1)
        ra.archive_report(str(tmp_path), "r.txt", "old", d)
        path = ra.archive_report(str(tmp_path), "r.txt", "new", d)
        with open(path, encoding="utf-8") as f:
            assert f.read() == "new"

    def test_creates_nested_directory(self, tmp_path):
        d = date(2026, 7, 4)
        path = ra.archive_report(str(tmp_path), "nested.txt", "data", d)
        assert os.path.isdir(os.path.dirname(path))

    def test_unicode_content_written(self, tmp_path):
        d = date(2026, 2, 14)
        path = ra.archive_report(str(tmp_path), "uni.txt", "中文报告内容", d)
        with open(path, encoding="utf-8") as f:
            assert f.read() == "中文报告内容"

    def test_idempotent_directory_creation(self, tmp_path):
        d = date(2026, 6, 6)
        ra.archive_report(str(tmp_path), "a.txt", "1", d)
        path = ra.archive_report(str(tmp_path), "b.txt", "2", d)
        with open(path, encoding="utf-8") as f:
            assert f.read() == "2"


class TestArchiveReportError:
    """archive_report 写入异常分支"""

    def test_write_oserror_returns_path_and_logs(self, tmp_path):
        d = date(2026, 8, 14)
        with patch("builtins.open", side_effect=OSError("boom")):
            path = ra.archive_report(str(tmp_path), "fail.txt", "x", d)
        expected = os.path.join(
            str(tmp_path), "每日报告归档", "2026-08-14", "fail.txt"
        )
        assert path == expected
