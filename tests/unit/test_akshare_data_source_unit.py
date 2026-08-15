"""AKShare Data Source 单元测试.

被测模块: utils/akshare_data_source.py
覆盖目标: >=60%
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.akshare_data_source import (  # noqa: E402
    AKShareDataSource,
    _safe_float,
)


class TestSafeFloat:
    def test_normal_value(self):
        assert _safe_float(3.14) == 3.14

    def test_none(self):
        assert _safe_float(None) == 0.0

    def test_string(self):
        assert _safe_float("3.14") == 3.14

    def test_invalid_string(self):
        assert _safe_float("invalid") == 0.0

    def test_nan(self):
        assert _safe_float(float("nan")) == 0.0

    def test_with_default(self):
        assert _safe_float("invalid", default=-1.0) == -1.0

    def test_zero(self):
        assert _safe_float(0) == 0.0

    def test_negative(self):
        assert _safe_float(-5.5) == -5.5


class TestToAkshareCode:
    @pytest.fixture
    def source(self):
        with patch.dict("sys.modules", {"akshare": MagicMock()}):
            return AKShareDataSource()

    def test_plain_code(self, source):
        assert source._to_akshare_code("600519") == "600519"

    def test_sh_prefix(self, source):
        assert source._to_akshare_code("sh600519") == "600519"

    def test_sz_prefix(self, source):
        assert source._to_akshare_code("sz000001") == "000001"

    def test_sh_suffix(self, source):
        assert source._to_akshare_code("600519.SH") == "600519"

    def test_sz_suffix(self, source):
        assert source._to_akshare_code("000001.SZ") == "000001"

    def test_bj_suffix(self, source):
        assert source._to_akshare_code("430047.BJ") == "430047"

    def test_lowercase_suffix(self, source):
        assert source._to_akshare_code("600519.sh") == "600519"

    def test_with_whitespace(self, source):
        assert source._to_akshare_code("  600519  ") == "600519"


class TestGetMarket:
    @pytest.fixture
    def source(self):
        with patch.dict("sys.modules", {"akshare": MagicMock()}):
            return AKShareDataSource()

    def test_sh_by_prefix_6(self, source):
        assert source._get_market("600519") == "sh"

    def test_sh_by_prefix(self, source):
        assert source._get_market("sh600519") == "sh"

    def test_sh_by_suffix(self, source):
        assert source._get_market("600519.SH") == "sh"

    def test_sz_by_prefix_0(self, source):
        assert source._get_market("000001") == "sz"

    def test_sz_by_prefix_3(self, source):
        assert source._get_market("300750") == "sz"

    def test_sz_by_prefix(self, source):
        assert source._get_market("sz000001") == "sz"

    def test_bj_by_prefix_4(self, source):
        assert source._get_market("430047") == "bj"

    def test_bj_by_prefix_8(self, source):
        assert source._get_market("830799") == "bj"

    def test_unknown_defaults_sh(self, source):
        assert source._get_market("999999") == "sh"


class TestCleanName:
    @pytest.fixture
    def source(self):
        with patch.dict("sys.modules", {"akshare": MagicMock()}):
            return AKShareDataSource()

    def test_normal_name(self, source):
        assert source._clean_name("贵州茅台") == "贵州茅台"

    def test_xd_prefix(self, source):
        assert source._clean_name("XD贵州茅台") == "贵州茅台"

    def test_xr_prefix(self, source):
        assert source._clean_name("XR贵州茅台") == "贵州茅台"

    def test_dr_prefix(self, source):
        assert source._clean_name("DR贵州茅台") == "贵州茅台"

    def test_lowercase_prefix(self, source):
        assert source._clean_name("xd贵州茅台") == "贵州茅台"

    def test_empty(self, source):
        assert source._clean_name("") == ""

    def test_none(self, source):
        assert source._clean_name(None) == ""

    def test_no_prefix(self, source):
        assert source._clean_name("中国平安") == "中国平安"


class TestInitConnection:
    def test_akshare_available(self):
        mock_ak = MagicMock()
        with patch.dict("sys.modules", {"akshare": mock_ak}):
            source = AKShareDataSource()
        assert source._connected is True
        assert source._ak is mock_ak

    def test_akshare_not_available(self):
        with patch.dict("sys.modules", {"akshare": None}):
            source = AKShareDataSource()
        assert source._connected is False


class TestEnsureConnected:
    def test_already_connected(self):
        with patch.dict("sys.modules", {"akshare": MagicMock()}):
            source = AKShareDataSource()
        assert source._ensure_connected() is True

    def test_not_connected(self):
        source = AKShareDataSource.__new__(AKShareDataSource)
        source._ak = None
        source._connected = False
        source._last_connect_time = None
        source._spot_cache = {}
        source._spot_cache_time = 0
        source._spot_cache_ttl = 60
        from utils.akshare_data_source import SourceHealthEntry
        source.source_health = {"akshare": SourceHealthEntry(ok=False, last_error=None, last_success=None)}
        with patch("builtins.__import__", side_effect=ImportError("no akshare")):
            assert source._ensure_connected() is False
