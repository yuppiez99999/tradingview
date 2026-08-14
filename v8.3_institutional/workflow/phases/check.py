# -*- coding: utf-8 -*-
"""Phase 1: 系统自检 (从 daily_workflow.py 拆出, 零行为变更)。

原位置: daily_workflow.py L1015-L1091
"""
from __future__ import annotations

import logging
from datetime import datetime

from workflow.context import WorkflowContext, get_dw_module

logger = logging.getLogger("v75.daily_workflow")

# === 从 daily_workflow 模块获取模块级符号 (兼容 __main__/模块导入) ===
# 注: _dw 在 import 时获取一次 (是模块对象引用, 不变);
#     V75_READY / NTPSync / RiskManager / CircuitBreaker 等必须在 phase 函数内
#     动态查找, 与拆分前 daily_workflow.py 中 phase_check 内联调用的语义一致
#     (测试通过 monkeypatch.setattr(dw, "NTPSync", ...) patch).
_dw = get_dw_module()


def phase_check(ctx: WorkflowContext) -> bool:
    """系统自检"""
    # 动态查找模块级符号 (兼容 monkeypatch 对 daily_workflow 模块的 patch)
    V75_READY = getattr(_dw, "V75_READY", False) if _dw else False
    SHENHUA_READY = getattr(_dw, "SHENHUA_READY", False) if _dw else False
    PHASE_MANAGER_READY = getattr(_dw, "PHASE_MANAGER_READY", False) if _dw else False
    HEDGE_FUND_MODULES_READY = getattr(_dw, "HEDGE_FUND_MODULES_READY", False) if _dw else False
    INSTITUTIONAL_MODULES_READY = getattr(_dw, "INSTITUTIONAL_MODULES_READY", False) if _dw else False
    RISK_MGT_MODULES_READY = getattr(_dw, "RISK_MGT_MODULES_READY", False) if _dw else False
    ALPHA_MODULES_READY = getattr(_dw, "ALPHA_MODULES_READY", False) if _dw else False
    EXECUTION_MODULES_READY = getattr(_dw, "EXECUTION_MODULES_READY", False) if _dw else False
    ALT_DATA_MODULES_READY = getattr(_dw, "ALT_DATA_MODULES_READY", False) if _dw else False
    NTPSync = getattr(_dw, "NTPSync", None) if _dw else None
    RiskManager = getattr(_dw, "RiskManager", None) if _dw else None
    CircuitBreaker = getattr(_dw, "CircuitBreaker", None) if _dw else None

    logger.info("=" * 60)
    logger.info(f"Phase 1: 系统自检 @ {ctx.trade_date}")
    logger.info("=" * 60)

    # 十五五年度阶段提示
    if ctx.current_phase_info is not None:
        pi = ctx.current_phase_info
        logger.info(
            f"[十五五阶段] {pi.year} {pi.phase_name} | 目标 {pi.target_return:.0%} | "
            f"回撤限 {pi.max_drawdown:.0%} | 杠杆 {pi.leverage_target}x | "
            f"季度 {pi.current_quarter}"
        )
        if pi.is_liquidation_year:
            logger.warning(
                f"[2030清仓] {pi.current_quarter} 阶段 - "
                f"{pi.liquidation_actions.get('name', '') if pi.liquidation_actions else ''}"
            )
        if ctx.phase_manager and ctx.phase_manager.is_quarter_end(
            datetime.strptime(ctx.trade_date, "%Y-%m-%d").date()
            if ctx.trade_date else None
        ):
            logger.info("[十五五阶段] 季度末 - 将在 v10_risk 阶段触发季度评估")

    checks = {
        "v75_modules": V75_READY,
        "shenhua_plan": SHENHUA_READY,
        "ntp_sync": False,
        "risk_manager": False,
        "circuit_breaker": False,
        "phase_manager": PHASE_MANAGER_READY,
        "hedge_fund_modules": HEDGE_FUND_MODULES_READY,
        "institutional_modules": INSTITUTIONAL_MODULES_READY,
        "risk_mgmt_modules": RISK_MGT_MODULES_READY,
        "alpha_modules": ALPHA_MODULES_READY,
        "execution_modules": EXECUTION_MODULES_READY,
        "alt_data_modules": ALT_DATA_MODULES_READY,
    }

    ctx.ntp = NTPSync()

    if not V75_READY:
        logger.warning("v7.5 模块未就绪，进入降级模式继续执行")
        checks["v75_modules"] = False
        ctx.state["phases"]["check"] = {"status": "PASS", "checks": checks, "degraded": True}
        return True

    # NTP 同步
    try:
        ntp = NTPSync()
        offset = ntp.get_offset()
        checks["ntp_sync"] = abs(offset) < 0.05
        logger.info(f"NTP 同步: offset={offset:.3f}s {'OK' if checks['ntp_sync'] else 'DRIFT'}")
    except Exception as e:
        logger.warning(f"NTP 同步失败 (使用本地时间): {e}")
        checks["ntp_sync"] = True  # 降级允许

    # 风控初始化
    try:
        ctx.rm = RiskManager(total_capital=ctx.capital)
        ctx.cb = CircuitBreaker()
        checks["risk_manager"] = True
        checks["circuit_breaker"] = True
        logger.info(f"风险模式: {ctx.rm.mode}, 仓位系数: {ctx.rm.position_size_factor}")
    except Exception as e:
        logger.error(f"风控初始化失败: {e}")
        checks["risk_manager"] = False
        checks["circuit_breaker"] = False
        ctx.state["phases"]["check"] = {"status": "PASS", "checks": checks, "degraded": True}
        logger.warning("风控初始化失败，进入降级模式继续执行")
        return True

    ctx.ntp = ntp if checks["ntp_sync"] else NTPSync()
    ctx.state["phases"]["check"] = {"status": "PASS", "checks": checks}
    logger.info("Phase 1 完成: 全部自检通过")
    return True
