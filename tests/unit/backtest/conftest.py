"""G15 单元测试共享 fixture。

提供合成的 OrderData/TickData/BarData 等测试数据,避免每个测试文件重复构造。
遵循 AAA 模式(Arrange-Act-Assert),fixture 仅负责 Arrange 部分。
"""
from __future__ import annotations

import pytest

from utils.wt_structs import BarData, ContractData, OrderData, TickData

# ============================================================
# OrderData fixtures
# ============================================================

@pytest.fixture
def sample_buy_order() -> OrderData:
    """合成买入订单: 600519 贵州茅台 100 股 @ 1800.0"""
    return OrderData(
        order_id="test-buy-001",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        offset="OPEN",
        order_type="LIMIT",
        price=1800.0,
        volume=100.0,
        traded_volume=0.0,
        status="NOT_REPORTED",
        timestamp=1700000000.0,
        datetime_str="2023-11-14 10:00:00",
    )


@pytest.fixture
def sample_sell_order() -> OrderData:
    """合成卖出订单: 600519 贵州茅台 100 股 @ 1850.0"""
    return OrderData(
        order_id="test-sell-001",
        code="600519.SH",
        exchange="SSE",
        direction="SELL",
        offset="CLOSE",
        order_type="LIMIT",
        price=1850.0,
        volume=100.0,
        traded_volume=0.0,
        status="NOT_REPORTED",
        timestamp=1700000001.0,
        datetime_str="2023-11-14 14:00:00",
    )


@pytest.fixture
def large_buy_order() -> OrderData:
    """合成大单买入: 1000 股,用于测试部分成交"""
    return OrderData(
        order_id="test-large-buy-001",
        code="510300.SH",
        exchange="SSE",
        direction="BUY",
        offset="OPEN",
        order_type="LIMIT",
        price=4.0,
        volume=1000.0,
        traded_volume=0.0,
        status="NOT_REPORTED",
        timestamp=1700000002.0,
        datetime_str="2023-11-14 10:30:00",
    )


# ============================================================
# BarData fixtures
# ============================================================

@pytest.fixture
def sample_bar() -> BarData:
    """合成日 K Bar: 600519 贵州茅台 2023-11-14"""
    return BarData(
        code="600519.SH",
        exchange="SSE",
        period="1d",
        open=1780.0,
        high=1860.0,
        low=1770.0,
        close=1820.0,
        volume=50000.0,
        amount=91000000.0,
        date=20231114,
        time=0,
    )


@pytest.fixture
def low_volume_bar() -> BarData:
    """合成低成交量 Bar: volume=200,用于测试部分成交"""
    return BarData(
        code="510300.SH",
        exchange="SSE",
        period="1d",
        open=3.95,
        high=4.05,
        low=3.90,
        close=4.00,
        volume=200.0,
        amount=800.0,
        date=20231114,
        time=0,
    )


# ============================================================
# TickData fixtures
# ============================================================

@pytest.fixture
def sample_tick() -> TickData:
    """合成 Tick: 600519 五档行情"""
    return TickData(
        code="600519.SH",
        exchange="SSE",
        price=1800.0,
        open=1780.0,
        high=1860.0,
        low=1770.0,
        pre_close=1780.0,
        volume=10000.0,
        amount=18000000.0,
        bid_prices=[1799.5, 1799.4, 1799.3, 1799.2, 1799.1],
        ask_prices=[1800.5, 1800.6, 1800.7, 1800.8, 1800.9],
        bid_volumes=[200, 150, 100, 80, 50],
        ask_volumes=[180, 120, 90, 60, 30],
        timestamp=1700000000.0,
        datetime_str="2023-11-14 10:00:00",
        date=20231114,
        time=100000,
    )


# ============================================================
# ContractData fixtures
# ============================================================

@pytest.fixture
def stock_contract() -> ContractData:
    """股票合约规格: 600519 贵州茅台"""
    return ContractData(
        code="600519.SH",
        exchange="SSE",
        name="贵州茅台",
        product_class="STOCK",
        contract_multiplier=1.0,
        price_tick=0.01,
        margin_rate=1.0,
        commission_rate=0.00025,
        stamp_duty=0.001,
        min_commission=5.0,
        volume_multiple=1.0,
    )


@pytest.fixture
def index_futures_contract() -> ContractData:
    """股指期货合约规格: IF.CFFEX 沪深300期货"""
    return ContractData(
        code="IF.CFFEX",
        exchange="CFFEX",
        name="沪深300股指期货",
        product_class="FUTURE",
        contract_multiplier=300.0,
        price_tick=0.2,
        margin_rate=0.12,
        commission_rate=0.000023,
        stamp_duty=0.0,
        min_commission=5.0,
        volume_multiple=1.0,
    )
