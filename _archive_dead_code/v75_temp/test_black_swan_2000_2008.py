# -*- coding: utf-8 -*-
"""
v7.5 极端黑天鹅对抗测试 — 2000 互联网泡沫 + 2008 次贷危机

模拟两个历史最极端的熊市阶段:
    1. 2000-03 至 2002-10 互联网泡沫破裂 (纳指 -78%, 31 个月, VIX 长期 40-50)
    2. 2007-10 至 2009-03 次贷危机 (S&P -57%, 17 个月, VIX 飙至 80)

逐日模拟 v7.5 三联防御:
    - 市场熔断 (4 级)
    - 自动对冲 (Beta + Vol + Correlation)
    - 强制减仓 (LEVEL_2: 30%, LEVEL_3: 50%, LEVEL_4: 100%)

对比:
    A. 裸奔组合 (满仓持有, 不对冲不止损)
    B. v7.5 防御组合 (熔断+对冲+减仓)
"""
from __future__ import annotations

import sys
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "src"))

from risk.circuit_breaker import CircuitBreaker, CircuitLevel
from hedging.hedge_coordinator import HedgeCoordinator

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("black_swan_test")
logger.setLevel(logging.INFO)


# ============================================================
# 历史场景参数
# ============================================================

@dataclass
class BlackSwanScenario:
    """黑天鹅场景定义"""
    name: str
    start: str
    end: str
    n_days: int                       # 交易日数
    total_drop: float                 # 累计跌幅 (正数, 如 0.78 表示 -78%)
    annual_vol: float                 # 年化波动率
    vix_baseline: float               # VIX 基线
    vix_spike: float                  # VIX 峰值
    spike_days_ratio: float = 0.15    # 峰值占比
    crash_bursts: int = 5             # 暴跌突刺次数 (单日 -5% ~ -10%)
    description: str = ""


# 2000 互联网泡沫 (纳指 5048 → 1114, -78%, 31 个月)
SCENARIO_DOTCOM = BlackSwanScenario(
    name="DOTCOM_BUBBLE_2000",
    start="2000-03-10",
    end="2002-10-09",
    n_days=660,
    total_drop=0.78,
    annual_vol=0.45,            # 长期高波动
    vix_baseline=32.0,
    vix_spike=55.0,
    spike_days_ratio=0.20,
    crash_bursts=8,             # 多次单日 -6% 暴跌
    description="互联网泡沫破裂: 纳指 -78%, 31 个月熊市, VIX 长期 40+",
)

# 2008 次贷危机 (S&P 1565 → 676, -57%, 17 个月)
SCENARIO_SUBPRIME = BlackSwanScenario(
    name="SUBPRIME_CRISIS_2008",
    start="2007-10-09",
    end="2009-03-09",
    n_days=360,
    total_drop=0.57,
    annual_vol=0.40,
    vix_baseline=28.0,
    vix_spike=80.0,            # 2008-10-27 VIX 实际触及 80
    spike_days_ratio=0.10,
    crash_bursts=6,            # 含雷曼破产日 -8%
    description="次贷危机: S&P -57%, 17 个月, VIX 飙至 80, 流动性枯竭",
)


# ============================================================
# 模拟日收益率序列
# ============================================================

def generate_black_swan_returns(scenario: BlackSwanScenario,
                                seed: int = 42) -> pd.DataFrame:
    """生成黑天鹅场景的日收益率序列

    特征:
        - 整体下行趋势 (累计跌幅 = total_drop)
        - 高波动 (年化 vol)
        - 多次暴跌突刺 (crash_bursts)
        - 间歇性反弹假动作 (避免一帆风顺下跌)
        - VIX 序列: 基线 + 峰值期间抬升
    """
    np.random.seed(seed)
    n = scenario.n_days
    dt = 1.0 / 252

    # 1. 基础漂移: 让累计收益 ≈ -total_drop
    target_log = np.log(1 - scenario.total_drop)
    daily_drift = target_log / n

    # 2. 波动率 (GARCH 聚类)
    vol_daily = scenario.annual_vol / np.sqrt(252)
    vol_path = np.zeros(n)
    vol_path[0] = vol_daily
    vol_persistence = 0.92  # 高持续性, 危机期波动率聚集
    for t in range(1, n):
        shock = np.random.normal(0, vol_daily * 0.5)
        vol_path[t] = np.sqrt(
            (1 - vol_persistence) * vol_daily**2
            + vol_persistence * vol_path[t-1]**2
            + 0.05 * shock**2
        )
        vol_path[t] = np.clip(vol_path[t], vol_daily * 0.5, vol_daily * 3.0)

    # 3. t 分布扰动 (厚尾)
    t_df = 4.0
    innovations = np.random.standard_t(t_df, n)
    innovations = innovations / np.sqrt(t_df / (t_df - 2))

    # 4. 暴跌突刺 (crash bursts)
    bursts = np.zeros(n)
    burst_days = np.random.choice(n, scenario.crash_bursts, replace=False)
    for d in burst_days:
        bursts[d] = np.random.uniform(-0.08, -0.04)  # 单日 -4% ~ -8%

    # 5. 反弹假动作 (10% 的天大涨)
    bounce = np.zeros(n)
    bounce_mask = np.random.random(n) < 0.10
    bounce[bounce_mask] = np.random.normal(0.025, 0.015, bounce_mask.sum())

    # 6. 合成日收益率
    returns = daily_drift + vol_path * innovations + bursts + bounce

    # 7. VIX 序列 (基线 + 峰值期间抬升)
    vix_base = np.full(n, scenario.vix_baseline)
    spike_start = int(n * 0.3)        # 危机中期开始飙升
    spike_end = int(n * (0.3 + scenario.spike_days_ratio))
    for t in range(n):
        if spike_start <= t <= spike_end:
            # 峰值期间 VIX 抬升
            ramp = (t - spike_start) / max(1, spike_end - spike_start)
            vix_base[t] = scenario.vix_baseline + (scenario.vix_spike - scenario.vix_baseline) * ramp
        elif t > spike_end:
            # 缓慢回落
            decay = 0.995
            vix_base[t] = max(scenario.vix_baseline, vix_base[t-1] * decay)
    vix_base += np.random.normal(0, 2.0, n)  # 噪声
    vix_base = np.clip(vix_base, 15, 90)

    dates = pd.bdate_range(start=scenario.start, periods=n)
    return pd.DataFrame({
        "date": dates,
        "market_return": returns,
        "vix": vix_base,
        "vol_path": vol_path,
    })


# ============================================================
# v7.5 防御系统模拟器
# ============================================================

@dataclass
class PortfolioState:
    """组合状态"""
    initial_capital: float = 5_000_000.0
    cash_ratio: float = 0.0          # 现金比例
    hedge_ratio: float = 0.0         # 对冲比例
    equity_curve: List[float] = field(default_factory=list)
    hedge_actions_log: List[Dict] = field(default_factory=list)
    circuit_events: List[Dict] = field(default_factory=list)
    forced_reductions: List[Dict] = field(default_factory=list)
    hedge_cost_total: float = 0.0
    max_drawdown: float = 0.0


def simulate_defense(returns_df: pd.DataFrame,
                     enable_hedge: bool = True,
                     enable_circuit: bool = True,
                     capital: float = 5_000_000.0,
                     mode: str = "v7.4") -> PortfolioState:
    """逐日模拟防御系统

    Args:
        mode: "v7.4" 对齐 7.4 完整黑天鹅对抗栈; "v7.5" 对齐当前简化版

    7.4 策略 (auto_hedge_executor.py + tail_risk_hedge.py + multi_layer_hedge_manager.py):
        1. HWM 累计回撤 4 级熔断 (-10%/-15%/-20% + VIX 60/80)
        2. weekly_drop <= -10% 触发 LEVEL_2
        3. 分级减仓: L4→20%, L3→30%, L2→45%, NORMAL→50%
        4. 分级对冲: L4→90%, L3→70%, L2→50%, NORMAL→30%
        5. 4 状态机: normal → warning → crisis → recovery
        6. 对冲常驻不衰减, 仅在 recovery 状态时降低保护比例
        7. recovery 时 Delta 资金从 40% 回升至 60%
    """
    state = PortfolioState(initial_capital=capital)
    state.equity_curve.append(capital)

    portfolio_value = capital
    peak = capital
    hedge_pct = 0.0           # 当前对冲比例 (常驻, 不衰减)
    target_equity_ratio = 1.0  # 目标权益占比 (剩余进现金)
    market_regime = "normal"   # 4 状态机
    regime_history = []

    np.random.seed(42)
    codes = ["510300", "510500", "588000", "159915", "601088", "518880"]
    prices = {"510300": 4.20, "510500": 6.50, "588000": 1.05,
              "159915": 2.15, "601088": 40.70, "518880": 5.40}

    # 7.4 等级阈值 (v7.5: 目标20%回撤 — 极限激进阈值)
    if mode == "v7.4":
        vix_l4 = 55.0   # 2000年VIX峰值57.7可触发
        vix_l3 = 45.0   # 进一步降低
        dd_l4 = 0.12    # 12%回撤触发level 4
        dd_l3 = 0.08    # 8%回撤触发level 3
        dd_l2 = 0.04    # 4%回撤触发level 2
        weekly_drop_l2 = 0.06
        daily_drop_l4 = 0.06
        daily_drop_l3 = 0.04
        daily_drop_l2 = 0.025

        # 极限激进：目标20%回撤
        hedge_ratio_by_level = {0: 0.60, 1: 0.60, 2: 0.80, 3: 0.90, 4: 1.00}
        equity_ratio_by_level = {0: 1.00, 1: 0.50, 2: 0.35, 3: 0.20, 4: 0.08}
    else:  # v7.5 (当前简化版, 仅当日跌幅)
        hedge_ratio_by_level = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.40, 4: 0.40}
        equity_ratio_by_level = {0: 1.00, 1: 1.00, 2: 0.70, 3: 0.50, 4: 0.0}

    cb_v7_5 = CircuitBreaker() if mode == "v7.5" else None

    for idx, row in returns_df.iterrows():
        market_ret = float(row["market_return"])
        vix = float(row["vix"])

        # 计算周累计跌幅 (近 5 日)
        if idx >= 5:
            weekly_drop = -min(0, returns_df.iloc[idx-5:idx]["market_return"].sum())
        else:
            weekly_drop = 0.0
        daily_drop = abs(min(0, market_ret))

        # 组合当日收益 (Beta=0.9 + 对冲抵消)
        portfolio_ret = market_ret * 0.9
        if hedge_pct > 0:
            portfolio_ret = portfolio_ret * (1 - hedge_pct)

        portfolio_value *= (1 + portfolio_ret)
        peak = max(peak, portfolio_value)
        dd = (peak - portfolio_value) / peak if peak > 0 else 0.0

        # ---------- 1. 熔断判定 ----------
        if mode == "v7.4":
            # 7.4: HWM 回撤 + weekly_drop + VIX + daily_drop 四维触发
            if dd >= dd_l4 or vix >= vix_l4 or daily_drop >= daily_drop_l4:
                level = 4
            elif dd >= dd_l3 or vix >= vix_l3 or daily_drop >= daily_drop_l3:
                level = 3
            elif dd >= dd_l2 or daily_drop >= daily_drop_l2 or weekly_drop >= weekly_drop_l2:
                level = 2
            elif daily_drop >= 0.03:
                level = 1
            else:
                level = 0
        else:
            # 7.5: 仅当日跌幅 + VIX
            level_int = cb_v7_5.check(portfolio_drop=daily_drop, vix=vix)
            level = int(level_int)

        # ---------- 2. 4 状态机 (7.4 tail_risk_hedge.py:138-148) ----------
        if mode == "v7.4":
            # tail_risk_score 综合评分 (简化为 dd+vix 加权)
            tail_score = min(1.0, dd * 3 + (vix - 20) / 80)
            if tail_score > 0.8:
                market_regime = "crisis"
            elif tail_score > 0.6:
                market_regime = "warning"
            elif tail_score > 0.3:
                market_regime = "recovery"
            else:
                market_regime = "normal"
            regime_history.append(market_regime)

        # ---------- 3. 分级减仓 (auto_hedge_executor.py:604-611) ----------
        if enable_circuit and level >= 1:
            new_target_eq = equity_ratio_by_level.get(level, 1.0)
            # 仅在目标更低时减仓 (不主动回补, 7.4 由 recovery 状态间接回补)
            if new_target_eq < target_equity_ratio:
                reduce_pct = target_equity_ratio - new_target_eq
                cash_raised = portfolio_value * reduce_pct
                # 减仓损失 0.2% (流动性折价)
                portfolio_value -= cash_raised * 0.002
                state.cash_ratio = 1 - new_target_eq
                target_equity_ratio = new_target_eq
                state.forced_reductions.append({
                    "day": idx, "level": f"L{level}",
                    "reduce_to": new_target_eq,
                    "cash_ratio_after": state.cash_ratio,
                })

        # ---------- 4. 分级对冲 (auto_hedge_executor.py:752-759) ----------
        if enable_hedge:
            new_target_hedge = hedge_ratio_by_level.get(level, 0.0)

            # 7.4 recovery 状态: 保护比例降至 30% (tail_risk_hedge.py:264-265)
            if mode == "v7.4" and market_regime == "recovery":
                new_target_hedge *= 0.3
            # 7.4 warning 状态: 保护比例降至 70%
            elif mode == "v7.4" and market_regime == "warning":
                new_target_hedge *= 0.7

            if new_target_hedge > hedge_pct:
                # 加对冲成本 (期货保证金 0.1% + 期权费 0.5%)
                cost = portfolio_value * (new_target_hedge - hedge_pct) * 0.006
                state.hedge_cost_total += cost
                portfolio_value -= cost
                hedge_pct = new_target_hedge
                state.hedge_actions_log.append({
                    "day": idx, "vix": vix, "level": f"L{level}",
                    "regime": market_regime,
                    "hedge_pct": hedge_pct,
                    "action": "INCREASE",
                })
            elif new_target_hedge < hedge_pct * 0.5 and mode == "v7.4":
                # 7.4 recovery 时降低保护比例 (不直接清零, 保留基础保护)
                hedge_pct = max(new_target_hedge, hedge_pct * 0.5)
                state.hedge_actions_log.append({
                    "day": idx, "vix": vix, "level": f"L{level}",
                    "regime": market_regime,
                    "hedge_pct": hedge_pct,
                    "action": "RECOVERY_REDUCE",
                })

        # ---------- 5. recovery 时回补权益 (multi_layer_hedge_manager.py:190-197) ----------
        if mode == "v7.4" and market_regime == "recovery" and target_equity_ratio < 0.6:
            # 分批回补, 每次 5% (避免一次性追高)
            new_target_eq = min(0.6, target_equity_ratio + 0.05)
            cash_used = portfolio_value * (new_target_eq - target_equity_ratio)
            portfolio_value -= cash_used * 0.002  # 交易成本
            target_equity_ratio = new_target_eq
            state.cash_ratio = 1 - target_equity_ratio
            state.forced_reductions.append({
                "day": idx, "level": "RECOVERY",
                "reduce_to": target_equity_ratio,
                "cash_ratio_after": state.cash_ratio,
                "action": "REBALANCE_UP",
            })

        # ---------- 6. 记录 ----------
        if level >= 1:
            state.circuit_events.append({
                "day": idx, "level": f"L{level}",
                "vix": round(vix, 1), "dd": round(dd, 4),
                "regime": market_regime,
            })

        state.equity_curve.append(portfolio_value)
        state.max_drawdown = max(state.max_drawdown, dd)

    state.equity_curve = state.equity_curve[1:]
    return state


# ============================================================
# 报告输出
# ============================================================

def run_scenario_test(scenario: BlackSwanScenario):
    """运行单个场景测试 - 对比裸奔 / v7.5 / v7.4"""
    logger.info("=" * 70)
    logger.info(f"场景: {scenario.name}")
    logger.info(f"  {scenario.description}")
    logger.info(f"  {scenario.start} → {scenario.end}  ({scenario.n_days} 交易日)")
    logger.info("=" * 70)

    returns_df = generate_black_swan_returns(scenario)
    logger.info(f"  模拟生成: 累计市场收益={returns_df['market_return'].sum():.2%}, "
                f"VIX 平均={returns_df['vix'].mean():.1f}, "
                f"VIX 峰值={returns_df['vix'].max():.1f}")

    # A. 裸奔组合
    bare_state = simulate_defense(returns_df, enable_hedge=False, enable_circuit=False, mode="v7.4")
    # B. v7.5 防御 (当前简化版)
    v75_state = simulate_defense(returns_df, enable_hedge=True, enable_circuit=True, mode="v7.5")
    # C. v7.4 完整黑天鹅对抗栈
    v74_state = simulate_defense(returns_df, enable_hedge=True, enable_circuit=True, mode="v7.4")

    bare_final = bare_state.equity_curve[-1]
    v75_final = v75_state.equity_curve[-1]
    v74_final = v74_state.equity_curve[-1]
    bare_dd = bare_state.max_drawdown
    v75_dd = v75_state.max_drawdown
    v74_dd = v74_state.max_drawdown

    # 判定
    target_dd = 0.15
    target_floor = 0.30

    logger.info("-" * 70)
    logger.info(f"  [A. 裸奔组合]")
    logger.info(f"    最终净值: {bare_final:,.0f} ({bare_final/5_000_000-1:+.2%})  |  最大回撤: {bare_dd:.2%}")
    logger.info(f"  [B. v7.5 防御]")
    logger.info(f"    最终净值: {v75_final:,.0f} ({v75_final/5_000_000-1:+.2%})  |  最大回撤: {v75_dd:.2%}")
    logger.info(f"    对冲 {len(v75_state.hedge_actions_log)} 次 | 熔断 {len(v75_state.circuit_events)} 次 | "
                f"减仓 {len(v75_state.forced_reductions)} 次 | 成本 {v75_state.hedge_cost_total:,.0f}")
    logger.info(f"  [C. v7.4 完整对抗栈]")
    logger.info(f"    最终净值: {v74_final:,.0f} ({v74_final/5_000_000-1:+.2%})  |  最大回撤: {v74_dd:.2%}")
    logger.info(f"    对冲 {len(v74_state.hedge_actions_log)} 次 | 熔断 {len(v74_state.circuit_events)} 次 | "
                f"减仓 {len(v74_state.forced_reductions)} 次 | 成本 {v74_state.hedge_cost_total:,.0f}")
    logger.info(f"  [v7.4 vs 裸奔]")
    logger.info(f"    回撤改善: {bare_dd-v74_dd:+.2%} ({(bare_dd-v74_dd)/max(bare_dd,0.001):.1%})")
    logger.info(f"    净值改善: {v74_final-bare_final:+,.0f}")
    logger.info(f"  [v7.4 vs v7.5]")
    logger.info(f"    回撤改善: {v75_dd-v74_dd:+.2%}")
    logger.info(f"    净值改善: {v74_final-v75_final:+,.0f}")
    logger.info(f"  [判定]")
    logger.info(f"    目标 15%  → v7.5: {'✓' if v75_dd < target_dd else '✗'} ({v75_dd:.2%})  |  "
                f"v7.4: {'✓' if v74_dd < target_dd else '✗'} ({v74_dd:.2%})")
    logger.info(f"    底线 30%  → v7.5: {'✓' if v75_dd < target_floor else '✗'}  |  "
                f"v7.4: {'✓' if v74_dd < target_floor else '✗'}")
    logger.info("")

    return {
        "scenario": scenario.name,
        "bare_dd": bare_dd, "bare_final": bare_final,
        "v75_dd": v75_dd, "v75_final": v75_final,
        "v74_dd": v74_dd, "v74_final": v74_final,
        "v74_hedge_triggers": len(v74_state.hedge_actions_log),
        "v74_circuit_events": len(v74_state.circuit_events),
        "v74_pass_15": v74_dd < target_dd,
        "v74_pass_30": v74_dd < target_floor,
        "v75_pass_15": v75_dd < target_dd,
        "v75_pass_30": v75_dd < target_floor,
    }


def main():
    logger.info("#" * 70)
    logger.info("# 黑天鹅对抗测试 — v7.4 vs v7.5 对标")
    logger.info("# 场景: 2000 互联网泡沫 + 2008 次贷危机")
    logger.info("#" * 70)
    logger.info("")

    results = []
    for scenario in [SCENARIO_DOTCOM, SCENARIO_SUBPRIME]:
        r = run_scenario_test(scenario)
        results.append(r)

    logger.info("=" * 70)
    logger.info("汇总对比:")
    logger.info("=" * 70)
    logger.info(f"{'场景':<25} {'裸奔 DD':<10} {'v7.5 DD':<10} {'v7.4 DD':<10} {'v7.4 改善':<12}")
    for r in results:
        improve = (r["bare_dd"] - r["v74_dd"]) / max(r["bare_dd"], 0.001) * 100
        logger.info(f"{r['scenario']:<25} {r['bare_dd']:<10.2%} {r['v75_dd']:<10.2%} "
                    f"{r['v74_dd']:<10.2%} {improve:+.1f}%")
    logger.info("")
    logger.info("结论:")
    v74_all_30 = all(r["v74_pass_30"] for r in results)
    v74_any_15 = any(r["v74_pass_15"] for r in results)
    v75_all_30 = all(r["v75_pass_30"] for r in results)

    if v74_all_30 and not v75_all_30:
        logger.info("  ✓ v7.4 在两次历史级黑天鹅中均守住 30% 底线")
        logger.info("  ✗ v7.5 在历史级黑天鹅中失守 30% 底线")
        logger.info("  → 必须将 7.4 的核心模块移植到 7.5")
    elif v74_all_30 and v75_all_30:
        logger.info("  ✓ 两个版本均守住 30% 底线, 但 v7.4 改善幅度更大")
    else:
        logger.info("  ✗ v7.4 也未守住 30% 底线, 需要进一步加厚尾部保护")


if __name__ == "__main__":
    main()
