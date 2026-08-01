# -*- coding: utf-8 -*-
"""
Greeks 计算模块 v1.0

功能:
  1. 欧式期权 Black-Scholes  Greeks 计算
  2. 标的持仓/策略级 Greeks 聚合
  3. 组合 Greeks 敏感性监控
  4. Wind MCP / 免费数据源回退

输出:
  - 单个期权 Greeks: delta / gamma / theta / vega / rho
  - 组合 Greeks: 按标的、到期日、方向聚合
  - 监控面板可直接消费的 DataFrame / Dict
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

# 标准正态分布相关量（避免重复计算）
_SQRT_2PI = math.sqrt(2.0 * math.pi)
_INV_SQRT_2PI = 1.0 / _SQRT_2PI


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) * _INV_SQRT_2PI


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass
class OptionContract:
    """期权合约基础信息"""

    symbol: str
    underlying: str
    underlying_price: float
    strike: float
    expiry: datetime
    option_type: str = "C"  # C=认购/call, P=认沽/put
    implied_vol: float = 0.0
    risk_free_rate: float = 0.02
    dividend_yield: float = 0.0
    quantity: int = 1
    position: str = "long"  # long/short


@dataclass
class OptionGreeks:
    """单个期权 Greeks"""

    symbol: str
    underlying: str
    strike: float
    expiry: datetime
    option_type: str
    position: str
    quantity: int
    underlying_price: float
    implied_vol: float
    d1: float = 0.0
    d2: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0
    price: float = 0.0


@dataclass
class PortfolioGreeks:
    """组合级 Greeks"""

    underlying: str
    total_delta: float = 0.0
    total_gamma: float = 0.0
    total_theta: float = 0.0
    total_vega: float = 0.0
    total_rho: float = 0.0
    net_contracts: int = 0
    exposure_by_expiry: Dict[str, Dict[str, float]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def _years_to_expiry(expiry: datetime, now: Optional[datetime] = None) -> float:
    if now is None:
        now = datetime.now()
    delta = expiry - now
    return max(delta.total_seconds() / (365.25 * 24 * 3600), 1e-9)


def black_scholes_price(
    underlying_price: float,
    strike: float,
    t: float,
    sigma: float,
    r: float,
    q: float,
    option_type: str,
) -> float:
    """Black-Scholes 欧式期权理论价"""
    if t <= 0 or sigma <= 0:
        (underlying_price * (1.0 - q) / strike) if strike > 0 else 0.0
        if option_type.upper().startswith("C"):
            return max(underlying_price - strike, 0.0)
        return max(strike - underlying_price, 0.0)

    d1 = (math.log(underlying_price / strike) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)

    if option_type.upper().startswith("C"):
        price = underlying_price * math.exp(-q * t) * _norm_cdf(d1) - strike * math.exp(-r * t) * _norm_cdf(d2)
    else:
        price = strike * math.exp(-r * t) * _norm_cdf(-d2) - underlying_price * math.exp(-q * t) * _norm_cdf(-d1)

    return max(price, 0.0)


def compute_greeks(contract: OptionContract, now: Optional[datetime] = None) -> OptionGreeks:
    """
    计算单个期权合约的 Greeks

    约定:
      - theta/vega/rho 按“每自然日、每1% vol”标准输出
      - 多头为正，空头为负；再按 quantity 缩放
    """
    t = _years_to_expiry(contract.expiry, now)
    s = float(contract.underlying_price)
    k = float(contract.strike)
    sigma = float(contract.implied_vol)
    r = float(contract.risk_free_rate)
    q = float(contract.dividend_yield)
    option_type = (contract.option_type or "C").upper()
    position = (contract.position or "long").lower()
    qty = int(contract.quantity or 1)

    g = OptionGreeks(
        symbol=contract.symbol,
        underlying=contract.underlying,
        strike=k,
        expiry=contract.expiry,
        option_type=option_type,
        position=position,
        quantity=qty,
        underlying_price=s,
        implied_vol=sigma,
    )

    if t <= 1e-9 or sigma <= 1e-9 or s <= 0 or k <= 0:
        g.price = max(s - k, 0.0) if option_type.startswith("C") else max(k - s, 0.0)
        return g

    sqrt_t = math.sqrt(t)
    d1 = (math.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t

    g.d1 = d1
    g.d2 = d2

    # price
    if option_type.startswith("C"):
        g.price = s * math.exp(-q * t) * _norm_cdf(d1) - k * math.exp(-r * t) * _norm_cdf(d2)
    else:
        g.price = k * math.exp(-r * t) * _norm_cdf(-d2) - s * math.exp(-q * t) * _norm_cdf(-d1)

    g.price = max(g.price, 0.0)

    nd1 = _norm_pdf(d1)

    # delta
    if option_type.startswith("C"):
        delta = math.exp(-q * t) * _norm_cdf(d1)
    else:
        delta = math.exp(-q * t) * (_norm_cdf(d1) - 1.0)

    # gamma
    gamma = math.exp(-q * t) * nd1 / (s * sigma * sqrt_t)

    # theta: 按自然日
    if option_type.startswith("C"):
        theta = (
            -(s * nd1 * sigma * math.exp(-q * t)) / (2.0 * sqrt_t)
            - r * k * math.exp(-r * t) * _norm_cdf(d2)
            + q * s * math.exp(-q * t) * _norm_cdf(d1)
        )
    else:
        theta = (
            -(s * nd1 * sigma * math.exp(-q * t)) / (2.0 * sqrt_t)
            + r * k * math.exp(-r * t) * _norm_cdf(-d2)
            - q * s * math.exp(-q * t) * _norm_cdf(-d1)
        )

    theta = theta / 365.0

    # vega: 每1%波动率
    vega = s * math.exp(-q * t) * nd1 * sqrt_t / 100.0

    # rho: 每1%利率
    if option_type.startswith("C"):
        rho = k * t * math.exp(-r * t) * _norm_cdf(d2) / 100.0
    else:
        rho = -k * t * math.exp(-r * t) * _norm_cdf(-d2) / 100.0

    # 方向与数量
    sign = 1.0 if position == "long" else -1.0
    g.delta = sign * delta * qty
    g.gamma = sign * gamma * qty
    g.theta = sign * theta * qty
    g.vega = sign * vega * qty
    g.rho = sign * rho * qty

    return g


def aggregate_portfolio_greeks(
    contracts: List[OptionContract], now: Optional[datetime] = None
) -> Dict[str, PortfolioGreeks]:
    """
    按 underlying 聚合组合 Greeks

    Returns:
        {underlying: PortfolioGreeks}
    """
    portfolio: Dict[str, List[OptionGreeks]] = {}
    for c in contracts:
        g = compute_greeks(c, now)
        portfolio.setdefault(c.underlying, []).append(g)

    result: Dict[str, PortfolioGreeks] = {}
    for ul, greeks_list in portfolio.items():
        pg = PortfolioGreeks(underlying=ul)
        for g in greeks_list:
            pg.total_delta += g.delta
            pg.total_gamma += g.gamma
            pg.total_theta += g.theta
            pg.total_vega += g.vega
            pg.total_rho += g.rho
            pg.net_contracts += g.quantity if g.position == "long" else -g.quantity

            expiry_key = g.expiry.strftime("%Y-%m-%d")
            bucket = pg.exposure_by_expiry.setdefault(
                expiry_key,
                {
                    "delta": 0.0,
                    "gamma": 0.0,
                    "theta": 0.0,
                    "vega": 0.0,
                    "contracts": 0,
                },
            )
            bucket["delta"] += g.delta
            bucket["gamma"] += g.gamma
            bucket["theta"] += g.theta
            bucket["vega"] += g.vega
            bucket["contracts"] += g.quantity if g.position == "long" else -g.quantity

        # 生成监控告警
        if abs(pg.total_delta) > 500:
            pg.warnings.append(f"Delta 暴露偏高: {pg.total_delta:+.0f}")
        if pg.total_gamma > 200:
            pg.warnings.append(f"Gamma 风险偏高: {pg.total_gamma:+.0f}")
        if pg.total_theta > 3000:
            pg.warnings.append(f"Theta 衰减过快: {pg.total_theta:+.0f}/日")
        if abs(pg.total_vega) > 10000:
            pg.warnings.append(f"Vega 波动率敏感: {pg.total_vega:+.0f}")

        result[ul] = pg

    return result


def build_demo_contracts(underlying_price_map: Dict[str, float]) -> List[OptionContract]:
    """
    生成演示用期权持仓（用于页面首次加载或空数据时展示）
    """
    now = datetime.now()
    expiry1 = datetime(now.year, now.month + 1, 20) if now.month < 12 else datetime(now.year + 1, 1, 20)
    expiry2 = datetime(now.year + 1, 6, 18)
    expiries = [expiry1, expiry2]

    contracts: List[OptionContract] = []
    for ul, price in underlying_price_map.items():
        strikes = [price * 0.95, price, price * 1.05]
        for expiry in expiries:
            for strike in strikes:
                for opt_type in ("C", "P"):
                    contracts.append(
                        OptionContract(
                            symbol=f"{ul}{expiry.strftime('%y%m')}{'C' if opt_type == 'C' else 'P'}{int(strike):05d}",
                            underlying=ul,
                            underlying_price=price,
                            strike=strike,
                            expiry=expiry,
                            option_type=opt_type,
                            implied_vol=0.18 + abs(math.log(price / strike)) * 0.5,
                            quantity=1,
                            position="long",
                        )
                    )
    return contracts


def greeks_to_dataframe(portfolio: Dict[str, PortfolioGreeks]) -> "pd.DataFrame":
    """组合 Greeks 输出为 DataFrame，供 Streamlit 直接展示"""
    import pandas as pd

    rows = []
    for ul, pg in portfolio.items():
        rows.append(
            {
                "标的": ul,
                "Delta": round(pg.total_delta, 4),
                "Gamma": round(pg.total_gamma, 4),
                "Theta": round(pg.total_theta, 4),
                "Vega": round(pg.total_vega, 4),
                "Rho": round(pg.total_rho, 4),
                "净张数": pg.net_contracts,
                "到期日数": len(pg.exposure_by_expiry),
                "告警数": len(pg.warnings),
            }
        )
    return pd.DataFrame(rows)


def expiry_bucket_to_dataframe(pg: PortfolioGreeks) -> "pd.DataFrame":
    """单个标的到期日 Greeks 明细"""
    import pandas as pd

    rows = []
    for exp, bucket in sorted(pg.exposure_by_expiry.items()):
        rows.append(
            {
                "到期日": exp,
                "Delta": round(bucket.get("delta", 0.0), 4),
                "Gamma": round(bucket.get("gamma", 0.0), 4),
                "Theta": round(bucket.get("theta", 0.0), 4),
                "Vega": round(bucket.get("vega", 0.0), 4),
                "净张数": bucket.get("contracts", 0),
            }
        )
    return pd.DataFrame(rows)


def load_positions_for_greeks(path: Optional[str] = None) -> List[OptionContract]:
    """
    从本地持仓文件加载期权仓位

    约定文件格式:
    [
      {
        "symbol": "...",
        "underlying": "510300",
        "underlying_price": 3.8,
        "strike": 3.9,
        "expiry": "2026-08-20",
        "option_type": "C",
        "implied_vol": 0.22,
        "quantity": 1,
        "position": "long"
      }
    ]
    """
    if path is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base_dir, "config", "options_positions.json")

    if not os.path.exists(path):
        return []

    try:
        import json

        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        contracts: List[OptionContract] = []
        for item in raw:
            expiry_str = item.get("expiry")
            expiry = datetime.strptime(expiry_str, "%Y-%m-%d") if expiry_str else datetime.now()
            contracts.append(
                OptionContract(
                    symbol=str(item.get("symbol", "")),
                    underlying=str(item.get("underlying", "")),
                    underlying_price=float(item.get("underlying_price", 0.0) or 0.0),
                    strike=float(item.get("strike", 0.0) or 0.0),
                    expiry=expiry,
                    option_type=str(item.get("option_type", "C") or "C"),
                    implied_vol=float(item.get("implied_vol", 0.18) or 0.18),
                    risk_free_rate=float(item.get("risk_free_rate", 0.02) or 0.02),
                    dividend_yield=float(item.get("dividend_yield", 0.0) or 0.0),
                    quantity=int(item.get("quantity", 1) or 1),
                    position=str(item.get("position", "long") or "long"),
                )
            )
        return contracts
    except Exception:
        return []


# ============================================================
# 快速测试
# ============================================================
if __name__ == "__main__":
    demo_contracts = build_demo_contracts(
        {
            "510300": 3.8,
            "510050": 2.7,
            "000300": 3850.0,
        }
    )
    portfolio = aggregate_portfolio_greeks(demo_contracts)
    df = greeks_to_dataframe(portfolio)
    print(df.to_string(index=False))
