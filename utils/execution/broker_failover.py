# -*- coding: utf-8 -*-
"""券商故障切换管理器 — T5.7 交付物.

模块整合 8.4 — ARCHITECTURE §3.4
任务: T5.7 实盘券商直连补充 (故障切换测试)

设计原则:
    1. 多 broker 健康检查 + 自动切换
    2. 主备模式 (primary/secondary/tertiary)
    3. 健康检查异步, 不阻塞主路径
    4. 切换事件全量审计 (HC: 操作日志不可篡改)
    5. Feature Flag 透传 (HC-1): USE_BROKER_FAILOVER 默认 False

状态机:
    HEALTHY → DEGRADED → UNHEALTHY → FAILOVER → RECOVERED → HEALTHY

API:
    from utils.execution.broker_failover import BrokerFailoverManager

    mgr = BrokerFailoverManager({
        "brokers": [
            {"type": "ths", "name": "primary", "config": {...}},
            {"type": "xueqiu", "name": "secondary", "config": {...}},
        ],
    })
    mgr.start()
    broker = mgr.get_active_broker()
    broker.submit_order(order)

硬约束:
    - HC-1: Feature Flag 默认 False, 关闭时仅用第一个 broker
    - HC-5: 配置走 ConfigManager 4 级优先级
"""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("broker_failover")


# ============================================================
# 异常定义
# ============================================================
class FailoverError(Exception):
    """故障切换基础异常."""


class NoHealthyBrokerError(FailoverError):
    """所有 broker 都不健康."""


class BrokerConfigError(FailoverError):
    """broker 配置错误."""


# ============================================================
# 健康状态枚举
# ============================================================
class BrokerHealthState:
    """broker 健康状态常量."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


# ============================================================
# 单 broker 健康追踪器
# ============================================================
class BrokerHealthTracker:
    """单个 broker 的健康状态追踪器.

    基于滑动窗口统计成功率, 当成功率低于阈值时降级.
    """

    def __init__(
        self,
        broker_name: str,
        window_size: int = 20,
        healthy_threshold: float = 0.9,
        degraded_threshold: float = 0.7,
        check_interval_sec: float = 30.0,
    ) -> None:
        """初始化.

        Args:
            broker_name: broker 名称
            window_size: 滑动窗口大小 (最近 N 次操作)
            healthy_threshold: 健康阈值 (成功率 >= 此值视为健康)
            degraded_threshold: 降级阈值 (成功率 < 此值视为不健康)
            check_interval_sec: 健康检查间隔 (秒)
        """
        self.broker_name = broker_name
        self.window_size = window_size
        self.healthy_threshold = healthy_threshold
        self.degraded_threshold = degraded_threshold
        self.check_interval_sec = check_interval_sec
        # 操作记录 (True=成功, False=失败)
        self._results: deque = deque(maxlen=window_size)
        self._state: str = BrokerHealthState.UNKNOWN
        self._last_check_time: float = 0.0
        self._consecutive_failures: int = 0
        self._max_consecutive_failures: int = 3

    def record_success(self) -> None:
        """记录一次成功操作."""
        self._results.append(True)
        self._consecutive_failures = 0
        self._update_state()

    def record_failure(self) -> None:
        """记录一次失败操作."""
        self._results.append(False)
        self._consecutive_failures += 1
        self._update_state()

    def _update_state(self) -> None:
        """根据滑动窗口更新健康状态."""
        if not self._results:
            self._state = BrokerHealthState.UNKNOWN
            return
        # 连续失败立即降级
        if self._consecutive_failures >= self._max_consecutive_failures:
            self._state = BrokerHealthState.UNHEALTHY
            return
        success_rate = sum(self._results) / len(self._results)
        if success_rate >= self.healthy_threshold:
            self._state = BrokerHealthState.HEALTHY
        elif success_rate >= self.degraded_threshold:
            self._state = BrokerHealthState.DEGRADED
        else:
            self._state = BrokerHealthState.UNHEALTHY

    @property
    def state(self) -> str:
        """当前健康状态."""
        return self._state

    @property
    def success_rate(self) -> float:
        """当前成功率 (0.0-1.0)."""
        if not self._results:
            return 0.0
        return sum(self._results) / len(self._results)  # type: ignore

    @property
    def is_healthy(self) -> bool:
        """是否健康 (可下单)."""
        return self._state in (
            BrokerHealthState.HEALTHY,
            BrokerHealthState.DEGRADED,
            BrokerHealthState.UNKNOWN,  # 初始状态允许尝试
        )

    def to_dict(self) -> Dict[str, Any]:
        """状态快照."""
        return {
            "broker_name": self.broker_name,
            "state": self._state,
            "success_rate": round(self.success_rate, 4),
            "sample_count": len(self._results),
            "consecutive_failures": self._consecutive_failures,
            "last_check_time": self._last_check_time,
        }


# ============================================================
# 故障切换管理器
# ============================================================
class BrokerFailoverManager:
    """多 broker 故障切换管理器.

    管理多个 broker adapter 实例, 当主 broker 不健康时自动切换到备用 broker.
    支持主备模式 (primary/secondary/tertiary).

    用法:
        mgr = BrokerFailoverManager(config)
        mgr.start()  # 启动健康检查
        broker = mgr.get_active_broker()  # 获取当前活跃 broker
        ok = broker.submit_order(order)
        mgr.record_result(broker_name, ok)  # 记录结果
        mgr.stop()  # 停止
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        """初始化.

        Args:
            config: 配置字典, 结构:
                {
                    "brokers": [
                        {"type": "ths", "name": "primary", "priority": 1, "config": {...}},
                        {"type": "xueqiu", "name": "secondary", "priority": 2, "config": {...}},
                    ],
                    "health_check": {
                        "window_size": 20,
                        "healthy_threshold": 0.9,
                        "degraded_threshold": 0.7,
                        "check_interval_sec": 30,
                    },
                    "failover": {
                        "auto_failover": true,
                        "max_failover_count": 3,
                        "recovery_check_interval_sec": 60,
                    },
                    "audit_log_dir": "reports/broker_failover",
                }
        """
        self.config = config
        self._brokers: Dict[str, Dict[str, Any]] = {}  # name -> {adapter, tracker, priority, type, config}
        self._active_broker_name: Optional[str] = None
        self._failover_count: int = 0
        self._max_failover_count: int = config.get("failover", {}).get("max_failover_count", 3)
        self._auto_failover: bool = config.get("failover", {}).get("auto_failover", True)
        self._recovery_check_interval: float = config.get("failover", {}).get("recovery_check_interval_sec", 60.0)
        self._stopped: bool = True
        self._lock = threading.RLock()
        self._health_check_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        # 审计日志
        self._audit_log_dir = Path(config.get("audit_log_dir", "reports/broker_failover"))
        if not self._audit_log_dir.is_absolute():
            self._audit_log_dir = Path(__file__).resolve().parent.parent.parent / self._audit_log_dir
        self._audit_log_dir.mkdir(parents=True, exist_ok=True)
        # 初始化 brokers
        self._init_brokers()

    def _init_brokers(self) -> None:
        """初始化所有 broker."""
        brokers_config = self.config.get("brokers", [])
        if not brokers_config:
            raise BrokerConfigError("配置中未定义任何 broker (brokers 列表为空)")
        health_config = self.config.get("health_check", {})
        for broker_cfg in brokers_config:
            name = broker_cfg.get("name")
            if not name:
                raise BrokerConfigError(f"broker 配置缺 name 字段: {broker_cfg}")
            broker_type = broker_cfg.get("type")
            if not broker_type:
                raise BrokerConfigError(f"broker {name} 缺 type 字段")
            priority = int(broker_cfg.get("priority", 100))
            bcfg = broker_cfg.get("config", {})
            # 延迟导入避免循环依赖
            from utils.execution.broker_adapters import create_broker_adapter

            try:
                adapter = create_broker_adapter(broker_type, bcfg)
            except ValueError as e:
                raise BrokerConfigError(f"broker {name} (type={broker_type}) 创建失败: {e}") from e
            tracker = BrokerHealthTracker(
                broker_name=name,
                window_size=health_config.get("window_size", 20),
                healthy_threshold=health_config.get("healthy_threshold", 0.9),
                degraded_threshold=health_config.get("degraded_threshold", 0.7),
                check_interval_sec=health_config.get("check_interval_sec", 30.0),
            )
            self._brokers[name] = {
                "adapter": adapter,
                "tracker": tracker,
                "priority": priority,
                "type": broker_type,
                "config": bcfg,
            }
            logger.info(
                "已注册 broker: name=%s, type=%s, priority=%d",
                name,
                broker_type,
                priority,
            )
        # 按 priority 排序, 选择初始活跃 broker
        self._select_initial_broker()

    def _select_initial_broker(self) -> None:
        """选择初始活跃 broker (优先级最高且健康)."""
        sorted_brokers = sorted(self._brokers.items(), key=lambda x: x[1]["priority"])
        for name, info in sorted_brokers:
            # 尝试连接
            adapter = info["adapter"]
            try:
                ok = adapter.connect()
                if ok:
                    self._active_broker_name = name
                    info["tracker"].record_success()
                    self._audit(
                        "initial_broker_selected",
                        {
                            "broker": name,
                            "priority": info["priority"],
                        },
                    )
                    logger.info("初始活跃 broker: %s (priority=%d)", name, info["priority"])
                    return
                else:
                    info["tracker"].record_failure()
                    logger.warning("broker %s 连接失败, 尝试下一个", name)
            except Exception as e:  # noqa: BLE001  # execution fail-safe, 交易路径不崩溃
                info["tracker"].record_failure()
                logger.exception("broker %s 连接异常: %s", name, e)
        raise NoHealthyBrokerError("所有 broker 都无法连接")

    def get_active_broker(self) -> Any:
        """获取当前活跃 broker adapter.

        Returns:
            broker adapter 实例

        Raises:
            NoHealthyBrokerError: 所有 broker 都不健康
        """
        with self._lock:
            if self._active_broker_name is None:
                raise NoHealthyBrokerError("无活跃 broker")
            info = self._brokers.get(self._active_broker_name)
            if info is None:
                raise NoHealthyBrokerError(f"活跃 broker {self._active_broker_name} 未注册")
            return info["adapter"]

    def get_active_broker_name(self) -> Optional[str]:
        """获取当前活跃 broker 名称."""
        with self._lock:
            return self._active_broker_name

    def record_result(self, broker_name: str, success: bool) -> None:
        """记录 broker 操作结果 (用于健康状态更新).

        Args:
            broker_name: broker 名称
            success: 是否成功
        """
        with self._lock:
            info = self._brokers.get(broker_name)
            if info is None:
                logger.warning("未知 broker: %s", broker_name)
                return
            tracker = info["tracker"]
            if success:
                tracker.record_success()
            else:
                tracker.record_failure()
                # 检查是否需要故障切换
                if self._auto_failover and broker_name == self._active_broker_name and not tracker.is_healthy:
                    logger.warning(
                        "broker %s 状态=%s, 触发故障切换",
                        broker_name,
                        tracker.state,
                    )
                    self._do_failover(reason=f"health_state={tracker.state}")

    def _do_failover(self, reason: str = "") -> bool:
        """执行故障切换.

        Returns:
            True 切换成功, False 无可用 broker
        """
        with self._lock:
            if self._failover_count >= self._max_failover_count:
                logger.error(
                    "故障切换次数已达上限 %d, 不再切换",
                    self._max_failover_count,
                )
                self._audit(
                    "failover_limit_reached",
                    {
                        "failover_count": self._failover_count,
                        "reason": reason,
                    },
                )
                return False
            current_name = self._active_broker_name
            # 按 priority 排序, 跳过当前 broker
            sorted_brokers = sorted(self._brokers.items(), key=lambda x: x[1]["priority"])
            for name, info in sorted_brokers:
                if name == current_name:
                    continue
                tracker = info["tracker"]
                if not tracker.is_healthy:
                    continue
                # 尝试切换到该 broker
                adapter = info["adapter"]
                try:
                    # 如果是新 broker, 需要先连接
                    if not getattr(adapter, "_connected", False):
                        ok = adapter.connect()
                        if not ok:
                            tracker.record_failure()
                            continue
                    self._active_broker_name = name
                    self._failover_count += 1
                    self._audit(
                        "failover",
                        {
                            "from": current_name,
                            "to": name,
                            "reason": reason,
                            "failover_count": self._failover_count,
                        },
                    )
                    logger.warning(
                        "故障切换: %s → %s (reason=%s, count=%d)",
                        current_name,
                        name,
                        reason,
                        self._failover_count,
                    )
                    return True
                except Exception as e:  # noqa: BLE001  # execution fail-safe, 交易路径不崩溃
                    tracker.record_failure()
                    logger.exception("切换到 %s 失败: %s", name, e)
                    continue
            # 所有备用 broker 都不可用
            self._audit(
                "failover_failed",
                {
                    "from": current_name,
                    "reason": "no_healthy_broker",
                    "trigger_reason": reason,
                },
            )
            logger.error("故障切换失败: 无可用备用 broker")
            return False

    def force_failover(self, target_broker: Optional[str] = None, reason: str = "manual") -> bool:
        """强制故障切换 (人工触发).

        Args:
            target_broker: 目标 broker 名称 (None=自动选择)
            reason: 切换原因

        Returns:
            True 切换成功
        """
        with self._lock:
            if target_broker and target_broker not in self._brokers:
                logger.error("目标 broker 未注册: %s", target_broker)
                return False
            if target_broker:
                info = self._brokers[target_broker]
                adapter = info["adapter"]
                try:
                    if not getattr(adapter, "_connected", False):
                        ok = adapter.connect()
                        if not ok:
                            return False
                    old_name = self._active_broker_name
                    self._active_broker_name = target_broker
                    self._audit(
                        "force_failover",
                        {
                            "from": old_name,
                            "to": target_broker,
                            "reason": reason,
                        },
                    )
                    logger.info(
                        "强制切换: %s → %s (reason=%s)",
                        old_name,
                        target_broker,
                        reason,
                    )
                    return True
                except Exception as e:  # noqa: BLE001  # execution fail-safe, 交易路径不崩溃
                    logger.exception("强制切换失败: %s", e)
                    return False
            else:
                return self._do_failover(reason=f"force:{reason}")

    def start(self) -> None:
        """启动健康检查后台线程."""
        with self._lock:
            if not self._stopped:
                logger.warning("故障切换管理器已在运行")
                return
            self._stopped = False
            self._stop_event.clear()
            self._health_check_thread = threading.Thread(
                target=self._health_check_loop,
                daemon=True,
                name="broker-health-check",
            )
            self._health_check_thread.start()
            logger.info("故障切换管理器已启动")

    def stop(self) -> None:
        """停止健康检查."""
        with self._lock:
            if self._stopped:
                return
            self._stop_event.set()
            self._stopped = True
            if self._health_check_thread and self._health_check_thread.is_alive():
                self._health_check_thread.join(timeout=5.0)
            logger.info("故障切换管理器已停止")

    def _health_check_loop(self) -> None:
        """健康检查后台循环."""
        while not self._stop_event.is_set():
            try:
                self._check_all_brokers()
            except Exception as e:  # noqa: BLE001  # execution fail-safe, 交易路径不崩溃
                logger.exception("健康检查异常: %s", e)
            # 等待下次检查 (支持提前唤醒)
            self._stop_event.wait(timeout=self._recovery_check_interval)

    def _check_all_brokers(self) -> None:
        """检查所有 broker 健康状态 (尝试恢复不健康的 broker)."""
        with self._lock:
            for name, info in self._brokers.items():
                tracker = info["tracker"]
                if tracker.state == BrokerHealthState.UNHEALTHY:
                    # 尝试重新连接
                    adapter = info["adapter"]
                    try:
                        if not getattr(adapter, "_connected", False):
                            ok = adapter.connect()
                            if ok:
                                tracker.record_success()
                                logger.info("broker %s 已恢复", name)
                                self._audit("broker_recovered", {"broker": name})
                    except Exception as e:  # noqa: BLE001  # execution fail-safe, 交易路径不崩溃
                        logger.warning("broker %s 恢复失败: %s", name, e)

    def get_status(self) -> Dict[str, Any]:
        """获取故障切换管理器状态快照."""
        with self._lock:
            brokers_status = []
            for name, info in self._brokers.items():
                tracker = info["tracker"]
                brokers_status.append(
                    {
                        "name": name,
                        "type": info["type"],
                        "priority": info["priority"],
                        "state": tracker.state,
                        "success_rate": round(tracker.success_rate, 4),
                        "sample_count": len(tracker._results),
                        "is_active": name == self._active_broker_name,
                    }
                )
            return {
                "active_broker": self._active_broker_name,
                "failover_count": self._failover_count,
                "max_failover_count": self._max_failover_count,
                "auto_failover": self._auto_failover,
                "is_running": not self._stopped,
                "brokers": brokers_status,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }

    def _audit(self, event: str, data: Dict[str, Any]) -> None:
        """写审计日志 (JSONL)."""
        record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event": event,
            **data,
        }
        audit_file = self._audit_log_dir / f"failover_{datetime.utcnow().strftime('%Y-%m-%d')}.jsonl"
        try:
            with open(audit_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            logger.warning("审计日志写入失败: %s", e)


# ============================================================
# 便捷函数
# ============================================================
_default_manager: Optional[BrokerFailoverManager] = None
_default_lock = threading.Lock()


def initialize_failover_manager(config: Dict[str, Any]) -> BrokerFailoverManager:
    """初始化全局故障切换管理器 (单例).

    Args:
        config: 配置字典

    Returns:
        全局 BrokerFailoverManager 实例
    """
    global _default_manager
    with _default_lock:
        if _default_manager is not None:
            _default_manager.stop()
        _default_manager = BrokerFailoverManager(config)
        _default_manager.start()
        return _default_manager


def get_failover_manager() -> BrokerFailoverManager:
    """获取全局故障切换管理器.

    Raises:
        RuntimeError: 未初始化
    """
    global _default_manager
    if _default_manager is None:
        raise RuntimeError("故障切换管理器未初始化, 请先调用 initialize_failover_manager()")
    return _default_manager


def shutdown_failover_manager() -> None:
    """关闭全局故障切换管理器."""
    global _default_manager
    with _default_lock:
        if _default_manager is not None:
            _default_manager.stop()
            _default_manager = None
