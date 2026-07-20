# -*- coding: utf-8 -*-
"""
动态信号融合引擎 (Signal Fusion Engine)
========================================

把多源信号融合为可执行的交易信号：
- Alpha 因子信号
- LLM 辅助信号
- ETF 资金流信号
- 宏观/周期信号

输出：
- symbol -> signal_strength ∈ [-1, 1]
- symbol -> confidence ∈ [0, 1]
- 每标的风险调整后权重建议

用法:
    from utils.signal_fusion import SignalFusionEngine, FusionSignal
    engine = SignalFusionEngine()
    signals = engine.fuse(alpha_signals, llm_signals, etf_signals, macro_signals)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger("signal_fusion")


@dataclass
class FusionSignal:
    """融合后的单标信号"""
    symbol: str
    strength: float = 0.0
    confidence: float = 0.0
    sources: Dict[str, float] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "strength": round(self.strength, 4),
            "confidence": round(self.confidence, 4),
            "sources": self.sources,
            "meta": self.meta,
        }


class SignalFusionEngine:
    """动态信号融合引擎"""

    def __init__(
        self,
        alpha_weight: float = 0.70,
        llm_weight: float = 0.10,
        etf_weight: float = 0.12,
        macro_weight: float = 0.08,
        min_confidence: float = 0.35,
    ):
        # 默认权重：Alpha 为主，LLM/ETF/宏观为辅助
        self.alpha_weight = alpha_weight
        self.llm_weight = llm_weight
        self.etf_weight = etf_weight
        self.macro_weight = macro_weight
        self.min_confidence = min_confidence

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------

    def fuse(
        self,
        alpha_signals: Optional[Dict[str, Dict[str, Any]]] = None,
        llm_signals: Optional[Dict[str, Dict[str, Any]]] = None,
        etf_signals: Optional[Dict[str, Dict[str, Any]]] = None,
        macro_signals: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[FusionSignal]:
        """融合多源信号

        Args:
            alpha_signals: {symbol: {"strength": float, "confidence": float}}
            llm_signals: {symbol: {"strength": float, "confidence": float}}
            etf_signals: {symbol: {"strength": float, "confidence": float}}
            macro_signals: {symbol: {"strength": float, "confidence": float}}

        Returns:
            List[FusionSignal]
        """
        alpha_signals = alpha_signals or {}
        llm_signals = llm_signals or {}
        etf_signals = etf_signals or {}
        macro_signals = macro_signals or {}

        all_symbols = set(alpha_signals) | set(llm_signals) | set(etf_signals) | set(macro_signals)
        if not all_symbols:
            return []

        macro_bias = self._summarize_macro(macro_signals)

        results: List[FusionSignal] = []
        for symbol in all_symbols:
            signal = self._fuse_symbol(
                symbol,
                alpha_signals.get(symbol),
                llm_signals.get(symbol),
                etf_signals.get(symbol),
                macro_bias,
            )
            results.append(signal)

        results.sort(key=lambda s: abs(s.strength) * s.confidence, reverse=True)
        return results

    # ------------------------------------------------------------
    # 单标的融合
    # ------------------------------------------------------------

    def _fuse_symbol(
        self,
        symbol: str,
        alpha: Optional[Dict[str, Any]],
        llm: Optional[Dict[str, Any]],
        etf: Optional[Dict[str, Any]],
        macro_bias: float,
    ) -> FusionSignal:
        alpha_s = self._safe(alpha, "strength")
        alpha_c = self._safe(alpha, "confidence")
        llm_s = self._safe(llm, "strength")
        llm_c = self._safe(llm, "confidence")
        etf_s = self._safe(etf, "strength")
        etf_c = self._safe(etf, "confidence")

        weights = self._dynamic_weights(alpha_c, llm_c, etf_c)

        strength = (
            weights["alpha"] * alpha_s
            + weights["llm"] * llm_s
            + weights["etf"] * etf_s
            + weights["macro"] * macro_bias
        )
        strength = max(-1.0, min(1.0, strength))

        raw_confidence = (
            weights["alpha"] * alpha_c
            + weights["llm"] * llm_c
            + weights["etf"] * etf_c
            + 0.05
        )
        confidence = max(0.0, min(1.0, raw_confidence))

        if abs(strength) < 0.10 or confidence < self.min_confidence:
            strength = 0.0
            confidence = 0.0
        else:
            # 对非零信号做指数响应，放大强弱差异，让月度权重更容易随信号变化
            strength = float(np.sign(strength) * (abs(strength) ** 0.85))
            strength = max(-1.0, min(1.0, strength))

        return FusionSignal(
            symbol=symbol,
            strength=round(float(strength), 4),
            confidence=round(float(confidence), 4),
            sources={
                "alpha_strength": alpha_s,
                "llm_strength": llm_s,
                "etf_strength": etf_s,
                "macro_bias": macro_bias,
            },
            meta={
                "weights": weights,
                "alpha_confidence": alpha_c,
                "llm_confidence": llm_c,
                "etf_confidence": etf_c,
            },
        )

    # ------------------------------------------------------------
    # 权重与宏观
    # ------------------------------------------------------------

    def _dynamic_weights(self, alpha_c: float, llm_c: float, etf_c: float) -> Dict[str, float]:
        """根据置信度动态调整权重"""
        if alpha_c < 0.25:
            alpha_w = 0.45
            llm_w = 0.25
            etf_w = 0.20
        else:
            alpha_w = self.alpha_weight
            llm_w = self.llm_weight
            etf_w = self.etf_weight

        macro_w = self.macro_weight
        total = alpha_w + llm_w + etf_w + macro_w
        return {
            "alpha": alpha_w / total,
            "llm": llm_w / total,
            "etf": etf_w / total,
            "macro": macro_w / total,
        }

    def _summarize_macro(self, macro_signals: Optional[Dict[str, Dict[str, Any]]]) -> float:
        if not macro_signals:
            return 0.0
        vals = [self._safe(v, "strength") for v in macro_signals.values()]
        if not vals:
            return 0.0
        arr = np.array(vals, dtype=float)
        return float(np.mean(arr)) if arr.size else 0.0

    # ------------------------------------------------------------
    # 工具函数
    # ------------------------------------------------------------

    def _safe(self, src: Optional[Dict[str, Any]], key: str) -> float:
        if not isinstance(src, dict):
            return 0.0
        value = src.get(key, 0.0)
        try:
            v = float(value)
            return v if math.isfinite(v) else 0.0
        except Exception:
            return 0.0

    # ------------------------------------------------------------
    # 过滤
    # ------------------------------------------------------------

    def filter_tradable(self, signals: List[FusionSignal]) -> List[FusionSignal]:
        return [s for s in signals if s.strength != 0.0 and s.confidence >= self.min_confidence]

    def top(self, signals: List[FusionSignal], k: int = 10) -> List[FusionSignal]:
        ranked = sorted(signals, key=lambda s: abs(s.strength) * s.confidence, reverse=True)
        return ranked[:k]
