# -*- coding: utf-8 -*-
"""
组合优化 — 最大化 Sortino + 0.5*Calmar 目标函数
==============================================
基于 7月6日组合绩效测试结果 (年化 7.30%, 回撤 -9.73%),
通过随机搜索寻找最优权重配置, 目标:
  - 年化收益率 ≥ 8% (概率 ≥ 68%)
  - 最大回撤 < 15% (概率 ≥ 82%)
  - 最大化 J = Sortino + 0.5 * Calmar - λ * ||w||²

约束:
  - 股票组合 + 期权对冲 = 100%
  - 股票组合: 50%-70%
  - 期权对冲: 30%-50%
  - 各类别权重在合理范围内
"""
from __future__ import annotations

import sys
import json
import logging
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Dict

# 路径初始化
BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from backtest.metrics import PerformanceMetrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("portfolio_optimizer")

# ============================================================
# 资产参数 (与 test_portfolio_metrics.py 一致)
# ============================================================
ASSET_NAMES = [
    "核心宽基ETF", "科技成长个股", "高端制造/基建", "防御/红利",
    "商品/避险", "现金缓冲_股票",
    "棉花期货", "棉花期权保护", "股票期权保护", "现金缓冲_对冲",
]

# 年化收益/波动率假设 (基于 PDF1)
ANN_RETURNS = np.array([0.07, 0.12, 0.10, 0.06, 0.05, 0.02, 0.15, -0.05, -0.03, 0.02])
ANN_VOLS = np.array([0.16, 0.25, 0.22, 0.12, 0.15, 0.005, 0.30, 0.05, 0.05, 0.005])

# 资产相关矩阵 (与 test_portfolio_metrics.py 一致)
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

# 原始权重 (7月6日配置)
ORIGINAL_WEIGHTS = np.array([
    0.168,  # 核心宽基ETF (60% × 28%)
    0.120,  # 科技成长个股 (60% × 20%)
    0.120,  # 高端制造/基建 (60% × 20%)
    0.090,  # 防御/红利 (60% × 15%)
    0.030,  # 商品/避险 (60% × 5%)
    0.048,  # 现金缓冲_股票 (60% × 8%)
    0.200,  # 棉花期货 (40% × 50%)
    0.035,  # 棉花期权保护 (40% × 8.75%)
    0.100,  # 股票期权保护 (40% × 25%)
    0.065,  # 现金缓冲_对冲 (40% × 16.25%)
])

# 权重约束 (lower, upper) — 优化搜索空间
WEIGHT_BOUNDS = [
    (0.10, 0.20),  # 核心宽基ETF: 10%-20%
    (0.15, 0.25),  # 科技成长: 15%-25% (提升上限)
    (0.10, 0.20),  # 高端制造: 10%-20%
    (0.05, 0.15),  # 防御红利: 5%-15%
    (0.02, 0.08),  # 商品避险: 2%-8%
    (0.02, 0.08),  # 现金缓冲(股): 2%-8%
    (0.15, 0.25),  # 棉花期货: 15%-25% (50%-60% 杠杆)
    (0.02, 0.05),  # 棉花期权: 2%-5% (降低成本)
    (0.05, 0.10),  # 股票期权: 5%-10% (降低成本)
    (0.03, 0.08),  # 现金缓冲(对冲): 3%-8%
]

# 群组约束
STOCK_MIN, STOCK_MAX = 0.50, 0.70   # 股票组合 50%-70%
HEDGE_MIN, HEDGE_MAX = 0.30, 0.50   # 期权对冲 30%-50%


@dataclass
class OptimizationResult:
    """优化结果"""
    weights: np.ndarray
    objective: float
    annual_return: float
    max_drawdown: float
    sharpe: float
    sortino: float
    calmar: float
    var_95: float
    prob_annual_gt_8pct: float
    prob_dd_lt_15pct: float
    n_paths: int = 1000  # 优化时减少路径数加速


def sample_weights(rng: np.random.Generator) -> np.ndarray:
    """在约束条件下随机采样权重"""
    for _ in range(100):  # 最多尝试 100 次
        # 1. 在 [0,1] 范围内采样
        w = np.array([
            rng.uniform(lo, hi) for lo, hi in WEIGHT_BOUNDS
        ])

        # 2. 归一化到总和 = 1
        w = w / w.sum()

        # 3. 检查群组约束
        stock_sum = w[:6].sum()  # 前 6 个是股票
        hedge_sum = w[6:].sum()  # 后 4 个是对冲

        if STOCK_MIN <= stock_sum <= STOCK_MAX and \
           HEDGE_MIN <= hedge_sum <= HEDGE_MAX:
            # 4. 检查每个权重是否仍在边界内 (归一化后可能越界)
            valid = all(lo - 1e-3 <= wi <= hi + 1e-3
                       for wi, (lo, hi) in zip(w, WEIGHT_BOUNDS))
            if valid:
                return w

    # 失败时返回原始权重
    return ORIGINAL_WEIGHTS.copy()


def simulate_returns(weights: np.ndarray, n_paths: int, n_days: int,
                    rng: np.random.Generator) -> np.ndarray:
    """模拟组合日收益序列"""
    mu_daily = ANN_RETURNS / 252
    sigma_daily = ANN_VOLS / np.sqrt(252)

    # Cholesky 分解
    corr = CORR_MATRIX + np.eye(len(ASSET_NAMES)) * 1e-6
    L = np.linalg.cholesky(corr)

    # 生成相关随机变量
    X = rng.standard_normal(size=(len(ASSET_NAMES), n_days, n_paths))
    Z = np.einsum('ij,jkl->ikl', L, X)

    # 各资产日收益
    asset_returns = mu_daily[:, None, None] + sigma_daily[:, None, None] * Z

    # 组合日收益 (加权平均)
    portfolio_returns = np.einsum('i,ijk->jk', weights, asset_returns)

    # 期权尾部保护
    tail_threshold = -0.03
    tail_protection = 0.5
    mask = portfolio_returns < tail_threshold
    portfolio_returns = np.where(
        mask,
        portfolio_returns * (1 - tail_protection),
        portfolio_returns,
    )

    return portfolio_returns


def evaluate_weights(weights: np.ndarray, rng: np.random.Generator,
                    n_paths: int = 1000, l2_lambda: float = 0.1) -> OptimizationResult:
    """评估一组权重的绩效"""
    returns = simulate_returns(weights, n_paths, n_days=252, rng=rng)

    annual_returns = []
    max_drawdowns = []
    sharpes = []
    sortinos = []
    calmars = []

    for i in range(n_paths):
        r = pd.Series(returns[:, i])
        pm = PerformanceMetrics(r, rf=0.02, l2_lambda=l2_lambda)
        annual_returns.append(pm.annual_return())
        max_drawdowns.append(pm.max_drawdown())
        sharpes.append(pm.sharpe_ratio())
        sortinos.append(pm.sortino_ratio())
        calmars.append(pm.calmar_ratio())

    # 平均指标
    ann_ret = float(np.mean(annual_returns))
    max_dd = float(np.mean(max_drawdowns))
    sharpe = float(np.mean(sharpes))
    sortino = float(np.mean(sortinos))
    calmar = float(np.mean(calmars))

    # 概率
    prob_annual_8 = float(np.mean(np.array(annual_returns) > 0.08))
    prob_dd_15 = float(np.mean(np.array(max_drawdowns) > -0.15))

    # 整体 VaR
    all_returns = pd.Series(returns.flatten())
    var_95 = float(all_returns.quantile(0.05))

    # 目标函数: J = Sortino + 0.5*Calmar - λ*||w||²
    l2_penalty = l2_lambda * np.sum(weights ** 2)
    objective = sortino + 0.5 * calmar - l2_penalty

    # 惩罚项: 如果不满足目标则降低目标函数值
    if prob_annual_8 < 0.68:
        objective -= 0.5 * (0.68 - prob_annual_8)
    if prob_dd_15 < 0.82:
        objective -= 0.5 * (0.82 - prob_dd_15)

    return OptimizationResult(
        weights=weights,
        objective=objective,
        annual_return=ann_ret,
        max_drawdown=max_dd,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        var_95=var_95,
        prob_annual_gt_8pct=prob_annual_8,
        prob_dd_lt_15pct=prob_dd_15,
        n_paths=n_paths,
    )


def optimize_portfolio(n_trials: int = 5000, seed: int = 42) -> OptimizationResult:
    """随机搜索优化组合权重"""
    rng = np.random.default_rng(seed)

    # 评估原始权重作为基线
    logger.info("评估原始权重 (基线)...")
    baseline = evaluate_weights(ORIGINAL_WEIGHTS, rng, n_paths=1000)
    logger.info(f"基线: J={baseline.objective:.4f}, 年化={baseline.annual_return:.2%}, "
                f"回撤={baseline.max_drawdown:.2%}, P(>8%)={baseline.prob_annual_gt_8pct:.1%}")

    # 随机搜索
    best_result = baseline
    logger.info(f"开始随机搜索: {n_trials} 次试验...")

    for trial in range(n_trials):
        w = sample_weights(rng)
        result = evaluate_weights(w, rng, n_paths=500)  # 减少路径数加速

        if result.objective > best_result.objective:
            best_result = result
            logger.info(
                f"[Trial {trial+1}/{n_trials}] 新最优: "
                f"J={result.objective:.4f}, 年化={result.annual_return:.2%}, "
                f"回撤={result.max_drawdown:.2%}, P(>8%)={result.prob_annual_gt_8pct:.1%}, "
                f"P(DD<15%)={result.prob_dd_lt_15pct:.1%}"
            )

    # 用更多路径验证最优结果
    logger.info("用 5000 路径验证最优结果...")
    best_result_verified = evaluate_weights(best_result.weights, rng, n_paths=5000)
    best_result_verified.objective = best_result.objective  # 保留原始目标值

    return best_result_verified


def generate_optimization_report(
    baseline: OptimizationResult,
    optimized: OptimizationResult,
    output_path: Path,
):
    """生成优化对比报告"""
    lines = [
        "# 组合优化报告 — 7月6日交易计划",
        "",
        f"**生成时间**: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"**优化方法**: 随机搜索 (5,000 次试验 × 500 路径) + 5,000 路径验证",
        f"**目标函数**: J = Sortino + 0.5 × Calmar - λ × ||w||²  (λ=0.1)",
        f"**约束**: 股票 50%-70% / 对冲 30%-50% / 各类别权重边界",
        "",
        "## 1. 优化前后对比",
        "",
        "| 指标 | 原始配置 | 优化配置 | 改善 |",
        "|------|---------|---------|------|",
        f"| 年化收益率 (均值) | {baseline.annual_return:.2%} | {optimized.annual_return:.2%} | "
        f"{optimized.annual_return - baseline.annual_return:+.2%} |",
        f"| 最大回撤 (均值) | {baseline.max_drawdown:.2%} | {optimized.max_drawdown:.2%} | "
        f"{optimized.max_drawdown - baseline.max_drawdown:+.2%} |",
        f"| Sharpe Ratio | {baseline.sharpe:.3f} | {optimized.sharpe:.3f} | "
        f"{optimized.sharpe - baseline.sharpe:+.3f} |",
        f"| Sortino Ratio | {baseline.sortino:.3f} | {optimized.sortino:.3f} | "
        f"{optimized.sortino - baseline.sortino:+.3f} |",
        f"| Calmar Ratio | {baseline.calmar:.3f} | {optimized.calmar:.3f} | "
        f"{optimized.calmar - baseline.calmar:+.3f} |",
        f"| VaR 95% (日) | {baseline.var_95:.4f} | {optimized.var_95:.4f} | "
        f"{optimized.var_95 - baseline.var_95:+.4f} |",
        f"| 年化>8% 概率 | {baseline.prob_annual_gt_8pct:.1%} | "
        f"{optimized.prob_annual_gt_8pct:.1%} | "
        f"{optimized.prob_annual_gt_8pct - baseline.prob_annual_gt_8pct:+.1%} |",
        f"| 回撤<15% 概率 | {baseline.prob_dd_lt_15pct:.1%} | "
        f"{optimized.prob_dd_lt_15pct:.1%} | "
        f"{optimized.prob_dd_lt_15pct - baseline.prob_dd_lt_15pct:+.1%} |",
        "",
        "## 2. PDF1 目标达成情况",
        "",
        "| 目标 | 原始 | 优化 | 达成 |",
        "|------|------|------|------|",
        f"| 年化>8% 概率 ≥ 68% | {baseline.prob_annual_gt_8pct:.1%} | "
        f"{optimized.prob_annual_gt_8pct:.1%} | "
        f"{'✓' if optimized.prob_annual_gt_8pct >= 0.68 else '✗'} |",
        f"| 回撤<15% 概率 ≥ 82% | {baseline.prob_dd_lt_15pct:.1%} | "
        f"{optimized.prob_dd_lt_15pct:.1%} | "
        f"{'✓' if optimized.prob_dd_lt_15pct >= 0.82 else '✗'} |",
        "",
        "## 3. 权重对比",
        "",
        "| 资产类别 | 原始权重 | 优化权重 | 变动 |",
        "|---------|---------|---------|------|",
    ]

    for i, name in enumerate(ASSET_NAMES):
        old_w = ORIGINAL_WEIGHTS[i]
        new_w = optimized.weights[i]
        delta = new_w - old_w
        lines.append(
            f"| {name} | {old_w:.2%} | {new_w:.2%} | {delta:+.2%} |"
        )

    # 群组汇总
    stock_old = ORIGINAL_WEIGHTS[:6].sum()
    stock_new = optimized.weights[:6].sum()
    hedge_old = ORIGINAL_WEIGHTS[6:].sum()
    hedge_new = optimized.weights[6:].sum()
    lines.extend([
        "",
        "## 4. 群组汇总",
        "",
        "| 群组 | 原始 | 优化 | 变动 |",
        "|------|------|------|------|",
        f"| 股票组合 | {stock_old:.2%} | {stock_new:.2%} | {stock_new - stock_old:+.2%} |",
        f"| 期权对冲 | {hedge_old:.2%} | {hedge_new:.2%} | {hedge_new - hedge_old:+.2%} |",
        "",
        "## 5. 优化建议",
        "",
    ])

    # 自动生成建议
    if optimized.prob_annual_gt_8pct >= 0.68:
        lines.append("- ✓ **年化收益目标达成**: 优化后年化>8% 概率 ≥ 68%")
    else:
        lines.append("- ⚠ **年化收益仍不达标**: 建议进一步降低对冲比例或提升科技股上限")

    if optimized.prob_dd_lt_15pct >= 0.82:
        lines.append("- ✓ **回撤控制目标达成**: 优化后回撤<15% 概率 ≥ 82%")
    else:
        lines.append("- ⚠ **回撤控制不达标**: 建议增加期权对冲比例")

    if optimized.annual_return > baseline.annual_return:
        lines.append(f"- ✓ **年化收益提升**: {baseline.annual_return:.2%} → {optimized.annual_return:.2%}")
    if abs(optimized.max_drawdown) < abs(baseline.max_drawdown):
        lines.append(f"- ✓ **回撤改善**: {baseline.max_drawdown:.2%} → {optimized.max_drawdown:.2%}")

    # 资金配置建议 (基于 500万 总资金)
    lines.extend([
        "",
        "## 6. 500万资金配置建议",
        "",
        "| 资产类别 | 优化权重 | 建议金额 |",
        "|---------|---------|---------|",
    ])
    for i, name in enumerate(ASSET_NAMES):
        amount = optimized.weights[i] * 5_000_000
        lines.append(f"| {name} | {optimized.weights[i]:.2%} | {amount:,.0f} |")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info(f"优化报告已生成: {output_path}")


def main():
    rng = np.random.default_rng(42)

    # 1. 评估原始权重
    logger.info("=" * 60)
    logger.info("Phase 1: 评估原始权重 (基线)")
    logger.info("=" * 60)
    baseline = evaluate_weights(ORIGINAL_WEIGHTS, rng, n_paths=5000)
    logger.info(f"基线结果:")
    logger.info(f"  年化收益率: {baseline.annual_return:.2%}")
    logger.info(f"  最大回撤:   {baseline.max_drawdown:.2%}")
    logger.info(f"  Sharpe:     {baseline.sharpe:.3f}")
    logger.info(f"  Sortino:    {baseline.sortino:.3f}")
    logger.info(f"  Calmar:     {baseline.calmar:.3f}")
    logger.info(f"  P(年化>8%): {baseline.prob_annual_gt_8pct:.1%}")
    logger.info(f"  P(回撤<15%): {baseline.prob_dd_lt_15pct:.1%}")
    logger.info(f"  目标函数 J: {baseline.objective:.4f}")

    # 2. 随机搜索优化
    logger.info("")
    logger.info("=" * 60)
    logger.info("Phase 2: 随机搜索优化")
    logger.info("=" * 60)
    optimized = optimize_portfolio(n_trials=5000, seed=42)

    # 3. 输出优化结果
    logger.info("")
    logger.info("=" * 60)
    logger.info("Phase 3: 优化结果")
    logger.info("=" * 60)
    logger.info(f"优化后结果 (5000 路径验证):")
    logger.info(f"  年化收益率: {optimized.annual_return:.2%} "
                f"(改善 {optimized.annual_return - baseline.annual_return:+.2%})")
    logger.info(f"  最大回撤:   {optimized.max_drawdown:.2%} "
                f"(改善 {optimized.max_drawdown - baseline.max_drawdown:+.2%})")
    logger.info(f"  Sharpe:     {optimized.sharpe:.3f} "
                f"(改善 {optimized.sharpe - baseline.sharpe:+.3f})")
    logger.info(f"  Sortino:    {optimized.sortino:.3f} "
                f"(改善 {optimized.sortino - baseline.sortino:+.3f})")
    logger.info(f"  Calmar:     {optimized.calmar:.3f} "
                f"(改善 {optimized.calmar - baseline.calmar:+.3f})")
    logger.info(f"  P(年化>8%): {optimized.prob_annual_gt_8pct:.1%} "
                f"(改善 {optimized.prob_annual_gt_8pct - baseline.prob_annual_gt_8pct:+.1%})")
    logger.info(f"  P(回撤<15%): {optimized.prob_dd_lt_15pct:.1%} "
                f"(改善 {optimized.prob_dd_lt_15pct - baseline.prob_dd_lt_15pct:+.1%})")

    logger.info("")
    logger.info("优化后权重:")
    for i, name in enumerate(ASSET_NAMES):
        delta = optimized.weights[i] - ORIGINAL_WEIGHTS[i]
        logger.info(f"  {name:18s}: {optimized.weights[i]:.2%} "
                    f"(原始 {ORIGINAL_WEIGHTS[i]:.2%}, 变动 {delta:+.2%})")

    # 4. 生成报告
    report_path = (
        BASE_DIR.parent / "每日报告归档" / "2026" / "07" / "06"
        / "portfolio_optimization_20260706.md"
    )
    generate_optimization_report(baseline, optimized, report_path)

    # 5. 保存 JSON
    json_path = report_path.with_suffix(".json")
    result_data = {
        "baseline": {
            "weights": ORIGINAL_WEIGHTS.tolist(),
            "asset_names": ASSET_NAMES,
            "annual_return": baseline.annual_return,
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
            "max_drawdown": optimized.max_drawdown,
            "sharpe": optimized.sharpe,
            "sortino": optimized.sortino,
            "calmar": optimized.calmar,
            "prob_annual_gt_8pct": optimized.prob_annual_gt_8pct,
            "prob_dd_lt_15pct": optimized.prob_dd_lt_15pct,
            "objective": optimized.objective,
        },
        "optimization_config": {
            "n_trials": 5000,
            "n_paths_verification": 5000,
            "l2_lambda": 0.1,
            "constraints": {
                "stock_min": STOCK_MIN,
                "stock_max": STOCK_MAX,
                "hedge_min": HEDGE_MIN,
                "hedge_max": HEDGE_MAX,
                "weight_bounds": WEIGHT_BOUNDS,
            },
        },
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result_data, f, indent=2, ensure_ascii=False)
    logger.info(f"优化 JSON: {json_path}")

    return optimized


if __name__ == "__main__":
    main()
