"""Phase 5 子模块: Qlib 深度学习信号生成与转换 (从 daily_workflow.py 拆出, 零行为变更)。

原位置: daily_workflow.py
- _qlib_signal_to_factor (L1812-L1829, 静态)
- _qlib_signals_to_adjustments (L1831-L1893)
- _generate_qlib_signals (L2386-L2444)
- _generate_mock_ohlcv (L2446-L2479, 静态)

搬移内容:
- qlib_signal_to_factor: Qlib 信号 [-1,1] → 订单调整系数
- qlib_signals_to_adjustments: 按 Qlib 信号调整订单股数
- generate_qlib_signals: 为交易计划标的生成 Qlib 信号 (iFinD 数据 + 模拟兜底)
- generate_mock_ohlcv: 几何布朗运动生成模拟 OHLCV

依赖:
- ctx.trade_plan / ctx.signal_fusion (通过 WorkflowContext 代理)
- alpha.qlib_signal_adapter (运行时 import, 失败降级)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from workflow.context import WorkflowContext

logger = logging.getLogger("v75.daily_workflow")


def qlib_signal_to_factor(signal_value: float) -> float:
    """将 Qlib 信号映射到订单调整系数

    Args:
        signal_value: [-1, 1] 标准化信号

    Returns:
        订单调整系数，例如 1.3 表示加仓 30%，0 表示跳过
    """
    if signal_value >= 0.5:
        return 1.3
    if signal_value >= 0.15:
        return 1.0
    if signal_value > -0.15:
        return 0.8
    if signal_value > -0.5:
        return 0.5
    return 0.0


def qlib_signals_to_adjustments(
    qlib_signals: dict[str, float],
    *,
    morning_orders: list[dict[str, Any]],
    afternoon_orders: list[dict[str, Any]],
) -> dict[str, Any]:
    """按 Qlib 信号调整订单：强看多加仓、中性维持、看空减仓或跳过

    Returns:
        {
            "morning_orders": [...],
            "afternoon_orders": [...],
            "skip_count": int,
            "boost_count": int,
            "cut_count": int,
        }
    """
    if not qlib_signals:
        return {}

    def _apply(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        adjusted: list[dict[str, Any]] = []
        for order in orders:
            code = str(order.get("code", ""))
            signal_value = qlib_signals.get(code)
            if signal_value is None:
                adjusted.append(dict(order))
                continue

            factor = qlib_signal_to_factor(float(signal_value))
            if factor <= 0.0:
                logger.info("Qlib 跳过订单 [%s] signal=%+.4f", code, signal_value)
                continue

            new_order = dict(order)
            original_shares = int(order.get("shares", 0))
            float(order.get("est_amount", 0))
            new_shares = max(100, int(original_shares * factor / 100) * 100)
            new_order["shares"] = new_shares
            new_order["est_amount"] = round(new_shares * float(order.get("est_price", 0)), 2)
            new_order["original_shares"] = original_shares
            new_order["qlib_signal"] = round(float(signal_value), 4)
            new_order["qlib_factor"] = round(factor, 2)
            adjusted.append(new_order)

            if factor >= 1.3:
                logger.info("Qlib 加仓 [%s] signal=%+.4f -> factor=%.2f, %d 股", code, signal_value, factor, new_shares)
            elif factor <= 0.5:
                logger.info("Qlib 减仓 [%s] signal=%+.4f -> factor=%.2f, %d 股", code, signal_value, factor, new_shares)
        return adjusted

    new_morning = _apply(morning_orders)
    new_afternoon = _apply(afternoon_orders)

    def _count(orders, threshold):
        return sum(1 for o in orders if o.get("qlib_factor", 1.0) >= threshold)

    return {
        "morning_orders": new_morning,
        "afternoon_orders": new_afternoon,
        "skip_count": (len(morning_orders) - len(new_morning)) + (len(afternoon_orders) - len(new_afternoon)),
        "boost_count": _count(new_morning, 1.3) + _count(new_afternoon, 1.3),
        "cut_count": _count(new_morning, 0.5) + _count(new_afternoon, 0.5),
    }


def generate_qlib_signals(ctx: WorkflowContext) -> dict[str, float]:
    """为交易计划中的标的生成 Qlib 深度学习信号

    Args:
        ctx: WorkflowContext (代理 trade_plan / signal_fusion)

    Returns:
        {symbol: signal_value} 信号值在 [-1, 1] 区间
    """
    try:
        from alpha.qlib_signal_adapter import (
            fetch_ifind_ohlcv,
            generate_signal,
            is_qlib_available,
        )
    except ImportError:
        logger.warning("Qlib 信号适配器不可用")
        return {}

    if not is_qlib_available():
        logger.info("Qlib 不可用，使用本地 LightGBM 信号")

    signals = {}
    # 从交易计划中提取标的
    plan_exec = ctx.trade_plan.get("execution_plan", {}) if ctx.trade_plan else {}
    all_orders = plan_exec.get("morning_orders", []) + plan_exec.get("afternoon_orders", [])

    # 去重标的
    symbols = list(dict.fromkeys(o.get("code", "") for o in all_orders if o.get("code")))

    for symbol in symbols:  # 处理全部标的
        try:
            # 优先 iFinD 真实数据，失败则回退模拟数据
            df = fetch_ifind_ohlcv(symbol, days=500)
            if df is None or len(df) < 60:
                logger.debug(f"iFinD 数据不足，使用模拟数据 [{symbol}]")
                df = generate_mock_ohlcv(symbol, days=500)

            if df is None or len(df) < 60:
                continue

            signal = generate_signal(df, symbol, model_type="lightgbm")
            if signal is not None and len(signal) > 0:
                # 取最新信号值
                latest_signal = float(signal.iloc[-1])
                signals[symbol] = round(latest_signal, 4)
                logger.info(f"Qlib 信号 [{symbol}]: {latest_signal:+.4f}")
        except Exception as e:
            logger.warning(f"Qlib 信号生成失败 [{symbol}]: {e}")

    # === 将 Qlib 信号注入 SignalFusion ===
    if signals and hasattr(ctx, 'signal_fusion'):
        try:
            from alpha.qlib_signal_adapter import pd as qlib_pd
            # 构造等权信号序列（用于融合）
            signal_series = qlib_pd.Series(list(signals.values()), index=list(signals.keys()))
            ctx.signal_fusion.inject_qlib_signal(signal_series)
            logger.info(f"Qlib 信号已注入 SignalFusion: {len(signals)} 个标的")
        except Exception as e:
            logger.warning(f"Qlib 信号注入 SignalFusion 失败: {e}")

    return signals


def generate_mock_ohlcv(symbol: str, days: int = 120) -> Optional[Any]:
    """生成模拟 OHLCV 数据 (用于 Qlib 演示)

    Args:
        symbol: 标的代码
        days: 数据天数

    Returns:
        DataFrame 或 None
    """
    try:
        import numpy as np
        import pandas as pd
        np.random.seed(hash(symbol) % (2**32))

        dates = pd.date_range(end=datetime.now(), periods=days, freq="B")
        base_price = 50 + np.random.random() * 100

        # 几何布朗运动模拟
        returns = np.random.normal(0.0005, 0.02, days)
        prices = base_price * np.exp(np.cumsum(returns))

        df = pd.DataFrame({
            "open": prices * (1 + np.random.normal(0, 0.005, days)),
            "high": prices * (1 + np.abs(np.random.normal(0, 0.01, days))),
            "low": prices * (1 - np.abs(np.random.normal(0, 0.01, days))),
            "close": prices,
            "volume": np.random.randint(1000000, 10000000, days),
        }, index=dates)

        return df
    except Exception as e:
        logger.warning(f"模拟数据生成失败 [{symbol}]: {e}")
        return None
