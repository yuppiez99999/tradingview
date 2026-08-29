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
from typing import Any, Optional


def calculate_pnl(
    positions_data: dict[str, Any],
    market_prices: dict[str, dict],
    align_hfq: bool = False,
    hfq_date: Optional[str] = None,
) -> dict[str, Any]:
    """计算持仓盈亏明细

    Args:
        positions_data: positions.json 加载后的字典
        market_prices: fetch_market_prices 返回的价格字典
        align_hfq: U3 复权因子对齐开关, 默认 False (零行为变更).
            True 时对每个标的获取 hfq 因子, 除权日输出 aligned_* 字段,
            消除 prev_close 与 close 跳空偏差 (虚假回撤).
            依赖 utils.adjust_factor_provider, akshare 不可用时安全降级.
        hfq_date: 对齐参考日期 (YYYY-MM-DD), None=今天; 仅 align_hfq=True 时生效.

    Returns:
        含 details (List[Dict]) 与 summary (Dict) 的结果字典.
        align_hfq=True 时 detail 新增字段: hfq_factor / is_ex_dividend /
        aligned_prev_close / aligned_daily_pnl / aligned_daily_pnl_pct /
        aligned_cost_price / aligned_pnl_pct (除权日才有).
    """
    positions = positions_data.get("positions", {})

    # U3: 延迟初始化复权因子提供器 (仅 align_hfq=True 时加载)
    _hfq_provider = None
    if align_hfq:
        try:
            from utils.adjust_factor_provider import get_adjust_factor_provider

            _hfq_provider = get_adjust_factor_provider()
        except (ImportError, RuntimeError) as e:
            # 降级: 关闭对齐, 主流程继续
            import logging

            logging.getLogger("pnl_calculator").warning(
                "U3 复权因子提供器不可用, 关闭对齐: %s", e
            )
            _hfq_provider = None
            align_hfq = False

    pnl_details = []
    total_cost = 0.0
    total_market_value = 0.0
    total_pnl = 0.0
    # U3: 对齐后日内盈亏总计 (仅 align_hfq=True 时累积)
    total_aligned_daily_pnl = 0.0
    aligned_position_count = 0

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
            and price_data.get("source", "live")
            not in ("fallback", "fallback_corrected")
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
            daily_pnl = (
                shares * (close_price - prev_close) if close_price and prev_close else 0
            )
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

        detail = {
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
            "daily_pnl_pct": (
                round(daily_pnl_pct, 2) if daily_pnl_pct is not None else None
            ),
            "stop_loss": effective_stop_loss,
            "calc_mode": calc_mode,
            "status": get_position_status(pnl_pct, effective_stop_loss),
            "data_integrity": di,
        }

        # U3: 复权因子对齐 (align_hfq=True 时启用, 零行为变更)
        # - 非除权日: 仅注入 hfq_factor 字段
        # - 除权日: 注入 aligned_prev_close / aligned_daily_pnl / aligned_daily_pnl_pct
        #   消除 prev_close vs close 跳空偏差 (虚假回撤)
        # - 异常时安全降级 (不注入任何 hfq 字段, 不阻断主流程)
        if align_hfq and _hfq_provider is not None:
            try:
                hfq_factor = _hfq_provider.get_hfq_factor(code, date=hfq_date)
                is_ex_div = _hfq_provider.is_ex_dividend_date(code, date=hfq_date)
                detail["hfq_factor"] = round(hfq_factor, 6)
                detail["is_ex_dividend"] = bool(is_ex_div)

                # 除权日: 计算 aligned_prev_close / aligned_daily_pnl / aligned_daily_pnl_pct
                if (
                    is_ex_div
                    and prev_close
                    and prev_close > 0
                    and close_price
                    and close_price > 0
                ):
                    aligned_prev = _hfq_provider.get_aligned_prev_close(
                        code, prev_close, date=hfq_date
                    )
                    detail["aligned_prev_close"] = round(aligned_prev, 2)
                    aligned_daily = shares * (close_price - aligned_prev)
                    detail["aligned_daily_pnl"] = round(aligned_daily, 2)
                    detail["aligned_daily_pnl_pct"] = (
                        round((close_price - aligned_prev) / aligned_prev * 100, 2)
                        if aligned_prev > 0
                        else None
                    )

                    # 累积对齐后日内盈亏总计 (供 summary 使用)
                    total_aligned_daily_pnl += aligned_daily
                    aligned_position_count += 1

                # 除权日 + 有建仓日期: 计算 aligned_cost_price / aligned_pnl_pct
                # cost_price 是建仓当日未复权成本, 跨除权日需要按因子比调整
                buy_date = pos.get("buy_date") or pos.get("建仓日期")
                if is_ex_div and buy_date and cost_price and cost_price > 0:
                    try:
                        buy_factor = _hfq_provider.get_hfq_factor(code, date=buy_date)
                        today_factor = _hfq_provider.get_hfq_factor(code, date=hfq_date)
                        if buy_factor > 0 and today_factor > 0:
                            # 把 cost_price 从建仓当日口径调整到今日口径
                            aligned_cost = cost_price * (buy_factor / today_factor)
                            detail["aligned_cost_price"] = round(aligned_cost, 2)
                            detail["aligned_pnl_pct"] = (
                                round(
                                    (close_price - aligned_cost) / aligned_cost * 100, 2
                                )
                                if aligned_cost > 0
                                else None
                            )
                    except (ValueError, TypeError, KeyError):
                        # 建仓因子查询失败: 跳过 cost 对齐, 不阻断
                        pass
            except (ImportError, RuntimeError, ValueError, TypeError, OSError) as e:
                # U3 降级: 不注入 hfq 字段, 主流程继续
                import logging

                logging.getLogger("pnl_calculator").debug(
                    "U3 对齐失败 (%s): %s", code, e
                )

        pnl_details.append(detail)

    # 按盈亏排序（无数据的排在最后）
    pnl_details.sort(
        key=lambda x: x["daily_pnl_pct"] if x["daily_pnl_pct"] is not None else -9999,
        reverse=True,
    )

    result = {
        "details": pnl_details,
        "summary": {
            "total_cost": round(total_cost, 2),
            "total_market_value": round(total_market_value, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(
                (total_pnl / total_cost) * 100 if total_cost > 0 else 0, 2
            ),
            "position_count": len(pnl_details),
        },
    }

    # U3: 对齐后日内盈亏总计 (仅 align_hfq=True 且有除权日标的时输出)
    if align_hfq and aligned_position_count > 0:
        result["summary"]["total_aligned_daily_pnl"] = round(total_aligned_daily_pnl, 2)
        result["summary"]["aligned_position_count"] = aligned_position_count

    return result


def get_position_status(pnl_pct: float, stop_loss: float) -> str:
    """判断持仓状态 — 止损线由调用方保证为负值（如 -0.15）"""
    # 防御：止损为正或零表示配置异常，回退到默认 -0.15
    if stop_loss >= 0:
        stop_loss = -0.15
    if pnl_pct <= stop_loss:
        return "STOP_LOSS_TRIGGERED"
    if pnl_pct <= stop_loss * 0.7:
        return "WARNING"
    if pnl_pct >= 0.05:
        return "PROFIT"
    return "NORMAL"


def calculate_volatility(returns: list[float]) -> float:
    """计算波动率"""
    if len(returns) < 2:
        return 0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return variance**0.5


def _extract_return_from_report(r: dict, f_name: str) -> Optional[float]:
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


def _collect_pnl_from_reports_dir(
    reports_dir: _Path, pnl_history: list, max_daily_return: float
) -> None:
    """从指定目录收集 daily_pnl_report_*.json 中的净收益率"""
    if not reports_dir.exists():
        return
    for f in sorted(reports_dir.glob("daily_pnl_report_*.json")):
        try:
            with open(f, encoding="utf-8") as fp:
                r = json.load(fp)
            net_return = _extract_return_from_report(r, f.name)
            if net_return is None:
                continue
            date_str = f.stem.replace("daily_pnl_report_", "")
            if not any(h["date"] == date_str for h in pnl_history):
                if abs(net_return) > max_daily_return:
                    continue
                pnl_history.append(
                    {
                        "date": date_str,
                        "net_return": net_return,
                    }
                )
        except Exception:
            continue


def _collect_pnl_from_archive(
    archive_dir: _Path, pnl_history: list, max_daily_return: float
) -> None:
    """从每日报告归档目录补充 PnL 历史"""
    if not archive_dir.exists():
        return
    for date_dir in sorted(archive_dir.iterdir()):
        if not date_dir.is_dir():
            continue
        for f in sorted(date_dir.glob("daily_pnl_report_*.json")):
            date_str = f.stem.replace("daily_pnl_report_", "")
            if any(h["date"] == date_str for h in pnl_history):
                continue
            try:
                with open(f, encoding="utf-8") as fp:
                    r = json.load(fp)
                net_return = _extract_return_from_report(r, f.name)
                if net_return is None:
                    continue
                if abs(net_return) > max_daily_return:
                    continue
                pnl_history.append(
                    {
                        "date": date_str,
                        "net_return": net_return,
                    }
                )
            except Exception:
                continue


def _deduplicate_pnl_history(pnl_history: list) -> list:
    """去重并按日期排序"""
    seen = set()
    unique = []
    for h in sorted(pnl_history, key=lambda x: x["date"]):
        if h["date"] not in seen:
            seen.add(h["date"])
            unique.append(h)
    return unique


def _compute_max_dd_from_nav(unique: list) -> float:
    """从历史净收益率构建累计净值曲线并计算最大回撤"""
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


def calculate_max_drawdown(details: list) -> float:
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
    MAX_DAILY_RETURN = 0.20  # 单日收益率阈值，超过视为异常值  # noqa: N806

    # 1) 从 v8.3_institutional/reports/ 收集历史PnL
    for candidate_dir in ["v8.3_institutional", "v7.5_institutional"]:
        reports_dir = (
            _Path(__file__).resolve().parent.parent / candidate_dir / "reports"
        )
        _collect_pnl_from_reports_dir(reports_dir, pnl_history, MAX_DAILY_RETURN)

    # 2) 从每日报告归档/ 补充
    archive_dir = _Path(__file__).resolve().parent.parent / "每日报告归档"
    _collect_pnl_from_archive(archive_dir, pnl_history, MAX_DAILY_RETURN)

    # 3) 去重排序
    unique = _deduplicate_pnl_history(pnl_history)

    if len(unique) < 5:
        return None

    # 4) 构建累计净值曲线: NAV[t] = NAV[t-1] * (1 + net_return[t])
    return _compute_max_dd_from_nav(unique)


def count_stop_loss_status(details: list) -> dict:
    """统计止损状态"""
    status_count = {}
    for d in details:
        status = d["status"]
        status_count[status] = status_count.get(status, 0) + 1
    return status_count
