"""机构研报字段提取：GLM-5 抽取结构化字段，失败降级规则模板。"""

from __future__ import annotations

import re
from typing import Any

import llm

REPORT_PROMPT = """你是机构研报分析助理。请从下面研报文本提取结构化信息，仅输出 JSON（不要代码块），字段：
{
  "stock_code": "6位A股代码(无后缀, 如 600519)",
  "stock_name": "股票名称",
  "institution": "发布机构",
  "rating": "buy" | "overweight" | "neutral" | "sell",
  "target_price": 0.0,
  "eps_forecast": {"2026": 0.0, "2027": 0.0},
  "core_logic": "核心看多/看空逻辑(中文简述)",
  "risk_warnings": "主要风险(中文简述)",
  "industry": "所属行业"
}
研报文本：
"""

RATING_MAP = {
    "买入": "buy",
    "强推": "buy",
    "推荐": "overweight",
    "增持": "overweight",
    "谨慎推荐": "neutral",
    "中性": "neutral",
    "回避": "sell",
    "卖出": "sell",
}


def normalize_rating(rating: Any) -> str:
    if not rating:
        return "neutral"
    rating = str(rating).strip().lower()
    if rating in ("buy", "overweight", "neutral", "sell"):
        return rating
    return RATING_MAP.get(rating, "neutral")


def parse_json(text: str) -> dict | None:
    """兼容旧调用点；委托给 llm._repair_json。"""
    return llm._repair_json(text)


def _rule_extract(text: str) -> dict[str, Any]:
    match = re.search(r"(60\d{4}|00\d{4}|30\d{4}|68\d{4})", text or "")
    return {
        "stock_code": match.group(1) if match else "",
        "stock_name": "",
        "institution": "",
        "rating": "neutral",
        "target_price": 0.0,
        "eps_forecast": {},
        "core_logic": (text or "")[:400],
        "risk_warnings": "",
        "industry": "",
    }


def extract_report(raw_text: str) -> dict[str, Any]:
    """从研报 PDF 文本抽取结构化研报洞察。"""
    prompt = REPORT_PROMPT + (raw_text or "")[:8000]
    data = llm.extract_json(prompt)
    if data is None:
        data = _rule_extract(raw_text or "")
    data["rating"] = normalize_rating(data.get("rating"))
    return data
