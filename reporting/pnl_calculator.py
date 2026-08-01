"""盈亏计算 + 风险指标 (B3.2 拆分自 PortfolioAnalyzer)

提供以下纯函数:
  - calculate_pnl: 计算持仓盈亏明细
  - get_position_status: 判断持仓状态
  - calculate_volatility: 计算波动率
  - calculate_max_drawdown: 计算组合层面真实最大回撤
  - count_stop_loss_status: 统计止损状态

注: assess_data_source_health 已迁至 reporting/price_fetcher.py
"""

import json
from pathlib import Path as _Path
from typing import Any, Dict, List


def calculate_pnl(positions_data: Dict[str, Any], market_prices: Dict[str, Dict]) -> Dict[str, Any]:
    """计算持仓盈亏明细

    Args:
        positions_data: positions.json 加载后的字典
        market_prices: fetch_market_prices 返回的价格字典

    Returns:
        含 details (List[Dict]) 与 summary (Dict) 的结果字典
    """
    positions = positions_data.get("positions", {})

    pnl_details = []
    total_cost = 0.0
    total_market_value = 0.0
    total_pnl = 0.0

    for _key, pos in positions.items():
        code = pos.get("code", "")

        # 优先使用快照实际成交数据
        # 成本价按"第一次交易开盘价格"计算 (est_price), 不用成交均价 (actual_avg_cost)
        # 持仓量用快照实际成交量 (actual_shares)
        if "actual_shares" in pos or "actual_avg_cost" in pos:
            shares = pos.get("actual_shares", 0)
            cost_price = pos.get("est_price", pos.get("actual_avg_cost", 0))
            calc_mode = "snapshot"
        else:
            # 回退: phase1_shares (计划) -> shares (实际持仓, 由 execute_instructions 同步)
            shares = pos.get("phase1_shares", 0) or pos.get("shares", 0)
            # 回退: est_price (第一次交易开盘价) -> avg_cost (加权平均成本)
            cost_price = pos.get("est_price", 0) or pos.get("avg_cost", 0)
            calc_mode = "plan"

        # 获取收盘价 (兼容带后缀和不带后缀的 code)
        price_data = market_prices.get(code, {})
        if not price_data:
            # 回退: 用不带后缀的 code 查找 (fallback_prices 的 key 格式)
            code_num = code.split(".")[0]
            price_data = market_prices.get(code_num, {})
        close_price = price_data.get("close", None)
        prev_close = price_data.get("prev_close", None)
        change_pct = price_data.get("change_pct", None)

        # 判断数据真实性 (source字段为'fallback'或'fallback_corrected'说明非实时数据)
        data_source_real = (
            close_price is not None
            and close_price > 0
            and price_data.get("source", "live") not in ("fallback", "fallback_corrected")
        )

        if data_source_real:
            # 真实数据：正常计算
            if change_pct is None and close_price and prev_close and prev_close > 0:
                change_pct = (close_price - prev_close) / prev_close * 100
        else:
            # 非实时行情：区分"有fallback价格"与"完全无数据"
            fallback_has_price = close_price is not None and close_price > 0
            change_pct = None  # fallback数据不提供日内涨跌
            if calc_mode == "plan" or not fallback_has_price:
                calc_mode = "FALLBACK_NO_DATA"

        # 计算市值和盈亏
        cost_amount = shares * cost_price
        if data_source_real:
            market_value = shares * close_price
            pnl = market_value - cost_amount
            pnl_pct = (close_price - cost_price) / cost_price if cost_price > 0 else 0
            daily_pnl = shares * (close_price - prev_close) if close_price and prev_close else 0
            daily_pnl_pct = change_pct if (shares > 0 and change_pct is not None) else 0
        elif fallback_has_price:
            # fallback价格可用：可计算持仓盈亏(基于成本)，但日内涨跌不可用
            market_value = shares * close_price
            pnl = market_value - cost_amount
            pnl_pct = (close_price - cost_price) / cost_price if cost_price > 0 else 0
            daily_pnl = None
            daily_pnl_pct = None
        else:
            # 完全无行情数据：不计算任何盈亏
            market_value = 0
            pnl = 0
            pnl_pct = 0
            daily_pnl = None
            daily_pnl_pct = None

        total_cost += cost_amount
        total_market_value += market_value
        total_pnl += pnl

        # stop_loss: 优先使用持仓显式设置的，否则用标准默认值(-0.15 = -15%)
        effective_stop_loss = pos.get("stop_loss", None)
        if effective_stop_loss is None or effective_stop_loss >= 0:
            effective_stop_loss = -0.15

        # data_integrity: REAL(真实行情) / FALLBACK_PRICE(有兜底价) / NO_MARKET_DATA(完全无数据)
        if data_source_real:
            di = "REAL"
        elif fallback_has_price:
            di = "FALLBACK_PRICE"
        else:
            di = "NO_MARKET_DATA"

        pnl_details.append(
            {
                "code": code,
                "name": pos.get("name", ""),
                "style": pos.get("style", ""),
                "risk": pos.get("risk", ""),
                "shares": shares,
                "cost_price": round(cost_price, 2),
                "close_price": round(close_price, 2) if close_price else 0,
                "prev_close": round(prev_close, 2) if prev_close else 0,
                "cost_amount": round(cost_amount, 2),
                "market_value": round(market_value, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct * 100, 2),
                "daily_pnl": round(daily_pnl, 2) if daily_pnl is not None else None,
                "daily_pnl_pct": round(daily_pnl_pct, 2) if daily_pnl_pct is not None else None,
                "stop_loss": effective_stop_loss,
                "calc_mode": calc_mode,
                "status": get_position_status(pnl_pct, effective_stop_loss),
                "data_integrity": di,
            }
        )

    # 按盈亏排序（无数据的排在最后）
    pnl_details.sort(key=lambda x: x["daily_pnl_pct"] if x["daily_pnl_pct"] is not None else -9999, reverse=True)

    return {
        "details": pnl_details,
        "summary": {
            "total_cost": round(total_cost, 2),
            "total_market_value": round(total_market_value, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round((total_pnl / total_cost) * 100 if total_cost > 0 else 0, 2),
            "position_count": len(pnl_details),
        },
    }


def get_position_status(pnl_pct: float, stop_loss: float) -> str:
    """判断持仓状态 — 止损线由调用方保证为负值（如 -0.15）"""
    # 防御：止损为正或零表示配置异常，回退到默认 -0.15
    if stop_loss >= 0:
        stop_loss = -0.15
    if pnl_pct <= stop_loss:
        return "STOP_LOSS_TRIGGERED"
    elif pnl_pct <= stop_loss * 0.7:
        return "WARNING"
    elif pnl_pct >= 0.05:
        return "PROFIT"
    else:
        return "NORMAL"


def calculate_volatility(returns: List[float]) -> float:
    """计算波动率"""
    if len(returns) < 2:
        return 0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return variance**0.5


def calculate_max_drawdown(details: List) -> float:
    """计算组合层面真实最大回撤（从历史每日PnL报告构建累计净值曲线）

    注意：这不是单标的跌幅，而是组合累计净值从峰值到谷底的最大跌幅。
    加载 v8.3_institutional/reports/ 和历史归档中的 daily_pnl_report_*.json，
    构建累计净值曲线后计算真实 peak-to-trough 最大回撤。
    如果历史数据不足（<5天），返回 None 并标注"N/A"。

    异常值处理：单日收益率超过 ±20% 视为仓位重建/大额调仓事件，
    从净值曲线中剔除，避免对最大回撤计算造成失真。

    Args:
        details: 当前 pnl_data['details'] (保留参数以维持原签名, 函数内部未使用)
    """
    pnl_history = []
    MAX_DAILY_RETURN = 0.20  # 单日收益率阈值，超过视为异常值

    def _extract_return(r: dict, f_name: str) -> float:
        """从报告中提取单日净收益率，优先使用报告已计算的 net_pnl_pct。"""
        net = r.get("net_performance", {})
        pct = net.get("net_pnl_pct", None)
        if pct is not None and isinstance(pct, (int, float)):
            return pct / 100.0
        pnl = net.get("net_pnl", None)
        cost = r.get("portfolio_pnl", {}).get("summary", {}).get("total_cost", 0)
        if pnl is not None and cost > 0:
            return pnl / cost
        return None

    # 1) 从 v8.3_institutional/reports/ 收集历史PnL
    for candidate_dir in ["v8.3_institutional", "v7.5_institutional"]:
        reports_dir = _Path(__file__).resolve().parent.parent / candidate_dir / "reports"
        if reports_dir.exists():
            for f in sorted(reports_dir.glob("daily_pnl_report_*.json")):
                try:
                    with open(f, "r", encoding="utf-8") as fp:
                        r = json.load(fp)
                    net_return = _extract_return(r, f.name)
                    if net_return is None:
                        continue
                    date_str = f.stem.replace("daily_pnl_report_", "")
                    if not any(h["date"] == date_str for h in pnl_history):
                        if abs(net_return) > MAX_DAILY_RETURN:
                            continue
                        pnl_history.append(
                            {
                                "date": date_str,
                                "net_return": net_return,
                            }
                        )
                except Exception as e:
                    continue

    # 2) 从每日报告归档/ 补充
    archive_dir = _Path(__file__).resolve().parent.parent / "每日报告归档"
    if archive_dir.exists():
        for date_dir in sorted(archive_dir.iterdir()):
            if not date_dir.is_dir():
                continue
            for f in sorted(date_dir.glob("daily_pnl_report_*.json")):
                date_str = f.stem.replace("daily_pnl_report_", "")
                if any(h["date"] == date_str for h in pnl_history):
                    continue
                try:
                    with open(f, "r", encoding="utf-8") as fp:
                        r = json.load(fp)
                    net_return = _extract_return(r, f.name)
                    if net_return is None:
                        continue
                    if abs(net_return) > MAX_DAILY_RETURN:
                        continue
                    pnl_history.append(
                        {
                            "date": date_str,
                            "net_return": net_return,
                        }
                    )
                except Exception as e:
                    continue

    # 3) 去重排序
    seen = set()
    unique = []
    for h in sorted(pnl_history, key=lambda x: x["date"]):
        if h["date"] not in seen:
            seen.add(h["date"])
            unique.append(h)

    if len(unique) < 5:
        return None

    # 4) 构建累计净值曲线: NAV[t] = NAV[t-1] * (1 + net_return[t])
    nav = 1.0
    peak = 1.0
    max_dd = 0.0
    for h in unique:
        nav *= 1.0 + h["net_return"]
        if nav > peak:
            peak = nav
        dd = (nav - peak) / peak
        if dd < max_dd:
            max_dd = dd

    return round(max_dd * 100, 2)  # 转为百分比


def count_stop_loss_status(details: List) -> Dict:
    """统计止损状态"""
    status_count = {}
    for d in details:
        status = d["status"]
        status_count[status] = status_count.get(status, 0) + 1
    return status_count
