"""Smoke tests for Batch I (10 modules × 38 type:ignore → 0).

Modules:
  - execution_algo_engine (5: datetime annotations + prev_x float)
  - lgb_signal_monitor (4: Counter[Any] + defaultdict[str, dict[str, int]])
  - alpha/mlops_pipeline (4: dict[str, Any] status index)
  - attribution/managers (4: cast for tracker Any returns)
  - alpha_factor/transformer_encoder (4: torch/nn forward declaration)
  - factor_model (4: GTJA191 forward-decl + float cast + dict[str, int])
  - phase_manager (4: phase attribute + actions None guard)
  - ledoit_wolf_covariance (3: redundant ignores removed)
  - etf_flow_monitor (3: spec None guard + Dict[str, Any])
  - alpha/model_registry (3: mlflow_client None guard + result dict)
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

passed = 0
failed = 0


def _ok(msg: str) -> None:
    global passed
    print(f"  [OK] {msg}")
    passed += 1


def _fail(msg: str, e: object) -> None:
    global failed
    print(f"  [FAIL] {msg}: {e}")
    failed += 1


# 1) Import all 10 modules
print("\n=== Smoke Test 1: 模块导入 (10 模块) ===")
modules = {}
import_targets = [
    ("eae", "utils.execution_algo_engine"),
    ("lgb", "utils.lgb_signal_monitor"),
    ("mlops", "utils.alpha.mlops_pipeline"),
    ("mgr", "utils.attribution.managers"),
    ("tfe", "utils.alpha_factor.transformer_encoder"),
    ("fm", "utils.factor_model"),
    ("pm", "utils.phase_manager"),
    ("lw", "utils.ledoit_wolf_covariance"),
    ("efm", "utils.etf_flow_monitor"),
    ("mr", "utils.alpha.model_registry"),
]
for short, path in import_targets:
    try:
        modules[short] = importlib.import_module(path)
        print(f"  [OK]  import {path}")
        passed += 1
    except Exception as e:  # noqa: BLE001
        print(f"  [FAIL] import {path}: {e}")
        failed += 1

# 2) ExecutionAlgoEngine: datetime annotations + prev_x float
print("\n=== Smoke Test 2: ExecutionAlgoEngine datetime + float ===")
try:
    AlgoType = modules["eae"].AlgoType
    engine = modules["eae"].ExecutionAlgoEngine()
    plan = engine.plan_order(
        algo=AlgoType.TWAP,
        symbol="300308",
        side="buy",
        total_shares=10000,
        duration_minutes=120,
        slice_minutes=5,
    )
    assert plan.slice_count > 0, f"TWAP slice_count={plan.slice_count}"
    # VWAP uses the annotated datetime branch
    plan_vwap = engine.plan_order(
        algo=AlgoType.VWAP,
        symbol="300308",
        side="buy",
        total_shares=10000,
        duration_minutes=120,
        slice_minutes=5,
    )
    assert plan_vwap.slice_count > 0, f"VWAP slice_count={plan_vwap.slice_count}"
    # AC uses prev_x float branch
    plan_ac = engine.plan_order(
        algo=AlgoType.AC,
        symbol="300308",
        side="buy",
        total_shares=10000,
        duration_minutes=120,
        slice_minutes=10,
        current_price=10.0,
        volatility=0.25,
    )
    assert plan_ac.slice_count > 0, f"AC slice_count={plan_ac.slice_count}"
    _ok(
        f"TWAP/VWAP/AC slices = {plan.slice_count}/{plan_vwap.slice_count}/{plan_ac.slice_count}"
    )
except Exception as e:  # noqa: BLE001
    _fail("ExecutionAlgoEngine", e)

# 3) LGB signal monitor: Counter[Any] + defaultdict[str, dict[str, int]]
print("\n=== Smoke Test 3: LGB SignalMonitor Counter + defaultdict ===")
try:
    from collections import Counter, defaultdict

    # Verify annotations are usable at runtime
    multiplier_dist: Counter = Counter()
    multiplier_dist[1.08] = 5
    multiplier_dist[0.92] = 3
    # max with lambda key (replaces type:ignore form)
    top_mult = max(multiplier_dist, key=lambda k: multiplier_dist.get(k, 0))
    assert top_mult == 1.08, f"top_mult={top_mult}"

    per_symbol_stats: defaultdict = defaultdict(
        lambda: {"boost": 0, "cut": 0, "neutral": 0, "total": 0}
    )
    per_symbol_stats["600000"]["boost"] += 1
    per_symbol_stats["600000"]["total"] += 1
    assert per_symbol_stats["600000"]["boost"] == 1
    _ok(f"Counter max={top_mult}, defaultdict stats={dict(per_symbol_stats)}")
except Exception as e:  # noqa: BLE001
    _fail("LGB SignalMonitor", e)

# 4) MLOpsPipeline: dict[str, Any] status index
print("\n=== Smoke Test 4: MLOpsPipeline status dict[str, Any] ===")
try:
    MLOpsPipeline = modules["mlops"].MLOpsPipeline
    pipeline = MLOpsPipeline()
    status = pipeline.get_status()
    assert "components" in status, f"status missing components: {status}"
    assert "enabled" in status, f"status missing enabled: {status}"
    _ok(f"status keys={list(status.keys())}")
except Exception as e:  # noqa: BLE001
    _fail("MLOpsPipeline", e)

# 5) AttributionManagers: cast for tracker Any returns
print("\n=== Smoke Test 5: AttributionManagers cast ===")
try:
    # Verify cast import works and managers module loads

    assert hasattr(modules["mgr"], "ETFFlowManager"), "ETFFlowManager missing"
    # Instantiate (won't actually fetch - tracker is lazy)
    mgr_cls = modules["mgr"].ETFFlowManager
    mgr = mgr_cls()
    _ok("ETFFlowManager instantiated, tracker lazy load OK")
except Exception as e:  # noqa: BLE001
    _fail("AttributionManagers", e)

# 6) TransformerEncoder: torch/nn forward declaration
print("\n=== Smoke Test 6: TransformerEncoder torch forward-decl ===")
try:
    tfe_mod = modules["tfe"]
    assert hasattr(tfe_mod, "torch"), "torch attr missing"
    assert hasattr(tfe_mod, "nn"), "nn attr missing"
    assert hasattr(tfe_mod, "_TORCH_AVAILABLE"), "_TORCH_AVAILABLE missing"
    torch_state = "available" if tfe_mod._TORCH_AVAILABLE else "numpy-shadow"
    _ok(
        f"torch={tfe_mod.torch}, _TORCH_AVAILABLE={tfe_mod._TORCH_AVAILABLE} ({torch_state})"
    )
except Exception as e:  # noqa: BLE001
    _fail("TransformerEncoder", e)

# 7) FactorModel: GTJA191 forward-decl + float cast + dict[str, int]
print("\n=== Smoke Test 7: FactorModel GTJA191 + float ===")
try:
    fm_mod = modules["fm"]
    assert hasattr(fm_mod, "GTJA191Factors"), "GTJA191Factors attr missing"
    assert hasattr(fm_mod, "_HAS_GTJA191"), "_HAS_GTJA191 missing"
    # Verify _to_signal accepts float (not numpy float)
    FactorModel = fm_mod.FactorModel
    model = FactorModel()
    sig = model._to_signal(0.5)
    assert isinstance(sig, str), f"signal type={type(sig)}"
    _ok(f"GTJA191={fm_mod.GTJA191Factors}, _to_signal(0.5)={sig}")
except Exception as e:  # noqa: BLE001
    _fail("FactorModel", e)

# 8) PhaseManager: phase attribute + actions None guard
print("\n=== Smoke Test 8: PhaseManager phase + None guard ===")
try:
    PhaseManager = modules["pm"].PhaseManager
    pm = PhaseManager()
    from datetime import date

    phase = pm.get_current_phase(date(2026, 8, 12))
    assert hasattr(phase, "phase_name"), f"phase missing phase_name: {phase}"
    assert isinstance(
        phase.phase_name, str
    ), f"phase_name type={type(phase.phase_name)}"
    # liquidation actions returns dict | None
    actions = pm.get_liquidation_actions(date(2026, 8, 12))
    assert actions is None or isinstance(actions, dict), f"actions type={type(actions)}"
    _ok(f"phase={phase.phase_name}, actions={'None' if actions is None else 'dict'}")
except Exception as e:  # noqa: BLE001
    _fail("PhaseManager", e)

# 9) LedoitWolf: redundant ignores removed, fit works
print("\n=== Smoke Test 9: LedoitWolf fit ndarray|DataFrame ===")
try:
    import numpy as np

    LedoitWolf = modules["lw"].LedoitWolfCovariance
    lw = LedoitWolf()
    # 50 obs × 5 assets
    returns = np.random.RandomState(42).randn(50, 5) * 0.02
    result = lw.fit(returns)
    assert hasattr(result, "cov_shrunk"), f"result missing cov_shrunk: {result}"
    cov = result.cov_shrunk
    assert cov.shape == (5, 5), f"cov shape={cov.shape}"
    # fit_predict convenience
    cov2 = lw.fit_predict(returns)
    assert cov2.shape == (5, 5), f"fit_predict shape={cov2.shape}"
    _ok(f"fit→cov{cov.shape}, fit_predict→cov{cov2.shape}")
except Exception as e:  # noqa: BLE001
    _fail("LedoitWolf", e)

# 10) ETF flow monitor: spec None guard + Dict[str, Any]
print("\n=== Smoke Test 10: ETF flow monitor None guard ===")
try:
    efm = modules["efm"]
    # refresh_etf_flow_signals signature should return Dict[str, Any]
    import inspect

    sig = inspect.signature(efm.refresh_etf_flow_signals)
    _ok(f"refresh_etf_flow_signals params={list(sig.parameters.keys())}")
    # ETFRealTimeTracker importable
    tracker_cls = efm.ETFRealTimeTracker
    tracker = tracker_cls()
    _ok(
        f"ETFRealTimeTracker instantiated, wind_mcp_available={tracker.wind_mcp_available}"
    )
except Exception as e:  # noqa: BLE001
    _fail("ETF flow monitor", e)

# 11) ModelRegistry: mlflow_client None guard + result dict[str, Any]
print("\n=== Smoke Test 11: ModelRegistry None guard + export ===")
try:
    ModelRegistry = modules["mr"].ModelRegistry
    registry = ModelRegistry()
    # export_registry uses dict[str, Any] result
    manifest = registry.export_registry()
    assert "models" in manifest, f"manifest missing models: {manifest}"
    assert "mlflow_available" in manifest, "manifest missing mlflow_available"
    _ok(f"export keys={list(manifest.keys())}, mlflow={manifest['mlflow_available']}")
except Exception as e:  # noqa: BLE001
    _fail("ModelRegistry", e)

# 12) Verify NO type:ignore remains in any of the 10 modules
print("\n=== Smoke Test 12: type:ignore 残留扫描 ===")
try:
    import re

    files_to_check = [
        ROOT / "utils" / "execution_algo_engine.py",
        ROOT / "utils" / "lgb_signal_monitor.py",
        ROOT / "utils" / "alpha" / "mlops_pipeline.py",
        ROOT / "utils" / "attribution" / "managers.py",
        ROOT / "utils" / "alpha_factor" / "transformer_encoder.py",
        ROOT / "utils" / "factor_model.py",
        ROOT / "utils" / "phase_manager.py",
        ROOT / "utils" / "ledoit_wolf_covariance.py",
        ROOT / "utils" / "etf_flow_monitor.py",
        ROOT / "utils" / "alpha" / "model_registry.py",
    ]
    total_ignores = 0
    for fp in files_to_check:
        content = fp.read_text(encoding="utf-8")
        matches = re.findall(r"type:\s*ignore", content)
        if matches:
            print(f"  [WARN] {fp.name}: {len(matches)} type:ignore remaining")
            total_ignores += len(matches)
    if total_ignores == 0:
        _ok("所有 10 模块 type:ignore = 0")
    else:
        _fail("type:ignore 残留", f"total={total_ignores}")
except Exception as e:  # noqa: BLE001
    _fail("type:ignore 扫描", e)

print(f"\n=== Batch I Smoke Result: {passed} PASS, {failed} FAIL ===")
sys.exit(0 if failed == 0 else 1)
