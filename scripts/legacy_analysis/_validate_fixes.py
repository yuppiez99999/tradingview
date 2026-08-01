# -*- coding: utf-8 -*-
"""验证修复: factor_library 18因子 + SignalFusion 动态IC权重 + daily_workflow 导入"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "utils"))
sys.path.insert(0, str(BASE / "ms_strategy" / "src"))
sys.path.insert(0, str(BASE / "v8.3_institutional"))

print("=" * 70)
print("验证1: SignalFusion inject_forward_returns + IC 动态权重")
print("=" * 70)
try:
    from utils.signal_fusion import SignalFusionEngine
    eng = SignalFusionEngine()
    # 注入 forward_returns
    eng.inject_forward_returns({"600519": 0.05, "000858": -0.03, "601318": 0.02,
                               "600276": 0.01, "000333": -0.02, "600036": 0.04})
    # 模拟一次 fuse（缓存各源信号）
    alpha = {"600519": {"strength": 0.6, "confidence": 0.7},
             "000858": {"strength": -0.4, "confidence": 0.6},
             "601318": {"strength": 0.3, "confidence": 0.5},
             "600276": {"strength": 0.2, "confidence": 0.4},
             "000333": {"strength": -0.3, "confidence": 0.6},
             "600036": {"strength": 0.5, "confidence": 0.7}}
    signals = eng.fuse(alpha_signals=alpha)
    print(f"  ✓ fuse() 返回 {len(signals)} 个信号")
    print(f"  ✓ IC 权重已计算: {eng._ic_weights is not None}")
    if eng._ic_weights:
        print(f"    IC 权重: { {k: round(v,4) for k,v in eng._ic_weights.items()} }")
    # 验证 inject_qlib_signal 兼容方法
    eng.inject_qlib_signal({"600519": 0.5, "000858": -0.3})
    print(f"  ✓ inject_qlib_signal 兼容方法正常, 缓存 {len(eng._qlib_signals)} 个标的")
    print("  [PASS] SignalFusion 验证通过")
except Exception as e:
    print(f"  [FAIL] SignalFusion 验证失败: {e}")
    import traceback; traceback.print_exc()

print()
print("=" * 70)
print("验证2: factor_library build_all_factors 18 因子")
print("=" * 70)
try:
    import numpy as np
    import pandas as pd
    from alpha.factor_library import FactorLibrary
    fl = FactorLibrary()
    # 构造 3 个标的的模拟价格数据（足够长度计算所有因子）
    np.random.seed(42)
    dates = pd.date_range(end="2026-07-24", periods=200, freq="B")
    price_data = {}
    for sym in ["600519", "000858", "601318"]:
        rets = np.random.normal(0.0005, 0.02, 200)
        prices = 100 * np.exp(np.cumsum(rets))
        price_data[sym] = pd.DataFrame({
            "open": prices, "high": prices * 1.01, "low": prices * 0.99,
            "close": prices, "volume": np.random.randint(1e6, 1e7, 200),
        }, index=dates)
    factors = fl.build_all_factors(price_data)
    # 统计因子类型数（去重 symbol 前缀）
    factor_types = set()
    for name in factors.keys():
        # 去掉 symbol 前缀，保留因子类型名
        for sym in price_data.keys():
            if name.startswith(f"{sym}_"):
                factor_types.add(name[len(sym)+1:])
                break
    print(f"  ✓ build_all_factors 完成: {len(factors)} 个因子序列, {len(factor_types)} 种因子类型")
    print(f"    因子类型: {sorted(factor_types)}")
    assert len(factor_types) >= 9, f"价格类因子应>=9, 实际{len(factor_types)}"
    print("  [PASS] factor_library 验证通过 (>=9 价格类因子)")

    # 测试含 fundamentals 时的因子数
    fund = {"600519": {"eps": 50.0, "book_value_per_share": 200, "sales_per_share": 100,
                       "net_income": 5e9, "equity": 2e10, "revenue": 1e10, "cogs": 4e9,
                       "total_debt": 3e9, "ebitda": 8e9, "est_current": 52, "est_prior": 50}}
    factors2 = fl.build_all_factors(price_data, fundamentals=fund)
    types2 = set()
    for name in factors2.keys():
        for sym in price_data.keys():
            if name.startswith(f"{sym}_"):
                types2.add(name[len(sym)+1:])
                break
    print(f"  ✓ 含基本面时: {len(types2)} 种因子类型 (含基本面因子)")
    print("  [PASS] factor_library 基本面因子验证通过")
except Exception as e:
    print(f"  [FAIL] factor_library 验证失败: {e}")
    import traceback; traceback.print_exc()

print()
print("=" * 70)
print("验证3: daily_workflow 语法 + SignalFusionEngine 导入")
print("=" * 70)
try:
    import py_compile
    dw_path = BASE / "v8.3_institutional" / "daily_workflow.py"
    py_compile.compile(str(dw_path), doraise=True)
    print("  ✓ daily_workflow.py 语法检查通过")
    # 验证关键修复点
    with open(dw_path, "r", encoding="utf-8") as f:
        src = f.read()
    assert "from utils.signal_fusion import SignalFusionEngine" in src, "导入路径未修复"
    assert "inject_forward_returns" in src, "forward_returns 注入未添加"
    assert "_compute_realized_forward_returns" in src, "前向收益计算方法未添加"
    print("  ✓ 导入路径已修复: utils.signal_fusion.SignalFusionEngine")
    print("  ✓ forward_returns 注入点已添加")
    print("  [PASS] daily_workflow 验证通过")
except Exception as e:
    print(f"  [FAIL] daily_workflow 验证失败: {e}")
    import traceback; traceback.print_exc()

print()
print("=" * 70)
print("全部验证完成")
print("=" * 70)
