"""补足 Covered Call 备兑担保并同步 positions.json

功能:
- 510300.SH: 按实际持仓补足到 50000 份（默认补 2000）
- 510050.SH: 自动检查是否 >=50000 份，不足时提示补仓数量
- 写回前自动备份 positions.json 为 .bak
- 输出更新摘要，便于核对
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
POSITIONS_FILE = BASE_DIR / "config" / "positions.json"

# 1 张 Covered Call = 5 手 = 50000 份
COVERED_CALL_COLLATERAL = 50000


def _backup_positions(path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(path.suffix + f".bak_{ts}")
    shutil.copy2(path, backup)
    return backup


def _recalc_avg_cost(old_shares, old_avg_cost, topup_shares, topup_price):
    if topup_shares <= 0:
        return old_avg_cost
    old_total = old_shares * old_avg_cost
    new_total = old_total + topup_shares * topup_price
    new_shares = old_shares + topup_shares
    return new_total / new_shares


def sync_510300(topup_shares: int = 2000, topup_price: float | None = None):
    with open(POSITIONS_FILE, encoding="utf-8") as f:
        data = json.load(f)

    pos = data["positions"]["510300.SH"]
    old_shares = int(pos["shares"])
    old_avg_cost = float(pos["avg_cost"])
    est_price = float(pos["est_price"]) if topup_price is None else float(topup_price)

    if topup_shares <= 0:
        return {
            "code": "510300.SH",
            "status": "skipped",
            "reason": "topup_shares <= 0",
        }

    new_shares = old_shares + topup_shares
    new_avg_cost = _recalc_avg_cost(old_shares, old_avg_cost, topup_shares, est_price)

    pos["shares"] = int(new_shares)
    pos["amount"] = round(new_shares * pos["est_price"], 2)
    pos["avg_cost"] = round(float(new_avg_cost), 4)
    pos["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pos["price_source"] = "wind_mcp"

    return {
        "code": "510300.SH",
        "status": "updated",
        "old_shares": old_shares,
        "topup_shares": topup_shares,
        "new_shares": int(new_shares),
        "old_avg_cost": old_avg_cost,
        "new_avg_cost": round(float(new_avg_cost), 4),
        "topup_price": est_price,
        "collateral_status": (
            "sufficient" if new_shares >= COVERED_CALL_COLLATERAL else "insufficient"
        ),
        "data": data,  # 修复: 返回修改后的持仓数据, 供 main() 落盘 (此前 main 引用未定义的 data 抛 NameError)
    }


def check_510050_collateral():
    with open(POSITIONS_FILE, encoding="utf-8") as f:
        data = json.load(f)

    pos = data["positions"]["510050.SH"]
    system_shares = int(pos["shares"])
    est_price = float(pos["est_price"])
    gap = max(COVERED_CALL_COLLATERAL - system_shares, 0)

    return {
        "code": "510050.SH",
        "system_shares": system_shares,
        "required_shares": COVERED_CALL_COLLATERAL,
        "gap": gap,
        "est_price": est_price,
        "estimated_topup_cost": round(gap * est_price, 2) if gap > 0 else 0.0,
        "is_sufficient": system_shares >= COVERED_CALL_COLLATERAL,
    }


def main():
    print("=" * 60)
    print("Covered Call 备兑担保同步工具")
    print("=" * 60)

    backup = _backup_positions(POSITIONS_FILE)
    print(f"备份文件: {backup}\n")

    r510300 = sync_510300(topup_shares=2000)
    data = r510300.get("data")
    if data is None:
        # topup_shares<=0 早退分支未返回 data, 重新加载原始持仓避免落盘覆盖
        with open(POSITIONS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    print(f"[{r510300['code']}]")
    print(
        f"  原持仓: {r510300.get('old_shares')} -> 补仓: {r510300.get('topup_shares')} -> 新持仓: {r510300.get('new_shares')}"  # noqa: E501
    )
    print(f"  avg_cost: {r510300.get('old_avg_cost')} -> {r510300.get('new_avg_cost')}")
    print(f"  补仓价: {r510300.get('topup_price')}")
    print(
        f"  备兑状态: {r510300.get('collateral_status')} (要求 {COVERED_CALL_COLLATERAL} 份)\n"
    )

    r510050 = check_510050_collateral()
    print(f"[{r510050['code']}]")
    print(f"  系统持仓: {r510050['system_shares']}")
    print(f"  备兑要求: {r510050['required_shares']}")
    print(f"  缺口:     {r510050['gap']}")
    print(f"  是否满足: {'是' if r510050['is_sufficient'] else '否'}")
    if not r510050["is_sufficient"]:
        print(
            f"  建议: 补仓 {r510050['gap']} 份，预估成本 {r510050['estimated_topup_cost']} 元"
        )
    print()

    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("positions.json 已更新。")


if __name__ == "__main__":
    main()
