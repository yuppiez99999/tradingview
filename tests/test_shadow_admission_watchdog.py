"""Shadow Admission Watchdog 单元测试

验证 watchdog 的核心检测逻辑 (TDD):
1. check_dsr_outcome() 三态 (OK / MISSING / CORRUPT / STALE)
2. query_task_status() schtasks 输出解析
3. is_main_task_running() mtime 检测
4. 状态码翻译 (267011 = 任务从未运行)

不覆盖: 真实 schtasks 集成测试 (需 Windows 任务计划程序环境)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from utils.datetime_utils import now_bj

# 路径设置
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

# 导入被测模块 (TDD RED: 模块尚未实现, 此 import 会失败)
import shadow_admission_watchdog as wd  # noqa: E402


class TestCheckDsrOutcome(unittest.TestCase):
    """测试 DSR 文件结果检测 — outcome-based check 主检测逻辑"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.report_dir = Path(self.tmp) / "shadow"
        self.report_dir.mkdir(parents=True)
        self.today = "2026-07-30"

    def _write_dsr(self, date: str, valid: bool = True):
        path = self.report_dir / f"{date}_dsr.json"
        if valid:
            path.write_text(json.dumps({"date": date, "metrics": {}}), encoding="utf-8")
        else:
            path.write_text("{not valid json", encoding="utf-8")
        return path

    def test_outcome_ok_when_file_exists_valid_json_date_matches(self):
        """文件存在 + JSON 合法 + date 匹配 → OK"""
        self._write_dsr(self.today, valid=True)
        result = wd.check_dsr_outcome(self.report_dir, self.today)
        self.assertEqual(result, "OK")

    def test_outcome_missing_when_file_not_exists(self):
        """文件不存在 → MISSING"""
        result = wd.check_dsr_outcome(self.report_dir, self.today)
        self.assertEqual(result, "MISSING")

    def test_outcome_corrupt_when_json_invalid(self):
        """文件存在但 JSON 损坏 → CORRUPT"""
        self._write_dsr(self.today, valid=False)
        result = wd.check_dsr_outcome(self.report_dir, self.today)
        self.assertEqual(result, "CORRUPT")

    def test_outcome_stale_when_date_mismatch(self):
        """文件存在 + JSON 合法但 date 不匹配 → STALE"""
        path = self.report_dir / f"{self.today}_dsr.json"
        path.write_text(json.dumps({"date": "2026-07-28"}), encoding="utf-8")
        result = wd.check_dsr_outcome(self.report_dir, self.today)
        self.assertEqual(result, "STALE")


class TestQueryTaskStatus(unittest.TestCase):
    """测试 schtasks /Query 输出解析"""

    @patch("shadow_admission_watchdog.subprocess.run")
    def test_parses_last_run_and_result(self, mock_run):
        """解析 Last Run Time / Last Result / Next Run Time 三个字段"""
        mock_run.return_value = MagicMock(
            stdout=(
                "TaskName:                             \\v84_ShadowAdmissionDaily\r\n"
                "Next Run Time:                        2026/7/31 16:15:00\r\n"
                "Status:                               Ready\r\n"
                "Last Run Time:                        2026/7/30 16:15:02\r\n"
                "Last Result:                          0\r\n"
            ),
            returncode=0,
        )
        info = wd.query_task_status("v84_ShadowAdmissionDaily")
        self.assertEqual(info["last_run"], "2026/7/30 16:15:02")
        self.assertEqual(info["last_result"], "0")
        self.assertEqual(info["next_run"], "2026/7/31 16:15:00")

    @patch("shadow_admission_watchdog.subprocess.run")
    def test_handles_never_run_sentinel_267011(self, mock_run):
        """任务从未运行时 Last Result=267011, 应正常解析不崩溃"""
        mock_run.return_value = MagicMock(
            stdout=(
                "Last Run Time:                        1999/11/30 0:00:00\r\n"
                "Last Result:                          267011\r\n"
                "Next Run Time:                        2026/7/31 16:15:00\r\n"
            ),
            returncode=0,
        )
        info = wd.query_task_status("v84_ShadowAdmissionDaily")
        self.assertEqual(info["last_result"], "267011")
        self.assertEqual(info["last_run"], "1999/11/30 0:00:00")

    @patch("shadow_admission_watchdog.subprocess.run")
    def test_returns_error_info_on_exception(self, mock_run):
        """subprocess 抛异常时返回 error 信息而非崩溃"""
        mock_run.side_effect = TimeoutError("timeout")
        info = wd.query_task_status("v84_ShadowAdmissionDaily")
        self.assertIn("error", info["last_result"])


class TestIsMainTaskRunning(unittest.TestCase):
    """测试主任务运行检测 — 基于 admission_state.json mtime"""

    def test_returns_true_when_file_modified_recently(self):
        """admission_state.json 最近 1 分钟内被修改 → True (主任务正在跑)"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{}")
            path = f.name
        try:
            # 文件刚创建, mtime 就是现在, 应判为正在跑
            self.assertTrue(wd.is_main_task_running(Path(path), threshold_seconds=300))
        finally:
            os.unlink(path)

    def test_returns_false_when_file_modified_long_ago(self):
        """admission_state.json 10 分钟前修改 → False (主任务已结束)"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{}")
            path = f.name
        try:
            # 把 mtime 改成 10 分钟前
            old_time = (now_bj() - timedelta(minutes=10)).timestamp()
            os.utime(path, (old_time, old_time))
            self.assertFalse(wd.is_main_task_running(Path(path), threshold_seconds=300))
        finally:
            os.unlink(path)

    def test_returns_false_when_file_not_exists(self):
        """admission_state.json 不存在 → False (不阻塞 watchdog)"""
        self.assertFalse(wd.is_main_task_running(Path("/nonexistent/path/state.json")))


class TestTranslateTaskResult(unittest.TestCase):
    """测试 schtasks 状态码翻译 — 用于告警上下文"""

    def test_translate_never_run(self):
        self.assertEqual(
            wd.translate_task_result("267011"),
            "SCHED_E_TASK_HAS_NOT_RUN (任务从未运行)",
        )

    def test_translate_running(self):
        self.assertEqual(
            wd.translate_task_result("267009"), "SCHED_E_TASK_IS_RUNNING (任务正在运行)"
        )

    def test_translate_success(self):
        self.assertEqual(wd.translate_task_result("0"), "成功")

    def test_translate_unknown(self):
        self.assertEqual(wd.translate_task_result("999"), "未知状态码 999")


if __name__ == "__main__":
    unittest.main()
