# -*- coding: utf-8 -*-
"""
回测模式 — v5.10 P0-9 重构
"""

import os
from core.context import (
    BASE_DIR,
    logger,
    ProgressIndicator,
    load_portfolio_config,
)


def run_backtest(args):
    """回测模式 - 历史数据回测验证"""
    print("\n📊 运行回测")
    print("=" * 70)

    progress = ProgressIndicator("回测执行", 4)

    progress.update(1, "加载回测模块...")
    try:
        from fast_backtest import run_fast_backtest
        progress.update(2, "执行快速回测...")
        run_fast_backtest()
        progress.update(3, "生成报告...")
        progress.complete("\n✅ 回测完成")
        return
    except ImportError:
        try:
            from backtest_engine import BacktestEngine
            from portfolio_config import PortfolioConfig

            progress.update(2, "加载配置...")
            config = load_portfolio_config()

            if config:
                portfolio = PortfolioConfig()
                settings = {
                    'capital': {'total': 1000000},
                    'rebalance': {'threshold': 0.06, 'min_interval_days': 5},
                    'targets': {'annual_return': 0.08, 'max_drawdown': 0.15}
                }
                engine = BacktestEngine(portfolio, settings)

                progress.update(3, "查找历史数据...")
                excel_files = [f for f in os.listdir(BASE_DIR) if f.startswith('data_extraction') and f.endswith('.xlsx')]
                if excel_files:
                    result = engine.run_backtest(os.path.join(BASE_DIR, excel_files[0]))
                    print("\n✅ 回测完成")
                    print(result)
                else:
                    print("\n❌ 未找到历史数据文件")
            progress.complete()
        except Exception as e:
            progress.complete(f"❌ 回测模块不可用: {e}")
