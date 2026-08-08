# -*- coding: utf-8 -*-
"""
ETF资金流向监控模式
追踪国家队资金动向，分析ETF资金流向
"""

from __future__ import annotations

from core.context import (
    BASE_DIR,
    logger,
    connector_manager,
    ProgressIndicator,
)

from utils.cli_helpers import write_report_file, archive_report
from engine.managers import ETFFundFlowMonitor


def run_etf_flow_monitor(args):
    """ETF资金流向监控模式 - 追踪国家队资金动向"""
    print("\n📊 ETF国家队资金流向监控")
    print("=" * 70)

    progress = ProgressIndicator("ETF资金流向分析", 4)

    progress.update(1, "初始化监控器...")
    monitor = ETFFundFlowMonitor(data_connector_manager=connector_manager)

    progress.update(2, "获取ETF行情数据...")
    flow_data = monitor.analyze_fund_flow()

    progress.update(3, "检测国家队信号...")
    signals = monitor.detect_signals()

    progress.update(4, "生成投资建议...")
    suggestions = monitor.get_investment_suggestion()

    # 生成报告
    report = monitor.generate_report()
    print("\n" + report)

    write_report_file(report, getattr(args, 'output', None))
    archive_report(report, 'ETF资金流向')

    progress.complete(f"检测到 {len(signals)} 条信号")

    return monitor
