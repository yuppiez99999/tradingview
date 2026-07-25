# -*- coding: utf-8 -*-
"""
V9 + V7.2 Cap 后处理验证脚本
================================

动机:
    V9 回测 (2026-07-25 11:49) 在 P0-8 修复前运行, V7.2 bull regime 5% 上限未触发
    需验证: 应用 V7.2 cap 后, V9 净收益/DSR/Sharpe CV/最大回撤的变化

方法:
    1. 加载 V9 回测记录 (含每月 weights, returns, regime)
    2. 对 bull regime 月份应用 V7.2 cap (5% 上限 + 权重重分配)
    3. 对 bull regime 月份应用 V7.1 高波动惩罚 (vol20 > 4.5% → ×0.5)
    4. 重新计算 portfolio_return = sum(weight * return)
    5. 重新计算年化收益/最大回撤/Sharpe/DSR/Sharpe CV

注意:
    这是后处理近似验证, 与完整重跑的差异:
    - V7.1 惩罚使用 vol20 (从 data_cache 加载真实数据)
    - 权重重分配使用与生产代码相同的按比例重分配逻辑
    - 不重新训练 LGB 模型 (信号相同, 仅权重后处理不同)
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("v9_v72_validation")

BASE_DIR = Path(__file__).resolve().parent

# V7.2/V7.1 参数 (与 institutional_pipeline_runner.py 一致)
_V72_BULL_REGIME_MAX_WEIGHT = 0.05
_V71_BULL_HIGH_VOL_THRESHOLD = 0.045
_V71_BULL_VOL_PENALTY = 0.5


def load_v9_records() -> dict:
    """加载最新 V9 回测记录"""
    report_file = BASE_DIR / "output" / "validation_reports" / "v9_regime_specific_backtest_20260725_114943.json"
    with open(report_file, "r", encoding="utf-8") as f:
        return json.load(f)


def load_vol20(symbol: str, date_str: str) -> float:
    """加载标的在指定日期的 20 日实现波动率"""
    sym_file = BASE_DIR / "data_cache" / f"historical_{symbol}_5y_base.parquet"
    if not sym_file.exists():
        return 0.0
    try:
        df = pd.read_parquet(sym_file)
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df = df.sort_index()
        cutoff = pd.Timestamp(date_str).normalize()
        if hasattr(cutoff, "tz") and cutoff.tz is not None:
            cutoff = cutoff.tz_localize(None)
        df = df[df.index <= cutoff]
        if len(df) < 22:
            return 0.0
        daily_rets = df["close"].pct_change().tail(20)
        return float(daily_rets.std())
    except Exception:
        return 0.0


def apply_v72_cap(weights: dict, returns: dict, date_str: str, regime: str) -> tuple:
    """
    应用 V7.2 cap + V7.1 惩罚, 返回 (新权重, cap_info)

    逻辑与 institutional_pipeline_runner._apply_v72_bull_regime_cap 一致:
      1. V7.1: vol20 > 4.5% 的股票权重 ×0.5
      2. V7.2: 单票上限 5%, 截断释放的权重按比例重分配给未超限的股票
    """
    cap_info = {
        "regime": regime,
        "v72_cap_applied": False,
        "v71_penalty_applied": False,
        "capped_symbols": [],
        "penalized_symbols": [],
    }

    if regime != "bull" or not weights:
        return dict(weights), cap_info

    original_weights = dict(weights)

    # === V7.1: 高波动股权重惩罚 (vol20 > 4.5% → ×0.5) ===
    penalized = {}
    for symbol, w in original_weights.items():
        if w <= 0:
            continue
        vol20 = load_vol20(symbol, date_str)
        if vol20 > _V71_BULL_HIGH_VOL_THRESHOLD:
            penalized[symbol] = vol20

    if penalized:
        for symbol in penalized:
            original_weights[symbol] = original_weights[symbol] * _V71_BULL_VOL_PENALTY
        cap_info["v71_penalty_applied"] = True
        cap_info["penalized_symbols"] = [
            {"symbol": s, "vol20": round(v, 4), "penalty": _V71_BULL_VOL_PENALTY}
            for s, v in penalized.items()
        ]

    # === V7.2: bull regime 单票上限 5% (10% → 5%) ===
    capped_symbols = []
    capped_weights = {}
    excess_weight = 0.0
    for symbol, w in original_weights.items():
        if w > _V72_BULL_REGIME_MAX_WEIGHT:
            excess_weight += (w - _V72_BULL_REGIME_MAX_WEIGHT)
            capped_symbols.append({"symbol": symbol, "before": round(w, 4), "after": _V72_BULL_REGIME_MAX_WEIGHT})
            capped_weights[symbol] = _V72_BULL_REGIME_MAX_WEIGHT
        else:
            capped_weights[symbol] = w

    if capped_symbols:
        # 将截断释放的权重按比例重分配给未超限的股票
        capped_set = {c["symbol"] for c in capped_symbols}
        non_capped_total = sum(w for s, w in capped_weights.items() if s not in capped_set)
        if non_capped_total > 0:
            for symbol in capped_weights:
                if symbol not in capped_set:
                    capped_weights[symbol] += excess_weight * (capped_weights[symbol] / non_capped_total)
        cap_info["v72_cap_applied"] = True
        cap_info["capped_symbols"] = capped_symbols
    elif penalized:
        # 仅 V7.1 惩罚, 无 V7.2 截断, 仍需更新 weights
        pass

    return capped_weights, cap_info


def compute_portfolio_return(weights: dict, returns: dict) -> float:
    """计算组合月度收益 = sum(weight * return)"""
    total = 0.0
    for symbol, w in weights.items():
        if symbol in returns:
            total += w * returns[symbol]
    return total


def compute_metrics(monthly_returns: list) -> dict:
    """计算年化收益/最大回撤/Sharpe/胜率等指标"""
    if not monthly_returns:
        return {}

    returns = np.array(monthly_returns)
    n = len(returns)

    # 累计净值
    nav = np.cumprod(1 + returns)
    total_return = float(nav[-1] - 1)

    # 年化收益 (假设每月 21 个交易日, 一年 252 天)
    years = n / 12
    annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0

    # 最大回撤
    peak = np.maximum.accumulate(nav)
    drawdown = (nav - peak) / peak
    max_drawdown = float(abs(min(drawdown))) if len(drawdown) > 0 else 0

    # Sharpe (年化, 假设无风险利率 0)
    monthly_mean = float(np.mean(returns))
    monthly_std = float(np.std(returns, ddof=1)) if n > 1 else 0
    sharpe_annual = (monthly_mean / monthly_std) * math.sqrt(12) if monthly_std > 0 else 0

    # 胜率
    win_rate = float(np.mean(returns > 0))

    # 偏度/峰度
    skewness = float(pd.Series(returns).skew()) if n > 2 else 0
    kurtosis = float(pd.Series(returns).kurtosis()) if n > 3 else 0

    # === Sharpe CV (12 月滚动) ===
    rolling_window = 12
    rolling_sharpes = []
    if n >= rolling_window:
        for i in range(n - rolling_window + 1):
            window = returns[i : i + rolling_window]
            w_mean = float(np.mean(window))
            w_std = float(np.std(window, ddof=1))
            if w_std > 0:
                rolling_sharpes.append(w_mean / w_std * math.sqrt(12))
    sharpe_cv_rolling = float(np.std(rolling_sharpes) / abs(np.mean(rolling_sharpes))) if rolling_sharpes and abs(np.mean(rolling_sharpes)) > 0 else float("inf")

    # === DSR (Deflated Sharpe Ratio) ===
    # Bailey & López de Prado (2014) 公式
    # 使用 n_trials=10 (保守估计)
    n_trials = 10
    sharpe_observed = sharpe_annual
    # SR_0 = E[max(N)] * sigma_SR
    # E[max(N)] ≈ sqrt(2*ln(n_trials)) (n_trials > 1)
    if n_trials > 1:
        sr_std = math.sqrt((1 - skewness * monthly_mean + (kurtosis / 4) * monthly_mean**2) / (n - 1)) if n > 1 else 0
        sr_max_expected = math.sqrt(2 * math.log(n_trials)) * sr_std if sr_std > 0 else 0
    else:
        sr_std = 0
        sr_max_expected = 0

    if sr_std > 0:
        dsr = float((sharpe_observed - sr_max_expected) / sr_std * math.sqrt(12))
    else:
        dsr = 0.0

    # DSR p-value (单边正态检验)
    from scipy import stats as scipy_stats
    try:
        dsr_p_value = float(1 - scipy_stats.norm.cdf(dsr))
    except Exception:
        dsr_p_value = 0.5

    # === max_pass (DSR 在 n_trials=1..20 下通过的最大次数) ===
    max_pass = 0
    for nt in range(1, 21):
        if nt > 1:
            sr_max_nt = math.sqrt(2 * math.log(nt)) * sr_std if sr_std > 0 else 0
        else:
            sr_max_nt = 0
        if sr_std > 0:
            dsr_nt = (sharpe_observed - sr_max_nt) / sr_std * math.sqrt(12)
        else:
            dsr_nt = 0
        try:
            p_nt = float(1 - scipy_stats.norm.cdf(dsr_nt))
        except Exception:
            p_nt = 0.5
        if p_nt < 0.05 and dsr_nt > 0:
            max_pass = nt
        else:
            break

    return {
        "n_months": n,
        "total_return": round(total_return, 4),
        "annual_return": round(annual_return, 4),
        "max_drawdown": round(max_drawdown, 4),
        "sharpe_annual": round(sharpe_annual, 4),
        "win_rate": round(win_rate, 4),
        "skewness": round(skewness, 4),
        "kurtosis": round(kurtosis, 4),
        "sharpe_cv_rolling": round(sharpe_cv_rolling, 4),
        "rolling_sharpes_count": len(rolling_sharpes),
        "dsr_at_10": round(dsr, 4),
        "dsr_p_value_at_10": round(dsr_p_value, 4),
        "dsr_max_pass": max_pass,
    }


def main() -> None:
    """主入口: V9 + V7.2 cap 后处理验证"""
    logger.info("=" * 80)
    logger.info("V9 + V7.2 Cap 后处理验证")
    logger.info("=" * 80)

    # 1. 加载 V9 记录
    v9_data = load_v9_records()
    records = v9_data.get("records", [])
    logger.info("加载 V9 记录: %d 个月", len(records))

    # 2. 原始指标 (V9 无 V7.2 cap)
    original_returns = [r.get("portfolio_return", 0) for r in records]
    original_metrics = compute_metrics(original_returns)
    logger.info("\n=== 原始 V9 指标 (无 V7.2 cap) ===")
    for k, v in original_metrics.items():
        logger.info("  %s: %s", k, v)

    # 3. 应用 V7.2 cap + V7.1 惩罚
    adjusted_returns = []
    cap_summary = {"bull_months": 0, "v72_applied": 0, "v71_applied": 0, "capped_total": 0, "penalized_total": 0}
    monthly_details = []

    for rec in records:
        date = rec.get("date", "")
        regime = rec.get("market_regime", {}).get("regime", "unknown")
        weights = rec.get("weights", {})
        returns = rec.get("returns", {})
        original_pr = rec.get("portfolio_return", 0)

        if regime == "bull":
            cap_summary["bull_months"] += 1

        new_weights, cap_info = apply_v72_cap(weights, returns, date, regime)
        new_pr = compute_portfolio_return(new_weights, returns)

        if cap_info["v72_cap_applied"]:
            cap_summary["v72_applied"] += 1
            cap_summary["capped_total"] += len(cap_info["capped_symbols"])
        if cap_info["v71_penalty_applied"]:
            cap_summary["v71_applied"] += 1
            cap_summary["penalized_total"] += len(cap_info["penalized_symbols"])

        adjusted_returns.append(new_pr)
        monthly_details.append({
            "date": date,
            "regime": regime,
            "original_return": round(original_pr, 4),
            "adjusted_return": round(new_pr, 4),
            "delta": round(new_pr - original_pr, 4),
            "v72_cap_applied": cap_info["v72_cap_applied"],
            "v71_penalty_applied": cap_info["v71_penalty_applied"],
            "capped_symbols": cap_info["capped_symbols"],
            "penalized_symbols": cap_info["penalized_symbols"],
        })

    logger.info("\n=== V7.2 Cap 应用统计 ===")
    for k, v in cap_summary.items():
        logger.info("  %s: %s", k, v)

    # 4. 调整后指标
    adjusted_metrics = compute_metrics(adjusted_returns)
    logger.info("\n=== 调整后 V9 指标 (V9 + V7.2 cap + V7.1 惩罚) ===")
    for k, v in adjusted_metrics.items():
        logger.info("  %s: %s", k, v)

    # 5. 对比
    logger.info("\n=== 关键指标对比 ===")
    comparison = [
        ("年化收益", original_metrics["annual_return"], adjusted_metrics["annual_return"]),
        ("最大回撤", original_metrics["max_drawdown"], adjusted_metrics["max_drawdown"]),
        ("Sharpe (年化)", original_metrics["sharpe_annual"], adjusted_metrics["sharpe_annual"]),
        ("胜率", original_metrics["win_rate"], adjusted_metrics["win_rate"]),
        ("Sharpe CV (12月滚动)", original_metrics["sharpe_cv_rolling"], adjusted_metrics["sharpe_cv_rolling"]),
        ("DSR max_pass", original_metrics["dsr_max_pass"], adjusted_metrics["dsr_max_pass"]),
        ("偏度", original_metrics["skewness"], adjusted_metrics["skewness"]),
        ("峰度", original_metrics["kurtosis"], adjusted_metrics["kurtosis"]),
    ]
    logger.info("%-25s %15s %15s %10s", "指标", "原始 V9", "V9+V7.2", "变化")
    logger.info("-" * 70)
    for name, orig, adj in comparison:
        delta = adj - orig
        arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
        # 对于回撤/Sharpe CV/峰度, 降低是好的
        if name in ("最大回撤", "Sharpe CV (12月滚动)", "峰度"):
            arrow = "↓好" if delta < 0 else ("↑坏" if delta > 0 else "→")
        logger.info("%-25s %15s %15s %10s", name, f"{orig:.4f}", f"{adj:.4f}", f"{delta:+.4f} {arrow}")

    # 6. 月度对比 (仅显示 bull 月份)
    logger.info("\n=== Bull 月份月度收益对比 ===")
    logger.info("%-12s %-8s %12s %12s %10s %s", "日期", "regime", "原始收益", "调整后收益", "变化", "V7.2/V7.1")
    logger.info("-" * 80)
    for d in monthly_details:
        if d["regime"] == "bull":
            v72 = "V72" if d["v72_cap_applied"] else ""
            v71 = "V71" if d["v71_penalty_applied"] else ""
            logger.info("%-12s %-8s %12s %12s %10s %s",
                        d["date"], d["regime"],
                        f"{d['original_return']:.4f}", f"{d['adjusted_return']:.4f}",
                        f"{d['delta']:+.4f}",
                        f"{v72} {v71}".strip())

    # 7. 验收检查 (V9 综合评估标准)
    logger.info("\n=== V9 综合评估标准验收 ===")
    checks = [
        ("年化收益 >= 15%", adjusted_metrics["annual_return"] >= 0.15, f"{adjusted_metrics['annual_return']:.2%}"),
        ("最大回撤 <= 10%", adjusted_metrics["max_drawdown"] <= 0.10, f"{adjusted_metrics['max_drawdown']:.2%}"),
        ("DSR max_pass >= 5", adjusted_metrics["dsr_max_pass"] >= 5, f"max_pass={adjusted_metrics['dsr_max_pass']}"),
        ("Sharpe CV < 1.0", adjusted_metrics["sharpe_cv_rolling"] < 1.0, f"{adjusted_metrics['sharpe_cv_rolling']:.4f}"),
    ]
    all_pass = True
    for name, passed, detail in checks:
        status = "✅ PASS" if passed else "❌ FAIL"
        logger.info("  %s: %s (%s)", name, status, detail)
        if not passed:
            all_pass = False
    logger.info("\n综合验收: %s", "✅ 全部达标" if all_pass else "❌ 未达标")

    # 8. 保存结果
    result = {
        "description": "V9 + V7.2 Cap 后处理验证",
        "method": "后处理近似: 应用 V7.2 cap (5% 上限) + V7.1 惩罚 (vol20>4.5% → ×0.5) 到 V9 原始权重, 重新计算收益",
        "caveats": [
            "未重新训练 LGB 模型 (信号相同, 仅权重后处理不同)",
            "V7.1 惩罚使用 data_cache 中的真实 vol20 数据",
            "权重重分配逻辑与 institutional_pipeline_runner._apply_v72_bull_regime_cap 一致",
        ],
        "cap_summary": cap_summary,
        "original_metrics": original_metrics,
        "adjusted_metrics": adjusted_metrics,
        "monthly_details": monthly_details,
        "acceptance": {
            "all_pass": all_pass,
            "checks": [{"name": n, "passed": p, "detail": d} for n, p, d in checks],
        },
    }

    out_file = BASE_DIR / "output" / "validation_reports" / "v9_v72_cap_postvalidation.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    logger.info("\n结果已保存: %s", out_file)


if __name__ == "__main__":
    main()
