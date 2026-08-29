"""
添加国债ETF配置 - 降低组合Beta至0.65以下
"""

import json

TARGET_TOTAL = 4000000

NEW_TARGET_WEIGHTS = {
    "防御": 0.22,
    "国债": 0.25,
    "宽基": 0.15,
    "医药": 0.08,
    "金融": 0.08,
    "科技": 0.06,
    "资源": 0.05,
    "新能源": 0.02,
    "制造": 0.02,
    "顺周期": 0.02,
    "成长": 0.0,
}


def load_positions():
    # B1.7: 委托给 utils.positions_loader 统一入口
    from utils.positions_loader import load_positions as _load

    return _load("config/positions.json")


def save_positions(positions):
    with open("config/positions.json", "w", encoding="utf-8") as f:
        json.dump(positions, f, ensure_ascii=False, indent=2)


def main():
    positions = load_positions()

    sector_current = {}
    sector_positions = {}

    for code, pos in positions["positions"].items():
        sector = pos.get("sector", "其他")
        amount = pos.get("amount", 0)
        if sector not in sector_current:
            sector_current[sector] = 0
            sector_positions[sector] = []
        sector_current[sector] += amount
        sector_positions[sector].append((code, pos))

    print("当前行业分布:")
    total_current = sum(sector_current.values())
    for sector, amount in sector_current.items():
        print(f"  {sector}: {amount} ({amount/total_current*100:.1f}%)")
    print(f"  总计: {total_current}")

    print("\n目标行业分布:")
    target_amounts = {}
    for sector, weight in NEW_TARGET_WEIGHTS.items():
        target_amounts[sector] = int(weight * TARGET_TOTAL)
        print(f"  {sector}: {target_amounts[sector]} ({weight*100:.1f}%)")
    print(f"  总计: {TARGET_TOTAL}")

    print("\n调整方案:")
    for sector in sector_positions:
        if sector not in NEW_TARGET_WEIGHTS:
            continue

        current = sector_current[sector]
        target = target_amounts[sector]
        diff = target - current

        if diff == 0:
            continue

        print(f"  {sector}: {current} → {target} ({'+' if diff > 0 else ''}{diff})")

        positions_in_sector = sector_positions[sector]
        if diff > 0:
            avg_increase = diff / len(positions_in_sector)
            for _code, pos in positions_in_sector:
                pos["amount"] = int(pos.get("amount", 0) + avg_increase)
                pos["target_weight"] = round(pos["amount"] / TARGET_TOTAL, 4)
        else:
            total_sector = sum(p[1].get("amount", 0) for p in positions_in_sector)
            if total_sector > 0:
                for _code, pos in positions_in_sector:
                    ratio = pos.get("amount", 0) / total_sector
                    new_amount = int(target * ratio)
                    pos["amount"] = new_amount
                    pos["target_weight"] = round(new_amount / TARGET_TOTAL, 4)
            else:
                for _code, pos in positions_in_sector:
                    pos["amount"] = 0
                    pos["target_weight"] = 0

    treasury_amount = target_amounts.get("国债", 0)
    if treasury_amount > 0:
        if "511010.SH" in positions["positions"]:
            positions["positions"]["511010.SH"]["amount"] = treasury_amount
            positions["positions"]["511010.SH"]["target_weight"] = round(
                treasury_amount / TARGET_TOTAL, 4
            )
            positions["positions"]["511010.SH"]["shares"] = int(treasury_amount / 100)
            print(
                f"\n更新: 上证5年期国债ETF (511010.SH): {treasury_amount} ({treasury_amount/TARGET_TOTAL*100:.1f}%)"
            )
        else:
            treasury_etf = {
                "code": "511010.SH",
                "name": "上证5年期国债ETF",
                "shares": int(treasury_amount / 100),
                "phase1_shares": 0,
                "total_shares": 0,
                "est_price": 100.0,
                "avg_cost": 100.0,
                "target_weight": round(treasury_amount / TARGET_TOTAL, 4),
                "amount": treasury_amount,
                "style": "国债",
                "sector": "国债",
                "type": "ETF",
                "etf_flow_signal": "中性",
                "etf_inflow": 0.0,
                "reason": "国债ETF+低Beta0.1+稳定票息; 降低组合波动率; 熊市保护",
            }
            positions["positions"]["511010.SH"] = treasury_etf
            print(
                f"\n新增: 上证5年期国债ETF (511010.SH): {treasury_amount} ({treasury_amount/TARGET_TOTAL*100:.1f}%)"
            )

    sector_after = {}
    for _code, pos in positions["positions"].items():
        sector = pos.get("sector", "其他")
        if sector not in sector_after:
            sector_after[sector] = 0
        sector_after[sector] += pos.get("amount", 0)

    print("\n调整后行业分布:")
    total_after = sum(sector_after.values())
    for sector, amount in sector_after.items():
        print(f"  {sector}: {amount} ({amount/total_after*100:.1f}%)")
    print(f"  总计: {total_after}")

    sector_beta = {
        "科技": 1.3,
        "医药": 1.1,
        "宽基": 1.0,
        "金融": 0.9,
        "新能源": 1.4,
        "资源": 0.8,
        "防御": 0.5,
        "制造": 1.1,
        "顺周期": 1.0,
        "成长": 1.2,
        "国债": 0.1,
        "其他": 1.0,
    }

    portfolio_beta = sum(
        sector_after.get(sector, 0) / total_after * sector_beta.get(sector, 1.0)
        for sector in sector_after
    )
    print(f"\n组合Beta: {portfolio_beta:.2f}")

    save_positions(positions)
    print("\n配置已保存到 positions.json")


if __name__ == "__main__":
    main()
