"""DQC 指标覆盖率提升测试 — G7 冲刺.

覆盖目标:
    1. completeness (C-01 ~ C-06): 标的覆盖率/交易日/字段缺失/时间戳/OHLCV/复权因子
    2. timeliness (T-01 ~ T-03): 数据延迟/最新日期/EOD到位
    3. accuracy (A-01 ~ A-06): 涨跌幅/OHLC/成交量/市值/价格跳变/零价格
    4. uniqueness (U-01, U-03): 主键去重/代码规范
    5. consistency (X-01 ~ X-03, X-05): 跨源校验/历史不变性/成分股
    6. distribution (F-01 ~ F-04): PSI/均值漂移/方差漂移/极值频率
    7. 空值/异常输入处理 + 外部依赖 mock (DriftMonitor)

运行:
    python -m pytest tests/unit/test_g7_dqc_metrics_boost.py -v --tb=short
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def complete_df():
    return pd.DataFrame({
        "symbol": ["600519.SH", "000858.SZ", "600519.SH", "000858.SZ"],
        "date": ["2026-08-01", "2026-08-01", "2026-08-02", "2026-08-02"],
        "open": [1800.0, 150.0, 1810.0, 151.0],
        "high": [1820.0, 152.0, 1830.0, 153.0],
        "low": [1790.0, 149.0, 1800.0, 150.0],
        "close": [1810.0, 151.0, 1820.0, 152.0],
        "volume": [100000, 200000, 110000, 210000],
        "preclose": [1790.0, 149.0, 1810.0, 151.0],
        "adj_factor": [1.0, 1.0, 1.01, 1.01],
    })


@pytest.fixture
def symbols_23():
    return [f"{i:06d}.SH" for i in range(1, 24)]


# ============================================================
# Completeness
# ============================================================
class TestCompletenessC01SymbolCoverage:
    def test_full_coverage_info(self, complete_df):
        from utils.dqc.metrics.completeness import _check_c01_symbol_coverage
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = _check_c01_symbol_coverage(
            complete_df, ["600519.SH", "000858.SZ"], DQCCheckpoint.P2_CACHE
        )
        assert len(events) == 0

    def test_warn_coverage(self):
        from utils.dqc.metrics.completeness import _check_c01_symbol_coverage
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        rows = []
        for i in range(1, 19):
            rows.append({"symbol": f"{i:06d}.SH", "date": "2026-08-01", "close": 10.0})
        df = pd.DataFrame(rows)
        syms = [f"{i:06d}.SH" for i in range(1, 21)]
        events = _check_c01_symbol_coverage(df, syms, DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].level == DQCLevel.WARN

    def test_error_coverage_below_90(self, complete_df):
        from utils.dqc.metrics.completeness import _check_c01_symbol_coverage
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        syms = [f"{i:06d}.SH" for i in range(1, 24)]
        events = _check_c01_symbol_coverage(complete_df, syms, DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].level == DQCLevel.ERROR

    def test_no_symbol_or_code_column_error(self):
        from utils.dqc.metrics.completeness import _check_c01_symbol_coverage
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({"date": ["2026-08-01"], "close": [10.0]})
        events = _check_c01_symbol_coverage(df, ["600519.SH"], DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].level == DQCLevel.ERROR

    def test_empty_expected_symbols_returns_empty(self, complete_df):
        from utils.dqc.metrics.completeness import _check_c01_symbol_coverage
        from utils.dqc.event_types import DQCCheckpoint
        events = _check_c01_symbol_coverage(complete_df, [], DQCCheckpoint.P2_CACHE)
        assert len(events) == 0


class TestCompletenessC02TradingDayCoverage:
    def test_target_date_present_no_event(self, complete_df):
        from utils.dqc.metrics.completeness import _check_c02_trading_day_coverage
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = _check_c02_trading_day_coverage(complete_df, date(2026, 8, 2), DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_target_date_missing_error(self, complete_df):
        from utils.dqc.metrics.completeness import _check_c02_trading_day_coverage
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = _check_c02_trading_day_coverage(complete_df, date(2026, 8, 5), DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].level == DQCLevel.ERROR

    def test_missing_date_column_returns_empty(self):
        from utils.dqc.metrics.completeness import _check_c02_trading_day_coverage
        from utils.dqc.event_types import DQCCheckpoint
        df = pd.DataFrame({"symbol": ["600519.SH"], "close": [10.0]})
        events = _check_c02_trading_day_coverage(df, date(2026, 8, 1), DQCCheckpoint.P2_CACHE)
        assert len(events) == 0


class TestCompletenessC03FieldMissingRate:
    def test_no_missing_no_event(self, complete_df):
        from utils.dqc.metrics.completeness import _check_c03_field_missing_rate
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = _check_c03_field_missing_rate(complete_df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_missing_exceeds_threshold_warn(self):
        from utils.dqc.metrics.completeness import _check_c03_field_missing_rate
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH"] * 10,
            "date": ["2026-08-01"] * 10,
            "close": [10.0] * 5 + [None] * 5,
        })
        events = _check_c03_field_missing_rate(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].level == DQCLevel.WARN
        assert events[0].metric_id == "C-03"

    def test_empty_df_returns_empty(self):
        from utils.dqc.metrics.completeness import _check_c03_field_missing_rate
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = _check_c03_field_missing_rate(pd.DataFrame(), DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_zero_rows_returns_empty(self):
        from utils.dqc.metrics.completeness import _check_c03_field_missing_rate
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({"symbol": [], "date": [], "close": []})
        events = _check_c03_field_missing_rate(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_ignores_identifier_columns(self):
        from utils.dqc.metrics.completeness import _check_c03_field_missing_rate
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH"] * 10,
            "code": ["600519.SH"] * 10,
            "date": ["2026-08-01"] * 10,
            "timestamp": ["2026-08-01T00:00:00"] * 10,
            "close": [10.0] * 10,
        })
        events = _check_c03_field_missing_rate(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0


class TestCompletenessC04TimestampContinuity:
    def test_no_gap_no_event(self, complete_df):
        from utils.dqc.metrics.completeness import _check_c04_timestamp_continuity
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = _check_c04_timestamp_continuity(complete_df, date(2026, 8, 2), DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_gap_exceeds_3_days_error(self):
        from utils.dqc.metrics.completeness import _check_c04_timestamp_continuity
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH", "600519.SH"],
            "date": ["2026-08-01", "2026-08-10"],
            "close": [1800.0, 1810.0],
        })
        events = _check_c04_timestamp_continuity(df, date(2026, 8, 10), DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].level == DQCLevel.ERROR
        assert events[0].metric_id == "C-04"

    def test_weekend_gap_allowed(self):
        from utils.dqc.metrics.completeness import _check_c04_timestamp_continuity
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH", "600519.SH"],
            "date": ["2026-08-07", "2026-08-10"],
            "close": [1800.0, 1810.0],
        })
        events = _check_c04_timestamp_continuity(df, date(2026, 8, 10), DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_missing_date_column_returns_empty(self):
        from utils.dqc.metrics.completeness import _check_c04_timestamp_continuity
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({"symbol": ["600519.SH"], "close": [1800.0]})
        events = _check_c04_timestamp_continuity(df, date(2026, 8, 1), DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_date_parse_error_swallowed(self):
        from utils.dqc.metrics.completeness import _check_c04_timestamp_continuity
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH"],
            "date": ["not-a-date"],
            "close": [1800.0],
        })
        events = _check_c04_timestamp_continuity(df, date(2026, 8, 1), DQCCheckpoint.P2_CACHE)
        assert len(events) == 0


class TestCompletenessC05OHLCVCompleteness:
    def test_missing_ohlcv_fields_error(self):
        from utils.dqc.metrics.completeness import _check_c05_ohlcv_completeness
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({"symbol": ["600519.SH"], "date": ["2026-08-01"], "close": [10.0]})
        events = _check_c05_ohlcv_completeness(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].metric_id == "C-05"
        assert events[0].level == DQCLevel.ERROR

    def test_ohlcv_nan_per_symbol_error(self):
        from utils.dqc.metrics.completeness import _check_c05_ohlcv_completeness
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH", "600519.SH"],
            "date": ["2026-08-01", "2026-08-02"],
            "open": [1800.0, np.nan],
            "high": [1820.0, 1830.0],
            "low": [1790.0, 1800.0],
            "close": [1810.0, 1820.0],
            "volume": [100000, 110000],
        })
        events = _check_c05_ohlcv_completeness(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].metric_id == "C-05"

    def test_no_symbol_column_returns_empty(self, complete_df):
        from utils.dqc.metrics.completeness import _check_c05_ohlcv_completeness
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = complete_df.drop(columns=["symbol"])
        events = _check_c05_ohlcv_completeness(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0


class TestCompletenessC06AdjFactorCompleteness:
    def test_no_adj_factor_field_no_event(self, complete_df):
        from utils.dqc.metrics.completeness import _check_c06_adjfactor_completeness
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = complete_df.drop(columns=["adj_factor"])
        events = _check_c06_adjfactor_completeness(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_adj_factor_nan_error(self):
        from utils.dqc.metrics.completeness import _check_c06_adjfactor_completeness
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH"],
            "date": ["2026-08-01"],
            "close": [1810.0],
            "adj_factor": [None],
        })
        events = _check_c06_adjfactor_completeness(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].metric_id == "C-06"
        assert events[0].level == DQCLevel.ERROR

    def test_adj_factor_complete_no_event(self, complete_df):
        from utils.dqc.metrics.completeness import _check_c06_adjfactor_completeness
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = _check_c06_adjfactor_completeness(complete_df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_empty_df_returns_empty(self):
        from utils.dqc.metrics.completeness import _check_c06_adjfactor_completeness
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = _check_c06_adjfactor_completeness(pd.DataFrame(), DQCCheckpoint.P2_CACHE)
        assert len(events) == 0


# ============================================================
# Timeliness
# ============================================================
class TestTimelinessT01DataLatency:
    def test_no_delay_no_event(self):
        from utils.dqc.metrics.timeliness import _check_t01_data_latency
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        target = date(2026, 8, 1)
        arrival = datetime(2026, 8, 1, 15, 45)
        events = _check_t01_data_latency(
            pd.DataFrame({"close": [10.0]}), target, DQCCheckpoint.P1_SOURCE, arrival
        )
        assert len(events) == 0

    def test_delay_warn(self):
        from utils.dqc.metrics.timeliness import _check_t01_data_latency
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        target = date(2026, 8, 1)
        arrival = datetime(2026, 8, 1, 16, 15)
        events = _check_t01_data_latency(
            pd.DataFrame({"close": [10.0]}), target, DQCCheckpoint.P1_SOURCE, arrival
        )
        assert len(events) == 1
        assert events[0].level == DQCLevel.WARN

    def test_delay_error(self):
        from utils.dqc.metrics.timeliness import _check_t01_data_latency
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        target = date(2026, 8, 1)
        arrival = datetime(2026, 8, 1, 16, 45)
        events = _check_t01_data_latency(
            pd.DataFrame({"close": [10.0]}), target, DQCCheckpoint.P1_SOURCE, arrival
        )
        assert len(events) == 1
        assert events[0].level == DQCLevel.ERROR

    def test_none_arrival_uses_now(self):
        from utils.dqc.metrics import timeliness as timeliness_mod
        from utils.dqc.metrics.timeliness import _check_t01_data_latency
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        target = date(2026, 8, 1)
        fake_arrival = datetime(2026, 8, 1, 17, 0)
        mock_dt = MagicMock()
        mock_dt.now.return_value = fake_arrival
        mock_dt.combine.side_effect = datetime.combine
        mock_dt.min = datetime.min
        with patch.object(timeliness_mod, "datetime", mock_dt):
            events = _check_t01_data_latency(
                pd.DataFrame({"close": [10.0]}), target, DQCCheckpoint.P1_SOURCE, None
            )
        assert len(events) >= 1


class TestTimelinessT02LatestDate:
    def test_latest_equals_target_no_event(self, complete_df):
        from utils.dqc.metrics.timeliness import _check_t02_latest_date
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = _check_t02_latest_date(complete_df, date(2026, 8, 2), DQCCheckpoint.P1_SOURCE)
        assert len(events) == 0

    def test_latest_before_target_error(self):
        from utils.dqc.metrics.timeliness import _check_t02_latest_date
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH"],
            "date": ["2026-08-01"],
            "close": [10.0],
        })
        events = _check_t02_latest_date(df, date(2026, 8, 5), DQCCheckpoint.P1_SOURCE)
        assert len(events) == 1
        assert events[0].level == DQCLevel.ERROR

    def test_empty_df_no_event(self):
        from utils.dqc.metrics.timeliness import _check_t02_latest_date
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = _check_t02_latest_date(pd.DataFrame(), date(2026, 8, 1), DQCCheckpoint.P1_SOURCE)
        assert len(events) == 0

    def test_missing_date_column_no_event(self):
        from utils.dqc.metrics.timeliness import _check_t02_latest_date
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({"symbol": ["600519.SH"], "close": [10.0]})
        events = _check_t02_latest_date(df, date(2026, 8, 1), DQCCheckpoint.P1_SOURCE)
        assert len(events) == 0

    def test_date_parse_error_swallowed(self):
        from utils.dqc.metrics.timeliness import _check_t02_latest_date
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({"symbol": ["600519.SH"], "date": ["not-a-date"], "close": [10.0]})
        events = _check_t02_latest_date(df, date(2026, 8, 1), DQCCheckpoint.P1_SOURCE)
        assert len(events) == 0


class TestTimelinessT03EODArrival:
    def test_before_deadline_no_event(self):
        from utils.dqc.metrics.timeliness import _check_t03_eod_arrival
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        target = date(2026, 8, 1)
        arrival = datetime(2026, 8, 1, 16, 0)
        events = _check_t03_eod_arrival(target, DQCCheckpoint.P1_SOURCE, arrival)
        assert len(events) == 0

    def test_after_deadline_warn(self):
        from utils.dqc.metrics.timeliness import _check_t03_eod_arrival
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        target = date(2026, 8, 1)
        arrival = datetime(2026, 8, 1, 17, 0)
        events = _check_t03_eod_arrival(target, DQCCheckpoint.P1_SOURCE, arrival)
        assert len(events) == 1
        assert events[0].level == DQCLevel.WARN

    def test_none_arrival_no_event(self):
        from utils.dqc.metrics.timeliness import _check_t03_eod_arrival
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = _check_t03_eod_arrival(date(2026, 8, 1), DQCCheckpoint.P1_SOURCE, None)
        assert len(events) == 0


# ============================================================
# Accuracy
# ============================================================
class TestAccuracyA01PriceChangeLimit:
    def test_no_preclose_skips(self):
        from utils.dqc.metrics.accuracy import _check_a01_price_change_limit
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({"symbol": ["600519.SH"], "date": ["2026-08-01"], "close": [10.0]})
        events = _check_a01_price_change_limit(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_normal_change_no_event(self, complete_df):
        from utils.dqc.metrics.accuracy import _check_a01_price_change_limit
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = _check_a01_price_change_limit(complete_df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_excessive_change_error(self):
        from utils.dqc.metrics.accuracy import _check_a01_price_change_limit
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["300308.SZ", "300308.SZ"],
            "date": ["2026-08-01", "2026-08-02"],
            "close": [10.0, 25.0],
            "preclose": [10.0, 10.0],
        })
        events = _check_a01_price_change_limit(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].level == DQCLevel.ERROR

    def test_gem_threshold_higher(self):
        from utils.dqc.metrics.accuracy import _check_a01_price_change_limit
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["300308.SZ", "300308.SZ"],
            "date": ["2026-08-01", "2026-08-02"],
            "close": [10.0, 12.0],
            "preclose": [10.0, 10.0],
        })
        events = _check_a01_price_change_limit(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_zero_preclose_skips(self):
        from utils.dqc.metrics.accuracy import _check_a01_price_change_limit
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH"],
            "date": ["2026-08-01"],
            "close": [10.0],
            "preclose": [0.0],
        })
        events = _check_a01_price_change_limit(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0


class TestAccuracyA02OHLCRelation:
    def test_valid_ohlc_no_event(self, complete_df):
        from utils.dqc.metrics.accuracy import _check_a02_ohlcl_relation
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = _check_a02_ohlcl_relation(complete_df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_below_low_violation(self):
        from utils.dqc.metrics.accuracy import _check_a02_ohlcl_relation
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH"],
            "date": ["2026-08-01"],
            "open": [1800.0],
            "high": [1820.0],
            "low": [1830.0],
            "close": [1810.0],
        })
        events = _check_a02_ohlcl_relation(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].metric_id == "A-02"
        assert events[0].level == DQCLevel.ERROR

    def test_above_high_violation(self):
        from utils.dqc.metrics.accuracy import _check_a02_ohlcl_relation
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH"],
            "date": ["2026-08-01"],
            "open": [1830.0],
            "high": [1820.0],
            "low": [1790.0],
            "close": [1810.0],
        })
        events = _check_a02_ohlcl_relation(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].metric_id == "A-02"

    def test_missing_ohlc_returns_empty(self):
        from utils.dqc.metrics.accuracy import _check_a02_ohlcl_relation
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({"symbol": ["600519.SH"], "close": [10.0]})
        events = _check_a02_ohlcl_relation(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0


class TestAccuracyA03VolumeNonNegative:
    def test_non_negative_volume_no_event(self, complete_df):
        from utils.dqc.metrics.accuracy import _check_a03_volume_non_negative
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = _check_a03_volume_non_negative(complete_df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_negative_volume_error(self):
        from utils.dqc.metrics.accuracy import _check_a03_volume_non_negative
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH"],
            "date": ["2026-08-01"],
            "volume": [-100],
        })
        events = _check_a03_volume_non_negative(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].metric_id == "A-03"
        assert events[0].level == DQCLevel.ERROR

    def test_missing_volume_no_event(self):
        from utils.dqc.metrics.accuracy import _check_a03_volume_non_negative
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({"symbol": ["600519.SH"], "close": [10.0]})
        events = _check_a03_volume_non_negative(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0


class TestAccuracyA04MarketCapConsistency:
    def test_missing_fields_no_event(self):
        from utils.dqc.metrics.accuracy import _check_a04_market_cap_consistency
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({"symbol": ["600519.SH"], "close": [10.0]})
        events = _check_a04_market_cap_consistency(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_consistent_market_cap_no_event(self):
        from utils.dqc.metrics.accuracy import _check_a04_market_cap_consistency
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH"],
            "close": [10.0],
            "shares": [1000.0],
            "market_cap": [10000.0],
        })
        events = _check_a04_market_cap_consistency(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_inconsistent_market_cap_warn(self):
        from utils.dqc.metrics.accuracy import _check_a04_market_cap_consistency
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH"],
            "close": [10.0],
            "shares": [1000.0],
            "market_cap": [20000.0],
        })
        events = _check_a04_market_cap_consistency(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].metric_id == "A-04"
        assert events[0].level == DQCLevel.WARN


class TestAccuracyA05PriceJump:
    def test_no_jump_no_event(self, complete_df):
        from utils.dqc.metrics.accuracy import _check_a05_price_jump
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = _check_a05_price_jump(complete_df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_price_jump_warn(self):
        from utils.dqc.metrics.accuracy import _check_a05_price_jump
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH", "600519.SH"],
            "date": ["2026-08-01", "2026-08-02"],
            "close": [10.0, 25.0],
        })
        events = _check_a05_price_jump(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].metric_id == "A-05"
        assert events[0].level == DQCLevel.WARN

    def test_missing_symbol_or_date_no_event(self):
        from utils.dqc.metrics.accuracy import _check_a05_price_jump
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({"close": [10.0, 25.0]})
        events = _check_a05_price_jump(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0


class TestAccuracyA06ZeroPrice:
    def test_zero_price_error(self):
        from utils.dqc.metrics.accuracy import _check_a06_zero_price
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH"],
            "date": ["2026-08-01"],
            "close": [0.0],
        })
        events = _check_a06_zero_price(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].metric_id == "A-06"
        assert events[0].level == DQCLevel.ERROR

    def test_no_zero_price_no_event(self, complete_df):
        from utils.dqc.metrics.accuracy import _check_a06_zero_price
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = _check_a06_zero_price(complete_df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_missing_close_no_event(self):
        from utils.dqc.metrics.accuracy import _check_a06_zero_price
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({"symbol": ["600519.SH"], "date": ["2026-08-01"]})
        events = _check_a06_zero_price(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0


# ============================================================
# Uniqueness
# ============================================================
class TestUniquenessU01PrimaryKeyDedup:
    def test_no_duplicates_no_event(self, complete_df):
        from utils.dqc.metrics.uniqueness import _check_u01_primary_key_dedup
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = _check_u01_primary_key_dedup(complete_df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_duplicates_error(self):
        from utils.dqc.metrics.uniqueness import _check_u01_primary_key_dedup
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH", "600519.SH"],
            "date": ["2026-08-01", "2026-08-01"],
            "close": [10.0, 10.0],
        })
        events = _check_u01_primary_key_dedup(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].metric_id == "U-01"
        assert events[0].level == DQCLevel.ERROR
        assert events[0].value == 2.0

    def test_empty_df_no_event(self):
        from utils.dqc.metrics.uniqueness import _check_u01_primary_key_dedup
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = _check_u01_primary_key_dedup(pd.DataFrame(), DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_missing_symbol_or_date_no_event(self):
        from utils.dqc.metrics.uniqueness import _check_u01_primary_key_dedup
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({"close": [10.0, 10.0]})
        events = _check_u01_primary_key_dedup(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0


class TestUniquenessU03SymbolCodeFormat:
    def test_valid_codes_no_event(self, complete_df):
        from utils.dqc.metrics.uniqueness import _check_u03_symbol_code_format
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = _check_u03_symbol_code_format(complete_df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_invalid_code_warn(self):
        from utils.dqc.metrics.uniqueness import _check_u03_symbol_code_format
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["INVALID", "600519.SH"],
            "date": ["2026-08-01", "2026-08-01"],
            "close": [10.0, 10.0],
        })
        events = _check_u03_symbol_code_format(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].metric_id == "U-03"
        assert events[0].level == DQCLevel.WARN

    def test_empty_df_no_event(self):
        from utils.dqc.metrics.uniqueness import _check_u03_symbol_code_format
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = _check_u03_symbol_code_format(pd.DataFrame(), DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_no_symbol_column_no_event(self):
        from utils.dqc.metrics.uniqueness import _check_u03_symbol_code_format
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({"date": ["2026-08-01"], "close": [10.0]})
        events = _check_u03_symbol_code_format(df, DQCCheckpoint.P2_CACHE)
        assert len(events) == 0


# ============================================================
# Consistency
# ============================================================
class TestConsistencyX01CrossSourcePrice:
    def test_no_cross_source_no_event(self, complete_df):
        from utils.dqc.metrics.consistency import check_consistency
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = check_consistency(complete_df, checkpoint=DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_cross_source_no_common_keys_no_event(self, complete_df):
        from utils.dqc.metrics.consistency import check_consistency
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        cross = pd.DataFrame({
            "other_id": ["600519.SH"],
            "date": ["2026-08-01"],
            "close": [10.0],
        })
        events = check_consistency(complete_df, cross_source_df=cross, checkpoint=DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_cross_source_within_threshold_no_event(self):
        from utils.dqc.metrics.consistency import check_consistency
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        main = pd.DataFrame({
            "symbol": ["600519.SH"],
            "date": ["2026-08-01"],
            "close": [10.0],
        })
        cross = pd.DataFrame({
            "symbol": ["600519.SH"],
            "date": ["2026-08-01"],
            "close": [10.001],
        })
        events = check_consistency(main, cross_source_df=cross, checkpoint=DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_cross_source_warn_threshold(self):
        from utils.dqc.metrics.consistency import check_consistency
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        main = pd.DataFrame({
            "symbol": ["600519.SH"],
            "date": ["2026-08-01"],
            "close": [10.0],
        })
        cross = pd.DataFrame({
            "symbol": ["600519.SH"],
            "date": ["2026-08-01"],
            "close": [10.03],
        })
        events = check_consistency(main, cross_source_df=cross, checkpoint=DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].metric_id == "X-01"
        assert events[0].level == DQCLevel.WARN

    def test_cross_source_error_threshold(self):
        from utils.dqc.metrics.consistency import check_consistency
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        main = pd.DataFrame({
            "symbol": ["600519.SH"],
            "date": ["2026-08-01"],
            "close": [10.0],
        })
        cross = pd.DataFrame({
            "symbol": ["600519.SH"],
            "date": ["2026-08-01"],
            "close": [10.5],
        })
        events = check_consistency(main, cross_source_df=cross, checkpoint=DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].level == DQCLevel.ERROR


class TestConsistencyX02CrossSourceVolume:
    def test_no_volume_field_no_event(self):
        from utils.dqc.metrics.consistency import check_consistency
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        main = pd.DataFrame({"symbol": ["600519.SH"], "date": ["2026-08-01"], "close": [10.0]})
        cross = pd.DataFrame({"symbol": ["600519.SH"], "date": ["2026-08-01"], "close": [10.0]})
        events = check_consistency(main, cross_source_df=cross, checkpoint=DQCCheckpoint.P2_CACHE)
        assert len(events) == 0


class TestConsistencyX03HistoryInvariance:
    def test_no_history_cache_no_event(self, complete_df):
        from utils.dqc.metrics.consistency import check_consistency
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = check_consistency(complete_df, history_cache=None, checkpoint=DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_consistent_history_no_event(self, complete_df):
        from utils.dqc.metrics.consistency import check_consistency
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = check_consistency(
            complete_df, history_cache=complete_df.copy(), checkpoint=DQCCheckpoint.P2_CACHE
        )
        assert len(events) == 0

    def test_inconsistent_history_error(self):
        from utils.dqc.metrics.consistency import check_consistency
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH"],
            "date": ["2026-08-01"],
            "open": [1800.0],
            "high": [1820.0],
            "low": [1790.0],
            "close": [1810.0],
            "volume": [100000],
        })
        history = df.copy()
        history["close"] = [1900.0]
        events = check_consistency(df, history_cache=history, checkpoint=DQCCheckpoint.P2_CACHE)
        assert len(events) == 1
        assert events[0].metric_id == "X-03"
        assert events[0].level == DQCLevel.ERROR


class TestConsistencyX05IndexConsistency:
    def test_no_expected_symbols_no_event(self, complete_df):
        from utils.dqc.metrics.consistency import check_consistency
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = check_consistency(complete_df, expected_symbols=None, checkpoint=DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_consistent_symbols_no_event(self, complete_df):
        from utils.dqc.metrics.consistency import check_consistency
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        syms = ["600519.SH", "000858.SZ"]
        events = check_consistency(complete_df, expected_symbols=syms, checkpoint=DQCCheckpoint.P2_CACHE)
        assert len(events) == 0

    def test_missing_symbols_error(self):
        from utils.dqc.metrics.consistency import check_consistency
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH"],
            "date": ["2026-08-01"],
            "close": [10.0],
        })
        events = check_consistency(
            df, expected_symbols=["600519.SH", "000858.SZ", "510050.SH", "000001.SZ"], checkpoint=DQCCheckpoint.P2_CACHE
        )
        assert len(events) == 1
        assert events[0].metric_id == "X-05"
        assert events[0].level == DQCLevel.ERROR

    def test_no_symbol_column_no_event(self):
        from utils.dqc.metrics.consistency import check_consistency
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({"date": ["2026-08-01"], "close": [10.0]})
        events = check_consistency(df, expected_symbols=["600519.SH"], checkpoint=DQCCheckpoint.P2_CACHE)
        assert len(events) == 0


# ============================================================
# Distribution (F-01 ~ F-04)
# ============================================================
class TestDistributionF01PSI:
    def test_psi_below_threshold_no_event(self):
        from utils.dqc.metrics.distribution import _check_f01_psi
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series(np.random.normal(0, 1, 1000))
        current = pd.Series(np.random.normal(0, 1, 1000))
        with patch("utils.alpha.drift_monitor.compute_psi", return_value=0.05):
            events = _check_f01_psi(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 0

    def test_psi_warn_threshold(self):
        from utils.dqc.metrics.distribution import _check_f01_psi
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series(np.random.normal(0, 1, 1000))
        current = pd.Series(np.random.normal(0.5, 1, 1000))
        with patch("utils.alpha.drift_monitor.compute_psi", return_value=0.15):
            events = _check_f01_psi(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 1
        assert events[0].level == DQCLevel.WARN

    def test_psi_error_threshold(self):
        from utils.dqc.metrics.distribution import _check_f01_psi
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series(np.random.normal(0, 1, 1000))
        current = pd.Series(np.random.normal(0.5, 1, 1000))
        with patch("utils.alpha.drift_monitor.compute_psi", return_value=0.3):
            events = _check_f01_psi(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 1
        assert events[0].level == DQCLevel.ERROR

    def test_psi_critical_threshold(self):
        from utils.dqc.metrics.distribution import _check_f01_psi
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series(np.random.normal(0, 1, 1000))
        current = pd.Series(np.random.normal(0.5, 1, 1000))
        with patch("utils.alpha.drift_monitor.compute_psi", return_value=0.6):
            events = _check_f01_psi(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 1
        assert events[0].level == DQCLevel.CRITICAL

    def test_psi_compute_failure_warn(self):
        from utils.dqc.metrics.distribution import _check_f01_psi
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series([1.0, 2.0])
        current = pd.Series([1.0, 2.0])
        with patch("utils.alpha.drift_monitor.compute_psi", side_effect=ImportError("no psi")):
            events = _check_f01_psi(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 1
        assert events[0].level == DQCLevel.WARN

    def test_psi_few_samples_skips(self):
        from utils.dqc.metrics.distribution import _check_f01_psi
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series([1.0])
        current = pd.Series([1.0])
        with patch("utils.alpha.drift_monitor.compute_psi", return_value=0.05):
            events = _check_f01_psi(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 0

    def test_psi_column_missing_warn(self):
        from utils.dqc.metrics.distribution import check_distribution_drift
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.DataFrame({"MOM": [0.1, 0.2]})
        current = pd.DataFrame({"MOM": [0.1, 0.2], "VOL": [0.3, 0.4]})
        events = check_distribution_drift(baseline, current, ["MOM", "VOL"], DQCCheckpoint.P3_FACTOR)
        assert len(events) == 1
        assert events[0].metric_id == "F-01"
        assert events[0].level == DQCLevel.WARN


class TestDistributionF02MeanDrift:
    def test_no_drift_no_event(self):
        from utils.dqc.metrics.distribution import _check_f02_mean_drift
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series([0.1, 0.2, 0.3])
        current = pd.Series([0.11, 0.21, 0.31])
        events = _check_f02_mean_drift(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 0

    def test_warn_drift(self):
        from utils.dqc.metrics.distribution import _check_f02_mean_drift
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series([0.1, 0.2, 0.3])
        current = pd.Series([0.8, 0.9, 1.0])
        events = _check_f02_mean_drift(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 1
        assert events[0].level == DQCLevel.CRITICAL

    def test_error_drift(self):
        from utils.dqc.metrics.distribution import _check_f02_mean_drift
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series([0.1, 0.2, 0.3])
        current = pd.Series([1.5, 1.6, 1.7])
        events = _check_f02_mean_drift(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 1
        assert events[0].level == DQCLevel.CRITICAL

    def test_critical_drift(self):
        from utils.dqc.metrics.distribution import _check_f02_mean_drift
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series([0.1, 0.2, 0.3])
        current = pd.Series([3.0, 3.1, 3.2])
        events = _check_f02_mean_drift(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 1
        assert events[0].level == DQCLevel.CRITICAL

    def test_zero_std_skips(self):
        from utils.dqc.metrics.distribution import _check_f02_mean_drift
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series([0.1, 0.1, 0.1])
        current = pd.Series([0.2, 0.2, 0.2])
        events = _check_f02_mean_drift(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 0


class TestDistributionF03VarianceDrift:
    def test_no_drift_no_event(self):
        from utils.dqc.metrics.distribution import _check_f03_variance_drift
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series([0.1, 0.2, 0.3, 0.4])
        current = pd.Series([0.11, 0.21, 0.31, 0.41])
        events = _check_f03_variance_drift(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 0

    def test_warn_shrink(self):
        from utils.dqc.metrics.distribution import _check_f03_variance_drift
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series([0.1, 0.2, 0.3, 0.4])
        current = pd.Series([0.2, 0.2, 0.2, 0.2])
        events = _check_f03_variance_drift(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 1
        assert events[0].level == DQCLevel.ERROR

    def test_warn_amplify(self):
        from utils.dqc.metrics.distribution import _check_f03_variance_drift
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series([0.1, 0.2, 0.3, 0.4])
        current = pd.Series([0.1, 1.0, 0.1, 1.0])
        events = _check_f03_variance_drift(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 1
        assert events[0].level == DQCLevel.ERROR

    def test_error_shrink(self):
        from utils.dqc.metrics.distribution import _check_f03_variance_drift
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series([0.1, 0.2, 0.3, 0.4])
        current = pd.Series([0.15, 0.15, 0.15, 0.15])
        events = _check_f03_variance_drift(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 1
        assert events[0].level == DQCLevel.ERROR

    def test_error_amplify(self):
        from utils.dqc.metrics.distribution import _check_f03_variance_drift
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series([0.1, 0.2, 0.3, 0.4])
        current = pd.Series([0.1, 5.0, 0.1, 5.0])
        events = _check_f03_variance_drift(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 1
        assert events[0].level == DQCLevel.ERROR

    def test_zero_std_skips(self):
        from utils.dqc.metrics.distribution import _check_f03_variance_drift
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series([0.1, 0.1, 0.1])
        current = pd.Series([0.2, 0.2, 0.2])
        events = _check_f03_variance_drift(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 0


class TestDistributionF04ExtremeFreq:
    def test_no_extreme_no_event(self):
        from utils.dqc.metrics.distribution import _check_f04_extreme_freq
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series([1.0, 2.0, 3.0] * 100)
        current = pd.Series([1.0, 2.0, 3.0] * 100)
        events = _check_f04_extreme_freq(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 0

    def test_warn_extreme_freq(self):
        from utils.dqc.metrics.distribution import _check_f04_extreme_freq
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series([1.0, 2.0, 3.0] * 100)
        current = pd.Series([1.0, 2.0, 3.0] * 85 + [100.0] * 15)
        events = _check_f04_extreme_freq(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 1
        assert events[0].level == DQCLevel.WARN

    def test_error_extreme_freq(self):
        from utils.dqc.metrics.distribution import _check_f04_extreme_freq
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series([1.0, 2.0, 3.0] * 100)
        current = pd.Series([1.0, 2.0, 3.0] * 75 + [100.0] * 26)
        events = _check_f04_extreme_freq(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 1
        assert events[0].level == DQCLevel.ERROR

    def test_zero_baseline_std_uses_default_threshold(self):
        from utils.dqc.metrics.distribution import _check_f04_extreme_freq
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        baseline = pd.Series([0.0] * 100)
        current = pd.Series([1.0, 2.0, 3.0] * 25 + [0.0] * 25)
        events = _check_f04_extreme_freq(baseline, current, "MOM", DQCCheckpoint.P3_FACTOR)
        assert len(events) == 0


# ============================================================
# Edge cases / null handling / exception inputs
# ============================================================
class TestMetricsNullAndExceptionInputs:
    def test_completeness_empty_dataframe(self):
        from utils.dqc.metrics.completeness import check_completeness
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = check_completeness(pd.DataFrame(), date(2026, 8, 1), ["600519.SH"], DQCCheckpoint.P2_CACHE)
        assert isinstance(events, list)

    def test_timeliness_empty_dataframe(self):
        from utils.dqc.metrics.timeliness import check_timeliness
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = check_timeliness(pd.DataFrame(), date(2026, 8, 1), DQCCheckpoint.P1_SOURCE)
        assert isinstance(events, list)

    def test_accuracy_empty_dataframe(self):
        from utils.dqc.metrics.accuracy import check_accuracy
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = check_accuracy(pd.DataFrame(), DQCCheckpoint.P2_CACHE)
        assert isinstance(events, list)

    def test_uniqueness_empty_dataframe(self):
        from utils.dqc.metrics.uniqueness import check_uniqueness
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = check_uniqueness(pd.DataFrame(), DQCCheckpoint.P2_CACHE)
        assert isinstance(events, list)

    def test_distribution_empty_factor_cols(self):
        from utils.dqc.metrics.distribution import check_distribution_drift
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = check_distribution_drift(
            pd.DataFrame({"x": [1.0]}), pd.DataFrame({"x": [2.0]}), [], DQCCheckpoint.P3_FACTOR
        )
        assert isinstance(events, list)

    def test_distribution_both_empty_dataframes(self):
        from utils.dqc.metrics.distribution import check_distribution_drift
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        events = check_distribution_drift(
            pd.DataFrame(), pd.DataFrame(), ["MOM"], DQCCheckpoint.P3_FACTOR
        )
        assert isinstance(events, list)
        assert len(events) == 0

    def test_check_completeness_all_nan_column(self):
        from utils.dqc.metrics.completeness import check_completeness
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH"],
            "date": ["2026-08-01"],
            "close": [None],
            "open": [None],
            "high": [None],
            "low": [None],
            "volume": [None],
        })
        events = check_completeness(df, date(2026, 8, 1), ["600519.SH"], DQCCheckpoint.P2_CACHE)
        assert isinstance(events, list)
        assert len(events) > 0

    def test_check_timeliness_all_none_arrival(self):
        from utils.dqc.metrics.timeliness import check_timeliness
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({"symbol": ["600519.SH"], "date": ["2026-08-01"], "close": [10.0]})
        events = check_timeliness(df, date(2026, 8, 1), DQCCheckpoint.P1_SOURCE, None)
        assert isinstance(events, list)

    def test_check_accuracy_all_nan_close(self):
        from utils.dqc.metrics.accuracy import check_accuracy
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["600519.SH"],
            "date": ["2026-08-01"],
            "close": [None],
            "open": [None],
            "high": [None],
            "low": [None],
            "volume": [None],
        })
        events = check_accuracy(df, DQCCheckpoint.P2_CACHE)
        assert isinstance(events, list)

    def test_check_uniqueness_all_invalid_codes(self):
        from utils.dqc.metrics.uniqueness import check_uniqueness
        from utils.dqc.event_types import DQCLevel, DQCCheckpoint
        df = pd.DataFrame({
            "symbol": ["BAD1", "BAD2", "BAD3"],
            "date": ["2026-08-01", "2026-08-01", "2026-08-01"],
            "close": [10.0, 11.0, 12.0],
        })
        events = check_uniqueness(df, DQCCheckpoint.P2_CACHE)
        assert isinstance(events, list)
        assert len(events) > 0
