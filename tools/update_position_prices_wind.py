"""
持仓实时价格更新器 - Wind MCP 直连版
功能：
1. 直接从 Wind MCP 获取实时价格
2. 更新 positions.json 中的 est_price
3. 计算持仓盈亏
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from utils.datetime_utils import now_bj

# S3修复: 用 PROJECT_ROOT 替代硬编码绝对路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from wind_mcp_fetcher import wind_get_quote

POSITIONS_PATH = str(PROJECT_ROOT / "config" / "positions.json")


def to_wind_code(code: str):
    code = str(code).strip()
    for prefix in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
        if code.startswith(prefix):
            code = code[len(prefix) :]
            break
    for suffix in (".SH", ".SZ", ".BJ", ".sh", ".sz", ".bj"):
        if code.endswith(suffix):
            code = code[: -len(suffix)]
            break
    if code.startswith(("51", "58")):
        return f"{code}.SH", True
    if code.startswith(("15", "16")):
        return f"{code}.SZ", True
    if code.startswith(("00", "30")):
        return f"{code}.SZ", False
    if code.startswith("6"):
        return f"{code}.SH", False
    if code.startswith(("4", "8")):
        return f"{code}.BJ", False
    return f"{code}.SH", False


def load_positions():
    # B1.7: 委托给 utils.positions_loader 统一入口
    from utils.positions_loader import load_positions as _load

    return _load(POSITIONS_PATH)


def save_positions(data):
    with open(POSITIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def update_prices():
    print("=" * 70)
    print("持仓实时价格更新器 - Wind MCP 直连")
    print("=" * 70)
    print(f'运行时间: {now_bj().strftime("%Y-%m-%d %H:%M:%S")}')
    print()

    data = load_positions()
    positions = data.get("positions", {})
    meta = data.get("meta", {})

    print(f"持仓标的数: {len(positions)}")
    print(f'总资金: {meta.get("total_capital", 0):,.0f}')
    print(f'已建仓: {meta.get("day_capital", 0):,.0f}')
    print()

    update_count = 0
    fail_count = 0
    price_changes = []

    print("-" * 70)
    print(
        f'{"代码":12s} {"名称":12s} {"旧价格":>8s} {"新价格":>8s} {"变化":>8s} {"持仓金额":>12s} {"来源":20s}'
    )
    print("-" * 70)

    for _key, item in positions.items():
        code = item.get("code")
        old_price = item.get("est_price", 0.0)
        shares = (
            item.get("phase1_shares")
            or item.get("total_shares")
            or item.get("shares", 0)
        )

        if not code or not shares:
            continue

        wind_code, is_fund = to_wind_code(code)
        try:
            quote = wind_get_quote(wind_code, is_fund=is_fund)
        except Exception as e:
            quote = None
            fail_count += 1
            print(f'{code:12s} {item.get("name", ""):12s} Wind MCP 调用异常: {e}')
            continue

        if not quote or quote.get("price") is None:
            fail_count += 1
            print(
                f'{code:12s} {item.get("name", ""):12s} 获取价格失败: Wind MCP 返回空数据'
            )
            continue

        real_time_price = float(quote["price"])
        if real_time_price <= 0:
            fail_count += 1
            print(
                f'{code:12s} {item.get("name", ""):12s} 获取价格失败: Wind MCP 价格非正'
            )
            continue

        new_price = real_time_price
        item["est_price"] = new_price
        item["last_update"] = now_bj().isoformat()
        item["price_source"] = "wind_mcp"

        change_pct = (
            ((new_price - old_price) / old_price * 100) if old_price > 0 else 0.0
        )
        position_value = shares * new_price

        print(
            f'{code:12s} {item.get("name", ""):12s} '
            f"{old_price:>8.2f} {new_price:>8.2f} "
            f'{change_pct:>+7.2f}% {position_value:>12,.0f} {"wind_mcp":20s}'
        )

        price_changes.append(
            {
                "code": code,
                "name": item.get("name", ""),
                "old_price": old_price,
                "new_price": new_price,
                "change_pct": change_pct,
                "position_value": position_value,
                "source": "wind_mcp",
            }
        )
        update_count += 1

    print("-" * 70)
    print(f"更新成功: {update_count}, 失败: {fail_count}")
    print()

    total_value = sum(
        item.get("phase1_shares", 0) * item.get("est_price", 0.0)
        for item in positions.values()
    )

    total_cost = meta.get("day_capital", 0.0)
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0

    print("=" * 70)
    print("持仓概览")
    print("=" * 70)
    print(f"总持仓市值: {total_value:>12,.0f}")
    print(f"总成本:     {total_cost:>12,.0f}")
    print(f"总盈亏:     {total_pnl:>+12,.0f}")
    print(f"总盈亏率:   {total_pnl_pct:>+11.2f}%")
    print()

    style_stats = {}
    for item in positions.values():
        style = item.get("style", "其他")
        value = item.get("phase1_shares", 0) * item.get("est_price", 0.0)
        style_stats[style] = style_stats.get(style, 0) + value

    print("-" * 70)
    print(f'{"风格":12s} {"市值":>12s} {"占比":>8s}')
    print("-" * 70)
    for style, value in sorted(style_stats.items(), key=lambda x: x[1], reverse=True):
        pct = (value / total_value * 100) if total_value > 0 else 0.0
        print(f"{style:12s} {value:>12,.0f} {pct:>7.1f}%")
    print("-" * 70)
    print(f'{"合计":12s} {total_value:>12,.0f} {"100.0%":>8s}')
    print()

    save_positions(data)
    print("持仓数据已保存到: config/positions.json")
    print()

    report = {
        "update_time": now_bj().isoformat(),
        "update_count": update_count,
        "fail_count": fail_count,
        "total_value": total_value,
        "total_cost": total_cost,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "price_changes": price_changes,
        "style_stats": {k: v for k, v in style_stats.items()},
        "source": "wind_mcp",
    }

    report_dir = str(PROJECT_ROOT / "reports")
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(
        report_dir, f'price_update_{now_bj().strftime("%Y%m%d_%H%M%S")}_wind.json'
    )
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"价格更新报告: {report_path}")
    print()
    print("=" * 70)
    print("更新完成")
    print("=" * 70)

    return report


if __name__ == "__main__":
    update_prices()
