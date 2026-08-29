"""
每日早晨工作流脚本 — 每天早上7:00自动运行 (v5.9 统一入口)
功能：
  0. 晨间信息采集 (DeepSeek 驱动, 无视交易日) — 7 项报告
     · 晨间行情摘要 / 康波周期 / ETF 资金流向 / 舆情综合+动力煤
     · CNEMC 空气质量 / iFinD 自动研判 / 棉花加仓方案
  1. 检查是否为交易日 (仅决策类阶段需要, 信息采集始终运行)
  2. 运行盘前市场校准与风险评估
  3. 生成每日交易计划
  4. 应用大模型决策到交易计划 (DeepSeek API)
  5. 生成每日综合报告
  6. 将所有报告归档至 每日报告归档/YYYY-MM-DD/

使用方式：
  python run_daily_morning.py                            # 标准运行 (默认 calibrate)
  python run_daily_morning.py --force                    # 强制运行（忽略交易日检查）
  python run_daily_morning.py --dry-run                  # 试运行（不实际执行）
  python run_daily_morning.py --phase info               # 仅信息采集阶段 (周末也运行)
  python run_daily_morning.py --phase info --force       # 强制重新生成全部信息采集报告
  python run_daily_morning.py --phase calibrate          # 仅校准阶段
  python run_daily_morning.py --phase all --force        # 完整流程: info→calibrate→plan→report
"""

import argparse
import os
import shutil
import subprocess
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

# 强制 UTF-8 输出，解决 GBK 编码问题
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr.encoding != "utf-8":
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════
# 项目路径配置
# ═══════════════════════════════════════════════════════════════
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
ARCHIVE_DIR = PROJECT_ROOT / "每日报告归档"  # 归档到项目根目录下
# Python 解释器: 优先用环境变量 QUANT_PYTHON (Mac/Linux 跨平台), 回退本脚本同 venv
import os as _os

VENV_PYTHON = (
    _os.environ.get("QUANT_PYTHON")
    or _os.environ.get("QUANT_VENV_PYTHON")
    or sys.executable
)

# 关键脚本路径
DAILY_WORKFLOW_SCRIPT = PROJECT_ROOT / "v8.3_institutional" / "daily_workflow.py"
GENERATE_TRADE_PLAN_SCRIPT = (
    PROJECT_ROOT / "v8.3_institutional" / "generate_daily_trade_plan.py"
)
APPLY_LLM_SCRIPT = PROJECT_ROOT / "tools" / "apply_llm_decisions_to_plan.py"
GENERATE_REPORT_SCRIPT = PROJECT_ROOT / "generate_daily_report.py"
RUN_DAILY_EOD_SCRIPT = PROJECT_ROOT / "run_daily_eod.py"
CALENDAR_FILE = PROJECT_ROOT / "v8.3_institutional" / "data" / "trading_calendar.json"
# 信息采集阶段脚本 (新增, 调用 morning_info_runner.py 薄包装)
MORNING_INFO_SCRIPT = SCRIPT_DIR / "morning_info_runner.py"

# 可能输出的报告文件列表（用于归档）
REPORT_PATTERNS = [
    # 既有决策类
    "每日综合报告_*.md",
    "组合总盈亏报告_*.md",
    "交易计划_*.md",
    "市场环境分析_*.md",
    "风险评估_*.md",
    "信号监控_*.md",
    "daily_report_*.md",
    "pre_market_*.md",
    "calibrate_report_*.md",
    # 新增信息采集类 (v5.9 统一入口)
    "晨间行情摘要_*.md",
    "康波周期分析_*.md",
    "实时ETF资金流向_*.md",
    "舆情综合日报_*.md",
    "动力煤舆情日报_*.md",
    "空气质量CNEMC日报_*.md",
    "iFinD自动标的研判报告_*.md",
    "大宗商品交易机会扫描_*.md",
    "sentiment_summary_*.json",
    "morning_market_data_*.json",
    "air_quality_cnemc_*.json",
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
    try:
        log_file = get_log_file()
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def is_trading_day() -> bool:
    """检查今天是否为交易日 (B1.2: 委托 utils.trade_calendar 统一实现)

    迁移说明 (2026-08-01):
        原 MC9 实现: 读本地 CALENDAR_FILE, 缺失/损坏时 Fail-Safe 返回 False
        新统一实现: utils.trade_calendar.is_trading_day() 支持 akshare 在线拉取 + 缓存
        语义变化: 无数据时从"返回 False (保守)"变为"回退到周末判断 (weekday < 5)"
        风险评估: 统一实现会尝试 akshare 拉取, 仅在 akshare 也不可用时才回退周末判断;
                  比 MC9 的本地静态文件更可靠 (本地文件需手动维护, 易过期).
        调用方无需改动 (函数签名不变, 仍为无参=今天).
    """
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from utils.trade_calendar import is_trading_day as _unified_is_trading_day

        result = _unified_is_trading_day()  # 无参 = 今天
        log(f"交易日历检查 (统一实现): 今天 -> {'交易日' if result else '非交易日'}")
        return result
    except Exception as e:
        # 兜底: 统一实现导入失败时, 回退到周末判断 (与统一实现的回退逻辑一致)
        log(f"utils.trade_calendar 导入失败 ({e}), 回退到周末判断", "WARN")
        weekday = datetime.now().weekday()
        return weekday < 5


def run_step(name: str, script: Path, args: list, timeout_minutes: int = 30) -> bool:
    """
    运行一个工作流步骤
    返回 True 表示成功，False 表示失败
    """
    python = get_python()
    cmd = [python, str(script), *args]

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
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

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
    except (FileNotFoundError, PermissionError, OSError) as e:
        log(f"[FAIL] {name} 执行失败: {e}", "ERROR")
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
        SCRIPT_DIR,
        # 新增: 信息采集输出可能落在以下目录 (跨项目复用模块的直接输出)
        PROJECT_ROOT.parent / "15_每日工作流",  # morning_market_fetcher 直接输出
        PROJECT_ROOT.parent / "11_量化策略" / "engine",  # etf_flow 直接输出
        PROJECT_ROOT.parent / "02_舆情与竞品监控" / "舆情监控" / "data" / "综合日报",
        PROJECT_ROOT.parent / "02_舆情与竞品监控" / "舆情监控" / "煤炭舆情日报",
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
                    today_str_compact in fname
                    or today_str_dash in fname
                    or fname.endswith(f"_{today_str_compact}.md")
                    or fname.endswith(f"_{today_str_compact}.json")
                    or fname.endswith(f"_{today_str_dash}.md")
                    or fname.endswith(f"_{today_str_dash}.json")
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
                                "每日综合报告",
                                "组合总盈亏报告",
                                "交易计划",
                                "市场环境分析",
                                "风险评估",
                                "信号监控",
                                "daily_report",
                                "pre_market",
                                "calibrate_report",
                            ]
                        )

                if is_today_report:
                    dest = today_dir / fname
                    # 路径安全: 防止路径遍历攻击
                    try:
                        dest.resolve().relative_to(today_dir.resolve())
                    except ValueError:
                        log(f"  跳过路径遍历风险文件: {fname}", "WARN")
                        continue
                    if (
                        not dest.exists()
                        or file_path.stat().st_mtime > dest.stat().st_mtime
                    ):
                        shutil.copy2(file_path, dest)
                        archived_count += 1
                        log(f"  归档: {file_path.name} -> {today_dir.name}/")

        except Exception as e:
            log(f"  扫描目录异常 {search_dir}: {e}", "WARN")

    return archived_count


# ═══════════════════════════════════════════════════════════════
# 每日早晨工作流 — 提取函数 (降低 main() 圈复杂度)
# ═══════════════════════════════════════════════════════════════


def parse_morning_args():
    """解析每日早晨工作流命令行参数"""
    parser = argparse.ArgumentParser(description="每日早晨工作流 (7:00AM)")
    parser.add_argument("--force", action="store_true", help="强制运行，跳过交易日检查")
    parser.add_argument("--dry-run", action="store_true", help="试运行模式，不实际执行")
    parser.add_argument(
        "--phase",
        type=str,
        default="calibrate",
        choices=["info", "calibrate", "plan", "report", "all"],
        help="运行阶段 (默认: calibrate; all = info→calibrate→plan→report)",
    )
    parser.add_argument("--skip-archive", action="store_true", help="跳过报告归档")
    parser.add_argument(
        "--skip-system-check",
        action="store_true",
        help="跳过 P0 启动自检 (仅紧急情况使用,默认每次启动都自检)",
    )
    return parser.parse_args()


def run_p0_system_check_morning(args):
    """P0 启动自检 (早晨工作流版本)"""
    if args.skip_system_check:
        return
    try:
        # 显式将项目根加入 sys.path (此脚本位于 15_每日工作流/ 子目录)
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from utils.system_check import assert_system_ready

        assert_system_ready()  # 失败时 sys.exit(1)
    except SystemExit:
        raise
    except Exception:
        pass


def setup_morning_context(args):
    """初始化早晨工作流上下文 (日期/目录/banner)"""
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    today_dir = ARCHIVE_DIR / today_str

    # 计算上一个交易日 (供 LLM 决策灌入使用: 读昨日 PnL 报告 → 写今日计划)
    # PnL 报告由 v84_DailyPnlReport 任务在盘后 16:00 生成, 盘前阶段只能读昨日报告
    def _prev_trading_day(d: datetime) -> datetime:
        """回退到最近一个周一至周五 (不含节假日, 简化版)"""
        day = d - timedelta(days=1)
        while day.weekday() >= 5:  # 5=周六, 6=周日
            day -= timedelta(days=1)
        return day

    prev_trading_day_str = _prev_trading_day(today).strftime("%Y-%m-%d")

    log("╔" + "═" * 60 + "╗")
    log("║  每日早晨工作流启动                                ║")
    log(f"║  日期: {today_str}                                  ║")
    log(f"║  时间: {today.strftime('%H:%M:%S')}                 ║")
    log(f"║  模式: {'强制' if args.force else '标准'}           ║")
    log("╚" + "═" * 60 + "╝")

    return today_str, today_dir, prev_trading_day_str


def run_phase0_info(args, success_count, fail_count):
    """阶段零：晨间信息采集"""
    if args.phase not in ("info", "all"):
        return success_count, fail_count

    log("\n>>> 阶段零: 晨间信息采集 (DeepSeek 驱动) <<<")
    info_args = []
    if args.force:
        info_args.append("--force")
    if run_step("晨间信息采集", MORNING_INFO_SCRIPT, info_args, timeout_minutes=30):
        success_count += 1
    else:
        fail_count += 1
        log("信息采集部分失败, 后续决策阶段继续执行", "WARN")
    return success_count, fail_count


def run_morning_archive(today_dir, skip_archive=False):
    """归档报告（通用归档逻辑，供多个分支复用）"""
    if skip_archive:
        return
    log(f"\n>>> 归档报告到: {today_dir} <<<")
    try:
        count = archive_reports(today_dir)
        log(f"[OK] 归档完成，共 {count} 个文件")
    except Exception as e:
        log(f"[FAIL] 归档失败: {e}", "ERROR")
        traceback.print_exc()


def run_phase1_calibrate(args, success_count, fail_count):
    """阶段一：盘前市场校准"""
    if args.phase not in ("calibrate", "all"):
        return success_count, fail_count
    log("\n>>> 阶段一: 盘前市场校准 <<<")
    if run_step(
        "市场校准与风险评估",
        DAILY_WORKFLOW_SCRIPT,
        ["--phase", "calibrate"],
        timeout_minutes=20,
    ):
        success_count += 1
    else:
        fail_count += 1
        log("市场校准失败，后续步骤可能受影响", "WARN")
    return success_count, fail_count


def run_phase2_plan(args, success_count, fail_count):
    """阶段二：生成每日交易计划"""
    if args.phase not in ("plan", "all"):
        return success_count, fail_count
    log("\n>>> 阶段二: 生成每日交易计划 <<<")
    if run_step("生成交易计划", GENERATE_TRADE_PLAN_SCRIPT, [], timeout_minutes=15):
        success_count += 1
    else:
        fail_count += 1
    return success_count, fail_count


def run_phase3_llm(args, prev_trading_day_str, today_str, success_count, fail_count):
    """阶段三：应用大模型决策"""
    if args.phase not in ("plan", "all"):
        return success_count, fail_count
    log("\n>>> 阶段三: 应用大模型决策 <<<")
    # 读昨日 PnL 报告 → 灌入今日计划 (PnL 报告由 v84_DailyPnlReport 任务盘后 16:00 生成)
    # 盘前阶段只能用昨日报告, 而非今日 (今日报告要等今日盘后才会生成)
    log(
        f"  使用昨日 PnL 报告: daily_pnl_report_{prev_trading_day_str}.json → 计划日期 {today_str}"
    )
    if run_step(
        "应用LLM决策",
        APPLY_LLM_SCRIPT,
        [prev_trading_day_str, today_str],
        timeout_minutes=10,
    ):
        success_count += 1
    else:
        fail_count += 1
        # 昨日报告缺失时降级为脚本默认行为 (脚本内部 _prev_trading_day 会再尝试)
        log("  [WARN] 昨日 PnL 报告可能缺失, 后续可用 --phase plan 手动重试", "WARN")
    return success_count, fail_count


def run_phase4_report(args, success_count, fail_count):
    """阶段四：生成每日综合报告"""
    if args.phase not in ("report", "all"):
        return success_count, fail_count
    log("\n>>> 阶段四: 生成每日综合报告 <<<")
    if run_step("生成综合报告", GENERATE_REPORT_SCRIPT, [], timeout_minutes=15):
        success_count += 1
    else:
        fail_count += 1
    return success_count, fail_count


def print_morning_summary(success_count, fail_count, today_dir):
    """打印早晨工作流总结"""
    log(f"\n{'='*60}")
    log("║  每日早晨工作流完成                                  ║")
    log(f"║  成功: {success_count} | 失败: {fail_count}          ║")
    log(f"║  归档目录: {today_dir}                               ║")
    log(f"{'='*60}")


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════


def main():
    args = parse_morning_args()
    run_p0_system_check_morning(args)

    # GitHub 周热门项目集成自检 (2026-08-21, v8.6)
    try:
        import sys as _sys

        _sys.path.insert(0, str(PROJECT_ROOT))
        from utils.github_integration_registry import run_startup_selfcheck

        gh_report = run_startup_selfcheck()
        log(
            f"GitHub 集成自检: available={gh_report.available}/{gh_report.total}, "
            f"overall_ok={gh_report.overall_ok}"
        )
    except Exception as _e:
        log(f"GitHub 集成自检跳过: {_e}", "WARNING")

    today_str, today_dir, prev_trading_day_str = setup_morning_context(args)
    today_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        log(">>> 试运行模式，以下仅显示将要执行的步骤 <<<")
        log(f"  0. 信息采集: {MORNING_INFO_SCRIPT}")
        log(f"  1. 市场校准: {DAILY_WORKFLOW_SCRIPT} --phase calibrate")
        log(f"  2. 交易计划: {GENERATE_TRADE_PLAN_SCRIPT}")
        log(f"  3. LLM决策:  {APPLY_LLM_SCRIPT}")
        log(f"  4. 综合报告: {GENERATE_REPORT_SCRIPT}")
        log(f"  5. 归档目录: {today_dir}")
        return

    success_count = 0
    fail_count = 0

    success_count, fail_count = run_phase0_info(args, success_count, fail_count)

    if args.phase == "info":
        run_morning_archive(today_dir, args.skip_archive)
        print_morning_summary(success_count, fail_count, today_dir)
        return

    if not args.force and not is_trading_day():
        log("今天不是交易日, 决策类阶段跳过 (信息采集已完成)")
        run_morning_archive(today_dir, args.skip_archive)
        print_morning_summary(success_count, fail_count, today_dir)
        return

    success_count, fail_count = run_phase1_calibrate(args, success_count, fail_count)
    success_count, fail_count = run_phase2_plan(args, success_count, fail_count)
    success_count, fail_count = run_phase3_llm(
        args, prev_trading_day_str, today_str, success_count, fail_count
    )
    success_count, fail_count = run_phase4_report(args, success_count, fail_count)

    run_morning_archive(today_dir, args.skip_archive)
    print_morning_summary(success_count, fail_count, today_dir)


if __name__ == "__main__":
    main()
