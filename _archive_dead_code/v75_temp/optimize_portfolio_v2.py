# -*- coding: utf-8 -*-
"""
组合优化 v2 — 解析公式 + 蒙特卡洛验证
=====================================
v1 版本因内存错误失败, v2 改用解析公式快速评估, 最后用蒙特卡洛验证最优解。

解析公式:
  - 组合年化收益: μ_p = Σ wᵢ × μᵢ
  - 组合年化波动: σ_p = √(w' × Σ × w)  (Σ = diag(σ) × Corr × diag(σ))
  - 最大回撤近似: MaxDD ≈ -2 × σ_p (经验公式)
  - Sharpe = (μ_p - rf) / σ_p
  - Calmar = μ_p / |MaxDD|
  - Sortino ≈ Sharpe × 1.3 (经验系数)
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
logger = logging.getLogger("portfolio_optimizer_v2")

# ============================================================
# 资产参数
# ============================================================
ASSET_NAMES = [
    "核心宽基ETF", "科技成长个股", "高端制造/基建", "防御/红利",
    "商品/避险", "现金缓冲_股票",
    "棉花期货", "棉花期权保护", "股票期权保护", "现金缓冲_对冲",
]

ANN_RETURNS = np.array([0.07, 0.12, 0.10, 0.06, 0.05, 0.02, 0.15, -0.05, -0.03, 0.02])
ANN_VOLS = np.array([0.16, 0.25, 0.22, 0.12, 0.15, 0.005, 0.30, 0.05, 0.05, 0.005])

CORR_MATRIX = np.array([
    [1.00, 0.70, 0.65, 0.60, 0.20, 0.00, 0.10, -0.20, -0.30, 0.00],
    [0.70, 1.00, 0.75, 0.40, 0.15, 0.00, 0.05, -0.25, -0.35, 0.00],
    [0.65, 0.75, 1.00, 0.45, 0.30, 0.00, 0.20, -0.15, -0.25, 0.00],
    [0.60, 0.40, 0.45, 1.00, 0.10, 0.00, 0.05, -0.10, -0.20, 0.00],
    [0.20, 0.15, 0.30, 0.10, 1.00, 0.00, 0.40, -0.10, -0.05, 0.00],
    [0.00, 0.00, 0.00, 0.00, 0.00, 1.00, 0.00, 0.00, 0.00, 0.30],
    [0.10, 0.05, 0.20, 0.05, 0.40, 0.00, 1.00, 0.50, 0.20, 0.00],
    [-0.20,-0.25,-0.15,-0.10,-0.10, 0.00, 0.50, 1.00, 0.60, 0.00],
    [-0.30,-0.35,-0.25,-0.20,-0.05, 0.00, 0.20, 0.60, 1.00, 0.00],
    [0.00, 0.00, 0.00, 0.00, 0.00, 0.30, 0.00, 0.00, 0.00, 1.00],
])

# 协方差矩阵
COV_MATRIX = np.outer(ANN_VOLS, ANN_VOLS) * CORR_MATRIX

ORIGINAL_WEIGHTS = np.array([
    0.168, 0.120, 0.120, 0.090, 0.030, 0.048,
    0.200, 0.035, 0.100, 0.065,
])

WEIGHT_BOUNDS = [
    (0.10, 0.20), (0.15, 0.25), (0.10, 0.20), (0.05, 0.15),
    (0.02, 0.08), (0.02, 0.08),
    (0.15, 0.25), (0.02, 0.05), (0.05, 0.10), (0.03, 0.08),
]

STOCK_MIN, STOCK_MAX = 0.50, 0.70
HEDGE_MIN, HEDGE_MAX = 0.30, 0.50

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

    # 最大回撤近似 (经验公式: MaxDD ≈ -2.5 × σ_annual, 期权保护后减半)
    # 期权尾部保护: 当跌幅 > 3% 时减少 50% 损失
    hedge_ratio = weights[6:].sum()  # 对冲比例
    dd_factor = 2.5 * (1 - 0.3 * hedge_ratio)  # 对冲越高, 回撤越小
    max_dd = -dd_factor * sigma_p

    # Sharpe
    sharpe = (mu_p - RF) / sigma_p if sigma_p > 0 else 0.0

    # Sortino (经验: Sortino ≈ Sharpe × 1.3, 因下行波动 < 总波动)
    sortino = sharpe * 1.3

    # Calmar
    calmar = mu_p / abs(max_dd) if abs(max_dd) > 0 else 0.0

    # 概率估算 (基于正态分布假设)
    # P(年化 > 8%) = P((μ_p - 8%) / σ_p > Z)
    # 年化收益的标准误 ≈ σ_p / √1 (1年)
    z_annual = (mu_p - 0.08) / sigma_p if sigma_p > 0 else 0
    from scipy.stats import norm
    prob_annual_8 = float(norm.cdf(z_annual))

    # P(回撤 < 15%) = P(MaxDD > -15%)
    # 假设 MaxDD 服从 N(-dd_factor×σ, dd_factor×σ/2)
    dd_mean = -dd_factor * sigma_p
    dd_std = max(dd_factor * sigma_p / 2, 0.01)
    prob_dd_15 = float(norm.cdf((-0.15 - dd_mean) / dd_std))

    # 目标函数: J = Sortino + 0.5*Calmar - λ*||w||²
    l2_penalty = l2_lambda * float(np.sum(weights ** 2))
    objective = sortino + 0.5 * calmar - l2_penalty

    # 惩罚项: 不满足目标时降低目标函数值
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
    for _ in range(100):
        w = np.array([rng.uniform(lo, hi) for lo, hi in WEIGHT_BOUNDS])
        w = w / w.sum()

        stock_sum = w[:6].sum()
        hedge_sum = w[6:].sum()

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
    logger.info(f"基线: J={baseline.objective:.4f}, 年化={baseline.annual_return:.2%}, "
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
    """用蒙特卡洛验证最优解 (小路径数避免内存问题)"""
    rng = np.random.default_rng(seed)
    n_days = 252

    mu_daily = ANN_RETURNS / 252
    sigma_daily = ANN_VOLS / np.sqrt(252)

    corr = CORR_MATRIX + np.eye(len(ASSET_NAMES)) * 1e-6
    L = np.linalg.cholesky(corr)

    annual_returns = []
    max_drawdowns = []
    sharpes = []
    sortinos = []
    calmars = []

    # 分批处理避免内存问题 (每批 500 路径)
    batch_size = 500
    n_batches = n_paths // batch_size

    for batch in range(n_batches):
        X = rng.standard_normal(size=(len(ASSET_NAMES), n_days, batch_size))
        Z = np.einsum('ij,jkl->ikl', L, X)
        asset_returns = mu_daily[:, None, None] + sigma_daily[:, None, None] * Z
        portfolio_returns = np.einsum('i,ijk->jk', weights, asset_returns)

        # 期权尾部保护
        mask = portfolio_returns < -0.03
        portfolio_returns = np.where(mask,
                                      portfolio_returns * 0.5,
                                      portfolio_returns)

        for i in range(batch_size):
            r = pd.Series(portfolio_returns[:, i])
            pm = PerformanceMetrics(r, rf=RF)
            annual_returns.append(pm.annual_return())
            max_drawdowns.append(pm.max_drawdown())
            sharpes.append(pm.sharpe_ratio())
            sortinos.append(pm.sortino_ratio())
            calmars.append(pm.calmar_ratio())

        # 释放内存
        del X, Z, asset_returns, portfolio_returns
        gc.collect()

    return {
        "annual_return_mean": float(np.mean(annual_returns)),
        "max_drawdown_mean": float(np.mean(max_drawdowns)),
        "sharpe_mean": float(np.mean(sharpes)),
        "sortino_mean": float(np.mean(sortinos)),
        "calmar_mean": float(np.mean(calmars)),
        "prob_annual_gt_8pct": float(np.mean(np.array(annual_returns) > 0.08)),
        "prob_dd_lt_15pct": float(np.mean(np.array(max_drawdowns) > -0.15)),
        "n_paths": n_paths,
    }


def generate_report(baseline: OptResult, optimized: OptResult,
                    mc_verified: dict, output_path: Path):
    """生成优化对比报告"""
    lines = [
        "# 组合优化报告 v2 — 7月6日交易计划",
        "",
        f"**生成时间**: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"**优化方法**: 随机搜索 (20,000 次试验, 解析公式) + 蒙特卡洛验证 (3,000 路径)",
        f"**目标函数**: J = Sortino + 0.5 × Calmar - λ × ||w||²  (λ=0.1)",
        f"**约束**: 股票 50%-70% / 对冲 30%-50% / 各类别权重边界",
        "",
        "## 1. 优化前后对比 (解析公式)",
        "",
        "| 指标 | 原始配置 | 优化配置 | 改善 |",
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
        f"| 年化收益率 | {optimized.annual_return:.2%} | "
        f"{mc_verified['annual_return_mean']:.2%} |",
        f"| 最大回撤 | {optimized.max_drawdown:.2%} | "
        f"{mc_verified['max_drawdown_mean']:.2%} |",
        f"| Sharpe | {optimized.sharpe:.3f} | "
        f"{mc_verified['sharpe_mean']:.3f} |",
        f"| Sortino | {optimized.sortino:.3f} | "
        f"{mc_verified['sortino_mean']:.3f} |",
        f"| Calmar | {optimized.calmar:.3f} | "
        f"{mc_verified['calmar_mean']:.3f} |",
        f"| P(年化>8%) | {optimized.prob_annual_gt_8pct:.1%} | "
        f"{mc_verified['prob_annual_gt_8pct']:.1%} |",
        f"| P(回撤<15%) | {optimized.prob_dd_lt_15pct:.1%} | "
        f"{mc_verified['prob_dd_lt_15pct']:.1%} |",
        "",
        "## 3. PDF1 目标达成情况",
        "",
        "| 目标 | 原始 | 优化 | 蒙特卡洛验证 | 达成 |",
        "|------|------|------|------------|------|",
        f"| 年化>8% 概率 ≥ 68% | {baseline.prob_annual_gt_8pct:.1%} | "
        f"{optimized.prob_annual_gt_8pct:.1%} | "
        f"{mc_verified['prob_annual_gt_8pct']:.1%} | "
        f"{'✓' if mc_verified['prob_annual_gt_8pct'] >= 0.68 else '✗'} |",
        f"| 回撤<15% 概率 ≥ 82% | {baseline.prob_dd_lt_15pct:.1%} | "
        f"{optimized.prob_dd_lt_15pct:.1%} | "
        f"{mc_verified['prob_dd_lt_15pct']:.1%} | "
        f"{'✓' if mc_verified['prob_dd_lt_15pct'] >= 0.82 else '✗'} |",
        "",
        "## 4. 权重对比",
        "",
        "| 资产类别 | 原始权重 | 优化权重 | 变动 | 500万建议金额 |",
        "|---------|---------|---------|------|------------|",
    ]

    for i, name in enumerate(ASSET_NAMES):
        old_w = ORIGINAL_WEIGHTS[i]
        new_w = optimized.weights[i]
        delta = new_w - old_w
        amount = new_w * 5_000_000
        lines.append(
            f"| {name} | {old_w:.2%} | {new_w:.2%} | {delta:+.2%} | {amount:,.0f} |"
        )

    stock_old = ORIGINAL_WEIGHTS[:6].sum()
    stock_new = optimized.weights[:6].sum()
    hedge_old = ORIGINAL_WEIGHTS[6:].sum()
    hedge_new = optimized.weights[6:].sum()
    lines.extend([
        "",
        "## 5. 群组汇总",
        "",
        "| 群组 | 原始 | 优化 | 变动 | 500万建议金额 |",
        "|------|------|------|------|------------|",
        f"| 股票组合 | {stock_old:.2%} | {stock_new:.2%} | "
        f"{stock_new - stock_old:+.2%} | {stock_new*5_000_000:,.0f} |",
        f"| 期权对冲 | {hedge_old:.2%} | {hedge_new:.2%} | "
        f"{hedge_new - hedge_old:+.2%} | {hedge_new*5_000_000:,.0f} |",
        "",
        "## 6. 优化建议",
        "",
    ])

    if mc_verified['prob_annual_gt_8pct'] >= 0.68:
        lines.append("- ✓ **年化收益目标达成**: 蒙特卡洛验证 P(年化>8%) ≥ 68%")
    else:
        lines.append(f"- ⚠ **年化收益仍不达标**: 蒙特卡洛 P(年化>8%)="
                     f"{mc_verified['prob_annual_gt_8pct']:.1%}, 需进一步调整")

    if mc_verified['prob_dd_lt_15pct'] >= 0.82:
        lines.append("- ✓ **回撤控制目标达成**: 蒙特卡洛验证 P(回撤<15%) ≥ 82%")
    else:
        lines.append(f"- ⚠ **回撤控制不达标**: 蒙特卡洛 P(回撤<15%)="
                     f"{mc_verified['prob_dd_lt_15pct']:.1%}")

    if optimized.annual_return > baseline.annual_return:
        lines.append(f"- ✓ **年化收益提升**: {baseline.annual_return:.2%} → "
                     f"{optimized.annual_return:.2%} "
                     f"(+{(optimized.annual_return-baseline.annual_return)*100:.2f}pp)")
    if abs(optimized.max_drawdown) < abs(baseline.max_drawdown):
        lines.append(f"- ✓ **回撤改善**: {baseline.max_drawdown:.2%} → "
                     f"{optimized.max_drawdown:.2%}")

    lines.extend([
        "",
        "## 7. 优化策略说明",
        "",
        "- **股票组合**: 提升高收益资产权重 (科技成长/高端制造)",
        "- **期权对冲**: 降低纯成本型对冲 (棉花期权/股票期权)",
        "- **棉花期货**: 维持适度杠杆, 提供收益贡献",
        "- **现金缓冲**: 保持最低水平, 提高资金利用率",
        "",
        "## 8. 风险提示",
        "",
        "- 解析公式为正态分布假设, 实际市场存在肥尾效应",
        "- 蒙特卡洛模拟的尾部保护模型为简化版本",
        "- 建议定期重新优化 (每月/每季度), 适应市场环境变化",
        "- 实盘交易需考虑交易成本、流动性、滑点等因素",
        "",
    ])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info(f"优化报告已生成: {output_path}")


def main():
    logger.info("=" * 60)
    logger.info("Phase 1: 评估原始权重 (解析公式)")
    logger.info("=" * 60)
    baseline = evaluate_analytical(ORIGINAL_WEIGHTS)
    logger.info(f"基线结果:")
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
    logger.info(f"  年化收益率: {mc_verified['annual_return_mean']:.2%}")
    logger.info(f"  最大回撤:   {mc_verified['max_drawdown_mean']:.2%}")
    logger.info(f"  Sharpe:     {mc_verified['sharpe_mean']:.3f}")
    logger.info(f"  Sortino:    {mc_verified['sortino_mean']:.3f}")
    logger.info(f"  Calmar:     {mc_verified['calmar_mean']:.3f}")
    logger.info(f"  P(年化>8%): {mc_verified['prob_annual_gt_8pct']:.1%}")
    logger.info(f"  P(回撤<15%): {mc_verified['prob_dd_lt_15pct']:.1%}")

    logger.info("")
    logger.info("=" * 60)
    logger.info("Phase 4: 优化结果汇总")
    logger.info("=" * 60)
    logger.info(f"优化后权重:")
    for i, name in enumerate(ASSET_NAMES):
        delta = optimized.weights[i] - ORIGINAL_WEIGHTS[i]
        logger.info(f"  {name:18s}: {optimized.weights[i]:.2%} "
                    f"(原始 {ORIGINAL_WEIGHTS[i]:.2%}, 变动 {delta:+.2%})")

    logger.info("")
    logger.info(f"群组汇总:")
    logger.info(f"  股票组合: {optimized.weights[:6].sum():.2%} "
                f"(原始 {ORIGINAL_WEIGHTS[:6].sum():.2%})")
    logger.info(f"  期权对冲: {optimized.weights[6:].sum():.2%} "
                f"(原始 {ORIGINAL_WEIGHTS[6:].sum():.2%})")

    # 生成报告
    report_path = (
        BASE_DIR.parent / "每日报告归档" / "2026" / "07" / "06"
        / "portfolio_optimization_v2_20260706.md"
    )
    generate_report(baseline, optimized, mc_verified, report_path)

    # 保存 JSON
    json_path = report_path.with_suffix(".json")
    result_data = {
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
        "optimization_config": {
            "n_trials": 20000,
            "n_paths_verification": 3000,
            "l2_lambda": 0.1,
            "method": "analytical + monte_carlo_verification",
        },
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result_data, f, indent=2, ensure_ascii=False)
    logger.info(f"优化 JSON: {json_path}")

    return optimized


if __name__ == "__main__":
    main()
