"""根目录 shim — 让 `import deflated_sharpe` 和 `from deflated_sharpe import ...` 可解析.

实际实现在 utils/backtest/deflated_sharpe.py.
"""
from utils.backtest.deflated_sharpe import (
    deflated_sharpe_ratio,
    DSRResult,
)

__all__ = ["deflated_sharpe_ratio", "DSRResult"]
