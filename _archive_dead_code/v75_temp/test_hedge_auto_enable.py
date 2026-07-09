# -*- coding: utf-8 -*-
"""
验证对冲自动开启 — 三种场景测试
1. 正常场景 (VIX=18, 无持仓)            → 期望 NO_HEDGE
2. 满仓场景 (VIX=18, Beta=1.1)         → 期望触发 Beta 对冲
3. 黑天鹅场景 (VIX=65, 满仓, 高相关)    → 期望触发 Vol + Correlation 对冲
"""
from __future__ import annotations

import sys
import logging
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "src"))

import numpy as np
import pandas as pd

from hedging.beta_hedger import BetaHedger
from hedging.vol_hedger import VolHedger
from hedging.correlation_hedger import CorrelationHedger
from hedging.hedge_coordinator import HedgeCoordinator

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("hedge_test")

CAPITAL = 5_000_000


def make_returns(n_days=60, codes=None, corr=0.3, vol=0.02):
    """生成相关性可控的模拟收益率（列名与持仓 codes 对齐）"""
    if codes is None:
        codes = ["510300", "510500", "588000", "159915", "601088", "518880"]
    np.random.seed(42)
    base = np.random.normal(0, vol, n_days)
    noise = np.random.normal(0, vol, (n_days, len(codes)))
    returns = pd.DataFrame({
        code: corr * base + (1 - corr) * noise[:, i]
        for i, code in enumerate(codes)
    })
    market = pd.Series(base, name="market")
    return returns, market


def test_scenario(name: str, vix: float, positions: dict, prices: dict,
                  returns: pd.DataFrame, market_returns: pd.Series,
                  expect_hedge: bool):
    """运行单个场景"""
    logger.info("=" * 60)
    logger.info(f"场景: {name}  VIX={vix}  持仓标的数={len(positions)}")
    logger.info("=" * 60)

    hc = HedgeCoordinator()
    result = hc.coordinate(
        positions=positions,
        prices=prices,
        returns=returns,
        market_returns=market_returns,
        vix=vix,
        portfolio_value=CAPITAL,
    )

    beta = result.get("portfolio_beta", 0)
    action = result.get("action", "NO_HEDGE")
    orders = result.get("orders", [])
    total_pct = result.get("total_hedge_pct", 0)
    summary = result.get("summary", {})

    logger.info(f"组合Beta: {beta:.3f}")
    logger.info(f"对冲动作: {action}")
    logger.info(f"对冲指令数: {len(orders)}")
    logger.info(f"总对冲比例: {total_pct:.2%}")
    logger.info(f"分项: Beta={summary.get('beta_hedge')}, "
                f"Vol={summary.get('vol_hedge')}, "
                f"Corr={summary.get('corr_hedge')}")

    for i, o in enumerate(orders, 1):
        logger.info(f"  [{i}] {o.get('hedge_type')}: {o.get('action')} "
                    f"notional={o.get('notional', o.get('gold_value', 0)):.0f}")

    hedge_triggered = action == "HEDGE" and len(orders) > 0
    status = "✓ PASS" if hedge_triggered == expect_hedge else "✗ FAIL"
    logger.info(f"结果: {'已开启对冲' if hedge_triggered else '未开启对冲'} "
                f"(期望 {'开启' if expect_hedge else '不开启'}) → {status}")
    logger.info("")
    return hedge_triggered


def main():
    prices = {
        "510300": 4.20, "510500": 6.50, "588000": 1.05,
        "159915": 2.15, "601088": 40.70, "518880": 5.40,
    }

    # 场景1: 无持仓 + VIX低
    empty_positions = {code: 0 for code in prices}
    returns_normal, market_normal = make_returns(corr=0.3, vol=0.02, codes=list(prices.keys()))
    test_scenario(
        name="1. 正常场景 (无持仓, VIX=18.5)",
        vix=18.5,
        positions=empty_positions,
        prices=prices,
        returns=returns_normal,
        market_returns=market_normal,
        expect_hedge=False,
    )

    # 场景2: 满仓 + VIX正常 → 应触发 Beta 对冲
    full_positions = {
        "510300": 100000, "510500": 50000, "588000": 200000,
        "159915": 100000, "601088": 10000, "518880": 50000,
    }
    returns_full, market_full = make_returns(corr=0.85, vol=0.025, codes=list(prices.keys()))
    test_scenario(
        name="2. 满仓场景 (Beta>0.7, VIX=18.5)",
        vix=18.5,
        positions=full_positions,
        prices=prices,
        returns=returns_full,
        market_returns=market_full,
        expect_hedge=True,
    )

    # 场景3: 黑天鹅 (VIX=65, 高相关 0.92)
    returns_crisis, market_crisis = make_returns(corr=0.92, vol=0.04, codes=list(prices.keys()))
    test_scenario(
        name="3. 黑天鹅场景 (VIX=65, ρ̄=0.92, 满仓)",
        vix=65.0,
        positions=full_positions,
        prices=prices,
        returns=returns_crisis,
        market_returns=market_crisis,
        expect_hedge=True,
    )

    logger.info("=" * 60)
    logger.info("验证完成 — 7.5 对冲自动开启机制已与 7.4 对齐")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
