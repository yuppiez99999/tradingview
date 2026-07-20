# -*- coding: utf-8 -*-
"""
financial-services 补充信号
=============================
把 e:\\各种PY程序\\financial-services 的插件/技能覆盖映射为
行业/主题层面的补充信号，接入现有 signal fusion。

当前实现：
- 扫描 plugins/vertical-plugins 与 managed-agent-cookbooks 的关键技能目录
- 映射到持仓所属行业
- 输出 {symbol: {"strength": float, "confidence": float, "source": str}}
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("financial_services_signal")

BASE_DIR = os.path.abspath(r"E:\各种PY程序\financial-services")

# 持仓 -> 行业/主题关键词
SYMBOL_SECTOR_KEYWORDS: Dict[str, List[str]] = {
    "600519": ["白酒", "消费", "食品饮料", "零售", "pitch", "client"],
    "000858": ["白酒", "消费", "食品饮料", "零售", "pitch", "client"],
    "601318": ["保险", "金融", "银行", "wealth", "private-equity", "fund"],
    "000001": ["银行", "金融", "wealth", "fund"],
    "600036": ["银行", "金融", "wealth", "fund"],
    "601398": ["银行", "金融", "wealth", "fund"],
    "600276": ["医药", "创新药", "医疗", "health", "earnings"],
    "000063": ["通信", "5G", "科技", "半导体", "technology", "market"],
}

# 技能目录 -> 行业/主题标签映射
SKILL_DIR_LABELS: Dict[str, List[str]] = {
    "sector-overview": ["消费", "食品饮料", "医药", "通信", "科技", "金融", "银行", "保险"],
    "market-researcher": ["消费", "食品饮料", "医药", "通信", "科技", "金融", "银行", "保险", "零售"],
    "earnings-analysis": ["医药", "创新药", "食品饮料", "零售"],
    "earnings-preview": ["医药", "创新药", "食品饮料", "零售"],
    "morning-note": ["消费", "医药", "金融", "科技", "通信"],
    "catalyst-calendar": ["消费", "医药", "金融", "科技", "通信", "银行", "保险"],
    "comps-analysis": ["金融", "银行", "保险", "食品饮料", "医药"],
    "dcf-model": ["金融", "银行", "保险", "食品饮料", "医药", "通信", "科技"],
    "3-statement-model": ["金融", "银行", "保险", "食品饮料", "医药", "通信", "科技"],
    "valuation-reviewer": ["金融", "银行", "保险", "食品饮料", "医药", "通信", "科技"],
    "equity-research": ["金融", "银行", "保险", "食品饮料", "医药", "通信", "科技", "消费"],
    "macro-rates-monitor": ["金融", "银行", "保险", "债券", "利率"],
    "fixed-income-portfolio": ["金融", "银行", "保险", "债券"],
    "fx-carry-trade": ["金融", "汇率", "利率"],
    "bond-relative-value": ["金融", "债券", "利率"],
    "swap-curve-strategy": ["金融", "利率", "债券"],
    "lseg": ["金融", "宏观", "利率", "债券", "汇率", "commodity"],
    "spglobal": ["金融", "宏观", "债券", "股权", "ipo"],
    "fund-admin": ["基金", "净值", "会计", "合规"],
    "private-equity": ["股权", "并购", "资本运作"],
    "investment-banking": ["并购", "ipo", "资本运作", "债券"],
    "kyc-screener": ["合规", "反洗钱", "尽调"],
    "wealth-management": ["财富管理", "资产配置", "再平衡", "减亏"],
    "portfolio-rebalance": ["财富管理", "资产配置", "再平衡"],
    "tax-loss-harvesting": ["财富管理", "减亏", "税务"],
    "client-review": ["财富管理", "资产配置"],
    "investment-proposal": ["并购", "投资建议", "股权"],
}


def _dir_exists(*parts: str) -> bool:
    return os.path.isdir(os.path.join(BASE_DIR, *parts))


def _skill_exists(*parts: str) -> bool:
    return os.path.isdir(os.path.join(BASE_DIR, "plugins", *parts)) or \
           os.path.isdir(os.path.join(BASE_DIR, "managed-agent-cookbooks", *parts))


def _coverage_labels() -> List[str]:
    labels: List[str] = []
    for skill_dir, labels_list in SKILL_DIR_LABELS.items():
        if _skill_exists("vertical-plugins", "*", "skills", skill_dir) or \
           _skill_exists("agent-plugins", "*", "skills", skill_dir) or \
           _skill_exists("managed-agent-cookbooks", "*") or \
           os.path.isdir(os.path.join(BASE_DIR, "plugins", "partner-built", "lseg", "skills", skill_dir)) or \
           os.path.isdir(os.path.join(BASE_DIR, "plugins", "partner-built", "spglobal", "skills", skill_dir)):
            labels.extend(labels_list)
    return labels


def _match_score(symbol: str, labels: List[str]) -> float:
    keywords = SYMBOL_SECTOR_KEYWORDS.get(symbol, [])
    if not keywords:
        return 0.0
    match_count = sum(1 for k in keywords if any(k in label or label in k for label in labels))
    return match_count / max(len(keywords), 1)


def generate(symbols: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
    """生成 financial-services 补充信号"""
    symbols = symbols or list(SYMBOL_SECTOR_KEYWORDS.keys())
    labels = _coverage_labels()
    if not labels:
        logger.debug("financial-services 目录下未发现可用技能/插件，返回空信号")
        return {}

    results: Dict[str, Dict[str, Any]] = {}
    for symbol in symbols:
        score = _match_score(symbol, labels)
        if score <= 0.0:
            continue
        # 匹配度越高，confidence 越高；strength 轻微正向，作为补充
        confidence = min(1.0, 0.35 + 0.25 * score)
        strength = float(min(1.0, 0.15 + 0.20 * score))
        results[symbol] = {
            "strength": strength,
            "confidence": confidence,
            "source": "financial_services",
            "meta": {
                "matched_labels": [label for label in labels if any(k in label or label in k for k in SYMBOL_SECTOR_KEYWORDS.get(symbol, []))],
                "coverage_score": round(score, 4),
            },
        }
    logger.info("[FinancialServicesSignal] 生成补充信号: symbols=%s", list(results.keys()))
    return results
