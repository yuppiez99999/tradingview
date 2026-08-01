#!/usr/bin/env python
"""ai_decision 历史回放 + 三基线对比 CLI

任务: 步骤 6 CLI 入口 (阶段三 — 历史回放 + 基线对比)
触发: 手动 / 定期 (离线研究, 非每日)
产出: reports/ai_decision/backtest_replay_{timestamp}.md + .json

三基线对比:
  1. ai_debate   — 完整 run_decision (辩论+聚合+风控)
  2. five_agents — 仅五 Agent 共识 (跳过辩论/judge)
  3. rule_only   — 纯规则兜底 (跳过 AI)

用法:
    # 用 Mock 数据快速验证 (默认)
    python scripts/run_ai_decision_backtest.py

    # 指定标的池和天数
    python scripts/run_ai_decision_backtest.py --symbols 600519 000001 --days 90

    # 指定日期范围
    python scripts/run_ai_decision_backtest.py --start 2025-01-01 --end 2025-06-30

    # 打印 Markdown 到 stdout
    python scripts/run_ai_decision_backtest.py --print
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_decision.backtest_replay import (
    BacktestReplay,
    MockHistoryDataLoader,
    ReplayConfig,
)


def main() -> int:
    """CLI 主入口

    Returns:
        0=成功且建议 shadow, 1=成功且建议 paper/auto, 2=异常
    """
    parser = argparse.ArgumentParser(
        description="ai_decision 历史回放 + 三基线对比"
    )
    parser.add_argument(
        "--symbols", nargs="*", default=None,
        help="标的池 (空格分隔, 默认 Mock 3 只)",
    )
    parser.add_argument(
        "--days", type=int, default=60,
        help="回放天数 (Mock 模式, 默认 60)",
    )
    parser.add_argument(
        "--start", default=None,
        help="开始日期 (YYYY-MM-DD, 默认 Mock 起点)",
    )
    parser.add_argument(
        "--end", default=None,
        help="结束日期 (YYYY-MM-DD, 默认 Mock 终点)",
    )
    parser.add_argument(
        "--horizon", type=int, default=5,
        help="前瞻收益天数 (IC 计算, 默认 5)",
    )
    parser.add_argument(
        "--freq", default="W",
        help="调仓频率 (D/W/M, 默认 W)",
    )
    parser.add_argument(
        "--print", action="store_true",
        help="打印 Markdown 到 stdout",
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="不落盘, 仅返回报告",
    )
    args = parser.parse_args()

    try:
        # 构造 Mock 数据加载器 (真实数据源待接入)
        loader = MockHistoryDataLoader(
            symbols=args.symbols,
            days=args.days,
            start_date=args.start or "2025-01-01",
        )

        config = ReplayConfig(
            start_date=args.start or "",
            end_date=args.end or "",
            symbols=args.symbols,
            rebalance_freq=args.freq,
            forward_return_horizon=args.horizon,
            use_mock_providers=True,
        )

        replay = BacktestReplay(loader=loader, config=config)
        report = replay.compare_baselines()

        # 落盘
        if not args.no_save:
            path = replay.save(report)
            print(f"✅ 回放报告已生成: {path}")

        # 打印 Markdown
        if args.print:
            print()
            print("=" * 60)
            print(replay.to_markdown(report))

        # 摘要
        print()
        print("=" * 60)
        print("📊 三基线 Sharpe 对比:")
        for bt in ["ai_debate", "five_agents", "rule_only"]:
            bl = report.baselines.get(bt, {})
            sharpe = bl.get("metrics", {}).get("sharpe", 0.0)
            n_dec = bl.get("n_decisions", 0)
            print(f"  {bt:15s}: Sharpe={sharpe:+.4f}  决策数={n_dec}")
        print()
        print("📈 边际夏普:")
        print(f"  辩论增量 (debate-agents): {report.marginal_sharpe_debate_vs_agents:+.4f}")
        print(f"  Agent增量 (agents-rule):  {report.marginal_sharpe_agents_vs_rule:+.4f}")
        print()
        rec_icon = {"auto": "🟢", "paper": "🟡", "shadow": "🔴"}.get(
            report.recommendation, "⚪"
        )
        print(f"{rec_icon} 上线建议: {report.recommendation.upper()}")

        # 偏差校验
        bias_failed = [k for k, v in report.bias_checks.items() if not v]
        if bias_failed:
            print(f"⚠️  前视偏差校验未通过: {', '.join(bias_failed)}")
            return 1

        return 1 if report.recommendation in ("auto", "paper") else 0

    except Exception as exc:
        print(f"❌ 回放失败: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
