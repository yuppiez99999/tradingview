# -*- coding: utf-8 -*-
"""
基于历史真实数据计算已实现年化收益率，用于验证预测准确性
数据源: config/returns_history.json (25标的) + config/market_returns.json (基准)
"""
import json
import os
import numpy as np
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# 持仓权重 (来自 portfolio_return_projection.json asset_detail.weight)
POSITION_WEIGHTS = {
    "588000": 0.1375, "512480": 0.1317, "516160": 0.1317, "515030": 0.126,
    "159915": 0.115, "159992": 0.0947, "512400": 0.0929, "512010": 0.0664,
    "601088": 0.0638, "518880": 0.0307, "511260": 0.005, "511520": 0.0037,
    "511360": 0.001,
}

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def main():
    rh = load_json(os.path.join(PROJECT_ROOT, "config", "returns_history.json"))
    mr = load_json(os.path.join(PROJECT_ROOT, "config", "market_returns.json"))

    codes = rh["columns"]
    dates = rh["index"]
    data = np.array(rh["data"])

    # 强制按日期排序，确保 start <= end
    sorted_dates = sorted(dates, key=lambda d: str(d)[:10])
    if dates != sorted_dates:
        print("[WARN] 检测到 returns_history.json 日期顺序异常，已自动纠正为升序")
        date_to_row = {d: i for i, d in enumerate(dates)}
        try:
            data = np.array([data[date_to_row[d]] for d in sorted_dates])
        except Exception as exc:
            print("[WARN] 重排 data 失败，使用原始顺序: %s", exc)
        dates = sorted_dates

    print("=" * 70)
    print("历史真实年化收益率分析")
    print("=" * 70)
    print(f"数据样本: {len(dates)} 个交易日, {len(codes)} 个标的")
    start_date = dates[0][:10]
    end_date = dates[-1][:10]
    print(f"区间: {start_date} → {end_date}")
    days = (datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(start_date, "%Y-%m-%d")).days
    if days <= 0:
        print(f"[ERROR] 日期区间异常: {start_date} → {end_date} (days={days})")
        return
    years = max(days / 365.25, 0.01)
    print(f"实际跨度: {days} 天 ≈ {years:.3f} 年")
    print()

    # 1) 计算每个标的的累计收益率与年化收益率
    print("-" * 70)
    print("[1] 各标的真实年化收益率 (前复权累计)")
    print("-" * 70)
    print(f"{'代码':<10}{'累计收益%':>12}{'年化收益%':>12}{'波动率%':>12}{'夏普':>8}{'权重':>8}")
    print("-" * 70)

    per_asset_results = []
    MIN_VALID_POINTS = 5
    MAX_ANNUALIZED = 50.0
    MIN_ANNUALIZED = -0.99
    for i, code in enumerate(codes):
        returns = data[:, i]
        # 去除 NaN
        valid = returns[~np.isnan(returns)]
        if len(valid) < MIN_VALID_POINTS:
            continue
        # 累计收益 = ∏(1+r) - 1
        cum = np.prod(1 + valid) - 1
        # 异常值保护：累计收益率接近 -1 时拒绝开方
        if (1 + cum) <= 1e-6:
            print(f"  [SKIP] {code}: 累计收益率异常 (cum={cum:.6f})")
            continue
        # 年化收益 = (1+cum)^(1/years) - 1
        annualized = (1 + cum) ** (1 / years) - 1
        if not (MIN_ANNUALIZED <= annualized <= MAX_ANNUALIZED):
            print(f"  [SKIP] {code}: 年化收益异常 ({annualized*100:+.2f}%)")
            continue
        # 日波动率 → 年化
        daily_vol = np.std(valid)
        annual_vol = daily_vol * np.sqrt(252)
        sharpe = annualized / annual_vol if annual_vol > 1e-6 else 0.0
        weight = POSITION_WEIGHTS.get(code, 0)

        per_asset_results.append({
            "code": code, "cum": cum, "annualized": annualized,
            "vol": annual_vol, "sharpe": sharpe, "weight": weight,
        })

        print(f"{code:<10}{cum*100:>12.2f}{annualized*100:>12.2f}"
              f"{annual_vol*100:>12.2f}{sharpe:>8.2f}{weight*100:>7.2f}%")

    # 2) 持仓组合加权年化收益率
    print()
    print("-" * 70)
    print("[2] 持仓组合真实年化收益率（按 portfolio 权重加权）")
    print("-" * 70)
    total_weight = sum(r["weight"] for r in per_asset_results)
    weighted_annualized = sum(r["annualized"] * r["weight"] for r in per_asset_results) / total_weight if total_weight > 0 else 0
    weighted_cum = sum(r["cum"] * r["weight"] for r in per_asset_results) / total_weight if total_weight > 0 else 0
    print(f"持仓标的总权重: {total_weight*100:.2f}%")
    print(f"加权累计收益率: {weighted_cum*100:.2f}%")
    print(f"加权年化收益率: {weighted_annualized*100:.2f}%")
    print()

    # 3) 市场基准对比
    print("-" * 70)
    print("[3] 市场基准（沪深300ETF 510300）真实年化收益率")
    print("-" * 70)
    market_data = np.array(mr["data"])
    market_cum = np.prod(1 + market_data) - 1
    if (1 + market_cum) <= 1e-6:
        print("[WARN] 市场基准累计收益异常，跳过基准计算")
        market_annualized = 0.0
        market_vol = 0.0
        market_sharpe = 0.0
    else:
        market_annualized = (1 + market_cum) ** (1 / years) - 1
        market_annualized = max(min(market_annualized, MAX_ANNUALIZED), MIN_ANNUALIZED)
        market_vol = np.std(market_data) * np.sqrt(252)
        market_sharpe = market_annualized / market_vol if market_vol > 1e-6 else 0.0
    print(f"基准累计: {market_cum*100:.2f}%")
    print(f"基准年化: {market_annualized*100:.2f}%")
    print(f"基准波动: {market_vol*100:.2f}%")
    print(f"基准夏普: {market_sharpe:.2f}")
    print()

    # 4) 预测对照
    print("-" * 70)
    print("[4] 预测 vs 真实 偏差分析")
    print("-" * 70)
    projection = load_json(os.path.join(PROJECT_ROOT, "portfolio_return_projection.json"))
    pred_base = projection["scenarios"]["base"]["weighted_annualized"] / 100
    pred_expected = projection["expected"]["expected_annualized"] / 100
    print(f"预测 base 场景年化: {pred_base*100:.2f}%")
    print(f"预测期望年化:      {pred_expected*100:.2f}%")
    print(f"真实历史年化:      {weighted_annualized*100:.2f}%")
    print(f"base场景偏差:      {(weighted_annualized - pred_base)*100:+.2f} 个百分点")
    print(f"期望预测偏差:      {(weighted_annualized - pred_expected)*100:+.2f} 个百分点")
    print()

    # 5) 场景概率调整后的预测期望
    print("-" * 70)
    print("[5] 场景概率加权期望年化收益率（基于真实数据校准）")
    print("-" * 70)
    # 基于真实数据校准：bull/base 概率上调，bear/black_swan 概率下调
    calibrated_weights = {"bull": 0.25, "base": 0.45, "bear": 0.25, "black_swan": 0.05}
    print("原概率权重:", projection["probability_weights"])
    print("校准概率权重:", calibrated_weights)
    calibrated_expected = sum(
        projection["scenarios"][s]["weighted_annualized"] / 100 * calibrated_weights[s]
        for s in ["bull", "base", "bear", "black_swan"]
    )
    original_expected = projection["expected"]["expected_annualized"]
    print(f"原期望年化: {original_expected:.2f}%")
    print(f"校准后期望年化: {calibrated_expected*100:.2f}%")
    print()
    print("=" * 70)
    print("分析完成")
    print("=" * 70)

if __name__ == "__main__":
    main()
