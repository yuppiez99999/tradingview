"""
ETF期权对冲子组合 — 最优方案回测 v2
=====================================
核心改进 (vs v1):
  1. 真实期权保护模拟: BS定价 + 每日盯市内在价值, 下跌时认沽提供保护
  2. 动态波动率定价: 20日滚动波动率计算IV, 替代固定值
  3. 修复S5尾部对冲逻辑
  4. 新增策略: S6条件性认沽 / S7领口Collar / S8动态对冲比例
  5. 完整风险指标: Calmar / Sortino / 胜率 / 盈亏比

数据: data/etf_option_backtest/all_etf_daily.parquet (14 ETF, 2021-2026)
配置: config/etf_option_subportfolio.yaml
运行: python data/etf_option_backtest/run_etf_option_backtest_v2.py
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from utils.datetime_utils import now_bj

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config" / "etf_option_subportfolio.yaml"

INITIAL_CAPITAL = 2_000_000
TRANSACTION_COST = 0.0003
SLIPPAGE = 0.001
REBALANCE_THRESHOLD = 0.06
RISK_FREE_RATE = 0.02
TRADING_DAYS = 252

OPTION_UNDERLYING_ETFS = {"510300", "510050", "588000", "159915"}
OPTION_MULTIPLIER = 10000
OTM_PCT = 0.05
OPTION_ROLL_DAYS = 30
HEDGE_RATIO_BASE = 0.50

BENCHMARK = "510300"


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_put_price(spot: float, strike: float, dte: int, iv: float, r: float = 0.02) -> float:
    if spot <= 0 or strike <= 0 or dte <= 0 or iv <= 0:
        return 0.0
    T = dte / 365.0  # noqa: N806
    sqrtT = math.sqrt(T)  # noqa: N806
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * T) / (iv * sqrtT)
    d2 = d1 - iv * sqrtT
    return strike * math.exp(-r * T) * norm_cdf(-d2) - spot * norm_cdf(-d1)


def bs_call_price(spot: float, strike: float, dte: int, iv: float, r: float = 0.02) -> float:
    if spot <= 0 or strike <= 0 or dte <= 0 or iv <= 0:
        return 0.0
    T = dte / 365.0  # noqa: N806
    sqrtT = math.sqrt(T)  # noqa: N806
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * T) / (iv * sqrtT)
    d2 = d1 - iv * sqrtT
    return spot * norm_cdf(d1) - strike * math.exp(-r * T) * norm_cdf(d2)


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_etf_prices() -> pd.DataFrame:
    merged = DATA_DIR / "all_etf_daily.parquet"
    if merged.exists():
        df = pd.read_parquet(merged)
    else:
        frames = []
        for p in sorted(DATA_DIR.glob("*.parquet")):
            if p.name.startswith("all_"):
                continue
            code = p.stem
            tmp = pd.read_parquet(p)
            tmp["code"] = code
            frames.append(tmp)
        df = pd.concat(frames, ignore_index=True)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
    if "code" not in df.columns:
        return df
    df["code"] = df["code"].str.split(".").str[0]
    close_col = "close" if "close" in df.columns else "CLOSE"
    pivot = df.pivot_table(index=df.index, columns="code", values=close_col)
    return pivot.sort_index()


def compute_rolling_iv(prices: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    log_ret = np.log(prices / prices.shift(1))
    rolling_vol = log_ret.rolling(window=window, min_periods=10).std() * math.sqrt(TRADING_DAYS)
    rolling_vol = rolling_vol.fillna(0.22)
    rolling_vol = rolling_vol.clip(lower=0.10, upper=0.60)
    return rolling_vol


def extract_target_weights(cfg: dict) -> dict[str, float]:
    weights = {}
    for code, info in cfg.get("positions", {}).items():
        pure_code = code.split(".")[0]
        weights[pure_code] = info["target_weight"]
    return weights


@dataclass
class OptionPosition:
    underlying: str
    strike: float
    contracts: int
    entry_date: int
    expiry_date: int
    premium_paid: float
    option_type: str = "PUT"
    call_strike: float = 0.0
    call_premium: float = 0.0

    def intrinsic_value(self, spot: float) -> float:
        if self.option_type == "PUT":
            put_iv = max(self.strike - spot, 0.0) * self.contracts * OPTION_MULTIPLIER
        else:
            put_iv = 0.0
        if self.call_strike > 0:
            call_iv = -max(spot - self.call_strike, 0.0) * self.contracts * OPTION_MULTIPLIER
        else:
            call_iv = 0.0
        return put_iv + call_iv

    def net_cost(self) -> float:
        return self.premium_paid - self.call_premium * self.contracts * OPTION_MULTIPLIER


@dataclass
class BacktestState:
    cash: float = INITIAL_CAPITAL
    pos: dict[str, int] = field(default_factory=dict)
    option_positions: list[OptionPosition] = field(default_factory=list)
    total_premium_paid: float = 0.0
    total_premium_recovered: float = 0.0
    total_tc: float = 0.0


def apply_trade_cost(trade_value: float) -> float:
    return abs(trade_value) * (TRANSACTION_COST + SLIPPAGE)


def rebalance_to_target(state: BacktestState, tw: dict[str, float],
                        px: dict[str, float], codes: list[str]) -> None:
    sv = sum(state.pos.get(c, 0) * px.get(c, 0) for c in codes)
    total = state.cash + sv
    if total <= 0:
        return
    max_dev = 0.0
    for c in codes:
        if c in px:
            cur_w = state.pos.get(c, 0) * px[c] / total
            dev = abs(cur_w - tw[c])
            max_dev = max(max_dev, dev)
    if max_dev < REBALANCE_THRESHOLD:
        return
    for c in codes:
        if c in px:
            target_amt = total * tw[c]
            cur_amt = state.pos.get(c, 0) * px[c]
            diff = target_amt - cur_amt
            if abs(diff) < 1000:
                continue
            delta_shares = int(diff / px[c] / 100) * 100
            if delta_shares == 0:
                continue
            cost = apply_trade_cost(delta_shares * px[c])
            state.pos[c] = state.pos.get(c, 0) + delta_shares
            state.cash -= delta_shares * px[c] + cost
            state.total_tc += cost


def init_positions(state: BacktestState, tw: dict[str, float],
                   px: dict[str, float], codes: list[str]) -> None:
    for c in codes:
        if c in px and px[c] > 0:
            target_amt = INITIAL_CAPITAL * tw[c]
            shares = int(target_amt / px[c] / 100) * 100
            cost = apply_trade_cost(shares * px[c])
            state.pos[c] = shares
            state.cash -= shares * px[c] + cost
            state.total_tc += cost


def etf_value(state: BacktestState, px: dict[str, float], codes: list[str]) -> float:
    return sum(state.pos.get(c, 0) * px.get(c, 0) for c in codes)


def options_value(state: BacktestState, px: dict[str, float]) -> float:
    return sum(opt.intrinsic_value(px.get(opt.underlying, 0)) for opt in state.option_positions)


def total_value(state: BacktestState, px: dict[str, float], codes: list[str]) -> float:
    etf_val = etf_value(state, px, codes)
    opt_val = options_value(state, px)
    return state.cash + etf_val + opt_val


def roll_options(state: BacktestState, px: dict[str, float], iv_row: pd.Series,
                 current_i: int, hedge_ratio: float = HEDGE_RATIO_BASE,
                 collar: bool = False, conditional_vol: float | None = None,
                 vol_threshold: float = 0.18) -> None:
    for opt in state.option_positions:
        if current_i >= opt.expiry_date:
            spot = px.get(opt.underlying, 0)
            recover = opt.intrinsic_value(spot)
            state.cash += recover
            state.total_premium_recovered += recover
    state.option_positions = [o for o in state.option_positions if current_i < o.expiry_date]

    if conditional_vol is not None and conditional_vol < vol_threshold:
        return

    portfolio_val = total_value(state, px, list(px.keys()))
    if portfolio_val <= 0:
        return

    rolls_per_year = TRADING_DAYS / OPTION_ROLL_DAYS
    annual_cost_budget = portfolio_val * 0.025
    per_roll_budget = annual_cost_budget / rolls_per_year
    n_underlying = len([c for c in OPTION_UNDERLYING_ETFS if c in px])
    if n_underlying == 0:
        return
    budget_per_etf = per_roll_budget * hedge_ratio / n_underlying

    for code in OPTION_UNDERLYING_ETFS:
        if code not in px:
            continue
        spot = px[code]
        if spot <= 0:
            continue
        already_hedged = any(o.underlying == code for o in state.option_positions)
        if already_hedged:
            continue

        iv = float(iv_row.get(code, 0.22)) if iv_row is not None else 0.22
        strike = round(spot * (1 - OTM_PCT), 4)
        dte = OPTION_ROLL_DAYS
        put_premium = bs_put_price(spot, strike, dte, iv)
        if put_premium <= 0:
            continue

        cost_per_contract = put_premium * OPTION_MULTIPLIER
        contracts = int(budget_per_etf / cost_per_contract)
        contracts = max(0, min(contracts, 50))
        if contracts == 0:
            continue

        premium_total = put_premium * contracts * OPTION_MULTIPLIER

        call_strike = 0.0
        call_premium = 0.0
        if collar:
            call_strike = round(spot * (1 + OTM_PCT), 4)
            call_premium = bs_call_price(spot, call_strike, dte, iv)

        state.cash -= premium_total
        state.total_premium_paid += premium_total
        if collar and call_premium > 0:
            call_total = call_premium * contracts * OPTION_MULTIPLIER
            state.cash += call_total
            state.total_premium_recovered += call_total

        state.option_positions.append(OptionPosition(
            underlying=code,
            strike=strike,
            contracts=contracts,
            entry_date=current_i,
            expiry_date=current_i + OPTION_ROLL_DAYS,
            premium_paid=premium_total,
            option_type="PUT",
            call_strike=call_strike,
            call_premium=call_premium,
        ))


def run_strategy(prices: pd.DataFrame, tw: dict[str, float], iv_df: pd.DataFrame,  # noqa: C901
                 strategy: str) -> tuple[list[float], BacktestState]:
    codes = [c for c in tw if c in prices.columns]
    state = BacktestState()
    eq = []
    first = True
    peak = INITIAL_CAPITAL
    list(prices.index)

    for i, (_date, row) in enumerate(prices.iterrows()):
        px = {c: row[c] for c in codes if c in row and not np.isnan(row[c])}
        iv_row = iv_df.iloc[i] if i < len(iv_df) else None

        if first and px:
            init_positions(state, tw, px, codes)
            first = False

        if strategy in ("s2_rebalance", "s4_full", "s5_tail", "s6_conditional",
                        "s7_collar", "s8_dynamic"):
            rebalance_to_target(state, tw, px, codes)

        if strategy == "s3_option" or strategy == "s4_full":
            if i % OPTION_ROLL_DAYS == 0:
                roll_options(state, px, iv_row, i)

        elif strategy == "s5_tail":
            tv = total_value(state, px, codes)
            peak = max(peak, tv)
            dd = (peak - tv) / peak if peak > 0 else 0
            if dd > 0.15:
                hr = 1.0
            elif dd > 0.10:
                hr = 0.75
            else:
                hr = 0.50
            has_expired = any(i >= opt.expiry_date for opt in state.option_positions)
            if i % OPTION_ROLL_DAYS == 0 or (dd > 0.10 and has_expired):
                roll_options(state, px, iv_row, i, hedge_ratio=hr)

        elif strategy == "s6_conditional":
            avg_vol = float(np.mean([iv_row.get(c, 0.22) for c in OPTION_UNDERLYING_ETFS
                                     if c in iv_row])) if iv_row is not None else 0.22
            if i % OPTION_ROLL_DAYS == 0:
                roll_options(state, px, iv_row, i, conditional_vol=avg_vol,
                             vol_threshold=0.18)

        elif strategy == "s7_collar":
            if i % OPTION_ROLL_DAYS == 0:
                roll_options(state, px, iv_row, i, collar=True)

        elif strategy == "s8_dynamic":
            avg_vol = float(np.mean([iv_row.get(c, 0.22) for c in OPTION_UNDERLYING_ETFS
                                     if c in iv_row])) if iv_row is not None else 0.22
            hr = 0.30 + (avg_vol - 0.15) * 2.0
            hr = max(0.20, min(1.20, hr))
            if i % OPTION_ROLL_DAYS == 0:
                roll_options(state, px, iv_row, i, hedge_ratio=hr)

        tv = total_value(state, px, codes)
        eq.append(tv)

    return eq, state


def run_benchmark(prices: pd.DataFrame) -> list[float]:
    if BENCHMARK not in prices.columns:
        return [INITIAL_CAPITAL] * len(prices)
    first_px = prices[BENCHMARK].iloc[0]
    shares = INITIAL_CAPITAL / first_px
    return [shares * px for px in prices[BENCHMARK]]


def compute_metrics(eq: list[float], benchmark_eq: list[float]) -> dict:
    eq_arr = np.array(eq, dtype=float)
    n = len(eq_arr)
    if n < 2:
        return {}
    daily_ret = np.diff(eq_arr) / eq_arr[:-1]
    total_ret = eq_arr[-1] / eq_arr[0] - 1
    years = n / TRADING_DAYS
    annual_ret = (1 + total_ret) ** (1 / years) - 1 if years > 0 else 0
    peak = np.maximum.accumulate(eq_arr)
    drawdown = (peak - eq_arr) / peak
    max_dd = float(np.max(drawdown))
    vol = float(np.std(daily_ret) * np.sqrt(TRADING_DAYS)) if n > 1 else 0
    sharpe = (annual_ret - RISK_FREE_RATE) / vol if vol > 0 else 0
    downside_ret = daily_ret[daily_ret < 0]
    downside_vol = float(np.std(downside_ret) * np.sqrt(TRADING_DAYS)) if len(downside_ret) > 1 else 0
    sortino = (annual_ret - RISK_FREE_RATE) / downside_vol if downside_vol > 0 else 0
    calmar = annual_ret / max_dd if max_dd > 0 else 0
    win_rate = float(np.mean(daily_ret > 0)) if n > 1 else 0
    wins = daily_ret[daily_ret > 0]
    losses = daily_ret[daily_ret < 0]
    profit_loss_ratio = float(np.mean(wins) / abs(np.mean(losses))) if len(wins) > 0 and len(losses) > 0 else 0
    bench_arr = np.array(benchmark_eq, dtype=float)
    bench_ret = bench_arr[-1] / bench_arr[0] - 1
    bench_annual = (1 + bench_ret) ** (1 / years) - 1 if years > 0 else 0
    excess = annual_ret - bench_annual
    return {
        "total_return": float(total_ret),
        "annual_return": float(annual_ret),
        "max_drawdown": float(max_dd),
        "volatility": float(vol),
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "calmar": float(calmar),
        "win_rate": float(win_rate),
        "profit_loss_ratio": float(profit_loss_ratio),
        "benchmark_annual": float(bench_annual),
        "excess_return": float(excess),
        "final_value": float(eq_arr[-1]),
        "n_days": int(n),
    }


def format_report(results: dict, prices: pd.DataFrame, states: dict) -> str:
    lines = []
    lines.append("=" * 110)
    lines.append("  ETF期权对冲子组合 — 最优方案回测 v2 (真实期权保护模拟 + 动态IV)")
    lines.append("=" * 110)
    lines.append(f"  回测区间: {prices.index[0].date()} ~ {prices.index[-1].date()} | 初始资金: {INITIAL_CAPITAL:,}元")
    lines.append(f"  ETF数: {len(prices.columns)} | 期权标的: {sorted(OPTION_UNDERLYING_ETFS)} | 期权乘数: {OPTION_MULTIPLIER}")  # noqa: E501
    lines.append(f"  OTM: {OTM_PCT*100:.0f}% | 滚仓周期: {OPTION_ROLL_DAYS}天 | 基准: {BENCHMARK}")
    lines.append("")
    lines.append(f"{'策略':<32} {'年化%':>7} {'回撤%':>7} {'Sharpe':>7} {'Sortino':>7} {'Calmar':>7} {'胜率%':>6} {'期末':>12} {'权利金':>10} {'赔付':>10}")  # noqa: E501
    lines.append("-" * 115)
    for name, m in results.items():
        s = states.get(name)
        prem = s.total_premium_paid if s else 0
        rec = s.total_premium_recovered if s else 0
        lines.append(
            f"{name:<32} {m['annual_return']*100:>6.2f} {m['max_drawdown']*100:>6.2f} "
            f"{m['sharpe']:>6.3f} {m['sortino']:>6.3f} {m['calmar']:>6.3f} "
            f"{m['win_rate']*100:>5.1f} {m['final_value']:>11,.0f} {prem:>9,.0f} {rec:>9,.0f}"
        )
    lines.append("")
    lines.append("  目标: 年化 >= 8% / 回撤 < 15% / Sharpe >= 0.80")
    best_name = max(
        (k for k in results if k != "基准 沪深300ETF"),
        key=lambda k: results[k]["sharpe"],
    )
    best = results[best_name]
    lines.append(f"  最优策略(Sharpe): {best_name} — 年化 {best['annual_return']*100:.2f}% / 回撤 {best['max_drawdown']*100:.2f}% / Sharpe {best['sharpe']:.3f}")  # noqa: E501
    target_achieved = best["annual_return"] >= 0.08 and best["max_drawdown"] < 0.15
    lines.append(f"  达标: {'是' if target_achieved else '否'} (年化>8% 且 回撤<15%)")
    lines.append("")
    lines.append("  期权保护效果分析 (真实模拟 vs v1固定成本):")
    for name in ("S3 真实认沽(静态+保护)", "S4 再平衡+真实认沽", "S5 尾部对冲(回撤加码)",
                 "S6 条件性认沽(高波动)", "S7 领口策略(Collar)", "S8 动态对冲比例"):
        if name not in results or name not in states:
            continue
        m = results[name]
        s = states[name]
        net_cost = s.total_premium_paid - s.total_premium_recovered
        protection_ratio = s.total_premium_recovered / s.total_premium_paid * 100 if s.total_premium_paid > 0 else 0
        lines.append(
            f"    {name:<30} 权利金 {s.total_premium_paid:>8,.0f} / 赔付 {s.total_premium_recovered:>8,.0f} "
            f"/ 净成本 {net_cost:>8,.0f} / 保护率 {protection_ratio:>5.1f}% / 回撤 {m['max_drawdown']*100:.2f}%"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    logger.info("加载配置 %s", CONFIG_PATH)
    cfg = load_config()
    tw = extract_target_weights(cfg)
    logger.info("目标权重: %d ETF, 总和 %.4f", len(tw), sum(tw.values()))

    logger.info("加载ETF价格数据...")
    prices = load_etf_prices()
    logger.info("  %d 交易日, %d ETF, %s ~ %s",
                len(prices), len(prices.columns),
                prices.index[0].date(), prices.index[-1].date())

    logger.info("计算20日滚动波动率(动态IV)...")
    iv_df = compute_rolling_iv(prices, window=20)
    logger.info("  IV范围: %.2f ~ %.2f (均值 %.2f)",
                float(iv_df.values.min()), float(iv_df.values.max()), float(np.nanmean(iv_df.values)))

    bench_eq = run_benchmark(prices)

    strategies = [
        ("S1 基线(静态无对冲)", "s1_baseline"),
        ("S2 再平衡(6%阈值)", "s2_rebalance"),
        ("S3 真实认沽(静态+保护)", "s3_option"),
        ("S4 再平衡+真实认沽", "s4_full"),
        ("S5 尾部对冲(回撤加码)", "s5_tail"),
        ("S6 条件性认沽(高波动)", "s6_conditional"),
        ("S7 领口策略(Collar)", "s7_collar"),
        ("S8 动态对冲比例", "s8_dynamic"),
    ]

    results = {}
    states = {}
    for name, strat_id in strategies:
        logger.info("运行 %s ...", name)
        eq, state = run_strategy(prices, tw, iv_df, strat_id)
        m = compute_metrics(eq, bench_eq)
        m["transaction_costs"] = float(state.total_tc)
        m["total_premium_paid"] = float(state.total_premium_paid)
        m["total_premium_recovered"] = float(state.total_premium_recovered)
        results[name] = m
        states[name] = state
        logger.info("  %s: 年化 %.2f%% / 回撤 %.2f%% / Sharpe %.3f / 权利金 %.0f / 赔付 %.0f",
                    name, m["annual_return"] * 100, m["max_drawdown"] * 100, m["sharpe"],
                    state.total_premium_paid, state.total_premium_recovered)

    bench_m = compute_metrics(bench_eq, bench_eq)
    results["基准 沪深300ETF"] = bench_m

    report = format_report(results, prices, states)

    timestamp = now_bj().strftime("%Y%m%d_%H%M%S")
    report_path = DATA_DIR / f"backtest_report_v2_{timestamp}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info("报告已保存: %s", report_path)

    json_path = DATA_DIR / f"backtest_result_v2_{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "version": "v2",
            "generated_at": now_bj().isoformat(),
            "start_date": str(prices.index[0].date()),
            "end_date": str(prices.index[-1].date()),
            "initial_capital": INITIAL_CAPITAL,
            "n_etfs": len(prices.columns),
            "n_days": len(prices),
            "option_config": {
                "underlying_etfs": sorted(OPTION_UNDERLYING_ETFS),
                "otm_pct": OTM_PCT,
                "roll_days": OPTION_ROLL_DAYS,
                "multiplier": OPTION_MULTIPLIER,
                "hedge_ratio_base": HEDGE_RATIO_BASE,
            },
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    logger.info("结果已保存: %s", json_path)

    return results, states


if __name__ == "__main__":
    main()
