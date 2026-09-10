"""fast_backtest — 快速回测 shim

re-export utils/alpha/fast_backtest.py 的入口, 保持主入口文件
`from fast_backtest import run_fast_backtest` 兼容。

2026-09-10 (审计 item 14): 追加 re-export 真实样本外验证入口
(FastBacktest.run_walk_forward / run_cpcv), 使主入口也能拿到 walk-forward 能力。
"""

from __future__ import annotations

from utils.alpha.fast_backtest import (
    FastBacktest,
    WalkForwardConfig,
    WalkForwardResult,
    run_fast_backtest,
)

__all__ = [
    "FastBacktest",
    "WalkForwardConfig",
    "WalkForwardResult",
    "run_fast_backtest",
]
