# -*- coding: utf-8 -*-
"""
风格 Beta 代理 (B-4.2)
====================

基于风格标签的 rough beta estimation, 用于组合 Beta 估算。

统一来源: 消除 hedge_quantity_calculator.py 和 today_hedge_decision.py 的重复定义。

使用方式:
  from utils.risk.style_beta import STYLE_BETA_PROXY, get_style_beta

  beta = get_style_beta("科技")      # 1.20
  beta = get_style_beta("未知风格")   # 1.0 (默认回退)
"""

from typing import Dict

# ── 风格 Beta 代理字典 (基于风格标签的 rough estimation) ──
# 来源: hedge_quantity_calculator.py L42-56 / today_hedge_decision.py L71-86
# (2026-08-01 统一抽取, 消除 DRY 违规)
STYLE_BETA_PROXY: Dict[str, float] = {
    "宽基": 0.95,
    "高端制造": 1.15,
    "科技": 1.20,
    "制造": 1.05,
    "新能源": 1.10,
    "医药": 0.85,
    "化工": 1.00,
    "银行": 0.75,
    "防御": 0.60,
    "顺周期": 1.10,
    "避险": -0.10,
    "红利": 0.70,
    "成长": 1.25,
}

# 默认 beta (未知风格的回退值)
DEFAULT_STYLE_BETA = 1.0


def get_style_beta(style: str) -> float:
    """获取风格对应的 Beta 代理值

    Args:
        style: 风格标签 (如 "科技"/"防御"/"宽基")

    Returns:
        beta: 风格 Beta 代理值, 未知风格返回 DEFAULT_STYLE_BETA (1.0)
    """
    return STYLE_BETA_PROXY.get(style, DEFAULT_STYLE_BETA)


__all__ = ["STYLE_BETA_PROXY", "DEFAULT_STYLE_BETA", "get_style_beta"]
