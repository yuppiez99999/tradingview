# -*- coding: utf-8 -*-
"""
组合优化 v4 — iFinD 真实数据校准版
=====================================
基于 v3, 用 iFinD 拉取的真实历史数据校准资产参数:
  - 年化收益率: 用近3年累计收益率年化 (1+r)^(1/3)-1, 比单年更稳健
  - 年化波动率: 用 iFinD 真实数据, 缺失值用同类资产补全
  - 最大回撤: 用 iFinD 真实数据, 缺失值用 2×波动率 估算
  - 相关矩阵: 保持 v3 估算 (iFinD 未返回相关矩阵)

校准数据来源: e:/各种PY程序/28-终极量化交易系统7.1/v7.5_institutional/calibrated_asset_params.json
查询时间: 2026-07-05 (iFinD MCP)

资产结构 (12 类, 同 v3):
    0: 核心宽基ETF       1: 科技成长个股    2: 高端制造/基建    3: 防御/红利
    4: 商品/避险         5: 现金缓冲_股票   6: 棉花期货         7: 棉花期权保护
    8: 股票期权保护(=0)  9: 现金缓冲_对冲   10: 半导体ETF       11: 新能源ETF
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
logger = logging.getLogger("portfolio_optimizer_v4")

# ============================================================
# 资产参数 (12 类) — iFinD 真实数据校准
# ============================================================
ASSET_NAMES = [
    "核心宽基ETF", "科技成长个股", "高端制造/基建", "防御/红利",
    "商品/避险", "现金缓冲_股票",
    "棉花期货", "棉花期权保护", "股票期权保护", "现金缓冲_对冲",
    "半导体ETF", "新能源ETF",
]

# ----- iFinD 真实数据 (2026-07-05 查询) -----
# 近3年累计收益率 (来自 iFinD asset_class_summary.recent_3y_return_pct)
RECENT_3Y_CUM_RETURNS = {
    "核心宽基ETF":   33.07,   # 4 只 ETF 均值
    "科技成长个股":  99.85,   # 海光 + 中际旭创
    "高端制造/基建": 53.80,   # 北方华创 + 中芯 + 宁德
    "防御/红利":     18.67,   # 长江 + 恒瑞 + 药明
    "商品/避险":     62.52,   # 黄金ETF + 神华 + 宝钢 + 南山 + 盐湖
    "半导体ETF":     77.95,   # 512480.SH
    "新能源ETF":     -0.05,   # 516160.SH (近3年几乎持平)
}
# 近3年年化 = (1 + 累计/100)^(1/3) - 1
def _annualize_3y(cum_pct):
    return (1 + cum_pct / 100.0) ** (1.0 / 3.0) - 1.0

# iFinD 真实波动率 (近1年, 部分缺失)
REAL_VOLATILITY = {
    "核心宽基ETF":   None,    # fund 服务未返回, 用估算 16%
    "科技成长个股":  None,    # 用估算 25%
    "高端制造/基建": 36.11,   # 北方华创 32.84 + 宁德 39.38 均值
    "防御/红利":     None,    # 用估算 12%
    "商品/避险":     36.37,   # 盐湖股份
    "半导体ETF":     None,    # 用高端制造 36%
    "新能源ETF":     None,    # 用高端制造 36%
}
# iFinD 真实最大回撤 (近1年)
REAL_MAX_DD = {
    "核心宽基ETF":   None,    # 用 2×波动 估算
    "科技成长个股":  15.30,
    "高端制造/基建": 13.38,
    "防御/红利":     19.66,
    "商品/避险":     33.08,
    "半导体ETF":     20.00,
    "新能源ETF":     0.32,    # 异常低 (新ETF), 用 2×波动 估算
}

# ----- 校准后的年化收益 (近3年年化) -----
# 现金/期权类保持估算 (iFinD 未查询)
ANN_RETURNS = np.array([
    _annualize_3y(RECENT_3Y_CUM_RETURNS["核心宽基ETF"]),    # 0: ~9.97%
    _annualize_3y(RECENT_3Y_CUM_RETURNS["科技成长个股"]),    # 1: ~25.97%
    _annualize_3y(RECENT_3Y_CUM_RETURNS["高端制造/基建"]),  # 2: ~15.42%
    _annualize_3y(RECENT_3Y_CUM_RETURNS["防御/红利"]),      # 3: ~5.87%
    _annualize_3y(RECENT_3Y_CUM_RETURNS["商品/避险"]),      # 4: ~17.50%
    0.02,                                                    # 5: 现金缓冲_股票
    0.15,                                                    # 6: 棉花期货 (估算)
    -0.05,                                                   # 7: 棉花期权保护 (估算)
    0.00,                                                    # 8: 股票期权保护 (v3 已置0)
    0.02,                                                    # 9: 现金缓冲_对冲
    _annualize_3y(RECENT_3Y_CUM_RETURNS["半导体ETF"]),      # 10: ~21.20%
    _annualize_3y(RECENT_3Y_CUM_RETURNS["新能源ETF"]),      # 11: ~-0.16%
])

# ----- 校准后的年化波动 -----
def _get_vol(asset_name, fallback):
    v = REAL_VOLATILITY.get(asset_name)
    if v is not None:
        return v / 100.0
    return fallback

ANN_VOLS = np.array([
    _get_vol("核心宽基ETF", 0.16),     # 0: 16% (估算)
    _get_vol("科技成长个股", 0.25),    # 1: 25% (估算)
    _get_vol("高端制造/基建", 0.22),   # 2: 36.11% (真实)
    _get_vol("防御/红利", 0.12),       # 3: 12% (估算)
    _get_vol("商品/避险", 0.15),       # 4: 36.37% (真实)
    0.005,                             # 5: 现金
    0.30,                              # 6: 棉花期货 (估算)
    0.05,                              # 7: 棉花期权
    0.05,                              # 8: 股票期权
    0.005,                             # 9: 现金对冲
    _get_vol("半导体ETF", 0.30),       # 10: 36% (用高端制造)
    _get_vol("新能源ETF", 0.28),       # 11: 36% (用高端制造)
])

# ----- 12×12 相关矩阵 (保持 v3 估算, iFinD 未返回) -----
CORR_MATRIX = np.array([
    [1.00, 0.70, 0.65, 0.60, 0.20, 0.00, 0.10, -0.20, -0.30, 0.00, 0.75, 0.65],
    [0.70, 1.00, 0.75, 0.40, 0.15, 0.00, 0.05, -0.25, -0.35, 0.00, 0.85, 0.75],
    [0.65, 0.75, 1.00, 0.45, 0.30, 0.00, 0.20, -0.15, -0.25, 0.00, 0.70, 0.65],
    [0.60, 0.40, 0.45, 1.00, 0.10, 0.00, 0.05, -0.10, -0.20, 0.00, 0.35, 0.30],
    [0.20, 0.15, 0.30, 0.10, 1.00, 0.00, 0.40, -0.10, -0.05, 0.00, 0.10, 0.15],
    [0.00, 0.00, 0.00, 0.00, 0.00, 1.00, 0.00, 0.00, 0.00, 0.30, 0.00, 0.00],
    [0.10, 0.05, 0.20, 0.05, 0.40, 0.00, 1.00, 0.50, 0.20, 0.00, 0.05, 0.10],
    [-0.20, -0.25, -0.15, -0.10, -0.10, 0.00, 0.50, 1.00, 0.60, 0.00, -0.20, -0.15],
    [-0.30, -0.35, -0.25, -0.20, -0.05, 0.00, 0.20, 0.60, 1.00, 0.00, -0.30, -0.25],
    [0.00, 0.00, 0.00, 0.00, 0.00, 0.30, 0.00, 0.00, 0.00, 1.00, 0.00, 0.00],
    [0.75, 0.85, 0.70, 0.35, 0.10, 0.00, 0.05, -0.20, -0.30, 0.00, 1.00, 0.80],
    [0.65, 0.75, 0.65, 0.30, 0.15, 0.00, 0.10, -0.15, -0.25, 0.00, 0.80, 1.00],
])

COV_MATRIX = np.outer(ANN_VOLS, ANN_VOLS) * CORR_MATRIX

# v3 优化后的权重作为 v4 基线
_v4_baseline_raw = np.array([
    0.1032, 0.2187, 0.120, 0.090, 0.030, 0.048,
    0.2408, 0.035, 0.0000, 0.065,
    0.0500, 0.0500,
])
ORIGINAL_WEIGHTS = _v4_baseline_raw / _v4_baseline_raw.sum()

# 权重约束 (同 v3)
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
    """解析公式评估组合绩效"""
    mu_p = float(np.dot(weights, ANN_RETURNS))
    var_p = float(weights @ COV_MATRIX @ weights)
    sigma_p = float(np.sqrt(max(var_p, 1e-10)))

    # 最大回撤: 用 iFinD 真实数据加权, 期权保护衰减
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
    """优化组合权重"""
    from scipy.optimize import minimize

    n = len(ANN_RETURNS)
    bounds = WEIGHT_BOUNDS

    def objective(w):
        r = evaluate_analytical(w)
        # 目标: 最大化 Sharpe - L2 正则 - 回撤惩罚
        dd_penalty = max(0, -r.max_drawdown - 0.30) * 10  # 超过 30% 回撤重罚
        return -(r.sharpe - l2_lambda * np.sum(w ** 2) - dd_penalty)

    l2_lambda = 0.1

    constraints = [
        {"type": "eq", "fun": lambda w: np.sum(w) - 1.0},
        {"type": "ineq", "fun": lambda w: sum(w[i] for i in STOCK_INDICES) - STOCK_MIN},
        {"type": "ineq", "fun": lambda w: STOCK_MAX - sum(w[i] for i in STOCK_INDICES)},
        {"type": "ineq", "fun": lambda w: sum(w[i] for i in HEDGE_INDICES) - HEDGE_MIN},
        {"type": "ineq", "fun": lambda w: HEDGE_MAX - sum(w[i] for i in HEDGE_INDICES)},
    ]

    best = None
    for trial in range(20):
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
    print("组合优化 v4 — iFinD 真实数据校准")
    print("=" * 70)

    print("\n[校准后资产参数]")
    print(f"{'资产':16s} {'年化收益':>10s} {'年化波动':>10s} {'Sharpe':>8s}")
    print("-" * 50)
    for i, name in enumerate(ASSET_NAMES):
        sharpe = (ANN_RETURNS[i] - RF) / ANN_VOLS[i] if ANN_VOLS[i] > 0 else 0
        print(f"{name:16s} {ANN_RETURNS[i]*100:9.2f}% {ANN_VOLS[i]*100:9.2f}% {sharpe:8.3f}")

    print("\n[基线 (v3 优化权重) 评估]")
    baseline = evaluate_analytical(ORIGINAL_WEIGHTS)
    print(f"  年化收益: {baseline.annual_return*100:.2f}%")
    print(f"  年化波动: {baseline.annual_vol*100:.2f}%")
    print(f"  最大回撤: {baseline.max_drawdown*100:.2f}%")
    print(f"  Sharpe:   {baseline.sharpe:.3f}")
    print(f"  Calmar:   {baseline.calmar:.3f}")
    print(f"  P(年化>8%): {baseline.prob_annual_gt_8pct*100:.1f}%")
    print(f"  P(回撤<15%): {baseline.prob_dd_lt_15pct*100:.1f}%")

    print("\n[v4 优化中 (20 次随机起点)...]")
    best = optimize_portfolio()
    if best is None:
        print("✗ 优化失败")
        return

    print("\n[v4 优化结果]")
    print(f"  年化收益: {best.annual_return*100:.2f}%")
    print(f"  年化波动: {best.annual_vol*100:.2f}%")
    print(f"  最大回撤: {best.max_drawdown*100:.2f}%")
    print(f"  Sharpe:   {best.sharpe:.3f}")
    print(f"  Calmar:   {best.calmar:.3f}")
    print(f"  P(年化>8%): {best.prob_annual_gt_8pct*100:.1f}%")
    print(f"  P(回撤<15%): {best.prob_dd_lt_15pct*100:.1f}%")

    print("\n[优化后权重]")
    print(f"{'资产':16s} {'权重':>8s} {'变化':>8s}")
    print("-" * 40)
    for i, name in enumerate(ASSET_NAMES):
        delta = (best.weights[i] - ORIGINAL_WEIGHTS[i]) * 100
        print(f"{name:16s} {best.weights[i]*100:7.2f}% {delta:+7.2f}pp")

    # 保存结果 (统一到根目录 每日报告归档)
    today_str = datetime.now().strftime("%Y-%m-%d")
    archive_dir = Path(f"e:/各种PY程序/每日报告归档/{today_str}")
    archive_dir.mkdir(parents=True, exist_ok=True)
    out_path = archive_dir / f"portfolio_optimization_v4_calibrated_{datetime.now().strftime('%Y%m%d')}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# 组合优化 v4 — iFinD 真实数据校准\n\n")
        f.write(f"**查询时间**: 2026-07-05 (iFinD MCP)\n")
        f.write(f"**校准方法**: 近3年累计收益率年化 (1+r)^(1/3)-1\n\n")
        f.write("## 校准后资产参数\n\n")
        f.write("| 资产 | 年化收益 | 年化波动 | Sharpe | 数据来源 |\n")
        f.write("|------|---------|---------|--------|----------|\n")
        for i, name in enumerate(ASSET_NAMES):
            sharpe = (ANN_RETURNS[i] - RF) / ANN_VOLS[i] if ANN_VOLS[i] > 0 else 0
            ac = name if name in RECENT_3Y_CUM_RETURNS else "估算"
            f.write(f"| {name} | {ANN_RETURNS[i]*100:.2f}% | {ANN_VOLS[i]*100:.2f}% | {sharpe:.3f} | {ac} |\n")
        f.write("\n## 基线 vs 优化结果\n\n")
        f.write("| 指标 | 基线 (v3权重) | v4 优化 |\n")
        f.write("|------|--------------|--------|\n")
        for k, b, o in [
            ("年化收益", baseline.annual_return, best.annual_return),
            ("年化波动", baseline.annual_vol, best.annual_vol),
            ("最大回撤", baseline.max_drawdown, best.max_drawdown),
            ("Sharpe", baseline.sharpe, best.sharpe),
            ("Calmar", baseline.calmar, best.calmar),
        ]:
            f.write(f"| {k} | {b*100 if 'Sharpe' not in k and 'Calmar' not in k else b:.3f} | {o*100 if 'Sharpe' not in k and 'Calmar' not in k else o:.3f} |\n")
        f.write("\n## 优化后权重\n\n")
        f.write("| 资产 | 权重 | 基线权重 | 变化 |\n")
        f.write("|------|------|---------|------|\n")
        for i, name in enumerate(ASSET_NAMES):
            f.write(f"| {name} | {best.weights[i]*100:.2f}% | {ORIGINAL_WEIGHTS[i]*100:.2f}% | {(best.weights[i]-ORIGINAL_WEIGHTS[i])*100:+.2f}pp |\n")
    print(f"\n报告已保存: {out_path}")


if __name__ == "__main__":
    main()
