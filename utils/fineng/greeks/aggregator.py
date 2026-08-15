"""
组合希腊字母聚合器 (Portfolio Greeks Aggregator)

将单个期权的 Greeks 按持仓量加权聚合为组合级别的 Greeks 暴露。
支持期权、期货、现货混合持仓的 Greeks 归并。

设计原则:
    - 期权持仓: 使用 BS Greeks (从 fineng.pricing.black_scholes 导入)
    - 期货/现货持仓: Delta = qty * beta, Gamma/Theta/Vega/Rho = 0
    - 聚合时自动乘以合约乘数和持仓数量
"""

from __future__ import annotations

from dataclasses import dataclass, field

from utils.fineng.pricing.black_scholes import bs_all_greeks


@dataclass
class PositionGreeks:
    """单个持仓的 Greeks 贡献"""

    code: str = ""
    instrument_type: str = "STOCK"  # STOCK / FUTURES / OPTION
    direction: str = "LONG"         # LONG / SHORT
    quantity: float = 0.0           # 持仓数量 (正数)
    multiplier: float = 1.0         # 合约乘数
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0
    market_value: float = 0.0       # 市值


@dataclass
class PortfolioGreeks:
    """组合级别 Greeks 汇总"""

    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0
    total_market_value: float = 0.0
    positions: list[PositionGreeks] = field(default_factory=list)

    # 衍生指标
    @property
    def delta_pct(self) -> float:
        """Delta 占组合市值的百分比"""
        if self.total_market_value > 0:
            return self.delta / self.total_market_value
        return 0.0

    @property
    def gamma_per_delta(self) -> float:
        """Gamma/Delta 比率 (衡量 Delta 对标的变动的敏感度)"""
        if abs(self.delta) > 1e-10:
            return self.gamma / abs(self.delta)
        return 0.0

    @property
    def daily_theta(self) -> float:
        """每日 Theta (年化 Theta / 365)"""
        return self.theta / 365.0

    def to_dict(self) -> dict:
        """转为字典 (用于 JSON 序列化/报告)"""
        return {
            "delta": round(self.delta, 4),
            "gamma": round(self.gamma, 6),
            "theta": round(self.theta, 4),
            "vega": round(self.vega, 4),
            "rho": round(self.rho, 4),
            "total_market_value": round(self.total_market_value, 2),
            "delta_pct": round(self.delta_pct, 4),
            "gamma_per_delta": round(self.gamma_per_delta, 6),
            "daily_theta": round(self.daily_theta, 4),
            "n_positions": len(self.positions),
        }


class PortfolioGreeksAggregator:
    """组合 Greeks 聚合器

    将混合持仓 (股票+期货+期权) 的 Greeks 按权重聚合。

    Usage:
        agg = PortfolioGreeksAggregator()
        portfolio = agg.aggregate([
            {"code": "510050.SH", "qty": 10000, "price": 2.75, "type": "STOCK"},
            {"code": "510050C2500M6.SH", "qty": -5, "price": 0.15,
             "type": "OPTION", "K": 2.50, "T": 0.25, "sigma": 0.22, "multiplier": 10000},
        ])
    """

    # -----------------------------------------------------------
    # 批量聚合
    # -----------------------------------------------------------

    def aggregate(
        self,
        holdings: list[dict],
        r: float = 0.02,
    ) -> PortfolioGreeks:
        """聚合持仓列表的 Greeks

        Args:
            holdings: 持仓列表, 每项为 dict:
                - STOCK:  {"code", "qty", "price", "beta"(可选, 默认1)}
                - FUTURES: {"code", "qty", "price", "multiplier", "beta"(可选)}
                - OPTION:  {"code", "qty", "price", "S", "K", "T", "sigma",
                            "is_call", "multiplier"}
            r: 无风险利率

        Returns:
            PortfolioGreeks
        """
        portfolio = PortfolioGreeks()
        total_mv = 0.0

        for h in holdings:
            inst_type = h.get("type", "STOCK").upper()
            qty = float(h.get("qty", 0))
            multiplier = float(h.get("multiplier", 1.0))
            price = float(h.get("price", 0))
            mv = abs(qty) * price * multiplier

            if inst_type == "OPTION":
                pg = self._calc_option_greeks(h, r)
            else:
                pg = self._calc_linear_greeks(h)

            pg.quantity = qty
            pg.multiplier = multiplier
            pg.market_value = mv
            portfolio.positions.append(pg)

            # 方向符号 (LONG=+1, SHORT=-1)
            sign = 1.0 if qty > 0 else -1.0
            portfolio.delta += pg.delta * sign * multiplier
            portfolio.gamma += pg.gamma * sign * multiplier
            portfolio.theta += pg.theta * sign * multiplier
            portfolio.vega += pg.vega * sign * multiplier
            portfolio.rho += pg.rho * sign * multiplier
            total_mv += mv

        portfolio.total_market_value = total_mv
        return portfolio

    # -----------------------------------------------------------
    # 单持仓 Greeks 计算
    # -----------------------------------------------------------

    def _calc_option_greeks(self, h: dict, r: float) -> PositionGreeks:
        """计算期权持仓 Greeks"""
        S = float(h.get("S", 0))
        K = float(h.get("K", 0))
        T = float(h.get("T", 0))
        sigma = float(h.get("sigma", 0.20))
        is_call = bool(h.get("is_call", True))

        g = bs_all_greeks(S, K, T, r, sigma, is_call)
        sign = 1.0 if float(h.get("qty", 0)) > 0 else -1.0

        return PositionGreeks(
            code=str(h.get("code", "")),
            instrument_type="OPTION",
            direction="LONG" if sign > 0 else "SHORT",
            delta=g.delta,
            gamma=g.gamma,
            theta=g.theta,
            vega=g.vega,
            rho=g.rho,
        )

    def _calc_linear_greeks(self, h: dict) -> PositionGreeks:
        """计算线性工具 (股票/期货) Greeks"""
        beta = float(h.get("beta", 1.0))
        price = float(h.get("price", 0))
        qty = float(h.get("qty", 0))

        return PositionGreeks(
            code=str(h.get("code", "")),
            instrument_type=h.get("type", "STOCK").upper(),
            direction="LONG" if qty > 0 else "SHORT",
            delta=price * beta,
            gamma=0.0,
            theta=0.0,
            vega=0.0,
            rho=0.0,
        )

    # -----------------------------------------------------------
    # 对冲信号生成
    # -----------------------------------------------------------

    def rebalance_signal(
        self,
        portfolio: PortfolioGreeks,
        delta_tolerance: float = 0.02,
        gamma_tolerance: float = 0.01,
        vega_tolerance: float = 0.05,
    ) -> dict:
        """生成 Greeks 再平衡信号

        Args:
            portfolio: 当前组合 Greeks
            delta_tolerance: Delta 容忍度 (占组合市值比例)
            gamma_tolerance: Gamma 容忍度 (相对)
            vega_tolerance: Vega 容忍度 (相对)

        Returns:
            dict: {
                "need_rebalance": bool,
                "delta_excess": float,
                "gamma_excess": float,
                "vega_excess": float,
                "alerts": list[str],
            }
        """
        alerts = []
        mv = portfolio.total_market_value

        delta_excess = abs(portfolio.delta) - delta_tolerance * mv if mv > 0 else 0
        if delta_excess > 0:
            alerts.append(f"Delta 超标: {portfolio.delta:.0f} (容忍 {delta_tolerance * mv:.0f})")

        gamma_excess = abs(portfolio.gamma) - gamma_tolerance * abs(portfolio.delta) if abs(portfolio.delta) > 1e-6 else 0
        if gamma_excess > 0:
            alerts.append(f"Gamma 超标: {portfolio.gamma:.6f}")

        vega_excess = abs(portfolio.vega) - vega_tolerance * mv if mv > 0 else 0
        if vega_excess > 0:
            alerts.append(f"Vega 超标: {portfolio.vega:.0f} (容忍 {vega_tolerance * mv:.0f})")

        return {
            "need_rebalance": len(alerts) > 0,
            "delta_excess": round(delta_excess, 4),
            "gamma_excess": round(gamma_excess, 6),
            "vega_excess": round(vega_excess, 4),
            "alerts": alerts,
        }
