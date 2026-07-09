# -*- coding: utf-8 -*-
"""
组合优化 v3 — 激进优化版
=========================
基于 v2, 实施用户提出的 4 项进一步优化方向:

1. 科技股上限 25% → 30%   (提升科技成长权重上限)
2. 棉花期货杠杆 60% → 70% (棉花期货权重上限 25% → 35%)
3. 股票期权保护 5.66% → 0% (完全取消股票期权保护)
4. 引入更高收益资产: 半导体ETF (年化15%/波动30%) + 新能源ETF (年化14%/波动28%)

资产结构 (12 类, 较 v2 增加 2 类):
    0: 核心宽基ETF       1: 科技成长个股    2: 高端制造/基建    3: 防御/红利
    4: 商品/避险         5: 现金缓冲_股票   6: 棉花期货         7: 棉花期权保护
    8: 股票期权保护(=0)  9: 现金缓冲_对冲   10: 半导体ETF (新)  11: 新能源ETF (新)

股票组合 = 0-5 + 10-11 (8类), 期权对冲 = 6-9 (4类)
"""
from __future__ import annotations

import sys
import json
import gc
import logging
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass

BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from backtest.metrics import PerformanceMetrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("portfolio_optimizer_v3")

# ============================================================
# 资产参数 (12 类)
# ============================================================
ASSET_NAMES = [
    "核心宽基ETF", "科技成长个股", "高端制造/基建", "防御/红利",
    "商品/避险", "现金缓冲_股票",
    "棉花期货", "棉花期权保护", "股票期权保护", "现金缓冲_对冲",
    "半导体ETF", "新能源ETF",  # ← v3 新增
]

# 年化收益 (高收益新资产: 半导体 15%, 新能源 14%)
ANN_RETURNS = np.array([
    0.07, 0.12, 0.10, 0.06, 0.05, 0.02,         # 股票 0-5
    0.15, -0.05, -0.03, 0.02,                    # 对冲 6-9
    0.15, 0.14,                                  # 新增 10-11
])

# 年化波动
ANN_VOLS = np.array([
    0.16, 0.25, 0.22, 0.12, 0.15, 0.005,
    0.30, 0.05, 0.05, 0.005,
    0.30, 0.28,                                  # 半导体 / 新能源 ETF
])

# 12×12 相关矩阵
# 半导体ETF 与科技成长正相关 0.85, 新能源ETF 与科技成长 0.75
# 半导体 ↔ 新能源 相关 0.80
# 期权类与新增ETF负相关 (期权保护作用)
CORR_MATRIX = np.array([
    #  核宽   科技   高端   防御   商品   现股   棉期   棉权   股权   现对   半导   新能
    [1.00, 0.70, 0.65, 0.60, 0.20, 0.00, 0.10, -0.20, -0.30, 0.00, 0.75, 0.65],  # 核心宽基
    [0.70, 1.00, 0.75, 0.40, 0.15, 0.00, 0.05, -0.25, -0.35, 0.00, 0.85, 0.75],  # 科技成长
    [0.65, 0.75, 1.00, 0.45, 0.30, 0.00, 0.20, -0.15, -0.25, 0.00, 0.70, 0.65],  # 高端制造
    [0.60, 0.40, 0.45, 1.00, 0.10, 0.00, 0.05, -0.10, -0.20, 0.00, 0.35, 0.30],  # 防御红利
    [0.20, 0.15, 0.30, 0.10, 1.00, 0.00, 0.40, -0.10, -0.05, 0.00, 0.10, 0.15],  # 商品避险
    [0.00, 0.00, 0.00, 0.00, 0.00, 1.00, 0.00, 0.00, 0.00, 0.30, 0.00, 0.00],  # 现金股票
    [0.10, 0.05, 0.20, 0.05, 0.40, 0.00, 1.00, 0.50, 0.20, 0.00, 0.05, 0.10],  # 棉花期货
    [-0.20, -0.25, -0.15, -0.10, -0.10, 0.00, 0.50, 1.00, 0.60, 0.00, -0.20, -0.15],  # 棉花期权
    [-0.30, -0.35, -0.25, -0.20, -0.05, 0.00, 0.20, 0.60, 1.00, 0.00, -0.30, -0.25],  # 股票期权
    [0.00, 0.00, 0.00, 0.00, 0.00, 0.30, 0.00, 0.00, 0.00, 1.00, 0.00, 0.00],  # 现金对冲
    [0.75, 0.85, 0.70, 0.35, 0.10, 0.00, 0.05, -0.20, -0.30, 0.00, 1.00, 0.80],  # 半导体ETF
    [0.65, 0.75, 0.65, 0.30, 0.15, 0.00, 0.10, -0.15, -0.25, 0.00, 0.80, 1.00],  # 新能源ETF
])

# 协方差矩阵
COV_MATRIX = np.outer(ANN_VOLS, ANN_VOLS) * CORR_MATRIX

# v2 优化后的权重作为 v3 基线 (10 类, v3 中将股票期权保护置0)
ORIGINAL_WEIGHTS_V2 = np.array([
    0.1032, 0.2187, 0.120, 0.090, 0.030, 0.048,
    0.2408, 0.035, 0.0566, 0.065,
])
# v3 基线: 在 v2 基础上, 股票期权=0, 新增半导体/新能源 ETF=0.05/0.05, 其余按比例调整
# 总和: 0.1032+0.2187+0.120+0.090+0.030+0.048 + 0.2408+0.035+0.0+0.065 + 0.05+0.05
#      = 0.6099 + 0.3408 + 0.10 = 1.0507 (溢出 5%)
# 需重新归一化: 0.1032/1.0507=0.0982, ..., 总和=1.0
_v3_baseline_raw = np.array([
    0.1032, 0.2187, 0.120, 0.090, 0.030, 0.048,
    0.2408, 0.035, 0.0000, 0.065,
    0.0500, 0.0500,
])
ORIGINAL_WEIGHTS = _v3_baseline_raw / _v3_baseline_raw.sum()

# v3 权重约束 (lower, upper)
# 1. 科技成长上限 25% → 30%
# 2. 棉花期货上限 25% → 35% (70% 杠杆)
# 3. 股票期权保护 = 0
# 4. 新增 半导体ETF (2%-10%), 新能源ETF (2%-10%)
WEIGHT_BOUNDS = [
    (0.08, 0.20),  # 0: 核心宽基ETF (下调下限, 给新资产让位)
    (0.15, 0.30),  # 1: 科技成长: 上限 25% → 30%  ★ v3 变更
    (0.08, 0.20),  # 2: 高端制造 (下调下限)
    (0.04, 0.15),  # 3: 防御红利 (下调下限)
    (0.02, 0.08),  # 4: 商品避险
    (0.02, 0.08),  # 5: 现金缓冲_股票
    (0.20, 0.35),  # 6: 棉花期货: 上限 25% → 35% (70% 杠杆)  ★ v3 变更
    (0.02, 0.05),  # 7: 棉花期权保护
    (0.00, 0.00),  # 8: 股票期权保护: 0% (完全取消)  ★ v3 变更
    (0.03, 0.08),  # 9: 现金缓冲_对冲
    (0.02, 0.10),  # 10: 半导体ETF (新增)  ★ v3 新增
    (0.02, 0.10),  # 11: 新能源ETF (新增)  ★ v3 新增
]

# 股票组合 = 0-5 + 10-11 (8类, 含新增2类)
# 期权对冲 = 6-9 (4类)
STOCK_INDICES = [0, 1, 2, 3, 4, 5, 10, 11]
HEDGE_INDICES = [6, 7, 8, 9]
STOCK_MIN, STOCK_MAX = 0.55, 0.75   # 股票组合下限提升 (新增高收益资产)
HEDGE_MIN, HEDGE_MAX = 0.25, 0.45   # 对冲组合下限下调 (取消股票期权)

RF = 0.02  # 无风险利率


@dataclass
class OptResult:
    weights: np.ndarray
    objective: float
    annual_return: float
    annual_vol: float
    max_drawdown: float
    sharpe: float
    sortino: float
    calmar: float
    prob_annual_gt_8pct: float = 0.0
    prob_dd_lt_15pct: float = 0.0


def evaluate_analytical(weights: np.ndarray, l2_lambda: float = 0.1) -> OptResult:
    """解析公式评估组合绩效 (无需蒙特卡洛)"""
    # 组合年化收益
    mu_p = float(np.dot(weights, ANN_RETURNS))

    # 组合年化波动
    var_p = float(weights @ COV_MATRIX @ weights)
    sigma_p = float(np.sqrt(max(var_p, 1e-10)))

    # 最大回撤近似: MaxDD ≈ -2.5 × σ_annual, 期权保护后衰减
    # 对冲比例 = 棉花期权 + 股票期权 (本应为0) + 棉花期货×0.3 (期货有部分对冲效果)
    hedge_ratio = weights[7] + weights[8] + 0.3 * weights[6]
    dd_factor = 2.5 * (1 - 0.3 * hedge_ratio)
    max_dd = -dd_factor * sigma_p

    # Sharpe
    sharpe = (mu_p - RF) / sigma_p if sigma_p > 0 else 0.0

    # Sortino (经验: Sortino ≈ Sharpe × 1.3)
    sortino = sharpe * 1.3

    # Calmar
    calmar = mu_p / abs(max_dd) if abs(max_dd) > 0 else 0.0

    # 概率估算 (正态分布假设)
    z_annual = (mu_p - 0.08) / sigma_p if sigma_p > 0 else 0
    from scipy.stats import norm
    prob_annual_8 = float(norm.cdf(z_annual))

    dd_mean = -dd_factor * sigma_p
    dd_std = max(dd_factor * sigma_p / 2, 0.01)
    prob_dd_15 = float(norm.cdf((-0.15 - dd_mean) / dd_std))

    # 目标函数: J = Sortino + 0.5*Calmar - λ*||w||²
    l2_penalty = l2_lambda * float(np.sum(weights ** 2))
    objective = sortino + 0.5 * calmar - l2_penalty

    # 不满足目标的惩罚
    if prob_annual_8 < 0.68:
        objective -= 0.5 * (0.68 - prob_annual_8)
    if prob_dd_15 < 0.82:
        objective -= 0.5 * (0.82 - prob_dd_15)

    return OptResult(
        weights=weights,
        objective=objective,
        annual_return=mu_p,
        annual_vol=sigma_p,
        max_drawdown=max_dd,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        prob_annual_gt_8pct=prob_annual_8,
        prob_dd_lt_15pct=prob_dd_15,
    )


def sample_weights(rng: np.random.Generator) -> np.ndarray:
    """在约束条件下随机采样权重"""
    for _ in range(200):
        w = np.array([rng.uniform(lo, hi) for lo, hi in WEIGHT_BOUNDS])
        # 强制股票期权保护 = 0
        w[8] = 0.0
        w = w / w.sum()

        stock_sum = w[STOCK_INDICES].sum()
        hedge_sum = w[HEDGE_INDICES].sum()

        if STOCK_MIN <= stock_sum <= STOCK_MAX and \
           HEDGE_MIN <= hedge_sum <= HEDGE_MAX:
            valid = all(lo - 1e-3 <= wi <= hi + 1e-3
                       for wi, (lo, hi) in zip(w, WEIGHT_BOUNDS))
            if valid:
                return w
    return ORIGINAL_WEIGHTS.copy()


def optimize_portfolio(n_trials: int = 20000, seed: int = 42) -> OptResult:
    """随机搜索优化 (解析公式, 快速)"""
    rng = np.random.default_rng(seed)

    baseline = evaluate_analytical(ORIGINAL_WEIGHTS)
    logger.info(f"v3 基线: J={baseline.objective:.4f}, 年化={baseline.annual_return:.2%}, "
                f"σ={baseline.annual_vol:.2%}, 回撤={baseline.max_drawdown:.2%}, "
                f"P(>8%)={baseline.prob_annual_gt_8pct:.1%}, "
                f"P(DD<15%)={baseline.prob_dd_lt_15pct:.1%}")

    best_result = baseline
    logger.info(f"开始随机搜索: {n_trials} 次试验 (解析公式)")

    for trial in range(n_trials):
        w = sample_weights(rng)
        result = evaluate_analytical(w)

        if result.objective > best_result.objective:
            best_result = result
            if (trial + 1) <= 200 or (trial + 1) % 1000 == 0:
                logger.info(
                    f"[Trial {trial+1}/{n_trials}] J={result.objective:.4f}, "
                    f"年化={result.annual_return:.2%}, σ={result.annual_vol:.2%}, "
                    f"回撤={result.max_drawdown:.2%}, "
                    f"P(>8%)={result.prob_annual_gt_8pct:.1%}, "
                    f"P(DD<15%)={result.prob_dd_lt_15pct:.1%}"
                )

    return best_result


def verify_with_montecarlo(weights: np.ndarray, n_paths: int = 3000,
                           seed: int = 42) -> dict:
    """用蒙特卡洛验证最优解 (分批避免内存问题)"""
    rng = np.random.default_rng(seed)
    n_days = 252
    n_assets = len(ASSET_NAMES)

    mu_daily = ANN_RETURNS / 252
    sigma_daily = ANN_VOLS / np.sqrt(252)

    corr = CORR_MATRIX + np.eye(n_assets) * 1e-6
    L = np.linalg.cholesky(corr)

    annual_returns = []
    max_drawdowns = []
    sharpes = []
    sortinos = []
    calmars = []

    batch_size = 500
    n_batches = n_paths // batch_size

    for batch in range(n_batches):
        X = rng.standard_normal(size=(n_assets, n_days, batch_size))
        Z = np.einsum('ij,jkl->ikl', L, X)
        asset_returns = mu_daily[:, None, None] + sigma_daily[:, None, None] * Z
        portfolio_returns = np.einsum('i,ijk->jk', weights, asset_returns)

        # 期权尾部保护: 仅棉花期权部分提供保护 (股票期权=0, 无尾部保护)
        # 棉花期权保护: 当跌幅 > 3% 时减少 30% 损失 (衰减, 因棉花期权规模小)
        cotton_opt_ratio = weights[7]
        protection_factor = 0.3 * cotton_opt_ratio / 0.05  # 归一化到5%基准
        protection_factor = min(protection_factor, 0.5)
        mask = portfolio_returns < -0.03
        portfolio_returns = np.where(mask,
                                      portfolio_returns * (1 - protection_factor),
                                      portfolio_returns)

        for i in range(batch_size):
            r = pd.Series(portfolio_returns[:, i])
            pm = PerformanceMetrics(r, rf=RF)
            annual_returns.append(pm.annual_return())
            max_drawdowns.append(pm.max_drawdown())
            sharpes.append(pm.sharpe_ratio())
            sortinos.append(pm.sortino_ratio())
            calmars.append(pm.calmar_ratio())

        del X, Z, asset_returns, portfolio_returns
        gc.collect()

    return {
        "annual_return_mean": float(np.mean(annual_returns)),
        "annual_return_p50": float(np.median(annual_returns)),
        "annual_return_p10": float(np.percentile(annual_returns, 10)),
        "annual_return_p90": float(np.percentile(annual_returns, 90)),
        "max_drawdown_mean": float(np.mean(max_drawdowns)),
        "max_drawdown_p95": float(np.percentile(max_drawdowns, 5)),  # 5%最差情况
        "sharpe_mean": float(np.mean(sharpes)),
        "sortino_mean": float(np.mean(sortinos)),
        "calmar_mean": float(np.mean(calmars)),
        "prob_annual_gt_8pct": float(np.mean(np.array(annual_returns) > 0.08)),
        "prob_annual_gt_10pct": float(np.mean(np.array(annual_returns) > 0.10)),
        "prob_dd_lt_15pct": float(np.mean(np.array(max_drawdowns) > -0.15)),
        "prob_dd_lt_20pct": float(np.mean(np.array(max_drawdowns) > -0.20)),
        "n_paths": n_paths,
    }


def generate_report(baseline: OptResult, optimized: OptResult,
                    mc_verified: dict, output_path: Path):
    """生成优化对比报告 v3"""
    lines = [
        "# 组合优化报告 v3 — 激进优化版",
        "",
        f"**生成时间**: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "**版本**: v3 (相对 v2 进一步激进优化)",
        "",
        "## 优化方向 (相对 v2)",
        "",
        "1. **科技股上限**: 25% → 30% (提升上限 +5pp)",
        "2. **棉花期货杠杆**: 60% → 70% (权重上限 25% → 35%)",
        "3. **股票期权保护**: 完全取消 (5.66% → 0%)",
        "4. **新增高收益资产**: 半导体ETF (年化15%/波动30%) + 新能源ETF (年化14%/波动28%)",
        "",
        f"**优化方法**: 随机搜索 (20,000 次试验, 解析公式) + 蒙特卡洛验证 (3,000 路径)",
        f"**目标函数**: J = Sortino + 0.5 × Calmar - λ × ||w||²  (λ=0.1)",
        f"**约束**: 股票 55%-75% / 对冲 25%-45% / 各类别权重边界",
        "",
        "## 1. 优化前后对比 (解析公式)",
        "",
        "| 指标 | v3 基线 | v3 优化 | 改善 |",
        "|------|---------|---------|------|",
        f"| 年化收益率 | {baseline.annual_return:.2%} | {optimized.annual_return:.2%} | "
        f"{optimized.annual_return - baseline.annual_return:+.2%} |",
        f"| 年化波动率 | {baseline.annual_vol:.2%} | {optimized.annual_vol:.2%} | "
        f"{optimized.annual_vol - baseline.annual_vol:+.2%} |",
        f"| 最大回撤 (估算) | {baseline.max_drawdown:.2%} | {optimized.max_drawdown:.2%} | "
        f"{optimized.max_drawdown - baseline.max_drawdown:+.2%} |",
        f"| Sharpe Ratio | {baseline.sharpe:.3f} | {optimized.sharpe:.3f} | "
        f"{optimized.sharpe - baseline.sharpe:+.3f} |",
        f"| Sortino Ratio | {baseline.sortino:.3f} | {optimized.sortino:.3f} | "
        f"{optimized.sortino - baseline.sortino:+.3f} |",
        f"| Calmar Ratio | {baseline.calmar:.3f} | {optimized.calmar:.3f} | "
        f"{optimized.calmar - baseline.calmar:+.3f} |",
        f"| P(年化>8%) | {baseline.prob_annual_gt_8pct:.1%} | "
        f"{optimized.prob_annual_gt_8pct:.1%} | "
        f"{optimized.prob_annual_gt_8pct - baseline.prob_annual_gt_8pct:+.1%} |",
        f"| P(回撤<15%) | {baseline.prob_dd_lt_15pct:.1%} | "
        f"{optimized.prob_dd_lt_15pct:.1%} | "
        f"{optimized.prob_dd_lt_15pct - baseline.prob_dd_lt_15pct:+.1%} |",
        "",
        "## 2. 蒙特卡洛验证 (3,000 路径)",
        "",
        "| 指标 | 解析公式 | 蒙特卡洛 |",
        "|------|---------|---------|",
        f"| 年化收益率 (均值) | {optimized.annual_return:.2%} | "
        f"{mc_verified['annual_return_mean']:.2%} |",
        f"| 年化收益率 (中位数) | - | "
        f"{mc_verified['annual_return_p50']:.2%} |",
        f"| 年化收益率 (P10) | - | "
        f"{mc_verified['annual_return_p10']:.2%} |",
        f"| 年化收益率 (P90) | - | "
        f"{mc_verified['annual_return_p90']:.2%} |",
        f"| 最大回撤 (均值) | {optimized.max_drawdown:.2%} | "
        f"{mc_verified['max_drawdown_mean']:.2%} |",
        f"| 最大回撤 (P95 最差5%) | - | "
        f"{mc_verified['max_drawdown_p95']:.2%} |",
        f"| Sharpe | {optimized.sharpe:.3f} | "
        f"{mc_verified['sharpe_mean']:.3f} |",
        f"| Sortino | {optimized.sortino:.3f} | "
        f"{mc_verified['sortino_mean']:.3f} |",
        f"| Calmar | {optimized.calmar:.3f} | "
        f"{mc_verified['calmar_mean']:.3f} |",
        f"| P(年化>8%) | {optimized.prob_annual_gt_8pct:.1%} | "
        f"{mc_verified['prob_annual_gt_8pct']:.1%} |",
        f"| P(年化>10%) | - | "
        f"{mc_verified['prob_annual_gt_10pct']:.1%} |",
        f"| P(回撤<15%) | {optimized.prob_dd_lt_15pct:.1%} | "
        f"{mc_verified['prob_dd_lt_15pct']:.1%} |",
        f"| P(回撤<20%) | - | "
        f"{mc_verified['prob_dd_lt_20pct']:.1%} |",
        "",
        "## 3. PDF1 目标达成情况",
        "",
        "| 目标 | v3 基线 | v3 优化 | 蒙特卡洛验证 | 达成 |",
        "|------|---------|---------|------------|------|",
        f"| 年化>8% 概率 ≥ 68% | {baseline.prob_annual_gt_8pct:.1%} | "
        f"{optimized.prob_annual_gt_8pct:.1%} | "
        f"{mc_verified['prob_annual_gt_8pct']:.1%} | "
        f"{'✓' if mc_verified['prob_annual_gt_8pct'] >= 0.68 else '✗'} |",
        f"| 回撤<15% 概率 ≥ 82% | {baseline.prob_dd_lt_15pct:.1%} | "
        f"{optimized.prob_dd_lt_15pct:.1%} | "
        f"{mc_verified['prob_dd_lt_15pct']:.1%} | "
        f"{'✓' if mc_verified['prob_dd_lt_15pct'] >= 0.82 else '✗'} |",
        "",
        "## 4. 权重对比 (12 类资产)",
        "",
        "| 资产类别 | v3 基线 | v3 优化 | 变动 | 500万建议金额 |",
        "|---------|---------|---------|------|------------|",
    ]

    for i, name in enumerate(ASSET_NAMES):
        old_w = ORIGINAL_WEIGHTS[i]
        new_w = optimized.weights[i]
        delta = new_w - old_w
        amount = new_w * 5_000_000
        flag = ""
        if name == "科技成长个股":
            flag = " (上限30%)"
        elif name == "棉花期货":
            flag = " (70%杠杆)"
        elif name == "股票期权保护":
            flag = " (取消)"
        elif name in ("半导体ETF", "新能源ETF"):
            flag = " (v3新增)"
        lines.append(
            f"| {name}{flag} | {old_w:.2%} | {new_w:.2%} | {delta:+.2%} | {amount:,.0f} |"
        )

    stock_old = ORIGINAL_WEIGHTS[STOCK_INDICES].sum()
    stock_new = optimized.weights[STOCK_INDICES].sum()
    hedge_old = ORIGINAL_WEIGHTS[HEDGE_INDICES].sum()
    hedge_new = optimized.weights[HEDGE_INDICES].sum()
    lines.extend([
        "",
        "## 5. 群组汇总",
        "",
        "| 群组 | v3 基线 | v3 优化 | 变动 | 500万建议金额 |",
        "|------|---------|---------|------|------------|",
        f"| 股票组合 (8类, 含半导体/新能源ETF) | {stock_old:.2%} | {stock_new:.2%} | "
        f"{stock_new - stock_old:+.2%} | {stock_new*5_000_000:,.0f} |",
        f"| 期权对冲 (4类, 股票期权=0) | {hedge_old:.2%} | {hedge_new:.2%} | "
        f"{hedge_new - hedge_old:+.2%} | {hedge_new*5_000_000:,.0f} |",
        "",
        "## 6. v3 vs v2 对比",
        "",
        "| 指标 | v2 优化 | v3 优化 | 改善 |",
        "|------|---------|---------|------|",
        f"| 年化收益率 | 8.93% | {optimized.annual_return:.2%} | "
        f"{optimized.annual_return - 0.0893:+.2%} |",
        f"| P(年化>8%) | 53.0% | {mc_verified['prob_annual_gt_8pct']:.1%} | "
        f"{mc_verified['prob_annual_gt_8pct'] - 0.530:+.1%} |",
        f"| P(回撤<15%) | 80.4% | {mc_verified['prob_dd_lt_15pct']:.1%} | "
        f"{mc_verified['prob_dd_lt_15pct'] - 0.804:+.1%} |",
        "",
        "## 7. 优化结论与建议",
        "",
    ])

    if mc_verified['prob_annual_gt_8pct'] >= 0.68:
        lines.append("- ✓ **年化收益目标达成**: 蒙特卡洛验证 P(年化>8%) ≥ 68%")
    else:
        lines.append(f"- ⚠ **年化收益仍不达标**: 蒙特卡洛 P(年化>8%)="
                     f"{mc_verified['prob_annual_gt_8pct']:.1%}, 目标 68%")

    if mc_verified['prob_dd_lt_15pct'] >= 0.82:
        lines.append("- ✓ **回撤控制目标达成**: 蒙特卡洛验证 P(回撤<15%) ≥ 82%")
    else:
        lines.append(f"- ⚠ **回撤控制不达标**: 蒙特卡洛 P(回撤<15%)="
                     f"{mc_verified['prob_dd_lt_15pct']:.1%}")

    lines.extend([
        "",
        "## 8. 风险提示",
        "",
        "- **取消股票期权保护**: 极端行情下尾部风险暴露增加, 建议关注 VIX>60 时手动对冲",
        "- **新增高波动资产**: 半导体/新能源ETF波动率 28%-30%, 短期回撤可能加大",
        "- **70% 杠杆棉花期货**: 杠杆放大, 强烈建议严格执行止损纪律",
        "- **解析公式假设**: 正态分布, 实际市场存在肥尾效应",
        "- **蒙特卡洛尾部保护**: 仅考虑棉花期权部分, 股票期权已取消",
        "- 建议每周重新优化一次, 适应市场环境变化",
        "",
    ])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info(f"v3 优化报告已生成: {output_path}")


def main():
    logger.info("=" * 60)
    logger.info("v3 激进优化 — 4 项优化方向")
    logger.info("=" * 60)
    logger.info("1. 科技股上限: 25% → 30%")
    logger.info("2. 棉花期货杠杆: 60% → 70% (权重上限 35%)")
    logger.info("3. 股票期权保护: 完全取消 (=0%)")
    logger.info("4. 新增: 半导体ETF + 新能源ETF")
    logger.info("")

    logger.info("=" * 60)
    logger.info("Phase 1: 评估 v3 基线权重 (解析公式)")
    logger.info("=" * 60)
    baseline = evaluate_analytical(ORIGINAL_WEIGHTS)
    logger.info(f"v3 基线权重:")
    for i, name in enumerate(ASSET_NAMES):
        logger.info(f"  {name:18s}: {ORIGINAL_WEIGHTS[i]:.2%}")
    logger.info(f"  股票组合 (8类): {ORIGINAL_WEIGHTS[STOCK_INDICES].sum():.2%}")
    logger.info(f"  期权对冲 (4类): {ORIGINAL_WEIGHTS[HEDGE_INDICES].sum():.2%}")
    logger.info("")
    logger.info(f"v3 基线结果:")
    logger.info(f"  年化收益率: {baseline.annual_return:.2%}")
    logger.info(f"  年化波动率: {baseline.annual_vol:.2%}")
    logger.info(f"  最大回撤:   {baseline.max_drawdown:.2%}")
    logger.info(f"  Sharpe:     {baseline.sharpe:.3f}")
    logger.info(f"  Sortino:    {baseline.sortino:.3f}")
    logger.info(f"  Calmar:     {baseline.calmar:.3f}")
    logger.info(f"  P(年化>8%): {baseline.prob_annual_gt_8pct:.1%}")
    logger.info(f"  P(回撤<15%): {baseline.prob_dd_lt_15pct:.1%}")
    logger.info(f"  目标函数 J: {baseline.objective:.4f}")

    logger.info("")
    logger.info("=" * 60)
    logger.info("Phase 2: 随机搜索优化 (20,000 次试验, 解析公式)")
    logger.info("=" * 60)
    optimized = optimize_portfolio(n_trials=20000, seed=42)

    logger.info("")
    logger.info("=" * 60)
    logger.info("Phase 3: 蒙特卡洛验证 (3,000 路径)")
    logger.info("=" * 60)
    mc_verified = verify_with_montecarlo(optimized.weights, n_paths=3000, seed=42)

    logger.info(f"蒙特卡洛验证结果:")
    logger.info(f"  年化收益率: 均值={mc_verified['annual_return_mean']:.2%}, "
                f"P50={mc_verified['annual_return_p50']:.2%}, "
                f"P10={mc_verified['annual_return_p10']:.2%}, "
                f"P90={mc_verified['annual_return_p90']:.2%}")
    logger.info(f"  最大回撤:   均值={mc_verified['max_drawdown_mean']:.2%}, "
                f"P95={mc_verified['max_drawdown_p95']:.2%}")
    logger.info(f"  Sharpe:     {mc_verified['sharpe_mean']:.3f}")
    logger.info(f"  Sortino:    {mc_verified['sortino_mean']:.3f}")
    logger.info(f"  Calmar:     {mc_verified['calmar_mean']:.3f}")
    logger.info(f"  P(年化>8%):  {mc_verified['prob_annual_gt_8pct']:.1%}")
    logger.info(f"  P(年化>10%): {mc_verified['prob_annual_gt_10pct']:.1%}")
    logger.info(f"  P(回撤<15%): {mc_verified['prob_dd_lt_15pct']:.1%}")
    logger.info(f"  P(回撤<20%): {mc_verified['prob_dd_lt_20pct']:.1%}")

    logger.info("")
    logger.info("=" * 60)
    logger.info("Phase 4: v3 优化结果汇总")
    logger.info("=" * 60)
    logger.info(f"v3 优化后权重:")
    for i, name in enumerate(ASSET_NAMES):
        delta = optimized.weights[i] - ORIGINAL_WEIGHTS[i]
        logger.info(f"  {name:18s}: {optimized.weights[i]:.2%} "
                    f"(基线 {ORIGINAL_WEIGHTS[i]:.2%}, 变动 {delta:+.2%})")

    logger.info("")
    logger.info(f"群组汇总:")
    logger.info(f"  股票组合 (8类): {optimized.weights[STOCK_INDICES].sum():.2%} "
                f"(基线 {ORIGINAL_WEIGHTS[STOCK_INDICES].sum():.2%})")
    logger.info(f"  期权对冲 (4类): {optimized.weights[HEDGE_INDICES].sum():.2%} "
                f"(基线 {ORIGINAL_WEIGHTS[HEDGE_INDICES].sum():.2%})")

    # PDF1 目标达成判定
    logger.info("")
    logger.info("=" * 60)
    logger.info("PDF1 目标达成判定")
    logger.info("=" * 60)
    target1_pass = mc_verified['prob_annual_gt_8pct'] >= 0.68
    target2_pass = mc_verified['prob_dd_lt_15pct'] >= 0.82
    logger.info(f"  年化>8% 概率 ≥ 68%: {mc_verified['prob_annual_gt_8pct']:.1%} "
                f"{'✓ PASS' if target1_pass else '✗ FAIL'}")
    logger.info(f"  回撤<15% 概率 ≥ 82%: {mc_verified['prob_dd_lt_15pct']:.1%} "
                f"{'✓ PASS' if target2_pass else '✗ FAIL'}")

    if target1_pass and target2_pass:
        logger.info("  >>> PDF1 双目标达成! v3 激进优化成功")
    elif target1_pass:
        logger.info("  >>> 仅年化收益达标, 回撤控制需进一步调整")
    elif target2_pass:
        logger.info("  >>> 仅回撤控制达标, 年化收益需进一步调整")
    else:
        logger.info("  >>> 双目标均未达成, 建议放宽约束或调整资产参数")

    # 生成报告
    report_path = (
        BASE_DIR.parent / "每日报告归档" / "2026" / "07" / "06"
        / "portfolio_optimization_v3_20260706.md"
    )
    generate_report(baseline, optimized, mc_verified, report_path)

    # 保存 JSON
    json_path = report_path.with_suffix(".json")
    result_data = {
        "version": "v3",
        "optimization_directions": [
            "科技股上限 25% → 30%",
            "棉花期货杠杆 60% → 70% (权重上限 35%)",
            "股票期权保护 5.66% → 0% (完全取消)",
            "新增: 半导体ETF (年化15%/波动30%) + 新能源ETF (年化14%/波动28%)",
        ],
        "baseline": {
            "weights": ORIGINAL_WEIGHTS.tolist(),
            "asset_names": ASSET_NAMES,
            "annual_return": baseline.annual_return,
            "annual_vol": baseline.annual_vol,
            "max_drawdown": baseline.max_drawdown,
            "sharpe": baseline.sharpe,
            "sortino": baseline.sortino,
            "calmar": baseline.calmar,
            "prob_annual_gt_8pct": baseline.prob_annual_gt_8pct,
            "prob_dd_lt_15pct": baseline.prob_dd_lt_15pct,
        },
        "optimized": {
            "weights": optimized.weights.tolist(),
            "annual_return": optimized.annual_return,
            "annual_vol": optimized.annual_vol,
            "max_drawdown": optimized.max_drawdown,
            "sharpe": optimized.sharpe,
            "sortino": optimized.sortino,
            "calmar": optimized.calmar,
            "prob_annual_gt_8pct": optimized.prob_annual_gt_8pct,
            "prob_dd_lt_15pct": optimized.prob_dd_lt_15pct,
            "objective": optimized.objective,
        },
        "montecarlo_verified": mc_verified,
        "pdf1_targets": {
            "annual_gt_8pct_target": 0.68,
            "annual_gt_8pct_actual": mc_verified['prob_annual_gt_8pct'],
            "annual_gt_8pct_pass": target1_pass,
            "dd_lt_15pct_target": 0.82,
            "dd_lt_15pct_actual": mc_verified['prob_dd_lt_15pct'],
            "dd_lt_15pct_pass": target2_pass,
        },
        "optimization_config": {
            "n_trials": 20000,
            "n_paths_verification": 3000,
            "l2_lambda": 0.1,
            "method": "analytical + monte_carlo_verification",
            "weight_bounds": WEIGHT_BOUNDS,
            "stock_indices": STOCK_INDICES,
            "hedge_indices": HEDGE_INDICES,
            "stock_range": [STOCK_MIN, STOCK_MAX],
            "hedge_range": [HEDGE_MIN, HEDGE_MAX],
        },
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result_data, f, indent=2, ensure_ascii=False)
    logger.info(f"v3 优化 JSON: {json_path}")

    return optimized


if __name__ == "__main__":
    main()
