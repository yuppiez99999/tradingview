"""Shadow Admission Watchdog — DSR 自愈与告警机制

反馈机制 (2026-07-30 设计):
    主任务 v84_ShadowAdmissionDaily (16:15) 跑 shadow_admission_launcher.py daily 生成 DSR.
    若主任务未跑成功, 本 watchdog (17:00) 检测并补跑; 补跑仍失败则告警.

工作流:
    1. 30s 退避避免主任务竞态
    2. 加载 admission_state.json → 检查 fail_fast_triggered / 文件存在性
    3. outcome check: reports/shadow/{today}_dsr.json 是否存在且 JSON 合法且 date 匹配
    4. 若主任务正在跑 (admission_state.json mtime < 5min) → 跳过
    5. 触发补跑: python scripts/shadow_admission_launcher.py daily
    6. 复检 outcome → 成功则 recovered; 失败则告警

退出码 (对齐 shadow_admission_launcher.py 惯例):
    0 = 健康 / 已恢复 / 主动跳过 (fail-fast/未启动/主任务在跑)
    1 = 需人工介入 (补跑失败 / DSR 损坏)
    2 = watchdog 自身配置错误

自监控 (三层防静默死亡):
    - 心跳: logs/shadow_watchdog_heartbeat.jsonl (每次运行 append)
    - 告警: logs/shadow_watchdog_alerts.jsonl (仅告警条目)
    - 次日简报: observation_daily_briefing.py 拾取心跳缺失并红字曝光

用法:
    # 手动调试 (跳过 30s 退避)
    python scripts/shadow_admission_watchdog.py --no-sleep

    # 定时任务 (17:00 自动触发)
    # 已注册为 v84_ShadowAdmissionWatchdog (见 register_all_tasks_unified.ps1)
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# ============================================================
# 常量与路径
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHADOW_DIR = PROJECT_ROOT / "reports" / "shadow"
STATE_FILE = SHADOW_DIR / "admission_state.json"
LOGS_DIR = PROJECT_ROOT / "logs"
HEARTBEAT_FILE = LOGS_DIR / "shadow_watchdog_heartbeat.jsonl"
ALERTS_FILE = LOGS_DIR / "shadow_watchdog_alerts.jsonl"
LAUNCHER_SCRIPT = PROJECT_ROOT / "scripts" / "shadow_admission_launcher.py"
MAIN_TASK_NAME = "v84_ShadowAdmissionDaily"

# NO_PROXY (项目硬约束: akshare/requests 前必须设置, 否则系统代理拒绝国内金融 API)
NO_PROXY_VALUE = (
    "push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,"
    "sinajs.cn,sina.com.cn"
)

# 退避时间 (秒) — 避免与主任务竞态
BACKOFF_SECONDS = 30

# 主任务运行检测阈值 (秒) — admission_state.json mtime 在此窗口内认为主任务正在跑
MAIN_TASK_RUNNING_THRESHOLD = 300

# 补跑超时 (秒)
RETRY_TIMEOUT = 300

# schtasks 状态码翻译表 (常见的几个, 其余返回 "未知状态码 N")
TASK_RESULT_CODES = {
    "0": "成功",
    "1": "脚本退出码 1 (失败)",
    "2": "脚本退出码 2 (用法错误)",
    "267009": "SCHED_E_TASK_IS_RUNNING (任务正在运行)",
    "267011": "SCHED_E_TASK_HAS_NOT_RUN (任务从未运行)",
    "267012": "SCHED_E_SERVICE_NOT_RUNNING (任务计划服务未运行)",
    "267014": "SCHED_E_TASK_NOT_READY (任务未就绪)",
    "267015": "SCHED_E_TASK_NOT_SCHEDULED (任务未调度)",
}

logger = logging.getLogger("shadow_admission_watchdog")


# ============================================================
# 工具函数
# ============================================================
def today_str() -> str:
    """今日日期字符串 (本地时区, YYYY-MM-DD)"""
    return datetime.now().strftime("%Y-%m-%d")


def now_iso() -> str:
    """当前本地时间 ISO 字符串"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def load_state(state_file: Path) -> dict[str, Any] | None:
    """加载状态文件 (复用 shadow_admission_launcher.py:108-117 模式).

    Returns:
        状态字典; 文件不存在或解析失败时返回 None
    """
    if not state_file.exists():
        return None
    try:
        with open(state_file, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("状态文件读取失败: %s (%s)", state_file, e)
        return None


def check_dsr_outcome(report_dir: Path, today: str) -> str:
    """检查今日 DSR 报告的产出结果 (outcome-based check).

    Args:
        report_dir: 报告目录 (reports/shadow/)
        today: 今日日期字符串 (YYYY-MM-DD)

    Returns:
        "OK"       — 文件存在 + JSON 合法 + date 字段匹配 today
        "MISSING"  — 文件不存在
        "CORRUPT"  — 文件存在但 JSON 损坏
        "STALE"    — JSON 合法但 date 字段不匹配 today
    """
    dsr_path = report_dir / f"{today}_dsr.json"
    if not dsr_path.exists():
        return "MISSING"
    try:
        with open(dsr_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return "CORRUPT"
    if data.get("date") != today:
        return "STALE"
    return "OK"


def query_task_status(task_name: str) -> dict[str, str]:
    """查询 Windows 任务计划程序状态 (复制自 observation_daily_briefing.py:56-77).

    Args:
        task_name: 任务名 (如 "v84_ShadowAdmissionDaily")

    Returns:
        {"last_run": str, "last_result": str, "next_run": str}
        异常时各字段含 "error: ..." 信息
    """
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", task_name, "/V", "/FO", "LIST"],
            capture_output=True,
            text=True,
            encoding="gbk",
            errors="replace",  # 防止中文任务描述触发 UnicodeDecodeError
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


def translate_task_result(result_str: str) -> str:
    """翻译 schtasks Last Result 状态码为人类可读描述.

    Args:
        result_str: 状态码字符串 (如 "0", "267011")

    Returns:
        人类可读描述
    """
    return TASK_RESULT_CODES.get(result_str, f"未知状态码 {result_str}")


def is_main_task_running(state_file: Path, threshold_seconds: int = MAIN_TASK_RUNNING_THRESHOLD) -> bool:
    """检测主任务是否正在跑 (基于 admission_state.json mtime).

    主任务 cmd_daily 末尾会 _save_state() 写 admission_state.json.
    若文件最近 threshold_seconds 内被修改过, 认为主任务正在跑, 跳过本次 watchdog.

    Args:
        state_file: admission_state.json 路径
        threshold_seconds: mtime 窗口阈值 (默认 300 秒 = 5 分钟)

    Returns:
        True = 主任务正在跑 (应跳过); False = 主任务未在跑或文件不存在
    """
    if not state_file.exists():
        return False
    try:
        mtime = state_file.stat().st_mtime
        age_seconds = time.time() - mtime
        return age_seconds < threshold_seconds
    except OSError:
        return False


def run_retry(python_exe: str, launcher_script: Path, cwd: Path) -> dict[str, Any]:
    """触发补跑: 调用 shadow_admission_launcher.py daily.

    显式设置 NO_PROXY 环境变量 (项目硬约束).
    encoding="utf-8" + errors="replace" 防止中文日志 UnicodeDecodeError.

    Args:
        python_exe: Python 解释器路径 (sys.executable)
        launcher_script: shadow_admission_launcher.py 绝对路径
        cwd: 工作目录 (项目根)

    Returns:
        {"returncode": int, "stdout": str, "stderr": str, "timed_out": bool}
    """
    env = {**os.environ, "NO_PROXY": NO_PROXY_VALUE}
    try:
        result = subprocess.run(
            [python_exe, str(launcher_script), "daily"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=RETRY_TIMEOUT,
            env=env,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "returncode": -1,
            "stdout": (e.stdout or "") if isinstance(e.stdout, str) else "",
            "stderr": (e.stderr or "") if isinstance(e.stderr, str) else "",
            "timed_out": True,
        }
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        return {
            "returncode": -2,
            "stdout": "",
            "stderr": f"watchdog retry exception: {type(e).__name__}: {e}",
            "timed_out": False,
        }


def write_heartbeat(heartbeat_file: Path, outcome: str, exit_code: int) -> None:
    """追加一条心跳记录到 JSONL 文件.

    Args:
        heartbeat_file: logs/shadow_watchdog_heartbeat.jsonl
        outcome: 运行结果 ("healthy" / "recovered" / "alerted" /
                 "skipped_running" / "skipped_fail_fast" / "skipped_not_started")
        exit_code: 退出码 (0/1/2)
    """
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": now_iso(),
        "outcome": outcome,
        "exit_code": exit_code,
    }
    with open(heartbeat_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_alert(
    report_dir: Path,
    today: str,
    reason: str,
    task_info: dict[str, str] | None,
    retry_result: dict[str, Any] | None,
    level: str = "CRITICAL",
) -> Path:
    """写告警文件 + append 到告警归档.

    产出两个文件:
    1. reports/shadow/watchdog_alert_{today}.json — 当日告警单文件 (便于人工打开)
    2. logs/shadow_watchdog_alerts.jsonl — 告警历史归档 (对齐 kill_switch_events.jsonl 惯例)

    Args:
        report_dir: reports/shadow/ 目录
        today: 今日日期
        reason: 告警原因码 ("dsr_missing_after_retry" / "observation_not_started" / ...)
        task_info: query_task_status() 返回值 (可 None)
        retry_result: run_retry() 返回值 (可 None)
        level: 告警级别 ("CRITICAL" / "WARN")

    Returns:
        告警单文件路径
    """
    alert_path = report_dir / f"watchdog_alert_{today}.json"
    report_dir.mkdir(parents=True, exist_ok=True)
    ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)

    alert = {
        "alert_date": today,
        "alert_level": level,
        "detected_at": now_iso(),
        "reason": reason,
        "task_status": {
            **(task_info or {}),
            "translated_result": translate_task_result(task_info["last_result"]) if task_info else None,
        } if task_info else None,
        "retry_result": {
            "returncode": retry_result["returncode"],
            "timed_out": retry_result["timed_out"],
            "stdout_last_500_chars": (retry_result["stdout"] or "")[-500:],
            "stderr_last_500_chars": (retry_result["stderr"] or "")[-500:],
        } if retry_result else None,
        "action_required": (
            "人工运行: python scripts/shadow_admission_launcher.py daily; "
            "检查 admission_state.json 与 logs/; "
            "若反复失败, 检查 SYSTEM 账户权限 / Python 路径 / NO_PROXY 设置"
        ),
    }

    # 1. 单文件 (便于人工打开)
    with open(alert_path, "w", encoding="utf-8") as f:
        json.dump(alert, f, ensure_ascii=False, indent=2)

    # 2. JSONL 归档 (对齐 kill_switch_events.jsonl 惯例)
    with open(ALERTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(alert, ensure_ascii=False) + "\n")

    return alert_path


# ============================================================
# 主流程
# ============================================================
def main() -> int:
    """Watchdog 主入口.

    Returns:
        0 = 健康 / 已恢复 / 主动跳过
        1 = 需人工介入
        2 = watchdog 自身配置错误
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # --no-sleep 标志用于手动调试跳过 30s 退避
    no_sleep = "--no-sleep" in sys.argv

    today = today_str()
    logger.info("=" * 70)
    logger.info("Shadow Admission Watchdog — 检测+补跑+告警 (%s)", today)
    logger.info("=" * 70)

    # 1. 退避避免主任务竞态 (定时任务触发时)
    if not no_sleep:
        logger.info("[BACKOFF] 退避 %ds 避免主任务竞态...", BACKOFF_SECONDS)
        time.sleep(BACKOFF_SECONDS)
    else:
        logger.info("[BACKOFF] --no-sleep 模式, 跳过退避")

    # 2. 检查 state 是否存在 (观察期是否已启动)
    state = load_state(STATE_FILE)
    if state is None:
        logger.info("[SKIP] admission_state.json 不存在, 观察期未启动")
        write_heartbeat(HEARTBEAT_FILE, "skipped_not_started", 0)
        write_alert(
            SHADOW_DIR, today,
            reason="observation_not_started",
            task_info=None, retry_result=None,
            level="WARN",
        )
        return 0

    # 3. fail_fast 已触发 → 跳过 (观察期已终止, 不再生成 DSR)
    if state.get("fail_fast_triggered"):
        logger.info("[SKIP] fail-fast 已触发, 观察期已终止, 无需补跑")
        write_heartbeat(HEARTBEAT_FILE, "skipped_fail_fast", 0)
        return 0

    # 4. outcome check (主检测)
    outcome = check_dsr_outcome(SHADOW_DIR, today)
    logger.info("[CHECK] DSR outcome: %s", outcome)
    if outcome == "OK":
        logger.info("[OK] 今日 DSR 已生成, 无需补跑")
        write_heartbeat(HEARTBEAT_FILE, "healthy", 0)
        return 0

    # 5. 主任务正在跑? (基于 admission_state.json mtime)
    if is_main_task_running(STATE_FILE):
        logger.info("[SKIP] 主任务 v84_ShadowAdmissionDaily 正在跑 (admission_state.json mtime < %ds), 跳过本次检查",
                    MAIN_TASK_RUNNING_THRESHOLD)
        write_heartbeat(HEARTBEAT_FILE, "skipped_running", 0)
        return 0

    # 6. 触发补跑
    logger.info("[WARN] 今日 DSR 缺失 (%s), 触发补跑...", outcome)
    task_info = query_task_status(MAIN_TASK_NAME)
    logger.info("[INFO] 主任务状态: last_run=%s, last_result=%s (%s)",
                task_info["last_run"], task_info["last_result"],
                translate_task_result(task_info["last_result"]))

    retry_result = run_retry(sys.executable, LAUNCHER_SCRIPT, PROJECT_ROOT)
    logger.info("[RETRY] 补跑结果: returncode=%d, timed_out=%s",
                retry_result["returncode"], retry_result["timed_out"])

    # 7. 复检 outcome
    outcome2 = check_dsr_outcome(SHADOW_DIR, today)
    if outcome2 == "OK":
        logger.info("[OK] 补跑成功, DSR 已生成")
        write_heartbeat(HEARTBEAT_FILE, "recovered", 0)
        return 0

    # 8. 告警
    logger.info("[CRITICAL] 补跑后 DSR 仍缺失 (outcome=%s), 写告警", outcome2)
    alert_path = write_alert(
        SHADOW_DIR, today,
        reason=f"dsr_missing_after_retry (outcome_before={outcome}, outcome_after={outcome2})",
        task_info=task_info,
        retry_result=retry_result,
        level="CRITICAL",
    )
    logger.info("[CRITICAL] 告警文件: %s", alert_path)
    logger.info("[CRITICAL] 需人工介入: python scripts/shadow_admission_launcher.py daily")
    write_heartbeat(HEARTBEAT_FILE, "alerted", 1)
    return 1


if __name__ == "__main__":
    sys.exit(main())
