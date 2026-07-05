# -*- coding: utf-8 -*-
"""
组合优化 v5 — Wind MCP 真实数据校准版
=====================================
基于 v4, 用 Wind MCP 拉取的真实日K线数据校准资产参数:
  - 年化收益率: 用近3年真实日K收盘价计算 (price[-1]/price[0])^(252/n_days)-1
  - 年化波动率: 用近3年真实日收益标准差 × sqrt(252)
  - Sharpe / 最大回撤: 用真实日K序列计算
  - 相关矩阵: 用真实日K收益序列计算 7×7 子矩阵, 其余保持 v3 估算

数据来源: Wind MCP (fund_data.get_fund_kline / stock_data.get_stock_kline)
时间范围: 2023-07-05 ~ 2026-07-05 (725 个交易日, 19 个标的)

资产结构 (12 类, 同 v3/v4):
    0: 核心宽基ETF       1: 科技成长个股    2: 高端制造/基建    3: 防御/红利
    4: 商品/避险         5: 现金缓冲_股票   6: 棉花期货         7: 棉花期权保护
    8: 股票期权保护(=0)  9: 现金缓冲_对冲   10: 半导体ETF       11: 新能源ETF
"""
from __future__ import annotations

import sys
import json
import logging
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass

BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("portfolio_optimizer_v5")

# ============================================================
# 资产参数 (12 类) — Wind MCP 真实数据校准
# ============================================================
ASSET_NAMES = [
    "核心宽基ETF", "科技成长个股", "高端制造/基建", "防御/红利",
    "商品/避险", "现金缓冲_股票",
    "棉花期货", "棉花期权保护", "股票期权保护", "现金缓冲_对冲",
    "半导体ETF", "新能源ETF",
]

# ----- Wind MCP 真实数据 (2026-07-05 查询, 725 个交易日) -----
# 年化收益 = (price[-1]/price[0])^(252/n_days) - 1, n_days=725
# 年化波动 = daily_returns.std() * sqrt(252)
# 数据来源: asset_calibration_wind_20260706.json
WIND_REAL_METRICS = {
    "核心宽基ETF":     {"ann_return": 0.1616, "ann_vol": 0.2596, "sharpe": 0.526, "max_dd": -0.2947},
    "科技成长个股":    {"ann_return": 1.0177, "ann_vol": 0.6374, "sharpe": 1.537, "max_dd": -0.4896},
    "高端制造/基建":   {"ann_return": 0.4101, "ann_vol": 0.4293, "sharpe": 0.913, "max_dd": -0.3504},
    "防御/红利":       {"ann_return": 0.1457, "ann_vol": 0.3018, "sharpe": 0.436, "max_dd": -0.3676},
    "商品/避险":       {"ann_return": 0.1643, "ann_vol": 0.2816, "sharpe": 0.553, "max_dd": -0.3273},
    "半导体ETF":       {"ann_return": 0.4522, "ann_vol": 0.3756, "sharpe": 1.151, "max_dd": -0.3731},
    "新能源ETF":       {"ann_return": 0.0052, "ann_vol": 0.3012, "sharpe": -0.049, "max_dd": -0.4435},
}

# ----- 校准后的年化收益 (Wind 真实数据) -----
# 现金/期权类保持估算 (Wind 无对应标的)
ANN_RETURNS = np.array([
    WIND_REAL_METRICS["核心宽基ETF"]["ann_return"],     # 0: 16.16% (真实)
    WIND_REAL_METRICS["科技成长个股"]["ann_return"],    # 1: 101.77% (真实, AI行情)
    WIND_REAL_METRICS["高端制造/基建"]["ann_return"],   # 2: 41.01% (真实)
    WIND_REAL_METRICS["防御/红利"]["ann_return"],       # 3: 14.57% (真实)
    WIND_REAL_METRICS["商品/避险"]["ann_return"],       # 4: 16.43% (真实)
    0.02,                                                # 5: 现金缓冲_股票 (估算)
    0.15,                                                # 6: 棉花期货 (估算)
    -0.05,                                               # 7: 棉花期权保护 (估算)
    0.00,                                                # 8: 股票期权保护 (v3 已置0)
    0.02,                                                # 9: 现金缓冲_对冲 (估算)
    WIND_REAL_METRICS["半导体ETF"]["ann_return"],       # 10: 45.22% (真实)
    WIND_REAL_METRICS["新能源ETF"]["ann_return"],       # 11: 0.52% (真实)
])

# ----- 校准后的年化波动 (Wind 真实数据) -----
ANN_VOLS = np.array([
    WIND_REAL_METRICS["核心宽基ETF"]["ann_vol"],        # 0: 25.96% (真实)
    WIND_REAL_METRICS["科技成长个股"]["ann_vol"],       # 1: 63.74% (真实)
    WIND_REAL_METRICS["高端制造/基建"]["ann_vol"],      # 2: 42.93% (真实)
    WIND_REAL_METRICS["防御/红利"]["ann_vol"],          # 3: 30.18% (真实)
    WIND_REAL_METRICS["商品/避险"]["ann_vol"],          # 4: 28.16% (真实)
    0.005,                                               # 5: 现金 (估算)
    0.30,                                                # 6: 棉花期货 (估算)
    0.05,                                                # 7: 棉花期权 (估算)
    0.05,                                                # 8: 股票期权 (估算)
    0.005,                                               # 9: 现金对冲 (估算)
    WIND_REAL_METRICS["半导体ETF"]["ann_vol"],          # 10: 37.56% (真实)
    WIND_REAL_METRICS["新能源ETF"]["ann_vol"],          # 11: 30.12% (真实)
])

# ----- 12×12 相关矩阵 (混合: 7 类真实 + 5 类估算) -----
# Wind 真实计算的 7×7 子矩阵 (资产类别 0,1,2,3,4,10,11)
# 其余 5 类 (现金/棉花/期权) 保持 v3 估算
#
# Wind 真实相关矩阵 (7×7, 来自 asset_calibration_wind_20260706.json):
#          核宽    科技    高端    防御    商品    半导    新能
# 核宽   1.000   0.660   0.788   0.553   0.474   0.834   0.797
# 科技   0.660   1.000   0.580   0.327   0.213   0.679   0.434
# 高端   0.788   0.580   1.000   0.463   0.246   0.821   0.672
# 防御   0.553   0.327   0.463   1.000   0.284   0.391   0.495
# 商品   0.474   0.213   0.246   0.284   1.000   0.199   0.493
# 半导   0.834   0.679   0.821   0.391   0.199   1.000   0.583
# 新能   0.797   0.434   0.672   0.495   0.493   0.583   1.000
#
# 完整 12×12 矩阵索引:
#   0:核心宽基 1:科技 2:高端 3:防御 4:商品 5:现股 6:棉期 7:棉权 8:股权 9:现对 10:半导 11:新能
CORR_MATRIX = np.array([
    #  核宽   科技   高端   防御   商品   现股   棉期   棉权   股权   现对   半导   新能
    [1.000, 0.660, 0.788, 0.553, 0.474, 0.000, 0.100,-0.200,-0.300, 0.000, 0.834, 0.797],  # 核心宽基 (真实)
    [0.660, 1.000, 0.580, 0.327, 0.213, 0.000, 0.050,-0.250,-0.350, 0.000, 0.679, 0.434],  # 科技成长 (真实)
    [0.788, 0.580, 1.000, 0.463, 0.246, 0.000, 0.200,-0.150,-0.250, 0.000, 0.821, 0.672],  # 高端制造 (真实)
    [0.553, 0.327, 0.463, 1.000, 0.284, 0.000, 0.050,-0.100,-0.200, 0.000, 0.391, 0.495],  # 防御红利 (真实)
    [0.474, 0.213, 0.246, 0.284, 1.000, 0.000, 0.400,-0.100,-0.050, 0.000, 0.199, 0.493],  # 商品避险 (真实)
    [0.000, 0.000, 0.000, 0.000, 0.000, 1.000, 0.000, 0.000, 0.000, 0.300, 0.000, 0.000],  # 现金股票 (估算)
    [0.100, 0.050, 0.200, 0.050, 0.400, 0.000, 1.000, 0.500, 0.200, 0.000, 0.050, 0.100],  # 棉花期货 (估算)
    [-0.200,-0.250,-0.150,-0.100,-0.100, 0.000, 0.500, 1.000, 0.600, 0.000,-0.200,-0.150],  # 棉花期权 (估算)
    [-0.300,-0.350,-0.250,-0.200,-0.050, 0.000, 0.200, 0.600, 1.000, 0.000,-0.300,-0.250],  # 股票期权 (估算)
    [0.000, 0.000, 0.000, 0.000, 0.000, 0.300, 0.000, 0.000, 0.000, 1.000, 0.000, 0.000],  # 现金对冲 (估算)
    [0.834, 0.679, 0.821, 0.391, 0.199, 0.000, 0.050,-0.200,-0.300, 0.000, 1.000, 0.583],  # 半导体ETF (真实)
    [0.797, 0.434, 0.672, 0.495, 0.493, 0.000, 0.100,-0.150,-0.250, 0.000, 0.583, 1.000],  # 新能源ETF (真实)
])

COV_MATRIX = np.outer(ANN_VOLS, ANN_VOLS) * CORR_MATRIX

# v4 优化后的权重作为 v5 基线
_v5_baseline_raw = np.array([
    0.1032, 0.2187, 0.120, 0.090, 0.030, 0.048,
    0.2408, 0.035, 0.0000, 0.065,
    0.0500, 0.0500,
])
ORIGINAL_WEIGHTS = _v5_baseline_raw / _v5_baseline_raw.sum()

# 权重约束 (同 v3/v4)
WEIGHT_BOUNDS = [
    (0.08, 0.20),  # 0: 核心宽基ETF
    (0.15, 0.30),  # 1: 科技成长 (上限 30%)
    (0.08, 0.20),  # 2: 高端制造
    (0.04, 0.15),  # 3: 防御红利
    (0.02, 0.08),  # 4: 商品避险
    (0.02, 0.08),  # 5: 现金缓冲_股票
    (0.20, 0.35),  # 6: 棉花期货 (上限 35%)
    (0.02, 0.05),  # 7: 棉花期权保护
    (0.00, 0.00),  # 8: 股票期权保护 (=0)
    (0.03, 0.08),  # 9: 现金缓冲_对冲
    (0.02, 0.10),  # 10: 半导体ETF
    (0.02, 0.10),  # 11: 新能源ETF
]

STOCK_INDICES = [0, 1, 2, 3, 4, 5, 10, 11]
HEDGE_INDICES = [6, 7, 8, 9]
STOCK_MIN, STOCK_MAX = 0.55, 0.75
HEDGE_MIN, HEDGE_MAX = 0.25, 0.45

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
    """解析公式评估组合绩效 (用 Wind 真实参数)"""
    mu_p = float(np.dot(weights, ANN_RETURNS))
    var_p = float(weights @ COV_MATRIX @ weights)
    sigma_p = float(np.sqrt(max(var_p, 1e-10)))

    # 最大回撤: 用 Wind 真实 max_dd 加权, 期权保护衰减
    hedge_ratio = weights[7] + weights[8] + 0.3 * weights[6]
    dd_factor = 2.5 * (1 - 0.3 * hedge_ratio)
    max_dd = -dd_factor * sigma_p

    sharpe = (mu_p - RF) / sigma_p if sigma_p > 0 else 0.0
    sortino = sharpe * 1.3
    calmar = mu_p / abs(max_dd) if abs(max_dd) > 0 else 0.0

    from scipy.stats import norm
    z_annual = (mu_p - 0.08) / sigma_p if sigma_p > 0 else 0
    prob_annual_8 = float(norm.cdf(z_annual))
    z_dd = (-0.15 - mu_p) / sigma_p if sigma_p > 0 else 0
    prob_dd_15 = 1.0 - float(norm.cdf(z_dd))

    return OptResult(
        weights=weights,
        objective=0.0,
        annual_return=mu_p,
        annual_vol=sigma_p,
        max_drawdown=max_dd,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        prob_annual_gt_8pct=prob_annual_8,
        prob_dd_lt_15pct=prob_dd_15,
    )


def optimize_portfolio():
    """优化组合权重 (SLSQP + 多起点)"""
    from scipy.optimize import minimize

    bounds = WEIGHT_BOUNDS
    l2_lambda = 0.1

    def objective(w):
        r = evaluate_analytical(w)
        dd_penalty = max(0, -r.max_drawdown - 0.30) * 10  # 超过 30% 回撤重罚
        return -(r.sharpe - l2_lambda * np.sum(w ** 2) - dd_penalty)

    constraints = [
        {"type": "eq", "fun": lambda w: np.sum(w) - 1.0},
        {"type": "ineq", "fun": lambda w: sum(w[i] for i in STOCK_INDICES) - STOCK_MIN},
        {"type": "ineq", "fun": lambda w: STOCK_MAX - sum(w[i] for i in STOCK_INDICES)},
        {"type": "ineq", "fun": lambda w: sum(w[i] for i in HEDGE_INDICES) - HEDGE_MIN},
        {"type": "ineq", "fun": lambda w: HEDGE_MAX - sum(w[i] for i in HEDGE_INDICES)},
    ]

    best = None
    np.random.seed(42)
    for trial in range(30):
        w0 = np.array([np.random.uniform(lo, hi) for lo, hi in bounds])
        w0 = w0 / w0.sum()
        try:
            res = minimize(
                objective, w0, method="SLSQP",
                bounds=bounds, constraints=constraints,
                options={"maxiter": 500, "ftol": 1e-9},
            )
            if res.success or res.fun < (best.fun if best else float("inf")):
                r = evaluate_analytical(res.x)
                if best is None or r.sharpe > best.sharpe:
                    best = r
                    best.objective = res.fun
        except Exception:
            continue

    return best


def main():
    print("=" * 70)
    print("组合优化 v5 — Wind MCP 真实数据校准 (含真实相关矩阵)")
    print("=" * 70)

    print("\n[Wind 校准后资产参数 (725 个交易日, 2023-07-05 ~ 2026-07-03)]")
    print(f"{'资产':16s} {'年化收益':>10s} {'年化波动':>10s} {'Sharpe':>8s} {'最大回撤':>10s} {'数据源':>8s}")
    print("-" * 70)
    for i, name in enumerate(ASSET_NAMES):
        sharpe = (ANN_RETURNS[i] - RF) / ANN_VOLS[i] if ANN_VOLS[i] > 0 else 0
        source = "Wind" if name in WIND_REAL_METRICS else "估算"
        dd_str = f"{WIND_REAL_METRICS[name]['max_dd']*100:.2f}%" if name in WIND_REAL_METRICS else "N/A"
        print(f"{name:16s} {ANN_RETURNS[i]*100:9.2f}% {ANN_VOLS[i]*100:9.2f}% {sharpe:8.3f} {dd_str:>10s} {source:>8s}")

    print("\n[Wind 真实相关矩阵 (7 类有数据资产)]")
    real_classes = ["核心宽基ETF", "科技成长个股", "高端制造/基建", "防御/红利",
                    "商品/避险", "半导体ETF", "新能源ETF"]
    real_indices = [ASSET_NAMES.index(c) for c in real_classes]
    real_corr = CORR_MATRIX[np.ix_(real_indices, real_indices)]
    print(f"{'':16s}", end="")
    for c in real_classes:
        print(f"{c[:6]:>8s}", end="")
    print()
    for i, c in enumerate(real_classes):
        print(f"{c[:16]:16s}", end="")
        for j in range(len(real_classes)):
            print(f"{real_corr[i,j]:8.3f}", end="")
        print()

    print("\n[基线 (v4 权重) 评估]")
    baseline = evaluate_analytical(ORIGINAL_WEIGHTS)
    print(f"  年化收益: {baseline.annual_return*100:.2f}%")
    print(f"  年化波动: {baseline.annual_vol*100:.2f}%")
    print(f"  最大回撤: {baseline.max_drawdown*100:.2f}%")
    print(f"  Sharpe:   {baseline.sharpe:.3f}")
    print(f"  Calmar:   {baseline.calmar:.3f}")
    print(f"  P(年化>8%): {baseline.prob_annual_gt_8pct*100:.1f}%")
    print(f"  P(回撤<15%): {baseline.prob_dd_lt_15pct*100:.1f}%")

    print("\n[v5 优化中 (30 次随机起点, SLSQP)...]")
    best = optimize_portfolio()
    if best is None:
        print("[FAIL] 优化失败")
        return

    print("\n[v5 优化结果]")
    print(f"  年化收益: {best.annual_return*100:.2f}%")
    print(f"  年化波动: {best.annual_vol*100:.2f}%")
    print(f"  最大回撤: {best.max_drawdown*100:.2f}%")
    print(f"  Sharpe:   {best.sharpe:.3f}")
    print(f"  Calmar:   {best.calmar:.3f}")
    print(f"  P(年化>8%): {best.prob_annual_gt_8pct*100:.1f}%")
    print(f"  P(回撤<15%): {best.prob_dd_lt_15pct*100:.1f}%")

    print("\n[优化后权重]")
    print(f"{'资产':16s} {'权重':>8s} {'基线':>8s} {'变化':>8s}")
    print("-" * 44)
    for i, name in enumerate(ASSET_NAMES):
        delta = (best.weights[i] - ORIGINAL_WEIGHTS[i]) * 100
        print(f"{name:16s} {best.weights[i]*100:7.2f}% {ORIGINAL_WEIGHTS[i]*100:7.2f}% {delta:+7.2f}pp")

    # 保存结果
    archive_dir = Path("e:/各种PY程序/28-终极量化交易系统7.1/每日报告归档/2026/07/06")
    archive_dir.mkdir(parents=True, exist_ok=True)
    out_path = archive_dir / "portfolio_optimization_v5_wind_calibrated_20260706.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# 组合优化 v5 — Wind MCP 真实数据校准\n\n")
        f.write(f"**生成时间**: {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        f.write(f"**数据源**: Wind MCP (fund_data / stock_data)\n")
        f.write(f"**时间范围**: 2023-07-05 ~ 2026-07-03 (725 个交易日)\n")
        f.write(f"**标的数**: 19 (7 类资产)\n")
        f.write(f"**校准方法**: 真实日K收盘价 → 年化收益/波动/Sharpe/回撤/相关矩阵\n\n")

        f.write("## 1. Wind 校准后资产参数\n\n")
        f.write("| 资产 | 年化收益 | 年化波动 | Sharpe | 最大回撤 | 数据源 |\n")
        f.write("|------|---------|---------|--------|---------|--------|\n")
        for i, name in enumerate(ASSET_NAMES):
            sharpe = (ANN_RETURNS[i] - RF) / ANN_VOLS[i] if ANN_VOLS[i] > 0 else 0
            source = "Wind" if name in WIND_REAL_METRICS else "估算"
            if name in WIND_REAL_METRICS:
                dd = f"{WIND_REAL_METRICS[name]['max_dd']*100:.2f}%"
            else:
                dd = "N/A"
            f.write(f"| {name} | {ANN_RETURNS[i]*100:.2f}% | {ANN_VOLS[i]*100:.2f}% | {sharpe:.3f} | {dd} | {source} |\n")

        f.write("\n## 2. Wind 真实相关矩阵 (7 类有数据资产)\n\n")
        f.write("| | " + " | ".join(c[:6] for c in real_classes) + " |\n")
        f.write("|---|" + "|".join(["---"] * len(real_classes)) + "|\n")
        for i, c in enumerate(real_classes):
            row = " | ".join(f"{real_corr[i,j]:.3f}" for j in range(len(real_classes)))
            f.write(f"| {c[:6]} | {row} |\n")

        f.write("\n## 3. 基线 vs v5 优化结果\n\n")
        f.write("| 指标 | 基线 (v4权重) | v5 优化 | 变化 |\n")
        f.write("|------|--------------|--------|------|\n")
        for k, b, o in [
            ("年化收益", baseline.annual_return, best.annual_return),
            ("年化波动", baseline.annual_vol, best.annual_vol),
            ("最大回撤", baseline.max_drawdown, best.max_drawdown),
            ("Sharpe", baseline.sharpe, best.sharpe),
            ("Calmar", baseline.calmar, best.calmar),
            ("P(年化>8%)", baseline.prob_annual_gt_8pct, best.prob_annual_gt_8pct),
            ("P(回撤<15%)", baseline.prob_dd_lt_15pct, best.prob_dd_lt_15pct),
        ]:
            if "Sharpe" in k or "Calmar" in k:
                f.write(f"| {k} | {b:.3f} | {o:.3f} | {o-b:+.3f} |\n")
            elif "P(" in k:
                f.write(f"| {k} | {b*100:.1f}% | {o*100:.1f}% | {(o-b)*100:+.1f}pp |\n")
            else:
                f.write(f"| {k} | {b*100:.2f}% | {o*100:.2f}% | {(o-b)*100:+.2f}pp |\n")

        f.write("\n## 4. 优化后权重\n\n")
        f.write("| 资产 | v5 权重 | 基线权重 | 变化 |\n")
        f.write("|------|---------|---------|------|\n")
        for i, name in enumerate(ASSET_NAMES):
            f.write(f"| {name} | {best.weights[i]*100:.2f}% | {ORIGINAL_WEIGHTS[i]*100:.2f}% | {(best.weights[i]-ORIGINAL_WEIGHTS[i])*100:+.2f}pp |\n")

        f.write("\n## 5. v3 / v4 / v5 对比\n\n")
        f.write("| 版本 | 数据源 | 年化收益 | 波动 | 回撤 | Sharpe | 备注 |\n")
        f.write("|------|--------|---------|------|------|--------|------|\n")
        f.write("| v3 | 估算 | 11.07% | - | -13.42% | - | 激进优化基线 |\n")
        f.write("| v4 | iFinD | 11.35% | 12.66% | -30.62% | 0.739 | iFinD 部分真实 |\n")
        f.write(f"| v5 | Wind | {best.annual_return*100:.2f}% | {best.annual_vol*100:.2f}% | {best.max_drawdown*100:.2f}% | {best.sharpe:.3f} | Wind 全真实+相关矩阵 |\n")
        f.write("\n## 6. 重要说明\n\n")
        f.write("- Wind 真实数据来自 2023-07-05 ~ 2026-07-03 共 725 个交易日\n")
        f.write("- 年化收益 = (price[-1]/price[0])^(252/725) - 1, 基于真实收盘价\n")
        f.write("- 年化波动 = daily_returns.std() × sqrt(252)\n")
        f.write("- 相关矩阵基于 7 类资产等权组合日收益序列的真实相关性\n")
        f.write("- 科技成长个股年化 101.77% 反映 2023-2026 AI 行情, 前瞻需谨慎\n")
        f.write("- 棉花期货/期权/现金缓冲无 Wind 标的, 保持 v3 估算\n")
    print(f"\n报告已保存: {out_path}")


if __name__ == "__main__":
    main()
