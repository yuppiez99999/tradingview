#!/usr/bin/env python3
"""持仓盈亏明细快照 — 基于 config/positions.json 的 est_price 计算.

价格日期: positions.json 中各标的的 last_update (默认 2026-07-22 快照).
如需实时盈亏, 先运行 tools/update_position_prices_wind.py 更新 est_price 后重算.
"""
import json
import os
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POS_PATH = os.path.join(BASE, "config", "positions.json")

with open(POS_PATH, encoding="utf-8") as f:
    data = json.load(f)

positions = data["positions"]
meta = data.get("meta", {})
total_capital = float(meta.get("total_capital", 0))

# 收集有效持仓 (shares > 0 且 est_price > 0)
rows = []
latest_price_date = None
for code, p in positions.items():
    shares = float(p.get("shares", 0) or 0)
    est_price = float(p.get("est_price", 0) or 0)
    avg_cost = float(p.get("avg_cost", 0) or 0)
    if shares <= 0 or est_price <= 0:
        continue
    market_value = shares * est_price
    cost_value = shares * avg_cost
    pnl = market_value - cost_value
    pnl_pct = (pnl / cost_value * 100) if cost_value > 0 else 0.0
    rows.append({
        "code": code,
        "name": p.get("name", ""),
        "type": p.get("type", ""),
        "style": p.get("style", p.get("sector", "")),
        "shares": int(shares),
        "avg_cost": avg_cost,
        "est_price": est_price,
        "market_value": market_value,
        "cost_value": cost_value,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "last_update": p.get("last_update", ""),
    })
    # 取最新价格日期
    lu = p.get("last_update", "")
    if lu:
        try:
            d = lu[:10]
            if latest_price_date is None or d > latest_price_date:
                latest_price_date = d
        except Exception:
            pass

# 按盈亏排序 (盈亏金额降序, 亏损在前)
rows.sort(key=lambda r: r["pnl"])

# 汇总
total_mv = sum(r["market_value"] for r in rows)
total_cost = sum(r["cost_value"] for r in rows)
total_pnl = sum(r["pnl"] for r in rows)
total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0

# 按风格分组
style_groups = defaultdict(lambda: {"mv": 0.0, "cost": 0.0, "pnl": 0.0, "count": 0})
for r in rows:
    g = style_groups[r["style"]]
    g["mv"] += r["market_value"]
    g["cost"] += r["cost_value"]
    g["pnl"] += r["pnl"]
    g["count"] += 1

# 输出
print("=" * 110)
print(f"  持仓盈亏明细  |  价格快照日期: {latest_price_date or '未知'}  |  总资金: {total_capital:,.0f}  |  持仓标的: {len(rows)} 个")
print("=" * 110)
print(f"{'代码':<12}{'名称':<14}{'类型':<6}{'风格':<8}{'持仓':>8}{'成本价':>10}{'现价':>10}{'市值':>12}{'盈亏':>12}{'盈亏率':>9}")
print("-" * 110)
for r in rows:
    pnl_sign = "🔴" if r["pnl"] < 0 else ("🟢" if r["pnl"] > 0 else "⚪")
    print(f"{r['code']:<12}{r['name']:<14}{r['type']:<6}{r['style']:<8}"
          f"{r['shares']:>8}{r['avg_cost']:>10.4f}{r['est_price']:>10.4f}"
          f"{r['market_value']:>12,.0f}{r['pnl']:>11,.0f} {pnl_sign}{r['pnl_pct']:>7.2f}%")

print("-" * 110)
print(f"{'合计':<40}{'':>8}{'':>10}{'':>10}{total_mv:>12,.0f}{total_pnl:>11,.0f}  {total_pnl_pct:>7.2f}%")
print("=" * 110)

# 风格分组
print("\n📊 按风格分组:")
print(f"{'风格':<10}{'标的数':>6}{'市值':>14}{'占比':>8}{'盈亏':>14}{'盈亏率':>9}")
print("-" * 65)
for style, g in sorted(style_groups.items(), key=lambda x: -x[1]["mv"]):
    pct = g["mv"] / total_mv * 100 if total_mv > 0 else 0
    g_pnl_pct = (g["pnl"] / g["cost"] * 100) if g["cost"] > 0 else 0
    sign = "🔴" if g["pnl"] < 0 else "🟢"
    print(f"{style:<10}{g['count']:>6}{g['mv']:>14,.0f}{pct:>7.1f}%{g['pnl']:>13,.0f} {sign}{g_pnl_pct:>7.2f}%")
print("-" * 65)
print(f"{'合计':<10}{len(rows):>6}{total_mv:>14,.0f}{100.0:>7.1f}%{total_pnl:>13,.0f}  {total_pnl_pct:>7.2f}%")

# 资金占用
print(f"\n💰 资金占用: 持仓市值 {total_mv:,.0f} / 总资金 {total_capital:,.0f} = {total_mv/total_capital*100:.1f}%")
print(f"   浮动盈亏: {total_pnl:+,.0f} ({total_pnl_pct:+.2f}%)")
print("   未建仓标的: 300308.SZ 中际旭创 (目标权重 1.5%, shares=0)")

# 盈亏分布
winners = [r for r in rows if r["pnl"] > 0]
losers = [r for r in rows if r["pnl"] < 0]
print(f"\n📈 盈亏分布: 盈利 {len(winners)} 个, 亏损 {len(losers)} 个")
if winners:
    best = max(winners, key=lambda r: r["pnl_pct"])
    print(f"   最大盈利: {best['name']} ({best['code']}) +{best['pnl_pct']:.2f}%  盈亏 {best['pnl']:+,.0f}")
if losers:
    worst = min(losers, key=lambda r: r["pnl_pct"])
    print(f"   最大亏损: {worst['name']} ({worst['code']}) {worst['pnl_pct']:.2f}%  盈亏 {worst['pnl']:+,.0f}")
