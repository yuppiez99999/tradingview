# -*- coding: utf-8 -*-
"""
MacroAgent — 宏观分析 Agent (利率 / 周期 / 资金流)
==================================================

借鉴 awesome-llm-apps/ai_hedge_fund 的 institutional_agent 模式.

决策逻辑:
  1. 利率环境: shibor_1y / 国债收益率
     - 10Y 国债收益率 > 3.5% → 看空 (资金紧)
     - 10Y 国债收益率 < 2.5% → 看多 (资金松)
  2. 北向资金: 当日净流入
     - 净流入 > 50 亿 → 看多 (strength += 0.3)
     - 净流出 > 50 亿 → 看空 (strength -= 0.3)
  3. 行业景气: 行业 PE 分位 / 营收增速
     - 行业景气度 > 0.7 → 看多 (strength += 0.2)
  4. 市场情绪: 上证综指 20 日涨跌幅
     - > +5% → 看多 (strength += 0.2)
     - < -5% → 看空 (strength -= 0.2)

注意:
  - 宏观信号对所有标的相同 (基于宏观环境的整体判断)
  - confidence 较低 (0.3-0.5), 因为宏观因素传导有时滞

集成日期: 2026-07-26
"""

from __future__ import annotations

from typing import Any, Dict

from utils.finance_agents.base_agent import BaseAgent, AgentDecision


class MacroAgent(BaseAgent):
    """宏观分析 Agent"""

    def __init__(self, name: str = "macro"):
        super().__init__(name=name)

    def is_available(self, context: Dict[str, Any]) -> bool:
        """需要 macro_data"""
        macro = self._safe_get(context, "macro_data")
        return isinstance(macro, dict) and len(macro) > 0

    def analyze(self, symbol: str, context: Dict[str, Any]) -> AgentDecision:
        """宏观分析主入口"""
        macro = self._safe_get(context, "macro_data", default={}) or {}
        if not isinstance(macro, dict) or not macro:
            return AgentDecision(
                agent_name=self.name,
                symbol=symbol,
                action="hold",
                confidence=0.0,
                reasoning="宏观数据缺失, 无法分析",
            )

        strength = 0.0
        signals = []
        metrics: Dict[str, Any] = {}

        # 1. 10Y 国债收益率
        bond_10y = self._safe_float(macro.get("bond_10y_yield"))
        if bond_10y > 0:
            metrics["bond_10y_yield"] = bond_10y
            if bond_10y > 0.035:
                strength -= 0.2
                signals.append(f"10Y 国债收益率 {bond_10y:.2%} 偏高 (资金紧)")
            elif bond_10y < 0.025:
                strength += 0.2
                signals.append(f"10Y 国债收益率 {bond_10y:.2%} 偏低 (资金松)")

        # 2. 北向资金 (A股特有)
        north_flow = self._safe_float(macro.get("north_flow"))
        if north_flow != 0:
            metrics["north_flow"] = round(north_flow, 0)
            if north_flow > 5e9:  # 50 亿
                strength += 0.3
                signals.append(f"北向净流入 {north_flow/1e8:.1f} 亿 (强势)")
            elif north_flow < -5e9:
                strength -= 0.3
                signals.append(f"北向净流出 {abs(north_flow)/1e8:.1f} 亿 (弱势)")

        # 3. 行业景气度 (0-1)
        industry_score = self._safe_float(macro.get("industry_score"))
        if industry_score > 0:
            metrics["industry_score"] = industry_score
            if industry_score > 0.7:
                strength += 0.2
                signals.append(f"行业景气度 {industry_score:.2f} 高")
            elif industry_score < 0.3:
                strength -= 0.2
                signals.append(f"行业景气度 {industry_score:.2f} 低")

        # 4. 大盘 20 日涨跌
        index_return_20d = self._safe_float(macro.get("index_return_20d"))
        if index_return_20d != 0:
            metrics["index_return_20d"] = round(index_return_20d, 4)
            if index_return_20d > 0.05:
                strength += 0.2
                signals.append(f"大盘 20 日涨 {index_return_20d:.1%}")
            elif index_return_20d < -0.05:
                strength -= 0.2
                signals.append(f"大盘 20 日跌 {abs(index_return_20d):.1%}")

        # 决策动作
        if strength > 0.3:
            action = "buy"
        elif strength < -0.3:
            action = "sell"
        else:
            action = "hold"

        # 宏观信号 confidence 较低 (传导有时滞)
        confidence = min(0.6, 0.2 + 0.1 * len(signals))

        return AgentDecision(
            agent_name=self.name,
            symbol=symbol,
            action=action,
            strength=max(-1.0, min(1.0, strength)),
            confidence=confidence,
            reasoning="; ".join(signals) if signals else "宏观指标中性",
            key_metrics=metrics,
        )
