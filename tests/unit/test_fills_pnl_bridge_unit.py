"""fills_pnl_bridge 单元测试 — 成交回报 PnL 桥接层全分支覆盖"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.execution import fills_pnl_bridge  # noqa: E402


@pytest.fixture
def mock_store():
    store = MagicMock(name="FillsStore")
    return store


@pytest.fixture
def patched_bridge(mock_store):
    with patch.object(fills_pnl_bridge, "FillsStore", return_value=mock_store):
        yield mock_store


class TestAugmentMarketPricesNoFills:
    def test_empty_latest_returns_copy(self, patched_bridge):
        patched_bridge.latest_avg_price_by_symbol.return_value = {}
        mp = {"600519": {"close": 1700.0, "prev_close": 1680.0}}
        result = fills_pnl_bridge.augment_market_prices(mp)
        assert result == {"600519": {"close": 1700.0, "prev_close": 1680.0}}
        assert result is not mp

    def test_empty_latest_preserves_original(self, patched_bridge):
        patched_bridge.latest_avg_price_by_symbol.return_value = {}
        mp = {"600519": {"close": 1700.0}}
        original_snapshot = dict(mp)
        fills_pnl_bridge.augment_market_prices(mp)
        assert mp == original_snapshot


class TestAugmentMarketPricesMatching:
    def test_matching_code_overwrites_close(self, patched_bridge):
        patched_bridge.latest_avg_price_by_symbol.return_value = {"600519": 1750.5}
        mp = {"600519": {"close": 1700.0, "prev_close": 1680.0}}
        result = fills_pnl_bridge.augment_market_prices(mp)
        assert result["600519"]["close"] == 1750.5
        assert result["600519"]["close_source"] == "fill"
        assert result["600519"]["prev_close"] == 1680.0

    def test_matching_code_does_not_mutate_original(self, patched_bridge):
        patched_bridge.latest_avg_price_by_symbol.return_value = {"600519": 1750.5}
        mp = {"600519": {"close": 1700.0, "prev_close": 1680.0}}
        fills_pnl_bridge.augment_market_prices(mp)
        assert mp["600519"]["close"] == 1700.0
        assert "close_source" not in mp["600519"]

    def test_inner_dict_is_copy_not_reference(self, patched_bridge):
        patched_bridge.latest_avg_price_by_symbol.return_value = {"600519": 1750.5}
        inner = {"close": 1700.0, "prev_close": 1680.0}
        mp = {"600519": inner}
        result = fills_pnl_bridge.augment_market_prices(mp)
        result["600519"]["close"] = 9999.0
        assert inner["close"] == 1700.0


class TestAugmentMarketPricesSuffix:
    def test_suffix_code_strips_dot(self, patched_bridge):
        patched_bridge.latest_avg_price_by_symbol.return_value = {"600519.SH": 1800.0}
        mp = {"600519": {"close": 1700.0}}
        result = fills_pnl_bridge.augment_market_prices(mp)
        assert result["600519"]["close"] == 1800.0
        assert result["600519"]["close_source"] == "fill"

    def test_suffix_code_no_match_in_augmented(self, patched_bridge):
        patched_bridge.latest_avg_price_by_symbol.return_value = {"999999.SH": 100.0}
        mp = {"600519": {"close": 1700.0}}
        result = fills_pnl_bridge.augment_market_prices(mp)
        assert result == {"600519": {"close": 1700.0}}


class TestAugmentMarketPricesNotMatching:
    def test_fill_code_not_in_market_prices(self, patched_bridge):
        patched_bridge.latest_avg_price_by_symbol.return_value = {"000001": 10.0}
        mp = {"600519": {"close": 1700.0}}
        result = fills_pnl_bridge.augment_market_prices(mp)
        assert result == {"600519": {"close": 1700.0}}

    def test_partial_match_only_covered_codes_changed(self, patched_bridge):
        patched_bridge.latest_avg_price_by_symbol.return_value = {
            "600519": 1750.0,
            "000001": 10.0,
        }
        mp = {
            "600519": {"close": 1700.0},
            "000001": {"close": 9.5},
            "300750": {"close": 200.0},
        }
        result = fills_pnl_bridge.augment_market_prices(mp)
        assert result["600519"]["close"] == 1750.0
        assert result["600519"]["close_source"] == "fill"
        assert result["000001"]["close"] == 10.0
        assert result["000001"]["close_source"] == "fill"
        assert result["300750"] == {"close": 200.0}


class TestAugmentMarketPricesFailOpen:
    def test_value_error_returns_copy(self, patched_bridge):
        patched_bridge.latest_avg_price_by_symbol.side_effect = ValueError("boom")
        mp = {"600519": {"close": 1700.0}}
        result = fills_pnl_bridge.augment_market_prices(mp)
        assert result == {"600519": {"close": 1700.0}}
        assert result is not mp

    def test_os_error_returns_copy(self, patched_bridge):
        patched_bridge.latest_avg_price_by_symbol.side_effect = OSError("disk")
        mp = {"600519": {"close": 1700.0}}
        result = fills_pnl_bridge.augment_market_prices(mp)
        assert result == {"600519": {"close": 1700.0}}

    def test_key_error_returns_copy(self, patched_bridge):
        patched_bridge.latest_avg_price_by_symbol.side_effect = KeyError("x")
        mp = {"600519": {"close": 1700.0}}
        result = fills_pnl_bridge.augment_market_prices(mp)
        assert result == {"600519": {"close": 1700.0}}

    def test_attribute_error_returns_copy(self, patched_bridge):
        patched_bridge.latest_avg_price_by_symbol.side_effect = AttributeError("x")
        mp = {"600519": {"close": 1700.0}}
        result = fills_pnl_bridge.augment_market_prices(mp)
        assert result == {"600519": {"close": 1700.0}}

    def test_type_error_returns_copy(self, patched_bridge):
        patched_bridge.latest_avg_price_by_symbol.side_effect = TypeError("x")
        mp = {"600519": {"close": 1700.0}}
        result = fills_pnl_bridge.augment_market_prices(mp)
        assert result == {"600519": {"close": 1700.0}}

    def test_constructor_exception_returns_copy(self):
        with patch.object(fills_pnl_bridge, "FillsStore", side_effect=OSError("init fail")):
            mp = {"600519": {"close": 1700.0}}
            result = fills_pnl_bridge.augment_market_prices(mp)
            assert result == {"600519": {"close": 1700.0}}
            assert result is not mp


class TestAugmentMarketPricesDateParam:
    def test_date_forwarded_to_store(self, patched_bridge):
        patched_bridge.latest_avg_price_by_symbol.return_value = {}
        fills_pnl_bridge.augment_market_prices({}, date="2026-08-14")
        patched_bridge.latest_avg_price_by_symbol.assert_called_once_with("2026-08-14", strategies=None)

    def test_date_none_forwarded(self, patched_bridge):
        patched_bridge.latest_avg_price_by_symbol.return_value = {}
        fills_pnl_bridge.augment_market_prices({})
        patched_bridge.latest_avg_price_by_symbol.assert_called_once_with(None, strategies=None)


class TestRealizedPnl:
    def test_success_returns_dict(self, patched_bridge):
        patched_bridge.realized_pnl.return_value = {"600519": 1234.5, "000001": -50.0}
        result = fills_pnl_bridge.realized_pnl("2026-08-14")
        assert result == {"600519": 1234.5, "000001": -50.0}
        patched_bridge.realized_pnl.assert_called_once_with("2026-08-14", strategies=None)

    def test_success_no_date(self, patched_bridge):
        patched_bridge.realized_pnl.return_value = {"600519": 100.0}
        result = fills_pnl_bridge.realized_pnl()
        assert result == {"600519": 100.0}
        patched_bridge.realized_pnl.assert_called_once_with(None, strategies=None)

    def test_value_error_returns_empty(self, patched_bridge):
        patched_bridge.realized_pnl.side_effect = ValueError("boom")
        assert fills_pnl_bridge.realized_pnl() == {}

    def test_os_error_returns_empty(self, patched_bridge):
        patched_bridge.realized_pnl.side_effect = OSError("disk")
        assert fills_pnl_bridge.realized_pnl() == {}

    def test_key_error_returns_empty(self, patched_bridge):
        patched_bridge.realized_pnl.side_effect = KeyError("x")
        assert fills_pnl_bridge.realized_pnl() == {}

    def test_type_error_returns_empty(self, patched_bridge):
        patched_bridge.realized_pnl.side_effect = TypeError("x")
        assert fills_pnl_bridge.realized_pnl() == {}

    def test_attribute_error_returns_empty(self, patched_bridge):
        patched_bridge.realized_pnl.side_effect = AttributeError("x")
        assert fills_pnl_bridge.realized_pnl() == {}

    def test_constructor_exception_returns_empty(self):
        with patch.object(fills_pnl_bridge, "FillsStore", side_effect=OSError("init")):
            assert fills_pnl_bridge.realized_pnl() == {}


class TestImmutability:
    def test_original_unchanged_with_multiple_codes(self, patched_bridge):
        patched_bridge.latest_avg_price_by_symbol.return_value = {
            "600519": 1750.0,
            "000001.SH": 10.0,
        }
        mp = {
            "600519": {"close": 1700.0, "prev_close": 1680.0},
            "000001": {"close": 9.5},
        }
        original = {k: dict(v) for k, v in mp.items()}
        fills_pnl_bridge.augment_market_prices(mp)
        assert mp == original

    def test_empty_market_prices(self, patched_bridge):
        patched_bridge.latest_avg_price_by_symbol.return_value = {"600519": 1750.0}
        result = fills_pnl_bridge.augment_market_prices({})
        assert result == {}
