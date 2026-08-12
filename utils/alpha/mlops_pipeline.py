"""MLops 流水线编排入口 — T5.8 交付物.

模块整合 8.4 — ARCHITECTURE §4.2
任务: T5.8 MLops 流水线 (编排入口)

设计原则:
    1. Facade 模式整合 model_registry + ab_testing + drift_monitor + auto_retrain
    2. 统一生命周期管理 (start/stop/status)
    3. 容错降级 (子模块失败不阻塞整体)
    4. Feature Flag 透传 (HC-1): USE_MLOPS_PIPELINE 默认 False

编排流程:
    Train → Register → Drift Monitor → A/B Test → Promote / Rollback

API:
    from utils.alpha.mlops_pipeline import MLOpsPipeline

    pipeline = MLOpsPipeline()
    pipeline.start()  # 启动监控
    # ... 模型训练 ...
    pipeline.register_and_test(model, metrics)  # 注册 + 启动 A/B
    pipeline.stop()

硬约束:
    - HC-1: Feature Flag 默认 False, 关闭时 pipeline 为 no-op
    - HC-4: Shadow 14 天验证未通过前不自动晋升
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("mlops_pipeline")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class MLOpsPipelineError(Exception):
    """MLops pipeline 基础异常."""


class MLOpsPipeline:
    """MLops 流水线编排入口 (Facade).

    整合:
        - ModelRegistry: 模型版本化
        - ABTestFramework: A/B 测试
        - DriftMonitor: 漂移检测
        - AutoRetrainScheduler: 自动重训练
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        feature_flag_name: str = "USE_MLOPS_PIPELINE",
    ) -> None:
        """初始化.

        Args:
            config: 配置字典 (None=从 ConfigManager 加载)
            feature_flag_name: Feature Flag 名称
        """
        self.feature_flag_name = feature_flag_name
        # 检查 Feature Flag (HC-1)
        self._enabled = self._check_feature_flag(feature_flag_name)

        # 加载配置
        if config is None:
            config = self._load_config()
        self.config = config

        # 子模块 (延迟初始化)
        self._model_registry: Any | None = None
        self._ab_framework: Any | None = None
        self._drift_monitor: Any | None = None
        self._retrain_scheduler: Any | None = None

        # 状态
        self._started = False
        self._pipeline_log: list[dict[str, Any]] = []

        # 日志目录
        self._log_dir = _PROJECT_ROOT / "reports" / "mlops"
        self._log_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "MLOpsPipeline 初始化: enabled=%s (flag=%s)",
            self._enabled,
            feature_flag_name,
        )

    # ============================================================
    # Feature Flag + 配置
    # ============================================================
    def _check_feature_flag(self, name: str) -> bool:
        """检查 Feature Flag (HC-1)."""
        try:
            from utils.infra.feature_flags import is_enabled

            return bool(is_enabled(name))
        except (ImportError, AttributeError) as e:
            logger.warning("Feature Flag 检查失败, 默认禁用: %s", e)
            return False

    def _load_config(self) -> dict[str, Any]:
        """从 ConfigManager 加载配置."""
        try:
            from utils.config_manager import get_config

            return get_config("mlops", default={})
        except (ImportError, AttributeError) as e:
            logger.warning("加载 mlops 配置失败: %s", e)
            return {}

    @property
    def enabled(self) -> bool:
        """是否启用."""
        return self._enabled

    # ============================================================
    # 子模块延迟加载
    # ============================================================
    @property
    def model_registry(self) -> Any:
        if self._model_registry is None:
            from utils.alpha.model_registry import ModelRegistry

            self._model_registry = ModelRegistry(
                registry_dir=self.config.get("registry_dir"),
                use_mlflow=self.config.get("use_mlflow"),
            )
        return self._model_registry

    @property
    def ab_framework(self) -> Any:
        if self._ab_framework is None:
            from utils.alpha.ab_testing import ABTestFramework

            self._ab_framework = ABTestFramework(
                results_dir=self.config.get("ab_tests_dir"),
                model_registry=self.model_registry,
            )
        return self._ab_framework

    @property
    def drift_monitor(self) -> Any:
        if self._drift_monitor is None:
            from utils.alpha.drift_monitor import DriftMonitor

            retrain_cfg = self.config.get("auto_retrain", {})
            self._drift_monitor = DriftMonitor(
                model_name=self.config.get("default_model_name", "v9_lgb"),
                retrain_callback=self._on_drift_trigger,
                retrain_threshold_count=int(retrain_cfg.get("drift_threshold_count", 3)),
                retrain_threshold_severity=retrain_cfg.get("drift_threshold_severity", "critical"),
            )
        return self._drift_monitor

    @property
    def retrain_scheduler(self) -> Any:
        if self._retrain_scheduler is None:
            from utils.alpha.auto_retrain_scheduler import AutoRetrainScheduler

            self._retrain_scheduler = AutoRetrainScheduler(
                config=self.config.get("auto_retrain", {}),
                model_registry=self.model_registry,
                ab_framework=self.ab_framework,
                drift_monitor=self.drift_monitor,
            )
        return self._retrain_scheduler

    # ============================================================
    # 生命周期
    # ============================================================
    def start(self) -> None:
        """启动 MLops 流水线 (drift 监控 + 定时调度)."""
        if not self._enabled:
            logger.warning("MLops pipeline 未启用 (flag=%s=False)", self.feature_flag_name)
            return
        if self._started:
            logger.warning("MLops pipeline 已在运行")
            return
        try:
            # 启动 drift 监控
            monitor_interval = float(self.config.get("drift_check_interval_sec", 60))
            self.drift_monitor.start_monitoring(interval_sec=monitor_interval)
            # 启动自动重训练调度器
            self.retrain_scheduler.start()
            self._started = True
            self._log_event("pipeline_started", {})
            logger.info("MLops pipeline 已启动")
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.exception("MLops pipeline 启动失败: %s", e)
            raise MLOpsPipelineError(f"启动失败: {e}") from e

    def stop(self) -> None:
        """停止 MLops 流水线."""
        if not self._started:
            return
        try:
            if self._drift_monitor is not None:
                self._drift_monitor.stop_monitoring()
            if self._retrain_scheduler is not None:
                self._retrain_scheduler.stop()
            self._log_event("pipeline_stopped", {})
            logger.info("MLops pipeline 已停止")
        except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
            logger.exception("MLops pipeline 停止异常: %s", e)
        finally:
            # 无论是否异常, 都标记为已停止
            self._started = False

    # ============================================================
    # 核心 API: 注册模型 + 启动 A/B 测试
    # ============================================================
    def register_and_test(
        self,
        model: Any,
        metrics: dict[str, float],
        model_name: str = "v9_lgb",
        params: dict[str, Any] | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        """注册新模型并自动启动 A/B 测试.

        Args:
            model: 模型对象
            metrics: 评估指标
            model_name: 模型名称
            params: 训练参数
            description: 模型描述

        Returns:
            {"version": ModelVersion, "ab_test": ABTest}
        """
        if not self._enabled:
            logger.warning("MLops pipeline 未启用, 跳过注册")
            return {"skipped": True, "reason": "pipeline_disabled"}
        result: dict[str, Any] = {}
        # 1. 注册到 ModelRegistry
        try:
            version = self.model_registry.register_model(
                name=model_name,
                model=model,
                metrics=metrics,
                params=params or {},
                description=description or "MLops pipeline 自动注册",
                created_by="mlops_pipeline",
            )
            result["version"] = version
            self._log_event(
                "model_registered",
                {
                    "model_name": model_name,
                    "version": version.version,
                    "metrics": metrics,
                },
            )
        except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
            logger.exception("模型注册失败: %s", e)
            result["register_error"] = str(e)
            return result
        # 2. 启动 A/B 测试
        try:
            from utils.alpha.ab_testing import (
                ABTestConfig,
                SplitStrategy,
            )

            test_name = f"mlops_{model_name}_v{version.version}_{datetime.utcnow().strftime('%Y%m%d')}"
            config = ABTestConfig(
                name=test_name,
                champion_model=model_name,  # 当前 production
                challenger_model=model_name,  # 新版本
                traffic_split=0.2,
                split_strategy=SplitStrategy.HASH_SYMBOL.value,
                min_samples=14,
                max_duration_days=14,
                description=f"MLops pipeline 自动 A/B (v{version.version})",
            )
            self.ab_framework.create_test(config)
            self.ab_framework.start_test(test_name)
            result["ab_test"] = test_name
            self._log_event(
                "ab_test_started",
                {
                    "test_name": test_name,
                    "model_name": model_name,
                    "version": version.version,
                },
            )
        except (ImportError, AttributeError) as e:
            logger.exception("A/B 测试启动失败: %s", e)
            result["ab_test_error"] = str(e)
        return result

    # ============================================================
    # Drift 触发回调
    # ============================================================
    def _on_drift_trigger(self, alerts: list[Any]) -> bool:
        """drift 告警触发重训练."""
        if not self._enabled:
            return False
        try:
            from utils.alpha.auto_retrain_scheduler import RetrainTrigger

            self.retrain_scheduler.trigger_retrain(
                trigger=RetrainTrigger.DRIFT_DETECTED,
                reason=f"MLops pipeline drift 触发 ({len(alerts)} 告警)",
            )
            self._log_event(
                "drift_triggered_retrain",
                {
                    "alerts_count": len(alerts),
                },
            )
            return True
        except (ImportError, AttributeError) as e:
            logger.exception("drift 触发重训练失败: %s", e)
            return False

    # ============================================================
    # 状态 + 日志
    # ============================================================
    def get_status(self) -> dict[str, Any]:
        """获取 pipeline 状态快照."""
        status: dict[str, Any] = {
            "enabled": self._enabled,
            "started": self._started,
            "feature_flag": self.feature_flag_name,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "components": {},
        }
        # 收集子模块状态 (容错)
        try:
            if self._drift_monitor is not None:
                status["components"]["drift_monitor"] = self._drift_monitor.get_status()
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            pass
        try:
            if self._retrain_scheduler is not None:
                status["components"]["retrain_scheduler"] = self._retrain_scheduler.get_status()
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            pass
        try:
            if self._model_registry is not None:
                status["components"]["model_registry"] = {
                    "models": self._model_registry.list_models(),
                }
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            pass
        try:
            if self._ab_framework is not None:
                status["components"]["ab_framework"] = {
                    "tests_count": len(self._ab_framework.list_tests()),
                }
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            pass
        return status

    def _log_event(self, event: str, data: dict[str, Any]) -> None:
        """记录 pipeline 事件."""
        record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event": event,
            **data,
        }
        self._pipeline_log.append(record)
        # 持久化
        log_file = self._log_dir / f"pipeline_{datetime.utcnow().strftime('%Y-%m-%d')}.jsonl"
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            logger.warning("pipeline 日志写入失败: %s", e)

    def get_pipeline_log(self, limit: int = 100) -> list[dict[str, Any]]:
        """获取 pipeline 事件日志."""
        return list(self._pipeline_log[-limit:])


# ============================================================
# 单例 + 便捷函数
# ============================================================
_default_pipeline: MLOpsPipeline | None = None


def get_pipeline() -> MLOpsPipeline:
    """获取全局 MLops pipeline 单例."""
    global _default_pipeline
    if _default_pipeline is None:
        _default_pipeline = MLOpsPipeline()
    return _default_pipeline


def initialize_pipeline(config: dict[str, Any] | None = None) -> MLOpsPipeline:
    """初始化全局 MLops pipeline."""
    global _default_pipeline
    _default_pipeline = MLOpsPipeline(config=config)
    return _default_pipeline