"""P3 修正实验 — Regime 切换 + 动态训练窗口

原 P3 失败根因: regime 切换固定 train=252, 而 MVSK 在 252 日根本性失效.
训练窗口扫描发现 train ≥ 378 时 MVSK 重新跑赢 MV.

本实验: regime 检测到高波动 → 用 378 日训练窗口 + MVSK; 低波动 → 252 日 + MV.
对比 5 方案:
    1. 等权
    2. 始终 BL+MV (train=252)
    3. 始终 BL+MVSK (train=378, γ_s=0.1, γ_k=0.05)
    4. 原 Regime 切换 (train=252 固定, 仅切 MV/MVSK) — 已知失败
    5. 修正 Regime 切换 (低波动→252+MV, 高波动→378+MVSK)

Usage:
    .venv\\Scripts\\python.exe research\\regime_fixed_backtest.py
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
from utils.mvsk_regime_detector import RegimeDetector  # noqa: E402

CACHE_PATH = _PROJECT_ROOT / "research" / "mvsk_real_data_cache.npz"

HOLD_PERIOD = 21
TURNOVER_BPS = 15.0
TRAIN_SHORT = 252
TRAIN_LONG = 378
GAMMA_S_FIX = 0.1
GAMMA_K_FIX = 0.05


def backtest(R, codes, scheme, detector=None):  # noqa: N806
    T, N = R.shape  # noqa: N806
    w_bench = np.ones(N) / N
    w_old = w_bench.copy()
    daily_rets = []
    regime_log = []

    t = TRAIN_SHORT  # 从最短训练窗口开始
    while t + HOLD_PERIOD <= T:
        # 决定训练窗口和是否用 MVSK
        if scheme == "equal":
            w_new = w_bench.copy()
            tw_used = TRAIN_SHORT
            use_mvsk = False
        elif scheme == "bl_mv_252":
            R_train = R[t - TRAIN_SHORT : t]
            cov = np.cov(R_train, rowvar=False) * 252
            mu_bl = bl_posterior_mu(codes, R_train, cov, w_bench)
            w_new = optimize_weights(mu_bl, cov, w_bench)
            tw_used = TRAIN_SHORT
            use_mvsk = False
        elif scheme == "bl_mvsk_378":
            if t < TRAIN_LONG:
                # 数据不够 378, 回退 252+MV
                R_train = R[t - TRAIN_SHORT : t]
                cov = np.cov(R_train, rowvar=False) * 252
                mu_bl = bl_posterior_mu(codes, R_train, cov, w_bench)
                w_new = optimize_weights(mu_bl, cov, w_bench)
                tw_used = TRAIN_SHORT
                use_mvsk = False
            else:
                R_train = R[t - TRAIN_LONG : t]
                cov = np.cov(R_train, rowvar=False) * 252
                mu_bl = bl_posterior_mu(codes, R_train, cov, w_bench)
                w_new = optimize_weights(
                    mu_bl, cov, w_bench, R_train, GAMMA_S_FIX, GAMMA_K_FIX
                )
                tw_used = TRAIN_LONG
                use_mvsk = True
        elif scheme == "regime_old":
            # 原 P3: train=252 固定, 仅切 MV/MVSK
            R_train = R[t - TRAIN_SHORT : t]
            cov = np.cov(R_train, rowvar=False) * 252
            mu_bl = bl_posterior_mu(codes, R_train, cov, w_bench)
            r_det = detector.detect(R_train)
            if r_det.use_mvsk:
                w_new = optimize_weights(
                    mu_bl, cov, w_bench, R_train, GAMMA_S_FIX, GAMMA_K_FIX
                )
            else:
                w_new = optimize_weights(mu_bl, cov, w_bench)
            tw_used = TRAIN_SHORT
            use_mvsk = r_det.use_mvsk
        elif scheme == "regime_fixed":
            # 修正: 低波动→252+MV, 高波动→378+MVSK
            R_train_short = R[t - TRAIN_SHORT : t]
            r_det = detector.detect(R_train_short)
            if r_det.use_mvsk and t >= TRAIN_LONG:
                R_train = R[t - TRAIN_LONG : t]
                cov = np.cov(R_train, rowvar=False) * 252
                mu_bl = bl_posterior_mu(codes, R_train, cov, w_bench)
                w_new = optimize_weights(
                    mu_bl, cov, w_bench, R_train, GAMMA_S_FIX, GAMMA_K_FIX
                )
                tw_used = TRAIN_LONG
            else:
                R_train = R_train_short
                cov = np.cov(R_train, rowvar=False) * 252
                mu_bl = bl_posterior_mu(codes, R_train, cov, w_bench)
                w_new = optimize_weights(mu_bl, cov, w_bench)
                tw_used = TRAIN_SHORT
            use_mvsk = r_det.use_mvsk and t >= TRAIN_LONG
        else:
            raise ValueError(scheme)

        regime_log.append({"t": int(t), "tw": tw_used, "mvsk": use_mvsk})

        R_hold = R[t : t + HOLD_PERIOD]
        turnover = 0.5 * float(np.abs(w_new - w_old).sum())
        cost = turnover * TURNOVER_BPS / 1e4
        period_rets = R_hold @ w_new
        period_rets[0] -= cost
        daily_rets.extend(period_rets.tolist())
        w_old = w_new.copy()
        t += HOLD_PERIOD

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
        "regime_log": regime_log,
    }


def main() -> int:
    cache = np.load(CACHE_PATH, allow_pickle=True)
    R = cache["returns"]  # noqa: N806
    codes = [str(c) for c in cache["codes"]]
    T, N = R.shape  # noqa: N806
    print(f"加载缓存: {T} 日 × {N} 股")
    print(f"修正实验: 低波动→{TRAIN_SHORT}日+MV, 高波动→{TRAIN_LONG}日+MVSK\n")

    detector = RegimeDetector(window=60, vol_quantile=0.75, kurtosis_threshold=3.0)

    schemes = [
        ("等权", "equal"),
        ("始终BL+MV(252)", "bl_mv_252"),
        ("始终BL+MVSK(378)", "bl_mvsk_378"),
        ("原Regime(252固定)", "regime_old"),
        ("修正Regime(动态tw)", "regime_fixed"),
    ]

    print(
        f"{'方案':<24}{'夏普':<10}{'净值':<10}{'收益':<10}{'回撤':<10}{'偏度':<8}{'峰度':<8}"
    )
    print("-" * 80)
    results = {}
    for label, key in schemes:
        m = backtest(R, codes, key, detector)
        results[label] = m
        print(
            f"{label:<24}{m['夏普']:<10.4f}{m['末净值']:<10.4f}{m['收益']:<10.4f}"
            f"{m['回撤']:<10.4f}{m['偏度']:<8.3f}{m['峰度']:<8.3f}"
        )

    # 增量分析
    print("\n" + "=" * 80)
    mv = results["始终BL+MV(252)"]
    mvsk = results["始终BL+MVSK(378)"]
    old = results["原Regime(252固定)"]
    fix = results["修正Regime(动态tw)"]
    print(
        f"修正 Regime vs 始终 BL+MV:    Δ夏普={fix['夏普']-mv['夏普']:+.4f}  Δ净值={fix['末净值']-mv['末净值']:+.4f}"
    )
    print(
        f"修正 Regime vs 始终 BL+MVSK:  Δ夏普={fix['夏普']-mvsk['夏普']:+.4f}  Δ净值={fix['末净值']-mvsk['末净值']:+.4f}"  # noqa: E501
    )
    print(
        f"修正 Regime vs 原 Regime:     Δ夏普={fix['夏普']-old['夏普']:+.4f}  Δ净值={fix['末净值']-old['末净值']:+.4f}"
    )

    # regime 时间线
    log = fix["regime_log"]
    n_mvsk = sum(1 for r in log if r["mvsk"])
    n_long = sum(1 for r in log if r["tw"] == TRAIN_LONG)
    print(f"\n修正 Regime 时间线 ({len(log)} 次换仓):")
    print(f"  MVSK 启用: {n_mvsk}/{len(log)}  长训练窗口: {n_long}/{len(log)}")
    print(f"  {'t':<6}{'tw':<6}{'mvsk':<6}")
    for r in log:
        print(f"  {r['t']:<6}{r['tw']:<6}{r['mvsk']:<6}")

    out = {
        "结果": {
            scheme: {k: v for k, v in m.items() if k != "regime_log"}
            for scheme, m in results.items()
        },
        "regime_log": fix["regime_log"],
    }
    out_path = _PROJECT_ROOT / "research" / "regime_fixed_backtest_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
