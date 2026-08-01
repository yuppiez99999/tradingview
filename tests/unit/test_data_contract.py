# -*- coding: utf-8 -*-
"""ECC GAP-8: 数据契约测试.

测试覆盖:
    1. DataContract 不可变性 (frozen=True)
    2. 必填列检查 (缺失 → CRITICAL)
    3. 特征 schema 检查 (类型 / null 比例 / value_range)
    4. 标签检查 (缺失 → ERROR, null 比例)
    5. 实体粒度检查 (重复行 → ERROR)
    6. point-in-time 检查 (未来数据 → CRITICAL)
    7. warn_only 模式 (不抛异常, 仅日志)
    8. enforce 模式 (抛 DataContractViolationError)
    9. V9_DEFAULT_CONTRACT 校验干净 panel 通过
    10. ValidationResult 序列化为 dict
"""
import json
import sys
from dataclasses import FrozenInstanceError
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# 确保项目根目录在 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.data_contract import (  # noqa: E402
    DataContractError,
    DataContractViolationError,
    FeatureSchema,
    Severity,
    ValidationMode,
    Violation,
    V9_DEFAULT_CONTRACT,
    validate_point_in_time,
)


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def clean_panel() -> pd.DataFrame:
    """干净 panel (符合 V9 契约, 无违规)."""
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=20, freq="B")
    codes = ["000001", "000002", "000003"]
    rows = []
    for code in codes:
        for date in dates:
            close = float(np.random.uniform(10, 50))
            rows.append({
                "code": code,
                "date": date.strftime("%Y-%m-%d"),
                "open": close * 0.99,
                "high": close * 1.02,
                "low": close * 0.98,
                "close": close,
                "volume": float(np.random.randint(100000, 1000000)),
                "y": float(np.random.randn() * 0.02),
                "MOM_5D": float(np.random.uniform(-0.1, 0.1)),
                "MOM_20D": float(np.random.uniform(-0.2, 0.2)),
                "VOL_5D": float(np.random.uniform(0.01, 0.05)),
                "VOL_20D": float(np.random.uniform(0.01, 0.05)),
                "RSI_14D": float(np.random.uniform(20, 80)),
                "MACD": float(np.random.uniform(-0.01, 0.01)),
                "SIZE_PROXY": close,
            })
    return pd.DataFrame(rows)


@pytest.fixture
def panel_missing_required_column(clean_panel: pd.DataFrame) -> pd.DataFrame:
    """缺失必填列 (close) 的 panel."""
    return clean_panel.drop(columns=["close"])


@pytest.fixture
def panel_with_nulls(clean_panel: pd.DataFrame) -> pd.DataFrame:
    """含大量 null 的 panel (MOM_5D null 比例 > 5%)."""
    panel = clean_panel.copy()
    # 设置 30% 的 MOM_5D 为 NaN
    n_null = int(len(panel) * 0.3)
    panel.loc[:n_null, "MOM_5D"] = np.nan
    return panel


@pytest.fixture
def panel_with_future_data(clean_panel: pd.DataFrame) -> pd.DataFrame:
    """含未来日期的 panel (point-in-time 违规)."""
    panel = clean_panel.copy()
    # 把最后 5 行的日期改为未来
    future_dates = pd.date_range("2025-12-01", periods=5, freq="B")
    panel.loc[len(panel)-5:, "date"] = future_dates.strftime("%Y-%m-%d")
    return panel


@pytest.fixture
def panel_with_duplicates(clean_panel: pd.DataFrame) -> pd.DataFrame:
    """含重复行的 panel (entity grain 违规)."""
    panel = clean_panel.copy()
    # 复制第一行
    first_row = panel.iloc[[0]].copy()
    return pd.concat([panel, first_row], ignore_index=True)


# ============================================================
# 测试组 1: DataContract 不可变性
# ============================================================
class TestDataContractImmutability:
    """ECC coding-standards: 不可变优先 (frozen=True)."""

    def test_frozen_dataclass_blocks_attribute_assignment(self):
        """frozen=True 时直接赋值应抛 FrozenInstanceError."""
        with pytest.raises(FrozenInstanceError):
            V9_DEFAULT_CONTRACT.version = "2.0"  # type: ignore[misc]

    def test_frozen_feature_schema_blocks_assignment(self):
        """FeatureSchema frozen=True."""
        schema = FeatureSchema("MOM_5D")
        with pytest.raises(FrozenInstanceError):
            schema.feature_name = "MOM_10D"  # type: ignore[misc]

    def test_frozen_violation_blocks_assignment(self):
        """Violation frozen=True."""
        v = Violation(field="test", severity=Severity.WARNING, message="test")
        with pytest.raises(FrozenInstanceError):
            v.field = "other"  # type: ignore[misc]

    def test_replace_returns_new_instance(self):
        """dataclasses.replace 返回新实例."""
        from dataclasses import replace
        v2 = replace(V9_DEFAULT_CONTRACT, version="2.0")
        assert V9_DEFAULT_CONTRACT.version == "1.0"
        assert v2.version == "2.0"


# ============================================================
# 测试组 2: 必填列检查
# ============================================================
class TestRequiredColumnsCheck:
    """ECC GAP-8: 必填列存在性."""

    def test_clean_panel_passes_required_columns(self, clean_panel: pd.DataFrame):
        """干净 panel 必填列检查通过."""
        result = V9_DEFAULT_CONTRACT.validate(clean_panel, mode="warn_only")
        # 无 CRITICAL 级必填列缺失违规
        critical_violations = [
            v for v in result.violations
            if v.severity == Severity.CRITICAL and "必填列缺失" in v.message
        ]
        assert len(critical_violations) == 0

    def test_missing_required_column_yields_critical(
        self, panel_missing_required_column: pd.DataFrame
    ):
        """缺失 close 列 → CRITICAL 违规."""
        result = V9_DEFAULT_CONTRACT.validate(panel_missing_required_column, mode="warn_only")
        close_violations = [
            v for v in result.violations
            if v.field == "close" and v.severity == Severity.CRITICAL
        ]
        assert len(close_violations) > 0
        assert result.passed is False

    def test_missing_required_column_enforce_raises(
        self, panel_missing_required_column: pd.DataFrame
    ):
        """enforce 模式下缺失必填列 → 抛 DataContractViolationError."""
        with pytest.raises(DataContractViolationError) as exc_info:
            V9_DEFAULT_CONTRACT.validate(panel_missing_required_column, mode="enforce")
        assert "close" in str(exc_info.value)


# ============================================================
# 测试组 3: 特征 schema 检查
# ============================================================
class TestFeatureSchemaCheck:
    """ECC GAP-8: 类型 / null / value_range."""

    def test_null_ratio_warning(self, panel_with_nulls: pd.DataFrame):
        """null 比例 > 5% → 违规 (WARNING if <20%, ERROR if >=20%).

        panel_with_nulls fixture 设置 30% null, 触发 ERROR 级.
        """
        result = V9_DEFAULT_CONTRACT.validate(panel_with_nulls, mode="warn_only")
        null_violations = [
            v for v in result.violations
            if v.field == "MOM_5D" and "null 比例过高" in v.message
        ]
        assert len(null_violations) > 0
        # 30% null > 20% 阈值, 触发 ERROR (非 WARNING)
        assert null_violations[0].severity == Severity.ERROR

    def test_low_null_ratio_yields_warning(self, clean_panel: pd.DataFrame):
        """null 比例 5-20% → WARNING (非 ERROR)."""
        panel = clean_panel.copy()
        # 设置 10% 的 MOM_5D 为 NaN (5% < 10% < 20%)
        n_null = int(len(panel) * 0.1)
        panel.loc[:n_null, "MOM_5D"] = np.nan
        result = V9_DEFAULT_CONTRACT.validate(panel, mode="warn_only")
        null_violations = [
            v for v in result.violations
            if v.field == "MOM_5D" and "null 比例过高" in v.message
        ]
        assert len(null_violations) > 0
        assert null_violations[0].severity == Severity.WARNING

    def test_value_range_violation_detected(self, clean_panel: pd.DataFrame):
        """value_range 超界 → WARNING."""
        panel = clean_panel.copy()
        panel.loc[0, "RSI_14D"] = 150.0  # 超出 (0, 100) 范围
        result = V9_DEFAULT_CONTRACT.validate(panel, mode="warn_only")
        range_violations = [
            v for v in result.violations
            if v.field == "RSI_14D" and "值范围超界" in v.message
        ]
        assert len(range_violations) > 0


# ============================================================
# 测试组 4: 标签检查
# ============================================================
class TestLabelCheck:
    """ECC GAP-8: 标签列存在性 + null 比例."""

    def test_missing_label_yields_error(self, clean_panel: pd.DataFrame):
        """标签列 y 缺失 → ERROR."""
        panel = clean_panel.drop(columns=["y"])
        result = V9_DEFAULT_CONTRACT.validate(panel, mode="warn_only")
        label_violations = [
            v for v in result.violations
            if v.field == "y" and "标签列缺失" in v.message
        ]
        assert len(label_violations) > 0
        assert label_violations[0].severity == Severity.ERROR


# ============================================================
# 测试组 5: 实体粒度检查
# ============================================================
class TestEntityGrainCheck:
    """ECC GAP-8: 主键唯一性."""

    def test_duplicate_rows_yields_error(self, panel_with_duplicates: pd.DataFrame):
        """重复行 → ERROR."""
        result = V9_DEFAULT_CONTRACT.validate(panel_with_duplicates, mode="warn_only")
        dup_violations = [
            v for v in result.violations
            if "实体粒度重复" in v.message
        ]
        assert len(dup_violations) > 0
        assert dup_violations[0].severity == Severity.ERROR


# ============================================================
# 测试组 6: point-in-time 检查
# ============================================================
class TestPointInTimeCheck:
    """ECC GAP-8: 未来信息泄漏检测."""

    def test_future_data_yields_critical(self, panel_with_future_data: pd.DataFrame):
        """含未来日期 → CRITICAL 违规."""
        current_date = datetime(2024, 6, 30)
        result = V9_DEFAULT_CONTRACT.validate(
            panel_with_future_data, mode="warn_only", current_date=current_date
        )
        pit_violations = [
            v for v in result.violations
            if v.field == "date" and "point-in-time 违规" in v.message
        ]
        assert len(pit_violations) > 0
        assert pit_violations[0].severity == Severity.CRITICAL

    def test_no_future_data_passes(self, clean_panel: pd.DataFrame):
        """无未来数据 → 无 PIT 违规."""
        current_date = datetime(2025, 12, 31)
        result = V9_DEFAULT_CONTRACT.validate(
            clean_panel, mode="warn_only", current_date=current_date
        )
        pit_violations = [
            v for v in result.violations
            if "point-in-time 违规" in v.message
        ]
        assert len(pit_violations) == 0

    def test_validate_point_in_time_standalone_function(self, clean_panel: pd.DataFrame):
        """validate_point_in_time 独立函数."""
        # 无未来数据
        assert validate_point_in_time(clean_panel, datetime(2025, 12, 31)) is True
        # 有未来数据
        assert validate_point_in_time(clean_panel, datetime(2020, 1, 1)) is False


# ============================================================
# 测试组 7: warn_only vs enforce 模式
# ============================================================
class TestValidationModes:
    """ECC GAP-8: 校验模式."""

    def test_warn_only_does_not_raise(self, panel_missing_required_column: pd.DataFrame):
        """warn_only 模式不抛异常, 仅返回 failed=True."""
        result = V9_DEFAULT_CONTRACT.validate(panel_missing_required_column, mode="warn_only")
        assert result.passed is False
        assert result.mode == ValidationMode.WARN_ONLY

    def test_enforce_raises_on_critical(self, panel_missing_required_column: pd.DataFrame):
        """enforce 模式有 CRITICAL → 抛 DataContractViolationError."""
        with pytest.raises(DataContractViolationError):
            V9_DEFAULT_CONTRACT.validate(panel_missing_required_column, mode="enforce")

    def test_enforce_passes_clean_panel(self, clean_panel: pd.DataFrame):
        """enforce 模式干净 panel 通过 (不抛)."""
        result = V9_DEFAULT_CONTRACT.validate(clean_panel, mode="enforce")
        # 可能有 WARNING 但无 ERROR/CRITICAL
        assert result.passed is True

    def test_invalid_mode_raises_error(self, clean_panel: pd.DataFrame):
        """无效 mode → DataContractError."""
        with pytest.raises(DataContractError):
            V9_DEFAULT_CONTRACT.validate(clean_panel, mode="invalid_mode")


# ============================================================
# 测试组 8: V9_DEFAULT_CONTRACT 实例
# ============================================================
class TestV9DefaultContract:
    """ECC GAP-8: V9 默认契约实例."""

    def test_v9_contract_name_and_version(self):
        """V9 契约 name/version 正确."""
        assert V9_DEFAULT_CONTRACT.contract_name == "v9_lgb_data_contract"
        assert V9_DEFAULT_CONTRACT.version == "1.0"

    def test_v9_contract_entity_grain(self):
        """V9 契约 entity_grain 为 (code, date)."""
        assert V9_DEFAULT_CONTRACT.entity_grain == ("code", "date")

    def test_v9_contract_label_def(self):
        """V9 契约 label_def 含 forward_return_5d."""
        assert "forward_return_5d" in V9_DEFAULT_CONTRACT.label_def
        assert V9_DEFAULT_CONTRACT.label_delay_days == 5

    def test_v9_contract_has_feature_schemas(self):
        """V9 契约有 feature_schemas."""
        assert len(V9_DEFAULT_CONTRACT.feature_schemas) > 0
        feature_names = [s.feature_name for s in V9_DEFAULT_CONTRACT.feature_schemas]
        assert "MOM_5D" in feature_names
        assert "VOL_20D" in feature_names

    def test_v9_contract_required_columns(self):
        """V9 契约 required_columns 含 code/date/close/open/high/low/volume."""
        required = set(V9_DEFAULT_CONTRACT.required_columns)
        assert {"code", "date", "close", "open", "high", "low", "volume"}.issubset(required)

    def test_v9_contract_validates_clean_panel(self, clean_panel: pd.DataFrame):
        """V9 契约校验干净 panel 通过 (passed=True)."""
        result = V9_DEFAULT_CONTRACT.validate(clean_panel, mode="warn_only")
        assert result.passed is True, (
            f"干净 panel 应通过 V9 契约校验, 但有违规: "
            f"{[v.field + ':' + v.message for v in result.violations if v.severity in (Severity.ERROR, Severity.CRITICAL)]}"
        )


# ============================================================
# 测试组 9: ValidationResult 序列化
# ============================================================
class TestValidationResultSerialization:
    """ECC GAP-8: 校验结果序列化 (供 manifest.json 记录)."""

    def test_to_dict_contains_required_fields(self, clean_panel: pd.DataFrame):
        """to_dict() 包含 passed/mode/violations 等字段."""
        result = V9_DEFAULT_CONTRACT.validate(clean_panel, mode="warn_only")
        d = result.to_dict()
        assert "passed" in d
        assert "mode" in d
        assert "contract_version" in d
        assert "timestamp" in d
        assert "violation_count" in d
        assert "violations" in d

    def test_to_dict_serializable_to_json(self, clean_panel: pd.DataFrame):
        """to_dict() 可序列化为 JSON."""
        result = V9_DEFAULT_CONTRACT.validate(clean_panel, mode="warn_only")
        d = result.to_dict()
        json_str = json.dumps(d, default=str)
        assert json.loads(json_str) == d

    def test_error_count_and_critical_count(self, panel_missing_required_column: pd.DataFrame):
        """error_count 和 critical_count 正确."""
        result = V9_DEFAULT_CONTRACT.validate(panel_missing_required_column, mode="warn_only")
        # 缺失 close 列 → CRITICAL
        assert result.critical_count > 0
        assert result.error_count >= 0  # 可能有 entity grain 重复等其他 ERROR
