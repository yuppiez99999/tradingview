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
        try:
            from utils.trading_env import get_trading_env, TradingEnv
            if get_trading_env() == TradingEnv.PRODUCTION:
                _research_weight = 0.0
                logger.info(
                    "[SignalFusion] production 实盘模式: research_distilled_weight 强制为 0 "
                    "(v8.6.9 环境隔离: 仅 shadow/development 模式激活)"
                )
        except Exception:
            # trading_env 不可用时保持配置值 (fail-open for 新功能, 不影响主流程)
            pass
        self.research_distilled_weight = _research_weight
        # v8.7: LGB 增强信号权重（保守 4%, post-mix）
        # 设计依据: 23 标的平均 IC=0.1631, IC>0.3 的 6 个, IC>0.2 的 9 个
        # 安全设计: post-mix 模式 + 低质量标的 (LOW_QUALITY) 降权至 50%
        self.lgb_enhanced_weight = lgb_enhanced_weight
        # 动态 IC 权重支持：注入 forward_returns 后按各源 IC 动态加权
        self._forward_returns: Optional[Dict[str, float]] = None
        self._ic_weights: Optional[Dict[str, float]] = None
        # 缓存最近一次 fuse 的各源信号值，用于 IC 计算
        self._last_signals_by_source: Optional[Dict[str, Dict[str, float]]] = None
        # Qlib 信号缓存（由 inject_qlib_signal 注入，可作为 alpha 源）
        self._qlib_signals: Dict[str, float] = {}
        # v8.6.4: Pipeline 因子组合信号缓存（由 inject_pipeline_factor_signals 注入）
        # 信号范围 [-1, 1]，正值看涨负值看跌
        self._pipeline_factor_signals: Dict[str, float] = {}
        # v8.6.9: 研究蒸馏信号缓存（由 inject_research_distilled_signals 注入）
        # 信号范围 [-1, 1]，来自 ResearchDistiller.load_daily_snapshot()
        self._research_distilled_signals: Dict[str, float] = {}
        # v8.7: LGB 增强信号缓存（由 inject_lgb_enhanced_signals 注入）
        # 信号范围 [-1, 1], 来自 models/lgb_enhanced/lgb_enhanced_signals.json
        # 包含 quality_flag 信息: OK / LOW_QUALITY (降权 50%)
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
            self._qlib_signals = {str(k): float(v) for k, v in qlib_dict.items()
                                  if isinstance(v, (int, float)) and math.isfinite(float(v))}
            logger.info("已注入 Qlib 信号: %d 个标的", len(self._qlib_signals))
        except Exception as e:
            logger.warning("inject_qlib_signal 异常: %s", e)

    def inject_pipeline_factor_signals(self, signals: Dict[str, float]) -> None:
        """注入 Pipeline 因子组合信号（v8.6.4 P0-A 深度修复）

        将 PipelineOrchestrator 离线生成的 IC 加权组合信号注入融合引擎，
        作为第 5 个信号源参与融合，权重默认 0.05（保守起步）。

        信号范围 [-1, 1]：
        - 正值 = 看涨（IC 加权后预期正收益）
        - 负值 = 看跌（IC 加权后预期负收益）
        - 0 = 中性

        设计依据:
        - PipelineOrchestrator v6.9 实测 IC_IR=+0.5840, live_dsr=+2.2033
        - 影子账户 OOS 验证通过后可上调 pipeline_factor_weight
        - 当前仅影响影子账户，不影响 500万 实盘

        Args:
            signals: {symbol: signal ∈ [-1, 1]} 来自 PortfolioOptimizer.load_factor_signals()
        """
        try:
            if not isinstance(signals, dict) or not signals:
                logger.warning("inject_pipeline_factor_signals: 输入为空或非 dict, 跳过")
                return
            self._pipeline_factor_signals = {
                str(k): float(v) for k, v in signals.items()
                if isinstance(v, (int, float)) and math.isfinite(float(v))
            }
            logger.info(
                "已注入 Pipeline 因子组合信号: %d 个标的 (weight=%.2f)",
                len(self._pipeline_factor_signals),
                self.pipeline_factor_weight,
            )
        except Exception as e:
            logger.warning("inject_pipeline_factor_signals 异常: %s", e)

    def inject_research_distilled_signals(self, signals: Dict[str, float]) -> None:
        """注入研究蒸馏信号（v8.6.9 第 6 信号源）

        将 ResearchDistiller 离线蒸馏的研报/业绩会/书籍信号注入融合引擎，
        作为第 6 个信号源参与融合，权重默认 0.03（保守 post-mix）。

        信号范围 [-1, 1]：
        - 正值 = 看涨（研报/业绩会利好）
        - 负值 = 看跌（研报/业绩会利空）
        - 0 = 中性

        设计依据:
        - cangjie-skill RIA--TV++ 方法论量化版
        - 离线蒸馏(06:00) + 在线注入(07:00), 不增加关键路径耗时
        - post-mix 模式: 不修改主融合公式 alpha(0.70)+llm(0.10)+etf(0.12)+macro(0.08)
        - 仅影响影子账户, 不影响 500万 实盘

        降级链:
        - 输入空/非 dict → 跳过注入, 不影响融合
        - 信号值 NaN/Inf → 过滤掉, 仅保留有效值
        - 全部无效 → 空缓存, post-mix 块不触发

        Args:
            signals: {symbol: signal ∈ [-1, 1]} 来自 ResearchDistiller.load_daily_snapshot()
        """
        try:
            if not isinstance(signals, dict) or not signals:
                logger.warning("inject_research_distilled_signals: 输入为空或非 dict, 跳过")
                return
            self._research_distilled_signals = {
                str(k): float(v) for k, v in signals.items()
                if isinstance(v, (int, float)) and math.isfinite(float(v))
            }
            logger.info(
                "已注入研究蒸馏信号: %d 个标的 (weight=%.2f)",
                len(self._research_distilled_signals),
                self.research_distilled_weight,
            )
        except Exception as e:
            logger.warning("inject_research_distilled_signals 异常: %s", e)

    def inject_lgb_enhanced_signals(
        self,
        signals: Dict[str, Any],
        quality_flags: Optional[Dict[str, str]] = None,
    ) -> None:
        """注入 LGB 增强信号（v8.7 第 7 信号源）

        将 lgb_enhanced_trainer 离线训练的 LightGBM 信号注入融合引擎，
        作为第 7 个信号源参与融合，权重默认 0.04（保守 post-mix）。

        信号范围 [-1, 1]：
        - 正值 = 看涨（模型预测未来 horizon 日正收益）
        - 负值 = 看跌（模型预测未来 horizon 日负收益）
        - 0 = 中性

        设计依据:
        - lgb_enhanced_trainer v8.7 GPU 训练, 平均 IC=0.1631
        - 23 标的全覆盖, 其中 IC>0.3 的 6 个, IC>0.2 的 9 个
        - 真实 OHLCV + 新闻情绪因子 (Wind MCP 优先, iFinD 回退)
        - post-mix 模式: 不修改主融合公式, 在 pipeline/research 之后叠加

        降权机制:
        - quality_flag=LOW_QUALITY 的标的 (如 688981/600036/600219) 权重降至 50%
        - 设计依据: 这些标的 CV IC 不稳定或样本数不足, 信号可信度较低
        - 安全设计: 降权而非清零, 保留信号方向但减少影响

        降级链:
        - 输入空/非 dict → 跳过注入, 不影响融合
        - 信号值 NaN/Inf → 过滤掉, 仅保留有效值
        - 全部无效 → 空缓存, post-mix 块不触发

        Args:
            signals: 支持两种格式:
                1. {symbol: signal ∈ [-1, 1]} 扁平格式
                2. {symbol: {"signal": float, "quality_flag": str, ...}} 结构化格式
                   (匹配 models/lgb_enhanced/lgb_enhanced_signals.json)
            quality_flags: 可选, {symbol: "OK" | "LOW_QUALITY"}
                若 signals 为结构化格式, 优先从 signals 中提取 quality_flag
        """
        try:
            if not isinstance(signals, dict) or not signals:
                logger.warning("inject_lgb_enhanced_signals: 输入为空或非 dict, 跳过")
                return

            # 解析两种格式: 扁平 / 结构化
            parsed_signals: Dict[str, float] = {}
            parsed_flags: Dict[str, str] = {}

            for sym, val in signals.items():
                if isinstance(val, dict):
                    # 结构化格式: {"signal": float, "quality_flag": str, ...}
                    sig = val.get("signal")
                    flag = val.get("quality_flag", "OK")
                    if isinstance(sig, (int, float)) and math.isfinite(float(sig)):
                        parsed_signals[str(sym)] = float(sig)
                        parsed_flags[str(sym)] = str(flag) if flag in ("OK", "LOW_QUALITY") else "OK"
                elif isinstance(val, (int, float)):
                    # 扁平格式: {symbol: signal}
                    if math.isfinite(float(val)):
                        parsed_signals[str(sym)] = float(val)
                        parsed_flags[str(sym)] = "OK"
                # 其他类型跳过

            # 若显式传入 quality_flags, 覆盖结构化解析结果
            if quality_flags and isinstance(quality_flags, dict):
                for sym, flag in quality_flags.items():
                    if str(sym) in parsed_signals and flag in ("OK", "LOW_QUALITY"):
                        parsed_flags[str(sym)] = str(flag)

            self._lgb_enhanced_signals = parsed_signals
            self._lgb_quality_flags = parsed_flags

            # 统计质量分布
            ok_count = sum(1 for f in parsed_flags.values() if f == "OK")
            low_quality_count = sum(1 for f in parsed_flags.values() if f == "LOW_QUALITY")

            logger.info(
                "已注入 LGB 增强信号: %d 个标的 (weight=%.2f, OK=%d, LOW_QUALITY=%d)",
                len(parsed_signals),
                self.lgb_enhanced_weight,
                ok_count,
                low_quality_count,
            )
        except Exception as e:
            logger.warning("inject_lgb_enhanced_signals 异常: %s", e)

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

        # v8.6.8 P1-LIVE-06: 防御性 NaN 检查
        # _safe() 已经过滤 NaN/Inf, 但若上游传入 dict[float('nan')] 仍可能漏网
        # 这里对最终 strength 再做一次 NaN 防御, 避免污染融合结果
        if not math.isfinite(macro_bias):
            macro_bias = 0.0

        weights = self._dynamic_weights(alpha_c, llm_c, etf_c)

        strength = (
            weights["alpha"] * alpha_s
            + weights["llm"] * llm_s
            + weights["etf"] * etf_s
            + weights["macro"] * macro_bias
        )
        # v8.6.8 P1-LIVE-06: NaN 防御性检查
        # 若 weights/signal 任一为 NaN (理论上 _safe 已过滤), 强制归零
        if not math.isfinite(strength):
            logger.warning(
                "[SignalFusion] 检测到 NaN strength (symbol=%s): "
                "alpha_s=%s, llm_s=%s, etf_s=%s, macro_bias=%s, weights=%s",
                symbol, alpha_s, llm_s, etf_s, macro_bias, weights,
            )
            strength = 0.0
        strength = max(-1.0, min(1.0, strength))

        # v8.6.4 P0-A 深度修复: Pipeline 因子组合信号调整（保守 5%）
        # 设计依据: PipelineOrchestrator 实测 IC_IR=+0.5840, 仅在影子账户层影响
        pipeline_s = self._pipeline_factor_signals.get(symbol, 0.0)
        # v8.6.8 P1-LIVE-06: pipeline_s 防御性 NaN 检查
        # inject_pipeline_factor_signals 已经过滤 NaN, 但此处再防御一次
        if not math.isfinite(pipeline_s):
            logger.warning(
                "[SignalFusion] pipeline_factor_signals[%s] = NaN, 强制归零",
                symbol,
            )
            pipeline_s = 0.0
        if pipeline_s != 0.0 and self.pipeline_factor_weight > 0:
            strength = (
                strength * (1.0 - self.pipeline_factor_weight)
                + pipeline_s * self.pipeline_factor_weight
            )
            if not math.isfinite(strength):
                logger.warning(
                    "[SignalFusion] pipeline 融合后 strength 为 NaN (symbol=%s), 归零",
                    symbol,
                )
                strength = 0.0
            strength = max(-1.0, min(1.0, strength))

        # v8.6.9 新增: 研究蒸馏信号 post-mix 调整（保守 3%）
        # 设计依据: RIA--TV++ 量化版, post-mix 模式不修改主融合公式
        # 4 层 NaN 防御: 注入过滤 + 取值防御 + 融合后检查 + 最终裁剪
        research_s = self._research_distilled_signals.get(symbol, 0.0)
        # 层 2: 取值防御 (inject 已过滤, 但 dict.get 后再防御一次)
        if not math.isfinite(research_s):
            logger.warning(
                "[SignalFusion] research_distilled_signals[%s] = NaN, 强制归零",
                symbol,
            )
            research_s = 0.0
        if research_s != 0.0 and self.research_distilled_weight > 0:
            # post-mix: 在 pipeline 调整后的 strength 上叠加 research 信号
            strength = (
                strength * (1.0 - self.research_distilled_weight)
                + research_s * self.research_distilled_weight
            )
            # 层 3: 融合后 NaN 检查
            if not math.isfinite(strength):
                logger.warning(
                    "[SignalFusion] research_distilled 融合后 strength 为 NaN (symbol=%s), 归零",
                    symbol,
                )
                strength = 0.0
            # 层 4: 最终边界裁剪
            strength = max(-1.0, min(1.0, strength))

        # v8.7 新增: LGB 增强信号 post-mix 调整（保守 4%, 含 LOW_QUALITY 降权）
        # 设计依据: lgb_enhanced_trainer v8.7 平均 IC=0.1631, 23 标的全覆盖
        # post-mix 模式: 在 alpha+llm+etf+macro+pipeline+research 之后叠加
        # 4 层 NaN 防御: 注入过滤 + 取值防御 + 融合后检查 + 最终裁剪
        # LOW_QUALITY 降权: 688981/600036/600219 等标的权重降至 50%
        lgb_s = self._lgb_enhanced_signals.get(symbol, 0.0)
        # 层 2: 取值防御 (inject 已过滤, 但 dict.get 后再防御一次)
        if not math.isfinite(lgb_s):
            logger.warning(
                "[SignalFusion] lgb_enhanced_signals[%s] = NaN, 强制归零",
                symbol,
            )
            lgb_s = 0.0
        # LOW_QUALITY 降权: 权重降至 50% (保留信号方向但减少影响)
        lgb_quality_flag = self._lgb_quality_flags.get(symbol, "OK")
        effective_lgb_weight = self.lgb_enhanced_weight
        if lgb_quality_flag == "LOW_QUALITY":
            effective_lgb_weight = self.lgb_enhanced_weight * 0.5
        if lgb_s != 0.0 and effective_lgb_weight > 0:
            # post-mix: 在 research 调整后的 strength 上叠加 lgb 信号
            strength = (
                strength * (1.0 - effective_lgb_weight)
                + lgb_s * effective_lgb_weight
            )
            # 层 3: 融合后 NaN 检查
            if not math.isfinite(strength):
                logger.warning(
                    "[SignalFusion] lgb_enhanced 融合后 strength 为 NaN (symbol=%s), 归零",
                    symbol,
                )
                strength = 0.0
            # 层 4: 最终边界裁剪
            strength = max(-1.0, min(1.0, strength))

        raw_confidence = (
            weights["alpha"] * alpha_c
            + weights["llm"] * llm_c
            + weights["etf"] * etf_c
            + 0.05
        )
        # v8.6.8 P1-LIVE-06: confidence NaN 防御
        if not math.isfinite(raw_confidence):
            logger.warning(
                "[SignalFusion] confidence 为 NaN (symbol=%s), 归零",
                symbol,
            )
            raw_confidence = 0.0
        confidence = max(0.0, min(1.0, raw_confidence))

        if abs(strength) < 0.10 or confidence < self.min_confidence:
            strength = 0.0
            confidence = 0.0
        else:
            # 对非零信号做指数响应，放大强弱差异，让月度权重更容易随信号变化
            strength = float(np.sign(strength) * (abs(strength) ** 0.85))
            # v8.6.8 P1-LIVE-06: 最终 NaN 防御
            if not math.isfinite(strength):
                strength = 0.0
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
                "pipeline_factor_strength": pipeline_s,
                "research_distilled_strength": research_s,
                # v8.7: LGB 增强信号 (LightGBM 离线训练输出)
                "lgb_enhanced_strength": lgb_s,
            },
            meta={
                "weights": weights,
                "alpha_confidence": alpha_c,
                "llm_confidence": llm_c,
                "etf_confidence": etf_c,
                "pipeline_factor_weight": self.pipeline_factor_weight,
                "pipeline_factor_applied": pipeline_s != 0.0,
                "research_distilled_weight": self.research_distilled_weight,
                # applied=True 仅当信号非零且权重>0 (即 post-mix 实际执行)
                "research_distilled_applied": research_s != 0.0 and self.research_distilled_weight > 0,
                # v8.7: LGB 增强信号元数据 (用于审计与调试)
                "lgb_enhanced_weight": self.lgb_enhanced_weight,
                "effective_lgb_weight": effective_lgb_weight,
                "lgb_quality_flag": lgb_quality_flag,
                # applied=True 仅当信号非零且 effective_weight>0 (即 post-mix 实际执行)
                "lgb_enhanced_applied": lgb_s != 0.0 and effective_lgb_weight > 0,
                "nan_defense_applied": True,  # v8.6.8 P1-LIVE-06 标记
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
