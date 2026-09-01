"""
ai_decision.execution_tca — TCA (交易成本分析) 双轨
====================================================

从 execution_bridge.py 拆分 (v8.6 重构, 接口完全不变).

包含:
  - _tca_pre_trade_enabled / _tca_post_trade_enabled: Feature Flag
  - _build_fills_from_execution: 从 execution_result 构造 FillRecord
  - _build_benchmark_from_market_data: 构造 BenchmarkPrices
  - _tca_report_to_dict: TCAReport 序列化
  - _run_tca_pre_trade: TCA 执行前预筛
  - _run_tca_post_trade: TCA 事后归因

注意: _run_tca_pre_trade / _run_tca_post_trade 通过 execution_bridge 模块引用
调用 _tca_pre_trade_enabled / _tca_post_trade_enabled, 以支持测试 monkey patch
(eb._tca_pre_trade_enabled = lambda: True).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ai_decision.models import TradingDecision

logger = logging.getLogger("ai_decision.execution_tca")


# ============================================================
# TCA Feature Flag (步骤 2: 双轨独立, 与 utils/execution_router 解耦)
# ============================================================


def _tca_pre_trade_enabled() -> bool:
    """USE_AI_DECISION_TCA_PRE_TRADE Feature Flag (默认 False, fail-safe)"""
    try:
        from utils.infra.feature_flags import is_enabled

        return bool(is_enabled("USE_AI_DECISION_TCA_PRE_TRADE"))
    except (ImportError, AttributeError, TypeError, KeyError):
        return False


def _tca_post_trade_enabled() -> bool:
    """USE_AI_DECISION_TCA_POST_TRADE Feature Flag (默认 False, fail-safe)"""
    try:
        from utils.infra.feature_flags import is_enabled

        return bool(is_enabled("USE_AI_DECISION_TCA_POST_TRADE"))
    except (ImportError, AttributeError, TypeError, KeyError):
        return False


# ============================================================
# TCA 辅助函数 (构造 FillRecord / BenchmarkPrices / 报告序列化)
# ============================================================


def _build_fills_from_execution(
    execution_plan: dict[str, Any],
    execution_result: dict[str, Any],
) -> list[Any]:
    """从 execution_result 构造 FillRecord 列表 (供 TCAManager.analyze 使用)

    兼容 paper 模式 (_simulate_fill 返回 average_price) 和 auto 模式 (routed_orders)
    """
    try:
        from utils.tca_engine import FillRecord
    except ImportError:
        return []

    symbol = execution_plan.get("symbol", "")
    side = execution_plan.get("side", "BUY")
    qty = int(execution_plan.get("qty", 0))

    # paper 模式: average_price 字段
    avg_price = (
        execution_result.get("average_price")
        or execution_result.get("avg_price")
        or 0.0
    )
    # auto 模式: 从 routed_orders 提取成交价
    if not avg_price:
        routed = execution_result.get("routed_orders", [])
        if isinstance(routed, list):
            for order in routed:
                if isinstance(order, dict) and order.get("price"):
                    avg_price = float(order["price"])
                    break

    if not avg_price or avg_price <= 0 or qty <= 0:
        return []

    timestamp = execution_result.get("timestamp", datetime.now().isoformat())
    return [
        FillRecord(
            symbol=symbol,
            side=side,
            shares=qty,
            price=float(avg_price),
            timestamp=timestamp,
        )
    ]


def _build_benchmark_from_market_data(
    market_data: dict[str, Any] | None,
    execution_plan: dict[str, Any],
) -> Any | None:
    """从 market_data 构造 BenchmarkPrices (供 TCAManager.analyze 使用)

    Args:
        market_data: {decision_price, arrival_price, vwap, close_price, market_cap, adv, volatility}
        execution_plan: 兜底取 limit_price 作为 decision_price
    Returns:
        BenchmarkPrices 或 None (数据不足时)
    """
    if not market_data:
        return None
    try:
        from utils.tca_engine import BenchmarkPrices
    except ImportError:
        return None

    # 决策价 = 下单时刻参考价 (兜底用 limit_price)
    decision_price = float(
        market_data.get("decision_price", execution_plan.get("limit_price", 0))
    )
    if decision_price <= 0:
        return None

    arrival_price = float(market_data.get("arrival_price", decision_price))
    vwap = float(market_data.get("vwap", 0))
    close_price = float(market_data.get("close_price", 0))

    return BenchmarkPrices(
        decision_price=decision_price,
        arrival_price=arrival_price,
        vwap=vwap,
        close_price=close_price,
    )


def _tca_report_to_dict(report: Any) -> dict[str, Any]:
    """将 TCAReport 转为 dict (兼容 dataclass + 自定义 to_dict)"""
    try:
        if hasattr(report, "to_dict"):
            return report.to_dict()
        from dataclasses import asdict

        return asdict(report)
    except (TypeError, ValueError, AttributeError, ImportError):
        # to_dict 抛 TypeError/ValueError, asdict 抛 TypeError (非 dataclass), ImportError 防御
        return {
            "symbol": getattr(report, "symbol", ""),
            "quality_grade": getattr(report, "quality_grade", ""),
            "is_cost_bps": getattr(report, "is_cost_bps", 0.0),
        }


# ============================================================
# TCA 执行前预筛 / 事后归因
# ============================================================


def _run_tca_pre_trade(
    decision: TradingDecision,
    execution_plan: dict[str, Any],
    market_data_for_tca: dict[str, Any] | None,
    tca_pre_trade_estimator: Any,
) -> tuple[dict[str, Any] | None, bool, str, str]:
    """TCA 执行前预筛

    预筛否决是软阈值 (escalation 而非 veto)。
    异常隔离: TCA 异常仅记日志 + tca_error, 主路径不阻断 (fail-safe)。

    注意: 通过 execution_bridge 模块引用调用 _tca_pre_trade_enabled,
    以支持测试 monkey patch (eb._tca_pre_trade_enabled = lambda: True).
    """
    tca_pre_estimate: dict[str, Any] | None = None
    tca_error = ""
    escalation = False
    escalation_reason = ""

    # 通过 execution_bridge 模块引用调用, 支持 monkey patch
    from ai_decision import execution_bridge

    if (
        execution_bridge._tca_pre_trade_enabled()
        and tca_pre_trade_estimator is not None
    ):
        try:
            tca_order = {
                "symbol": execution_plan["symbol"],
                "side": execution_plan["side"],
                "shares": execution_plan["qty"],
                "price": execution_plan["limit_price"],
                "notional": execution_plan["notional"],
                "market_cap": (market_data_for_tca or {}).get("market_cap"),
            }
            estimate = tca_pre_trade_estimator.estimate(tca_order, market_data_for_tca)
            tca_pre_estimate = estimate.to_dict()
            if not estimate.approved:
                escalation = True
                escalation_reason = f"TCA 预筛否决: {estimate.rejection_reason}"
                logger.warning(
                    "[TCA-PreTrade] %s 预筛否决: %s (cost=%.2f bps)",
                    decision.symbol,
                    estimate.rejection_reason,
                    estimate.estimated_cost_bps,
                )
            else:
                logger.info(
                    "[TCA-PreTrade] %s 预筛通过 (cost=%.2f bps, tier=%s)",
                    decision.symbol,
                    estimate.estimated_cost_bps,
                    estimate.tier,
                )
        except (ValueError, KeyError, AttributeError, TypeError, RuntimeError) as exc:
            # TCA 预筛可能抛出的具体异常: 参数错误/字段缺失/估算失败
            logger.error("[ExecutionBridge] TCA 预筛异常 (降级为不预估): %s", exc)
            tca_error = f"pre_trade: {exc}"

    return tca_pre_estimate, escalation, escalation_reason, tca_error


def _run_tca_post_trade(
    tca_post_trade_manager: Any,
    execution_plan: dict[str, Any],
    execution_result: dict[str, Any] | None,
    market_data_for_tca: dict[str, Any] | None,
    decision: TradingDecision,
) -> tuple[dict[str, Any] | None, str]:
    """TCA 事后归因

    仅执行成功后调用。异常隔离: 归因异常仅记日志 + tca_error, 主路径不阻断。

    注意: 通过 execution_bridge 模块引用调用 _tca_post_trade_enabled,
    以支持测试 monkey patch (eb._tca_post_trade_enabled = lambda: True).
    """
    tca_post_report: dict[str, Any] | None = None
    tca_error = ""

    # 通过 execution_bridge 模块引用调用, 支持 monkey patch
    from ai_decision import execution_bridge

    if not (
        execution_bridge._tca_post_trade_enabled()
        and tca_post_trade_manager is not None
        and execution_result is not None
        and execution_result.get("success", True)
    ):
        return tca_post_report, tca_error

    try:
        fills = _build_fills_from_execution(execution_plan, execution_result)
        benchmark = _build_benchmark_from_market_data(
            market_data_for_tca, execution_plan
        )
        if fills and benchmark:
            report = tca_post_trade_manager.analyze(
                fills=fills,
                benchmark=benchmark,
                order_shares=execution_plan.get("qty"),
            )
            tca_post_report = _tca_report_to_dict(report)
            logger.info(
                "[TCA-PostTrade] %s 归因完成: IS=%.2f bps, grade=%s",
                decision.symbol,
                getattr(report, "is_cost_bps", 0.0),
                getattr(report, "quality_grade", "N/A"),
            )
        else:
            logger.debug(
                "[TCA-PostTrade] %s 跳过归因 (fills/benchmark 数据不足)",
                decision.symbol,
            )
    except (ValueError, KeyError, AttributeError, TypeError, RuntimeError) as exc:
        # TCA 事后归因可能抛出的具体异常: 参数错误/字段缺失/分析失败
        logger.error("[ExecutionBridge] TCA 事后归因异常: %s", exc)
        tca_error = f"post_trade: {exc}"

    return tca_post_report, tca_error
