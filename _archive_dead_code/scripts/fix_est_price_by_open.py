# -*- coding: utf-8 -*-
"""
用 Wind MCP 开盘价修正 positions.json 的 est_price 与 phase1_amount。
规则：
  1. 取每个标的今日开盘价
  2. cost = 开盘价 × phase1_shares
  3. 更新 est_price = 开盘价，phase1_amount = cost
  4. 保留 phase1_shares 不变
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wind_mcp_fetcher import wind_get_quote, wind_get_batch_quotes

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSITIONS_PATH = os.path.join(BASE_DIR, "config", "positions.json")


def to_wind_code(code: str, item: dict):
    """把 positions.json 的 code 转成 windcode"""
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


def fetch_open_prices(positions: dict) -> dict:
    """批量获取开盘价，返回 {code: open}"""
    codes = []
    mapping = []
    for key, item in positions.items():
        code = item.get("code")
        windcode, is_fund = to_wind_code(code, item)
        codes.append(windcode)
        mapping.append((key, code, is_fund))

    result = {}
    for (key, code, is_fund), windcode in zip(mapping, codes):
        try:
            quote = wind_get_quote(windcode, is_fund=is_fund)
        except Exception as e:
            print(f"[WARN] {code} 获取行情失败: {e}")
            continue
        if not quote:
            print(f"[WARN] {code} 无行情返回")
            continue
        open_price = quote.get("open")
        price = quote.get("price")
        if open_price is None and price is not None:
            open_price = price
        if open_price is None:
            print(f"[WARN] {code} 无开盘价")
            continue
        try:
            open_price = float(open_price)
        except (TypeError, ValueError):
            print(f"[WARN] {code} 开盘价异常: {open_price}")
            continue
        if open_price <= 0:
            print(f"[WARN] {code} 开盘价<=0: {open_price}")
            continue
        result[code] = open_price
    return result


def main():
    print("=" * 70)
    print("按开盘价修正 est_price / phase1_amount")
    print("=" * 70)

    with open(POSITIONS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    positions = data.get("positions", {})
    if not positions:
        print("positions.json 为空")
        return

    print(f"标的数量: {len(positions)}")
    open_prices = fetch_open_prices(positions)
    print(f"成功获取开盘价: {len(open_prices)}/{len(positions)}")

    updated = []
    for key, item in positions.items():
        code = item.get("code")
        name = item.get("name", "")
        shares = item.get("phase1_shares") or item.get("total_shares") or item.get("shares", 0)
        open_price = open_prices.get(code)
        if open_price is None:
            continue
        if not shares:
            continue

        cost = round(open_price * shares, 2)
        old_est = item.get("est_price")
        old_amount = item.get("phase1_amount")

        item["est_price"] = round(open_price, 4)
        item["phase1_amount"] = cost
        item["last_update"] = datetime.now().isoformat()
        item["price_source"] = "wind_mcp_open"

        updated.append({
            "code": code,
            "name": name,
            "shares": shares,
            "old_est_price": old_est,
            "open_price": round(open_price, 4),
            "old_amount": old_amount,
            "new_amount": cost,
        })

    with open(POSITIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print()
    print("-" * 70)
    print(f"{'代码':12s} {'名称':12s} {'股数':>8s} {'旧est':>10s} {'开盘价':>10s} {'旧金额':>12s} {'新金额':>12s}")
    print("-" * 70)
    for u in updated:
        print(f"{u['code']:12s} {u['name']:12s} {u['shares']:>8,} "
              f"{u['old_est_price']:>10.4f} {u['open_price']:>10.4f} "
              f"{u['old_amount']:>12.2f} {u['new_amount']:>12.2f}")

    print("-" * 70)
    print(f"已修正 {len(updated)} 个标的")
    print(f"文件已保存: {POSITIONS_PATH}")


if __name__ == "__main__":
    main()
