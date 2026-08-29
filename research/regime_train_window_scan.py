"""训练窗口扫描 — 验证 MVSK 优势是否随训练窗口长度恢复

γ 扫描发现 252 日滚动窗口下所有 γ 都跑不赢 BL+MV. 但 P2 单次 split (train=376)
时 BL+MVSK 优于 BL+MV. 本脚本扫描训练窗口长度, 找 MVSK 优势的临界点.

扫描: train ∈ {252, 336, 378, 420}, 每个窗口下对比:
    - BL+MV (γ_s=0, γ_k=0)
    - BL+MVSK (γ_s=0.1, γ_k=0.05 — P1 最优)
    - BL+MVSK (γ_s=0.5, γ_k=0.05 — γ 扫描中 252 日最佳)

Usage:
    .venv\\Scripts\\python.exe research\\regime_train_window_scan.py
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

HOLD_PERIOD = 21
TURNOVER_BPS = 15.0
TRAIN_WINDOWS = [252, 336, 378, 420]

SCHEMES = [
    ("BL+MV", 0.0, 0.0),
    ("BL+MVSK(0.1,0.05)", 0.1, 0.05),
    ("BL+MVSK(0.5,0.05)", 0.5, 0.05),
]


def backtest(R, codes, train_window, gs, gk):  # noqa: N806
    T, N = R.shape  # noqa: N806
    w_bench = np.ones(N) / N
    w_old = w_bench.copy()
    daily_rets = []
    n_rebal = 0

    t = train_window
    while t + HOLD_PERIOD <= T:
        R_train = R[t - train_window : t]  # noqa: N806
        R_hold = R[t : t + HOLD_PERIOD]  # noqa: N806
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
        n_rebal += 1

    daily_rets = np.array(daily_rets)
    cum_nav = np.cumprod(1.0 + daily_rets)
    m = compute_risk_metrics(daily_rets.reshape(-1, 1), np.array([1.0]))
    peak = np.maximum.accumulate(cum_nav)
    max_dd = float((cum_nav / peak - 1.0).min())
    return {
        "夏普": m["年化夏普"],
        "收益": m["年化收益"],
        "末净值": float(cum_nav[-1]),
        "回撤": max_dd,
        "偏度": m["组合偏度"],
        "峰度": m["超额峰度"],
        "n_rebal": n_rebal,
        "样本外天数": len(daily_rets),
    }


def main() -> int:
    cache = np.load(CACHE_PATH, allow_pickle=True)
    R = cache["returns"]  # noqa: N806
    codes = [str(c) for c in cache["codes"]]
    T, N = R.shape  # noqa: N806
    print(f"加载缓存: {T} 日 × {N} 股\n")

    results = {}
    print(
        f"{'train':<8}{'方案':<22}{'换仓':<6}{'样本外':<8}{'夏普':<10}{'净值':<10}{'回撤':<10}{'偏度':<8}{'峰度':<8}"
    )
    print("-" * 90)

    for tw in TRAIN_WINDOWS:
        results[tw] = {}
        for label, gs, gk in SCHEMES:
            m = backtest(R, codes, tw, gs, gk)
            results[tw][label] = m
            print(
                f"{tw:<8}{label:<22}{m['n_rebal']:<6}{m['样本外天数']:<8}"
                f"{m['夏普']:<10.4f}{m['末净值']:<10.4f}{m['回撤']:<10.4f}"
                f"{m['偏度']:<8.3f}{m['峰度']:<8.3f}"
            )
        # MVSK vs MV 增量
        mv = results[tw]["BL+MV"]
        mvsk = results[tw]["BL+MVSK(0.1,0.05)"]
        delta = mvsk["夏普"] - mv["夏普"]
        tag = "✅ MVSK 优" if delta > 0 else "❌ MV 优"
        print(f"{'':<8}Δ夏普(0.1,0.05 vs MV)={delta:+.4f}  {tag}")
        print()

    # 结论
    print("=" * 90)
    print("结论: MVSK 优势 vs 训练窗口长度")
    print("=" * 90)
    for tw in TRAIN_WINDOWS:
        mv = results[tw]["BL+MV"]["夏普"]
        mvsk1 = results[tw]["BL+MVSK(0.1,0.05)"]["夏普"]
        mvsk2 = results[tw]["BL+MVSK(0.5,0.05)"]["夏普"]
        print(
            f"train={tw}: MV={mv:+.3f}  MVSK(0.1)={mvsk1:+.3f} (Δ={mvsk1-mv:+.3f})  "
            f"MVSK(0.5)={mvsk2:+.3f} (Δ={mvsk2-mv:+.3f})"
        )

    out = {"train_windows": TRAIN_WINDOWS, "结果": results}
    out_path = _PROJECT_ROOT / "research" / "regime_train_window_scan_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
