"""CLI mode: 自动对冲再平衡。

支持三个CLI参数:
    --auto-hedge-rebalance    触发EOD决策
    --auto-hedge-intraday     触发盘中紧急再评估
    --auto-hedge-report       生成目标达成监控报告
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.auto_hedge_rebalance.engine import AutoHedgeRebalanceEngine


def run_eod_decision(args: argparse.Namespace) -> int:
    """执行EOD决策。"""
    engine = AutoHedgeRebalanceEngine(
        base_dir=str(PROJECT_ROOT),
        config_path="config/auto_hedge_rebalance.yaml",
    )
    plan = engine.run_eod_decision(
        portfolio_volatility=getattr(args, "volatility", 0.18),
        portfolio_drawdown_60d=getattr(args, "drawdown", 0.0),
    )

    print("=" * 60)
    print("自动对冲再平衡 EOD 决策结果")
    print("=" * 60)
    print(f"时间: {plan.timestamp}")
    print(f"工具类型: {plan.tool_selection.tool_type.value}")
    print(f"对冲比例: {plan.tool_selection.hedge_ratio:.0%}")
    print(f"滚动年化收益: {plan.monitor.rolling_annual_return:.2%}")
    print(f"滚动最大回撤: {plan.monitor.rolling_max_drawdown:.2%}")
    print(f"策略等级: {plan.strategy_state.current_level.value}")
    if plan.degradation_flags:
        print(f"降级标记: {', '.join(plan.degradation_flags)}")
    print("=" * 60)
    return 0


def run_intraday_check(args: argparse.Namespace) -> int:
    """执行盘中紧急再评估。"""
    engine = AutoHedgeRebalanceEngine(
        base_dir=str(PROJECT_ROOT),
        config_path="config/auto_hedge_rebalance.yaml",
    )
    action = engine.run_intraday_check(
        current_portfolio_value=getattr(args, "current_value", 1000.0),
        previous_portfolio_value=getattr(args, "previous_value", 1000.0),
    )

    print("=" * 60)
    print("盘中紧急再评估结果")
    print("=" * 60)
    print(f"动作类型: {action.action_type}")
    print(f"描述: {action.description}")
    print("=" * 60)
    return 0


def run_monitor_report(args: argparse.Namespace) -> int:
    """生成目标达成监控报告。"""
    engine = AutoHedgeRebalanceEngine(
        base_dir=str(PROJECT_ROOT),
        config_path="config/auto_hedge_rebalance.yaml",
    )
    report = engine.get_monitor_report()

    print("=" * 60)
    print("目标达成监控报告")
    print("=" * 60)
    print(f"滚动年化收益: {report.rolling_annual_return:.2%}")
    print(f"滚动最大回撤: {report.rolling_max_drawdown:.2%}")
    print(f"收益偏离度: {report.return_deviation:.2%}")
    print(f"回撤余量: {report.drawdown_margin:.2%}")
    print(f"纠偏动作: {report.correction_action.value}")
    print(f"样本不足: {report.sample_insufficient}")
    print("=" * 60)
    return 0


def register_args(parser: argparse.ArgumentParser) -> None:
    """注册CLI参数。"""
    parser.add_argument("--auto-hedge-rebalance", action="store_true", help="执行EOD自动对冲再平衡决策")
    parser.add_argument("--auto-hedge-intraday", action="store_true", help="执行盘中紧急再评估")
    parser.add_argument("--auto-hedge-report", action="store_true", help="生成目标达成监控报告")
    parser.add_argument("--volatility", type=float, default=0.18, help="组合年化波动率")
    parser.add_argument("--drawdown", type=float, default=0.0, help="60日最大回撤")
    parser.add_argument("--current-value", type=float, default=1000.0, help="当前组合价值")
    parser.add_argument("--previous-value", type=float, default=1000.0, help="上一时刻组合价值")


def run(args: argparse.Namespace) -> int:
    """CLI入口。"""
    if getattr(args, "auto_hedge_rebalance", False):
        return run_eod_decision(args)
    if getattr(args, "auto_hedge_intraday", False):
        return run_intraday_check(args)
    if getattr(args, "auto_hedge_report", False):
        return run_monitor_report(args)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="自动对冲再平衡CLI")
    register_args(parser)
    args = parser.parse_args()
    sys.exit(run(args))
