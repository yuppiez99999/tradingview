"""深入分析 2024-06-03 (bull regime, -5.11%) 大跌原因"""
import json

# 加载 V6.2 结果
with open('output/validation_reports/lgb_backtest_v6_2_profit_taking_20260725_065118.json', encoding='utf-8') as f:
    v62 = json.load(f)

# 找到 2024-06-03 和邻近月份
target_dates = ['2024-04-01', '2024-05-01', '2024-06-03', '2024-07-01', '2024-08-01']
print("=== 2024-04 ~ 2024-08 月度详情 ===")
print(f"{'日期':<12} {'收益':<10} {'regime':<10} {'factor':<8} {'dd_level':<10} {'dd_factor':<10}")
print("-" * 65)
for r in v62['records']:
    if r['date'] in target_dates:
        regime = r['market_regime']
        dd = r['drawdown_breaker']
        print(f"{r['date']:<12} {r['portfolio_return']*100:<10.2f} {regime.get('regime', 'N/A'):<10} {regime.get('factor', 0):<8.2f} {dd['level']:<10} {dd['factor']:<10.2f}")

# 分析 2024-06-03 的标的收益和权重
print("\n=== 2024-06-03 标的级别分析 ===")
target = None
for r in v62['records']:
    if r['date'] == '2024-06-03':
        target = r
        break

if target:
    weights = target['weights']
    rets = target['returns']

    # 计算每个标的的贡献
    contributions = []
    for sym in weights:
        w = weights.get(sym, 0)
        ret = rets.get(sym, 0)
        contrib = w * ret
        contributions.append((sym, w, ret, contrib))

    # 按贡献排序
    contributions.sort(key=lambda x: x[3])

    print(f"{'标的':<8} {'权重':<10} {'收益':<10} {'贡献':<10}")
    print("-" * 40)
    total_contrib = 0
    for sym, w, ret, contrib in contributions:
        if abs(contrib) > 0.0001 or w > 0.01:  # 只显示有权重或贡献的标的
            print(f"{sym:<8} {w:<10.4f} {ret*100:<10.2f} {contrib*100:<10.2f}")
            total_contrib += contrib
    print(f"\n总贡献(组合收益): {total_contrib*100:.2f}%")

    # 找出最大负贡献标的
    print("\n=== 最大负贡献标的 ===")
    for sym, w, ret, contrib in contributions[:5]:
        if contrib < 0:
            print(f"  {sym}: 权重={w:.4f}, 收益={ret*100:.2f}%, 贡献={contrib*100:.2f}%")

# 分析 2024-06-03 的市场状态
print("\n=== 2024-06-03 市场状态详情 ===")
if target:
    regime = target['market_regime']
    print(f"regime: {regime.get('regime')}")
    print(f"factor: {regime.get('factor')}")
    print(f"base_factor: {regime.get('base_factor')}")
    print(f"vol_override: {regime.get('vol_override')}")
    print(f"mom_override: {regime.get('mom_override')}")
    print(f"realized_vol_20d: {regime.get('realized_vol_20d')}")
    print(f"mom_20d: {regime.get('mom_20d')}")
    print(f"close: {regime.get('close')}")
    print(f"ma60: {regime.get('ma60')}")
    print(f"exposure_before: {regime.get('exposure_before')}")
    print(f"exposure_after: {regime.get('exposure_after')}")

# 分析 2024-05 ~ 2024-07 的权益曲线
print("\n=== 2024-05 ~ 2024-07 权益曲线 ===")
for r in v62['records']:
    if r['date'] in ['2024-05-01', '2024-06-03', '2024-07-01']:
        dd = r['drawdown_breaker']
        print(f"{r['date']}: equity={dd['equity']:.4f}, peak={dd['peak']:.4f}, prev_dd={dd['prev_dd']:.4f}, level={dd['level']}, factor={dd['factor']}")

# 分析 Window 1 的 bull regime 月份
print("\n=== Window 1 bull regime 月份分析 ===")
w1_records = v62['records'][15:30]  # Window 1
bull_months = [r for r in w1_records if r['market_regime'].get('regime') == 'bull']
print(f"{'日期':<12} {'收益':<10} {'factor':<8} {'vol_20d':<10} {'mom_20d':<10} {'exposure':<10}")
print("-" * 65)
for r in bull_months:
    regime = r['market_regime']
    print(f"{r['date']:<12} {r['portfolio_return']*100:<10.2f} {regime.get('factor', 0):<8.2f} {regime.get('realized_vol_20d', 0)*100:<10.2f} {regime.get('mom_20d', 0)*100:<10.2f} {regime.get('exposure_after', 0):<10.4f}")

# 分析 2024-06 前后的 LGB 信号质量
print("\n=== 2024-06 前后标的收益对比 ===")
print("（查看 LGB 是否给出了错误的权重）")
print(f"{'标的':<8} {'2024-05收益':<12} {'2024-06收益':<12} {'2024-06权重':<12} {'诊断':<20}")
print("-" * 70)
for r in v62['records']:
    if r['date'] == '2024-05-01':
        may_rets = r['returns']
    if r['date'] == '2024-06-03':
        jun_rets = r['returns']
        jun_weights = r['weights']

for sym in sorted(jun_weights.keys()):
    if jun_weights[sym] > 0.01:  # 只看权重>1%的标的
        may_ret = may_rets.get(sym, 0) * 100
        jun_ret = jun_rets.get(sym, 0) * 100
        w = jun_weights[sym]
        diagnosis = ""
        if jun_ret < -5 and w > 0.03:
            diagnosis = "← 高权重+大跌"
        elif jun_ret > 5 and w < 0.02:
            diagnosis = "← 低权重+大涨(错过)"
        print(f"{sym:<8} {may_ret:<12.2f} {jun_ret:<12.2f} {w:<12.4f} {diagnosis:<20}")
