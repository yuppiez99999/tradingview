"""
ETF期权对冲子组合 — 最优方案回测 v3 (回撤控制<20%)
=====================================================
基于 v2 S5尾部对冲，增加:
  1. 回撤熔断减仓 (drawdown breaker): >12%降至70%敞口 / >15%降至50% / >18%降至30%
  2. 十五五规划高评分ETF增配: 科创50(92分)+2pp / 创业板(85分)+1pp / 中证1000(82分)+1pp
  3. 增强期权保护: 年化预算2.5%→3.5%, 回撤加码更激进
  4. 收紧再平衡阈值: 6%→4%
  5. 防御资产增配: 黄金ETF 8%→10%, 红利ETF 3%→5%, 国债ETF 4%→5%

数据: data/etf_option_backtest/all_etf_daily.parquet (14 ETF, 2021-2026)
运行: python data/etf_option_backtest/run_etf_option_backtest_v3.py
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
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

FIFTEEN_FIVE_SCORES = {
    "588000": 92, "300308": 90, "688041": 90, "603019": 90,
    "159915": 85, "300274": 85, "688017": 85, "300750": 85,
    "600276": 88, "002371": 88, "688981": 88,
    "512100": 82, "601088": 82, "600875": 82,
    "510500": 80, "600089": 80, "600406": 78,
    "510300": 75, "600900": 72, "000792": 72,
    "510310": 55, "518880": 45,
}

FIFTEEN_FIVE_ADJUSTED_WEIGHTS: dict[str, float] = {
    "510300": 0.15,
    "510500": 0.07,
    "510050": 0.10,
    "512100": 0.09,
    "588000": 0.10,
    "159915": 0.08,
    "512480": 0.05,
    "512010": 0.06,
    "512660": 0.04,
    "515170": 0.03,
    "159939": 0.03,
    "518880": 0.10,
    "511260": 0.05,
    "510310": 0.05,
}

DRAWDOWN_BREAKER_LEVELS = [
    (0.12, 1.00),
    (0.15, 0.70),
    (0.18, 0.50),
    (1.00, 0.30),
]
DRAWDOWN_RECOVERY_THRESHOLD = 0.08


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


@dataclass
class BacktestState:
    cash: float = INITIAL_CAPITAL
    pos: dict[str, int] = field(default_factory=dict)
    option_positions: list[OptionPosition] = field(default_factory=list)
    total_premium_paid: float = 0.0
    total_premium_recovered: float = 0.0
    total_tc: float = 0.0
    current_exposure_target: float = 1.0
    breaker_triggered_count: int = 0


def apply_trade_cost(trade_value: float) -> float:
    return abs(trade_value) * (TRANSACTION_COST + SLIPPAGE)


def etf_value(state: BacktestState, px: dict[str, float], codes: list[str]) -> float:
    return sum(state.pos.get(c, 0) * px.get(c, 0) for c in codes)


def options_value(state: BacktestState, px: dict[str, float]) -> float:
    return sum(opt.intrinsic_value(px.get(opt.underlying, 0)) for opt in state.option_positions)


def total_value(state: BacktestState, px: dict[str, float], codes: list[str]) -> float:
    return state.cash + etf_value(state, px, codes) + options_value(state, px)


def apply_drawdown_breaker(state: BacktestState, px: dict[str, float],
                           codes: list[str], peak: float) -> float:
    tv = total_value(state, px, codes)
    dd = (peak - tv) / peak if peak > 0 else 0

    if dd < DRAWDOWN_RECOVERY_THRESHOLD:
        target_exposure = 1.0
    else:
        target_exposure = 1.0
        for threshold, exposure in DRAWDOWN_BREAKER_LEVELS:
            if dd >= threshold:
                target_exposure = exposure

    if target_exposure >= state.current_exposure_target and dd < DRAWDOWN_RECOVERY_THRESHOLD:
        state.current_exposure_target = 1.0

    if target_exposure < state.current_exposure_target:
        state.current_exposure_target = target_exposure
        state.breaker_triggered_count += 1
        logger.debug("回撤熔断触发: dd=%.2f%% -> 敞口降至%.0f%%", dd * 100, target_exposure * 100)

    if state.current_exposure_target < 1.0:
        etf_val = etf_value(state, px, codes)
        if etf_val > 0 and tv > 0:
            current_exposure = etf_val / tv
            if current_exposure > state.current_exposure_target + 0.02:
                reduce_ratio = state.current_exposure_target / current_exposure
                for c in codes:
                    if c in px and px[c] > 0:
                        old_shares = state.pos.get(c, 0)
                        if old_shares <= 0:
                            continue
                        new_shares = int(old_shares * reduce_ratio / 100) * 100
                        delta = old_shares - new_shares
                        if delta > 0:
                            cost = apply_trade_cost(delta * px[c])
                            state.pos[c] = new_shares
                            state.cash += delta * px[c] - cost
                            state.total_tc += cost

    return dd


def restore_exposure(state: BacktestState, px: dict[str, float],
                      codes: list[str], tw: dict[str, float]) -> None:
    if state.current_exposure_target >= 1.0:
        return
    tv = total_value(state, px, codes)
    if tv <= 0:
        return
    for c in codes:
        if c in px and px[c] > 0:
            target_amt = tv * tw[c] * state.current_exposure_target
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


def rebalance_to_target(state: BacktestState, tw: dict[str, float],
                        px: dict[str, float], codes: list[str],
                        threshold: float = 0.04) -> None:
    sv = etf_value(state, px, codes)
    total = state.cash + sv
    if total <= 0:
        return
    max_dev = 0.0
    for c in codes:
        if c in px:
            cur_w = state.pos.get(c, 0) * px[c] / total
            dev = abs(cur_w - tw[c] * state.current_exposure_target)
            max_dev = max(max_dev, dev)
    if max_dev < threshold:
        return
    for c in codes:
        if c in px:
            target_amt = total * tw[c] * state.current_exposure_target
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
    annual_cost_budget = portfolio_val * ANNUAL_OPTION_BUDGET
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
        contracts = max(0, min(contracts, 60))
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

    for i, (_date, row) in enumerate(prices.iterrows()):
        px = {c: row[c] for c in codes if c in row and not np.isnan(row[c])}
        iv_row = iv_df.iloc[i] if i < len(iv_df) else None

        if first and px:
            init_positions(state, tw, px, codes)
            first = False

        dd = apply_drawdown_breaker(state, px, codes, peak)

        if strategy in ("s2_rebalance", "s4_full", "s5_tail", "s6_conditional",
                        "s7_collar", "s8_dynamic", "s9_breaker_hedge",
                        "s10_full_control"):
            rebalance_to_target(state, tw, px, codes, threshold=0.04)

        if strategy == "s3_option" or strategy == "s4_full":
            if i % OPTION_ROLL_DAYS == 0:
                roll_options(state, px, iv_row, i)

        elif strategy == "s5_tail":
            tv = total_value(state, px, codes)
            peak = max(peak, tv)
            dd_now = (peak - tv) / peak if peak > 0 else 0
            if dd_now > 0.15:
                hr = 1.0
            elif dd_now > 0.10:
                hr = 0.75
            else:
                hr = 0.50
            has_expired = any(i >= opt.expiry_date for opt in state.option_positions)
            if i % OPTION_ROLL_DAYS == 0 or (dd_now > 0.10 and has_expired):
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

        elif strategy == "s9_breaker_hedge":
            tv = total_value(state, px, codes)
            peak = max(peak, tv)
            dd_now = (peak - tv) / peak if peak > 0 else 0
            if dd_now > 0.15:
                hr = 2.0
            elif dd_now > 0.12:
                hr = 1.5
            elif dd_now > 0.10:
                hr = 1.0
            else:
                hr = 0.50
            has_expired = any(i >= opt.expiry_date for opt in state.option_positions)
            if i % OPTION_ROLL_DAYS == 0 or (dd_now > 0.10 and has_expired):
                roll_options(state, px, iv_row, i, hedge_ratio=hr)

        elif strategy == "s10_full_control":
            tv = total_value(state, px, codes)
            peak = max(peak, tv)
            dd_now = (peak - tv) / peak if peak > 0 else 0
            if dd_now > 0.15:
                hr = 2.0
            elif dd_now > 0.12:
                hr = 1.5
            elif dd_now > 0.10:
                hr = 1.0
            else:
                hr = 0.60
            has_expired = any(i >= opt.expiry_date for opt in state.option_positions)
            if i % OPTION_ROLL_DAYS == 0 or (dd_now > 0.08 and has_expired):
                roll_options(state, px, iv_row, i, hedge_ratio=hr)
            if dd < DRAWDOWN_RECOVERY_THRESHOLD and state.current_exposure_target < 1.0:
                state.current_exposure_target = 1.0
                restore_exposure(state, px, codes, tw)

        tv = total_value(state, px, codes)
        peak = max(peak, tv)
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


def format_report(results: dict, prices: pd.DataFrame, states: dict, tw_used: dict) -> str:
    lines = []
    lines.append("=" * 120)
    lines.append("  ETF期权对冲子组合 — 最优方案回测 v3 (回撤控制<20% + 十五五规划对齐)")
    lines.append("=" * 120)
    lines.append(f"  回测区间: {prices.index[0].date()} ~ {prices.index[-1].date()} | 初始资金: {INITIAL_CAPITAL:,}元")
    lines.append(f"  ETF数: {len(prices.columns)} | 期权标的: {sorted(OPTION_UNDERLYING_ETFS)} | 期权预算: {ANNUAL_OPTION_BUDGET*100:.1f}%")  # noqa: E501
    lines.append(f"  回撤熔断: >12%→70%敞口 / >15%→50% / >18%→30% | 恢复阈值: {DRAWDOWN_RECOVERY_THRESHOLD*100:.0f}%")
    lines.append("  再平衡阈值: 4% (v2为6%) | 十五五高评分增配: 科创50(92)+2pp / 创业板(85)+1pp / 中证1000(82)+1pp")
    lines.append("")
    lines.append(f"{'策略':<34} {'年化%':>7} {'回撤%':>7} {'Sharpe':>7} {'Sortino':>7} {'Calmar':>7} {'胜率%':>6} {'期末':>12} {'熔断次数':>8}")  # noqa: E501
    lines.append("-" * 100)
    for name, m in results.items():
        s = states.get(name)
        bk = s.breaker_triggered_count if s else 0
        lines.append(
            f"{name:<34} {m['annual_return']*100:>6.2f} {m['max_drawdown']*100:>6.2f} "
            f"{m['sharpe']:>6.3f} {m['sortino']:>6.3f} {m['calmar']:>6.3f} "
            f"{m['win_rate']*100:>5.1f} {m['final_value']:>11,.0f} {bk:>7}"
        )
    lines.append("")
    lines.append("  十五五规划权重调整 (v2原权重 → v3新权重):")
    for code, new_w in sorted(tw_used.items(), key=lambda x: -x[1]):
        score = FIFTEEN_FIVE_SCORES.get(code, 0)
        lines.append(f"    {code} (评分{score:>3}): {new_w*100:>5.1f}%")
    lines.append("")
    lines.append("  目标: 年化 >= 8% / 回撤 < 20% / Sharpe >= 0.50")
    candidates = {k: v for k, v in results.items() if k != "基准 沪深300ETF"}
    best_dd = min(candidates.items(), key=lambda x: x[1]["max_drawdown"])
    best_sharpe = max(candidates.items(), key=lambda x: x[1]["sharpe"])
    lines.append(f"  最低回撤: {best_dd[0]} — 回撤 {best_dd[1]['max_drawdown']*100:.2f}% / 年化 {best_dd[1]['annual_return']*100:.2f}%")  # noqa: E501
    lines.append(f"  最高Sharpe: {best_sharpe[0]} — Sharpe {best_sharpe[1]['sharpe']:.3f} / 年化 {best_sharpe[1]['annual_return']*100:.2f}%")  # noqa: E501
    dd_ok = best_dd[1]["max_drawdown"] < 0.20
    lines.append(f"  回撤<20%达标: {'是' if dd_ok else '否'}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    logger.info("加载配置 %s", CONFIG_PATH)
    cfg = load_config()
    tw_original = extract_target_weights(cfg)

    tw_v3 = dict(FIFTEEN_FIVE_ADJUSTED_WEIGHTS)
    for code in tw_original:
        if code not in tw_v3:
            tw_v3[code] = tw_original[code]
    total_w = sum(tw_v3.values())
    if total_w > 0:
        tw_v3 = {k: v / total_w for k, v in tw_v3.items()}

    logger.info("v3十五五调整权重: %d ETF, 总和 %.4f", len(tw_v3), sum(tw_v3.values()))
    logger.info("权重变化(>1pp):")
    for code in tw_v3:
        old_w = tw_original.get(code, 0)
        new_w = tw_v3[code]
        if abs(new_w - old_w) > 0.005:
            score = FIFTEEN_FIVE_SCORES.get(code, 0)
            logger.info("  %s (评分%d): %.1f%% -> %.1f%% (%+.1fpp)",
                        code, score, old_w*100, new_w*100, (new_w-old_w)*100)

    logger.info("加载ETF价格数据...")
    prices = load_etf_prices()
    logger.info("  %d 交易日, %d ETF, %s ~ %s",
                len(prices), len(prices.columns),
                prices.index[0].date(), prices.index[-1].date())

    logger.info("计算20日滚动波动率(动态IV)...")
    iv_df = compute_rolling_iv(prices, window=20)

    bench_eq = run_benchmark(prices)

    strategies = [
        ("S2 再平衡(4%阈值)", "s2_rebalance"),
        ("S5 尾部对冲(v2基线)", "s5_tail"),
        ("S9 回撤熔断+增强对冲", "s9_breaker_hedge"),
        ("S10 全量控制(熔断+对冲+恢复)", "s10_full_control"),
    ]

    results = {}
    states = {}
    for name, strat_id in strategies:
        logger.info("运行 %s ...", name)
        eq, state = run_strategy(prices, tw_v3, iv_df, strat_id)
        m = compute_metrics(eq, bench_eq)
        m["transaction_costs"] = float(state.total_tc)
        m["total_premium_paid"] = float(state.total_premium_paid)
        m["total_premium_recovered"] = float(state.total_premium_recovered)
        m["breaker_triggered_count"] = state.breaker_triggered_count
        results[name] = m
        states[name] = state
        logger.info("  %s: 年化 %.2f%% / 回撤 %.2f%% / Sharpe %.3f / 熔断 %d次",
                    name, m["annual_return"] * 100, m["max_drawdown"] * 100, m["sharpe"],
                    state.breaker_triggered_count)

    bench_m = compute_metrics(bench_eq, bench_eq)
    results["基准 沪深300ETF"] = bench_m

    report = format_report(results, prices, states, tw_v3)

    timestamp = now_bj().strftime("%Y%m%d_%H%M%S")
    report_path = DATA_DIR / f"backtest_report_v3_{timestamp}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info("报告已保存: %s", report_path)

    json_path = DATA_DIR / f"backtest_result_v3_{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "version": "v3",
            "generated_at": now_bj().isoformat(),
            "start_date": str(prices.index[0].date()),
            "end_date": str(prices.index[-1].date()),
            "initial_capital": INITIAL_CAPITAL,
            "n_etfs": len(prices.columns),
            "n_days": len(prices),
            "config": {
                "annual_option_budget": ANNUAL_OPTION_BUDGET,
                "rebalance_threshold": 0.04,
                "drawdown_breaker_levels": DRAWDOWN_BREAKER_LEVELS,
                "drawdown_recovery_threshold": DRAWDOWN_RECOVERY_THRESHOLD,
                "fifteen_five_weights": tw_v3,
                "fifteen_five_scores": FIFTEEN_FIVE_SCORES,
            },
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    logger.info("结果已保存: %s", json_path)

    return results, states


if __name__ == "__main__":
    main()
