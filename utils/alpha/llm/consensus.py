"""统一多模型共识层 (Multi-Model Consensus Layer)
================================================
从 dual_model_judge.py 抽象的通用共识中间件, 三处复用:
    1. dual_model_judge.py — 风控决策 (cross_validate 模式)
    2. mmr L3 judge — ocr 候选二次验证 (vote 模式)
    3. mmr L4 deep — 文档/数据结论全评审 (vote 模式)

设计原则 (继承自 dual_model_judge.py):
    1. 多模型独立调用: 同一上下文分别喂各模型, 避免互相污染
    2. 结构化输出: 强制 JSON schema
    3. 共识机制: cross_validate (双模型取一致/高置信) | vote (多模型多数表决)
    4. 降级安全: 部分失败 -> 单模型决策; 全失败 -> 规则兜底
    5. 审计可追溯: 记录各模型原始回复 + 共识过程 + 最终结论

用法:
    from utils.alpha.llm.consensus import run_consensus
    result = run_consensus(artifact_text, lens="data-claim",
                            models=["deepseek", "glm"], judge_mode="vote")
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from utils.datetime_utils import now_bj

logger = logging.getLogger("mmr_consensus")


@dataclass
class Finding:
    id: str = ""
    lens: str = ""
    severity: str = "medium"
    title: str = ""
    description: str = ""
    evidence: str = ""
    location: str = ""
    model: str = ""


@dataclass
class Verdict:
    finding_id: str = ""
    votes: list[dict[str, Any]] = field(default_factory=list)
    confirmed_count: int = 0
    total_judges: int = 0
    verdict: str = "dismissed"
    confidence: float = 0.0


@dataclass
class ConsensusResult:
    artifact: str = ""
    lens: str = ""
    discoveries: list[Finding] = field(default_factory=list)
    verdicts: list[Verdict] = field(default_factory=list)
    confirmed_findings: list[Finding] = field(default_factory=list)
    mode: str = ""
    agreement: bool = True
    divergence_reason: str | None = None
    raw_responses: dict[str, str | None] = field(default_factory=dict)
    generated_at: str = ""
    success: bool = False
    error: str | None = None


LENS_PROMPTS: dict[str, str] = {
    "correctness": (
        "你是代码正确性审查专家。审查给定制品的正确性问题: "
        "逻辑错误、边界条件、空值处理、类型安全、并发问题。"
    ),
    "testing": (
        "你是测试覆盖审查专家。审查给定制品的测试缺陷: "
        "缺失边界测试、断言不足、mock 滥用、集成测试缺口。"
    ),
    "adversarial": (
        "你是对抗性审查专家。从攻击者视角审查给定制品: "
        "注入风险、权限提升、数据泄漏、拒绝服务、逻辑绕过。"
    ),
    "data-claim": (
        "你是A股量化交易系统的数据结论审查专家。对回测报告/压力测试/"
        "季度复盘中的统计结论做对抗性审查: "
        "1. 夏普比率是否经 DSR (Deflated Sharpe Ratio) 调整? "
        "2. 回测是否使用 CPCV (Combinatorial Purged Cross-Validation)? "
        "3. 收益率是否经噪声注入稳定性测试? "
        "4. 样本外/内表现差异是否合理 (过拟合信号)? "
        "5. 因子 IC/IR 的 t 值是否 > 2 (统计显著性)? "
        "6. 结论是否与已有知识专题文档矛盾? "
        "仅报告有证据支撑的发现, 不臆测。"
    ),
    "judge": (
        "你是评审裁判。对给定候选发现做独立裁决: "
        "该发现是否有证据支撑? 严重度是否合理? 是否为误报?"
    ),
    "product": (
        "你是产品评审专家。审查 PRD/需求文档: "
        "需求完整性、可测试性、优先级合理性、边界场景覆盖。"
    ),
    "architecture": (
        "你是架构评审专家。审查架构设计: "
        "分层合理性、耦合度、可扩展性、容错设计、性能瓶颈。"
    ),
}

JUDGE_SYSTEM_PROMPT = (
    "你是独立评审裁判。对给定候选发现做严格裁决。必须返回严格JSON:\n"
    "{\n"
    '  "verdict": "confirmed|dismissed",\n'
    '  "confidence": 0.0-1.0,\n'
    '  "severity_adjustment": "保持|升级|降级",\n'
    '  "reason": "裁决理由(<=80字)"\n'
    "}\n"
    "只返回JSON,不要任何其他文字。"
)

DISCOVER_SYSTEM_PROMPT = (
    "你是独立评审专家。审查给定制品并报告发现。必须返回严格JSON:\n"
    "{\n"
    '  "findings": [\n'
    "    {\n"
    '      "id": "F1",\n'
    '      "severity": "critical|high|medium|low",\n'
    '      "title": "问题标题",\n'
    '      "description": "问题描述",\n'
    '      "evidence": "原文证据引用",\n'
    '      "location": "位置"\n'
    "    }\n"
    "  ]\n"
    "}\n"
    '只返回JSON。若无疑似问题,返回 {"findings": []}。'
)


def _parse_json_response(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return cast("dict[str, Any] | None", json.loads(cleaned))
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            try:
                return cast("dict[str, Any] | None", json.loads(match.group(0)))
            except json.JSONDecodeError:
                return None
    return None


class MultiModelConsensus:
    """统一多模型共识层

    Args:
        models: 参与模型列表, 如 ["deepseek", "glm"]
        judge_mode: "cross_validate" (双模型取一致/高置信) | "vote" (多模型多数表决)
        temperature: LLM 温度
        max_tokens: 最大 token
        timeout: 超时秒
    """

    def __init__(
        self,
        models: list[str] | None = None,
        judge_mode: str = "vote",
        temperature: float = 0.3,
        max_tokens: int = 2000,
        timeout: int = 20,
    ) -> None:
        self.models = models or ["deepseek", "glm"]
        self.judge_mode = judge_mode
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._router: Any = None

    def _get_router(self) -> Any:
        if self._router is None:
            from utils.alpha.llm.router import LLMRouter

            self._router = LLMRouter.get_instance()
        return self._router

    def _call_model(self, model: str, prompt: str, system: str) -> str | None:
        # router 初始化也纳入 try: CI/异常环境下 LLMRouter 可能抛异常
        # (例: 配置缺失、依赖不可用), 直接抛会导致 mmr-deep 等观测路径失败
        try:
            router = self._get_router()
            if model == "deepseek":
                # router 动态类 (Any): cast 收窄 LLM 返回为 str|None
                return cast(
                    "str | None",
                    router._call_deepseek(
                        prompt,
                        system,
                        self.temperature,
                        self.max_tokens,
                        self.timeout,
                    ),
                )
            if model == "glm":
                return cast(
                    "str | None",
                    router._call_glm(
                        prompt,
                        system,
                        self.temperature,
                        self.max_tokens,
                        self.timeout,
                    ),
                )
            logger.warning("未知模型: %s, 跳过", model)
            return None
        except (
            ImportError,
            RuntimeError,
            ValueError,
            TypeError,
            AttributeError,
        ) as exc:
            logger.warning("%s 调用失败: %s", model, exc)
            return None

    def discover(
        self,
        artifact: str,
        lens: str = "correctness",
    ) -> tuple[list[Finding], dict[str, str | None]]:
        system = (
            DISCOVER_SYSTEM_PROMPT
            + "\n\n"
            + LENS_PROMPTS.get(lens, LENS_PROMPTS["correctness"])
        )
        user_prompt = f"## 待审查制品\n\n{artifact}\n\n请独立审查并报告发现。"

        findings: list[Finding] = []
        raws: dict[str, str | None] = {}

        for model in self.models:
            raw = self._call_model(model, user_prompt, system)
            raws[model] = raw
            if not raw:
                continue
            parsed = _parse_json_response(raw)
            if not parsed:
                continue
            for f in parsed.get("findings", []):
                findings.append(
                    Finding(
                        id=f.get("id", f"F{len(findings) + 1}"),
                        lens=lens,
                        severity=f.get("severity", "medium"),
                        title=f.get("title", ""),
                        description=f.get("description", ""),
                        evidence=f.get("evidence", ""),
                        location=f.get("location", ""),
                        model=model,
                    )
                )

        return findings, raws

    def judge(self, candidates: list[Finding]) -> list[Verdict]:
        verdicts: list[Verdict] = []

        for cand in candidates:
            votes: list[dict[str, Any]] = []
            user_prompt = (
                f"## 候选发现\n"
                f"- 标题: {cand.title}\n"
                f"- 严重度: {cand.severity}\n"
                f"- 描述: {cand.description}\n"
                f"- 证据: {cand.evidence}\n"
                f"- 位置: {cand.location}\n"
                f"- 来源模型: {cand.model}\n\n"
                f"请独立裁决该发现是否成立。"
            )

            for model in self.models:
                raw = self._call_model(model, user_prompt, JUDGE_SYSTEM_PROMPT)
                if not raw:
                    continue
                parsed = _parse_json_response(raw)
                if not parsed:
                    continue
                votes.append({"model": model, **parsed})

            confirmed = sum(1 for v in votes if v.get("verdict") == "confirmed")
            total = len(votes)

            if self.judge_mode == "cross_validate" and total == 2:
                if confirmed == 2:
                    verdict_str = "confirmed"
                elif confirmed == 0:
                    verdict_str = "dismissed"
                else:
                    confs = [float(v.get("confidence", 0.5)) for v in votes]
                    winner_idx = 0 if confs[0] >= confs[1] else 1
                    verdict_str = votes[winner_idx].get("verdict", "dismissed")
            else:
                threshold = (total // 2) + 1
                verdict_str = "confirmed" if confirmed >= threshold else "dismissed"

            avg_conf = (
                sum(float(v.get("confidence", 0.5)) for v in votes) / total
                if total > 0
                else 0.0
            )

            verdicts.append(
                Verdict(
                    finding_id=cand.id,
                    votes=votes,
                    confirmed_count=confirmed,
                    total_judges=total,
                    verdict=verdict_str,
                    confidence=round(avg_conf, 4),
                )
            )

        return verdicts

    def run(
        self,
        artifact: str,
        lens: str = "correctness",
        candidates: list[Finding] | None = None,
    ) -> ConsensusResult:
        result = ConsensusResult(
            artifact=artifact[:200],
            lens=lens,
            generated_at=now_bj().isoformat(),
        )

        try:
            if candidates is not None:
                discoveries = candidates
                raws: dict[str, str | None] = {}
            else:
                discoveries, raws = self.discover(artifact, lens)

            result.discoveries = discoveries
            result.raw_responses = raws

            if not discoveries:
                result.mode = "no_findings"
                result.success = True
                return result

            verdicts = self.judge(discoveries)
            result.verdicts = verdicts

            confirmed_ids = {v.finding_id for v in verdicts if v.verdict == "confirmed"}
            result.confirmed_findings = [
                f for f in discoveries if f.id in confirmed_ids
            ]

            active_models = [
                m
                for m in self.models
                if raws.get(m) or any(v.total_judges > 0 for v in verdicts)
            ]
            if len(active_models) >= 2:
                result.mode = (
                    "cross_validate" if self.judge_mode == "cross_validate" else "vote"
                )
            elif len(active_models) == 1:
                result.mode = f"single_{active_models[0]}"
            else:
                result.mode = "rule_fallback"

            result.agreement = (
                len(confirmed_ids) == len(verdicts) or len(confirmed_ids) == 0
            )
            if not result.agreement:
                result.divergence_reason = (
                    f"{len(confirmed_ids)}/{len(verdicts)} 候选被 confirmed"
                )
            result.success = True

        except (ValueError, TypeError, RuntimeError, KeyError) as exc:
            result.error = str(exc)
            result.mode = "rule_fallback"
            logger.error("共识层失败: %s", exc)

        return result

    @staticmethod
    def to_dict(result: ConsensusResult) -> dict[str, Any]:
        return {
            "artifact": result.artifact,
            "lens": result.lens,
            "mode": result.mode,
            "agreement": result.agreement,
            "divergence_reason": result.divergence_reason,
            "success": result.success,
            "error": result.error,
            "generated_at": result.generated_at,
            "discoveries_count": len(result.discoveries),
            "confirmed_count": len(result.confirmed_findings),
            "findings": [
                {
                    "id": f.id,
                    "lens": f.lens,
                    "severity": f.severity,
                    "title": f.title,
                    "description": f.description,
                    "evidence": f.evidence,
                    "location": f.location,
                    "model": f.model,
                }
                for f in result.confirmed_findings
            ],
            "verdicts": [
                {
                    "finding_id": v.finding_id,
                    "confirmed_count": v.confirmed_count,
                    "total_judges": v.total_judges,
                    "verdict": v.verdict,
                    "confidence": v.confidence,
                }
                for v in result.verdicts
            ],
        }


def run_consensus(
    artifact: str,
    lens: str = "correctness",
    models: list[str] | None = None,
    judge_mode: str = "vote",
    candidates: list[Finding] | None = None,
    output_path: str | Path | None = None,
) -> ConsensusResult:
    consensus = MultiModelConsensus(
        models=models or ["deepseek", "glm"],
        judge_mode=judge_mode,
    )
    result = consensus.run(artifact, lens=lens, candidates=candidates)

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(
                MultiModelConsensus.to_dict(result), f, ensure_ascii=False, indent=2
            )
        logger.info("共识结果已保存: %s", out)

    return result


def findings_from_ocr(
    ocr_json: dict[str, Any], severity_filter: list[str] | None = None
) -> list[Finding]:
    """从 ocr 产出中提取 Finding 列表 (L3 级联入口)

    Args:
        ocr_json: ocr scan 产出的 JSON (含 comments 列表)
        severity_filter: 仅保留这些严重度, 默认 ["high", "critical"]
    """
    if severity_filter is None:
        severity_filter = ["high", "critical"]

    comments = ocr_json.get("comments", ocr_json if isinstance(ocr_json, list) else [])
    findings: list[Finding] = []
    for i, c in enumerate(comments):
        sev = c.get("severity", "medium")
        if sev not in severity_filter:
            continue
        findings.append(
            Finding(
                id=c.get("id", f"OCR{i + 1}"),
                lens="correctness",
                severity=sev,
                title=c.get("title", c.get("message", "")[:80]),
                description=c.get("message", c.get("description", "")),
                evidence=c.get("evidence", c.get("snippet", "")),
                location=c.get("file", c.get("location", "")),
                model="ocr-glm",
            )
        )
    return findings
