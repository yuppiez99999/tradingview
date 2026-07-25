# -*- coding: utf-8 -*-
"""
步骤1+2：去除极端月份重新评估 + 不同 n_trials 的 DSR 对比
"""

import sys, json, math, pathlib
import numpy as np
import pandas as pd

BASE_DIR = pathlib.Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "data_cache"
VALIDATION_DIR = BASE_DIR / "v8.3_institutional" / "src" / "validation"
sys.path.insert(0, str(VALIDATION_DIR))

from deflated_sharpe import deflated_sharpe_ratio


def load_daily_returns() -> pd.Series:
    """从月度权重 + 日K线 计算日度组合收益"""
    with open(BASE_DIR / "output" / "backtest_result_latest.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    records = data["records"]

    all_daily_rets = []
    all_dates = []
    for i, record in enumerate(records):
        date_str = record["date"]
        weights = record["weights"]
        rebalance_date = pd.Timestamp(date_str).normalize()
        next_date = pd.Timestamp(records[i + 1]["date"]).normalize() if i + 1 < len(records) else rebalance_date + pd.Timedelta(days=31)

        daily_rets_dict = {}
        for symbol, weight in weights.items():
            if weight == 0:
                continue
            cache_file = CACHE_DIR / f"historical_{symbol}_5y_base.parquet"
            if not cache_file.exists():
                continue
            df = pd.read_parquet(cache_file)
            if hasattr(df.index, "tz") and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            mask = (df.index > rebalance_date) & (df.index <= next_date)
            month_df = df[mask]
            if month_df.empty:
                continue
            daily_ret = month_df["close"].pct_change().dropna()
            daily_rets_dict[symbol] = daily_ret

        if not daily_rets_dict:
            continue
        rets_df = pd.DataFrame(daily_rets_dict).fillna(0)
        weight_series = pd.Series(weights)
        available = [s for s in rets_df.columns if s in weight_series]
        if not available:
            continue
        w = weight_series[available].values
        w_sum = w.sum()
        if w_sum > 0:
            w = w / w_sum
        portfolio_daily = rets_df[available].values @ w
        all_daily_rets.extend(portfolio_daily.tolist())
        all_dates.extend(rets_df.index.tolist())

    daily = pd.Series(all_daily_rets, index=pd.DatetimeIndex(all_dates))
    daily = daily[~daily.index.duplicated(keep="last")]
    return daily


def calc_stats(daily_rets: pd.Series) -> dict:
    """计算年化收益、夏普、回撤"""
    if len(daily_rets) < 10:
        return {"n": 0, "ann_ret": 0, "sharpe": 0, "max_dd": 0}
    ann_ret = daily_rets.mean() * 252
    ann_vol = daily_rets.std() * math.sqrt(252)
    sharpe = (ann_ret - 0.03) / ann_vol if ann_vol > 0 else 0
    equity = (1 + daily_rets).cumprod()
    max_dd = float(((equity.cummax() - equity) / equity.cummax()).max())
    return {
        "n": len(daily_rets),
        "ann_ret": ann_ret,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "ann_vol": ann_vol,
    }


def main():
    daily = load_daily_returns()
    print("=" * 70)
    print("  步骤1：去除极端月份重新评估")
    print("=" * 70)

    # 按月分组
    monthly = daily.resample("M").sum()
    monthly_pct = (1 + daily).resample("M").prod() - 1

    print(f"\n全部月度收益（按时间排序）:")
    for date, ret in monthly_pct.items():
        bar = "█" * int(abs(ret) * 100)
        sign = "+" if ret >= 0 else "-"
        print(f"  {date.strftime('%Y-%m')}: {ret*100:+7.2f}%  {sign}{bar}")

    # 不同剔除场景
    print(f"\n{'='*70}")
    print(f"  {'场景':<25} {'天数':>6} {'年化收益':>10} {'夏普':>8} {'回撤':>8}")
    print(f"  {'-'*65}")

    # 场景0：全部
    s = calc_stats(daily)
    print(f"  {'全部 24 个月':<25} {s['n']:>6} {s['ann_ret']*100:>9.2f}% {s['sharpe']:>8.2f} {s['max_dd']*100:>7.2f}%")

    # 场景1：剔除 2025-08, 2025-09
    mask = ~daily.index.isin(pd.date_range("2025-08-01", "2025-09-30"))
    s1 = calc_stats(daily[mask])
    print(f"  {'剔除 2025-08/09':<25} {s1['n']:>6} {s1['ann_ret']*100:>9.2f}% {s1['sharpe']:>8.2f} {s1['max_dd']*100:>7.2f}%")

    # 场景2：剔除收益最高的2个月
    top2 = monthly_pct.nlargest(2)
    mask2 = ~daily.index.to_period("M").isin(top2.index.to_period("M"))
    s2 = calc_stats(daily[mask2])
    top2_names = ", ".join([d.strftime("%Y-%m") for d in top2.index])
    print(f"  {'剔除最高2月(' + top2_names + ')':<25} {s2['n']:>6} {s2['ann_ret']*100:>9.2f}% {s2['sharpe']:>8.2f} {s2['max_dd']*100:>7.2f}%")

    # 场景3：剔除收益最高的3个月
    top3 = monthly_pct.nlargest(3)
    mask3 = ~daily.index.to_period("M").isin(top3.index.to_period("M"))
    s3 = calc_stats(daily[mask3])
    print(f"  {'剔除最高3月':<25} {s3['n']:>6} {s3['ann_ret']*100:>9.2f}% {s3['sharpe']:>8.2f} {s3['max_dd']*100:>7.2f}%")

    # 场景4：只用前18个月（2024-01 ~ 2025-06）
    mask4 = daily.index < pd.Timestamp("2025-07-01")
    s4 = calc_stats(daily[mask4])
    print(f"  {'仅前18月(2024H1+2025H1)':<25} {s4['n']:>6} {s4['ann_ret']*100:>9.2f}% {s4['sharpe']:>8.2f} {s4['max_dd']*100:>7.2f}%")

    # 场景5：剔除单月超10%的月份
    extreme_months = monthly_pct[monthly_pct.abs() > 0.10]
    mask5 = ~daily.index.to_period("M").isin(extreme_months.index.to_period("M"))
    s5 = calc_stats(daily[mask5])
    ext_names = ", ".join([d.strftime("%Y-%m") for d in extreme_months.index])
    print(f"  {'剔除|月收益|>10%(' + ext_names + ')':<25} {s5['n']:>6} {s5['ann_ret']*100:>9.2f}% {s5['sharpe']:>8.2f} {s5['max_dd']*100:>7.2f}%")

    print(f"  {'-'*65}")

    # 步骤2：不同 n_trials 的 DSR 对比
    print(f"\n{'='*70}")
    print(f"  步骤2：不同 n_trials 的 DSR 对比")
    print(f"{'='*70}")
    print(f"\n  {'n_trials':>10} {'E[max_SR]':>12} {'观测Sharpe':>12} {'DSR':>10} {'p-value':>10} {'通过':>6}")
    print(f"  {'-'*65}")

    rets_list = daily.tolist()
    obs_sharpe = None
    for n_trials in [10, 23, 50, 100, 200]:
        result = deflated_sharpe_ratio(rets_list, n_trials=n_trials, required_dsr=0.95, risk_free_rate=0.03)
        if obs_sharpe is None:
            obs_sharpe = result.sharpe_ratio
        passed = "✅" if result.is_pass else "❌"
        print(f"  {n_trials:>10} {result.e_max_sr:>12.4f} {result.sharpe_ratio:>12.4f} {result.deflated_sharpe_ratio:>10.4f} {result.p_value:>10.6f} {passed:>6}")

    print(f"  {'-'*65}")
    print(f"\n  说明:")
    print(f"    - n_trials=23: 每个标的1次独立试验（最保守的合理估计）")
    print(f"    - n_trials=100: 含特征组合搜索（中等保守）")
    print(f"    - n_trials=200: 含超参数搜索（最保守）")
    print(f"\n  结论: 观测 Sharpe={obs_sharpe:.2f}")

    # 找到 DSR 能通过的 n_trials
    for n_trials in range(1, 300):
        result = deflated_sharpe_ratio(rets_list, n_trials=n_trials, required_dsr=0.95, risk_free_rate=0.03)
        if result.is_pass:
            print(f"    DSR 在 n_trials={n_trials} 时通过 (DSR={result.deflated_sharpe_ratio:.4f})")
            break
    else:
        print(f"    DSR 在 n_trials=1~299 范围内均未通过 0.95 阈值")
        # 找到 DSR > 0.5 的 n_trials
        for n_trials in range(1, 300):
            result = deflated_sharpe_ratio(rets_list, n_trials=n_trials, required_dsr=0.95, risk_free_rate=0.03)
            if result.deflated_sharpe_ratio > 0.5:
                print(f"    DSR 在 n_trials={n_trials} 时达到 0.5 (DSR={result.deflated_sharpe_ratio:.4f})")
                break


if __name__ == "__main__":
    main()
