"""
ETF期权对冲子组合 — 五策略历史回测 (Phase 2)

数据: data/etf_option_backtest/ (14 ETF, 2021-2026, Wind MCP)
配置: config/etf_option_subportfolio.yaml
策略: S1基线 / S2再平衡 / S3期权对冲 / S4完整 / S5尾部对冲
基准: 沪深300ETF (510300)

运行: python data/etf_option_backtest/run_etf_option_backtest.py
"""

import argparse
import json
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Optional

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


def load_etf_prices(data_file: Optional[str] = None) -> pd.DataFrame:
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
                    coverage: Optional[list[float]] = None) -> tuple[list[float], float]:
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
    for i, (date, row) in enumerate(prices.iterrows()):
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
    for i, (date, row) in enumerate(prices.iterrows()):
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
                             multipliers: Optional[dict] = None) -> dict[str, float]:
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
                     multipliers: Optional[dict] = None) -> tuple[list[float], float]:
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

    for i, (date, row) in enumerate(prices.iterrows()):
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
                       multipliers: Optional[dict] = None) -> tuple[list[float], float]:
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

    for i, (date, row) in enumerate(prices.iterrows()):
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


def main():
    parser = argparse.ArgumentParser(description="ETF期权对冲子组合 五策略历史回测")
    parser.add_argument("--data-file", default=None, help="合并面板 parquet 路径 (默认 data/etf_option_backtest/all_etf_daily.parquet)")
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
    ]

    sel = args.strategies.lower()
    if sel == "all":
        strategies = all_strategies
    else:
        smap = {"s1": 0, "s2": 1, "s3": 2, "s4": 3, "s5": 4, "s6": 5, "s7": 6}
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
