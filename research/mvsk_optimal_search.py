"""MVSK 最优方案搜索 — 三阶段系统化搜索

阶段1: 细网格 γ_s/γ_k/δ 样本内搜索 → Top 10
阶段2: 滚动窗口稳定性验证 → 筛出跨窗口稳定参数
阶段3: 样本外验证 (train/test split) → 最终最优

Usage:
    .venv\\Scripts\\python.exe research\\mvsk_optimal_search.py
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

# 阶段1 网格
GAMMA_S_FINE = [0.05, 0.08, 0.1, 0.15, 0.2, 0.3]
GAMMA_K_FINE = [0.02, 0.05, 0.08, 0.1, 0.15, 0.2]
DELTA_GRID = [2.0, 2.5, 3.0]


def _optimize_and_eval(
    R: np.ndarray,  # noqa: N806
    mu: np.ndarray,
    cov: np.ndarray,
    w_bench: np.ndarray,
    gs: float,
    gk: float,
    delta: float,
    max_te: float = 0.06,
    max_weight: float = 0.15,
) -> dict:
    """单次优化 + 评估."""
    opt = RiskBudgetOptimizer(risk_aversion=delta)
    n = len(mu)
    symbols = [f"a{i}" for i in range(n)]
    res = opt.optimize(
        symbols=symbols,
        expected_returns=mu,
        cov_matrix=cov,
        benchmark_weights=w_bench,
        max_tracking_error=max_te,
        max_weight=max_weight,
        min_weight=0.0,
        return_matrix=R,
        skew_aversion=gs,
        kurtosis_aversion=gk,
    )
    w = res.optimal_weights
    m = compute_risk_metrics(R, w)
    turn, cost = turnover_cost(w, w_bench, cost_bps=15.0)
    return {
        "gamma_s": gs,
        "gamma_k": gk,
        "delta": delta,
        "年化收益": m["年化收益"],
        "年化夏普": m["年化夏普"],
        "组合偏度": m["组合偏度"],
        "超额峰度": m["超额峰度"],
        "日CVaR95": m["日CVaR95"],
        "换手率": turn,
        "成本后净收益": m["年化收益"] - cost,
        "求解器": res.solver_status,
    }


def phase1_in_sample(R, mu, cov, w_bench):  # noqa: N806
    """阶段1: 细网格样本内搜索."""
    print(
        "\n[阶段1] 细网格样本内搜索 "
        f"({len(GAMMA_S_FINE)}×{len(GAMMA_K_FINE)}×{len(DELTA_GRID)}="
        f"{len(GAMMA_S_FINE)*len(GAMMA_K_FINE)*len(DELTA_GRID)} 组合)..."
    )

    # MV 基准
    mv = _optimize_and_eval(R, mu, cov, w_bench, 0.0, 0.0, 2.5)
    print(
        f"  MV 基准: 夏普={mv['年化夏普']:.4f} 净收益={mv['成本后净收益']:.4f} "
        f"偏度={mv['组合偏度']:.4f} 峰度={mv['超额峰度']:.4f}"
    )

    results = []
    for gs in GAMMA_S_FINE:
        for gk in GAMMA_K_FINE:
            for d in DELTA_GRID:
                r = _optimize_and_eval(R, mu, cov, w_bench, gs, gk, d)
                results.append(r)

    # Top 10 by 样本内夏普
    top10 = sorted(results, key=lambda x: x["年化夏普"], reverse=True)[:10]
    print("  Top 10 by 样本内夏普:")
    print(
        f"  {'γ_s':<8}{'γ_k':<8}{'δ':<6}{'夏普':<10}{'净收益':<10}{'偏度':<10}{'峰度':<10}"
    )
    for r in top10:
        print(
            f"  {r['gamma_s']:<8.2f}{r['gamma_k']:<8.2f}{r['delta']:<6.1f}"
            f"{r['年化夏普']:<10.4f}{r['成本后净收益']:<10.4f}"
            f"{r['组合偏度']:<10.4f}{r['超额峰度']:<10.4f}"
        )

    return top10, mv


def phase2_rolling_stability(R, w_bench, top10):  # noqa: N806
    """阶段2: 滚动窗口稳定性."""
    window = 252
    step = 84
    T = R.shape[0]  # noqa: N806
    starts = list(range(0, T - window + 1, step))
    print(f"\n[阶段2] 滚动窗口稳定性 ({len(starts)} 窗口 × {len(top10)} 参数组合)...")
    print(f"  窗口: {[(s, s+window) for s in starts]}")

    stability = []
    for params in top10:
        gs, gk, d = params["gamma_s"], params["gamma_k"], params["delta"]
        sharpes = []
        for s in starts:
            Rw = R[s : s + window]  # noqa: N806
            muw = Rw.mean(axis=0) * 252
            covw = np.cov(Rw, rowvar=False) * 252
            r = _optimize_and_eval(Rw, muw, covw, w_bench, gs, gk, d)
            sharpes.append(r["年化夏普"])
        sharpes = np.array(sharpes)
        stability.append(
            {
                "gamma_s": gs,
                "gamma_k": gk,
                "delta": d,
                "窗口夏普均值": float(sharpes.mean()),
                "窗口夏普std": float(sharpes.std()),
                "窗口夏普CV": (
                    float(sharpes.std() / abs(sharpes.mean()))
                    if sharpes.mean() != 0
                    else 99
                ),
                "窗口夏普序列": [float(x) for x in sharpes],
                "稳定": (
                    bool(sharpes.std() / abs(sharpes.mean()) < 0.5)
                    if sharpes.mean() != 0
                    else False
                ),
            }
        )

    stable = [s for s in stability if s["稳定"]]
    print(f"  稳定参数 (夏普CV<0.5): {len(stable)}/{len(top10)}")
    print(f"  {'γ_s':<8}{'γ_k':<8}{'δ':<6}{'均值':<10}{'std':<10}{'CV':<8}{'稳定':<6}")
    for s in sorted(stability, key=lambda x: x["窗口夏普CV"]):
        print(
            f"  {s['gamma_s']:<8.2f}{s['gamma_k']:<8.2f}{s['delta']:<6.1f}"
            f"{s['窗口夏普均值']:<10.4f}{s['窗口夏普std']:<10.4f}"
            f"{s['窗口夏普CV']:<8.4f}{'✓' if s['稳定'] else '✗':<6}"
        )

    return stability


def phase3_out_of_sample(R, w_bench, stability):  # noqa: N806
    """阶段3: 样本外验证."""
    T = R.shape[0]  # noqa: N806
    train_end = int(T * 0.75)  # 376 日训练
    R_train = R[:train_end]  # noqa: N806
    R_test = R[train_end:]  # noqa: N806
    print(f"\n[阶段3] 样本外验证 (训练 {train_end} 日 / 测试 {T-train_end} 日)...")

    mu_train = R_train.mean(axis=0) * 252
    cov_train = np.cov(R_train, rowvar=False) * 252
    R_test.mean(axis=0) * 252
    np.cov(R_test, rowvar=False) * 252

    # MV 基准: 训练选权, 测试评估
    opt_mv = RiskBudgetOptimizer(risk_aversion=2.5)
    n = R.shape[1]
    symbols = [f"a{i}" for i in range(n)]
    res_mv = opt_mv.optimize(
        symbols=symbols,
        expected_returns=mu_train,
        cov_matrix=cov_train,
        benchmark_weights=w_bench,
        max_tracking_error=0.06,
        max_weight=0.15,
        min_weight=0.0,
    )
    mv_test = compute_risk_metrics(R_test, res_mv.optimal_weights)
    print(
        f"  MV 样本外: 夏普={mv_test['年化夏普']:.4f} 收益={mv_test['年化收益']:.4f} "
        f"偏度={mv_test['组合偏度']:.4f} 峰度={mv_test['超额峰度']:.4f}"
    )

    # 稳定参数样本外验证
    candidates = sorted(stability, key=lambda x: x["窗口夏普CV"])[:5]  # Top 5 稳定
    oos_results = []
    for c in candidates:
        gs, gk, d = c["gamma_s"], c["gamma_k"], c["delta"]
        opt = RiskBudgetOptimizer(risk_aversion=d)
        res = opt.optimize(
            symbols=symbols,
            expected_returns=mu_train,
            cov_matrix=cov_train,
            benchmark_weights=w_bench,
            max_tracking_error=0.06,
            max_weight=0.15,
            min_weight=0.0,
            return_matrix=R_train,
            skew_aversion=gs,
            kurtosis_aversion=gk,
        )
        test_m = compute_risk_metrics(R_test, res.optimal_weights)
        oos_results.append(
            {
                "gamma_s": gs,
                "gamma_k": gk,
                "delta": d,
                "样本外夏普": test_m["年化夏普"],
                "样本外收益": test_m["年化收益"],
                "样本外偏度": test_m["组合偏度"],
                "样本外峰度": test_m["超额峰度"],
                "样本外CVaR95": test_m["日CVaR95"],
                "胜MV夏普": test_m["年化夏普"] > mv_test["年化夏普"],
                "胜MV峰度": test_m["超额峰度"] < mv_test["超额峰度"],
            }
        )

    print("\n  样本外对比 (Top 5 稳定参数):")
    print(
        f"  {'γ_s':<8}{'γ_k':<8}{'δ':<6}{'夏普':<10}{'收益':<10}{'偏度':<10}{'峰度':<10}{'胜MV':<10}"
    )
    for r in oos_results:
        beat = ("夏普✓" if r["胜MV夏普"] else "夏普✗") + (
            "峰度✓" if r["胜MV峰度"] else "峰度✗"
        )
        print(
            f"  {r['gamma_s']:<8.2f}{r['gamma_k']:<8.2f}{r['delta']:<6.1f}"
            f"{r['样本外夏普']:<10.4f}{r['样本外收益']:<10.4f}"
            f"{r['样本外偏度']:<10.4f}{r['样本外峰度']:<10.4f}{beat:<10}"
        )

    # 最终最优: 样本外夏普最大且峰度优于 MV
    valid = [r for r in oos_results if r["胜MV峰度"]]
    if valid:
        best = max(valid, key=lambda x: x["样本外夏普"])
        print("\n  *** 最终最优 ***")
        print(
            f"  γ_s={best['gamma_s']:.2f} γ_k={best['gamma_k']:.2f} δ={best['delta']:.1f}"
        )
        print(
            f"  样本外: 夏普={best['样本外夏普']:.4f} 收益={best['样本外收益']:.4f} "
            f"偏度={best['样本外偏度']:.4f} 峰度={best['样本外峰度']:.4f}"
        )
        print(
            f"  vs MV: 夏普={mv_test['年化夏普']:.4f} 收益={mv_test['年化收益']:.4f} "
            f"偏度={mv_test['组合偏度']:.4f} 峰度={mv_test['超额峰度']:.4f}"
        )
        return best, mv_test, oos_results
    else:
        print("\n  无参数在样本外峰度优于 MV, 建议用 γ_s=0.1 γ_k=0.05 (样本内最优)")
        return None, mv_test, oos_results


def main() -> int:
    if not CACHE_PATH.exists():
        print(f"缓存不存在: {CACHE_PATH}，请先跑 mvsk_ab_test_real.py")
        return 1

    cache = np.load(CACHE_PATH, allow_pickle=True)
    R = cache["returns"]  # noqa: N806
    T, N = R.shape  # noqa: N806
    print(f"加载缓存: {T} 日 × {N} 股")

    mu = R.mean(axis=0) * 252
    cov = np.cov(R, rowvar=False) * 252
    w_bench = np.ones(N) / N

    print("=" * 88)
    print("MVSK 最优方案搜索 (三阶段)")
    print("=" * 88)

    top10, mv_in = phase1_in_sample(R, mu, cov, w_bench)
    stability = phase2_rolling_stability(R, w_bench, top10)
    best, mv_oos, oos_results = phase3_out_of_sample(R, w_bench, stability)

    print("=" * 88)

    out = {
        "阶段1_top10": top10,
        "阶段2_稳定性": stability,
        "阶段3_样本外": oos_results,
        "MV样本内": mv_in,
        "MV样本外": {
            "年化夏普": mv_oos["年化夏普"],
            "年化收益": mv_oos["年化收益"],
            "组合偏度": mv_oos["组合偏度"],
            "超额峰度": mv_oos["超额峰度"],
        },
        "最终最优": best,
    }
    out_path = _PROJECT_ROOT / "research" / "mvsk_optimal_search_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
