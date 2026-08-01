"""CapacityAnalyzer 单元测试 - CIO v1.0"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.validators.capacity_analyzer import (
    quick_analyze,
)


def _make_factor(n_syms=100, seed=42):
    import numpy as np
    rng = np.random.default_rng(seed)
    syms = [f"S{i:03d}" for i in range(n_syms)]
    fv = {s: float(rng.normal(0, 1)) for s in syms}
    adv = {s: float(rng.uniform(5e6, 5e7)) for s in syms}  # 5M-50M ADV
    return fv, adv


def test_high_capacity_passes():
    print("\n[Test 1] 高容量因子通过...")
    fv, adv = _make_factor(n_syms=100)
    r = quick_analyze(fv, adv, turnover=0.2, portfolio_value=1e8, factor_name="VT_HIGH_CAP")
    print(f"  cap={r.capacity_usd/1e6:.2f}M ratio={r.capacity_ratio:.4f}")
    assert r.pass_capacity, "高容量应通过"


def test_low_capacity_rejected():
    print("\n[Test 2] 低容量因子拒绝...")
    fv, adv = _make_factor(n_syms=10)  # 标的少
    r = quick_analyze(fv, adv, turnover=0.5, portfolio_value=1e9, factor_name="VT_LOW_CAP")
    print(f"  cap={r.capacity_usd/1e6:.2f}M ratio={r.capacity_ratio:.4f}")
    assert not r.pass_capacity, "低容量应被拒"


def test_high_turnover_penalty():
    print("\n[Test 3] 高换手率惩罚...")
    fv, adv = _make_factor(n_syms=100)
    r_low = quick_analyze(fv, adv, turnover=0.1, portfolio_value=1e8, factor_name="VT_LOW_TO")
    r_high = quick_analyze(fv, adv, turnover=0.5, portfolio_value=1e8, factor_name="VT_HIGH_TO")
    print(f"  低换手率 cap={r_low.capacity_usd/1e6:.2f}M")
    print(f"  高换手率 cap={r_high.capacity_usd/1e6:.2f}M")
    assert r_high.capacity_usd < r_low.capacity_usd, "高换手率应导致更低容量"
    assert r_high.turnover_penalty > r_low.turnover_penalty


def test_no_common_symbols():
    print("\n[Test 4] 无共同标的降级...")
    fv = {"A": 0.5, "B": -0.3}
    adv = {"C": 1e7, "D": 2e7}  # 不与 fv 重叠
    r = quick_analyze(fv, adv, turnover=0.3, portfolio_value=1e8, factor_name="VT_NO_COMMON")
    assert not r.pass_capacity
    assert "无共同标的" in r.reason or "no result" in r.reason.lower() or r.reason != "pass"
    print(f"  ✓ 拒绝原因: {r.reason}")


def test_serializable():
    print("\n[Test 5] 序列化...")
    import json
    fv, adv = _make_factor(n_syms=50)
    r = quick_analyze(fv, adv, turnover=0.3, portfolio_value=1e8, factor_name="VT_SER")
    d = r.to_dict()
    json_str = json.dumps(d, ensure_ascii=False)
    assert len(json_str) > 0
    print(f"  ✓ 序列化长度={len(json_str)}")


def main():
    print("=" * 60)
    print("CapacityAnalyzer 单元测试 - CIO v1.0")
    print("=" * 60)
    tests = [
        test_high_capacity_passes,
        test_low_capacity_rejected,
        test_high_turnover_penalty,
        test_no_common_symbols,
        test_serializable,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"  ✗ FAIL: {e}")
        except Exception as e:
            failed += 1
            print(f"  ✗ ERROR: {type(e).__name__}: {e}")
    print("\n" + "=" * 60)
    print(f"总计: {passed} 通过, {failed} 失败 (共 {len(tests)} 项)")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
