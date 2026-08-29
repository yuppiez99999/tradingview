"""Smoke tests for Batch H (4 modules × 5 = 20 type:ignore → 0).
Modules: black_litterman_optimizer, ml_enhanced_selector, quant_neutral_runner, trading_rules.
"""

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
    ("bl", "utils.black_litterman_optimizer"),
    ("ml", "utils.alpha.ml_enhanced_selector"),
    ("qn", "utils.quant_neutral_runner"),
    ("tr", "utils.trading_rules"),
]:
    try:
        modules[name] = importlib.import_module(path)
        print(f"  [OK]  import {path}")
        passed += 1
    except Exception as e:  # noqa: BLE001
        print(f"  [FAIL] import {path}: {e}")
        failed += 1

# 2) BlackLitterman: PEP 604 params + ndarray cast returns
print("\n=== Smoke Test 2: BlackLittermanOptimizer PEP604 + cast ===")
try:
    import numpy as np

    BLOpt = modules["bl"].BlackLittermanOptimizer
    opt = BLOpt()
    assets = ["A", "B", "C"]
    mkt_w = np.array([0.4, 0.3, 0.3])
    cov = np.eye(3) * 0.04
    # optimize with PEP 604 cov_matrix: np.ndarray | pd.DataFrame
    result = opt.optimize(assets, mkt_w, cov)
    assert hasattr(
        result, "optimal_weights"
    ), f"BLResult missing optimal_weights: {result}"
    w = np.asarray(result.optimal_weights)
    assert len(w) == 3, f"weights len={len(w)}, expected 3"
    print(f"  [OK] BL optimize() → weights={w.round(4).tolist()}")
    passed += 1
except Exception as e:  # noqa: BLE001
    print(f"  [FAIL] BL: {e}")
    failed += 1

# 3) MLEnhancedSelector: assert self._model is not None narrow
print("\n=== Smoke Test 3: MLEnhancedSelector assert-narrow ===")
try:
    MLS = modules["ml"].MLEnhancedSelector
    selector = MLS()
    # Train a tiny model (need >= 10 samples)
    X = np.array([[float(i)] * 2 for i in range(12)])
    y = np.array([0, 1] * 6)
    selector.train(X, y, feature_names=["f1", "f2"])
    # predict_proba — uses assert self._model is not None
    proba = selector.predict_proba(X)
    assert (
        proba is not None and len(proba) == 12
    ), f"proba len={len(proba) if proba is not None else None}"
    # predict — same assert
    preds = selector.predict(X)
    assert len(preds) == 12, f"preds len={len(preds)}"
    # get_feature_importance — same assert
    imp = selector.get_feature_importance()
    assert isinstance(imp, dict) and len(imp) == 2, f"importance={imp}"
    print(
        f"  [OK] MLS predict_proba({len(proba)}) + predict({len(preds)}) + importance({list(imp.keys())})"
    )
    passed += 1
except Exception as e:  # noqa: BLE001
    print(f"  [FAIL] MLS: {e}")
    failed += 1

# 4) QuantNeutralRunner: 3-class forward declaration + float return
print("\n=== Smoke Test 4: QuantNeutralRunner forward-decl + float ===")
try:
    QNR = modules["qn"].QuantNeutralRunner
    runner = QNR()
    # ic_calc: either real ICHedgeCalculator or None (no crash)
    assert hasattr(runner, "ic_calc"), "runner missing ic_calc"
    # _calc_portfolio_beta with list of dicts
    positions = [
        {"weight": 0.3, "beta": 1.2},
        {"weight": 0.5, "beta": 0.8},
        {"weight": 0.2, "beta": 1.0},
    ]
    beta = runner.calculate_portfolio_beta(positions)
    assert isinstance(beta, float), f"beta not float: {type(beta)}"
    expected = (0.3 * 1.2 + 0.5 * 0.8 + 0.2 * 1.0) / 1.0
    assert abs(beta - expected) < 1e-6, f"beta={beta}, expected={expected}"
    print(f"  [OK] QNR ic_calc={runner.ic_calc}, portfolio_beta={beta:.4f}")
    passed += 1
except Exception as e:  # noqa: BLE001
    print(f"  [FAIL] QNR: {e}")
    failed += 1

# 5) TradingRules: dict[str, Any] heterogeneous values
print("\n=== Smoke Test 5: TradingRules heterogeneous dict[str, Any] ===")
try:
    tr = modules["tr"]
    # FUTURE
    r1 = tr.get_trading_rule("IF2406", product_class="FUTURE")
    assert r1["price_limit_pct"] == 0.10, f"FUTURE limit={r1['price_limit_pct']}"
    assert r1["margin_required"] is True
    # OPTION
    r2 = tr.get_trading_rule("10004001", product_class="OPTION")
    assert r2["price_limit_pct"] == 0.0, f"OPTION limit={r2['price_limit_pct']}"
    # 科创板 (68)
    r3 = tr.get_trading_rule("688001", product_class="STOCK")
    assert r3["price_limit_pct"] == 0.20, f"688 limit={r3['price_limit_pct']}"
    # 创业板 (3)
    r4 = tr.get_trading_rule("300001", product_class="STOCK")
    assert r4["price_limit_pct"] == 0.20, f"300 limit={r4['price_limit_pct']}"
    # 主板
    r5 = tr.get_trading_rule("600001", product_class="STOCK")
    assert r5["price_limit_pct"] == 0.10, f"600 limit={r5['price_limit_pct']}"
    print(
        f"  [OK] TR FUTURE/OPTION/688/300/600 limits = {r1['price_limit_pct']}/{r2['price_limit_pct']}/{r3['price_limit_pct']}/{r4['price_limit_pct']}/{r5['price_limit_pct']}"
    )
    passed += 1
except Exception as e:  # noqa: BLE001
    print(f"  [FAIL] TR: {e}")
    failed += 1

print(f"\n=== Batch H Smoke Result: {passed} PASS, {failed} FAIL ===")
sys.exit(0 if failed == 0 else 1)
