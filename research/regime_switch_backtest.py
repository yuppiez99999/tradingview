"""Regime 动态切换回测 — P3 核心

论文发现三: 高阶矩价值是 regime dependent. 高波动肥尾 → MVSK 显著跑赢 MV;
低波动正态 → 增量有限. 本脚本用 RegimeDetector 在滚动回测中动态切换
BL+MV / BL+MVSK, 验证 regime 切换 vs 始终单一方案的增量价值.

滚动回测设计:
    - train_window=252 (1 年训练估计 μ/cov/BL 观点)
    - hold_period=21  (约 1 月换仓)
    - 每次换仓: regime detector 检测过去 train_window 日 → 决定 MV/MVSK

对比 4 方案 (样本外累积净值):
    1. 等权基准
    2. 始终 BL+MV      — 不论 regime 都用纯均值方差
    3. 始终 BL+MVSK    — 不论 regime 都用高阶矩
    4. Regime 动态切换 — LOW_VOL_NORMAL→BL+MV, HIGH_VOL_FAT_TAIL→BL+MVSK

Usage:
    .venv\\Scripts\\python.exe research\\regime_switch_backtest.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.bl_mvsk_joint import (  # noqa: E402
    GAMMA_K,
    GAMMA_S,
    bl_posterior_mu,
    optimize_weights,
)
from research.mvsk_ab_test import compute_risk_metrics  # noqa: E402
from utils.mvsk_regime_detector import RegimeDetector  # noqa: E402

CACHE_PATH = _PROJECT_ROOT / "research" / "mvsk_real_data_cache.npz"

# 回测参数
TRAIN_WINDOW = 252  # 训练窗口 (1 年)
HOLD_PERIOD = 21  # 换仓周期 (约 1 月)
TURNOVER_BPS = 15.0  # 单边换仓成本 bp


def backtest_scheme(
    R: np.ndarray,  # noqa: N806
    codes: list[str],
    scheme: str,
    detector: RegimeDetector | None = None,
) -> dict:
    """单方案滚动回测.

    scheme: 'equal', 'bl_mv', 'bl_mvsk', 'regime'
    """
    T, N = R.shape  # noqa: N806
    w_bench = np.ones(N) / N
    w_old = w_bench.copy()

    daily_rets = []  # 每日组合收益 (扣换仓成本)
    turnover_log = []
    regime_log = []  # 每个换仓点的 regime (仅 regime 方案记录)

    t = TRAIN_WINDOW
    while t + HOLD_PERIOD <= T:
        R_train = R[t - TRAIN_WINDOW : t]  # noqa: N806
        R_hold = R[t : t + HOLD_PERIOD]  # noqa: N806
        cov = np.cov(R_train, rowvar=False) * 252
        mu_bl = bl_posterior_mu(codes, R_train, cov, w_bench)

        if scheme == "equal":
            w_new = w_bench.copy()
        elif scheme == "bl_mv":
            w_new = optimize_weights(mu_bl, cov, w_bench)
        elif scheme == "bl_mvsk":
            w_new = optimize_weights(mu_bl, cov, w_bench, R_train, GAMMA_S, GAMMA_K)
        elif scheme == "regime":
            assert detector is not None
            r_det = detector.detect(R_train)
            regime_log.append(
                {
                    "t": int(t),
                    "regime": r_det.regime.value,
                    "use_mvsk": r_det.use_mvsk,
                    "vol_rank": float(r_det.vol_quantile_rank),
                    "exkurt": float(r_det.excess_kurtosis),
                    "trigger": r_det.trigger,
                }
            )
            if r_det.use_mvsk:
                w_new = optimize_weights(mu_bl, cov, w_bench, R_train, GAMMA_S, GAMMA_K)
            else:
                w_new = optimize_weights(mu_bl, cov, w_bench)
        else:
            raise ValueError(f"未知 scheme: {scheme}")

        # 换仓成本 (首日扣)
        turnover = 0.5 * float(np.abs(w_new - w_old).sum())
        cost = turnover * TURNOVER_BPS / 1e4
        turnover_log.append(turnover)

        # 持有期每日收益
        period_rets = R_hold @ w_new
        period_rets[0] -= cost  # 首日扣换仓成本
        daily_rets.extend(period_rets.tolist())

        w_old = w_new.copy()
        t += HOLD_PERIOD

    daily_rets = np.array(daily_rets)
    cum_nav = np.cumprod(1.0 + daily_rets)
    metrics = compute_risk_metrics(daily_rets.reshape(-1, 1), np.array([1.0]))
    # 补最大回撤
    peak = np.maximum.accumulate(cum_nav)
    max_dd = float((cum_nav / peak - 1.0).min())
    metrics["最大回撤"] = max_dd
    metrics["末净值"] = float(cum_nav[-1])
    metrics["平均换仓"] = float(np.mean(turnover_log)) if turnover_log else 0.0
    return {
        "metrics": metrics,
        "cum_nav": cum_nav.tolist(),
        "regime_log": regime_log,
        "n_rebalance": len(turnover_log),
    }


def main() -> int:
    if not CACHE_PATH.exists():
        print(f"缓存不存在: {CACHE_PATH}，请先跑 mvsk_ab_test_real.py")
        return 1

    cache = np.load(CACHE_PATH, allow_pickle=True)
    R = cache["returns"]  # noqa: N806
    codes = [str(c) for c in cache["codes"]]
    T, N = R.shape  # noqa: N806
    print(f"加载缓存: {T} 日 × {N} 股")
    print(
        f"滚动回测: train={TRAIN_WINDOW} / hold={HOLD_PERIOD} "
        f"/ 样本外={T-TRAIN_WINDOW} 日 / 换仓={(T-TRAIN_WINDOW)//HOLD_PERIOD} 次"
    )

    detector = RegimeDetector(window=60, vol_quantile=0.75, kurtosis_threshold=3.0)

    schemes = [
        ("等权", "equal"),
        ("始终BL+MV", "bl_mv"),
        ("始终BL+MVSK", "bl_mvsk"),
        ("Regime切换", "regime"),
    ]

    print("\n滚动回测 4 方案...")
    results = {}
    for label, key in schemes:
        print(f"  跑 {label}...")
        results[label] = backtest_scheme(
            R, codes, key, detector if key == "regime" else None
        )

    # 对比表
    print("\n" + "=" * 100)
    print("Regime 动态切换 滚动回测对比 (样本外)")
    print("=" * 100)
    hdr = f"{'方案':<16}{'末净值':<10}{'年化收益':<10}{'年化波动':<10}{'夏普':<10}{'最大回撤':<10}{'偏度':<8}{'峰度':<8}{'平均换仓':<10}"  # noqa: E501
    print(hdr)
    print("-" * 100)
    for label, _ in schemes:
        m = results[label]["metrics"]
        print(
            f"{label:<16}{m['末净值']:<10.4f}{m['年化收益']:<10.4f}"
            f"{m['年化波动']:<10.4f}{m['年化夏普']:<10.4f}{m['最大回撤']:<10.4f}"
            f"{m['组合偏度']:<8.3f}{m['超额峰度']:<8.3f}{m['平均换仓']:<10.4f}"
        )
    print("=" * 100)

    # 增量分析
    m_reg = results["Regime切换"]["metrics"]
    m_mv = results["始终BL+MV"]["metrics"]
    m_mvsk = results["始终BL+MVSK"]["metrics"]
    print("\n增量分析 (Regime切换 vs 始终BL+MV):")
    print(
        f"  Δ夏普={m_reg['年化夏普']-m_mv['年化夏普']:+.4f}  "
        f"Δ末净值={m_reg['末净值']-m_mv['末净值']:+.4f}  "
        f"Δ回撤={m_reg['最大回撤']-m_mv['最大回撤']:+.4f}"
    )
    print("增量分析 (Regime切换 vs 始终BL+MVSK):")
    print(
        f"  Δ夏普={m_reg['年化夏普']-m_mvsk['年化夏普']:+.4f}  "
        f"Δ末净值={m_reg['末净值']-m_mvsk['末净值']:+.4f}  "
        f"Δ回撤={m_reg['最大回撤']-m_mvsk['最大回撤']:+.4f}"
    )

    # Regime 时间线
    regime_log = results["Regime切换"]["regime_log"]
    n_mvsk = sum(1 for r in regime_log if r["use_mvsk"])
    print(f"\nRegime 时间线 ({len(regime_log)} 次换仓):")
    print(
        f"  MVSK 启用: {n_mvsk}/{len(regime_log)} ({100*n_mvsk/len(regime_log):.0f}%)"
    )
    print(f"  {'t':<6}{'regime':<20}{'vol_rank':<10}{'exkurt':<10}{'trigger':<10}")
    for r in regime_log:
        print(
            f"  {r['t']:<6}{r['regime']:<20}{r['vol_rank']:<10.3f}"
            f"{r['exkurt']:<10.3f}{r['trigger']:<10}"
        )

    # 保存
    out = {
        "参数": {
            "train_window": TRAIN_WINDOW,
            "hold_period": HOLD_PERIOD,
            "turnover_bps": TURNOVER_BPS,
            "gamma_s": GAMMA_S,
            "gamma_k": GAMMA_K,
            "detector": {"window": 60, "vol_quantile": 0.75, "kurtosis_threshold": 3.0},
        },
        "样本外天数": T - TRAIN_WINDOW,
        "结果": {
            label: {
                "metrics": r["metrics"],
                "n_rebalance": r["n_rebalance"],
                "regime_log": r["regime_log"],
            }
            for label, r in results.items()
        },
        "cum_nav": {label: r["cum_nav"] for label, r in results.items()},
    }
    out_path = _PROJECT_ROOT / "research" / "regime_switch_backtest_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
