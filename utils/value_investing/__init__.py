"""value_investing — 价值投资决策工具集（源自 ai-berkshire）

提供精确金融验证、报告审计、股票筛选、动量回测、晨星公允价值、A 股数据等
价值投资分析能力。所有模块零外部依赖（仅 stdlib），可独立运行。

主要 API:
    financial_rigor: exact, verify_market_cap, verify_valuation, cross_validate,
                     benford_check, three_scenario_valuation
    report_audit:    extract_data_points, sample_points, render_verdict
    stock_screener:  scan_ticker, check_momentum, check_value, grade_signal
    momentum_backtest: backtest_ticker, compute_momentum_signals, verify_value
    ashare_data:     cmd_quote, cmd_valuation, cmd_financials, cmd_search
    morningstar_fair_value: fetch_page, extract_ticker

Usage:
    from utils.value_investing import scan_ticker, verify_valuation
    from utils.value_investing import financial_rigor as fr
"""
from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "financial_rigor",
    "report_audit",
    "stock_screener",
    "momentum_backtest",
    "momentum_backtest_v2",
    "morningstar_fair_value",
    "ashare_data",
    "load_module",
]


def load_module(name: str) -> Any:
    """按名懒加载子模块。name ∈ {financial_rigor, report_audit, stock_screener,
    momentum_backtest, momentum_backtest_v2, morningstar_fair_value, ashare_data}"""
    if name not in __all__:
        raise ValueError(f"unknown value_investing module: {name}")
    return importlib.import_module(f"{__name__}.{name}")


def __getattr__(name: str) -> Any:
    """PEP 562 模块级 __getattr__ — 首次访问时懒加载子模块。"""
    if name in __all__ and name != "load_module":
        return load_module(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
