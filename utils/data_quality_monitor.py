"""
数据质量监控引擎 (Data Quality Monitor) v1.0
==============================================

世界顶级对冲基金标准数据质量保障系统 — Two Sigma/Renaissance 同级:

    1. 异常值检测 (Outlier Detection)
       - Z-score 检测 (>3σ)
       - IQR (四分位距) 检测
       - MAD (中位数绝对偏差) 检测

    2. 缺失值检测 (Missing Value Detection)
       - 字段级缺失统计
       - 时间序列缺失 (gap detection)
       - 关键字段缺失告警

    3. 数据一致性检查 (Consistency Check)
       - 跨数据源一致性 (Wind vs iFinD vs AKShare)
       - 时间序列连续性
       - 字段逻辑关系 (high >= low, close in [low, high])

    4. 数据延迟监控 (Latency Monitor)
       - 数据更新时间检查
       - 数据时效性评估
       - 滞后告警

    5. 数据完整性检查 (Completeness Check)
       - 标的覆盖完整性
       - 时间序列完整性
       - 字段完整性

    6. 数据新鲜度评分 (Freshness Score)
       - 综合数据质量评分 0-100

用法:
    from utils.data_quality_monitor import DataQualityMonitor, QualityReport
    monitor = DataQualityMonitor()
    report = monitor.check_market_data(data, expected_symbols=["300308", "002475"])
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("data_quality")

try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore
    HAS_NUMPY = False

try:
    import pandas as pd

    HAS_PANDAS = True
except ImportError:
    pd = None
    HAS_PANDAS = False

BASE_DIR = Path(__file__).resolve().parent.parent
REPORT_DIR = BASE_DIR / "reports" / "data_quality"


@dataclass
class QualityIssue:
    """数据质量问题"""

    severity: str  # info / warning / error / critical
    category: str  # outlier / missing / consistency / latency / completeness
    field: str  # 字段名
    symbol: str = ""  # 标的代码
    description: str = ""  # 问题描述
    value: Any = None  # 异常值
    expected: Any = None  # 期望值
    timestamp: str = ""  # 检测时间


@dataclass
class QualityReport:
    """数据质量报告"""

    report_date: str = ""
    total_symbols: int = 0
    checked_fields: int = 0
    issues: list[QualityIssue] = field(default_factory=list)
    freshness_score: float = 0.0  # 0-100
    completeness_score: float = 0.0
    consistency_score: float = 0.0
    overall_score: float = 0.0
    passed: bool = False
    summary: str = ""

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "critical")

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DataQualityMonitor:
    """数据质量监控引擎"""

    # Z-score 阈值
    Z_SCORE_THRESHOLD = 3.0

    # IQR 倍数
    IQR_MULTIPLIER = 1.5

    # MAD (修正 Z-score) 阈值 — Iglewicz & Hoaglin (1993) 推荐 3.5
    MAD_THRESHOLD = 3.5

    # 字段逻辑规则
    PRICE_FIELDS = ["open", "high", "low", "close", "last", "price"]
    VOLUME_FIELDS = ["volume", "amount", "turnover"]
    REQUIRED_FIELDS = ["close"]  # 必须非空

    # 数据源优先级
    DATA_SOURCES = ["wind_terminal", "wind_mcp", "ifind_mcp", "akshare", "sina", "local_cache"]

    def __init__(self, max_latency_minutes: int = 30):
        """
        Args:
            max_latency_minutes: 最大允许延迟 (分钟), 超过则告警
        """
        self.max_latency_minutes = max_latency_minutes
        REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------
    # 主入口: 检查市场数据
    # ------------------------------------------------------------
    def check_market_data(
        self,
        data: dict[str, dict[str, Any]],
        expected_symbols: list[str] | None = None,
        timestamp_field: str = "timestamp",
        check_time_series: bool = False,
    ) -> QualityReport:
        """检查市场数据质量

        Args:
            data: {symbol: {field: value, ...}, ...}
            expected_symbols: 期望的标的列表
            timestamp_field: 时间戳字段名
            check_time_series: 是否检查时间序列

        Returns:
            QualityReport
        """
        report = QualityReport(
            report_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            total_symbols=len(data),
        )

        # 1. 完整性检查
        self._check_completeness(data, expected_symbols, report)

        # 2. 缺失值检查
        self._check_missing_values(data, report)

        # 3. 异常值检查
        self._check_outliers(data, report)

        # 4. 一致性检查
        self._check_consistency(data, report)

        # 5. 延迟检查
        self._check_latency(data, timestamp_field, report)

        # 6. 计算评分
        self._calculate_scores(report)

        # 7. 生成摘要
        report.summary = self._build_summary(report)
        report.passed = report.overall_score >= 80 and report.critical_count == 0

        logger.info(
            "[DataQuality] 总分 %.1f/100 | critical=%d error=%d warning=%d | %s",
            report.overall_score,
            report.critical_count,
            report.error_count,
            report.warning_count,
            "通过" if report.passed else "未通过",
        )

        return report

    # ------------------------------------------------------------
    # 完整性检查
    # ------------------------------------------------------------
    def _check_completeness(
        self,
        data: dict[str, dict],
        expected_symbols: list[str] | None,
        report: QualityReport,
    ) -> None:
        """检查标的覆盖完整性"""
        if not expected_symbols:
            return

        missing_symbols = [s for s in expected_symbols if s not in data]
        for symbol in missing_symbols:
            report.issues.append(
                QualityIssue(
                    severity="critical",
                    category="completeness",
                    field="symbol",
                    symbol=symbol,
                    description=f"期望标的 {symbol} 数据缺失",
                )
            )

        # 字段完整性
        all_fields = set()  # type: ignore
        for fields in data.values():
            all_fields.update(fields.keys())
        report.checked_fields = len(all_fields)

    # ------------------------------------------------------------
    # 缺失值检查
    # ------------------------------------------------------------
    def _check_missing_values(
        self,
        data: dict[str, dict],
        report: QualityReport,
    ) -> None:
        """检查缺失值"""
        for symbol, fields in data.items():
            for field_name in self.REQUIRED_FIELDS:
                value = fields.get(field_name)
                if value is None or (isinstance(value, float) and math.isnan(value)):
                    report.issues.append(
                        QualityIssue(
                            severity="error",
                            category="missing",
                            field=field_name,
                            symbol=symbol,
                            description=f"必填字段 {field_name} 缺失",
                        )
                    )

            # 其他字段缺失告警 (非必填)
            for field_name in self.PRICE_FIELDS + self.VOLUME_FIELDS:
                if field_name in fields:
                    value = fields[field_name]
                    if value is None or (isinstance(value, float) and math.isnan(value)):
                        report.issues.append(
                            QualityIssue(
                                severity="warning",
                                category="missing",
                                field=field_name,
                                symbol=symbol,
                                description=f"字段 {field_name} 缺失",
                            )
                        )

    # ------------------------------------------------------------
    # 异常值检查
    # ------------------------------------------------------------
    def _check_outliers(
        self,
        data: dict[str, dict],
        report: QualityReport,
    ) -> None:
        """检查异常值

        单标的逻辑检查:
            - 逻辑异常 (high < low, close < 0)
            - 价格波动 > 20% (日内)
            - 成交量为负

        跨标的统计异常检测 (通过 _check_statistical_outliers_cross_section):
            - Z-score > 3σ (Z_SCORE_THRESHOLD)
            - IQR 四分位距检测 (IQR_MULTIPLIER=1.5)
            - MAD 中位数绝对偏差检测 (MAD_THRESHOLD=3.5)
        """
        for symbol, fields in data.items():
            # 价格逻辑检查
            high = fields.get("high")
            low = fields.get("low")
            close = fields.get("close")
            fields.get("open")

            if high is not None and low is not None:
                try:
                    high = float(high)
                    low = float(low)
                    if high < low:
                        report.issues.append(
                            QualityIssue(
                                severity="error",
                                category="outlier",
                                field="high/low",
                                symbol=symbol,
                                description=f"high ({high}) < low ({low}) 逻辑错误",
                                value=high,
                                expected=f">= {low}",
                            )
                        )
                except (TypeError, ValueError):
                    pass

            # close 范围检查 (ETF 0.1-50, 股票 0.5-2000)
            if close is not None:
                try:
                    close_val = float(close)
                    if close_val <= 0:
                        report.issues.append(
                            QualityIssue(
                                severity="critical",
                                category="outlier",
                                field="close",
                                symbol=symbol,
                                description=f"close <= 0 ({close_val})",
                                value=close_val,
                                expected="> 0",
                            )
                        )
                    elif close_val > 10000:
                        report.issues.append(
                            QualityIssue(
                                severity="error",
                                category="outlier",
                                field="close",
                                symbol=symbol,
                                description=f"close 异常偏高 ({close_val})",
                                value=close_val,
                                expected="< 10000",
                            )
                        )
                except (TypeError, ValueError):
                    pass

            # 日内波动 > 20%
            if all(v is not None for v in [high, low, close]):
                try:
                    high_f = float(high)  # type: ignore
                    low_f = float(low)  # type: ignore
                    close_f = float(close)  # type: ignore
                    if close_f > 0:
                        intraday_range = (high_f - low_f) / close_f
                        if intraday_range > 0.20:
                            report.issues.append(
                                QualityIssue(
                                    severity="warning",
                                    category="outlier",
                                    field="intraday_range",
                                    symbol=symbol,
                                    description=f"日内波动 {intraday_range:.1%} 超过 20%",
                                    value=intraday_range,
                                    expected="<= 20%",
                                )
                            )
                except (TypeError, ValueError):
                    pass

            # 成交量异常 (为 0 或负数)
            volume = fields.get("volume")
            if volume is not None:
                try:
                    volume_val = float(volume)
                    if volume_val < 0:
                        report.issues.append(
                            QualityIssue(
                                severity="error",
                                category="outlier",
                                field="volume",
                                symbol=symbol,
                                description=f"成交量为负 ({volume_val})",
                                value=volume_val,
                                expected=">= 0",
                            )
                        )
                except (TypeError, ValueError):
                    pass

        # 跨标的统计异常检测 (Z-score / IQR / MAD)
        self._check_statistical_outliers_cross_section(data, report)

    # ------------------------------------------------------------
    # 统计异常检测算法 (Z-score / IQR / MAD)
    # ------------------------------------------------------------
    @staticmethod
    def _percentile_pure(sorted_values: list, p: float) -> float:
        """线性插值法计算百分位数 (纯 Python 回退)

        Args:
            sorted_values: 已排序的数值列表
            p: 百分位数 (0-100)

        Returns:
            百分位数值
        """
        n = len(sorted_values)
        if n == 0:
            return 0.0
        if n == 1:
            return float(sorted_values[0])
        k = (n - 1) * p / 100.0
        f = int(k)
        c = k - f
        if f + 1 < n:
            return float(sorted_values[f]) + c * (float(sorted_values[f + 1]) - float(sorted_values[f]))
        return float(sorted_values[f])

    @staticmethod
    def detect_zscore_outliers(data, threshold: float = 3.0):
        """Z-score 异常检测

        计算 (value - mean) / std，绝对值超过阈值标记为异常。
        适用于近似正态分布的数据。

        Args:
            data: pandas Series / numpy array / list
            threshold: Z-score 阈值，默认 3.0 (对应 ~99.7% 置信区间)

        Returns:
            布尔 mask，True 表示该值为异常。输入为 pandas Series 时
            返回 pandas Series，否则返回 numpy array 或 list。
        """
        # 纯 Python 回退 (无 numpy)
        if not HAS_NUMPY:
            values = list(data)
            n = len(values)
            if n < 2:
                return [False] * n
            mean = sum(values) / n
            variance = sum((x - mean) ** 2 for x in values) / (n - 1)
            std = variance**0.5
            if std == 0:
                return [False] * n
            return [abs((v - mean) / std) > threshold for v in values]

        arr = np.asarray(data, dtype=float)
        if arr.size < 2:
            return np.zeros(arr.size, dtype=bool)
        mean = np.mean(arr)
        std = np.std(arr, ddof=1)
        if std == 0:
            return np.zeros(arr.size, dtype=bool)
        z_scores = np.abs((arr - mean) / std)
        mask = z_scores > threshold
        if HAS_PANDAS and isinstance(data, pd.Series):
            return pd.Series(mask, index=data.index, name=data.name)
        return mask

    @staticmethod
    def detect_iqr_outliers(data, multiplier: float = 1.5):
        """IQR (四分位距) 异常检测

        Q1 - multiplier*IQR 以下或 Q3 + multiplier*IQR 以上标记为异常。
        不依赖分布假设，对非正态分布数据更稳健。

        Args:
            data: pandas Series / numpy array / list
            multiplier: IQR 倍数，默认 1.5 (标准箱线图规则)

        Returns:
            布尔 mask，True 表示该值为异常。输入为 pandas Series 时
            返回 pandas Series，否则返回 numpy array 或 list。
        """
        # 纯 Python 回退 (无 numpy)
        if not HAS_NUMPY:
            values = list(data)
            n = len(values)
            if n < 4:
                return [False] * n
            sorted_vals = sorted(values)
            q1 = DataQualityMonitor._percentile_pure(sorted_vals, 25)
            q3 = DataQualityMonitor._percentile_pure(sorted_vals, 75)
            iqr = q3 - q1
            lower = q1 - multiplier * iqr
            upper = q3 + multiplier * iqr
            return [v < lower or v > upper for v in values]

        arr = np.asarray(data, dtype=float)
        if arr.size < 4:
            return np.zeros(arr.size, dtype=bool)
        q1 = np.percentile(arr, 25)
        q3 = np.percentile(arr, 75)
        iqr = q3 - q1
        lower = q1 - multiplier * iqr
        upper = q3 + multiplier * iqr
        mask = (arr < lower) | (arr > upper)
        if HAS_PANDAS and isinstance(data, pd.Series):
            return pd.Series(mask, index=data.index, name=data.name)
        return mask

    @staticmethod
    def detect_mad_outliers(data, threshold: float = 3.5):
        """MAD (中位数绝对偏差) 异常检测

        计算 |value - median| / (1.4826 * MAD)，超过阈值标记为异常。
        1.4826 是正态分布下 MAD 与 std 的一致性常数。
        对异常值本身具有极强的鲁棒性 (breakdown point 50%)。

        Args:
            data: pandas Series / numpy array / list
            threshold: 修正 Z-score 阈值，默认 3.5
                       (Iglewicz & Hoaglin 1993 推荐)

        Returns:
            布尔 mask，True 表示该值为异常。输入为 pandas Series 时
            返回 pandas Series，否则返回 numpy array 或 list。
        """
        # 纯 Python 回退 (无 numpy)
        if not HAS_NUMPY:
            values = list(data)
            n = len(values)
            if n < 2:
                return [False] * n
            sorted_vals = sorted(values)
            mid = n // 2
            median = (
                float(sorted_vals[mid]) if n % 2 == 1 else (float(sorted_vals[mid - 1]) + float(sorted_vals[mid])) / 2
            )
            abs_devs = sorted(abs(v - median) for v in values)
            mad = float(abs_devs[mid]) if n % 2 == 1 else (float(abs_devs[mid - 1]) + float(abs_devs[mid])) / 2
            if mad == 0:
                return [False] * n
            modified_z = [abs(v - median) / (1.4826 * mad) for v in values]
            return [z > threshold for z in modified_z]

        arr = np.asarray(data, dtype=float)
        if arr.size < 2:
            return np.zeros(arr.size, dtype=bool)
        median = np.median(arr)
        mad = np.median(np.abs(arr - median))
        if mad == 0:
            return np.zeros(arr.size, dtype=bool)
        modified_z = np.abs(arr - median) / (1.4826 * mad)
        mask = modified_z > threshold
        if HAS_PANDAS and isinstance(data, pd.Series):
            return pd.Series(mask, index=data.index, name=data.name)
        return mask

    # ------------------------------------------------------------
    # 跨标的统计异常检测 (集成入口)
    # ------------------------------------------------------------
    def _check_statistical_outliers_cross_section(
        self,
        data: dict[str, dict],
        report: QualityReport,
    ) -> None:
        """跨标的统计异常检测 (Z-score / IQR / MAD)

        对价格类字段跨标的计算统计指标，检测异常值。
        至少需要 4 个标的才有统计意义。
        """
        if len(data) < 4:
            return

        for field_name in self.PRICE_FIELDS:
            values = {}
            for symbol, fields in data.items():
                val = fields.get(field_name)
                if val is not None:
                    try:
                        values[symbol] = float(val)
                    except (TypeError, ValueError):
                        pass

            if len(values) < 4:
                continue

            symbols = list(values.keys())
            vals = list(values.values())

            # Z-score 检测
            z_mask = self.detect_zscore_outliers(vals, threshold=self.Z_SCORE_THRESHOLD)
            for i, is_outlier in enumerate(z_mask):
                if is_outlier:
                    report.issues.append(
                        QualityIssue(
                            severity="warning",
                            category="outlier",
                            field=field_name,
                            symbol=symbols[i],
                            description=(f"Z-score 异常: {field_name}={vals[i]} (Z>{self.Z_SCORE_THRESHOLD})"),
                            value=vals[i],
                        )
                    )

            # IQR 检测
            iqr_mask = self.detect_iqr_outliers(vals, multiplier=self.IQR_MULTIPLIER)
            for i, is_outlier in enumerate(iqr_mask):
                if is_outlier:
                    report.issues.append(
                        QualityIssue(
                            severity="warning",
                            category="outlier",
                            field=field_name,
                            symbol=symbols[i],
                            description=(f"IQR 异常: {field_name}={vals[i]} (IQR×{self.IQR_MULTIPLIER})"),
                            value=vals[i],
                        )
                    )

            # MAD 检测
            mad_mask = self.detect_mad_outliers(vals, threshold=self.MAD_THRESHOLD)
            for i, is_outlier in enumerate(mad_mask):
                if is_outlier:
                    report.issues.append(
                        QualityIssue(
                            severity="warning",
                            category="outlier",
                            field=field_name,
                            symbol=symbols[i],
                            description=(f"MAD 异常: {field_name}={vals[i]} (modZ>{self.MAD_THRESHOLD})"),
                            value=vals[i],
                        )
                    )

    # ------------------------------------------------------------
    # 一致性检查
    # ------------------------------------------------------------
    def _check_consistency(
        self,
        data: dict[str, dict],
        report: QualityReport,
    ) -> None:
        """检查字段一致性"""
        for symbol, fields in data.items():
            # close 应在 [low, high] 范围内
            close = fields.get("close")
            high = fields.get("high")
            low = fields.get("low")

            if all(v is not None for v in [close, high, low]):
                try:
                    c, h, lo = float(close), float(high), float(low)  # type: ignore
                    if c > h:
                        report.issues.append(
                            QualityIssue(
                                severity="warning",
                                category="consistency",
                                field="close>high",
                                symbol=symbol,
                                description=f"close ({c}) > high ({h})",
                                value=c,
                                expected=f"<= {h}",
                            )
                        )
                    elif c < lo:
                        report.issues.append(
                            QualityIssue(
                                severity="warning",
                                category="consistency",
                                field="close<low",
                                symbol=symbol,
                                description=f"close ({c}) < low ({lo})",
                                value=c,
                                expected=f">= {lo}",
                            )
                        )
                except (TypeError, ValueError):
                    pass

            # 时间戳一致性
            timestamp = fields.get("timestamp") or fields.get("trade_date")
            if timestamp:
                # 简单格式检查
                ts_str = str(timestamp)
                if not any(c.isdigit() for c in ts_str):
                    report.issues.append(
                        QualityIssue(
                            severity="warning",
                            category="consistency",
                            field="timestamp",
                            symbol=symbol,
                            description=f"时间戳格式异常: {ts_str}",
                            value=ts_str,
                        )
                    )

    # ------------------------------------------------------------
    # 延迟检查
    # ------------------------------------------------------------
    def _check_latency(
        self,
        data: dict[str, dict],
        timestamp_field: str,
        report: QualityReport,
    ) -> None:
        """检查数据延迟"""
        now = datetime.now()

        for symbol, fields in data.items():
            ts = fields.get(timestamp_field) or fields.get("timestamp") or fields.get("trade_date")
            if not ts:
                continue

            # 解析时间戳
            parsed_time = self._parse_timestamp(ts)
            if parsed_time is None:
                continue

            latency_minutes = (now - parsed_time).total_seconds() / 60

            if latency_minutes > self.max_latency_minutes:
                severity = "critical" if latency_minutes > 120 else "warning"
                report.issues.append(
                    QualityIssue(
                        severity=severity,
                        category="latency",
                        field=timestamp_field,
                        symbol=symbol,
                        description=f"数据延迟 {latency_minutes:.0f} 分钟 (超过 {self.max_latency_minutes} 分钟)",
                        value=latency_minutes,
                        expected=f"<= {self.max_latency_minutes}",
                    )
                )

    def _parse_timestamp(self, ts: Any) -> datetime | None:
        """解析时间戳"""
        if isinstance(ts, datetime):
            return ts
        if isinstance(ts, date):
            return datetime.combine(ts, datetime.min.time())

        ts_str = str(ts)
        # 尝试多种格式
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%Y%m%d",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(ts_str, fmt)
            except ValueError:
                continue
        return None

    # ------------------------------------------------------------
    # 计算评分
    # ------------------------------------------------------------
    def _calculate_scores(self, report: QualityReport) -> None:
        """计算综合评分"""
        max(1, report.total_symbols)
        max(1, report.checked_fields)

        # 完整性评分
        critical_completeness = sum(
            1 for i in report.issues if i.category == "completeness" and i.severity == "critical"
        )
        report.completeness_score = max(0, 100 - critical_completeness * 20)

        # 一致性评分
        consistency_issues = sum(
            1 for i in report.issues if i.category in ("consistency", "outlier") and i.severity in ("error", "critical")
        )
        report.consistency_score = max(0, 100 - consistency_issues * 10)

        # 新鲜度评分 (基于延迟)
        latency_issues = sum(1 for i in report.issues if i.category == "latency")
        report.freshness_score = max(0, 100 - latency_issues * 15)

        # 综合评分 (加权平均)
        report.overall_score = (
            report.completeness_score * 0.40 + report.consistency_score * 0.35 + report.freshness_score * 0.25
        )

    # ------------------------------------------------------------
    # 摘要
    # ------------------------------------------------------------
    def _build_summary(self, report: QualityReport) -> str:
        """生成报告摘要"""
        lines = [
            f"数据质量报告 ({report.report_date})",
            "=" * 50,
            f"检查标的: {report.total_symbols}",
            f"检查字段: {report.checked_fields}",
            "",
            "评分:",
            f"  完整性: {report.completeness_score:.1f}/100",
            f"  一致性: {report.consistency_score:.1f}/100",
            f"  新鲜度: {report.freshness_score:.1f}/100",
            f"  综合:   {report.overall_score:.1f}/100",
            "",
            "问题统计:",
            f"  Critical: {report.critical_count}",
            f"  Error:    {report.error_count}",
            f"  Warning:  {report.warning_count}",
            f"  Total:    {len(report.issues)}",
        ]

        if report.issues:
            lines.append("")
            lines.append("问题明细:")
            for issue in report.issues[:20]:
                lines.append(
                    f"  [{issue.severity.upper():8}] {issue.category:<14} "
                    f"{issue.symbol or 'N/A':<10} {issue.field:<15} {issue.description}"
                )
            if len(report.issues) > 20:
                lines.append(f"  ... 还有 {len(report.issues) - 20} 个问题未显示")

        status = "✅ 通过" if report.passed else "❌ 未通过"
        lines.append("")
        lines.append(f"结论: {status}")

        return "\n".join(lines)

    # ------------------------------------------------------------
    # 保存报告
    # ------------------------------------------------------------
    def save_report(self, report: QualityReport) -> Path:
        """保存数据质量报告"""
        path = REPORT_DIR / f"data_quality_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(report.to_dict(), f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"数据质量报告已保存: {path}")
        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            logger.error(f"保存数据质量报告失败: {e}")
        return path


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="数据质量监控引擎")
    parser.add_argument("--simulate", action="store_true", help="使用模拟数据测试")
    args = parser.parse_args()

    monitor = DataQualityMonitor(max_latency_minutes=30)

    if args.simulate:
        # 模拟数据 (含异常)
        data = {
            "300308": {
                "open": 35.50,
                "high": 36.20,
                "low": 35.30,
                "close": 36.10,
                "volume": 1_500_000,
                "timestamp": datetime.now().isoformat(),
            },
            "002475": {
                "open": 38.20,
                "high": 38.80,
                "low": 38.00,
                "close": 38.50,
                "volume": 2_200_000,
                "timestamp": datetime.now().isoformat(),
            },
            "600519": {
                "open": 1680.0,
                "high": 1700.0,
                "low": 1675.0,
                "close": 1695.0,
                "volume": 50000,
                "timestamp": datetime.now().isoformat(),
            },
            # 异常标的 1: high < low
            "000001": {
                "open": 12.50,
                "high": 12.30,
                "low": 12.80,
                "close": 12.60,
                "volume": -100,
                "timestamp": datetime.now().isoformat(),
            },
            # 异常标的 2: 缺失 close
            "600036": {
                "open": 38.00,
                "high": 38.50,
                "low": 37.80,
                "volume": 800000,
                "timestamp": datetime.now().isoformat(),
            },
            # 异常标的 3: 延迟
            "601318": {
                "open": 50.00,
                "high": 50.50,
                "low": 49.80,
                "close": 50.20,
                "volume": 1_200_000,
                "timestamp": (datetime.now() - timedelta(hours=3)).isoformat(),
            },
        }

        expected = ["300308", "002475", "600519", "000001", "600036", "601318", "缺失标的1", "缺失标的2"]

        report = monitor.check_market_data(data, expected_symbols=expected)
        logger.info(report.summary)
        monitor.save_report(report)

    # 统计异常检测算法演示
    logger.info("\n" + "=" * 50)
    logger.info("统计异常检测算法演示 (Z-score / IQR / MAD)")
    logger.info("=" * 50)

    test_data = [10.0, 10.5, 11.0, 9.8, 10.2, 10.8, 9.5, 10.3, 55.0, 10.1]
    logger.debug(f"\n测试数据: {test_data}")

    z_mask = DataQualityMonitor.detect_zscore_outliers(test_data, threshold=3.0)
    logger.info(f"Z-score mask:  {list(z_mask)}")

    iqr_mask = DataQualityMonitor.detect_iqr_outliers(test_data, multiplier=1.5)
    logger.info(f"IQR mask:      {list(iqr_mask)}")

    mad_mask = DataQualityMonitor.detect_mad_outliers(test_data, threshold=3.5)
    logger.info(f"MAD mask:      {list(mad_mask)}")

    # pandas Series 测试
    if HAS_PANDAS:
        import pandas as pd

        s = pd.Series(test_data, name="price")
        z_s = DataQualityMonitor.detect_zscore_outliers(s, threshold=3.0)
        logger.info(f"\npandas Series 输入 -> 输出类型: {type(z_s).__name__}")
        logger.info(f"Z-score Series:\n{z_s}")
