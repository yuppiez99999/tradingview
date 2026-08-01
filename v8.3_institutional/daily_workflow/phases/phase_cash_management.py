#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase implementation: phase_cash_management

Extracted from original DailyWorkflow class for modularization.
This module contains the standalone phase function implementing the phase_cash_management phase.

The function receives a DailyWorkflow instance as its first parameter ("workflow").
"""

from __future__ import annotations

import logging
from typing import Any, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def phase_cash_management(workflow) -> Dict[str, Any]:
    """现金管理 — 逆回购自动下单 + 应急金监控 + 保证金追加检查

    v10.0 投资计划 cash_management (130 万资金, 占总资本 26%):
        - 期货保证金 50 万 (维持率 ≥ 60%)
        - 期权抵押金 10 万
        - 应急保证金 30 万 (2 日内补足)
        - 逆回购 / 货基 40 万 (RCO001, 每日自动)

    自动化:
        - 每日 14:30 评估闲置资金
        - 闲置资金 > 1 万 → 自动下单 RCO001
        - 月末季末高利率期 → 加大投放 20%
        - 应急金动用 → 2 日内补足

    Returns:
        现金管理结果
    """
    logger.info("=" * 60)
    logger.info("Phase 4.8: 现金管理 (逆回购 + 货基 + 应急金)")
    logger.info("=" * 60)

    result: Dict[str, Any] = {
        "status": "PASS",
        "action": "skip",
        "total_cash": 0.0,
        "reverse_repo_amount": 0.0,
        "estimated_daily_income": 0.0,
        "repo_order": {},
        "margin_call": {},
        "emergency_replenish": {},
    }

    if not V10_STRATEGY_READY:
        result["status"] = "SKIP"
        result["reason"] = "v10.0 现金管理模块未加载"
        logger.warning("[CashManager] v10.0 模块未加载, 跳过")
        workflow.state["phases"]["cash_management"] = result
        return result

    try:
        # 1. 加载当前现金状态
        total_cash, futures_margin_used, options_collateral_used, emergency_used = workflow._load_cash_state()

        # 2. 获取当前逆回购利率
        repo_rate = workflow._get_current_repo_rate()

        # 3. 执行闲置资金分配
        cm = CashManager()
        cm_result = cm.allocate_idle_cash(
            total_cash=total_cash,
            futures_margin_used=futures_margin_used,
            options_collateral_used=options_collateral_used,
            emergency_used=emergency_used,
            current_repo_rate=repo_rate,
            trade_date=date.today(),
        )

        # 4. 输出摘要
        summary = cm.summary(cm_result)
        logger.info("\n" + summary)

        # 5. 检查应急金补足
        if emergency_used > 0:
            replenish = cm.check_emergency_replenish(emergency_used)
            result["emergency_replenish"] = replenish
            if replenish.get("action") == "replenish_now":
                logger.warning(f"[CashManager] {replenish['reason']}")

        # 6. 检查期货保证金追加
        futures_account_value, futures_margin_used_actual = workflow._load_futures_account_state()
        if futures_account_value > 0:
            margin_check = cm.check_margin_call(futures_account_value, futures_margin_used_actual)
            result["margin_call"] = margin_check
            if margin_check.get("action") != "no_action":
                logger.warning(f"[CashManager] {margin_check['reason']}")

        # 7. 更新结果
        result.update({
            "status": "PASS",
            "action": cm_result.action,
            "total_cash": cm_result.total_cash,
            "reverse_repo_amount": cm_result.reverse_repo,
            "estimated_daily_income": cm_result.estimated_daily_income,
            "estimated_annual_yield": cm_result.estimated_annual_yield,
            "repo_order": cm_result.repo_order,
            "is_month_end": cm_result.is_month_end,
            "is_quarter_end": cm_result.is_quarter_end,
            "futures_margin_ratio": cm_result.futures_margin_ratio,
            "emergency_replenish_needed": cm_result.emergency_replenish_needed,
        })

    except Exception as e:
        logger.error(f"[CashManager] 现金管理失败: {e}", exc_info=True)
        result["status"] = "ERROR"
        result["reason"] = str(e)

    # === 写入 state ===
    workflow.state["phases"]["cash_management"] = result
    logger.info("-" * 60)
    logger.info("Phase 4.8 完成: 动作=%s, 逆回购=¥%.0f, 日收益=¥%.2f",
                result.get("action", ""),
                result.get("reverse_repo_amount", 0),
                result.get("estimated_daily_income", 0))
    logger.info("=" * 60)
    return result


