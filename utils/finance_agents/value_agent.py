"""
ValueAgent — 估值分析 Agent (DCF / PE / PB)
=============================================

借鉴 awesome-llm-apps/ai_hedge_fund 的 valuation_agent 模式.

决策逻辑:
  1. PE 分位数: 当前 PE 处于历史 5 年分位
     - 分位 < 20%  → 看多 (strength += 0.4)
     - 分位 > 80%  → 看空 (strength -= 0.4)
  2. PB 分位数: 同 PE 逻辑 (weight 0.3)
  3. ROE 检查: ROE > 15% 视为优质 (strength += 0.2)
  4. DCF 折价率: (可选) 若有 DCF 内在价, 计算折价率
     - 折价 > 20%  → 看多 (strength += 0.3)
     - 溢价 > 20%  → 看空 (strength -= 0.3)

降级策略:
  - 数据缺失 → action=hold, confidence=0.0
  - 部分指标缺失 → 用可用指标计算, 降低 confidence

集成日期: 2026-07-26
"""

from __future__ import annotations

from typing import Any

from utils.finance_agents.base_agent import AgentDecision, BaseAgent


class ValueAgent(BaseAgent):
    """估值分析 Agent"""

    def __init__(self, name: str = "value"):
        super().__init__(name=name)

    def is_available(self, context: dict[str, Any]) -> bool:
        """需要 fundamentals 数据才可用"""
        fund = self._safe_get(context, "fundamentals")
        return isinstance(fund, dict) and len(fund) > 0

    def analyze(self, symbol: str, context: dict[str, Any]) -> AgentDecision:
        """估值分析主入口"""
        fund = self._safe_get(context, "fundamentals", default={}) or {}
        if not isinstance(fund, dict) or not fund:
            return AgentDecision(
                agent_name=self.name,
                symbol=symbol,
                action="hold",
                confidence=0.0,
                reasoning="估值数据缺失, 无法分析",
            )

        pe = self._safe_float(self._safe_get_fallback(fund, "pe", "pe_ttm"))
        pb = self._safe_float(self._safe_get_fallback(fund, "pb"))
        roe = self._safe_float(self._safe_get_fallback(fund, "roe", "roe_ttm"))
        pe_percentile = self._safe_float(self._safe_get_fallback(fund, "pe_percentile"))
        pb_percentile = self._safe_float(self._safe_get_fallback(fund, "pb_percentile"))
        dcf_intrinsic = self._safe_float(self._safe_get_fallback(fund, "dcf_intrinsic_value"))
        current_price = self._safe_float(self._safe_get(context, "market_data", "close"))

        strength = 0.0
        signals = []
        metrics: dict[str, Any] = {
            "pe": pe,
            "pb": pb,
            "roe": roe,
            "pe_percentile": pe_percentile,
            "pb_percentile": pb_percentile,
        }

        # 1. PE 分位数
        if pe_percentile > 0:
            if pe_percentile < 0.20:
                strength += 0.4
                signals.append(f"PE 分位 {pe_percentile:.0%} 处历史低位 (估值便宜)")
            elif pe_percentile > 0.80:
                strength -= 0.4
                signals.append(f"PE 分位 {pe_percentile:.0%} 处历史高位 (估值偏贵)")

        # 2. PB 分位数
        if pb_percentile > 0:
            if pb_percentile < 0.20:
                strength += 0.3
                signals.append(f"PB 分位 {pb_percentile:.0%} 处历史低位")
            elif pb_percentile > 0.80:
                strength -= 0.3
                signals.append(f"PB 分位 {pb_percentile:.0%} 处历史高位")

        # 3. ROE 检查
        if roe > 0:
            if roe > 0.20:
                strength += 0.2
                signals.append(f"ROE {roe:.1%} 优秀 (>20%)")
            elif roe > 0.15:
                strength += 0.1
                signals.append(f"ROE {roe:.1%} 良好 (>15%)")
            elif roe < 0.05:
                strength -= 0.1
                signals.append(f"ROE {roe:.1%} 偏低 (<5%)")

        # 4. DCF 折价率
        if dcf_intrinsic > 0 and current_price > 0:
            discount_ratio = (dcf_intrinsic - current_price) / current_price
            metrics["dcf_discount_ratio"] = round(discount_ratio, 4)
            if discount_ratio > 0.20:
                strength += 0.3
                signals.append(f"DCF 折价 {discount_ratio:.1%} (显著低估)")
            elif discount_ratio < -0.20:
                strength -= 0.3
                signals.append(f"DCF 溢价 {abs(discount_ratio):.1%} (显著高估)")

        # 决策动作
        if strength > 0.3:
            action = "buy"
        elif strength < -0.3:
            action = "sell"
        else:
            action = "hold"

        # 置信度: 信号数越多越高
        confidence = min(0.9, 0.3 + 0.15 * len(signals))

        return AgentDecision(
            agent_name=self.name,
            symbol=symbol,
            action=action,
            strength=max(-1.0, min(1.0, strength)),
            confidence=confidence,
            reasoning="; ".join(signals) if signals else "估值指标中性",
            key_metrics=metrics,
        )
