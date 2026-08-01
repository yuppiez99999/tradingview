# -*- coding: utf-8 -*-
"""读取 2024-09-02 缓存权重, 对比 V6/V6.2 最终权重, 推断 V6 版本逻辑"""
import json
from pathlib import Path

# 读取 2024-09-02 缓存权重
cache_file = Path("output/institutional_pipeline/2024-09-02/pipeline_backtest.json")
with open(cache_file, "r", encoding="utf-8") as f:
    cache = json.load(f)

cache_weights = cache.get("steps", {}).get("portfolio_decision", {}).get("target_weights", {})
print("=== 2024-09-02 缓存权重 ===")
print(f"缓存权重总和: {sum(cache_weights.values()):.6f}")
print(f"缓存标的数: {len(cache_weights)}")
print()

# 加载 V6 和 V6.2 结果
v6_files = sorted(Path('output/validation_reports').glob('lgb_backtest_v6_alpha_quality*.json'), key=lambda p: p.stat().st_mtime)
with open(v6_files[-1], 'r', encoding='utf-8') as f:
    v6 = json.load(f)
with open('output/validation_reports/lgb_backtest_v6_2_profit_taking_20260725_065118.json', 'r', encoding='utf-8') as f:
    v62 = json.load(f)

# 获取 2024-09-02 的最终权重
v6_final = None
v62_final = None
for r in v6['records']:
    if r['date'] == '2024-09-02':
        v6_final = r['weights']
        v6_regime = r['market_regime']
        v6_dd = r['drawdown_breaker']
        break
for r in v62['records']:
    if r['date'] == '2024-09-02':
        v62_final = r['weights']
        v62_regime = r['market_regime']
        v62_dd = r['drawdown_breaker']
        break

# 对比
print("=== 2024-09-02 权重对比 ===")
print(f"{'标的':<8} {'缓存权重':<12} {'V6 最终权重':<14} {'V6.2 最终权重':<14} {'V6/缓存':<10} {'V6.2/缓存':<10}")
print("-" * 80)

# 获取 V6 的 symbol_rets_20d 用于计算 adjustment
v6_symbol_rets = v6_regime.get('defensive', {}).get('symbol_rets_20d', {})
factor = 0.5  # regime factor
dd_factor = 0.6  # dd_breaker factor

for sym in sorted(cache_weights.keys()):
    cw = cache_weights.get(sym, 0)
    v6_w = v6_final.get(sym, 0) if v6_final else 0
    v62_w = v62_final.get(sym, 0) if v62_final else 0
    v6_ratio = v6_w / cw if cw > 0 else 0
    v62_ratio = v62_w / cw if cw > 0 else 0

    # 计算 adjustment
    ret_20d = v6_symbol_rets.get(sym, 0)
    if ret_20d < -0.10:
        adj = 1.3
        adj_str = "×1.3(超跌)"
    elif ret_20d > 0.10:
        adj = 0.7
        adj_str = "×0.7(超涨)"
    else:
        adj = 1.0
        adj_str = "×1.0"

    print(f"{sym:<8} {cw:<12.6f} {v6_w:<14.6f} {v62_w:<14.6f} {v6_ratio:<10.4f} {v62_ratio:<10.4f}  {adj_str}")

print()
print(f"缓存权重总和: {sum(cache_weights.values()):.6f}")
print(f"V6 最终权重总和: {sum(v6_final.values()):.6f}")
print(f"V6.2 最终权重总和: {sum(v62_final.values()):.6f}")
print()
print(f"V6 总权重 / 缓存总权重 = {sum(v6_final.values()) / sum(cache_weights.values()):.4f}")
print(f"V6.2 总权重 / 缓存总权重 = {sum(v62_final.values()) / sum(cache_weights.values()):.4f}")
print()

# 推断 V6 版本的逻辑
print("=== 推断 V6 版本的逻辑 ===")
print(f"factor = {factor}, dd_factor = {dd_factor}")
print(f"如果 V6 = 缓存 × factor (无 dd_factor, 无归一化): 总权重 = {sum(cache_weights.values()) * factor:.6f}")
print(f"如果 V6 = 缓存 × factor × dd_factor (有 dd_factor, 无归一化): 总权重 = {sum(cache_weights.values()) * factor * dd_factor:.6f}")
print(f"如果 V6 = 缓存 × factor (无 dd_factor, 有归一化): 总权重 = {sum(cache_weights.values()) * factor:.6f}")
print()
print(f"实际 V6 总权重: {sum(v6_final.values()):.6f}")
print(f"实际 V6.2 总权重: {sum(v62_final.values()):.6f}")

# 检查 V6 的权重是否符合 缓存 × factor × adjustment (无归一化, 无 dd_factor)
print()
print("=== 验证 V6 = 缓存 × factor × adjustment (无归一化, 无 dd_factor) ===")
print(f"{'标的':<8} {'缓存×factor×adj':<18} {'V6 实际':<14} {'匹配':<6}")
print("-" * 50)
match_count = 0
total_count = 0
for sym in sorted(cache_weights.keys()):
    cw = cache_weights.get(sym, 0)
    v6_w = v6_final.get(sym, 0) if v6_final else 0
    ret_20d = v6_symbol_rets.get(sym, 0)
    if ret_20d < -0.10:
        adj = 1.3
    elif ret_20d > 0.10:
        adj = 0.7
    else:
        adj = 1.0
    expected = cw * factor * adj
    match = abs(expected - v6_w) < 0.0001
    if match:
        match_count += 1
    total_count += 1
    print(f"{sym:<8} {expected:<18.6f} {v6_w:<14.6f} {'✓' if match else '✗':<6}")

print(f"\n匹配: {match_count}/{total_count}")
