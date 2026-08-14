"""G7 覆盖率冲刺 — data_provider.py 深度补测。

目标文件: utils/data_provider.py
目标覆盖: 25.63% -> 75%+
测试重点:
    1. 初始化 / 配置加载
    2. 数据获取流程（实时 / 历史）
    3. 缓存读写逻辑（内存 + 持久化）
    4. 异常处理 / 重试机制
    5. 多数据源切换
    6. 数据清洗 / 预处理
    7. 批量查询（便捷函数）
    8. 错误返回与日志

运行:
    python -m pytest tests/unit/test_g7_data_provider_boost.py -v
    python -m pytest tests/unit/test_g7_data_provider_boost.py --cov=utils/data_provider.py --cov-report=term-missing
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, call, patch

import pandas as pd
import pytest

from utils.data_provider import MarketDataProvider, _data_provider, get_market_data


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture()
def provider(tmp_path: Path) -> MarketDataProvider:
    """创建使用临时持久化缓存目录的 MarketDataProvider。"""
    with patch.object(MarketDataProvider, "__init__", lambda self, cache_size=1000, backtest_mode=False: None):
        provider = MarketDataProvider.__new__(MarketDataProvider)
        provider.cache_size = 1000
        provider.backtest_mode = False
        provider._backtest_date = None
        provider.data_cache = {}
        provider.cache_lock = __import__("threading").Lock()
        provider.persistent_cache_dir = tmp_path / "data_cache"
        provider.persistent_cache_dir.mkdir(parents=True, exist_ok=True)
        provider.source_health = {
            "wind_mcp": {"ok": False, "last_error": None},
            "tdx": {"ok": False, "last_error": None},
            "akshare": {"ok": False, "last_error": None},
            "sina_http": {"ok": False, "last_error": None},
        }
        provider.data_sources = {
            "real_time": {"enabled": True, "refresh_interval": 60, "last_update": None},
            "historical": {"enabled": True, "cache_days": 365, "update_frequency": "daily"},
            "sentiment": {"enabled": True, "refresh_interval": 300, "last_update": None},
        }
        provider._wind_mcp_client = None
        provider._tdx_source = None
        provider._akshare_source = None
        return provider


@pytest.fixture(autouse=True)
def _reset_global_data_provider():
    """避免全局单例污染测试。"""
    original = _data_provider
    try:
        yield
    finally:
        import utils.data_provider as dp
        dp._data_provider = original


# ============================================================
# 1. 初始化 / 配置加载
# ============================================================

class TestInitAndConfig:
    """初始化与配置加载覆盖。"""

    def test_init_default(self, tmp_path: Path) -> None:
        with patch.object(MarketDataProvider, "_init_wind_mcp"), \
             patch.object(MarketDataProvider, "_init_tdx"), \
             patch.object(MarketDataProvider, "_init_akshare"), \
             patch("utils.data_provider.make_no_proxy_session"), \
             patch.object(Path, "mkdir"):
            provider = MarketDataProvider()
            assert provider.cache_size == 1000
            assert provider.backtest_mode is False
            assert provider._backtest_date is None
            assert provider.data_cache == {}
            assert "wind_mcp" in provider.source_health
            assert provider._wind_mcp_client is None

    def test_init_custom_cache_size(self, tmp_path: Path) -> None:
        with patch.object(MarketDataProvider, "_init_wind_mcp"), \
             patch.object(MarketDataProvider, "_init_tdx"), \
             patch.object(MarketDataProvider, "_init_akshare"), \
             patch("utils.data_provider.make_no_proxy_session"), \
             patch.object(Path, "mkdir"):
            provider = MarketDataProvider(cache_size=500, backtest_mode=True)
            assert provider.cache_size == 500
            assert provider.backtest_mode is True

    def test_set_backtest_date(self, provider: MarketDataProvider) -> None:
        provider.set_backtest_date("2026-01-15")
        assert provider._backtest_date == "2026-01-15"

    def test_cache_suffix_normal(self, provider: MarketDataProvider) -> None:
        provider.backtest_mode = False
        assert provider._cache_suffix() == ""

    def test_cache_suffix_backtest_with_date(self, provider: MarketDataProvider) -> None:
        provider.backtest_mode = True
        provider._backtest_date = "2026-01-15"
        assert provider._cache_suffix() == "_2026-01-15"

    def test_cache_suffix_backtest_without_date(self, provider: MarketDataProvider) -> None:
        provider.backtest_mode = True
        provider._backtest_date = None
        assert provider._cache_suffix() == ""

    def test_source_health_default(self, provider: MarketDataProvider) -> None:
        expected_keys = {"wind_mcp", "tdx", "akshare", "sina_http"}
        assert set(provider.source_health.keys()) == expected_keys
        for entry in provider.source_health.values():
            assert entry == {"ok": False, "last_error": None}

    def test_data_sources_default(self, provider: MarketDataProvider) -> None:
        assert provider.data_sources["real_time"]["enabled"] is True
        assert provider.data_sources["historical"]["enabled"] is True
        assert provider.data_sources["sentiment"]["enabled"] is True

    def test_init_wind_mcp_missing_file(self, provider: MarketDataProvider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        monkeypatch.setattr(Path, "is_file", lambda self: False)
        with caplog.at_level(logging.WARNING):
            provider._init_wind_mcp()
        assert provider._wind_mcp_client is None
        assert provider.source_health["wind_mcp"]["ok"] is False
        assert "文件不存在" in provider.source_health["wind_mcp"]["last_error"]
        assert any("Wind MCP 文件不存在" in r.message for r in caplog.records)

    def test_init_wind_mcp_spec_failure(self, provider: MarketDataProvider, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        import importlib.util
        monkeypatch.setattr(Path, "is_file", lambda self: True)
        monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda *args, **kwargs: None)
        with caplog.at_level(logging.WARNING):
            provider._init_wind_mcp()
        assert provider._wind_mcp_client is None
        assert "无法创建 importlib spec" in provider.source_health["wind_mcp"]["last_error"]

    def test_init_wind_mcp_missing_functions(self, provider: MarketDataProvider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        fake_module = type(sys)("fake_wind_mcp_fetcher")
        fake_module.wind_get_quote = lambda *args, **kwargs: {}
        monkeypatch.setitem(sys.modules, "wind_mcp_fetcher", fake_module)
        fake_spec = MagicMock()
        fake_spec.loader = MagicMock()
        monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda *args, **kwargs: fake_spec)
        monkeypatch.setattr(importlib.util, "module_from_spec", lambda spec: fake_module)
        with caplog.at_level(logging.WARNING):
            provider._init_wind_mcp()
        assert provider._wind_mcp_client is None
        assert "模块缺少" in provider.source_health["wind_mcp"]["last_error"]

    def test_init_wind_mcp_load_exception(self, provider: MarketDataProvider, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        def boom(*args, **kwargs):
            raise RuntimeError("load boom")
        monkeypatch.setattr(Path, "is_file", lambda self: True)
        monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda *args, **kwargs: boom())
        with caplog.at_level(logging.WARNING):
            provider._init_wind_mcp()
        assert provider._wind_mcp_client is None
        assert "load boom" in provider.source_health["wind_mcp"]["last_error"]

    def test_init_tdx_import_error(self, provider: MarketDataProvider, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        monkeypatch.setitem(sys.modules, "utils.tdx_data_source", None)
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "utils.tdx_data_source":
                raise ImportError("no tdx")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with caplog.at_level(logging.WARNING):
            provider._init_tdx()
        assert provider._tdx_source is None
        assert "模块导入失败" in provider.source_health["tdx"]["last_error"]

    def test_init_tdx_init_failure(self, provider: MarketDataProvider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        fake_module = type(sys)("utils.tdx_data_source")
        fake_tdx = MagicMock()
        fake_tdx.source_health = {"tdx": {"ok": False}}
        fake_module.get_tdx_source = lambda: fake_tdx
        monkeypatch.setitem(sys.modules, "utils.tdx_data_source", fake_module)
        with caplog.at_level(logging.WARNING):
            provider._init_tdx()
        assert provider._tdx_source is fake_tdx
        assert provider.source_health["tdx"]["ok"] is False

    def test_init_tdx_runtime_error(self, provider: MarketDataProvider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        fake_module = type(sys)("utils.tdx_data_source")
        fake_module.get_tdx_source = lambda: (_ for _ in ()).throw(RuntimeError("tdx boom"))
        monkeypatch.setitem(sys.modules, "utils.tdx_data_source", fake_module)
        with caplog.at_level(logging.WARNING):
            provider._init_tdx()
        assert "tdx boom" in provider.source_health["tdx"]["last_error"]

    def test_init_akshare_import_error(self, provider: MarketDataProvider, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "utils.akshare_data_source":
                raise ImportError("no akshare")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with caplog.at_level(logging.WARNING):
            provider._init_akshare()
        assert provider._akshare_source is None
        assert "模块导入失败" in provider.source_health["akshare"]["last_error"]

    def test_init_akshare_success(self, provider: MarketDataProvider, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        fake_module = type(sys)("utils.akshare_data_source")
        fake_ak = MagicMock()
        fake_ak.source_health = {"akshare": {"ok": True}}
        fake_module.get_akshare_source = lambda: fake_ak
        monkeypatch.setitem(sys.modules, "utils.akshare_data_source", fake_module)
        with caplog.at_level(logging.INFO):
            provider._init_akshare()
        assert provider._akshare_source is fake_ak
        assert provider.source_health["akshare"]["ok"] is True


# ============================================================
# 2. 代码转换与工具静态方法
# ============================================================

class TestCodeMapping:
    """_to_wind_code / _is_fund / _to_sina_code 分支覆盖。"""

    @pytest.mark.parametrize("symbol,expected", [
        ("sh600519", "600519.SH"),
        ("SH600519", "600519.SH"),
        ("sz000858", "000858.SZ"),
        ("bj300001", "300001.SZ"),
        ("600519.SH", "600519.SH"),
        ("000858.SZ", "000858.SZ"),
        ("300001.BJ", "300001.SZ"),
        ("510300", "510300.SH"),
        ("159915", "159915.SZ"),
        ("150001", "150001.SZ"),
        ("4xxxx", "4xxxx.BJ"),
        ("8xxxx", "8xxxx.BJ"),
        ("", ""),
    ])
    def test_to_wind_code(self, symbol: str, expected: str) -> None:
        result = MarketDataProvider._to_wind_code(symbol)
        assert result == expected

    @pytest.mark.parametrize("symbol,expected", [
        ("159915", True),
        ("510300", True),
        ("150001", True),
        ("160xxx", True),
        ("600519", False),
        ("000001", False),
        ("300001", False),
        ("", False),
    ])
    def test_is_fund(self, symbol: str, expected: bool) -> None:
        assert MarketDataProvider._is_fund(symbol) is expected

    @pytest.mark.parametrize("symbol,expected", [
        ("sh600519", "sh600519"),
        ("600519.SH", "600519"),
        ("510300", "sh510300"),
        ("159915", "sz159915"),
        ("000858", "sz000858"),
        ("6xxxx", "sh6xxxx"),
        ("4xxxx", "bj4xxxx"),
    ])
    def test_to_sina_code(self, symbol: str, expected: str) -> None:
        result = MarketDataProvider._to_sina_code(symbol)
        assert result == expected


# ============================================================
# 3. 实时数据获取流程
# ============================================================

class TestRealtimeFlow:
    """实时数据获取与多数据源 fallback。"""

    def test_fetch_real_time_data_wind_success(self, provider: MarketDataProvider) -> None:
        provider._wind_mcp_client = {"quote": lambda *args, **kwargs: {"price": 100.0, "prev_close": 99.0, "open": 99.5, "high": 101.0, "low": 99.0, "volume": 1000}}
        data = provider._fetch_real_time_data("600519")
        assert data["source"] == "wind_mcp"
        assert data["index_price"] == 100.0

    def test_fetch_real_time_data_wind_empty(self, provider: MarketDataProvider) -> None:
        provider._wind_mcp_client = {"quote": lambda *args, **kwargs: {}}
        provider._tdx_source = None
        provider._akshare_source = None
        fake_resp = MagicMock()
        fake_resp.text = 'var hq_str_sh600519="";'
        fake_resp.encoding = "gbk"
        with patch("utils.data_provider._SINA_SESSION") as mock_session:
            mock_session.get.return_value = fake_resp
            with pytest.raises(RuntimeError, match="所有数据源获取实时数据失败"):
                provider._fetch_real_time_data("600519")

    def test_fetch_real_time_data_wind_exception(self, provider: MarketDataProvider) -> None:
        provider._wind_mcp_client = {"quote": lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("wind error"))}
        provider._tdx_source = None
        provider._akshare_source = None
        fake_resp = MagicMock()
        fake_resp.text = 'var hq_str_sh600519="";'
        fake_resp.encoding = "gbk"
        with patch("utils.data_provider._SINA_SESSION") as mock_session:
            mock_session.get.return_value = fake_resp
            with pytest.raises(RuntimeError, match="获取实时数据失败"):
                provider._fetch_real_time_data("600519")

    def test_fetch_real_time_data_tdx_success(self, provider: MarketDataProvider) -> None:
        provider._wind_mcp_client = {"quote": lambda *args, **kwargs: {}}
        tdx = MagicMock()
        tdx.get_realtime_quote.return_value = {"index_price": 100.0, "prev_close": 99.0}
        provider._tdx_source = tdx
        data = provider._fetch_real_time_data("600519")
        assert data["source"] == "tdx"
        assert data["index_price"] == 100.0

    def test_fetch_real_time_data_akshare_success(self, provider: MarketDataProvider) -> None:
        provider._wind_mcp_client = {"quote": lambda *args, **kwargs: {}}
        provider._tdx_source = None
        akshare = MagicMock()
        akshare.get_realtime_quote.return_value = {"index_price": 100.0}
        provider._akshare_source = akshare
        data = provider._fetch_real_time_data("600519")
        assert data["source"] == "akshare"

    def test_fetch_real_time_data_sina_success(self, provider: MarketDataProvider) -> None:
        sina_data = {"source": "sina_http", "name": "贵州茅台", "index_price": 100.0}
        provider._wind_mcp_client = {"quote": lambda *args, **kwargs: {}}
        provider._tdx_source = None
        provider._akshare_source = None
        with patch.object(provider, "_try_sina_http_realtime", return_value=sina_data) as mock_sina:
            data = provider._fetch_real_time_data("600519")
        assert data["source"] == "sina_http"
        assert data["name"] == "贵州茅台"
        mock_sina.assert_called_once_with("600519")

    def test_fetch_real_time_data_sina_empty_payload(self, provider: MarketDataProvider) -> None:
        provider._wind_mcp_client = {"quote": lambda *args, **kwargs: {}}
        provider._tdx_source = None
        provider._akshare_source = None
        fake_resp = MagicMock()
        fake_resp.text = 'var hq_str_sh600519="";'
        fake_resp.encoding = "gbk"
        with patch("utils.data_provider._SINA_SESSION") as mock_session:
            mock_session.get.return_value = fake_resp
            with pytest.raises(RuntimeError, match="所有数据源获取实时数据失败"):
                provider._fetch_real_time_data("600519")

    def test_try_wind_mcp_realtime_success(self, provider: MarketDataProvider) -> None:
        provider._wind_mcp_client = {"quote": lambda *args, **kwargs: {"price": 100.0, "prev_close": 99.0}}
        data = provider._try_wind_mcp_realtime("600519")
        assert data is not None
        assert data["source"] == "wind_mcp"
        assert provider.source_health["wind_mcp"]["ok"] is True

    def test_try_wind_mcp_realtime_empty(self, provider: MarketDataProvider) -> None:
        provider._wind_mcp_client = {"quote": lambda *args, **kwargs: {}}
        data = provider._try_wind_mcp_realtime("600519")
        assert data is None
        assert provider.source_health["wind_mcp"]["last_error"] == "empty_quote"

    def test_try_wind_mcp_realtime_exception(self, provider: MarketDataProvider) -> None:
        provider._wind_mcp_client = {"quote": lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))}
        data = provider._try_wind_mcp_realtime("600519")
        assert data is None
        assert provider.source_health["wind_mcp"]["ok"] is False
        assert "boom" in provider.source_health["wind_mcp"]["last_error"]

    def test_try_tdx_realtime_success(self, provider: MarketDataProvider) -> None:
        tdx = MagicMock()
        tdx.get_realtime_quote.return_value = {"index_price": 100.0, "prev_close": 99.0}
        provider._tdx_source = tdx
        data = provider._try_tdx_realtime("600519")
        assert data is not None
        assert data["source"] == "tdx"

    def test_try_tdx_realtime_empty(self, provider: MarketDataProvider) -> None:
        provider._tdx_source = MagicMock()
        provider._tdx_source.get_realtime_quote.return_value = {}
        data = provider._try_tdx_realtime("600519")
        assert data is None

    def test_try_akshare_realtime_success(self, provider: MarketDataProvider) -> None:
        akshare = MagicMock()
        akshare.get_realtime_quote.return_value = {"index_price": 100.0}
        provider._akshare_source = akshare
        data = provider._try_akshare_realtime("600519")
        assert data is not None
        assert data["source"] == "akshare"

    def test_try_sina_http_realtime_success(self, provider: MarketDataProvider) -> None:
        fake_resp = MagicMock()
        fake_resp.text = 'var hq_str_sh600519="贵州茅台,100.0,99.0,100.0,101.0,99.0,100.0,1000,1000000,0";'
        fake_resp.encoding = "gbk"
        with patch("utils.data_provider._SINA_SESSION") as mock_session:
            mock_session.get.return_value = fake_resp
            data = provider._try_sina_http_realtime("600519")
        assert data is not None
        assert data["source"] == "sina_http"
        assert data["name"] == "贵州茅台"

    def test_try_sina_http_realtime_zero_price(self, provider: MarketDataProvider) -> None:
        fake_resp = MagicMock()
        fake_resp.text = 'var hq_str_sh600519="贵州茅台,0.0,99.0,0.0,101.0,99.0,0.0,0,0,0";'
        fake_resp.encoding = "gbk"
        with patch("utils.data_provider._SINA_SESSION") as mock_session:
            mock_session.get.return_value = fake_resp
            data = provider._try_sina_http_realtime("600519")
        assert data is None
        assert provider.source_health["sina_http"]["last_error"] == "zero_price"

    def test_try_sina_http_realtime_insufficient_fields(self, provider: MarketDataProvider) -> None:
        fake_resp = MagicMock()
        fake_resp.text = 'var hq_str_sh600519="贵州茅台,100.0";'
        fake_resp.encoding = "gbk"
        with patch("utils.data_provider._SINA_SESSION") as mock_session:
            mock_session.get.return_value = fake_resp
            data = provider._try_sina_http_realtime("600519")
        assert data is None
        assert provider.source_health["sina_http"]["last_error"] == "insufficient_fields"

    def test_try_sina_http_realtime_no_match(self, provider: MarketDataProvider) -> None:
        fake_resp = MagicMock()
        fake_resp.text = 'var hq_str_sh600519="";'
        fake_resp.encoding = "gbk"
        with patch("utils.data_provider._SINA_SESSION") as mock_session:
            mock_session.get.return_value = fake_resp
            data = provider._try_sina_http_realtime("600519")
        assert data is None
        assert provider.source_health["sina_http"]["last_error"] == "insufficient_fields"

    def test_get_market_data_cache_hit(self, provider: MarketDataProvider) -> None:
        provider.data_cache["market_600519"] = {
            "data": {"index_price": 100.0},
            "timestamp": datetime.now(),
        }
        data = provider.get_market_data("600519")
        assert data["index_price"] == 100.0

    def test_get_market_data_cache_expired(self, provider: MarketDataProvider) -> None:
        provider.data_cache["market_600519"] = {
            "data": {"index_price": 100.0},
            "timestamp": datetime.now() - timedelta(seconds=120),
        }
        with patch.object(provider, "_fetch_real_time_data", return_value={"index_price": 101.0}) as mock_fetch:
            data = provider.get_market_data("600519")
        assert data["index_price"] == 101.0
        mock_fetch.assert_called_once_with("600519")

    def test_get_market_data_fetch_failure(self, provider: MarketDataProvider) -> None:
        with patch.object(provider, "_fetch_real_time_data", side_effect=RuntimeError("fail")):
            with pytest.raises(RuntimeError, match="fail"):
                provider.get_market_data("600519")

    def test_get_market_data_default_symbol(self, provider: MarketDataProvider) -> None:
        with patch.object(provider, "_fetch_real_time_data", return_value={"index_price": 3000.0}) as mock_fetch:
            data = provider.get_market_data()
        assert data["index_price"] == 3000.0
        mock_fetch.assert_called_once_with("SPY")


# ============================================================
# 4. 历史数据获取流程
# ============================================================

class TestHistoricalFlow:
    """历史数据获取与多数据源 fallback。"""

    def test_fetch_historical_data_wind_success(self, provider: MarketDataProvider) -> None:
        provider._wind_mcp_client = {"kline": lambda *args, **kwargs: [
            {"date": "2026-01-01", "close": 100.0, "open": 99.0, "high": 101.0, "low": 99.0, "volume": 1000}
        ]}
        df = provider._fetch_historical_data("600519", "1y")
        assert df is not None
        assert not df.empty

    def test_fetch_historical_data_wind_empty(self, provider: MarketDataProvider) -> None:
        provider._wind_mcp_client = {"kline": lambda *args, **kwargs: []}
        provider._tdx_source = None
        provider._akshare_source = None
        fake_resp = MagicMock()
        fake_resp.text = "null"
        fake_resp.raise_for_status.return_value = None
        with patch("utils.data_provider._SINA_SESSION") as mock_session:
            mock_session.get.return_value = fake_resp
            with pytest.raises(RuntimeError, match="所有数据源获取历史数据失败"):
                provider._fetch_historical_data("600519", "1y")

    def test_fetch_historical_data_tdx_success(self, provider: MarketDataProvider) -> None:
        provider._wind_mcp_client = {"kline": lambda *args, **kwargs: []}
        tdx = MagicMock()
        tdx.get_historical_klines.return_value = pd.DataFrame({"close": [100.0, 101.0]})
        provider._tdx_source = tdx
        df = provider._fetch_historical_data("600519", "1y")
        assert df is not None

    def test_fetch_historical_data_akshare_success(self, provider: MarketDataProvider) -> None:
        provider._wind_mcp_client = {"kline": lambda *args, **kwargs: []}
        provider._tdx_source = None
        akshare = MagicMock()
        akshare.get_historical_klines.return_value = pd.DataFrame({"close": [100.0]})
        provider._akshare_source = akshare
        df = provider._fetch_historical_data("600519", "1y")
        assert df is not None

    def test_fetch_historical_data_sina_success(self, provider: MarketDataProvider) -> None:
        provider._wind_mcp_client = {"kline": lambda *args, **kwargs: []}
        provider._tdx_source = None
        provider._akshare_source = None
        payload = json.dumps([
            {"day": "2026-01-01", "open": "100.0", "high": "101.0", "low": "99.0", "close": "100.0", "volume": "1000"}
        ])
        fake_resp = MagicMock()
        fake_resp.text = payload
        fake_resp.raise_for_status.return_value = None
        with patch("utils.data_provider._SINA_SESSION") as mock_session:
            mock_session.get.return_value = fake_resp
            df = provider._fetch_historical_data("600519", "1y")
        assert df is not None
        assert not df.empty

    def test_fetch_historical_data_sina_null_payload(self, provider: MarketDataProvider) -> None:
        provider._wind_mcp_client = {"kline": lambda *args, **kwargs: []}
        provider._tdx_source = None
        provider._akshare_source = None
        fake_resp = MagicMock()
        fake_resp.text = "null"
        fake_resp.raise_for_status.return_value = None
        with patch("utils.data_provider._SINA_SESSION") as mock_session:
            mock_session.get.return_value = fake_resp
            with pytest.raises(RuntimeError, match="所有数据源获取历史数据失败"):
                provider._fetch_historical_data("600519", "1y")

    def test_try_wind_mcp_historical_success(self, provider: MarketDataProvider) -> None:
        provider._wind_mcp_client = {"kline": lambda *args, **kwargs: [
            {"date": "2026-01-01", "close": 100.0, "volume": 1000}
        ]}
        df = provider._try_wind_mcp_historical("600519", "1y")
        assert df is not None
        assert not df.empty

    def test_try_wind_mcp_historical_empty_after_parse(self, provider: MarketDataProvider) -> None:
        provider._wind_mcp_client = {"kline": lambda *args, **kwargs: [
            {"date": "2026-01-01", "close": 0.0, "volume": 0}
        ]}
        df = provider._try_wind_mcp_historical("600519", "1y")
        assert df is None

    def test_try_tdx_historical_success(self, provider: MarketDataProvider) -> None:
        tdx = MagicMock()
        tdx.get_historical_klines.return_value = pd.DataFrame({"close": [100.0]})
        provider._tdx_source = tdx
        df = provider._try_tdx_historical("600519", "1y")
        assert df is not None

    def test_try_akshare_historical_success(self, provider: MarketDataProvider) -> None:
        akshare = MagicMock()
        akshare.get_historical_klines.return_value = pd.DataFrame({"close": [100.0]})
        provider._akshare_source = akshare
        df = provider._try_akshare_historical("600519", "1y")
        assert df is not None

    def test_try_sina_http_historical_success(self, provider: MarketDataProvider) -> None:
        payload = json.dumps([{"day": "2026-01-01", "close": "100.0", "volume": "1000"}])
        fake_resp = MagicMock()
        fake_resp.text = payload
        fake_resp.raise_for_status.return_value = None
        with patch("utils.data_provider._SINA_SESSION") as mock_session:
            mock_session.get.return_value = fake_resp
            df = provider._try_sina_http_historical("600519", "1y")
        assert df is not None
        assert not df.empty

    def test_get_historical_data_cache_hit(self, provider: MarketDataProvider) -> None:
        now = datetime.now()
        provider.data_cache["historical_600519_1y"] = {
            "data": pd.DataFrame({"close": [100.0]}),
            "timestamp": now,
        }
        df = provider.get_historical_data("600519", "1y")
        assert not df.empty

    def test_get_historical_data_persistent_cache_hit(self, provider: MarketDataProvider) -> None:
        pytest.importorskip("pyarrow")
        pd.DataFrame({"close": [100.0]}).to_parquet(provider.persistent_cache_dir / "historical_600519_1y.parquet", index=True)
        df = provider.get_historical_data("600519", "1y")
        assert not df.empty

    def test_get_historical_data_persistent_cache_expired(self, provider: MarketDataProvider) -> None:
        pytest.importorskip("pyarrow")
        cache_file = provider.persistent_cache_dir / "historical_600519_1y.parquet"
        pd.DataFrame({"close": [100.0]}).to_parquet(cache_file, index=True)
        old_time = time.time() - 86400 * 2
        import os
        os.utime(cache_file, (old_time, old_time))
        with patch.object(provider, "_fetch_historical_data", return_value=pd.DataFrame({"close": [101.0]})) as mock_fetch:
            df = provider.get_historical_data("600519", "1y")
        assert not df.empty
        mock_fetch.assert_called_once_with("600519", "1y")

    def test_get_historical_data_fetch_failure(self, provider: MarketDataProvider) -> None:
        with patch.object(provider, "_fetch_historical_data", side_effect=RuntimeError("hist fail")):
            with pytest.raises(RuntimeError, match="hist fail"):
                provider.get_historical_data("600519", "1y")


# ============================================================
# 5. 缓存读写逻辑（内存 + 持久化）
# ============================================================

class TestCacheLogic:
    """缓存读写、过期、淘汰、持久化。"""

    def test_memory_cache_write_and_evict(self, provider: MarketDataProvider) -> None:
        provider.cache_size = 2
        for i in range(3):
            with patch.object(provider, "_fetch_real_time_data", return_value={"index_price": 100.0 + i}):
                provider.get_market_data(f"symbol{i}")
        assert len(provider.data_cache) <= provider.cache_size

    def test_persistent_cache_write_and_read(self, provider: MarketDataProvider) -> None:
        pytest.importorskip("pyarrow")
        df = pd.DataFrame({"close": [100.0, 101.0]})
        provider._save_persistent_cache("test_key", df)
        cache_file = provider.persistent_cache_dir / "test_key.parquet"
        assert cache_file.exists()
        loaded = provider._load_persistent_cache("test_key")
        assert loaded is not None
        assert list(loaded["close"]) == [100.0, 101.0]

    def test_persistent_cache_skip_none(self, provider: MarketDataProvider) -> None:
        provider._save_persistent_cache("none_key", None)
        assert not (provider.persistent_cache_dir / "none_key.parquet").exists()

    def test_persistent_cache_skip_empty(self, provider: MarketDataProvider) -> None:
        pytest.importorskip("pyarrow")
        provider._save_persistent_cache("empty_key", pd.DataFrame())
        assert not (provider.persistent_cache_dir / "empty_key.parquet").exists()

    def test_persistent_cache_expired_deleted(self, provider: MarketDataProvider) -> None:
        cache_file = provider.persistent_cache_dir / "expire_key.parquet"
        cache_file.write_text("fake")
        old_time = time.time() - 86400 * 2
        import os
        os.utime(cache_file, (old_time, old_time))
        result = provider._load_persistent_cache("expire_key")
        assert result is None
        assert not cache_file.exists()

    def test_persistent_cache_corrupt_file(self, provider: MarketDataProvider, caplog: pytest.LogCaptureFixture) -> None:
        pytest.importorskip("pyarrow")
        cache_file = provider.persistent_cache_dir / "corrupt.parquet"
        cache_file.write_text("not_parquet")
        with caplog.at_level(logging.DEBUG):
            result = provider._load_persistent_cache("corrupt")
        assert result is None
        assert any("加载持久化缓存失败" in r.message for r in caplog.records)

    def test_clear_cache(self, provider: MarketDataProvider, caplog: pytest.LogCaptureFixture) -> None:
        provider.data_cache["x"] = {"data": {}, "timestamp": datetime.now()}
        with caplog.at_level(logging.INFO):
            provider.clear_cache()
        assert provider.data_cache == {}
        assert any("数据缓存已清除" in r.message for r in caplog.records)

    def test_get_cache_info(self, provider: MarketDataProvider) -> None:
        provider.data_cache["x"] = {"data": {}, "timestamp": datetime.now()}
        info = provider.get_cache_info()
        assert info["cache_size"] == 1
        assert info["max_cache_size"] == 1000
        assert "x" in info["cached_items"]


# ============================================================
# 6. 情绪数据与技术指标
# ============================================================

class TestSentimentAndTechnical:
    """情绪数据、技术指标、默认废弃方法。"""

    def test_get_sentiment_data_none(self, provider: MarketDataProvider, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            data = provider.get_sentiment_data("600519")
        assert data is None
        assert any("无真实情绪数据源可用" in r.message for r in caplog.records)

    def test_get_sentiment_data_cache_hit(self, provider: MarketDataProvider) -> None:
        provider.data_cache["sentiment_600519"] = {
            "data": {"score": 0.8},
            "timestamp": datetime.now(),
        }
        data = provider.get_sentiment_data("600519")
        assert data["score"] == 0.8

    def test_get_technical_indicators_success(self, provider: MarketDataProvider) -> None:
        hist = pd.DataFrame({
            "close": [100.0 + (i % 20) for i in range(60)],
            "volume": [1000.0 for _ in range(60)],
        })
        with patch.object(provider, "get_historical_data", return_value=hist):
            indicators = provider.get_technical_indicators("600519")
        assert indicators
        assert "ma20" in indicators
        assert "rsi" in indicators
        assert "macd" in indicators

    def test_get_technical_indicators_short_data(self, provider: MarketDataProvider) -> None:
        hist = pd.DataFrame({"close": [100.0], "volume": [1000.0]})
        with patch.object(provider, "get_historical_data", return_value=hist):
            indicators = provider.get_technical_indicators("600519")
        assert indicators == {}

    def test_get_technical_indicators_failure(self, provider: MarketDataProvider, caplog: pytest.LogCaptureFixture) -> None:
        with patch.object(provider, "get_historical_data", side_effect=OSError("boom")):
            with caplog.at_level(logging.ERROR):
                indicators = provider.get_technical_indicators("600519")
        assert indicators == {}
        assert any("获取技术指标失败" in r.message for r in caplog.records)

    def test_deprecated_market_data_raises(self, provider: MarketDataProvider) -> None:
        with pytest.raises(RuntimeError, match="已废弃"):
            provider._get_default_market_data()

    def test_deprecated_historical_data_raises(self, provider: MarketDataProvider) -> None:
        with pytest.raises(RuntimeError, match="已废弃"):
            provider._get_default_historical_data()

    def test_deprecated_sentiment_data_raises(self, provider: MarketDataProvider) -> None:
        with pytest.raises(RuntimeError, match="已废弃"):
            provider._get_default_sentiment_data()


# ============================================================
# 7. 技术指标计算细节
# ============================================================

class TestTechnicalIndicatorsDetail:
    """_calculate_technical_indicators / _calculate_ema 边界。"""

    def test_calculate_ema_short(self, provider: MarketDataProvider) -> None:
        assert provider._calculate_ema([100.0, 101.0], 5) == 100.5

    def test_calculate_ema_normal(self, provider: MarketDataProvider) -> None:
        prices = [100.0 + i for i in range(20)]
        ema = provider._calculate_ema(prices, 12)
        assert isinstance(ema, float)

    def test_calculate_technical_indicators_full(self, provider: MarketDataProvider) -> None:
        prices = [100.0 + (i % 5) for i in range(200)]
        volumes = [1000.0 for _ in range(200)]
        data = pd.DataFrame({"close": prices, "volume": volumes})
        indicators = provider._calculate_technical_indicators(data)
        assert "ma20" in indicators
        assert "bb_upper" in indicators
        assert "bb_lower" in indicators
        assert "trend" in indicators

    def test_calculate_technical_indicators_exception(self, provider: MarketDataProvider, caplog: pytest.LogCaptureFixture) -> None:
        bad_data = pd.DataFrame({"close": ["nan", "nan"], "volume": [1000, 1000]})
        with caplog.at_level(logging.ERROR):
            result = provider._calculate_technical_indicators(bad_data)
        assert result == {}


# ============================================================
# 8. 便捷函数 / 扩展能力
# ============================================================

class TestModuleHelpers:
    """便捷函数、外部模块能力、HFQ 富化。"""

    def test_get_market_data_convenience(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_provider = MagicMock()
        fake_provider.get_market_data.return_value = {"index_price": 100.0}
        monkeypatch.setattr("utils.data_provider._data_provider", fake_provider, raising=False)
        import utils.data_provider as dp_mod
        dp_mod._data_provider = fake_provider
        data = get_market_data("600519")
        assert data["index_price"] == 100.0
        fake_provider.get_market_data.assert_called_once_with("600519")

    def test_get_historical_data_convenience(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_provider = MagicMock()
        fake_provider.get_historical_data.return_value = pd.DataFrame({"close": [100.0]})
        import utils.data_provider as dp_mod
        dp_mod._data_provider = fake_provider
        from utils.data_provider import get_historical_data
        df = get_historical_data("600519", "1y")
        assert not df.empty

    def test_get_price_prediction_short_history(self, provider: MarketDataProvider, caplog: pytest.LogCaptureFixture) -> None:
        hist = pd.DataFrame({"close": [100.0, 101.0]})
        with patch.object(provider, "get_historical_data", return_value=hist):
            with caplog.at_level(logging.WARNING):
                result = provider.get_price_prediction("600519", horizon=5)
        assert result == {}

    def test_get_price_prediction_failure(self, provider: MarketDataProvider, caplog: pytest.LogCaptureFixture) -> None:
        hist = pd.DataFrame({"close": [100.0 + i for i in range(40)]})
        fake_predictor = MagicMock()
        fake_predictor.predict.side_effect = ValueError("predict fail")
        with patch.object(provider, "get_historical_data", return_value=hist):
            with patch.dict(sys.modules, {"utils.tf_price_predictor": MagicMock(PricePredictor=MagicMock(return_value=fake_predictor))}):
                with caplog.at_level(logging.WARNING):
                    result = provider.get_price_prediction("600519", horizon=5)
        assert result == {}

    def test_get_external_macro_failure(self, provider: MarketDataProvider, caplog: pytest.LogCaptureFixture) -> None:
        with patch("utils.external_data_source.ExternalDataManager", side_effect=RuntimeError("ext fail")):
            with caplog.at_level(logging.WARNING):
                result = provider.get_external_macro()
        assert result == {}

    def test_get_risk_sentiment_failure(self, provider: MarketDataProvider, caplog: pytest.LogCaptureFixture) -> None:
        with patch("utils.external_data_source.ExternalDataManager", side_effect=RuntimeError("risk fail")):
            with caplog.at_level(logging.WARNING):
                result = provider.get_risk_sentiment()
        assert result == {}

    def test_get_news_sentiment_success(self, provider: MarketDataProvider) -> None:
        fake_news = [MagicMock(to_dict=MagicMock(return_value={"title": "news"}))]
        fake_sentiments = [MagicMock(score=0.8)]
        with patch.dict(sys.modules, {
            "utils.web_scraper": MagicMock(WebScraper=MagicMock(return_value=MagicMock(fetch_announcements=MagicMock(return_value=fake_news)))),
            "utils.ai_report_agent": MagicMock(AIReportAgent=MagicMock(return_value=MagicMock(analyze_news_sentiment=MagicMock(return_value=fake_sentiments)))),
        }):
            result = provider.get_news_sentiment("600519", limit=5)
        assert isinstance(result, list)

    def test_get_news_sentiment_not_list(self, provider: MarketDataProvider) -> None:
        with patch.dict(sys.modules, {
            "utils.web_scraper": MagicMock(WebScraper=MagicMock(return_value=MagicMock(fetch_announcements=MagicMock(return_value="bad")))),
        }):
            result = provider.get_news_sentiment("600519")
        assert result == []

    def test_get_ai_daily_report_success(self, provider: MarketDataProvider) -> None:
        fake_report = MagicMock()
        fake_report.__dict__ = {"summary": "ok"}
        with patch.dict(sys.modules, {
            "utils.ai_report_agent": MagicMock(AIReportAgent=MagicMock(return_value=MagicMock(generate_daily_report=MagicMock(return_value=fake_report)))),
        }):
            result = provider.get_ai_daily_report(["600519"])
        assert result == {"summary": "ok"}

    def test_get_extended_status(self, provider: MarketDataProvider, monkeypatch: pytest.MonkeyPatch) -> None:
        provider.source_health["wind_mcp"]["ok"] = True
        provider.data_cache["x"] = {"data": {}, "timestamp": datetime.now()}
        fake_mod = MagicMock()
        monkeypatch.setitem(sys.modules, "utils.tf_price_predictor", fake_mod)
        monkeypatch.setitem(sys.modules, "utils.external_data_source", fake_mod)
        monkeypatch.setitem(sys.modules, "utils.web_scraper", fake_mod)
        monkeypatch.setitem(sys.modules, "utils.ai_report_agent", fake_mod)
        status = provider.get_extended_status()
        assert status["data_sources"]["wind_mcp"] is True
        assert status["cache"]["cache_size"] == 1

    def test_get_hfq_factor_success(self, provider: MarketDataProvider) -> None:
        fake_provider = MagicMock()
        fake_provider.get_hfq_factor.return_value = 1.25
        with patch.dict(sys.modules, {"utils.adjust_factor_provider": MagicMock(get_adjust_factor_provider=MagicMock(return_value=fake_provider))}):
            factor = provider.get_hfq_factor("600519", date="2026-01-01")
        assert factor == 1.25

    def test_get_hfq_factor_failure(self, provider: MarketDataProvider) -> None:
        with patch.dict(sys.modules, {"utils.adjust_factor_provider": MagicMock(get_adjust_factor_provider=MagicMock(side_effect=RuntimeError("hfq fail")))}):
            factor = provider.get_hfq_factor("600519")
        assert factor == 1.0

    def test_enrich_realtime_with_hfq_success(self, provider: MarketDataProvider) -> None:
        quote = {"index_price": 100.0}
        fake_provider = MagicMock()
        fake_provider.get_hfq_factor.return_value = 1.2
        fake_provider.is_ex_dividend_date.return_value = False
        with patch.dict(sys.modules, {
            "utils.adjust_factor_provider": MagicMock(
                get_adjust_factor_provider=MagicMock(return_value=fake_provider),
                unadjusted_to_hfq=MagicMock(side_effect=lambda price, factor: price * factor),
            )
        }):
            enriched = provider.enrich_realtime_with_hfq(quote, "600519")
        assert enriched["hfq_factor"] == 1.2
        assert enriched["hfq_equivalent_price"] == 120.0

    def test_enrich_realtime_with_hfq_failure(self, provider: MarketDataProvider) -> None:
        quote = {"index_price": 100.0}
        with patch.dict(sys.modules, {"utils.adjust_factor_provider": MagicMock(get_adjust_factor_provider=MagicMock(side_effect=RuntimeError("hfq enrich fail")))}):
            enriched = provider.enrich_realtime_with_hfq(quote, "600519")
        assert enriched["hfq_factor"] == 1.0
        assert enriched["hfq_equivalent_price"] == 100.0


# ============================================================
# 9. 日志与错误返回
# ============================================================

class TestErrorLogging:
    """错误返回格式与日志触发。"""

    def test_fetch_real_time_data_logs_all_sources_failed(self, provider: MarketDataProvider, caplog: pytest.LogCaptureFixture) -> None:
        provider._wind_mcp_client = {"quote": lambda *args, **kwargs: {}}
        provider._tdx_source = None
        provider._akshare_source = None
        fake_resp = MagicMock()
        fake_resp.text = 'var hq_str_sh600519="";'
        fake_resp.encoding = "gbk"
        with patch("utils.data_provider._SINA_SESSION") as mock_session, caplog.at_level(logging.WARNING):
            mock_session.get.return_value = fake_resp
            with pytest.raises(RuntimeError):
                provider._fetch_real_time_data("600519")
        assert any("Wind MCP 实时数据获取失败" in r.message for r in caplog.records)
        assert any("通达信实时数据获取失败" in r.message for r in caplog.records)
        assert any("AKShare 实时数据获取失败" in r.message for r in caplog.records)
        assert any("新浪财经实时行情获取失败" in r.message for r in caplog.records)

    def test_fetch_historical_data_logs_all_sources_failed(self, provider: MarketDataProvider, caplog: pytest.LogCaptureFixture) -> None:
        provider._wind_mcp_client = {"kline": lambda *args, **kwargs: []}
        provider._tdx_source = None
        provider._akshare_source = None
        fake_resp = MagicMock()
        fake_resp.text = "null"
        fake_resp.raise_for_status.return_value = None
        with patch("utils.data_provider._SINA_SESSION") as mock_session, caplog.at_level(logging.WARNING):
            mock_session.get.return_value = fake_resp
            with pytest.raises(RuntimeError):
                provider._fetch_historical_data("600519", "1y")
        assert any("Wind MCP 历史数据获取失败" in r.message for r in caplog.records)

    def test_source_health_updated_on_failure(self, provider: MarketDataProvider) -> None:
        provider._wind_mcp_client = {"quote": lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("wind fail"))}
        provider._try_wind_mcp_realtime("600519")
        assert provider.source_health["wind_mcp"]["ok"] is False
        assert provider.source_health["wind_mcp"]["last_error"] == "wind fail"
