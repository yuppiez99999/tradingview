# -*- coding: utf-8 -*-
"""hedge_execution_orders 纯函数单元测试 — S4/S5 修复验证

覆盖:
  - S4: 期权最小名义阈值 (alloc 不足 1 张 → 跳过)
  - S5: 期货 instrument/multiplier 从配置读取 (默认 IF/300)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import hedge_execution_orders as heo  # noqa: E402; note: module uses `tuple[str, int]` which requires __future__ or 3.9+


class TestBuildBetaOptionOrders:
    """_build_beta_option_orders: S4 最小名义阈值"""

    def test_option_skip_below_min_notional(self):
        """alloc_notional 低于 1 张合约名义 → 跳过该期权, 返回空"""
        # est_price=4.0, multiplier=10000 → 1 张 = 40000
        # option_notional=10000 < 3*40000, 每个 alloc weight 均不足
        orders = heo._build_beta_option_orders(
            beta=1.0,
            hedge_value=1000000,
            option_notional=10000,  # 极小名义, 不够 1 张
            prices={"510050.SH": 4.0, "510300.SH": 3.5, "159915.SZ": 1.0},
            hedge_positions={},
            target=1000000,
        )
        assert len(orders) == 0

    def test_option_normal_alloc_produces_orders(self):
        """正常 alloc 足够开仓 → 产生订单"""
        orders = heo._build_beta_option_orders(
            beta=1.0,
            hedge_value=1000000,
            option_notional=500000,
            prices={"510050.SH": 4.0, "510300.SH": 3.5, "159915.SZ": 1.0},
            hedge_positions={},
            target=1000000,
        )
        assert len(orders) > 0
        # 第一只 510050.SH weight=0.5, alloc=250000, 1张=40000 → contracts >= 1
        assert orders[0]["type"] == "OPTIONS"
        assert orders[0]["contracts"] >= 1


class TestBuildBetaFuturesOrder:
    """_build_beta_futures_order: S5 instrument/multiplier 配置化"""

    def test_default_if_300(self):
        """默认配置: IF/300"""
        orders = heo._build_beta_futures_order(
            futures_notional=1000000,
            prices={"IF": 3800.0},
            hedge_positions={"IF_futures": {"target_contracts": 1}},
            plan={},
            target=1000000,
            beta=1.0,
        )
        assert len(orders) == 1
        assert orders[0]["instrument"] == "IF"
        assert orders[0]["multiplier"] == 300

    def test_custom_ic_from_config(self):
        """配置传入 IC/200 → 订单使用 IC/200"""
        orders = heo._build_beta_futures_order(
            futures_notional=1000000,
            prices={"IC": 5500.0},
            hedge_positions={
                "IF_futures": {
                    "target_contracts": 1,
                    "instrument": "IC",
                    "multiplier": 200,
                }
            },
            plan={},
            target=1000000,
            beta=1.0,
        )
        assert len(orders) == 1
        assert orders[0]["instrument"] == "IC"
        assert orders[0]["multiplier"] == 200
        # notional = 200 * 5500 = 1100000
        assert orders[0]["notional"] == 200 * 5500.0

    def test_zero_notional_returns_empty(self):
        """futures_notional=0 → 空列表"""
        orders = heo._build_beta_futures_order(
            futures_notional=0,
            prices={},
            hedge_positions={},
            plan={},
            target=1000000,
            beta=1.0,
        )
        assert orders == []

    def test_zero_target_contracts_returns_empty(self):
        """target_contracts=0 → 空列表"""
        orders = heo._build_beta_futures_order(
            futures_notional=1000000,
            prices={"IF": 3800.0},
            hedge_positions={"IF_futures": {"target_contracts": 0}},
            plan={},
            target=1000000,
            beta=1.0,
        )
        assert orders == []
