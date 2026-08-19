"""fast_backtest — 快速回测 shim

re-export utils/alpha/fast_backtest.py 的 run_fast_backtest,
保持主入口文件 `from fast_backtest import run_fast_backtest` 兼容。
"""
from __future__ import annotations

from utils.alpha.fast_backtest import run_fast_backtest  # noqa: F401

__all__ = ['run_fast_backtest']
