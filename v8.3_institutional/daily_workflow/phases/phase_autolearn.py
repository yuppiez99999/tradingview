#!/usr/bin/env python3
"""
Phase implementation: phase_autolearn

Extracted from original DailyWorkflow class for modularization.
This module contains the standalone phase function implementing the phase_autolearn phase.

The function receives a DailyWorkflow instance as its first parameter ("workflow").
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def phase_autolearn(workflow) -> bool:
    """自主学习量化训练 (优先使用增强训练器)"""
    logger.info("=" * 60)
    logger.info(f"Phase 8: 自主学习量化训练 @ {workflow.trade_date}")
    logger.info(f"  引擎: {AUTOLEARN_ENGINE}")
    logger.info("=" * 60)

    if not AUTOLEARN_READY:
        logger.warning("自主学习训练模块未就绪, 跳过训练")
        workflow.state["phases"]["autolearn"] = {
            "status": "SKIP",
            "reason": "训练模块未导入",
        }
        return True

    try:
        # 执行训练 (7 天内不重训)
        # 增强训练器: 真实OHLCV + 情绪因子 + 自适应重训
        # 旧训练器: 合成OHLCV + LGB+XGB 集成
        result = _run_autolearn(force_retrain=False)

        if result.get("status") != "OK":
            logger.error(f"自主学习训练失败: {result}")
            workflow.state["phases"]["autolearn"] = {
                "status": "FAIL",
                "error": str(result)[:500],
            }
            return True

        # 生成日报
        try:
            report_path = _generate_autolearn_report(result)
            logger.info(f"自主学习日报: {report_path}")
        except Exception as e:
            logger.warning(f"日报生成失败: {e}")
            report_path = None

        # 保存状态
        signals = result.get("signals", {})
        summary = signals.get("summary", {})
        workflow.state["phases"]["autolearn"] = {
            "status": "PASS",
            "engine": AUTOLEARN_ENGINE,
            "total": result.get("total", 0),
            "trained": result.get("trained", 0),
            "skipped": result.get("skipped", 0),
            "failed": result.get("failed", 0),
            "signals_summary": summary,
            "top_signals": sorted(
                [(k, v.get("signal", 0)) for k, v in signals.get("signals", {}).items()],
                key=lambda x: abs(x[1]),
                reverse=True,
            )[:5],
            "report_path": str(report_path) if report_path else None,
        }

        # === v8.5: Purged K-Fold 验证 (训练后验证过拟合风险) ===
        try:
            if V85_READY:
                from model_validation.purged_kfold_cv import PurgedKFoldCV
                pkf = PurgedKFoldCV(n_splits=5, purge_window=5)
                cv_result = pkf.validate(result)
                workflow.state["phases"]["autolearn"]["purged_kfold"] = cv_result
                logger.info(
                    "[v8.5 PurgedKFold] 验证完成: avg_IC=%.4f, ICIR=%.4f, "
                    "过拟合标志=%s, 评分=%s",
                    cv_result.get("avg_ic", 0),
                    cv_result.get("icir", 0),
                    cv_result.get("overfit_detected", False),
                    cv_result.get("grade", "N/A"),
                )
                if cv_result.get("overfit_detected"):
                    logger.warning("[v8.5 PurgedKFold] 过拟合风险检测! 建议review因子/参数")
            else:
                workflow.state["phases"]["autolearn"]["purged_kfold"] = {"status": "SKIP", "reason": "v85_not_ready"}
        except Exception as e:
            logger.error(f"[v8.5 PurgedKFold] 验证失败: {e}", exc_info=True)
            workflow.state["phases"]["autolearn"]["purged_kfold"] = {"status": "ERROR", "error": str(e)}

        logger.info(f"Phase 8 完成: 训练 {result.get('trained', 0)} 标的, "
                    f"信号 {summary.get('total', 0)} 个 "
                    f"(多 {summary.get('bullish', 0)}/空 {summary.get('bearish', 0)}/"
                    f"中性 {summary.get('neutral', 0)})")
        return True

    except Exception as e:
        logger.error(f"Phase 8 异常: {e}", exc_info=True)
        workflow.state["phases"]["autolearn"] = {
            "status": "FAIL",
            "error": str(e),
        }
        return True

    # --------------------------------------------------------
    # Phase 9: FactorKillSwitch 因子实时监控 (S6 持续监控)
    #   - 监控现有 51 个生产因子 + 16 个 vibe_trading 候选因子
    #   - 状态机: ACTIVE -> WARNED -> DEGRADED -> DISABLED -> RETIRED
    #     紧急退出: EMERGENCY_EXIT (单日/累计回撤触发)
    #   - 每日增量更新, 状态持久化到 reports/kill_switch/{trade_date}/
    #   - 失败降级为 SKIP, 不影响主工作流 (P0.6 异常隔离要求)
    # --------------------------------------------------------

