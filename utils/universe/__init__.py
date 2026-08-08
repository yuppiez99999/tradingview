"""
全市场自动选股系统 — 对冲基金工业级 Pipeline

模块组成：
- stock_universe:    沪深300+中证500成分股获取
- risk_filter:       风险前置过滤（ST/停牌/流动性）
- factor_scorer:     458 因子横截面均衡打分
- portfolio_builder: 分层组合构建（短20+中30+长50）
- report_generator:  可视化报告（matplotlib）
- scheduler:         每日盘后定时调度
- survivorship_free_universe: 幸存者偏差免费宇宙（P0-2 FIX）
"""

from __future__ import annotations

from .factor_scorer import batch_compute_factors, cross_sectional_score, industry_neutralize
from .portfolio_builder import LayeredPortfolio, PortfolioConfig, build_layered_portfolio
from .report_generator import generate_full_report
from .risk_filter import RiskFilterConfig, filter_universe
from .scheduler import run_daily_scan
from .stock_universe import get_hs300_constituents, get_universe, get_zz500_constituents
from .survivorship_free_universe import (
    SurvivorshipBiasFreeUniverse,
    get_sfu,
    get_universe_at_date,
    validate_backtest,
)

__all__ = [
    "LayeredPortfolio",
    "PortfolioConfig",
    "RiskFilterConfig",
    "SurvivorshipBiasFreeUniverse",
    "batch_compute_factors",
    "build_layered_portfolio",
    "cross_sectional_score",
    "filter_universe",
    "generate_full_report",
    "get_hs300_constituents",
    "get_sfu",
    "get_universe",
    "get_universe_at_date",
    "get_zz500_constituents",
    "industry_neutralize",
    "run_daily_scan",
    "validate_backtest",
]
