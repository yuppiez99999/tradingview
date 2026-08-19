#!/usr/bin/env python
"""
金融工程闭环流水线 — CLI 入口
=============================

支持模式:
  run_pipeline.py full              # 完整闭环
  run_pipeline.py data-cleaning     # 仅数据清洗
  run_pipeline.py alpha             # 仅 Alpha 信号
  run_pipeline.py backtest          # 仅回测验证
  run_pipeline.py execution         # 仅执行
  run_pipeline.py risk              # 仅风控检查
  run_pipeline.py status            # 查看状态

用法:
  python scripts/run_pipeline.py full --mode=dry_run
  python scripts/run_pipeline.py alpha --force-retrain
  python scripts/run_pipeline.py execution --dry-run --confirmation-token=xxx

作者: 终极量化交易系统 v8.4
日期: 2026-08-02
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.pipeline import PipelineOrchestrator


def setup_logging(level: str = "INFO") -> None:
    """配置日志"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def cmd_full(args: argparse.Namespace) -> int:
    """完整闭环"""
    orchestrator = PipelineOrchestrator()
    result = orchestrator.run_full_cycle(
        mode=args.mode,
        symbols=args.symbols.split(",") if args.symbols else None,
    )

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.success else 1


def cmd_data_cleaning(args: argparse.Namespace) -> int:
    """仅数据清洗"""
    orchestrator = PipelineOrchestrator()
    reports, result = orchestrator.run_data_cleaning_only(symbols=args.symbols.split(",") if args.symbols else None)

    print(json.dumps({
        "success": result.success,
        "reports_count": len(reports),
        "avg_quality": sum(r.quality_score for r in reports) / len(reports) if reports else 0,
        "failed": [r.symbol for r in reports if not r.passed],
    }, ensure_ascii=False, indent=2))
    return 0 if result.success else 1


def cmd_alpha(args: argparse.Namespace) -> int:
    """仅 Alpha 信号"""
    orchestrator = PipelineOrchestrator()
    signal, result = orchestrator.run_alpha_only(
        force_retrain=args.force_retrain,
        symbols=args.symbols.split(",") if args.symbols else None,
    )

    output = {
        "success": result.success,
        "model": signal.model_name if signal else "",
        "signals_count": len(signal.signals) if signal else 0,
        "top_signals": dict(list(signal.signals.items())[:10]) if signal else {},
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if result.success else 1


def cmd_execution(args: argparse.Namespace) -> int:
    """仅执行"""
    orchestrator = PipelineOrchestrator()
    exec_result, result = orchestrator.run_execution_only(
        dry_run=args.dry_run,
        confirmation_token=args.confirmation_token,
    )

    print(json.dumps({
        "success": result.success,
        "total_orders": exec_result.total_orders,
        "filled_orders": exec_result.filled_orders,
        "fill_rate": exec_result.fill_rate,
        "dry_run": exec_result.dry_run,
    }, ensure_ascii=False, indent=2))
    return 0 if result.success else 1


def cmd_status(args: argparse.Namespace) -> int:
    """查看状态"""
    orchestrator = PipelineOrchestrator()
    status = orchestrator.get_status()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="金融工程闭环流水线 CLI")
    parser.add_argument("--log-level", default="INFO", help="日志级别")

    sub = parser.add_subparsers(dest="command", required=True)

    # full
    p_full = sub.add_parser("full", help="完整闭环")
    p_full.add_argument("--mode", default="auto", choices=["auto", "manual", "dry_run"])
    p_full.add_argument("--symbols", default="", help="逗号分隔的标的列表")
    p_full.set_defaults(func=cmd_full)

    # data-cleaning
    p_dc = sub.add_parser("data-cleaning", help="仅数据清洗")
    p_dc.add_argument("--symbols", default="", help="逗号分隔的标的列表")
    p_dc.set_defaults(func=cmd_data_cleaning)

    # alpha
    p_alpha = sub.add_parser("alpha", help="仅 Alpha 信号")
    p_alpha.add_argument("--force-retrain", action="store_true", help="强制重训")
    p_alpha.add_argument("--symbols", default="", help="逗号分隔的标的列表")
    p_alpha.set_defaults(func=cmd_alpha)

    # execution
    p_exec = sub.add_parser("execution", help="仅执行")
    p_exec.add_argument("--dry-run", action="store_true", help="模拟执行")
    p_exec.add_argument("--confirmation-token", default="", help="实盘确认令牌")
    p_exec.set_defaults(func=cmd_execution)

    # status
    p_status = sub.add_parser("status", help="查看状态")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    setup_logging(args.log_level)

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
