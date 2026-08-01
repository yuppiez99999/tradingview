# -*- coding: utf-8 -*-
"""
组合优化脚本 - 调整行业配置以满足8%年化和15%回撤约束
"""

import json

TARGET_TOTAL = 4000000

TARGET_WEIGHTS = {
    '防御': 0.32,
    '宽基': 0.24,
    '医药': 0.12,
    '金融': 0.10,
    '科技': 0.12,
    '资源': 0.05,
    '新能源': 0.03,
    '制造': 0.01,
    '顺周期': 0.01,
    '成长': 0.0,
}


def load_positions():
    # B1.7: 委托给 utils.positions_loader 统一入口
    from utils.positions_loader import load_positions as _load
    return _load('config/positions.json')


def save_positions(positions):
    with open('config/positions.json', 'w', encoding='utf-8') as f:
        json.dump(positions, f, ensure_ascii=False, indent=2)


def main():
    positions = load_positions()

    sector_current = {}
    sector_positions = {}

    for code, pos in positions['positions'].items():
        sector = pos.get('sector', '其他')
        if sector not in sector_current:
            sector_current[sector] = 0
            sector_positions[sector] = []
        sector_current[sector] += pos.get('amount', 0)
        sector_positions[sector].append((code, pos))

    print("当前行业分布:")
    for sector, amount in sector_current.items():
        print(f"  {sector}: {amount} ({amount/4500000*100:.1f}%)")
    print(f"  总计: {sum(sector_current.values())}")

    print("\n目标行业分布:")
    target_amounts = {}
    for sector, weight in TARGET_WEIGHTS.items():
        target_amounts[sector] = int(weight * TARGET_TOTAL)
        print(f"  {sector}: {target_amounts[sector]} ({weight*100:.1f}%)")
    print(f"  总计: {TARGET_TOTAL}")

    print("\n调整方案:")
    for sector in sector_positions:
        if sector not in TARGET_WEIGHTS:
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
                pos['amount'] = int(pos.get('amount', 0) + avg_increase)
                pos['target_weight'] = round(pos['amount'] / TARGET_TOTAL, 4)
        else:
            total_current = sum(p[1].get('amount', 0) for p in positions_in_sector)
            for _code, pos in positions_in_sector:
                ratio = pos.get('amount', 0) / total_current if total_current > 0 else 0
                new_amount = int(target * ratio)
                pos['amount'] = new_amount
                pos['target_weight'] = round(new_amount / TARGET_TOTAL, 4)

    sector_after = {}
    for _code, pos in positions['positions'].items():
        sector = pos.get('sector', '其他')
        if sector not in sector_after:
            sector_after[sector] = 0
        sector_after[sector] += pos.get('amount', 0)

    print("\n调整后行业分布:")
    for sector, amount in sector_after.items():
        print(f"  {sector}: {amount} ({amount/TARGET_TOTAL*100:.1f}%)")
    print(f"  总计: {sum(sector_after.values())}")

    save_positions(positions)
    print("\n配置已保存到 positions.json")


if __name__ == '__main__':
    main()
