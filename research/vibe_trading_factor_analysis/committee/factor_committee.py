# -*- coding: utf-8 -*-
"""FactorCommittee - Stage 8 多 Agent 决策治理（CIO 视角 v1.0）

5 专家 Agent + Chair 投票审批因子准入生产因子库。
锁定参数（DECISION v1.0）：
  - 5 专家：AlphaAgent / RiskAgent / ExecutionAgent / EconomicAgent / CapacityAgent
  - Chair：聚合 + 平局裁决 + avg<6 强制否决
  - 评分范围：0-10（半分制）
  - 准入平均分：>= 7.0
  - 单 Agent 否决权：有
  - 双重否决（Chair）：avg<6 时强制否决

通过条件：avg_score >= 7.0 AND no_veto AND chair_avg >= 6
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger("factor_committee")
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============== CIO 锁定参数（DECISION v1.0）==============
COMMITTEE_ADMIT_AVG = 7.0           # 准入平均分阈值
CHAIR_VETO_AVG = 6.0                # Chair 强制否决线（avg < 6）
SCORE_STEP = 0.5                     # 半分制


@dataclass
class AgentVote:
    """单 Agent 投票"""
    agent_name: str
    score: float = 0.0              # 0-10
    veto: bool = False              # 否决
    rationale: str = ""             # 评分理由
    evidence: Dict[str, Any] = field(default_factory=dict)  # 评分依据（数值证据）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CommitteeVerdict:
    """委员会决议"""
    factor_name: str
    votes: List[AgentVote] = field(default_factory=list)
    avg_score: float = 0.0
    min_score: float = 0.0
    has_veto: bool = False
    veto_by: List[str] = field(default_factory=list)
    chair_decision: str = ""       # approve / reject / chair_veto
    approved: bool = False
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 5 个专家 Agent
# ============================================================

class AlphaAgent:
    """Alpha 专家 - 验证 IC/IC_IR/DSR 历史

    评分依据：
    - IC_IR 120d：>= 0.5 -> 10; 0.3-0.5 -> 7-9; < 0.3 -> 0-3
    - DSR：> 1.0 -> +; 0.5-1.0 -> ok; < 0.5 -> veto
    """
    name = "AlphaAgent"

    def vote(self, factor_report: Dict[str, Any]) -> AgentVote:
        v = AgentVote(agent_name=self.name)
        ic_ir = float(factor_report.get("ic_ir_120d", 0.0))
        dsr = float(factor_report.get("dsr_value", 0.0))
        ic_decay = float(factor_report.get("ic_decay", 1.0))

        v.evidence = {"ic_ir_120d": ic_ir, "dsr_value": dsr, "ic_decay": ic_decay}

        # 评分
        if ic_ir >= 0.5:
            v.score = 10.0
        elif ic_ir >= 0.4:
            v.score = 9.0
        elif ic_ir >= 0.3:
            v.score = 7.0
        elif ic_ir >= 0.2:
            v.score = 4.0
        else:
            v.score = 1.0

        # IC 衰减惩罚
        if ic_decay < 0.6:
            v.score -= 2.0
            v.evidence["decay_penalty"] = -2.0

        # DSR 否决
        if dsr < 0.5:
            v.veto = True
            v.rationale = f"DSR={dsr:.3f} < 0.5（多重检验不可信）"
        else:
            v.rationale = f"IC_IR={ic_ir:.3f}, DSR={dsr:.3f}, decay={ic_decay:.2f}"

        v.score = max(0.0, min(10.0, round(v.score / SCORE_STEP) * SCORE_STEP))
        return v


class RiskAgent:
    """风险专家 - 验证正交性 + 风险贡献 + 相关性压力测试

    评分依据：
    - max |corr|：< 0.3 -> 10; 0.3-0.5 -> 7-9; >= 0.5 -> veto
    - 风险贡献：< 3% -> +; 3-5% -> ok; > 5% -> 减分
    - 尾部相关性：压力期 corr > 0.8 -> 减分
    """
    name = "RiskAgent"

    def vote(self, factor_report: Dict[str, Any]) -> AgentVote:
        v = AgentVote(agent_name=self.name)
        max_corr = float(factor_report.get("max_abs_corr", 1.0))
        risk_contrib = float(factor_report.get("risk_contribution", 0.0))
        tail_corr = float(factor_report.get("tail_corr_max", 0.0))

        v.evidence = {"max_abs_corr": max_corr, "risk_contribution": risk_contrib, "tail_corr_max": tail_corr}

        if max_corr < 0.3:
            v.score = 10.0
        elif max_corr < 0.5:
            v.score = 8.0
        elif max_corr < 0.7:
            v.score = 5.0
        else:
            v.score = 2.0

        if risk_contrib > 0.05:
            v.score -= 2.0
            v.evidence["risk_contrib_penalty"] = -2.0
        elif risk_contrib < 0.03:
            v.score += 1.0

        if tail_corr > 0.8:
            v.score -= 2.0
            v.evidence["tail_corr_penalty"] = -2.0

        if max_corr >= 0.5:
            v.veto = True
            v.rationale = f"max_corr={max_corr:.3f} >= 0.5（与现有因子共线）"
        else:
            v.rationale = f"max_corr={max_corr:.3f}, risk_contrib={risk_contrib:.3f}, tail_corr={tail_corr:.3f}"

        v.score = max(0.0, min(10.0, round(v.score / SCORE_STEP) * SCORE_STEP))
        return v


class ExecutionAgent:
    """执行专家 - 验证容量 / 换手率 / 滑点

    评分依据：
    - 容量：>= 组合 5% -> 10; 2-5% -> 7-9; < 2% -> veto
    - 换手率：< 30% -> +; 30-70% -> ok; > 70% -> 减分
    - 滑点（bps）：< 5 -> +; 5-15 -> ok; > 15 -> 减分
    """
    name = "ExecutionAgent"

    def vote(self, factor_report: Dict[str, Any]) -> AgentVote:
        v = AgentVote(agent_name=self.name)
        capacity_ratio = float(factor_report.get("capacity_ratio", 0.0))  # 容量 / 组合价值
        turnover = float(factor_report.get("turnover", 0.0))
        slippage_bps = float(factor_report.get("slippage_bps", 0.0))

        v.evidence = {"capacity_ratio": capacity_ratio, "turnover": turnover, "slippage_bps": slippage_bps}

        if capacity_ratio >= 0.05:
            v.score = 10.0
        elif capacity_ratio >= 0.03:
            v.score = 8.0
        elif capacity_ratio >= 0.02:
            v.score = 7.0
        else:
            v.score = 3.0

        if turnover > 0.7:
            v.score -= 2.0
        elif turnover < 0.3:
            v.score += 1.0

        if slippage_bps > 15:
            v.score -= 2.0

        if capacity_ratio < 0.02:
            v.veto = True
            v.rationale = f"capacity={capacity_ratio:.3f} < 2%（容量不足）"
        else:
            v.rationale = f"capacity={capacity_ratio:.3f}, turnover={turnover:.2f}, slippage={slippage_bps:.1f}bps"

        v.score = max(0.0, min(10.0, round(v.score / SCORE_STEP) * SCORE_STEP))
        return v


class EconomicAgent:
    """经济逻辑专家 - 验证学术依据 + A股适配

    评分依据：
    - 经济逻辑评分（0-10）：来自 Gate4 输出
    - A股适配：1.0 完美 / 0.7 适配 / 0.5 部分 / 0.3 借鉴
    - 学术依据：有顶刊论文 -> +; 仅有研报 -> 中; 无依据 -> veto
    """
    name = "EconomicAgent"

    def vote(self, factor_report: Dict[str, Any]) -> AgentVote:
        v = AgentVote(agent_name=self.name)
        econ_score = float(factor_report.get("economic_logic_score", 0.0))  # 0-10
        a_share_fit = float(factor_report.get("a_share_fit", 0.0))  # 0-1
        has_paper = bool(factor_report.get("has_academic_paper", False))

        v.evidence = {"econ_score": econ_score, "a_share_fit": a_share_fit, "has_paper": has_paper}

        # 基础分 = 经济逻辑分 × A股适配度（0-10 * 0-1 = 0-10）
        v.score = econ_score * a_share_fit
        if has_paper:
            v.score = min(10.0, v.score + 1.0)

        if econ_score < 5.0:
            v.veto = True
            v.rationale = f"econ_score={econ_score:.1f} < 5（经济逻辑不充分）"
        elif a_share_fit < 0.3:
            v.veto = True
            v.rationale = f"a_share_fit={a_share_fit:.2f} < 0.3（A股不适用）"
        else:
            v.rationale = f"econ={econ_score:.1f}, a_share_fit={a_share_fit:.2f}, paper={has_paper}"

        v.score = max(0.0, min(10.0, round(v.score / SCORE_STEP) * SCORE_STEP))
        return v


class CapacityAgent:
    """容量/Regime 专家 - 验证 regime 全场景有效性

    评分依据：
    - min_regime_ic_ir：>= 0.5 -> 10; 0.3-0.5 -> 7-9; 0.2-0.3 -> 5-6; < 0.2 -> veto
    - weakest_regime 标注

    P2.2 v6.2d 改进：对"样本不足"的 regime 不触发 veto
    - 当 weakest_regime == "insufficient_samples" 时，说明某些 regime 样本数 < 5
    - 这种情况下 IC_IR 统计不可靠，不应作为 veto 依据
    - 改为给中性评分 5.0，记录观察理由

    P2.2 v6.2e 改进：当有效 regime 数 < 2 时不 veto
    - 当只有 1 个 regime 有足够样本（如只有 choppy，bull/bear 样本不足）
    - 无法判断全 regime 普适性，不应基于单一 regime 的 IC_IR 否决因子
    - 改为给中性评分 5.0，记录"单 regime 验证不足"理由
    """
    name = "CapacityAgent"

    def vote(self, factor_report: Dict[str, Any]) -> AgentVote:
        v = AgentVote(agent_name=self.name)
        min_regime_ic_ir = float(factor_report.get("min_regime_ic_ir", 0.0))
        weakest = str(factor_report.get("weakest_regime", "unknown"))
        regime_tag = str(factor_report.get("regime_tag", "none"))
        n_regimes_pass = int(factor_report.get("n_regimes_pass", 0))
        # P2.2 v6.2e：有效 regime 数（样本数 >= 5 的 regime 数量）
        n_valid_regimes = int(factor_report.get("n_valid_regimes", 0))
        per_regime_samples = factor_report.get("per_regime_samples", {}) or {}

        v.evidence = {
            "min_regime_ic_ir": min_regime_ic_ir,
            "weakest_regime": weakest,
            "regime_tag": regime_tag,
            "n_regimes_pass": n_regimes_pass,
            "n_valid_regimes": n_valid_regimes,
            "per_regime_samples": per_regime_samples,
        }

        # P2.2 v6.2d：所有 regime 样本不足时不 veto，给中性评分
        if weakest == "insufficient_samples":
            v.score = 5.0
            v.veto = False
            v.rationale = (
                "regime 样本不足（所有 regime < 5 天），IC_IR 统计不可靠，"
                "给予中性评分 5.0 观察期"
            )
            v.score = max(0.0, min(10.0, round(v.score / SCORE_STEP) * SCORE_STEP))
            return v

        # P2.2 v6.2e：有效 regime 数 < 2 时不 veto，给中性评分
        # 依据：当只有 1 个 regime 有足够样本时（如只有 choppy 55 天，bull 4 天 bear 1 天），
        # 无法判断全 regime 普适性，不应基于单一 regime 的 IC_IR 否决因子
        # 典型场景：QualityTrend 季度频率因子在 120 天窗口内，bull/bear regime 可能样本不足
        if n_valid_regimes < 2 and n_valid_regimes > 0:
            v.score = 5.0
            v.veto = False
            v.rationale = (
                f"有效 regime 数={n_valid_regimes} < 2（{per_regime_samples}），"
                f"无法判断全 regime 普适性，给予中性评分 5.0 观察期"
            )
            v.score = max(0.0, min(10.0, round(v.score / SCORE_STEP) * SCORE_STEP))
            return v

        if min_regime_ic_ir >= 0.5:
            v.score = 10.0
        elif min_regime_ic_ir >= 0.4:
            v.score = 9.0
        elif min_regime_ic_ir >= 0.3:
            v.score = 7.0
        elif min_regime_ic_ir >= 0.2:
            v.score = 5.0
        else:
            v.score = 2.0

        # 全 regime 通过加 1 分
        if regime_tag == "all_regime":
            v.score = min(10.0, v.score + 1.0)

        if min_regime_ic_ir < 0.2 and regime_tag != "all_regime":
            v.veto = True
            v.rationale = f"min_regime_ic_ir={min_regime_ic_ir:.3f} < 0.2（regime 不普适）"
        else:
            v.rationale = f"min_ic_ir={min_regime_ic_ir:.3f}, weakest={weakest}, tag={regime_tag}"

        v.score = max(0.0, min(10.0, round(v.score / SCORE_STEP) * SCORE_STEP))
        return v


# ============================================================
# Chair Agent（聚合 + 平局裁决 + 双重否决）
# ============================================================

class ChairAgent:
    """Chair - 聚合 5 专家评分，做出最终决议

    规则（DECISION v1.0）：
    - avg_score >= 7.0 AND no_veto -> approve
    - avg_score < 6.0 -> chair_veto（强制否决）
    - 其他 -> reject
    """
    name = "ChairAgent"
    ADMIT_AVG = COMMITTEE_ADMIT_AVG
    CHAIR_VETO_AVG_LINE = CHAIR_VETO_AVG

    def aggregate(self, factor_name: str, votes: List[AgentVote]) -> CommitteeVerdict:
        v = CommitteeVerdict(factor_name=factor_name, votes=votes)
        if not votes:
            v.chair_decision = "reject"
            v.rationale = "无投票"
            return v

        scores = [vv.score for vv in votes]
        v.avg_score = float(np.mean(scores))
        v.min_score = float(min(scores))
        v.has_veto = any(vv.veto for vv in votes)
        v.veto_by = [vv.agent_name for vv in votes if vv.veto]

        # Chair 决策
        if v.avg_score < self.CHAIR_VETO_AVG_LINE:
            v.chair_decision = "chair_veto"
            v.approved = False
            v.rationale = (
                f"Chair 强制否决：avg={v.avg_score:.2f} < {self.CHAIR_VETO_AVG_LINE}"
            )
        elif v.has_veto:
            v.chair_decision = "reject"
            v.approved = False
            v.rationale = f"被否决（专家否决：{', '.join(v.veto_by)}）"
        elif v.avg_score >= self.ADMIT_AVG:
            v.chair_decision = "approve"
            v.approved = True
            v.rationale = f"准入通过：avg={v.avg_score:.2f} >= {self.ADMIT_AVG}"
        else:
            v.chair_decision = "reject"
            v.approved = False
            v.rationale = (
                f"评分不足：avg={v.avg_score:.2f} < {self.ADMIT_AVG}（{self.CHAIR_VETO_AVG_LINE}-{self.ADMIT_AVG} 区间观察）"
            )

        logger.info(
            "[Chair] %s | avg=%.2f min=%.1f veto=%s -> %s",
            factor_name, v.avg_score, v.min_score, v.has_veto, v.chair_decision,
        )
        return v


# ============================================================
# FactorCommittee - 编排 5 专家 + Chair
# ============================================================

class FactorCommittee:
    """因子委员会 - 多 Agent 决策治理（Stage 8）

    用法：
        >>> committee = FactorCommittee()
        >>> verdict = committee.review(
        ...     factor_name="VT_MOM_ILLIQUID_60D",
        ...     factor_report={...}  # 含 ic_ir_120d, dsr_value, max_abs_corr, ...
        ... )
        >>> if verdict.approved:
        ...     # 进入生产因子库
        ...     pass
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        c = config or {}
        self.alpha_agent = AlphaAgent()
        self.risk_agent = RiskAgent()
        self.execution_agent = ExecutionAgent()
        self.economic_agent = EconomicAgent()
        self.capacity_agent = CapacityAgent()
        self.chair = ChairAgent()
        self.agents = [
            self.alpha_agent, self.risk_agent, self.execution_agent,
            self.economic_agent, self.capacity_agent,
        ]
        logger.info(
            "[FactorCommittee] 初始化 | 5 专家 + Chair | admit_avg=%.1f chair_veto<%.1f",
            self.chair.ADMIT_AVG, self.chair.CHAIR_VETO_AVG_LINE,
        )

    def review(self, factor_name: str, factor_report: Dict[str, Any]) -> CommitteeVerdict:
        """委员会评审单个因子

        Args:
            factor_name: 因子名称
            factor_report: 因子报告（含 ic_ir_120d, dsr_value, max_abs_corr,
                          capacity_ratio, economic_logic_score, min_regime_ic_ir 等）

        Returns:
            CommitteeVerdict
        """
        votes = []
        for agent in self.agents:
            try:
                vote = agent.vote(factor_report)  # type: ignore
                votes.append(vote)
                logger.info(
                    "[Committee] %s | %s score=%.1f veto=%s | %s",
                    factor_name, agent.name, vote.score, vote.veto, vote.rationale,  # type: ignore
                )
            except Exception as e:
                logger.error("[Committee] %s | %s 评分失败：%s", factor_name, agent.name, e)  # type: ignore
                error_vote = AgentVote(
                    agent_name=agent.name, score=0.0, veto=True,  # type: ignore
                    rationale=f"评分异常：{type(e).__name__}: {e}",
                )
                votes.append(error_vote)

        return self.chair.aggregate(factor_name, votes)

    def review_batch(
        self, factor_reports: Dict[str, Dict[str, Any]]
    ) -> Dict[str, CommitteeVerdict]:
        """批量评审"""
        return {name: self.review(name, report) for name, report in factor_reports.items()}


# ============================================================
# 便捷函数
# ============================================================

def quick_review(factor_name: str, factor_report: Dict[str, Any]) -> CommitteeVerdict:
    """快速委员会评审（使用默认配置）"""
    committee = FactorCommittee()
    return committee.review(factor_name, factor_report)
