#!/usr/bin/env python3
"""门禁三件套每日聚合 (v84_GateCheckDaily 调度目标).

每日 EOD 后运行 industrial_grade_check + assert_data_validity +
engineering_debt_gate, 聚合结果落盘到 reports/gate/gate_daily_YYYY-MM-DD.json,
并维护 reports/gate/gate_streak.json 的 21 天连续 0-FAIL 计数
(对齐 cairn/live-trading-admission-criteria-20260811.md 硬性门槛).

fail-open: 单个门禁崩溃不影响其余门禁记录; 仅记日志.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

from utils.datetime_utils import now_bj

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("gate_check_daily")

GATE_DIR = _PROJECT_ROOT / "reports" / "gate"
GATE_SCRIPTS = [
    "scripts/industrial_grade_check.py",
    "scripts/assert_data_validity.py",
    "scripts/engineering_debt_gate.py",
]
STREAK_FILE = GATE_DIR / "gate_streak.json"
REQUIRED_STREAK_DAYS = 21


def _run_gate(script_rel: str) -> dict:
    """运行单个门禁脚本, 返回结构化结果 (fail-open)."""
    name = Path(script_rel).stem
    script_path = _PROJECT_ROOT / script_rel
    if not script_path.exists():
        return {
            "name": name,
            "ok": False,
            "exit_code": -1,
            "error": f"script missing: {script_rel}",
        }
    try:
        proc = subprocess.run(
            [
                str(_PROJECT_ROOT / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python")),  # noqa: E501
                str(script_path),
            ],
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1200,
        )
        return {
            "name": name,
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout_tail": (proc.stdout or "")[-500:],
            "stderr_tail": (proc.stderr or "")[-300:],
        }
    except (subprocess.TimeoutExpired, OSError, ValueError) as e:
        return {"name": name, "ok": False, "exit_code": -2, "error": str(e)}


def run_all(date_str: str) -> dict:
    GATE_DIR.mkdir(parents=True, exist_ok=True)
    results = [_run_gate(s) for s in GATE_SCRIPTS]
    all_ok = all(r["ok"] for r in results)
    summary = {
        "date": date_str,
        "all_ok": all_ok,
        "gates": results,
        "checked_at": now_bj().isoformat(),
    }
    out_file = GATE_DIR / f"gate_daily_{date_str}.json"
    out_file.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _update_streak(all_ok, date_str)
    return summary


def _update_streak(all_ok: bool, date_str: str) -> None:
    """维护连续 21 天 0-FAIL 计数."""
    streak = {"current_streak": 0, "best_streak": 0, "last_date": None, "history": []}
    if STREAK_FILE.exists():
        try:
            streak = json.loads(STREAK_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    history = streak.get("history", [])
    # 避免同日重复计数
    if history and history[-1].get("date") == date_str:
        history.pop()

    if all_ok:
        streak["current_streak"] = streak.get("current_streak", 0) + 1
    else:
        streak["current_streak"] = 0
    streak["best_streak"] = max(streak.get("best_streak", 0), streak["current_streak"])
    streak["last_date"] = date_str
    history.append({"date": date_str, "ok": all_ok})
    # 仅保留最近 60 天
    streak["history"] = history[-60:]
    streak["required_days"] = REQUIRED_STREAK_DAYS
    streak["meets_admission"] = streak["current_streak"] >= REQUIRED_STREAK_DAYS

    STREAK_FILE.write_text(
        json.dumps(streak, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "门禁连续达标: %d/%d 天 (best=%d, 今日=%s)",
        streak["current_streak"],
        REQUIRED_STREAK_DAYS,
        streak["best_streak"],
        "PASS" if all_ok else "FAIL",
    )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="门禁三件套每日聚合")
    parser.add_argument(
        "--date",
        type=str,
        default=now_bj().strftime("%Y-%m-%d"),
        help="运行日期 YYYY-MM-DD (默认今天)",
    )
    args = parser.parse_args()
    summary = run_all(args.date)
    # fail-open: 即使有门禁 FAIL 也返回 0, 不阻断上游管道
    logger.info(
        "门禁聚合完成: all_ok=%s, 结果=%s",
        summary["all_ok"],
        GATE_DIR / f"gate_daily_{args.date}.json",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
