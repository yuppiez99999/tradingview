"""报告生成 (B3.2 拆分自 PortfolioAnalyzer)

提供以下纯函数:
  - generate_report: 生成完整收盘报告 (组装所有数据为最终 dict)

注: 此函数为协调器, 接收所有已计算的子结果 (pnl_data / hedge_data / hedge_plan / 等)
    并组装为最终报告字典。各子结果由 PortfolioAnalyzer 调用其他子模块计算后传入。
"""

from datetime import datetime
from typing import Any, Dict, List

from reporting.pnl_calculator import (
    calculate_volatility,
    calculate_max_drawdown,
    count_stop_loss_status,
)
from reporting.price_fetcher import assess_data_source_health


def generate_report(
    report_date: str,
    positions_data: Dict[str, Any],
    hedge_data: Dict[str, Any],
    market_prices: Dict[str, Dict],
    pnl_data: Dict[str, Any],
    hedge_position_data: Dict[str, Any],
    hedge_plan: Dict[str, Any],
    return_projection: Dict[str, Any],
    ai_recommendations: List[str],
    next_day_plan: Dict[str, Any],
) -> Dict[str, Any]:
    """生成完整收盘报告

    Args:
        report_date: 报告日期 (YYYY-MM-DD)
        positions_data: positions.json 加载后的字典 (用于 meta.phase)
        hedge_data: hedge_execution_fill_*.json 加载后的字典
        market_prices: fetch_market_prices 返回的价格字典 (保留参数, 当前实现未直接使用)
        pnl_data: calculate_pnl 返回的盈亏明细
        hedge_position_data: analyze_hedge_position 返回的对冲头寸分析
        hedge_plan: analyze_hedge_positions_plan 返回的对冲计划
        return_projection: _load_return_projection 返回的收益预测
        ai_recommendations: AI 决策建议列表
        next_day_plan: generate_next_day_plan 返回的次日计划

    Returns:
        完整收盘报告字典
    """
    # 组合整体表现
    portfolio_pnl = pnl_data["summary"]["total_pnl"]
    hedge_pnl = sum(h["hedge_pnl"] for h in hedge_position_data["details"])
    hedge_cost = hedge_data.get("total_cost", 0)
    net_pnl = portfolio_pnl + hedge_pnl - hedge_cost

    # 风险指标计算（过滤掉无数据的标的）
    daily_returns = [d["daily_pnl_pct"] for d in pnl_data["details"] if d["daily_pnl_pct"] is not None]
    avg_return = sum(daily_returns) / len(daily_returns) if daily_returns else 0
    volatility = calculate_volatility(daily_returns)

    # 数据源健康状态
    data_source_health = assess_data_source_health(pnl_data)

    report = {
        "meta": {
            "report_date": report_date,
            "report_type": "收盘盈亏明细",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "phase": positions_data.get("meta", {}).get("phase", "第一阶段"),
            "fund_style": "Bridgewater/Renaissance 标准对冲基金视角",
            "data_source_health": data_source_health,
        },
        "market_overview": {
            "market_regime": hedge_data.get("regime", "normal"),
            "vix_estimate": 18.5,
            "market_trend": "震荡上行",
            "key_events": [],
            "data_source_status": data_source_health.get("status", "UNKNOWN"),
        },
        "portfolio_pnl": pnl_data,
        "hedge_position": hedge_position_data,
        "hedge_position_plan": hedge_plan,
        "net_performance": {
            "portfolio_pnl": round(portfolio_pnl, 2),
            "hedge_pnl": round(hedge_pnl, 2),
            "hedge_cost": round(hedge_cost, 2),
            "net_pnl": round(net_pnl, 2),
            "net_pnl_pct": round(
                (net_pnl / pnl_data["summary"]["total_cost"]) * 100 if pnl_data["summary"]["total_cost"] > 0 else 0,
                2,
            ),
        },
        "risk_metrics": {
            "avg_daily_return_pct": round(avg_return, 2),
            "portfolio_volatility_pct": round(volatility, 2),
            "max_drawdown_pct": calculate_max_drawdown(pnl_data["details"]),
            "stop_loss_status": count_stop_loss_status(pnl_data["details"]),
            "beta_exposure": round(
                hedge_data.get("portfolio_beta", 1.3)
                - sum(h.get("beta_reduced", 0) for h in hedge_position_data["details"]),
                3,
            ),
        },
        "ai_recommendations": ai_recommendations,
        "next_day_plan": next_day_plan,
        "return_projection": return_projection,
    }

    return report
