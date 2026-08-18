"""
单元测试: utils/qlib_data_bridge.py
覆盖 to_qlib_symbol / from_qlib_symbol / dataframe_to_qlib_record / qlib_signal_to_system / get_qlib_cache_root
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from utils.qlib_data_bridge import (
    dataframe_to_qlib_record,
    from_qlib_symbol,
    get_qlib_cache_root,
    qlib_signal_to_system,
    to_qlib_symbol,
)


class TestToQlibSymbol:
    def test_sh_code(self):
        assert to_qlib_symbol("600519") == "600519.SH"

    def test_sz_code(self):
        assert to_qlib_symbol("000001") == "000001.SZ"

    def test_bj_code(self):
        assert to_qlib_symbol("430001") == "430001.BJ"

    def test_etf_sh(self):
        assert to_qlib_symbol("510300") == "510300.SH"

    def test_etf_sz(self):
        assert to_qlib_symbol("159915") == "159915.SZ"

    def test_with_sh_suffix(self):
        assert to_qlib_symbol("600519.SH") == "600519.SH"

    def test_with_sz_suffix(self):
        assert to_qlib_symbol("000001.SZ") == "000001.SZ"

    def test_with_sh_prefix(self):
        assert to_qlib_symbol("sh600519") == "600519.SH"

    def test_with_sz_prefix(self):
        assert to_qlib_symbol("sz000001") == "000001.SZ"

    def test_empty_string(self):
        assert to_qlib_symbol("") == ""

    def test_unknown_prefix_defaults_sh(self):
        assert to_qlib_symbol("999999") == "999999.SH"


class TestFromQlibSymbol:
    def test_sh(self):
        assert from_qlib_symbol("600519.SH") == "600519"

    def test_sz(self):
        assert from_qlib_symbol("000001.SZ") == "000001"

    def test_bj(self):
        assert from_qlib_symbol("430001.BJ") == "430001"

    def test_no_dot(self):
        assert from_qlib_symbol("600519") == "600519"

    def test_lowercase_suffix(self):
        assert from_qlib_symbol("600519.sh") == "600519"


class TestDataframeToQlibRecord:
    def test_empty_dataframe(self):
        df = pd.DataFrame()
        assert dataframe_to_qlib_record(df) == []

    def test_non_dataframe(self):
        assert dataframe_to_qlib_record("not a df") == []

    def test_basic_conversion(self):
        df = pd.DataFrame(
            {"open": [10.0, 11.0], "high": [11.0, 12.0], "low": [9.0, 10.0],
             "close": [10.5, 11.5], "volume": [1000, 2000]},
            index=pd.to_datetime(["2026-01-01", "2026-01-02"]),
        )
        records = dataframe_to_qlib_record(df)
        assert len(records) == 2
        assert records[0]["date"] == "2026-01-01"
        assert records[0]["open"] == 10.0
        assert records[0]["close"] == 10.5
        assert records[0]["volume"] == 1000.0

    def test_with_amount(self):
        df = pd.DataFrame(
            {"open": [10.0], "high": [11.0], "low": [9.0],
             "close": [10.5], "volume": [1000], "amount": [5000]},
            index=pd.to_datetime(["2026-01-01"]),
        )
        records = dataframe_to_qlib_record(df)
        assert records[0]["amount"] == 5000.0

    def test_missing_columns_default_zero(self):
        df = pd.DataFrame(
            {"close": [10.5]},
            index=pd.to_datetime(["2026-01-01"]),
        )
        records = dataframe_to_qlib_record(df)
        assert records[0]["open"] == 0.0
        assert records[0]["close"] == 10.5


class TestQlibSignalToSystem:
    def test_dict_with_score(self):
        result = qlib_signal_to_system({"score": 0.5})
        assert result["score"] == 0.5
        assert result["source"] == "qlib"

    def test_dict_with_y(self):
        result = qlib_signal_to_system({"y": 0.3})
        assert result["score"] == 0.3

    def test_dict_with_pred(self):
        result = qlib_signal_to_system({"pred": 0.2})
        assert result["score"] == 0.2

    def test_dict_with_direction(self):
        result = qlib_signal_to_system({"score": 0.5, "direction": "buy"})
        assert result["direction"] == "buy"

    def test_dict_with_confidence(self):
        result = qlib_signal_to_system({"score": 0.5, "confidence": 0.8})
        assert result["confidence"] == 0.8

    def test_numpy_scalar(self):
        result = qlib_signal_to_system(np.float64(0.5))
        assert result["score"] == 0.5
        assert result["direction"] == "buy"

    def test_negative_numpy_scalar(self):
        result = qlib_signal_to_system(np.float64(-0.3))
        assert result["direction"] == "sell"

    def test_unknown_type(self):
        result = qlib_signal_to_system("unknown")
        assert result["score"] == 0.0
        assert result["direction"] == "neutral"


class TestGetQlibCacheRoot:
    def test_returns_string(self):
        result = get_qlib_cache_root()
        assert isinstance(result, str)
        assert ".qlib_cache" in result