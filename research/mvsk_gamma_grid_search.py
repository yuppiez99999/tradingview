"""MVSK γ_s/γ_k 网格搜索 — 找收益-高阶矩帕累托前沿与倒 U 型峰值

复用 research/mvsk_real_data_cache.npz 真实 A 股数据,
沿 γ_s × γ_k 网格搜索, 定位论文发现一的倒 U 型曲线峰值.

Usage:
    .venv\\Scripts\\python.exe research\\mvsk_gamma_grid_search.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.mvsk_ab_test import compute_risk_metrics, turnover_cost  # noqa: E402
from utils.risk_budget_optimizer import RiskBudgetOptimizer  # noqa: E402

CACHE_PATH = _PROJECT_ROOT / "research" / "mvsk_real_data_cache.npz"

# γ 网格
GAMMA_S_GRID = [0.1, 0.3, 0.5, 0.8, 1.0, 1.5, 2.0]
GAMMA_K_GRID = [0.05, 0.1, 0.2, 0.3, 0.5, 0.8, 1.0]


def run_grid(
    R: np.ndarray,  # noqa: N806
    mu: np.ndarray,
    cov: np.ndarray,
    w_bench: np.ndarray,
) -> list[dict]:
    """跑 γ_s × γ_k 网格, 返回每个组合的指标."""
    opt = RiskBudgetOptimizer(risk_aversion=2.5)
    n = len(mu)
    symbols = [f"asset_{i:03d}" for i in range(n)]
    results = []

    for gs in GAMMA_S_GRID:
        for gk in GAMMA_K_GRID:
            res = opt.optimize(
                symbols=symbols,
                expected_returns=mu,
                cov_matrix=cov,
                benchmark_weights=w_bench,
                max_tracking_error=0.06,
                max_weight=0.15,
                min_weight=0.0,
                return_matrix=R,
                skew_aversion=gs,
                kurtosis_aversion=gk,
            )
            w = res.optimal_weights
            m = compute_risk_metrics(R, w)
            turn, cost = turnover_cost(w, w_bench, cost_bps=15.0)
            net = m["年化收益"] - cost
            results.append(
                {
                    "gamma_s": gs,
                    "gamma_k": gk,
                    "年化收益": m["年化收益"],
                    "年化夏普": m["年化夏普"],
                    "组合偏度": m["组合偏度"],
                    "超额峰度": m["超额峰度"],
                    "日CVaR95": m["日CVaR95"],
                    "换手率": turn,
                    "成本后净收益": net,
                    "求解器": res.solver_status,
                }
            )

    return results


def print_heatmap(results: list[dict], metric: str, title: str) -> None:
    """打印 γ_s × γ_k 热力图表格."""
    print(f"\n### {title}")
    # 表头
    header = f"{'γ_s＼γ_k':<10}" + "".join(f"{gk:>10.2f}" for gk in GAMMA_K_GRID)
    print(header)
    print("-" * (10 + 10 * len(GAMMA_K_GRID)))
    for gs in GAMMA_S_GRID:
        row = f"{gs:<10.1f}"
        for gk in GAMMA_K_GRID:
            val = next(
                r[metric] for r in results if r["gamma_s"] == gs and r["gamma_k"] == gk
            )
            row += f"{val:>10.4f}"
        print(row)


def print_pareto(results: list[dict]) -> None:
    """打印帕累托前沿 (收益 vs 峰度)."""
    print("\n### 帕累托前沿 (成本后净收益 vs 超额峰度, 非劣解)")
    # 简单帕累托: 找不被其他解支配的点 (收益高且峰度低)
    pareto = []
    for r in results:
        dominated = False
        for s in results:
            if s is r:
                continue
            if (
                s["成本后净收益"] >= r["成本后净收益"]
                and s["超额峰度"] <= r["超额峰度"]
                and (
                    s["成本后净收益"] > r["成本后净收益"]
                    or s["超额峰度"] < r["超额峰度"]
                )
            ):
                dominated = True
                break
        if not dominated:
            pareto.append(r)

    pareto.sort(key=lambda x: x["超额峰度"])
    print(
        f"{'γ_s':<8}{'γ_k':<8}{'净收益':<12}{'夏普':<10}{'偏度':<10}{'峰度':<10}{'CVaR95':<12}"
    )
    print("-" * 70)
    for r in pareto:
        print(
            f"{r['gamma_s']:<8.1f}{r['gamma_k']:<8.2f}{r['成本后净收益']:<12.4f}"
            f"{r['年化夏普']:<10.4f}{r['组合偏度']:<10.4f}{r['超额峰度']:<10.4f}"
            f"{r['日CVaR95']:<12.4f}"
        )


def find_optima(results: list[dict]) -> None:
    """找各种目标下的最优组合."""
    print("\n### 最优组合")

    best_sharpe = max(results, key=lambda r: r["年化夏普"])
    best_net = max(results, key=lambda r: r["成本后净收益"])
    best_skew = max(results, key=lambda r: r["组合偏度"])
    best_kurt = min(results, key=lambda r: r["超额峰度"])

    # 平衡点: 偏度>0.3 且 峰度<6 中净收益最高
    balanced = [r for r in results if r["组合偏度"] > 0.3 and r["超额峰度"] < 6]
    best_balanced = max(balanced, key=lambda r: r["成本后净收益"]) if balanced else None

    def show(label, r):
        print(
            f"  {label}: γ_s={r['gamma_s']:.1f} γ_k={r['gamma_k']:.2f} → "
            f"净收益={r['成本后净收益']:.4f} 夏普={r['年化夏普']:.4f} "
            f"偏度={r['组合偏度']:.4f} 峰度={r['超额峰度']:.4f}"
        )

    show("最大夏普", best_sharpe)
    show("最大净收益", best_net)
    show("最大偏度  ", best_skew)
    show("最小峰度  ", best_kurt)
    if best_balanced:
        show("平衡点(偏度>0.3&峰度<6)", best_balanced)
    else:
        print("  平衡点(偏度>0.3&峰度<6): 无满足条件的组合")


def print_u_curve(results: list[dict]) -> None:
    """打印倒 U 型曲线截面 (固定 γ_k=0.1, 看 γ_s 变化)."""
    print("\n### 倒 U 型曲线 (固定 γ_k=0.1, γ_s 扫描)")
    print(f"{'γ_s':<8}{'净收益':<12}{'夏普':<10}{'偏度':<10}{'峰度':<10}")
    print("-" * 50)
    for gs in GAMMA_S_GRID:
        r = next(r for r in results if r["gamma_s"] == gs and r["gamma_k"] == 0.1)
        print(
            f"{gs:<8.1f}{r['成本后净收益']:<12.4f}{r['年化夏普']:<10.4f}"
            f"{r['组合偏度']:<10.4f}{r['超额峰度']:<10.4f}"
        )


def main() -> int:
    if not CACHE_PATH.exists():
        print(f"缓存不存在: {CACHE_PATH}")
        print("请先运行: python research/mvsk_ab_test_real.py")
        return 1

    cache = np.load(CACHE_PATH, allow_pickle=True)
    R = cache["returns"]  # noqa: N806
    codes = list(cache["codes"])
    T, N = R.shape  # noqa: N806
    print(f"加载缓存: {T} 日 × {N} 股 ({len(codes)} 只)")

    mu = R.mean(axis=0) * 252
    cov = np.cov(R, rowvar=False) * 252
    w_bench = np.ones(N) / N

    print(
        f"\n网格搜索: {len(GAMMA_S_GRID)} × {len(GAMMA_K_GRID)} = {len(GAMMA_S_GRID)*len(GAMMA_K_GRID)} 组合..."
    )
    results = run_grid(R, mu, cov, w_bench)
    print(f"完成 {len(results)} 组合")

    print("\n" + "=" * 88)
    print("MVSK γ_s/γ_k 网格搜索 (真实 A 股 30 股 × 502 日)")
    print("=" * 88)

    print_heatmap(results, "成本后净收益", "成本后净收益 热力图")
    print_heatmap(results, "年化夏普", "年化夏普 热力图")
    print_heatmap(results, "组合偏度", "组合偏度 热力图")
    print_heatmap(results, "超额峰度", "超额峰度 热力图")
    print_u_curve(results)
    find_optima(results)
    print_pareto(results)
    print("=" * 88)

    out_path = _PROJECT_ROOT / "research" / "mvsk_gamma_grid_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "网格": results,
                "gamma_s_grid": GAMMA_S_GRID,
                "gamma_k_grid": GAMMA_K_GRID,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\n结果已保存: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
