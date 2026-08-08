"""验证 V6 版本的 bug: 确认 V6 没有应用 factor 和反转调整"""
import json
from pathlib import Path

# 加载 V6 结果
v6_files = sorted(Path('output/validation_reports').glob('lgb_backtest_v6_alpha_quality*.json'), key=lambda p: p.stat().st_mtime)
with open(v6_files[-1], encoding='utf-8') as f:
    v6 = json.load(f)

# 加载 V6.2 结果
with open('output/validation_reports/lgb_backtest_v6_2_profit_taking_20260725_065118.json', encoding='utf-8') as f:
    v62 = json.load(f)

# 检查 V6 和 V6.2 的 exposure_before / exposure_after
print("=== V6 vs V6.2 exposure_before / exposure_after 对比 ===")
print(f"{'日期':<12} {'V6 before':<12} {'V6 after':<12} {'V6 after/before':<15} {'V6.2 before':<12} {'V6.2 after':<12} {'V6.2 after/before':<15}")
print("-" * 95)

# 只打印 bear/rebound regime 的月份 (这些月份会触发反转调整)
bear_months = []
for i, r in enumerate(v6['records']):
    regime = r.get('market_regime', {}).get('regime', '')
    if regime in ('bear', 'rebound'):
        bear_months.append(i)

for i in bear_months[:10]:  # 只打印前 10 个 bear/rebound 月份
    v6_r = v6['records'][i]
    v62_r = v62['records'][i]
    v6_before = v6_r['market_regime'].get('exposure_before', 0)
    v6_after = v6_r['market_regime'].get('exposure_after', 0)
    v62_before = v62_r['market_regime'].get('exposure_before', 0)
    v62_after = v62_r['market_regime'].get('exposure_after', 0)
    v6_ratio = v6_after / v6_before if v6_before > 0 else 0
    v62_ratio = v62_after / v62_before if v62_before > 0 else 0
    print(f"{v6_r['date']:<12} {v6_before:<12.6f} {v6_after:<12.6f} {v6_ratio:<15.4f} {v62_before:<12.6f} {v62_after:<12.6f} {v62_ratio:<15.4f}")

# 检查 V6 的 factor 是否被应用到 exposure_after
print()
print("=== V6 的 factor 应用检查 ===")
print("如果 V6 正确应用了 factor, exposure_after/exposure_before 应该 ≈ factor")
print("如果 V6 没有应用 factor, exposure_after/exposure_before 应该 ≈ 1.0 (或 adjustment 加权)")
print()

v6_ratios = []
v62_ratios = []
for i in bear_months:
    v6_r = v6['records'][i]
    v62_r = v62['records'][i]
    v6_before = v6_r['market_regime'].get('exposure_before', 0)
    v6_after = v6_r['market_regime'].get('exposure_after', 0)
    v62_before = v62_r['market_regime'].get('exposure_before', 0)
    v62_after = v62_r['market_regime'].get('exposure_after', 0)
    if v6_before > 0:
        v6_ratios.append(v6_after / v6_before)
    if v62_before > 0:
        v62_ratios.append(v62_after / v62_before)

import statistics  # noqa: E402

print("V6 bear/rebound 月份 exposure_after/before:")
print(f"  平均: {statistics.mean(v6_ratios):.4f}")
print(f"  范围: {min(v6_ratios):.4f} ~ {max(v6_ratios):.4f}")
print("  (如果正确应用 factor=0.5, 应该 ≈ 0.5)")
print()
print("V6.2 bear/rebound 月份 exposure_after/before:")
print(f"  平均: {statistics.mean(v62_ratios):.4f}")
print(f"  范围: {min(v62_ratios):.4f} ~ {max(v62_ratios):.4f}")
print("  (如果正确应用 factor=0.5, 应该 ≈ 0.5)")

# 检查 V6 的最终权重是否 = 缓存权重 × dd_breaker (没有 factor)
print()
print("=== V6 权重 = 缓存 × dd_breaker 验证 ===")
print("如果 V6 没有应用 factor, V6 最终权重 = 缓存 × dd_breaker × (归一化)")
print("如果 V6 应用了 factor, V6 最终权重 = 缓存 × factor × dd_breaker × (归一化)")
print()

# 检查几个 bear 月份的权重比例
for i in bear_months[:3]:
    v6_r = v6['records'][i]
    v62_r = v62['records'][i]
    date = v6_r['date']
    v6_total = sum(v6_r['weights'].values())
    v62_total = sum(v62_r['weights'].values())
    v6_dd = v6_r['drawdown_breaker']['factor']
    v62_dd = v62_r['drawdown_breaker']['factor']
    v6_factor = v6_r['market_regime'].get('factor', 1.0)
    v62_factor = v62_r['market_regime'].get('factor', 1.0)
    print(f"{date}:")
    print(f"  V6:  总权重={v6_total:.4f}, factor={v6_factor}, dd={v6_dd}, factor×dd={v6_factor*v6_dd:.4f}")
    print(f"  V6.2: 总权重={v62_total:.4f}, factor={v62_factor}, dd={v62_dd}, factor×dd={v62_factor*v62_dd:.4f}")
    print(f"  V6 总权重 / V6.2 总权重 = {v6_total/v62_total:.4f} (如果 V6 没应用 factor, 应该 ≈ 1/factor = {1/v62_factor:.4f})")
    print()
