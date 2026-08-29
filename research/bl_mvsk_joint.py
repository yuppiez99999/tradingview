"""BL+MVSK 联合优化 — P2 核心实现

三阶段搜索发现样本外失效根因是 μ 估计不稳定 (Markowitz's Curse).
本脚本用 Black-Litterman 后验 μ 替换历史均值 μ, 再进 MVSK 优化器,
验证 BL+MVSK 联合是否改善样本外表现.

对比 5 方案 (train 376 / test 126):
    1. 等权基准
    2. MV(历史μ)       — 纯均值方差, 历史均值 μ
    3. MVSK(历史μ)     — MVSK, 历史均值 μ
    4. BL+MV(后验μ)    — 纯均值方差, BL 后验 μ
    5. BL+MVSK(后验μ)  — MVSK, BL 后验 μ  ← 本脚本主角

Usage:
    .venv\\Scripts\\python.exe research\\bl_mvsk_joint.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.mvsk_ab_test import compute_risk_metrics  # noqa: E402
from utils.black_litterman_optimizer import BlackLittermanOptimizer, View  # noqa: E402
from utils.risk_budget_optimizer import RiskBudgetOptimizer  # noqa: E402

CACHE_PATH = _PROJECT_ROOT / "research" / "mvsk_real_data_cache.npz"

# MVSK 参数 (γ 网格搜索最优)
GAMMA_S = 0.1
GAMMA_K = 0.05
DELTA = 2.5
MAX_TE = 0.06
MAX_WEIGHT = 0.15

# BL 观点参数
MOMENTUM_WINDOW = 60  # 动量信号回看窗口
VIEW_CONFIDENCE = 0.3  # 弱观点, 让 BL 主要靠市场均衡
TAU = 0.05


def build_bl_views(
    codes: list[str],
    R_train: np.ndarray,  # noqa: N806
) -> list[View]:
    """用动量信号构造 BL 观点.

    每只股票: 绝对观点, expected_return = 过去 MOMENTUM_WINDOW 日年化收益.
    confidence=0.3 (弱观点), 让 BL 把历史 μ 和市场均衡融合.
    """
    window = min(MOMENTUM_WINDOW, R_train.shape[0])
    recent = R_train[-window:]
    ann_momentum = recent.mean(axis=0) * 252  # 年化动量

    views = []
    for i, code in enumerate(codes):
        views.append(
            View(
                type="absolute",
                assets=[code],
                weights=[1.0],
                expected_return=float(ann_momentum[i]),
                confidence=VIEW_CONFIDENCE,
            )
        )
    return views


def bl_posterior_mu(
    codes: list[str],
    R_train: np.ndarray,  # noqa: N806
    cov: np.ndarray,
    w_mkt: np.ndarray,
) -> np.ndarray:
    """用 Black-Litterman 算后验 μ."""
    bl = BlackLittermanOptimizer(risk_aversion=DELTA, tau=TAU)
    views = build_bl_views(codes, R_train)
    result = bl.optimize(
        assets=codes,
        market_weights=w_mkt,
        cov_matrix=cov,
        views=views,
        risk_free_rate=0.03,
    )
    return result.posterior_returns


def optimize_weights(
    mu: np.ndarray,
    cov: np.ndarray,
    w_bench: np.ndarray,
    R: np.ndarray | None = None,  # noqa: N806
    gs: float = 0.0,
    gk: float = 0.0,
) -> np.ndarray:
    """用 risk_budget_optimizer 优化权重."""
    opt = RiskBudgetOptimizer(risk_aversion=DELTA)
    n = len(mu)
    symbols = [f"a{i}" for i in range(n)]
    res = opt.optimize(
        symbols=symbols,
        expected_returns=mu,
        cov_matrix=cov,
        benchmark_weights=w_bench,
        max_tracking_error=MAX_TE,
        max_weight=MAX_WEIGHT,
        min_weight=0.0,
        return_matrix=R,
        skew_aversion=gs,
        kurtosis_aversion=gk,
    )
    return res.optimal_weights


def main() -> int:
    if not CACHE_PATH.exists():
        print(f"缓存不存在: {CACHE_PATH}，请先跑 mvsk_ab_test_real.py")
        return 1

    cache = np.load(CACHE_PATH, allow_pickle=True)
    R = cache["returns"]  # noqa: N806
    codes = [str(c) for c in cache["codes"]]
    T, N = R.shape  # noqa: N806
    print(f"加载缓存: {T} 日 × {N} 股")

    # train/test split
    train_end = int(T * 0.75)
    R_train = R[:train_end]  # noqa: N806
    R_test = R[train_end:]  # noqa: N806
    print(f"训练 {train_end} 日 / 测试 {T-train_end} 日")

    # 训练期估计
    mu_hist = R_train.mean(axis=0) * 252
    cov_train = np.cov(R_train, rowvar=False) * 252
    w_bench = np.ones(N) / N

    # BL 后验 μ
    print("构造 BL 后验 μ (动量信号观点, confidence=0.3)...")
    mu_bl = bl_posterior_mu(codes, R_train, cov_train, w_bench)

    # 诊断: μ 对比
    print("\nμ 诊断 (前 5 股):")
    print(f"  历史 μ: {mu_hist[:5]}")
    print(f"  BL 后验: {mu_bl[:5]}")
    print(
        f"  历史 μ std={mu_hist.std():.4f}  BL 后验 std={mu_bl.std():.4f}  (BL 应更收缩)"
    )

    # 5 方案训练期选权
    print("\n训练期优化 5 方案...")
    w_eq = w_bench.copy()
    w_mv = optimize_weights(mu_hist, cov_train, w_bench)
    w_mvsk = optimize_weights(mu_hist, cov_train, w_bench, R_train, GAMMA_S, GAMMA_K)
    w_blmv = optimize_weights(mu_bl, cov_train, w_bench)
    w_blmvsk = optimize_weights(mu_bl, cov_train, w_bench, R_train, GAMMA_S, GAMMA_K)

    schemes = [
        ("等权", w_eq),
        ("MV(历史μ)", w_mv),
        ("MVSK(历史μ)", w_mvsk),
        ("BL+MV(后验μ)", w_blmv),
        ("BL+MVSK(后验μ)", w_blmvsk),
    ]

    # 样本外评估
    print("\n" + "=" * 92)
    print("BL+MVSK 联合优化 样本外对比 (train 376 / test 126)")
    print("=" * 92)
    print(
        f"{'方案':<20}{'夏普':<10}{'收益':<10}{'波动':<10}{'偏度':<10}{'峰度':<10}{'CVaR95':<10}"
    )
    print("-" * 92)

    results = []
    for label, w in schemes:
        m = compute_risk_metrics(R_test, w)
        results.append({"方案": label, **m})
        print(
            f"{label:<20}{m['年化夏普']:<10.4f}{m['年化收益']:<10.4f}"
            f"{m['年化波动']:<10.4f}{m['组合偏度']:<10.4f}"
            f"{m['超额峰度']:<10.4f}{m['日CVaR95']:<10.4f}"
        )

    print("=" * 92)

    # 增量分析
    mv_r = next(r for r in results if r["方案"] == "MV(历史μ)")
    blmvsk_r = next(r for r in results if r["方案"] == "BL+MVSK(后验μ)")
    print("\n增量分析 (BL+MVSK vs MV):")
    print(
        f"  Δ夏普={blmvsk_r['年化夏普']-mv_r['年化夏普']:+.4f}  "
        f"Δ收益={blmvsk_r['年化收益']-mv_r['年化收益']:+.4f}  "
        f"Δ偏度={blmvsk_r['组合偏度']-mv_r['组合偏度']:+.4f}  "
        f"Δ峰度={blmvsk_r['超额峰度']-mv_r['超额峰度']:+.4f}"
    )

    blmv_r = next(r for r in results if r["方案"] == "BL+MV(后验μ)")
    print("\n增量分析 (BL+MVSK vs BL+MV, 验证 MVSK 增量):")
    print(
        f"  Δ夏普={blmvsk_r['年化夏普']-blmv_r['年化夏普']:+.4f}  "
        f"Δ峰度={blmvsk_r['超额峰度']-blmv_r['超额峰度']:+.4f}  "
        f"Δ偏度={blmvsk_r['组合偏度']-blmv_r['组合偏度']:+.4f}"
    )

    # 保存
    out = {
        "参数": {
            "gamma_s": GAMMA_S,
            "gamma_k": GAMMA_K,
            "delta": DELTA,
            "momentum_window": MOMENTUM_WINDOW,
            "view_confidence": VIEW_CONFIDENCE,
            "tau": TAU,
        },
        "训练期": train_end,
        "测试期": T - train_end,
        "μ诊断": {"历史_std": float(mu_hist.std()), "BL后验_std": float(mu_bl.std())},
        "样本外结果": results,
    }
    out_path = _PROJECT_ROOT / "research" / "bl_mvsk_joint_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
