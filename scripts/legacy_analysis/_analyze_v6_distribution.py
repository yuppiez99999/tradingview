# -*- coding: utf-8 -*-
"""分析V6月度收益分布, 识别峰度来源, 设计止盈调优方案"""
import json
import numpy as np
import pandas as pd
from pathlib import Path

# 加载V6结果
v6_file = Path("output/validation_reports/lgb_backtest_v6_alpha_quality_20260725_063847.json")
with open(v6_file, "r", encoding="utf-8") as f:
    data = json.load(f)

records = data.get("records", [])
returns = pd.Series([r["portfolio_return"] for r in records])
dates = [r["date"] for r in records]

print("=" * 70)
print("V6 月度收益分布分析")
print("=" * 70)
print(f"月份总数: {len(returns)}")
print(f"均值: {returns.mean()*100:.2f}%")
print(f"标准差: {returns.std()*100:.2f}%")
print(f"偏度: {returns.skew():.4f}")
print(f"峰度(超额): {returns.kurt():.4f}")
print()

# 按收益排序, 显示所有月份
sorted_idx = returns.sort_values(ascending=False).index
print("全部月份收益排序 (高→低):")
print(f"{'排名':>4} {'日期':<12} {'月收益':>8} {'累计贡献':>10}")
print("-" * 40)
cumulative = 0
for rank, idx in enumerate(sorted_idx, 1):
    ret = returns[idx]
    cumulative += ret
    marker = ""
    if ret > 0.12:
        marker = " ← 组合止盈触发线(>12%)"
    if ret > 0.15:
        marker = " ← 超高收益(>15%)"
    print(f"{rank:4d} {dates[idx]:<12} {ret*100:7.2f}% {cumulative*100:9.2f}%{marker}")

print()
print("=" * 70)
print("极端月份分析 (贡献峰度的关键月份)")
print("=" * 70)

# 分析top 5月份的构成
top5_idx = sorted_idx[:5]
for rank, idx in enumerate(top5_idx, 1):
    ret = returns[idx]
    record = records[idx]
    weights = record.get("weights", {})
    stock_returns = record.get("returns", {})

    # 找出贡献最大的持仓
    contributions = []
    for sym, w in weights.items():
        if w > 0 and sym in stock_returns:
            contrib = w * stock_returns[sym]
            contributions.append((sym, w, stock_returns[sym], contrib))
    contributions.sort(key=lambda x: x[3], reverse=True)

    print(f"\n排名{rank}: {dates[idx]} 月收益={ret*100:.2f}%")
    print("  Top 5 贡献持仓:")
    for sym, w, sr, contrib in contributions[:5]:
        print(f"    {sym}: 权重={w*100:.1f}% 个股收益={sr*100:+.1f}% 贡献={contrib*100:+.2f}%")

    # 检查止盈状态
    pt = record.get("profit_taking", {})
    pt_syms = pt.get("symbols", {})
    pt_port = pt.get("portfolio_factor", 1.0)
    if pt_syms:
        print(f"  止盈-个股: {pt_syms}")
    if pt_port < 1.0:
        print(f"  止盈-组合: ×{pt_port}")

print()
print("=" * 70)
print("止盈调优模拟")
print("=" * 70)

# 模拟不同止盈阈值的效果
# 当前规则: 组合>12%→×0.85, 个股>50%→×0.6
# 思路: 降低组合止盈阈值, 或增加分级止盈

scenarios = [
    ("V6当前", 0.12, 0.85, 0.50, 0.60),
    ("方案A: 组合>10%→×0.80", 0.10, 0.80, 0.50, 0.60),
    ("方案B: 组合>10%→×0.75", 0.10, 0.75, 0.50, 0.60),
    ("方案C: 分级(>10%→×0.85, >15%→×0.70)", 0.10, 0.85, 0.50, 0.60, 0.15, 0.70),
    ("方案D: 组合>8%→×0.80", 0.08, 0.80, 0.50, 0.60),
    ("方案E: 个股>40%→×0.5+组合>10%→×0.80", 0.10, 0.80, 0.40, 0.50),
]

for scenario in scenarios:
    name = scenario[0]
    port_threshold = scenario[1]
    port_factor = scenario[2]
    stock_threshold = scenario[3]
    stock_factor = scenario[4]
    has_tier2 = len(scenario) > 5
    tier2_threshold = scenario[5] if has_tier2 else 999
    tier2_factor = scenario[6] if has_tier2 else 1.0

    # 模拟: 遍历月份, 应用止盈规则
    sim_returns = []
    portfolio_pt_factor = 1.0
    stock_pt_factors = {}

    for _i, record in enumerate(records):
        ret = record["portfolio_return"]

        # 应用上月止盈因子到本月收益
        adjusted_ret = ret * portfolio_pt_factor

        # 个股止盈: 如果上月有个股触发止盈, 本月该股收益受限(近似)
        # 这里简化: 只用组合止盈因子

        sim_returns.append(adjusted_ret)

        # 更新止盈因子
        if adjusted_ret > tier2_threshold:
            portfolio_pt_factor = tier2_factor
        elif adjusted_ret > port_threshold:
            portfolio_pt_factor = port_factor
        else:
            portfolio_pt_factor = 1.0

    sim_series = pd.Series(sim_returns)
    sim_ann_ret = float((1 + sim_series.mean()) ** 12 - 1)
    sim_kurt = float(sim_series.kurt())
    sim_skew = float(sim_series.skew())
    sim_sharpe = sim_ann_ret / (sim_series.std() * np.sqrt(12)) if sim_series.std() > 0 else 0
    sim_max = float(sim_series.max())

    # Walk-Forward Sharpe CV (简化计算)
    n = len(sim_series)
    wsize = n // 3
    sharpes = []
    for j in range(3):
        s = j * wsize
        e = (j + 1) * wsize if j < 2 else n
        wr = sim_series.iloc[s:e]
        w_ann = float((1 + wr.mean()) ** 12 - 1)
        w_vol = float(wr.std() * np.sqrt(12))
        w_sharpe = w_ann / w_vol if w_vol > 0 else 0
        sharpes.append(w_sharpe)
    sim_cv = float(np.std(sharpes) / (np.mean(sharpes) + 1e-9)) if np.mean(sharpes) > 0 else 0

    # 去极端月年化
    top2 = sim_series.nlargest(2).index
    without_top2 = sim_series.drop(top2)
    sim_without = float((1 + without_top2.mean()) ** 12 - 1)

    print(f"\n{name}:")
    print(f"  年化={sim_ann_ret*100:.2f}% Sharpe={sim_sharpe:.3f} 峰度={sim_kurt:.2f} "
          f"偏度={sim_skew:.2f} SharpeCV={sim_cv:.2f} 去极端月={sim_without*100:.2f}% 最大月={sim_max*100:.1f}%")

    # DSR估算 (简化: 只看kurtosis和sharpe的关系)
    # DSR受 (γ4-1)/4 × SR² 影响, 降低kurtosis直接提升DSR
    dsr_penalty = (sim_kurt) / 4 * sim_sharpe**2
    print(f"  DSR峰度惩罚项: {dsr_penalty:.4f} (越小DSR越高, V6当前={returns.kurt()/4*(sim_sharpe)**2:.4f})")
