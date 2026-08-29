"""utils.fineng.greeks.aggregator 单元测试 — PortfolioGreeksAggregator 组合希腊字母聚合 / 再平衡信号 / 属性"""

from __future__ import annotations

import pytest

from utils.fineng.greeks.aggregator import (
    PortfolioGreeks,
    PortfolioGreeksAggregator,
)


def test_aggregate_stock_only():
    """纯股票持仓: delta=price*beta*sign, gamma/vega/theta 为 0"""
    holdings = [
        {"code": "510300.SH", "qty": 10000, "price": 4.0, "type": "STOCK", "beta": 0.8},
    ]
    agg = PortfolioGreeksAggregator()
    pg = agg.aggregate(holdings)
    # 多头: delta = price*beta = 4.0*0.8 = 3.2 (未乘 multiplier, 默认1)
    assert pg.delta == pytest.approx(3.2)
    assert pg.gamma == pytest.approx(0.0)
    assert pg.vega == pytest.approx(0.0)
    assert pg.theta == pytest.approx(0.0)
    assert pg.total_market_value == pytest.approx(10000 * 4.0)


def test_aggregate_stock_futures_option_mixed():
    """三类持仓加权聚合 (期货 multiplier=1000, 期权走 BS Greeks)"""
    holdings = [
        # 股票: qty 10000, price 4.0, beta 0.8 → delta = 4.0*0.8 = 3.2
        {"code": "510300.SH", "qty": 10000, "price": 4.0, "type": "STOCK", "beta": 0.8},
        # 期货: qty 2, price 4000, multiplier 1000, beta 0.5 → delta = 4000*0.5 = 2000
        {
            "code": "IF.CFE",
            "qty": 2,
            "price": 4000.0,
            "multiplier": 1000.0,
            "type": "FUTURES",
            "beta": 0.5,
        },
        # 期权: 多头 Put, S=2.75 K=2.50 T=0.25 sigma=0.22 → delta 负
        {
            "code": "510050P2500M6.SH",
            "qty": 5,
            "price": 0.15,
            "type": "OPTION",
            "S": 2.75,
            "K": 2.50,
            "T": 0.25,
            "sigma": 0.22,
            "is_call": False,
            "multiplier": 10000,
        },
    ]
    agg = PortfolioGreeksAggregator()
    pg = agg.aggregate(holdings)
    # 聚合后 delta 应等于各持仓贡献之和 (delta * sign * multiplier)
    expected = sum(
        p.delta * (1.0 if p.quantity > 0 else -1.0) * p.multiplier for p in pg.positions
    )
    assert pg.delta == pytest.approx(expected, abs=1.0)
    # 股票 delta=3.2, 期货 delta=2000.0 → 线性部分占主导
    linear_delta = 3.2 + 2000.0
    assert abs(pg.delta - linear_delta) > 1.0  # 期权贡献已计入
    # 期权贡献 gamma/vega/theta 非零 (多头 put)
    assert pg.gamma != 0.0
    assert pg.vega != 0.0
    assert pg.theta != 0.0
    # 组合市值 = 股票 + 期货 + 期权
    assert pg.total_market_value == pytest.approx(
        10000 * 4.0 + 2 * 4000.0 * 1000.0 + 5 * 0.15 * 10000.0
    )


def test_aggregate_empty():
    """空持仓 → 全 0"""
    agg = PortfolioGreeksAggregator()
    pg = agg.aggregate([])
    assert pg.delta == pytest.approx(0.0)
    assert pg.gamma == pytest.approx(0.0)
    assert pg.vega == pytest.approx(0.0)
    assert pg.theta == pytest.approx(0.0)
    assert pg.total_market_value == pytest.approx(0.0)


def test_aggregate_short_sign():
    """空头持仓 delta 为负"""
    holdings = [
        {"code": "X", "qty": -10000, "price": 4.0, "type": "STOCK", "beta": 1.0},
    ]
    pg = PortfolioGreeksAggregator().aggregate(holdings)
    assert pg.delta < 0.0


def test_portfolio_greeks_properties():
    """PortfolioGreeks 派生属性: delta_pct / gamma_per_delta / daily_theta / to_dict"""
    pg = PortfolioGreeks(
        delta=5000.0,
        gamma=200.0,
        vega=30000.0,
        theta=-1500.0,
        total_market_value=1_000_000.0,
    )
    assert pg.delta_pct == pytest.approx(5000.0 / 1_000_000.0)
    assert pg.gamma_per_delta == pytest.approx(200.0 / 5000.0)
    assert pg.daily_theta == pytest.approx(-1500.0 / 365.0)
    d = pg.to_dict()
    assert set(d.keys()) >= {
        "delta",
        "gamma",
        "vega",
        "theta",
        "delta_pct",
        "daily_theta",
        "n_positions",
    }


def test_portfolio_greeks_gamma_per_delta_zero_delta():
    """delta=0 时 gamma_per_delta 应为 0 (避免除零)"""
    pg = PortfolioGreeks(delta=0.0, gamma=200.0, total_market_value=1.0)
    assert pg.gamma_per_delta == pytest.approx(0.0)


def test_rebalance_signal_all_ok():
    """希腊字母均在阈值内 → need_rebalance=False"""
    agg = PortfolioGreeksAggregator()
    pg = PortfolioGreeks(
        delta=0.02 * 1_000_000.0,
        gamma=0.0,
        vega=0.0,
        theta=0.0,
        total_market_value=1_000_000.0,
    )
    sig = agg.rebalance_signal(
        pg, delta_tolerance=0.05, gamma_tolerance=0.02, vega_tolerance=0.02
    )
    assert sig["need_rebalance"] is False
    assert sig["alerts"] == []


def test_rebalance_signal_delta_breach():
    """delta 超出阈值 → need_rebalance=True 且含 delta 告警"""
    agg = PortfolioGreeksAggregator()
    # delta_pct = 0.10 > 0.05 阈值
    pg = PortfolioGreeks(
        delta=0.10 * 1_000_000.0,
        gamma=0.0,
        vega=0.0,
        theta=0.0,
        total_market_value=1_000_000.0,
    )
    sig = agg.rebalance_signal(
        pg, delta_tolerance=0.05, gamma_tolerance=0.02, vega_tolerance=0.02
    )
    assert sig["need_rebalance"] is True
    assert any("Delta" in a for a in sig["alerts"])
    assert sig["delta_excess"] > 0.0


def test_rebalance_signal_vega_breach():
    """vega 超出阈值 → need_rebalance=True 且含 vega 告警"""
    agg = PortfolioGreeksAggregator()
    pg = PortfolioGreeks(
        delta=0.0,
        gamma=0.0,
        vega=0.03 * 1_000_000.0,
        theta=0.0,
        total_market_value=1_000_000.0,
    )
    sig = agg.rebalance_signal(
        pg, delta_tolerance=0.05, gamma_tolerance=0.02, vega_tolerance=0.02
    )
    assert sig["need_rebalance"] is True
    assert any("Vega" in a for a in sig["alerts"])
