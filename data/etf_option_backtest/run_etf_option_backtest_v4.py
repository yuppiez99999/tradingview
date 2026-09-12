"""
ETF期权对冲子组合 — v4 自适应动态再平衡 + 预测年化
=====================================================
vs v3改进:
  1. 自适应再平衡阈值: 基于波动率动态调整(高波动收紧/低波动放宽)
  2. 动量趋势过滤: MA60判断牛熊, 熊市再平衡时额外减仓
  3. 波动率调权: 高波动增防御/低波动增进攻
  4. 自适应熔断阈值: 波动率调整熔断触发点
  5. 预测年化: 蒙卡洛10000路径 + 分年度稳定性 + 95%置信区间

运行: python data/etf_option_backtest/run_etf_option_backtest_v4.py
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
RISK_FREE_RATE = 0.02
TRADING_DAYS = 252

OPTION_UNDERLYING_ETFS = {"510300", "510050", "588000", "159915"}
OPTION_MULTIPLIER = 10000
OTM_PCT = 0.05
OPTION_ROLL_DAYS = 30
HEDGE_RATIO_BASE = 0.50
ANNUAL_OPTION_BUDGET = 0.035

BENCHMARK = "510300"
TREND_MA_WINDOW = 60

FIFTEEN_FIVE_SCORES = {
    "588000": 92, "159915": 85, "512100": 82, "510500": 80,
    "510300": 75, "510310": 55, "518880": 45,
}

BASE_WEIGHTS: dict[str, float] = {
    "510300": 0.15, "510500": 0.07, "510050": 0.10, "512100": 0.09,
    "588000": 0.10, "159915": 0.08, "512480": 0.05, "512010": 0.06,
    "512660": 0.04, "515170": 0.03, "159939": 0.03,
    "518880": 0.10, "511260": 0.05, "510310": 0.05,
}

DEFENSIVE_ETFS = {"518880", "511260", "510310"}
OFFENSIVE_ETFS = {"588000", "159915", "512100", "512480", "515170", "159939"}


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
            tmp = pd.read_parquet(p)
            tmp["code"] = p.stem
            frames.append(tmp)
        df = pd.concat(frames, ignore_index=True)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
    df["code"] = df["code"].str.split(".").str[0]
    close_col = "close" if "close" in df.columns else "CLOSE"
    return df.pivot_table(index=df.index, columns="code", values=close_col).sort_index()


def compute_rolling_iv(prices: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    log_ret = np.log(prices / prices.shift(1))
    vol = log_ret.rolling(window=window, min_periods=10).std() * math.sqrt(TRADING_DAYS)
    return vol.fillna(0.22).clip(lower=0.10, upper=0.60)


def compute_trend_signal(prices: pd.DataFrame, window: int = TREND_MA_WINDOW,
                         confirm_days: int = 5) -> pd.Series:
    if BENCHMARK in prices.columns:
        ma = prices[BENCHMARK].rolling(window=window, min_periods=20).mean()
        above = (prices[BENCHMARK] > ma).astype(int)
        below = (1 - above)
        below_streak = below.rolling(window=confirm_days, min_periods=confirm_days).sum()
        confirmed_bear = (below_streak >= confirm_days).astype(float)
        return 1.0 - confirmed_bear
    return pd.Series(1.0, index=prices.index)


def adaptive_rebalance_threshold(avg_vol: float) -> float:
    if avg_vol < 0.15:
        return 0.06
    if avg_vol < 0.25:
        return 0.04
    return 0.02


def adaptive_drawdown_levels(avg_vol: float) -> list[tuple[float, float]]:
    adj = max(0.85, min(1.15, avg_vol / 0.22))
    return [
        (0.12 * adj, 1.00),
        (0.15 * adj, 0.70),
        (0.18 * adj, 0.50),
        (1.00, 0.30),
    ]


def vol_adjusted_weights(base_tw: dict[str, float], avg_vol: float) -> dict[str, float]:
    if avg_vol > 0.25:
        def_scale, off_scale = 1.3, 0.8
    elif avg_vol < 0.15:
        def_scale, off_scale = 0.9, 1.2
    else:
        return dict(base_tw)

    adjusted = {}
    for code, w in base_tw.items():
        if code in DEFENSIVE_ETFS:
            adjusted[code] = w * def_scale
        elif code in OFFENSIVE_ETFS:
            adjusted[code] = w * off_scale
        else:
            adjusted[code] = w
    total = sum(adjusted.values())
    return {k: v / total for k, v in adjusted.items()} if total > 0 else dict(base_tw)


@dataclass
class OptionPosition:
    underlying: str
    strike: float
    contracts: int
    entry_date: int
    expiry_date: int
    premium_paid: float

    def intrinsic_value(self, spot: float) -> float:
        return max(self.strike - spot, 0.0) * self.contracts * OPTION_MULTIPLIER


@dataclass
class BacktestState:
    cash: float = INITIAL_CAPITAL
    pos: dict[str, int] = field(default_factory=dict)
    option_positions: list[OptionPosition] = field(default_factory=list)
    total_premium_paid: float = 0.0
    total_premium_recovered: float = 0.0
    total_tc: float = 0.0
    current_exposure_target: float = 1.0
    breaker_count: int = 0
    rebalance_count: int = 0
    trend_filter_count: int = 0
    vol_adjust_count: int = 0


def apply_trade_cost(tv: float) -> float:
    return abs(tv) * (TRANSACTION_COST + SLIPPAGE)


def etf_value(state: BacktestState, px: dict[str, float], codes: list[str]) -> float:
    return sum(state.pos.get(c, 0) * px.get(c, 0) for c in codes)


def options_value(state: BacktestState, px: dict[str, float]) -> float:
    return sum(opt.intrinsic_value(px.get(opt.underlying, 0)) for opt in state.option_positions)


def total_value(state: BacktestState, px: dict[str, float], codes: list[str]) -> float:
    return state.cash + etf_value(state, px, codes) + options_value(state, px)


def apply_drawdown_breaker(state: BacktestState, px: dict[str, float],
                           codes: list[str], peak: float,
                           dd_levels: list[tuple[float, float]]) -> float:
    tv = total_value(state, px, codes)
    dd = (peak - tv) / peak if peak > 0 else 0

    target_exposure = 1.0
    for threshold, exposure in dd_levels:
        if dd >= threshold:
            target_exposure = exposure

    if dd < 0.08:
        state.current_exposure_target = 1.0
    elif target_exposure < state.current_exposure_target:
        state.current_exposure_target = target_exposure
        state.breaker_count += 1

    if state.current_exposure_target < 1.0:
        ev = etf_value(state, px, codes)
        if ev > 0 and tv > 0:
            cur_exp = ev / tv
            if cur_exp > state.current_exposure_target + 0.02:
                ratio = state.current_exposure_target / cur_exp
                for c in codes:
                    if c in px and px[c] > 0:
                        old = state.pos.get(c, 0)
                        if old <= 0:
                            continue
                        new = int(old * ratio / 100) * 100
                        delta = old - new
                        if delta > 0:
                            cost = apply_trade_cost(delta * px[c])
                            state.pos[c] = new
                            state.cash += delta * px[c] - cost
                            state.total_tc += cost
    return dd


def adaptive_rebalance(state: BacktestState, tw: dict[str, float],
                       px: dict[str, float], codes: list[str],
                       threshold: float, trend_bull: bool) -> None:
    effective_tw = tw
    exposure = state.current_exposure_target
    if not trend_bull and exposure > 0.8:
        exposure = 0.8
        state.trend_filter_count += 1

    sv = etf_value(state, px, codes)
    total = state.cash + sv
    if total <= 0:
        return

    max_dev = 0.0
    for c in codes:
        if c in px:
            cur_w = state.pos.get(c, 0) * px[c] / total
            dev = abs(cur_w - effective_tw.get(c, 0) * exposure)
            max_dev = max(max_dev, dev)
    if max_dev < threshold:
        return

    state.rebalance_count += 1
    for c in codes:
        if c in px:
            target_amt = total * effective_tw.get(c, 0) * exposure
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


def roll_options(state: BacktestState, px: dict[str, float], iv_row: pd.Series,
                 current_i: int, hedge_ratio: float = HEDGE_RATIO_BASE) -> None:
    for opt in state.option_positions:
        if current_i >= opt.expiry_date:
            spot = px.get(opt.underlying, 0)
            recover = opt.intrinsic_value(spot)
            state.cash += recover
            state.total_premium_recovered += recover
    state.option_positions = [o for o in state.option_positions if current_i < o.expiry_date]

    portfolio_val = total_value(state, px, list(px.keys()))
    if portfolio_val <= 0:
        return

    rolls_per_year = TRADING_DAYS / OPTION_ROLL_DAYS
    per_roll_budget = portfolio_val * ANNUAL_OPTION_BUDGET / rolls_per_year
    n_und = len([c for c in OPTION_UNDERLYING_ETFS if c in px])
    if n_und == 0:
        return
    budget_per_etf = per_roll_budget * hedge_ratio / n_und

    for code in OPTION_UNDERLYING_ETFS:
        if code not in px:
            continue
        spot = px[code]
        if spot <= 0:
            continue
        if any(o.underlying == code for o in state.option_positions):
            continue

        iv = float(iv_row.get(code, 0.22)) if iv_row is not None else 0.22
        strike = round(spot * (1 - OTM_PCT), 4)
        put_prem = bs_put_price(spot, strike, OPTION_ROLL_DAYS, iv)
        if put_prem <= 0:
            continue

        cost_per_ct = put_prem * OPTION_MULTIPLIER
        contracts = max(0, min(int(budget_per_etf / cost_per_ct), 60))
        if contracts == 0:
            continue

        prem_total = put_prem * contracts * OPTION_MULTIPLIER
        state.cash -= prem_total
        state.total_premium_paid += prem_total
        state.option_positions.append(OptionPosition(
            underlying=code, strike=strike, contracts=contracts,
            entry_date=current_i, expiry_date=current_i + OPTION_ROLL_DAYS,
            premium_paid=prem_total,
        ))


def run_v4_strategy(prices: pd.DataFrame, base_tw: dict[str, float],
                    iv_df: pd.DataFrame, trend_signal: pd.Series) -> tuple[list[float], BacktestState, list[dict]]:
    codes = [c for c in base_tw if c in prices.columns]
    state = BacktestState()
    eq = []
    daily_log = []
    first = True
    peak = INITIAL_CAPITAL

    for i, (date, row) in enumerate(prices.iterrows()):
        px = {c: row[c] for c in codes if c in row and not np.isnan(row[c])}
        iv_row = iv_df.iloc[i] if i < len(iv_df) else None

        avg_vol = float(np.nanmean([iv_row.get(c, 0.22) for c in OPTION_UNDERLYING_ETFS
                                    if c in iv_row])) if iv_row is not None else 0.22
        trend_bull = bool(trend_signal.iloc[i]) if i < len(trend_signal) else True

        adaptive_tw = vol_adjusted_weights(base_tw, avg_vol)
        if adaptive_tw != base_tw:
            state.vol_adjust_count += 1

        threshold = adaptive_rebalance_threshold(avg_vol)
        dd_levels = adaptive_drawdown_levels(avg_vol)

        if first and px:
            init_positions(state, adaptive_tw, px, codes)
            first = False

        dd = apply_drawdown_breaker(state, px, codes, peak, dd_levels)
        adaptive_rebalance(state, adaptive_tw, px, codes, threshold, trend_bull)

        tv_pre = total_value(state, px, codes)
        peak = max(peak, tv_pre)
        dd_now = (peak - tv_pre) / peak if peak > 0 else 0

        hr = 0.50
        if dd_now > 0.15:
            hr = 2.0
        elif dd_now > 0.12:
            hr = 1.5
        elif dd_now > 0.10:
            hr = 1.0

        has_expired = any(i >= opt.expiry_date for opt in state.option_positions)
        if i % OPTION_ROLL_DAYS == 0 or (dd_now > 0.08 and has_expired):
            roll_options(state, px, iv_row, i, hedge_ratio=hr)

        if dd < 0.08 and state.current_exposure_target < 1.0:
            state.current_exposure_target = 1.0

        tv = total_value(state, px, codes)
        peak = max(peak, tv)
        eq.append(tv)

        daily_log.append({
            "i": i, "date": str(date.date()), "eq": tv,
            "dd": (peak - tv) / peak if peak > 0 else 0,
            "avg_vol": avg_vol, "threshold": threshold,
            "trend_bull": trend_bull, "exposure": state.current_exposure_target,
            "hr": hr,
        })

    return eq, state, daily_log


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
    downside = daily_ret[daily_ret < 0]
    dvol = float(np.std(downside) * np.sqrt(TRADING_DAYS)) if len(downside) > 1 else 0
    sortino = (annual_ret - RISK_FREE_RATE) / dvol if dvol > 0 else 0
    calmar = annual_ret / max_dd if max_dd > 0 else 0
    win_rate = float(np.mean(daily_ret > 0)) if n > 1 else 0
    wins = daily_ret[daily_ret > 0]
    losses = daily_ret[daily_ret < 0]
    pl_ratio = float(np.mean(wins) / abs(np.mean(losses))) if len(wins) > 0 and len(losses) > 0 else 0
    bench_arr = np.array(benchmark_eq, dtype=float)
    bench_annual = (1 + bench_arr[-1] / bench_arr[0] - 1) ** (1 / years) - 1 if years > 0 else 0
    return {
        "total_return": float(total_ret), "annual_return": float(annual_ret),
        "max_drawdown": float(max_dd), "volatility": float(vol),
        "sharpe": float(sharpe), "sortino": float(sortino), "calmar": float(calmar),
        "win_rate": float(win_rate), "profit_loss_ratio": float(pl_ratio),
        "benchmark_annual": float(bench_annual),
        "excess_return": float(annual_ret - bench_annual),
        "final_value": float(eq_arr[-1]), "n_days": int(n),
    }


def predict_annual_return(eq: list[float], n_simulations: int = 10000,
                          horizon_years: int = 3) -> dict:
    eq_arr = np.array(eq, dtype=float)
    daily_ret = np.diff(eq_arr) / eq_arr[:-1]
    mu = float(np.mean(daily_ret))
    sigma = float(np.std(daily_ret))
    skew = float((((daily_ret - mu) / sigma) ** 3).mean()) if sigma > 0 else 0
    kurt = float((((daily_ret - mu) / sigma) ** 4).mean()) - 3 if sigma > 0 else 0

    horizon_days = horizon_years * TRADING_DAYS
    rng = np.random.default_rng(42)
    n_hist = len(daily_ret)

    final_returns = np.zeros(n_simulations)
    for s in range(n_simulations):
        indices = rng.integers(0, n_hist, size=horizon_days)
        sim_daily = daily_ret[indices]
        cum = np.cumprod(1 + sim_daily)
        final_returns[s] = cum[-1] - 1

    valid = final_returns[final_returns > -0.99]
    annual_returns = (1 + valid) ** (1 / horizon_years) - 1
    return {
        "method": "monte_carlo",
        "n_simulations": n_simulations,
        "horizon_years": horizon_years,
        "historical_daily_mean": mu,
        "historical_daily_std": sigma,
        "historical_skewness": skew,
        "historical_kurtosis": kurt,
        "predicted_mean_annual": float(np.mean(annual_returns)),
        "predicted_median_annual": float(np.median(annual_returns)),
        "predicted_std_annual": float(np.std(annual_returns)),
        "ci_5_annual": float(np.percentile(annual_returns, 5)),
        "ci_25_annual": float(np.percentile(annual_returns, 25)),
        "ci_75_annual": float(np.percentile(annual_returns, 75)),
        "ci_95_annual": float(np.percentile(annual_returns, 95)),
        "prob_above_8pct": float(np.mean(annual_returns >= 0.08)),
        "prob_above_0pct": float(np.mean(annual_returns >= 0)),
        "prob_below_neg_5pct": float(np.mean(annual_returns < -0.05)),
    }


def analyze_yearly_returns(eq: list[float], dates: pd.DatetimeIndex) -> list[dict]:
    eq_arr = np.array(eq, dtype=float)
    years = pd.Series(dates).dt.year
    results = []
    for yr in sorted(years.unique()):
        mask = (years == yr).values
        yr_eq = eq_arr[mask]
        if len(yr_eq) < 2:
            continue
        yr_ret = yr_eq[-1] / yr_eq[0] - 1
        peak = np.maximum.accumulate(yr_eq)
        dd = (peak - yr_eq) / peak
        results.append({
            "year": int(yr), "return": float(yr_ret),
            "max_drawdown": float(np.max(dd)),
            "n_days": int(len(yr_eq)),
            "start_value": float(yr_eq[0]), "end_value": float(yr_eq[-1]),
        })
    return results


def main() -> None:
    logger.info("加载配置和数据...")
    load_config()
    prices = load_etf_prices()
    iv_df = compute_rolling_iv(prices, 20)
    trend_signal = compute_trend_signal(prices, TREND_MA_WINDOW)

    logger.info("  %d 交易日, %d ETF, %s ~ %s", len(prices), len(prices.columns),
                prices.index[0].date(), prices.index[-1].date())
    logger.info("  趋势信号: 牛市占比 %.1f%%", trend_signal.mean() * 100)

    bench_eq = run_benchmark(prices)

    logger.info("运行 v4 自适应动态再平衡...")
    eq, state, daily_log = run_v4_strategy(prices, BASE_WEIGHTS, iv_df, trend_signal)
    metrics = compute_metrics(eq, bench_eq)
    logger.info("  年化 %.2f%% / 回撤 %.2f%% / Sharpe %.3f", metrics["annual_return"]*100,
                metrics["max_drawdown"]*100, metrics["sharpe"])
    logger.info("  再平衡 %d次 / 熔断 %d次 / 趋势过滤 %d次 / 波动率调权 %d次",
                state.rebalance_count, state.breaker_count, state.trend_filter_count,
                state.vol_adjust_count)

    logger.info("预测年化收益(蒙特卡洛10000路径, 3年)...")
    prediction = predict_annual_return(eq, n_simulations=10000, horizon_years=3)
    logger.info("  预测中位数 %.2f%% / 95%%CI [%.2f%%, %.2f%%] / P(>8%%) = %.1f%%",
                prediction["predicted_median_annual"]*100,
                prediction["ci_5_annual"]*100, prediction["ci_95_annual"]*100,
                prediction["prob_above_8pct"]*100)

    yearly = analyze_yearly_returns(eq, prices.index)

    bench_m = compute_metrics(bench_eq, bench_eq)

    lines = []
    lines.append("=" * 120)
    lines.append("  ETF期权对冲子组合 — v4 自适应动态再平衡 + 预测年化")
    lines.append("=" * 120)
    lines.append(f"  回测区间: {prices.index[0].date()} ~ {prices.index[-1].date()} | 初始资金: {INITIAL_CAPITAL:,}元")
    lines.append("  自适应机制: 波动率调阈值 + MA60趋势过滤 + 波动率调权 + 自适应熔断")
    lines.append("")
    lines.append("  === 回测结果 ===")
    lines.append(f"  年化收益: {metrics['annual_return']*100:.2f}% | 最大回撤: {metrics['max_drawdown']*100:.2f}% | Sharpe: {metrics['sharpe']:.3f}")  # noqa: E501
    lines.append(f"  Sortino: {metrics['sortino']:.3f} | Calmar: {metrics['calmar']:.3f} | 胜率: {metrics['win_rate']*100:.1f}%")  # noqa: E501
    lines.append(f"  期末市值: {metrics['final_value']:,.0f} | 超额收益: {metrics['excess_return']*100:.2f}%")
    lines.append(f"  基准沪深300ETF: 年化 {bench_m['annual_return']*100:.2f}% / 回撤 {bench_m['max_drawdown']*100:.2f}%")  # noqa: E501
    lines.append("")
    lines.append("  === 自适应机制触发统计 ===")
    lines.append(f"  再平衡次数: {state.rebalance_count} | 回撤熔断: {state.breaker_count} | 趋势过滤: {state.trend_filter_count} | 波动率调权: {state.vol_adjust_count}")  # noqa: E501
    lines.append(f"  期权权利金: {state.total_premium_paid:,.0f} | 期权赔付: {state.total_premium_recovered:,.0f}")
    lines.append("")
    lines.append("  === 预测年化收益 (蒙特卡洛10000路径, 3年) ===")
    lines.append(f"  历史日收益: 均值 {prediction['historical_daily_mean']*100:.4f}% / 标准差 {prediction['historical_daily_std']*100:.3f}%")  # noqa: E501
    lines.append(f"  偏度 {prediction['historical_skewness']:.3f} / 超额峰度 {prediction['historical_kurtosis']:.3f}")
    lines.append(f"  预测年化: 均值 {prediction['predicted_mean_annual']*100:.2f}% / 中位数 {prediction['predicted_median_annual']*100:.2f}%")  # noqa: E501
    lines.append(f"  95%置信区间: [{prediction['ci_5_annual']*100:.2f}%, {prediction['ci_95_annual']*100:.2f}%]")
    lines.append(f"  50%置信区间: [{prediction['ci_25_annual']*100:.2f}%, {prediction['ci_75_annual']*100:.2f}%]")
    lines.append(f"  概率: P(>8%) = {prediction['prob_above_8pct']*100:.1f}% / P(>0%) = {prediction['prob_above_0pct']*100:.1f}% / P(<-5%) = {prediction['prob_below_neg_5pct']*100:.1f}%")  # noqa: E501
    lines.append("")
    lines.append("  === 分年度收益分析 ===")
    lines.append(f"  {'年份':>6} {'收益率':>10} {'回撤%':>8} {'交易日':>6} {'期初':>12} {'期末':>12}")
    for yr in yearly:
        lines.append(f"  {yr['year']:>6} {yr['return']*100:>9.2f}% {yr['max_drawdown']*100:>7.2f}% {yr['n_days']:>6} {yr['start_value']:>11,.0f} {yr['end_value']:>11,.0f}")  # noqa: E501
    lines.append("")
    lines.append("  === 目标达标 ===")
    lines.append(f"  年化 >= 8%: {'✅' if metrics['annual_return']>=0.08 else '❌'} ({metrics['annual_return']*100:.2f}%)")  # noqa: E501
    lines.append(f"  回撤 < 20%: {'✅' if metrics['max_drawdown']<0.20 else '❌'} ({metrics['max_drawdown']*100:.2f}%)")
    lines.append(f"  Sharpe >= 0.50: {'✅' if metrics['sharpe']>=0.50 else '❌'} ({metrics['sharpe']:.3f})")
    lines.append(f"  Calmar >= 0.40: {'✅' if metrics['calmar']>=0.40 else '❌'} ({metrics['calmar']:.3f})")
    lines.append(f"  P(>8%) >= 60%: {'✅' if prediction['prob_above_8pct']>=0.60 else '❌'} ({prediction['prob_above_8pct']*100:.1f}%)")  # noqa: E501
    lines.append("")
    report = "\n".join(lines)

    timestamp = now_bj().strftime("%Y%m%d_%H%M%S")
    report_path = DATA_DIR / f"backtest_report_v4_{timestamp}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info("报告已保存: %s", report_path)

    json_path = DATA_DIR / f"backtest_result_v4_{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "version": "v4",
            "generated_at": now_bj().isoformat(),
            "start_date": str(prices.index[0].date()),
            "end_date": str(prices.index[-1].date()),
            "backtest_metrics": metrics,
            "benchmark_metrics": bench_m,
            "adaptive_stats": {
                "rebalance_count": state.rebalance_count,
                "breaker_count": state.breaker_count,
                "trend_filter_count": state.trend_filter_count,
                "vol_adjust_count": state.vol_adjust_count,
                "total_premium_paid": state.total_premium_paid,
                "total_premium_recovered": state.total_premium_recovered,
            },
            "prediction": prediction,
            "yearly_returns": yearly,
        }, f, indent=2, ensure_ascii=False)
    logger.info("结果已保存: %s", json_path)

    return metrics, prediction, yearly


if __name__ == "__main__":
    main()
