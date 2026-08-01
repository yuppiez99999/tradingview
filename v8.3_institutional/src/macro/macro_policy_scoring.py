#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
十五五规划 + 康波周期/周金涛理论 评分模块（v7.5 轻量接入版）

提供两类评分：
1. 十五五规划评分：按“政策对齐 + 战略新兴 + 国产替代 + 安全底线”打分
2. 康波周期评分：按复苏/繁荣转换期，给高端制造/算力/资源/防御打分
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

# =====================
# 十五五规划
# =====================

FIFTEEN_FIVE_DIRECTIONS = [
    {"id": "ai_infra", "name": "AI 算力基础设施", "weight": 1.15},
    {"id": "advanced_equip", "name": "高端装备制造", "weight": 1.10},
    {"id": "semiconductor", "name": "半导体与集成电路", "weight": 1.15},
    {"id": "new_energy", "name": "新能源与储能", "weight": 1.05},
    {"id": "biomedicine", "name": "创新药与高端医疗器械", "weight": 1.05},
    {"id": "resource_security", "name": "战略资源与安全", "weight": 1.05},
    {"id": "defensive_utility", "name": "防御型公用事业", "weight": 0.95},
]

FIFTEEN_FIVE_SYMBOL_MAP: Dict[str, List[str]] = {
    "ai_infra": ["sz300308", "sz002371", "sh603019"],
    "advanced_equip": ["sh600089", "sz000425", "sh688017"],
    "semiconductor": ["sh688041", "sh688981"],
    "new_energy": ["sz300274"],
    "biomedicine": ["sh600276"],
    "resource_security": ["sz518880", "sh600219", "sh600019"],
    "defensive_utility": ["sh600900", "sz515180", "sz510300", "sz510500", "sz512100", "sz588000", "sz159915"],
}


@dataclass
class FifteenFiveScore:
    direction_id: str
    direction_name: str
    score: float = 0.0
    weight: float = 1.0
    symbols: List[str] = field(default_factory=list)
    note: str = ""


def score_fifteen_five(symbols: List[str]) -> Dict[str, FifteenFiveScore]:
    results: Dict[str, FifteenFiveScore] = {}
    for item in FIFTEEN_FIVE_DIRECTIONS:
        mapped = [s for s in symbols if s in FIFTEEN_FIVE_SYMBOL_MAP.get(item["id"], [])]
        base = 1.0 if mapped else 0.7
        score = round(base * float(item["weight"]), 4)
        results[item["id"]] = FifteenFiveScore(
            direction_id=item["id"],
            direction_name=item["name"],
            score=score,
            weight=float(item["weight"]),
            symbols=mapped,
            note="命中战略方向" if mapped else "未命中核心映射",
        )
    return results


# =====================
# 康波周期 / 周金涛
# =====================

# 第六轮康波：复苏 -> 繁荣转换期（2025-2030）
# 风格权重：高端制造/算力 > 资源 > 防御 > 传统顺周期

KONDRATIEV_STYLE_WEIGHTS = {
    "高端制造": 1.15,
    "科技": 1.15,
    "新能源": 1.10,  # 2026-07-09 新增: 康波繁荣期成长股
    "制造": 1.10,
    "资源": 1.10,  # 2026-07-09 新增: 周金涛理论大宗商品主升浪
    "避险": 1.05,
    "医药": 1.05,
    "红利": 1.00,
    "宽基": 1.00,
    "顺周期": 0.90,
    "成长": 0.95,
    "化工": 0.90,
    "银行": 0.90,
}

KONDRATIEV_SYMBOL_STYLE_MAP: Dict[str, str] = {
    "sz300308": "科技",
    "sz002371": "科技",
    "sh688041": "科技",
    "sh688981": "科技",
    "sh603019": "科技",
    "sh600089": "制造",
    "sz000425": "制造",
    "sh688017": "制造",
    "sz300274": "新能源",  # 2026-07-09 新增
    "sz588000": "高端制造",
    "sz518880": "避险",
    "sh600276": "医药",
    "sh600900": "防御",
    "sz515180": "红利",
    "sz510300": "宽基",
    "sz510500": "宽基",
    "sz512100": "宽基",
    "sz159915": "成长",
    "sh600875": "制造",
    "sh600406": "制造",
    "sh600989": "化工",
    "sh600036": "银行",
    "sh601088": "顺周期",
    "sh600219": "资源",  # 2026-07-09 新增: 战略资源
    "sh600019": "资源",  # 2026-07-09 新增: 黑色系
}


@dataclass
class KondratievScore:
    symbol: str
    style: str
    cycle_score: float = 1.0
    weight: float = 1.0
    note: str = ""


def score_kondratiev(symbols: List[str]) -> Dict[str, KondratievScore]:
    results: Dict[str, KondratievScore] = {}
    for symbol in symbols:
        style = KONDRATIEV_SYMBOL_STYLE_MAP.get(symbol, "宽基")
        weight = float(KONDRATIEV_STYLE_WEIGHTS.get(style, 1.0))
        cycle_score = round(weight, 4)
        note = "康波复苏/繁荣受益" if weight >= 1.10 else "康波中性/防御" if weight >= 0.95 else "康波偏弱"
        results[symbol] = KondratievScore(
            symbol=symbol,
            style=style,
            cycle_score=cycle_score,
            weight=weight,
            note=note,
        )
    return results


# =====================
# 综合评分
# =====================


@dataclass
class MacroPolicyScore:
    symbol: str
    fifteen_five_score: float = 1.0
    kondratiev_score: float = 1.0
    combined_score: float = 1.0
    fifteen_five_note: str = ""
    kondratiev_note: str = ""


def score_macro_policy(symbols: List[str]) -> Dict[str, MacroPolicyScore]:
    ff = score_fifteen_five(symbols)
    kp = score_kondratiev(symbols)

    symbol_ff_score: Dict[str, float] = {}
    symbol_ff_note: Dict[str, str] = {}
    for item in ff.values():
        for s in item.symbols:
            symbol_ff_score[s] = item.score
            symbol_ff_note[s] = item.note

    results: Dict[str, MacroPolicyScore] = {}
    for symbol in symbols:
        ff_score = symbol_ff_score.get(symbol, 1.0)
        kp_score = kp[symbol].cycle_score if symbol in kp else 1.0
        combined = round((ff_score + kp_score) / 2, 4)
        results[symbol] = MacroPolicyScore(
            symbol=symbol,
            fifteen_five_score=ff_score,
            kondratiev_score=kp_score,
            combined_score=combined,
            fifteen_five_note=symbol_ff_note.get(symbol, "未命中十五五核心映射"),
            kondratiev_note=kp[symbol].note if symbol in kp else "未参与康波风格映射",
        )
    return results


def macro_score_to_factor(
    combined_score: float,
    *,
    boost_threshold: float = 1.15,
    neutral_min: float = 1.0,
    cut_max: float = 0.85,
    clamp_min: float = 0.5,
    clamp_max: float = 1.3,
) -> float:
    """将宏观综合评分映射到订单调整系数

    Args:
        combined_score: 十五五 + 康波综合评分
        boost_threshold: 强政策/周期共振阈值
        neutral_min: 中性维持下限
        cut_max: 减仓/跳过上限
        clamp_min: 系数下限
        clamp_max: 系数上限

    Returns:
        订单调整系数，例如 1.2 表示加仓 20%，0.0 表示跳过
    """
    if combined_score >= boost_threshold:
        return clamp_max
    if combined_score >= neutral_min:
        return 1.0
    if combined_score >= cut_max:
        return 0.8
    return 0.0


# =====================
# 候选标的池 (AI 决策整合)
# =====================
# 基于十五五规划缺口 + 康波周期资源/制造薄弱, 给出建议候选
# 由 daily_workflow Phase 1.5 (calibrate) 每日调用, 生成评估报告
# 不自动纳入 positions.json, 仅生成建议供人工/AI 审核

CANDIDATE_POOL: List[Dict[str, str]] = [
    # 高优先级 - 补十五五关键缺口
    {
        "code": "sz300274",
        "name": "阳光电源",
        "fifteen_five_dir": "new_energy",
        "kondratiev_style": "高端制造",
        "reason": "补十五五新能源与储能缺口; 康波繁荣期成长股优先",
        "suggested_weight": 0.05,
        "priority": "HIGH",
    },
    {
        "code": "sh603019",
        "name": "中科曙光",
        "fifteen_five_dir": "ai_infra",
        "kondratiev_style": "科技",
        "reason": "补 AI 算力基础设施; 国产替代核心标的",
        "suggested_weight": 0.04,
        "priority": "HIGH",
    },
    {
        "code": "sh600089",
        "name": "特变电工",
        "fifteen_five_dir": "advanced_equip",
        "kondratiev_style": "制造",
        "reason": "补高端装备制造覆盖过薄; 算力+电网输变电",
        "suggested_weight": 0.03,
        "priority": "HIGH",
    },
    # 中优先级 - 补资源/防御
    {
        "code": "sh600219",
        "name": "南山铝业",
        "fifteen_five_dir": "resource_security",
        "kondratiev_style": "制造",
        "reason": "补战略资源; 康波繁荣期大宗商品主升浪",
        "suggested_weight": 0.03,
        "priority": "MEDIUM",
    },
    {
        "code": "sh600019",
        "name": "宝钢股份",
        "fifteen_five_dir": "resource_security",
        "kondratiev_style": "制造",
        "reason": "补黑色系; 周金涛理论强调繁荣期铜铁主升浪",
        "suggested_weight": 0.02,
        "priority": "MEDIUM",
    },
    {
        "code": "sh688017",
        "name": "绿的谐波",
        "fifteen_five_dir": "advanced_equip",
        "kondratiev_style": "制造",
        "reason": "补机器人产业链; 高端制造谐波减速器龙头",
        "suggested_weight": 0.02,
        "priority": "MEDIUM",
    },
]


@dataclass
class CandidateEvaluation:
    """候选标的评估结果"""

    code: str
    name: str
    priority: str
    fifteen_five_score: float
    kondratiev_score: float
    combined_score: float
    suggested_weight: float
    reason: str
    in_position: bool  # 是否已在持仓
    recommendation: str  # "ADD" / "WATCH" / "HOLD"


def evaluate_candidate_pool(current_positions: List[str]) -> List[CandidateEvaluation]:
    """评估候选标的池, 返回带综合评分的建议清单

    Args:
        current_positions: 当前持仓代码列表 (如 ['sz588000', 'sh688041', ...])

    Returns:
        候选标的评估结果列表, 按综合评分降序排序
    """
    current_set = set(current_positions)
    results: List[CandidateEvaluation] = []

    for candidate in CANDIDATE_POOL:
        code = candidate["code"]
        ff_dir = candidate["fifteen_five_dir"]
        kp_style = candidate["kondratiev_style"]

        # 查找十五五方向权重
        ff_weight = 1.0
        for d in FIFTEEN_FIVE_DIRECTIONS:
            if d["id"] == ff_dir:
                ff_weight = float(d["weight"])
                break

        # 查找康波风格权重 (默认 1.0)
        kp_weight = float(KONDRATIEV_STYLE_WEIGHTS.get(kp_style, 1.0))

        # 综合评分 = (十五五命中分 + 康波分) / 2
        # 命中则用权重, 未命中用 0.7 基准
        ff_score = ff_weight  # 候选池均为已映射标的, 命中权重
        kp_score = kp_weight
        combined = round((ff_score + kp_score) / 2, 4)

        in_pos = code in current_set

        # 推荐: 未在持仓且综合分>=1.10 为 ADD; 未在持仓且>=1.0 为 WATCH
        if in_pos:
            recommendation = "HOLD"
        elif combined >= 1.10:
            recommendation = "ADD"
        elif combined >= 1.00:
            recommendation = "WATCH"
        else:
            recommendation = "WATCH"

        results.append(
            CandidateEvaluation(
                code=code,
                name=candidate["name"],
                priority=candidate["priority"],
                fifteen_five_score=ff_score,
                kondratiev_score=kp_score,
                combined_score=combined,
                suggested_weight=float(candidate["suggested_weight"]),
                reason=candidate["reason"],
                in_position=in_pos,
                recommendation=recommendation,
            )
        )

    # 按综合评分降序
    results.sort(key=lambda x: x.combined_score, reverse=True)
    return results
