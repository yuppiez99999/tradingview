#!/usr/bin/env python
"""每日 EOD 生成 ai_decision 审计日志复盘报告

任务: 步骤 7 CLI 入口 (阶段五 — 审计日志自动复盘)
触发: 每日收盘后 (Windows 任务计划程序 / cron)
产出: reports/ai_decision/eod_review_{date}.md + .json

5 维度复盘:
  1. 决策分布 (action/mode/verdict_type/confidence)
  2. 辩论有效性 (触发率/FP 率/置信度)
  3. 风控拦截 (veto/escalation Top 5 原因)
  4. 执行质量 (成功率/延迟/TCA 评级)
  5. 异常检测 (模型失败/Brier/auto 放量)

5 条告警规则:
  - model_consecutive_failures  (WARNING)
  - brier_threshold             (CRITICAL)
  - auto_daily_limit            (CRITICAL)
  - veto_spike                  (WARNING)
  - tca_grade_df                (WARNING)

用法:
    python scripts/run_ai_decision_eod.py                    # 生成今日复盘
    python scripts/run_ai_decision_eod.py --date 2026-07-28  # 指定日期
    python scripts/run_ai_decision_eod.py --print            # 打印 Markdown 到 stdout
"""

from __future__ import annotations

import argparse
import os
import sys

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_decision.eod_review import EODReviewGenerator


def main() -> int:
    """CLI 主入口

    Returns:
        0=成功无告警, 1=成功但有告警, 2=异常
    """
    parser = argparse.ArgumentParser(description="ai_decision 每日 EOD 审计日志复盘")
    parser.add_argument(
        "--date",
        default=None,
        help="报告日期 (YYYY-MM-DD), 默认今天",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="打印 Markdown 到 stdout (除落盘外)",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="不落盘, 仅返回报告 (默认会落盘)",
    )
    args = parser.parse_args()

    try:
        gen = EODReviewGenerator()
        report = gen.generate_eod_review(args.date)

        # 落盘
        if not args.no_save:
            path = gen.save(report, args.date)
            print(f"✅ 复盘报告已生成: {path}")

        # 打印 Markdown
        if args.print:
            print()
            print("=" * 60)
            print(gen.to_markdown(report))

        # 告警摘要
        alerts = report.get("alerts", [])
        if alerts:
            critical = [a for a in alerts if a.get("severity") == "CRITICAL"]
            warning = [a for a in alerts if a.get("severity") == "WARNING"]
            print(f"⚠️  告警: CRITICAL={len(critical)} WARNING={len(warning)}")
            for a in critical:
                print(f"  🔴 [{a.get('rule', '')}] {a.get('message', '')}")
            for a in warning:
                print(f"  🟡 [{a.get('rule', '')}] {a.get('message', '')}")
            return 1
        print("✅ 无告警")
        return 0

    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ) as exc:

        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        print(f"❌ 复盘生成失败: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
