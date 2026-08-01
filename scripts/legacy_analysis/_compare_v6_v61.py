"""对比V6和V6.1月度收益, 找出Window 1差异根因"""
import json
from pathlib import Path

# 加载V6结果
v6_file = Path("output/validation_reports/lgb_backtest_v6_alpha_quality_20260725_063847.json")
with open(v6_file, encoding="utf-8") as f:
    v6_data = json.load(f)

# 加载V6.1结果
v61_files = sorted(Path("output/validation_reports").glob("lgb_backtest_v6_1*.json"))
with open(v61_files[-1], encoding="utf-8") as f:
    v61_data = json.load(f)

v6_recs = {r["date"]: r for r in v6_data["records"]}
v61_recs = {r["date"]: r for r in v61_data["records"]}

print("=" * 90)
print("V6 vs V6.1 月度收益对比 (找出差异月份)")
print("=" * 90)
print(f"{'日期':<12} {'V6收益':>8} {'V6.1收益':>8} {'差异':>8} {'V6止盈':>20} {'V6.1止盈':>20}")
print("-" * 90)

for date in sorted(v6_recs.keys()):
    v6_ret = v6_recs[date]["portfolio_return"]
    v61_ret = v61_recs.get(date, {}).get("portfolio_return", 0)
    diff = v61_ret - v6_ret

    v6_pt = v6_recs[date].get("profit_taking", {})
    v61_pt = v61_recs.get(date, {}).get("profit_taking", {})

    v6_pt_str = ""
    if v6_pt.get("portfolio_factor", 1.0) < 1.0:
        v6_pt_str += f"组合×{v6_pt['portfolio_factor']:.2f}"
    if v6_pt.get("symbols"):
        v6_pt_str += f" 个股{len(v6_pt['symbols'])}"

    v61_pt_str = ""
    if v61_pt.get("portfolio_factor", 1.0) < 1.0:
        v61_pt_str += f"组合×{v61_pt['portfolio_factor']:.2f}"
    if v61_pt.get("symbols"):
        v61_pt_str += f" 个股{len(v61_pt['symbols'])}"

    marker = ""
    if abs(diff) > 0.001:
        marker = " ← DIFF"

    print(f"{date:<12} {v6_ret*100:7.2f}% {v61_ret*100:7.2f}% {diff*100:+7.2f}% {v6_pt_str:>20} {v61_pt_str:>20}{marker}")
