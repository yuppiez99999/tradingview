# -*- coding: utf-8 -*-
"""
风控/对冲检查 — 基于修正后的 positions.json（est_price=开盘价）
"""
import json
import os
import sys
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wind_mcp_fetcher import wind_get_quote

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSITIONS_PATH = os.path.join(BASE_DIR, "config", "positions.json")


def to_wind_code(code: str, item: dict):
    kind = item.get("type", "个股")
    if kind in ("ETF", "商品"):
        if code.startswith("51") or code.startswith("58"):
            return code + ".SH", True
        if code.startswith("15") or code.startswith("16"):
            return code + ".SZ", True
        return code + ".SH", True
    if code.startswith("6") or code.startswith("5"):
        return code + ".SH", False
    return code + ".SZ", False


def fetch_prices(positions: dict) -> dict:
    result = {}
    for key, item in positions.items():
        code = item.get("code")
        windcode, is_fund = to_wind_code(code, item)
        try:
            quote = wind_get_quote(windcode, is_fund=is_fund)
        except Exception as e:
            print(f"[WARN] {code} 获取行情失败: {e}")
            continue
        if not quote:
            print(f"[WARN] {code} 无行情返回")
            continue
        price = quote.get("price") or quote.get("close") or quote.get("last")
        if price is None:
            print(f"[WARN] {code} 无最新价")
            continue
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        result[code] = {
            "price": price,
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "prev_close": quote.get("prev_close"),
            "source": quote.get("source", "unknown"),
        }
    return result


def main():
    print("=" * 80)
    print("风控/对冲检查 — 基于开盘价成本")
    print("=" * 80)

    with open(POSITIONS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    positions = data.get("positions", {})
    meta = data.get("meta", {})
    total_capital = meta.get("total_capital", 0)
    day_capital = meta.get("day_capital", 0)

    print(f"总资金: {total_capital:,.0f}")
    print(f"当日建仓金额(旧): {day_capital:,.0f}")
    print(f"标的数: {len(positions)}")
    print()

    prices = fetch_prices(positions)
    print(f"成功获取价格: {len(prices)}/{len(positions)}")
    print()

    rows = []
    total_cost = 0.0
    total_value = 0.0
    style_values = defaultdict(float)
    single_values = {}

    for key, item in positions.items():
        code = item.get("code")
        name = item.get("name", "")
        shares = item.get("phase1_shares") or item.get("total_shares") or item.get("shares", 0)
        cost_price = item.get("est_price", 0.0)
        cost = item.get("phase1_amount", 0.0)
        stop_loss = item.get("stop_loss", -0.08)
        style = item.get("style", "其他")
        risk = item.get("risk", "中")
        q = prices.get(code)
        current_price = q["price"] if q else None

        position_value = shares * current_price if current_price is not None else 0.0
        pnl = position_value - cost
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0
        deviation = None
        if current_price is not None and cost_price > 0:
            deviation = (current_price - cost_price) / cost_price

        total_cost += cost
        total_value += position_value
        style_values[style] += position_value
        single_values[code] = position_value

        rows.append({
            "code": code,
            "name": name,
            "style": style,
            "risk": risk,
            "shares": shares,
            "cost_price": cost_price,
            "cost": cost,
            "current_price": current_price,
            "position_value": position_value,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "deviation": deviation,
            "stop_loss": stop_loss,
            "source": q["source"] if q else "N/A",
        })

    # ===== 1. 持仓盈亏总览 =====
    print("=" * 80)
    print("一、持仓盈亏总览")
    print("=" * 80)
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0
    print(f"总成本:     {total_cost:>12,.0f}")
    print(f"总市值:     {total_value:>12,.0f}")
    print(f"总盈亏:     {total_pnl:>+12,.0f}")
    print(f"总盈亏率:   {total_pnl_pct:>+11.2f}%")
    print()

    # ===== 2. 逐标的风控 =====
    print("=" * 80)
    print("二、逐标的风控检查")
    print("=" * 80)
    header = f"{'代码':12s} {'名称':12s} {'风格':8s} {'风险':4s} {'成本价':>8s} {'现价':>8s} {'偏离':>8s} {'盈亏率':>8s} {'止损距':>8s} {'状态'}"
    print(header)
    print("-" * 110)

    alerts = []
    for r in rows:
        if r["current_price"] is None:
            status = "价格缺失"
            alerts.append((r["code"], r["name"], "价格缺失"))
        else:
            dev_str = f"{r['deviation']*100:>+7.2f}%" if r["deviation"] is not None else "  N/A  "
            stop_distance = (r["current_price"] * (1 + r["stop_loss"]) - r["cost_price"]) / r["cost_price"] if r["cost_price"] > 0 else 0
            stop_str = f"{stop_distance*100:>7.2f}%"

            if r["pnl_pct"] <= r["stop_loss"] * 100:
                status = "触发止损"
                alerts.append((r["code"], r["name"], f"触发止损 {r['pnl_pct']:.2f}%"))
            elif abs(r["deviation"] or 0) > 0.15:
                status = "价格偏离>15%"
                alerts.append((r["code"], r["name"], f"价格偏离 {r['deviation']*100:.1f}%"))
            elif r["pnl_pct"] < -5:
                status = "浮亏>5%"
                alerts.append((r["code"], r["name"], f"浮亏 {r['pnl_pct']:.2f}%"))
            else:
                status = "正常"

            print(f"{r['code']:12s} {r['name']:12s} {r['style']:8s} {r['risk']:4s} "
                  f"{r['cost_price']:>8.3f} {r['current_price']:>8.3f} {dev_str} "
                  f"{r['pnl_pct']:>+7.2f}% {stop_str} {status}")

    print()
    if alerts:
        print("告警:")
        for a in alerts:
            print(f"  - {a[0]} {a[1]}: {a[2]}")
    else:
        print("告警: 无")

    # ===== 3. 风格集中度 =====
    print()
    print("=" * 80)
    print("三、风格集中度")
    print("=" * 80)
    total_style = sum(style_values.values()) or 1
    for style, value in sorted(style_values.items(), key=lambda x: -x[1]):
        pct = value / total_style * 100
        bar = "#" * int(pct / 2)
        print(f"  {style:10s} {value:>12,.0f} {pct:>6.1f}% {bar}")
    print(f"  {'合计':10s} {total_style:>12,.0f} {'100.0%':>8s}")
    print()

    # 检查集中度告警
    style_alerts = []
    for style, value in style_values.items():
        pct = value / total_style
        if pct > 0.60:
            style_alerts.append(f"{style} 占比 {pct*100:.1f}% > 60%")
        if pct > 0.65:
            style_alerts.append(f"{style} 占比 {pct*100:.1f}% > 65%，建议启动风格对冲")

    if style_alerts:
        print("风格集中度告警:")
        for msg in style_alerts:
            print(f"  - {msg}")
    else:
        print("风格集中度: 正常")

    # ===== 4. 个股集中度 =====
    print()
    print("=" * 80)
    print("四、个股集中度 (Top 10)")
    print("=" * 80)
    total_single = sum(single_values.values()) or 1
    for code, value in sorted(single_values.items(), key=lambda x: -x[1])[:10]:
        name = next((r["name"] for r in rows if r["code"] == code), code)
        pct = value / total_single * 100
        bar = "#" * int(pct / 2)
        print(f"  {code} {name:10s} {value:>12,.0f} {pct:>6.1f}% {bar}")

    single_alerts = []
    for code, value in single_values.items():
        pct = value / total_single
        if pct > 0.15:
            name = next((r["name"] for r in rows if r["code"] == code), code)
            single_alerts.append(f"{name}({code}) 占比 {pct*100:.1f}% > 15%")

    if single_alerts:
        print()
        print("个股集中度告警:")
        for msg in single_alerts:
            print(f"  - {msg}")
    else:
        print()
        print("个股集中度: 正常")

    # ===== 5. 对冲建议 =====
    print()
    print("=" * 80)
    print("五、对冲建议")
    print("=" * 80)

    hedge_needed = False
    hedge_reasons = []

    # 检查总浮亏
    if total_pnl_pct < -3:
        hedge_needed = True
        hedge_reasons.append(f"组合浮亏 {total_pnl_pct:.2f}% < -3%")

    # 检查风格集中度
    for style, value in style_values.items():
        pct = value / total_style
        if pct > 0.60:
            hedge_needed = True
            hedge_reasons.append(f"{style} 占比 {pct*100:.1f}% > 60%")

    # 检查个股集中度
    for code, value in single_values.items():
        pct = value / total_single
        if pct > 0.15:
            hedge_needed = True
            hedge_reasons.append(f"{code} 占比 {pct*100:.1f}% > 15%")

    # 检查止损触发数
    stop_loss_count = sum(1 for r in rows if r["current_price"] is not None and r["pnl_pct"] <= r["stop_loss"] * 100)
    if stop_loss_count > 0:
        hedge_needed = True
        hedge_reasons.append(f"{stop_loss_count} 个标的触发止损")

    if hedge_needed:
        print("建议启动/升级对冲:")
        for reason in hedge_reasons:
            print(f"  - {reason}")
        print()
        print("对冲方案:")
        print("  - Layer 1: 股指期货空单对冲 (IF/IC/IM，按Beta加权)")
        print("  - Layer 2: 科创50/创业板 ETF Put 保护")
        print("  - Layer 3: 增加黄金ETF(518880)和短融ETF(511360)防御仓位")
    else:
        print("当前暂不需要额外对冲，保持观察")
        print("监控要点:")
        print("  - 高端制造板块回撤 > 5% 时启动行业轮动对冲")
        print("  - 组合回撤 > 3% 时考虑加仓黄金/债券")
        print("  - 个股触发止损时自动减仓")

    print()
    print("=" * 80)
    print("检查完成")
    print("=" * 80)


if __name__ == "__main__":
    main()
