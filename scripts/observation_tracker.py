#!/usr/bin/env python3
"""T0.6 观察期累积追踪器 — 汇总观察期进度/数据质量/就绪状态.

ARCHITECTURE_自我进化框架 §7 — 观察期追踪
创建: 2026-08-02

数据源:
    - reports/shadow/daily_returns.jsonl  → Shadow 账户每日收益率
    - reports/evolution/decisions.jsonl    → 进化编排器决策日志
    - reports/evolution/phase_b_status.json → 阶段 B 状态
    - reports/evolution/status.json        → 进化评估器状态

输出:
    - reports/evolution/observation_progress.json → 观察期快照 (JSON)
    - stdout: 人类可读进度摘要

运行:
    py -X utf8 scripts/observation_tracker.py
    py -X utf8 scripts/observation_tracker.py --json
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

REPORTS_DIR = PROJECT_ROOT / "reports" / "evolution"
SHADOW_RETURNS = PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
DECISIONS_LOG = REPORTS_DIR / "decisions.jsonl"
PHASE_B_STATUS = REPORTS_DIR / "phase_b_status.json"
EVAL_STATUS = REPORTS_DIR / "status.json"
OBS_DELTA = PROJECT_ROOT / "reports" / "evolution" / "observation_progress.json"

# 单事实源: shadow_admission.yaml (PM 决策后 observation_days=21, min_samples_for_dsr=20)
# 代码不得再硬编码观察天数, 否则 yaml 升级后观察期永不生效.
SHADOW_ADMISSION_YAML = (
    PROJECT_ROOT / "v8.3_institutional" / "config" / "shadow_admission.yaml"
)


def _load_shadow_admission() -> dict:
    """读取 shadow_admission.yaml, 失败安全降级到默认 14 天/20 样本."""
    try:
        with open(SHADOW_ADMISSION_YAML, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg
    except (OSError, yaml.YAMLError):
        return {}


_CFG = _load_shadow_admission()
OBSERVATION_START = "2026-07-23"
# yaml 优先, 回退默认 14 (兼容离线 / yaml 缺失)
# 注意: 观察天数字段在 settings.observation_days 嵌套层 (非顶层)
_SETTINGS = _CFG.get("settings", {})
OBSERVATION_DAYS = int(_SETTINGS.get("observation_days", _SETTINGS.get("min_observation_days", 14)))
MIN_SAMPLES = int(
    _CFG.get("admission_criteria", {}).get(
        "min_samples_for_dsr", _CFG.get("min_samples", 20)
    )
)


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        return None


def count_trading_days_since(start_str: str) -> int:
    """计算自 start_str 以来的交易日数."""
    try:
        start = datetime.strptime(start_str, "%Y-%m-%d")
    except ValueError:
        return 0
    now = datetime.now()
    days = 0
    current = start
    while current <= now:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days


def generate_snapshot() -> dict[str, Any]:
    """生成观察期快照."""
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    # 1) Shadow 数据统计
    shadow_records = load_jsonl(SHADOW_RETURNS)
    shadow_dates = sorted(set(r.get("date", "")[:10] for r in shadow_records))
    n_shadow_days = len(shadow_dates)
    n_shadow_records = len(shadow_records)

    # 最近收益
    recent_returns = []
    total_return = 0.0
    for r in shadow_records:
        ret = r.get("daily_return", 0.0)
        total_return += ret
        if r.get("date", "")[:10] >= (now - timedelta(days=5)).strftime("%Y-%m-%d"):
            recent_returns.append({
                "date": r.get("date", "")[:10],
                "daily_return": ret,
            })

    cumulative_return = total_return  # 简化: 对数加总

    # 2) 观察期进度
    trading_days_elapsed = count_trading_days_since(OBSERVATION_START)
    obs_days = min(n_shadow_days, trading_days_elapsed)
    obs_remaining = max(0, OBSERVATION_DAYS - obs_days)
    obs_progress_pct = min(100.0, round(obs_days / OBSERVATION_DAYS * 100, 1))
    samples_remaining = max(0, MIN_SAMPLES - n_shadow_days)

    # 3) 预计完成日期
    estimated_remaining_days = obs_remaining
    remaining_days_count = 0
    future = now
    while remaining_days_count < estimated_remaining_days and future < now + timedelta(days=60):
        future += timedelta(days=1)
        if future.weekday() < 5:
            remaining_days_count += 1 if remaining_days_count < estimated_remaining_days else 0

    est_completion = future.strftime("%Y-%m-%d") if estimated_remaining_days > 0 else now_str[:10]

    # 4) 决策日志统计
    decisions = load_jsonl(DECISIONS_LOG)
    n_decisions = len(decisions)
    recent_decisions = [d for d in decisions[-10:] if d] if decisions else []

    # 5) 阶段 B 状态
    phase_b = load_json(PHASE_B_STATUS) or {}
    eval_status = load_json(EVAL_STATUS) or {}

    # 6) 就绪评估
    ready_for_phase_b = obs_days >= OBSERVATION_DAYS and n_shadow_days >= MIN_SAMPLES

    snapshot = {
        "generated_at": now_str,
        "observation": {
            "start_date": OBSERVATION_START,
            "required_days": OBSERVATION_DAYS,
            "days_completed": obs_days,
            "days_remaining": obs_remaining,
            "progress_pct": obs_progress_pct,
            "trading_days_elapsed": trading_days_elapsed,
            "min_samples": MIN_SAMPLES,
            "samples_collected": n_shadow_days,
            "samples_remaining": samples_remaining,
            "estimated_completion": est_completion,
            "ready_for_phase_b": ready_for_phase_b,
        },
        "shadow_account": {
            "total_records": n_shadow_records,
            "unique_dates": n_shadow_days,
            "cumulative_return": round(cumulative_return * 100, 4),
            "recent_5d_returns": recent_returns,
        },
        "decisions": {
            "total_decisions": n_decisions,
            "recent_10": [
                {
                    "ts": d.get("ts", ""),
                    "action": d.get("action", ""),
                    "status": d.get("status", ""),
                }
                for d in recent_decisions
            ],
        },
        "phase_b": {
            "stage": phase_b.get("stage", "waiting_observation"),
            "flags_enabled": phase_b.get("flags_enabled", {}),
            "last_updated": phase_b.get("last_updated", ""),
        },
        "eval_status": {
            "status": eval_status.get("status", "?"),
            "next_action": eval_status.get("next_action", ""),
        },
    }

    return snapshot


def print_summary(snapshot: dict) -> None:
    """打印人类可读摘要."""
    obs = snapshot["observation"]
    shadow = snapshot["shadow_account"]
    pb = snapshot["phase_b"]
    ev = snapshot["eval_status"]

    status_light = "[GREEN]" if obs["ready_for_phase_b"] else "[YELLOW]"

    print("=" * 60)
    print(f"T0.6 观察期追踪 — {snapshot['generated_at']}")
    print("=" * 60)
    print()
    print(f"  状态: {status_light} {'可启用阶段 B' if obs['ready_for_phase_b'] else '观察中'}")
    print()
    print("  [观察期进度]")
    print(f"    起始: {obs['start_date']}  → 目前 {obs['days_completed']}/{obs['required_days']} 天 ({obs['progress_pct']}%)")
    print(f"    剩余: {obs['days_remaining']} 天 (预计 {obs['estimated_completion']} 完成)")
    print(f"    交易日流逝: {obs['trading_days_elapsed']} 天")
    print()
    print("  [数据收集]")
    print(f"    Shadow 样本: {obs['samples_collected']}/{obs['min_samples']} ({'OK' if obs['samples_collected'] >= obs['min_samples'] else 'NOK'})")
    print(f"    累计收益: {shadow['cumulative_return']}%")
    print(f"    数据记录: {shadow['total_records']} 条 / {shadow['unique_dates']} 天")
    if shadow["recent_5d_returns"]:
        print("    最近 5 日收益: ", end="")
        for rr in shadow["recent_5d_returns"]:
            sign = "+" if rr["daily_return"] > 0 else ""
            print(f"[{rr['date']}: {sign}{rr['daily_return']*100:.2f}%] ", end="")
        print()
    print()
    print("  [阶段 B 状态]")
    print(f"    阶段: {pb['stage']}")
    if pb['flags_enabled']:
        print(f"    Flags: {', '.join(f'{k}={v}' for k, v in pb['flags_enabled'].items())}")
    print()
    print("  [评估器]")
    print(f"    状态: {ev['status']}")
    if ev["next_action"]:
        print(f"    下一步: {ev['next_action']}")
    print()
    print("  [决策日志]")
    print(f"    累计: {snapshot['decisions']['total_decisions']} 条")
    print()

    # 剩余天数摘要
    if obs["ready_for_phase_b"]:
        print("  [下一步] 观察期已满且数据充足, 可执行:")
        print('    py -X utf8 scripts/phase_b_progressive_enabler.py --advance')
    else:
        reasons = []
        if obs["days_remaining"] > 0:
            reasons.append(f"观察期还需 {obs['days_remaining']} 天")
        if obs["samples_remaining"] > 0:
            reasons.append(f"样本还需 {obs['samples_remaining']} 条")
        print(f"  [阻塞] {', '.join(reasons)}")
        print(f"    预计可启动: {obs['estimated_completion']}")

    print("=" * 60)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="T0.6 观察期累积追踪器")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()

    snapshot = generate_snapshot()

    # 保存进度文件
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with OBS_DELTA.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2, default=str)

    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str))
    else:
        print_summary(snapshot)

    return 0


if __name__ == "__main__":
    sys.exit(main())
