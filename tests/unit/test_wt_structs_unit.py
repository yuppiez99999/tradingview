"""
单元测试: utils/wt_structs.py
覆盖 TickData / BarData / OrderData / TradeData / PositionData / ContractData / tick_to_dict / bar_to_dict / strict_symbol_validation
"""

from __future__ import annotations

import warnings

import pytest

from utils.wt_structs import (
    BarData,
    CodeExchangeMismatchError,
    CodeExchangeMismatchWarning,
    ContractData,
    OrderData,
    PositionData,
    TickData,
    TradeData,
    bar_to_dict,
    is_strict_symbol_validation,
    strict_symbol_validation,
    tick_to_dict,
)


class TestTickData:
    def test_basic(self):
        tick = TickData(
            code="600519.SH",
            exchange="SSE",
            price=1800.0,
            open=1750.0,
            high=1810.0,
            low=1740.0,
            pre_close=1750.0,
            volume=10000,
            amount=18000000,
        )
        assert tick.code == "600519.SH"
        assert tick.price == 1800.0

    def test_defaults(self):
        tick = TickData(
            code="600519",
            exchange="SSE",
            price=1800.0,
            open=1750.0,
            high=1810.0,
            low=1740.0,
            pre_close=1750.0,
            volume=10000,
            amount=18000000,
        )
        assert tick.bid_prices == []
        assert tick.timestamp == 0.0
        assert tick.ts_event == 0

    def test_mismatch_warning(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            TickData(
                code="600519.SH",
                exchange="SZSE",
                price=1800.0,
                open=1750.0,
                high=1810.0,
                low=1740.0,
                pre_close=1750.0,
                volume=10000,
                amount=18000000,
            )
            assert any(issubclass(x.category, CodeExchangeMismatchWarning) for x in w)

    def test_mismatch_strict_raises(self):
        with strict_symbol_validation(), pytest.raises(CodeExchangeMismatchError):
            TickData(
                code="600519.SH",
                exchange="SZSE",
                price=1800.0,
                open=1750.0,
                high=1810.0,
                low=1740.0,
                pre_close=1750.0,
                volume=10000,
                amount=18000000,
            )

    def test_bare_code_no_validation(self):
        tick = TickData(
            code="600519",
            exchange="SZSE",
            price=1800.0,
            open=1750.0,
            high=1810.0,
            low=1740.0,
            pre_close=1750.0,
            volume=10000,
            amount=18000000,
        )
        assert tick.code == "600519"

    def test_unknown_exchange_no_validation(self):
        tick = TickData(
            code="600519.SH",
            exchange="UNKNOWN",
            price=1800.0,
            open=1750.0,
            high=1810.0,
            low=1740.0,
            pre_close=1750.0,
            volume=10000,
            amount=18000000,
        )
        assert tick.exchange == "UNKNOWN"


class TestBarData:
    def test_basic(self):
        bar = BarData(
            code="600519.SH",
            exchange="SSE",
            period="1d",
            open=10.0,
            high=11.0,
            low=9.0,
            close=10.5,
            volume=1000,
        )
        assert bar.close == 10.5
        assert bar.amount == 0.0

    def test_mismatch_warning(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            BarData(
                code="600519.SH",
                exchange="SZSE",
                period="1d",
                open=10.0,
                high=11.0,
                low=9.0,
                close=10.5,
                volume=1000,
            )
            assert any(issubclass(x.category, CodeExchangeMismatchWarning) for x in w)


class TestOrderData:
    def test_basic(self):
        order = OrderData(
            order_id="001",
            code="600519.SH",
            exchange="SSE",
            direction="BUY",
            price=1800.0,
            volume=100,
        )
        assert order.offset == "OPEN"
        assert order.order_type == "LIMIT"
        assert order.status == "NOT_REPORTED"

    def test_mismatch_strict(self):
        with strict_symbol_validation(), pytest.raises(CodeExchangeMismatchError):
            OrderData(
                order_id="001", code="600519.SH", exchange="SZSE", direction="BUY"
            )


class TestTradeData:
    def test_basic(self):
        trade = TradeData(
            trade_id="t1",
            order_id="o1",
            code="600519.SH",
            exchange="SSE",
            direction="BUY",
            offset="OPEN",
            price=1800.0,
            volume=100,
            amount=180000,
        )
        assert trade.price == 1800.0


class TestPositionData:
    def test_basic(self):
        pos = PositionData(code="600519.SH", exchange="SSE")
        assert pos.direction == "LONG"
        assert pos.volume == 0.0


class TestContractData:
    def test_basic(self):
        contract = ContractData(code="600519.SH", exchange="SSE", name="贵州茅台")
        assert contract.product_class == "STOCK"
        assert contract.contract_multiplier == 1.0
        assert contract.price_tick == 0.01


class TestTickToDict:
    def test_basic(self):
        tick = TickData(
            code="600519.SH",
            exchange="SSE",
            price=1800.0,
            open=1750.0,
            high=1810.0,
            low=1740.0,
            pre_close=1750.0,
            volume=10000,
            amount=18000000,
        )
        d = tick_to_dict(tick)
        assert d["code"] == "600519.SH"
        assert d["price"] == 1800.0
        assert d["volume"] == 10000


class TestBarToDict:
    def test_basic(self):
        bar = BarData(
            code="600519.SH",
            exchange="SSE",
            period="1d",
            open=10.0,
            high=11.0,
            low=9.0,
            close=10.5,
            volume=1000,
        )
        d = bar_to_dict(bar)
        assert d["code"] == "600519.SH"
        assert d["close"] == 10.5
        assert d["period"] == "1d"


class TestStrictSymbolValidation:
    def test_enable(self):
        with strict_symbol_validation():
            assert is_strict_symbol_validation() is True
        assert is_strict_symbol_validation() is False

    def test_disable(self):
        with strict_symbol_validation(enabled=False):
            assert is_strict_symbol_validation() is False

    def test_nested(self):
        with strict_symbol_validation():
            with strict_symbol_validation(enabled=False):
                assert is_strict_symbol_validation() is False
            assert is_strict_symbol_validation() is True
