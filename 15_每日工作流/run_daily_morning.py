"""
每日早晨工作流脚本 — 每天早上7:00自动运行
功能：
  1. 检查是否为交易日
  2. 运行盘前市场校准与风险评估
  3. 生成每日交易计划
  4. 应用大模型决策到交易计划
  5. 生成每日综合报告
  6. 将所有报告归档至 每日报告归档/YYYY-MM-DD/

使用方式：
  python run_daily_morning.py                    # 标准运行
  python run_daily_morning.py --force            # 强制运行（忽略交易日检查）
  python run_daily_morning.py --dry-run          # 试运行（不实际执行）
  python run_daily_morning.py --phase calibrate   # 仅校准阶段
"""

import os
import sys
import json
import shutil
import subprocess
import argparse
import traceback
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

# 强制 UTF-8 输出，解决 GBK 编码问题
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.stderr.encoding != 'utf-8':
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════
# 项目路径配置
# ═══════════════════════════════════════════════════════════════
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
ARCHIVE_DIR = PROJECT_ROOT / "每日报告归档"
VENV_PYTHON = r"C:\Python312\python.exe"  # 默认Python路径，可按需修改

# 关键脚本路径
DAILY_WORKFLOW_SCRIPT = PROJECT_ROOT / "v8.3_institutional" / "daily_workflow.py"
GENERATE_TRADE_PLAN_SCRIPT = PROJECT_ROOT / "v8.3_institutional" / "generate_daily_trade_plan.py"
APPLY_LLM_SCRIPT = PROJECT_ROOT / "tools" / "apply_llm_decisions_to_plan.py"
GENERATE_REPORT_SCRIPT = PROJECT_ROOT / "generate_daily_report.py"
RUN_DAILY_EOD_SCRIPT = PROJECT_ROOT / "run_daily_eod.py"
CALENDAR_FILE = PROJECT_ROOT / "v8.3_institutional" / "data" / "trading_calendar.json"

# 可能输出的报告文件列表（用于归档）
REPORT_PATTERNS = [
    "每日综合报告_*.md",
    "组合总盈亏报告_*.md",
    "交易计划_*.md",
    "市场环境分析_*.md",
    "风险评估_*.md",
    "信号监控_*.md",
    "daily_report_*.md",
    "pre_market_*.md",
    "calibrate_report_*.md",
]

# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def get_python() -> str:
    """获取Python解释器路径"""
    if os.path.exists(VENV_PYTHON):
        return VENV_PYTHON
    return sys.executable


def get_log_file() -> Path:
    """获取日志文件路径"""
    today = datetime.now().strftime("%Y%m%d")
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"daily_morning_{today}.log"


def log(msg: str, level: str = "INFO"):
    """写日志到文件并打印"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{level}] {msg}"
    print(line)
    try:
        log_file = get_log_file()
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def is_trading_day() -> bool:
    """检查今天是否为交易日"""
    # 周末直接返回False
    weekday = datetime.now().weekday()
    if weekday >= 5:
        log(f"今天是周末 (weekday={weekday})，非交易日")
        return False

    # 检查交易日历文件
    today_str = datetime.now().strftime("%Y-%m-%d")
    if CALENDAR_FILE.exists():
        try:
            with open(CALENDAR_FILE, "r", encoding="utf-8") as f:
                calendar = json.load(f)
            trading_days = calendar.get("trading_days", []) or calendar.get("交易日", [])
            if isinstance(trading_days, list) and len(trading_days) > 0:
                is_trading = today_str in trading_days
                log(f"交易日历检查: {today_str} -> {'交易日' if is_trading else '非交易日'}")
                return is_trading
        except Exception as e:
            log(f"交易日历读取失败: {e}，按工作日处理", "WARN")

    # 没有交易日历默认按工作日处理（周一到周五）
    log(f"未找到交易日历，默认按工作日处理")
    return True


def run_step(name: str, script: Path, args: list, timeout_minutes: int = 30) -> bool:
    """
    运行一个工作流步骤
    返回 True 表示成功，False 表示失败
    """
    python = get_python()
    cmd = [python, str(script)] + args

    log(f"{'='*60}")
    log(f">>> 开始执行: {name}")
    log(f">>> 命令: {' '.join(cmd)}")
    log(f"{'='*60}")

    if not script.exists():
        log(f"[FAIL] 脚本不存在: {script}", "ERROR")
        return False

    try:
        # 清理可能干扰子进程的环境变量，并强制UTF-8编码
        env = os.environ.copy()
        env.pop('PYTHONHOME', None)
        env.pop('PYTHONPATH', None)
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUTF8'] = '1'

        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_minutes * 60,
            env=env,
        )

        # 记录输出
        if result.stdout:
            # 截断过长输出
            stdout_preview = result.stdout[:5000]
            if len(result.stdout) > 5000:
                stdout_preview += f"\n... [截断，共{len(result.stdout)}字符]"
            log(f"STDOUT:\n{stdout_preview}")

        if result.stderr:
            stderr_preview = result.stderr[:3000]
            if len(result.stderr) > 3000:
                stderr_preview += f"\n... [截断，共{len(result.stderr)}字符]"
            log(f"STDERR:\n{stderr_preview}")

        if result.returncode == 0:
            log(f"[OK] {name} 执行成功 (exit_code=0)")
            return True
        else:
            log(f"[FAIL] {name} 执行失败 (exit_code={result.returncode})", "ERROR")
            return False

    except subprocess.TimeoutExpired:
        log(f"[FAIL] {name} 执行超时 (>{timeout_minutes}分钟)", "ERROR")
        return False
    except Exception as e:
        log(f"[FAIL] {name} 执行异常: {e}", "ERROR")
        traceback.print_exc()
        return False


def archive_reports(today_dir: Path) -> int:
    """
    扫描项目目录中的报告文件，归档到指定目录
    返回归档文件数量
    """
    archived_count = 0
    today_dir.mkdir(parents=True, exist_ok=True)

    # 可能产生报告的关键目录
    search_dirs = [
        PROJECT_ROOT,
        PROJECT_ROOT / "v8.3_institutional",
        PROJECT_ROOT / "v8.3_institutional" / "reports",
        PROJECT_ROOT / "v8.3_institutional",
        PROJECT_ROOT / "v8.3_institutional" / "reports",
        SCRIPT_DIR,
    ]

    today_str_compact = datetime.now().strftime("%Y%m%d")
    today_str_dash = datetime.now().strftime("%Y-%m-%d")

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        try:
            for file_path in search_dir.iterdir():
                if not file_path.is_file():
                    continue
                fname = file_path.name

                # 检查文件名是否包含今天的日期
                is_today_report = (
                    today_str_compact in fname or
                    today_str_dash in fname or
                    fname.endswith(f"_{today_str_compact}.md") or
                    fname.endswith(f"_{today_str_compact}.json") or
                    fname.endswith(f"_{today_str_dash}.md") or
                    fname.endswith(f"_{today_str_dash}.json")
                )

                # 额外检查：最近修改时间在今天
                if not is_today_report:
                    mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                    if mtime.strftime("%Y-%m-%d") == today_str_dash:
                        # 可能是没有日期标记的报告文件
                        is_today_report = any(
                            fname.startswith(pat.replace("_*.md", ""))
                            or fname.endswith(".md")
                            for pat in [
                                "每日综合报告", "组合总盈亏报告", "交易计划",
                                "市场环境分析", "风险评估", "信号监控",
                                "daily_report", "pre_market", "calibrate_report",
                            ]
                        )

                if is_today_report:
                    dest = today_dir / fname
                    if not dest.exists() or file_path.stat().st_mtime > dest.stat().st_mtime:
                        shutil.copy2(file_path, dest)
                        archived_count += 1
                        log(f"  归档: {file_path.name} -> {today_dir.name}/")

        except Exception as e:
            log(f"  扫描目录异常 {search_dir}: {e}", "WARN")

    return archived_count


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="每日早晨工作流 (7:00AM)")
    parser.add_argument("--force", action="store_true", help="强制运行，跳过交易日检查")
    parser.add_argument("--dry-run", action="store_true", help="试运行模式，不实际执行")
    parser.add_argument("--phase", type=str, default="calibrate",
                        choices=["calibrate", "plan", "report", "all"],
                        help="运行阶段 (默认: calibrate)")
    parser.add_argument("--skip-archive", action="store_true", help="跳过报告归档")
    args = parser.parse_args()

    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    today_dir = ARCHIVE_DIR / today_str

    log(f"╔══════════════════════════════════════════════════════╗")
    log(f"║  每日早晨工作流启动                                ║")
    log(f"║  日期: {today_str}                                  ║")
    log(f"║  时间: {today.strftime('%H:%M:%S')}                 ║")
    log(f"║  模式: {'强制' if args.force else '标准'}           ║")
    log(f"╚══════════════════════════════════════════════════════╝")

    # --- 1. 交易日检查 ---
    if not args.force and not is_trading_day():
        log("今天不是交易日，工作流跳过")
        return

    if args.dry_run:
        log(">>> 试运行模式，以下仅显示将要执行的步骤 <<<")
        log(f"  1. 市场校准: {DAILY_WORKFLOW_SCRIPT} --phase calibrate")
        log(f"  2. 交易计划: {GENERATE_TRADE_PLAN_SCRIPT}")
        log(f"  3. LLM决策:  {APPLY_LLM_SCRIPT}")
        log(f"  4. 综合报告: {GENERATE_REPORT_SCRIPT}")
        log(f"  5. 归档目录: {today_dir}")
        return

    # 创建归档目录
    today_dir.mkdir(parents=True, exist_ok=True)

    success_count = 0
    fail_count = 0

    # --- 2. 阶段一：盘前校准 (calibrate) ---
    if args.phase in ("calibrate", "all"):
        log("\n>>> 阶段一: 盘前市场校准 <<<")
        if run_step("市场校准与风险评估", DAILY_WORKFLOW_SCRIPT, ["--phase", "calibrate"], timeout_minutes=20):
            success_count += 1
        else:
            fail_count += 1
            log("市场校准失败，后续步骤可能受影响", "WARN")

    # --- 3. 阶段二：生成交易计划 (plan) ---
    if args.phase in ("plan", "all"):
        log("\n>>> 阶段二: 生成每日交易计划 <<<")
        if run_step("生成交易计划", GENERATE_TRADE_PLAN_SCRIPT, [], timeout_minutes=15):
            success_count += 1
        else:
            fail_count += 1

    # --- 4. 阶段三：应用LLM决策 ---
    if args.phase in ("plan", "all"):
        log("\n>>> 阶段三: 应用大模型决策 <<<")
        # 校准阶段已生成今天P&L报告，使用今天日期（而非昨日默认值）
        if run_step("应用LLM决策", APPLY_LLM_SCRIPT, [today_str, today_str], timeout_minutes=10):
            success_count += 1
        else:
            fail_count += 1

    # --- 5. 阶段四：生成综合报告 (report) ---
    if args.phase in ("report", "all"):
        log("\n>>> 阶段四: 生成每日综合报告 <<<")
        if run_step("生成综合报告", GENERATE_REPORT_SCRIPT, [], timeout_minutes=15):
            success_count += 1
        else:
            fail_count += 1

    # --- 6. 归档报告 ---
    if not args.skip_archive:
        log(f"\n>>> 归档报告到: {today_dir} <<<")
        try:
            count = archive_reports(today_dir)
            log(f"[OK] 归档完成，共 {count} 个文件")
        except Exception as e:
            log(f"[FAIL] 归档失败: {e}", "ERROR")
            traceback.print_exc()

    # --- 7. 总结 ---
    log(f"\n{'='*60}")
    log(f"║  每日早晨工作流完成                                  ║")
    log(f"║  成功: {success_count} | 失败: {fail_count}          ║")
    log(f"║  归档目录: {today_dir}                               ║")
    log(f"{'='*60}")


if __name__ == "__main__":
    main()
