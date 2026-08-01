#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""持仓盈亏明细 — 今日收盘价版 (2026-07-29).

数据源: AKShare (P3, 免费回退)
  - stock_zh_a_spot_em: A 股实时行情 (收盘后=收盘价)
  - fund_etf_spot_em: ETF 实时行情
NO_PROXY 必须在 import akshare 前设置 (项目硬约束: 系统代理拒绝国内金融 API).
"""
import os

# NO_PROXY 必须在 import akshare/requests 前 (项目硬约束)
os.environ['NO_PROXY'] = 'push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,sinajs.cn,sina.com.cn,api.dataide.eastmoney.com'
os.environ['no_proxy'] = os.environ['NO_PROXY']

import json
import sys
import time
from collections import defaultdict
from datetime import datetime

BASE = r'e:\各种PY程序\28-终极量化交易系统8.4'
sys.path.insert(0, BASE)

# 加载持仓
with open(os.path.join(BASE, 'config', 'positions.json'), 'r', encoding='utf-8') as f:
    data = json.load(f)

positions = data['positions']
meta = data.get('meta', {})
total_capital = float(meta.get('total_capital', 0))

print("=" * 100)
print(f"  持仓盈亏明细 (今日收盘价)  |  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  总资金: {total_capital:,.0f}")
print("=" * 100)

# ---------- 获取今日价格 ----------
price_map = {}  # {6位代码: (price, name, source)}
data_source = "未知"

try:
    import akshare as ak
    print(f"[数据源] akshare {ak.__version__} 加载中...")
    t0 = time.time()

    # A 股实时行情 (含股票)
    try:
        stock_df = ak.stock_zh_a_spot_em()
        for _, row in stock_df.iterrows():
            code = str(row['代码']).zfill(6)
            try:
                price = float(row['最新价'])
                if price > 0:
                    price_map[code] = (price, str(row.get('名称', '')), 'akshare_stock')
            except (ValueError, TypeError):
                continue
        print(f"  [OK] A 股行情: {len(price_map)} 个标的")
    except Exception as e:
        print(f"  [FAIL] A 股行情获取失败: {e}")

    # ETF 实时行情
    etf_count_before = len(price_map)
    try:
        etf_df = ak.fund_etf_spot_em()
        for _, row in etf_df.iterrows():
            code = str(row['代码']).zfill(6)
            try:
                price = float(row['最新价'])
                if price > 0:
                    price_map[code] = (price, str(row.get('名称', '')), 'akshare_etf')
            except (ValueError, TypeError):
                continue
        print(f"  [OK] ETF 行情: +{len(price_map) - etf_count_before} 个标的")
    except Exception as e:
        print(f"  [FAIL] ETF 行情获取失败: {e}")

    data_source = f"akshare ({time.time()-t0:.1f}s)"
    print(f"[数据源] 完成, 共 {len(price_map)} 个标的价格, 耗时 {time.time()-t0:.1f}s")

except Exception as e:
    print(f"[数据源] akshare 加载失败: {e}")
    import traceback; traceback.print_exc()

# ---------- 匹配持仓并计算盈亏 ----------
rows = []
missing = []
for key, p in positions.items():
    code_full = p.get('code', key)
    code6 = code_full.split('.')[0]  # "600276.SH" -> "600276"
    shares = float(p.get('shares', 0) or 0)
    avg_cost = float(p.get('avg_cost', 0) or 0)
    old_price = float(p.get('est_price', 0) or 0)

    if shares <= 0:
        continue  # 跳过未建仓

    if code6 in price_map and price_map[code6][0] > 0:
        new_price, ak_name, src = price_map[code6]
    else:
        missing.append((code_full, p.get('name', '')))
        if old_price > 0:
            new_price, ak_name, src = old_price, p.get('name', ''), '旧快照(7/22)'
        else:
            continue

    market_value = shares * new_price
    cost_value = shares * avg_cost
    pnl = market_value - cost_value
    pnl_pct = (pnl / cost_value * 100) if cost_value > 0 else 0.0
    day_change = ((new_price - old_price) / old_price * 100) if old_price > 0 else 0.0

    rows.append({
        'code': code_full, 'code6': code6,
        'name': p.get('name', ak_name), 'type': p.get('type', ''),
        'style': p.get('style', p.get('sector', '')),
        'shares': int(shares), 'avg_cost': avg_cost,
        'new_price': new_price, 'old_price': old_price,
        'market_value': market_value, 'cost_value': cost_value,
        'pnl': pnl, 'pnl_pct': pnl_pct, 'day_change': day_change,
        'src': src,
    })

rows.sort(key=lambda r: r['pnl'])

# 汇总
total_mv = sum(r['market_value'] for r in rows)
total_cost = sum(r['cost_value'] for r in rows)
total_pnl = sum(r['pnl'] for r in rows)
total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0

# 风格分组
style_groups = defaultdict(lambda: {"mv": 0.0, "cost": 0.0, "pnl": 0.0, "count": 0})
for r in rows:
    g = style_groups[r['style']]
    g['mv'] += r['market_value']; g['cost'] += r['cost_value']
    g['pnl'] += r['pnl']; g['count'] += 1

# ---------- 输出 ----------
print()
print(f"{'代码':<12}{'名称':<14}{'类型':<6}{'风格':<8}{'持仓':>7}{'成本价':>9}{'今收':>9}{'日涨跌':>8}{'市值':>11}{'盈亏':>11}{'盈亏率':>9}{'来源'}")
print("-" * 120)
for r in rows:
    sign = "🔴" if r['pnl'] < 0 else ("🟢" if r['pnl'] > 0 else "⚪")
    day_sign = "🔺" if r['day_change'] > 0 else ("🔻" if r['day_change'] < 0 else "➖")
    src_tag = "" if r['src'].startswith('akshare') else f"[{r['src']}]"
    print(f"{r['code']:<12}{r['name']:<14}{r['type']:<6}{r['style']:<8}"
          f"{r['shares']:>7}{r['avg_cost']:>9.3f}{r['new_price']:>9.3f}"
          f"{day_sign}{r['day_change']:>+6.2f}%{r['market_value']:>11,.0f}"
          f"{r['pnl']:>+10,.0f} {sign}{r['pnl_pct']:>+6.2f}% {src_tag}")

print("-" * 120)
print(f"{'合计':<40}{'':>7}{'':>9}{'':>9}{'':>8}{total_mv:>11,.0f}{total_pnl:>+10,.0f}  {total_pnl_pct:>+6.2f}%")
print("=" * 120)

# 风格分组
print("\n📊 按风格分组:")
print(f"{'风格':<10}{'标的':>5}{'市值':>13}{'占比':>8}{'盈亏':>13}{'盈亏率':>9}")
print("-" * 60)
for style, g in sorted(style_groups.items(), key=lambda x: -x[1]['mv']):
    pct = g['mv'] / total_mv * 100 if total_mv > 0 else 0
    g_pct = (g['pnl'] / g['cost'] * 100) if g['cost'] > 0 else 0
    sign = "🔴" if g['pnl'] < 0 else "🟢"
    print(f"{style:<10}{g['count']:>5}{g['mv']:>13,.0f}{pct:>7.1f}%{g['pnl']:>+12,.0f} {sign}{g_pct:>+6.2f}%")
print("-" * 60)
print(f"{'合计':<10}{len(rows):>5}{total_mv:>13,.0f}{100.0:>7.1f}%{total_pnl:>+12,.0f}  {total_pnl_pct:>+6.2f}%")

# 资金与盈亏分布
print(f"\n💰 资金占用: {total_mv:,.0f} / {total_capital:,.0f} = {total_mv/total_capital*100:.1f}%")
print(f"   浮动盈亏: {total_pnl:+,.0f} ({total_pnl_pct:+.2f}%)")

winners = [r for r in rows if r['pnl'] > 0]
losers = [r for r in rows if r['pnl'] < 0]
print(f"\n📈 盈亏分布: 盈利 {len(winners)} 个, 亏损 {len(losers)} 个")
if winners:
    best = max(winners, key=lambda r: r['pnl_pct'])
    print(f"   最大盈利: {best['name']} {best['pnl_pct']:+.2f}%  {best['pnl']:+,.0f}")
if losers:
    worst = min(losers, key=lambda r: r['pnl_pct'])
    print(f"   最大亏损: {worst['name']} {worst['pnl_pct']:+.2f}%  {worst['pnl']:+,.0f}")

# 日内涨跌
day_winners = [r for r in rows if r['day_change'] > 0]
day_losers = [r for r in rows if r['day_change'] < 0]
print(f"\n📅 今日涨跌 (vs 7/22 快照): 涨 {len(day_winners)} 个, 跌 {len(day_losers)} 个")
if day_winners:
    best_day = max(day_winners, key=lambda r: r['day_change'])
    print(f"   今日最强: {best_day['name']} {best_day['day_change']:+.2f}%")
if day_losers:
    worst_day = min(day_losers, key=lambda r: r['day_change'])
    print(f"   今日最弱: {worst_day['name']} {worst_day['day_change']:+.2f}%")

if missing:
    print(f"\n⚠️ 未获取到今日价格 ({len(missing)} 个, 回退到 7/22 快照):")
    for code, name in missing:
        print(f"   {code} {name}")
