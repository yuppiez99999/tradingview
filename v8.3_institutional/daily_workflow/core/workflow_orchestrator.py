#!/usr/bin/env python3
"""工作流编排器 — 核心调度逻辑，仅负责按顺序调用各 phase。"""

from __future__ import annotations

import logging
from typing import Optional

from .config.workflow_config import CircuitLevel, WorkflowConfig
from .phases.phase_autolearn import phase_autolearn as phase_autolearn_impl
from .phases.phase_calibrate import phase_calibrate as phase_calibrate_impl
from .phases.phase_check import phase_check as phase_check_impl
from .phases.phase_execute import phase_execute as phase_execute_impl
from .phases.phase_factor_kill import phase_factor_kill as phase_factor_kill_impl
from .phases.phase_hedge import phase_hedge as phase_hedge_impl
from .phases.phase_market import phase_market as phase_market_impl
from .phases.phase_report import phase_report as phase_report_impl
from .phases.phase_risk import phase_risk as phase_risk_impl
from .phases.phase_shadow import phase_shadow as phase_shadow_impl
from .phases.phase_signal import phase_signal as phase_signal_impl

logger = logging.getLogger("v75.workflow.orchestrator")


class DailyWorkflow:
    def __init__(self, config: Optional[WorkflowConfig] = None):
        self.config = config or WorkflowConfig.default()
        self.trade_date = self.config.trade_date
        self.circuit_level = CircuitLevel.LEVEL_1

    def run(self, phase_name: Optional[str] = None) -> bool:
        if phase_name:
            logger.info(f"模式：单相执行 — phase={phase_name}")
            return self._run_single_phase(phase_name)

        logger.info(f"模式：全量工作流 — trade_date={self.trade_date}")
        phases = [
            ("check", phase_check_impl), ("calibrate", phase_calibrate_impl), ("market", phase_market_impl),
            ("risk", phase_risk_impl), ("hedge", phase_hedge_impl), ("signal", phase_signal_impl),
            ("execute", phase_execute_impl), ("report", phase_report_impl), ("autolearn", phase_autolearn_impl),
            ("factor_kill_switch", phase_factor_kill_impl), ("shadow_monitor", phase_shadow_impl),
        ]
        for name, func in phases:
            logger.info(f"▶ 开始执行 phase: {name}")
            try:
                func(self)
            except Exception as e:
                logger.error(f"❌ phase {name} 发生异常: {e}")
                self._trigger_circuit_breaker(name, e)
                return False
            logger.info(f"✓ phase {name} 完成")
        logger.info("✅ 所有 phase 顺利完成")
        return True

    def _run_single_phase(self, phase_name: str) -> bool:
        phase_map = {"check": phase_check_impl, "calibrate": phase_calibrate_impl, "market": phase_market_impl,
                     "risk": phase_risk_impl, "hedge": phase_hedge_impl, "signal": phase_signal_impl,
                     "execute": phase_execute_impl, "report": phase_report_impl, "autolearn": phase_autolearn_impl,
                     "factor_kill_switch": phase_factor_kill_impl, "shadow_monitor": phase_shadow_impl}
        if phase_name not in phase_map:
            logger.error(f"未知 phase: {phase_name}, 可选值: {list(phase_map.keys())}")
            return False
        func = phase_map[phase_name]
        logger.info(f"执行单相: {phase_name}")
        try:
            func(self)
            logger.info(f"✓ phase {phase_name} 完成")
            return True
        except Exception as e:
            logger.error(f"❌ phase {phase_name} 异常: {e}")
            self._trigger_circuit_breaker(phase_name, e)
            return False

    def _trigger_circuit_breaker(self, phase_name: str, exc: Exception) -> None:
        self.circuit_level = CircuitLevel.LEVEL_2
        logger.warning(f"⚡ 熔断触发 level={self.circuit_level}, phase={phase_name}, error={exc}")

    def get_available_phases(self) -> list[str]:
        return ["check", "calibrate", "market", "risk", "hedge", "signal", "execute", "report", "autolearn", "factor_kill_switch", "shadow_monitor"]
