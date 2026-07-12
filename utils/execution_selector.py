# -*- coding: utf-8 -*-
"""
智能执行算法选择器

基于交易成本模型自动选择最优执行算法：
- 对每笔订单评估 min_impact / twap / vwap / immediate
- 优先选择综合成本最低、执行时间可接受的方案
- 供 daily_trade_executor 和 hedge_execution_orders 使用
"""

from __future__ import annotations

from typing import Dict, Optional


def choose_execution_algorithm(target_amount: float,
                               ref_price: float,
                               avg_daily_volume: float = 0,
                               max_execution_minutes: Optional[float] = None,
                               cost_weight: float = 0.7,
                               time_weight: float = 0.3) -> Dict:
    """选择最优执行算法

    Args:
        target_amount: 目标金额
        ref_price: 参考价格
        avg_daily_volume: 日均成交量
        max_execution_minutes: 最大可接受执行时间（分钟）
        cost_weight: 成本权重
        time_weight: 时间权重

    Returns:
        {
            algorithm,
            reason,
            estimated_cost,
            cost_bps,
            execution_time_minutes,
            orders,
            comparison
        }
    """
    try:
        from utils.wt_execution_algo import compare_execution
        comparison = compare_execution(target_amount, ref_price, avg_daily_volume)
    except Exception as e:
        return {
            "algorithm": "immediate",
            "reason": f"执行算法比较失败，回退 immediate: {e}",
            "estimated_cost": 0.0,
            "cost_bps": 0.0,
            "execution_time_minutes": 0.0,
            "orders": [],
            "comparison": {},
        }

    if not comparison:
        return {
            "algorithm": "immediate",
            "reason": "无可比较的执行算法结果",
            "estimated_cost": 0.0,
            "cost_bps": 0.0,
            "execution_time_minutes": 0.0,
            "orders": [],
            "comparison": {},
        }

    candidates = []
    for algo, result in comparison.items():
        cost = float(result.get("estimated_cost", 0.0))
        time_min = float(result.get("execution_time_minutes", 0.0))
        cost_bps = float(result.get("cost_bps", 0.0))

        if max_execution_minutes is not None and time_min > max_execution_minutes:
            continue

        score = cost_weight * cost_bps + time_weight * (time_min / 60.0)
        candidates.append((score, algo, result, cost, cost_bps, time_min))

    if not candidates:
        return {
            "algorithm": "immediate",
            "reason": "所有算法均超时可接受执行时间，回退 immediate",
            "estimated_cost": 0.0,
            "cost_bps": 0.0,
            "execution_time_minutes": 0.0,
            "orders": comparison.get("immediate", {}).get("orders", []),
            "comparison": comparison,
        }

    candidates.sort(key=lambda x: x[0])
    best = candidates[0]
    return {
        "algorithm": best[1],
        "reason": f"综合成本+时间最优: cost_bps={best[4]:.2f}, time={best[5]:.1f}min",
        "estimated_cost": best[3],
        "cost_bps": best[4],
        "execution_time_minutes": best[5],
        "orders": best[2].get("orders", []),
        "comparison": comparison,
    }
