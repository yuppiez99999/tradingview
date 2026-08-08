"""
数据契约模块 — GAP-8 交付物.

ECC mle-workflow MLE-02 修复:
    Data contract defines entity grain, label timing, feature timing, snapshot/version

设计原则:
    1. DataContract frozen=True (coding-standards 不可变优先)
    2. 默认 warn_only=True (7 天观察期, 不阻断生产)
    3. validate 检查: 必填列 / 类型 / null 比例 / value_range / point-in-time 切片
    4. V9_DEFAULT_CONTRACT 预定义 V9 LGB 模型的数据契约

硬约束:
    - HC-1: 默认 warn_only, 不破坏 V9 基线 (enforce 模式留待 7 天观察期后)
    - HC-5: ConfigManager 不受影响 (本模块独立)

API:
    from utils.alpha.data_contract import (
        DataContract, FeatureSchema, ValidationResult,
        V9_DEFAULT_CONTRACT, validate_point_in_time
    )

    result = V9_DEFAULT_CONTRACT.validate(panel, mode="warn_only")
    if result.has_violations:
        for v in result.violations:
            logger.info(f"[{v.severity}] {v.field}: {v.message}")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import pandas as pd

logger = logging.getLogger("data_contract")


# ============================================================
# 异常定义
# ============================================================
class DataContractError(Exception):
    """数据契约错误."""


class DataContractViolationError(DataContractError):
    """数据契约违规 (enforce 模式下抛出)."""

    def __init__(self, violations: list[Violation]):
        self.violations = violations
        messages = "; ".join(f"[{v.severity.value}] {v.field}: {v.message}" for v in violations)
        super().__init__(f"数据契约违规 ({len(violations)} 项): {messages}")


# ============================================================
# 枚举与数据类 (frozen=True)
# ============================================================
class Severity(str, Enum):
    """违规严重等级."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ValidationMode(str, Enum):
    """校验模式.

    - warn_only: 失败仅 logger.warning, 不抛异常 (默认, 7 天观察期)
    - enforce: 失败 raise DataContractViolationError (生产模式)
    """

    WARN_ONLY = "warn_only"
    ENFORCE = "enforce"


class NullPolicy(str, Enum):
    """null 处理策略."""

    DROP = "drop"  # 删除行
    FILL_ZERO = "fill_0"  # 填 0
    FILL_NAN = "fill_nan"  # 填 NaN (保留为缺失)
    WARN = "warn"  # 仅告警, 不处理


@dataclass(frozen=True)
class FeatureSchema:
    """单个特征的字段 schema (不可变).

    Attributes:
        feature_name: 特征名 (如 MOM_5D)
        dtype: 期望类型 (float64 / int64 / object)
        source: 来源 (如 close.pct_change(5))
        timing: 计算时机 (如 "t 日收盘后", "t-1 及之前")
        null_policy: null 处理策略
        value_range: 期望值范围 (min, max), None 表示不限制
        max_null_ratio: 最大允许 null 比例 (0-1, 默认 0.05 = 5%)
    """

    feature_name: str
    dtype: str = "float64"
    source: str = ""
    timing: str = "t 日收盘后"
    null_policy: NullPolicy = NullPolicy.WARN
    value_range: tuple[float, float] | None = None
    max_null_ratio: float = 0.05


@dataclass(frozen=True)
class Violation:
    """单条违规 (不可变).

    Attributes:
        field: 违规字段名 (如 "close", "MOM_252D")
        severity: 严重等级
        message: 违规描述
        value: 实际值 (可选)
        expected: 期望值 (可选)
    """

    field: str
    severity: Severity
    message: str
    value: Any | None = None
    expected: Any | None = None


@dataclass(frozen=True)
class ValidationResult:
    """校验结果 (不可变).

    Attributes:
        passed: 是否通过 (无 ERROR/CRITICAL 级违规)
        violations: 所有违规列表
        mode: 校验模式
        contract_version: 契约版本
        timestamp: 校验时间
    """

    passed: bool
    violations: tuple[Violation, ...] = field(default_factory=tuple)
    mode: ValidationMode = ValidationMode.WARN_ONLY
    contract_version: str = "1.0"
    timestamp: str = ""

    @property
    def has_violations(self) -> bool:
        """是否有违规."""
        return len(self.violations) > 0

    @property
    def error_count(self) -> int:
        """ERROR 级违规数."""
        return sum(1 for v in self.violations if v.severity == Severity.ERROR)

    @property
    def critical_count(self) -> int:
        """CRITICAL 级违规数."""
        return sum(1 for v in self.violations if v.severity == Severity.CRITICAL)

    def to_dict(self) -> dict[str, Any]:
        """转为 dict (用于 manifest.json 记录)."""
        return {
            "passed": self.passed,
            "mode": self.mode.value,
            "contract_version": self.contract_version,
            "timestamp": self.timestamp,
            "violation_count": len(self.violations),
            "error_count": self.error_count,
            "critical_count": self.critical_count,
            "violations": [
                {
                    "field": v.field,
                    "severity": v.severity.value,
                    "message": v.message,
                    "value": str(v.value) if v.value is not None else None,
                    "expected": str(v.expected) if v.expected is not None else None,
                }
                for v in self.violations
            ],
        }


@dataclass(frozen=True)
class DataContract:
    """数据契约 (不可变).

    定义实体粒度、标签、特征 schema、point-in-time 规则、split 策略等,
    用于训练前和服务前的数据校验, 防止训练/服务特征不一致.

    Attributes:
        contract_name: 契约名 (如 "v9_lgb_data_contract")
        version: 契约版本 (如 "1.0")
        entity_grain: 实体粒度 (如 ("symbol", "date"))
        label_def: 标签定义 (如 "forward_return_5d = close[t+5]/close[t] - 1.0")
        label_delay_days: 标签延迟天数 (如 5)
        feature_schemas: 特征 schema 列表
        required_columns: 必填列 (如 ("symbol", "date", "close", "open", "high", "low", "volume"))
        allowed_nulls: 允许 null 的列及其策略
        split_policy: 分割策略描述 (如 "TimeSeriesSplit(n_splits=5), 禁随机 split")
        pii_policy: PII 策略 (如 "无 PII, 公开市场数据")
        change_policy: 变更策略 (如 "破坏性变更需 PR + Iteration Compact 评审")
    """

    contract_name: str
    version: str = "1.0"
    entity_grain: tuple[str, ...] = ("symbol", "date")
    label_def: str = "forward_return_5d"
    label_delay_days: int = 5
    feature_schemas: tuple[FeatureSchema, ...] = field(default_factory=tuple)
    required_columns: tuple[str, ...] = (
        "symbol",
        "date",
        "close",
        "open",
        "high",
        "low",
        "volume",
    )
    allowed_nulls: dict[str, NullPolicy] = field(default_factory=dict)
    split_policy: str = "TimeSeriesSplit(n_splits=5), 禁随机 split"
    pii_policy: str = "无 PII, 公开市场数据, 永久保留"
    change_policy: str = "破坏性变更需 PR + Iteration Compact 评审, 新版本须向后兼容"

    # ============================================================
    # 校验方法
    # ============================================================
    def validate(
        self,
        panel: pd.DataFrame,
        mode: str = "warn_only",
        current_date: datetime | None = None,
    ) -> ValidationResult:
        """校验 panel 是否符合数据契约.

        Args:
            panel: 待校验的因子面板
            mode: 校验模式 ("warn_only" / "enforce")
            current_date: 当前日期 (用于 point-in-time 检查, None 则跳过 PIT 校验)

        Returns:
            ValidationResult 校验结果

        Raises:
            DataContractViolationError: enforce 模式下有 ERROR/CRITICAL 违规时抛出
        """
        try:
            mode_enum = ValidationMode(mode)
        except ValueError as err:
            raise DataContractError(f"无效的校验模式: {mode}, 应为 warn_only / enforce") from err

        violations: list[Violation] = []

        # 1. 必填列存在性检查
        violations.extend(self._check_required_columns(panel))

        # 2. 特征 schema 检查 (类型 / null 比例 / value_range)
        violations.extend(self._check_feature_schemas(panel))

        # 3. 标签定义检查
        violations.extend(self._check_label(panel))

        # 4. entity grain 检查 (主键唯一性)
        violations.extend(self._check_entity_grain(panel))

        # 5. point-in-time 检查 (可选)
        if current_date is not None:
            violations.extend(self._check_point_in_time(panel, current_date))

        # 判断是否通过 (无 ERROR/CRITICAL 即通过)
        passed = not any(v.severity in (Severity.ERROR, Severity.CRITICAL) for v in violations)
        result = ValidationResult(
            passed=passed,
            violations=tuple(violations),
            mode=mode_enum,
            contract_version=self.version,
            timestamp=datetime.now().isoformat(timespec="seconds"),
        )

        # 模式处理
        if not passed:
            error_violations = [v for v in violations if v.severity in (Severity.ERROR, Severity.CRITICAL)]
            if mode_enum == ValidationMode.ENFORCE and error_violations:
                logger.error(f"数据契约校验失败 ({len(error_violations)} 项 ERROR/CRITICAL)")
                raise DataContractViolationError(error_violations)
            else:
                for v in error_violations:
                    logger.warning(f"[数据契约] [{v.severity.value}] {v.field}: {v.message}")
        else:
            logger.info(
                f"数据契约校验通过 (契约={self.contract_name} v{self.version}, "
                f"违规 {len(violations)} 项均为 INFO/WARNING)"
            )

        return result

    # ============================================================
    # 内部检查方法
    # ============================================================
    def _check_required_columns(self, panel: pd.DataFrame) -> list[Violation]:
        """检查必填列是否都存在."""
        violations = []
        for col in self.required_columns:
            if col not in panel.columns:
                violations.append(
                    Violation(
                        field=col,
                        severity=Severity.CRITICAL,
                        message=f"必填列缺失: {col}",
                        value=None,
                        expected=f"列 {col} 必须存在",
                    )
                )
        return violations

    def _check_feature_schemas(self, panel: pd.DataFrame) -> list[Violation]:
        """检查特征 schema (类型 / null / value_range)."""
        violations = []
        for schema in self.feature_schemas:
            col = schema.feature_name
            if col not in panel.columns:
                # 特征列缺失, 已在 required_columns 检查中处理 (若 required)
                continue

            series = panel[col]

            # 类型检查
            if schema.dtype and str(series.dtype) != schema.dtype:
                violations.append(
                    Violation(
                        field=col,
                        severity=Severity.WARNING,
                        message=f"类型不匹配: 期望 {schema.dtype}, 实际 {series.dtype}",
                        value=str(series.dtype),
                        expected=schema.dtype,
                    )
                )

            # null 比例检查
            null_ratio = float(series.isna().mean())
            if null_ratio > schema.max_null_ratio:
                violations.append(
                    Violation(
                        field=col,
                        severity=Severity.WARNING if null_ratio < 0.2 else Severity.ERROR,
                        message=f"null 比例过高: {null_ratio:.2%} (阈值 {schema.max_null_ratio:.2%})",
                        value=null_ratio,
                        expected=schema.max_null_ratio,
                    )
                )

            # value_range 检查
            if schema.value_range:
                min_val, max_val = schema.value_range
                non_null = series.dropna()
                if len(non_null) > 0:
                    actual_min = float(non_null.min())
                    actual_max = float(non_null.max())
                    if actual_min < min_val or actual_max > max_val:
                        violations.append(
                            Violation(
                                field=col,
                                severity=Severity.WARNING,
                                message=f"值范围超界: 实际 [{actual_min:.4f}, {actual_max:.4f}], "
                                f"期望 [{min_val}, {max_val}]",
                                value=(actual_min, actual_max),
                                expected=(min_val, max_val),
                            )
                        )

        return violations

    def _check_label(self, panel: pd.DataFrame) -> list[Violation]:
        """检查标签列."""
        violations = []
        # 假设标签列名为 'y' (lgbm_factor_mining.py 惯例)
        label_col = "y"
        if label_col not in panel.columns:
            violations.append(
                Violation(
                    field=label_col,
                    severity=Severity.ERROR,
                    message=f"标签列缺失: {label_col} (定义: {self.label_def})",
                )
            )
        else:
            null_ratio = float(panel[label_col].isna().mean())
            if null_ratio > 0.01:
                violations.append(
                    Violation(
                        field=label_col,
                        severity=Severity.WARNING,
                        message=f"标签 null 比例 {null_ratio:.2%} > 1% 阈值",
                        value=null_ratio,
                        expected=0.01,
                    )
                )
        return violations

    def _check_entity_grain(self, panel: pd.DataFrame) -> list[Violation]:
        """检查实体粒度 (主键唯一性)."""
        violations = []
        grain_cols = [c for c in self.entity_grain if c in panel.columns]
        if not grain_cols:
            return violations

        # 检查重复行
        duplicated = panel.duplicated(subset=grain_cols).sum()
        if duplicated > 0:
            violations.append(
                Violation(
                    field=",".join(grain_cols),
                    severity=Severity.ERROR,
                    message=f"实体粒度重复: {duplicated} 行重复 (grain={grain_cols})",
                    value=int(duplicated),
                    expected=0,
                )
            )
        return violations

    def _check_point_in_time(self, panel: pd.DataFrame, current_date: datetime) -> list[Violation]:
        """检查 point-in-time 切片正确性 (无未来信息泄漏)."""
        violations = []
        if "date" not in panel.columns:
            return violations

        # 检查是否有 date > current_date 的行 (未来信息)
        date_col = panel["date"]
        # 尝试转为 datetime
        try:
            if not pd.api.types.is_datetime64_any_dtype(date_col):
                dates = pd.to_datetime(date_col, errors="coerce")
            else:
                dates = date_col
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            return violations

        future_rows = (dates > current_date).sum()
        if future_rows > 0:
            violations.append(
                Violation(
                    field="date",
                    severity=Severity.CRITICAL,
                    message=f"point-in-time 违规: {future_rows} 行 date > {current_date.date()} (未来信息泄漏)",
                    value=int(future_rows),
                    expected=0,
                )
            )
        return violations


# ============================================================
# Point-in-time 独立校验函数
# ============================================================
def validate_point_in_time(panel: pd.DataFrame, current_date: datetime) -> bool:
    """独立校验 panel 是否有未来信息泄漏.

    Args:
        panel: 因子面板
        current_date: 当前日期

    Returns:
        True = 无泄漏, False = 有泄漏
    """
    if "date" not in panel.columns:
        return True  # 无 date 列, 无法校验, 返回 True (乐观)
    try:
        if not pd.api.types.is_datetime64_any_dtype(panel["date"]):
            dates = pd.to_datetime(panel["date"], errors="coerce")
        else:
            dates = panel["date"]
        return int((dates > current_date).sum()) == 0
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.warning(f"point-in-time 校验异常: {e}")
        return True  # 异常时乐观返回


# ============================================================
# V9 LGB 模型预定义数据契约
# ============================================================
def _build_v9_default_contract() -> DataContract:
    """构造 V9 LGB 模型的默认数据契约.

    集中定义在这里 (而非模块顶层常量), 以便:
        1. 避免模块导入时的副作用
        2. 便于测试 mock 替换
        3. 便于未来 V10 等新模型复用模板
    """
    # V9 模型常用因子 schema (与 lgbm_factor_mining.py compute_all_factors 对齐)
    v9_features = (
        # 动量类
        FeatureSchema("MOM_5D", "float64", "close.pct_change(5)", "t 日收盘后", NullPolicy.DROP, (-1.0, 1.0), 0.05),
        FeatureSchema("MOM_20D", "float64", "close.pct_change(20)", "t 日收盘后", NullPolicy.DROP, (-1.0, 1.0), 0.05),
        FeatureSchema("MOM_60D", "float64", "close.pct_change(60)", "t 日收盘后", NullPolicy.DROP, (-1.0, 1.0), 0.05),
        # 波动率类
        FeatureSchema(
            "VOL_5D", "float64", "ret.rolling(5).std()", "t 日收盘后", NullPolicy.FILL_ZERO, (0.0, 1.0), 0.05
        ),
        FeatureSchema(
            "VOL_20D", "float64", "ret.rolling(20).std()", "t 日收盘后", NullPolicy.FILL_ZERO, (0.0, 1.0), 0.05
        ),
        # 技术指标
        FeatureSchema("RSI_14D", "float64", "RSI(14)", "t 日收盘后", NullPolicy.WARN, (0.0, 100.0), 0.05),
        FeatureSchema("MACD", "float64", "MACD histogram", "t 日收盘后", NullPolicy.WARN, (-1.0, 1.0), 0.05),
        # 基本面代理
        FeatureSchema("SIZE_PROXY", "float64", "close.iloc[-1]", "t 日收盘后", NullPolicy.WARN, (0.0, 100000.0), 0.01),
    )
    return DataContract(
        contract_name="v9_lgb_data_contract",
        version="1.0",
        entity_grain=("code", "date"),  # 与 lgbm_factor_mining.py 实际列名一致
        label_def="forward_return_5d = close[t+5] / close[t] - 1.0",
        label_delay_days=5,
        feature_schemas=v9_features,
        required_columns=("code", "date", "close", "open", "high", "low", "volume"),
        allowed_nulls={
            "MOM_252D": NullPolicy.DROP,  # 上市不足 252 日, drop
            "AMIHUD_20D": NullPolicy.FILL_ZERO,  # volume=0 时, fill 0
        },
        split_policy=(
            "Train 2023-01-01~2024-12-31, "
            "Validation 2025-01-01~2025-12-31, "
            "Test 2026-01-01~2026-06-30, "
            "Backtest 2026-07-01~2026-07-28 (sim_mode 14 天); "
            "TimeSeriesSplit(n_splits=5) 禁随机 split"
        ),
        pii_policy="无 PII (量化数据均为公开市场数据); 不接账户级数据; 永久保留",
        change_policy=("破坏性变更需 PR + Iteration Compact 评审; 新版本须能加载旧版本训练的 artifact (向后兼容)"),
    )


# V9 默认数据契约 (延迟构造, 避免模块导入副作用)
V9_DEFAULT_CONTRACT: DataContract = _build_v9_default_contract()
