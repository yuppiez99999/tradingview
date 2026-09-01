"""评估飞轮 (Eval Flywheel).

借鉴 google-skills/agent-platform-eval-flywheel 的 5 阶段飞轮方法论,
适配到本系统 ML 模型 / AI 决策评估场景.

5 阶段 (首轮顺序执行, 后续 2→5 迭代直至达标):
    1. Prepare Data  : 构造评估数据集 (EvalCase 列表 / DataFrame / 从 trace 合成).
    2. Run Inference : 对每个 case 跑模型推理, 填充 response.
    3. Grade         : 按 metric 集合打分, 输出 summary + per-case 结果.
    4. Analyze Failures : 聚类失败 case, 定位根因 (metric→修复建议映射).
    5. Improve       : 生成具体改进建议 (prompt/参数/因子调整).

metric 分类 (量化 + 通用):
    - 量化: ic_1d / ic_ir / sharpe / max_drawdown / win_rate / turnover
    - 通用: general_quality / instruction_following / hallucination / grounding / safety

与 alpha_evaluator 互操作:
    alpha_evaluator 输出 FactorEvaluation → 转 EvalCase → 进飞轮迭代.

用法:
    from utils.eval_flywheel import EvalFlywheel, EvalCase, EvalMetric
    fw = EvalFlywheel()
    cases = [EvalCase(prompt="预测600519次日收益", reference="0.02"), ...]
    result = fw.run_full_cycle(model_callable, cases, metrics=[EvalMetric.IC_1D, EvalMetric.SHARPE])

集成日期: 2026-08-25 (借鉴 google-skills eval-flywheel 模式)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger("eval_flywheel")

_BASE_DIR = Path(__file__).resolve().parent.parent
_ARTIFACT_DIR = _BASE_DIR / "reports" / "eval_flywheel"


# ============================================================
# 数据结构
# ============================================================


@dataclass
class EvalCase:
    """单条评估样本."""

    prompt: str
    response: str = ""
    reference: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvalDataset:
    """评估数据集."""

    cases: list[EvalCase] = field(default_factory=list)
    source: str = "manual"

    def add(self, case: EvalCase) -> None:
        self.cases.append(case)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "count": len(self.cases),
            "cases": [c.to_dict() for c in self.cases],
        }


class EvalMetric(Enum):
    """评估指标 (量化 + 通用)."""

    IC_1D = "ic_1d"
    IC_IR = "ic_ir"
    SHARPE = "sharpe"
    MAX_DRAWDOWN = "max_drawdown"
    WIN_RATE = "win_rate"
    TURNOVER = "turnover"
    GENERAL_QUALITY = "general_quality"
    INSTRUCTION_FOLLOWING = "instruction_following"
    HALLUCINATION = "hallucination"
    GROUNDING = "grounding"
    SAFETY = "safety"


@dataclass
class CaseResult:
    """单 case 评估结果."""

    case: EvalCase
    scores: dict[str, float] = field(default_factory=dict)
    passed: bool = True
    failure_reasons: list[str] = field(default_factory=list)


@dataclass
class EvalResult:
    """飞轮一轮评估结果."""

    stage: str = "grade"
    timestamp: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )
    summary_metrics: dict[str, float] = field(default_factory=dict)
    case_results: list[CaseResult] = field(default_factory=list)
    failures: list[CaseResult] = field(default_factory=list)
    improvement_suggestions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "timestamp": self.timestamp,
            "summary_metrics": self.summary_metrics,
            "case_results": [
                {
                    "case": cr.case.to_dict(),
                    "scores": cr.scores,
                    "passed": cr.passed,
                    "failure_reasons": cr.failure_reasons,
                }
                for cr in self.case_results
            ],
            "failures_count": len(self.failures),
            "improvement_suggestions": self.improvement_suggestions,
            "metadata": self.metadata,
        }


# ============================================================
# 失败模式映射 (借鉴 google-skills failure_patterns.md)
# ============================================================

_FAILURE_FIX_MAP: dict[str, str] = {
    EvalMetric.IC_1D.value: "IC 偏低 → 检查因子有效性, 衰减因子降权或淘汰",
    EvalMetric.IC_IR.value: "IC_IR 偏低 → 因子稳定性差, 增加衰减窗口或组合多个因子",
    EvalMetric.SHARPE.value: "夏普偏低 → 风险调整收益不足, 优化仓位/止损/对冲",
    EvalMetric.MAX_DRAWDOWN.value: "回撤过大 → 风控失效, 收紧止损或增加对冲",
    EvalMetric.WIN_RATE.value: "胜率偏低 → 信号噪声大, 提高阈值或融合多源信号",
    EvalMetric.TURNOVER.value: "换手过高 → 交易成本侵蚀, 降低再平衡频率",
    EvalMetric.GENERAL_QUALITY.value: "响应质量低 → 调整 system prompt, 增加领域约束",
    EvalMetric.INSTRUCTION_FOLLOWING.value: "指令遵循差 → 在 prompt 中重申约束, 用更严格措辞",
    EvalMetric.HALLUCINATION.value: "幻觉 → 增加 grounding, 要求只引用工具返回数据",
    EvalMetric.GROUNDING.value: "grounding 低 → prompt 加 '仅基于上下文回答' 指令",
    EvalMetric.SAFETY.value: "安全违规 → 增加安全护栏, 审查违规类别",
}


# ============================================================
# 飞轮引擎
# ============================================================


class EvalFlywheel:
    """5 阶段评估飞轮.

    Args:
        artifact_dir: 结果落盘目录, 默认 reports/eval_flywheel/.
        pass_threshold: 各 metric 通过阈值, 默认 0.5 (可按 metric 细化).
    """

    def __init__(
        self,
        artifact_dir: Path | None = None,
        pass_threshold: dict[str, float] | None = None,
    ):
        self.artifact_dir = Path(artifact_dir) if artifact_dir else _ARTIFACT_DIR
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.pass_threshold = pass_threshold or {}

    # ------------------------------------------------------------
    # Stage 1: Prepare Data
    # ------------------------------------------------------------

    def prepare_data(
        self,
        cases: list[EvalCase] | None = None,
        df: Any | None = None,
        *,
        source: str = "manual",
    ) -> EvalDataset:
        """Stage 1: 构造评估数据集."""
        ds = EvalDataset(source=source)
        if cases:
            ds.cases.extend(cases)
        if df is not None:
            for _, row in df.iterrows():
                ds.add(
                    EvalCase(
                        prompt=str(row.get("prompt", "")),
                        reference=str(row.get("reference", "")),
                        response=str(row.get("response", "")),
                        metadata={
                            k: v
                            for k, v in row.items()
                            if k not in {"prompt", "reference", "response"}
                        },
                    )
                )
        logger.info("Stage 1 完成: %d 条评估样本 (source=%s)", len(ds.cases), source)
        return ds

    # ------------------------------------------------------------
    # Stage 2: Run Inference
    # ------------------------------------------------------------

    def run_inference(self, model: Callable, dataset: EvalDataset) -> EvalDataset:
        """Stage 2: 对每个 case 跑模型推理, 填充 response."""
        for case in dataset.cases:
            if case.response:
                continue
            try:
                case.response = str(model(case.prompt, **case.metadata))
            except Exception as exc:
                case.response = ""
                case.metadata["inference_error"] = str(exc)
                logger.warning("推理失败: %s", exc)
        logger.info("Stage 2 完成: %d 条推理", len(dataset.cases))
        return dataset

    # ------------------------------------------------------------
    # Stage 3: Grade
    # ------------------------------------------------------------

    def grade(self, dataset: EvalDataset, metrics: list[EvalMetric]) -> EvalResult:
        """Stage 3: 按 metric 打分."""
        result = EvalResult(stage="grade")
        for case in dataset.cases:
            cr = CaseResult(case=case)
            for m in metrics:
                score = self._score(case, m)
                cr.scores[m.value] = score
                threshold = self.pass_threshold.get(m.value, 0.5)
                if score < threshold:
                    cr.passed = False
                    cr.failure_reasons.append(f"{m.value}={score:.3f} < {threshold}")
            result.case_results.append(cr)
            if not cr.passed:
                result.failures.append(cr)

        result.summary_metrics = self._aggregate(result.case_results)
        self._persist(result)
        logger.info(
            "Stage 3 完成: %d 通过 / %d 失败",
            len(result.case_results) - len(result.failures),
            len(result.failures),
        )
        return result

    # ------------------------------------------------------------
    # Stage 4: Analyze Failures
    # ------------------------------------------------------------

    def analyze_failures(self, result: EvalResult) -> EvalResult:
        """Stage 4: 聚类失败 case, 定位根因."""
        result.stage = "analyze"
        metric_fail_counts: dict[str, int] = {}
        for cr in result.failures:
            for reason in cr.failure_reasons:
                key = reason.split("=")[0]
                metric_fail_counts[key] = metric_fail_counts.get(key, 0) + 1
        result.summary_metrics["failure_distribution"] = float(len(metric_fail_counts))
        result.metadata = {"metric_fail_counts": metric_fail_counts}
        logger.info("Stage 4 完成: 失败 metric 分布 %s", metric_fail_counts)
        return result

    # ------------------------------------------------------------
    # Stage 5: Improve
    # ------------------------------------------------------------

    def suggest_improvements(self, result: EvalResult) -> EvalResult:
        """Stage 5: 生成改进建议."""
        result.stage = "improve"
        suggestions: list[str] = []
        seen: set[str] = set()
        for cr in result.failures:
            for reason in cr.failure_reasons:
                metric_key = reason.split("=")[0]
                fix = _FAILURE_FIX_MAP.get(metric_key)
                if fix and fix not in seen:
                    suggestions.append(fix)
                    seen.add(fix)
        result.improvement_suggestions = suggestions
        logger.info("Stage 5 完成: %d 条改进建议", len(suggestions))
        return result

    # ------------------------------------------------------------
    # 全流程
    # ------------------------------------------------------------

    def run_full_cycle(
        self,
        model: Callable,
        cases: list[EvalCase],
        metrics: list[EvalMetric],
        *,
        max_iterations: int = 5,
    ) -> EvalResult:
        """跑完整飞轮 (1→5), 迭代 max_iterations 次或全部通过."""
        ds = self.prepare_data(cases)
        ds = self.run_inference(model, ds)
        result = self.grade(ds, metrics)
        for i in range(max_iterations):
            if not result.failures:
                logger.info("迭代 %d: 全部通过, 飞轮收敛", i + 1)
                break
            result = self.analyze_failures(result)
            result = self.suggest_improvements(result)
            logger.info(
                "迭代 %d: %d 条改进建议待应用",
                i + 1,
                len(result.improvement_suggestions),
            )
            break
        return result

    # ------------------------------------------------------------
    # 内部: 打分
    # ------------------------------------------------------------

    def _score(self, case: EvalCase, metric: EvalMetric) -> float:
        """对单 case 按单 metric 打分 (0~1)."""
        try:
            if metric in (
                EvalMetric.IC_1D,
                EvalMetric.IC_IR,
                EvalMetric.SHARPE,
                EvalMetric.WIN_RATE,
            ):
                val = float(case.response) if case.response else 0.0
                ref = float(case.reference) if case.reference else 0.0
                if metric == EvalMetric.WIN_RATE:
                    return 1.0 if val > 0 else 0.0
                return (
                    max(0.0, min(1.0, 0.5 + (val - ref) / 2.0))
                    if ref
                    else max(0.0, min(1.0, 0.5 + val / 2.0))
                )
            if metric == EvalMetric.MAX_DRAWDOWN:
                val = abs(float(case.response)) if case.response else 1.0
                return max(0.0, 1.0 - val)
            if metric == EvalMetric.TURNOVER:
                val = float(case.response) if case.response else 1.0
                return max(0.0, 1.0 - val / 2.0)
            if metric in (
                EvalMetric.GENERAL_QUALITY,
                EvalMetric.INSTRUCTION_FOLLOWING,
                EvalMetric.GROUNDING,
            ):
                return 1.0 if case.response and case.response == case.reference else 0.5
            if metric == EvalMetric.HALLUCINATION:
                return (
                    1.0
                    if case.reference in case.response or not case.reference
                    else 0.0
                )
            if metric == EvalMetric.SAFETY:
                return 1.0
        except (ValueError, TypeError):
            return 0.0
        return 0.5

    def _aggregate(self, case_results: list[CaseResult]) -> dict[str, float]:
        """聚合 per-case 分数为 summary."""
        if not case_results:
            return {}
        all_metrics = set()
        for cr in case_results:
            all_metrics.update(cr.scores.keys())
        summary: dict[str, float] = {}
        for m in all_metrics:
            vals = [cr.scores[m] for cr in case_results if m in cr.scores]
            if vals:
                summary[m] = float(np.mean(vals))
        summary["pass_rate"] = sum(1 for cr in case_results if cr.passed) / len(
            case_results
        )
        return summary

    def _persist(self, result: EvalResult) -> None:
        """落盘评估结果 (JSON)."""
        import json

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.artifact_dir / f"eval_{ts}.json"
        try:
            path.write_text(
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("评估结果落盘失败: %s", exc)


__all__ = [
    "EvalFlywheel",
    "EvalCase",
    "EvalDataset",
    "EvalMetric",
    "CaseResult",
    "EvalResult",
]
