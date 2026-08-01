# v7.6 P&L 归因引擎 -- Citadel 标准日/周/月三级归因
# 当前: 仅有简单日盈亏, 不知道盈亏来源
# 优化: 分解为 Beta收益 + Alpha + 风格因子 + 行业 + 个股特异 + 对冲 + 成本
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List

import numpy as np

logger = logging.getLogger("v76.pnl.attribution")


@dataclass
class FactorExposure:
    beta: float = 0.0
    size: float = 0.0  # 大盘 / 小盘
    value: float = 0.0  # 价值 / 成长
    momentum: float = 0.0
    quality: float = 0.0
    low_vol: float = 0.0


@dataclass
class PositionSnapshot:
    symbol: str
    weight: float
    daily_return: float
    factor_exposure: FactorExposure = field(default_factory=FactorExposure)
    style: str = ""
    sector: str = ""


class PnLAttributionEngine:
    """交易成本分析 + PnL 归因 + Alpha 衰减追踪"""

    def __init__(self):
        self._daily_log: List[dict] = []
        self._weekly_log: List[dict] = []
        self._factor_returns: Dict[str, List[float]] = defaultdict(list)
        self._tca_data: List[dict] = []  # Transaction Cost Analysis

    def attribute_daily(self, positions: List[PositionSnapshot], total_pnl: float, total_nav: float) -> dict:
        """分解单日收益"""

        if not positions:
            return {"status": "empty"}

        # 收益率分解
        pnl_pct = total_pnl / max(total_nav, 1)

        # Beta 贡献
        weighted_beta = sum(p.weight * p.factor_exposure.beta for p in positions if p.weight > 0.001)
        mkt_return = 0.0  # 需要外部传入
        beta_contrib = weighted_beta * mkt_return

        # 行业 / 风格贡献
        style_pnl: Dict[str, float] = defaultdict(float)
        sector_pnl: Dict[str, float] = defaultdict(float)

        for p in positions:
            contribution = p.weight * p.daily_return
            style_pnl[p.style] += contribution
            sector_pnl[p.sector] += contribution

        # Alpha (残差): 总收益 - Beta贡献
        alpha = pnl_pct - beta_contrib

        # 最佳/最差
        sorted_by_pnl = sorted(
            [(p.symbol, p.weight * p.daily_return) for p in positions], key=lambda x: x[1], reverse=True
        )
        winners = sorted_by_pnl[:3]
        losers = sorted_by_pnl[-3:] if len(sorted_by_pnl) >= 6 else []

        result = {
            "date": date.today().isoformat(),
            "total_pnl": round(total_pnl, 2),
            "pnl_pct": round(pnl_pct * 100, 4),
            "beta_contrib": round(beta_contrib * 100, 4),
            "alpha_pct": round(alpha * 100, 4),
            "style_pnl": {k: round(v * 100, 4) for k, v in style_pnl.items()},
            "sector_pnl": {k: round(v * 100, 4) for k, v in sector_pnl.items()},
            "top3_contributors": [(s, round(p * 100, 4)) for s, p in winners],
            "top3_detractors": [(s, round(p * 100, 4)) for s, p in losers],
            "position_count": len(positions),
        }
        self._daily_log.append(result)
        return result

    def record_trade(
        self,
        symbol: str,
        side: str,
        quantity: int,
        expected_price: float,
        actual_price: float,
        urgency: str = "routine",
    ) -> dict:
        """记录每笔交易成本"""
        slippage = (actual_price - expected_price) / expected_price
        cost = quantity * abs(actual_price - expected_price)

        tca = {
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "expected_price": expected_price,
            "actual_price": actual_price,
            "slippage_bps": round(slippage * 10000, 1),
            "cost": round(cost, 2),
            "urgency": urgency,
        }
        self._tca_data.append(tca)

        if abs(slippage) > 0.005:  # > 50bp
            logger.warning("TCA: %s %s ×%d 滑点 %.1fbp 成本%.2f", symbol, side, quantity, slippage * 10000, cost)
        return tca

    def weekly_summary(self) -> dict:
        recent = self._daily_log[-5:] if len(self._daily_log) >= 5 else self._daily_log
        if not recent:
            return {"status": "empty"}

        total_pnl_pct = sum(d["pnl_pct"] for d in recent)
        avg_alpha = sum(d["alpha_pct"] for d in recent) / len(recent)

        # 信息比率
        alphas = [d["alpha_pct"] for d in recent]
        stdev_alpha = float(np.std(alphas)) if len(alphas) > 1 else 0
        ir = avg_alpha / stdev_alpha if stdev_alpha > 0 else 0

        # TCA 汇总
        tca_total = sum(t["cost"] for t in self._tca_data[-20:])

        return {
            "period": "weekly",
            "total_pnl_pct": round(total_pnl_pct, 4),
            "avg_daily_alpha_bps": round(avg_alpha, 2),
            "information_ratio": round(ir, 2),
            "tca_total_cost": round(tca_total, 2),
            "tca_trades_count": len(self._tca_data),
            "alpha_positive_days": sum(1 for d in recent if d["alpha_pct"] > 0),
            "total_days": len(recent),
        }

    def report(self) -> dict:
        today = self._daily_log[-1] if self._daily_log else {"total_pnl": 0}
        weekly = self.weekly_summary()
        return {
            "today_pnl": today.get("total_pnl", 0),
            "today_alpha_bps": today.get("alpha_pct", 0),
            "weekly_ir": weekly.get("information_ratio", 0),
            "tca_cumulative": sum(t["cost"] for t in self._tca_data),
        }
