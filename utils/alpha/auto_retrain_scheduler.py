# -*- coding: utf-8 -*-
"""自动重训练调度器 — T5.8 交付物.

模块整合 8.4 — ARCHITECTURE §4.2
任务: T5.8 MLops 流水线 (自动重训练触发)

设计原则:
    1. 包装 DriftMonitor 的 should_retrain 判断
    2. 集成 V9 训练脚本 (subprocess 调用, 隔离失败)
    3. 训练完成后自动注册到 ModelRegistry
    4. 自动启动 A/B 测试 (champion vs 新版本)
    5. Feature Flag 透传 (HC-1): USE_AUTO_RETRAIN 默认 False

调度模式:
    1. 事件驱动: drift 检测到告警 → 触发重训练
    2. 定时调度: 每周/每月固定时间检查 + 训练
    3. 手动触发: API 调用

API:
    from utils.alpha.auto_retrain_scheduler import (
        AutoRetrainScheduler, RetrainTrigger, RetrainStatus
    )

    scheduler = AutoRetrainScheduler()
    scheduler.start()  # 启动后台调度
    # 或手动触发
    scheduler.trigger_retrain(reason="manual_test")

硬约束:
    - HC-1: Feature Flag 默认 False, 关闭时不自动训练
    - HC-4: Shadow 14 天验证未通过前不自动晋升
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("auto_retrain")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ============================================================
# 异常定义
# ============================================================
class AutoRetrainError(Exception):
    """自动重训练基础异常."""


class TrainingInProgressError(AutoRetrainError):
    """已有训练任务在执行."""


# ============================================================
# 状态枚举
# ============================================================
class RetrainTrigger(str, Enum):
    """重训练触发源."""

    DRIFT_DETECTED = "drift_detected"
    SCHEDULED = "scheduled"
    MANUAL = "manual"
    PERFORMANCE_DROP = "performance_drop"


class RetrainStatus(str, Enum):
    """重训练任务状态."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"  # 跳过 (如已有任务在跑)


@dataclass
class RetrainTask:
    """重训练任务记录."""

    task_id: str
    trigger: str  # RetrainTrigger.value
    reason: str
    status: str = RetrainStatus.PENDING.value
    started_at: str = ""
    ended_at: str = ""
    duration_sec: float = 0.0
    model_name: str = ""
    new_version: Optional[int] = None
    metrics: Dict[str, float] = field(default_factory=dict)
    error: str = ""
    ab_test_started: Optional[str] = None  # 关联的 A/B 测试名称

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 自动重训练调度器
# ============================================================
class AutoRetrainScheduler:
    """自动重训练调度器.

    编排流程:
        1. 监听 drift 告警 (DriftMonitor 回调)
        2. 触发训练 (subprocess 调用 V9 训练脚本)
        3. 训练完成 → 注册到 ModelRegistry (STAGING 阶段)
        4. 自动启动 A/B 测试 (champion vs 新版本)
        5. A/B 测试通过 → 晋升到 PRODUCTION

    配置 (走 ConfigManager 4 级优先级):
        auto_retrain:
            enabled: false  # HC-1 默认 False
            drift_threshold_count: 3
            drift_threshold_severity: "critical"
            min_interval_hours: 24  # 最小重训练间隔 (避免频繁训练)
            training_script: "qlib_v9_train.py"
            training_timeout_sec: 1800  # 30 分钟
            auto_register: true  # 训练完成自动注册到 registry
            auto_start_ab_test: true  # 自动启动 A/B 测试
            ab_test_duration_days: 14
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        model_registry: Optional[Any] = None,
        ab_framework: Optional[Any] = None,
        drift_monitor: Optional[Any] = None,
    ) -> None:
        """初始化.

        Args:
            config: 配置字典 (None=从 ConfigManager 加载)
            model_registry: ModelRegistry 实例
            ab_framework: ABTestFramework 实例
            drift_monitor: DriftMonitor 实例
        """
        # 加载配置
        if config is None:
            config = self._load_config()
        self.config = config
        self.enabled = bool(config.get("enabled", False))
        self.drift_threshold_count = int(config.get("drift_threshold_count", 3))
        self.drift_threshold_severity = config.get("drift_threshold_severity", "critical")
        self.min_interval_hours = float(config.get("min_interval_hours", 24))
        self.training_script = config.get("training_script", "qlib_v9_train.py")
        self.training_timeout_sec = int(config.get("training_timeout_sec", 1800))
        self.auto_register = bool(config.get("auto_register", True))
        self.auto_start_ab_test = bool(config.get("auto_start_ab_test", True))
        self.ab_test_duration_days = int(config.get("ab_test_duration_days", 14))

        # 依赖注入 (延迟加载)
        self._model_registry = model_registry
        self._ab_framework = ab_framework
        self._drift_monitor = drift_monitor

        # 状态
        self._tasks: List[RetrainTask] = []
        self._current_task: Optional[RetrainTask] = None
        self._lock = threading.RLock()
        self._scheduler_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_retrain_time: Optional[datetime] = None

        # 任务持久化目录
        self._tasks_dir = _PROJECT_ROOT / "reports" / "auto_retrain"
        self._tasks_dir.mkdir(parents=True, exist_ok=True)
        self._load_tasks()

    # ============================================================
    # 配置加载 (走 ConfigManager, HC-5)
    # ============================================================
    def _load_config(self) -> Dict[str, Any]:
        """从 ConfigManager 加载配置."""
        try:
            from utils.config_manager import get_config

            mlops_cfg = get_config("mlops", default={})
            return mlops_cfg.get("auto_retrain", {})  # type: ignore
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.warning("加载 mlops 配置失败, 使用默认值: %s", e)
            return {"enabled": False}

    # ============================================================
    # 延迟依赖
    # ============================================================
    @property
    def model_registry(self) -> Any:
        if self._model_registry is None:
            from utils.alpha.model_registry import ModelRegistry

            self._model_registry = ModelRegistry()
        return self._model_registry

    @property
    def ab_framework(self) -> Any:
        if self._ab_framework is None:
            from utils.alpha.ab_testing import ABTestFramework

            self._ab_framework = ABTestFramework(model_registry=self.model_registry)
        return self._ab_framework

    @property
    def drift_monitor(self) -> Any:
        if self._drift_monitor is None:
            from utils.alpha.drift_monitor import DriftMonitor

            # 注入重训练回调
            self._drift_monitor = DriftMonitor(
                model_name="v9_lgb",
                retrain_callback=self._on_drift_alerts,
                retrain_threshold_count=self.drift_threshold_count,
                retrain_threshold_severity=self.drift_threshold_severity,
            )
        return self._drift_monitor

    # ============================================================
    # 任务持久化
    # ============================================================
    def _load_tasks(self) -> None:
        """加载历史任务."""
        tasks_file = self._tasks_dir / "tasks.jsonl"
        if not tasks_file.exists():
            return
        try:
            with open(tasks_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        data = json.loads(line)
                        self._tasks.append(RetrainTask(**data))
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.warning("加载历史任务失败: %s", e)

    def _save_task(self, task: RetrainTask) -> None:
        """保存任务到 JSONL."""
        tasks_file = self._tasks_dir / "tasks.jsonl"
        try:
            with open(tasks_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(task.to_dict(), ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            logger.warning("任务保存失败: %s", e)

    # ============================================================
    # 触发重训练
    # ============================================================
    def _on_drift_alerts(self, alerts: List[Any]) -> bool:
        """drift 告警回调 (由 DriftMonitor 调用)."""
        if not self.enabled:
            logger.info("自动重训练未启用, 跳过 drift 触发")
            return False
        return self.trigger_retrain(
            trigger=RetrainTrigger.DRIFT_DETECTED,
            reason=f"检测到 {len(alerts)} 个漂移告警",
        )

    def trigger_retrain(
        self,
        trigger: RetrainTrigger = RetrainTrigger.MANUAL,
        reason: str = "",
        model_name: str = "v9_lgb",
    ) -> bool:
        """触发重训练.

        Args:
            trigger: 触发源
            reason: 触发原因
            model_name: 模型名称

        Returns:
            True 已触发, False 跳过

        Raises:
            TrainingInProgressError: 已有训练在跑
        """
        with self._lock:
            # 检查是否已有训练在跑
            if self._current_task is not None and self._current_task.status == RetrainStatus.RUNNING.value:
                raise TrainingInProgressError(f"已有训练任务在执行: {self._current_task.task_id}")
            # 检查最小间隔
            if self._last_retrain_time is not None:
                elapsed = datetime.utcnow() - self._last_retrain_time
                if elapsed.total_seconds() < self.min_interval_hours * 3600:
                    logger.info(
                        "距上次重训练不足 %.1f 小时, 跳过 (elapsed=%.1f h)",
                        self.min_interval_hours,
                        elapsed.total_seconds() / 3600,
                    )
                    return False
            # 创建任务
            task_id = f"retrain_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{trigger.value}"
            task = RetrainTask(
                task_id=task_id,
                trigger=trigger.value,
                reason=reason,
                status=RetrainStatus.PENDING.value,
                model_name=model_name,
            )
            self._tasks.append(task)
            self._current_task = task
            self._save_task(task)
            # 异步执行训练
            thread = threading.Thread(
                target=self._run_training,
                args=(task,),
                daemon=True,
                name=f"retrain-{task_id}",
            )
            thread.start()
            logger.info(
                "重训练已触发: task_id=%s, trigger=%s, reason=%s",
                task_id,
                trigger.value,
                reason,
            )
            return True

    # ============================================================
    # 训练执行 (subprocess)
    # ============================================================
    def _run_training(self, task: RetrainTask) -> None:
        """执行训练 (在子线程中调用 subprocess)."""
        task.status = RetrainStatus.RUNNING.value
        task.started_at = datetime.utcnow().isoformat() + "Z"
        start_time = time.time()
        try:
            # 1. 执行训练脚本
            training_result = self._execute_training_script(task)
            if not training_result.get("success"):
                task.status = RetrainStatus.FAILED.value
                task.error = training_result.get("error", "训练失败")
                logger.error("训练失败: %s (task=%s)", task.error, task.task_id)
                return
            # 2. 加载训练好的模型 + 评估指标
            model, metrics = self._load_trained_model(training_result)
            task.metrics = metrics
            # 3. 自动注册到 ModelRegistry
            if self.auto_register:
                version = self._register_model(task, model, metrics, training_result)
                task.new_version = version.version if version else None
                # 4. 自动启动 A/B 测试
                if self.auto_start_ab_test and version:
                    ab_test_name = self._start_ab_test(task, version)
                    task.ab_test_started = ab_test_name
            task.status = RetrainStatus.COMPLETED.value
            self._last_retrain_time = datetime.utcnow()
            logger.info(
                "训练完成: task=%s, metrics=%s",
                task.task_id,
                metrics,
            )
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            task.status = RetrainStatus.FAILED.value
            task.error = str(e)
            logger.exception("训练异常: %s", e)
        finally:
            task.ended_at = datetime.utcnow().isoformat() + "Z"
            task.duration_sec = round(time.time() - start_time, 2)
            self._save_task(task)
            with self._lock:
                if self._current_task is task:
                    self._current_task = None

    def _execute_training_script(self, task: RetrainTask) -> Dict[str, Any]:
        """执行训练脚本 (subprocess).

        Returns:
            {"success": bool, "model_path": str, "metrics": dict, "error": str}
        """
        script_path = _PROJECT_ROOT / self.training_script
        if not script_path.exists():
            return {
                "success": False,
                "error": f"训练脚本不存在: {script_path}",
            }
        try:
            # 使用 subprocess 隔离执行
            cmd = [sys.executable, str(script_path)]
            logger.info("执行训练脚本: %s", " ".join(cmd))
            result = subprocess.run(
                cmd,
                cwd=str(_PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=self.training_timeout_sec,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                return {
                    "success": False,
                    "error": f"returncode={result.returncode}, stderr={result.stderr[-500:]}",
                }
            return {
                "success": True,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"训练超时 ({self.training_timeout_sec}s)"}
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            return {"success": False, "error": str(e)}

    def _load_trained_model(self, training_result: Dict[str, Any]) -> tuple:
        """加载训练好的模型 (默认实现返回占位符).

        实际使用时需子类化或注入 callback 加载真实模型.
        """
        # TODO: 实际接入时从训练脚本输出加载模型
        # V9 训练脚本当前不保存模型, 这里返回 None + 占位指标
        metrics = {
            "dsr": 0.0,
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe_cv": 0.0,
        }
        return None, metrics

    def _register_model(
        self,
        task: RetrainTask,
        model: Any,
        metrics: Dict[str, float],
        training_result: Dict[str, Any],
    ) -> Optional[Any]:
        """注册新模型到 ModelRegistry."""
        try:
            version = self.model_registry.register_model(
                name=task.model_name,
                model=model if model is not None else {"placeholder": True},
                metrics=metrics,
                params={"trigger": task.trigger, "reason": task.reason},
                tags={
                    "retrain_task": task.task_id,
                    "auto_registered": "true",
                },
                description=f"自动重训练 (trigger={task.trigger})",
                created_by=f"auto_retrain:{task.task_id}",
            )
            # 转换到 STAGING 阶段
            from utils.alpha.model_registry import ModelStage

            self.model_registry.transition_stage(
                task.model_name,
                version.version,
                ModelStage.STAGING,
                by=f"auto_retrain:{task.task_id}",
            )
            logger.info(
                "模型已注册: %s v%d (STAGING)",
                task.model_name,
                version.version,
            )
            return version
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.exception("模型注册失败: %s", e)
            return None

    def _start_ab_test(self, task: RetrainTask, new_version: Any) -> Optional[str]:
        """启动 A/B 测试 (champion vs 新版本)."""
        try:
            from utils.alpha.ab_testing import (
                ABTestConfig,
                SplitStrategy,
            )

            # 获取当前 production 版本作为 champion
            champion = self.model_registry.get_production_version(task.model_name)
            _champion_version = f"{task.model_name}_v{champion.version}" if champion else "baseline"  # noqa: F841
            test_name = f"auto_{task.model_name}_{task.task_id}"
            config = ABTestConfig(
                name=test_name,
                champion_model=task.model_name,  # production 版本
                challenger_model=task.model_name,  # 新 STAGING 版本
                traffic_split=0.2,
                split_strategy=SplitStrategy.HASH_SYMBOL.value,
                min_samples=14,
                max_duration_days=self.ab_test_duration_days,
                description=f"自动 A/B 测试 (task={task.task_id})",
            )
            self.ab_framework.create_test(config)
            self.ab_framework.start_test(test_name)
            logger.info(
                "A/B 测试已启动: %s (champion=prod, challenger=v%d)",
                test_name,
                new_version.version,
            )
            return test_name
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.exception("A/B 测试启动失败: %s", e)
            return None

    # ============================================================
    # 定时调度
    # ============================================================
    def start(self, check_interval_sec: float = 3600.0) -> None:
        """启动后台调度 (定时检查 + 监听 drift)."""
        if not self.enabled:
            logger.warning("自动重训练未启用 (enabled=False), 不启动调度")
            return
        with self._lock:
            if self._scheduler_thread and self._scheduler_thread.is_alive():
                logger.warning("调度器已在运行")
                return
            self._stop_event.clear()
            self._scheduler_thread = threading.Thread(
                target=self._scheduler_loop,
                kwargs={"check_interval_sec": check_interval_sec},
                daemon=True,
                name="auto-retrain-scheduler",
            )
            self._scheduler_thread.start()
            # 启动 drift 监控
            self.drift_monitor.start_monitoring()
            logger.info(
                "自动重训练调度器已启动 (check_interval=%.0fs)",
                check_interval_sec,
            )

    def stop(self) -> None:
        """停止调度."""
        with self._lock:
            # 未启动时 no-op (避免误调子模块 stop)
            if self._scheduler_thread is None and not self._stop_event.is_set():
                # _stop_event 默认未 set, 用 thread None 判断是否启动过
                pass
            self._stop_event.set()
            thread_was_running = self._scheduler_thread is not None and self._scheduler_thread.is_alive()
            if self._scheduler_thread and self._scheduler_thread.is_alive():
                self._scheduler_thread.join(timeout=5.0)
            self._scheduler_thread = None
            # 只在曾启动过时才停止 drift 监控 (避免未启动时误调)
            if thread_was_running and self._drift_monitor is not None:
                self._drift_monitor.stop_monitoring()
            logger.info("自动重训练调度器已停止")

    def _scheduler_loop(self, check_interval_sec: float) -> None:
        """定时调度循环."""
        while not self._stop_event.is_set():
            try:
                self._scheduled_check()
            except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
                logger.exception("定时检查异常: %s", e)
            self._stop_event.wait(timeout=check_interval_sec)

    def _scheduled_check(self) -> None:
        """定时检查 (是否需要触发重训练)."""
        # 检查 drift 状态
        if self._drift_monitor is not None:
            should, reason = self.drift_monitor.should_retrain()
            if should:
                logger.info("定时检查触发重训练: %s", reason)
                try:
                    self.trigger_retrain(
                        trigger=RetrainTrigger.PERFORMANCE_DROP,
                        reason=reason,
                    )
                except TrainingInProgressError:
                    pass  # 已有训练在跑

    # ============================================================
    # 查询
    # ============================================================
    def get_status(self) -> Dict[str, Any]:
        """获取调度器状态."""
        with self._lock:
            return {
                "enabled": self.enabled,
                "is_running": (self._scheduler_thread is not None and self._scheduler_thread.is_alive()),
                "current_task": (self._current_task.to_dict() if self._current_task else None),
                "tasks_count": len(self._tasks),
                "last_retrain_time": (self._last_retrain_time.isoformat() + "Z" if self._last_retrain_time else None),
                "config": {
                    "min_interval_hours": self.min_interval_hours,
                    "training_script": self.training_script,
                    "training_timeout_sec": self.training_timeout_sec,
                    "auto_register": self.auto_register,
                    "auto_start_ab_test": self.auto_start_ab_test,
                    "ab_test_duration_days": self.ab_test_duration_days,
                },
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }

    def list_tasks(self, limit: int = 50) -> List[Dict[str, Any]]:
        """列出历史任务."""
        with self._lock:
            return [t.to_dict() for t in self._tasks[-limit:]]

    def get_task(self, task_id: str) -> Optional[RetrainTask]:
        """获取指定任务详情."""
        with self._lock:
            for t in self._tasks:
                if t.task_id == task_id:
                    return t
        return None
