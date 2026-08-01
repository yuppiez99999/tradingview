#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase implementation: phase_market

Extracted from original DailyWorkflow class for modularization.
This module contains the standalone phase function implementing the phase_market phase.

The function receives a DailyWorkflow instance as its first parameter ("workflow").
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def phase_market(workflow) -> CircuitLevel:
    """市场状态评估"""
    logger.info("=" * 60)
    logger.info("Phase 2: 市场状态评估")
    logger.info("=" * 60)

    # 懒初始化（支持单独运行该 phase）
    if not hasattr(self, "cb"):
        try:
            from risk.circuit_breaker import CircuitBreaker
            # 修复 P0: CircuitBreaker 必须传入 name 参数
            workflow.cb = CircuitBreaker(name="daily_workflow")
        except Exception as e:
            logger.warning(f"CircuitBreaker 初始化失败，使用模拟模式: {e}")
            workflow.cb = None

    # 模拟市场数据 (实盘应从 Wind/iFinD 获取)
    market_data = {
        "vix": 18.5,                    # VIX 18.5 (正常偏低)
        "portfolio_drop": 0.0,          # 当日无跌
        "index_return_20d": 0.02,       # 20日 +2%
        "index_return_60d": 0.05,       # 60日 +5%
    }

    # 熔断级别判定
    try:
        level = workflow.cb.check(portfolio_drop=market_data["portfolio_drop"], vix=market_data["vix"])
        actions = workflow.cb.allowed_actions()
    except (AttributeError, TypeError):
        pass

