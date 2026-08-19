"""Phase 4.5: 对冲基金视角融合 (从 daily_workflow.py 拆出, 零行为变更)。

原位置: daily_workflow.py L1085-L1219 (phase_hedge_fund)

搬移内容:
- phase_hedge_fund: Theta/Gamma/KillSwitch/LiquidationScheduler 四引擎融合

模块级依赖 (动态查找, 兼容 daily_workflow 作为 __main__/模块导入):
- HEDGE_FUND_MODULES_READY, ThetaEngine, GammaEngine, KillSwitch, LiquidationScheduler
"""
from __future__ import annotations

import logging
from typing import Any

from workflow.context import WorkflowContext, get_dw_module

logger = logging.getLogger("v75.daily_workflow")

# === 从 daily_workflow 模块获取模块级符号 ===
_dw = get_dw_module()


def phase_hedge_fund(ctx: WorkflowContext) -> dict[str, Any]:
    """对冲基金视角融合阶段 — Theta/Gamma/KillSwitch/LiquidationScheduler"""
    # 动态查找模块级符号 (兼容 monkeypatch 对 daily_workflow 模块的 patch)
    HEDGE_FUND_MODULES_READY = bool(getattr(_dw, "HEDGE_FUND_MODULES_READY", False)) if _dw else False
    ThetaEngine = getattr(_dw, "ThetaEngine", None) if _dw else None
    GammaEngine = getattr(_dw, "GammaEngine", None) if _dw else None
    KillSwitch = getattr(_dw, "KillSwitch", None) if _dw else None
    LiquidationScheduler = getattr(_dw, "LiquidationScheduler", None) if _dw else None

    logger.info("=" * 60)
    logger.info("Phase 4.5: 对冲基金视角融合 (Theta/Gamma/KillSwitch/Liquidation)")
    logger.info("=" * 60)

    result: dict[str, Any] = {
        "status": "PASS",
        "modules_loaded": HEDGE_FUND_MODULES_READY,
        "theta": {},
        "gamma": {},
        "kill_switch": {},
        "liquidation": {},
    }

    if not HEDGE_FUND_MODULES_READY:
        logger.warning("对冲基金模块未加载, 跳过本阶段")
        result["status"] = "SKIP"
        result["reason"] = "modules_not_loaded"
        ctx.state["phases"]["hedge_fund"] = result
        return result

    # === 1. Theta引擎 — 月度Covered Call计划 + 滚仓检查 ===
    try:
        if ThetaEngine is None:
            raise ImportError("ThetaEngine 未加载")
        theta = ThetaEngine()
        theta_cfg = getattr(theta, "config", {}) or {}
        if not theta_cfg.get("enabled", False):
            logger.info("[Theta] 引擎未启用, 跳过")
            result["theta"] = {"enabled": False}
        else:
            # 滚仓检查 (到期前5个交易日)
            rollover_plan = theta.check_rollover()
            if rollover_plan:
                logger.info("[Theta] 检测到需滚仓头寸: %d 个", len(rollover_plan.get("positions", [])))
                result["theta"]["rollover"] = rollover_plan
            else:
                # 生成/刷新月度计划
                monthly_plan = theta.generate_monthly_plan()
                logger.info("[Theta] 月度Covered Call计划: %d 个头寸, 预期权利金 %.0f",
                            len(monthly_plan.get("positions", [])),
                            monthly_plan.get("total_est_premium", 0))
                result["theta"] = {
                    "enabled": True,
                    "plan_date": monthly_plan.get("plan_date"),
                    "positions_count": len(monthly_plan.get("positions", [])),
                    "total_premium": monthly_plan.get("total_est_premium", 0),
                    "monthly_return_pct": monthly_plan.get("portfolio_yield_monthly", 0),
                    "annualized_pct": monthly_plan.get("portfolio_yield_annualized", 0),
                    "plan_path": monthly_plan.get("plan_path", ""),
                }
    except Exception as e:
        logger.error(f"[Theta] 引擎执行失败: {e}", exc_info=True)
        result["theta"] = {"status": "ERROR", "error": str(e)}

    # === 2. Gamma/Vega引擎 — 尾部危机监控 ===
    try:
        if GammaEngine is None:
            raise ImportError("GammaEngine 未加载")
        gamma = GammaEngine()
        monitor_result = gamma.monitor()
        logger.info("[Gamma] 监控完成: MA60=%s, IV分位=%s, 触发=%s",
                    monitor_result.get("ma60_status"),
                    monitor_result.get("iv_percentile"),
                    monitor_result.get("triggered"))
        result["gamma"] = monitor_result
        # 若触发, 记录但不在此自动执行 (由 phase_execute 接管)
        if monitor_result.get("triggered"):
            logger.warning("[Gamma] 尾部对冲触发! 类型=%s, 预算=%.0f",
                           monitor_result.get("trigger_type"),
                           monitor_result.get("budget", 0))
    except Exception as e:
        logger.error(f"[Gamma] 引擎执行失败: {e}", exc_info=True)
        result["gamma"] = {"status": "ERROR", "error": str(e)}

    # === 3. 三级熔断协议 — 保证金占用率检查 ===
    try:
        if KillSwitch is None:
            raise ImportError("KillSwitch 未加载")
        ks = KillSwitch()
        ks_status = ks.check_margin_status()
        ks_level = int(ks_status.get("level", 0)) if isinstance(ks_status, dict) else 0
        logger.info("[KillSwitch] 当前熔断级别: L%d (%s), 保证金占用率: %.1f%%",
                    ks_level,
                    ks_status.get("level_name", "正常") if isinstance(ks_status, dict) else "未知",
                    (ks_status.get("margin_usage_ratio", 0) if isinstance(ks_status, dict) else 0) * 100)
        result["kill_switch"] = {
            "level": ks_level,
            "level_name": ks_status.get("level_name", "正常") if isinstance(ks_status, dict) else "未知",
            "margin_usage_ratio": ks_status.get("margin_usage_ratio", 0) if isinstance(ks_status, dict) else 0,
            "triggered": ks_level > 0,
        }
        if ks_level >= 1:
            logger.warning("[KillSwitch] L1触发: 停止新开仓, 进入防守模式")
        if ks_level >= 2:
            logger.error("[KillSwitch] L2触发: 强平深虚值期权空头!")
        if ks_level >= 3:
            logger.critical("[KillSwitch] L3触发: 变现红利ETF跨品种注入!")
            # 执行L3紧急协议
            try:
                ks.execute_kill_switch(3)
            except Exception as e3:
                logger.error(f"[KillSwitch] L3执行失败: {e3}")
    except Exception as e:
        logger.error(f"[KillSwitch] 检查失败: {e}", exc_info=True)
        result["kill_switch"] = {"status": "ERROR", "error": str(e)}

    # === 4. 2030清仓协议 — 阶段切换 + 预警 ===
    try:
        if LiquidationScheduler is None:
            raise ImportError("LiquidationScheduler 未加载")
        ls = LiquidationScheduler()
        current_phase = ls.get_current_phase()
        phase_num = current_phase.get("phase", 0) if current_phase else 0
        phase_name = current_phase.get("name", "未知") if current_phase else "未知"
        days_to_next = current_phase.get("days_to_next_phase") if current_phase else None
        logger.info("[Liquidation] 当前阶段: Phase %d (%s), 距下一阶段: %s 天",
                    phase_num, phase_name,
                    days_to_next if days_to_next is not None else "N/A")
        alert = ls.check_alert(days_threshold=30)
        result["liquidation"] = {
            "phase": phase_num,
            "phase_name": phase_name,
            "days_to_next": days_to_next,
            "alert": alert or {"alert": False},
        }
        if alert and alert.get("alert"):
            logger.warning("[Liquidation] 清仓预警: %s", alert.get("message", ""))
    except Exception as e:
        logger.error(f"[Liquidation] 调度器检查失败: {e}", exc_info=True)
        result["liquidation"] = {"status": "ERROR", "error": str(e)}

    # === 写入 state ===
    ctx.state["phases"]["hedge_fund"] = result
    logger.info("-" * 60)
    logger.info("Phase 4.5 完成: Theta=%s, Gamma触发=%s, 熔断级别=L%d, 清仓阶段=Phase %s",
                "OK" if result["theta"] else "SKIP",
                result["gamma"].get("triggered", False),
                result["kill_switch"].get("level", 0),
                result["liquidation"].get("phase", "UNKNOWN"))
    logger.info("=" * 60)
    return result
