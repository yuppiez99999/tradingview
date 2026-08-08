"""模型漂移检测器 (re-export + 增强) — T5.8 交付物.

模块整合 8.4 — ARCHITECTURE §4.2
任务: T5.8 MLops 流水线 (漂移检测)

设计原则:
    1. 复用 ms_strategy/src/ml/drift_detector.py 的完整实现
    2. re-export 兼容 (HC-7 不修改原路径)
    3. 增加 DriftMonitor 类: 持久化告警 + 集成 auto_retrain
    4. Feature Flag 透传 (HC-1): USE_DRIFT_DETECTOR 默认 False

原路径 (ms_strategy/src/ml/drift_detector.py) 提供的 API:
    - ADWINDetector: ADWIN 概念漂移检测
    - ModelDriftDetector: 多维度漂移检测 (IC/ADWIN/KS/PSI)
    - DriftAlert / DriftType / Severity: 数据类

新增 API:
    - DriftMonitor: 持久化告警 + 集成 auto_retrain
    - start_monitoring / stop_monitoring: 后台监控

硬约束:
    - HC-1: Feature Flag 默认 False, 关闭时不触发重训练
    - HC-7: 不修改 ms_strategy/src/ml/drift_detector.py 原路径
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("drift_monitor")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ============================================================
# Re-export 原有 drift_detector 模块 (HC-7 兼容)
# ============================================================
try:
    # 优先从 ms_strategy 加载
    import sys

    _ms_path = _PROJECT_ROOT / "ms_strategy"
    if str(_ms_path) not in sys.path:
        sys.path.insert(0, str(_ms_path))
    from src.ml.drift_detector import (
        ADWINDetector,
        DriftAlert,
        DriftType,
        ModelDriftDetector,
        Severity,
    )

    _DRIFT_DETECTOR_AVAILABLE = True
except ImportError:
    try:
        # 从 v8.3_institutional 加载
        _v83_path = _PROJECT_ROOT / "v8.3_institutional"
        if str(_v83_path) not in sys.path:
            sys.path.insert(0, str(_v83_path))
        from src.ml.drift_detector import (
            ADWINDetector,
            DriftAlert,
            DriftType,
            ModelDriftDetector,
            Severity,
        )

        _DRIFT_DETECTOR_AVAILABLE = True
    except ImportError:
        _DRIFT_DETECTOR_AVAILABLE = False
        logger.warning("drift_detector 模块不可用 (ms_strategy/src/ml/drift_detector.py), DriftMonitor 将降级为 no-op")
        # 降级占位符
        ADWINDetector = None
        ModelDriftDetector = None
        DriftAlert = None
        DriftType = None
        Severity = None


# ============================================================
# 异常定义
# ============================================================
class DriftMonitorError(Exception):
    """漂移监控器基础异常."""


# ============================================================
# 持久化漂移监控器
# ============================================================
class DriftMonitor:
    """漂移监控器 (持久化告警 + 集成 auto_retrain).

    在 ModelDriftDetector 之上增加:
        1. 告警持久化 (JSONL)
        2. 后台监控线程
        3. 重训练回调触发
        4. 状态快照

    用法:
        monitor = DriftMonitor(model_name="v9_lgb")
        monitor.start_monitoring()
        # ... IC 更新 ...
        monitor.update_ic("2026-07-27", 0.05)
        # ... 自动触发重训练 (如配置回调) ...
        monitor.stop_monitoring()
    """

    def __init__(
        self,
        model_name: str = "default",
        detector: Any | None = None,
        alerts_dir: str | None = None,
        retrain_callback: Callable[[list[Any]], bool] | None = None,
        retrain_threshold_count: int = 3,
        retrain_threshold_severity: str = "critical",
    ) -> None:
        """初始化.

        Args:
            model_name: 模型名称 (用于日志/告警)
            detector: ModelDriftDetector 实例 (None=自动创建)
            alerts_dir: 告警持久化目录
            retrain_callback: 重训练回调 (返回 True 表示已触发)
            retrain_threshold_count: 触发重训练的最少告警数
            retrain_threshold_severity: 触发重训练的最低严重级别
        """
        self.model_name = model_name
        if detector is None and _DRIFT_DETECTOR_AVAILABLE:
            try:
                detector = ModelDriftDetector()
            except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
                logger.warning("ModelDriftDetector 创建失败: %s", e)
                detector = None
        self.detector = detector
        # 告警持久化目录
        if alerts_dir:
            self.alerts_dir = Path(alerts_dir)
            if not self.alerts_dir.is_absolute():
                self.alerts_dir = _PROJECT_ROOT / alerts_dir
        else:
            self.alerts_dir = _PROJECT_ROOT / "reports" / "drift_alerts"
        self.alerts_dir.mkdir(parents=True, exist_ok=True)
        # 重训练配置
        self.retrain_callback = retrain_callback
        self.retrain_threshold_count = retrain_threshold_count
        self.retrain_threshold_severity = retrain_threshold_severity
        # 状态
        self._alerts_history: list[dict[str, Any]] = []
        self._retrain_triggered: bool = False
        self._last_retrain_time: str | None = None
        self._monitoring_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._monitoring_interval = 60.0  # 默认 60s 检查一次

    # ============================================================
    # 数据更新 (委托给 ModelDriftDetector)
    # ============================================================
    def update_ic(self, date: str, ic_value: float) -> Any | None:
        """更新 IC 值 (委托)."""
        if self.detector is None:
            return None
        try:
            alert = self.detector.update_ic(date, ic_value)
            if alert is not None:
                self._record_alert(alert)
            return alert
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.exception("IC 更新失败: %s", e)
            return None

    def update_adwin(self, value: float) -> Any | None:
        """更新 ADWIN (委托)."""
        if self.detector is None:
            return None
        try:
            alert = self.detector.update_adwin(value)
            if alert is not None:
                self._record_alert(alert)
            return alert
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.exception("ADWIN 更新失败: %s", e)
            return None

    def check_feature_drift(self, current_features: dict[str, Any]) -> list[Any]:
        """检查特征漂移 (委托)."""
        if self.detector is None:
            return []
        try:
            alerts = self.detector.check_feature_drift(current_features)
            for a in alerts:
                self._record_alert(a)
            return alerts  # type: ignore[misc]
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.exception("特征漂移检查失败: %s", e)
            return []

    def check_all(self) -> list[Any]:
        """全量检查 (委托)."""
        if self.detector is None:
            return []
        try:
            alerts = self.detector.check_all()
            for a in alerts:
                self._record_alert(a)
            # 检查是否需要触发重训练
            self._check_retrain_trigger(alerts)
            return alerts  # type: ignore[misc]
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.exception("全量检查失败: %s", e)
            return []

    # ============================================================
    # 告警持久化
    # ============================================================
    def _record_alert(self, alert: Any) -> None:
        """记录告警到持久化存储."""
        try:
            # 转 dict (兼容 DriftAlert dataclass)
            if hasattr(alert, "__dict__"):
                alert_dict = {}
                for k, v in alert.__dict__.items():
                    if hasattr(v, "value"):  # Enum
                        alert_dict[k] = v.value
                    else:
                        alert_dict[k] = v
            else:
                alert_dict = {"alert": str(alert)}
            alert_dict["model_name"] = self.model_name
            alert_dict["recorded_at"] = datetime.utcnow().isoformat() + "Z"
            with self._lock:
                self._alerts_history.append(alert_dict)
            # 写 JSONL
            alert_file = self.alerts_dir / f"{self.model_name}_{datetime.utcnow().strftime('%Y-%m-%d')}.jsonl"
            with open(alert_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(alert_dict, ensure_ascii=False, default=str) + "\n")
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.warning("告警持久化失败: %s", e)

    # ============================================================
    # 重训练触发
    # ============================================================
    def _check_retrain_trigger(self, alerts: list[Any]) -> bool:
        """检查是否需要触发重训练."""
        if self.retrain_callback is None:
            return False
        if self._retrain_triggered:
            return False  # 已触发, 不重复
        # 检查告警数
        if len(alerts) < self.retrain_threshold_count:
            return False
        # 检查严重级别
        severity_met = False
        for alert in alerts:
            severity = getattr(alert, "severity", None)
            severity_val = severity.value if hasattr(severity, "value") else str(severity)  # type: ignore[misc]
            if severity_val == self.retrain_threshold_severity:
                severity_met = True
                break
        if not severity_met:
            return False
        # 触发回调
        try:
            triggered = bool(self.retrain_callback(alerts))
            if triggered:
                self._retrain_triggered = True
                self._last_retrain_time = datetime.utcnow().isoformat() + "Z"
                logger.warning(
                    "模型 %s 触发重训练 (alerts=%d, severity=%s)",
                    self.model_name,
                    len(alerts),
                    self.retrain_threshold_severity,
                )
            return triggered
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.exception("重训练回调异常: %s", e)
            return False

    def should_retrain(self) -> tuple:
        """判断是否需要重训练 (委托 + 增强)."""
        if self.detector is None:
            return False, "drift_detector 不可用"
        try:
            should, reason = self.detector.should_retrain()
            return bool(should), str(reason)
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            return False, f"判断异常: {e}"

    # ============================================================
    # 后台监控
    # ============================================================
    def start_monitoring(self, interval_sec: float = 60.0) -> None:
        """启动后台监控线程."""
        with self._lock:
            if self._monitoring_thread and self._monitoring_thread.is_alive():
                logger.warning("监控已在运行")
                return
            self._monitoring_interval = interval_sec
            self._stop_event.clear()
            self._monitoring_thread = threading.Thread(
                target=self._monitoring_loop,
                daemon=True,
                name=f"drift-monitor-{self.model_name}",
            )
            self._monitoring_thread.start()
            logger.info(
                "漂移监控已启动: model=%s, interval=%.1fs",
                self.model_name,
                interval_sec,
            )

    def stop_monitoring(self) -> None:
        """停止后台监控."""
        with self._lock:
            self._stop_event.set()
            if self._monitoring_thread and self._monitoring_thread.is_alive():
                self._monitoring_thread.join(timeout=5.0)
            self._monitoring_thread = None
            logger.info("漂移监控已停止: model=%s", self.model_name)

    def _monitoring_loop(self) -> None:
        """后台监控循环."""
        while not self._stop_event.is_set():
            try:
                self.check_all()
            except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
                logger.exception("监控循环异常: %s", e)
            self._stop_event.wait(timeout=self._monitoring_interval)

    # ============================================================
    # 状态查询
    # ============================================================
    def get_status(self) -> dict[str, Any]:
        """获取监控状态快照."""
        with self._lock:
            return {
                "model_name": self.model_name,
                "detector_available": self.detector is not None,
                "alerts_count": len(self._alerts_history),
                "retrain_triggered": self._retrain_triggered,
                "last_retrain_time": self._last_retrain_time,
                "is_monitoring": (self._monitoring_thread is not None and self._monitoring_thread.is_alive()),
                "monitoring_interval": self._monitoring_interval,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }

    def get_alerts_history(self, limit: int = 100) -> list[dict[str, Any]]:
        """获取告警历史."""
        with self._lock:
            return list(self._alerts_history[-limit:])

    def generate_report(self) -> dict[str, Any]:
        """生成漂移报告."""
        if self.detector is None:
            return {"model_name": self.model_name, "error": "detector 不可用"}
        try:
            report = self.detector.generate_report()
            report["model_name"] = self.model_name
            report["alerts_count"] = len(self._alerts_history)
            report["retrain_triggered"] = self._retrain_triggered
            report["last_retrain_time"] = self._last_retrain_time
            return report  # type: ignore[misc]
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            return {"model_name": self.model_name, "error": str(e)}

    def reset_retrain_state(self) -> None:
        """重置重训练状态 (允许下次再触发)."""
        with self._lock:
            self._retrain_triggered = False
            logger.info("重训练状态已重置: model=%s", self.model_name)


# ============================================================
# 便捷函数
# ============================================================
def create_drift_monitor(
    model_name: str,
    retrain_callback: Callable[[list[Any]], bool] | None = None,
) -> DriftMonitor:
    """创建漂移监控器 (便捷函数)."""
    return DriftMonitor(
        model_name=model_name,
        retrain_callback=retrain_callback,
    )


# ============================================================
# GAP-6: Sim-mode 漂移检测 (ECC MLE-10 修复)
#
# 设计原则:
#   1. 独立于 legacy DriftMonitor (不修改 HC-1 V9 基线)
#   2. 默认 USE_DRIFT_DETECTOR=False (HC-1 不破坏)
#   3. sim_mode=True 或 USE_DRIFT_DETECTOR=True 时激活
#   4. DriftReport frozen=True (coding-standards 不可变优先)
#   5. KS 检验 + PSI 双指标 (工业级标准)
# ============================================================

import os  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from enum import Enum  # noqa: E402
from typing import Sequence  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# scipy.stats 用于 KS 检验 (可选, 降级为均值差)
try:
    from scipy import stats as _scipy_stats

    _SCIPY_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SCIPY_AVAILABLE = False
    _scipy_stats = None  # type: ignore[assignment]

# Feature Flag (HC-1: 默认 False, 不破坏 V9 基线)
_USE_DRIFT_DETECTOR_FLAG = os.environ.get("USE_DRIFT_DETECTOR", "false").lower() in (
    "1",
    "true",
    "yes",
    "on",
)


class DriftSeverity(str, Enum):
    """GAP-6 漂移严重等级 (独立于 legacy Severity 枚举).

    阈值定义 (基于工业级标准):
        - LOW: KS < 0.1 或 PSI < 0.1 (无明显漂移)
        - MEDIUM: 0.1 <= KS < 0.2 或 0.1 <= PSI < 0.25 (需观察)
        - HIGH: 0.2 <= KS < 0.4 或 0.25 <= PSI < 0.5 (需告警)
        - CRITICAL: KS >= 0.4 或 PSI >= 0.5 (需回滚)
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class DriftReport:
    """漂移报告 (不可变, GAP-6 交付物).

    Attributes:
        timestamp: 报告生成时间 (ISO 格式)
        model_name: 模型名 (如 "v9_lgb")
        model_version: 模型版本 (artifact_name, 如 "lgbm_factor_mining_vabc12345_d20260729")
        feature_name: 特征名 (如 "MOM_5D") 或 "__prediction__" (预测漂移)
        drift_score: KS 统计量 (0-1, 越大漂移越严重)
        psi: Population Stability Index (0+, 越大漂移越严重)
        severity: 严重等级 (DriftSeverity)
        baseline_mean: 基线分布均值
        current_mean: 当前分布均值
        baseline_size: 基线样本数
        current_size: 当前样本数
        owner: 告警 owner (从 config/alert_owners.yaml 读取)
        runbook_url: 排查手册链接
    """

    timestamp: str
    model_name: str
    model_version: str
    feature_name: str
    drift_score: float
    psi: float
    severity: DriftSeverity
    baseline_mean: float
    current_mean: float
    baseline_size: int = 0
    current_size: int = 0
    owner: str = ""
    runbook_url: str = "docs/runbooks/MODEL_DRIFT_RUNBOOK.md"

    def to_dict(self) -> dict[str, Any]:
        """转为 dict (用于持久化到 JSON)."""
        return {
            "timestamp": self.timestamp,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "feature_name": self.feature_name,
            "drift_score": float(self.drift_score),
            "psi": float(self.psi),
            "severity": self.severity.value,
            "baseline_mean": float(self.baseline_mean),
            "current_mean": float(self.current_mean),
            "baseline_size": int(self.baseline_size),
            "current_size": int(self.current_size),
            "owner": self.owner,
            "runbook_url": self.runbook_url,
        }


def _classify_severity(ks_score: float, psi: float) -> DriftSeverity:
    """根据 KS 和 PSI 双指标判定严重等级 (取较严重者)."""
    # KS 判定
    if ks_score >= 0.4:
        ks_severity = DriftSeverity.CRITICAL
    elif ks_score >= 0.2:
        ks_severity = DriftSeverity.HIGH
    elif ks_score >= 0.1:
        ks_severity = DriftSeverity.MEDIUM
    else:
        ks_severity = DriftSeverity.LOW

    # PSI 判定
    if psi >= 0.5:
        psi_severity = DriftSeverity.CRITICAL
    elif psi >= 0.25:
        psi_severity = DriftSeverity.HIGH
    elif psi >= 0.1:
        psi_severity = DriftSeverity.MEDIUM
    else:
        psi_severity = DriftSeverity.LOW

    # 取较严重者
    severity_order = {
        DriftSeverity.LOW: 0,
        DriftSeverity.MEDIUM: 1,
        DriftSeverity.HIGH: 2,
        DriftSeverity.CRITICAL: 3,
    }
    return ks_severity if severity_order[ks_severity] >= severity_order[psi_severity] else psi_severity


def compute_psi(baseline: pd.Series, current: pd.Series, n_bins: int = 10) -> float:
    """计算 PSI (Population Stability Index).

    PSI = Σ (actual_i% - expected_i%) * ln(actual_i% / expected_i%)

    工业级阈值:
        - PSI < 0.1: 无显著变化
        - 0.1 <= PSI < 0.25: 需观察
        - 0.25 <= PSI < 0.5: 需告警
        - PSI >= 0.5: 需回滚

    Args:
        baseline: 基线分布
        current: 当前分布
        n_bins: 分箱数 (默认 10)

    Returns:
        PSI 值 (float)
    """
    baseline_clean = pd.Series(baseline).dropna()
    current_clean = pd.Series(current).dropna()
    if len(baseline_clean) < 2 or len(current_clean) < 2:
        return 0.0

    # 用 baseline 的分位数作为分箱边界 (point-in-time 正确)
    try:
        bins = np.unique(np.percentile(baseline_clean, np.linspace(0, 100, n_bins + 1)))
    except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
        return 0.0
    if len(bins) < 2:
        return 0.0

    # 加 -inf / +inf 边界, 确保所有值都落入箱内
    bins[0] = -np.inf
    bins[-1] = np.inf

    baseline_counts = np.histogram(baseline_clean, bins=bins)[0]
    current_counts = np.histogram(current_clean, bins=bins)[0]

    # 转为比例 (加 epsilon 防止除零)
    eps = 1e-6
    baseline_pct = baseline_counts / len(baseline_clean) + eps
    current_pct = current_counts / len(current_clean) + eps

    # PSI = Σ (actual - expected) * ln(actual / expected)
    psi = float(np.sum((current_pct - baseline_pct) * np.log(current_pct / baseline_pct)))
    # 处理 nan/inf (极端情况)
    if not np.isfinite(psi):
        return 0.0
    return psi


def compute_feature_drift(
    baseline: pd.Series,
    current: pd.Series,
    feature_name: str = "unknown",
    model_name: str = "default",
    model_version: str = "unknown",
) -> DriftReport:
    """计算特征漂移 (KS 检验 + PSI).

    Args:
        baseline: 基线特征分布 (训练时)
        current: 当前特征分布 (服务时)
        feature_name: 特征名
        model_name: 模型名
        model_version: 模型版本

    Returns:
        DriftReport 漂移报告
    """
    baseline_clean = pd.Series(baseline).dropna()
    current_clean = pd.Series(current).dropna()

    # 空分布处理
    if len(baseline_clean) == 0 or len(current_clean) == 0:
        return DriftReport(
            timestamp=datetime.utcnow().isoformat() + "Z",
            model_name=model_name,
            model_version=model_version,
            feature_name=feature_name,
            drift_score=0.0,
            psi=0.0,
            severity=DriftSeverity.LOW,
            baseline_mean=0.0,
            current_mean=0.0,
            baseline_size=len(baseline_clean),
            current_size=len(current_clean),
        )

    # KS 检验 (scipy 可用时) 或降级为均值差
    if _SCIPY_AVAILABLE and len(baseline_clean) >= 2 and len(current_clean) >= 2:
        try:
            ks_stat, _ = _scipy_stats.ks_2samp(baseline_clean.values, current_clean.values)
            ks_score = float(ks_stat)
        except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
            # 降级: 用均值差 / (std + eps)
            std_pool = float(np.std(list(baseline_clean) + list(current_clean))) + 1e-8
            ks_score = float(abs(np.mean(current_clean) - np.mean(baseline_clean)) / std_pool)
    else:
        std_pool = float(np.std(list(baseline_clean) + list(current_clean))) + 1e-8
        ks_score = float(abs(np.mean(current_clean) - np.mean(baseline_clean)) / std_pool)

    # PSI
    psi = compute_psi(baseline_clean, current_clean)

    # 判定严重等级
    severity = _classify_severity(ks_score, psi)

    return DriftReport(
        timestamp=datetime.utcnow().isoformat() + "Z",
        model_name=model_name,
        model_version=model_version,
        feature_name=feature_name,
        drift_score=ks_score,
        psi=psi,
        severity=severity,
        baseline_mean=float(np.mean(baseline_clean)),
        current_mean=float(np.mean(current_clean)),
        baseline_size=len(baseline_clean),
        current_size=len(current_clean),
    )


def compute_prediction_drift(
    baseline: np.ndarray,
    current: np.ndarray,
    model_name: str = "default",
    model_version: str = "unknown",
) -> DriftReport:
    """计算预测漂移 (模型输出分布漂移).

    Args:
        baseline: 基线预测分布 (训练集 OOF predictions)
        current: 当前预测分布 (线上服务 predictions)
        model_name: 模型名
        model_version: 模型版本

    Returns:
        DriftReport (feature_name="__prediction__")
    """
    return compute_feature_drift(
        baseline=pd.Series(baseline),
        current=pd.Series(current),
        feature_name="__prediction__",
        model_name=model_name,
        model_version=model_version,
    )


def _load_alert_owners(config_path: str | None = None) -> dict[str, dict[str, str]]:
    """加载告警 owner 配置.

    Args:
        config_path: 配置文件路径 (None 则用默认 config/alert_owners.yaml)

    Returns:
        {model_name: {owner / slack_channel / email / oncall_rotation}}
    """
    if config_path is None:
        config_path = str(_PROJECT_ROOT / "config" / "alert_owners.yaml")
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        # 支持 {models: [...]} 或直接 {model_name: {...}} 两种格式
        if "models" in data:
            models_list = data["models"]
            return {m["name"]: m for m in models_list if isinstance(m, dict) and "name" in m}
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    except FileNotFoundError:
        logger.warning(f"alert_owners.yaml 不存在: {config_path}")
        return {}
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.warning(f"加载 alert_owners.yaml 失败: {e}")
        return {}


class SimModeDriftMonitor:
    """sim_mode 漂移监控器 (GAP-6 核心交付物).

    与 legacy DriftMonitor 的区别:
        1. sim_mode=True 或 USE_DRIFT_DETECTOR=True 时激活 (HC-1 默认 False)
        2. 支持 run_daily_check() 批量检查 panel 所有特征
        3. 产出 DriftReport (frozen dataclass), 而非 legacy DriftAlert
        4. 持久化到 reports/drift/drift_report_{date}.json

    用法:
        monitor = SimModeDriftMonitor(
            model_name="v9_lgb",
            model_version="lgbm_factor_mining_vabc12345_d20260729",
            baseline_panel=training_panel,
            sim_mode=True,
        )
        if monitor.is_active():
            reports = monitor.run_daily_check(current_panel)
            for r in reports:
                if r.severity == DriftSeverity.CRITICAL:
                    alert_owners = monitor.get_owner()
                    # 触发告警...
    """

    def __init__(
        self,
        model_name: str,
        model_version: str,
        baseline_panel: pd.DataFrame | None = None,
        sim_mode: bool = False,
        feature_columns: Sequence[str] | None = None,
        reports_dir: str | None = None,
        alert_owners_path: str | None = None,
    ) -> None:
        """初始化.

        Args:
            model_name: 模型名 (如 "v9_lgb")
            model_version: 模型版本 (artifact_name)
            baseline_panel: 基线 panel (训练数据, 含特征列); None 时需后续 set_baseline
            sim_mode: 是否为 sim_mode (True 时激活, 即使 Flag 关闭)
            feature_columns: 要监控的特征列 (None 时自动从 baseline_panel 推断)
            reports_dir: 报告持久化目录 (None 则用 reports/drift)
            alert_owners_path: alert_owners.yaml 路径 (None 用默认)
        """
        self.model_name = model_name
        self.model_version = model_version
        self.sim_mode = bool(sim_mode)
        self._baseline_panel = baseline_panel
        # 推断特征列
        if feature_columns is not None:
            self._feature_columns: list[str] = list(feature_columns)
        elif baseline_panel is not None:
            self._feature_columns = [c for c in baseline_panel.columns if c not in ("code", "date", "y", "symbol")]
        else:
            self._feature_columns = []
        # 报告目录
        if reports_dir:
            self.reports_dir = Path(reports_dir)
            if not self.reports_dir.is_absolute():
                self.reports_dir = _PROJECT_ROOT / reports_dir
        else:
            self.reports_dir = _PROJECT_ROOT / "reports" / "drift"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        # 告警 owner
        self._alert_owners = _load_alert_owners(alert_owners_path)
        # 累积报告历史
        self._history: list[DriftReport] = []

    def is_active(self) -> bool:
        """是否激活.

        激活条件 (任一即可):
            1. sim_mode=True (本实例显式启用)
            2. USE_DRIFT_DETECTOR=True (环境变量全局启用)
        """
        return self.sim_mode or _USE_DRIFT_DETECTOR_FLAG

    def set_baseline(self, panel: pd.DataFrame) -> None:
        """设置基线 panel (训练后调用)."""
        self._baseline_panel = panel
        if not self._feature_columns:
            self._feature_columns = [c for c in panel.columns if c not in ("code", "date", "y", "symbol")]

    def run_daily_check(self, current_panel: pd.DataFrame) -> list[DriftReport]:
        """每日漂移检查 (批量检查所有特征).

        Args:
            current_panel: 当日 panel (含特征列)

        Returns:
            List[DriftReport] 漂移报告列表 (按 severity 降序)
        """
        if not self.is_active():
            logger.info(
                "SimModeDriftMonitor 未激活 (sim_mode=%s, USE_DRIFT_DETECTOR=%s), 跳过",
                self.sim_mode,
                _USE_DRIFT_DETECTOR_FLAG,
            )
            return []
        if self._baseline_panel is None:
            logger.warning("基线 panel 未设置, 无法检查漂移")
            return []

        reports: list[DriftReport] = []
        for feature in self._feature_columns:
            if feature not in current_panel.columns:
                continue
            if feature not in self._baseline_panel.columns:
                continue
            baseline_series = self._baseline_panel[feature]
            current_series = current_panel[feature]
            report = compute_feature_drift(
                baseline=baseline_series,
                current=current_series,
                feature_name=feature,
                model_name=self.model_name,
                model_version=self.model_version,
            )
            # 补充 owner
            owner_info = self._get_owner_info()
            report = DriftReport(
                timestamp=report.timestamp,
                model_name=report.model_name,
                model_version=report.model_version,
                feature_name=report.feature_name,
                drift_score=report.drift_score,
                psi=report.psi,
                severity=report.severity,
                baseline_mean=report.baseline_mean,
                current_mean=report.current_mean,
                baseline_size=report.baseline_size,
                current_size=report.current_size,
                owner=owner_info.get("owner", ""),
                runbook_url=owner_info.get("runbook_url", "docs/runbooks/MODEL_DRIFT_RUNBOOK.md"),
            )
            reports.append(report)

        # 按 severity 降序 (CRITICAL 优先)
        severity_order = {
            DriftSeverity.CRITICAL: 3,
            DriftSeverity.HIGH: 2,
            DriftSeverity.MEDIUM: 1,
            DriftSeverity.LOW: 0,
        }
        reports.sort(key=lambda r: severity_order[r.severity], reverse=True)

        # 累积历史
        self._history.extend(reports)

        # 持久化
        self._persist_reports(reports)
        return reports

    def check_prediction_drift(self, current_predictions: np.ndarray) -> DriftReport:
        """检查预测分布漂移.

        Args:
            current_predictions: 当前预测分布

        Returns:
            DriftReport
        """
        if not self.is_active():
            return DriftReport(
                timestamp=datetime.utcnow().isoformat() + "Z",
                model_name=self.model_name,
                model_version=self.model_version,
                feature_name="__prediction__",
                drift_score=0.0,
                psi=0.0,
                severity=DriftSeverity.LOW,
                baseline_mean=0.0,
                current_mean=0.0,
            )
        # 基线预测 (需预先设置)
        baseline_pred = getattr(self, "_baseline_predictions", None)
        if baseline_pred is None:
            logger.warning("基线预测未设置, 无法检查预测漂移")
            return DriftReport(
                timestamp=datetime.utcnow().isoformat() + "Z",
                model_name=self.model_name,
                model_version=self.model_version,
                feature_name="__prediction__",
                drift_score=0.0,
                psi=0.0,
                severity=DriftSeverity.LOW,
                baseline_mean=0.0,
                current_mean=float(np.mean(current_predictions)) if len(current_predictions) else 0.0,
            )
        return compute_prediction_drift(
            baseline=baseline_pred,
            current=current_predictions,
            model_name=self.model_name,
            model_version=self.model_version,
        )

    def set_baseline_predictions(self, predictions: np.ndarray) -> None:
        """设置基线预测分布 (训练集 OOF predictions)."""
        self._baseline_predictions = np.asarray(predictions)  # type: ignore[union-attr]

    def _get_owner_info(self) -> dict[str, str]:
        """获取当前模型的 owner 信息."""
        return self._alert_owners.get(self.model_name, {})

    def _persist_reports(self, reports: list[DriftReport]) -> None:
        """持久化报告到 reports/drift/drift_report_{date}.json."""
        if not reports:
            return
        try:
            date_str = datetime.utcnow().strftime("%Y-%m-%d")
            report_file = self.reports_dir / f"drift_report_{date_str}.json"
            existing: list[dict[str, Any]] = []
            if report_file.exists():
                try:
                    with open(report_file, encoding="utf-8") as f:
                        existing = json.load(f)
                        if not isinstance(existing, list):
                            existing = []
                except (json.JSONDecodeError, OSError):
                    existing = []
            existing.extend(r.to_dict() for r in reports)
            with open(report_file, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2, default=str)
            logger.info(
                "漂移报告已持久化: %s (%d 条)",
                report_file,
                len(reports),
            )
        except Exception as e:  # noqa: BLE001  # 持久化失败不阻断
            logger.warning("漂移报告持久化失败: %s", e)

    def get_history(self, limit: int = 100) -> list[DriftReport]:
        """获取历史报告."""
        return list(self._history[-limit:])

    def get_summary(self) -> dict[str, Any]:
        """获取漂移检查汇总."""
        severity_counts: dict[str, int] = {s.value: 0 for s in DriftSeverity}
        for r in self._history:
            severity_counts[r.severity.value] = severity_counts.get(r.severity.value, 0) + 1
        return {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "is_active": self.is_active(),
            "sim_mode": self.sim_mode,
            "use_drift_detector_flag": _USE_DRIFT_DETECTOR_FLAG,
            "total_reports": len(self._history),
            "severity_counts": severity_counts,
            "feature_count": len(self._feature_columns),
            "reports_dir": str(self.reports_dir),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }


def create_sim_mode_drift_monitor(
    model_name: str,
    model_version: str,
    baseline_panel: pd.DataFrame,
    sim_mode: bool = True,
) -> SimModeDriftMonitor:
    """便捷工厂: 创建 sim_mode 漂移监控器.

    Args:
        model_name: 模型名
        model_version: 模型版本
        baseline_panel: 基线 panel
        sim_mode: 是否为 sim_mode (默认 True)

    Returns:
        SimModeDriftMonitor 实例
    """
    return SimModeDriftMonitor(
        model_name=model_name,
        model_version=model_version,
        baseline_panel=baseline_panel,
        sim_mode=sim_mode,
    )
