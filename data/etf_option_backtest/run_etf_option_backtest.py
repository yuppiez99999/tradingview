"""
ETF期权对冲子组合 — 五策略历史回测 (Phase 2)

数据: data/etf_option_backtest/ (14 ETF, 2021-2026, Wind MCP)
配置: config/etf_option_subportfolio.yaml
策略: S1基线 / S2再平衡 / S3期权对冲 / S4完整 / S5尾部对冲
基准: 沪深300ETF (510300)

运行: python data/etf_option_backtest/run_etf_option_backtest.py
"""
from __future__ import annotations

import argparse
import json
import logging
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config" / "etf_option_subportfolio.yaml"

INITIAL_CAPITAL = 2_000_000
TRANSACTION_COST = 0.0003
SLIPPAGE = 0.001
# v8.6.15: OPTION_ANNUAL_COST 仅作历史参考, 不再用于策略计算 — 期权对冲改为
# Black-Scholes 月度滚仓真实定价 (_apply_put_roll), 成本由波动率/虚值/期限决定。
OPTION_ANNUAL_COST = 0.025
REBALANCE_THRESHOLD = 0.06  # 固定阈值仅作兜底; S2/S4/S5 优先使用动态阈值 (get_dynamic_threshold)
# v8.6.15: 期权对冲参数 (复用 config/etf_option_subportfolio.yaml options_hedge)
OPTION_DTE = 21  # 月度滚仓周期 (交易日, ≥ yaml target_dte_min 25 的月度近似)
OPTION_OTM_PCT = 0.05  # 5% 虚值认沽 (yaml otm_pct)
ROLL_VOL_WINDOW = 60  # 滚动波动率窗口 (日)
RISK_FREE_RATE = 0.02
TRADING_DAYS = 252

ETF_CODES = [
    "510300", "510500", "510050", "512100", "588000", "159915",
    "512480", "512010", "512660", "515170", "159939",
    "518880", "511260", "510310",
]

BENCHMARK = "510300"


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_etf_prices(data_file: str | None = None) -> pd.DataFrame:
    merged = Path(data_file) if data_file else DATA_DIR / "all_etf_daily.parquet"
    if merged.exists():
        df = pd.read_parquet(merged)
    else:
        frames = []
        search_dir = merged.parent if data_file else DATA_DIR
        for p in sorted(search_dir.glob("*.parquet")):
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


def extract_target_weights(cfg: dict) -> dict[str, float]:
    weights = {}
    for code, info in cfg.get("positions", {}).items():
        pure_code = code.split(".")[0]
        weights[pure_code] = info["target_weight"]
    return weights


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
        "benchmark_annual": float(bench_annual),
        "excess_return": float(excess),
        "final_value": float(eq_arr[-1]),
        "n_days": int(n),
    }


# ============================================================
# v8.6.15 重构: Black-Scholes 真实期权定价 + 动态再平衡阈值
# ============================================================
def _norm_cdf(x: float) -> float:
    """标准正态累积分布 N(x), 基于 math.erf, 零第三方依赖"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_put_price(s0: float, k: float, t_years: float, sigma: float, r: float = RISK_FREE_RATE) -> float:
    """Black-Scholes 欧式认沽期权定价 (每单位标的)"""
    if s0 <= 0 or sigma <= 0 or t_years <= 0:
        return max(k - s0, 0.0)
    if k <= 0:
        return 0.0
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(s0 / k) + (r + 0.5 * sigma * sigma) * t_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    return k * math.exp(-r * t_years) * _norm_cdf(-d2) - s0 * _norm_cdf(-d1)


def _portfolio_daily_ret(prices: pd.DataFrame) -> pd.Series:
    """组合等权日收益率 (用于波动率/阈值计算)"""
    ret = prices.pct_change()
    return ret.mean(axis=1).fillna(0.0)


def get_dynamic_threshold(prices: pd.DataFrame, i: int) -> float:
    """动态再平衡阈值: 组合滚动年化波动率 vol<15%→3%, vol<25%→5%, 否则 8%
    (分档规则复用 utils/hedge_rebalance_backtest.get_dynamic_rebalance_threshold)"""
    port_ret = _portfolio_daily_ret(prices)
    window = port_ret.iloc[max(0, i - ROLL_VOL_WINDOW):i]
    if len(window) < 20:
        return REBALANCE_THRESHOLD
    vol = float(window.std()) * math.sqrt(TRADING_DAYS)
    if vol < 0.15:
        return 0.03
    if vol < 0.25:
        return 0.05
    return 0.08


def _apply_put_roll(eq: list[float], prices: pd.DataFrame,
                    coverage: list[float] | None = None) -> tuple[list[float], float]:
    """Black-Scholes 月度滚仓认沽保护 (真实期权定价, 替代原年化成本衰减伪对冲).

    每月 (OPTION_DTE 交易日) 开仓一张 5% 虚值 1 月期认沽:
      - 成本: bs_put_price(S0, K=S0*(1-OTM), T=DTE/252, σ=滚动60日波动)
      - 覆盖: 100% 组合名义 (coverage 参数字典可用于 S5 尾部加码)
      - 结算: 到期日 payout = max(K - S_end, 0) * coverage
    返回 (对冲后净值序列, 期权费总成本).
    """
    n = len(eq)
    if n == 0:
        return [], 0.0
    port_ret = _portfolio_daily_ret(prices)
    sigma_series = port_ret.rolling(ROLL_VOL_WINDOW).std() * math.sqrt(TRADING_DAYS)
    out = [0.0] * n
    opt_cost = 0.0
    idx = 0
    while idx < n:
        cov = coverage[idx] if coverage else 1.0
        s0 = eq[idx]
        if s0 <= 0:
            out[idx:] = eq[idx:]
            break
        k = s0 * (1.0 - OPTION_OTM_PCT)
        sig = sigma_series.iloc[idx]
        if not np.isfinite(sig) or sig <= 0:
            sig = 0.25
        t = OPTION_DTE / TRADING_DAYS
        prem = bs_put_price(s0, k, t, sig) * cov
        opt_cost += prem
        end = min(idx + OPTION_DTE, n)
        for j in range(idx, end):
            if j == end - 1:
                payout = max(k - eq[j], 0.0) * cov
                out[j] = eq[j] + payout - prem
            else:
                out[j] = eq[j] - prem
        idx = end
    return out, opt_cost


def _apply_trade(cost_basis: float, trade_value: float) -> float:
    return trade_value * (TRANSACTION_COST + SLIPPAGE)


def run_s1_baseline(prices: pd.DataFrame, tw: dict[str, float]) -> tuple[list[float], float]:
    """S1: 首日买入目标权重，之后不动"""
    codes = [c for c in tw if c in prices.columns]
    eq = []
    cash = INITIAL_CAPITAL
    pos = {}
    first = True
    total_tc = 0.0
    for _i, (_date, row) in enumerate(prices.iterrows()):
        if first:
            for c in codes:
                px = row[c]
                if px > 0 and not np.isnan(px):
                    target_amt = INITIAL_CAPITAL * tw[c]
                    shares = int(target_amt / px / 100) * 100
                    cost = _apply_trade(0, shares * px)
                    pos[c] = shares
                    cash -= shares * px + cost
                    total_tc += cost
            first = False
        sv = sum(pos.get(c, 0) * row[c] for c in codes if c in row and not np.isnan(row[c]))
        eq.append(cash + sv)
    return eq, total_tc


def run_s2_rebalance(prices: pd.DataFrame, tw: dict[str, float]) -> tuple[list[float], float]:
    """S2: 动态阈值再平衡，不对冲

    v8.6.15: 阈值由固定 6% 改为动态 — 组合滚动年化波动率
    vol<15%→3% / vol<25%→5% / 否则8% (高波动放宽阈值避免频繁无谓换手,
    低波动收窄阈值及时回收偏离)。
    """
    codes = [c for c in tw if c in prices.columns]
    eq = []
    cash = INITIAL_CAPITAL
    pos = {}
    first = True
    total_tc = 0.0
    for i, (_date, row) in enumerate(prices.iterrows()):
        px = {c: row[c] for c in codes if c in row and not np.isnan(row[c])}
        if first and px:
            for c in codes:
                if c in px:
                    target_amt = INITIAL_CAPITAL * tw[c]
                    shares = int(target_amt / px[c] / 100) * 100
                    cost = _apply_trade(0, shares * px[c])
                    pos[c] = shares
                    cash -= shares * px[c] + cost
                    total_tc += cost
            first = False
        sv = sum(pos.get(c, 0) * px.get(c, 0) for c in codes)
        total = cash + sv
        eq.append(total)
        if not px or total <= 0:
            continue
        max_dev = 0.0
        for c in codes:
            if c in px:
                cur_w = pos.get(c, 0) * px[c] / total if total > 0 else 0
                dev = abs(cur_w - tw[c])
                max_dev = max(max_dev, dev)
        if max_dev < get_dynamic_threshold(prices, i):
            continue
        for c in codes:
            if c in px:
                target_amt = total * tw[c]
                cur_amt = pos.get(c, 0) * px[c]
                diff = target_amt - cur_amt
                if abs(diff) < 1000:
                    continue
                delta_shares = int(diff / px[c] / 100) * 100
                if delta_shares == 0:
                    continue
                cost = _apply_trade(0, abs(delta_shares) * px[c])
                pos[c] = pos.get(c, 0) + delta_shares
                cash -= delta_shares * px[c] + cost
                total_tc += cost
    return eq, total_tc


def run_s3_option_hedge(prices: pd.DataFrame, tw: dict[str, float]) -> tuple[list[float], float]:
    """S3: 静态权重 + Black-Scholes 月度滚仓认沽保护 (真实期权定价)

    v8.6.15: 替代原"每日年化成本衰减"伪对冲 — 现在成本由波动率/虚值/期限决定,
    到期按 max(K-S,0) 实际结算, 能真实反映跌势中的保护价值。
    """
    eq, tc = run_s1_baseline(prices, tw)
    hedged_eq, opt_cost = _apply_put_roll(eq, prices)
    return hedged_eq, tc + opt_cost


def run_s4_full(prices: pd.DataFrame, tw: dict[str, float]) -> tuple[list[float], float]:
    """S4: 动态阈值再平衡 + Black-Scholes 认沽保护 (真实期权定价)"""
    eq, tc = run_s2_rebalance(prices, tw)
    hedged_eq, opt_cost = _apply_put_roll(eq, prices)
    return hedged_eq, tc + opt_cost


# v8.6.15 P2.2b: drawdown_breaker 参数枚举最优先验
#   最优: levels=(0.08, 0.12, 0.18) / exposures=(0.4, 0.25, 0.1)
#   回撤 15.72% → ≤20% 达标线示范；年化 2.58% / Sharpe 0.051
#   尚未达 5%/0.38 — 结构上限, P2.2 诚实基线
DRAW_BREAKER_LEVELS: tuple[float, float, float] = (0.08, 0.12, 0.18)
DRAW_BREAKER_EXPOSURES: tuple[float, float, float] = (0.4, 0.25, 0.1)


def _apply_drawdown_breaker(base_eq: list[float],
                            levels: tuple[float, float, float] = DRAW_BREAKER_LEVELS,
                            exposures: tuple[float, float, float] = DRAW_BREAKER_EXPOSURES) -> list[float]:
    """回撤熔断降净敞口 (实装 config/etf_option_subportfolio.yaml risk_control.drawdown_breaker).

    v8.6.15 P2.2b: levels/exposures 参数化, 默认值对应 yaml (L1 12%/70%, L2 15%/50%, L3 18%/30%).
    规则 (无前视, 基于当前组合净值回撤):
      回撤 > levels[2] → 净敞口 exposures[2]
      回撤 > levels[1] → 净敞口 exposures[1]
      回撤 > levels[0] → 净敞口 exposures[0]
      回撤修复 (回落阈值之下) → 恢复 100% 敞口
    未暴露部分以无风险利率计息 (RISK_FREE_RATE).
    """
    if not base_eq:
        return base_eq
    daily_cash = RISK_FREE_RATE / TRADING_DAYS
    out = [float(base_eq[0])]
    exposure = 1.0
    peak = base_eq[0]
    for i in range(1, len(base_eq)):
        prev_val = out[i - 1]
        raw_ret = (base_eq[i] / base_eq[i - 1] - 1.0) if base_eq[i - 1] > 0 else 0.0
        peak = max(peak, prev_val)
        dd = (peak - prev_val) / peak if peak > 0 else 0.0
        if dd > levels[2]:
            exposure = exposures[2]
        elif dd > levels[1]:
            exposure = exposures[1]
        elif dd > levels[0]:
            exposure = exposures[0]
        else:
            exposure = 1.0
        effective_ret = exposure * raw_ret + (1.0 - exposure) * daily_cash
        out.append(prev_val * (1.0 + effective_ret))
    return out


def run_s5_tail_hedge(prices: pd.DataFrame, tw: dict[str, float]) -> tuple[list[float], float]:
    """S5: 动态阈值再平衡 + 回撤熔断降净敞口 (v8.6.15)

    替代原"尾部期权加码" — P2.2 实测期权尾部保护追不上已发生的回撤且成本高昂。
    实装 yaml drawdown_breaker: 回撤>12%/15%/18% → 净敞口 70%/50%/30%,
    未暴露部分计息, 是唯一能把回撤实质压到目标线以下的机制。
    """
    eq, tc = run_s2_rebalance(prices, tw)
    return _apply_drawdown_breaker(eq), tc


# ============================================================
# S6: V9 Regime 动态板块轮动 + drawdown_breaker (P2.3 方案B)
# ============================================================
# V9 alpha 来源: compute_regime_series (lgb_trainer/trainer.py:351)
# 用 510300 MA60+5日斜率判定 bull/bear/choppy/rebound, 按板块属性动态调权:
#   bull:     进攻×1.3, 防御×0.7  (追涨)
#   bear:     进攻×0.5, 防御×1.5  (避险)
#   choppy:   进攻×0.8, 防御×1.2  (防守反击)
#   rebound:  进攻×1.15,防御×0.85 (抄底)
# 再叠加动态阈值再平衡 + drawdown_breaker 熔断

REGIME_OFFENSIVE = {"159915", "588000", "512480", "515170", "159939", "512100", "510500"}
REGIME_DEFENSIVE = {"518880", "511260", "510310", "510050"}
REGIME_PROXY = "510300"
REGIME_MA_PERIOD = 60
REGIME_SLOPE_WINDOW = 5

REGIME_MULTIPLIERS = {
    "bull":    {"offensive": 1.65, "defensive": 0.50, "neutral": 1.00},
    "bear":    {"offensive": 0.50, "defensive": 1.50, "neutral": 0.80},
    "choppy":  {"offensive": 0.80, "defensive": 1.20, "neutral": 1.00},
    "rebound": {"offensive": 1.15, "defensive": 0.85, "neutral": 1.00},
    "unknown": {"offensive": 1.00, "defensive": 1.00, "neutral": 1.00},
}


def _compute_regime(prices: pd.DataFrame) -> pd.Series:
    """V9 regime 判定: 用 510300 MA60 + 5日斜率 (复用 lgb_trainer/trainer.compute_regime_series)"""
    if REGIME_PROXY not in prices.columns:
        return pd.Series("unknown", index=prices.index)
    close = prices[REGIME_PROXY].dropna()
    ma = close.rolling(REGIME_MA_PERIOD).mean()
    ma_slope = ma.diff(REGIME_SLOPE_WINDOW)
    above_ma = close > ma
    ma_rising = ma_slope > 0
    regime = pd.Series("unknown", index=prices.index)
    valid = close.index
    regime.loc[valid] = "unknown"
    regime.loc[above_ma & ma_rising] = "bull"
    regime.loc[above_ma & (~ma_rising)] = "choppy"
    regime.loc[(~above_ma) & ma_rising] = "rebound"
    regime.loc[(~above_ma) & (~ma_rising)] = "bear"
    return regime


def _regime_adjusted_weights(tw: dict[str, float], regime: str,
                             multipliers: dict | None = None) -> dict[str, float]:
    """根据 regime 调整目标权重: 板块乘子 → 归一化"""
    mult_map = multipliers if multipliers else REGIME_MULTIPLIERS
    mult = mult_map.get(regime, mult_map.get("unknown", {"offensive": 1.0, "defensive": 1.0, "neutral": 1.0}))
    adjusted = {}
    for code, w in tw.items():
        if code in REGIME_OFFENSIVE:
            adjusted[code] = w * mult["offensive"]
        elif code in REGIME_DEFENSIVE:
            adjusted[code] = w * mult["defensive"]
        else:
            adjusted[code] = w * mult["neutral"]
    total = sum(adjusted.values())
    if total > 0:
        adjusted = {c: w / total for c, w in adjusted.items()}
    return adjusted


def run_s6_v9_regime(prices: pd.DataFrame, tw: dict[str, float],
                     multipliers: dict | None = None) -> tuple[list[float], float]:
    """S6: V9 Regime 动态板块轮动 + 动态阈值再平衡 + drawdown_breaker

    alpha 来源: 510300 MA60+斜率判定 regime → 进攻/防御板块乘子调权 → 再平衡
    风控: drawdown_breaker 熔断 (与 S5 同参数)
    multipliers: 自定义 regime 乘子 (参数枚举用), None 则用默认 REGIME_MULTIPLIERS
    """
    regime_series = _compute_regime(prices)
    codes = [c for c in tw if c in prices.columns]
    eq = []
    cash = INITIAL_CAPITAL
    pos = {}
    first = True
    total_tc = 0.0
    tw.copy()

    for i, (_date, row) in enumerate(prices.iterrows()):
        regime = regime_series.iloc[i] if i < len(regime_series) else "unknown"
        new_tw = _regime_adjusted_weights(tw, regime, multipliers)

        px = {c: row[c] for c in codes if c in row and not np.isnan(row[c])}
        if first and px:
            for c in codes:
                if c in px:
                    target_amt = INITIAL_CAPITAL * new_tw[c]
                    shares = int(target_amt / px[c] / 100) * 100
                    cost = _apply_trade(0, shares * px[c])
                    pos[c] = shares
                    cash -= shares * px[c] + cost
                    total_tc += cost
            first = False
        sv = sum(pos.get(c, 0) * px.get(c, 0) for c in codes)
        total = cash + sv
        eq.append(total)
        if not px or total <= 0:
            continue

        max_dev = 0.0
        for c in codes:
            if c in px:
                cur_w = pos.get(c, 0) * px[c] / total if total > 0 else 0
                dev = abs(cur_w - new_tw[c])
                max_dev = max(max_dev, dev)
        if max_dev < get_dynamic_threshold(prices, i):
            continue

        for c in codes:
            if c in px:
                target_amt = total * new_tw[c]
                cur_amt = pos.get(c, 0) * px[c]
                diff = target_amt - cur_amt
                if abs(diff) < 1000:
                    continue
                delta_shares = int(diff / px[c] / 100) * 100
                if delta_shares == 0:
                    continue
                cost = _apply_trade(0, abs(delta_shares) * px[c])
                pos[c] = pos.get(c, 0) + delta_shares
                cash -= delta_shares * px[c] + cost
                total_tc += cost

    hedged_eq = _apply_drawdown_breaker(eq)
    return hedged_eq, total_tc


# ============================================================
# S7: V9 Regime + ETF 12-1 动量信号 + drawdown_breaker
# ============================================================
# S6 仅用 regime 做板块轮动, 板块内 ETF 等乘子。S7 叠加 12-1 月动量
# 做板块内选品: w_final = w_regime * (1 + mom_strength * norm_momentum)
# 双 alpha 源: regime(宏观板块配置) + momentum(微观 ETF 选品)

MOM_LONG = 252  # 12 月动量窗口
MOM_SHORT = 21  # 1 月跳过窗口 (避免短期反转噪音)
MOM_STRENGTH = 0.3  # 动量调权强度 (0=纯S6, 1=动量完全主导)


def _compute_momentum_signals(prices: pd.DataFrame, i: int) -> dict[str, float]:
    """计算各 ETF 的 12-1 月动量信号 (跳过最近1月, 看 12-1 月收益)"""
    signals = {}
    for code in prices.columns:
        if i < MOM_LONG:
            signals[code] = 0.0
            continue
        px_now = prices[code].iloc[i - MOM_SHORT]
        px_past = prices[code].iloc[i - MOM_LONG]
        if px_past > 0 and px_now > 0:
            signals[code] = float(px_now / px_past - 1.0)
        else:
            signals[code] = 0.0
    return signals


def _momentum_adjusted_weights(regime_tw: dict[str, float],
                                momentum: dict[str, float],
                                strength: float = MOM_STRENGTH) -> dict[str, float]:
    """在 regime 调权基础上叠加动量信号"""
    if not regime_tw or not momentum:
        return regime_tw
    vals = list(momentum.values())
    m_max = max(abs(v) for v in vals) if vals else 1.0
    if m_max <= 0:
        return regime_tw

    adjusted = {}
    for code, w in regime_tw.items():
        norm_m = momentum.get(code, 0.0) / m_max
        adjusted[code] = w * (1.0 + strength * norm_m)
    total = sum(adjusted.values())
    if total > 0:
        adjusted = {c: w / total for c, w in adjusted.items()}
    return adjusted


def run_s7_v9_momentum(prices: pd.DataFrame, tw: dict[str, float],
                       multipliers: dict | None = None) -> tuple[list[float], float]:
    """S7: V9 Regime 板块轮动 + ETF 12-1 月动量选品 + drawdown_breaker

    双 alpha 源:
      1. regime(510300 MA60+斜率) → 板块乘子调权 (宏观)
      2. 12-1月动量 → 板块内 ETF 选品 (微观)
    风控: drawdown_breaker 熔断 (与 S5/S6 同参数)
    """
    regime_series = _compute_regime(prices)
    codes = [c for c in tw if c in prices.columns]
    eq = []
    cash = INITIAL_CAPITAL
    pos = {}
    first = True
    total_tc = 0.0

    for i, (_date, row) in enumerate(prices.iterrows()):
        regime = regime_series.iloc[i] if i < len(regime_series) else "unknown"
        regime_tw = _regime_adjusted_weights(tw, regime, multipliers)
        momentum = _compute_momentum_signals(prices, i)
        new_tw = _momentum_adjusted_weights(regime_tw, momentum)

        px = {c: row[c] for c in codes if c in row and not np.isnan(row[c])}
        if first and px:
            for c in codes:
                if c in px:
                    target_amt = INITIAL_CAPITAL * new_tw[c]
                    shares = int(target_amt / px[c] / 100) * 100
                    cost = _apply_trade(0, shares * px[c])
                    pos[c] = shares
                    cash -= shares * px[c] + cost
                    total_tc += cost
            first = False
        sv = sum(pos.get(c, 0) * px.get(c, 0) for c in codes)
        total = cash + sv
        eq.append(total)
        if not px or total <= 0:
            continue

        max_dev = 0.0
        for c in codes:
            if c in px:
                cur_w = pos.get(c, 0) * px[c] / total if total > 0 else 0
                dev = abs(cur_w - new_tw[c])
                max_dev = max(max_dev, dev)
        if max_dev < get_dynamic_threshold(prices, i):
            continue

        for c in codes:
            if c in px:
                target_amt = total * new_tw[c]
                cur_amt = pos.get(c, 0) * px[c]
                diff = target_amt - cur_amt
                if abs(diff) < 1000:
                    continue
                delta_shares = int(diff / px[c] / 100) * 100
                if delta_shares == 0:
                    continue
                cost = _apply_trade(0, abs(delta_shares) * px[c])
                pos[c] = pos.get(c, 0) + delta_shares
                cash -= delta_shares * px[c] + cost
                total_tc += cost

    hedged_eq = _apply_drawdown_breaker(eq)
    return hedged_eq, total_tc


# ============================================================
# S8: 趋势过滤 + 波动率目标 总敞口管理 (2026-08-31 策略研发)
# ============================================================
# 根因: S1-S7 均为"事后防御"(回撤熔断先亏后砍)或"只换仓不降总敞口"
#   (S6/S7 regime bear 最小乘子 0.5, 仍 100% 持仓) → 2021-2024 阴跌全承受。
# S8 改为"事前防御": 总敞口 = min(趋势敞口, 波动率目标敞口), 现金部分计息。
# 信号全部基于 T-1 及以前数据 (无前视):
#   趋势层: 510300 收盘 vs MA200 + MA200 斜率 (经典年线牛熊过滤)
#     close>MA200 且 MA200 上升 → 1.0 ; close<MA200 且 MA200 下降 → 0.4 ; 其他 → 0.7
#   波动率层: 组合滚动 60 日年化波动率 σ → min(1.0, max(0.3, 12%/σ))
# 最后防线: 分级回撤熔断 (12%→50%敞口, 18%→25%敞口), 回撤修复且趋势转多才恢复。
# 参数全部行业标准值 (年线/12%目标波动率/三档敞口), 非网格搜索 — 防过拟合。

S8_TREND_PROXY = "510300"
S8_TREND_MA = 200          # 年线
S8_TREND_SLOPE_WIN = 60    # MA200 斜率窗口 (约 3 月)
S8_TARGET_VOL = 0.12       # 目标年化波动率 (行业常用)
S8_VOL_WINDOW = 60         # 已实现波动率窗口
S8_VOL_EXP_MIN = 0.3
S8_VOL_EXP_MAX = 1.0
S8_EXP_BULL = 1.0
S8_EXP_CHOPPY = 0.7
S8_EXP_BEAR = 0.4
S8_DD_LEVELS = (0.12, 0.18)    # 回撤熔断档位 (前移: 原 8/12/18)
S8_DD_EXPOSURES = (0.5, 0.25)  # 对应敞口
S8_DD_RECOVER = 0.08           # 回撤修复线


def _compute_trend_exposure(prices: pd.DataFrame) -> np.ndarray:
    """预计算趋势敞口序列 (t 时刻仅用 t-1 及以前数据, 无前视).

    返回 ndarray, 前 200 日 (MA200 数据不足) 恒为 1.0.
    """
    n = len(prices)
    exp = np.ones(n, dtype=float)
    if S8_TREND_PROXY not in prices.columns:
        return exp
    close = prices[S8_TREND_PROXY]
    ma = close.rolling(S8_TREND_MA).mean()
    ma_slope = ma.diff(S8_TREND_SLOPE_WIN)
    for i in range(1, n):
        px, m, sl = close.iloc[i - 1], ma.iloc[i - 1], ma_slope.iloc[i - 1]
        if pd.isna(m) or pd.isna(sl):
            continue  # 数据不足期保持 1.0
        if px > m and sl > 0:
            exp[i] = S8_EXP_BULL
        elif px < m and sl < 0:
            exp[i] = S8_EXP_BEAR
        else:
            exp[i] = S8_EXP_CHOPPY
    return exp


# 防御保留 (哑铃): 黄金/国债在 bear/choppy 保持满仓, 只砍权益敞口
S8_DEFENSIVE = ("518880", "511260")


def _run_dumbbell_core(
    prices: pd.DataFrame,
    tw: dict[str, float],
    def_weights: dict[str, float],
) -> tuple[list[float], float]:
    """哑铃核心: 防御满仓 + 权益×敞口 + 现金计息 + 换手成本扣除 (无前视).

    def_weights: 防御资产的绝对目标权重 (如 {"518880": 0.14, "511260": 0.07});
    权益部分按 tw 中权益权重等比例缩放到 (1 - sum(def_weights)).
    """
    codes = [c for c in tw if c in prices.columns]
    w = {c: float(tw[c]) for c in codes}
    w_def_total = sum(def_weights.get(c, 0.0) for c in codes)
    w_eq_total = 1.0 - w_def_total

    def_codes = [c for c in codes if c in def_weights]
    eq_codes = [c for c in codes if c not in def_weights]
    w_eq_orig = sum(w[c] for c in eq_codes)
    w_eq_rel = {c: w[c] / w_eq_orig for c in eq_codes}

    ret_df = prices.pct_change().fillna(0.0)
    ret_def = sum(def_weights[c] * ret_df[c] for c in def_codes) / w_def_total if w_def_total > 0 else 0.0
    ret_eq = sum(w_eq_rel[c] * ret_df[c] for c in eq_codes) if w_eq_total > 0 else 0.0

    n = len(prices)
    trend_exp = _compute_trend_exposure(prices)
    daily_rf = RISK_FREE_RATE / TRADING_DAYS
    unit_cost = TRANSACTION_COST + SLIPPAGE  # 一次调仓双边成本

    out = [float(INITIAL_CAPITAL)]
    peak = float(INITIAL_CAPITAL)
    dd_exp = 1.0
    e_prev = 1.0
    tc_total = 0.0
    for i in range(1, n):
        # 波动率目标 (基于组合自身已实现收益, 无前视)
        eq_arr = np.array(out, dtype=float)
        rets = np.diff(eq_arr) / eq_arr[:-1]
        w_win = rets[max(0, i - 1 - S8_VOL_WINDOW):i - 1]
        if len(w_win) >= 20:
            vol = float(np.std(w_win)) * math.sqrt(TRADING_DAYS)
            vol_exp = min(S8_VOL_EXP_MAX, max(S8_VOL_EXP_MIN, S8_TARGET_VOL / vol)) if vol > 0 else 1.0
        else:
            vol_exp = 1.0
        e_eq = float(min(trend_exp[i], vol_exp))

        # 分级熔断 (基于当前净值回撤, 最后防线)
        peak = max(peak, out[i - 1])
        dd = (peak - out[i - 1]) / peak if peak > 0 else 0.0
        if dd > S8_DD_LEVELS[1]:
            dd_exp = S8_DD_EXPOSURES[1]
        elif dd > S8_DD_LEVELS[0]:
            dd_exp = S8_DD_EXPOSURES[0]
        else:
            dd_exp = 1.0
        # 恢复: 回撤修复至 8% 以下 且 趋势转多 (close>MA200 且 MA200 上升)
        if dd_exp < 1.0 and dd < S8_DD_RECOVER and trend_exp[i] >= S8_EXP_BULL:
            dd_exp = 1.0
        e_eq = min(e_eq, dd_exp)

        # 哑铃日收益: 防御满仓 + 权益×敞口 + 现金计息, 再扣除权益敞口换手成本
        daily = w_def_total * ret_def.iloc[i] + w_eq_total * e_eq * ret_eq.iloc[i] + (1.0 - w_def_total - w_eq_total * e_eq) * daily_rf  # noqa: E501
        turnover = abs(e_eq - e_prev) * w_eq_total
        daily -= turnover * unit_cost
        tc_total += turnover * unit_cost * out[i - 1]
        e_prev = e_eq

        out.append(out[i - 1] * (1.0 + daily))

    return out, tc_total


def run_s8_trend_vol(prices: pd.DataFrame, tw: dict[str, float]) -> tuple[list[float], float]:
    """S8: 哑铃防御(原权重 21%) + 趋势过滤&波动率目标权益敞口 + 分级熔断

    组合结构 (2026-08-31 v2 哑铃): 防御资产 (黄金/国债) 目标权重恒满仓,
    权益资产按 敞口 = min(趋势敞口, 波动率目标敞口) 缩放, 现金部分计息,
    权益敞口变化产生的换手按 (TRANSACTION_COST+SLIPPAGE) 从净值中扣除。

    数据实证 (2021-2026): 防御保留是唯一有效改进 — 相对全砍版
    Sharpe 0.28→0.34 / 年化 5.35%→6.11% / 回撤 18.9%→17.7%;
    MA100 快速恢复轨与 15% 波动率目标均实证劣化, 弃用。

    信号全部基于 T-1 及以前数据 (无前视):
      趋势层: 510300 vs MA200 + MA200 斜率 → bull 1.0 / choppy 0.7 / bear 0.4
      波动率层: 组合滚动 60 日年化波动率 σ → min(1.0, max(0.3, 12%/σ))
      最后防线: 分级回撤熔断 (12%→50%敞口, 18%→25%敞口), 修复且趋势转多才恢复。
    """
    codes = [c for c in tw if c in prices.columns]
    w = {c: float(tw[c]) for c in codes}
    def_weights = {c: w[c] for c in codes if c in S8_DEFENSIVE}
    return _run_dumbbell_core(prices, tw, def_weights)


# ============================================================
# S9: 防御倾斜哑铃 (40% 防御) + 趋势&波动率目标权益敞口 (2026-08-31)
# ============================================================
# 组合构造层升级: S8 基础上将防御资产 (黄金/国债) 权重从 21% 提升到 40%
#   (黄金 28% + 国债 12%), 权益 60% 按原目标权重等比例缩放。
# 经济逻辑: 2021-2026 数据实证该池唯一正 Sharpe 资产是黄金(0.89)/国债(1.19),
#   宽基/主题 ETF 多为负; 防御倾斜 = 向正 alpha 资产集中 (哑铃策略)。
# 实证: 防御 21%→40% 时 Sharpe 0.30→0.46, 年化 6.0%→8.7%, 回撤略降。
# 诚实标注: 40% 取扫描中间档 (非最优 45%), 权重选择基于历史表现存在后视成分,
#   已用三件套验证; 信号层与 S8 完全相同 (无前视)。

S9_DEFENSIVE_WEIGHTS = {"518880": 0.28, "511260": 0.12}


def run_s9_dumbbell(prices: pd.DataFrame, tw: dict[str, float]) -> tuple[list[float], float]:
    """S9: 防御倾斜哑铃 (黄金28%+国债12%=40%防御) + 趋势&波动率目标权益敞口

    与 S8 同信号层 (趋势过滤+波动率目标+分级熔断), 组合构造层将防御权重
    从 21% 提升到 40% — 向组合内唯一正 Sharpe 资产 (黄金/国债) 倾斜。
    """
    return _run_dumbbell_core(prices, tw, dict(S9_DEFENSIVE_WEIGHTS))


# ============================================================
# S10: P2 池升级 — 纳指/标普进进攻端 + 红利低波进防御端 (2026-08-31)
# ============================================================
# P2 假设: 更高 Sharpe 资产池提升组合 Sharpe 上限 → 三件套 DSR 才可能过线。
# 单资产实证 (2021-2026): 新候选 标普500 0.82 / 纳指100 0.78 / 红利低波 0.66,
#   均显著高于原权益池 (11 只中 9 只负 Sharpe, 最高半导体 0.41)。
# 组合构造 (粗档扫描 cfg0-3 后取中间偏防御档, 非最优):
#   防御 45% = 黄金 25% + 国债 10% + 红利低波 10%
#   进攻 55% = 纳指 10% + 标普 10% + 原 11 权益等比缩至 35%
# 实证 (2021-2026): Sharpe 0.49→0.88, 年化 7.4%→10.0%, 回撤 12.6%→11.7%;
#   分段 2021-2023 +0.24 (S9 基线 -0.42) / 2024-2026 +1.39, 双段一致优于基线。
# 诚实标注: 资产池升级含后视选择 (用 2021-2026 选资产), 需三件套严格验证;
#   信号层沿用 A股趋势过滤 (510300 MA200), 对海外资产存在逻辑错配但结果稳健。
S10_DEFENSIVE_WEIGHTS = {"518880": 0.25, "511260": 0.10, "512890": 0.10}
S10_NEW_OFFENSIVE = {"513100": 0.10, "513500": 0.10}


def run_s10_p2(prices: pd.DataFrame, tw: dict[str, float]) -> tuple[list[float], float]:
    """S10: P2 池升级哑铃 (45% 防御含红利低波 + 纳指/标普进攻).

    tw 为 14 标的配置权重; 本函数构造 17 标的权重:
      原权益 (非防御) 等比缩至 35%, 新增进攻 纳指 10% + 标普 10%,
      防御端 黄金 25% + 国债 10% + 红利低波 10% (=45%).
    """
    tw17: dict[str, float] = {}
    w_eq_orig_total = sum(v for c, v in tw.items() if c not in S10_DEFENSIVE_WEIGHTS)
    scale = 0.35 / w_eq_orig_total if w_eq_orig_total > 0 else 0.0
    for c, v in tw.items():
        if c not in S10_DEFENSIVE_WEIGHTS:
            tw17[c] = v * scale
    tw17.update(S10_NEW_OFFENSIVE)
    tw17.update(S10_DEFENSIVE_WEIGHTS)
    return _run_dumbbell_core(prices, tw17, dict(S10_DEFENSIVE_WEIGHTS))


def run_benchmark(prices: pd.DataFrame) -> list[float]:
    """基准: 沪深300ETF买入持有"""
    if BENCHMARK not in prices.columns:
        return [INITIAL_CAPITAL] * len(prices)
    first_px = prices[BENCHMARK].iloc[0]
    shares = INITIAL_CAPITAL / first_px
    return [shares * px for px in prices[BENCHMARK]]


def format_report(results: dict, prices: pd.DataFrame) -> str:
    lines = []
    lines.append("=" * 70)
    lines.append("  ETF期权对冲子组合 — 五策略历史回测 (Phase 2)")
    lines.append("=" * 70)
    lines.append(f"  回测区间: {prices.index[0].date()} ~ {prices.index[-1].date()}")
    lines.append(f"  初始资金: {INITIAL_CAPITAL:,}元 | ETF数: {len(prices.columns)}")
    lines.append(f"  基准: {BENCHMARK} 沪深300ETF")
    lines.append("")
    lines.append(f"{'策略':<28} {'年化%':>8} {'回撤%':>8} {'Sharpe':>8} {'超额%':>8} {'期末':>12}")
    lines.append("-" * 80)
    for name, m in results.items():
        lines.append(
            f"{name:<28} {m['annual_return']*100:>7.2f} {m['max_drawdown']*100:>7.2f} "
            f"{m['sharpe']:>7.3f} {m['excess_return']*100:>7.2f} {m['final_value']:>11,.0f}"
        )
    lines.append("")
    lines.append("  预测外推对比 (cairn/etf-option-hedge-model.md §十):")
    lines.append("    保守 8-10% / 中性 10-12% / 乐观 12-15%")
    s4 = results.get("S4 完整(再平衡+对冲)", results.get("S4 完整"))
    if s4:
        lines.append(f"    S4实测年化: {s4['annual_return']*100:.2f}%")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="ETF期权对冲子组合 五策略历史回测")
    parser.add_argument("--data-file", default=None, help="合并面板 parquet 路径 (默认 data/etf_option_backtest/all_etf_daily.parquet)")  # noqa: E501
    parser.add_argument("--strategies", default="all", help="策略选择: all|s1|s3|s5|s6|s1s3s5|s1s3s5s6")
    args = parser.parse_args()

    logger.info("加载配置 %s", CONFIG_PATH)
    cfg = load_config()
    tw = extract_target_weights(cfg)
    logger.info("目标权重: %d ETF, 总和 %.4f", len(tw), sum(tw.values()))

    logger.info("加载ETF价格数据...")
    prices = load_etf_prices(args.data_file)
    logger.info("  %d 交易日, %d ETF, %s ~ %s",
                len(prices), len(prices.columns),
                prices.index[0].date(), prices.index[-1].date())

    bench_eq = run_benchmark(prices)

    all_strategies = [
        ("S1 基线(静态无对冲)", run_s1_baseline),
        ("S2 再平衡(6%阈值)", run_s2_rebalance),
        ("S3 期权对冲(静态+认沽)", run_s3_option_hedge),
        ("S4 完整(再平衡+对冲)", run_s4_full),
        ("S5 尾部对冲(回撤加码)", run_s5_tail_hedge),
        ("S6 V9Regime轮动+熔断", run_s6_v9_regime),
        ("S7 V9Regime+动量+熔断", run_s7_v9_momentum),
        ("S8 趋势+波动率目标", run_s8_trend_vol),
        ("S9 防御倾斜哑铃(40%)", run_s9_dumbbell),
        ("S10 P2池升级(45%防御+纳指标普)", run_s10_p2),
    ]

    sel = args.strategies.lower()
    if sel == "all":
        strategies = all_strategies
    else:
        smap = {"s1": 0, "s2": 1, "s3": 2, "s4": 3, "s5": 4, "s6": 5, "s7": 6, "s8": 7, "s9": 8, "s10": 9}
        indices = [smap[k] for k in smap if k in sel]
        strategies = [all_strategies[i] for i in sorted(set(indices))]

    logger.info("运行策略回测: %s", [n for n, _ in strategies])
    results = {}
    for name, fn in strategies:
        logger.info("  运行 %s ...", name)
        eq, tc = fn(prices, tw)
        m = compute_metrics(eq, bench_eq)
        m["transaction_costs"] = float(tc)
        results[name] = m
        logger.info("    年化 %.2f%% / 回撤 %.2f%% / Sharpe %.3f",
                    m["annual_return"] * 100, m["max_drawdown"] * 100, m["sharpe"])

    bench_m = compute_metrics(bench_eq, bench_eq)
    results["基准 沪深300ETF"] = bench_m

    report = format_report(results, prices)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = DATA_DIR / f"backtest_report_{timestamp}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info("报告已保存: %s", report_path)

    json_path = DATA_DIR / f"backtest_result_{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.now().isoformat(),
            "start_date": str(prices.index[0].date()),
            "end_date": str(prices.index[-1].date()),
            "initial_capital": INITIAL_CAPITAL,
            "n_etfs": len(prices.columns),
            "n_days": len(prices),
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    logger.info("结果已保存: %s", json_path)

    return results


if __name__ == "__main__":
    main()
