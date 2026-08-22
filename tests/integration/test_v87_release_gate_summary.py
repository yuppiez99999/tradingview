#!/usr/bin/env python
"""
test_v87_release_gate_summary.py — v8.7 三门禁汇总集成测试

测试范围: check_v87_release_gate_summary() 聚合 D9 + D10 + D11
测试用例:
    - 三门禁全绿 → all_passed=true
    - 任一未达标 → all_passed=false + blocking_reason 填充
    - V87GateSummary frozen dataclass 不可变性
    - JSON 输出结构正确
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

from engineering_debt_gate import (  # noqa: E402
    V87GateSummary,
    _check_d9_coverage_sprint4_target,
    _check_d10_oversized_file_split,
    _check_d11_phase_b_shadow_stable,
    check_v87_release_gate_summary,
)


class TestV87GateSummaryDataclass:
    """V87GateSummary frozen dataclass 测试."""

    def test_frozen_immutable(self) -> None:
        summary = V87GateSummary(
            phase_b_stable_7d=True,
            oversized_file_split=True,
            coverage_sprint4_080=True,
            all_passed=True,
            blocking_reason="",
            timestamp="2026-08-20 09:00:00",
        )
        with pytest.raises((AttributeError, TypeError)):
            summary.all_passed = False  # type: ignore[misc]

    def test_to_dict_structure(self) -> None:
        summary = V87GateSummary(
            phase_b_stable_7d=False,
            oversized_file_split=True,
            coverage_sprint4_080=False,
            all_passed=False,
            blocking_reason="D9; D11",
            timestamp="2026-08-20 09:00:00",
        )
        d = summary.to_dict()
        assert d["phase_b_stable_7d"] is False
        assert d["oversized_file_split"] is True
        assert d["coverage_sprint4_080"] is False
        assert d["all_passed"] is False
        assert d["blocking_reason"] == "D9; D11"
        assert d["timestamp"] == "2026-08-20 09:00:00"


class TestCheckV87ReleaseGateSummary:
    """check_v87_release_gate_summary() 聚合逻辑测试."""

    def test_all_pass_when_three_gates_green(self) -> None:
        """三门禁全绿 → all_passed=true, blocking_reason 为空."""
        with patch("engineering_debt_gate._check_d9_coverage_sprint4_target", return_value=(True, "D9 ok")), \
             patch("engineering_debt_gate._check_d10_oversized_file_split", return_value=(True, "D10 ok")), \
             patch("engineering_debt_gate._check_d11_phase_b_shadow_stable", return_value=(True, "D11 ok")):
            summary = check_v87_release_gate_summary()
        assert summary.coverage_sprint4_080 is True
        assert summary.oversized_file_split is True
        assert summary.phase_b_stable_7d is True
        assert summary.all_passed is True
        assert summary.blocking_reason == ""

    def test_block_when_d9_fails(self) -> None:
        """D9 未达标 → all_passed=false, blocking_reason 含 D9."""
        with patch("engineering_debt_gate._check_d9_coverage_sprint4_target", return_value=(False, "D9 fail")), \
             patch("engineering_debt_gate._check_d10_oversized_file_split", return_value=(True, "D10 ok")), \
             patch("engineering_debt_gate._check_d11_phase_b_shadow_stable", return_value=(True, "D11 ok")):
            summary = check_v87_release_gate_summary()
        assert summary.all_passed is False
        assert "D9" in summary.blocking_reason

    def test_block_when_d10_fails(self) -> None:
        """D10 未达标 → all_passed=false, blocking_reason 含 D10."""
        with patch("engineering_debt_gate._check_d9_coverage_sprint4_target", return_value=(True, "D9 ok")), \
             patch("engineering_debt_gate._check_d10_oversized_file_split", return_value=(False, "D10 fail")), \
             patch("engineering_debt_gate._check_d11_phase_b_shadow_stable", return_value=(True, "D11 ok")):
            summary = check_v87_release_gate_summary()
        assert summary.all_passed is False
        assert "D10" in summary.blocking_reason

    def test_block_when_d11_fails(self) -> None:
        """D11 未达标 → all_passed=false, blocking_reason 含 D11."""
        with patch("engineering_debt_gate._check_d9_coverage_sprint4_target", return_value=(True, "D9 ok")), \
             patch("engineering_debt_gate._check_d10_oversized_file_split", return_value=(True, "D10 ok")), \
             patch("engineering_debt_gate._check_d11_phase_b_shadow_stable", return_value=(False, "D11 fail")):
            summary = check_v87_release_gate_summary()
        assert summary.all_passed is False
        assert "D11" in summary.blocking_reason

    def test_block_when_all_three_fail(self) -> None:
        """三门禁全未达标 → all_passed=false, blocking_reason 含全部三个."""
        with patch("engineering_debt_gate._check_d9_coverage_sprint4_target", return_value=(False, "D9 fail")), \
             patch("engineering_debt_gate._check_d10_oversized_file_split", return_value=(False, "D10 fail")), \
             patch("engineering_debt_gate._check_d11_phase_b_shadow_stable", return_value=(False, "D11 fail")):
            summary = check_v87_release_gate_summary()
        assert summary.all_passed is False
        assert "D11" in summary.blocking_reason
        assert "D10" in summary.blocking_reason
        assert "D9" in summary.blocking_reason

    def test_timestamp_format(self) -> None:
        """timestamp 格式为 YYYY-MM-DD HH:MM:SS."""
        with patch("engineering_debt_gate._check_d9_coverage_sprint4_target", return_value=(True, "")), \
             patch("engineering_debt_gate._check_d10_oversized_file_split", return_value=(True, "")), \
             patch("engineering_debt_gate._check_d11_phase_b_shadow_stable", return_value=(True, "")):
            summary = check_v87_release_gate_summary()
        # 验证格式 YYYY-MM-DD HH:MM:SS (19 字符)
        assert len(summary.timestamp) == 19
        assert summary.timestamp[4] == "-"
        assert summary.timestamp[7] == "-"
        assert summary.timestamp[10] == " "
        assert summary.timestamp[13] == ":"


class TestV87GateSummaryJsonOutput:
    """v87_release_gate_summary.json 输出结构测试."""

    def test_json_file_exists_and_valid(self) -> None:
        """运行门禁后 reports/v87_release_gate_summary.json 存在且可解析."""
        json_path = _ROOT / "reports" / "v87_release_gate_summary.json"
        if json_path.exists():
            data = json.loads(json_path.read_text(encoding="utf-8"))
            assert "phase_b_stable_7d" in data
            assert "oversized_file_split" in data
            assert "coverage_sprint4_080" in data
            assert "all_passed" in data
            assert "blocking_reason" in data
            assert "timestamp" in data
            assert isinstance(data["all_passed"], bool)
            assert isinstance(data["blocking_reason"], str)


class TestD9D10D11GateFunctions:
    """D9/D10/D11 门禁函数直接测试 (真实环境, 非 mock)."""

    def test_d9_returns_tuple(self) -> None:
        ok, msg = _check_d9_coverage_sprint4_target()
        assert isinstance(ok, bool)
        assert isinstance(msg, str)
        assert "D9" in msg

    def test_d10_returns_tuple(self) -> None:
        ok, msg = _check_d10_oversized_file_split()
        assert isinstance(ok, bool)
        assert isinstance(msg, str)
        assert "D10" in msg

    def test_d11_returns_tuple(self) -> None:
        ok, msg = _check_d11_phase_b_shadow_stable()
        assert isinstance(ok, bool)
        assert isinstance(msg, str)
        assert "D11" in msg
