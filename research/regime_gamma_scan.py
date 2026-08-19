"""γ 扫描实验 — P3 调参: 验证 MVSK 在滚动回测中是否有参数能跑赢 BL+MV

P3 回测发现始终 BL+MVSK(γ_s=0.1, γ_k=0.05) 夏普 -0.988 远差于 BL+MV +0.356.
本脚本扫描 γ_s/γ_k 网格, 判断:
    - 是否存在 γ 让 MVSK 跑赢 BL+MV → 调参有意义
    - 所有 γ 都跑不赢 → MVSK 在这段数据 + 滚动回测设置下根本性失效

扫描网格: γ_s ∈ {0, 0.05, 0.1, 0.2, 0.5}, γ_k ∈ {0, 0.05, 0.1, 0.2, 0.3}
γ_s=0/γ_k=0 退化成 BL+MV (基准).

Usage:
    .venv\\Scripts\\python.exe research\\regime_gamma_scan.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.bl_mvsk_joint import bl_posterior_mu, optimize_weights  # noqa: E402
from research.mvsk_ab_test import compute_risk_metrics  # noqa: E402

CACHE_PATH = _PROJECT_ROOT / "research" / "mvsk_real_data_cache.npz"

TRAIN_WINDOW = 252
HOLD_PERIOD = 21
TURNOVER_BPS = 15.0

GAMMA_S_GRID = [0.0, 0.05, 0.1, 0.2, 0.5]
GAMMA_K_GRID = [0.0, 0.05, 0.1, 0.2, 0.3]


def backtest_mvsk(
    R: np.ndarray,  # noqa: N806
    codes: list[str],
    gs: float,
    gk: float,
) -> dict:
    """单 (γ_s, γ_k) 滚动回测."""
    T, N = R.shape  # noqa: N806
    w_bench = np.ones(N) / N
    w_old = w_bench.copy()
    daily_rets = []

    t = TRAIN_WINDOW
    while t + HOLD_PERIOD <= T:
        R_train = R[t - TRAIN_WINDOW:t]  # noqa: N806
        R_hold = R[t:t + HOLD_PERIOD]  # noqa: N806
        cov = np.cov(R_train, rowvar=False) * 252
        mu_bl = bl_posterior_mu(codes, R_train, cov, w_bench)

        if gs == 0.0 and gk == 0.0:
            w_new = optimize_weights(mu_bl, cov, w_bench)
        else:
            w_new = optimize_weights(mu_bl, cov, w_bench, R_train, gs, gk)

        turnover = 0.5 * float(np.abs(w_new - w_old).sum())
        cost = turnover * TURNOVER_BPS / 1e4
        period_rets = R_hold @ w_new
        period_rets[0] -= cost
        daily_rets.extend(period_rets.tolist())
        w_old = w_new.copy()
        t += HOLD_PERIOD

    daily_rets = np.array(daily_rets)
    cum_nav = np.cumprod(1.0 + daily_rets)
    metrics = compute_risk_metrics(daily_rets.reshape(-1, 1), np.array([1.0]))
    peak = np.maximum.accumulate(cum_nav)
    max_dd = float((cum_nav / peak - 1.0).min())
    return {
        "夏普": metrics["年化夏普"], "收益": metrics["年化收益"],
        "波动": metrics["年化波动"], "末净值": float(cum_nav[-1]),
        "回撤": max_dd, "偏度": metrics["组合偏度"], "峰度": metrics["超额峰度"],
    }


def main() -> int:
    if not CACHE_PATH.exists():
        print(f"缓存不存在: {CACHE_PATH}")
        return 1

    cache = np.load(CACHE_PATH, allow_pickle=True)
    R = cache["returns"]  # noqa: N806
    codes = [str(c) for c in cache["codes"]]
    T, N = R.shape  # noqa: N806
    print(f"加载缓存: {T} 日 × {N} 股")
    print(f"滚动回测: train={TRAIN_WINDOW} / hold={HOLD_PERIOD} / 样本外={T-TRAIN_WINDOW} 日")
    print(f"γ 网格: {len(GAMMA_S_GRID)}×{len(GAMMA_K_GRID)}={len(GAMMA_S_GRID)*len(GAMMA_K_GRID)} 组合\n")

    results = []
    blmv_ref = None
    for gs in GAMMA_S_GRID:
        for gk in GAMMA_K_GRID:
            m = backtest_mvsk(R, codes, gs, gk)
            tag = "BL+MV" if gs == 0.0 and gk == 0.0 else f"γ_s={gs} γ_k={gk}"
            print(f"{tag:<20} 夏普={m['夏普']:+.4f}  净值={m['末净值']:.4f}  "
                  f"回撤={m['回撤']:+.4f}  偏度={m['偏度']:+.3f}  峰度={m['峰度']:+.3f}")
            row = {"gamma_s": gs, "gamma_k": gk, **m}
            results.append(row)
            if gs == 0.0 and gk == 0.0:
                blmv_ref = m

    # 分析
    print("\n" + "=" * 80)
    print("分析: 是否存在 γ 让 MVSK 跑赢 BL+MV?")
    print("=" * 80)
    blmv_sharpe = blmv_ref["夏普"]
    blmv_nav = blmv_ref["末净值"]
    print(f"BL+MV 基准 (γ_s=0, γ_k=0): 夏普={blmv_sharpe:+.4f}  净值={blmv_nav:.4f}\n")

    winners = [r for r in results if r["gamma_s"] != 0.0 or r["gamma_k"] != 0.0]
    beat_sharpe = [r for r in winners if r["夏普"] > blmv_sharpe]
    beat_nav = [r for r in winners if r["末净值"] > blmv_nav]

    print(f"跑赢 BL+MV 夏普的 γ 组合: {len(beat_sharpe)}/{len(winners)}")
    if beat_sharpe:
        best = max(beat_sharpe, key=lambda r: r["夏普"])
        print(f"  最佳: γ_s={best['gamma_s']} γ_k={best['gamma_k']} "
              f"夏普={best['夏普']:+.4f} (Δ={best['夏普']-blmv_sharpe:+.4f})")
    print(f"跑赢 BL+MV 净值的 γ 组合: {len(beat_nav)}/{len(winners)}")
    if beat_nav:
        best = max(beat_nav, key=lambda r: r["末净值"])
        print(f"  最佳: γ_s={best['gamma_s']} γ_k={best['gamma_k']} "
              f"净值={best['末净值']:.4f} (Δ={best['末净值']-blmv_nav:+.4f})")

    if not beat_sharpe and not beat_nav:
        print("\n⚠️ 所有 γ 组合都跑不赢 BL+MV → MVSK 在这段数据 + 滚动回测设置下根本性失效")
        print("   可能根因: 252 日训练窗口 μ 估计不稳 + 换仓成本吃掉分布改善收益")
    elif beat_sharpe:
        print("\n✅ 存在 γ 让 MVSK 跑赢 BL+MV → 调参有意义, 可继续 regime 自适应实验")

    # 保存
    out = {
        "参数": {"train_window": TRAIN_WINDOW, "hold_period": HOLD_PERIOD,
                  "turnover_bps": TURNOVER_BPS},
        "bl_mv基准": {"夏普": blmv_sharpe, "末净值": blmv_nav},
        "网格结果": results,
        "跑赢夏普数": len(beat_sharpe), "跑赢净值数": len(beat_nav),
    }
    out_path = _PROJECT_ROOT / "research" / "regime_gamma_scan_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
