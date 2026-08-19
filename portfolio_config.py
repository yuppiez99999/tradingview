"""portfolio_config — 组合配置 shim

re-export utils/universe/portfolio_builder.py 的 PortfolioConfig,
保持主入口文件 `from portfolio_config import PortfolioConfig` 兼容。
"""
from __future__ import annotations

from utils.universe.portfolio_builder import PortfolioConfig  # noqa: F401

__all__ = ['PortfolioConfig']
