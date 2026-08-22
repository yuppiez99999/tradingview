#!/usr/bin/env python
"""
test_coverage_sprint4_gate.py — 覆盖率守卫门禁单元测试

测试范围:
    - check_d9_coverage_sprint4_target() 门禁
    - detect_lookahead_tests() 前视偏差检出
    - detect_mock_inflation() mock 虚增检出
    - detect_stagnation() 停滞检测
    - find_uncovered_p02_branches() 未覆盖分支识别
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

from _detect_coverage_stagnation import detect_stagnation  # noqa: E402
from _detect_lookahead_tests import detect_lookahead_tests  # noqa: E402
from _detect_mock_inflation import detect_mock_inflation  # noqa: E402
from _find_uncovered_p02_branches import find_uncovered_p02_branches  # noqa: E402
from engineering_debt_gate import _check_d9_coverage_sprint4_target  # noqa: E402


class TestCheckD9CoverageSprint4Target:
    """D9 覆盖率 Sprint4 0.80 达标门禁测试."""

    def test_returns_tuple(self) -> None:
        ok, msg = _check_d9_coverage_sprint4_target()
        assert isinstance(ok, bool)
        assert isinstance(msg, str)

    def test_message_contains_d9(self) -> None:
        ok, msg = _check_d9_coverage_sprint4_target()
        assert "D9" in msg

    def test_current_baseline_meets_080(self) -> None:
        """当前基线已达标 0.833 ≥ 0.80, 门禁应通过."""
        ok, msg = _check_d9_coverage_sprint4_target()
        assert ok is True
        assert "0.80" in msg or "达标" in msg


class TestDetectLookaheadTests:
    """前视偏差测试检出器测试."""

    def test_returns_list(self, tmp_path: Path) -> None:
        violations = detect_lookahead_tests(tmp_path)
        assert isinstance(violations, list)

    def test_detects_shift_negative(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test_bad_lookahead.py"
        test_file.write_text(
            "import pandas as pd\n"
            "def test_bad():\n"
            "    df = pd.DataFrame({'a': [1, 2, 3]})\n"
            "    future = df.shift(-1)\n"
            "    assert future.iloc[0, 0] == 2\n",
            encoding="utf-8",
        )
        violations = detect_lookahead_tests(tmp_path)
        assert len(violations) > 0
        assert "test_bad_lookahead.py" in violations[0]

    def test_exempt_honest_validation_file(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test_g7_backtest_honest_validation_boost.py"
        test_file.write_text(
            "def test_hv():\n"
            "    future = df.shift(-1)\n",
            encoding="utf-8",
        )
        violations = detect_lookahead_tests(tmp_path)
        assert all("honest_validation" not in v for v in violations)

    def test_no_violations_in_clean_test(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test_clean.py"
        test_file.write_text(
            "def test_clean():\n"
            "    assert 1 + 1 == 2\n",
            encoding="utf-8",
        )
        violations = detect_lookahead_tests(tmp_path)
        assert len(violations) == 0


class TestDetectMockInflation:
    """mock 虚增覆盖率检出器测试."""

    def test_returns_list(self, tmp_path: Path) -> None:
        violations = detect_mock_inflation(tmp_path)
        assert isinstance(violations, list)

    def test_detects_core_link_mock(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test_mock_core.py"
        test_file.write_text(
            "from unittest.mock import patch\n"
            "def test_mocked():\n"
            "    with patch('utils.risk.pretrade_guard.PreTradeGuard.check'):\n"
            "        pass\n",
            encoding="utf-8",
        )
        violations = detect_mock_inflation(tmp_path)
        assert len(violations) > 0

    def test_ignores_io_mock(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test_mock_io.py"
        test_file.write_text(
            "from unittest.mock import patch\n"
            "def test_mocked_io():\n"
            "    with patch('requests.get'):\n"
            "        pass\n",
            encoding="utf-8",
        )
        violations = detect_mock_inflation(tmp_path)
        assert len(violations) == 0

    def test_no_mock_no_violation(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test_no_mock.py"
        test_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        violations = detect_mock_inflation(tmp_path)
        assert len(violations) == 0


class TestDetectStagnation:
    """覆盖率提升停滞检测器测试."""

    def test_no_stagnation_with_insufficient_data(self) -> None:
        assert detect_stagnation([0.68, 0.69], threshold=0.01, window=3) is False

    def test_no_stagnation_with_improving(self) -> None:
        series = [0.68, 0.70, 0.75, 0.80]
        assert detect_stagnation(series, threshold=0.01, window=3) is False

    def test_stagnation_when_plateau(self) -> None:
        series = [0.68, 0.70, 0.701, 0.702, 0.703]
        assert detect_stagnation(series, threshold=0.01, window=3) is True

    def test_stagnation_at_exact_threshold(self) -> None:
        series = [0.68, 0.69, 0.69, 0.69, 0.69]
        assert detect_stagnation(series, threshold=0.01, window=3) is True


class TestFindUncoveredP02Branches:
    """P0-P2 未覆盖分支识别器测试."""

    def test_returns_list(self, tmp_path: Path) -> None:
        cov_xml = tmp_path / "coverage.xml"
        cov_xml.write_text('<?xml version="1.0"?><coverage></coverage>', encoding="utf-8")
        branches = find_uncovered_p02_branches(cov_xml)
        assert isinstance(branches, list)

    def test_empty_when_no_file(self, tmp_path: Path) -> None:
        branches = find_uncovered_p02_branches(tmp_path / "nonexistent.xml")
        assert branches == []

    def test_parses_uncovered_branches(self, tmp_path: Path) -> None:
        cov_xml = tmp_path / "coverage.xml"
        cov_xml.write_text(
            '<?xml version="1.0"?><coverage>'
            '<package><class filename="utils/risk/guard.py">'
            '<line number="10" branch="true" missing-branches="1"/>'
            '<line number="20" branch="true" missing-branches="0"/>'
            '</class></package>'
            '</coverage>',
            encoding="utf-8",
        )
        branches = find_uncovered_p02_branches(cov_xml)
        assert len(branches) == 1
        assert branches[0].priority == "P0"
        assert branches[0].line_start == 10

    def test_excludes_p3_modules(self, tmp_path: Path) -> None:
        cov_xml = tmp_path / "coverage.xml"
        cov_xml.write_text(
            '<?xml version="1.0"?><coverage>'
            '<package><class filename="docs/helper.py">'
            '<line number="5" branch="true" missing-branches="1"/>'
            '</class></package>'
            '</coverage>',
            encoding="utf-8",
        )
        branches = find_uncovered_p02_branches(cov_xml)
        assert len(branches) == 0
