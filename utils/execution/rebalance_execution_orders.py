"""
再平衡执行单生成器
基于当前持仓与目标配置，生成可执行的再平衡订单

T3.6 迁移: 2026-07-27 从项目根目录迁移到 utils/execution/
- 修正硬编码的 v7.1 旧路径为 v8.4 项目根目录 (基于 __file__ 动态解析)
- 修正路径: sys.path / positions.json / 输出文件路径
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

# T3.6 修正: 动态解析项目根目录 (utils/execution/ → 项目根)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

TARGET_ALLOCATION = {
    "宽基": 0.25,
    "科技": 0.20,
    "制造": 0.20,
    "新能源": 0.10,
    "医药": 0.10,
    "银行": 0.05,
    "防御": 0.05,
    "避险": 0.05,
}

MIN_TRADE_AMOUNT = 10000
MAX_SINGLE_ORDER_AMOUNT = 200000
MIN_LOT_SIZE = 100
TARGET_TOTAL = 5_000_000.0


def load_positions():
    # T3.6 修正: 使用动态解析的项目根目录 (不再硬编码 v7.1 路径)
    path = _PROJECT_ROOT / "config" / "positions.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)["positions"]
    positions = {}
    prices = {}
    styles = {}
    for item in data.values():
        code = item.get("code")
        qty = item.get("phase1_shares") or item.get("total_shares") or item.get("shares", 0)
        price = item.get("est_price", 0.0)
        style = item.get("style", "其他")
        if code and qty:
            positions[code] = float(qty)
            prices[code] = float(price)
            styles[code] = style
    return positions, prices, styles


def classify_style(style_map: dict) -> Dict[str, Dict[str, Any]]:
    style_allocation: Dict[str, Dict[str, Any]] = {}
    for code, style in style_map.items():
        if style not in style_allocation:
            style_allocation[style] = {"amount": 0.0, "codes": []}
        style_allocation[style]["codes"].append(code)
    return style_allocation


def calc_current_allocation(positions: dict, prices: dict, style_map: dict) -> dict:
    total = sum(positions.get(s, 0) * prices.get(s, 0.0) for s in positions)
    style_allocation = classify_style(style_map)
    for _style, info in style_allocation.items():
        amount = sum(positions.get(s, 0) * prices.get(s, 0.0) for s in info["codes"])
        info["amount"] = amount
        info["weight"] = amount / total if total > 0 else 0.0
    return style_allocation


def validate_order(code: str, action: str, shares: int, price: float, positions: dict) -> dict:
    est_amount = shares * price
    errors = []
    warnings = []

    if est_amount < MIN_TRADE_AMOUNT:
        errors.append(f"金额不足 {MIN_TRADE_AMOUNT} 元")

    if est_amount > MAX_SINGLE_ORDER_AMOUNT:
        warnings.append(f"单笔金额超过 {MAX_SINGLE_ORDER_AMOUNT} 元")

    if shares % MIN_LOT_SIZE != 0:
        errors.append(f"数量不是 {MIN_LOT_SIZE} 的倍数")

    if action == "SELL":
        current_qty = positions.get(code, 0)
        if shares > current_qty:
            errors.append(f"卖出数量超过持仓: 持仓={current_qty}, 卖出={shares}")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "est_amount": est_amount,
    }


def generate_rebalance_orders(style_allocation: dict, target_allocation: dict, positions: dict, prices: dict) -> list:
    orders = []

    for style, target_weight in target_allocation.items():
        info = style_allocation.get(style, {"amount": 0.0, "weight": 0.0, "codes": []})
        current_weight = info["weight"]
        target_amount = TARGET_TOTAL * target_weight
        current_amount = info["amount"]
        gap = current_amount - target_amount

        if abs(gap) < MIN_TRADE_AMOUNT:
            continue

        action = "SELL" if gap > 0 else "BUY"
        remaining_gap = abs(gap)

        for code in info["codes"]:
            if code not in prices or code not in positions:
                continue

            price = prices[code]
            if price <= 0:
                continue

            max_shares_for_code = int(MAX_SINGLE_ORDER_AMOUNT / price / MIN_LOT_SIZE) * MIN_LOT_SIZE
            needed_shares = int(remaining_gap / price / MIN_LOT_SIZE) * MIN_LOT_SIZE
            qty = min(max_shares_for_code, needed_shares)

            if qty == 0:
                continue

            validation = validate_order(code, action, qty, price, positions)

            orders.append(
                {
                    "style": style,
                    "code": code,
                    "action": action,
                    "order_type": "LIMIT",
                    "shares": qty,
                    "est_price": price,
                    "est_amount": validation["est_amount"],
                    "target_weight": target_weight,
                    "current_weight": current_weight,
                    "gap": gap,
                    "validation": validation,
                }
            )

            remaining_gap -= validation["est_amount"]
            if remaining_gap < MIN_TRADE_AMOUNT:
                break

    orders.sort(key=lambda o: abs(o["gap"]), reverse=True)
    return orders


def build_report(style_allocation: dict, target_allocation: dict, orders: list) -> dict:
    total = sum(style_allocation[s]["amount"] for s in style_allocation)
    valid_orders = [o for o in orders if o["validation"]["valid"]]
    report = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_value": total,
        "style_allocation": {
            s: {"amount": style_allocation[s]["amount"], "weight": style_allocation[s]["weight"]}
            for s in style_allocation
        },
        "target_allocation": target_allocation,
        "orders": orders,
        "summary": {
            "total_orders": len(orders),
            "valid_orders": len(valid_orders),
            "buy_orders": sum(1 for o in orders if o["action"] == "BUY"),
            "sell_orders": sum(1 for o in orders if o["action"] == "SELL"),
            "total_trade_value": sum(abs(o["est_amount"]) for o in valid_orders),
            "min_trade_amount": MIN_TRADE_AMOUNT,
            "max_single_order": MAX_SINGLE_ORDER_AMOUNT,
        },
    }
    return report


def main():
    positions, prices, styles = load_positions()
    style_allocation = calc_current_allocation(positions, prices, styles)
    orders = generate_rebalance_orders(style_allocation, TARGET_ALLOCATION, positions, prices)
    report = build_report(style_allocation, TARGET_ALLOCATION, orders)

    print("=" * 70)
    print("再平衡执行单")
    print("=" * 70)
    print(f"日期: {report['date']}")
    print(f"组合总市值: {report['total_value']:,.0f}")
    print()
    print(f"{'风格':10s} {'当前权重':>10s} {'目标权重':>10s} {'偏差':>10s}")
    print("-" * 70)
    for style in sorted(set(list(style_allocation.keys()) + list(TARGET_ALLOCATION.keys()))):
        current = style_allocation.get(style, {}).get("weight", 0.0)
        target = TARGET_ALLOCATION.get(style, 0.0)
        deviation = current - target
        status = "✅" if abs(deviation) < 0.02 else "⚠️"
        print(f"{style:10s} {current:>10.2%} {target:>10.2%} {deviation:>+10.2%} {status}")
    print("-" * 70)
    print()
    print(f"订单数: {report['summary']['total_orders']} (有效 {report['summary']['valid_orders']})")
    print(f"买入: {report['summary']['buy_orders']} | 卖出: {report['summary']['sell_orders']}")
    print(f"总交易金额: {report['summary']['total_trade_value']:,.0f}")
    print(f"单笔限额: {MIN_TRADE_AMOUNT:,} ~ {MAX_SINGLE_ORDER_AMOUNT:,} 元")
    print()

    for i, o in enumerate(orders, 1):
        status = "✅" if o["validation"]["valid"] else "❌"
        print(f"[{i}] {status} {o['action']} | {o['code']} | {o['style']}")
        print(f"    数量: {o['shares']} | 预估金额: {o['est_amount']:,.0f}")
        print(f"    当前权重: {o['current_weight']:.2%} | 目标权重: {o['target_weight']:.2%}")
        if o["validation"]["warnings"]:
            print(f"    警告: {'; '.join(o['validation']['warnings'])}")
        if not o["validation"]["valid"]:
            print(f"    错误: {'; '.join(o['validation']['errors'])}")
    print("=" * 70)

    # T3.6 修正: 输出路径使用项目根目录的 reports/
    out_path = _PROJECT_ROOT / "reports" / f"rebalance_execution_orders_{datetime.now():%Y%m%d}.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"已保存: {out_path}")


if __name__ == "__main__":
    main()
