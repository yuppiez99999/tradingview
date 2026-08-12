# -*- coding: utf-8 -*-
"""Phase 1.5: 收益预测动态校准 (从 daily_workflow.py 拆出, 零行为变更)。

原位置: daily_workflow.py L1099-L1177

Step 1: Wind MCP 拉取最新日K, 更新 returns_history.json + market_returns.json
Step 2: 计算已实现年化收益率
Step 3: 校准 portfolio_return_projection.json 概率权重
"""
from __future__ import annotations

import logging

from workflow.context import WorkflowContext, get_dw_module

logger = logging.getLogger("v75.daily_workflow")

# === 从 daily_workflow 模块获取模块级符号 ===
_dw = get_dw_module()

CALIBRATE_READY = getattr(_dw, "CALIBRATE_READY", False) if _dw else False

# 函数 — 仅当 daily_workflow 模块中已定义时才引入
if _dw is not None and hasattr(_dw, "_run_calibration"):
    _run_calibration = _dw._run_calibration


def phase_calibrate(ctx: WorkflowContext) -> bool:
    """收益预测动态校准"""
    logger.info("=" * 60)
    logger.info(f"Phase 1.5: 收益预测动态校准 @ {ctx.trade_date}")
    logger.info("=" * 60)

    if not CALIBRATE_READY:
        logger.warning("calibrate_returns_projection 模块未就绪, 跳过校准")
        ctx.state["phases"]["calibrate"] = {
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
            ctx.state["phases"]["calibrate"] = {
                "status": "FAIL",
                "error": str(result)[:500],
            }
            # 校准失败不阻断后续阶段
            return True

        step1 = result.get("step1_update", {})
        step2 = result.get("step2_realized", {})
        step3 = result.get("step3_calibration", {})

        ctx.state["phases"]["calibrate"] = {
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
        ctx.state["phases"]["calibrate"] = {
            "status": "FAIL",
            "error": str(e),
        }
        return True  # 不阻断后续流程
