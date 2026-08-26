"""Phase 5 子模块: iFinD 新闻研判 + 宏观政策评分 + 期权市场快照 (从 daily_workflow.py 拆出, 零行为变更)。

原位置: daily_workflow.py
- _ifind_signal_to_factor (L1896-L1916, 静态)
- _apply_ifind_news_adjustments (L2192-L2275)
- _apply_macro_policy_adjustments (L2277-L2352)
- _options_market_snapshot (L2481-L2501) — 被 phase_execute 跨 phase 调用

搬移内容:
- ifind_signal_to_factor: iFinD 研判方向+置信度 → 订单调整系数
- apply_ifind_news_adjustments: 按 iFinD 新闻研判调整订单
- apply_macro_policy_adjustments: 按十五五/康波宏观评分调整订单
- options_market_snapshot: 期权市场快照 (VIX 从 VixDataSource 获取)

依赖:
- ctx.ifind_analyzer (通过 WorkflowContext 代理)
- src.macro.macro_policy_scoring (运行时 import, 失败降级)
- utils.alpha.vix_data_source (运行时 import, 失败降级)

跨 phase 调用:
- options_market_snapshot 被 phase_execute 调用, daily_workflow.py 保留门面转发
"""
from __future__ import annotations

import logging
from typing import Any

from workflow.context import WorkflowContext

logger = logging.getLogger("v75.daily_workflow")


def ifind_signal_to_factor(direction: str, confidence: float) -> float:
    """将 iFinD 新闻研判映射到订单调整系数

    Args:
        direction: positive / negative / neutral
        confidence: 0.0-1.0 研判置信度

    Returns:
        订单调整系数，例如 1.2 表示加仓 20%，0 表示跳过
    """
    if direction == "positive" and confidence >= 0.7:
        return 1.2
    if direction == "positive" and confidence >= 0.5:
        return 1.0
    if direction == "neutral":
        return 1.0
    if direction == "negative" and confidence >= 0.85:
        return 0.0
    if direction == "negative" and confidence >= 0.7:
        return 0.5
    return 1.0


def apply_ifind_news_adjustments(
    ctx: WorkflowContext,
    *,
    morning_orders: list[dict[str, Any]],
    afternoon_orders: list[dict[str, Any]],
) -> dict[str, Any]:
    """根据 iFinD 新闻/公告研判结果调整订单

    Args:
        ctx: WorkflowContext (代理 ifind_analyzer)

    Returns:
        {
            "morning_orders": [...],
            "afternoon_orders": [...],
            "skip_count": int,
            "boost_count": int,
            "cut_count": int,
        }
    """
    if ctx.ifind_analyzer is None:
        return {}

    symbols = []
    name_map = {}
    for order in morning_orders + afternoon_orders:
        code = str(order.get("code", ""))
        name = str(order.get("name", ""))
        if code and code not in name_map:
            symbols.append(code)
            name_map[code] = name

    if not symbols:
        return {}

    try:
        insights = ctx.ifind_analyzer.batch_analyze(symbols, name_map=name_map, size=4, days=3)
    except Exception:  # fail-safe
        logger.error("iFinD 批量研判失败", exc_info=True)
        return {}

    insight_map = {item.symbol: item for item in insights}

    def _apply(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        adjusted: list[dict[str, Any]] = []
        for order in orders:
            code = str(order.get("code", ""))
            insight = insight_map.get(code)
            if insight is None:
                adjusted.append(dict(order))
                continue

            factor = ifind_signal_to_factor(insight.direction, float(insight.confidence))
            if factor <= 0.0:
                logger.info("iFinD 跳过订单 [%s] %s confidence=%.2f", code, insight.direction, insight.confidence)
                continue

            new_order = dict(order)
            original_shares = int(order.get("shares", 0))
            float(order.get("est_amount", 0))
            new_shares = max(100, int(original_shares * factor / 100) * 100)
            new_order["shares"] = new_shares
            new_order["est_amount"] = round(new_shares * float(order.get("est_price", 0)), 2)
            new_order["original_shares"] = original_shares
            new_order["ifind_direction"] = insight.direction
            new_order["ifind_confidence"] = round(float(insight.confidence), 2)
            new_order["ifind_factor"] = round(factor, 2)
            new_order["ifind_reasons"] = insight.reasons[:3]
            adjusted.append(new_order)

            if factor >= 1.2:
                logger.info("iFinD 加仓 [%s] %s confidence=%.2f -> factor=%.2f, %d 股", code, insight.direction, insight.confidence, factor, new_shares)
            elif factor <= 0.5:
                logger.info("iFinD 减仓 [%s] %s confidence=%.2f -> factor=%.2f, %d 股", code, insight.direction, insight.confidence, factor, new_shares)
        return adjusted

    new_morning = _apply(morning_orders)
    new_afternoon = _apply(afternoon_orders)

    def _count(orders: list[dict[str, Any]], threshold: float) -> int:
        return sum(1 for o in orders if o.get("ifind_factor", 1.0) >= threshold)

    return {
        "morning_orders": new_morning,
        "afternoon_orders": new_afternoon,
        "skip_count": (len(morning_orders) - len(new_morning)) + (len(afternoon_orders) - len(new_afternoon)),
        "boost_count": _count(new_morning, 1.2) + _count(new_afternoon, 1.2),
        "cut_count": _count(new_morning, 0.5) + _count(new_afternoon, 0.5),
    }


def apply_macro_policy_adjustments(
    *,
    morning_orders: list[dict[str, Any]],
    afternoon_orders: list[dict[str, Any]],
    macro_scores: dict[str, Any],
) -> dict[str, Any]:
    """根据十五五/康波宏观评分调整订单

    Args:
        morning_orders: 上午订单
        afternoon_orders: 下午订单
        macro_scores: phase_signal 中计算的宏观评分

    Returns:
        {
            "morning_orders": [...],
            "afternoon_orders": [...],
            "skip_count": int,
            "boost_count": int,
            "cut_count": int,
        }
    """
    if not macro_scores:
        return {}

    try:
        from src.macro.macro_policy_scoring import macro_score_to_factor
    except Exception:  # fail-safe
        return {}

    def _apply(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        adjusted: list[dict[str, Any]] = []
        for order in orders:
            code = str(order.get("code", ""))
            score_info = macro_scores.get(code)
            if not score_info:
                adjusted.append(dict(order))
                continue

            combined = float(getattr(score_info, "combined_score", 1.0))
            factor = macro_score_to_factor(combined)
            if factor <= 0.0:
                logger.info("宏观评分跳过订单 [%s] combined=%.4f", code, combined)
                continue

            new_order = dict(order)
            original_shares = int(order.get("shares", 0))
            float(order.get("est_amount", 0))
            new_shares = max(100, int(original_shares * factor / 100) * 100)
            new_order["shares"] = new_shares
            new_order["est_amount"] = round(new_shares * float(order.get("est_price", 0)), 2)
            new_order["original_shares"] = original_shares
            new_order["macro_combined_score"] = round(combined, 4)
            new_order["macro_factor"] = round(factor, 2)
            new_order["fifteen_five_score"] = round(float(getattr(score_info, "fifteen_five_score", 1.0)), 4)
            new_order["kondratiev_score"] = round(float(getattr(score_info, "kondratiev_score", 1.0)), 4)
            adjusted.append(new_order)

            if factor >= 1.2:
                logger.info("宏观加仓 [%s] combined=%.4f -> factor=%.2f, %d 股", code, combined, factor, new_shares)
            elif factor <= 0.8:
                logger.info("宏观减仓 [%s] combined=%.4f -> factor=%.2f, %d 股", code, combined, factor, new_shares)
        return adjusted

    new_morning = _apply(morning_orders)
    new_afternoon = _apply(afternoon_orders)

    def _count(orders: list[dict[str, Any]], threshold: float) -> int:
        return sum(1 for o in orders if o.get("macro_factor", 1.0) >= threshold)

    return {
        "morning_orders": new_morning,
        "afternoon_orders": new_afternoon,
        "skip_count": (len(morning_orders) - len(new_morning)) + (len(afternoon_orders) - len(new_afternoon)),
        "boost_count": _count(new_morning, 1.2) + _count(new_afternoon, 1.2),
        "cut_count": _count(new_morning, 0.8) + _count(new_afternoon, 0.8),
    }


def options_market_snapshot() -> dict[str, Any]:
    """期权市场快照（最小可用版本）

    实盘应接入期权行情/IV/Greek；当前仅返回占位结构，
    保证 OptionsRunner 可正常执行计划生成流程。
    G13 修复 (2026-08-06): VIX 从 VixDataSource 获取真实值, 非硬编码。
    """
    _vix_default = 18.5
    try:
        from utils.alpha.vix_data_source import fetch_vix
        _vix_fetched = fetch_vix(use_cache=True)
        if _vix_fetched is not None and 5.0 <= _vix_fetched <= 150.0:
            _vix_default = float(_vix_fetched)
    except Exception:  # noqa: BLE001
        pass
    return {
        "vix": _vix_default,
        "market_status": "NORMAL",
        "underlyings": [],
        "note": "最小快照, 实盘应接入期权行情",
    }
