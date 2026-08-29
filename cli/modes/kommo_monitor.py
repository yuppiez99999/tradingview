"""
康波周期监控模式
大宗商品全维度监控
"""

from __future__ import annotations

import os

from engine.managers import KommoCommodityMonitor

from core.context import (
    ProgressIndicator,
)
from utils.cli_helpers import archive_report, write_report_file


def run_kommo_monitor(args):
    """康波周期监控模式 - 大宗商品全维度监控"""
    print("\n🌍 康波周期大宗商品监控")
    print("=" * 70)

    progress = ProgressIndicator("康波周期监控", 3)

    ts_token = os.environ.get("TS_TOKEN", "")
    progress.update(1, "初始化监控器...")
    monitor = KommoCommodityMonitor(ts_token=ts_token)

    progress.update(2, "获取商品价格与宏观指标...")
    commodity_result, macro = monitor.monitor()

    progress.update(3, "生成报告...")
    report = monitor.generate_report()
    print("\n" + report)

    write_report_file(report, getattr(args, "output", None))
    archive_report(report, "康波周期监控")

    progress.complete(f"检测到 {len(commodity_result)} 只商品")

    return monitor
