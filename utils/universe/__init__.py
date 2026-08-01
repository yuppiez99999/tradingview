# -*- coding: utf-8 -*-
"""
全市场自动选股系统 — 对冲基金工业级 Pipeline

模块组成：
- stock_universe:    沪深300+中证500成分股获取
- risk_filter:       风险前置过滤（ST/停牌/流动性）
- factor_scorer:     458 因子横截面均衡打分
- portfolio_builder: 分层组合构建（短20+中30+长50）
- report_generator:  可视化报告（matplotlib）
- scheduler:         每日盘后定时调度
"""

from __future__ import annotations

from .stock_universe import get_universe, get_hs300_constituents, get_zz500_constituents
from .risk_filter import filter_universe, RiskFilterConfig
from .factor_scorer import batch_compute_factors, cross_sectional_score, industry_neutralize
from .portfolio_builder import build_layered_portfolio, PortfolioConfig, LayeredPortfolio
from .report_generator import generate_full_report
from .scheduler import run_daily_scan

__all__ = [
    "LayeredPortfolio",
    "PortfolioConfig",
    "RiskFilterConfig",
    "batch_compute_factors",
    "build_layered_portfolio",
    "cross_sectional_score",
    "filter_universe",
    "generate_full_report",
    "get_hs300_constituents",
    "get_universe",
    "get_zz500_constituents",
    "industry_neutralize",
    "run_daily_scan",
]
