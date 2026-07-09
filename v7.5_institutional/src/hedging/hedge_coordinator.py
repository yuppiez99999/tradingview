# -*- coding: utf-8 -*-
"""
v7.5 三联对冲协调器 —— Beta + Vol + Correlation 联动决策

职责:
    1. 并行调用三个对冲子模块
    2. 汇总对冲指令
    3. 防止对冲叠加 (避免三重对冲导致净敞口过低)
    4. 输出统一的对冲计划

调用顺序:
    1. Correlation Hedge (避险资产配置, 最优先)
    2. Beta Hedge (期货空头)
    3. Vol Hedge (期权保护)
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import pandas as pd

from .beta_hedger import BetaHedger
from .vol_hedger import VolHedger
from .correlation_hedger import CorrelationHedger
from .tail_risk_hedge import TailRiskHedger, MarketRegime

logger = logging.getLogger("v75.hedging.coordinator")


class HedgeCoordinator:
    """三联对冲协调器"""

    def __init__(self,
                 beta_hedger: Optional[BetaHedger] = None,
                 vol_hedger: Optional[VolHedger] = None,
                 corr_hedger: Optional[CorrelationHedger] = None,
                 tail_hedger: Optional[TailRiskHedger] = None,
                 max_total_hedge_pct: float = 0.40,
                 enable_tail_risk: bool = True):
        """
        Args:
            beta_hedger: Beta 对冲器 (None 则用默认配置)
            vol_hedger: 波动率对冲器
            corr_hedger: 相关性对冲器
            tail_hedger: 7.4 移植 尾部风险对冲器 (4 状态机 + OTM 阶梯)
            max_total_hedge_pct: 总对冲资金占比上限 (默认 40%)
            enable_tail_risk: 是否启用 7.4 尾部风险模块
        """
        self.beta_hedger = beta_hedger or BetaHedger()
        self.vol_hedger = vol_hedger or VolHedger()
        self.corr_hedger = corr_hedger or CorrelationHedger()
        self.tail_hedger = tail_hedger or TailRiskHedger()
        self.enable_tail_risk = enable_tail_risk
        # 7.4 移植: 极端行情下上限动态提升至 90% (auto_hedge_executor.py:754)
        self.max_total_hedge_normal = float(max_total_hedge_pct)
        self.max_total_hedge_crisis = 0.90   # LEVEL_4 时
        self.max_total_hedge_warning = 0.70  # LEVEL_3 时

    @property
    def max_total_hedge(self) -> float:
        """7.4 移植: 动态上限 — 根据当前市场状态调整"""
        regime = self.tail_hedger.current_regime
        if regime == MarketRegime.CRISIS:
            return self.max_total_hedge_crisis
        elif regime == MarketRegime.WARNING:
            return self.max_total_hedge_warning
        elif regime == MarketRegime.RECOVERY:
            return self.max_total_hedge_normal * 0.6  # 恢复期降低对冲占比
        else:
            return self.max_total_hedge_normal

    def coordinate(self,
                   positions: Dict[str, float],
                   prices: Dict[str, float],
                   returns: pd.DataFrame,
                   market_returns: pd.Series,
                   vix: float,
                   portfolio_value: Optional[float] = None,
                   hwm_drawdown: float = 0.0,
                   bs_loss: float = 0.0) -> Dict[str, object]:
        """协调三联对冲 (7.4 移植增强版)

        7.4 新增:
            - 调用 TailRiskHedger 判定 4 状态机
            - hwm_drawdown + bs_loss 作为额外输入
            - 动态上限: crisis 90% / warning 70% / recovery 24%

        Args:
            positions: 持仓 {symbol: quantity}
            prices: 当前价格
            returns: 历史收益率
            market_returns: 市场收益率
            vix: VIX 指数
            portfolio_value: 组合市值 (None 则自动计算)
            hwm_drawdown: 距历史高点的累计回撤 (正数)
            bs_loss: 黑天鹅损失幅度 (0-1, 用于决定 OTM 深度)

        Returns:
            统一对冲计划
        """
        # 1. 计算组合市值
        if portfolio_value is None:
            portfolio_value = sum(positions.get(s, 0) * prices.get(s, 0)
                                  for s in positions if s in prices)
        if portfolio_value <= 0:
            return {"action": "SKIP", "reason": "组合市值为 0"}

        # 2. 7.4 移植: 调用尾部风险模块判定 4 状态机
        if len(returns) > 0 and not returns.isna().all().all():
            try:
                portfolio_vol = float(returns.std().mean() * (252 ** 0.5))
                portfolio_vol = 0.0 if not np.isfinite(portfolio_vol) else portfolio_vol
            except Exception:
                portfolio_vol = 0.0
        else:
            portfolio_vol = 0.0
        regime = self.tail_hedger.analyze_market_regime(
            vix=vix,
            hwm_drawdown=hwm_drawdown,
            portfolio_volatility=portfolio_vol,
        )

        # 3. 7.4 移植: 尾部风险对冲决策 (5 级 + OTM 阶梯)
        tail_order = {}
        if self.enable_tail_risk:
            spot = list(prices.values())[0] if prices else 1.0
            tail_order = self.tail_hedger.compute_hedge(
                vix=vix,
                hwm_drawdown=hwm_drawdown,
                portfolio_value=portfolio_value,
                spot_price=spot,
                bs_loss=bs_loss,
                portfolio_volatility=portfolio_vol,
            )

        # 4. 计算组合 Beta
        beta_port = self.beta_hedger.portfolio_beta(
            positions, prices, returns, market_returns)

        # 5. 并行调用三个对冲器
        beta_order = self.beta_hedger.compute_hedge(beta_port, portfolio_value)
        vol_order = self.vol_hedger.compute_hedge(vix, portfolio_value)
        corr_order = self.corr_hedger.compute_hedge(returns, portfolio_value)

        # 6. 汇总对冲成本
        total_cost = 0.0
        total_hedge_value = 0.0
        orders: List[Dict] = []

        # 7.4 移植: recovery 状态衰减对冲比例 (tail_risk_hedge.py:264-265)
        recovery_scale = 0.3 if regime == MarketRegime.RECOVERY else \
                         0.7 if regime == MarketRegime.WARNING else 1.0

        for name, order in [("CORR", corr_order),
                            ("BETA", beta_order),
                            ("VOL", vol_order)]:
            if order.get("action") in ("NO_HEDGE", "SKIP", "ERROR"):
                continue
            cost = order.get("estimated_cost", 0.0) or order.get("budget", 0.0)
            hedge_value = order.get("notional", 0.0) or order.get("gold_value", 0.0)
            total_cost += float(cost)
            total_hedge_value += float(hedge_value)
            order["hedge_type"] = name
            orders.append(order)

        # 加入尾部风险对冲指令
        if tail_order and tail_order.get("action") not in ("NO_HEDGE",):
            tail_action_value = portfolio_value * tail_order.get("protection_ratio", 0)
            total_hedge_value += float(tail_action_value)
            total_cost += float(tail_order.get("budget_total", 0))
            orders.append({
                "hedge_type": "TAIL",
                "action": tail_order.get("action"),
                "regime": regime,
                "protection_ratio": tail_order.get("protection_ratio", 0),
                "budget": tail_order.get("budget_total", 0),
                "notional": tail_action_value,
                "otm_ladder": tail_order.get("otm_ladder", []),
                "budget_allocation": tail_order.get("budget_allocation", {}),
            })

        # 7. 总对冲占比检查 (动态上限)
        total_hedge_pct = total_hedge_value / portfolio_value \
            if portfolio_value > 0 else 0.0

        # 8. 防止过度对冲 (使用动态上限)
        current_max = self.max_total_hedge
        if total_hedge_pct > current_max:
            scale = current_max / total_hedge_pct
            logger.warning("过度对冲: 总占比 %.2f%% > 上限 %.2f%% (regime=%s), 缩放 %.2f",
                           total_hedge_pct * 100,
                           current_max * 100, regime, scale)
            for o in orders:
                if "notional" in o:
                    o["notional"] = o["notional"] * scale
                    o["contracts"] = int(o.get("contracts", 0) * scale)
                if "gold_value" in o:
                    o["gold_value"] = o["gold_value"] * scale
                    o["gold_weight"] = o["gold_weight"] * scale
                if "repo_value" in o:
                    o["repo_value"] = o["repo_value"] * scale
                    o["repo_weight"] = o["repo_weight"] * scale
                if "budget" in o:
                    o["budget"] = o["budget"] * scale
            total_hedge_value *= scale
            total_cost *= scale
            total_hedge_pct = current_max

        return {
            "action": "HEDGE" if orders else "NO_HEDGE",
            "portfolio_beta": float(beta_port),
            "portfolio_value": float(portfolio_value),
            "vix": float(vix),
            "regime": regime,                     # 7.4 移植
            "hwm_drawdown": float(hwm_drawdown),  # 7.4 移植
            "orders": orders,
            "total_hedge_value": float(total_hedge_value),
            "total_hedge_pct": float(total_hedge_pct),
            "total_cost": float(total_cost),
            "total_cost_pct": float(total_cost / portfolio_value
                                    if portfolio_value > 0 else 0.0),
            "max_hedge_limit": current_max,       # 7.4 移植
            "summary": {
                "beta_hedge": beta_order.get("action"),
                "vol_hedge": vol_order.get("action"),
                "corr_hedge": corr_order.get("action"),
                "tail_hedge": tail_order.get("action", "DISABLED"),
                "regime": regime,
            },
        }

    def snapshot(self) -> Dict[str, str]:
        """对冲配置快照"""
        return {
            "beta_target": str(self.beta_hedger.beta_target),
            "beta_trigger": str(self.beta_hedger.beta_trigger),
            "vix_trigger": str(self.vol_hedger.vix_trigger),
            "vix_emergency": str(self.vol_hedger.vix_emergency),
            "corr_trigger": str(self.corr_hedger.corr_trigger),
            "max_total_hedge_pct": str(self.max_total_hedge),
        }
