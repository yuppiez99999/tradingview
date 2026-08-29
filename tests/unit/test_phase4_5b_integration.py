"""验证 run_phase4_5b_shadow_state_sync 集成正确性.

测试 3 个场景:
    1. --skip-shadow=True 时, 阶段4_5B 应跳过
    2. --skip-shadow=False 但 phase4_5 未成功时, 阶段4_5B 应跳过 (前置依赖检查)
    3. --skip-shadow=False 且 phase4_5 成功时, 阶段4_5B 应正常执行
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW_SCRIPT = _PROJECT_ROOT / "15_每日工作流" / "run_daily_eod_workflow.py"

# 用 importlib 加载 (目录名 15_每日工作流 以数字开头, 不能作为 Python 模块名)
_spec = importlib.util.spec_from_file_location(
    "run_daily_eod_workflow", str(_WORKFLOW_SCRIPT)
)
eod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eod)


class MockArgs:
    """模拟 argparse 参数."""

    def __init__(self, skip_shadow: bool = False):
        self.skip_shadow = skip_shadow


def test_skip_shadow_true():
    """测试 1: --skip-shadow=True 时, 阶段4_5B 应跳过."""
    print("=== 测试 1: --skip-shadow=True 时, 阶段4_5B 应跳过 ===")
    args = MockArgs(skip_shadow=True)
    summary = {"phases": {}}
    result = eod.run_phase4_5b_shadow_state_sync("2026-08-04", summary, args)
    phase_result = summary["phases"]["phase4_5b_shadow_state_sync"]
    print(f"  result={result}, phase={phase_result}")
    assert result is False, "skip_shadow=True 应返回 False"
    assert phase_result.get("skipped") is True, "应标记 skipped"
    print("  PASSED")
    print()


def test_phase4_5_not_successful():
    """测试 2: --skip-shadow=False 但 phase4_5 未成功时, 阶段4_5B 应跳过."""
    print("=== 测试 2: phase4_5 未成功时, 阶段4_5B 应跳过 (前置依赖检查) ===")
    args = MockArgs(skip_shadow=False)
    summary = {
        "phases": {"phase4_5_shadow_monitor": {"success": False, "written": False}}
    }
    result = eod.run_phase4_5b_shadow_state_sync("2026-08-04", summary, args)
    phase_result = summary["phases"]["phase4_5b_shadow_state_sync"]
    print(f"  result={result}, phase={phase_result}")
    assert result is False, "phase4_5 未成功应返回 False"
    assert phase_result.get("reason") == "phase4_5_not_successful"
    print("  PASSED")
    print()


def test_phase4_5_successful():
    """测试 3: --skip-shadow=False 且 phase4_5 成功时, 阶段4_5B 应正常执行."""
    print("=== 测试 3: phase4_5 成功时, 阶段4_5B 应正常执行 ===")
    args = MockArgs(skip_shadow=False)
    summary = {
        "phases": {"phase4_5_shadow_monitor": {"success": True, "written": True}}
    }
    result = eod.run_phase4_5b_shadow_state_sync("2026-08-04", summary, args)
    phase_result = summary["phases"]["phase4_5b_shadow_state_sync"]
    print(f"  result={result}, phase={phase_result}")
    assert result is True, "phase4_5 成功时 rebuild 应返回 True"
    assert phase_result.get("w13a_day5_state_sync") is True
    print("  PASSED")
    print()


if __name__ == "__main__":
    test_skip_shadow_true()
    test_phase4_5_not_successful()
    test_phase4_5_successful()
    print("=== 全部测试通过 ===")
