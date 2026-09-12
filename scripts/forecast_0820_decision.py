#!/usr/bin/env python3
"""决策日样本达标预测与预案 (P0-2 + P2-2).

2026-08-11 v8.6.14: 决策日从 08-20 延期到 08-24 (周一)
原因: 08-20 前预计只有 18 条样本 (差 2 条), 延期到 08-24 可凑齐 21 条

计算:
    1. 从今天到决策日 (2026-08-24) 剩余交易日数
    2. 若每天写入 1 条, 决策日当天累计样本
    3. 判定 shadow_admission.yaml (min_samples_for_dsr=20) 是否达标
    4. 若不达标, 输出 3 档预案:
         Plan A: 降低 DSR 样本门槛 (20 → 15)
         Plan B: 启用 G15 事件驱动引擎回测补样本
         Plan C: 推迟决策至 2026-08-31 (样本 20+) 并启用影子 10% 灰度
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from utils.datetime_utils import now_bj

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _iter_trading_days(start: datetime, end: datetime) -> list[datetime]:
    """交易日近似: 周一至周五 (不考虑节假日, 保守估算)."""
    days: list[datetime] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def main() -> int:
    parser = argparse.ArgumentParser(description="决策日样本预测与预案")
    parser.add_argument(
        "--start",
        default=now_bj().strftime("%Y-%m-%d"),
        help="今天日期 (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--decision-day",
        default="2026-08-24",
        help="决策日 (YYYY-MM-DD), 默认 2026-08-24 (从 08-20 延期)",
    )
    parser.add_argument(
        "--current-samples",
        type=int,
        default=12,
        help="当前 shadow 样本数",
    )
    parser.add_argument(
        "--required",
        type=int,
        default=20,
        help="DSR 最小样本要求 (shadow_admission.yaml)",
    )
    args = parser.parse_args()

    today = datetime.strptime(args.start, "%Y-%m-%d")
    decision_day = datetime.strptime(args.decision_day, "%Y-%m-%d")

    remaining = _iter_trading_days(today, decision_day)
    # 今天 (若为交易日) 已经有一条, 所以"新增" = total - 1
    will_accumulate = len(remaining)
    # 决策日当天也会产生 1 条 (盘后 EOD), 所以累计到决策日收盘 = current + remaining
    projected_samples = args.current_samples + will_accumulate

    will_meet = projected_samples >= args.required
    gap = max(0, args.required - projected_samples)

    report: dict[str, Any] = {
        "generated_at": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "today": args.start,
        "decision_day": args.decision_day,
        "current_samples": args.current_samples,
        "required_samples": args.required,
        "remaining_trading_days": will_accumulate,
        "projected_samples_on_decision_day": projected_samples,
        "will_meet_requirement": will_meet,
        "gap_samples": gap,
        "plan": [],
    }

    print("=" * 60)
    print(f"决策日 ({args.decision_day}) 样本达标预测 — {args.start}")
    print("=" * 60)
    print(f"  交易日剩余 (含决策日): {will_accumulate} 天")
    print(f"  当前 shadow 样本:      {args.current_samples} 条")
    print(f"  决策日预计样本:        {projected_samples} 条")
    print(f"  达标 (>= {args.required}):     {'YES' if will_meet else 'NO'}")
    if gap:
        print(f"  缺口:                  还差 {gap} 条")
    print()

    if will_meet:
        plan = {
            "label": "MAIN",
            "description": f"直接按原准入标准执行 {args.decision_day} 决策, 进入阶段 B 灰度",
            "steps": [
                f"运行 scripts/shadow_admission_launcher.py evaluate --date {args.decision_day}",
                "生成 DSR 报告, 若 DSR>=0.5 触发 shadow 10% 资金灰度 (stage_1)",
                "feature_flag 开启 DriftMonitor + FeedbackLoop",
            ],
        }
        print("[MAIN] 主预案: 直接准入")
        for s in plan["steps"]:
            print(f"  - {s}")
        report["plan"].append(plan)
    else:
        # Plan A: 降门槛
        plan_a = {
            "label": "PLAN_A",
            "description": f"降低 DSR 样本门槛 {args.required} → 15 (临时放宽, 事后恢复)",
            "steps": [
                "修改 shadow_admission.yaml: min_samples_for_dsr: 15 (临时)",
                f"运行 scripts/shadow_admission_launcher.py evaluate --date {args.decision_day}",
                "若 DSR>=0.5 且 max_dd<=10%, 准入; 否则触发 Plan C",
                f"决策日过后 ({args.decision_day} 次日) 恢复 min_samples_for_dsr=20",
            ],
        }
        report["plan"].append(plan_a)

        # Plan B: G15 回测补样本
        plan_b = {
            "label": "PLAN_B",
            "description": "G15 事件驱动引擎对 2026-07-23 ~ 2026-08-10 进行 TICK 回测, 得到 ≥20 条样本",
            "steps": [
                "python -m utils.backtest.event_driven_engine --start 2026-07-23 --end 2026-08-10",
                "将回测 daily_return 写入 reports/shadow/backtest_returns.jsonl",
                "与 real shadow returns 合并, 构造混合样本 (需标记 source=backtest)",
                "对混合样本重算 DSR 并生成准入报告",
                "准入决策需注明 '混合样本', 并在 Phase B 前补齐实时数据",
            ],
        }
        report["plan"].append(plan_b)

        # Plan C: 推迟 + 影子灰度
        plan_c = {
            "label": "PLAN_C",
            "description": "推迟决策至 2026-08-31, 在此前以 10% 资金运行影子灰度",
            "steps": [
                f"{args.decision_day} 当日: 宣布 '观察期延至 08-31'",
                f"{args.decision_day} ~ 08-30: 用 stage_1 (10% 资金) 先跑真实交易, 收集样本",
                "08-31: 样本 ≥20 时再次评估, 通过后进入 stage_2",
                "若 08-31 仍未达标 (≤ 5 条异常), 强制启动 Plan B",
            ],
        }
        report["plan"].append(plan_c)

        print("[PLAN_A] 降低 DSR 样本门槛 20 → 15 (临时)")
        for s in plan_a["steps"]:
            print(f"  - {s}")
        print()
        print("[PLAN_B] G15 事件驱动引擎回测补样本 (更严谨但需验证 20 条)")
        for s in plan_b["steps"]:
            print(f"  - {s}")
        print()
        print("[PLAN_C] 推迟决策至 2026-08-31, 以 10% 资金影子灰度过渡")
        for s in plan_c["steps"]:
            print(f"  - {s}")

    out_path = (
        _PROJECT_ROOT
        / "reports"
        / "evolution"
        / f"decision_day_plan_{args.decision_day}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print()
    print(f"完整预案已归档: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
