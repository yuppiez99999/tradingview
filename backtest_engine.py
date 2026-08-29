"""backtest_engine — 回测引擎 shim

re-export utils/wt_backtest_engine.py 的 BacktestEngine,
保持主入口文件 `from backtest_engine import BacktestEngine` 兼容。
"""

from __future__ import annotations

from utils.wt_backtest_engine import BacktestEngine  # noqa: F401

__all__ = ["BacktestEngine"]
