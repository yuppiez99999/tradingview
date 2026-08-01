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

import numpy as np
import pandas as pd
import pytest

# ============================================================
# 路径设置 — 确保两个测试目录都能找到核心模块
# ============================================================
# v8.6.7 修复: conftest.py 位于 tests/ 子目录, 需上溯一层才是项目根目录
# 原 _PROJECT_ROOT = os.path.dirname(__file__) → tests/ (错误)
# 新 _PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__)) → 项目根 (正确)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    # 原有标记
    config.addinivalue_line("markers", "slow: 标记慢速测试 (>5s)")
    config.addinivalue_line("markers", "integration: 标记需要外部 API 的集成测试")
    config.addinivalue_line("markers", "smoke: 标记冒烟测试 (核心路径)")
    # v8.6.7 测试金字塔新增标记
    config.addinivalue_line("markers", "unit: 单元测试 (全 mock, <1s)")
    config.addinivalue_line("markers", "e2e: 端到端测试 (真实历史数据)")
    config.addinivalue_line("markers", "regression: 回归测试 (对应已修复的 bug 编号)")
    config.addinivalue_line("markers", "p0: P0 致命级 bug 回归")
    config.addinivalue_line("markers", "p1: P1 风险缺口级 bug 回归")
    config.addinivalue_line("markers", "bug(id): 关联 bug 编号, 如 @pytest.mark.bug('P0-E')")
    # ECC GAP-7/8/6 新增标记 (2026-07-29)
    config.addinivalue_line("markers", "reproducibility: 可复现性测试 (ECC GAP-7, 同 config+seed+dataset 重跑一致性)")
    config.addinivalue_line("markers", "contract: 数据契约测试 (ECC GAP-8, 字段/类型/null/point-in-time 校验)")
    config.addinivalue_line("markers", "drift: 漂移监控测试 (ECC GAP-6, sim_mode 激活 + KS/PSI + 延迟标签)")

    import logging
    logging.getLogger("matplotlib").setLevel(logging.WARNING)


# ============================================================
# v8.6.7 测试金字塔新增 fixtures
# ============================================================

import json
from pathlib import Path
from unittest.mock import MagicMock


@pytest.fixture(scope="session")
def reports_dir():
    """v8.3 真实历史报告目录 (E2E 黄金数据源)"""
    return Path(_PROJECT_ROOT) / "v8.3_institutional" / "reports"


@pytest.fixture(scope="session")
def trade_plans_dir():
    """v8.3 交易计划目录"""
    return Path(_PROJECT_ROOT) / "v8.3_institutional" / "trade_plans"


@pytest.fixture(scope="session")
def real_pnl_report(reports_dir):
    """加载真实生产报告 daily_pnl_report_2026-07-21.json (26 标的完整格式)

    用于 E2E 测试和报告兼容性单元测试的黄金样本
    """
    path = reports_dir / "daily_pnl_report_2026-07-21.json"
    if not path.exists():
        pytest.skip(f"真实报告文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def sample_pnl_report_full():
    """完整格式 pnl_report (portfolio_pnl.details, 3 标的)

    用于测试 _extract_positions 兼容层
    """
    return {
        "meta": {"report_date": "2026-07-21"},
        "portfolio_pnl": {
            "summary": {
                "total_cost": 1_000_000,
                "total_market_value": 1_050_000,
                "total_pnl": 50_000,
                "total_pnl_pct": 5.0,
                "margin_used": 600_000,
                "total_equity": 1_500_000,
            },
            "details": [
                {"code": "588080.SH", "name": "科创50ETF", "market_value": 350_000,
                 "pnl": 20_000, "daily_pnl_pct": 6.0, "cost_amount": 330_000},
                {"code": "512880.SH", "name": "证券ETF", "market_value": 400_000,
                 "pnl": 15_000, "daily_pnl_pct": 3.9, "cost_amount": 385_000},
                {"code": "510050.SH", "name": "上证50ETF", "market_value": 300_000,
                 "pnl": 15_000, "daily_pnl_pct": 5.3, "cost_amount": 285_000},
            ],
        },
    }


@pytest.fixture
def sample_pnl_report_simplified():
    """简化格式 pnl_report (顶层 positions, list of dicts)

    模拟无横杠文件名 daily_pnl_report_YYYYMMDD.json 的结构
    """
    return {
        "date": "2026-07-21",
        "summary": {
            "total_cost": 800_000,
            "total_market_value": 840_000,
            "total_pnl": 40_000,
            "total_pnl_pct": 5.0,
        },
        "positions": [
            {"code": "588080.SH", "pnl": 20_000, "market_value": 350_000, "daily_pnl_pct": 6.0},
            {"code": "512880.SH", "pnl": 20_000, "market_value": 490_000, "daily_pnl_pct": 4.2},
        ],
    }


@pytest.fixture
def sample_pnl_report_broken_p0d():
    """P0-D bug 重现样本: margin_used/total_equity 为 None"""
    return {
        "portfolio_pnl": {
            "summary": {
                "total_cost": 1_000_000,
                "total_market_value": 1_050_000,
                "margin_used": None,       # bug 触发条件
                "total_equity": None,      # bug 触发条件
                "positions": {},
            }
        }
    }


@pytest.fixture
def sample_trade_plan():
    """标准测试交易计划 (含 BUY + SELL 订单, 用于 L2/L3 过滤测试)"""
    return {
        "trade_date": "2026-07-22",
        "phase": {"daily_capital": 150_000, "day_capital": 150_000},
        "execution_plan": {
            "morning_orders": [
                {"symbol": "588080.SH", "direction": "BUY", "shares": 1000, "est_amount": 100_000},
                {"symbol": "512880.SH", "direction": "SELL", "shares": 500, "est_amount": 50_000},
            ],
            "afternoon_orders": [
                {"symbol": "510050.SH", "direction": "BUY", "shares": 2000, "est_amount": 200_000},
            ],
        },
        "market_state": {},
        "risk_guard": {},
        "hedge_config": {
            "layers": {"layer1_futures": {"ratio": 0.15}}
        },
    }


@pytest.fixture
def broken_hedge_positions_p0e():
    """P0-E bug 重现样本: hedge_positions 含字符串字段 (description/hedge_mode)"""
    return {
        "meta": {"total_capital": 5_000_000, "hedge_capital": 1_000_000},
        "positions": {
            "588080.SH": {"shares": 14100, "est_price": 1.95, "sector": "科技"}
        },
        "hedge_positions": {
            "description": "200万纯期权对冲 — 无期货空头",   # P0-E bug 触发: str
            "hedge_mode": "OPTIONS_ONLY",                  # P0-E bug 触发: str
            "ETF_put_options": {
                "instrument": "510050 Put",
                "exchange": "SSE",
                "target_contracts": 60,
                "premium_budget": 900_000,
                "strike": "OTM_5%",
            },
            "ETF_put_options_2": {
                "instrument": "588080 Put",
                "exchange": "SSE",
                "target_contracts": 25,
                "premium_budget": 300_000,
            },
        },
    }


@pytest.fixture
def broker_callback_mock():
    """模拟券商执行回调 (替代真实 QMT/CTP 接口)

    签名: callback(level: int, actions: list) -> Dict
    """
    callback = MagicMock(name="broker_callback")
    callback.return_value = {
        "executed": True,
        "orders_sent": 5,
        "broker_status": "OK",
    }
    return callback


@pytest.fixture
def mock_astock_realtime(monkeypatch):
    """mock utils.astock_realtime.get_realtime_quotes

    返回沪深300ETF -5.2% (触发 L2) 的模拟行情
    """
    def _fake_quotes(codes):
        return {
            "510300": {
                "price": 3.85,
                "pre_close": 4.06,
                "change_pct": -5.2,   # 触发 L2
            }
        }
    # 多种可能的导入路径
    try:
        monkeypatch.setattr("utils.astock_realtime.get_realtime_quotes", _fake_quotes)
    except (AttributeError, ImportError):
        pass


@pytest.fixture
def mock_akshare_unavailable(monkeypatch):
    """模拟 akshare 不可用 (触发 fail-closed 路径)"""
    def _raise(*args, **kwargs):
        raise ImportError("akshare not installed (mocked)")
    try:
        monkeypatch.setattr("akshare.stock_zh_a_spot_em", _raise, raising=False)
        monkeypatch.setattr("akshare.stock_zh_index_spot_em", _raise, raising=False)
    except (AttributeError, ImportError):
        pass


@pytest.fixture
def mock_external_data_unavailable(monkeypatch):
    """模拟 ExternalDataManager 不可用 (触发 overnight_gap fail-closed)"""
    def _raise(*args, **kwargs):
        raise ConnectionError("ExternalDataSource unavailable (mocked)")
    try:
        monkeypatch.setattr(
            "utils.external_data_source.ExternalDataManager",
            _raise,
            raising=False,
        )
    except (AttributeError, ImportError):
        pass


# ============================================================
# v8.6.7 跨层共享 fixture (unit + integration + e2e 都可用)
# ============================================================
# 这些 fixture 原本在 tests/unit/conftest.py, 现提升到顶层 conftest
# 使 integration 测试也能复用 KillSwitch / 环境变量隔离


@pytest.fixture
def tmp_kill_switch_log(tmp_path, monkeypatch):
    """隔离 kill_switch_events.jsonl 写入, 避免污染 logs/

    monkeypatch utils.kill_switch.KILL_SWITCH_LOG 指向 tmp_path
    """
    tmp_log = tmp_path / "kill_switch_events.jsonl"

    try:
        import utils.kill_switch as ks_module
        monkeypatch.setattr(ks_module, "KILL_SWITCH_LOG", tmp_log)
    except ImportError:
        pass

    return tmp_log


@pytest.fixture
def clean_env(monkeypatch):
    """清理 KillSwitch / Guard 相关环境变量, 保证测试可重现

    清理:
        - KILL_SWITCH_SIM_MODE
        - KILL_SWITCH_MARGIN_RATIO
        - TRADING_ENV
    """
    for var in ("KILL_SWITCH_SIM_MODE", "KILL_SWITCH_MARGIN_RATIO", "TRADING_ENV"):
        monkeypatch.delenv(var, raising=False)
    return None


@pytest.fixture
def production_env(monkeypatch):
    """设置 TRADING_ENV=production, 触发 fail-closed 路径"""
    monkeypatch.setenv("TRADING_ENV", "production")
    return "production"
