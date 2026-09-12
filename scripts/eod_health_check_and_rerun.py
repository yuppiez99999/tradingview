#!/usr/bin/env python3
"""
EOD 健康检查 + 自动重跑 (08-21 保障机制)
================================================================
用途: 兜底检查每日 EOD 是否成功写入 shadow 收益数据, 失败则自动重跑.
      注册为计划任务 v84_EOD_Fallback, 每日 16:00 触发 (主任务 15:30 后 30 分钟).

检查逻辑:
  1. 读取 reports/shadow/daily_returns.jsonl 最新记录日期
  2. 若最新日期 >= 目标日期 → 成功, 退出 0
  3. 若缺失 → 重跑 run_daily_eod_workflow.py --date <目标日期>
  4. 重跑后再检查, 最多重试 max_retries 次, 每次间隔 retry_interval 秒
  5. 全部失败 → 写告警日志 + 退出 1

用法:
  python eod_health_check_and_rerun.py                      # 检查今日
  python eod_health_check_and_rerun.py --date 2026-08-21    # 检查指定日期
  python eod_health_check_and_rerun.py --dry-run            # 只检查不重跑
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SHADOW_RETURNS = PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
EOD_SCRIPT = PROJECT_ROOT / "15_每日工作流" / "run_daily_eod_workflow.py"
FALLBACK_LOG = PROJECT_ROOT / "logs" / "eod_fallback.log"
ALERT_FILE = PROJECT_ROOT / "reports" / "shadow" / "eod_fallback_alert.json"

VENV_PYTHON = sys.executable

# SYSTEM 用户下补齐 Administrator user site-packages + 项目 tools/ 目录
# 根因: urllib3 装在 Administrator user site, wind_mcp_fetcher 在 tools/
_ADMIN_USER_SITE = os.environ.get("ADMIN_USER_SITE", "")
_TOOLS_DIR = str(PROJECT_ROOT / "tools")
_SRC_DIR = str(PROJECT_ROOT / "src")
_EXTRA_PATHS = [p for p in (_ADMIN_USER_SITE, _TOOLS_DIR, _SRC_DIR) if p]


def _build_env() -> dict:
    """构造子进程环境变量, 补齐 SYSTEM 用户缺失的 PYTHONPATH."""
    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    parts = [p for p in existing_pp.split(os.pathsep) if p] + _EXTRA_PATHS
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def log(msg: str) -> None:
    ts = now_bj().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        FALLBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
        with FALLBACK_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_latest_shadow_date() -> str | None:
    """读取 daily_returns.jsonl 最新记录日期."""
    if not SHADOW_RETURNS.exists():
        return None
    latest = None
    try:
        with SHADOW_RETURNS.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    d = rec.get("date")
                    if d and (latest is None or d > latest):
                        latest = d
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        log(f"读取 shadow 数据失败: {exc}")
        return None
    return latest


def run_eod(target_date: str, skip_system_check: bool = True) -> bool:
    """重跑 EOD 工作流, 返回是否成功."""
    cmd = [VENV_PYTHON, str(EOD_SCRIPT), "--date", target_date]
    if skip_system_check:
        cmd.append("--skip-system-check")
    log(f"重跑 EOD: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=600,
            encoding="utf-8",
            errors="replace",
            env=_build_env(),
        )
        exit_code = result.returncode
        if exit_code == 0:
            log("EOD 重跑成功 (exit=0)")
            return True
        log(f"EOD 重跑失败 (exit={exit_code})")
        if result.stderr:
            log(f"STDERR 末尾: {result.stderr[-500:]}")
        return False
    except subprocess.TimeoutExpired:
        log("EOD 重跑超时 (600s)")
        return False
    except Exception as exc:
        log(f"EOD 重跑异常: {exc}")
        return False


def write_alert(target_date: str, latest_date: str | None, retries: int) -> None:
    """写告警 JSON."""
    try:
        ALERT_FILE.parent.mkdir(parents=True, exist_ok=True)
        alert = {
            "alert_time": now_bj().isoformat(),
            "target_date": target_date,
            "latest_shadow_date": latest_date,
            "retries_attempted": retries,
            "status": "EOD_FAILED",
            "action_required": "手动检查 v84_PostMarket 计划任务 + 数据源连通性",
        }
        ALERT_FILE.write_text(
            json.dumps(alert, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log(f"告警已写入: {ALERT_FILE}")
    except Exception as exc:
        log(f"写告警失败: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="EOD 健康检查 + 自动重跑")
    parser.add_argument("--date", help="目标日期 YYYY-MM-DD (默认今日)")
    parser.add_argument("--dry-run", action="store_true", help="只检查不重跑")
    parser.add_argument(
        "--max-retries", type=int, default=2, help="最大重试次数 (默认 2)"
    )
    parser.add_argument(
        "--retry-interval", type=int, default=60, help="重试间隔秒 (默认 60)"
    )
    parser.add_argument(
        "--no-skip-system-check", action="store_true", help="不跳过系统检查"
    )
    args = parser.parse_args()

    target = args.date or now_bj().strftime("%Y-%m-%d")
    skip_sc = not args.no_skip_system_check

    log("=" * 60)
    log(f"EOD 健康检查启动 | 目标日期={target} | dry_run={args.dry_run}")
    log(
        f"max_retries={args.max_retries} | retry_interval={args.retry_interval}s | skip_system_check={skip_sc}"
    )
    log("=" * 60)

    latest = get_latest_shadow_date()
    log(f"当前 shadow 最新日期: {latest}")

    if latest and latest >= target:
        log(f"✅ 检查通过: shadow 最新日期 {latest} >= 目标 {target}")
        return 0

    log(f"❌ 检查失败: shadow 最新日期 {latest} < 目标 {target}")

    if args.dry_run:
        log("dry-run 模式, 不重跑")
        return 1

    for attempt in range(1, args.max_retries + 1):
        log(f"--- 重试 {attempt}/{args.max_retries} ---")
        if attempt > 1:
            log(f"等待 {args.retry_interval}s 后重试...")
            time.sleep(args.retry_interval)

        success = run_eod(target, skip_system_check=skip_sc)
        if not success:
            continue

        latest = get_latest_shadow_date()
        log(f"重跑后 shadow 最新日期: {latest}")
        if latest and latest >= target:
            log(f"✅ 重跑成功: shadow 最新日期 {latest} >= 目标 {target}")
            return 0

    log(f"❌ 全部 {args.max_retries} 次重试失败")
    write_alert(target, latest, args.max_retries)
    return 1


if __name__ == "__main__":
    sys.exit(main())
