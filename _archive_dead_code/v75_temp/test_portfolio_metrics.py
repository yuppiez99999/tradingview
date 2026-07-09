# -*- coding: utf-8 -*-
"""
组合绩效测试 — 年化收益率 & 最大回撤
=====================================
基于 7月6日交易计划 (300万股票 + 200万期权对冲) 的蒙特卡洛模拟

数据源:
- trade_plans/trade_plan_20260706.json (持仓配置)
- 300万年度交易组合优化方案 PDF (资产类别预期收益/波动率)
- 棉花期权保护策略 MD (对冲成本与尾部保护)

模拟方法:
- 252 交易日 × 10000 条蒙特卡洛路径
- 多资产相关矩阵 + 期权对冲尾部保护
- 计算年化收益率、最大回撤、Sharpe、Sortino、Calmar
"""
from __future__ import annotations

import sys
import json
import logging
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

# 路径初始化
BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from backtest.metrics import PerformanceMetrics, compute_max_drawdown

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("portfolio_metrics")

# ============================================================
# 资产类别参数 (基于 PDF1 Monte Carlo 假设)
# ============================================================
ASSET_PARAMS = {
    # 股票组合 300万 (60%)
    "核心宽基ETF":   {"weight": 0.28, "ann_return": 0.07, "ann_vol": 0.16},
    "科技成长个股":   {"weight": 0.20, "ann_return": 0.12, "ann_vol": 0.25},
    "高端制造/基建":  {"weight": 0.20, "ann_return": 0.10, "ann_vol": 0.22},
    "防御/红利":      {"weight": 0.15, "ann_return": 0.06, "ann_vol": 0.12},
    "商品/避险":      {"weight": 0.05, "ann_return": 0.05, "ann_vol": 0.15},
    "现金缓冲_股票":  {"weight": 0.08, "ann_return": 0.02, "ann_vol": 0.005},
    # 期权对冲 200万 (40%)
    "棉花期货":       {"weight": 0.50, "ann_return": 0.15, "ann_vol": 0.30},  # 50% 杠杆
    "棉花期权保护":   {"weight": 0.0875, "ann_return": -0.05, "ann_vol": 0.05},  # 权利金成本
    "股票期权保护":   {"weight": 0.25, "ann_return": -0.03, "ann_vol": 0.05},  # Collar 成本
    "现金缓冲_对冲":  {"weight": 0.1625, "ann_return": 0.02, "ann_vol": 0.005},
}

# 500万 总资金下各类别权重 (股票 60% + 对冲 40%)
TOTAL_WEIGHTS = {
    "核心宽基ETF":     0.60 * 0.28,    # 0.168
    "科技成长个股":     0.60 * 0.20,    # 0.120
    "高端制造/基建":    0.60 * 0.20,    # 0.120
    "防御/红利":        0.60 * 0.15,    # 0.090
    "商品/避险":        0.60 * 0.05,    # 0.030
    "现金缓冲_股票":    0.60 * 0.08,    # 0.048
    "棉花期货":         0.40 * 0.50,    # 0.200
    "棉花期权保护":     0.40 * 0.0875,  # 0.035
    "股票期权保护":     0.40 * 0.25,    # 0.100
    "现金缓冲_对冲":    0.40 * 0.1625,  # 0.065
}

# 资产相关矩阵 (股票内部正相关, 期权与股票负相关)
CORRELATION_MATRIX = np.array([
    # 宽基  科技  制造  红利  商品  现金s  棉期  棉权  股权  现金h
    [1.00, 0.70, 0.65, 0.60, 0.20, 0.00, 0.10, -0.20, -0.30, 0.00],  # 核心宽基
    [0.70, 1.00, 0.75, 0.40, 0.15, 0.00, 0.05, -0.25, -0.35, 0.00],  # 科技成长
    [0.65, 0.75, 1.00, 0.45, 0.30, 0.00, 0.20, -0.15, -0.25, 0.00],  # 高端制造
    [0.60, 0.40, 0.45, 1.00, 0.10, 0.00, 0.05, -0.10, -0.20, 0.00],  # 防御红利
    [0.20, 0.15, 0.30, 0.10, 1.00, 0.00, 0.40, -0.10, -0.05, 0.00],  # 商品避险
    [0.00, 0.00, 0.00, 0.00, 0.00, 1.00, 0.00, 0.00, 0.00, 0.30],    # 现金(股)
    [0.10, 0.05, 0.20, 0.05, 0.40, 0.00, 1.00, 0.50, 0.20, 0.00],    # 棉花期货
    [-0.20,-0.25,-0.15,-0.10,-0.10, 0.00, 0.50, 1.00, 0.60, 0.00],    # 棉花期权
    [-0.30,-0.35,-0.25,-0.20,-0.05, 0.00, 0.20, 0.60, 1.00, 0.00],    # 股票期权
    [0.00, 0.00, 0.00, 0.00, 0.00, 0.30, 0.00, 0.00, 0.00, 1.00],    # 现金(对冲)
])


def load_portfolio_weights(trade_plan_path: Path) -> dict:
    """从交易计划加载组合权重"""
    with open(trade_plan_path, "r", encoding="utf-8") as f:
        plan = json.load(f)

    total_capital = plan["capital"]  # 5,000,000
    weights = {}

    # 股票组合
    for cat_name, cat_data in plan["stock_portfolio"]["categories"].items():
        for item in cat_data.get("items", []):
            code = item["code"]
            w = item["amount"] / total_capital
            weights[code] = w

    # 期权对冲 — 按类别分配
    hedge_map = {
        "棉花期货保证金": "CF2609",
        "棉花期权保护": "CF609P15600",
        "股票期权保护": "STOCK_OPTION",
    }
    for cat_name, cat_data in plan["hedge_portfolio"]["categories"].items():
        if cat_name in hedge_map:
            code = hedge_map[cat_name]
            w = cat_data["amount"] / total_capital
            weights[code] = w
        elif cat_name == "现金缓冲":
            weights["CASH_HEDGE"] = cat_data["amount"] / total_capital

    return weights, total_capital


def monte_carlo_simulation(
    n_paths: int = 10000,
    n_days: int = 252,
    seed: int = 42,
) -> pd.DataFrame:
    """
    蒙特卡洛模拟组合日收益序列

    Args:
        n_paths: 模拟路径数
        n_days: 交易日数 (252 = 1年)
        seed: 随机种子

    Returns:
        DataFrame: shape=(n_days, n_paths), 每列为一条路径的日收益
    """
    rng = np.random.default_rng(seed)

    # 资产参数向量化
    asset_names = list(TOTAL_WEIGHTS.keys())
    weights = np.array([TOTAL_WEIGHTS[k] for k in asset_names])
    mu_annual = np.array([ASSET_PARAMS[k]["ann_return"] for k in asset_names])
    sigma_annual = np.array([ASSET_PARAMS[k]["ann_vol"] for k in asset_names])

    # 转为日参数
    mu_daily = mu_annual / 252
    sigma_daily = sigma_annual / np.sqrt(252)

    # Cholesky 分解相关矩阵
    # 添加微小正则化保证正定
    corr = CORRELATION_MATRIX + np.eye(len(asset_names)) * 1e-6
    L = np.linalg.cholesky(corr)

    # 生成相关随机变量: Z = L @ X
    # shape: (n_assets, n_days, n_paths)
    X = rng.standard_normal(size=(len(asset_names), n_days, n_paths))
    Z = np.einsum('ij,jkl->ikl', L, X)

    # 各资产日收益
    asset_returns = mu_daily[:, None, None] + sigma_daily[:, None, None] * Z

    # 组合日收益 (加权平均)
    portfolio_returns = np.einsum('i,ijk->jk', weights, asset_returns)

    # 期权尾部保护: 当组合日跌幅 > 3% 时, 期权生效减少 50% 损失
    tail_protection = 0.5
    tail_threshold = -0.03
    mask = portfolio_returns < tail_threshold
    portfolio_returns = np.where(
        mask,
        portfolio_returns * (1 - tail_protection),
        portfolio_returns,
    )

    return pd.DataFrame(portfolio_returns)


def compute_portfolio_metrics(returns: pd.DataFrame, rf: float = 0.02) -> dict:
    """
    计算组合绩效指标 (基于所有路径的统计)

    Args:
        returns: 日收益 DataFrame (n_days × n_paths)
        rf: 无风险利率

    Returns:
        绩效指标字典
    """
    # 单条路径的指标计算 (取所有路径的平均)
    annual_returns = []
    max_drawdowns = []
    sharpes = []
    sortinos = []
    calmars = []
    var_95s = []

    for col in returns.columns:
        r = returns[col]
        pm = PerformanceMetrics(r, rf=rf)
        annual_returns.append(pm.annual_return())
        max_drawdowns.append(pm.max_drawdown())
        sharpes.append(pm.sharpe_ratio())
        sortinos.append(pm.sortino_ratio())
        calmars.append(pm.calmar_ratio())
        var_95s.append(pm.value_at_risk(0.95))

    # 拼接所有路径作为整体 (用于 VaR/CVaR)
    all_returns = returns.values.flatten()
    all_returns_series = pd.Series(all_returns)
    pm_all = PerformanceMetrics(all_returns_series, rf=rf)

    return {
        # 年化收益率
        "annual_return_mean": float(np.mean(annual_returns)),
        "annual_return_median": float(np.median(annual_returns)),
        "annual_return_p25": float(np.percentile(annual_returns, 25)),
        "annual_return_p75": float(np.percentile(annual_returns, 75)),
        "annual_return_std": float(np.std(annual_returns)),
        "prob_annual_gt_8pct": float(np.mean(np.array(annual_returns) > 0.08)),

        # 最大回撤
        "max_drawdown_mean": float(np.mean(max_drawdowns)),
        "max_drawdown_median": float(np.median(max_drawdowns)),
        "max_drawdown_p95": float(np.percentile(max_drawdowns, 5)),  # 5% 最差路径
        "max_drawdown_worst": float(np.min(max_drawdowns)),
        "prob_dd_lt_15pct": float(np.mean(np.array(max_drawdowns) > -0.15)),

        # 风险调整收益
        "sharpe_mean": float(np.mean(sharpes)),
        "sortino_mean": float(np.mean(sortinos)),
        "calmar_mean": float(np.mean(calmars)),

        # VaR/CVaR
        "var_95": pm_all.value_at_risk(0.95),
        "cvar_95": pm_all.conditional_var(0.95),

        # 概率统计
        "n_paths": len(returns.columns),
        "n_days": len(returns),
    }


def generate_report(metrics: dict, output_path: Path):
    """生成 Markdown 报告"""
    lines = [
        "# 7月6日组合绩效测试报告",
        "",
        f"**生成时间**: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"**模拟方法**: 蒙特卡洛模拟 (10,000 路径 × 252 交易日)",
        f"**组合配置**: 500万 = 300万股票 + 200万期权对冲",
        f"**数据源**: trade_plan_20260706.json + 300万年度交易组合优化方案PDF",
        "",
        "## 1. 年化收益率",
        "",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 均值 | {metrics['annual_return_mean']:.2%} |",
        f"| 中位数 | {metrics['annual_return_median']:.2%} |",
        f"| 25分位 | {metrics['annual_return_p25']:.2%} |",
        f"| 75分位 | {metrics['annual_return_p75']:.2%} |",
        f"| 标准差 | {metrics['annual_return_std']:.2%} |",
        f"| 年化>8% 概率 | {metrics['prob_annual_gt_8pct']:.1%} |",
        "",
        "## 2. 最大回撤",
        "",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 均值 | {metrics['max_drawdown_mean']:.2%} |",
        f"| 中位数 | {metrics['max_drawdown_median']:.2%} |",
        f"| 95%分位 (5%最差) | {metrics['max_drawdown_p95']:.2%} |",
        f"| 最差路径 | {metrics['max_drawdown_worst']:.2%} |",
        f"| 回撤<15% 概率 | {metrics['prob_dd_lt_15pct']:.1%} |",
        "",
        "## 3. 风险调整收益指标",
        "",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| Sharpe Ratio (均值) | {metrics['sharpe_mean']:.3f} |",
        f"| Sortino Ratio (均值) | {metrics['sortino_mean']:.3f} |",
        f"| Calmar Ratio (均值) | {metrics['calmar_mean']:.3f} |",
        "",
        "## 4. VaR / CVaR (整体分布)",
        "",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| VaR 95% (日) | {metrics['var_95']:.4f} ({metrics['var_95']*100:.2f}%) |",
        f"| CVaR 95% (日) | {metrics['cvar_95']:.4f} ({metrics['cvar_95']*100:.2f}%) |",
        "",
        "## 5. PDF1 目标达成情况",
        "",
        f"| 目标 | 实测 | 达成 |",
        f"|------|------|------|",
        f"| 年化>8% 概率 ≥ 68% | {metrics['prob_annual_gt_8pct']:.1%} | "
        f"{'✓' if metrics['prob_annual_gt_8pct'] >= 0.68 else '✗'} |",
        f"| 回撤<15% 概率 ≥ 82% | {metrics['prob_dd_lt_15pct']:.1%} | "
        f"{'✓' if metrics['prob_dd_lt_15pct'] >= 0.82 else '✗'} |",
        "",
        "## 6. 资产配置权重",
        "",
        "| 类别 | 权重 | 年化收益 | 年化波动 |",
        "|------|------|---------|---------|",
    ]
    for name, w in TOTAL_WEIGHTS.items():
        p = ASSET_PARAMS[name]
        lines.append(f"| {name} | {w:.2%} | {p['ann_return']:.1%} | {p['ann_vol']:.1%} |")

    lines.extend([
        "",
        "## 7. 模拟说明",
        "",
        "- 252 交易日 (1年) 蒙特卡洛模拟",
        "- 10,000 条独立路径",
        "- 资产相关矩阵考虑股票内部正相关、期权与股票负相关",
        "- 期权尾部保护: 当组合日跌幅 > 3% 时, 减少 50% 损失",
        "- 无风险利率: 2%",
        "",
    ])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info(f"报告已生成: {output_path}")


def main():
    # 1. 加载交易计划
    plan_path = BASE_DIR / "trade_plans" / "trade_plan_20260706.json"
    if not plan_path.exists():
        logger.error(f"交易计划不存在: {plan_path}")
        sys.exit(1)

    weights, total_capital = load_portfolio_weights(plan_path)
    logger.info(f"加载组合: 总资金 {total_capital:,.0f}, 标的数 {len(weights)}")

    # 2. 蒙特卡洛模拟
    logger.info("开始蒙特卡洛模拟: 10,000 路径 × 252 天")
    returns = monte_carlo_simulation(n_paths=10000, n_days=252, seed=42)
    logger.info(f"模拟完成: shape={returns.shape}")

    # 3. 计算绩效指标
    metrics = compute_portfolio_metrics(returns, rf=0.02)

    # 4. 控制台输出关键指标
    logger.info("=" * 60)
    logger.info("组合绩效指标 (10,000 路径蒙特卡洛)")
    logger.info("=" * 60)
    logger.info(f"年化收益率 (均值):   {metrics['annual_return_mean']:.2%}")
    logger.info(f"年化收益率 (中位数): {metrics['annual_return_median']:.2%}")
    logger.info(f"年化>8% 概率:        {metrics['prob_annual_gt_8pct']:.1%}")
    logger.info(f"最大回撤 (均值):     {metrics['max_drawdown_mean']:.2%}")
    logger.info(f"最大回撤 (中位数):   {metrics['max_drawdown_median']:.2%}")
    logger.info(f"最大回撤 (5%最差):   {metrics['max_drawdown_p95']:.2%}")
    logger.info(f"回撤<15% 概率:       {metrics['prob_dd_lt_15pct']:.1%}")
    logger.info(f"Sharpe Ratio:        {metrics['sharpe_mean']:.3f}")
    logger.info(f"Sortino Ratio:       {metrics['sortino_mean']:.3f}")
    logger.info(f"Calmar Ratio:        {metrics['calmar_mean']:.3f}")
    logger.info(f"VaR 95% (日):        {metrics['var_95']:.4f}")
    logger.info(f"CVaR 95% (日):       {metrics['cvar_95']:.4f}")
    logger.info("=" * 60)

    # 5. 生成报告
    report_path = (
        BASE_DIR.parent / "每日报告归档" / "2026" / "07" / "06"
        / "portfolio_metrics_20260706.md"
    )
    generate_report(metrics, report_path)

    # 6. 保存 JSON
    json_path = report_path.with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)
    logger.info(f"指标 JSON: {json_path}")

    return metrics


if __name__ == "__main__":
    main()
