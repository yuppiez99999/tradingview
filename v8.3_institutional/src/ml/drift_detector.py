# -*- coding: utf-8 -*-
"""
模型漂移检测器 v1.0 — P0 修复

检测模型性能衰减和数据分布变化:
1. IC 衰减监控: 滚动 IC 连续 N 日 < 阈值 → 告警
2. ADWIN 概念漂移: 自适应窗口检测数据分布变化
3. KS 检验: 特征分布偏移检测 (Kolmogorov-Smirnov)
4. PSI: 人口稳定性指数 (Population Stability Index)
5. 自动重训练触发: 多维度触发条件

使用方式:
    detector = ModelDriftDetector()
    detector.update_ic(date, ic_value)
    detector.update_feature_dist(feature_name, current_values, reference_values)
    alerts = detector.check_all()
    if alerts:
        print(f"检测到 {len(alerts)} 个漂移告警")
        if any(a['severity'] == 'critical' for a in alerts):
            print("触发自动重训练!")
"""

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("drift_detector")


class DriftType(Enum):
    IC_DECAY = "ic_decay"
    CONCEPT_DRIFT = "concept_drift"
    FEATURE_SHIFT = "feature_shift"
    PSI_DRIFT = "psi_drift"


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class DriftAlert:
    """漂移告警"""

    timestamp: str
    drift_type: DriftType
    severity: Severity
    message: str
    metric_name: str
    current_value: float
    threshold: float
    recommendation: str = ""


class ADWINDetector:
    """ADWIN (Adaptive Windowing) 概念漂移检测器

    维护一个自适应窗口, 当窗口内两段数据的均值差异显著时,
    认为发生了概念漂移.

    参考: Bifet & Gavaldà (2007) "Learning from Time-Changing Data"
    """

    def __init__(self, delta: float = 0.002, min_window: int = 30):
        """
        Args:
            delta: 显著性水平 (越小越保守)
            min_window: 最小窗口长度
        """
        self.delta = delta
        self.min_window = min_window
        self.window: deque = deque()
        self._total: float = 0.0
        self._variance: float = 0.0

    def add(self, value: float) -> bool:
        """添加新值, 返回是否检测到漂移

        Args:
            value: 新的观测值 (如 IC 值)

        Returns:
            True 如果检测到概念漂移
        """
        self.window.append(value)
        self._total += value

        if len(self.window) < self.min_window * 2:
            return False

        # 尝试在各个位置切割窗口
        window_list = list(self.window)
        n = len(window_list)

        for split in range(self.min_window, n - self.min_window + 1):
            w0 = window_list[:split]
            w1 = window_list[split:]

            n0, n1 = len(w0), len(w1)
            mean0, mean1 = np.mean(w0), np.mean(w1)

            # ADWIN 检验统计量
            n_total = n0 + n1
            epsilon = np.sqrt(1.0 / (2.0 * n_total) * np.log(4.0 / self.delta))

            if abs(mean0 - mean1) > epsilon:
                # 检测到漂移, 截断旧窗口
                logger.warning(
                    f"ADWIN 漂移检测: 窗口分割 {split}/{n}, 均值差异 {mean0:.4f} vs {mean1:.4f}, 阈值 {epsilon:.4f}"
                )
                # 保留新窗口
                self.window = deque(w1)
                self._total = sum(w1)
                return True

        return False


class ModelDriftDetector:
    """模型漂移检测器

    多维度检测模型性能衰减和数据分布变化:

    1. IC 衰减: 滚动 IC 均值连续 N 日 < 阈值
    2. ADWIN: 概念漂移检测
    3. KS 检验: 特征分布偏移
    4. PSI: 人口稳定性指数
    """

    def __init__(
        self,
        ic_window: int = 20,
        ic_threshold: float = 0.02,
        ic_consecutive_days: int = 5,
        ks_pvalue: float = 0.05,
        psi_threshold: float = 0.25,
        adwin_delta: float = 0.002,
    ):
        """
        Args:
            ic_window: IC 滚动窗口
            ic_threshold: IC 告警阈值 (低于此值告警)
            ic_consecutive_days: 连续 N 天低 IC 才告警
            ks_pvalue: KS 检验 p 值阈值
            psi_threshold: PSI 告警阈值 (>0.25 为显著变化)
            adwin_delta: ADWIN 显著性水平
        """
        self.ic_window = ic_window
        self.ic_threshold = ic_threshold
        self.ic_consecutive_days = ic_consecutive_days
        self.ks_pvalue = ks_pvalue
        self.psi_threshold = psi_threshold
        self.adwin_delta = adwin_delta

        # IC 历史
        self._ic_history: deque = deque(maxlen=252)  # 1 年
        self._low_ic_streak: int = 0

        # ADWIN 检测器
        self._adwin = ADWINDetector(delta=adwin_delta)

        # 特征参考分布
        self._reference_features: Dict[str, np.ndarray] = {}

        # 告警历史
        self._alert_history: List[DriftAlert] = []

        logger.info(
            f"ModelDriftDetector 初始化: IC窗口={ic_window}, "
            f"IC阈值={ic_threshold}, 连续天数={ic_consecutive_days}, "
            f"PSI阈值={psi_threshold}"
        )

    # ============================================================
    # IC 衰减监控
    # ============================================================

    def update_ic(self, date, ic_value: float) -> Optional[DriftAlert]:
        """更新 IC 值并检查衰减

        Args:
            date: 日期
            ic_value: 当日 IC 值

        Returns:
            DriftAlert 如果检测到衰减, 否则 None
        """
        self._ic_history.append({"date": date, "ic": ic_value, "timestamp": datetime.now()})

        # 检查连续低 IC
        if abs(ic_value) < self.ic_threshold:
            self._low_ic_streak += 1
        else:
            self._low_ic_streak = 0

        # 连续 N 天低 IC → 告警
        if self._low_ic_streak >= self.ic_consecutive_days:
            alert = DriftAlert(
                timestamp=datetime.now().isoformat(),
                drift_type=DriftType.IC_DECAY,
                severity=Severity.CRITICAL if self._low_ic_streak >= self.ic_consecutive_days * 2 else Severity.WARNING,
                message=f"IC 连续 {self._low_ic_streak} 天低于阈值 {self.ic_threshold}",
                metric_name="rolling_ic",
                current_value=ic_value,
                threshold=self.ic_threshold,
                recommendation="触发模型重训练, 检查特征有效性",
            )
            self._alert_history.append(alert)
            logger.warning(alert.message)
            return alert

        # 检查滚动 IC 均值下降
        if len(self._ic_history) >= self.ic_window:
            recent_ics = [h["ic"] for h in list(self._ic_history)[-self.ic_window :]]
            rolling_mean = np.mean(recent_ics)
            rolling_std = np.std(recent_ics)

            if rolling_mean < 0 and rolling_std > 0.1:
                alert = DriftAlert(
                    timestamp=datetime.now().isoformat(),
                    drift_type=DriftType.IC_DECAY,
                    severity=Severity.WARNING,
                    message=f"滚动 {self.ic_window} 日 IC 均值为负 ({rolling_mean:.4f})",
                    metric_name="rolling_ic_mean",
                    current_value=rolling_mean,
                    threshold=0.0,
                    recommendation="检查模型是否过期, 考虑增量更新",
                )
                self._alert_history.append(alert)
                logger.warning(alert.message)
                return alert

        return None

    # ============================================================
    # ADWIN 概念漂移
    # ============================================================

    def update_adwin(self, value: float) -> Optional[DriftAlert]:
        """用 ADWIN 检测概念漂移

        Args:
            value: 观测值 (如 IC, 预测误差, 夏普比率)

        Returns:
            DriftAlert 如果检测到漂移
        """
        drift_detected = self._adwin.add(value)

        if drift_detected:
            alert = DriftAlert(
                timestamp=datetime.now().isoformat(),
                drift_type=DriftType.CONCEPT_DRIFT,
                severity=Severity.CRITICAL,
                message=f"ADWIN 检测到概念漂移 (当前值: {value:.4f})",
                metric_name="adwin",
                current_value=value,
                threshold=0.0,
                recommendation="数据分布发生显著变化, 立即重训练模型",
            )
            self._alert_history.append(alert)
            logger.critical(alert.message)
            return alert

        return None

    # ============================================================
    # KS 检验 — 特征分布偏移
    # ============================================================

    def set_reference_features(self, features: Dict[str, np.ndarray]):
        """设置参考特征分布 (通常是训练集特征)

        Args:
            features: {feature_name: values} 字典
        """
        self._reference_features = {k: np.array(v) for k, v in features.items()}
        logger.info(f"设置参考特征分布: {len(features)} 个特征")

    def check_feature_drift(self, current_features: Dict[str, np.ndarray]) -> List[DriftAlert]:
        """检查特征分布偏移 (KS 检验)

        Args:
            current_features: 当前特征值

        Returns:
            偏移告警列表
        """
        if not self._reference_features:
            logger.debug("未设置参考特征分布, 跳过 KS 检验")
            return []

        from scipy.stats import ks_2samp

        alerts = []
        for name, current_vals in current_features.items():
            if name not in self._reference_features:
                continue

            ref_vals = self._reference_features[name]
            if len(ref_vals) < 10 or len(current_vals) < 10:
                continue

            # KS 检验
            statistic, pvalue = ks_2samp(ref_vals, current_vals)

            if pvalue < self.ks_pvalue:
                severity = Severity.CRITICAL if statistic > 0.3 else Severity.WARNING
                alert = DriftAlert(
                    timestamp=datetime.now().isoformat(),
                    drift_type=DriftType.FEATURE_SHIFT,
                    severity=severity,
                    message=f"特征 '{name}' 分布偏移 (KS stat={statistic:.4f}, p={pvalue:.4f})",
                    metric_name=name,
                    current_value=statistic,
                    threshold=self.ks_pvalue,
                    recommendation=f"特征 '{name}' 分布已变化, 检查数据源或重训练",
                )
                alerts.append(alert)
                self._alert_history.append(alert)

        if alerts:
            logger.warning(f"检测到 {len(alerts)} 个特征分布偏移")

        return alerts

    # ============================================================
    # PSI — 人口稳定性指数
    # ============================================================

    @staticmethod
    def compute_psi(expected: np.ndarray, actual: np.ndarray, bins: int = 10) -> float:
        """计算 PSI (Population Stability Index)

        PSI < 0.1: 稳定
        0.1 ≤ PSI < 0.25: 轻微变化
        PSI ≥ 0.25: 显著变化

        Args:
            expected: 参考分布 (训练集)
            actual: 实际分布 (当前)
            bins: 分箱数

        Returns:
            PSI 值
        """
        # 合并分箱边界
        breakpoints = np.percentile(np.concatenate([expected, actual]), np.linspace(0, 100, bins + 1))
        breakpoints = np.unique(breakpoints)

        # 计算各组频率
        expected_counts, _ = np.histogram(expected, bins=breakpoints)
        actual_counts, _ = np.histogram(actual, bins=breakpoints)

        # 避免除零
        expected_pct = expected_counts / max(len(expected), 1)
        actual_pct = actual_counts / max(len(actual), 1)

        expected_pct = np.where(expected_pct == 0, 0.0001, expected_pct)
        actual_pct = np.where(actual_pct == 0, 0.0001, actual_pct)

        # PSI = Σ (actual% - expected%) * ln(actual% / expected%)
        psi = np.sum((actual_pct - expected_pct) * np.log(actual_pct / expected_pct))

        return float(psi)

    def check_psi(self, current_features: Dict[str, np.ndarray]) -> List[DriftAlert]:
        """用 PSI 检查特征稳定性

        Args:
            current_features: 当前特征值

        Returns:
            PSI 告警列表
        """
        if not self._reference_features:
            return []

        alerts = []
        for name, current_vals in current_features.items():
            if name not in self._reference_features:
                continue

            ref_vals = self._reference_features[name]
            if len(ref_vals) < 20 or len(current_vals) < 20:
                continue

            psi = self.compute_psi(ref_vals, current_vals)

            if psi > self.psi_threshold:
                severity = Severity.CRITICAL if psi > 0.5 else Severity.WARNING
                alert = DriftAlert(
                    timestamp=datetime.now().isoformat(),
                    drift_type=DriftType.PSI_DRIFT,
                    severity=severity,
                    message=f"特征 '{name}' PSI={psi:.4f} (阈值 {self.psi_threshold})",
                    metric_name=name,
                    current_value=psi,
                    threshold=self.psi_threshold,
                    recommendation=f"特征 '{name}' 稳定性差, 考虑重新训练或剔除",
                )
                alerts.append(alert)
                self._alert_history.append(alert)

        if alerts:
            logger.warning(f"检测到 {len(alerts)} 个 PSI 告警")

        return alerts

    # ============================================================
    # 综合检查
    # ============================================================

    def check_all(self) -> List[DriftAlert]:
        """执行所有漂移检查

        Returns:
            所有告警列表
        """
        # 返回最近 24 小时内的告警
        cutoff = datetime.now() - timedelta(hours=24)
        recent = [a for a in self._alert_history if datetime.fromisoformat(a.timestamp) > cutoff]
        return recent

    def should_retrain(self) -> Tuple[bool, str]:
        """判断是否需要重训练模型

        Returns:
            (是否需要重训练, 原因)
        """
        recent = self.check_all()
        critical = [a for a in recent if a.severity == Severity.CRITICAL]

        if critical:
            reasons = "; ".join(a.message for a in critical[:3])
            return True, f"检测到 {len(critical)} 个严重漂移: {reasons}"

        if len(recent) >= 3:
            return True, f"检测到 {len(recent)} 个告警, 建议重训练"

        return False, "模型状态正常"

    # ============================================================
    # 报告
    # ============================================================

    def generate_report(self) -> dict:
        """生成漂移检测报告"""
        recent = self.check_all()

        # IC 统计
        ics = [h["ic"] for h in self._ic_history]
        ic_stats = {
            "n_samples": len(ics),
            "latest_ic": ics[-1] if ics else None,
            "mean_ic_20d": float(np.mean(ics[-20:])) if len(ics) >= 20 else None,
            "mean_ic_60d": float(np.mean(ics[-60:])) if len(ics) >= 60 else None,
            "low_ic_streak": self._low_ic_streak,
            "ic_threshold": self.ic_threshold,
        }

        return {
            "timestamp": datetime.now().isoformat(),
            "ic_stats": ic_stats,
            "alerts_24h": [
                {
                    "type": a.drift_type.value,
                    "severity": a.severity.value,
                    "message": a.message,
                    "metric": a.metric_name,
                    "current": a.current_value,
                    "threshold": a.threshold,
                    "recommendation": a.recommendation,
                }
                for a in recent
            ],
            "should_retrain": self.should_retrain(),
            "total_alerts": len(self._alert_history),
        }
