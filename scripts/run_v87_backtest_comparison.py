"""v8.7 对冲优化回测对比脚本 — v5.9(S5固定阈值) vs v8.7(S6 RegimeFolio动态阈值)

运行 2021-2026 回测, 对比:
  S5 (v5.9): S2动态再平衡 + 固定阈值尾部对冲(vol>28%/DD>12%)
  S6 (v8.7): S2动态再平衡 + RegimeFolio动态阈值尾部对冲(VIX regime自适应)

输出: reports/v87_backtest_comparison_YYYY-MM-DD.md

用法:
  python scripts/run_v87_backtest_comparison.py
  python scripts/run_v87_backtest_comparison.py --force-dl  # 强制重新下载数据
"""

import argparse
import os
import sys
import time
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.hedge_rebalance_backtest import (
    END_DATE,
    PORTFOLIO_CODES,
    RISK_FREE_RATE,
    START_DATE,
    BacktestDataLoader,
    HedgeRebalanceBacktest,
    compute_multi_index_beta_weights,
    compute_vix_from_csi300,
)


def _classify_regime_simple(vix: float) -> str:
    if vix < 15:
        return "low_vol"
    if vix < 20:
        return "normal"
    if vix < 30:
        return "high_vol"
    return "crisis"


def compute_vix_regime_stats(csi300_ret, n_days: int) -> dict:
    regimes = {"low_vol": 0, "normal": 0, "high_vol": 0, "crisis": 0}
    vix_values = []
    for i in range(n_days):
        vix = compute_vix_from_csi300(csi300_ret, i)
        vix_values.append(vix)
        regimes[_classify_regime_simple(vix)] += 1
    total = max(n_days, 1)
    return {
        "regime_days": regimes,
        "regime_pct": {k: v / total for k, v in regimes.items()},
        "vix_mean": float(np.mean(vix_values)),
        "vix_min": float(np.min(vix_values)),
        "vix_max": float(np.max(vix_values)),
        "vix_std": float(np.std(vix_values)),
    }


def format_v87_comparison(s5, s6, vix_stats) -> str:
    lines = []
    lines.append("=" * 90)
    lines.append(
        "  v8.7 对冲优化回测对比报告 — v5.9(S5固定阈值) vs v8.7(S6 RegimeFolio动态阈值)"
    )
    lines.append("=" * 90)
    lines.append(f"  生成时间: {now_bj().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"  回测区间: {START_DATE} ~ {END_DATE}")
    lines.append(f"  初始资金: 1,000,000元 | 标的: {len(PORTFOLIO_CODES)}只")
    lines.append(f"  无风险利率: {RISK_FREE_RATE * 100:.0f}%")
    lines.append("")

    lines.append("[1] VIX Regime 分布统计")
    lines.append("-" * 90)
    lines.append(
        f"  VIX 均值: {vix_stats['vix_mean']:.1f} | 范围: [{vix_stats['vix_min']:.1f}, {vix_stats['vix_max']:.1f}] | 标准差: {vix_stats['vix_std']:.1f}"  # noqa: E501
    )
    lines.append("")
    lines.append(
        "  Regime      | 交易日数  | 占比    | 动态vol_trigger | 动态dd_trigger"
    )
    lines.append("  " + "-" * 80)
    regime_info = {
        "low_vol": ("低波(VIX<15)", 0.35, 0.15),
        "normal": ("正常(15-20)", 0.28, 0.12),
        "high_vol": ("高波(20-30)", 0.25, 0.11),
        "crisis": ("危机(>=30)", 0.15, 0.08),
    }
    for key, (label, vt, dt) in regime_info.items():
        days = vix_stats["regime_days"][key]
        pct = vix_stats["regime_pct"][key] * 100
        lines.append(
            f"  {label:12s} | {days:6d}    | {pct:5.1f}% | {vt:.2f}            | {dt:.2f}"
        )
    lines.append(
        f"  {'v5.9固定阈值':12s} | {'全部':>6s}    | 100.0% | 0.28            | 0.12"
    )
    lines.append("")

    lines.append("[2] 核心指标对比")
    lines.append("-" * 90)
    header = f"  {'指标':20s} | {'S5 (v5.9固定)':>16s} | {'S6 (v8.7动态)':>16s} | {'差异':>12s} | {'改善':>6s}"
    lines.append(header)
    lines.append("  " + "-" * 80)

    def _row(label, v5, v6, fmt="{:.4f}", better="higher"):
        diff = v6 - v5
        if better == "higher":
            improved = "✅" if diff > 0 else ("❌" if diff < 0 else "—")
        else:
            improved = "✅" if diff < 0 else ("❌" if diff > 0 else "—")
        lines.append(
            f"  {label:20s} | {fmt.format(v5):>16s} | {fmt.format(v6):>16s} | {fmt.format(diff):>12s} | {improved:>6s}"
        )

    _row("年化收益率", s5.annual_return, s6.annual_return, "{:.2%}")
    _row("年化波动率", s5.annual_volatility, s6.annual_volatility, "{:.2%}", "lower")
    _row("夏普比率", s5.sharpe_ratio, s6.sharpe_ratio, "{:.4f}")
    _row("最大回撤", s5.max_drawdown, s6.max_drawdown, "{:.2%}", "lower")
    _row("Calmar比率", s5.calmar_ratio, s6.calmar_ratio, "{:.4f}")
    _row("日胜率", s5.win_rate, s6.win_rate, "{:.2%}")
    _row("总收益率", s5.total_return, s6.total_return, "{:.2%}")
    lines.append("")

    lines.append("[3] 对冲有效性对比")
    lines.append("-" * 90)
    s5_hedge_cost = sum(s5.hedge_costs)
    s6_hedge_cost = sum(s6.hedge_costs)
    s5_total_cost = s5_hedge_cost + sum(s5.transaction_costs)
    s6_total_cost = s6_hedge_cost + sum(s6.transaction_costs)
    lines.append(
        f"  {'指标':20s} | {'S5 (v5.9)':>16s} | {'S6 (v8.7)':>16s} | {'差异':>12s}"
    )
    lines.append("  " + "-" * 70)
    lines.append(
        f"  {'对冲激活天数':20s} | {s5.hedge_days:>16d} | {s6.hedge_days:>16d} | {s6.hedge_days - s5.hedge_days:>12d}"
    )
    lines.append(
        f"  {'对冲总盈亏':20s} | {s5.hedge_pnl_total:>16,.0f} | {s6.hedge_pnl_total:>16,.0f} | {s6.hedge_pnl_total - s5.hedge_pnl_total:>12,.0f}"  # noqa: E501
    )
    lines.append(
        f"  {'对冲成本':20s} | {s5_hedge_cost:>16,.0f} | {s6_hedge_cost:>16,.0f} | {s6_hedge_cost - s5_hedge_cost:>12,.0f}"  # noqa: E501
    )
    lines.append(
        f"  {'交易成本':20s} | {sum(s5.transaction_costs):>16,.0f} | {sum(s6.transaction_costs):>16,.0f} | {sum(s6.transaction_costs) - sum(s5.transaction_costs):>12,.0f}"  # noqa: E501
    )
    lines.append(
        f"  {'总成本':20s} | {s5_total_cost:>16,.0f} | {s6_total_cost:>16,.0f} | {s6_total_cost - s5_total_cost:>12,.0f}"  # noqa: E501
    )
    lines.append(
        f"  {'交易次数':20s} | {s5.trade_count:>16d} | {s6.trade_count:>16d} | {s6.trade_count - s5.trade_count:>12d}"
    )
    s5_daily_pnl = s5.hedge_pnl_total / s5.hedge_days if s5.hedge_days > 0 else 0.0
    s6_daily_pnl = s6.hedge_pnl_total / s6.hedge_days if s6.hedge_days > 0 else 0.0
    lines.append(
        f"  {'对冲日均盈亏':20s} | {s5_daily_pnl:>16,.0f} | {s6_daily_pnl:>16,.0f} | {s6_daily_pnl - s5_daily_pnl:>12,.0f}"  # noqa: E501
    )
    lines.append("")

    lines.append("[4] 逐年绩效对比")
    lines.append("-" * 90)
    if s5.yearly_stats and s6.yearly_stats:
        lines.append(
            f"  {'年份':6s} | {'S5年化':>8s} {'S5回撤':>8s} {'S5夏普':>8s} | {'S6年化':>8s} {'S6回撤':>8s} {'S6夏普':>8s} | {'Δ夏普':>8s}"  # noqa: E501
        )
        lines.append("  " + "-" * 80)
        for ys5, ys6 in zip(s5.yearly_stats, s6.yearly_stats, strict=False):
            year = ys5["year"]
            s5_sharpe = (ys5["return"] - RISK_FREE_RATE) / max(ys5["volatility"], 0.001)
            s6_sharpe = (ys6["return"] - RISK_FREE_RATE) / max(ys6["volatility"], 0.001)
            lines.append(
                f"  {year:6d} | {ys5['return']:>7.1%} {ys5['max_drawdown']:>7.1%} {s5_sharpe:>8.3f} | "
                f"{ys6['return']:>7.1%} {ys6['max_drawdown']:>7.1%} {s6_sharpe:>8.3f} | {s6_sharpe - s5_sharpe:>8.3f}"
            )
    lines.append("")

    lines.append("[5] 结论")
    lines.append("-" * 90)
    delta_sharpe = s6.sharpe_ratio - s5.sharpe_ratio
    delta_dd = s6.max_drawdown - s5.max_drawdown
    delta_ret = s6.annual_return - s5.annual_return

    if delta_sharpe > 0.01:
        lines.append(f"  ✅ v8.7 RegimeFolio动态阈值 夏普改善 +{delta_sharpe:.4f}")
    elif delta_sharpe > -0.01:
        lines.append(f"  — v8.7 与 v5.9 夏普基本持平 (Δ={delta_sharpe:+.4f})")
    else:
        lines.append(f"  ❌ v8.7 夏普下降 {delta_sharpe:.4f}")

    if delta_dd < -0.005:
        lines.append(f"  ✅ v8.7 最大回撤改善 {delta_dd:.2%}")
    elif delta_dd < 0.005:
        lines.append(f"  — v8.7 与 v5.9 回撤基本持平 (Δ={delta_dd:+.2%})")
    else:
        lines.append(f"  ❌ v8.7 回撤增加 {delta_dd:+.2%}")

    if delta_ret > 0.005:
        lines.append(f"  ✅ v8.7 年化收益改善 +{delta_ret:.2%}")
    elif delta_ret > -0.005:
        lines.append(f"  — v8.7 与 v5.9 收益基本持平 (Δ={delta_ret:+.2%})")
    else:
        lines.append(f"  ❌ v8.7 收益下降 {delta_ret:.2%}")

    lines.append("")
    lines.append(
        f"  对冲激活天数: S5={s5.hedge_days} vs S6={s6.hedge_days} (v8.7动态阈值更敏感)"
    )
    lines.append(
        f"  VIX regime分布: 低波{vix_stats['regime_pct']['low_vol'] * 100:.1f}% / 正常{vix_stats['regime_pct']['normal'] * 100:.1f}% / 高波{vix_stats['regime_pct']['high_vol'] * 100:.1f}% / 危机{vix_stats['regime_pct']['crisis'] * 100:.1f}%"  # noqa: E501
    )
    lines.append("")
    lines.append("=" * 90)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="v8.7 对冲优化回测对比")
    parser.add_argument("--force-dl", action="store_true", help="强制重新下载数据")
    parser.add_argument("--output-dir", default=None, help="报告输出目录")
    args = parser.parse_args()

    t0 = time.time()

    print("=" * 60)
    print("  v8.7 对冲优化回测对比 — v5.9 vs v8.7")
    print("=" * 60)

    print("\n[1/4] 加载数据 (baostock)...")
    loader = BacktestDataLoader()
    loader.load_or_download(PORTFOLIO_CODES, START_DATE, END_DATE, retry=args.force_dl)
    loader.load_multi_index(START_DATE, END_DATE)
    print(f"  已加载 {len(loader._price_data)}/{len(PORTFOLIO_CODES)} 只股票")
    print(f"  已加载 {len(loader._index_data)} 个指数")

    betas, _ = compute_multi_index_beta_weights()
    print(
        f"  组合多指数Beta: IF={betas['IF']:.2f}, IC={betas['IC']:.2f}, IM={betas['IM']:.2f}"
    )

    print("\n[2/4] 构建价格矩阵...")
    price_df, csi300_ret, index_rets = loader.build_unified_dataframe(
        START_DATE, END_DATE
    )
    n_days = len(price_df)
    print(
        f"  交易日: {n_days} | 日期: {price_df.index[0].date()} ~ {price_df.index[-1].date()}"
    )

    print("\n[3/4] 运行六策略回测...")
    bt = HedgeRebalanceBacktest(price_df, csi300_ret, index_rets)
    multi = bt.run_all()

    s5 = multi.strategies[4]
    s6 = multi.strategies[5]

    print("\n[4/4] 生成 v5.9 vs v8.7 对比报告...")
    vix_stats = compute_vix_regime_stats(csi300_ret, n_days)
    report = format_v87_comparison(s5, s6, vix_stats)

    output_dir = args.output_dir or os.path.join(
        os.path.dirname(__file__), "..", "reports"
    )
    os.makedirs(output_dir, exist_ok=True)
    fpath = os.path.join(
        output_dir, f"v87_backtest_comparison_{now_bj().strftime('%Y-%m-%d')}.md"
    )
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\n  报告已保存: {fpath}")
    print(f"  耗时: {time.time() - t0:.1f}秒")
    print()
    print(report)


if __name__ == "__main__":
    main()
