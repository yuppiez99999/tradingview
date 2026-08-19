"""跨周期滚动回测 — P4: 验证 BL+MVSK(378) 在 4 年数据上的稳健性

数据: 995 日 × 30 股 (2022-07-11 ~ 2026-08-17), 覆盖 2022 熊市/2023 震荡/2024 反弹/2025-2026.
回测: train=378 / hold=21 / 样本外 617 日 / 换仓 29 次.
对比: 等权 / BL+MV(378) / BL+MVSK(378, γ_s=0.1, γ_k=0.05).
分段: 每 126 日 (约半年) 一段, 看每段 MVSK vs MV 增量.

Usage:
    .venv\\Scripts\\python.exe research\\cross_cycle_backtest.py
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

CACHE_PATH = _PROJECT_ROOT / "research" / "mvsk_real_data_long_cache.npz"

TRAIN_WINDOW = 378
HOLD_PERIOD = 21
TURNOVER_BPS = 15.0
GAMMA_S = 0.1
GAMMA_K = 0.05
SEGMENT_LEN = 126  # 分段长度 (约半年)


def backtest_scheme(R, codes, scheme):  # noqa: N806
    T, N = R.shape  # noqa: N806
    w_bench = np.ones(N) / N
    w_old = w_bench.copy()
    daily_rets = []
    rebalance_dates = []  # 每次换仓的 t 索引

    t = TRAIN_WINDOW
    while t + HOLD_PERIOD <= T:
        R_train = R[t - TRAIN_WINDOW:t]  # noqa: N806
        R_hold = R[t:t + HOLD_PERIOD]  # noqa: N806
        cov = np.cov(R_train, rowvar=False) * 252
        mu_bl = bl_posterior_mu(codes, R_train, cov, w_bench)

        if scheme == "equal":
            w_new = w_bench.copy()
        elif scheme == "bl_mv":
            w_new = optimize_weights(mu_bl, cov, w_bench)
        elif scheme == "bl_mvsk":
            w_new = optimize_weights(mu_bl, cov, w_bench, R_train, GAMMA_S, GAMMA_K)
        else:
            raise ValueError(scheme)

        turnover = 0.5 * float(np.abs(w_new - w_old).sum())
        cost = turnover * TURNOVER_BPS / 1e4
        period_rets = R_hold @ w_new
        period_rets[0] -= cost
        daily_rets.extend(period_rets.tolist())
        rebalance_dates.extend([t] * len(period_rets))
        w_old = w_new.copy()
        t += HOLD_PERIOD

    daily_rets = np.array(daily_rets)
    rebalance_dates = np.array(rebalance_dates)
    cum_nav = np.cumprod(1.0 + daily_rets)
    return daily_rets, cum_nav, rebalance_dates


def metrics_block(daily_rets):
    m = compute_risk_metrics(daily_rets.reshape(-1, 1), np.array([1.0]))
    cum_nav = np.cumprod(1.0 + daily_rets)
    peak = np.maximum.accumulate(cum_nav)
    max_dd = float((cum_nav / peak - 1.0).min())
    return {
        "夏普": m["年化夏普"], "收益": m["年化收益"], "波动": m["年化波动"],
        "末净值": float(cum_nav[-1]), "回撤": max_dd,
        "偏度": m["组合偏度"], "峰度": m["超额峰度"],
    }


def main() -> int:
    cache = np.load(CACHE_PATH, allow_pickle=True)
    R = cache["returns"]  # noqa: N806
    codes = [str(c) for c in cache["codes"]]
    dates = [str(d) for d in cache["dates"]]
    T, N = R.shape  # noqa: N806
    print(f"加载缓存: {T} 日 × {N} 股 ({dates[0]} ~ {dates[-1]})")
    print(f"滚动回测: train={TRAIN_WINDOW} / hold={HOLD_PERIOD} / 样本外={T-TRAIN_WINDOW} 日\n")

    schemes = [("等权", "equal"), ("BL+MV(378)", "bl_mv"), ("BL+MVSK(378)", "bl_mvsk")]
    results = {}
    for label, key in schemes:
        dr, cn, rd = backtest_scheme(R, codes, key)
        results[label] = {"daily_rets": dr, "cum_nav": cn, "rebal_dates": rd}

    # 全样本外对比
    print(f"{'方案':<16}{'夏普':<10}{'收益':<10}{'波动':<10}{'净值':<10}{'回撤':<10}{'偏度':<8}{'峰度':<8}")
    print("-" * 80)
    for label, _ in schemes:
        m = metrics_block(results[label]["daily_rets"])
        print(f"{label:<16}{m['夏普']:<10.4f}{m['收益']:<10.4f}{m['波动']:<10.4f}"
              f"{m['末净值']:<10.4f}{m['回撤']:<10.4f}{m['偏度']:<8.3f}{m['峰度']:<8.3f}")

    mv_m = metrics_block(results["BL+MV(378)"]["daily_rets"])
    mvsk_m = metrics_block(results["BL+MVSK(378)"]["daily_rets"])
    print(f"\n全样本外 MVSK vs MV: Δ夏普={mvsk_m['夏普']-mv_m['夏普']:+.4f}  "
          f"Δ净值={mvsk_m['末净值']-mv_m['末净值']:+.4f}  Δ回撤={mvsk_m['回撤']-mv_m['回撤']:+.4f}")

    # 分段分析
    print(f"\n{'='*90}")
    print(f"分段分析 (每 {SEGMENT_LEN} 日一段, 看每段 MVSK vs MV 增量)")
    print(f"{'='*90}")
    seg_results = []
    n_segs = (T - TRAIN_WINDOW) // SEGMENT_LEN
    for seg_i in range(n_segs):
        start = seg_i * SEGMENT_LEN
        end = min((seg_i + 1) * SEGMENT_LEN, T - TRAIN_WINDOW)
        seg_mv = metrics_block(results["BL+MV(378)"]["daily_rets"][start:end])
        seg_mvsk = metrics_block(results["BL+MVSK(378)"]["daily_rets"][start:end])
        seg_eq = metrics_block(results["等权"]["daily_rets"][start:end])
        d_sharpe = seg_mvsk["夏普"] - seg_mv["夏普"]
        d_nav = seg_mvsk["末净值"] - seg_mv["末净值"]
        tag = "✅" if d_sharpe > 0 else "❌"
        date_start = dates[TRAIN_WINDOW + start]
        date_end = dates[TRAIN_WINDOW + end - 1]
        print(f"段{seg_i+1} {date_start}~{date_end}: MV夏普={seg_mv['夏普']:+.3f} "
              f"MVSK夏普={seg_mvsk['夏普']:+.3f} Δ={d_sharpe:+.3f} {tag}  "
              f"(MV净值={seg_mv['末净值']:.3f} MVSK净值={seg_mvsk['末净值']:.3f})")
        seg_results.append({
            "段": seg_i + 1, "日期": f"{date_start}~{date_end}",
            "MV": seg_mv, "MVSK": seg_mvsk, "等权": seg_eq,
            "Δ夏普": d_sharpe, "Δ净值": d_nav,
        })

    n_mvsk_win = sum(1 for s in seg_results if s["Δ夏普"] > 0)
    print(f"\nMVSK 跑赢 MV 的段数: {n_mvsk_win}/{n_segs}")

    # 保存
    out = {
        "参数": {"train": TRAIN_WINDOW, "hold": HOLD_PERIOD, "turnover_bps": TURNOVER_BPS,
                  "gamma_s": GAMMA_S, "gamma_k": GAMMA_K},
        "数据": {"天数": T, "股票数": N, "日期范围": f"{dates[0]}~{dates[-1]}"},
        "全样本外": {scheme: metrics_block(results[scheme]["daily_rets"]) for scheme, _ in schemes},
        "分段": seg_results,
        "cum_nav": {scheme: results[scheme]["cum_nav"].tolist() for scheme, _ in schemes},
    }
    out_path = _PROJECT_ROOT / "research" / "cross_cycle_backtest_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
