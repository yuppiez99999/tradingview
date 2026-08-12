"""Smoke tests for Tier-1 type:ignore elimination batch (drift_monitor, daily_panel,
data_quality_monitor, external_data_source, stock_universe)."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

passed = 0
failed = 0

# 1) Import all modules
print("\n=== Smoke Test 1: 模块导入 ===")
modules = {}
for name, path in [
    ("drift", "utils.alpha.drift_monitor"),
    ("panel", "utils.attribution.daily_panel"),
    ("dqm", "utils.data_quality_monitor"),
    ("eds", "utils.external_data_source"),
    ("stock", "utils.universe.stock_universe"),
]:
    try:
        modules[name] = importlib.import_module(path)
        print(f"  [OK]  import {path}")
        passed += 1
    except Exception as e:  # noqa: BLE001
        print(f"  [FAIL] import {path}: {e}")
        failed += 1

# 2) TypedDict / Forward declaration imports
print("\n=== Smoke Test 2: TypedDict & 前向声明 ===")
try:
    from utils.external_data_source import ExternalDataManager  # noqa: F401

    print("  [OK] ExternalDataManager importable")
    passed += 1
except Exception as e:  # noqa: BLE001
    print(f"  [FAIL] ExternalDataSource import: {e}")
    failed += 1

# drift_monitor: SimModeDriftMonitor class-level annotations exist
try:
    Dm = modules["drift"].SimModeDriftMonitor
    # class-level annotations should contain _baseline_predictions etc
    anns = getattr(Dm, "__annotations__", {})
    for attr in ("_baseline_predictions", "_baseline_panel"):
        assert attr in anns, f"Missing class annotation {attr}"
    print("  [OK] SimModeDriftMonitor annotations:", list(anns.keys()))
    passed += 1
except Exception as e:  # noqa: BLE001
    print(f"  [FAIL] SimModeDriftMonitor annotations: {e}")
    failed += 1

# 3) module-level forward-declared optionals
print("\n=== Smoke Test 3: 模块级前向声明 Optional ===")
# drift_monitor._scipy_stats: Optional[type]
try:
    assert hasattr(modules["drift"], "_scipy_stats"), "drift _scipy_stats missing"
    # Either scipy installed (module) or None. It should match Optional[type] pattern.
    val = modules["drift"]._scipy_stats
    assert val is None or hasattr(val, "__name__"), f"_scipy_stats type issue: {type(val)}"
    print(f"  [OK] drift._scipy_stats = {type(val).__name__}")
    passed += 1
except Exception as e:  # noqa: BLE001
    print(f"  [FAIL] _scipy_stats: {e}")
    failed += 1

# daily_panel._FeatureFlags: Optional[type]
try:
    val = modules["panel"]._FeatureFlags
    assert val is None or callable(val), f"_FeatureFlags wrong: {type(val)}"
    print(f"  [OK] daily_panel._FeatureFlags = {val}")
    passed += 1
except Exception as e:  # noqa: BLE001
    print(f"  [FAIL] _FeatureFlags: {e}")
    failed += 1

# data_quality_monitor np/pd Optional
try:
    np_val = modules["dqm"].np
    pd_val = modules["dqm"].pd
    print(f"  [OK] dqm np={np_val is not None}, pd={pd_val is not None}")
    passed += 1
except Exception as e:  # noqa: BLE001
    print(f"  [FAIL] dqm np/pd: {e}")
    failed += 1

# 4) stock_universe _get_akshare() -> Any
print("\n=== Smoke Test 4: stock_universe stub-less akshare 收窄 ===")
try:
    s = modules["stock"]
    fn = s._get_akshare
    hints = (fn.__annotations__ or {}).get("return")
    print(f"  [OK] _get_akshare return hint: {hints}")
    passed += 1
except Exception as e:  # noqa: BLE001
    print(f"  [FAIL] stock_universe: {e}")
    failed += 1

# 5) DataQualityMonitor empty-run (stub objects) — None guards
print("\n=== Smoke Test 5: DataQualityMonitor None guards ===")
try:
    DQM = modules["dqm"].DataQualityMonitor
    report = modules["dqm"].QualityReport()
    monitor = DQM()
    # empty input check
    monitor._check_outliers({"A": {"close": 10.0, "high": 11.0, "low": 9.5}}, report)
    monitor._check_consistency({"A": {"close": 10.0, "high": 11.0, "low": 9.5}}, report)
    print(f"  [OK] DQM None-guard check pass, issues={len(report.issues)}")
    passed += 1
except Exception as e:  # noqa: BLE001
    print(f"  [FAIL] DQM: {e}")
    failed += 1

# 6) daily_panel FeatureFlags methods (None when module missing)
print("\n=== Smoke Test 6: daily_panel flag guard ===")
try:
    P = modules["panel"].DailyAttributionPanel
    p = P()
    f1 = p._is_feature_flag_enabled()
    f2 = p._is_brinson_flag_enabled()
    f3 = p._is_factor_flag_enabled()
    f4 = p._is_tca_flag_enabled()
    f5 = modules["panel"].is_daily_panel_enabled()
    assert isinstance(f1, bool) and isinstance(f2, bool) and isinstance(f3, bool)
    assert isinstance(f4, bool) and isinstance(f5, bool)
    print(f"  [OK] flags: feature={f1} brinson={f2} factor={f3} tca={f4} panel={f5}")
    passed += 1
except Exception as e:  # noqa: BLE001
    print(f"  [FAIL] daily_panel flags: {e}")
    failed += 1

print(f"\n=== Smoke Result: {passed} PASS, {failed} FAIL ===")
sys.exit(0 if failed == 0 else 1)
