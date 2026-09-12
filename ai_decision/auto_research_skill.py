"""AutoResearch Skill — 因子自动迭代闭环骨架 (Day 1)

职责:
    - 在 G15 事件驱动回测 + AlphaFactorLibrary + HonestValidation 三件套之上
    - 实现因子「发现 → 评估 → 门禁 → 入库 → 监控 → 退役」自动闭环
    - 默认 Shadow / dry_run 模式, 不直接修改生产因子库

核心闭环 (与 cairn/factor-discovery-loop-engineering.md 对齐):
    ┌─────────────────────────────────────────────────────────┐
    │  FactorGenerator  →  list[FactorCandidate]              │
    │         ↓                                               │
    │  FactorEvaluator  →  FactorEvaluationResult             │
    │       (AlphaFactorLibrary + G15 EventDrivenEngine       │
    │        + ResultConverter + HonestValidation 三件套)      │
    │         ↓                                               │
    │  FactorGate (S1-S7)  →  pass / reject                   │
    │         ↓                                               │
    │  FactorRegistry  →  register / retire                   │
    │         ↓                                               │
    │  FactorDecayMonitor  →  自动退役 (ICIR<阈值持续 N 月)    │
    └─────────────────────────────────────────────────────────┘

S1-S7 门禁 (与 ROADMAP Wave 5 CHAIN_MOM_60D 入库流程对齐):
    S1: effective IC ≥ 0.03 (因子有效性)
    S2: effective ICIR ≥ 0.30 (信号稳定性)
    S3: 多空夏普 ≥ 1.0 (组合层面)
    S4: 与现有因子相关性 < 0.7 (正交性)
    S5: 基准组合夏普边际改善 ≥ 0.05 (增量贡献)
    S6: 纸交易 ≥ 3 月 (实盘前验证)
    S7: 小资金 5-10% ≥ 3 月 (灰度验证)

设计原则 (AGENTS.md):
    - 不可变性 (§5.1): 所有数据类 frozen=True
    - 单一职责: Skill 只编排, 生成/评估/门禁/注册为可替换 ABC
    - 多小文件 (§5.3): 本骨架 < 400 行, 仅定义接口, 不实现具体逻辑
    - 复用现有组件: FactorValue / FactorTearSheet / EngineSummary /
                    HonestValidationResult / AlphaFactorLibrary

依赖前置 (已就绪):
    - G15 事件驱动回测 (W6.3 Sprint 3, 2026-08-12 完成)
    - AlphaFactorLibrary 13+ 大类 117 因子 (W6.1 Sprint 1)
    - HonestValidation 三件套 (W6.6.2)
    - 因子表达式引擎 (W6.6.1, 用于 FactorGenerator 默认实现)

集成日期: 2026-08-12 (阶段 A, OPTIMAL_PLAN v3)
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from utils.datetime_utils import now_bj

logger = logging.getLogger("ai_decision.auto_research")

# ============================================================
# 1. 数据类 (不可变)
# ============================================================


@dataclass(frozen=True)
class FactorCandidate:
    """候选因子定义 (不可变).

    Attributes:
        name: 因子名 (如 "FM_RET_1D", "EXPR_RANK_CLOSE_20")
        category: 大类 (Momentum/Value/Expression/ChipDistribution/...)
        source: 来源 "expression" / "mining" / "eigenalpha" / "manual" / "llm"
        expression: 因子表达式或算法描述 (供 FactorEvaluator 解释执行)
        description: 人类可读说明
        metadata: 扩展字段 (如 LLM 生成时的 prompt / temperature / model)
        created_at: ISO 时间戳
        candidate_id: 唯一 ID (uuid4 前 12 位)
    """

    name: str
    category: str
    source: str
    expression: str = ""
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: now_bj().isoformat())
    candidate_id: str = field(default_factory=lambda: f"fc_{uuid.uuid4().hex[:12]}")


@dataclass(frozen=True)
class GateStage:
    """S1-S7 门禁阶段常量."""

    S1_EFFECTIVE_IC = "S1_EFFECTIVE_IC"
    S2_EFFECTIVE_ICIR = "S2_EFFECTIVE_ICIR"
    S3_LONG_SHORT_SHARPE = "S3_LONG_SHORT_SHARPE"
    S4_ORTHOGONAL = "S4_ORTHOGONAL"
    S5_BACKTEST_INCREMENT = "S5_BACKTEST_INCREMENT"
    S6_PAPER_TRADING = "S6_PAPER_TRADING"
    S7_SMALL_CAPITAL = "S7_SMALL_CAPITAL"
    ALL_STAGES = (
        S1_EFFECTIVE_IC,
        S2_EFFECTIVE_ICIR,
        S3_LONG_SHORT_SHARPE,
        S4_ORTHOGONAL,
        S5_BACKTEST_INCREMENT,
        S6_PAPER_TRADING,
        S7_SMALL_CAPITAL,
    )


@dataclass
class GateStatus:
    """单因子 S1-S7 门禁状态 (可变, 因 S6/S7 为长期跟踪).

    S1-S5 为离线可计算门禁, S6/S7 为长期运行态门禁.
    """

    s1_effective_ic: float = 0.0  # 阈值 ≥ 0.03
    s2_effective_icir: float = 0.0  # 阈值 ≥ 0.30
    s3_long_short_sharpe: float = 0.0  # 阈值 ≥ 1.0
    s4_max_corr_with_existing: float = 0.0  # 阈值 < 0.7
    s5_backtest_increment: float = 0.0  # 阈值 ≥ 0.05 (基准组合夏普边际改善)
    s6_paper_trading_days: int = 0  # 目标 ≥ 63 (3 月)
    s7_small_capital_days: int = 0  # 目标 ≥ 63 (3 月, 5-10% 资金)
    current_stage: str = GateStage.S1_EFFECTIVE_IC
    passed_stages: list[str] = field(default_factory=list)
    failed_stage: str = ""  # 首个失败阶段 (空表示全通过)
    failure_reason: str = ""

    @property
    def passed_s1_to_s5(self) -> bool:
        """离线门禁全通过 (S1-S5)."""
        return bool(self.passed_stages) and all(
            s in self.passed_stages for s in GateStage.ALL_STAGES[:5]
        )

    @property
    def fully_promoted(self) -> bool:
        """S1-S7 全通过 (可加入生产因子库)."""
        return all(s in self.passed_stages for s in GateStage.ALL_STAGES)


@dataclass(frozen=True)
class FactorEvaluationResult:
    """单因子完整评估结果 (不可变).

    封装 G15 引擎 + 三件套 + Tear Sheet + 门禁状态.
    """

    candidate: FactorCandidate
    gate_status: GateStatus
    # 复用现有评估产物 (Any 避免硬依赖, 实际类型见类型提示注释)
    tear_sheet: Any | None = None  # utils.alpha_factor.evaluator.FactorTearSheet
    engine_summary: Any | None = (
        None  # utils.backtest.event_driven_engine.EngineSummary
    )
    backtest_result: Any | None = (
        None  # utils.hedge_rebalance_backtest.BacktestResult
    )
    honest_validation: Any | None = (
        None  # utils.backtest.honest_validation.HonestValidationResult
    )
    # 诊断字段
    n_observations: int = 0
    evaluation_time_ms: float = 0.0
    error_message: str = ""

    @property
    def passed(self) -> bool:
        """通过 S1-S5 离线门禁 (S6/S7 长期跟踪另算)."""
        return self.gate_status.passed_s1_to_s5 and not self.error_message


@dataclass(frozen=True)
class ResearchIteration:
    """单次研究迭代结果 (不可变)."""

    iteration_id: str
    timestamp: str
    candidates_generated: list[FactorCandidate] = field(default_factory=list)
    evaluations: list[FactorEvaluationResult] = field(default_factory=list)
    promoted_factors: list[str] = field(default_factory=list)  # 通过门禁并注册的因子名
    retired_factors: list[str] = field(default_factory=list)  # 本次退役的因子名
    summary: str = ""
    duration_ms: float = 0.0


@dataclass(frozen=True)
class AutoResearchConfig:
    """AutoResearch Skill 配置 (不可变)."""

    # 门禁阈值 (与 ROADMAP Wave 5 对齐)
    s1_min_effective_ic: float = 0.03
    s2_min_effective_icir: float = 0.30
    s3_min_long_short_sharpe: float = 1.0
    s4_max_corr: float = 0.7
    s5_min_increment: float = 0.05
    # 退役监控
    retire_icir_threshold: float = 0.2
    retire_consecutive_months: int = 6
    # 运行模式
    dry_run: bool = True  # True=不写入生产因子库
    max_candidates_per_iteration: int = 20
    enable_honest_validation: bool = True  # 三件套 (CPCV+DSR+Noise)
    # G15 引擎配置
    event_clock_mode: str = "MONOTONIC_INDEX"
    initial_capital: float = 1_000_000.0


# ============================================================
# 2. 抽象基类 (可替换组件)
# ============================================================


class FactorGenerator(ABC):
    """因子生成器抽象基类.

    实现示例:
        - ExpressionFactorGenerator: 基于 expression_engine.py DSL 生成候选
        - LLMFactorGenerator: 调用 LLM 生成因子表达式 (Phase D)
        - MiningFactorGenerator: 基于 factor-mining 模板批量生成
    """

    @abstractmethod
    def generate(self, context: ResearchContext) -> list[FactorCandidate]:
        """生成候选因子列表.

        Args:
            context: 研究上下文 (含行情/基本面/已有因子清单等)

        Returns:
            候选因子列表 (长度 ≤ config.max_candidates_per_iteration)
        """
        raise NotImplementedError


class FactorEvaluator(ABC):
    """因子评估器抽象基类.

    默认实现组合:
        AlphaFactorLibrary.compute_all → FactorValue
        → G15 EventDrivenEngine.run → EngineSummary
        → ResultConverter.convert → BacktestResult
        → run_honest_validation → HonestValidationResult
        → FactorTearSheet (alphalens 风格)
    """

    @abstractmethod
    def evaluate(
        self,
        candidate: FactorCandidate,
        context: ResearchContext,
    ) -> FactorEvaluationResult:
        """评估单个候选因子.

        Args:
            candidate: 候选因子
            context: 研究上下文

        Returns:
            完整评估结果 (含门禁状态)
        """
        raise NotImplementedError


class FactorGate(ABC):
    """因子门禁抽象基类 (单一阶段).

    一个 FactorGate 实例对应 S1-S7 中的一个阶段.
    AutoResearchSkill 持有 list[FactorGate] 串联执行.
    """

    @property
    @abstractmethod
    def stage(self) -> str:
        """门禁阶段 (GateStage.S1_xxx ~ S7_xxx)."""
        raise NotImplementedError

    @abstractmethod
    def check(
        self,
        candidate: FactorCandidate,
        eval_result: FactorEvaluationResult,
    ) -> tuple[bool, str]:
        """检查候选因子是否通过本门禁.

        Args:
            candidate: 候选因子
            eval_result: 评估结果

        Returns:
            (passed, reason) — (True, "") 或 (False, "失败原因")
        """
        raise NotImplementedError


class FactorRegistry(ABC):
    """因子注册表抽象基类 (入库 / 退役 / 查询).

    默认实现:
        - InMemoryFactorRegistry: dry_run 模式用, 不影响生产
        - ProductionFactorRegistry: 写入 AlphaFactorLibrary + 持久化 JSON
    """

    @abstractmethod
    def register(self, candidate: FactorCandidate) -> bool:
        """注册因子到生产库 (idempotent)."""
        raise NotImplementedError

    @abstractmethod
    def retire(self, factor_name: str, reason: str) -> bool:
        """退役因子 (标记而非物理删除)."""
        raise NotImplementedError

    @abstractmethod
    def list_active(self) -> list[str]:
        """列出当前活跃因子."""
        raise NotImplementedError

    @abstractmethod
    def is_registered(self, factor_name: str) -> bool:
        """查询因子是否已注册."""
        raise NotImplementedError


# ============================================================
# 3. 研究上下文 (可变, 跨组件共享)
# ============================================================


@dataclass
class ResearchContext:
    """研究上下文 — 跨 Generator/Evaluator/Gate/Registry 共享.

    可变以允许各组件在执行中追加诊断信息.
    """

    # 行情数据 (与 AlphaFactorLibrary.compute_all 入参对齐)
    price_data: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 基本面数据
    fundamentals: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 已注册的活跃因子清单 (供 S4 正交性检查)
    active_factors: list[str] = field(default_factory=list)
    # 基准组合权益曲线 (供 S5 增量评估)
    baseline_equity_curve: list[float] = field(default_factory=list)
    # 回测起止日期
    backtest_start: str = ""
    backtest_end: str = ""
    # 扩展字段 (图谱 / 资金流 / 新闻情绪 等)
    extras: dict[str, Any] = field(default_factory=dict)


# ============================================================
# 4. 主 Skill 类 — 编排器
# ============================================================


class AutoResearchSkill:
    """AutoResearch Skill — 因子自动迭代闭环编排器.

    生命周期:
        skill = AutoResearchSkill(generator, evaluator, gates, registry, config)
        iteration = skill.run_iteration(context)             # 单次迭代
        retired = skill.monitor_and_retire(active_factors)   # 衰减监控

    线程安全:
        非线程安全 (研究流程为单线程批处理)

    Shadow 模式铁律 (AGENTS.md):
        - dry_run=True 时, FactorRegistry.register 仅记录日志, 不写入生产
        - 切换 dry_run=False 需显式构造 config, 并经人工审批
    """

    def __init__(
        self,
        generator: FactorGenerator,
        evaluator: FactorEvaluator,
        gates: list[FactorGate],
        registry: FactorRegistry,
        config: AutoResearchConfig | None = None,
    ) -> None:
        """
        Args:
            generator: 因子生成器
            evaluator: 因子评估器
            gates: 门禁列表 (按 S1-S7 顺序, 可只含 S1-S5 离线门禁)
            registry: 因子注册表 (dry_run 时用 InMemoryFactorRegistry)
            config: 配置 (None 时用默认 dry_run=True)
        """
        if not gates:
            raise ValueError("gates 不能为空, 至少需要一个门禁阶段")
        self._generator = generator
        self._evaluator = evaluator
        self._gates = sorted(
            gates,
            key=lambda g: (
                GateStage.ALL_STAGES.index(g.stage)
                if g.stage in GateStage.ALL_STAGES
                else 99
            ),
        )
        self._registry = registry
        self._config = config or AutoResearchConfig()
        self._iterations: list[ResearchIteration] = []

        if not self._config.dry_run:
            logger.warning(
                "AutoResearchSkill 启动于非 dry_run 模式, "
                "FactorRegistry 将实际写入生产因子库"
            )

    # ----------------------------------------------------------
    # 公开 API
    # ----------------------------------------------------------

    def run_iteration(
        self,
        context: ResearchContext,
        max_candidates: int | None = None,
    ) -> ResearchIteration:
        """运行单次研究迭代.

        流程:
            1. generator.generate(context) → list[FactorCandidate]
            2. 对每个 candidate:
               a. evaluator.evaluate(candidate, context) → FactorEvaluationResult
               b. 串联 gates 检查, 首次失败即停止该 candidate
               c. 全部门禁通过 + 非 dry_run → registry.register
            3. 返回 ResearchIteration

        Args:
            context: 研究上下文
            max_candidates: 本次迭代最大候选数 (None 时用 config)

        Returns:
            单次迭代结果
        """
        iter_id = f"iter_{uuid.uuid4().hex[:12]}"
        ts = now_bj().isoformat()
        started = now_bj()

        # 1. 生成候选
        try:
            candidates = self._generator.generate(context)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.error("因子生成失败: %s", exc)
            return ResearchIteration(
                iteration_id=iter_id,
                timestamp=ts,
                summary=f"生成失败: {exc}",
                duration_ms=(now_bj() - started).total_seconds() * 1000,
            )

        cap = max_candidates or self._config.max_candidates_per_iteration
        candidates = candidates[:cap]
        logger.info("[%s] 生成 %d 个候选因子", iter_id, len(candidates))

        # 2. 逐个评估 + 门禁
        evaluations: list[FactorEvaluationResult] = []
        promoted: list[str] = []
        for cand in candidates:
            eval_result = self._evaluator.evaluate(cand, context)
            evaluations.append(eval_result)

            if eval_result.error_message:
                logger.warning(
                    "[%s] %s 评估失败: %s",
                    iter_id,
                    cand.name,
                    eval_result.error_message,
                )
                continue

            # 串联门禁
            passed, fail_reason = self._run_gates(cand, eval_result)
            if not passed:
                logger.info("[%s] %s 门禁未通过: %s", iter_id, cand.name, fail_reason)
                continue

            # 通过门禁 → 注册 (dry_run 时仅日志)
            if self._registry.register(cand):
                promoted.append(cand.name)
                logger.info(
                    "[%s] %s 已注册 (dry_run=%s)",
                    iter_id,
                    cand.name,
                    self._config.dry_run,
                )

        duration_ms = (now_bj() - started).total_seconds() * 1000
        iteration = ResearchIteration(
            iteration_id=iter_id,
            timestamp=ts,
            candidates_generated=candidates,
            evaluations=evaluations,
            promoted_factors=promoted,
            summary=f"候选 {len(candidates)} / 评估 {len(evaluations)} / 入库 {len(promoted)}",
            duration_ms=duration_ms,
        )
        self._iterations.append(iteration)
        return iteration

    def monitor_and_retire(
        self,
        active_factors: list[str],
        decay_signals: dict[str, float] | None = None,
    ) -> list[str]:
        """衰减监控 → 自动退役.

        Args:
            active_factors: 当前活跃因子清单
            decay_signals: {factor_name: latest_icir}, None 时跳过退役判定

        Returns:
            本次退役的因子名列表
        """
        if not decay_signals:
            logger.info("未提供 decay_signals, 跳过退役判定")
            return []

        threshold = self._config.retire_icir_threshold
        retired: list[str] = []
        for name, icir in decay_signals.items():
            if name not in active_factors:
                continue
            if icir < threshold:
                reason = f"ICIR={icir:.3f} < 阈值 {threshold} 持续达退役条件"
                if self._registry.retire(name, reason):
                    retired.append(name)
                    logger.info("因子退役: %s (%s)", name, reason)
        return retired

    def get_history(self) -> list[ResearchIteration]:
        """返回历史迭代记录 (副本)."""
        return list(self._iterations)

    @property
    def config(self) -> AutoResearchConfig:
        """当前配置."""
        return self._config

    # ----------------------------------------------------------
    # 内部: 串联门禁
    # ----------------------------------------------------------

    def _run_gates(
        self,
        candidate: FactorCandidate,
        eval_result: FactorEvaluationResult,
    ) -> tuple[bool, str]:
        """串联执行所有门禁, 首次失败即返回."""
        for gate in self._gates:
            try:
                passed, reason = gate.check(candidate, eval_result)
            except (ValueError, RuntimeError, AttributeError, TypeError) as exc:
                return False, f"{gate.stage} 门禁异常: {exc}"
            if not passed:
                return False, f"{gate.stage}: {reason}"
        return True, ""


# ============================================================
# 5. 默认 InMemoryFactorRegistry (dry_run 用, 开箱即用)
# ============================================================


class InMemoryFactorRegistry(FactorRegistry):
    """内存因子注册表 — dry_run 默认实现.

    不影响生产 AlphaFactorLibrary, 仅在内存中维护注册/退役状态.
    适合单元测试与 Shadow 模式.
    """

    def __init__(self) -> None:
        self._active: dict[str, FactorCandidate] = {}
        self._retired: dict[str, str] = {}  # name -> reason

    def register(self, candidate: FactorCandidate) -> bool:
        if candidate.name in self._retired:
            logger.warning("因子 %s 已退役, 跳过注册", candidate.name)
            return False
        self._active[candidate.name] = candidate
        return True

    def retire(self, factor_name: str, reason: str) -> bool:
        if factor_name not in self._active:
            return False
        del self._active[factor_name]
        self._retired[factor_name] = reason
        return True

    def list_active(self) -> list[str]:
        return list(self._active.keys())

    def is_registered(self, factor_name: str) -> bool:
        return factor_name in self._active


__all__ = [
    # 数据类
    "FactorCandidate",
    "GateStage",
    "GateStatus",
    "FactorEvaluationResult",
    "ResearchIteration",
    "AutoResearchConfig",
    "ResearchContext",
    # 抽象基类
    "FactorGenerator",
    "FactorEvaluator",
    "FactorGate",
    "FactorRegistry",
    # 主类
    "AutoResearchSkill",
    # 默认实现
    "InMemoryFactorRegistry",
]
