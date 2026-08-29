"""test_factor_discovery_unit.py — 因子挖掘工具单元测试

覆盖要点:
    - UNIVERSE_PRESETS 常量
    - FactorValidationResult / DiscoveryReport dataclass
    - FactorDataFetcher._normalize_columns (列名映射/数值转换)
    - FactorDataFetcher.get_available_cached_symbols (缓存不存在)
    - FactorValidator 常量
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from utils.factor_research.factor_discovery import (
    UNIVERSE_PRESETS,
    DiscoveryReport,
    FactorDataFetcher,
    FactorValidationResult,
    FactorValidator,
)

# ============================================================
# 常量
# ============================================================


class TestConstants:
    @pytest.mark.unit
    def test_universe_presets(self):
        assert "etf_core" in UNIVERSE_PRESETS
        assert "etf_broad" in UNIVERSE_PRESETS
        assert "etf50" in UNIVERSE_PRESETS

    @pytest.mark.unit
    def test_etf_core_subset_of_broad(self):
        assert set(UNIVERSE_PRESETS["etf_core"]).issubset(
            set(UNIVERSE_PRESETS["etf_broad"])
        )

    @pytest.mark.unit
    def test_etf50_largest(self):
        assert len(UNIVERSE_PRESETS["etf50"]) > len(UNIVERSE_PRESETS["etf_broad"])


# ============================================================
# FactorValidationResult
# ============================================================


class TestFactorValidationResult:
    @pytest.mark.unit
    def test_defaults(self):
        r = FactorValidationResult(factor_name="MOM_20D", category="momentum")
        assert r.ic_mean == 0.0
        assert r.effective is False
        assert r.direction == "positive"
        assert r.score == 0.0

    @pytest.mark.unit
    def test_with_values(self):
        r = FactorValidationResult(
            factor_name="MOM_20D",
            category="momentum",
            ic_mean=0.05,
            ic_ir=0.6,
            effective=True,
        )
        assert r.ic_mean == 0.05
        assert r.effective is True


# ============================================================
# DiscoveryReport
# ============================================================


class TestDiscoveryReport:
    @pytest.mark.unit
    def test_defaults(self):
        r = DiscoveryReport(
            universe=["A", "B"],
            start_date="2024-01-01",
            end_date="2024-06-01",
            n_symbols=2,
            n_dates=100,
            n_factors_tested=10,
        )
        assert r.effective_factors == []
        assert r.strong_factors == []
        assert r.generation_time == ""


# ============================================================
# FactorDataFetcher._normalize_columns
# ============================================================


class TestNormalizeColumns:
    @pytest.mark.unit
    def test_chinese_column_mapping(self):
        fetcher = FactorDataFetcher.__new__(FactorDataFetcher)
        df = pd.DataFrame(
            {
                "日期": ["2024-01-01", "2024-01-02"],
                "开盘": [10.0, 11.0],
                "收盘": [10.5, 11.5],
                "最高": [11.0, 12.0],
                "最低": [9.5, 10.5],
                "成交量": [1000, 2000],
            }
        )
        result = fetcher._normalize_columns(df)
        assert "open" in result.columns
        assert "close" in result.columns
        assert result.index.name == "date"  # date 被设为 index

    @pytest.mark.unit
    def test_numeric_conversion(self):
        fetcher = FactorDataFetcher.__new__(FactorDataFetcher)
        df = pd.DataFrame(
            {
                "日期": ["2024-01-01"],
                "收盘": ["10.5"],
                "成交量": ["1000"],
            }
        )
        result = fetcher._normalize_columns(df)
        assert pd.api.types.is_numeric_dtype(result["close"])
        assert pd.api.types.is_numeric_dtype(result["volume"])


# ============================================================
# FactorDataFetcher.get_available_cached_symbols
# ============================================================


class TestGetAvailableCachedSymbols:
    @pytest.mark.unit
    def test_cache_dir_not_exists(self):
        fetcher = FactorDataFetcher.__new__(FactorDataFetcher)
        with patch.object(
            FactorDataFetcher, "CACHE_DIR", MagicMock(exists=lambda: False)
        ):
            symbols = fetcher.get_available_cached_symbols()
        assert symbols == []


# ============================================================
# FactorValidator 常量
# ============================================================


class TestFactorValidator:
    @pytest.mark.unit
    def test_constants(self):
        assert FactorValidator.MIN_SAMPLES == 5
        assert FactorValidator.IC_STRONG_THRESHOLD == 0.05
        assert FactorValidator.IC_EFFECTIVE_THRESHOLD == 0.02
        assert FactorValidator.IR_STRONG_THRESHOLD == 0.5
        assert FactorValidator.IR_EFFECTIVE_THRESHOLD == 0.2

    @pytest.mark.unit
    def test_default_forward_days(self):
        validator = FactorValidator()
        assert validator.forward_days == [1, 5, 10, 20]

    @pytest.mark.unit
    def test_custom_forward_days(self):
        validator = FactorValidator(forward_days=[5, 20])
        assert validator.forward_days == [5, 20]
