#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase implementation: phase_calibrate

Extracted from original DailyWorkflow class for modularization.
This module contains the standalone phase function implementing the phase_calibrate phase.

The function receives a DailyWorkflow instance as its first parameter ("workflow").
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def phase_calibrate(workflow) -> bool:
    """收益预测动态校准"""
    logger.info("=" * 60)
    logger.info(f"Phase 1.5: 收益预测动态校准 @ {workflow.trade_date}")
    logger.info("=" * 60)

    if not CALIBRATE_READY:
        logger.warning("calibrate_returns_projection 模块未就绪, 跳过校准")
        workflow.state["phases"]["calibrate"] = {
            "status": "SKIP",
            "reason": "calibrate_returns_projection 模块未导入",
        }
        return True  # 不阻断后续流程

    try:
        # 执行三步校准: Wind 拉取 → 计算已实现 → 校准 projection
        result = _run_calibration()

        status = result.get("status", "FAIL")
        if status not in ("OK", "DEGRADED"):
            logger.error(f"收益预测校准失败: {result}")
            workflow.state["phases"]["calibrate"] = {
                "status": "FAIL",
                "error": str(result)[:500],
            }
            # 校准失败不阻断后续阶段
            return True

        step1 = result.get("step1_update", {})
        step2 = result.get("step2_realized", {})
        step3 = result.get("step3_calibration", {})

        workflow.state["phases"]["calibrate"] = {
            "status": "PASS" if status == "OK" else "DEGRADED",
            "wind_fetch": {
                "success": step1.get("success", 0),
                "fail": step1.get("fail", 0),
                "total_days": step1.get("total_days", 0),
                "total_symbols": step1.get("total_symbols", 0),
                "degraded_reason": step1.get("degraded_reason"),
            },
            "realized": {
                "start_date": step2.get("start_date"),
                "end_date": step2.get("end_date"),
                "years": step2.get("years"),
                "portfolio_weighted_annualized": step2.get(
                    "portfolio_weighted_annualized", 0
                ),
                "market_annualized": step2.get("market_annualized", 0),
                "market_sharpe": step2.get("market_sharpe", 0),
                "portfolio_weight_total": step2.get(
                    "portfolio_weight_total", 0
                ),
            },
            "calibration": {
                "original_weights": step3.get("original_weights"),
                "calibrated_weights": step3.get("calibrated_weights"),
                "calibration_reason": step3.get("calibration_reason"),
                "calibrated_expected_annualized": step3.get(
                    "calibrated_expected_annualized"
                ),
                "calibrated_expected_final": step3.get(
                    "calibrated_expected_final"
                ),
            },
        }
        if status == "DEGRADED":
            logger.warning("Phase 1.5 完成 (降级模式): Wind 拉取失败, 使用现有历史数据校准")
        else:
            logger.info("Phase 1.5 完成: 收益预测校准成功")
        return True

    except Exception as e:
        logger.error(f"Phase 1.5 异常: {e}", exc_info=True)
        workflow.state["phases"]["calibrate"] = {
            "status": "FAIL",
            "error": str(e),
        }
        return True  # 不阻断后续流程

    # --------------------------------------------------------
    # Phase 2: 市场状态评估
    # --------------------------------------------------------

