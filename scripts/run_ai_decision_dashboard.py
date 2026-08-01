#!/usr/bin/env python
"""每日 EOD 生成 ai_decision 延迟/成本看板

任务: 步骤 4 CLI 入口
触发: 每日收盘后 (Windows 任务计划程序 / cron)
产出: reports/ai_decision/dashboard_{date}.md + .json

用法:
    python scripts/run_ai_decision_dashboard.py                    # 生成今日看板
    python scripts/run_ai_decision_dashboard.py --date 2026-07-28  # 指定日期
    python scripts/run_ai_decision_dashboard.py --degrade-check    # 仅检查降级
"""
from __future__ import annotations

import argparse
import os
import sys

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_decision.dashboard import DashboardGenerator


def main() -> int:
    """CLI 主入口

    Returns:
        0=成功无告警, 1=成功但有告警, 2=异常
    """
    parser = argparse.ArgumentParser(
        description="ai_decision 每日延迟/成本看板"
    )
    parser.add_argument(
        "--date", default=None,
        help="报告日期 (YYYY-MM-DD), 默认今天",
    )
    parser.add_argument(
        "--degrade-check", action="store_true",
        help="仅检查超预算降级, 不生成完整看板",
    )
    args = parser.parse_args()

    try:
        gen = DashboardGenerator()

        if args.degrade_check:
            # 仅检查降级
            degrade = gen.maybe_degrade_on_budget()
            if degrade:
                print(f"⚠️  需降级角色: {', '.join(degrade.keys())}")
                for role, action in degrade.items():
                    print(f"  {role} → {action}")
                return 1
            else:
                print("✅ 无需降级")
                return 0

        # 生成完整看板
        report = gen.generate_daily_dashboard(args.date)
        path = gen.save(report, args.date)
        print(f"✅ 看板已生成: {path}")

        # 打印告警摘要
        alerts = report.get("alerts", [])
        if alerts:
            critical = [a for a in alerts if a.get("severity") == "CRITICAL"]
            warning = [a for a in alerts if a.get("severity") == "WARNING"]
            print(f"⚠️  告警: CRITICAL={len(critical)} WARNING={len(warning)}")
            for a in critical:
                print(f"  🔴 [{a.get('dimension', '')}] {a.get('message', '')}")
            for a in warning:
                print(f"  🟡 [{a.get('dimension', '')}] {a.get('message', '')}")
            return 1
        else:
            print("✅ 无告警")
            return 0

    except Exception as exc:
        print(f"❌ 看板生成失败: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
