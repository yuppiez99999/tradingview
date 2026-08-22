"""DriftMonitor + DelayedLabelTracker + Shadow 真实数据桥接器 — W1.3b (2026-08-07).

=================================================================
任务: W1.3b Day 1 (Wave 1 第三子任务)
关联缺口:
    - G2: DriftMonitor 在空/模拟数据上运行, 未消费 daily_returns.jsonl
    - G6: DriftMonitor 与 DelayedLabelTracker 未集成

功能:
    1. 从 daily_returns.jsonl 读取真实 Shadow 收益
    2. 桥接 DriftMonitor (特征漂移检测) 与 DelayedLabelTracker (IC/IC_IR 计算)
    3. 检测 IC_IR 退化, 生成告警
    4. PSI 阈值校准 (Day 3 任务)

设计原则 (HC 合规):
    - HC-1: 不切任何 Feature Flag (USE_DRIFT_DETECTOR 保持 False, 用 sim_mode=True 绕过)
    - HC-4: 只读评估, 不修改 V9 基线 / positions.json
    - HC-5: 配置通过 __init__ 参数注入
    - fail-safe: 任一模块失败不中断 EOD 工作流

粒度处理:
    daily_returns.jsonl 只有组合级收益 (daily_return), 而 DelayedLabelTracker
    需要 symbol 级标签 (actual_label). 两种模式:
        1. symbol 级模式 (推荐): 调用方提供 symbol_returns_provider, 获取每个 symbol 的收益
        2. 组合级降级模式: 无 symbol 级数据时, 用组合收益作为整体代理标签

参考:
    - 设计: docs/自我进化框架/W1.3b_DRIFT_MONITOR_REAL_DATA_INTEGRATION.md
    - 上游: utils/alpha/shadow_real_data_feeder.py (daily_returns.jsonl 产出)
    - 依赖: utils/alpha/drift_monitor.py (SimModeDriftMonitor)
    - 依赖: utils/alpha/delayed_label_tracker.py (DelayedLabelTracker)

用法:
    # 单日集成 (生产)
    from utils.alpha.drift_shadow_integrator import DriftShadowIntegrator
    from utils.alpha.drift_monitor import SimModeDriftMonitor
    from utils.alpha.delayed_label_tracker import DelayedLabelTracker

    monitor = SimModeDriftMonitor(model_name="v9_lgb", model_version="v9...", sim_mode=True)
    tracker = DelayedLabelTracker(model_name="v9_lgb")
    integrator = DriftShadowIntegrator(drift_monitor=monitor, label_tracker=tracker)

    result = integrator.run_daily_integration("2026-08-07")

    # 历史回填
    results = integrator.backfill_history("2026-07-23", "2026-08-07")
=================================================================
"""
from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================

DEFAULT_DAILY_RETURNS_PATH = _PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
DEFAULT_INTEGRATION_REPORTS_DIR = _PROJECT_ROOT / "reports" / "drift"

# IC_IR 退化阈值 (|baseline - current| > 0.3 触发告警)
DEFAULT_IC_DEGRADATION_THRESHOLD = 0.3

# 最小样本数 (低于此数降级模式, 仅记录不告警)
MIN_SAMPLES_FOR_ALERT = 20

# 日期格式
DATE_FMT = "%Y-%m-%d"
ISO_FMT = "%Y-%m-%dT%H:%M:%S"


# ============================================================
# 数据类
# ============================================================


@dataclass
class IntegrationResult:
    """单日集成运行结果.

    Attributes:
        date: 日期 (YYYY-MM-DD)
        daily_return: 当日组合收益 (从 daily_returns.jsonl 读取)
        drift_reports: DriftMonitor 产出的漂移报告列表
        delayed_metrics: DelayedLabelTracker 计算的 IC/IC_IR 指标 (None 表示未计算)
        ic_degradation: IC_IR 退化量 (|baseline - current|), None 表示无法计算
        alerts: 告警列表 (IC 退化 / PSI 超阈值 / 数据不足等)
        symbols_updated: 更新标签的 symbol 数
        skipped: 是否跳过 (数据缺失 / 模块未激活)
        error: 错误信息 (失败时填)
    """

    date: str
    daily_return: float = 0.0
    drift_reports: list[Any] = field(default_factory=list)  # list[DriftReport]
    delayed_metrics: Optional[Any] = None  # DelayedMetrics
    ic_degradation: Optional[float] = None
    alerts: list[str] = field(default_factory=list)
    symbols_updated: int = 0
    skipped: bool = False
    error: Optional[str] = None

    @property
    def is_success(self) -> bool:
        """是否成功 (至少一个模块产出结果)."""
        return (not self.skipped) and (
            len(self.drift_reports) > 0 or self.delayed_metrics is not None
        )

    def to_dict(self) -> dict[str, Any]:
        """转为 dict (用于 JSON 持久化)."""
        result = {
            "date": self.date,
            "daily_return": self.daily_return,
            "drift_reports_count": len(self.drift_reports),
            "drift_reports": [
                r.to_dict() if hasattr(r, "to_dict") else str(r)
                for r in self.drift_reports
            ],
            "delayed_metrics": (
                self.delayed_metrics.to_dict()
                if self.delayed_metrics and hasattr(self.delayed_metrics, "to_dict")
                else None
            ),
            "ic_degradation": self.ic_degradation,
            "alerts": self.alerts,
            "symbols_updated": self.symbols_updated,
            "skipped": self.skipped,
            "error": self.error,
        }
        return result


@dataclass
class PSICalibrationResult:
    """PSI 阈值校准结果 (Day 3 任务).

    Attributes:
        feature_name: 特征名
        low_threshold: LOW 阈值 (默认 0.1)
        medium_threshold: MEDIUM 阈值 (默认 0.25)
        high_threshold: HIGH 阈值 (校准值, 95th percentile)
        critical_threshold: CRITICAL 阈值 (校准值, 99th percentile)
        industrial_low: 工业标准 LOW (0.1)
        industrial_medium: 工业标准 MEDIUM (0.25)
        industrial_high: 工业标准 HIGH (0.25)
        industrial_critical: 工业标准 CRITICAL (0.5)
        sample_size: 样本数
        is_calibrated: 是否成功校准 (样本不足时为 False, 用工业标准)
    """

    feature_name: str
    low_threshold: float = 0.1
    medium_threshold: float = 0.25
    high_threshold: float = 0.25
    critical_threshold: float = 0.5
    industrial_low: float = 0.1
    industrial_medium: float = 0.25
    industrial_high: float = 0.25
    industrial_critical: float = 0.5
    sample_size: int = 0
    is_calibrated: bool = False

    @property
    def exceeds_industrial_2x(self) -> bool:
        """校准值是否超过工业标准 2x (过拟合风险)."""
        return (
            self.high_threshold > self.industrial_high * 2
            or self.critical_threshold > self.industrial_critical * 2
        )


# ============================================================
# 异常体系
# ============================================================


class DriftShadowIntegratorError(Exception):
    """DriftShadowIntegrator 基础异常."""


# ============================================================
# 主类
# ============================================================


class DriftShadowIntegrator:
    """DriftMonitor + DelayedLabelTracker + Shadow 真实数据桥接器.

    设计原则:
        1. 不修改 DriftMonitor / DelayedLabelTracker 核心逻辑 (HC-1)
        2. 不切 Feature Flag (USE_DRIFT_DETECTOR 保持 False, 用 sim_mode=True)
        3. fail-safe: 任一模块失败不中断 EOD 工作流
        4. 增量运行: 每日盘后调用, 累积漂移历史

    HC 合规:
        - HC-1: 不切 Feature Flag
        - HC-4: 只读评估, 不修改 V9 基线
        - HC-5: 配置通过 __init__ 参数注入
    """

    def __init__(
        self,
        drift_monitor: Any,
        label_tracker: Any,
        daily_returns_path: Path = DEFAULT_DAILY_RETURNS_PATH,
        reports_dir: Optional[Path] = None,
        symbol_returns_provider: Optional[Callable[[str], dict[str, float]]] = None,
        baseline_ic_ir: Optional[float] = None,
        ic_degradation_threshold: float = DEFAULT_IC_DEGRADATION_THRESHOLD,
        verbose: bool = False,
    ) -> None:
        """初始化 DriftShadowIntegrator.

        Args:
            drift_monitor: SimModeDriftMonitor 实例 (必须实现 run_daily_check)
            label_tracker: DelayedLabelTracker 实例 (必须实现 update_actual_labels_batch)
            daily_returns_path: daily_returns.jsonl 路径 (Shadow 真实收益)
            reports_dir: 集成报告持久化目录 (None 则用 reports/drift)
            symbol_returns_provider: 获取 symbol 级收益的回调
                回调签名: (date: str) -> {symbol: return}
                None 时降级为组合级代理标签
            baseline_ic_ir: 基线 IC_IR (V9 基线 0.88, None 时不做退化检测)
            ic_degradation_threshold: IC_IR 退化告警阈值 (默认 0.3)
            verbose: 是否输出详细日志

        Raises:
            ValueError: 参数无效
        """
        if drift_monitor is None:
            raise ValueError("drift_monitor 不能为 None")
        if label_tracker is None:
            raise ValueError("label_tracker 不能为 None")
        if not hasattr(drift_monitor, "run_daily_check"):
            raise ValueError(
                f"drift_monitor 必须实现 run_daily_check 方法, 实际类型: {type(drift_monitor).__name__}"
            )
        if not hasattr(label_tracker, "update_actual_labels_batch"):
            raise ValueError(
                f"label_tracker 必须实现 update_actual_labels_batch 方法, 实际类型: {type(label_tracker).__name__}"
            )
        if ic_degradation_threshold < 0:
            raise ValueError(
                f"ic_degradation_threshold 必须 >= 0, 实际: {ic_degradation_threshold}"
            )

        self._monitor = drift_monitor
        self._tracker = label_tracker
        self._daily_returns_path = Path(daily_returns_path)
        self._reports_dir = Path(reports_dir) if reports_dir else DEFAULT_INTEGRATION_REPORTS_DIR
        self._reports_dir.mkdir(parents=True, exist_ok=True)
        self._symbol_returns_provider = symbol_returns_provider
        self._baseline_ic_ir = baseline_ic_ir
        self._ic_degradation_threshold = ic_degradation_threshold
        self._verbose = verbose

        # 累积历史 (用于趋势分析)
        self._history: list[IntegrationResult] = []

    # ------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------

    def run_daily_integration(
        self,
        date: str,
        current_panel: Optional[Any] = None,
        current_predictions: Optional[dict[str, float]] = None,
        model_version: Optional[str] = None,
    ) -> IntegrationResult:
        """每日集成运行.

        步骤:
            1. 从 daily_returns.jsonl 读取当日真实收益
            2. 若 current_panel 传入, 调用 drift_monitor.run_daily_check()
            3. 若 current_predictions 传入, 调用 label_tracker.record_predictions_batch()
            4. 用真实收益更新 label_tracker 的 actual_label
            5. 调用 label_tracker.compute_delayed_metrics() 计算 IC/IC_IR
            6. 检测 IC_IR 退化, 若超阈值则生成告警

        Args:
            date: 日期 (YYYY-MM-DD)
            current_panel: 当日特征 panel (None 时跳过特征漂移检测)
            current_predictions: 当日预测 {symbol: score} (None 时跳过预测记录)
            model_version: 模型版本 (None 时用 tracker 默认)

        Returns:
            IntegrationResult: 含 drift_reports, delayed_metrics, ic_degradation, alerts
        """
        result = IntegrationResult(date=date)

        # 1. 读取当日真实收益
        try:
            daily_return = self._read_daily_return(date)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("读取 daily_returns 失败 (date=%s): %s", date, e)
            result.skipped = True
            result.error = f"daily_returns_read_failed: {e}"
            return result

        if daily_return is None:
            logger.info("当日无 Shadow 数据 (date=%s), 跳过", date)
            result.skipped = True
            result.error = "no_shadow_data_for_date"
            return result

        result.daily_return = daily_return
        logger.info(
            "集成运行 date=%s, daily_return=%.4f%%", date, daily_return * 100
        )

        # 2. 特征漂移检测 (若 panel 传入)
        if current_panel is not None:
            try:
                drift_reports = self._monitor.run_daily_check(current_panel)
                result.drift_reports = drift_reports or []
                logger.info(
                    "DriftMonitor 产出 %d 个报告", len(result.drift_reports)
                )
                # 检查 PSI 超阈值
                for report in result.drift_reports:
                    psi = getattr(report, "psi", 0.0) or 0.0
                    if psi >= 0.25:  # HIGH 阈值
                        feature = getattr(report, "feature_name", "unknown")
                        result.alerts.append(
                            f"psi_high: feature={feature}, psi={psi:.4f}"
                        )
            except (RuntimeError, ValueError, OSError) as e:
                logger.warning("DriftMonitor.run_daily_check 失败: %s", e)
                result.alerts.append(f"drift_monitor_failed: {e}")
        else:
            logger.info("无 current_panel, 跳过特征漂移检测")

        # 3. 记录预测 (若 predictions 传入)
        if current_predictions is not None and model_version is not None:
            try:
                self._tracker.record_predictions_batch(
                    date=date,
                    predictions=current_predictions,
                    model_version=model_version,
                )
                logger.info(
                    "记录预测: date=%s, n=%d", date, len(current_predictions)
                )
            except (RuntimeError, OSError) as e:
                logger.warning("record_predictions_batch 失败: %s", e)
                result.alerts.append(f"record_predictions_failed: {e}")

        # 4. 更新实际标签 (用真实收益)
        labels_updated = self._update_labels_with_real_returns(date)
        result.symbols_updated = labels_updated
        logger.info("更新标签: %d 个 symbol", labels_updated)

        # 5. 计算 IC/IC_IR
        try:
            metrics = self._tracker.compute_delayed_metrics(model_version)
            result.delayed_metrics = metrics
            logger.info(
                "IC=%.4f, IC_IR=%.4f, observed=%d/%d",
                metrics.ic,
                metrics.ic_ir,
                metrics.n_observed,
                metrics.n_predictions,
            )

            # 6. 检测 IC_IR 退化
            if self._baseline_ic_ir is not None and metrics.n_observed >= MIN_SAMPLES_FOR_ALERT:
                degradation = abs(self._baseline_ic_ir - metrics.ic_ir)
                result.ic_degradation = degradation
                if degradation > self._ic_degradation_threshold:
                    result.alerts.append(
                        f"ic_ir_degradation: baseline={self._baseline_ic_ir:.4f}, "
                        f"current={metrics.ic_ir:.4f}, degradation={degradation:.4f}"
                    )
                    logger.warning(
                        "IC_IR 退化告警: baseline=%.4f, current=%.4f, degradation=%.4f",
                        self._baseline_ic_ir,
                        metrics.ic_ir,
                        degradation,
                    )
            elif metrics.n_observed < MIN_SAMPLES_FOR_ALERT:
                result.alerts.append(
                    f"insufficient_samples: n_observed={metrics.n_observed} < {MIN_SAMPLES_FOR_ALERT}, 降级模式"
                )

        except (RuntimeError, ValueError, ZeroDivisionError) as e:
            logger.warning("compute_delayed_metrics 失败: %s", e)
            result.alerts.append(f"compute_metrics_failed: {e}")

        # 持久化
        self._persist_result(result)
        self._history.append(result)
        return result

    def backfill_history(
        self,
        start_date: str,
        end_date: str,
        panel_history: Optional[dict[str, Any]] = None,
        prediction_history: Optional[dict[str, dict[str, float]]] = None,
        model_version: Optional[str] = None,
    ) -> list[IntegrationResult]:
        """历史回填 (用于 2026-07-23 ~ 2026-08-07 观察期).

        遍历日期范围, 对每个交易日调用 run_daily_integration.
        若 panel_history / prediction_history 未提供, 仅做收益标签回填 + IC 计算.

        Args:
            start_date: 起始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            panel_history: {date: panel} 历史特征 panel
            prediction_history: {date: {symbol: score}} 历史预测
            model_version: 模型版本

        Returns:
            list[IntegrationResult]: 每日结果 (按日期升序)
        """
        try:
            start_dt = datetime.strptime(start_date, DATE_FMT)
            end_dt = datetime.strptime(end_date, DATE_FMT)
        except ValueError as e:
            raise ValueError(f"日期格式错误, 期望 YYYY-MM-DD: {e}") from e

        if start_dt > end_dt:
            raise ValueError(
                f"start_date ({start_date}) 不能晚于 end_date ({end_date})"
            )

        results: list[IntegrationResult] = []
        current_dt = start_dt
        skipped_weekend = 0

        while current_dt <= end_dt:
            date_str = current_dt.strftime(DATE_FMT)

            # 跳过周末
            if current_dt.weekday() >= 5:
                skipped_weekend += 1
                current_dt += timedelta(days=1)
                continue

            panel = panel_history.get(date_str) if panel_history else None
            predictions = (
                prediction_history.get(date_str) if prediction_history else None
            )

            result = self.run_daily_integration(
                date=date_str,
                current_panel=panel,
                current_predictions=predictions,
                model_version=model_version,
            )
            results.append(result)
            current_dt += timedelta(days=1)

        logger.info(
            "历史回填完成: %d 个交易日, %d 个周末跳过, %d 个成功, %d 个跳过",
            len(results),
            skipped_weekend,
            sum(1 for r in results if r.is_success),
            sum(1 for r in results if r.skipped),
        )
        return results

    def calibrate_psi_thresholds(
        self,
        reference_panel: Optional[Any] = None,
        target_false_positive_rate: float = 0.05,
        rolling_window: int = 20,
        factor_frequency: str = "daily",
    ) -> list[PSICalibrationResult]:
        """PSI 阈值校准 (P0 改进 — 2026-08-19 实现).

        用参考期特征分布作为基准, 计算各特征的 PSI 分布,
        校准阈值使得误报率 ≈ target_false_positive_rate.

        策略:
            1. 取参考期特征 panel (baseline)
            2. 对每个特征, 用 rolling window 计算 PSI 序列
            3. 取 PSI 分布的 (1 - fpr) percentile 作为 HIGH 阈值
            4. 取更高 percentile 作为 CRITICAL 阈值
            5. 校准值不得超过工业标准 2x (防过拟合)
            6. 按因子频率分组校准 (日频/周频/月频不同窗口)

        Args:
            reference_panel: 参考期特征 panel (None 时用 DriftMonitor 的 baseline)
            target_false_positive_rate: 目标误报率 (默认 5%)
            rolling_window: rolling window 大小 (默认 20)
            factor_frequency: 因子频率 ("daily"/"weekly"/"monthly")

        Returns:
            list[PSICalibrationResult]: 各特征的校准结果
        """
        logger.info(
            "PSI 阈值校准: target_fpr=%.2f, window=%d, freq=%s",
            target_false_positive_rate,
            rolling_window,
            factor_frequency,
        )

        freq_window_map = {"daily": 20, "weekly": 8, "monthly": 3}
        effective_window = freq_window_map.get(factor_frequency, rolling_window)

        baseline = reference_panel if reference_panel is not None else getattr(self._monitor, "_baseline_panel", None)
        if baseline is None:
            logger.warning("无基线 panel, 无法校准 PSI 阈值")
            return []

        try:
            import pandas as pd
        except ImportError:
            logger.warning("pandas 不可用, 无法校准")
            return []

        feature_columns = getattr(self._monitor, "_feature_columns", []) or [
            c for c in baseline.columns if c not in ("code", "date", "y", "symbol")
        ]

        sample_size = len(baseline) if hasattr(baseline, "__len__") else 0
        if sample_size < effective_window * 2:
            logger.warning(
                "样本不足 (%d < %d), 返回工业标准阈值",
                sample_size,
                effective_window * 2,
            )
            return [
                PSICalibrationResult(
                    feature_name=feature,
                    sample_size=sample_size,
                    is_calibrated=False,
                )
                for feature in feature_columns
            ]

        from utils.alpha.drift_monitor import compute_psi

        high_percentile = (1.0 - target_false_positive_rate) * 100.0
        critical_percentile = (1.0 - target_false_positive_rate / 5.0) * 100.0

        results: list[PSICalibrationResult] = []
        for feature in feature_columns:
            if feature not in baseline.columns:
                results.append(
                    PSICalibrationResult(
                        feature_name=feature,
                        sample_size=sample_size,
                        is_calibrated=False,
                    )
                )
                continue

            feature_series = pd.Series(baseline[feature]).dropna()
            if len(feature_series) < effective_window * 2:
                results.append(
                    PSICalibrationResult(
                        feature_name=feature,
                        sample_size=len(feature_series),
                        is_calibrated=False,
                    )
                )
                continue

            psi_values: list[float] = []
            for i in range(effective_window, len(feature_series)):
                window_baseline = feature_series.iloc[: i - effective_window + 1]
                window_current = feature_series.iloc[i - effective_window + 1 : i + 1]
                if len(window_baseline) < 2 or len(window_current) < 2:
                    continue
                psi_val = compute_psi(window_baseline, window_current)
                if psi_val > 0:
                    psi_values.append(psi_val)

            if len(psi_values) < 10:
                results.append(
                    PSICalibrationResult(
                        feature_name=feature,
                        sample_size=len(psi_values),
                        is_calibrated=False,
                    )
                )
                continue

            import numpy as np

            psi_array = np.array(psi_values)
            calibrated_high = float(np.percentile(psi_array, high_percentile))
            calibrated_critical = float(np.percentile(psi_array, critical_percentile))

            industrial_high = 0.25
            industrial_critical = 0.5
            max_high = industrial_high * 2.0
            max_critical = industrial_critical * 2.0

            calibrated_high = min(max(calibrated_high, 0.05), max_high)
            calibrated_critical = min(max(calibrated_critical, 0.10), max_critical)
            if calibrated_critical <= calibrated_high:
                calibrated_critical = calibrated_high + 0.05

            result = PSICalibrationResult(
                feature_name=feature,
                low_threshold=0.1,
                medium_threshold=0.25,
                high_threshold=calibrated_high,
                critical_threshold=calibrated_critical,
                industrial_low=0.1,
                industrial_medium=0.25,
                industrial_high=industrial_high,
                industrial_critical=industrial_critical,
                sample_size=len(psi_values),
                is_calibrated=True,
            )
            results.append(result)

            if result.exceeds_industrial_2x:
                logger.warning(
                    "PSI 校准: %s 阈值超过工业标准 2x (过拟合风险), high=%.4f, critical=%.4f",
                    feature,
                    calibrated_high,
                    calibrated_critical,
                )

        calibrated_count = sum(1 for r in results if r.is_calibrated)
        logger.info(
            "PSI 校准完成: %d/%d 特征成功校准 (freq=%s, window=%d)",
            calibrated_count,
            len(results),
            factor_frequency,
            effective_window,
        )
        return results

    # ------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------

    def _read_daily_return(self, date: str) -> Optional[float]:
        """从 daily_returns.jsonl 读取指定日期的收益.

        Args:
            date: 日期 (YYYY-MM-DD)

        Returns:
            daily_return 值 (None 表示当日无数据)
        """
        if not self._daily_returns_path.exists():
            logger.debug("daily_returns.jsonl 不存在: %s", self._daily_returns_path)
            return None

        with self._daily_returns_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if record.get("date") == date:
                        return float(record.get("daily_return", 0.0))
                except (json.JSONDecodeError, ValueError, TypeError) as e:
                    logger.debug("跳过无效行: %s", e)
                    continue

        return None

    def _update_labels_with_real_returns(self, date: str) -> int:
        """用真实收益更新 DelayedLabelTracker 的标签.

        两种模式:
            1. symbol 级模式: 调用 symbol_returns_provider 获取每个 symbol 的收益
            2. 组合级降级模式: 无 provider 时, 用组合收益作为所有 symbol 的标签

        Args:
            date: 预测日期 (t 日)

        Returns:
            更新成功的 symbol 数
        """
        if self._symbol_returns_provider is not None:
            # symbol 级模式
            try:
                symbol_returns = self._symbol_returns_provider(date)
                if not symbol_returns:
                    logger.info("symbol_returns_provider 返回空 (date=%s)", date)
                    return 0
                labels = {date: symbol_returns}
                count = self._tracker.update_actual_labels_batch(labels)
                return count
            except (RuntimeError, OSError, ValueError) as e:
                logger.warning("symbol_returns_provider 失败: %s, 降级为组合级", e)
                # 降级为组合级

        # 组合级降级模式: 用组合收益作为所有已记录预测的标签
        # 注意: 这是降级代理, IC 计算可能不准确
        try:
            # 读取组合收益
            daily_return = self._read_daily_return(date)
            if daily_return is None:
                return 0

            # 获取当日已记录的预测
            records = self._tracker.get_records(date=date) if hasattr(
                self._tracker, "get_records"
            ) else []
            if not records:
                return 0

            # 用组合收益作为所有 symbol 的标签
            labels = {
                date: {r.symbol: daily_return for r in records}
            }
            count = self._tracker.update_actual_labels_batch(labels)
            return count
        except (RuntimeError, OSError, AttributeError) as e:
            logger.warning("组合级标签更新失败: %s", e)
            return 0

    def _persist_result(self, result: IntegrationResult) -> None:
        """持久化集成结果到 JSON.

        文件: reports/drift/integration_{date}.json
        """
        report_path = self._reports_dir / f"integration_{result.date}.json"
        try:
            with report_path.open("w", encoding="utf-8") as f:
                json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
            logger.debug("集成结果已持久化: %s", report_path)
        except OSError as e:
            logger.warning("持久化集成结果失败: %s", e)

    def get_history(self) -> list[IntegrationResult]:
        """获取累积历史.

        Returns:
            list[IntegrationResult]: 历史结果列表 (按时间顺序)
        """
        return list(self._history)

    def get_summary(self) -> dict[str, Any]:
        """获取汇总统计.

        Returns:
            dict: 含 total_days, success_days, skipped_days, avg_ic, avg_ic_ir 等
        """
        if not self._history:
            return {"total_days": 0, "success_days": 0, "skipped_days": 0}

        success_days = sum(1 for r in self._history if r.is_success)
        skipped_days = sum(1 for r in self._history if r.skipped)
        ic_values = [
            r.delayed_metrics.ic
            for r in self._history
            if r.delayed_metrics is not None
        ]
        ic_ir_values = [
            r.delayed_metrics.ic_ir
            for r in self._history
            if r.delayed_metrics is not None
        ]

        return {
            "total_days": len(self._history),
            "success_days": success_days,
            "skipped_days": skipped_days,
            "avg_ic": sum(ic_values) / len(ic_values) if ic_values else 0.0,
            "avg_ic_ir": (
                sum(ic_ir_values) / len(ic_ir_values) if ic_ir_values else 0.0
            ),
            "total_alerts": sum(len(r.alerts) for r in self._history),
            "total_drift_reports": sum(len(r.drift_reports) for r in self._history),
        }


# ============================================================
# CLI 入口
# ============================================================


def _build_cli_parser():
    """构建 CLI 参数解析器."""
    import argparse

    parser = argparse.ArgumentParser(
        description="DriftShadowIntegrator — DriftMonitor + DelayedLabelTracker + Shadow 真实数据桥接 (W1.3b)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 单日集成
  py -m utils.alpha.drift_shadow_integrator --date 2026-08-07

  # 历史回填
  py -m utils.alpha.drift_shadow_integrator --start 2026-07-23 --end 2026-08-07

  # PSI 阈值校准
  py -m utils.alpha.drift_shadow_integrator --calibrate-psi
""",
    )
    parser.add_argument("--date", help="单日集成日期 (YYYY-MM-DD)")
    parser.add_argument("--start", help="历史回填起始日期 (YYYY-MM-DD)")
    parser.add_argument("--end", help="历史回填结束日期 (YYYY-MM-DD)")
    parser.add_argument(
        "--calibrate-psi", action="store_true", help="PSI 阈值校准 (Day 3)"
    )
    parser.add_argument(
        "--model-version", default="v9_lgb", help="模型版本 (默认 v9_lgb)"
    )
    parser.add_argument(
        "--baseline-ic-ir", type=float, default=0.88, help="基线 IC_IR (默认 0.88)"
    )
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    return parser


def _load_latest_predictions() -> dict[str, float] | None:
    """读取最近一次 AlphaPipeline 信号报告中的 predictions."""
    try:
        report_dir = _PROJECT_ROOT / "reports" / "pipeline"
        if not report_dir.exists():
            return None
        candidates = sorted(report_dir.glob("alpha_signals_*.json"), reverse=True)
        if not candidates:
            return None
        path = candidates[0]
        data = json.loads(path.read_text(encoding="utf-8"))
        signals = data.get("signals") or {}
        if not isinstance(signals, dict):
            return None
        return {str(k): float(v) for k, v in signals.items()}
    except (OSError, json.JSONDecodeError, ValueError, TypeError, KeyError):
        return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 主入口.

    Returns:
        0 = 成功, 1 = 失败
    """
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # 延迟导入 (避免循环依赖)
    from utils.alpha.delayed_label_tracker import DelayedLabelTracker
    from utils.alpha.drift_monitor import SimModeDriftMonitor

    # 初始化模块
    monitor = SimModeDriftMonitor(
        model_name="v9_lgb",
        model_version=args.model_version,
        sim_mode=True,  # HC-1: 用 sim_mode 绕过 Flag
    )
    tracker = DelayedLabelTracker(model_name="v9_lgb")

    integrator = DriftShadowIntegrator(
        drift_monitor=monitor,
        label_tracker=tracker,
        baseline_ic_ir=args.baseline_ic_ir,
        verbose=args.verbose,
    )

    if args.calibrate_psi:
        results = integrator.calibrate_psi_thresholds()
        logger.info(f"\n=== PSI 校准结果 ({len(results)} 个特征) ===")
        for r in results:
            logger.error(
                f"  {r.feature_name}: HIGH={r.high_threshold:.4f}, "
                f"CRITICAL={r.critical_threshold:.4f}, calibrated={r.is_calibrated}"
            )
        return 0

    if args.date:
        current_predictions = _load_latest_predictions()
        if current_predictions:
            logger.info("已加载最新 AlphaPipeline 信号: %d 个标的", len(current_predictions))
        else:
            logger.info("未找到 AlphaPipeline 信号报告, 跳过 predictions 记录")
        result = integrator.run_daily_integration(
            date=args.date,
            current_predictions=current_predictions,
            model_version=args.model_version,
        )
        logger.info(f"\n=== 集成结果 ({args.date}) ===")
        logger.info(f"  daily_return:    {result.daily_return:+.4f}%")
        logger.info(f"  drift_reports:   {len(result.drift_reports)}")
        logger.info(f"  symbols_updated: {result.symbols_updated}")
        logger.info(f"  alerts:          {len(result.alerts)}")
        if result.delayed_metrics:
            m = result.delayed_metrics
            logger.info(f"  IC:              {m.ic:.4f}")
            logger.info(f"  IC_IR:           {m.ic_ir:.4f}")
            logger.info(f"  observed:        {m.n_observed}/{m.n_predictions}")
        if result.alerts:
            logger.info("  --- 告警 ---")
            for a in result.alerts:
                logger.info(f"    ! {a}")
        return 0 if result.is_success else 1

    if args.start and args.end:
        results = integrator.backfill_history(
            start_date=args.start,
            end_date=args.end,
            model_version=args.model_version,
        )
        summary = integrator.get_summary()
        logger.info("\n=== 历史回填汇总 ===")
        logger.info(f"  总交易日:       {summary['total_days']}")
        logger.info(f"  成功:           {summary['success_days']}")
        logger.info(f"  跳过:           {summary['skipped_days']}")
        logger.info(f"  平均 IC:        {summary['avg_ic']:.4f}")
        logger.info(f"  平均 IC_IR:     {summary['avg_ic_ir']:.4f}")
        logger.info(f"  总告警:         {summary['total_alerts']}")
        logger.info(f"  总漂移报告:     {summary['total_drift_reports']}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
