# -*- coding: utf-8 -*-
"""烟雾测试运行器 — GAP-1 交付物.

ECC coding-standards 修复:
    自动化烟雾测试, CI 无法快速验证关键模块可导入、关键 API 可调用.

设计原则:
    1. 三类烟雾: 导入 / API / 配置
    2. 每项 < 5s, 总耗时 < 60s (CI 友好)
    3. 不做业务断言 (只验证 "能跑", 不验证 "跑对")
    4. 失败立即 exit 1, 输出明确错误信息

用法:
    python scripts/_smoke_runner.py
    python scripts/_smoke_runner.py --verbose
    python scripts/_smoke_runner.py --category import  # 只跑导入类
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "v8.3_institutional"))
sys.path.insert(0, str(_PROJECT_ROOT / "v8.3_institutional" / "src"))


# ============================================================
# 烟雾测试结果
# ============================================================
class SmokeResult:
    """单个烟雾测试结果."""

    def __init__(self, name: str, category: str):
        self.name = name
        self.category = category
        self.passed = False
        self.duration_ms = 0.0
        self.error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "passed": self.passed,
            "duration_ms": round(self.duration_ms, 1),
            "error": self.error,
        }


def _run_smoke(
    name: str, category: str, func: Callable[[], None]
) -> SmokeResult:
    """执行单个烟雾测试.

    Args:
        name: 测试名
        category: 类别 (import / api / config)
        func: 测试函数 (无参, 无返回; 抛异常即失败)

    Returns:
        SmokeResult
    """
    result = SmokeResult(name=name, category=category)
    start = time.perf_counter()
    try:
        func()
        result.passed = True
    except Exception as e:
        result.passed = False
        result.error = f"{type(e).__name__}: {e}"
        # 完整 traceback 仅在 verbose 模式输出
    result.duration_ms = (time.perf_counter() - start) * 1000
    return result


# ============================================================
# 1. 导入烟雾测试
# ============================================================
def _smoke_import(module_path: str) -> Callable[[], None]:
    """构造一个导入烟雾测试函数."""
    def _test():
        importlib.import_module(module_path)
    return _test


IMPORT_SMOKE_TESTS = [
    # ECC 新增模块 (GAP-6/7/8)
    ("research.lgbm_reproducibility", "import"),
    ("utils.alpha.data_contract", "import"),
    ("utils.alpha.drift_monitor", "import"),
    ("utils.alpha.delayed_label_tracker", "import"),
    # 核心基础设施
    ("utils.config_manager", "import"),
    ("utils.alpha.model_registry", "import"),
    # 配置文件相关
    # (gate_manager 需要 v8.3 路径, 已在 sys.path)
]


# ============================================================
# 2. API 烟雾测试
# ============================================================
def _api_data_contract_validate():
    """API: V9_DEFAULT_CONTRACT.validate 能调用."""
    import pandas as pd

    from utils.alpha.data_contract import V9_DEFAULT_CONTRACT
    empty_panel = pd.DataFrame(columns=["code", "date", "close", "open", "high", "low", "volume", "y"])
    result = V9_DEFAULT_CONTRACT.validate(empty_panel, mode="warn_only")
    assert result is not None, "validate() 应返回 ValidationResult"


def _api_drift_severity():
    """API: DriftSeverity 枚举可用."""
    from utils.alpha.drift_monitor import DriftSeverity
    assert DriftSeverity.LOW.value == "low"
    assert DriftSeverity.CRITICAL.value == "critical"


def _api_compute_psi():
    """API: compute_psi 能计算."""
    import pandas as pd

    from utils.alpha.drift_monitor import compute_psi
    psi = compute_psi(pd.Series([1, 2, 3, 4, 5]), pd.Series([1, 2, 3, 4, 5]))
    assert psi >= 0, "PSI 应非负"


def _api_training_config():
    """API: TrainingConfig 能构造."""
    from research.lgbm_reproducibility import TrainingConfig
    config = TrainingConfig(model_name="smoke_test", seed=42)
    assert config.model_name == "smoke_test"
    assert config.seed == 42


def _api_artifact_name():
    """API: artifact_name 能计算."""
    from research.lgbm_reproducibility import TrainingConfig, artifact_name
    config = TrainingConfig(model_name="smoke_test", seed=42)
    config = config.with_config_hash()
    name = artifact_name(config)
    assert name.startswith("smoke_test"), f"artifact_name 应以 model_name 开头: {name}"


def _api_delayed_label_tracker():
    """API: DelayedLabelTracker 能实例化."""
    from utils.alpha.delayed_label_tracker import DelayedLabelTracker
    tracker = DelayedLabelTracker(model_name="smoke_test", label_delay_days=5)
    assert tracker.model_name == "smoke_test"


API_SMOKE_TESTS = [
    ("V9_DEFAULT_CONTRACT.validate", "api", _api_data_contract_validate),
    ("DriftSeverity 枚举", "api", _api_drift_severity),
    ("compute_psi 计算", "api", _api_compute_psi),
    ("TrainingConfig 构造", "api", _api_training_config),
    ("artifact_name 计算", "api", _api_artifact_name),
    ("DelayedLabelTracker 实例化", "api", _api_delayed_label_tracker),
]


# ============================================================
# 3. 配置烟雾测试
# ============================================================
def _config_load_json(path: str) -> Callable[[], None]:
    def _test():
        full_path = _PROJECT_ROOT / path
        if not full_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {full_path}")
        with open(full_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data is not None, f"配置文件为空: {path}"
    return _test


def _config_load_yaml(path: str) -> Callable[[], None]:
    def _test():
        full_path = _PROJECT_ROOT / path
        if not full_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {full_path}")
        try:
            import yaml
        except ImportError as e:
            raise ImportError(f"yaml 模块未安装: {e}") from e
        with open(full_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert data is not None, f"配置文件为空: {path}"
    return _test


CONFIG_SMOKE_TESTS = [
    # JSON 配置
    ("config/gate_thresholds.json", "config", _config_load_json("config/gate_thresholds.json")),
    ("config/shadow_account_config.json", "config", _config_load_json("config/shadow_account_config.json")),
    ("config/positions.json", "config", _config_load_json("config/positions.json")),
    # YAML 配置
    ("config/alert_owners.yaml", "config", _config_load_yaml("config/alert_owners.yaml")),
    ("v8.3_institutional/config/feature_flags.yaml", "config", _config_load_yaml("v8.3_institutional/config/feature_flags.yaml")),
    ("v8.3_institutional/config/execution.yaml", "config", _config_load_yaml("v8.3_institutional/config/execution.yaml")),
]


# ============================================================
# 主入口
# ============================================================
def main() -> int:
    """主入口.

    Returns:
        0 = 全部通过, 1 = 有失败
    """
    parser = argparse.ArgumentParser(description="GAP-1 烟雾测试运行器")
    parser.add_argument("--verbose", action="store_true", help="输出完整 traceback")
    parser.add_argument(
        "--category",
        choices=["import", "api", "config", "all"],
        default="all",
        help="只跑指定类别 (默认 all)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("SMOKE TEST REPORT (GAP-1)")
    print("=" * 60)

    # 收集要跑的测试
    tests: List[Tuple[str, str, Callable[[], None]]] = []
    if args.category in ("import", "all"):
        for module_path, cat in IMPORT_SMOKE_TESTS:
            tests.append((f"import {module_path}", cat, _smoke_import(module_path)))
    if args.category in ("api", "all"):
        tests.extend(API_SMOKE_TESTS)
    if args.category in ("config", "all"):
        tests.extend(CONFIG_SMOKE_TESTS)

    # 执行
    results: List[SmokeResult] = []
    total_start = time.perf_counter()
    for name, cat, func in tests:
        result = _run_smoke(name, cat, func)
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"  [{status}] {cat:8s} | {name:50s} | {result.duration_ms:6.1f}ms")
        if not result.passed and args.verbose:
            traceback.print_exc()
            print(f"    Error: {result.error}")

    total_ms = (time.perf_counter() - total_start) * 1000

    # 汇总
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    print("-" * 60)
    print(f"  Total: {len(results)} | PASS: {passed} | FAIL: {failed} | Time: {total_ms:.1f}ms")

    if failed > 0:
        print("\n  FAILED ITEMS:")
        for r in results:
            if not r.passed:
                print(f"    [FAIL] {r.category}: {r.name}")
                if r.error:
                    print(f"           {r.error}")
        print("\n" + "=" * 60)
        print("SMOKE TEST FAILED")
        print("=" * 60)
        return 1
    else:
        print("\n" + "=" * 60)
        print("SMOKE TEST PASSED")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
