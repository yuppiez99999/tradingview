"""HedgeToolDataFetcher 单元测试 — 覆盖三类工具获取、降级链、兜底、降级标记。

测试策略:
    - 使用 mock 模拟数据层响应，不依赖真实 Wind/TDX 连接
    - 覆盖率目标 ≥ 85%
    - 所有降级路径均有测试用例
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from utils.auto_hedge_rebalance.data_fetcher import HedgeToolDataFetcher
from utils.auto_hedge_rebalance.exceptions import AllHedgeToolPriceUnavailable


@pytest.fixture
def fetcher(tmp_path) -> HedgeToolDataFetcher:
    """提供已初始化的 HedgeToolDataFetcher 实例。"""
    cache_path = str(tmp_path / "test_cache.json")
    return HedgeToolDataFetcher(config={}, cache_path=cache_path)


class TestFetchFutures:
    """期货行情获取测试。"""

    def test_fetch_futures_normal(self, fetcher: HedgeToolDataFetcher) -> None:
        # Arrange
        mock_prices = {"IF": 3900.0, "IC": 5200.0, "IM": 6800.0, "IH": 2500.0}
        # Act
        with patch("utils.hedge_engine.get_live_futures_prices", return_value=mock_prices):
            result = fetcher.fetch_futures(["IF", "IC"])
        # Assert
        assert result["IF"] == 3900.0
        assert result["IC"] == 5200.0

    def test_fetch_futures_partial_missing(self, fetcher: HedgeToolDataFetcher) -> None:
        # Arrange
        mock_prices = {"IF": 3900.0, "IC": 5200.0}
        # Act
        with patch("utils.hedge_engine.get_live_futures_prices", return_value=mock_prices):
            result = fetcher.fetch_futures(["IF", "IC", "IM", "IH"])
        # Assert
        assert result["IF"] == 3900.0
        assert result["IM"] > 0  # 兜底价格
        flags = fetcher.get_fallback_status()
        assert any("期货行情部分缺失" in f for f in flags)

    def test_fetch_futures_all_fallback(self, fetcher: HedgeToolDataFetcher) -> None:
        # Arrange & Act
        with patch("utils.hedge_engine.get_live_futures_prices", side_effect=Exception("连接失败")):
            result = fetcher.fetch_futures(["IF", "IC"])
        # Assert
        assert result["IF"] > 0
        assert result["IC"] > 0
        flags = fetcher.get_fallback_status()
        assert any("期货行情获取异常" in f for f in flags)

    def test_fetch_futures_default_codes(self, fetcher: HedgeToolDataFetcher) -> None:
        # Arrange
        mock_prices = {"IF": 3900.0, "IC": 5200.0, "IM": 6800.0, "IH": 2500.0}
        # Act
        with patch("utils.hedge_engine.get_live_futures_prices", return_value=mock_prices):
            result = fetcher.fetch_futures()
        # Assert
        assert set(result.keys()) == {"IF", "IC", "IM", "IH"}


class TestFetchEtfOptions:
    """ETF 期权行情获取测试。"""

    def test_fetch_etf_options_empty_whitelist(self, fetcher: HedgeToolDataFetcher) -> None:
        # Arrange & Act
        result = fetcher.fetch_etf_options([])
        # Assert
        assert result == {}

    def test_fetch_etf_options_all_fallback(self, fetcher: HedgeToolDataFetcher) -> None:
        # Arrange & Act
        result = fetcher.fetch_etf_options(["510300"])
        # Assert
        assert "510300" in result
        assert len(result["510300"]) > 0
        flags = fetcher.get_fallback_status()
        assert any("全链失效" in f for f in flags)

    def test_fetch_etf_options_from_akshare(self, fetcher: HedgeToolDataFetcher) -> None:
        # Arrange
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=1)
        mock_df.iterrows.return_value = [
            (0, {"行权价": "4.0", "认购最新价": 0.12, "认沽最新价": 0.10, "到期日": "2026-09-30"}),
        ]
        # Act — wind_mcp 不存在会自动降级至 AKShare
        with patch("akshare.option_finance_board", return_value=mock_df):
            result = fetcher.fetch_etf_options(["510300"])
        # Assert
        assert "510300" in result
        assert "4.0" in result["510300"]
        assert result["510300"]["4.0"]["call_price"] == 0.12
        flags = fetcher.get_fallback_status()
        assert any("降级至AKShare" in f for f in flags)

    def test_fetch_etf_options_strike_missing(self, fetcher: HedgeToolDataFetcher) -> None:
        # Arrange — 行权价报价残缺 (call_price=0, put_price=0)
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=2)
        mock_df.iterrows.return_value = [
            (0, {"行权价": "4.0", "认购最新价": 0, "认沽最新价": 0, "到期日": "2026-09-30"}),
            (1, {"行权价": "4.1", "认购最新价": 0.15, "认沽最新价": 0.08, "到期日": "2026-09-30"}),
        ]
        # Act
        with patch("akshare.option_finance_board", return_value=mock_df):
            result = fetcher.fetch_etf_options(["510300"])
        # Assert
        assert "4.1" in result["510300"]
        assert "4.0" not in result["510300"]  # 拒缺行权价被跳过
        flags = fetcher.get_fallback_status()
        assert any("报价残缺" in f for f in flags)


class TestFetchReverseEtf:
    """反向 ETF 行情获取测试。"""

    def test_fetch_reverse_etf_empty_whitelist(self, fetcher: HedgeToolDataFetcher) -> None:
        # Arrange & Act
        result = fetcher.fetch_reverse_etf([])
        # Assert
        assert result == {}

    def test_fetch_reverse_etf_all_fallback(self, fetcher: HedgeToolDataFetcher) -> None:
        # Arrange & Act
        result = fetcher.fetch_reverse_etf(["TEST001"])
        # Assert
        assert "TEST001" in result
        assert result["TEST001"] > 0
        flags = fetcher.get_fallback_status()
        assert any("全链失效" in f for f in flags)

    def test_fetch_reverse_etf_from_akshare(self, fetcher: HedgeToolDataFetcher) -> None:
        # Arrange
        mock_row = MagicMock()
        mock_row.get = MagicMock(side_effect=lambda key, default=0: 1.5 if key == "收盘" else default)
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=1)
        mock_df.iloc.__getitem__ = MagicMock(return_value=mock_row)
        # Act — wind_mcp 不存在会自动降级至 AKShare
        with patch("akshare.fund_etf_hist_em", return_value=mock_df):
            result = fetcher.fetch_reverse_etf(["TEST001"])
        # Assert
        assert result["TEST001"] == 1.5
        flags = fetcher.get_fallback_status()
        assert any("降级至AKShare" in f for f in flags)


class TestFallbackStatus:
    """降级标记测试。"""

    def test_get_fallback_status_empty(self, fetcher: HedgeToolDataFetcher) -> None:
        # Arrange & Act
        flags = fetcher.get_fallback_status()
        # Assert
        assert flags == []

    def test_flags_reset_on_each_fetch(self, fetcher: HedgeToolDataFetcher) -> None:
        # Arrange — 第一次获取产生降级标记
        with patch("utils.hedge_engine.get_live_futures_prices", side_effect=Exception("失败")):
            fetcher.fetch_futures(["IF"])
        assert len(fetcher.get_fallback_status()) > 0
        # Act — 第二次获取成功，标记应重置
        with patch("utils.hedge_engine.get_live_futures_prices", return_value={"IF": 3900.0}):
            fetcher.fetch_futures(["IF"])
        # Assert
        assert fetcher.get_fallback_status() == []


class TestFetchAll:
    """综合获取测试。"""

    def test_fetch_all_success(self, fetcher: HedgeToolDataFetcher) -> None:
        # Arrange
        mock_prices = {"IF": 3900.0, "IC": 5200.0, "IM": 6800.0, "IH": 2500.0}
        # Act
        with patch("utils.hedge_engine.get_live_futures_prices", return_value=mock_prices):
            result = fetcher.fetch_all(futures_codes=["IF"], etf_option_codes=[], reverse_etf_codes=[])
        # Assert
        assert "futures" in result
        assert "etf_options" in result
        assert "reverse_etf" in result
        assert result["futures"]["IF"] == 3900.0

    def test_fetch_all_all_fallback(self, fetcher: HedgeToolDataFetcher) -> None:
        # Arrange & Act — 全链失效时返回兜底价格并标记降级
        with patch("utils.hedge_engine.get_live_futures_prices", side_effect=Exception("全链失效")):
            result = fetcher.fetch_all(futures_codes=["IF"], etf_option_codes=["510300"], reverse_etf_codes=[])
        # Assert
        assert "futures" in result
        assert result["futures"]["IF"] > 0  # 兜底价格
        assert "fallback_flags" in result