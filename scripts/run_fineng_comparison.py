#!/usr/bin/env python3
"""FinEng 三模块只读对照 — GARCH / Kalman / EVT

ARCHITECTURE_金融工程闭环 — T-NEXT-3.3
创建: 2026-08-02

功能:
    1. GARCH(1,1) vs EWMA(λ=0.94) 波动率预测对照
    2. Kalman Filter vs Rolling OLS 时变 Beta 对冲效率对照
    3. POT-GPD 尾部风险估计 (VaR/ES)

输出:
    - reports/evolution/fineng_comparison/comparison_YYYY-MM-DD.json
    - reports/evolution/fineng_comparison/comparison_latest.json

所有模块均为只读对照 — 不修改任何生产链路参数。
"""

from __future__ import annotations

import json
import logging
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("fineng_comparison")

OUTPUT_DIR = PROJECT_ROOT / "reports" / "evolution" / "fineng_comparison"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_shadow_returns() -> tuple[list[float], list[str]]:
    """加载 Shadow 日收益率数据."""
    shadow_path = PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
    returns: list[float] = []
    dates: list[str] = []
    if not shadow_path.exists():
        logger.warning("Shadow 数据不存在: %s", shadow_path)
        return returns, dates
    with open(shadow_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if "daily_return" in rec:
                returns.append(float(rec["daily_return"]))
                dates.append(rec.get("date", ""))
    return returns, dates


def load_index_returns() -> tuple[list[float], list[str]]:
    """尝试加载沪深300指数收益率 (用于 Kalman Beta 对照).

    查找顺序:
        1. reports/shadow/index_returns.jsonl
        2. reports/shadow/csi300_returns.jsonl
        3. reports/index_returns.jsonl
    """
    candidates = [
        PROJECT_ROOT / "reports" / "shadow" / "index_returns.jsonl",
        PROJECT_ROOT / "reports" / "shadow" / "csi300_returns.jsonl",
        PROJECT_ROOT / "reports" / "index_returns.jsonl",
    ]
    for path in candidates:
        if path.exists():
            returns: list[float] = []
            dates: list[str] = []
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if "daily_return" in rec:
                        returns.append(float(rec["daily_return"]))
                        dates.append(rec.get("date", ""))
            if returns:
                logger.info("加载指数收益: %s (%d 条)", path, len(returns))
                return returns, dates
    logger.warning("未找到指数收益数据, Kalman 对照将跳过")
    return [], []


def run_garch_comparison(daily_returns: list[float], date_str: str) -> dict[str, Any]:
    """T-NEXT-3.3a: GARCH vs EWMA 波动率对照."""
    logger.info("=" * 50)
    logger.info("GARCH vs EWMA 波动率对照")
    logger.info("=" * 50)

    if len(daily_returns) < 60:
        return {
            "status": "skipped",
            "reason": f"数据不足 (n={len(daily_returns)}, 需 >=60)",
            "garch_converged": False,
        }

    try:
        from utils.fineng.vol_forecast import generate_comparison

        report = generate_comparison(
            daily_returns,
            date_str=date_str,
            ewma_lambda=0.94,
        )

        result = {
            "status": "completed",
            "date": date_str,
            "garch_vol": (
                round(report.garch_vol, 6) if not math.isnan(report.garch_vol) else None
            ),
            "ewma_vol": round(report.ewma_vol, 6),
            "ratio": (
                round(report.garch_vs_ewma_ratio, 4)
                if not math.isnan(report.garch_vs_ewma_ratio)
                else None
            ),
            "garch_persistence": round(report.garch_persistence, 4),
            "garch_converged": report.garch_converged,
            "interpretation": _interpret_garch(report),
        }
        logger.info(
            "GARCH: vol=%.4f, EWMA=%.4f, ratio=%.2f, converged=%s",
            report.garch_vol if not math.isnan(report.garch_vol) else 0,
            report.ewma_vol,
            (
                report.garch_vs_ewma_ratio
                if not math.isnan(report.garch_vs_ewma_ratio)
                else 0
            ),
            report.garch_converged,
        )
        return result

    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ) as e:

        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.exception("GARCH 对照异常: %s", e)
        return {"status": "error", "reason": str(e)}


def run_kalman_comparison(
    portfolio_returns: list[float],
    index_returns: list[float],
    date_str: str,
) -> dict[str, Any]:
    """T-NEXT-3.3b: Kalman vs Rolling OLS 对冲效率对照."""
    logger.info("=" * 50)
    logger.info("Kalman vs OLS 对冲效率对照")
    logger.info("=" * 50)

    n = min(len(portfolio_returns), len(index_returns))
    if n < 60:
        return {
            "status": "skipped",
            "reason": f"数据不足 (n={n}, 需 >=60)",
            "kalman_beta": None,
        }

    try:
        from utils.fineng.kalman_beta import backtest_hedge_comparison

        comparison = backtest_hedge_comparison(
            portfolio_returns[:n],
            index_returns[:n],
            ols_window=min(60, n // 2),
            date_str=date_str,
        )

        result = {
            "status": "completed",
            "date": date_str,
            "latest_kalman_beta": (
                round(comparison.latest_kalman_beta, 4)
                if not math.isnan(comparison.latest_kalman_beta)
                else None
            ),
            "latest_ols_beta": (
                round(comparison.latest_ols_beta, 4)
                if not math.isnan(comparison.latest_ols_beta)
                else None
            ),
            "beta_diff": round(
                abs(comparison.latest_kalman_beta - comparison.latest_ols_beta), 4
            ),
            "kalman_hedged_var": round(comparison.kalman_hedged_var, 8),
            "ols_hedged_var": round(comparison.ols_hedged_var, 8),
            "unhedged_var": round(comparison.unhedged_var, 8),
            "kalman_var_reduction_pct": round(comparison.kalman_var_reduction_pct, 2),
            "ols_var_reduction_pct": round(comparison.ols_var_reduction_pct, 2),
            "kalman_vs_ols_improvement_pct": round(
                comparison.kalman_vs_ols_improvement_pct, 2
            ),
            "kalman_converged": comparison.kalman_converged,
            "interpretation": _interpret_kalman(comparison),
        }
        logger.info(
            "Kalman: beta=%.3f, var_red=%.1f%% vs OLS: beta=%.3f, var_red=%.1f%%, improvement=%.1f%%",
            comparison.latest_kalman_beta,
            comparison.kalman_var_reduction_pct,
            comparison.latest_ols_beta,
            comparison.ols_var_reduction_pct,
            comparison.kalman_vs_ols_improvement_pct,
        )
        return result

    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ) as e:

        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.exception("Kalman 对照异常: %s", e)
        return {"status": "error", "reason": str(e)}


def run_evt_comparison(daily_returns: list[float], date_str: str) -> dict[str, Any]:
    """T-NEXT-3.3c: EVT 尾部风险估计."""
    logger.info("=" * 50)
    logger.info("EVT 尾部风险估计")
    logger.info("=" * 50)

    if len(daily_returns) < 120:
        return {
            "status": "skipped",
            "reason": f"数据不足 (n={len(daily_returns)}, 需 >=120)",
            "converged": False,
        }

    try:
        from utils.fineng.tail_risk_evt import evt_var_es, fit_evt

        evt = fit_evt(daily_returns, threshold_percentile=0.95, min_history=120)
        var_es = evt_var_es(evt, confidence_level=0.99)

        result = {
            "status": "completed",
            "date": date_str,
            "converged": evt.converged,
            "n_excess": evt.n_excess,
            "threshold_u": round(evt.threshold_u, 6),
            "sigma": round(evt.sigma, 6),
            "xi": round(evt.xi, 6),
            "var_99": round(var_es.var_99, 6),
            "es_99": round(var_es.es_99, 6),
            "var_99_annualized": (
                round(var_es.var_99 * math.sqrt(252), 4)
                if not math.isnan(var_es.var_99)
                else None
            ),
            "es_99_annualized": (
                round(var_es.es_99 * math.sqrt(252), 4)
                if not math.isnan(var_es.es_99)
                else None
            ),
            "interpretation": _interpret_evt(evt, var_es),
        }
        logger.info(
            "EVT: ξ=%.4f, VaR99=%.4f, ES99=%.4f, converged=%s",
            evt.xi,
            var_es.var_99,
            var_es.es_99,
            evt.converged,
        )
        return result

    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ) as e:

        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.exception("EVT 对照异常: %s", e)
        return {"status": "error", "reason": str(e)}


# ============================================================
# 解读函数 (人类可读)
# ============================================================


def _interpret_garch(report: Any) -> str:
    if not report.garch_converged:
        return "GARCH 未收敛, 回退 EWMA"
    if report.garch_persistence > 0.98:
        return f"波动率高度持续(α+β={report.garch_persistence:.3f}), 市场冲击衰减极慢"
    if report.garch_vs_ewma_ratio > 1.5:
        return f"GARCH 预测波动率显著高于 EWMA ×{report.garch_vs_ewma_ratio:.1f}, 当前市场波动放大"
    if report.garch_vs_ewma_ratio < 0.7:
        return f"GARCH 预测波动率显著低于 EWMA ×{report.garch_vs_ewma_ratio:.1f}, 波动率回归均值"
    return f"GARCH/EWMA 比率={report.garch_vs_ewma_ratio:.2f}, 正常区间内"


def _interpret_kalman(comparison: Any) -> str:
    if comparison.kalman_vs_ols_improvement_pct > 10:
        return f"Kalman 对冲效率显著优于 OLS ({comparison.kalman_vs_ols_improvement_pct:.1f}%), 建议关注"
    if comparison.kalman_vs_ols_improvement_pct < -10:
        return f"OLS 对冲效率显著优于 Kalman ({abs(comparison.kalman_vs_ols_improvement_pct):.1f}%), Kalman 可能过拟合"
    return f"Kalman 与 OLS 对冲效率相当, 差异={comparison.kalman_vs_ols_improvement_pct:.1f}%"


def _interpret_evt(evt: Any, var_es: Any) -> str:
    if not evt.converged:
        return "EVT 未收敛 (超越样本不足)"
    if evt.xi > 0.3:
        return f"ξ={evt.xi:.3f} > 0.3, 分布极厚尾, 极端风险显著高于正态假设"
    if evt.xi < 0:
        return f"ξ={evt.xi:.3f} < 0, 分布有上界, 极端风险有限"
    return (
        f"ξ={evt.xi:.3f}, 厚尾适中, ES99 年化={abs(var_es.es_99 * math.sqrt(252)):.2%}"
    )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="FinEng 三模块只读对照")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--skip-garch", action="store_true")
    parser.add_argument("--skip-kalman", action="store_true")
    parser.add_argument("--skip-evt", action="store_true")
    args = parser.parse_args()

    date_str = datetime.now().strftime("%Y-%m-%d")
    results: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "date": date_str,
        "mode": "readonly_comparison",
        "garch": {},
        "kalman": {},
        "evt": {},
    }

    portfolio_returns, _ = load_shadow_returns()
    index_returns, _ = load_index_returns()

    # 1. GARCH
    if not args.skip_garch:
        results["garch"] = run_garch_comparison(portfolio_returns, date_str)

    # 2. Kalman (需指数收益)
    if not args.skip_kalman and index_returns:
        results["kalman"] = run_kalman_comparison(
            portfolio_returns, index_returns, date_str
        )
    elif not args.skip_kalman:
        results["kalman"] = {"status": "skipped", "reason": "无指数收益数据"}

    # 3. EVT
    if not args.skip_evt:
        results["evt"] = run_evt_comparison(portfolio_returns, date_str)

    # 持久化
    date_filename = OUTPUT_DIR / f"comparison_{date_str}.json"
    latest_filename = OUTPUT_DIR / "comparison_latest.json"
    output = json.dumps(results, ensure_ascii=False, indent=2, default=str)

    with open(date_filename, "w", encoding="utf-8") as f:
        f.write(output)
    with open(latest_filename, "w", encoding="utf-8") as f:
        f.write(output)

    logger.info("对照报告已保存: %s", date_filename)

    if args.json:
        print(output)  # noqa: T201
    else:
        print(f"FinEng 三模块对照 — {date_str}")
        print(f"  GARCH:  {results['garch'].get('status', 'N/A')}")
        print(f"  Kalman: {results['kalman'].get('status', 'N/A')}")
        print(f"  EVT:    {results['evt'].get('status', 'N/A')}")
        print(f"  输出:   {date_filename}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
