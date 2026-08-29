"""
RiskAgent — 风险分析 Agent (含 veto 权)
========================================

设计借鉴:
  - awesome-llm-apps/ai_hedge_fund 的 risk_agent 模式
  - 拥有 veto 权: 极端情况下可一票否决 (即使其他 Agent 强烈看多)

决策逻辑:
  1. 个股回撤: 近 20 日最大回撤
     - 回撤 > 15% → 看空 (strength -= 0.4)
     - 回撤 > 25% → 触发 veto (action=veto)
  2. 波动率: 近 20 日年化波动率
     - 波动率 > 40% → 看空 (strength -= 0.2)
     - 波动率 > 60% → 触发 veto
  3. 相关性: 与大盘相关性 (β)
     - β > 1.5 → 看空 (strength -= 0.1, 高Beta风险)
  4. 持仓集中度: 该标的在组合中的权重
     - 权重 > 15% → 看空 (strength -= 0.1, 集中度风险)
  5. 流动性: 20 日均成交额
     - < 5000 万 → 看空 (strength -= 0.2, 流动性风险)

veto 优先级最高: 若任一指标触发 veto, 直接返回 veto, 不计算其他指标.

集成日期: 2026-07-26
"""

from __future__ import annotations

import math
from typing import Any

from utils.finance_agents.base_agent import AgentDecision, BaseAgent


class RiskAgent(BaseAgent):
    """风险分析 Agent (含 veto 权)"""

    # 阈值常量 (可调)
    DRAWDOWN_WARN_PCT = 0.15  # 15% 回撤预警
    DRAWDOWN_VETO_PCT = 0.25  # 25% 回撤 veto
    VOLATILITY_WARN_PCT = 0.40  # 40% 年化波动率预警
    VOLATILITY_VETO_PCT = 0.60  # 60% 年化波动率 veto
    WEIGHT_WARN_PCT = 0.15  # 15% 权重预警
    LIQUIDITY_MIN_AMOUNT = 50_000_000  # 5000 万最低流动性

    def __init__(self, name: str = "risk"):
        super().__init__(name=name)

    def is_available(self, context: dict[str, Any]) -> bool:
        """风险 Agent 始终可用 (即使数据缺失也返回 hold)"""
        return True

    def analyze(self, symbol: str, context: dict[str, Any]) -> AgentDecision:
        """风险分析主入口"""
        kline: list[dict] = self._safe_get(context, "kline", default=[]) or []
        position_weight = self._safe_float(self._safe_get(context, "position_weight"))
        beta = self._safe_float(self._safe_get(context, "beta"))

        metrics: dict[str, Any] = {
            "position_weight": position_weight,
            "beta": beta,
        }

        # 1. 计算 20 日最大回撤
        drawdown = self._calc_drawdown(kline, 20)
        if drawdown is not None:
            metrics["max_drawdown_20d"] = round(drawdown, 4)
            if drawdown >= self.DRAWDOWN_VETO_PCT:
                return AgentDecision(
                    agent_name=self.name,
                    symbol=symbol,
                    action="veto",
                    strength=-1.0,
                    confidence=0.95,
                    reasoning=f"20日最大回撤 {drawdown:.1%} >= {self.DRAWDOWN_VETO_PCT:.0%}",
                    key_metrics=metrics,
                    veto_reason=f"回撤超 veto 阈值 ({drawdown:.1%})",
                )
            if drawdown >= self.DRAWDOWN_WARN_PCT:
                # 进入看空区, 后续继续算其他指标
                pass

        # 2. 计算年化波动率
        volatility = self._calc_volatility(kline, 20)
        if volatility is not None:
            metrics["volatility_20d_annual"] = round(volatility, 4)
            if volatility >= self.VOLATILITY_VETO_PCT:
                return AgentDecision(
                    agent_name=self.name,
                    symbol=symbol,
                    action="veto",
                    strength=-1.0,
                    confidence=0.9,
                    reasoning=f"20日年化波动率 {volatility:.1%} >= {self.VOLATILITY_VETO_PCT:.0%}",
                    key_metrics=metrics,
                    veto_reason=f"波动率超 veto 阈值 ({volatility:.1%})",
                )

        # 3. 综合风险评分 (非 veto 路径)
        strength = 0.0
        signals = []

        if drawdown is not None and drawdown >= self.DRAWDOWN_WARN_PCT:
            strength -= 0.4
            signals.append(f"回撤 {drawdown:.1%} (高)")

        if volatility is not None and volatility >= self.VOLATILITY_WARN_PCT:
            strength -= 0.2
            signals.append(f"波动率 {volatility:.1%} (高)")

        if beta > 1.5:
            strength -= 0.1
            signals.append(f"β={beta:.2f} (高 Beta)")

        if position_weight > self.WEIGHT_WARN_PCT:
            strength -= 0.1
            signals.append(f"权重 {position_weight:.1%} (集中)")

        # 4. 流动性
        liquidity_amount = self._calc_avg_amount(kline, 20)
        if liquidity_amount is not None:
            metrics["avg_amount_20d"] = round(liquidity_amount, 0)
            if liquidity_amount < self.LIQUIDITY_MIN_AMOUNT:
                strength -= 0.2
                signals.append(
                    f"20日均成交额 {liquidity_amount / 1e8:.2f}亿 (低流动性)"
                )

        # 决策动作
        if strength < -0.5:
            action = "sell"
        elif strength < -0.3:
            action = "hold"  # 风险高但不强烈看空
        else:
            action = "hold"

        confidence = min(0.85, 0.4 + 0.1 * len(signals))

        return AgentDecision(
            agent_name=self.name,
            symbol=symbol,
            action=action,
            strength=max(-1.0, min(1.0, strength)),
            confidence=confidence,
            reasoning="; ".join(signals) if signals else "风险指标正常",
            key_metrics=metrics,
        )

    # ----------------------------------------------------------
    # 风险指标计算
    # ----------------------------------------------------------

    @staticmethod
    def _calc_drawdown(kline: list[dict], period: int):
        """计算最近 period 日的最大回撤

        Returns:
            float or None: 最大回撤比例 [0, 1], 数据不足返回 None
        """
        if not isinstance(kline, list) or len(kline) < period:
            return None
        closes = []
        for k in kline[-period:]:
            if not isinstance(k, dict):
                continue
            c = RiskAgent._safe_float(k.get("close"))
            if c > 0:
                closes.append(c)
        if len(closes) < 5:
            return None

        max_dd = 0.0
        peak = closes[0]
        for c in closes:
            if c > peak:
                peak = c
            dd = (peak - c) / peak
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @staticmethod
    def _calc_volatility(kline: list[dict], period: int):
        """计算 period 日对数收益的年化波动率

        Returns:
            float or None: 年化波动率 [0, +inf), 数据不足返回 None
        """
        if not isinstance(kline, list) or len(kline) < period + 1:
            return None
        closes = []
        for k in kline[-(period + 1) :]:
            if not isinstance(k, dict):
                continue
            c = RiskAgent._safe_float(k.get("close"))
            if c > 0:
                closes.append(c)
        if len(closes) < 10:
            return None

        # 对数收益
        log_rets = []
        for i in range(1, len(closes)):
            if closes[i - 1] > 0:
                log_rets.append(math.log(closes[i] / closes[i - 1]))

        if len(log_rets) < 5:
            return None

        mean = sum(log_rets) / len(log_rets)
        var = sum((r - mean) ** 2 for r in log_rets) / max(1, len(log_rets) - 1)
        daily_vol = math.sqrt(var)
        # 年化 (A股 252 个交易日)
        annual_vol = daily_vol * math.sqrt(252)
        return annual_vol

    @staticmethod
    def _calc_avg_amount(kline: list[dict], period: int):
        """计算 period 日平均成交额"""
        if not isinstance(kline, list) or len(kline) < period:
            return None
        amounts = []
        for k in kline[-period:]:
            if not isinstance(k, dict):
                continue
            amt = RiskAgent._safe_float(k.get("amount"))
            if amt > 0:
                amounts.append(amt)
        if not amounts:
            return None
        return sum(amounts) / len(amounts)
