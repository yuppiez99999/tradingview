"""对冲分析 (B3.2 拆分自 PortfolioAnalyzer)

提供以下纯函数:
  - analyze_hedge_position: 分析对冲头寸
  - calculate_hedge_effectiveness: 计算对冲有效性
  - analyze_hedge_positions_plan: 分析期货期权计划头寸
"""
from __future__ import annotations

import math
from typing import Any

from reporting.price_fetcher import fetch_sina_realtime
from utils.datetime_utils import now_bj


def _norm_cdf(x: float) -> float:
    """标准正态分布累积分布函数 (math.erf 实现, 无第三方依赖)"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_option_price(
    S: float, K: float, T: float, r: float, sigma: float, option_type: str
) -> float:
    """Black-Scholes 欧式期权定价 (T 为年化到期时间, option_type='put'/'call')

    用于估算期权对冲头寸的当前市值。当 T<=0 或 sigma<=0 时退化为内在价值。
    """
    if S <= 0 or K <= 0:
        return 0.0
    if T <= 0 or sigma <= 0:
        if option_type == "put":
            return max(K - S, 0.0)
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if option_type == "put":
        return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def _estimate_option_expiry() -> str:
    """估算期权次月主力到期日 (格式 YYYY-MM-DD)。

    A股ETF期权为月度合约, 取下月第四个周三。简化: 取下月15日附近。
    返回 ISO 日期字符串。
    """
    now = now_bj()
    year, month = now.year, now.month
    # 下月
    if month == 12:
        year, month = year + 1, 1
    else:
        month += 1
    # 取该月第 4 个周三 (A股ETF期权到期规则)
    import calendar

    c = calendar.monthcalendar(year, month)
    wednesdays = [wk[calendar.WEDNESDAY] for wk in c if wk[calendar.WEDNESDAY] != 0]
    day = wednesdays[3] if len(wednesdays) > 3 else wednesdays[-1]
    return f"{year:04d}-{month:02d}-{day:02d}"


def _resolve_option_underlying_price(
    underlying: str, market_prices: dict | None
) -> float | None:
    """从 market_prices 解析期权标的今日收盘价。

    fill 文件中 underlying 可能为 '510300' / '510300.SH' / '510050'，
    market_prices 的 key 可能为 '510300.SH' 或 '510300'，兼容两类。
    """
    if not market_prices or not underlying:
        return None
    candidates = [underlying, underlying + ".SH", underlying + ".SZ"]
    # 去掉后缀再试
    base = underlying.split(".")[0]
    candidates += [base, base + ".SH", base + ".SZ"]
    for key in candidates:
        pd = market_prices.get(key)
        if pd:
            close = pd.get("close")
            if close and close > 0:
                return float(close)
    return None


def _parse_option_strike(strike_raw, spot: float, is_put: bool) -> float:
    """解析期权行权价。

    fill 文件中 strike 常为规则字符串 (如 'OTM_5pct_to_8pct'),
    无真实数字行权价时按 OTM 规则从当前标的价推算:
      - 认沽(Put):  行权价 = spot * (1 - pct)
      - 认购(Call):  行权价 = spot * (1 + pct)
    pct 取区间中值 (5%~8% -> 6.5%)。若已有真实数字行权价直接返回。
    """
    if strike_raw is None:
        return 0.0
    if isinstance(strike_raw, (int, float)):
        return float(strike_raw)
    s = str(strike_raw)
    if s.replace(".", "", 1).isdigit():
        return float(s)
    # 解析 OTM_xpct_to_ypct
    import re

    m = re.search(r"(\d+(?:\.\d+)?)\s*pct\s*to\s*(\d+(?:\.\d+)?)\s*pct", s)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        pct = (lo + hi) / 200.0  # /100 转小数, /2 取中值
    elif "otm" in s.lower():
        pct = 0.065  # 默认 OTM 6.5%
    else:
        pct = 0.05
    if is_put:
        return spot * (1.0 - pct)
    return spot * (1.0 + pct)


def _resolve_order_direction(order: dict[str, Any]) -> str:
    """兼容 direction / side 字段，统一返回方向 (SELL_SHORT → SELL)"""
    direction = order.get("direction", "")
    if not direction:
        side = order.get("side", "")
        direction = "SELL" if side in ("SELL_SHORT", "SELL", "SHORT") else side
    return direction


def _resolve_order_futures_price(order: dict[str, Any]) -> float:
    """兼容 futures_price / price 字段"""
    futures_price = order.get("futures_price", 0)
    if not futures_price:
        futures_price = order.get("price", 0)
    return futures_price


def _generate_if_contract_codes() -> list[str]:
    """动态生成当月/下月/季月主力合约代码 (RC2 修复: 替代过期硬编码 IF2407)"""
    _now = now_bj()
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
    return [_cur, _next, _quarter]


def _fetch_if_close_from_sina(futures_price: float, if_codes: list[str]) -> float:
    """从新浪财经获取 IF 期货实时价格，未找到有效价格时返回原 futures_price"""
    # 新浪期货代码: hf_ 前缀 (小写) + 合约代码 (大写)
    if_codes_sina = [f"hf_{c}" for c in if_codes]
    sina_res = fetch_sina_realtime(if_codes_sina)
    if sina_res:
        for _, sp in sina_res.items():
            fc_price = sp.get("close", 0)
            if fc_price > 0 and futures_price > 0:
                ratio = fc_price / futures_price
                if 0.8 <= ratio <= 1.2:
                    return fc_price
    return futures_price


def _fetch_if_close_from_provider(
    futures_price: float, if_codes: list[str], data_provider
) -> float:
    """从 data_provider 获取 IF 期货收盘价，未找到有效价格时返回原 futures_price"""
    if not data_provider:
        return futures_price
    try:
        # RC2 修复: 同步使用动态合约代码
        futures_codes = if_codes + ["IF"]
        for fc in futures_codes:
            futures_data = data_provider.get_market_data(fc)
            if not futures_data:
                continue
            fc_price = (
                futures_data.get("close")
                or futures_data.get("last")
                or futures_data.get("price")
            )
            if not fc_price or fc_price <= 0:
                continue
            if futures_price > 0:
                ratio = fc_price / futures_price
                if ratio > 1.2 or ratio < 0.8:
                    continue
            return fc_price
    except Exception:
        pass
    return futures_price


def analyze_hedge_position(
    hedge_data: dict[str, Any], data_provider=None, market_prices: dict | None = None
) -> dict[str, Any]:
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
    # 同时支持 OPTIONS 期权类型 (BUY_PUT / SELL_COVERED_CALL), 读取真实成交字段并估算当前市值
    for order in hedge_orders:
        instrument = order.get("instrument", "")
        contracts = order.get("contracts", 0)
        direction = _resolve_order_direction(order)
        order_type = order.get(
            "type", order.get("order_type", "FUTURES")
        )  # FUTURES / OPTIONS / FUTURES_OPTIONS

        # ---- 期权分支: 正确计算期权对冲盈亏 (修复原 bug: 期权 hedge_pnl 恒为 0) ----
        if order_type == "OPTIONS":
            underlying = order.get("underlying", "")
            # 当前标的价格: 优先 market_prices, 其次开仓时标的参考价
            spot = _resolve_option_underlying_price(underlying, market_prices)
            if spot is None:
                spot = float(
                    order.get("index_price", order.get("underlying_price", 0)) or 0
                )

            # 期权类型: BUY_PUT -> 买入认沽; SELL_COVERED_CALL -> 备兑卖出认购
            is_put = direction == "BUY_PUT"
            opt_kind = "put" if is_put else "call"

            # 行权价: fill 文件为 OTM 规则字符串, 无真实数字时按 OTM 规则从当前标的价推算
            strike = _parse_option_strike(order.get("strike"), spot, is_put)

            premium_total = float(order.get("premium_total", 0) or 0)
            delta = order.get("delta", 0)
            beta_reduced_raw = order.get("beta_reduction", order.get("beta_reduced", 0))

            # 估算当前期权市值 (BS 模型, 剩余到期约 44 天, IV 15%, 无风险利率 2%)
            now_price = 0.0
            if spot > 0 and strike > 0:
                expiry = _estimate_option_expiry()
                from datetime import datetime

                T = max(
                    (datetime.strptime(expiry, "%Y-%m-%d") - now_bj()).days
                    / 365.0,
                    0.0,
                )
                now_price = _bs_option_price(spot, strike, T, 0.02, 0.15, opt_kind)

            multiplier = order.get("multiplier", 10000)
            # 当前市值: 多头为正(持有的权利), 空头为负(负债)
            current_market_value = now_price * contracts * multiplier
            # hedge_pnl 含义: 当前期权市值 (多头正/空头负), 配合 net_pnl 公式 = 现货 + 期权市值 - 建仓支出
            hedge_pnl = current_market_value

            if_change_pct = (
                ((now_price / (premium_total / contracts / multiplier)) - 1) * 100
                if (premium_total > 0 and contracts > 0)
                else 0
            )

            total_hedge_notional += abs(premium_total)

            hedge_details.append(
                {
                    "instrument": instrument,
                    "contracts": contracts,
                    "direction": direction,
                    "entry_price": round(
                        premium_total / contracts if contracts else premium_total, 2
                    ),
                    "close_price": round(now_price, 4),
                    "multiplier": multiplier,
                    "notional": round(abs(premium_total), 2),
                    "cost": round(premium_total, 2),
                    "hedge_pnl": round(hedge_pnl, 2),
                    "hedge_pnl_pct": round(if_change_pct, 2),
                    "hedge_type": order.get("hedge_type", order.get("type", "OPTIONS")),
                    # Beta 降低取绝对值 (fill 文件记录为负值, 真实降低量)
                    "beta_reduced": round(abs(beta_reduced_raw), 4),
                    "cost_breakdown": order.get("cost_breakdown"),
                    "option_meta": {
                        "underlying": underlying,
                        "strike": strike,
                        "spot": round(spot, 2) if spot else None,
                        "delta": delta,
                        "current_market_value": round(current_market_value, 2),
                    },
                }
            )
            continue

        # ---- 期货分支 (原逻辑保持不变) ----
        futures_price = _resolve_order_futures_price(order)

        multiplier = order.get("multiplier", 300)  # IF 合约乘数 300
        notional = order.get("notional", 0)

        # 获取IF期货真实收盘价
        # 数据源优先级: 新浪财经 > data_provider(close/last/price) > 开仓价兜底
        # 不用 index_price (它是沪深300指数点位, 不是期货合约价)
        if_close = futures_price  # 默认使用开仓价 (数据源不可用时兜底)
        if instrument == "IF":
            # 1. 尝试新浪财经获取 IF 期货实时价格
            try:
                if_codes = _generate_if_contract_codes()
                if_close = _fetch_if_close_from_sina(futures_price, if_codes)
            except Exception:
                raise  # Re-raise unknown exception
            # 2. 尝试 data_provider (close/last/price)
            if if_close == futures_price:
                if_close = _fetch_if_close_from_provider(
                    futures_price, if_codes, data_provider
                )

        if_change_pct = (
            (if_close - futures_price) / futures_price if futures_price > 0 else 0
        )

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
                "hedge_pnl_pct": round(
                    if_change_pct * 100 * (-1 if direction == "SELL" else 1), 2
                ),
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
            # 优先使用 fill 文件顶层的真实 Beta 降低 (对冲后 Beta), 避免从 order 累加的误差
            "beta_after_hedge": round(
                hedge_data.get("beta_after_hedge", target_beta), 3
            ),
            "total_beta_reduction": round(
                hedge_data.get("portfolio_beta", 1.0)
                - hedge_data.get("beta_after_hedge", target_beta),
                4,
            ),
            "target_beta": round(target_beta, 3),
            "hedge_effectiveness": calculate_hedge_effectiveness(
                hedge_details, hedge_data
            ),
        },
    }


def calculate_hedge_effectiveness(hedge_details: list, hedge_data: dict) -> float:
    """计算对冲有效性"""
    # 简化模型：Beta降低比例作为有效性指标
    beta_reduced = sum(h.get("beta_reduced", 0) for h in hedge_details)
    original_beta = hedge_data.get("portfolio_beta", 1.3)
    effectiveness = beta_reduced / original_beta if original_beta > 0 else 0
    return round(effectiveness * 100, 2)


def _exchange_from_code(code: str) -> str:
    """从标的代码推断交易所"""
    if not code:
        return ""
    if code.endswith(".SH") or code.endswith(".SS"):
        return "上交所"
    if code.endswith(".SZ"):
        return "深交所"
    if code.endswith(".CF"):
        return "中金所"
    return ""


def _build_hedge_detail(
    key: str,
    instrument: str,
    underlying_code: str,
    direction: str,
    contracts: int,
    strike,
    premium_budget: float,
    premium_total: float,
    beta_reduction: float,
    underlying_price: float,
    reason: str,
) -> dict[str, Any]:
    """构造单个对冲头寸明细项 (统一字段契约)"""
    is_option = "put" in instrument.lower() or "call" in instrument.lower()
    multiplier = 10000 if is_option else 0
    margin_rate = 0.0
    abs_beta = abs(beta_reduction) if beta_reduction else 0.0
    if is_option:
        notional = contracts * multiplier * (underlying_price or 0)
        cost = premium_total or premium_budget
    else:
        index_point = 4726.0 if instrument.upper().startswith("IF") else 7500.0
        notional = contracts * multiplier * index_point
        cost = notional * margin_rate
    return {
        "key": key,
        "instrument": instrument,
        "exchange": _exchange_from_code(underlying_code),
        "direction": direction,
        "target_contracts": contracts,
        "multiplier": multiplier,
        "margin_rate": margin_rate,
        "target_beta_reduction": round(abs_beta, 4),
        "strike": strike if strike else "",
        "premium_budget": premium_budget or 0,
        "estimated_notional": round(notional, 2),
        "estimated_cost": round(cost, 2),
        "is_option": is_option,
        "reason": reason,
    }


def analyze_hedge_positions_plan(positions_data: dict[str, Any]) -> dict[str, Any]:
    """分析期货期权计划头寸 (来自 positions.json 的 hedge_positions)

    优先从 active_orders / actual_positions 提取已执行的真实头寸
    (Covered Call 备兑开仓 + 买入 Put 保护), fallback 到配置层。
    跳过 budget_summary (汇总) / vega_event_driven (enabled=false) /
    bear_put_spread (enabled=false) 等非头寸项。

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

    active_orders = hedge_positions.get("active_orders", {})
    actual_positions = hedge_positions.get("actual_positions", [])

    if active_orders and isinstance(active_orders, dict):
        for cc in active_orders.get("covered_call", []) or []:
            if not isinstance(cc, dict):
                continue
            d = _build_hedge_detail(
                key=cc.get("instrument", "covered_call"),
                instrument=cc.get("instrument", ""),
                underlying_code=cc.get("underlying_code", ""),
                direction=cc.get("direction", "SELL_CALL_COVERED"),
                contracts=cc.get("contracts", 0),
                strike=cc.get("est_strike_price", ""),
                premium_budget=cc.get("est_monthly_premium", 0),
                premium_total=cc.get("premium_total", 0),
                beta_reduction=cc.get("beta_reduction", 0),
                underlying_price=cc.get("underlying_price", 0),
                reason=f"备兑开仓 {cc.get('collateral', '')}",
            )
            details.append(d)
            total_notional += d["estimated_notional"]
            total_premium += d["premium_budget"]
            total_beta_reduction += d["target_beta_reduction"]

        for pp in active_orders.get("put_protection", []) or []:
            if not isinstance(pp, dict):
                continue
            d = _build_hedge_detail(
                key=pp.get("instrument", "put_protection"),
                instrument=pp.get("instrument", ""),
                underlying_code=pp.get("underlying_code", ""),
                direction=pp.get("direction", "BUY_PUT"),
                contracts=pp.get("contracts", 0),
                strike=pp.get("est_strike_price", ""),
                premium_budget=pp.get("premium_budget", 0),
                premium_total=pp.get("premium_total", 0),
                beta_reduction=pp.get("beta_reduction", 0),
                underlying_price=pp.get("underlying_price", 0),
                reason=f"尾部保护 {pp.get('strike_rule', '')}",
            )
            details.append(d)
            total_notional += d["estimated_notional"]
            total_premium += d["premium_budget"]
            total_beta_reduction += d["target_beta_reduction"]
    elif actual_positions and isinstance(actual_positions, list):
        for ap in actual_positions:
            if not isinstance(ap, dict):
                continue
            d = _build_hedge_detail(
                key=ap.get("instrument", "actual"),
                instrument=ap.get("instrument", ""),
                underlying_code=ap.get("underlying", ""),
                direction=ap.get("direction", ""),
                contracts=ap.get("contracts", 0),
                strike=ap.get("strike", ""),
                premium_budget=ap.get("premium_total", 0),
                premium_total=ap.get("premium_total", 0),
                beta_reduction=ap.get("beta_reduction", 0),
                underlying_price=0,
                reason=ap.get("expiry", ""),
            )
            details.append(d)
            total_notional += d["estimated_notional"]
            total_premium += d["premium_budget"]
            total_beta_reduction += d["target_beta_reduction"]
    else:
        cc_overlay = hedge_positions.get("covered_call_overlay", {})
        if isinstance(cc_overlay, dict) and cc_overlay.get("enabled", False):
            for u in cc_overlay.get("underlyings", []) or []:
                if not isinstance(u, dict):
                    continue
                d = _build_hedge_detail(
                    key=u.get("code", "covered_call"),
                    instrument=f"{u.get('code', '')} Call",
                    underlying_code=u.get("code", ""),
                    direction="SELL_CALL_COVERED",
                    contracts=0,
                    strike=u.get("strike_rule", ""),
                    premium_budget=u.get("estimated_monthly_premium", 0),
                    premium_total=u.get("estimated_monthly_premium", 0),
                    beta_reduction=0,
                    underlying_price=0,
                    reason=f"备兑担保 {u.get('note', '')}",
                )
                details.append(d)
                total_premium += d["premium_budget"]

        rrc = hedge_positions.get("risk_reversal_collar", {})
        if isinstance(rrc, dict):
            put_prot = rrc.get("put_protection", {})
            if isinstance(put_prot, dict):
                for pk, pp in put_prot.items():
                    if not isinstance(pp, dict):
                        continue
                    d = _build_hedge_detail(
                        key=pk,
                        instrument=pp.get("instrument", pk),
                        underlying_code="",
                        direction=pp.get("direction", "BUY_PUT"),
                        contracts=pp.get("target_contracts", 0),
                        strike=pp.get("strike", ""),
                        premium_budget=pp.get("premium_budget", 0),
                        premium_total=pp.get("premium_budget", 0),
                        beta_reduction=0,
                        underlying_price=0,
                        reason=pp.get("purpose", ""),
                    )
                    details.append(d)
                    total_premium += d["premium_budget"]

    return {
        "details": details,
        "summary": {
            "total_estimated_notional": round(total_notional, 2),
            "total_premium_budget": round(total_premium, 2),
            "total_beta_reduction": round(total_beta_reduction, 3),
            "tool_count": len(details),
        },
    }
