"""对比 V6 vs V6.2 在 2024-09 的详细信息, 找出回撤熔断差异原因"""
import json
from pathlib import Path

# 加载 V6.2 结果
with open('output/validation_reports/lgb_backtest_v6_2_profit_taking_20260725_065118.json', encoding='utf-8') as f:
    v62 = json.load(f)

# 加载 V6 结果
v6_files = sorted(Path('output/validation_reports').glob('lgb_backtest_v6_alpha_quality*.json'), key=lambda p: p.stat().st_mtime)
with open(v6_files[-1], encoding='utf-8') as f:
    v6 = json.load(f)

# 加载 V6.1 结果
v61_files = sorted(Path('output/validation_reports').glob('lgb_backtest_v6_1*.json'), key=lambda p: p.stat().st_mtime)
with open(v61_files[-1], encoding='utf-8') as f:
    v61 = json.load(f)

# 对比 2024-07 ~ 2024-10 的详细信息
print("=" * 100)
print("对比 V6 vs V6.1 vs V6.2 在 2024-07 ~ 2024-10 的详细信息")
print("=" * 100)

target_dates = ['2024-07-01', '2024-08-01', '2024-09-02', '2024-10-01', '2024-11-01']
for target_date in target_dates:
    print(f"\n--- {target_date} ---")
    for name, data in [("V6", v6), ("V6.1", v61), ("V6.2", v62)]:
        for r in data['records']:
            if r['date'] == target_date:
                regime = r.get('market_regime', {})
                dd_breaker = r.get('drawdown_breaker', {})
                pt = r.get('profit_taking', {})
                print(f"  {name}: return={r['portfolio_return']*100:+.2f}%")
                print(f"    regime={regime.get('regime', 'N/A')} factor={regime.get('factor', 'N/A')} "
                      f"base_factor={regime.get('base_factor', 'N/A')} vol_override={regime.get('vol_override', 'N/A')} "
                      f"mom_override={regime.get('mom_override', 'N/A')}")
                print(f"    defensive={regime.get('defensive', {})}")
                print(f"    dd_breaker: level={dd_breaker.get('level', 'N/A')} factor={dd_breaker.get('factor', 'N/A')} "
                      f"prev_dd={dd_breaker.get('prev_dd', 'N/A'):.4f} equity={dd_breaker.get('equity', 'N/A'):.4f} "
                      f"peak={dd_breaker.get('peak', 'N/A'):.4f}")
                print(f"    pt_applied={pt.get('applied_this_month', [])}")
                break

# 累计权益曲线对比
print()
print("=" * 100)
print("累计权益曲线对比 (V6 vs V6.1 vs V6.2)")
print("=" * 100)
print(f"{'日期':<12} {'V6 equity':<15} {'V6.1 equity':<15} {'V6.2 equity':<15} {'V6 peak':<15} {'V6.2 peak':<15}")
print("-" * 90)
for i in range(len(v6['records'])):
    v6_r = v6['records'][i]
    v61_r = v61['records'][i]
    v62_r = v62['records'][i]
    v6_eq = v6_r['drawdown_breaker']['equity']
    v61_eq = v61_r['drawdown_breaker']['equity']
    v62_eq = v62_r['drawdown_breaker']['equity']
    v6_pk = v6_r['drawdown_breaker']['peak']
    v62_pk = v62_r['drawdown_breaker']['peak']
    print(f"{v6_r['date']:<12} {v6_eq:<15.6f} {v61_eq:<15.6f} {v62_eq:<15.6f} {v6_pk:<15.6f} {v62_pk:<15.6f}")
    if i > 15:  # 只打印到 2024-09 附近
        break
