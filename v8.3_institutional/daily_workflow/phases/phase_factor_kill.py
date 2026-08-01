#!/usr/bin/env python3
"""
Phase implementation: phase_factor_kill_switch

Extracted from original DailyWorkflow class for modularization.
This module contains the standalone phase function implementing the phase_factor_kill_switch phase.

The function receives a DailyWorkflow instance as its first parameter ("workflow").
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def phase_factor_kill_switch(workflow) -> bool:
    """FactorKillSwitch 每日监控 (S6 持续监控)"""
    logger.info("=" * 60)
    logger.info(f"Phase 9: FactorKillSwitch 因子实时监控 @ {workflow.trade_date}")
    logger.info("=" * 60)

    if not FACTOR_KS_READY:
        logger.warning("FactorKillSwitch 模块未就绪, 跳过 Phase 9")
        workflow.state["phases"]["factor_kill_switch"] = {
            "status": "SKIP",
            "reason": "FactorKillSwitch 模块未导入",
        }
        return True

    try:
        result = _run_factor_kill_switch_daily(trade_date=workflow.trade_date)
        status = result.get("status", "FAIL")

        if status == "PASS":
            total = result.get("total_monitored", 0)
            state_dist = result.get("state_distribution", {})
            triggered_today = result.get("triggered_today", [])
            active_cnt = state_dist.get("active", 0)
            active_rate = (active_cnt / total * 100) if total > 0 else 0

            logger.info(
                f"Phase 9 完成: 监控 {total} 个因子 | "
                f"ACTIVE {active_cnt}/{total} ({active_rate:.1f}%) | "
                f"当日新触发 {len(triggered_today)} 个"
            )
            if triggered_today:
                for t in triggered_today[:5]:
                    logger.warning(
                        "[FactorKillSwitch] %s: %s -> %s (%s)",
                        t.get("factor", "?"),
                        t.get("prev_status", "?"),
                        t.get("new_status", "?"),
                        t.get("trigger", "")[:80],
                    )

        elif status == "SKIP":
            logger.warning(f"Phase 9 跳过: {result.get('reason', 'unknown')}")
        else:
            logger.error(f"Phase 9 失败: {result.get('error', 'unknown')}")

        workflow.state["phases"]["factor_kill_switch"] = result
        return True

    except Exception as e:
        logger.error(f"Phase 9 异常: {e}", exc_info=True)
        workflow.state["phases"]["factor_kill_switch"] = {
            "status": "FAIL",
            "error": str(e),
        }
        # 异常隔离: Phase 9 失败不影响已完成的 Phase 1-8
        return True

    # --------------------------------------------------------
    # Phase 10: 影子账户监控 (Shadow Account Monitoring)
    # --------------------------------------------------------

