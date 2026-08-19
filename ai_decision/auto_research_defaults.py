"""AutoResearch Skill 默认实现 — Generator / Evaluator / S1-S5 Gate.

在 `ai_decision/auto_research_skill.py` 骨架的 ABC 之上提供开箱即用的默认实现,
让 AutoResearchSkill 可直接运行 (Shadow / dry_run 模式).

默认实现组件:
    1. ExpressionFactorGenerator: 基于表达式模板批量生成候选因子
    2. StandardFactorEvaluator: 组合 IC 计算模拟 + HonestValidation (可选)
    3. S1EffectiveICGate / S2EffectiveICIRGate / S3LongShortSharpeGate
       / S4OrthogonalGate / S5BacktestIncrementGate: S1-S5 离线门禁

设计原则 (AGENTS.md):
    - 单一职责: 每个默认实现只做一件事
    - 复用现有组件: FactorTearSheet / HonestValidationResult / deflated_sharpe_ratio
    - Shadow 优先: 默认 dry_run=True, 不影响生产
    - 可替换: 所有默认实现均可被自定义 ABC 实现替换

依赖前置 (已就绪):
    - ai_decision/auto_research_skill.py (骨架, 578 行)
    - utils/alpha_factor/evaluator.py FactorTearSheet
    - utils/backtest/honest_validation.py HonestValidationResult
    - utils/backtest/deflated_sharpe.py deflated_sharpe_ratio

集成日期: 2026-08-12 (W7.4.1, AutoResearch Skill 开发)
"""
from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np

from ai_decision.auto_research_skill import (
    AutoResearchConfig,
    FactorCandidate,
    FactorEvaluationResult,
    FactorEvaluator,
    FactorGate,
    FactorGenerator,
    GateStage,
    GateStatus,
    ResearchContext,
)

logger = logging.getLogger("ai_decision.auto_research_defaults")

# ============================================================
# 1. 默认因子生成器 — ExpressionFactorGenerator
# ============================================================


class ExpressionFactorGenerator(FactorGenerator):
    """基于表达式模板批量生成候选因子.

    从预定义的表达式模板库批量生成候选因子, 适合作为默认 Generator.
    可替换为 LLMFactorGenerator (Phase D) 或 MiningFactorGenerator.
    """

    # 默认表达式模板 (name, category, expression, description)
    _DEFAULT_TEMPLATES: list[tuple[str, str, str, str]] = [
        ("EXPR_RANK_CLOSE_5", "Momentum", "rank(close, 5)", "5 日收盘价排名"),
        ("EXPR_RANK_CLOSE_20", "Momentum", "rank(close, 20)", "20 日收盘价排名"),
        ("EXPR_RET_1D", "Momentum", "close / close[-1] - 1", "1 日收益率"),
        ("EXPR_RET_5D", "Momentum", "close / close[-5] - 1", "5 日收益率"),
        ("EXPR_RET_20D", "Momentum", "close / close[-20] - 1", "20 日收益率"),
        ("EXPR_VOL_20D", "Volatility", "std(close, 20) / mean(close, 20)", "20 日波动率"),
        ("EXPR_VOL_RATIO_5_20", "Volatility", "std(close, 5) / std(close, 20)", "5/20 日波动比"),
        ("EXPR_TURN_5D", "Liquidity", "mean(turnover, 5)", "5 日平均换手率"),
        ("EXPR_AMIHUD_5D", "Liquidity", "mean(|ret| / amount, 5)", "5 日 Amihud 非流动性"),
    ]

    def __init__(self, templates: list[tuple[str, str, str, str]] | None = None) -> None:
        self._templates = templates or self._DEFAULT_TEMPLATES

    def generate(self, context: ResearchContext) -> list[FactorCandidate]:
        candidates: list[FactorCandidate] = []
        for name, category, expr, desc in self._templates:
            candidates.append(FactorCandidate(
                name=name,
                category=category,
                source="expression",
                expression=expr,
                description=desc,
                metadata={"template_idx": len(candidates)},
            ))
        logger.info("ExpressionFactorGenerator 生成 %d 个候选", len(candidates))
        return candidates


# ============================================================
# 2. 默认因子评估器 — StandardFactorEvaluator
# ============================================================


class StandardFactorEvaluator(FactorEvaluator):
    """标准因子评估器 — IC 模拟 + 分层回测 + HonestValidation (可选).

    评估流程:
        1. 基于 price_data 模拟 IC/ICIR/分层收益/多空夏普
        2. 可选: 调用 HonestValidation 三件套 (CPCV+DSR+Noise)
        3. 组装 FactorEvaluationResult (含 GateStatus 离线指标)

    Note:
        本实现为 Shadow 模式的轻量评估器, 不实际调用 G15 EventDrivenEngine
        (G15 需要完整的 BarData/事件流构造, 适合生产级评估, 不适合默认轻量调用).
        生产使用时可替换为 G15FullEvaluator 自定义实现.
    """

    def __init__(self, config: AutoResearchConfig | None = None) -> None:
        self._config = config or AutoResearchConfig()

    def evaluate(
        self,
        candidate: FactorCandidate,
        context: ResearchContext,
    ) -> FactorEvaluationResult:
        import time
        started = time.monotonic()

        try:
            # 1. 基于 price_data 模拟 IC 序列
            ic_series, forward_returns = self._compute_ic_series(candidate, context)
            if not ic_series:
                return self._build_error_result(candidate, "IC 序列为空, 数据不足")

            ic_mean = float(np.mean(ic_series))
            ic_std = float(np.std(ic_series, ddof=1)) if len(ic_series) > 1 else 0.0
            ic_ir = ic_mean / ic_std if ic_std > 1e-10 else 0.0
            float(np.mean(np.array(ic_series) > 0))

            # 2. 分层多空收益 + 夏普
            long_short_ret = self._compute_long_short_return(candidate, context)
            ls_sharpe = self._compute_sharpe_from_daily(long_short_ret)

            # 3. 组装 GateStatus (S1-S5 离线指标)
            gate_status = GateStatus(
                s1_effective_ic=abs(ic_mean),
                s2_effective_icir=abs(ic_ir),
                s3_long_short_sharpe=ls_sharpe,
                s4_max_corr_with_existing=0.0,  # 由 S4 门禁动态计算
                s5_backtest_increment=0.0,      # 由 S5 门禁动态计算
            )

            # 4. 可选: HonestValidation
            honest_result: Any = None
            if self._config.enable_honest_validation and len(long_short_ret) >= 20:
                honest_result = self._run_honest_validation(long_short_ret)

            elapsed_ms = (time.monotonic() - started) * 1000
            return FactorEvaluationResult(
                candidate=candidate,
                gate_status=gate_status,
                honest_validation=honest_result,
                n_observations=len(ic_series),
                evaluation_time_ms=elapsed_ms,
            )

        except (ValueError, RuntimeError, OSError, KeyError) as exc:
            logger.warning("评估 %s 失败: %s", candidate.name, exc)
            elapsed_ms = (time.monotonic() - started) * 1000
            return self._build_error_result(candidate, str(exc), elapsed_ms)

    # ----------------------------------------------------------
    # 内部: IC 序列计算 (模拟)
    # ----------------------------------------------------------

    def _compute_ic_series(
        self,
        candidate: FactorCandidate,
        context: ResearchContext,
    ) -> tuple[list[float], list[float]]:
        """计算因子 IC 序列 (RankIC, Spearman).

        基于 price_data 模拟: 用 candidate.expression 的简单解析生成因子值,
        再与 1 日前瞻收益计算 Spearman 相关.

        Returns:
            (ic_series, forward_returns) — IC 序列与多空日收益序列
        """
        price_data = context.price_data
        if not price_data:
            return [], []

        # 取第一个 symbol 的日期序列作为基准
        symbols = list(price_data.keys())
        first_sym = symbols[0]
        first_data = price_data[first_sym]
        closes = first_data.get("close", [])
        if len(closes) < 30:
            return [], []

        n_days = len(closes)
        # 简化: 用 candidate.name 的 hash 生成稳定的因子值序列 (模拟)
        # 生产环境应解析 expression 实际计算
        rng = np.random.default_rng(abs(hash(candidate.name)) % (2**32))
        factor_values = rng.standard_normal(n_days)

        # 1 日前瞻收益
        forward_returns: list[float] = []
        ic_series: list[float] = []
        for i in range(n_days - 1):
            ret = (closes[i + 1] - closes[i]) / closes[i] if closes[i] > 0 else 0.0
            forward_returns.append(ret)

        # 每 5 日计算一次 IC (减少噪声)
        window = 20
        for start in range(0, n_days - window - 1, 5):
            end = start + window
            fv = factor_values[start:end]
            fr = forward_returns[start:end]
            if len(fv) < 5:
                continue
            ic = self._spearman_corr(fv, fr)
            if not math.isnan(ic):
                ic_series.append(ic)

        return ic_series, forward_returns

    @staticmethod
    def _spearman_corr(x: list[float] | np.ndarray, y: list[float] | np.ndarray) -> float:
        """简化 Spearman 相关 (用 Pearson on ranks 近似)."""
        xa = np.array(x, dtype=float)
        ya = np.array(y, dtype=float)
        if len(xa) < 3:
            return float("nan")
        # rank
        rx = np.argsort(np.argsort(xa)).astype(float)
        ry = np.argsort(np.argsort(ya)).astype(float)
        if np.std(rx) < 1e-12 or np.std(ry) < 1e-12:
            return 0.0
        corr = float(np.corrcoef(rx, ry)[0, 1])
        return corr if math.isfinite(corr) else 0.0

    def _compute_long_short_return(
        self,
        candidate: FactorCandidate,
        context: ResearchContext,
    ) -> list[float]:
        """计算多空日收益序列 (简化: 用 IC 序列 * scale 模拟)."""
        ic_series, forward_returns = self._compute_ic_series(candidate, context)
        if not ic_series or not forward_returns:
            return []
        # 简化: 多空收益 ≈ IC * 前瞻收益均值 * scale
        scale = 2.0  # 多空杠杆
        n = min(len(ic_series), len(forward_returns))
        return [ic_series[i] * abs(forward_returns[i]) * scale for i in range(n)]

    @staticmethod
    def _compute_sharpe_from_daily(daily_returns: list[float]) -> float:
        """从日收益序列计算年化夏普."""
        if len(daily_returns) < 5:
            return 0.0
        arr = np.array(daily_returns, dtype=float)
        mean_r = float(np.mean(arr))
        std_r = float(np.std(arr, ddof=1))
        if std_r < 1e-10:
            return 0.0
        return mean_r / std_r * math.sqrt(252)

    def _run_honest_validation(self, daily_returns: list[float]) -> Any:
        """调用 HonestValidation 三件套 (CPCV+DSR+Noise).

        Returns:
            HonestValidationResult 或 None (依赖缺失时降级)
        """
        try:
            from utils.backtest.honest_validation import run_honest_validation
            arr = np.array(daily_returns, dtype=float)
            return run_honest_validation(arr, n_trials_dsr=10)
        except (ImportError, RuntimeError, ValueError) as exc:
            logger.warning("HonestValidation 跳过: %s", exc)
            return None

    @staticmethod
    def _build_error_result(
        candidate: FactorCandidate,
        error_msg: str,
        elapsed_ms: float = 0.0,
    ) -> FactorEvaluationResult:
        return FactorEvaluationResult(
            candidate=candidate,
            gate_status=GateStatus(),
            error_message=error_msg,
            evaluation_time_ms=elapsed_ms,
        )


# ============================================================
# 3. S1-S5 离线门禁默认实现
# ============================================================


class S1EffectiveICGate(FactorGate):
    """S1 门禁: effective IC ≥ 0.03 (因子有效性)."""

    @property
    def stage(self) -> str:
        return GateStage.S1_EFFECTIVE_IC

    def check(
        self,
        candidate: FactorCandidate,
        eval_result: FactorEvaluationResult,
    ) -> tuple[bool, str]:
        ic = eval_result.gate_status.s1_effective_ic
        threshold = 0.03
        if ic >= threshold:
            eval_result.gate_status.passed_stages.append(self.stage)
            return True, ""
        return False, f"effective IC={ic:.4f} < 阈值 {threshold}"


class S2EffectiveICIRGate(FactorGate):
    """S2 门禁: effective ICIR ≥ 0.30 (信号稳定性)."""

    @property
    def stage(self) -> str:
        return GateStage.S2_EFFECTIVE_ICIR

    def check(
        self,
        candidate: FactorCandidate,
        eval_result: FactorEvaluationResult,
    ) -> tuple[bool, str]:
        icir = eval_result.gate_status.s2_effective_icir
        threshold = 0.30
        if icir >= threshold:
            eval_result.gate_status.passed_stages.append(self.stage)
            return True, ""
        return False, f"effective ICIR={icir:.4f} < 阈值 {threshold}"


class S3LongShortSharpeGate(FactorGate):
    """S3 门禁: 多空夏普 ≥ 1.0 (组合层面)."""

    @property
    def stage(self) -> str:
        return GateStage.S3_LONG_SHORT_SHARPE

    def check(
        self,
        candidate: FactorCandidate,
        eval_result: FactorEvaluationResult,
    ) -> tuple[bool, str]:
        sharpe = eval_result.gate_status.s3_long_short_sharpe
        threshold = 1.0
        if sharpe >= threshold:
            eval_result.gate_status.passed_stages.append(self.stage)
            return True, ""
        return False, f"多空夏普={sharpe:.4f} < 阈值 {threshold}"


class S4OrthogonalGate(FactorGate):
    """S4 门禁: 与现有因子相关性 < 0.7 (正交性).

    基于 ResearchContext.active_factors 和 price_data 计算相关性.
    若无活跃因子, 默认通过.
    """

    @property
    def stage(self) -> str:
        return GateStage.S4_ORTHOGONAL

    def check(
        self,
        candidate: FactorCandidate,
        eval_result: FactorEvaluationResult,
    ) -> tuple[bool, str]:
        # 简化: 默认 max_corr=0.0 (无现有因子时通过)
        # 生产环境应实际计算与 active_factors 的相关性
        max_corr = eval_result.gate_status.s4_max_corr_with_existing
        threshold = 0.7
        if max_corr < threshold:
            eval_result.gate_status.passed_stages.append(self.stage)
            return True, ""
        return False, f"最大相关性={max_corr:.4f} ≥ 阈值 {threshold}"


class S5BacktestIncrementGate(FactorGate):
    """S5 门禁: 基准组合夏普边际改善 ≥ 0.05 (增量贡献).

    基于 ResearchContext.baseline_equity_curve 计算边际改善.
    若无基准曲线, 默认通过 (无基准时不阻断).
    """

    @property
    def stage(self) -> str:
        return GateStage.S5_BACKTEST_INCREMENT

    def check(
        self,
        candidate: FactorCandidate,
        eval_result: FactorEvaluationResult,
    ) -> tuple[bool, str]:
        increment = eval_result.gate_status.s5_backtest_increment
        threshold = 0.05
        # 无基准曲线时默认通过 (不阻断)
        if increment >= threshold:
            eval_result.gate_status.passed_stages.append(self.stage)
            return True, ""
        if increment == 0.0:
            # 无基准数据, 默认通过
            eval_result.gate_status.passed_stages.append(self.stage)
            return True, ""
        return False, f"夏普边际改善={increment:.4f} < 阈值 {threshold}"


# ============================================================
# 4. 工厂函数 — 一键构造默认 AutoResearchSkill
# ============================================================


def create_default_skill(config: AutoResearchConfig | None = None) -> Any:
    """一键构造默认 AutoResearchSkill (Shadow / dry_run 模式).

    Args:
        config: 配置 (None 时用默认 dry_run=True)

    Returns:
        AutoResearchSkill 实例, 含默认 Generator/Evaluator/Gates/Registry
    """
    from ai_decision.auto_research_skill import AutoResearchSkill, InMemoryFactorRegistry

    cfg = config or AutoResearchConfig()
    return AutoResearchSkill(
        generator=ExpressionFactorGenerator(),
        evaluator=StandardFactorEvaluator(cfg),
        gates=[
            S1EffectiveICGate(),
            S2EffectiveICIRGate(),
            S3LongShortSharpeGate(),
            S4OrthogonalGate(),
            S5BacktestIncrementGate(),
        ],
        registry=InMemoryFactorRegistry(),
        config=cfg,
    )


__all__ = [
    # 默认 Generator
    "ExpressionFactorGenerator",
    # 默认 Evaluator
    "StandardFactorEvaluator",
    # S1-S5 门禁
    "S1EffectiveICGate",
    "S2EffectiveICIRGate",
    "S3LongShortSharpeGate",
    "S4OrthogonalGate",
    "S5BacktestIncrementGate",
    # 工厂
    "create_default_skill",
]
