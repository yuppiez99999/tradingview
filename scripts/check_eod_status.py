#!/usr/bin/env python3
"""EOD 状态检查器 — 每日确认 Shadow EOD 是否跑完 + 观察期达标进度.

用法:
    python scripts/check_eod_status.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
SHADOW_PATH = _ROOT / "reports" / "shadow" / "daily_returns.jsonl"
PROGRESS_PATH = _ROOT / "reports" / "evolution" / "observation_progress.json"


def check_eod_status() -> int:
    if not SHADOW_PATH.exists():
        print("❌ daily_returns.jsonl 不存在")
        return 1

    lines = SHADOW_PATH.read_text(encoding="utf-8").strip().split("\n")
    records = []
    for line in lines:
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not records:
        print("❌ daily_returns.jsonl 无有效记录")
        return 1

    last = records[-1]
    last_date = str(last.get("date", ""))
    today = date.today().isoformat()
    today_dt = date.today()
    weekday = today_dt.weekday()

    print("=" * 55)
    print("EOD Shadow 状态检查")
    print("=" * 55)
    print(f"今日:           {today} (周{'一二三四五六日'[weekday]})")
    print(f"最新记录日期:   {last_date}")
    print(f"最新 daily_return: {last.get('daily_return', '?')}")
    print(f"总记录数:       {len(records)}")

    is_trading_day = weekday < 5
    if is_trading_day:
        if last_date == today:
            print("\n✅ 今日 EOD 已跑完")
        else:
            print(f"\n⚠️ 今日 EOD 未跑! 最新 {last_date} < 今日 {today}")
            print("   需检查定时任务或手动补跑 + Wind MCP 补录")
    else:
        if last_date >= (today_dt.replace(day=today_dt.day - 1)).isoformat():
            print("\n✅ 非交易日, 最新交易日 EOD 正常")
        else:
            print(f"\n⚠️ 非交易日但最新记录 {last_date} 过旧")

    if PROGRESS_PATH.exists():
        prog = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
        obs = prog.get("observation", {})
        print("\n--- 观察期达标进度 ---")
        print(f"GATE-A 天数:   {obs.get('days_completed', '?')}/{obs.get('required_days', 21)}")
        print(f"GATE-B 样本:   {obs.get('samples_collected', '?')}/{obs.get('min_samples', 20)}")
        print(f"预计达标日:    {obs.get('estimated_completion', '?')}")
        print(f"ready_for_phase_b: {obs.get('ready_for_phase_b', False)}")

    return 0


if __name__ == "__main__":
    sys.exit(check_eod_status())
