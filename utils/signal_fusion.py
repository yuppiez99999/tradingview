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
        # 动态 IC 权重支持：注入 forward_returns 后按各源 IC 动态加权
        self._forward_returns: Optional[Dict[str, float]] = None
        self._ic_weights: Optional[Dict[str, float]] = None
        # 缓存最近一次 fuse 的各源信号值，用于 IC 计算
        self._last_signals_by_source: Optional[Dict[str, Dict[str, float]]] = None
        # Qlib 信号缓存（由 inject_qlib_signal 注入，可作为 alpha 源）
        self._qlib_signals: Dict[str, float] = {}

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

        # 缓存各源信号强度，供 inject_forward_returns 计算 IC 使用
        self._last_signals_by_source = {
            "alpha": {s: self._safe(alpha_signals.get(s), "strength") for s in all_symbols},
            "llm": {s: self._safe(llm_signals.get(s), "strength") for s in all_symbols},
            "etf": {s: self._safe(etf_signals.get(s), "strength") for s in all_symbols},
        }

        # 若已注入 forward_returns，自动计算 IC 权重
        if self._forward_returns is not None and self._ic_weights is None:
            self._compute_ic_weights()

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
    # 动态 IC 权重注入
    # ------------------------------------------------------------

    def inject_qlib_signal(self, signal_series: Any) -> None:
        """注入 Qlib 深度学习信号（兼容 daily_workflow 旧调用）

        将 Qlib 信号序列存储为 alpha 信号源，供后续 fuse() 调用使用。
        若已存在 alpha 信号，按 50/50 融合。

        Args:
            signal_series: pd.Series(index=symbol, values=signal) 或 dict
        """
        try:
            if hasattr(signal_series, "to_dict"):
                qlib_dict = signal_series.to_dict()
            elif isinstance(signal_series, dict):
                qlib_dict = signal_series
            else:
                logger.warning("inject_qlib_signal: 不支持的类型 %s", type(signal_series))
                return
            self._qlib_signals = {str(k): float(v) for k, v in qlib_dict.items()
                                  if isinstance(v, (int, float)) and math.isfinite(float(v))}
            logger.info("已注入 Qlib 信号: %d 个标的", len(self._qlib_signals))
        except Exception as e:
            logger.warning("inject_qlib_signal 异常: %s", e)

    def inject_forward_returns(self, forward_returns: Dict[str, float]) -> None:
        """注入前向收益，激活动态 IC 加权

        注入后，下次 fuse() 调用会自动计算各信号源(alpha/llm/etf)与
        forward_returns 的 Spearman IC，并按 |IC| 归一化作为动态权重。
        IC 数据不足或为零时回退到默认置信度权重（fail-safe）。

        Args:
            forward_returns: {symbol: forward_return} 各标的的前向收益
        """
        if not isinstance(forward_returns, dict) or not forward_returns:
            logger.warning("inject_forward_returns: 输入为空，跳过")
            return
        self._forward_returns = {str(k): float(v) for k, v in forward_returns.items()
                                 if isinstance(v, (int, float)) and math.isfinite(float(v))}
        # 重置 IC 权重，等待下次 fuse() 或显式 _compute_ic_weights 计算
        self._ic_weights = None
        logger.info("已注入 forward_returns: %d 个标的，将激活动态 IC 权重", len(self._forward_returns))

    def _compute_ic_weights(self) -> None:
        """根据各源信号与 forward_returns 计算 IC 权重

        对每个信号源，计算其信号强度与 forward_returns 的 Spearman 秩相关(IC)。
        权重 = |IC| 归一化；IC 不足时回退默认权重。
        """
        if self._forward_returns is None or self._last_signals_by_source is None:
            return

        ic_by_source: Dict[str, float] = {}
        for source, sig_map in self._last_signals_by_source.items():
            # 配对 (signal, forward_return)
            pairs = []
            for sym, sig_val in sig_map.items():
                fr = self._forward_returns.get(sym)
                if fr is not None and sig_val != 0.0:
                    pairs.append((sig_val, fr))
            if len(pairs) < 5:
                # 样本不足，IC 视为 0（回退默认权重）
                ic_by_source[source] = 0.0
                continue
            try:
                import pandas as _pd
                sig_series = _pd.Series([p[0] for p in pairs])
                fr_series = _pd.Series([p[1] for p in pairs])
                ic = float(sig_series.corr(fr_series, method="spearman"))
                if not math.isfinite(ic):
                    ic = 0.0
            except Exception:
                ic = 0.0
            ic_by_source[source] = ic

        # 按 |IC| 归一化为权重；全部为 0 时回退默认权重
        abs_ic = {s: abs(v) for s, v in ic_by_source.items()}
        total_abs = sum(abs_ic.values())
        defaults = {"alpha": self.alpha_weight, "llm": self.llm_weight, "etf": self.etf_weight}
        default_total = sum(defaults.values())

        if total_abs < 1e-6:
            # IC 全为零：回退默认权重（归一化）
            self._ic_weights = {s: defaults[s] / default_total for s in defaults}
            logger.info("IC 权重: 各源 IC≈0，回退默认权重 %s", self._ic_weights)
        else:
            # 混合: 70% IC 权重 + 30% 默认权重（避免极端单一源主导）
            ic_norm = {s: abs_ic[s] / total_abs for s in abs_ic}
            default_norm = {s: defaults[s] / default_total for s in defaults}
            self._ic_weights = {s: 0.7 * ic_norm.get(s, 0.0) + 0.3 * default_norm.get(s, 0.0)
                                for s in defaults}
            logger.info("IC 权重已计算: IC=%s -> 权重=%s",
                        {s: round(v, 4) for s, v in ic_by_source.items()},
                        {s: round(v, 4) for s, v in self._ic_weights.items()})

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
        """根据置信度（或 IC 权重）动态调整权重

        优先级：
        1. 若已注入 forward_returns 并计算出 IC 权重，使用 IC 权重（动态 IC 加权）
        2. 否则回退到置信度阈值权重
        """
        # 优先使用 IC 动态权重（已注入 forward_returns 时激活）
        if self._ic_weights is not None:
            ic = self._ic_weights
            macro_w = self.macro_weight
            total = ic["alpha"] + ic["llm"] + ic["etf"] + macro_w
            return {
                "alpha": ic["alpha"] / total,
                "llm": ic["llm"] / total,
                "etf": ic["etf"] / total,
                "macro": macro_w / total,
            }

        # 回退：置信度阈值权重
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
