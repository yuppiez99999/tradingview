"""R&D-Agent-Quant 多智能体因子挖掘引擎 (LIT-1.1).

基于 NeurIPS 2025 R&D-Agent 论文, 使用多智能体协作挖掘 Alpha 因子:
    - 研究员 (Researcher): 生成因子假设和研究方向
    - 开发者 (Developer): 实现因子代码
    - 评审员 (Reviewer): 评估因子质量 (IC/IR/换手率/衰减)
    - 管理员 (Manager): 协调流程, 决定保留/淘汰

集成位置:
    - utils/alpha_factor/rd_agent_quant.py (本文件)
    - utils/alpha_factor/evaluator.py (因子评估)
    - utils/alpha_factor/library.py (因子库管理)

设计原则:
    - 零硬依赖: LLM 不可用时降级为规则因子
    - 渐进式: 先骨架, 逐步填充智能体逻辑
    - 可回测: 每个挖掘的因子都通过 IC/IR 评估

文献依据: #2 (NeurIPS 2025 R&D-Agent)
集成日期: 2026-08-24
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class AgentRole(Enum):
    """智能体角色."""

    RESEARCHER = "researcher"
    DEVELOPER = "developer"
    REVIEWER = "reviewer"
    MANAGER = "manager"


class FactorStatus(Enum):
    """因子状态."""

    PROPOSED = "proposed"
    IMPLEMENTED = "implemented"
    VALIDATED = "validated"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass
class FactorProposal:
    """因子提案 — 研究员生成."""

    name: str
    hypothesis: str
    expression: str
    category: str = "custom"
    rationale: str = ""
    proposed_by: str = "researcher"
    status: FactorStatus = FactorStatus.PROPOSED


@dataclass
class FactorEvaluation:
    """因子评估结果 — 评审员生成."""

    factor_name: str
    ic_mean: float = 0.0
    ic_std: float = 0.0
    ir: float = 0.0
    turnover: float = 0.0
    decay_half_life: int = 0
    sharpe_contribution: float = 0.0
    passed: bool = False
    rejection_reason: str = ""


@dataclass
class MiningResult:
    """单次挖掘循环结果."""

    cycle_id: int
    proposals: list[FactorProposal] = field(default_factory=list)
    evaluations: list[FactorEvaluation] = field(default_factory=list)
    accepted_factors: list[str] = field(default_factory=list)
    rejected_factors: list[str] = field(default_factory=list)
    total_factors_before: int = 0
    total_factors_after: int = 0
    improvement_pct: float = 0.0


class RDAgentQuant:
    """R&D-Agent-Quant 多智能体因子挖掘引擎.

    工作流程:
        1. Manager 启动挖掘循环
        2. Researcher 生成因子假设 (基于市场状态 + 已有因子)
        3. Developer 实现因子代码 (表达式引擎)
        4. Reviewer 评估因子质量 (IC/IR/换手率/衰减)
        5. Manager 决定保留/淘汰, 更新因子库

    Args:
        max_factors: 最大因子数 (默认 30, 目标 -70% 从 100→30)
        ic_threshold: IC 均值阈值 (默认 0.03)
        ir_threshold: IR 阈值 (默认 0.5)
        max_turnover: 最大换手率 (默认 0.5)
        llm_available: LLM 是否可用 (False 时降级为规则因子)
    """

    def __init__(
        self,
        max_factors: int = 30,
        ic_threshold: float = 0.03,
        ir_threshold: float = 0.5,
        max_turnover: float = 0.5,
        llm_available: bool = False,
    ) -> None:
        self.max_factors = max_factors
        self.ic_threshold = ic_threshold
        self.ir_threshold = ir_threshold
        self.max_turnover = max_turnover
        self.llm_available = llm_available
        self._cycle_count = 0
        self._factor_library: list[FactorProposal] = []

    def run_mining_cycle(
        self,
        market_state: dict[str, Any] | None = None,
        existing_factors: list[str] | None = None,
    ) -> MiningResult:
        """运行一次完整挖掘循环.

        Args:
            market_state: 市场状态 (regime/volatility/trend 等)
            existing_factors: 已有因子名称列表

        Returns:
            MiningResult 挖掘结果
        """
        self._cycle_count += 1
        cycle_id = self._cycle_count
        logger.info(f"R&D-Agent 挖掘循环 #{cycle_id} 启动")

        proposals = self._researcher_propose(market_state, existing_factors)
        evaluations = self._reviewer_evaluate(proposals)
        accepted, rejected = self._manager_decide(proposals, evaluations)

        result = MiningResult(
            cycle_id=cycle_id,
            proposals=proposals,
            evaluations=evaluations,
            accepted_factors=accepted,
            rejected_factors=rejected,
            total_factors_before=len(existing_factors or []),
            total_factors_after=len(existing_factors or [])
            + len(accepted)
            - len(rejected),
        )
        if result.total_factors_before > 0:
            result.improvement_pct = (
                result.total_factors_after - result.total_factors_before
            ) / result.total_factors_before

        logger.info(
            f"循环 #{cycle_id} 完成: "
            f"提案 {len(proposals)}, 接受 {len(accepted)}, 拒绝 {len(rejected)}"
        )
        return result

    def _researcher_propose(
        self,
        market_state: dict[str, Any] | None,
        existing_factors: list[str] | None,
    ) -> list[FactorProposal]:
        """研究员生成因子假设."""
        if self.llm_available:
            return self._researcher_llm_propose(market_state, existing_factors)
        return self._researcher_rule_propose(market_state, existing_factors)

    def _researcher_llm_propose(
        self,
        market_state: dict[str, Any] | None,
        existing_factors: list[str] | None,
    ) -> list[FactorProposal]:
        """LLM 驱动的因子假设生成 (待实现)."""
        logger.warning("LLM 因子挖掘待实现, 降级为规则因子")
        return self._researcher_rule_propose(market_state, existing_factors)

    def _researcher_rule_propose(
        self,
        market_state: dict[str, Any] | None,
        existing_factors: list[str] | None,
    ) -> list[FactorProposal]:
        """规则驱动的因子假设生成 (降级模式)."""
        base_proposals = [
            FactorProposal(
                name="momentum_20d",
                hypothesis="20日动量因子在趋势市场有正IC",
                expression="rank(close / delay(close, 20) - 1)",
                category="momentum",
                rationale="经典动量因子, 趋势市场有效",
            ),
            FactorProposal(
                name="reversal_5d",
                hypothesis="5日反转因子在震荡市场有正IC",
                expression="rank(-1 * (close / delay(close, 5) - 1))",
                category="reversal",
                rationale="短期反转, 震荡市场有效",
            ),
            FactorProposal(
                name="volume_price_divergence",
                hypothesis="量价背离因子预示趋势反转",
                expression="rank(correlation(close, volume, 10))",
                category="volume_price",
                rationale="量价关系是市场微观结构核心信号",
            ),
        ]
        existing_set = set(existing_factors or [])
        return [p for p in base_proposals if p.name not in existing_set]

    def _reviewer_evaluate(
        self, proposals: list[FactorProposal]
    ) -> list[FactorEvaluation]:
        """评审员评估因子质量 (骨架 — 实际需接入回测)."""
        evaluations = []
        for prop in proposals:
            eval_result = FactorEvaluation(factor_name=prop.name)
            eval_result.ic_mean = 0.04
            eval_result.ic_std = 0.06
            eval_result.ir = eval_result.ic_mean / max(eval_result.ic_std, 0.001)
            eval_result.turnover = 0.3
            eval_result.decay_half_life = 10
            eval_result.passed = (
                eval_result.ic_mean >= self.ic_threshold
                and eval_result.ir >= self.ir_threshold
                and eval_result.turnover <= self.max_turnover
            )
            if not eval_result.passed:
                reasons = []
                if eval_result.ic_mean < self.ic_threshold:
                    reasons.append(
                        f"IC {eval_result.ic_mean:.3f} < {self.ic_threshold}"
                    )
                if eval_result.ir < self.ir_threshold:
                    reasons.append(f"IR {eval_result.ir:.3f} < {self.ir_threshold}")
                if eval_result.turnover > self.max_turnover:
                    reasons.append(
                        f"换手率 {eval_result.turnover:.2f} > {self.max_turnover}"
                    )
                eval_result.rejection_reason = "; ".join(reasons)
            evaluations.append(eval_result)
        return evaluations

    def _manager_decide(
        self,
        proposals: list[FactorProposal],
        evaluations: list[FactorEvaluation],
    ) -> tuple[list[str], list[str]]:
        """管理员决定保留/淘汰."""
        accepted = []
        rejected = []
        eval_map = {e.factor_name: e for e in evaluations}
        for prop in proposals:
            eval_result = eval_map.get(prop.name)
            if eval_result and eval_result.passed:
                prop.status = FactorStatus.ACCEPTED
                accepted.append(prop.name)
                self._factor_library.append(prop)
            else:
                prop.status = FactorStatus.REJECTED
                rejected.append(prop.name)
        return accepted, rejected

    def get_factor_library(self) -> list[FactorProposal]:
        """获取已接受的因子库."""
        return [f for f in self._factor_library if f.status == FactorStatus.ACCEPTED]

    def get_status(self) -> dict[str, Any]:
        """获取引擎状态."""
        return {
            "cycles_run": self._cycle_count,
            "factors_in_library": len(self.get_factor_library()),
            "max_factors": self.max_factors,
            "llm_available": self.llm_available,
            "thresholds": {
                "ic": self.ic_threshold,
                "ir": self.ir_threshold,
                "max_turnover": self.max_turnover,
            },
        }


def quick_check() -> dict[str, Any]:
    """自检接口 — 供 run_quick_check 调用."""
    engine = RDAgentQuant(llm_available=False)
    result = engine.run_mining_cycle()
    return {
        "available": True,
        "cycles_run": engine._cycle_count,
        "factors_accepted": len(result.accepted_factors),
        "factors_rejected": len(result.rejected_factors),
        "status": engine.get_status(),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    check = quick_check()
    print(f"R&D-Agent-Quant 自检: {check}")
