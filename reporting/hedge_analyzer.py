"""对冲分析 (B3.2 拆分自 PortfolioAnalyzer)

提供以下纯函数:
  - analyze_hedge_position: 分析对冲头寸
  - calculate_hedge_effectiveness: 计算对冲有效性
  - analyze_hedge_positions_plan: 分析期货期权计划头寸
"""

from datetime import datetime as _dt
from typing import Any, Dict, List

from reporting.price_fetcher import fetch_sina_realtime


def analyze_hedge_position(
    hedge_data: Dict[str, Any], data_provider=None
) -> Dict[str, Any]:
    """分析对冲头寸

    Args:
        hedge_data: hedge_execution_fill_*.json 加载后的字典
        data_provider: 可选的 MarketDataProvider 实例

    Returns:
        含 details (List[Dict]) 与 summary (Dict) 的结果字典
    """
    hedge_orders = hedge_data.get("orders", [])

    hedge_details = []
    total_hedge_notional = 0.0

    # 兼容 hedge_execution_fill 格式 (side=SELL_SHORT) 与 hedge_decision 格式 (direction=SELL)
    for order in hedge_orders:
        instrument = order.get("instrument", "")
        contracts = order.get("contracts", 0)
        # direction 兼容: direction / side (SELL_SHORT → SELL)
        direction = order.get("direction", "")
        if not direction:
            side = order.get("side", "")
            direction = "SELL" if side in ("SELL_SHORT", "SELL", "SHORT") else side

        # futures_price 兼容: futures_price / price
        futures_price = order.get("futures_price", 0)
        if not futures_price:
            futures_price = order.get("price", 0)

        multiplier = order.get("multiplier", 300)  # IF 合约乘数 300
        notional = order.get("notional", 0)

        # 获取IF期货真实收盘价
        # 数据源优先级: 新浪财经 > data_provider(close/last/price) > 开仓价兜底
        # 不用 index_price (它是沪深300指数点位, 不是期货合约价)
        if_close = futures_price  # 默认使用开仓价 (数据源不可用时兜底)
        if instrument == "IF":
            # 1. 尝试新浪财经获取 IF 期货实时价格
            try:
                # RC2 修复: 动态生成当月/下月/季月主力合约代码
                # 原硬编码 IF2407 是 2024年7月合约, 已过期两年, 实时价格永远拿不到
                _now = _dt.now()
                _yy = _now.year % 100
                _mm = _now.month
                _cur = f"IF{_yy:02d}{_mm:02d}"
                _next_mm = _mm + 1 if _mm < 12 else 1
                _next_yy = _yy if _mm < 12 else _yy + 1
                _next = f"IF{_next_yy:02d}{_next_mm:02d}"
                _quarter_months = [3, 6, 9, 12]
                _next_q = next((m for m in _quarter_months if m > _mm), 3)
                _next_q_yy = _yy if _next_q > _mm else _yy + 1
                _quarter = f"IF{_next_q_yy:02d}{_next_q:02d}"
                # 新浪期货代码: hf_ 前缀 (小写) + 合约代码 (大写)
                if_codes_sina = [f"hf_{_cur}", f"hf_{_next}", f"hf_{_quarter}"]
                sina_res = fetch_sina_realtime(if_codes_sina)
                if sina_res:
                    for _, sp in sina_res.items():
                        fc_price = sp.get("close", 0)
                        if fc_price > 0 and futures_price > 0:
                            ratio = fc_price / futures_price
                            if 0.8 <= ratio <= 1.2:
                                if_close = fc_price
                                break
            except Exception as e:
                raise  # Re-raise unknown exception
            # 2. 尝试 data_provider (close/last/price)
            if if_close == futures_price and data_provider:
                try:
                    # RC2 修复: 同步使用动态合约代码
                    futures_codes = [_cur, _next, _quarter, "IF"]
                    for fc in futures_codes:
                        futures_data = data_provider.get_market_data(fc)
                        if not futures_data:
                            continue
                        fc_price = (
                            futures_data.get("close") or futures_data.get("last") or futures_data.get("price")
                        )
                        if not fc_price or fc_price <= 0:
                            continue
                        if futures_price > 0:
                            ratio = fc_price / futures_price
                            if ratio > 1.2 or ratio < 0.8:
                                continue
                        if_close = fc_price
                        break
                except Exception as e:
                    print(f"获取IF期货价格失败: {e}")

        if_change_pct = (if_close - futures_price) / futures_price if futures_price > 0 else 0

        # 对冲头寸盈亏（做空方向）
        hedge_pnl = 0.0
        if direction == "SELL" and futures_price > 0:
            # 做空: 开仓价 - 收盘价 (价格下跌盈利)
            hedge_pnl = contracts * multiplier * (futures_price - if_close)

        total_hedge_notional += notional

        hedge_details.append(
            {
                "instrument": instrument,
                "contracts": contracts,
                "direction": direction,
                "entry_price": round(futures_price, 2),
                "close_price": round(if_close, 2),
                "multiplier": multiplier,
                "notional": round(notional, 2),
                "cost": order.get("cost", 0),
                "hedge_pnl": round(hedge_pnl, 2),
                "hedge_pnl_pct": round(if_change_pct * 100 * (-1 if direction == "SELL" else 1), 2),
                "hedge_type": order.get("hedge_type", order.get("type", "")),
                "beta_reduced": round(order.get("beta_reduced", 0), 3),
                "cost_breakdown": order.get("cost_breakdown"),
            }
        )

    # 目标 beta: 从 hedge_execution_fill.orders[0].target_beta 取,
    # 若不存在则用 hedge_config.layers.layer1_futures.target_beta 或默认 0.3
    target_beta = 0.3
    if hedge_orders:
        target_beta = float(hedge_orders[0].get("target_beta", target_beta))

    return {
        "details": hedge_details,
        "summary": {
            "total_hedge_notional": round(total_hedge_notional, 2),
            "current_portfolio_beta": round(hedge_data.get("portfolio_beta", 1.0), 3),
            "target_beta": round(target_beta, 3),
            "hedge_effectiveness": calculate_hedge_effectiveness(hedge_details, hedge_data),
        },
    }


def calculate_hedge_effectiveness(hedge_details: List, hedge_data: Dict) -> float:
    """计算对冲有效性"""
    # 简化模型：Beta降低比例作为有效性指标
    beta_reduced = sum(h.get("beta_reduced", 0) for h in hedge_details)
    original_beta = hedge_data.get("portfolio_beta", 1.3)
    effectiveness = beta_reduced / original_beta if original_beta > 0 else 0
    return round(effectiveness * 100, 2)


def analyze_hedge_positions_plan(positions_data: Dict[str, Any]) -> Dict[str, Any]:
    """分析期货期权计划头寸 (来自 positions.json 的 hedge_positions)

    覆盖三类对冲工具:
      - IF 期货  (CFFEX, 沪深300股指期货, Beta加权)
      - IM 期货  (CFFEX, 中证1000股指期货, 中小盘对冲)
      - 510050 Put 期权 (SSE, 上证50ETF认沽, 尾部风险保护)

    Returns:
        {
            'details': List[Dict],   # 每个对冲工具的明细
            'summary': Dict,          # 汇总: 总名义价值, 总权利金预算, 总Beta降低
        }
    """
    hedge_positions = positions_data.get("hedge_positions", {})

    details = []
    total_notional = 0.0
    total_premium = 0.0
    total_beta_reduction = 0.0

    for key, pos in hedge_positions.items():
        if not isinstance(pos, dict):
            continue
        instrument = pos.get("instrument", key)
        exchange = pos.get("exchange", "")
        direction = pos.get("direction", "")
        target_contracts = pos.get("target_contracts", 0)
        multiplier = pos.get("multiplier", 0)
        margin_rate = pos.get("margin_rate", 0.0)
        beta_reduction = pos.get("target_beta_reduction", 0.0)
        strike = pos.get("strike", "")
        premium_budget = pos.get("premium_budget", 0)
        reason = pos.get("reason", "")

        # 估算名义价值
        # 期货: 名义 = 手数 × 乘数 × 标的指数点位 (用 IF 4726 / IM 7500 估算)
        # 期权: 名义 = 权利金预算 (实际是成本, 不是名义价值)
        is_option = instrument.lower().endswith("put") or "put" in key.lower()
        if is_option:
            # 期权: 名义价值不适用, 用权利金预算作为成本
            notional = 0
            cost = premium_budget
            total_premium += premium_budget
        else:
            # 期货: 用 IF=4726 / IM=7500 估算名义
            index_point = 4726.0 if instrument.upper().startswith("IF") else 7500.0
            notional = target_contracts * multiplier * index_point
            cost = notional * margin_rate  # 保证金 = 名义 × 保证金率
            total_notional += notional

        total_beta_reduction += beta_reduction

        details.append(
            {
                "key": key,
                "instrument": instrument,
                "exchange": exchange,
                "direction": direction,
                "target_contracts": target_contracts,
                "multiplier": multiplier,
                "margin_rate": margin_rate,
                "target_beta_reduction": beta_reduction,
                "strike": strike,
                "premium_budget": premium_budget,
                "estimated_notional": round(notional, 2),
                "estimated_cost": round(cost, 2),
                "is_option": is_option,
                "reason": reason,
            }
        )

    return {
        "details": details,
        "summary": {
            "total_estimated_notional": round(total_notional, 2),
            "total_premium_budget": round(total_premium, 2),
            "total_beta_reduction": round(total_beta_reduction, 3),
            "tool_count": len(details),
        },
    }
