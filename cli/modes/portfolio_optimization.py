# -*- coding: utf-8 -*-
"""
投资组合优化模式
多策略资产配置对比
"""

from __future__ import annotations

from core.context import (
    BASE_DIR,
    logger,
    ProgressIndicator,
)

from utils.cli_helpers import write_report_file, archive_report
from engine.managers import PortfolioOptimizationEngine


def run_portfolio_optimization(args):
    """投资组合优化模式 - 多策略资产配置对比"""
    print("\n📊 投资组合优化与回测")
    print("=" * 70)

    progress = ProgressIndicator("投资组合优化", 5)

    progress.update(1, "初始化优化引擎...")
    engine = PortfolioOptimizationEngine()

    progress.update(2, "生成模拟数据...")
    if not engine.generate_simulation_data():
        progress.complete("❌ 数据生成失败")
        return

    progress.update(3, "计算相关性矩阵...")
    engine.calculate_correlation_matrix()

    progress.update(4, "运行优化策略...")
    engine.run_all_strategies()

    progress.update(5, "生成报告...")
    report = engine.generate_report()
    print("\n" + report)

    write_report_file(report, getattr(args, 'output', None))
    archive_report(report, '投资组合优化')

    progress.complete("✅ 投资组合优化完成")

    return engine
