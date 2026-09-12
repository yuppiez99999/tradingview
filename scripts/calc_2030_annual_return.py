import sys
from datetime import datetime
from pathlib import Path

# P2 修复 (2026-09-09): 用 positions_loader 统一入口, 不依赖 CWD
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from utils.positions_loader import load_positions
from utils.risk_thresholds import (  # SC-24: 资金口径唯一事实源
    get_stock_etf_capital,
    get_total_capital,
)

data = load_positions()
meta = data.get("meta", {})
positions = data.get("positions", {})

total_cost = 0.0
total_value = 0.0
total_pnl = 0.0
details = []

for code, item in positions.items():
    shares = (
        item.get("phase1_shares") or item.get("total_shares") or item.get("shares", 0)
    )
    avg_cost = item.get("avg_cost", 0)
    est_price = item.get("est_price", 0)
    cost = abs(shares) * avg_cost
    value = abs(shares) * est_price
    pnl = value - cost
    pnl_pct = pnl / cost * 100 if cost > 0 else 0
    total_cost += cost
    total_value += value
    total_pnl += pnl
    details.append(
        {
            "code": code,
            "name": item.get("name", ""),
            "shares": shares,
            "avg_cost": avg_cost,
            "est_price": est_price,
            "cost": round(cost, 2),
            "value": round(value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
        }
    )

# SC-24 (2026-09-12, capital_base 消费点复验): 原 3_000_000 / 5_000_000 为
#   P1-2 资金口径统一时漏改的残留 (5M = 证券 300w + 期货 200w 旧计划口径)。
#   现统一走唯一事实源: 证券/ETF 腿 200 万, 总口径 300 万 (200 + 100)。
stock_etf_target = meta.get("stock_etf_capital", get_stock_etf_capital())
total_capital = meta.get("total_capital", get_total_capital())
start_date = meta.get("start_date", "2026-07-13")
clearance_date = meta.get("clearance_date", "2030-12-31")

print("=" * 80)
print("当前持仓汇总")
print("=" * 80)
print(f"标的数: {len(positions)}")
print(f"总成本: ¥{total_cost:,.2f}")
print(f"当前市值: ¥{total_value:,.2f}")
print(f"总盈亏: ¥{total_pnl:,.2f}")
if total_cost > 0:
    print(f"当前收益率: {total_pnl/total_cost*100:.2f}%")
print(f"股票ETF目标: ¥{stock_etf_target:,}")
print(f"总资本: ¥{total_capital:,}")
print(f"建仓开始: {start_date}")
print(f"清仓日期: {clearance_date}")
print()

print("=" * 80)
print("各标的盈亏 (按收益率排序)")
print("=" * 80)
for d in sorted(details, key=lambda x: x["pnl_pct"]):
    print(
        f'{d["name"]:14s} {d["code"]:12s} 持{d["shares"]:>6d} 成本¥{d["avg_cost"]:.3f} 现价¥{d["est_price"]:.3f} 盈亏¥{d["pnl"]:>+10,.0f} ({d["pnl_pct"]:>+6.1f}%)'  # noqa: E501
    )

print()
print("=" * 80)
print("2030年平仓年化收益率测算")
print("=" * 80)

# 计算持有期
start_dt = datetime.strptime(start_date, "%Y-%m-%d")
end_dt = datetime.strptime(clearance_date, "%Y-%m-%d")
holding_days = (end_dt - start_dt).days
holding_years = holding_days / 365.25

print(
    f"持有期: {start_date} → {clearance_date} = {holding_days}天 = {holding_years:.2f}年"
)
print()

# 场景分析: 不同2030年平仓总市值下的年化收益率
# 年化收益率 = (最终市值 / 总成本)^(1/年数) - 1
print(
    f'{"场景":20s} {"2030市值":>12s} {"总盈亏":>12s} {"总收益率":>10s} {"年化收益率":>10s}'
)
print("-" * 80)

scenarios = [
    ("当前市值不变", total_value),
    ("回到成本线", total_cost),
    ("+10%", total_cost * 1.10),
    ("+20%", total_cost * 1.20),
    ("+30%", total_cost * 1.30),
    ("+50%", total_cost * 1.50),
    ("+80%", total_cost * 1.80),
    ("+100% (翻倍)", total_cost * 2.00),
    ("股票ETF目标300万", stock_etf_target),
    ("总资本500万", total_capital),
    ("总资本+50%=750万", total_capital * 1.5),
    ("总资本翻倍=1000万", total_capital * 2.0),
]

for name, final_value in scenarios:
    pnl = final_value - total_cost
    total_return = pnl / total_cost * 100 if total_cost > 0 else 0
    if total_cost > 0 and final_value > 0:
        annualized = (final_value / total_cost) ** (1 / holding_years) - 1
        ann_str = f"{annualized*100:>9.2f}%"
    else:
        ann_str = "N/A"
    print(
        f"{name:20s} ¥{final_value:>10,.0f} ¥{pnl:>+10,.0f} {total_return:>+9.1f}% {ann_str}"
    )

print()
print("=" * 80)
print("关键指标")
print("=" * 80)

# 当前已实现的年化收益率 (从建仓到现在)
now = now_bj()
elapsed_days = (now - start_dt).days
elapsed_years = elapsed_days / 365.25
if elapsed_years > 0 and total_cost > 0:
    current_ann = (total_value / total_cost) ** (1 / elapsed_years) - 1
    print(f"已持有: {elapsed_days}天 = {elapsed_years:.2f}年")
    print(f"当前已实现年化: {current_ann*100:.2f}%")
    print(f"当前总收益率: {total_pnl/total_cost*100:.2f}%")
else:
    print("持有期不足，无法计算年化")

print()
# 要达到不同年化目标，2030年需要的市值
print(
    f'{"目标年化":>10s} {"需要2030市值":>14s} {"需要总盈亏":>14s} {"较当前盈亏":>14s}'
)
print("-" * 60)
for target_ann in [0.05, 0.08, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30]:
    needed_value = total_cost * (1 + target_ann) ** holding_years
    needed_pnl = needed_value - total_cost
    diff = needed_pnl - total_pnl
    print(
        f"{target_ann*100:>9.0f}% ¥{needed_value:>12,.0f} ¥{needed_pnl:>+12,.0f} ¥{diff:>+12,.0f}"
    )
