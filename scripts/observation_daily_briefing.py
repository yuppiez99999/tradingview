#!/usr/bin/env python3
"""观察期每日播报生成器 (Observation Daily Briefing)

每日 16:10 由 v84_ObservationBriefing 定时任务调用,
生成 reports/evolution/daily_briefing_YYYYMMDD.md
并打印到 stdout 让任务计划程序记录.

查看方式:
    Get-Content reports\\evolution\\daily_briefing_latest.md -Encoding UTF8
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports" / "evolution"
DAILY_RETURNS_PATH = PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
DECISIONS_PATH = PROJECT_ROOT / "reports" / "evolution" / "decisions.jsonl"
STATUS_PATH = PROJECT_ROOT / "reports" / "evolution" / "status.json"
SHADOW_STATE_PATH = PROJECT_ROOT / "output" / "shadow_account" / "shadow_state.json"
# Shadow Admission Watchdog 心跳文件 (shadow_admission_watchdog.py:52)
SHADOW_WATCHDOG_HEARTBEAT_PATH = PROJECT_ROOT / "logs" / "shadow_watchdog_heartbeat.jsonl"


def load_jsonl(path: Path) -> list:
    """加载 JSONL 文件"""
    records = []
    if not path.exists():
        return records
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
    """加载 JSON 文件"""
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        return None


def load_last_heartbeat(heartbeat_path: Path, today: str) -> dict:
    """加载 Shadow Watchdog 心跳文件最后一条记录 (对齐 shadow_admission_watchdog.py:259).

    心跳记录结构: {"ts": "YYYY-MM-DDTHH:MM:SS", "outcome": str, "exit_code": int}
    outcome 取值: healthy / recovered / alerted / skipped_running / skipped_fail_fast / skipped_not_started

    Args:
        heartbeat_path: logs/shadow_watchdog_heartbeat.jsonl
        today: 今日日期字符串 (YYYY-MM-DD), 用于判断心跳是否今天

    Returns:
        {"last": dict | None, "today": dict | None}
        - last: 文件中最后一条心跳记录 (可能为今天, 也可能是历史)
        - today: 今天的心跳记录 (若今天无心跳则为 None)
    """
    records = load_jsonl(heartbeat_path)
    if not records:
        return {"last": None, "today": None}
    last = records[-1]
    today_record = None
    # 从后往前找今天的心跳 (心跳 ts 形如 "2026-07-30T17:00:05")
    for r in reversed(records):
        ts = str(r.get("ts", ""))
        if ts.startswith(today):
            today_record = r
            break
    return {"last": last, "today": today_record}


def query_task_status(task_name: str) -> dict:
    """查询 Windows 任务计划程序状态"""
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", task_name, "/V", "/FO", "LIST"],
            capture_output=True,
            text=True,
            encoding="gbk",
            errors="replace",  # 防止中文任务描述触发 UnicodeDecodeError (对齐 shadow_admission_watchdog.py:158)
            timeout=10,
        )
        info = {"last_run": "?", "last_result": "?", "next_run": "?"}
        stdout = result.stdout or ""
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("Last Run Time:"):
                info["last_run"] = line.split(":", 1)[1].strip()
            elif line.startswith("Last Result:"):
                info["last_result"] = line.split(":", 1)[1].strip()
            elif line.startswith("Next Run Time:"):
                info["next_run"] = line.split(":", 1)[1].strip()
        return info
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        return {"last_run": "?", "last_result": f"error: {e}", "next_run": "?"}


def next_trading_day(start: datetime, days: int) -> datetime:
    """计算 days 天后的下一个交易日"""
    target = start + timedelta(days=days)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return target


def main() -> int:
    """生成每日播报"""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    today_compact = today.strftime("%Y%m%d")
    now_str = today.strftime("%Y-%m-%d %H:%M:%S")

    briefing_path = REPORTS_DIR / f"daily_briefing_{today_compact}.md"
    latest_path = REPORTS_DIR / "daily_briefing_latest.md"

    # 加载数据
    daily_returns = load_jsonl(DAILY_RETURNS_PATH)
    decisions = load_jsonl(DECISIONS_PATH)
    status = load_json(STATUS_PATH)
    shadow_state = load_json(SHADOW_STATE_PATH)

    has_today_data = any(r.get("date") == today_str for r in daily_returns)

    # 任务状态
    postmarket = query_task_status("v84_PostMarket")
    eval_task = query_task_status("v84_EvolutionEval")
    shadow_admission_daily_task = query_task_status("v84_ShadowAdmissionDaily")
    watchdog_task = query_task_status("v84_ShadowAdmissionWatchdog")
    # Shadow Watchdog 心跳拾取 (三层防静默死亡: 简报曝光)
    # 注意: 简报 16:10 跑, watchdog 17:00 跑, 所以今天的 watchdog 还没跑是正常的.
    # 判定逻辑基于"最后一条心跳距今是否 > 24 小时" (而非"今天是否有心跳"):
    #   - 无心跳记录 → 从未运行
    #   - 最后心跳距今 > 24h → 心跳缺失 (昨天或更早的 watchdog 没跑)
    #   - 最后心跳距今 <= 24h 但 exit_code != 0 → 最近告警未解决
    #   - 最后心跳距今 <= 24h 且 exit_code == 0 → OK
    heartbeat = load_last_heartbeat(SHADOW_WATCHDOG_HEARTBEAT_PATH, today_str)
    watchdog_warning = None
    last_record = heartbeat["last"]
    if last_record is None:
        watchdog_warning = (
            "[CRITICAL] Shadow Watchdog 心跳文件为空 (从未运行); "
            "手动执行: py scripts\\shadow_admission_watchdog.py --no-sleep"
        )
    else:
        last_ts = str(last_record.get("ts", ""))
        try:
            last_dt = datetime.strptime(last_ts, "%Y-%m-%dT%H:%M:%S")
            age_hours = (today - last_dt).total_seconds() / 3600.0
        except (ValueError, TypeError):
            age_hours = float("inf")
            last_ts = last_ts or "?"
        if age_hours > 24:
            watchdog_warning = (
                f"[CRITICAL] Shadow Watchdog 心跳缺失, 最后心跳: {last_ts} "
                f"(距今 {age_hours:.1f}h, 阈值 24h; "
                f"手动执行: py scripts\\shadow_admission_watchdog.py --no-sleep)"
            )
        elif last_record.get("exit_code", 0) != 0:
            ec = last_record.get("exit_code", "?")
            outcome = last_record.get("outcome", "?")
            watchdog_warning = (
                f"[CRITICAL] Shadow Watchdog 最近告警未解决 "
                f"(exit_code={ec}, outcome={outcome}, ts={last_ts}); "
                f"详见 reports/shadow/watchdog_alert_*.json"
            )

    # 进度计算
    # 观察期 21 天 (与 v8.3_institutional/config/shadow_admission.yaml observation_days=21 对齐,
    # 原 14 → 21 由 PM 双管齐下方案调整; 注意勿改回 14, 否则播报与 admission_state.json 脱节)
    obs_days = len(daily_returns)
    obs_total = 21
    obs_remaining = max(0, obs_total - obs_days)
    obs_progress_pct = min(100.0, round(obs_days / obs_total * 100, 1))

    min_samples = 20
    samples_remaining = max(0, min_samples - obs_days)

    obs_end_date = next_trading_day(today, obs_remaining)
    eval_ready_date = next_trading_day(today, max(obs_remaining, samples_remaining))

    # 状态红绿灯
    if not has_today_data:
        status_light, status_text = "[RED]", "今日数据缺失"
    elif obs_days >= obs_total and obs_days >= min_samples:
        status_light, status_text = "[GREEN]", "可启用 Stage 2"
    else:
        status_light, status_text = "[YELLOW]", "进行中"

    # 构建报告
    lines = []
    lines.append(f"# {status_light} 自我进化观察期每日播报")
    lines.append("")
    lines.append(f"**日期**: {today_str}")
    lines.append(f"**状态**: {status_light} {status_text}")
    lines.append(f"**生成时间**: {now_str}")
    lines.append("")
    # Shadow Watchdog 红字警告 (三层防静默死亡: 简报曝光层)
    if watchdog_warning:
        lines.append(f"> **{watchdog_warning}**")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 核心进度")
    lines.append("")
    lines.append("| 指标 | 当前 | 阈值 | 进度 |")
    lines.append("|---|---|---|---|")
    lines.append(
        f"| 观察期天数 | {obs_days} / {obs_total} 天 | 21 天 | {obs_progress_pct}% |"
    )
    lines.append(
        f"| 数据样本数 | {obs_days} / {min_samples} 条 | 20 条 | "
        f"{min(100.0, round(obs_days / min_samples * 100, 1))}% |"
    )
    lines.append(
        f"| 今日数据写入 | {'YES 已写入' if has_today_data else 'NO 未写入'} | - | - |"
    )
    lines.append(f"| 决策日志累计 | {len(decisions)} 条 | - | - |")
    lines.append("")
    lines.append(
        f"**观察期剩余**: {obs_remaining} 天 "
        f"(预计 {obs_end_date.strftime('%Y-%m-%d')} 结束)"
    )
    lines.append(
        f"**评估启用预计**: {eval_ready_date.strftime('%Y-%m-%d')}"
    )
    lines.append("")

    # 最近收益曲线
    lines.append("## 最近收益曲线 (最近 5 天)")
    lines.append("")
    recent = daily_returns[-5:]
    for r in recent:
        ret = r.get("daily_return", 0.0)
        pct = round(ret * 100, 4)
        arrow = "UP" if ret > 0 else ("DOWN" if ret < 0 else "FLAT")
        src = r.get("source", "?")
        lines.append(f"- {r.get('date', '?')}: [{arrow}] {pct}% (source: {src})")
    if not recent:
        lines.append("- (无数据)")
    lines.append("")

    # Shadow 账户状态
    if shadow_state:
        lines.append("## Shadow 账户状态")
        lines.append("")
        lines.append(f"- **账户ID**: {shadow_state.get('account_id', '?')}")
        lines.append(f"- **状态**: {shadow_state.get('status', '?')}")
        lines.append(
            f"- **初始资金**: Y{shadow_state.get('initial_capital', '?')}"
        )
        lines.append(f"- **当前净值**: {shadow_state.get('current_nav', '?')}")
        lines.append(
            f"- **当前资金**: Y{shadow_state.get('current_capital', '?')}"
        )
        nav_count = len(shadow_state.get("daily_nav", []))
        lines.append(f"- **运行天数**: {nav_count}")
        if shadow_state.get("status") == "TERMINATED":
            fail_log = shadow_state.get("fail_fast_log", [])
            reason = fail_log[-1].get("reason", "unknown") if fail_log else "unknown"
            lines.append(f"- **已终止**: {reason}")
        lines.append("")

    # 任务执行状态
    lines.append("## 定时任务状态")
    lines.append("")
    lines.append("| 任务 | 上次运行 | 上次结果 |")
    lines.append("|---|---|---|")
    lines.append(
        f"| v84_PostMarket (15:30) | {postmarket['last_run']} | "
        f"{postmarket['last_result']} |"
    )
    lines.append(
        f"| v84_EvolutionEval (16:05) | - | {eval_task['last_result']} |"
    )
    lines.append(
        f"| v84_ObservationBriefing (16:10) | {now_str} | 0 (本次) |"
    )
    lines.append(
        f"| v84_ShadowAdmissionDaily (16:15) | {shadow_admission_daily_task['last_run']} | "
        f"{shadow_admission_daily_task['last_result']} |"
    )
    lines.append(
        f"| v84_ShadowAdmissionWatchdog (17:00) | {watchdog_task['last_run']} | "
        f"{watchdog_task['last_result']} |"
    )
    lines.append("")

    # 下一步动作
    lines.append("## 下一步动作")
    lines.append("")
    if status and status.get("next_action"):
        lines.append(status["next_action"])
    else:
        lines.append("(无 status.json, EvolutionEval 可能未运行)")
    lines.append("")

    # 阻塞项
    lines.append("## 阻塞项")
    lines.append("")
    blockers = status.get("blockers", []) if status else []
    if blockers:
        for b in blockers:
            lines.append(f"- {b}")
    else:
        lines.append("无")
    lines.append("")

    # 数据缺失警告
    if not has_today_data:
        lines.append("## 今日数据缺失警告")
        lines.append("")
        lines.append(f"今日 ({today_str}) 的 Shadow 数据未写入 daily_returns.jsonl")
        lines.append("")
        lines.append("**可能原因**:")
        lines.append("1. v84_PostMarket 任务失败")
        lines.append("2. Phase 10 Shadow Monitor 执行失败")
        lines.append("3. shadow_state.json 不存在或已终止")
        lines.append("")
        lines.append("**手动修复命令**:")
        lines.append("```powershell")
        lines.append('cd "E:\\各种PY程序\\28-终极量化交易系统8.4"')
        lines.append(
            f'py -3.8 "v8.3_institutional\\daily_workflow.py" '
            f'--phase shadow_monitor --date {today_str}'
        )
        lines.append("```")
        lines.append("")

    # Feature Flags
    lines.append("## Feature Flags")
    lines.append("")
    if status and status.get("feature_flags"):
        flags = status["feature_flags"]
        lines.append(f"- USE_STRATEGY_EVALUATOR: {flags.get('USE_STRATEGY_EVALUATOR', '?')}")
        lines.append(
            f"- USE_EVOLUTION_ORCHESTRATOR: {flags.get('USE_EVOLUTION_ORCHESTRATOR', '?')}"
        )
        lines.append(f"- USE_MLOPS_PIPELINE: {flags.get('USE_MLOPS_PIPELINE', '?')}")
        lines.append(f"- USE_DRIFT_DETECTOR: {flags.get('USE_DRIFT_DETECTOR', '?')}")
        lines.append(f"- USE_AUTO_RETRAIN: {flags.get('USE_AUTO_RETRAIN', '?')}")
    else:
        lines.append("(无 status.json)")
    lines.append("")

    # 相关文件
    lines.append("---")
    lines.append("")
    lines.append("## 相关文件")
    lines.append("")
    lines.append(f"- 每日播报: `{briefing_path}`")
    lines.append(f"- 最新播报: `{latest_path}`")
    lines.append(f"- 进度状态: `{STATUS_PATH}`")
    lines.append(f"- 决策日志: `{DECISIONS_PATH}`")
    lines.append(f"- Shadow数据: `{DAILY_RETURNS_PATH}`")
    lines.append(f"- Shadow状态: `{SHADOW_STATE_PATH}`")
    lines.append("")

    # 快速查看命令
    lines.append("## 快速查看命令")
    lines.append("")
    lines.append("```powershell")
    lines.append('# 查看最新播报')
    lines.append(f'Get-Content "{latest_path}" -Encoding UTF8')
    lines.append("")
    lines.append("# 查看进度快照")
    lines.append('py -3.8 "scripts\\run_evolution_eval.py" --status')
    lines.append("")
    lines.append("# 查看Shadow数据")
    lines.append(f'Get-Content "{DAILY_RETURNS_PATH}" -Encoding UTF8')
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append(f"*本播报由 v84_ObservationBriefing 定时任务自动生成 ({today_str})*")

    report = "\n".join(lines)

    # 写入文件
    briefing_path.write_text(report, encoding="utf-8")
    latest_path.write_text(report, encoding="utf-8")

    # 打印到 stdout
    print(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
