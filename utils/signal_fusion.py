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
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger("signal_fusion")


# ============================================================
# P1-Q5 修复 (2026-07-26): PostMixLayer 抽象 — 统一信号叠加层
# 原始问题: _fuse_symbol 189 行, 4 层 post-mix 叠加 (pipeline/research/lgb)
#   每层重复 "4 层 NaN 防御" 是 defensive programming 反模式
# 修复方案: 提取为 PostMixLayer 类, 单一职责 + 单元可测
# 顶级对冲基金标准: 信号叠加层必须可独立测试 + 可热插拔
# ============================================================
@dataclass
class PostMixLayer:
    """信号 post-mix 叠加层 (P1-Q5 抽象)

    职责:
        在主融合 (alpha+llm+etf+macro) 完成后, 叠加一个外部信号源
        公式: new_strength = strength * (1 - weight) + signal * weight

    特性:
        - NaN/Inf 防御集中在 _sanitize_signals, 不再每层重复
        - weight ≤ 0 时自动跳过 (允许通过 config 一键关闭某层)
        - 支持质量降权 (LOW_QUALITY 标的权重降至 50% 等)

    Attributes:
        name: 层名 (用于审计/日志)
        weight: 默认叠加权重 (0-1)
        signals: 该层信号缓存 {symbol: signal ∈ [-1, 1]}
        quality_flags: 标的质量标记 {symbol: "OK"|"LOW_QUALITY"}
        quality_decay: LOW_QUALITY 标的权重衰减系数 (默认 0.5)
        enabled: 是否启用 (production 隔离时设为 False)
    """

    name: str
    weight: float
    signals: Dict[str, float] = field(default_factory=dict)
    quality_flags: Dict[str, str] = field(default_factory=dict)
    quality_decay: float = 0.5
    enabled: bool = True

    def update_signals(
        self,
        signals: Dict[str, Any],
        quality_flags: Optional[Dict[str, str]] = None,
    ) -> None:
        """更新信号缓存 (统一 NaN/Inf 过滤)

        Args:
            signals: {symbol: signal} 或 {symbol: {"signal": float, ...}}
            quality_flags: 可选, 标的质量标记 (覆盖结构化解析)
        """
        if not isinstance(signals, dict) or not signals:
            self.signals = {}
            logger.warning(f"[PostMixLayer:{self.name}] 输入为空, 信号缓存已清空")
            return

        parsed_signals: Dict[str, float] = {}
        parsed_flags: Dict[str, str] = {}

        for sym, val in signals.items():
            # 结构化格式: {"signal": float, "quality_flag": str, ...}
            if isinstance(val, dict):
                sig = val.get("signal")
                flag = val.get("quality_flag", "OK")
                if isinstance(sig, (int, float)) and math.isfinite(float(sig)):
                    parsed_signals[str(sym)] = float(sig)
                    parsed_flags[str(sym)] = str(flag) if flag in ("OK", "LOW_QUALITY") else "OK"
            # 扁平格式: {symbol: signal}
            elif isinstance(val, (int, float)):
                if math.isfinite(float(val)):
                    parsed_signals[str(sym)] = float(val)
                    parsed_flags[str(sym)] = "OK"
            # 其他类型跳过

        # 显式 quality_flags 覆盖
        if quality_flags and isinstance(quality_flags, dict):
            for sym, flag in quality_flags.items():
                if str(sym) in parsed_signals and flag in ("OK", "LOW_QUALITY"):
                    parsed_flags[str(sym)] = str(flag)

        self.signals = parsed_signals
        self.quality_flags = parsed_flags

        ok_count = sum(1 for f in parsed_flags.values() if f == "OK")
        low_q_count = sum(1 for f in parsed_flags.values() if f == "LOW_QUALITY")
        logger.info(
            f"[PostMixLayer:{self.name}] 已注入 {len(parsed_signals)} 标的 "
            f"(weight={self.weight:.2f}, enabled={self.enabled}, OK={ok_count}, LOW_QUALITY={low_q_count})"
        )

    def get_signal(self, symbol: str) -> float:
        """获取单标的信号值 (含 NaN 防御)"""
        sig = self.signals.get(symbol, 0.0)
        if not math.isfinite(sig):
            logger.warning(f"[PostMixLayer:{self.name}] {symbol} 信号为 NaN/Inf, 归零")
            return 0.0
        return sig

    def get_effective_weight(self, symbol: str) -> float:
        """获取单标的的有效权重 (含质量降权 + enabled 开关)"""
        if not self.enabled or self.weight <= 0:
            return 0.0
        flag = self.quality_flags.get(symbol, "OK")
        if flag == "LOW_QUALITY":
            return self.weight * self.quality_decay
        return self.weight

    def apply(self, strength: float, symbol: str) -> tuple:
        """叠加该层信号到当前 strength

        Args:
            strength: 当前 strength (上游已归一化到 [-1, 1])
            symbol: 标的代码

        Returns:
            (new_strength, applied: bool) — applied=True 表示实际执行了叠加
        """
        if not self.enabled or self.weight <= 0:
            return strength, False

        sig = self.get_signal(symbol)
        if sig == 0.0:
            return strength, False

        effective_weight = self.get_effective_weight(symbol)
        if effective_weight <= 0:
            return strength, False

        # post-mix 公式: new = strength * (1 - w) + signal * w
        new_strength = strength * (1.0 - effective_weight) + sig * effective_weight

        # NaN 防御 (理论上不会触发, 但保留兜底)
        if not math.isfinite(new_strength):
            logger.warning(
                f"[PostMixLayer:{self.name}] {symbol} 叠加后 NaN (strength={strength}, "
                f"sig={sig}, w={effective_weight}), 归零"
            )
            new_strength = 0.0

        # 边界裁剪
        new_strength = max(-1.0, min(1.0, new_strength))
        return new_strength, True


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
        # v8.6.4 P0-A 深度修复：Pipeline 因子组合信号（保守 5%）
        pipeline_factor_weight: float = 0.05,
        # v8.6.9 新增: 研究蒸馏信号（第 6 信号源, 保守 3%）
        # 设计依据: RIA--TV++ 量化版, 离线蒸馏(06:00) + 在线注入(07:00)
        # 安全设计: post-mix 模式 + 仅影响影子账户 + 4 层 NaN 防御
        research_distilled_weight: float = 0.03,
        # v8.7 新增: LGB 增强信号（第 7 信号源, 保守 4%）
        # 设计依据: lgb_enhanced_trainer v8.7 GPU 训练, 平均 IC=0.1631, 23 标的全覆盖
        # 数据来源: models/lgb_enhanced/lgb_enhanced_signals.json (真实OHLCV+情绪因子)
        # 安全设计: post-mix 模式 + NaN 防御 + 低质量标的 quality_flag=LOW_QUALITY 降权
        lgb_enhanced_weight: float = 0.04,
        # v8.4.1 新增: 外部策略信号（第 8 信号源, 保守 3%）
        # 设计依据: daily_stock_analysis 15种A股策略(缠论/龙头/情绪周期), LLM执行
        # 安全设计: post-mix 模式 + LLM不可用时自动降级为中性信号
        external_strategy_weight: float = 0.03,
        # v8.6.13 新增: 气象因子信号（第 9 信号源, 保守 4%）
        # 设计依据: weather_factor_engine 7因子体系(温度/降水/风速/辐照/气压/AQI/能见度)
        # 覆盖能源/矿业/农业/制造/医药/大宗商品板块
        # 安全设计: post-mix 模式 + apizero 不可用时自动降级为中性信号
        weather_signal_weight: float = 0.04,
    ):
        # 默认权重：Alpha 为主，LLM/ETF/宏观为辅助
        self.alpha_weight = alpha_weight
        self.llm_weight = llm_weight
        self.etf_weight = etf_weight
        self.macro_weight = macro_weight
        self.min_confidence = min_confidence
        # v8.6.4: Pipeline 因子组合信号权重（保守起步，影子账户 OOS 验证后可上调）
        # 设计依据: PipelineOrchestrator 实测 IC_IR=+0.5840, live_dsr=+2.2033（v6.9）
        # 安全设计: 5% 权重 + 影子账户 fail-fast 3%/5% 触发器隔离风险
        self.pipeline_factor_weight = pipeline_factor_weight
        # v8.6.9: 研究蒸馏信号权重（保守 3%, post-mix 模式）
        # 设计依据: cangjie-skill RIA--TV++ 方法论, 研报/业绩会/书籍蒸馏为交易信号
        # 安全设计: 不修改主融合公式, post-mix 叠加, 仅影响影子账户
        # v8.6.9 环境隔离: production 实盘模式下强制 weight=0 (纵深防御)
        # 用户要求: 暂不接入实盘, 用模拟盘跑数据
        _research_weight = research_distilled_weight
        _research_enabled = True
        try:
            from utils.trading_env import TradingEnv, get_trading_env

            if get_trading_env() == TradingEnv.PRODUCTION:
                _research_weight = 0.0
                _research_enabled = False
                logger.info(
                    "[SignalFusion] production 实盘模式: research_distilled_weight 强制为 0 "
                    "(v8.6.9 环境隔离: 仅 shadow/development 模式激活)"
                )
        except Exception:  # P2 模块 fail-safe, 待后续精确化
            # trading_env 不可用时保持配置值 (fail-open for 新功能, 不影响主流程)
            pass
        self.research_distilled_weight = _research_weight
        # v8.7: LGB 增强信号权重（保守 4%, post-mix）
        # 设计依据: 23 标的平均 IC=0.1631, IC>0.3 的 6 个, IC>0.2 的 9 个
        # 安全设计: post-mix 模式 + 低质量标的 (LOW_QUALITY) 降权至 50%
        self.lgb_enhanced_weight = lgb_enhanced_weight
        # v8.4.1: 外部策略信号权重（保守 3%, post-mix）
        # 设计依据: daily_stock_analysis 15种A股策略(缠论/龙头/情绪周期), LLM执行
        # 安全设计: post-mix 模式 + LLM不可用时自动降级为中性信号
        self.external_strategy_weight = external_strategy_weight
        # v8.6.13: 气象因子信号权重（保守 4%, post-mix）
        # 设计依据: 7因子体系 + 行业敏感度加权, 覆盖 14 标的 + 期货
        self.weather_signal_weight = weather_signal_weight
        # 动态 IC 权重支持：注入 forward_returns 后按各源 IC 动态加权
        self._forward_returns: Optional[Dict[str, float]] = None
        self._ic_weights: Optional[Dict[str, float]] = None
        # 缓存最近一次 fuse 的各源信号值，用于 IC 计算
        self._last_signals_by_source: Optional[Dict[str, Dict[str, float]]] = None
        # Qlib 信号缓存（由 inject_qlib_signal 注入，可作为 alpha 源）
        self._qlib_signals: Dict[str, float] = {}

        # P1-Q5 修复 (2026-07-26): 用 PostMixLayer 替代散落的信号缓存 + post-mix 逻辑
        # 优势:
        #   1. NaN 防御集中在 PostMixLayer, 不再每层重复
        #   2. 每层可独立单元测试
        #   3. 可通过 layer.enabled = False 一键关闭某层
        #   4. 新增信号源只需新增 PostMixLayer 实例, 不再修改 _fuse_symbol
        self._pipeline_layer = PostMixLayer(
            name="pipeline_factor",
            weight=self.pipeline_factor_weight,
        )
        self._research_layer = PostMixLayer(
            name="research_distilled",
            weight=self.research_distilled_weight,
            enabled=_research_enabled,
        )
        self._lgb_layer = PostMixLayer(
            name="lgb_enhanced",
            weight=self.lgb_enhanced_weight,
            quality_decay=0.5,  # LOW_QUALITY 标的权重降至 50%
        )
        # v8.4.1: 外部策略信号层 (daily_stock_analysis 15种A股策略)
        self._external_strategy_layer = PostMixLayer(
            name="external_strategy",
            weight=self.external_strategy_weight,
        )
        # v8.6.13: 气象因子信号层 (weather_factor_engine 7因子体系)
        self._weather_layer = PostMixLayer(
            name="weather_factor",
            weight=self.weather_signal_weight,
        )

        # 向后兼容: 保留旧字段供外部读取 (不直接用于 _fuse_symbol, 仅用于审计)
        self._pipeline_factor_signals: Dict[str, float] = {}
        self._research_distilled_signals: Dict[str, float] = {}
        self._lgb_enhanced_signals: Dict[str, float] = {}
        self._lgb_quality_flags: Dict[str, str] = {}

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
            self._qlib_signals = {
                str(k): float(v)
                for k, v in qlib_dict.items()
                if isinstance(v, (int, float)) and math.isfinite(float(v))
            }
            logger.info("已注入 Qlib 信号: %d 个标的", len(self._qlib_signals))
        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("inject_qlib_signal 异常: %s", e)

    def inject_pipeline_factor_signals(self, signals: Dict[str, float]) -> None:
        """注入 Pipeline 因子组合信号（v8.6.4 P0-A 深度修复 / P1-Q5 重构）

        P1-Q5 修复 (2026-07-26): 委托给 PostMixLayer.update_signals, 不再重复 NaN 过滤逻辑
        原始问题: 此方法与 inject_research_distilled_signals 和 inject_lgb_enhanced_signals
                  有大量重复的 NaN/Inf 过滤代码, 是 defensive programming 反模式
        修复后: 统一委托 PostMixLayer.update_signals, 单一职责
        """
        try:
            self._pipeline_layer.update_signals(signals)
            # 向后兼容: 同步更新旧字段供外部读取
            self._pipeline_factor_signals = self._pipeline_layer.signals
        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("inject_pipeline_factor_signals 异常: %s", e)

    def inject_research_distilled_signals(self, signals: Dict[str, float]) -> None:
        """注入研究蒸馏信号（v8.6.9 第 6 信号源 / P1-Q5 重构）

        P1-Q5 修复: 委托给 PostMixLayer.update_signals
        """
        try:
            self._research_layer.update_signals(signals)
            # 向后兼容
            self._research_distilled_signals = self._research_layer.signals
        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("inject_research_distilled_signals 异常: %s", e)

    def inject_lgb_enhanced_signals(
        self,
        signals: Dict[str, Any],
        quality_flags: Optional[Dict[str, str]] = None,
    ) -> None:
        """注入 LGB 增强信号（v8.7 第 7 信号源 / P1-Q5 重构）

        P1-Q5 修复: 委托给 PostMixLayer.update_signals
        PostMixLayer 原生支持结构化格式 {"signal": float, "quality_flag": str}
        和扁平格式 {symbol: signal}, 不再需要此方法手动解析
        """
        try:
            self._lgb_layer.update_signals(signals, quality_flags)
            # 向后兼容
            self._lgb_enhanced_signals = self._lgb_layer.signals
            self._lgb_quality_flags = self._lgb_layer.quality_flags
        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("inject_lgb_enhanced_signals 异常: %s", e)

    def inject_external_strategy_signals(self, signals: Dict[str, float]) -> None:
        """注入外部策略信号（v8.4.1 第 8 信号源）

        daily_stock_analysis 15种A股策略(缠论/龙头/情绪周期等)的共识信号.
        信号值范围: -1.0 (强看空) ~ +1.0 (强看空), 0 = 中性.

        Args:
            signals: {symbol: signal_value} 外部策略共识信号
        """
        try:
            self._external_strategy_layer.update_signals(signals)
        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("inject_external_strategy_signals 异常: %s", e)

    def inject_weather_signals(self, signals: Dict[str, float]) -> None:
        """注入气象因子信号（v8.6.13 第 9 信号源）

        weather_factor_engine 7因子体系(温度/降水/风速/辐照/气压/AQI/能见度)
        计算的气象条件对标的影响信号. 信号值范围: -1.0 (气象利空) ~ +1.0 (气象利好).

        Args:
            signals: {symbol: signal_value} 气象因子信号
                     可接受 {symbol: float} 或 {symbol: {"signal": float, "quality_flag": str}}
        """
        try:
            self._weather_layer.update_signals(signals)
        except Exception as e:
            logger.warning("inject_weather_signals 异常: %s", e)

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
        self._forward_returns = {
            str(k): float(v)
            for k, v in forward_returns.items()
            if isinstance(v, (int, float)) and math.isfinite(float(v))
        }
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
            except Exception:  # P2 模块 fail-safe, 待后续精确化
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
            self._ic_weights = {s: 0.7 * ic_norm.get(s, 0.0) + 0.3 * default_norm.get(s, 0.0) for s in defaults}
            logger.info(
                "IC 权重已计算: IC=%s -> 权重=%s",
                {s: round(v, 4) for s, v in ic_by_source.items()},
                {s: round(v, 4) for s, v in self._ic_weights.items()},
            )

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
        """单标的信号融合 (P1-Q5 重构后)

        重构说明 (2026-07-26):
            原函数 189 行, 含 4 层 post-mix 叠加 (主融合 + pipeline + research + lgb)
            每层重复 "4 层 NaN 防御", 是 defensive programming 反模式
            重构后: 主融合 + 3 个 PostMixLayer.apply() 调用, 共 ~40 行
            NaN 防御集中在 PostMixLayer, 可独立单元测试

        融合流程:
            1. 主融合: alpha + llm + etf + macro (动态权重)
            2. post-mix 1: pipeline_factor (5%, 影子账户)
            3. post-mix 2: research_distilled (3%, 影子账户, production 禁用)
            4. post-mix 3: lgb_enhanced (4%, LOW_QUALITY 降权至 2%)
            5. confidence 计算 + 阈值过滤
        """
        alpha_s = self._safe(alpha, "strength")
        alpha_c = self._safe(alpha, "confidence")
        llm_s = self._safe(llm, "strength")
        llm_c = self._safe(llm, "confidence")
        etf_s = self._safe(etf, "strength")
        etf_c = self._safe(etf, "confidence")

        # macro_bias NaN 防御 (主融合层)
        if not math.isfinite(macro_bias):
            macro_bias = 0.0

        weights = self._dynamic_weights(alpha_c, llm_c, etf_c)

        # === 主融合: alpha + llm + etf + macro ===
        strength = (
            weights["alpha"] * alpha_s + weights["llm"] * llm_s + weights["etf"] * etf_s + weights["macro"] * macro_bias
        )
        if not math.isfinite(strength):
            logger.warning(
                "[SignalFusion] 主融合 NaN (symbol=%s): alpha_s=%s, llm_s=%s, etf_s=%s, macro_bias=%s, weights=%s",
                symbol,
                alpha_s,
                llm_s,
                etf_s,
                macro_bias,
                weights,
            )
            strength = 0.0
        strength = max(-1.0, min(1.0, strength))

        # === P1-Q5 修复: post-mix 叠加委托给 PostMixLayer ===
        # 原代码: 每层重复 ~20 行 (NaN 检查 + 公式 + 边界裁剪)
        # 修复后: 单行调用, NaN 防御集中在 PostMixLayer.apply()
        pipeline_s = self._pipeline_layer.get_signal(symbol)
        strength, pipeline_applied = self._pipeline_layer.apply(strength, symbol)

        research_s = self._research_layer.get_signal(symbol)
        strength, research_applied = self._research_layer.apply(strength, symbol)

        lgb_s = self._lgb_layer.get_signal(symbol)
        strength, lgb_applied = self._lgb_layer.apply(strength, symbol)

        # v8.4.1: 外部策略信号叠加 (daily_stock_analysis 15种A股策略)
        self._external_strategy_layer.get_signal(symbol)
        strength, ext_applied = self._external_strategy_layer.apply(strength, symbol)

        # v8.6.13: 气象因子信号叠加 (weather_factor_engine 7因子)
        weather_s = self._weather_layer.get_signal(symbol)
        strength, weather_applied = self._weather_layer.apply(strength, symbol)

        # === confidence 计算 ===
        raw_confidence = weights["alpha"] * alpha_c + weights["llm"] * llm_c + weights["etf"] * etf_c + 0.05
        if not math.isfinite(raw_confidence):
            logger.warning("[SignalFusion] confidence NaN (symbol=%s), 归零", symbol)
            raw_confidence = 0.0
        confidence = max(0.0, min(1.0, raw_confidence))

        # === 阈值过滤 + 指数响应 ===
        if abs(strength) < 0.10 or confidence < self.min_confidence:
            strength = 0.0
            confidence = 0.0
        else:
            strength = float(np.sign(strength) * (abs(strength) ** 0.85))
            if not math.isfinite(strength):
                strength = 0.0
            strength = max(-1.0, min(1.0, strength))

        # 获取 LGB 质量标记用于审计 (PostMixLayer 内部已处理降权)
        lgb_quality_flag = self._lgb_layer.quality_flags.get(symbol, "OK")
        effective_lgb_weight = self._lgb_layer.get_effective_weight(symbol)

        return FusionSignal(
            symbol=symbol,
            strength=round(float(strength), 4),
            confidence=round(float(confidence), 4),
            sources={
                "alpha_strength": alpha_s,
                "llm_strength": llm_s,
                "etf_strength": etf_s,
                "macro_bias": macro_bias,
                "pipeline_factor_strength": pipeline_s,
                "research_distilled_strength": research_s,
                "lgb_enhanced_strength": lgb_s,
                "weather_factor_strength": weather_s,
            },
            meta={
                "weights": weights,
                "alpha_confidence": alpha_c,
                "llm_confidence": llm_c,
                "etf_confidence": etf_c,
                "pipeline_factor_weight": self._pipeline_layer.weight,
                "pipeline_factor_applied": pipeline_applied,
                "research_distilled_weight": self._research_layer.weight,
                "research_distilled_applied": research_applied,
                "lgb_enhanced_weight": self._lgb_layer.weight,
                "effective_lgb_weight": effective_lgb_weight,
                "lgb_quality_flag": lgb_quality_flag,
                "lgb_enhanced_applied": lgb_applied,
                "weather_factor_weight": self._weather_layer.weight,
                "weather_factor_applied": weather_applied,
                "nan_defense_applied": True,
                # P1-Q5 审计字段: 标记重构后使用 PostMixLayer 抽象
                "postmix_layer_refactored": True,
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
        except Exception:  # P2 模块 fail-safe, 待后续精确化
            return 0.0

    # ------------------------------------------------------------
    # 过滤
    # ------------------------------------------------------------

    def filter_tradable(self, signals: List[FusionSignal]) -> List[FusionSignal]:
        return [s for s in signals if s.strength != 0.0 and s.confidence >= self.min_confidence]

    def top(self, signals: List[FusionSignal], k: int = 10) -> List[FusionSignal]:
        ranked = sorted(signals, key=lambda s: abs(s.strength) * s.confidence, reverse=True)
        return ranked[:k]
