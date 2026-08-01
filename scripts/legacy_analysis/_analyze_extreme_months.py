# -*- coding: utf-8 -*-
"""分析2025-08/09极端月份的收益来源 - 是集中度风险还是真实alpha"""
import json

with open('output/validation_reports/lgb_backtest_45m_regime_mild_20260724_194324.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# 找出2025-08和2025-09的详细数据
for r in data['records']:
    if r['date'] in ('2025-08-01', '2025-09-01', '2025-07-01', '2025-10-01'):
        print(f"\n=== {r['date']} (组合收益: {r['portfolio_return']*100:+.2f}%) ===")
        weights = r.get('weights', {})
        returns = r.get('returns', {})
        # 按权重排序
        sorted_items = sorted(weights.items(), key=lambda x: x[1], reverse=True)
        print(f"{'标的':8s} | {'权重':>7s} | {'贡献':>8s} | {'个股收益':>10s}")
        print('-' * 45)
        total_contrib = 0
        for sym, w in sorted_items:
            ret = returns.get(sym, 0)
            contrib = w * ret
            total_contrib += contrib
            if w > 0.02 or abs(contrib) > 0.005:  # 只显示权重>2%或贡献>0.5%的
                print(f"{sym:8s} | {w*100:6.2f}% | {contrib*100:+7.3f}% | {ret*100:+9.2f}%")
        print(f"{'总计':8s} | {sum(weights.values())*100:6.2f}% | {total_contrib*100:+7.3f}% |")

# 分析2022-07和2024-12的亏损月份
print("\n\n=== 亏损月份分析 (regime=bull但大幅亏损) ===")
for r in data['records']:
    if r['date'] in ('2022-07-01', '2024-12-02'):
        print(f"\n=== {r['date']} (组合收益: {r['portfolio_return']*100:+.2f}%, regime={r['market_regime'].get('regime')}, factor={r['market_regime'].get('factor')}) ===")
        weights = r.get('weights', {})
        returns = r.get('returns', {})
        sorted_items = sorted(weights.items(), key=lambda x: x[1] * returns.get(x[0], 0))
        print(f"{'标的':8s} | {'权重':>7s} | {'贡献':>8s} | {'个股收益':>10s}")
        print('-' * 45)
        for sym, w in sorted_items[:10]:  # 最大的10个负贡献
            ret = returns.get(sym, 0)
            contrib = w * ret
            print(f"{sym:8s} | {w*100:6.2f}% | {contrib*100:+7.3f}% | {ret*100:+9.2f}%")
