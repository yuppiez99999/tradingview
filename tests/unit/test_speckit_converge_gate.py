"""`scripts/speckit_converge_gate.py` 单测（spec-kit 集成 §9.2, 2026-09-11）。

覆盖:
    1. 负向自证（真实子进程, CI 同格式）: 缺 --pytest-args → exit != 0（fail-closed, 拒绝未声明测试范围的收敛）
    2. 门禁顺序与短路: G1 失败后 G2~G4 不再执行
    3. 全绿路径: main 返回 0
    4. 命令构造: G1 只透传 .py 文件; G2 用 shlex 切分 --pytest-args
    5. 失败时输出含 FAIL 标记与尾部摘录提示（不吞门禁输出）
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import List
from unittest import mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "speckit_converge_gate.py"


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("speckit_converge_gate", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def gate() -> ModuleType:
    return _load_gate()


class FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


class TestBuildGates:
    def test_order_is_g1_to_g4(self, gate: ModuleType) -> None:
        gates = gate.build_gates(["a.py", "b.py"], "tests/unit/test_x.py -q")
        assert [g.name for g in gates] == [
            "G1-ruff-incremental",
            "G2-pytest",
            "G3-industrial-grade",
            "G4-data-validity",
        ]

    def test_g1_filters_non_py_files(self, gate: ModuleType) -> None:
        gates = gate.build_gates(["a.py", "b.md"], "tests/unit/test_x.py")
        g1_cmd = gates[0].cmd
        assert any(a.endswith("a.py") for a in g1_cmd)
        assert not any(a.endswith("b.md") for a in g1_cmd)

    def test_g2_splits_pytest_args_with_shlex(self, gate: ModuleType) -> None:
        gates = gate.build_gates([], 'tests/unit -k "test foo" -q')
        assert gates[1].cmd[-4:] == ["tests/unit", "-k", "test foo", "-q"]

    def test_gates_reference_repo_scripts(self, gate: ModuleType) -> None:
        gates = gate.build_gates([], "tests/unit")
        joined = " ".join(" ".join(g.cmd) for g in gates)
        assert "ruff_incremental_gate.py" in joined
        assert "industrial_grade_check.py" in joined
        assert "assert_data_validity.py" in joined


class TestMainOrchestration:
    def test_all_pass_returns_zero(self, gate: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
        with mock.patch.object(gate, "_run_gate", return_value=FakeCompleted(0, "ok")):
            rc = gate.main(["--files", "a.py", "--pytest-args", "tests/unit/test_x.py"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "ALL PASS" in out

    def test_first_failure_short_circuits(self, gate: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
        calls: List[str] = []

        def fake_run(g: gate.GateSpec) -> FakeCompleted:  # type: ignore[name-defined]
            calls.append(g.name)
            if g.name == "G1-ruff-incremental":
                return FakeCompleted(1, "[G-1] ruff 增量门禁失败:\n  [BLOCKING] a.py: 1")
            return FakeCompleted(0, "ok")

        with mock.patch.object(gate, "_run_gate", side_effect=fake_run):
            rc = gate.main(["--files", "a.py", "--pytest-args", "tests/unit/test_x.py"])
        assert rc == 1
        assert calls == ["G1-ruff-incremental"]  # 短路: 后续门禁不再执行
        out = capsys.readouterr().out
        assert "FAIL G1-ruff-incremental" in out
        assert "implementation-notes.md" in out  # 不吞输出: 提示摘录

    def test_mid_failure_reports_tail(self, gate: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
        def fake_run(g: gate.GateSpec) -> FakeCompleted:  # type: ignore[name-defined]
            if g.name == "G2-pytest":
                return FakeCompleted(1, "line1\nFAILED tests/unit/test_x.py::test_y\nshort test summary")
            return FakeCompleted(0, "ok")

        with mock.patch.object(gate, "_run_gate", side_effect=fake_run):
            rc = gate.main(["--pytest-args", "tests/unit/test_x.py"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "FAIL G2-pytest" in out
        assert "FAILED tests/unit/test_x.py::test_y" in out


class TestFailClosedCli:
    """负向自证（真实子进程）: 门禁类变更须附 CI 同格式负向验证 —— 修复前会失败。"""

    def test_missing_pytest_args_is_rejected(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--files", "a.py"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        assert proc.returncode != 0  # fail-closed: 未声明测试范围不得收敛
        assert "pytest-args" in (proc.stdout + proc.stderr)

    def test_gate_script_exists(self) -> None:
        assert SCRIPT.exists()
