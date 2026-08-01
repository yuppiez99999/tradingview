# -*- coding: utf-8 -*-
r"""v8.6.11 安全修复验证脚本

验证目标:
  - GitHub Dependabot 5 项告警全部修复
  - Python 3.14 生产环境关键模块仍可正常 import
  - 项目核心模块 (utils / v8.3_institutional) 加载无错

运行环境: Python 3.14.4 (junction C:\QuantSys 生产基线)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Tuple


def _check_version(name: str, expected_min: str, actual: str) -> bool:
    """简单版本比较: 仅按主.次.微 三段比较"""
    try:
        exp_parts = [int(x) for x in expected_min.split(".")]
        act_parts = [int(x) for x in actual.split(".")]
        # 补齐到同长度
        while len(exp_parts) < len(act_parts):
            exp_parts.append(0)
        while len(act_parts) < len(exp_parts):
            act_parts.append(0)
        return act_parts >= exp_parts
    except Exception:
        return False


def test_dependabot_alerts_fixed() -> None:
    """验证 5 项 Dependabot 告警对应包已升级到安全版本"""
    print("\n[1/3] 验证 Dependabot 5 项告警修复...")

    cases: List[Tuple[str, str, str, str, str]] = [
        # (包名, import名, 最低安全版本, 当前版本来源, 告警等级)
        ("lightgbm", "lightgbm", "4.6.0", "4.6.0", "HIGH (RCE)"),
        ("scikit-learn", "sklearn", "1.5.0", "1.9.0", "medium (敏感数据泄露)"),
        ("requests", "requests", "2.33.0", "2.33.0", "medium (3 项告警合并)"),
    ]

    all_pass = True
    for pkg, import_name, min_ver, _expected_actual, severity in cases:
        try:
            mod = __import__(import_name)
            actual = mod.__version__
        except Exception as e:
            print(f"  [FAIL] {pkg}: import 失败 ({e})")
            all_pass = False
            continue

        ok = _check_version(pkg, min_ver, actual)
        status = "[OK]  " if ok else "[FAIL]"
        print(
            f"  {status} {pkg:14s} 当前={actual:8s}  ≥{min_ver:8s}  "
            f"({severity})"
        )
        if not ok:
            all_pass = False

    assert all_pass, "Dependabot 告警修复验证未通过"


def test_python_version() -> None:
    """验证 Python 版本 >= 3.9 (scikit-learn 1.5+ 要求)"""
    print("\n[2/3] 验证 Python 版本兼容性...")
    v = sys.version_info
    print(f"  Python: {v.major}.{v.minor}.{v.micro}")
    assert (v.major, v.minor) >= (3, 9), (
        f"scikit-learn 1.5+ 要求 Python 3.9+, 当前 {v.major}.{v.minor}"
    )
    print("  [OK]  Python 版本满足 scikit-learn 1.5+ 要求")


def test_project_core_modules_import() -> None:
    """验证项目核心模块加载无错"""
    print("\n[3/3] 验证项目核心模块 import...")

    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))
    sys.path.insert(0, str(project_root / "utils"))
    sys.path.insert(0, str(project_root / "v8.3_institutional"))

    modules = [
        "utils.signal_fusion",
        "utils.kill_switch",
        "utils.risk_guard_integrator",
        "utils.vol_target_controller",
        "utils.finance_agent_orchestrator",
        "utils.akshare_data_source",
        "utils.tdx_data_source",
    ]

    failed: List[str] = []
    for mod in modules:
        try:
            __import__(mod)
            print(f"  [OK]  {mod}")
        except Exception as e:
            print(f"  [FAIL] {mod}: {type(e).__name__}: {e}")
            failed.append(mod)

    # 允许 finance_agent_orchestrator 因 LLM 依赖缺失而失败, 其他必须通过
    hard_failures = [m for m in failed if "finance_agent" not in m]
    assert not hard_failures, f"核心模块加载失败: {hard_failures}"


def main() -> int:
    print("=" * 60)
    print("v8.6.11 Dependabot 安全修复验证")
    print(f"Python: {sys.version.split()[0]}")
    print("=" * 60)

    try:
        test_dependabot_alerts_fixed()
        test_python_version()
        test_project_core_modules_import()
    except AssertionError as e:
        print(f"\n[FAILED] {e}")
        return 1
    except Exception as e:
        print(f"\n[ERROR] {type(e).__name__}: {e}")
        return 2

    print("\n" + "=" * 60)
    print("[SUCCESS] v8.6.11 安全修复验证全部通过")
    print("  - GitHub Dependabot 5 项告警对应依赖均已升级")
    print("  - Python 3.14 生产环境核心模块加载正常")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
