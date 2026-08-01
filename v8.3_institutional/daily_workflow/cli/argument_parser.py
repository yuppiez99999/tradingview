#!/usr/bin/env python3
"""CLI 参数解析与工作流调度入口。"""

from __future__ import annotations
import argparse
import logging
from datetime import date

from ..core.workflow_orchestrator import DailyWorkflow
from ..config.workflow_config import WorkflowConfig
from ..utils.logging_setup import setup_logger

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="v7.5 机构级每日交易工作流")
    parser.add_argument("--date", type=str, default=None, help="交易日格式: YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="干跑模式")
    parser.add_argument("--phase", choices=["check", "calibrate", "market", "risk", "hedge", "signal", "execute", "report", "autolearn", "factor_kill_switch", "shadow_monitor"], default=None, help="仅执行指定 phase")
    parser.add_argument("-v", "--verbose", action="store_true", help="增加日志输出级别")
    return parser

def main(args=None) -> int:
    parser = build_parser()
    opts = parser.parse_args(args)
    log_level = logging.DEBUG if opts.verbose else logging.INFO
    setup_logger(log_level)
    trade_date = date.today() if opts.date is None else date.fromisoformat(opts.date)
    workflow = DailyWorkflow(config=WorkflowConfig(trade_date=trade_date))
    success = workflow.run(phase_name=opts.phase)
    return 0 if success else 1

if __name__ == "__main__":
    main()
