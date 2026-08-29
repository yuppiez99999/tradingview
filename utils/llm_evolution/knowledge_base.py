"""D3 知识沉淀库 — 已验证/已证伪假设 JSONL 持久化 + LLM 上下文反馈.

属于 Wave 4 G6 Phase D 第 3 位, 核心目的: **沉淀已验证/已证伪的假设与归因结论,
反馈给 LLM 作为下一轮 Ideation 上下文, 形成"学到的知识越多 → 新假设质量越高"的正反馈**.

持久化: reports/evolution/knowledge_base.jsonl (JSONL 追加写入)
查询: 按状态/日期/因子名/风格 过滤
反馈: load_context_for_ideation() 返回最近 N 条已验证假设摘要

用法:
    from utils.llm_evolution.knowledge_base import KnowledgeBase, KnowledgeEntry
    kb = KnowledgeBase()
    kb.persist(hypothesis, verdict)
    ctx = kb.load_context_for_ideation()
    stats = kb.stats()
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("knowledge_base")


# ============================================================
# 数据结构
# ============================================================


@dataclass
class KnowledgeEntry:
    """知识库单条记录."""

    entry_id: str = ""
    timestamp: str = ""
    hypothesis_id: str = ""
    description: str = ""
    factor_name: str = ""
    strategy_style: str = ""
    factor_direction: str = ""

    # 验证结果
    status: str = "pending"  # validated / falsified / promoted
    rank_ic_mean: float = 0.0
    icir: float = 0.0
    ic_positive_ratio: float = 0.0
    falsified_reason: str = ""

    # 归因
    attribution: str = ""  # 为什么成功/失败的自然语言解释
    lessons: str = ""  # 经验教训

    # 元数据
    llm_model: str = ""
    cycle_id: str = ""

    def to_jsonl(self) -> str:
        """转为 JSONL 行."""
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_jsonl(cls, line: str) -> KnowledgeEntry | None:
        """从 JSONL 行解析."""
        try:
            data = json.loads(line.strip())
            return cls(**{k: data[k] for k in data if k in cls.__dataclass_fields__})
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(f"[D3] JSONL 解析失败: {exc}")
            return None


# ============================================================
# 主类
# ============================================================


class KnowledgeBase:
    """知识沉淀库 — JSONL 持久化 + 查询 + LLM 上下文反馈."""

    def __init__(
        self,
        path: str | Path | None = None,
        max_context_entries: int = 20,
    ) -> None:
        if path is None:
            path = Path("reports/evolution/knowledge_base.jsonl")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_context_entries = max_context_entries

    # ------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------

    def persist(
        self, hypothesis: Any, verdict: dict[str, Any] | None = None
    ) -> KnowledgeEntry:
        """将假设和验证结果沉淀到知识库.

        Args:
            hypothesis: Hypothesis 对象 (或有相同属性的对象)
            verdict: D2 验证结果 dict (可为 None)
        """
        verdict = verdict or {}
        entry = KnowledgeEntry(
            entry_id=f"kb_{datetime.now().strftime('%Y%m%d%H%M%S')}_{hypothesis.id[-6:] if hasattr(hypothesis, 'id') else 'unknown'}",
            timestamp=datetime.now().isoformat(timespec="seconds"),
            hypothesis_id=getattr(hypothesis, "id", ""),
            description=getattr(hypothesis, "description", ""),
            factor_name=verdict.get("factor_name", ""),
            strategy_style=getattr(hypothesis, "strategy_style", ""),
            factor_direction=getattr(hypothesis, "factor_direction", ""),
            status="validated" if verdict.get("enter_ab_bucket") else "falsified",
            rank_ic_mean=verdict.get("rank_ic_mean", 0.0),
            icir=verdict.get("icir", 0.0),
            ic_positive_ratio=verdict.get("ic_positive_ratio", 0.0),
            falsified_reason=verdict.get("falsified_reason", ""),
            attribution=self._generate_attribution(hypothesis, verdict),
            lessons=self._extract_lessons(verdict),
            llm_model=getattr(hypothesis, "llm_model", ""),
            cycle_id=getattr(hypothesis, "cycle_id", ""),
        )

        with open(self.path, "a", encoding="utf-8") as f:
            f.write(entry.to_jsonl() + "\n")

        logger.info(f"[D3] 知识库写入: {entry.entry_id} status={entry.status}")
        return entry

    def persist_entry(self, entry: KnowledgeEntry) -> None:
        """直接写入已构造的 KnowledgeEntry."""
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(entry.to_jsonl() + "\n")

    # ------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------

    def load_all(self) -> list[KnowledgeEntry]:
        """加载全部记录."""
        if not self.path.exists():
            return []
        entries: list[KnowledgeEntry] = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    entry = KnowledgeEntry.from_jsonl(line)
                    if entry is not None:
                        entries.append(entry)
        return entries

    def query(
        self,
        status: str | None = None,
        factor_name: str | None = None,
        strategy_style: str | None = None,
        since_date: str | None = None,
    ) -> list[KnowledgeEntry]:
        """条件查询."""
        entries = self.load_all()
        result = entries
        if status:
            result = [e for e in result if e.status == status]
        if factor_name:
            result = [e for e in result if e.factor_name == factor_name]
        if strategy_style:
            result = [e for e in result if e.strategy_style == strategy_style]
        if since_date:
            result = [e for e in result if e.timestamp >= since_date]
        return result

    # ------------------------------------------------------------
    # LLM 上下文反馈
    # ------------------------------------------------------------

    def load_context_for_ideation(self) -> str:
        """加载最近已验证假设作为 LLM 下一轮 Ideation 上下文.

        返回自然语言摘要, 告诉 LLM "哪些假设已验证/已证伪, 避免重复".
        """
        validated = self.query(status="validated")
        falsified = self.query(status="falsified")

        # 取最近 N 条
        recent_validated = validated[-self.max_context_entries :]
        recent_falsified = falsified[-self.max_context_entries // 2 :]

        lines: list[str] = []
        if recent_validated:
            lines.append("已验证假设 (可参考扩展):")
            for e in recent_validated:
                lines.append(
                    f"  - {e.description[:80]} (IC={e.rank_ic_mean:.4f}, ICIR={e.icir:.2f})"
                )

        if recent_falsified:
            lines.append("已证伪假设 (避免重复):")
            for e in recent_falsified:
                lines.append(f"  - {e.description[:80]} (原因: {e.falsified_reason})")

        return "\n".join(lines) if lines else ""

    # ------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """知识库统计."""
        entries = self.load_all()
        if not entries:
            return {"total": 0}

        validated = [e for e in entries if e.status == "validated"]
        falsified = [e for e in entries if e.status == "falsified"]
        promoted = [e for e in entries if e.status == "promoted"]

        # 按风格统计
        by_style: dict[str, int] = {}
        for e in entries:
            by_style[e.strategy_style] = by_style.get(e.strategy_style, 0) + 1

        # 唯一因子
        unique_factors = set(e.factor_name for e in entries if e.factor_name)

        return {
            "total": len(entries),
            "validated": len(validated),
            "falsified": len(falsified),
            "promoted": len(promoted),
            "validation_rate": len(validated) / len(entries) if entries else 0.0,
            "unique_factors": len(unique_factors),
            "by_style": by_style,
            "first_entry": entries[0].timestamp if entries else "",
            "last_entry": entries[-1].timestamp if entries else "",
        }

    # ------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------

    def _generate_attribution(self, hypothesis: Any, verdict: dict[str, Any]) -> str:
        """生成归因解释."""
        if verdict.get("enter_ab_bucket"):
            return (
                f"假设'{getattr(hypothesis, 'description', '')[:50]}'验证成功, "
                f"因子{verdict.get('factor_name', '')} IC={verdict.get('rank_ic_mean', 0):.4f}, "
                f"ICIR={verdict.get('icir', 0):.2f}, 进入 AB 桶"
            )
        return (
            f"假设'{getattr(hypothesis, 'description', '')[:50]}'验证失败: "
            f"{verdict.get('falsified_reason', '未知原因')}"
        )

    def _extract_lessons(self, verdict: dict[str, Any]) -> str:
        """提取经验教训."""
        if verdict.get("enter_ab_bucket"):
            return "因子方向正确, IC 显著且稳定, 可扩展到相关因子"
        reason = verdict.get("falsified_reason", "")
        if "IC 不显著" in reason:
            return "因子 IC 不显著, 可能方向错误或因子已衰减"
        if "不稳定" in reason:
            return "因子稳定性不足, 考虑增加样本或调整计算窗口"
        if "CRO Gate" in reason:
            return "CRO Gate 未通过, 可能过拟合或回撤过大"
        return "验证未通过, 需进一步分析"
