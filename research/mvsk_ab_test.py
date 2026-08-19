"""MVSK vs MV A/B 对比实验 (P1: YAND 启发的高阶矩优化验证)

目的:
    在合成 A 股特征收益数据上, 对比纯均值-方差 (MV) 与
    均值-方差-偏度-峰度 (MVSK) 优化的组合表现差异.
    验证 "高阶矩优化在中等野心区间有增量价值" 这一论文发现.

合成数据设计 (模拟 A 股涨跌停截断效应):
    - 混合正态: 90% N(μ, σ²) + 10% N(μ - 3σ, (2σ)²)
    - 主体正态 + 少数大跌冲击 → 负偏度 + 高峰度 (肥尾)
    - 这正是 YAND 论文指出的 MV 框架盲区

对比指标:
    - 组合偏度 / 超额峰度 (高阶矩优化目标)
    - VaR95 / CVaR95 (尾部风险)
    - 年化夏普 / 年化收益 / 年化波动
    - 换手率 / 交易成本 / 成本后净收益 (A 股 T+1 约束)

参考:
    - Yau et al. YAND (Yau's Affine-Normal Descent) 2026
    - Kraus & Litzenberger (1976) 偏度定价
    - Markowitz (1952) 均值-方差框架

Usage:
    .venv\\Scripts\\python.exe research\\mvsk_ab_test.py
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.risk_budget_optimizer import RiskBudgetOptimizer  # noqa: E402

# ============================================================
# 合成收益数据生成
# ============================================================

def generate_a_share_returns(
    n_assets: int = 30,
    n_days: int = 504,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """生成 A 股特征收益矩阵.

    混合正态: 90% 正常 regime + 10% 大跌 regime
    → 负偏度 + 肥尾 (超额峰度 > 0), 模拟涨跌停截断.

    Returns:
        returns: T×N 日收益矩阵
        mu: N 维年化预期收益
        cov: N×N 年化协方差
    """
    rng = np.random.RandomState(seed)

    # 每资产年化参数
    ann_mu = rng.uniform(0.05, 0.20, n_assets)  # 年化收益 5%-20%
    ann_vol = rng.uniform(0.20, 0.45, n_assets)  # 年化波动 20%-45%
    dt = 1.0 / 252
    mu_d = ann_mu * dt
    vol_d = ann_vol * math.sqrt(dt)

    # 生成日收益: 混合正态制造负偏度 + 肥尾
    R = np.zeros((n_days, n_assets))  # noqa: N806
    for t in range(n_days):
        regime = rng.rand(n_assets) < 0.10  # 10% 概率进入大跌 regime
        normal = mu_d + vol_d * rng.randn(n_assets)
        crash = mu_d - 3.0 * vol_d + 2.0 * vol_d * rng.randn(n_assets)
        R[t] = np.where(regime, crash, normal)

    # 年化 mu 与 cov
    mu_ann = R.mean(axis=0) * 252
    cov_ann = np.cov(R, rowvar=False) * 252

    return R, mu_ann, cov_ann


# ============================================================
# 风险指标计算
# ============================================================

def compute_risk_metrics(returns: np.ndarray, weights: np.ndarray) -> dict[str, float]:
    """计算组合风险指标 (基于历史收益)."""
    rp = returns @ weights
    ann = 252

    mean_d = float(rp.mean())
    std_d = float(rp.std())
    skew = float(((rp - mean_d) ** 3).mean() / std_d**3) if std_d > 0 else 0.0
    exkurt = float(((rp - mean_d) ** 4).mean() / std_d**4 - 3.0) if std_d > 0 else 0.0

    # VaR95 / CVaR95 (日频)
    var95 = float(np.percentile(rp, 5))  # 左尾 5%
    cvar95 = float(rp[rp <= var95].mean()) if np.any(rp <= var95) else var95

    return {
        "年化收益": mean_d * ann,
        "年化波动": std_d * math.sqrt(ann),
        "年化夏普": (mean_d * ann) / (std_d * math.sqrt(ann)) if std_d > 0 else 0.0,
        "组合偏度": skew,
        "超额峰度": exkurt,
        "日VaR95": var95,
        "日CVaR95": cvar95,
    }


def turnover_cost(
    w_new: np.ndarray,
    w_old: np.ndarray,
    cost_bps: float = 15.0,
) -> tuple[float, float]:
    """A 股交易成本 (T+1, 双边).

    Args:
        w_new: 新权重
        w_old: 旧权重 (基准)
        cost_bps: 单边成本 bp (印花税+佣金+滑点, 默认 15bp)

    Returns:
        (换手率, 成本占比)
    """
    turnover = 0.5 * float(np.abs(w_new - w_old).sum())
    cost = turnover * cost_bps / 10000.0
    return turnover, cost


# ============================================================
# A/B 实验主流程
# ============================================================

@dataclass
class ExperimentResult:
    label: str
    weights: np.ndarray
    metrics: dict[str, float]
    turnover: float
    trade_cost: float
    net_return: float
    solver: str


def run_experiment(
    label: str,
    R: np.ndarray,  # noqa: N806
    mu: np.ndarray,
    cov: np.ndarray,
    w_bench: np.ndarray,
    skew_aversion: float = 0.0,
    kurtosis_aversion: float = 0.0,
    max_te: float = 0.06,
) -> ExperimentResult:
    """跑单组优化实验."""
    opt = RiskBudgetOptimizer(risk_aversion=2.5)
    n = len(mu)
    symbols = [f"asset_{i:03d}" for i in range(n)]

    result = opt.optimize(
        symbols=symbols,
        expected_returns=mu,
        cov_matrix=cov,
        benchmark_weights=w_bench,
        max_tracking_error=max_te,
        max_weight=0.15,
        min_weight=0.0,
        return_matrix=R if (skew_aversion != 0 or kurtosis_aversion != 0) else None,
        skew_aversion=skew_aversion,
        kurtosis_aversion=kurtosis_aversion,
    )

    w = result.optimal_weights
    metrics = compute_risk_metrics(R, w)
    turn, cost = turnover_cost(w, w_bench, cost_bps=15.0)
    net = metrics["年化收益"] - cost

    return ExperimentResult(
        label=label,
        weights=w,
        metrics=metrics,
        turnover=turn,
        trade_cost=cost,
        net_return=net,
        solver=result.solver_status,
    )


def print_comparison(results: list[ExperimentResult]) -> None:
    """打印对比表格."""
    print("\n" + "=" * 88)
    print("MVSK vs MV A/B 对比 (P1: YAND 启发高阶矩优化)")
    print("=" * 88)

    cols = ["年化收益", "年化波动", "年化夏普", "组合偏度", "超额峰度", "日VaR95", "日CVaR95"]
    header = f"{'指标':<14}" + "".join(f"{r.label:>16}" for r in results)
    print(header)
    print("-" * 88)

    for col in cols:
        row = f"{col:<14}"
        for r in results:
            val = r.metrics[col]
            row += f"{val:>16.4f}"
        print(row)

    # 换手与成本
    print("-" * 88)
    row = f"{'换手率':<14}" + "".join(f"{r.turnover:>16.4f}" for r in results)
    print(row)
    row = f"{'交易成本(年化)':<14}" + "".join(f"{r.trade_cost * 252:>16.4f}" for r in results)
    print(row)
    row = f"{'成本后净收益':<14}" + "".join(f"{r.net_return:>16.4f}" for r in results)
    print(row)
    row = f"{'求解器':<14}" + "".join(f"{r.solver:>16}" for r in results)
    print(row)
    print("=" * 88)

    # 增量分析
    if len(results) >= 2:
        base = results[0]
        print(f"\n增量分析 (vs {base.label}):")
        for r in results[1:]:
            d_skew = r.metrics["组合偏度"] - base.metrics["组合偏度"]
            d_kurt = r.metrics["超额峰度"] - base.metrics["超额峰度"]
            d_cvar = r.metrics["日CVaR95"] - base.metrics["日CVaR95"]
            d_net = r.net_return - base.net_return
            print(
                f"  {r.label}: Δ偏度={d_skew:+.4f}  Δ峰度={d_kurt:+.4f}  "
                f"ΔCVaR95={d_cvar:+.4f}  Δ净收益={d_net:+.4f}"
            )


def main() -> int:
    print("生成合成 A 股收益数据 (30 资产 × 504 日)...")
    R, mu, cov = generate_a_share_returns(n_assets=30, n_days=504, seed=42)  # noqa: N806

    # 基准权重: 等权
    n = len(mu)
    w_bench = np.ones(n) / n

    # 数据诊断
    rp_bench = R @ w_bench
    bench_skew = float(((rp_bench - rp_bench.mean()) ** 3).mean() / rp_bench.std() ** 3)
    bench_kurt = float(((rp_bench - rp_bench.mean()) ** 4).mean() / rp_bench.std() ** 4 - 3.0)
    print(f"基准组合偏度={bench_skew:.4f}  超额峰度={bench_kurt:.4f}  (期望: 负偏度+肥尾)")

    # 三组实验
    results = [
        run_experiment("MV(纯均值方差)", R, mu, cov, w_bench, 0.0, 0.0),
        run_experiment("MVSK-轻(γs0.5γk0.1)", R, mu, cov, w_bench, 0.5, 0.1),
        run_experiment("MVSK-重(γs1.5γk0.5)", R, mu, cov, w_bench, 1.5, 0.5),
    ]

    print_comparison(results)

    # 保存结果
    out = {
        "基准组合偏度": bench_skew,
        "基准组合超额峰度": bench_kurt,
        "实验": [
            {
                "label": r.label,
                "metrics": r.metrics,
                "换手率": r.turnover,
                "交易成本年化": r.trade_cost * 252,
                "成本后净收益": r.net_return,
                "求解器": r.solver,
            }
            for r in results
        ],
    }
    out_path = _PROJECT_ROOT / "research" / "mvsk_ab_test_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
