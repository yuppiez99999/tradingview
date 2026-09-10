"""
ETF资金流向监控模式 (集成版)
追踪国家队资金动向，分析ETF资金流向 (Wind MCP → akshare → 模拟)
原版本依赖 engine.managers (未创建), 现改用 utils.etf_fund_tracker (v8.6 集成版)
"""

from __future__ import annotations

from core.context import ProgressIndicator
from utils.cli_helpers import write_report_file
from utils.etf_fund_tracker import ETFFundFlowTracker


def run_etf_flow_monitor(args):
    """ETF资金流向监控模式 - 追踪国家队资金动向 (Wind MCP 实时)"""
    print("\n📊 ETF国家队资金流向监控 (集成版)")
    print("=" * 70)

    progress = ProgressIndicator("ETF资金流向分析", 4)

    progress.update(1, "初始化监控器...")
    source = getattr(args, "source", "auto")
    days = getattr(args, "days", 5)
    tracker = ETFFundFlowTracker(days=days, source=source)

    progress.update(2, "获取ETF行情数据 (Wind MCP P1 → akshare P3 → 模拟)...")
    tracker.analyze_fund_flow()

    progress.update(3, "检测国家队信号...")
    signals = tracker.detect_signals()

    progress.update(4, "生成投资建议...")
    tracker.get_investment_suggestion()

    # 生成报告
    report = tracker.generate_report()
    print("\n" + report)

    write_report_file(report, getattr(args, "output", None))
    archive_path = tracker.archive(report)
    if archive_path:
        print(f"\n📁 已归档至: {archive_path}")

    progress.complete(f"检测到 {len(signals)} 条信号")

    return tracker
