"""
智能执行算法选择器

基于交易成本模型自动选择最优执行算法：
- 对每笔订单评估 min_impact / twap / vwap / immediate
- 优先选择综合成本最低、执行时间可接受的方案
- 供 daily_trade_executor 和 hedge_execution_orders 使用
"""

from __future__ import annotations


def _estimate_depth_ratio(target_amount: float, ref_price: float, avg_daily_volume: float) -> float:
    """估算订单占日均成交额比例"""
    if avg_daily_volume <= 0 or ref_price <= 0:
        return 0.0
    daily_turnover = avg_daily_volume * ref_price
    if daily_turnover <= 0:
        return 0.0
    return target_amount / daily_turnover


def _compute_adaptive_weights(
    depth_ratio: float,
    volatility: float,
    avg_daily_volume: float,
    cost_weight: float,
    time_weight: float,
) -> tuple[float, float, str, bool, bool]:
    """根据市场深度和波动率自适应调整成本/时间权重

    Returns:
        (adaptive_cost_weight, adaptive_time_weight, reason, shallow_market, high_volatility)
    """
    high_volatility = volatility > 0.05
    shallow_market = depth_ratio > 0.05 or avg_daily_volume <= 0

    if shallow_market and high_volatility:
        ac, at = 0.4, 0.6
    elif shallow_market:
        ac, at = 0.5, 0.5
    elif high_volatility:
        ac, at = 0.6, 0.4
    elif depth_ratio < 0.01:
        ac, at = 0.8, 0.2
    else:
        ac, at = cost_weight, time_weight

    reason = (
        f"depth_ratio={depth_ratio:.2%}, vol={volatility:.2%}, "
        f"cost_w={ac:.1f}, time_w={at:.1f}"
    )
    return ac, at, reason, shallow_market, high_volatility


def _make_result(
    algorithm: str,
    reason: str,
    comparison: dict | None = None,
    orders: list | None = None,
    estimated_cost: float = 0.0,
    cost_bps: float = 0.0,
    execution_time_minutes: float = 0.0,
    adaptive_params: dict | None = None,
) -> dict:
    """统一构造返回结果字典, 消除重复模板"""
    result = {
        "algorithm": algorithm,
        "reason": reason,
        "estimated_cost": estimated_cost,
        "cost_bps": cost_bps,
        "execution_time_minutes": execution_time_minutes,
        "orders": orders or [],
        "comparison": comparison or {},
    }
    if adaptive_params is not None:
        result["adaptive_params"] = adaptive_params
    return result


def choose_execution_algorithm(
    target_amount: float,
    ref_price: float,
    avg_daily_volume: float = 0,
    max_execution_minutes: float | None = None,
    cost_weight: float = 0.7,
    time_weight: float = 0.3,
    volatility: float = 0.02,
) -> dict:
    """选择最优执行算法

    Args:
        target_amount: 目标金额
        ref_price: 参考价格
        avg_daily_volume: 日均成交量
        max_execution_minutes: 最大可接受执行时间（分钟）
        cost_weight: 成本权重
        time_weight: 时间权重
        volatility: 波动率（用于自适应调整）

    Returns:
        {
            algorithm,
            reason,
            estimated_cost,
            cost_bps,
            execution_time_minutes,
            orders,
            comparison,
            adaptive_params,
        }
    """
    depth_ratio = _estimate_depth_ratio(target_amount, ref_price, avg_daily_volume)
    ac, at, adaptive_reason, shallow, high_vol = _compute_adaptive_weights(
        depth_ratio, volatility, avg_daily_volume, cost_weight, time_weight
    )

    try:
        from utils.wt_execution_algo import compare_execution

        comparison = compare_execution(target_amount, ref_price, avg_daily_volume)
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
        return _make_result("immediate", f"执行算法比较失败，回退 immediate: {e}")

    if not comparison:
        return _make_result("immediate", "无可比较的执行算法结果")

    candidates = []
    for algo, result in comparison.items():
        cost = float(result.get("estimated_cost", 0.0))
        time_min = float(result.get("execution_time_minutes", 0.0))
        cost_bps = float(result.get("cost_bps", 0.0))

        if max_execution_minutes is not None and time_min > max_execution_minutes:
            continue

        score = ac * cost_bps + at * (time_min / 60.0)
        candidates.append((score, algo, result, cost, cost_bps, time_min))

    if not candidates:
        return _make_result(
            "immediate",
            "所有算法均超时可接受执行时间，回退 immediate",
            comparison=comparison,
            orders=comparison.get("immediate", {}).get("orders", []),
        )

    candidates.sort(key=lambda x: x[0])
    best = candidates[0]
    return _make_result(
        algorithm=best[1],
        reason=(f"[自适应] {adaptive_reason} | 综合成本+时间最优: cost_bps={best[4]:.2f}, time={best[5]:.1f}min"),
        comparison=comparison,
        orders=best[2].get("orders", []),
        estimated_cost=best[3],
        cost_bps=best[4],
        execution_time_minutes=best[5],
        adaptive_params={
            "depth_ratio": round(depth_ratio, 4),
            "volatility": volatility,
            "shallow_market": shallow,
            "high_volatility": high_vol,
            "cost_weight": ac,
            "time_weight": at,
        },
    )
