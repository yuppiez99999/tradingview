"""
单元测试: utils/astock_realtime.py
覆盖 _secid / _tx_prefix / get_eastmoney_quotes / get_tencent_quotes / get_realtime_quotes
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from utils import astock_realtime as ar


@pytest.fixture(autouse=True)
def _reset_cache():
    ar._cache.clear()
    ar._eastmoney_blocked = False
    yield
    ar._cache.clear()
    ar._eastmoney_blocked = False


class TestSecid:
    def test_sh_code(self):
        result = ar._secid("600519")
        assert result == "1.600519"

    def test_sz_code(self):
        result = ar._secid("000001")
        assert result == "0.000001"

    def test_with_suffix(self):
        result = ar._secid("600519.SH")
        assert "600519" in result


class TestTxPrefix:
    def test_sh_prefix(self):
        result = ar._tx_prefix("600519")
        assert result == "sh600519"

    def test_sz_prefix(self):
        result = ar._tx_prefix("000001")
        assert result == "sz000001"

    def test_with_suffix(self):
        result = ar._tx_prefix("600519.SH")
        assert result == "sh600519.SH"


class TestGetEastmoneyQuotes:
    def test_empty_codes(self):
        assert ar.get_eastmoney_quotes([]) == {}

    def test_blocked_returns_empty(self):
        ar._eastmoney_blocked = True
        assert ar.get_eastmoney_quotes(["600519"]) == {}

    def test_successful_response(self):
        mock_response = {
            "data": {
                "diff": [
                    {
                        "f12": "600519",
                        "f14": "贵州茅台",
                        "f43": 1800.0,
                        "f60": 1750.0,
                        "f116": 2260000000000,
                        "f162": 25.0,
                        "f164": 24.0,
                        "f167": 8.0,
                        "f168": 1925.0,
                        "f170": 1575.0,
                    }
                ]
            }
        }
        with patch("utils.astock_realtime._http_get") as mock_http:
            mock_http.return_value = json.dumps(mock_response).encode("utf-8")
            result = ar.get_eastmoney_quotes(["600519"])
        assert "600519" in result
        assert result["600519"]["price"] == 1800.0
        assert result["600519"]["pre_close"] == 1750.0
        assert result["600519"]["source"] == "eastmoney"

    def test_http_failure_sets_blocked(self):
        with patch("utils.astock_realtime._http_get", side_effect=RuntimeError("network")):
            result = ar.get_eastmoney_quotes(["600519"])
        assert result == {}
        assert ar._eastmoney_blocked is True

    def test_change_pct_calculation(self):
        mock_response = {
            "data": {
                "diff": [
                    {"f12": "000001", "f14": "平安银行", "f43": 11.0, "f60": 10.0}
                ]
            }
        }
        with patch("utils.astock_realtime._http_get") as mock_http:
            mock_http.return_value = json.dumps(mock_response).encode("utf-8")
            result = ar.get_eastmoney_quotes(["000001"])
        assert result["000001"]["change_pct"] == 10.0


class TestGetTencentQuotes:
    def test_empty_codes(self):
        assert ar.get_tencent_quotes([]) == {}

    def test_successful_response(self):
        fields = [""] * 50
        fields[1] = "平安银行"
        fields[3] = "11.0"
        fields[4] = "10.0"
        fields[32] = "10.0"
        fields[39] = "5.0"
        fields[45] = "2000.0"
        fields[46] = "2.0"
        mock_raw = f'v_sz000001="{"~".join(fields)}";'
        with patch("utils.astock_realtime._http_get") as mock_http:
            mock_http.return_value = mock_raw.encode("gbk")
            result = ar.get_tencent_quotes(["000001"])
        assert "000001" in result
        assert result["000001"]["price"] == 11.0
        assert result["000001"]["source"] == "tencent"

    def test_http_failure(self):
        with patch("utils.astock_realtime._http_get", side_effect=RuntimeError("fail")):
            result = ar.get_tencent_quotes(["000001"])
        assert result == {}


class TestGetRealtimeQuotes:
    def test_empty_codes(self):
        assert ar.get_realtime_quotes([]) == {}

    def test_eastmoney_success(self):
        with patch("utils.astock_realtime.get_eastmoney_quotes") as mock_em:
            mock_em.return_value = {"600519": {"price": 1800.0, "source": "eastmoney"}}
            result = ar.get_realtime_quotes(["600519"], use_cache=False)
        assert "600519" in result
        assert result["600519"]["price"] == 1800.0

    def test_fallback_to_tencent(self):
        with patch("utils.astock_realtime.get_eastmoney_quotes") as mock_em, \
             patch("utils.astock_realtime.get_tencent_quotes") as mock_tx:
            mock_em.return_value = {}
            mock_tx.return_value = {"600519": {"price": 1800.0, "source": "tencent"}}
            result = ar.get_realtime_quotes(["600519"], use_cache=False)
        assert "600519" in result
        assert result["600519"]["source"] == "tencent"

    def test_cache_hit(self):
        with patch("utils.astock_realtime.get_eastmoney_quotes") as mock_em:
            mock_em.return_value = {"600519": {"price": 1800.0}}
            ar.get_realtime_quotes(["600519"], use_cache=True)
            mock_em.return_value = {"600519": {"price": 9999.0}}
            result = ar.get_realtime_quotes(["600519"], use_cache=True)
        assert result["600519"]["price"] == 1800.0
        assert mock_em.call_count == 1

    def test_zero_price_triggers_fallback(self):
        with patch("utils.astock_realtime.get_eastmoney_quotes") as mock_em, \
             patch("utils.astock_realtime.get_tencent_quotes") as mock_tx:
            mock_em.return_value = {"600519": {"price": 0.0}}
            mock_tx.return_value = {"600519": {"price": 1800.0, "source": "tencent"}}
            result = ar.get_realtime_quotes(["600519"], use_cache=False)
        assert result["600519"]["price"] == 1800.0

    def test_strips_codes(self):
        with patch("utils.astock_realtime.get_eastmoney_quotes") as mock_em:
            mock_em.return_value = {}
            ar.get_realtime_quotes(["  600519  ", None, ""], use_cache=False)
        called_codes = mock_em.call_args[0][0]
        assert "600519" in called_codes
