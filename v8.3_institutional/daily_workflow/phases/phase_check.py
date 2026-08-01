#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase implementation: phase_check

Extracted from original DailyWorkflow class for modularization.
This module contains the standalone phase function implementing the phase_check phase.

The function receives a DailyWorkflow instance as its first parameter ("workflow").
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.workflow_orchestrator import DailyWorkflow

logger = logging.getLogger(__name__)


def phase_check(workflow: "DailyWorkflow") -> bool:
        """系统自检"""
        logger.info("=" * 60)
        logger.info(f"Phase 1: 系统自检 @ {workflow.trade_date}")
        logger.info("=" * 60)

        # 十五五年度阶段提示
        if workflow.current_phase_info is not None:
            pi = workflow.current_phase_info
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
            if workflow.phase_manager and workflow.phase_manager.is_quarter_end(
                datetime.strptime(workflow.trade_date, "%Y-%m-%d").date()
                if workflow.trade_date else None
            ):
                logger.info("[十五五阶段] 季度末 - 将在 v10_risk 阶段触发季度评估")

        checks = {
            "v75_modules": V75_READY,
            "shenhua_plan": SHENHUA_READY,
            "ntp_sync": False,
            "risk_manager": False,
            "circuit_breaker": False,
            "phase_manager": PHASE_MANAGER_READY,
            "hedge_fund_core": HEDGE_FUND_CORE_READY,
            "hedge_fund_modules": HEDGE_FUND_MODULES_READY,
            "institutional_modules": INSTITUTIONAL_MODULES_READY,
            "risk_mgmt_modules": RISK_MGT_MODULES_READY,
            "alpha_modules": ALPHA_MODULES_READY,
            "execution_modules": EXECUTION_MODULES_READY,
            "alt_data_modules": ALT_DATA_MODULES_READY,
            "v85_modules": V85_READY,
            "v85_module_count": 9 - len(_V85_FAILURES),
            "v85_failures": _V85_FAILURES,
        }

        workflow.ntp = NTPSync()

        if not V75_READY:
            logger.warning("v7.5 模块未就绪，进入降级模式继续执行")
            checks["v75_modules"] = False
            workflow.state["phases"]["check"] = {"status": "PASS", "checks": checks, "degraded": True}
            return True

        # === v8.5: 增强 NTP 时间同步 (TimeSync) ===
        _ntp_instance = None
        try:
            if V85_READY:
                from utils.timesync import TimeSync
                _ntp_instance = TimeSync()
                ts_result = _ntp_instance.validate()
                checks["ntp_sync"] = ts_result.get("synced", False)
                checks["time_drift_ms"] = ts_result.get("drift_ms", 0)
                logger.info(
                    f"[v8.5] TimeSync: 同步={'OK' if checks['ntp_sync'] else 'DRIFT'}"
                    f", 漂移={ts_result.get('drift_ms', 0):.1f}ms"
                )
            else:
                _ntp_instance = NTPSync()
                offset = _ntp_instance.get_offset()
                checks["ntp_sync"] = abs(offset) < 0.05
                logger.info(f"NTP 同步: offset={offset:.3f}s {'OK' if checks['ntp_sync'] else 'DRIFT'}")
        except Exception as e:
            # v8.6.8 P0-03 FIX (2026-07-26): NTP 同步失败改 fail-closed
            # 原始 bug: checks["ntp_sync"] = True 假装健康, 与其他 fail-closed 处理矛盾
            # 修复原则 (与 KillSwitch/风控对齐): NTP 不可信时禁止开仓
            # 时钟漂移可能导致: 集合竞价订单错失/收盘订单错失/监管报送异常
            logger.error(f"NTP 同步失败 (fail-closed, 禁止开仓): {e}")
            checks["ntp_sync"] = False  # v8.6.8 P0-03: 不再降级允许, 必须真实健康
            checks["ntp_failure_reason"] = str(e)
            checks["ntp_fail_closed"] = True  # 标记触发 fail-closed
            # 创建降级 NTPSync 实例, 但标记不健康 (drift_ms 返回 0, is_healthy 返回 False)
            try:
                workflow.ntp = NTPSync()
                # 强制标记为不健康状态 (offset 未知, 视为不可信)
                if hasattr(workflow.ntp, 'sync_failed_count'):
                    workflow.ntp.sync_failed_count = 99  # 触发 is_healthy=False
            except Exception:
                workflow.ntp = None

        # === v8.5: 数据管道健康检查 ===
        try:
            if V85_READY:
                from data.data_pipeline import DataPipeline
                dp = DataPipeline()
                dp_status = dp.health_check()
                checks["data_pipeline"] = dp_status
                logger.info(
                    f"[v8.5] DataPipeline: 状态={dp_status.get('status', 'UNKNOWN')}"
                    f", 延迟={dp_status.get('latency_seconds', -1):.1f}s"
                )
            else:
                checks["data_pipeline"] = {"status": "SKIP", "reason": "v85_not_ready"}
        except Exception as e:
            logger.warning(f"[v8.5] DataPipeline 健康检查异常 (非致命): {e}")
            checks["data_pipeline"] = {"status": "ERROR", "error": str(e)}

        # 风控初始化
        try:
            workflow.rm = RiskManager(total_capital=workflow.capital)
            # 修复 P0: CircuitBreaker 必须传入 name 参数（否则 __init__ 抛 TypeError）
            workflow.cb = CircuitBreaker(name="daily_workflow")
            checks["risk_manager"] = True
            checks["circuit_breaker"] = True
            logger.info(f"风险模式: {workflow.rm.mode}, 仓位系数: {workflow.rm.position_size_factor}")
        except Exception as e:
            logger.error(f"风控初始化失败: {e}")
            checks["risk_manager"] = False
            checks["circuit_breaker"] = False
            # 修复 P0: fail-closed — 风控核心失败时禁止交易（而非继续执行）
            workflow.state["phases"]["check"] = {
                "status": "FAIL",
                "checks": checks,
                "degraded": True,
                "fail_closed": True,
                "reason": f"风控初始化失败, 已进入 fail-closed 模式, 禁止开仓: {e}",
            }
            logger.critical("风控初始化失败, 进入 fail-closed 模式, 禁止开仓")
            return True

        # v8.6.8 P0-03: NTP fail-closed — 若 ntp_sync 检查失败, 禁止开仓
        # _ntp_instance 在上方 try/except 中赋值, V85 时为 TimeSync, 否则为 NTPSync
        try:
            if checks.get("ntp_sync") and _ntp_instance is not None:
                workflow.ntp = _ntp_instance
            else:
                if not hasattr(workflow, 'ntp') or workflow.ntp is None:
                    workflow.ntp = NTPSync()
                logger.error("[Phase 1] NTP 同步失败, 进入 fail-closed 模式, 禁止开仓")
        except Exception:
            workflow.ntp = NTPSync() if not (hasattr(workflow, 'ntp') and workflow.ntp is not None) else workflow.ntp

        # v8.6.8 P0-03: NTP fail-closed 时, phase_check 标记 fail_closed
        if not checks.get("ntp_sync", True):
            workflow.state["phases"]["check"] = {
                "status": "FAIL",
                "checks": checks,
                "degraded": True,
                "fail_closed": True,
                "reason": (
                    "NTP 同步失败, 进入 fail-closed 模式, 禁止开仓. "
                    "时钟漂移可能导致集合竞价/收盘订单错失/监管报送异常. "
                    f"失败原因: {checks.get('ntp_failure_reason', 'unknown')}"
                ),
            }
            logger.critical("NTP fail-closed 已激活, 禁止开仓直到 NTP 恢复")
            return True
        workflow.state["phases"]["check"] = {
            "status": "PASS",
            "checks": checks,
            "v85_active": V85_READY,
            "v85_module_count": 9 - len(_V85_FAILURES),
        }
        logger.info(
            "Phase 1 完成: 自检通过 | v8.5模块=%d/9 | 风控=%s | 对冲核心=%s",
            9 - len(_V85_FAILURES),
            checks.get("risk_manager", "N/A"),
            checks.get("hedge_fund_core", "N/A"),
        )
        return True

    # --------------------------------------------------------
    # Phase 1.5: 收益预测动态校准 (新增)
    #   - Step 1: Wind MCP 拉取最新日K, 更新 returns_history.json + market_returns.json
    #   - Step 2: 计算已实现年化收益率
    #   - Step 3: 校准 portfolio_return_projection.json 概率权重
    # --------------------------------------------------------

