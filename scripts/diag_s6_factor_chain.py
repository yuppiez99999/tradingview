"""P0-1 S6 因子数据链路排查 — 逐环节复现 _fetch_daily_factors 降级根因.

环节: A 依赖导入 → B 股票池 → C 价格数据 fetch_prices(days=400)
      → D SupplyChainBuilder → E 因子计算+正交化
"""
import os
import sys

os.environ["NO_PROXY"] = ",".join(
    [
        "push2his.eastmoney.com",
        "push2.eastmoney.com",
        "eastmoney.com",
        "sinajs.cn",
        "sina.com.cn",
    ]
)
sys.path.insert(0, str(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

results = {}


def _dump(res: dict) -> None:
    """[DEBUG-s6v1] 提前退出时打印已完成的环节汇总."""
    print()
    print("=" * 60)
    print("提前退出 — 环节汇总")
    print("=" * 60)
    for k, v in res.items():
        status = "✅" if str(v).startswith("PASS") else "❌"
        print(f"{status} {k}: {v}")


# --- 环节 A: 依赖导入 ---
print("=" * 60)
print("[环节 A] 因子计算依赖导入")
print("=" * 60)
try:
    from utils.alpha_factor.gate1_validation import (
        _compute_momentum_factors,
        fetch_prices,
        load_expanded_universe,
    )
    from utils.alpha_factor.graph import (
        compute_lead_lag_factors,
        orthogonalize_chain_factors,
    )
    from utils.supply_chain_builder import SupplyChainBuilder

    print("PASS: 全部依赖可导入")
    results["A_imports"] = "PASS"
except ImportError as e:
    print(f"FAIL: {e}")
    results["A_imports"] = f"FAIL: {e}"
    _dump(results)
    sys.exit(1)

# --- 环节 B: 股票池 ---
print()
print("=" * 60)
print("[环节 B] load_expanded_universe")
print("=" * 60)
try:
    symbols, industries = load_expanded_universe()
    print(f"PASS: {len(symbols)} 只标的, {len(industries)} 个行业映射")
    print(f"  样本: {symbols[:5]}")
    results["B_universe"] = f"PASS ({len(symbols)} symbols)"
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
    results["B_universe"] = f"FAIL: {type(e).__name__}: {e}"
    _dump(results)
    sys.exit(1)

# --- 环节 C: 价格数据 ---
print()
print("=" * 60)
print("[环节 C] fetch_prices(symbols, days=400)")
print("=" * 60)
try:
    price_data = fetch_prices(symbols, days=400)
    valid = [
        s
        for s in symbols
        if s in price_data and len(price_data[s].get("closes", [])) > 60
    ]
    print(f"PASS: fetch 返回 {len(price_data)}/{len(symbols)} 标的, 有效(>60 closes) {len(valid)} 只")
    if not valid:
        print("FAIL: 无有效价格数据 → 这就是骨架降级根因")
        for s in list(price_data)[:5]:
            closes = price_data[s].get("closes", [])
            print(f"  {s}: closes={len(closes)}")
        results["C_prices"] = f"FAIL: 0 valid / {len(price_data)} fetched"
    else:
        results["C_prices"] = f"PASS ({len(valid)} valid)"
except Exception as e:
    import traceback

    traceback.print_exc()
    print(f"FAIL: {type(e).__name__}: {e}")
    results["C_prices"] = f"FAIL: {type(e).__name__}: {e}"

if not results.get("C_prices", "").startswith("PASS"):
    _dump(results)
    sys.exit(1)

# --- 环节 D: 供应链图 ---
print()
print("=" * 60)
print("[环节 D] SupplyChainBuilder.build()")
print("=" * 60)
try:
    builder = SupplyChainBuilder(symbols=valid, include_themes=True, max_hops=2)
    builder.build()
    n_edges = len(builder.graph) if hasattr(builder.graph, "__len__") else "?"
    print(f"PASS: 图构建完成, {n_edges} 边")
    results["D_graph"] = "PASS"
except Exception as e:
    import traceback

    traceback.print_exc()
    results["D_graph"] = f"FAIL: {type(e).__name__}: {e}"

# --- 环节 E: 因子计算 ---
print()
print("=" * 60)
print("[环节 E] compute_lead_lag_factors + 正交化")
print("=" * 60)
try:
    chain_factors = compute_lead_lag_factors(
        price_data, builder.graph, industries=industries
    )
    mom_factors = _compute_momentum_factors(price_data)
    chain_factors = orthogonalize_chain_factors(chain_factors, mom_factors)
    mom_60d = mom_factors.get("MOM_60D")
    chain_60d = chain_factors.get("CHAIN_MOM_60D")
    print(f"chain_factors keys: {list(chain_factors.keys())}")
    print(f"mom_60d: {'OK' if mom_60d else 'EMPTY'} ({len(mom_60d.values) if mom_60d else 0} 值)")
    print(f"chain_60d: {'OK' if chain_60d else 'EMPTY'} ({len(chain_60d.values) if chain_60d else 0} 值)")
    if mom_60d and chain_60d:
        print("PASS: 因子数据链路完整可用 → S6 可产出真实记录")
        results["E_factors"] = "PASS"
    else:
        print("FAIL: 因子返回空 → 骨架降级根因在此")
        results["E_factors"] = "FAIL: empty factors"
except Exception as e:
    import traceback

    traceback.print_exc()
    results["E_factors"] = f"FAIL: {type(e).__name__}: {e}"

# --- 汇总 ---
print()
print("=" * 60)
print("排查汇总")
print("=" * 60)
for k, v in results.items():
    status = "✅" if str(v).startswith("PASS") else "❌"
    print(f"{status} {k}: {v}")