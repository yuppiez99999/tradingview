"""论文方法提取：GLM-5 提取结构化字段，失败降级规则模板。"""

from __future__ import annotations

from typing import Any

import llm

PAPER_PROMPT = """你是量化研究助理。请从下面论文摘要中提取结构化信息，仅输出 JSON（不要代码块），字段：
{
  "title": "string",
  "authors": ["string"],
  "year": 0,
  "method_type": "factor" | "risk_model" | "execution" | "hedging" | "other",
  "factor_logic": "因子/方法构造逻辑(中文简述)",
  "backtest_setup": "回测设定(市场/频率/基准)",
  "key_findings": "关键结论",
  "reproducibility": "可复现性评估(high/medium/low + 原因)"
}
论文摘要：
"""


def parse_json(text: str) -> dict[str, Any] | None:
    """兼容旧调用点；委托给 llm._repair_json。"""
    return llm._repair_json(text)


def _rule_extract(text: str) -> dict[str, Any]:
    return {
        "title": "",
        "authors": [],
        "year": 0,
        "method_type": "other",
        "factor_logic": (text or "")[:500],
        "backtest_setup": "",
        "key_findings": "",
        "reproducibility": "unknown (LLM 不可用, 已降级为原文摘要)",
    }


def extract_paper(raw_text: str, title_hint: str | None = None) -> dict[str, Any]:
    """从论文摘要/全文抽取结构化洞察。"""
    prompt = PAPER_PROMPT + (raw_text or "")[:6000]
    data = llm.extract_json(prompt)
    if data is None:
        data = _rule_extract(raw_text or "")
    data.setdefault("title", title_hint or "")
    return data
