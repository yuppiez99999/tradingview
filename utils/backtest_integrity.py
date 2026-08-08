"""
回测完整性守卫 (Backtest Integrity Guard)
=========================================

顶级对冲基金标准：回测必须用真实历史行情与真实 alpha；任何前视偏差
(look-ahead bias) 或 mock alpha 都不能被报告为“有效回测”。

此前致命缺陷：
- output/institutional_pipeline/ 下存在命名为 2027/2028 的 pipeline_backtest.json，
  其内部 alpha_evaluation 的 report_date 实为 2026-07-18、category="mock"、
  active_factors=0。即所谓“回测”是用伪信号重跑实时管道，而非真实历史模拟。
- _step_alpha_evaluation 永远调用 _mock_factor_result()，却仍把结果落盘为
  pipeline_backtest.json 并可能被判读为有效回测。

本模块提供：
- check_no_future_leakage: 校验价格序列不含 as_of_date 之后的未来数据
- evaluate_alpha_provenance: 判定 alpha 来源为 real / mock
- validate_backtest: 综合判定回测是否有效，无效则明确标记，禁止冒充有效回测
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("backtest_integrity")


def check_no_future_leakage(prices, as_of_date) -> tuple[bool, str]:
    """校验价格序列索引是否含 as_of_date 之后的数据（前视偏差检测）。

    Args:
        prices: pd.Series / pd.DataFrame，index 为日期
        as_of_date: 回测截止日期 (str / pd.Timestamp)
    Returns:
        (无泄漏, 说明)
    """
    if prices is None or len(prices) == 0:
        return True, "无价格数据，跳过泄漏检测"
    try:
        import pandas as pd

        # BUG 修复 (2026-08-01 顶级对冲基金重跑验证发现):
        # 原代码 cutoff 未做 tz 规范化, 当 as_of_date 是 tz-aware Timestamp
        # 或 idx 元素为 tz-aware Timestamp 时, 比较抛
        # "Cannot compare tz-naive and tz-aware timestamps", 触发 fail-closed
        # 但这是检测器本身的 bug, 不是数据真的泄漏, 导致所有回测月份都被标记 invalid。
        # 修复: 统一两侧都强制 tz-naive 后再比较。
        cutoff = pd.Timestamp(as_of_date).normalize()
        if hasattr(cutoff, "tz") and cutoff.tz is not None:
            cutoff = cutoff.tz_localize(None)

        idx = prices.index
        if hasattr(idx, "tz") and idx.tz is not None:
            idx = idx.tz_localize(None)

        # 单个元素也强制 tz-naive (部分 pandas 版本下 idx.tz_localize(None)
        # 不会改变迭代元素的 tz 属性, 需逐个规范化)
        def _to_naive_ts(d):
            ts = pd.Timestamp(d)
            if hasattr(ts, "tz") and ts.tz is not None:
                ts = ts.tz_localize(None)
            return ts.normalize()

        future = [d for d in idx if _to_naive_ts(d) > cutoff]
        if future:
            return False, f"检测到 {len(future)} 条 as_of_date 之后的未来数据（前视偏差）"
        return True, "无未来数据泄漏"
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # pragma: no cover
        # 修复 BUG-R6: fail-open → fail-closed, 检测器崩溃时视为不通过
        # 原代码: return True (通过) — 有泄漏但检测器崩溃时系统会说"安全"
        return False, f"泄漏检测异常（fail-closed, 需人工介入）: {e}"


def evaluate_alpha_provenance(alpha_report: Any) -> str:
    """判定 alpha 来源。

    Returns:
        "real" / "mock" / "unknown"
    """
    if alpha_report is None:
        return "unknown"

    # dict 形态
    if isinstance(alpha_report, dict):
        if alpha_report.get("category") == "mock":
            return "mock"
        if alpha_report.get("category") == "real":
            return "real"
        active = alpha_report.get("active_factors", 0)
        evals = alpha_report.get("evaluations", []) or []
        real_evals = [e for e in evals if isinstance(e, dict) and e.get("category") != "mock"]
        if real_evals and active > 0:
            return "real"
        if active == 0 or (evals and all((e.get("category") == "mock") for e in evals if isinstance(e, dict))):
            return "mock"
        return "unknown"

    # 对象形态（AlphaEvaluationReport）
    category = getattr(alpha_report, "category", None)
    active = getattr(alpha_report, "active_factors", None)
    if category == "mock":
        return "mock"
    if category == "real":
        return "real"
    if active is not None and active == 0:
        return "mock"
    return "unknown"


def validate_backtest(
    as_of_date: str,
    alpha_report: Any,
    prices: dict[str, Any] | None = None,
    require_real_alpha: bool = True,
) -> tuple[bool, list[str]]:
    """综合判定回测是否有效。

    Args:
        as_of_date: 回测截止日期
        alpha_report: alpha 评估报告（dict 或对象）
        prices: {symbol: price_series} 用于前视偏差检测
        require_real_alpha: 是否要求真实 alpha（默认 True）

    Returns:
        (是否有效, 问题清单)
    """
    issues: list[str] = []

    # 1) 前视偏差
    if prices:
        for sym, series in prices.items():
            ok, reason = check_no_future_leakage(series, as_of_date)
            if not ok:
                issues.append(f"[{sym}] {reason}")
    else:
        issues.append("未提供价格数据，无法验证前视偏差")

    # 2) alpha 来源
    provenance = evaluate_alpha_provenance(alpha_report)
    if require_real_alpha and provenance != "real":
        issues.append(f"alpha 来源为 '{provenance}'，非真实信号；按尽职调查标准，该结果不得作为有效回测/收益证据")

    is_valid = len(issues) == 0
    if not is_valid:
        logger.warning("[BacktestIntegrity] 回测无效: %s", issues)
    return is_valid, issues
