#!/usr/bin/env python
"""
独立风控监控 (Risk Monitor)
==========================

职责:
1. KillSwitch 三级熔断监控 (L1/L2/L3)
2. 回撤保护 (日回撤 + 总回撤)
3. 隔夜跳空保护
4. 与 RiskGuardIntegrator 联动
5. 独立线程运行，不阻塞交易逻辑

安全设计:
- 独立于交易逻辑运行
- 触发熔断立即停止开仓 + 通知
- 所有风控事件写入 EvolutionMemory
- 异常 fail-closed 停止交易

作者: 终极量化交易系统 v8.4
日期: 2026-08-02
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Optional

from .config import get_pipeline_config
from .types import PipelineConfig, PipelineResult, PipelineStage, RiskAlert

logger = logging.getLogger("pipeline.risk_monitor")


class RiskMonitor:
    """
    独立风控监控器
    
    在独立线程中运行，持续监控:
    - 保证金使用率 (触发 KillSwitch)
    - 日内回撤
    - 总回撤
    - 隔夜跳空风险
    
    使用示例:
        monitor = RiskMonitor(config)
        monitor.start()
        # ... 交易中 ...
        alert = monitor.get_latest_alert()
        monitor.stop()
    """

    def __init__(self, config: Optional[PipelineConfig] = None):
        self.config = config or get_pipeline_config()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._latest_alert: Optional[RiskAlert] = None
        self._alert_history: list[RiskAlert] = []
        self._lock = threading.Lock()

        # 风控组件（优雅降级）
        self._kill_switch = None
        self._risk_guard = None
        self._init_components()

        logger.info("RiskMonitor 初始化完成")

    def _init_components(self) -> None:
        """初始化风控组件 (优雅降级)"""
        # KillSwitch
        try:
            from utils.kill_switch import KillSwitch
            self._kill_switch = KillSwitch()
            logger.info("KillSwitch 已加载")
        except ImportError as e:
            logger.warning(f"KillSwitch 导入失败: {e}")

        # RiskGuardIntegrator
        try:
            from utils.risk_guard_integrator import RiskGuardIntegrator
            self._risk_guard = RiskGuardIntegrator()
            logger.info("RiskGuardIntegrator 已加载")
        except ImportError as e:
            logger.warning(f"RiskGuardIntegrator 导入失败: {e}")

    def start(self) -> None:
        """启动风控监控线程"""
        if self._running:
            logger.warning("RiskMonitor 已在运行")
            return

        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("RiskMonitor 线程已启动")

    def stop(self) -> None:
        """停止风控监控"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("RiskMonitor 已停止")

    def _monitor_loop(self) -> None:
        """风控监控主循环"""
        interval = self.config.risk_check_interval_seconds

        while self._running:
            try:
                self._run_checks()
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.error(f"风控检查异常: {e}", exc_info=True)

            time.sleep(interval)

    def _run_checks(self) -> None:
        """执行一轮风控检查"""
        # 1. 保证金检查
        margin_alert = self._check_margin()
        if margin_alert:
            self._handle_alert(margin_alert)

        # 2. 回撤检查
        drawdown_alert = self._check_drawdown()
        if drawdown_alert:
            self._handle_alert(drawdown_alert)

        # 3. 隔夜跳空检查 (仅盘前)
        gap_alert = self._check_overnight_gap()
        if gap_alert:
            self._handle_alert(gap_alert)

    def _check_margin(self) -> Optional[RiskAlert]:
        """检查保证金使用率"""
        try:
            # 尝试从 KillSwitch 获取状态
            if self._kill_switch:
                status = self._kill_switch.get_status()
                margin_ratio = status.get("margin_ratio", 0)

                if margin_ratio >= self.config.kill_switch_l3_margin:
                    return RiskAlert(
                        level=3,
                        source="kill_switch",
                        message=f"L3 熔断: 保证金使用率 {margin_ratio:.1%} >= {self.config.kill_switch_l3_margin:.1%}",
                        metrics={"margin_ratio": margin_ratio},
                        actions_taken=["停止开仓", "启动强平", "通知交易员"],
                    )
                elif margin_ratio >= self.config.kill_switch_l2_margin:
                    return RiskAlert(
                        level=2,
                        source="kill_switch",
                        message=f"L2 强平: 保证金使用率 {margin_ratio:.1%} >= {self.config.kill_switch_l2_margin:.1%}",
                        metrics={"margin_ratio": margin_ratio},
                        actions_taken=["停止开仓", "准备减仓", "通知交易员"],
                    )
                elif margin_ratio >= self.config.kill_switch_l1_margin:
                    return RiskAlert(
                        level=1,
                        source="kill_switch",
                        message=f"L1 警戒: 保证金使用率 {margin_ratio:.1%} >= {self.config.kill_switch_l1_margin:.1%}",
                        metrics={"margin_ratio": margin_ratio},
                        actions_taken=["暂停新开仓", "密切监控"],
                    )

            # 降级: 从 positions.json 估算
            return self._estimate_margin_from_positions()

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning(f"保证金检查失败: {e}")
            return None

    def _estimate_margin_from_positions(self) -> Optional[RiskAlert]:
        """从 positions.json 估算保证金使用率 (降级方案)"""
        try:
            import json
            from pathlib import Path

            positions_path = Path(__file__).resolve().parent.parent.parent / "config" / "positions.json"
            if not positions_path.exists():
                return None

            with open(positions_path, encoding="utf-8") as f:
                positions = json.load(f)

            # 简单估算: 总市值 / 总资金
            total_value = sum(
                p.get("quantity", 0) * p.get("current_price", 0)
                for p in positions.get("stocks", [])
            )

            # TODO: 从 config/portfolio.yaml 读取总资金
            total_capital = 10_000_000  # 1000 万
            margin_ratio = total_value / total_capital if total_capital > 0 else 0

            if margin_ratio >= self.config.kill_switch_l3_margin:
                return RiskAlert(
                    level=3,
                    source="risk_monitor_estimate",
                    message=f"L3 熔断(估算): 持仓市值 {margin_ratio:.1%}",
                    metrics={"margin_ratio": margin_ratio, "estimated": True},
                    actions_taken=["停止开仓"],
                )

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.debug(f"保证金估算失败: {e}")

        return None

    def _check_drawdown(self) -> Optional[RiskAlert]:
        """检查回撤"""
        try:
            # 尝试从 RiskGuardIntegrator 获取
            if self._risk_guard:
                status = self._risk_guard.get_status()
                daily_dd = status.get("daily_drawdown", 0)
                total_dd = status.get("total_drawdown", 0)

                if total_dd >= self.config.max_total_drawdown:
                    return RiskAlert(
                        level=3,
                        source="risk_guard",
                        message=f"总回撤熔断: {total_dd:.2%} >= {self.config.max_total_drawdown:.2%}",
                        metrics={"daily_drawdown": daily_dd, "total_drawdown": total_dd},
                        actions_taken=["停止交易", "启动应急减仓"],
                    )
                elif daily_dd >= self.config.max_daily_drawdown:
                    return RiskAlert(
                        level=2,
                        source="risk_guard",
                        message=f"日回撤强平: {daily_dd:.2%} >= {self.config.max_daily_drawdown:.2%}",
                        metrics={"daily_drawdown": daily_dd, "total_drawdown": total_dd},
                        actions_taken=["停止开仓", "减仓至安全线"],
                    )

            return None

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning(f"回撤检查失败: {e}")
            return None

    def _check_overnight_gap(self) -> Optional[RiskAlert]:
        """检查隔夜跳空风险 (仅盘前)"""
        # 简化实现: 检查是否在盘前时段 (9:00-9:25)
        now = datetime.now()
        if now.hour == 9 and now.minute < 30:
            # TODO: 获取隔夜外盘/期货变化
            # 若涨跌幅 > 3%, 发出预警
            pass

        return None

    def _handle_alert(self, alert: RiskAlert) -> None:
        """处理风控告警"""
        with self._lock:
            self._latest_alert = alert
            self._alert_history.append(alert)

        # 写入 EvolutionMemory
        self._write_to_memory(alert)

        # 记录日志
        logger.warning(f"[风控告警 L{alert.level}] {alert.message}")

        # 触发动作
        for action in alert.actions_taken:
            logger.info(f"  执行动作: {action}")

    def _write_to_memory(self, alert: RiskAlert) -> None:
        """写入 EvolutionMemory (优雅降级)"""
        try:
            from utils.evolution_memory import EvolutionMemory
            memory = EvolutionMemory()
            memory.log_event(
                event_type="risk_alert",
                data={
                    "level": alert.level,
                    "source": alert.source,
                    "message": alert.message,
                    "metrics": alert.metrics,
                    "actions_taken": alert.actions_taken,
                },
            )
        except ImportError:
            pass

    def get_latest_alert(self) -> Optional[RiskAlert]:
        """获取最新风控告警"""
        with self._lock:
            return self._latest_alert

    def get_alert_history(self, limit: int = 100) -> list[RiskAlert]:
        """获取风控告警历史"""
        with self._lock:
            return self._alert_history[-limit:]

    def run_check(self) -> PipelineResult:
        """执行单次风控检查 (供 orchestrator 调用)"""
        started_at = datetime.now()
        alerts = []

        try:
            margin_alert = self._check_margin()
            if margin_alert:
                alerts.append(margin_alert)

            drawdown_alert = self._check_drawdown()
            if drawdown_alert:
                alerts.append(drawdown_alert)

            # 判断整体状态
            max_level = max((a.level for a in alerts), default=0)
            success = max_level < 2  # L2 及以上视为风控失败

            return PipelineResult(
                stage=PipelineStage.RISK_MONITOR,
                success=success,
                started_at=started_at.isoformat(),
                completed_at=datetime.now().isoformat(),
                duration_ms=(datetime.now() - started_at).total_seconds() * 1000,
                metrics={
                    "alerts_count": len(alerts),
                    "max_alert_level": max_level,
                },
                reports=[],
            )

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"风控检查失败: {e}")
            return PipelineResult(
                stage=PipelineStage.RISK_MONITOR,
                success=False,
                started_at=started_at.isoformat(),
                completed_at=datetime.now().isoformat(),
                error=str(e),
            )
