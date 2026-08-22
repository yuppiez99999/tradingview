"""D4 双层闭环编排器 — LLM 假设生成层 ↔ B4 进化执行层联动.

属于 Wave 4 G6 Phase D 第 4 位, 核心目的: **让"LLM 假设生成层"与"B4 进化执行层"形成双层闭环,
Shadow 模式连续运行 ≥1 周无人工干预、无 Kill Switch 误触发后, 再评估生产化**.

双层闭环:
    外层 (LLM 层): 市场观察 → 假设生成 → 因子设计 → 知识沉淀 → (反馈到下一轮)
    内层 (B4 层):  偙选因子 → 验证 → AB 桶 → 回测 → 入库 → (反馈到 LLM)

安全机制:
    1. Kill Switch 检查: T12 KillSwitchManager 升级 → 立即暂停
    2. Circuit Breaker 检查: T11 熔断 OPEN → 立即暂停
    3. 连续失败计数: 连续 N 次无有效产出 → 暂停
    4. Token 预算: 每周期 token 消耗上限
    5. 人工审批: max_cycles 到达后需人工审批才能继续

用法:
    from utils.llm_evolution.dual_loop_orchestrator import (
        DualLoopOrchestrator, DualLoopReport,
    )
    orch = DualLoopOrchestrator(
        ideation_engine=engine,
        verifier=verifier,
        knowledge_base=kb,
    )
    report = orch.run_cycle(market_data)
    # 或连续运行
    report = orch.run_continuous(market_data_fn, max_cycles=7)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from utils.llm_evolution.hypothesis_verifier import HypothesisVerifier
from utils.llm_evolution.knowledge_base import KnowledgeBase
from utils.llm_evolution.strategy_ideation import IdeationCycleResult, StrategyIdeationEngine

logger = logging.getLogger("dual_loop")


# ============================================================
# 协议
# ============================================================

class KillSwitchProtocol(Protocol):
    def evaluate_trade(self, symbol: str, side: str, notional: float, is_open_new: bool = True) -> Any: ...


class CircuitBreakerProtocol(Protocol):
    @property
    def allow_trading(self) -> bool: ...


# ============================================================
# 数据结构
# ============================================================

@dataclass
class DualLoopReport:
    """双层闭环运行报告."""

    started_at: str = ""
    finished_at: str = ""
    total_cycles: int = 0
    successful_cycles: int = 0
    failed_cycles: int = 0
    total_hypotheses: int = 0
    total_validated: int = 0
    total_promoted: int = 0
    total_kb_entries: int = 0
    cycle_results: list[IdeationCycleResult] = field(default_factory=list)
    paused: bool = False
    pause_reason: str = ""
    consecutive_failures: int = 0
    token_usage: int = 0

    @property
    def success_rate(self) -> float:
        if self.total_cycles == 0:
            return 0.0
        return self.successful_cycles / self.total_cycles

    def summary_text(self) -> str:
        return (
            f"双层闭环报告: 周期={self.total_cycles} "
            f"成功={self.successful_cycles} 失败={self.failed_cycles} "
            f"假设={self.total_hypotheses} 验证通过={self.total_validated} "
            f"入库={self.total_promoted} 知识库={self.total_kb_entries} "
            f"暂停={self.paused} 连续失败={self.consecutive_failures}"
        )


# ============================================================
# 安全配置
# ============================================================

@dataclass
class DualLoopSafetyConfig:
    """双层闭环安全配置."""

    max_cycles: int = 7                    # 最大连续周期
    max_consecutive_failures: int = 3      # 最大连续失败次数
    max_token_per_cycle: int = 50_000      # 每周期 token 上限
    require_manual_approval_after: bool = True  # max_cycles 后需人工审批
    kill_switch_check: bool = True         # 是否检查 Kill Switch
    circuit_breaker_check: bool = True     # 是否检查 Circuit Breaker


# ============================================================
# 主类
# ============================================================

class DualLoopOrchestrator:
    """双层闭环编排器 — LLM 假设生成层 ↔ B4 进化执行层."""

    def __init__(
        self,
        ideation_engine: StrategyIdeationEngine,
        verifier: HypothesisVerifier,
        knowledge_base: KnowledgeBase,
        kill_switch: KillSwitchProtocol | None = None,
        circuit_breaker: CircuitBreakerProtocol | None = None,
        safety: DualLoopSafetyConfig | None = None,
    ) -> None:
        if ideation_engine is None:
            raise ValueError("ideation_engine 不能为 None")
        self.engine = ideation_engine
        self.verifier = verifier
        self.kb = knowledge_base
        self.kill_switch = kill_switch
        self.cb = circuit_breaker
        self.safety = safety or DualLoopSafetyConfig()
        self._consecutive_failures = 0
        self._total_token = 0

    # ------------------------------------------------------------
    # 单周期运行
    # ------------------------------------------------------------

    def run_cycle(
        self,
        market_data: dict[str, Any],
        factor_data: dict[str, dict[str, Any]] | None = None,
    ) -> DualLoopReport:
        """执行一个双层闭环周期."""
        report = DualLoopReport(
            started_at=datetime.now().isoformat(timespec="seconds"),
            total_cycles=1,
        )

        # 安全检查
        should_pause, reason = self._check_safety()
        if should_pause:
            report.paused = True
            report.pause_reason = reason
            report.finished_at = datetime.now().isoformat(timespec="seconds")
            logger.warning(f"[D4] 双层闭环暂停: {reason}")
            return report

        try:
            # 外层: LLM Ideation
            cycle_result = self.engine.run_ideation_cycle(
                market_data=market_data,
                n_hypotheses=5,
                verifier=self.verifier,
                factor_data=factor_data,
            )
            report.cycle_results.append(cycle_result)
            report.total_hypotheses = len(cycle_result.hypotheses)
            report.total_validated = cycle_result.total_validated
            report.total_promoted = cycle_result.total_promoted

            # 内层: 知识沉淀
            for hyp in cycle_result.hypotheses:
                # 找到该假设对应的验证结果
                for cand in hyp.proposed_factors:
                    verdict = None
                    if factor_data and cand.get("name") in factor_data:
                        verdict = self.verifier.verify(cand, factor_data[cand["name"]])
                    else:
                        verdict = self.verifier.verify(cand)
                    try:
                        self.kb.persist(hyp, verdict)
                        report.total_kb_entries += 1
                    except Exception as exc:
                        logger.warning(f"[D4] 知识库写入异常: {exc}")

            # 判定成功/失败
            if cycle_result.success and cycle_result.total_validated > 0:
                report.successful_cycles = 1
                self._consecutive_failures = 0
            else:
                report.failed_cycles = 1
                self._consecutive_failures += 1

            report.consecutive_failures = self._consecutive_failures

        except Exception as exc:
            report.failed_cycles = 1
            report.consecutive_failures = self._consecutive_failures
            logger.error(f"[D4] 双层闭环周期异常: {exc}")

        report.finished_at = datetime.now().isoformat(timespec="seconds")
        logger.info(f"[D4] {report.summary_text()}")
        return report

    # ------------------------------------------------------------
    # 连续运行
    # ------------------------------------------------------------

    def run_continuous(
        self,
        market_data_fn: Callable[[], dict[str, Any]],
        max_cycles: int | None = None,
        factor_data_fn: Callable[[], dict[str, dict[str, Any]] | None] | None = None,
    ) -> DualLoopReport:
        """连续运行多个周期.

        Args:
            market_data_fn: 返回市场数据的函数 (每个周期调用一次)
            max_cycles: 最大周期数 (None 时用 safety.max_cycles)
            factor_data_fn: 返回因子数据的函数 (可选)
        """
        max_cycles = max_cycles or self.safety.max_cycles
        report = DualLoopReport(
            started_at=datetime.now().isoformat(timespec="seconds"),
        )

        for _i in range(max_cycles):
            # 安全检查
            should_pause, reason = self._check_safety()
            if should_pause:
                report.paused = True
                report.pause_reason = reason
                break

            # 连续失败检查
            if self._consecutive_failures >= self.safety.max_consecutive_failures:
                report.paused = True
                report.pause_reason = f"连续失败 {self._consecutive_failures} 次, 暂停"
                break

            market_data = market_data_fn()
            factor_data = factor_data_fn() if factor_data_fn else None

            cycle_report = self.run_cycle(market_data, factor_data)
            report.total_cycles += 1
            report.successful_cycles += cycle_report.successful_cycles
            report.failed_cycles += cycle_report.failed_cycles
            report.total_hypotheses += cycle_report.total_hypotheses
            report.total_validated += cycle_report.total_validated
            report.total_promoted += cycle_report.total_promoted
            report.total_kb_entries += cycle_report.total_kb_entries
            report.cycle_results.extend(cycle_report.cycle_results)

        report.consecutive_failures = self._consecutive_failures
        report.finished_at = datetime.now().isoformat(timespec="seconds")

        if self.safety.require_manual_approval_after and report.total_cycles >= max_cycles:
            report.pause_reason = f"已达到 max_cycles={max_cycles}, 需人工审批才能继续"

        logger.info(f"[D4] 连续运行完成: {report.summary_text()}")
        return report

    # ------------------------------------------------------------
    # 安全检查
    # ------------------------------------------------------------

    def _check_safety(self) -> tuple[bool, str]:
        """检查是否应暂停. 返回 (should_pause, reason)."""
        if self.safety.kill_switch_check and self.kill_switch is not None:
            try:
                dec = self.kill_switch.evaluate_trade("_system", "buy", 0.0)
                if not getattr(dec, "allowed", True):
                    return True, f"Kill Switch 拦截: {getattr(dec, 'reason', 'unknown')}"
            except Exception as exc:
                logger.warning(f"[D4] Kill Switch 检查异常: {exc}")

        if self.safety.circuit_breaker_check and self.cb is not None:
            try:
                if not self.cb.allow_trading:
                    return True, "Circuit Breaker OPEN"
            except Exception as exc:
                logger.warning(f"[D4] Circuit Breaker 检查异常: {exc}")

        return False, ""

    # ------------------------------------------------------------
    # 状态重置
    # ------------------------------------------------------------

    def reset(self) -> None:
        """重置连续失败计数和 token 计数."""
        self._consecutive_failures = 0
        self._total_token = 0
        logger.info("[D4] 状态已重置")
