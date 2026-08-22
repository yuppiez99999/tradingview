"""信息丰富度评级 (A/B/C) — 决定四大师研究策略

源自 ai-berkshire investment-team.md 第一步半。
"""

from typing import Any


def grade_info(market_meta: dict[str, Any]) -> str:
    """根据上市年数/券商覆盖度评级信息丰富度。

    Args:
        market_meta: {"上市年数": int, "券商覆盖数": int, "市场": str}

    Returns:
        "A" (信息充裕) / "B" (信息适中) / "C" (信息稀缺)
    """
    years = market_meta.get("上市年数", 0)
    coverage = market_meta.get("券商覆盖数", 0)

    if years >= 10 and coverage >= 20:
        return "A"
    if years >= 3 and coverage >= 5:
        return "B"
    return "C"


def grade_strategy_adjustment(grade: str) -> str:
    """返回该评级下的四大师策略调整提示。"""
    adjustments = {
        "A": "警惕共识陷阱, 重点找反面证据和非共识视角, 避免输出与市场一致的正确的废话。",
        "B": "推算数据必须标注置信度, 汇总时标注数据充分度。",
        "C": "转第一性原理模式: 聚焦商业本质核心问题, 允许留白, 不追求报告完整性。",
    }
    return adjustments.get(grade, adjustments["B"])


def grade_label(grade: str) -> str:
    """评级中文标签。"""
    return {"A": "信息充裕", "B": "信息适中", "C": "信息稀缺"}.get(grade, "信息适中")
