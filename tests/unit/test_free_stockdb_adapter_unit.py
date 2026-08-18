# -*- coding: utf-8 -*-
"""free_stockdb_adapter 单元测试 — free-stockdb 数据适配器"""
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from utils.free_stockdb_adapter import (
    _strip_suffix,
    _period_to_date_range,
    _normalize_fs_dataframe,
    is_available,
)


class TestStripSuffix:
    def test_sh(self):
        assert _strip_suffix("600519.SH") == "600519"

    def test_sz(self):
        assert _strip_suffix("000001.SZ") == "000001"

    def test_bj(self):
        assert _strip_suffix("430047.BJ") == "430047"

    def test_lowercase(self):
        assert _strip_suffix("600519.sh") == "600519"

    def test_no_suffix(self):
        assert _strip_suffix("600519") == "600519"

    def test_unknown_suffix(self):
        assert _strip_suffix("A.XYZ") == "A.XYZ"


class TestPeriodToDateRange:
    def test_1y(self):
        start, end = _period_to_date_range("1y")
        start_date = datetime.strptime(start, "%Y%m%d")
        end_date = datetime.strptime(end, "%Y%m%d")
        delta = end_date - start_date
        assert 360 <= delta.days <= 400

    def test_2y(self):
        start, end = _period_to_date_range("2y")
        start_date = datetime.strptime(start, "%Y%m%d")
        end_date = datetime.strptime(end, "%Y%m%d")
        delta = end_date - start_date
        assert 720 <= delta.days <= 760

    def test_3y(self):
        start, end = _period_to_date_range("3y")
        start_date = datetime.strptime(start, "%Y%m%d")
        end_date = datetime.strptime(end, "%Y%m%d")
        delta = end_date - start_date
        assert 1080 <= delta.days <= 1130

    def test_5y(self):
        start, end = _period_to_date_range("5y")
        start_date = datetime.strptime(start, "%Y%m%d")
        end_date = datetime.strptime(end, "%Y%m%d")
        delta = end_date - start_date
        assert 1800 <= delta.days <= 1860

    def test_default(self):
        start, end = _period_to_date_range("unknown")
        assert len(start) == 8
        assert len(end) == 8

    def test_numeric(self):
        start, end = _period_to_date_range("1")
        start_date = datetime.strptime(start, "%Y%m%d")
        end_date = datetime.strptime(end, "%Y%m%d")
        delta = end_date - start_date
        assert 360 <= delta.days <= 400


class TestNormalizeFsDataframe:
    def test_none(self):
        result = _normalize_fs_dataframe(None, "A")
        assert result.empty

    def test_empty_dataframe(self):
        result = _normalize_fs_dataframe(pd.DataFrame(), "A")
        assert result.empty

    def test_empty_list(self):
        result = _normalize_fs_dataframe([], "A")
        assert result.empty

    def test_list_of_dicts(self):
        data = [
            {"date": "20260101", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1000},
            {"date": "20260102", "open": 10.5, "high": 12, "low": 10, "close": 11.5, "volume": 2000},
        ]
        result = _normalize_fs_dataframe(data, "A")
        assert len(result) == 2
        assert "close" in result.columns

    def test_dataframe(self):
        df = pd.DataFrame({
            "date": ["20260101", "20260102"],
            "open": [10, 10.5],
            "high": [11, 12],
            "low": [9, 10],
            "close": [10.5, 11.5],
            "volume": [1000, 2000],
        })
        result = _normalize_fs_dataframe(df, "A")
        assert len(result) == 2

    def test_chinese_aliases(self):
        df = pd.DataFrame({
            "date": ["20260101"],
            "开盘价": [10],
            "最高价": [11],
            "最低价": [9],
            "收盘价": [10.5],
            "成交量": [1000],
        })
        result = _normalize_fs_dataframe(df, "A")
        assert "open" in result.columns
        assert "close" in result.columns

    def test_dropna_close(self):
        df = pd.DataFrame({
            "date": ["20260101", "20260102"],
            "close": [10.5, None],
        })
        result = _normalize_fs_dataframe(df, "A")
        assert len(result) == 1

    def test_sorted_index(self):
        df = pd.DataFrame({
            "date": ["20260102", "20260101"],
            "close": [11.5, 10.5],
        })
        result = _normalize_fs_dataframe(df, "A")
        assert result.index[0] < result.index[1]


class TestIsAvailable:
    def test_returns_bool(self):
        with patch("utils.free_stockdb_adapter._init_free_stockdb", return_value=False):
            result = is_available()
        assert isinstance(result, bool)