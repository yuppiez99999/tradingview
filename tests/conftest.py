# -*- coding: utf-8 -*-
"""
conftest.py — 量化交易系统 v8.4 统一测试配置

P2 FIX (2026-07-22): 统一 tests/ 和 v8.3_institutional/tests/ 两套测试
为所有测试文件提供:
  - 自动路径设置
  - 共享 fixture (样本数据/价格矩阵/配置)
  - Mock 工具支持
"""
import os
import sys
import warnings
import pytest
import numpy as np
import pandas as pd

# ============================================================
# 路径设置 — 确保两个测试目录都能找到核心模块
# ============================================================
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_V83_ROOT = os.path.join(_PROJECT_ROOT, "v8.3_institutional")

for _p in [_PROJECT_ROOT, _V83_ROOT, os.path.join(_V83_ROOT, "src")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

warnings.filterwarnings("ignore")

# ============================================================
# 共享 Fixtures
# ============================================================

@pytest.fixture(scope="session")
def project_root():
    """项目根目录"""
    return _PROJECT_ROOT


@pytest.fixture(scope="session")
def sample_symbols():
    """10 只测试标的代码"""
    return [f"TEST{i:03d}.SZ" for i in range(10)]


@pytest.fixture(scope="session")
def sample_prices(sample_symbols):
    """252 日 * 10 标的的价格矩阵 (有随机游走结构)"""
    np.random.seed(42)
    n_days = 252
    n_symbols = len(sample_symbols)
    returns = np.random.randn(n_days, n_symbols) * 0.02
    prices = 100 * np.exp(np.cumsum(returns, axis=0))
    return pd.DataFrame(prices, columns=sample_symbols)


@pytest.fixture(scope="session")
def sample_returns(sample_prices):
    """对数收益率矩阵"""
    return np.log(sample_prices / sample_prices.shift(1)).dropna()


@pytest.fixture(scope="session")
def sample_ohlcv(sample_symbols):
    """样本 OHLCV 数据 (60 日)"""
    np.random.seed(123)
    n = 60
    data = {}
    for sym in sample_symbols[:3]:
        close = 100 * np.exp(np.cumsum(np.random.randn(n) * 0.015))
        high = close * (1 + np.abs(np.random.randn(n) * 0.02))
        low = close * (1 - np.abs(np.random.randn(n) * 0.02))
        open_ = close * (1 + np.random.randn(n) * 0.005)
        volume = np.random.randint(1e6, 1e7, n)
        df = pd.DataFrame({
            "open": open_, "high": high, "low": low,
            "close": close, "volume": volume,
        }, index=pd.date_range("2026-01-01", periods=n, freq="B"))
        data[sym] = df
    return data


@pytest.fixture(scope="session")
def sample_sector_weights(sample_symbols):
    """样本行业权重"""
    return pd.Series(
        np.random.dirichlet(np.ones(len(sample_symbols))),
        index=sample_symbols,
    )


@pytest.fixture(scope="session")
def mock_config():
    """Mock 配置字典 (避免依赖 YAML 文件)"""
    return {
        "account": {
            "total_capital": 5_000_000,
            "stock_etf_capital": 4_000_000,
            "hedge_capital": 1_000_000,
        },
        "risk": {
            "max_single_position": 0.08,
            "max_sector_exposure": 0.30,
            "target_volatility": 0.10,
            "max_drawdown": 0.12,
        },
        "execution": {
            "slippage_bps": 5,
            "commission_rate": 0.0003,
        },
    }


# ============================================================
# Pytest 配置
# ============================================================

def pytest_configure(config):
    """注册自定义标记"""
    config.addinivalue_line("markers", "slow: 标记慢速测试 (>5s)")
    config.addinivalue_line("markers", "integration: 标记需要外部 API 的集成测试")
    config.addinivalue_line("markers", "smoke: 标记冒烟测试 (核心路径)")

    import logging
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
