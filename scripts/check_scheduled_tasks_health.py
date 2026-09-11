#!/usr/bin/env python3
"""
计划任务健康检查与告警 (v84_* Task Watchdog)
==============================================
版本: v8.6.14
来源: 2026-09-01 代码质量扫描 P0-1 后续 — V84_DailyMorning8Report 静默失败
      连续 6 天无人察觉 (退出码 9009, 无任何告警通道)。

判定规则 (对 v84_*/V84_* 前缀任务):
    FAILED: LastResult != 0 且 != 0x41303 (任务尚未运行过, 新任务容忍)
    STALE : LastRunTime 距今 > 36h (日频任务超期未跑)

动作:
    1. 结果原子写 reports/scheduled_tasks_health.json (供 UI/复盘)
    2. 存在 FAILED/STALE 时经 utils.notify 发外部告警
       (钉钉/飞书 webhook, 未配置时降级为日志 — fail-open 不崩溃)
    3. exit 0 = 全部健康, exit 1 = 存在异常

用法:
    .venv\\Scripts\\python.exe scripts\\check_scheduled_tasks_health.py
    (由计划任务 V84_TaskHealthCheck 每日 09:05 调用, 晨报 08:00 后及时发现问题)
"""

from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

REPORT_PATH = PROJECT_ROOT / "reports" / "scheduled_tasks_health.json"

# 0x41303 = 267011 "任务尚未运行" (新注册/未到触发时间, 容忍)
_SKIPPED_RESULT_CODES = {0, 267011}
# 排除自身: 健康检查发现异常时 exit 1, 不排除会把自己报成 FAILED (自我告警死循环)
_SELF_TASK = "V84_TaskHealthCheck"
# 日频任务超期阈值 (小时); 36h = 一个完整交易日 + 缓冲
_STALE_HOURS = 36

# 周任务 (每周四) 超期阈值放宽
_WEEKLY_TASKS = {"V84_WeeklyReport"}
_WEEKLY_STALE_HOURS = 8 * 24


def _query_tasks() -> list[dict[str, str]]:
    """schtasks 全量查询, 返回 CSV 行列表."""
    result = subprocess.run(
        ["schtasks", "/query", "/fo", "csv", "/v"],
        capture_output=True, text=True, encoding="gbk", errors="replace", timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"schtasks 查询失败: {result.stderr[:200]}")
    reader = csv.DictReader(io.StringIO(result.stdout))
    return [dict(row) for row in reader]


def _parse_time(s: str) -> datetime | None:
    """解析 schtasks 时间串 '2026/9/2 8:00:00' (无前导零), 失败返回 None."""
    s = (s or "").strip()
    if not s or s == "N/A" or s == "不可用":
        return None
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            # 1999-11-30 是 schtasks 的"从未运行"零值标记, 视为 None
            if dt.year < 2000:
                return None
            return dt
        except ValueError:
            continue
    return None


def check() -> tuple[list[dict], list[dict]]:
    """返回 (异常列表, 全量快照)."""
    rows = _query_tasks()
    now = datetime.now()
    problems: list[dict] = []
    snapshot: list[dict] = []

    for row in rows:
        task_name = (row.get("TaskName") or row.get("任务名") or "").strip().strip('"')
        if not task_name.startswith(("\\v84_", "\\V84_")):
            continue
        short = task_name.lstrip("\\")
        if short == _SELF_TASK:
            continue  # 排除自身 (见 _SELF_TASK 注释)

        # 跳过禁用任务 (如 v84_PreMarket 孤儿任务 / 已废弃功能)
        state = (
            row.get("Scheduled Task State") or row.get("计划任务状态") or ""
        ).strip()
        if "禁用" in state or "Disabled" in state.lower():
            continue

        last_result_raw = (
            row.get("Last Result") or row.get("上次结果") or ""
        ).strip()
        try:
            last_result = int(last_result_raw, 0)  # 支持 0x 十六进制
        except ValueError:
            last_result = -1

        last_run = _parse_time(row.get("Last Run Time") or row.get("上次运行时间") or "")
        stale_hours = _WEEKLY_STALE_HOURS if short in _WEEKLY_TASKS else _STALE_HOURS
        is_stale = last_run is not None and (now - last_run) > timedelta(hours=stale_hours)

        entry = {
            "task": short,
            "last_result": f"0x{last_result & 0xFFFFFFFF:08X}" if last_result >= 0 else last_result_raw,
            "last_run": last_run.isoformat() if last_run else None,
            "next_run": (row.get("Next Run Time") or row.get("下次运行时间") or "").strip(),
            "status": "OK",
        }

        if last_result not in _SKIPPED_RESULT_CODES:
            entry["status"] = "FAILED"
            entry["problem"] = f"上次退出码 {entry['last_result']}"
        elif is_stale:
            entry["status"] = "STALE"
            entry["problem"] = f"上次运行 {last_run:%Y-%m-%d %H:%M} (超 {stale_hours}h)"

        if entry["status"] != "OK":
            problems.append(entry)
        snapshot.append(entry)

    return problems, snapshot


def main() -> int:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    try:
        problems, snapshot = check()
    except (OSError, ValueError, TypeError, KeyError, IndexError) as e:  # fail-safe: 检查自身异常不崩溃
        print(f"[TaskHealth] ❌ 检查异常: {e}", file=sys.stderr)
        return 2

    # 落盘快照 (原子写)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REPORT_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(
            {"checked_at": datetime.now().isoformat(), "tasks": snapshot},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    tmp.replace(REPORT_PATH)

    ok_count = len(snapshot) - len(problems)
    print(f"[TaskHealth] 检查 {len(snapshot)} 个 v84 任务: {ok_count} OK, {len(problems)} 异常")
    print(f"[TaskHealth] 快照: {REPORT_PATH}")

    if problems:
        lines = [f"· {p['task']}: {p['problem']}" for p in problems]
        print("[TaskHealth] ❌ 异常任务:", file=sys.stderr)
        for line in lines:
            print(f"  {line}", file=sys.stderr)

        # 外部告警 (钉钉/飞书 webhook, 未配置时降级为日志)
        try:
            from utils.notify import send_alert

            send_alert(
                "计划任务异常告警",
                f"{len(problems)}/{len(snapshot)} 个 v84 计划任务异常:\n" + "\n".join(lines),
                level="critical",
            )
        except (ImportError, OSError, ValueError, TypeError, KeyError) as e:  # 告警失败不改变检查结论
            print(f"[TaskHealth] 告警发送异常 (不影响检查结论): {e}", file=sys.stderr)
        return 1

    print("[TaskHealth] ✅ 全部健康")
    return 0


if __name__ == "__main__":
    sys.exit(main())
