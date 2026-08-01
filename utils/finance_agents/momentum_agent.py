"""
MomentumAgent — 动量分析 Agent (突破 / 回调 / 趋势)
====================================================

借鉴 awesome-llm-apps/ai_hedge_fund 的 momentum_agent 模式.

决策逻辑:
  1. 均线突破: 价格突破 MA20 / MA60
     - 上穿 MA20 + 0.2, 上穿 MA60 + 0.3
     - 下穿 MA20 - 0.2, 下穿 MA60 - 0.3
  2. 短期动量: 5 日 / 20 日收益率
     - 5日收益 > 5%   → 看多 (strength += 0.2)
     - 5日收益 < -5%  → 看空 (strength -= 0.2)
  3. RSI 超买超卖:
     - RSI < 30 → 看多 (反转, strength += 0.2)
     - RSI > 70 → 看空 (反转, strength -= 0.2)
  4. 量价配合: 突破时成交量放大 (> 1.5x 均量)
     - 量价齐升 → strength += 0.15

降级策略:
  - K 线数据缺失 → hold, confidence=0
  - 部分指标缺失 → 用可用指标, 降低 confidence

集成日期: 2026-07-26
"""

from __future__ import annotations

from typing import Any

from utils.finance_agents.base_agent import AgentDecision, BaseAgent


class MomentumAgent(BaseAgent):
    """动量分析 Agent"""

    def __init__(self, name: str = "momentum"):
        super().__init__(name=name)

    def is_available(self, context: dict[str, Any]) -> bool:
        """需要 kline 数据"""
        kline = self._safe_get(context, "kline")
        return isinstance(kline, list) and len(kline) >= 20

    def analyze(self, symbol: str, context: dict[str, Any]) -> AgentDecision:
        """动量分析主入口"""
        kline: list[dict] = self._safe_get(context, "kline", default=[]) or []
        if not isinstance(kline, list) or len(kline) < 20:
            return AgentDecision(
                agent_name=self.name,
                symbol=symbol,
                action="hold",
                confidence=0.0,
                reasoning="K 线数据不足 (需 >=20 日), 无法分析动量",
            )

        # 提取收盘价序列
        closes = [self._safe_float(k.get("close")) for k in kline if isinstance(k, dict)]
        if len(closes) < 20:
            return AgentDecision(
                agent_name=self.name,
                symbol=symbol,
                action="hold",
                confidence=0.0,
                reasoning="收盘价数据不足",
            )

        current_close = closes[-1]
        ma20 = self._sma(closes, 20)
        ma60 = self._sma(closes, 60) if len(closes) >= 60 else None
        rsi = self._rsi(closes, 14)

        strength = 0.0
        signals = []
        metrics: dict[str, Any] = {
            "close": current_close,
            "ma20": round(ma20, 4) if ma20 else None,
            "ma60": round(ma60, 4) if ma60 else None,
            "rsi14": round(rsi, 2) if rsi else None,
        }

        # 1. 均线突破
        if ma20:
            if current_close > ma20:
                strength += 0.2
                signals.append(f"价格 {current_close:.2f} 上穿 MA20 {ma20:.2f}")
            else:
                strength -= 0.2
                signals.append(f"价格 {current_close:.2f} 下穿 MA20 {ma20:.2f}")
        if ma60:
            if current_close > ma60:
                strength += 0.3
                signals.append(f"价格 {current_close:.2f} 上穿 MA60 {ma60:.2f}")
            else:
                strength -= 0.3
                signals.append(f"价格 {current_close:.2f} 下穿 MA60 {ma60:.2f}")

        # 2. 短期动量 (5 日 / 20 日)
        if len(closes) >= 5:
            ret_5d = (closes[-1] - closes[-5]) / closes[-5] if closes[-5] > 0 else 0.0
            metrics["return_5d"] = round(ret_5d, 4)
            if ret_5d > 0.05:
                strength += 0.2
                signals.append(f"5 日涨幅 {ret_5d:.1%} (强势)")
            elif ret_5d < -0.05:
                strength -= 0.2
                signals.append(f"5 日跌幅 {ret_5d:.1%} (弱势)")
        if len(closes) >= 20:
            ret_20d = (closes[-1] - closes[-20]) / closes[-20] if closes[-20] > 0 else 0.0
            metrics["return_20d"] = round(ret_20d, 4)

        # 3. RSI 超买超卖 (反转信号)
        if rsi is not None:
            if rsi < 30:
                strength += 0.2
                signals.append(f"RSI {rsi:.1f} 超卖 (反弹预期)")
            elif rsi > 70:
                strength -= 0.2
                signals.append(f"RSI {rsi:.1f} 超买 (回调预期)")

        # 4. 量价配合 (突破时量放大)
        volumes = [self._safe_float(k.get("volume")) for k in kline if isinstance(k, dict)]
        if len(volumes) >= 20 and volumes[-1] > 0:
            avg_vol_20 = sum(volumes[-20:]) / 20.0
            if avg_vol_20 > 0:
                vol_ratio = volumes[-1] / avg_vol_20
                metrics["vol_ratio_20d"] = round(vol_ratio, 2)
                if vol_ratio > 1.5 and strength > 0:
                    strength += 0.15
                    signals.append(f"量价齐升 (量比 {vol_ratio:.2f}x)")

        # 决策动作
        if strength > 0.3:
            action = "buy"
        elif strength < -0.3:
            action = "sell"
        else:
            action = "hold"

        confidence = min(0.9, 0.3 + 0.1 * len(signals))

        return AgentDecision(
            agent_name=self.name,
            symbol=symbol,
            action=action,
            strength=max(-1.0, min(1.0, strength)),
            confidence=confidence,
            reasoning="; ".join(signals) if signals else "动量指标中性",
            key_metrics=metrics,
        )

    # ----------------------------------------------------------
    # 技术指标计算
    # ----------------------------------------------------------

    @staticmethod
    def _sma(closes: list[float], period: int) -> float:
        """简单移动平均"""
        if len(closes) < period:
            return 0.0
        return sum(closes[-period:]) / period

    @staticmethod
    def _rsi(closes: list[float], period: int = 14):
        """RSI 相对强弱指标

        Returns:
            float or None: RSI 值 [0, 100], 数据不足返回 None
        """
        if period <= 0 or len(closes) < period + 1:
            return None
        gains = []
        losses = []
        for i in range(-period, 0):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains.append(diff)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(diff))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi
