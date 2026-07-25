"""P0-8 极端压力测试模块

解决诊断报告:
  "当前压力测试仅包含'市场下跌-20%'和'单标的-30%'两个情景。
   A股历史极端事件推演显示...
   - 2015股灾 (沪深300 -45%) → 组合回撤 -35%至-42%
   - 2016熔断 (-25%) → -18%至-25%
   - 2018贸易战 (-32%) → -22%至-30%
   - 2020疫情闪崩 (-16%) → -12%至-18%
   - 2024国庆后暴跌 (-20%) → -18%至-25%"

功能:
  1. 6个历史极端情景定义
  2. 组合压力损失计算
  3. 板块损失分解
  4. TAIL_MAX_HEDGE 提升至 0.60
  5. 回撤>20%自动清仓至50%仓位硬止损
  6. 蒙特卡洛压力模拟
  7. MC VaR / ES 计算
  8. 压力测试日报生成
"""

from __future__ import annotations

import math
import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger('stress_test')

# ── 常量 ──────────────────────────────────────────────────────────────────

# v5.10 P0-8: 尾部保护阈值从 0.40 提升至 0.60
TAIL_HEDGE_THRESHOLD: float = 0.60

# 硬止损: 回撤超过此阈值自动减仓至 50%
HARD_STOP_MAX_DRAWDOWN: float = -0.20

# ── 历史极端情景 ──────────────────────────────────────────────────────────

HISTORICAL_SCENARIOS: List[Dict[str, Any]] = [
    {
        'name': '2015股灾',
        'market_drop': -0.45,
        'description': '2015年6月A股泡沫破裂，沪深300最大回撤约45%',
        'sector_impacts': {
            '高端制造': -0.50,
            '顺周期': -0.35,
            '资源': -0.05,
            '防御': -0.15,
        },
        'duration_days': 90,
        'recovery_months': 24,
    },
    {
        'name': '2016熔断',
        'market_drop': -0.25,
        'description': '2016年1月两次触发熔断机制，A股连续暴跌',
        'sector_impacts': {
            '高端制造': -0.30,
            '顺周期': -0.25,
            '资源': -0.15,
            '防御': -0.10,
        },
        'duration_days': 10,
        'recovery_months': 6,
    },
    {
        'name': '2018贸易战',
        'market_drop': -0.32,
        'description': '2018年中美贸易摩擦升级，沪深300下跌约32%',
        'sector_impacts': {
            '高端制造': -0.35,
            '顺周期': -0.30,
            '资源': -0.20,
            '防御': -0.10,
        },
        'duration_days': 180,
        'recovery_months': 12,
    },
    {
        'name': '2020疫情闪崩',
        'market_drop': -0.16,
        'description': '2020年新冠疫情全球爆发，A股春节后开盘暴跌',
        'sector_impacts': {
            '高端制造': -0.18,
            '顺周期': -0.20,
            '资源': -0.12,
            '防御': -0.08,
        },
        'duration_days': 5,
        'recovery_months': 3,
    },
    {
        'name': '2024国庆后暴跌',
        'market_drop': -0.20,
        'description': '2024年国庆后市场情绪急转，主要指数大幅回调',
        'sector_impacts': {
            '高端制造': -0.22,
            '顺周期': -0.18,
            '资源': -0.10,
            '防御': -0.08,
        },
        'duration_days': 15,
        'recovery_months': 4,
    },
    {
        'name': '黑天鹅',
        'market_drop': -0.35,
        'description': '假设性极端黑天鹅事件，流动性枯竭，所有资产同跌',
        'sector_impacts': {
            '高端制造': -0.40,
            '顺周期': -0.35,
            '资源': -0.25,
            '防御': -0.15,
        },
        'duration_days': 30,
        'recovery_months': 18,
    },
]

# ── 核心计算 ──────────────────────────────────────────────────────────────

def compute_stress_loss(
    weights: np.ndarray,
    sector_returns: Dict[str, float],
) -> float:
    """计算组合在压力情景下的损失比例。

    Args:
        weights: shape (n_assets,) 当前权重
        sector_returns: {板块: 收益率} 压力情景下各板块收益率

    Returns:
        组合总收益率 (负数表示亏损)
    """
    if len(weights) == 0:
        return 0.0
    # 简化: 假设每个资产属于一个板块，收益由板块决定
    # 实际应传入 asset->sector 映射，此处先用均匀近似
    sectors = list(sector_returns.keys())
    n = len(weights)
    per_sector = n / max(len(sectors), 1)
    total = 0.0
    for i, sector in enumerate(sectors):
        start = int(i * per_sector)
        end = int((i + 1) * per_sector) if i < len(sectors) - 1 else n
        if start >= n:
            break
        sector_weight = weights[start:end].sum()
        total += sector_weight * sector_returns[sector]
    return float(total)


def compute_sector_losses(
    sector_returns: Dict[str, float],
    weights: np.ndarray,
    sectors: Dict[str, str],
) -> Dict[str, float]:
    """计算各板块压力损失。

    Args:
        sector_returns: 板块收益率
        weights: 资产权重
        sectors: {资产名: 板块名}

    Returns:
        {板块: 板块总收益率}
    """
    sector_total: Dict[str, float] = {}
    sector_weight: Dict[str, float] = {}
    for name, sector in sectors.items():
        sector_total[sector] = sector_total.get(sector, 0.0)
        sector_weight[sector] = sector_weight.get(sector, 0.0)

    # weights 按 names 顺序对应
    names = list(sectors.keys())
    for i, name in enumerate(names):
        if i >= len(weights):
            break
        sector = sectors[name]
        sector_weight[sector] += weights[i]

    for sector in sector_returns:
        ret = sector_returns.get(sector, 0.0)
        sector_total[sector] = ret
    return sector_total


def should_reduce_position(
    daily_return: float,
    max_dd: float,
    threshold: float = HARD_STOP_MAX_DRAWDOWN,
) -> bool:
    """判断是否触发硬止损减仓。

    当回撤超过 threshold 时，自动清仓至 50% 仓位。

    Args:
        daily_return: 当日组合收益率
        max_dd: 当前最大回撤 (负数)
        threshold: 回撤阈值 (默认 -20%)

    Returns:
        是否应减仓
    """
    return bool(max_dd <= threshold)


# ── 蒙特卡洛压力模拟 ──────────────────────────────────────────────────────

def monte_carlo_stress(
    weights: np.ndarray,
    cov_matrix: np.ndarray,
    n_simulations: int = 1000,
    n_days: int = 63,
    shock_pct: float = -0.20,
    z_shock: float = -3.0,
) -> np.ndarray:
    """蒙特卡洛压力模拟。

    Args:
        weights: shape (n_assets,)
        cov_matrix: shape (n_assets, n_assets) 协方差矩阵
        n_simulations: 模拟路径数
        n_days: 模拟天数
        shock_pct: 初始冲击比例
        z_shock: 初始冲击的 z-score

    Returns:
        shape (n_simulations, n_days) 各路径 cumulative return
    """
    n_assets = len(weights)
    if n_assets == 0:
        return np.zeros((n_simulations, n_days))

    paths = np.zeros((n_simulations, n_days))
    rng = np.random.default_rng(seed=42)

    for sim in range(n_simulations):
        # 初始冲击
        shock = rng.normal(loc=shock_pct, scale=abs(shock_pct) * 0.5)
        daily_rets = rng.multivariate_normal(
            mean=np.full(n_assets, shock / n_days),
            cov=cov_matrix,
            size=n_days,
        )
        port_rets = daily_rets @ weights
        paths[sim] = np.cumsum(port_rets)

    return paths


def monte_carlo_var(
    final_returns: np.ndarray,
    confidence: float = 0.95,
) -> float:
    """蒙特卡洛 VaR。

    Args:
        final_returns: shape (n_simulations,) 最终收益率
        confidence: 置信水平

    Returns:
        VaR (负数)
    """
    if len(final_returns) == 0:
        return 0.0
    alpha = 1.0 - confidence
    return float(np.percentile(final_returns, alpha * 100))


def expected_shortfall(
    returns: np.ndarray,
    confidence: float = 0.95,
) -> float:
    """Expected Shortfall (CVaR) — 历史模拟法。

    Args:
        returns: 收益率序列
        confidence: 置信水平

    Returns:
        ES (负数，且 <= VaR)
    """
    if len(returns) == 0:
        return 0.0
    alpha = 1.0 - confidence
    var = np.percentile(returns, alpha * 100)
    tail = returns[returns <= var]
    if len(tail) == 0:
        return float(var)
    return float(np.mean(tail))


# ── 报告生成 ──────────────────────────────────────────────────────────────

def generate_stress_report(
    returns: np.ndarray,
    weights: np.ndarray,
    names: Optional[List[str]] = None,
    sectors: Optional[Dict[str, str]] = None,
) -> str:
    """生成压力测试日报。

    Args:
        returns: 历史收益率 (n_days, n_assets)
        weights: 当前权重
        names: 资产名称
        sectors: {名称: 板块}

    Returns:
        Markdown 报告
    """
    if names is None:
        names = [f'Asset_{i}' for i in range(len(weights))]
    if sectors is None:
        sectors = {name: '未分类' for name in names}

    # 年化波动率
    vols = np.std(returns, axis=0) * np.sqrt(252)
    cov = np.cov(returns, rowvar=False)  # 每列是一个资产，返回(n_assets, n_assets)
    port_vol = float(np.sqrt(weights @ cov @ weights) * np.sqrt(252))

    lines: List[str] = []
    lines.append('## 极端压力测试日报')
    lines.append('')
    lines.append(f'- 组合年化波动率: **{port_vol:.2%}**')
    lines.append(f'- 尾部保护阈值: **{TAIL_HEDGE_THRESHOLD:.0%}**')
    lines.append('')

    # 历史情景
    lines.append('### 历史极端情景')
    lines.append('')
    lines.append('| 情景 | 市场跌幅 | 预估组合回撤 | 当前保护能否守住15% |')
    lines.append('|------|---------|-------------|-------------------|')

    for scenario in HISTORICAL_SCENARIOS:
        sector_rets = scenario.get('sector_impacts', {})
        loss = compute_stress_loss(weights, sector_rets)
        can_hold = '是' if abs(loss) <= 0.15 else '否'
        lines.append(
            f"| {scenario['name']} | {scenario['market_drop']:.1%} | "
            f"{loss:.1%} | {can_hold} |"
        )
    lines.append('')

    # 蒙特卡洛
    try:
        mc_paths = monte_carlo_stress(
            weights,
            cov_matrix=cov,
            n_simulations=1000,
            n_days=63,
            shock_pct=-0.20,
        )
        final_rets = mc_paths[:, -1]
        mc_var = monte_carlo_var(final_rets, confidence=0.95)
        mc_es = expected_shortfall(final_rets, confidence=0.95)
        worst = float(np.min(final_rets))
        best = float(np.max(final_rets))

        lines.append('### 蒙特卡洛压力模拟 (1000路径, 63交易日)')
        lines.append('')
        lines.append(f'- MC VaR@95%: **{mc_var:.2%}**')
        lines.append(f'- MC ES@95%: **{mc_es:.2%}**')
        lines.append(f'- 最坏路径: **{worst:.2%}**')
        lines.append(f'- 最好路径: **{best:.2%}**')
        lines.append('')
    except Exception as exc:
        lines.append(f'*蒙特卡洛模拟失败: {exc}*')
        lines.append('')

    # 硬止损检查
    port_rets = returns @ weights if len(returns) > 0 else np.array([0])
    cum_rets = np.cumprod(1 + port_rets)
    running_max = np.maximum.accumulate(cum_rets)
    drawdowns = (cum_rets - running_max) / running_max
    max_dd = float(np.min(drawdowns))
    
    trigger = should_reduce_position(-0.20, max_dd=max_dd)
    lines.append('### 硬止损检查')
    lines.append('')
    lines.append(f'- 回撤阈值: **{HARD_STOP_MAX_DRAWDOWN:.1%}**')
    lines.append(f'- 当前回撤: **{max_dd:.1%}**')
    lines.append(f'- 应减仓至50%: **{"是" if trigger else "否"}**')
    lines.append('')

    return '\n'.join(lines)
