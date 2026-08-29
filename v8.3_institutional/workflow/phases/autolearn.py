"""Phase 8: 自主学习量化训练 (从 daily_workflow.py 拆出, 零行为变更)。

原位置: daily_workflow.py L5986-L6053
优先使用增强训练器 (真实OHLCV + 情绪因子 + 自适应重训), 不可用时回退到旧训练器。
"""

from __future__ import annotations

import logging

from workflow.context import WorkflowContext, get_dw_module

logger = logging.getLogger("v75.daily_workflow")

# === 从 daily_workflow 模块获取模块级符号 ===
_dw = get_dw_module()

AUTOLEARN_READY = getattr(_dw, "AUTOLEARN_READY", False) if _dw else False
AUTOLEARN_ENGINE = getattr(_dw, "AUTOLEARN_ENGINE", "none") if _dw else "none"

# 函数 — 仅当 daily_workflow 模块中已定义时才引入
if _dw is not None and hasattr(_dw, "_run_autolearn"):
    _run_autolearn = _dw._run_autolearn
if _dw is not None and hasattr(_dw, "_generate_autolearn_report"):
    _generate_autolearn_report = _dw._generate_autolearn_report


def phase_autolearn(ctx: WorkflowContext) -> bool:
    """自主学习量化训练 (优先使用增强训练器)"""
    logger.info("=" * 60)
    logger.info(f"Phase 8: 自主学习量化训练 @ {ctx.trade_date}")
    logger.info(f"  引擎: {AUTOLEARN_ENGINE}")
    logger.info("=" * 60)

    if not AUTOLEARN_READY:
        logger.warning("自主学习训练模块未就绪, 跳过训练")
        ctx.state["phases"]["autolearn"] = {
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
            ctx.state["phases"]["autolearn"] = {
                "status": "FAIL",
                "error": str(result)[:500],
            }
            return True

        # 生成日报
        try:
            report_path = _generate_autolearn_report(result)
            logger.info(f"自主学习日报: {report_path}")
        except Exception as e:  # fail-safe
            logger.warning(f"日报生成失败: {e}")
            report_path = None

        # 保存状态
        signals = result.get("signals", {})
        summary = signals.get("summary", {})
        ctx.state["phases"]["autolearn"] = {
            "status": "PASS",
            "engine": AUTOLEARN_ENGINE,
            "total": result.get("total", 0),
            "trained": result.get("trained", 0),
            "skipped": result.get("skipped", 0),
            "failed": result.get("failed", 0),
            "signals_summary": summary,
            "top_signals": sorted(
                [
                    (k, v.get("signal", 0))
                    for k, v in signals.get("signals", {}).items()
                ],
                key=lambda x: abs(x[1]),
                reverse=True,
            )[:5],
            "report_path": str(report_path) if report_path else None,
        }
        logger.info(
            f"Phase 8 完成: 训练 {result.get('trained', 0)} 标的, "
            f"信号 {summary.get('total', 0)} 个 "
            f"(多 {summary.get('bullish', 0)}/空 {summary.get('bearish', 0)}/"
            f"中性 {summary.get('neutral', 0)})"
        )
        return True

    except Exception as e:  # fail-safe
        logger.error(f"Phase 8 异常: {e}", exc_info=True)
        ctx.state["phases"]["autolearn"] = {
            "status": "FAIL",
            "error": str(e),
        }
        return True
