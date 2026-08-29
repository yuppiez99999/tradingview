"""检查交易逻辑问题."""
import json
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
pos = json.loads((PROJ / "config/positions.json").read_text(encoding="utf-8"))

# 1. 国债ETF详情
p = pos["positions"]["511010.SH"]
print("=== 511010.SH 国债ETF 持仓详情 ===")
for k in ["code", "name", "shares", "est_price", "avg_cost", "target_weight", "amount", "sector", "type", "reason"]:
    print(f"  {k}: {p.get(k)}")
print()

# 2. 资金分配
print(f"对冲模式: {pos['meta'].get('hedge_mode')}")
print(f"总资金: ¥{pos['meta']['total_capital']:,.0f}")
print(f"股票ETF资金: ¥{pos['meta']['stock_etf_capital']:,.0f}")
print(f"对冲资金: ¥{pos['meta']['hedge_capital']:,.0f}")
print()

# 3. 权重分析
total = sum(v["shares"] * v.get("est_price", 0) for v in pos["positions"].values() if v["shares"] > 0)
print(f"持仓总市值: ¥{total:,.0f}")
print(f"国债ETF占比: {p['shares']*p['est_price']/total*100:.1f}%")
print(f"国债ETF target_weight: {p.get('target_weight', 0)*100:.1f}%")
print()

# 4. 所有标的权重 vs target_weight
print("=== 权重偏离分析 ===")
print(f"{'代码':12s} {'名称':20s} {'实际权重':>8s} {'目标权重':>8s} {'偏离':>8s}")
sorted_pos = sorted(
    [(k, v) for k, v in pos["positions"].items() if v["shares"] > 0],
    key=lambda x: x[1]["shares"] * x[1].get("est_price", 0),
    reverse=True
)
for code, v in sorted_pos:
    mv = v["shares"] * v.get("est_price", 0)
    actual_w = mv / total if total > 0 else 0
    target_w = v.get("target_weight", 0)
    deviation = actual_w - target_w
    flag = " ***" if abs(deviation) > 0.05 else ""
    print(f"{code:12s} {v.get('name',''):20s} {actual_w*100:7.1f}% {target_w*100:7.1f}% {deviation*100:+7.1f}%{flag}")

# 5. 检查亏损标的
print()
print("=== 持仓盈亏分析 (est_price vs avg_cost) ===")
for code, v in sorted_pos:
    ep = v.get("est_price", 0)
    ac = v.get("avg_cost", 0)
    if ac > 0 and ep > 0:
        pnl_pct = (ep - ac) / ac
        flag = " *** 亏损" if pnl_pct < 0 else ""
        print(f"  {code:12s} 均成本{ac:8.3f} 现价{ep:8.3f} 盈亏{pnl_pct*100:+7.2f}%{flag}")