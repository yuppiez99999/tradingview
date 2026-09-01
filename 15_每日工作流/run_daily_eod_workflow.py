#!/usr/bin/env python3
"""
每日收盘工作流脚本 — 每日15:30收盘后自动运行 (v5.9 EOD 统一入口)
=================================================================

功能 (5阶段闭环):
  阶段一: 生成收盘盈亏报告 (DeepSeek 驱动 AI 决策建议)
      · 调用 generate_daily_report.py
      · DeepSeek API 生成 4-6 条结构化交易决策建议
      · 输出 daily_pnl_report_{date}.json/.md 到 v8.3_institutional/reports/
  阶段二: 生成次日交易计划
      · 调用 generate_daily_trade_plan.py
      · 基于阶段策略 (建仓/持有/减仓/清仓) 生成基础 trade_plan
  阶段三: 应用 DeepSeek 决策到次日计划
      · 调用 apply_llm_decisions_to_plan.py [today] [next_trading_day]
      · 识别 AI 建议关键词, 写入 trade_plan.llm_overrides
      · 支持: IF空头升级 / Put保护 / 建仓顺序 / 减持调整 / 止损设置
  阶段四: 执行 EOD 四 Guard 风控链
      · 调用 run_daily_eod.py
      · KillSwitch / DrawdownController / VolTargetController / HedgeExecutionEngine
      · 更新次日 trade_plan 的 risk_guard 字段
  阶段五: 归档所有报告到 每日报告归档/YYYY-MM-DD/

使用方式:
  python run_daily_eod_workflow.py                       # 标准运行 (今日)
  python run_daily_eod_workflow.py --date 2026-07-27     # 指定报告日期
  python run_daily_eod_workflow.py --dry-run             # 试运行 (不实际执行)
  python run_daily_eod_workflow.py --skip-risk-guard     # 跳过风控守卫阶段
  python run_daily_eod_workflow.py --skip-archive        # 跳过归档阶段
  python run_daily_eod_workflow.py --force               # 强制运行 (忽略交易日检查)

依赖:
  · generate_daily_report.py          (收盘报告 + DeepSeek AI 建议)
  · generate_daily_trade_plan.py      (次日基础交易计划)
  · apply_llm_decisions_to_plan.py    (LLM 决策灌入次日计划)
  · run_daily_eod.py                  (EOD 四 Guard 风控链)

环境变量:
  · DEEPSEEK_API_KEY                  (DeepSeek API 密钥, 必填)
  · TRADING_ENV                       (shadow/production, 默认 shadow)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

# 强制 UTF-8 输出, 解决 GBK 编码问题
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
PROJECT_ROOT = SCRIPT_DIR.parent  # e:\各种PY程序\28-终极量化交易系统8.4
ARCHIVE_DIR = PROJECT_ROOT / "每日报告归档"  # 归档到项目根目录下
# C8 修复: 不再硬编码 Python 解释器路径, 优先使用环境变量或当前解释器
VENV_PYTHON = os.environ.get("QUANT_PYTHON") or sys.executable

# 关键脚本路径
GENERATE_REPORT_SCRIPT = PROJECT_ROOT / "generate_daily_report.py"
GENERATE_TRADE_PLAN_SCRIPT = (
    PROJECT_ROOT / "v8.3_institutional" / "generate_daily_trade_plan.py"
)
APPLY_LLM_SCRIPT = PROJECT_ROOT / "tools" / "apply_llm_decisions_to_plan.py"
RUN_DAILY_EOD_SCRIPT = PROJECT_ROOT / "run_daily_eod.py"
# 注意: DAILY_WORKFLOW_SCRIPT 指向的 v8.3_institutional/daily_workflow.py 实际不存在,
# 导致 run_phase4_5_shadow 调用 run_step 时在第 179 行 (script.exists() 检查) 直接返回 (False, ""),
# 阶段四点五 Shadow 数据收集自始至终失败, daily_returns.jsonl 从未被生产管道产出 — 这是 G1 缺口的根因.
# 保留此常量仅为向后兼容 (可能被外部脚本引用), 实际 shadow 阶段已改用 SHADOW_FEEDER_SCRIPT.
DAILY_WORKFLOW_SCRIPT = PROJECT_ROOT / "v8.3_institutional" / "daily_workflow.py"
# W1.3a Day 3 (2026-08-06): Shadow 真实数据注入器 — 替代不存在的 daily_workflow.py --phase shadow_monitor
# 转发到 utils.alpha.shadow_real_data_feeder.ShadowRealDataFeeder, 从真实行情计算组合日收益.
SHADOW_FEEDER_SCRIPT = PROJECT_ROOT / "scripts" / "shadow_real_data_feeder.py"
# W1.3b Day 4 (2026-08-10): DriftShadowIntegrator — 桥接 DriftMonitor + DelayedLabelTracker + daily_returns.jsonl
# 在 Shadow 数据注入后执行漂移检测、IC/IC_IR 计算、IC_IR 退化告警 (HC-1: sim_mode=True 不切 Flag)
SHADOW_DRIFT_INTEGRATOR_SCRIPT = PROJECT_ROOT / "scripts" / "drift_shadow_integrator.py"
# W1.3a Day 5 (2026-08-04): Shadow 状态同步 — 把 daily_returns.jsonl 同步到 shadow_state.json
# 修复 G1 缺口延伸: feeder 写入 jsonl 后, shadow_state.json 的 daily_nav 未同步更新 (占位值断层)
# 在阶段四点五 (feeder) 之后、阶段四点七 (drift) 之前执行, 确保状态文件始终与 jsonl 同步
SHADOW_STATE_REBUILD_SCRIPT = (
    PROJECT_ROOT / "scripts" / "rebuild_shadow_state_from_returns.py"
)
# 阶段四点八: Phase B 状态回写 (T2 单事实源收敛, 2026-08-08)
# 在 Shadow 数据注入 (4.5) + 状态同步 (4.5b) + 漂移检测 (4.7) 之后执行:
#   调用 phase_b_progressive_enabler.py --auto, 把 observation_days_completed 刷新为
#   daily_returns.jsonl 实时天数, 并在观察期满时条件推进阶段 B.
# 修复决策日 (08-24, 2026-08-11 从 08-20 延期) 读到陈旧 5/14 的双源分裂: phase_b_status.json 从未被 EOD 自动回写.
PHASE_B_ENABLER_SCRIPT = PROJECT_ROOT / "scripts" / "phase_b_progressive_enabler.py"
# 阶段四点九: B2 shadow 每日预热 (任务3 B2/B3 启用顺序决策, 2026-08-26)
# 在 Phase B 状态回写 (4.8) 之后执行: 运行 FeedbackLoop shadow 比对 (不切 flag,
# USE_FEEDBACK_LOOP 保持 False), 累积 b2_shadow_status.json 的 warmup_days (目标 3 天)。
# 修复断链: 此前 b2_shadow_runner 从未接入任何调度, 预热永远卡 0/3, B2 永远无法启用。
PHASE_B_B2_SHADOW_SCRIPT = PROJECT_ROOT / "scripts" / "phase_b_b2_shadow_runner.py"
# 阶段四点八六: B4 shadow 每日预热 (MLOps 管线 LLM 反馈闭环验证, 2026-09-01)
# 在 B2 shadow (4.85) 之后执行: 运行 phase_b_b4_shadow_runner.py (不切 flag,
# USE_MLOPS_PIPELINE 保持 False), 累积 b4_shadow_status.json 的 warmup_days (目标 7 天)。
# 修复断链: 此前 b4_shadow_runner 从未接入任何调度, 预热永远卡 0/7, B4 永远无法启用
# (与 B2 shadow 08-26 同型断链)。B3 前置口径修正 (2026-09-01): b3_shadow_status.json
# 缺失时回退 enabler 阶段轨判定, 不再阻断 B4 shadow 启动。
PHASE_B_B4_SHADOW_SCRIPT = PROJECT_ROOT / "scripts" / "phase_b_b4_shadow_runner.py"
# 阶段零: 年化收益预测校准 (生成 portfolio_return_projection.json, 供阶段一报告引用)
CALIBRATE_PROJECTION_SCRIPT = (
    PROJECT_ROOT / "v8.3_institutional" / "calibrate_returns_projection.py"
)
TRADE_PLANS_DIR = PROJECT_ROOT / "v8.3_institutional" / "trade_plans"
REPORTS_DIR_V83 = PROJECT_ROOT / "v8.3_institutional" / "reports"

# EOD 报告归档模式 (扫描以下目录收集报告)
REPORT_PATTERNS = [
    "daily_pnl_report_*.md",
    "daily_pnl_report_*.json",
    "trade_plan_*.md",
    "trade_plan_*.json",
    "eod_guard_report_*.md",
    "hedge_execution_fill_*.json",
    "hedge_execution_orders_*.json",
    "weekly_trade_execution_*.md",
]


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════


def get_python() -> str:
    """获取 Python 解释器路径 (优先 Python 3.11, DeepSeek SSL 兼容)"""
    if os.path.exists(VENV_PYTHON):
        return VENV_PYTHON
    return sys.executable


def get_log_file(report_date: str) -> Path:
    """获取日志文件路径"""
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"daily_eod_{report_date.replace('-', '')}.log"


def log(msg: str, level: str = "INFO") -> None:
    """写日志到文件并打印"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{level}] {msg}"
    try:
        # 日志文件按报告日期命名
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = get_log_file(today)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_next_trading_day(current_date: str) -> str:
    """计算下一交易日 (跳过周末, 不含节假日)"""
    dt = datetime.strptime(current_date, "%Y-%m-%d")
    next_dt = dt + timedelta(days=1)
    while next_dt.weekday() >= 5:  # 5=周六, 6=周日
        next_dt += timedelta(days=1)
    return next_dt.strftime("%Y-%m-%d")


def is_trading_day(date_str: str) -> bool:
    """检查指定日期是否为交易日 (B1.2: 委托 utils.trade_calendar 统一实现)

    迁移说明 (2026-08-01):
        原 C3 实现: 仅判周末 (weekday < 5), 不覆盖法定节假日
        新统一实现: utils.trade_calendar.is_trading_day() 支持 akshare 在线拉取 +
                    缓存, 能正确识别国庆/春节等节假日
        改进: 从"仅周末判断"升级为"完整节假日识别", 避免节假日误运行 EOD
        调用方无需改动 (函数签名不变, 仍接受 date_str: str).
    """
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from utils.trade_calendar import is_trading_day as _unified_is_trading_day

        return _unified_is_trading_day(date_str)
    except Exception:
        # 兜底: 统一实现导入失败时, 回退到周末判断 (原 C3 逻辑)
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return dt.weekday() < 5
        except Exception:
            return False


def run_step(
    name: str,
    script: Path,
    args: list,
    timeout_minutes: int = 30,
    allowed_exit_codes: list | None = None,
) -> tuple:
    """
    运行一个工作流步骤
    返回 (success: bool, stdout: str)

    allowed_exit_codes: 业务性非 0 退出码白名单 (默认仅 [0]).
        用于区分 "脚本崩溃 (异常)" 与 "业务状态返回 (如观察期 WAIT / 风控数据缺失)".
        例: phase4_8 观察期未满返回 1 (WAIT), phase4 风控数据缺失返回 1, 均为预期状态.
    """
    if allowed_exit_codes is None:
        allowed_exit_codes = [0]
    python = get_python()
    cmd = [python, str(script)] + args

    log("=" * 60)
    log(f">>> 开始执行: {name}")
    log(f">>> 命令: {' '.join(cmd)}")
    log("=" * 60)

    if not script.exists():
        log(f"[FAIL] 脚本不存在: {script}", "ERROR")
        return False, ""

    try:
        # 清理可能干扰子进程的环境变量
        env = os.environ.copy()
        env.pop("PYTHONHOME", None)
        # W3 修复: 不再 pop PYTHONPATH — 子脚本依赖项目模块 (utils 等) 的解析路径,
        # 清空会导致 ModuleNotFoundError (如 phase_b_progressive_enabler 在 EOD 内失败).
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("PYTHONUTF8", None)

        # M3 修复: 使用 CREATE_NEW_PROCESS_GROUP 创建独立进程组, 便于超时后 taskkill /T 彻底清理孙进程
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_minutes * 60,
            env=env,
            creationflags=creationflags,
        )

        # 记录输出
        stdout_preview = result.stdout or ""
        if len(stdout_preview) > 5000:
            stdout_preview = (
                stdout_preview[:5000] + f"\n... [截断, 共{len(result.stdout)}字符]"
            )
        if stdout_preview:
            log(f"STDOUT:\n{stdout_preview}")

        if result.stderr:
            stderr_preview = result.stderr[:3000]
            if len(result.stderr) > 3000:
                stderr_preview += f"\n... [截断, 共{len(result.stderr)}字符]"
            log(f"STDERR:\n{stderr_preview}", "WARN")

        if result.returncode in allowed_exit_codes:
            log(f"[OK] {name} 执行成功 (exit_code={result.returncode})")
            return True, result.stdout or ""
        else:
            log(
                f"[FAIL] {name} 执行失败 (exit_code={result.returncode}, 允许码={allowed_exit_codes})",
                "ERROR",
            )
            return False, result.stdout or ""

    except subprocess.TimeoutExpired:
        log(f"[FAIL] {name} 执行超时 (>{timeout_minutes}分钟)", "ERROR")
        return False, ""
    except Exception as e:
        log(f"[FAIL] {name} 执行异常: {e}", "ERROR")
        traceback.print_exc()
        return False, ""


def archive_reports(today_dir: Path, report_date: str) -> int:
    """
    扫描项目目录中的报告文件, 归档到指定目录
    返回归档文件数量
    """
    import fnmatch

    archived_count = 0
    today_dir.mkdir(parents=True, exist_ok=True)

    # 可能产生报告的关键目录
    search_dirs = [
        PROJECT_ROOT,
        PROJECT_ROOT / "v8.3_institutional",
        PROJECT_ROOT / "v8.3_institutional" / "reports",
        PROJECT_ROOT / "v8.3_institutional" / "trade_plans",
        PROJECT_ROOT / "logs",
    ]

    date_compact = report_date.replace("-", "")
    date_dash = report_date

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        try:
            for file_path in search_dir.iterdir():
                if not file_path.is_file():
                    continue
                fname = file_path.name

                # 检查文件名是否包含指定日期
                is_target_report = (
                    date_compact in fname
                    or date_dash in fname
                    or fname.endswith(f"_{date_compact}.md")
                    or fname.endswith(f"_{date_compact}.json")
                    or fname.endswith(f"_{date_dash}.md")
                    or fname.endswith(f"_{date_dash}.json")
                )

                # C10 修复: 使用 fnmatch 进行通配符匹配 (原 startswith 把 * 当字面量, 永不命中)
                # 额外检查: EOD 守卫报告 / 对冲执行单
                if not is_target_report:
                    is_target_report = (
                        any(
                            fnmatch.fnmatch(fname, pat)
                            for pat in [
                                "eod_guard_report_*",
                                "hedge_execution_fill_*",
                                "eod_guard_report_*.md",
                                "hedge_execution_fill_*.json",
                            ]
                        )
                        and date_dash in fname
                    )

                if is_target_report:
                    dest = today_dir / fname
                    # 仅当目标不存在或源文件更新时复制
                    if (
                        not dest.exists()
                        or file_path.stat().st_mtime > dest.stat().st_mtime
                    ):
                        try:
                            shutil.copy2(file_path, dest)
                            archived_count += 1
                            log(f"  归档: {file_path.name} -> {today_dir.name}/")
                        except Exception as e:
                            log(f"  归档失败 {fname}: {e}", "WARN")

        except Exception as e:
            log(f"  扫描目录异常 {search_dir}: {e}", "WARN")

    return archived_count


def verify_llm_overrides_applied(plan_path: Path) -> dict:
    """验证 LLM 决策是否成功写入次日交易计划"""
    if not plan_path.exists():
        return {"applied": False, "reason": "plan file not found"}
    try:
        with open(plan_path, encoding="utf-8") as f:
            plan = json.load(f)
        # v8.6.13 P1 FIX (2026-08-01 AI 扫描):
        # 原代码 plan.get("llm_overrides", {}) 在 JSON 显式为 null 时返回 None (非默认 {}),
        # 调用方 "..." in overrides 会抛 TypeError. 改用 `or {}` 兜底 None.
        overrides = plan.get("llm_overrides") or {}
        adjustments = plan.get("metadata", {}).get("llm_adjustments") or {}
        return {
            "applied": bool(overrides),
            "overrides": overrides,
            "adjustments_source": adjustments.get("source", ""),
            "applied_at": adjustments.get("applied_at", ""),
        }
    except Exception as e:
        return {"applied": False, "reason": str(e)}


def parse_eod_args():
    """解析 EOD 工作流命令行参数"""
    parser = argparse.ArgumentParser(description="每日收盘工作流 (15:30 收盘后运行)")
    parser.add_argument(
        "--date", type=str, default=None, help="报告日期 YYYY-MM-DD (默认今天)"
    )
    parser.add_argument("--dry-run", action="store_true", help="试运行模式, 不实际执行")
    parser.add_argument("--force", action="store_true", help="强制运行, 跳过交易日检查")
    parser.add_argument(
        "--skip-risk-guard",
        action="store_true",
        help="跳过阶段四 (EOD 四 Guard 风控链)",
    )
    parser.add_argument(
        "--skip-archive", action="store_true", help="跳过阶段五 (报告归档)"
    )
    parser.add_argument(
        "--skip-audit", action="store_true", help="跳过阶段六 (EOD 收盘审核)"
    )
    parser.add_argument(
        "--skip-generate-plan",
        action="store_true",
        help="跳过阶段二 (生成次日交易计划, 使用已存在的计划)",
    )
    parser.add_argument(
        "--skip-shadow",
        action="store_true",
        help="跳过 Shadow 相关阶段 (四点五数据收集 + 四点五B状态同步 + 四点七漂移检测, 观察期专用)",
    )
    parser.add_argument(
        "--skip-feedback-loop",
        action="store_true",
        help="跳过阶段四点六 (FeedbackLoop 因子权重更新)",
    )
    parser.add_argument(
        "--skip-ecl",
        action="store_true",
        help="跳过阶段4.95 ECL旁路 (Wave10-CTX 经验上下文层, 调试/紧急排查用)",
    )
    parser.add_argument(
        "--skip-system-check",
        action="store_true",
        help="跳过 P0 启动自检 (仅紧急情况使用,默认每次启动都自检)",
    )
    return parser.parse_args()


def run_p0_system_check(args):
    """P0 启动自检"""
    if args.skip_system_check:
        return
    try:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from utils.system_check import assert_system_ready

        assert_system_ready()  # 失败时 sys.exit(1)
    except SystemExit:
        raise
    except Exception:
        pass


def setup_eod_context(args):
    """初始化 EOD 工作流上下文 (日期/目录/banner)"""
    report_date = args.date or datetime.now().strftime("%Y-%m-%d")
    # 路径安全: 拒绝路径遍历与非法日期格式
    if (
        not re.match(r"^\d{4}-\d{2}-\d{2}$", report_date)
        or ".." in report_date
        or "/" in report_date
        or "\\" in report_date
    ):
        raise ValueError(f"非法报告日期: {report_date}")
    next_trade_date = get_next_trading_day(report_date)
    today_dir = ARCHIVE_DIR / report_date

    log("╔" + "═" * 60 + "╗")
    log("║  每日收盘工作流启动 (EOD Workflow)                       ║")
    log(f"║  报告日期: {report_date}                                  ║")
    log(f"║  次交易日: {next_trade_date}                              ║")
    log(f"║  执行时间: {datetime.now().strftime('%H:%M:%S')}                 ║")
    log(f"║  模式: {'强制' if args.force else '标准'}                               ║")
    log("╚" + "═" * 60 + "╝")

    return report_date, next_trade_date, today_dir


def run_phase0_calibrate(report_date, eod_summary):
    """阶段零：年化收益预测校准"""
    log("\n>>> 阶段零: 年化收益预测校准 <<<")
    if CALIBRATE_PROJECTION_SCRIPT.exists():
        phase0_success, _ = run_step(
            "年化收益预测校准 (portfolio_return_projection.json)",
            CALIBRATE_PROJECTION_SCRIPT,
            [],
            timeout_minutes=5,
        )
        eod_summary["phases"]["phase0_calibrate_projection"] = {
            "success": phase0_success,
            "script": str(CALIBRATE_PROJECTION_SCRIPT),
        }
        if phase0_success:
            log("  ✅ portfolio_return_projection.json 已生成, 报告将引用真实测算值")
        else:
            log("  ⚠️ 年化收益校准失败, 报告预期绩效将显示 '待测算'", "WARN")
    else:
        log(f"  ⚠️ 校准脚本不存在: {CALIBRATE_PROJECTION_SCRIPT}", "WARN")
        phase0_success = False
        eod_summary["phases"]["phase0_calibrate_projection"] = {
            "skipped": True,
            "reason": "script not found",
        }
    return phase0_success


def run_phase1_generate_report(report_date, eod_summary):
    """阶段一：生成收盘盈亏报告"""
    log("\n>>> 阶段一: 生成收盘盈亏报告 (DeepSeek 驱动) <<<")
    phase1_success, _phase1_stdout = run_step(
        "收盘报告生成 (DeepSeek AI 建议)",
        GENERATE_REPORT_SCRIPT,
        [report_date],
        timeout_minutes=15,
    )
    eod_summary["phases"]["phase1_generate_report"] = {
        "success": phase1_success,
        "script": str(GENERATE_REPORT_SCRIPT),
    }
    if phase1_success:
        report_json = REPORTS_DIR_V83 / f"daily_pnl_report_{report_date}.json"
        if report_json.exists():
            try:
                with open(report_json, encoding="utf-8") as f:
                    report_data = json.load(f)
                ai_recs = report_data.get("ai_recommendations", [])
                log(f"  ✅ 报告包含 {len(ai_recs)} 条 AI 决策建议")
                for i, rec in enumerate(ai_recs, 1):
                    log(f"     {i}. {rec[:80]}{'...' if len(rec) > 80 else ''}")
                eod_summary["phases"]["phase1_generate_report"]["ai_recs_count"] = len(
                    ai_recs
                )
                eod_summary["phases"]["phase1_generate_report"][
                    "ai_recommendations"
                ] = ai_recs
            except Exception as e:
                log(f"  ⚠️ 读取报告 AI 建议失败: {e}", "WARN")
    return phase1_success


def run_phase2_generate_plan(report_date, next_trade_date, eod_summary, args):
    """阶段二：生成次日交易计划"""
    if args.skip_generate_plan:
        log("\n>>> 阶段二: 跳过 (使用已存在的次日交易计划) <<<")
        eod_summary["phases"]["phase2_generate_plan"] = {"skipped": True}
        return True

    log("\n>>> 阶段二: 生成次日交易计划 <<<")
    phase2_success, _ = run_step(
        "次日交易计划生成",
        GENERATE_TRADE_PLAN_SCRIPT,
        [next_trade_date],
        timeout_minutes=10,
    )
    eod_summary["phases"]["phase2_generate_plan"] = {
        "success": phase2_success,
        "script": str(GENERATE_TRADE_PLAN_SCRIPT),
    }
    return phase2_success


def validate_next_plan(next_trade_date, eod_summary):
    """验证次日计划存在，返回 (exists, skip_phase3, skip_phase4)"""
    next_plan_filename = f"trade_plan_{next_trade_date.replace('-', '')}.json"
    next_plan_path = TRADE_PLANS_DIR / next_plan_filename
    if not next_plan_path.exists():
        log(f"[FAIL] 次日交易计划不存在: {next_plan_path} [关键失败]", "ERROR")
        log(
            "请确认 generate_daily_trade_plan.py 已运行, 或使用 --skip-generate-plan 仅在计划已存在时使用",
            "ERROR",
        )
        log("阶段三 (LLM决策灌入) 和 阶段四 (风控守卫) 将跳过, 因无计划可操作", "ERROR")
        eod_summary["phases"]["phase2_generate_plan"] = eod_summary["phases"].get(
            "phase2_generate_plan", {}
        )
        eod_summary["phases"]["phase2_generate_plan"]["plan_exists"] = False
        eod_summary["phases"]["phase2_generate_plan"]["critical"] = True
        eod_summary["phases"]["phase2_generate_plan"]["skip_downstream"] = [
            "phase3_apply_llm",
            "phase4_risk_guard",
        ]
        return False, True, True
    log(f"  ✅ 次日交易计划已就绪: {next_plan_path.name}")
    return True, False, False


def run_phase3_apply_llm(report_date, next_trade_date, next_plan_path, eod_summary):
    """阶段三：应用 DeepSeek 决策到次日交易计划"""
    log("\n>>> 阶段三: 应用 DeepSeek 决策到次日交易计划 <<<")
    log(f"  报告日期: {report_date} → 次交易日: {next_trade_date}")
    phase3_success, _ = run_step(
        "LLM 决策灌入次日计划",
        APPLY_LLM_SCRIPT,
        [report_date, next_trade_date],
        timeout_minutes=5,
    )
    eod_summary["phases"]["phase3_apply_llm"] = {
        "success": phase3_success,
        "script": str(APPLY_LLM_SCRIPT),
        "report_date": report_date,
        "plan_date": next_trade_date,
    }
    if phase3_success:
        verify_result = verify_llm_overrides_applied(next_plan_path)
        eod_summary["phases"]["phase3_apply_llm"]["verification"] = verify_result
        if verify_result.get("applied"):
            overrides = verify_result.get("overrides") or {}
            log("  ✅ LLM 决策已写入次日计划:")
            if "futures_if_contracts" in overrides:
                log(f"     - IF 期货空头: {overrides['futures_if_contracts']} 手")
            if "put_protection" in overrides:
                log(f"     - Put 保护: {len(overrides['put_protection'])} 个标的")
            if "build_sequence" in overrides:
                log(f"     - 建仓顺序: {overrides['build_sequence']}")
            if "stop_loss_adjustments" in overrides:
                log(
                    f"     - 止损调整: {len(overrides['stop_loss_adjustments'])} 个标的"
                )
            if "position_adjustments" in overrides:
                log(f"     - 仓位调整: {len(overrides['position_adjustments'])} 个标的")
        else:
            log(
                f"  ⚠️ LLM 决策未写入: {verify_result.get('reason', '未知原因')}",
                "WARN",
            )
    return phase3_success


def run_phase4_risk_guard(report_date, eod_summary, skip_phase4, args):
    """阶段四：EOD 四 Guard 风控链"""
    if skip_phase4:
        log("\n>>> 阶段四: 跳过 (前置关键阶段失败, 无计划可更新风控字段) <<<", "WARN")
        eod_summary["phases"]["phase4_risk_guard"] = {
            "skipped": True,
            "skip_reason": "upstream critical failure (no trade plan available)",
        }
        return False

    if args.skip_risk_guard:
        log("\n>>> 阶段四: 跳过 EOD 风控守卫 (--skip-risk-guard) <<<")
        eod_summary["phases"]["phase4_risk_guard"] = {"skipped": True}
        return False

    log("\n>>> 阶段四: 执行 EOD 四 Guard 风控链 <<<")
    # W3 修复: 观察期/Shadow 环境下风控数据字段缺失属预期状态, run_daily_eod.py
    # 返回非 0 (如 drawdown 数据缺失视为未通过). 该阶段 EOD 已做 fail-open 处理,
    # 故允许业务性非 0 退出码 [0,1], 仅真崩溃 (异常/超时) 才判失败.
    phase4_success, _ = run_step(
        "EOD 四 Guard 风控守卫",
        RUN_DAILY_EOD_SCRIPT,
        ["--date", report_date],
        timeout_minutes=10,
        allowed_exit_codes=[0, 1],
    )
    eod_summary["phases"]["phase4_risk_guard"] = {
        "success": phase4_success,
        "script": str(RUN_DAILY_EOD_SCRIPT),
    }
    return phase4_success


def run_phase4_5_shadow(report_date, eod_summary, args):
    """阶段四点五：Shadow 真实数据收集 (W1.3a Day 3 修复 G1 缺口)

    修复说明 (2026-08-06):
        原 DAILY_WORKFLOW_SCRIPT (v8.3_institutional/daily_workflow.py) 不存在,
        run_step 在 script.exists() 检查处直接返回 (False, ""), 阶段四点五
        自始至终失败, daily_returns.jsonl 从未被生产管道产出.

    现调用 scripts/shadow_real_data_feeder.py (转发到 ShadowRealDataFeeder):
        1. 从 MarketDataProvider 拉取真实行情 (TDX 优先 + 多源降级)
        2. 按当日持仓权重计算组合日收益 (sum(weight * ret))
        3. 增量写入 reports/shadow/daily_returns.jsonl
    """
    if args.skip_shadow:
        log("\n>>> 阶段四点五: 跳过 Shadow 数据收集 (--skip-shadow) <<<")
        eod_summary["phases"]["phase4_5_shadow_monitor"] = {"skipped": True}
        return False

    log("\n>>> 阶段四点五: Shadow 真实数据注入 (W1.3a ShadowRealDataFeeder) <<<")
    log(f"  日期: {report_date}")
    log(f"  脚本: {SHADOW_FEEDER_SCRIPT}")
    phase_shadow_success, _ = run_step(
        "Shadow Real Data Feeder",
        SHADOW_FEEDER_SCRIPT,
        ["--date", report_date],
        timeout_minutes=5,
    )
    # 双保险校验: 即使 feeder 返回 success, 也确认 daily_returns.jsonl 实际包含该日期
    written = _check_daily_returns_has_date(report_date)
    eod_summary["phases"]["phase4_5_shadow_monitor"] = {
        "success": phase_shadow_success,
        "written": written,
        "script": str(SHADOW_FEEDER_SCRIPT),
        "date": report_date,
        "w13a_feeder": True,  # 标记使用新 feeder (区别于旧 daily_workflow)
    }
    if phase_shadow_success and written:
        log("  ✅ Shadow 数据已写入 reports/shadow/daily_returns.jsonl")
        return True

    # ===== 观察期数据记录失败 → 及时提示手动记录 =====
    _alert_observation_missing(report_date, phase_shadow_success, written, eod_summary)
    return False


def _check_daily_returns_has_date(report_date: str) -> bool:
    """校验 daily_returns.jsonl 是否已包含指定日期的记录.

    观察期数据断档会阻塞自我进化决策, 需双保险确认 (feeder 成功 ≠ 一定写盘).
    """
    try:
        path = PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
        if not path.exists():
            return False
        target = report_date
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    import json as _json

                    rec = _json.loads(line)
                    if str(rec.get("date", "")) == target:
                        return True
                except Exception:
                    continue
        return False
    except Exception:
        return False


def _alert_observation_missing(report_date, feeder_success, written, eod_summary):
    """观察期数据缺失时, 及时醒目提示手动记录 (避免数据断档静默).

    生成: ① 控制台醒目告警; ② EOD summary 告警字段; ③ 告警文件 (可追溯).
    """
    reason = (
        "feeder 运行失败" if not feeder_success else "daily_returns.jsonl 未包含该日期"
    )
    log("", "WARN")
    log("=" * 70, "WARN")
    log("⚠️  ⚠️  观察期数据记录失败 — 需手动记录  ⚠️  ⚠️", "WARN")
    log("=" * 70, "WARN")
    log(f"  日期: {report_date}", "WARN")
    log(
        f"  原因: {reason} (feeder_success={feeder_success}, written={written})", "WARN"
    )
    log("  影响: Shadow 样本断档, 阻塞自我进化观察期决策 (目标 ≥20 条)", "WARN")
    log("  手动记录步骤:", "WARN")
    log(
        "    1. 排查权重/数据源: 检查 config/positions.json 是否有效 + 行情源可用",
        "WARN",
    )
    log(
        f"    2. 重跑 feeder:  python -m utils.alpha.shadow_real_data_feeder --date {report_date}",
        "WARN",
    )
    log("    3. 若仍失败, 手工补录 daily_returns.jsonl (需真实组合日收益)", "WARN")
    log(
        "    4. 记录后检查: reports/shadow/daily_returns.jsonl 最后一行应含该日期",
        "WARN",
    )
    log("=" * 70, "WARN")
    # 写入 EOD summary, 供下游/告警系统感知
    eod_summary.setdefault("observation_alerts", []).append(
        {
            "date": report_date,
            "type": "observation_data_missing",
            "reason": reason,
            "feeder_success": bool(feeder_success),
            "written": bool(written),
            "action": "MANUAL_RECORD_REQUIRED",
        }
    )
    # 写告警文件 (可追溯)
    try:
        alert_dir = PROJECT_ROOT / "reports" / "evolution"
        alert_dir.mkdir(parents=True, exist_ok=True)
        alert_file = alert_dir / f"observation_alert_{report_date}.json"
        alert_file.write_text(
            json.dumps(
                {
                    "date": report_date,
                    "type": "observation_data_missing",
                    "reason": reason,
                    "feeder_success": bool(feeder_success),
                    "written": bool(written),
                    "action": "MANUAL_RECORD_REQUIRED",
                    "manual_steps": [
                        "check config/positions.json + data source",
                        f"rerun: python -m utils.alpha.shadow_real_data_feeder --date {report_date}",
                        "manual append daily_returns.jsonl if still failing",
                    ],
                    "created": datetime.now().isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        log(f"  告警文件: {alert_file}", "WARN")
    except Exception as exc:
        log(f"  告警文件写入失败: {exc}", "WARN")


def run_phase4_5b_shadow_state_sync(report_date, eod_summary, args):
    """阶段四点五B: Shadow 状态同步 (W1.3a Day 5 修复状态断层)

    在 Shadow 数据注入 (阶段四点五) 之后、漂移检测 (阶段四点七) 之前执行:
        1. 读取 daily_returns.jsonl 全部真实收益
        2. 从 nav=1.0 累乘重建 daily_nav
        3. 更新 shadow_state.json: daily_nav / current_nav / current_capital
        4. Fail-Fast 检查 (单日>3% 或 3日累计>5%)

    修复 G1 缺口延伸 (2026-08-04 发现):
        shadow_real_data_feeder 已成功写入真实日收益到 daily_returns.jsonl,
        但 shadow_state.json 的 daily_nav 未同步更新 (仍为占位值).
        根因: launch_shadow_account.py 只负责 init/status/advance,
        daily_workflow Phase 10 (记录净值) 链路已断, 无脚本把 jsonl 同步回 state.
        本阶段调用 rebuild_shadow_state_from_returns.py 补齐这一断层.

    HC 合规:
        - HC-4: 只读 daily_returns.jsonl, 只写 shadow_state.json (不碰 V9 基线)
        - fail-safe: 失败不中断 EOD 主流程 (仅 WARN)
    """
    if args.skip_shadow:
        log("\n>>> 阶段四点五B: 跳过 Shadow 状态同步 (--skip-shadow) <<<")
        eod_summary["phases"]["phase4_5b_shadow_state_sync"] = {"skipped": True}
        return False

    # 前置依赖检查: 阶段四点五必须成功执行
    phase_4_5_result = eod_summary.get("phases", {}).get("phase4_5_shadow_monitor", {})
    if not phase_4_5_result.get("success") or not phase_4_5_result.get("written"):
        log(
            "\n>>> 阶段四点五B: 跳过 Shadow 状态同步 (阶段四点五未成功写入数据) <<<",
            "WARN",
        )
        eod_summary["phases"]["phase4_5b_shadow_state_sync"] = {
            "skipped": True,
            "reason": "phase4_5_not_successful",
        }
        return False

    log("\n>>> 阶段四点五B: Shadow 状态同步 (rebuild_shadow_state_from_returns) <<<")
    log(f"  日期: {report_date}")
    log(f"  脚本: {SHADOW_STATE_REBUILD_SCRIPT}")
    phase_sync_success, _ = run_step(
        "Shadow State Rebuild",
        SHADOW_STATE_REBUILD_SCRIPT,
        [],  # 无参数, 自动读取 daily_returns.jsonl 全部记录重建
        timeout_minutes=3,
    )
    eod_summary["phases"]["phase4_5b_shadow_state_sync"] = {
        "success": phase_sync_success,
        "script": str(SHADOW_STATE_REBUILD_SCRIPT),
        "date": report_date,
        "w13a_day5_state_sync": True,
    }
    if phase_sync_success:
        log("  ✅ shadow_state.json 已同步 (daily_nav 从 daily_returns.jsonl 重建)")
    else:
        log(
            "  ⚠️ 状态同步失败, shadow_state.json 可能未更新 (不影响 EOD 主流程)",
            "WARN",
        )

    # ---- 阶段四点五B+1: Shadow 真实撮合桥接 (P0-1) ----
    # 消费 DTE-1 建仓撮合链的 FillsStore 真实成交, 写入 trade_log,
    # 使影子账户 NAV 基于真实撮合 (对齐 cairn/shadow-realness-audit P0 要求).
    # fail-open: 异常/无成交仅记日志, 不阻断 EOD 主流程.
    log("\n>>> 阶段四点五B+1: Shadow 真实撮合桥接 (shadow_fills_integrator) <<<")
    shadow_integrator_script = (
        PROJECT_ROOT / "scripts" / "run_shadow_fills_integrator.py"
    )
    phase_bridge_success, _ = run_step(
        "Shadow Fills Integration",
        shadow_integrator_script,
        [report_date],
        timeout_minutes=5,
    )
    eod_summary["phases"]["phase4_5b1_shadow_fills_bridge"] = {
        "success": phase_bridge_success,
        "script": str(shadow_integrator_script),
        "date": report_date,
        "p0_real_fills": True,
    }
    if phase_bridge_success:
        log("  ✅ 真实撮合成交已桥接至 shadow_state.json trade_log")
    else:
        log("  ⚠️ 撮合桥接未产生新数据或跳过 (不影响 EOD 主流程)", "WARN")

    return phase_sync_success


def _load_positions_for_attribution() -> list[dict]:
    """从 config/positions.json 加载持仓并转换为归因引擎所需格式.

    Returns:
        [{"code", "name", "weight", "sector", "amount", "style_exposures": {...}}]
    """
    positions_path = PROJECT_ROOT / "config" / "positions.json"
    if not positions_path.exists():
        return []
    with open(positions_path, encoding="utf-8") as f:
        data = json.load(f)
    raw = data.get("positions", {})
    if not raw:
        return []

    items = list(raw.values())
    total_amount = sum(float(p.get("amount", 0)) for p in items) or 1.0

    STYLE_MAP = {
        "科技": {"momentum": 0.6, "growth": 0.5, "valuation": -0.2},
        "高端制造": {"momentum": 0.4, "growth": 0.4, "valuation": 0.0},
        "顺周期": {"momentum": 0.2, "growth": 0.1, "valuation": 0.3},
        "资源": {
            "momentum": 0.1,
            "growth": 0.0,
            "valuation": 0.6,
            "earnings_quality": 0.4,
        },
        "防御": {
            "momentum": 0.0,
            "growth": 0.0,
            "valuation": 0.4,
            "earnings_quality": 0.6,
        },
        "消费": {
            "momentum": 0.2,
            "growth": 0.2,
            "valuation": 0.3,
            "earnings_quality": 0.5,
        },
    }

    result = []
    for p in items:
        amount = float(p.get("amount", 0))
        if amount <= 0:
            continue
        sector = p.get("sector") or p.get("style") or "other"
        result.append(
            {
                "code": p.get("code", ""),
                "name": p.get("name", ""),
                "weight": amount / total_amount,
                "sector": sector,
                "amount": amount,
                "market_value": amount,
                "style_exposures": STYLE_MAP.get(
                    sector, {"momentum": 0.1, "valuation": 0.1}
                ),
            }
        )
    return result


def _load_daily_return_for_date(report_date: str) -> float | None:
    """从 reports/shadow/daily_returns.jsonl 读取指定日期的组合日收益."""
    path = PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if str(rec.get("date", "")) == report_date:
                    return float(rec.get("daily_return", 0.0))
            except (ValueError, TypeError, KeyError):
                continue
    return None


def _load_hedge_pnl_from_eod_report(report_date: str) -> float:
    """从 EOD 报告 daily_pnl_report_{date}.json 读取对冲盈亏."""
    candidates = [
        PROJECT_ROOT
        / "每日报告归档"
        / report_date
        / f"daily_pnl_report_{report_date}.json",
        PROJECT_ROOT / "reports" / report_date / f"daily_pnl_report_{report_date}.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                return float(data.get("net_performance", {}).get("hedge_pnl", 0.0))
            except (ValueError, TypeError, KeyError, OSError):
                continue
    return 0.0


def run_phase4_55_attribution(report_date, eod_summary, args):
    """阶段四点五五: PnL 归因报告生成 (FeedbackLoop 前置依赖)

    在 Shadow 数据注入 (4.5) + 状态同步 (4.5b) + 漂移检测 (4.7) + Phase B 回写 (4.8) 之后、
    FeedbackLoop (4.6) 之前执行, 生成 reports/pnl_attribution/pnl_attribution_{date}.json.

    数据来源:
        - 持仓: config/positions.json
        - 组合日收益: reports/shadow/daily_returns.jsonl (ShadowRealDataFeeder 产出)
        - 对冲盈亏: 每日报告归档/{date}/daily_pnl_report_{date}.json
        - 基准/市场/因子/行业收益: 简化估算 (复用 v8.3_institutional/daily_workflow.py:2428-2443 逻辑)

    HC 合规:
        - HC-1: 不切 Feature Flag
        - HC-4: 只读评估, 不修改 V9 基线
        - fail-safe: 失败不中断 EOD 主流程 (FeedbackLoop 会优雅降级)
    """
    if args.skip_feedback_loop:
        log("\n>>> 阶段四点五五: 跳过 PnL 归因生成 (--skip-feedback-loop) <<<")
        eod_summary["phases"]["phase4_55_attribution"] = {"skipped": True}
        return False

    log("\n>>> 阶段四点五五: PnL 归因报告生成 (FeedbackLoop 前置) <<<")
    log(f"  日期: {report_date}")
    try:
        from utils.pnl_attribution_engine import PnLAttributionEngine

        positions = _load_positions_for_attribution()
        if not positions:
            log("  ⚠️ 无持仓数据 (config/positions.json), 跳过归因生成", "WARN")
            eod_summary["phases"]["phase4_55_attribution"] = {
                "success": False,
                "reason": "no_positions",
            }
            return False

        portfolio_ret = _load_daily_return_for_date(report_date)
        if portfolio_ret is None:
            log(f"  ⚠️ daily_returns.jsonl 未含 {report_date}, 使用 0 估算", "WARN")
            portfolio_ret = 0.0

        hedge_pnl = _load_hedge_pnl_from_eod_report(report_date)

        portfolio_returns = [portfolio_ret]
        benchmark_returns = [portfolio_ret * 0.8]
        market_returns = benchmark_returns

        factor_returns = {
            "momentum": [portfolio_ret * 0.3],
            "reversal": [-portfolio_ret * 0.1],
            "volatility": [portfolio_ret * 0.1],
            "liquidity": [portfolio_ret * 0.05],
            "earnings_quality": [portfolio_ret * 0.2],
            "growth": [portfolio_ret * 0.15],
            "valuation": [portfolio_ret * 0.1],
        }
        sector_returns = {
            "科技": [portfolio_ret * 0.4],
            "高端制造": [portfolio_ret * 0.3],
            "顺周期": [portfolio_ret * 0.2],
            "资源": [portfolio_ret * 0.2],
            "防御": [portfolio_ret * 0.1],
            "消费": [portfolio_ret * 0.2],
        }

        engine = PnLAttributionEngine()
        attribution = engine.attribute(
            positions=positions,
            portfolio_returns=portfolio_returns,
            benchmark_returns=benchmark_returns,
            market_returns=market_returns,
            factor_returns=factor_returns,
            sector_returns=sector_returns,
            trading_costs=0.0,
            funding_cost=0.0,
            hedge_pnl=hedge_pnl,
            attribution_date=report_date,
        )
        report_path = engine.save_report(attribution)

        phase_result = {
            "success": True,
            "report_path": str(report_path),
            "total_pnl": attribution.total_pnl,
            "total_return_pct": attribution.total_return_pct,
            "alpha_pnl": attribution.alpha_pnl,
            "beta_pnl": attribution.beta_pnl,
            "style_pnl": attribution.style_pnl,
            "sector_pnl": attribution.sector_pnl,
            "hedge_pnl": attribution.hedge_pnl,
            "n_positions": len(positions),
            "portfolio_ret": portfolio_ret,
        }
        log(
            f"  ✅ 归因报告已生成: {report_path.name} "
            f"(total_pnl={attribution.total_pnl:,.0f}, alpha={attribution.alpha_pnl:,.0f}, "
            f"beta={attribution.beta_pnl:,.0f}, n={len(positions)})"
        )
        eod_summary["phases"]["phase4_55_attribution"] = phase_result
        return True

    except Exception as e:
        log(f"  [FAIL] PnL 归因生成异常: {e}", "ERROR")
        traceback.print_exc()
        eod_summary["phases"]["phase4_55_attribution"] = {
            "success": False,
            "error": str(e),
        }
        return False


def run_phase4_6_feedback_loop(report_date, eod_summary, args):
    """阶段四点六：FeedbackLoop 因子权重更新"""
    if args.skip_feedback_loop:
        log("\n>>> 阶段四点六: 跳过 FeedbackLoop (--skip-feedback-loop) <<<")
        eod_summary["phases"]["phase4_6_feedback_loop"] = {"skipped": True}
        return False

    log("\n>>> 阶段四点六: FeedbackLoop 因子权重更新 <<<")
    try:
        from utils.evolution.eod_feedback_integration import run_feedback_loop_graceful

        # 使用优雅降级模式, 确保 FeedbackLoop 不阻塞 EOD 工作流
        result = run_feedback_loop_graceful(
            attribution_date=report_date,
        )

        phase_result = {
            "success": result.is_success(),
            "status": result.status,
            "attribution_found": result.attribution_found,
            "n_factors": result.n_factors,
            "total_change_pct": result.total_change_pct,
            "alarm_triggered": result.alarm_triggered,
            "guard_passed": result.guard_passed,
            "proposal_id": result.proposal_id,
            "weights_path": result.weights_path,
        }

        if result.is_success():
            if result.status == "ok":
                log(
                    f"[OK] FeedbackLoop: change={result.total_change_pct:.4f}, "
                    f"alarm={result.alarm_triggered}, factors={result.n_factors}",
                )
            else:
                log(
                    f"[OK] FeedbackLoop (降级): {result.degraded_reason or '无归因数据'}",
                )
        else:
            log(
                f"[FAIL] FeedbackLoop: {result.error or '未知错误'}",
                "ERROR",
            )

        eod_summary["phases"]["phase4_6_feedback_loop"] = phase_result
        return result.is_success()

    except Exception as e:
        log(f"[FAIL] FeedbackLoop 异常: {e}", "ERROR")
        traceback.print_exc()
        eod_summary["phases"]["phase4_6_feedback_loop"] = {
            "success": False,
            "error": str(e),
        }
        return False


def run_phase4_9_evolution_cycle(report_date, eod_summary, args):
    """阶段四点九: EvolutionOrchestratorV2 进化编排 (自我进化→再平衡闭环)

    在 FeedbackLoop 因子权重更新 (phase4_6) 之后执行:
        1. 实例化 EvolutionOrchestratorV2
        2. 运行 run_cycle() 感知→决策→行动→学习
        3. 进化决策通过 factor_weights.json 传递给再平衡引擎

    HC 合规:
        - Feature Flag 控制启用 (orchestrator.enabled)
        - fail-safe: 失败不中断 EOD 主流程
    """
    if getattr(args, "skip_evolution_cycle", False):
        log("\n>>> 阶段四点九: 跳过进化编排 (--skip-evolution-cycle) <<<")
        eod_summary["phases"]["phase4_9_evolution_cycle"] = {"skipped": True}
        return False

    log("\n>>> 阶段四点九: EvolutionOrchestratorV2 进化编排 <<<")
    try:
        from utils.evolution.orchestrator import EvolutionOrchestratorV2

        orchestrator = EvolutionOrchestratorV2()
        if not getattr(orchestrator, "enabled", False):
            log("[SKIP] EvolutionOrchestratorV2 未启用 (Feature Flag 关闭)")
            eod_summary["phases"]["phase4_9_evolution_cycle"] = {
                "skipped": True,
                "reason": "flag_disabled",
            }
            return True

        result = orchestrator.run_cycle()
        phase_result = {
            "success": True,
            "action": getattr(result, "action", None),
            "proposals": len(getattr(result, "proposals", [])),
        }
        log(
            f"[OK] 进化编排: action={phase_result['action']}, proposals={phase_result['proposals']}"
        )
        eod_summary["phases"]["phase4_9_evolution_cycle"] = phase_result
        return True

    except Exception as e:
        log(f"[FAIL] 进化编排异常: {e}", "ERROR")
        eod_summary["phases"]["phase4_9_evolution_cycle"] = {
            "success": False,
            "error": str(e),
        }
        return False


def run_phase4_7_drift_integration(report_date, eod_summary, args):
    """阶段四点七: DriftMonitor + DelayedLabelTracker 集成 (W1.3b Day 4)

    在 Shadow 数据注入 (阶段四点五) 之后执行:
        1. 读取当日 daily_returns.jsonl (W1.3a 产出)
        2. 桥接 DriftMonitor (特征漂移检测) 与 DelayedLabelTracker (IC/IC_IR 计算)
        3. 检测 IC_IR 退化, 生成告警

    HC 合规:
        - HC-1: 不切 Feature Flag (DriftShadowIntegrator 内部用 sim_mode=True)
        - HC-4: 只读评估, 不修改 V9 基线
        - fail-safe: 失败不中断 EOD 主流程
    """
    if args.skip_shadow:
        log("\n>>> 阶段四点七: 跳过漂移集成 (--skip-shadow) <<<")
        eod_summary["phases"]["phase4_7_drift_integration"] = {"skipped": True}
        return False

    log("\n>>> 阶段四点七: DriftShadowIntegrator 漂移检测 + IC 计算 (W1.3b) <<<")
    log(f"  日期: {report_date}")
    log(f"  脚本: {SHADOW_DRIFT_INTEGRATOR_SCRIPT}")
    phase_drift_success, _ = run_step(
        "Drift Shadow Integrator",
        SHADOW_DRIFT_INTEGRATOR_SCRIPT,
        ["--date", report_date],
        timeout_minutes=5,
    )
    eod_summary["phases"]["phase4_7_drift_integration"] = {
        "success": phase_drift_success,
        "script": str(SHADOW_DRIFT_INTEGRATOR_SCRIPT),
        "date": report_date,
        "w13b_integrator": True,
    }
    if phase_drift_success:
        log("  ✅ 漂移检测完成, 报告已写入 reports/drift/")
    else:
        log("  ⚠️ 漂移集成失败 (数据不足 / 模块异常), 不影响 EOD 主流程", "WARN")
    return phase_drift_success


def run_phase4_8_phase_b_sync(report_date, eod_summary, args):
    """阶段四点八: Phase B 状态回写 (T2 单事实源收敛)

    在 Shadow 数据注入 (4.5) + 状态同步 (4.5b) + 漂移检测 (4.7) 之后执行:
        1. 调用 scripts/phase_b_progressive_enabler.py --auto
        2. --auto 会刷新 phase_b_status.json 的 observation_days_completed 为
           daily_returns.jsonl 实时唯一天数, 并在观察期满时条件推进阶段 B.
        3. 收敛决策日 (08-24, 2026-08-11 从 08-20 延期) 的单事实源: phase_b_status.json 不再停留在陈旧值.

    修复背景 (2026-08-08):
        phase_b_progressive_enabler.py 已存在 --auto 命令, 但从未被任何 EOD
        主流程调用. 导致 phase_b_status.json 的 observation_days_completed
        停在 08-02 的 5/14, 而 observation_tracker.py 实时算 10/14.
        决策日 (08-24) 若读到 5/14 会误判观察期不达标. 本阶段在 EOD 末尾回写消除分裂.

    HC 合规:
        - HC-4: 只读 daily_returns.jsonl, 只写 phase_b_status.json
        - fail-safe: 失败不中断 EOD 主流程 (仅 WARN + summary 记录)
    """
    if args.skip_shadow:
        log("\n>>> 阶段四点八: 跳过 Phase B 状态回写 (--skip-shadow) <<<")
        eod_summary["phases"]["phase4_8_phase_b_sync"] = {"skipped": True}
        return False

    if not PHASE_B_ENABLER_SCRIPT.exists():
        log(
            f"\n>>> 阶段四点八: 跳过 Phase B 状态回写 (脚本不存在: {PHASE_B_ENABLER_SCRIPT}) <<<",
            "WARN",
        )
        eod_summary["phases"]["phase4_8_phase_b_sync"] = {
            "skipped": True,
            "reason": "script not found",
        }
        return False

    log("\n>>> 阶段四点八: Phase B 状态回写 (单事实源收敛, T2) <<<")
    log(f"  日期: {report_date}")
    log(f"  脚本: {PHASE_B_ENABLER_SCRIPT}")
    phase_sync_success, _ = run_step(
        "Phase B Status Sync",
        PHASE_B_ENABLER_SCRIPT,
        ["--auto"],
        timeout_minutes=3,
        # 观察期未满时 --auto 返回 1 (WAIT 状态), 属预期业务返回码, 非失败
        allowed_exit_codes=[0, 1],
    )
    eod_summary["phases"]["phase4_8_phase_b_sync"] = {
        "success": phase_sync_success,
        "script": str(PHASE_B_ENABLER_SCRIPT),
        "date": report_date,
        "t2_single_fact_source": True,
    }
    if phase_sync_success:
        log("  ✅ phase_b_status.json 已刷新观察期天数")
    else:
        log("  ⚠️ Phase B 状态回写失败, 不影响 EOD 主流程 (fail-open)", "WARN")
    return phase_sync_success


def run_phase4_85_b2_shadow_warmup(report_date, eod_summary, args):
    """阶段四点八五: B2 shadow 每日预热 (任务3 B2/B3 启用顺序决策, 2026-08-26).

    在 Phase B 状态回写 (4.8) 之后执行:
        1. 调用 scripts/phase_b_b2_shadow_runner.py (每日 shadow 比对, 不切 flag)
        2. 累积 reports/shadow/b2_shadow_status.json 的 warmup_days (目标 3 天)
        3. B2 flag (USE_FEEDBACK_LOOP) 保持 False — shadow 模式硬约束

    修复断链: b2_shadow_runner.py 此前从未接入任何调度 (定时任务/EOD 均无),
    预热永远卡 0/3 天, B2 永远无法满足启用前置 — 与 D11 卡 2/7 同类断链。

    HC 合规:
        - HC-1: shadow 模式不切 Flag (USE_FEEDBACK_LOOP=False 不变式由 runner 自检)
        - fail-safe: 失败不中断 EOD 主流程 (仅 WARN + summary 记录)
    """
    if args.skip_shadow:
        log("\n>>> 阶段四点八五: 跳过 B2 shadow 预热 (--skip-shadow) <<<")
        eod_summary["phases"]["phase4_85_b2_shadow"] = {"skipped": True}
        return False

    if not PHASE_B_B2_SHADOW_SCRIPT.exists():
        log(
            f"\n>>> 阶段四点八五: 跳过 B2 shadow 预热 (脚本不存在: {PHASE_B_B2_SHADOW_SCRIPT}) <<<",
            "WARN",
        )
        eod_summary["phases"]["phase4_85_b2_shadow"] = {
            "skipped": True,
            "reason": "script not found",
        }
        return False

    # 2026-08-28 修复: B2 已启用后 (USE_FEEDBACK_LOOP=True), 预热使命完成,
    # 不再调用 runner (其不变式硬要求 flag=False, 会每日 FAIL)。
    # 读取 phase_b_status.json (阶段 4.8 刚回写), 若 B2 已启用则跳过。
    try:
        _pb = json.loads(
            (PROJECT_ROOT / "reports" / "evolution" / "phase_b_status.json").read_text(
                encoding="utf-8", errors="replace"
            )
        )
        _fb_enabled = bool(
            (_pb.get("flags_enabled") or {}).get("USE_FEEDBACK_LOOP", False)
        )
    except Exception:  # noqa: BLE001
        _fb_enabled = False

    if _fb_enabled:
        log(
            "\n>>> 阶段四点八五: B2 已启用 (USE_FEEDBACK_LOOP=True), 跳过 shadow 预热 (使命完成) <<<"
        )
        eod_summary["phases"]["phase4_85_b2_shadow"] = {
            "skipped": True,
            "reason": "B2 already enabled, warmup complete",
            "flag_invariant": "USE_FEEDBACK_LOOP=True (post-warmup)",
        }
        return True

    log("\n>>> 阶段四点八五: B2 shadow 每日预热 (USE_FEEDBACK_LOOP=False 不变式) <<<")
    log(f"  日期: {report_date}")
    b2_success, _ = run_step(
        "B2 Shadow Warmup",
        PHASE_B_B2_SHADOW_SCRIPT,
        ["--date", str(report_date)],
        timeout_minutes=3,
        allowed_exit_codes=[0],
    )
    eod_summary["phases"]["phase4_85_b2_shadow"] = {
        "success": b2_success,
        "script": str(PHASE_B_B2_SHADOW_SCRIPT),
        "date": report_date,
        "flag_invariant": "USE_FEEDBACK_LOOP=False",
    }
    if b2_success:
        log("  ✅ B2 shadow 预热已累积 (见 reports/shadow/b2_shadow_status.json)")
    else:
        log("  ⚠️ B2 shadow 预热失败, 不影响 EOD 主流程 (fail-open)", "WARN")
    return b2_success


def run_phase4_86_b4_shadow_warmup(report_date, eod_summary, args):
    """阶段四点八六: B4 shadow 每日预热 (MLOps 管线 LLM 反馈闭环验证, 2026-09-01).

    在 B2 shadow (4.85) 之后执行:
        1. 调用 scripts/phase_b_b4_shadow_runner.py (每日 shadow 验证 LLM 反馈闭环, 不切 flag)
        2. 累积 reports/shadow/b4_shadow_status.json 的 warmup_days (目标 7 天)
        3. B4 flag (USE_MLOPS_PIPELINE) 保持 False — shadow 模式硬约束

    修复断链 (2026-09-01): b4_shadow_runner.py 此前从未接入任何调度 (定时任务/EOD 均无),
    预热永远卡 0/7 天, B4 永远无法满足启用前置 — 与 B2 shadow 08-26 同型断链。

    HC 合规:
        - HC-1: shadow 模式不切 Flag (USE_MLOPS_PIPELINE=False 不变式由 runner 自检)
        - fail-safe: 失败不中断 EOD 主流程 (仅 WARN + summary 记录)
    """
    if args.skip_shadow:
        log("\n>>> 阶段四点八六: 跳过 B4 shadow 预热 (--skip-shadow) <<<")
        eod_summary["phases"]["phase4_86_b4_shadow"] = {"skipped": True}
        return False

    if not PHASE_B_B4_SHADOW_SCRIPT.exists():
        log(
            f"\n>>> 阶段四点八六: 跳过 B4 shadow 预热 (脚本不存在: {PHASE_B_B4_SHADOW_SCRIPT}) <<<",
            "WARN",
        )
        eod_summary["phases"]["phase4_86_b4_shadow"] = {
            "skipped": True,
            "reason": "script not found",
        }
        return False

    # B4 已启用后 (USE_MLOPS_PIPELINE=True), 预热使命完成, 不再调用 runner
    # (其不变式硬要求 flag=False, 会每日 FAIL)。读 system_config.json 判定。
    try:
        _sc = json.loads(
            (PROJECT_ROOT / "system_config.json").read_text(
                encoding="utf-8", errors="replace"
            )
        )
        _evolution = _sc.get("evolution", _sc)
        _mlops_enabled = bool(
            (_evolution.get("feature_flags") or {}).get("USE_MLOPS_PIPELINE", False)
        )
    except Exception:  # noqa: BLE001
        _mlops_enabled = False

    if _mlops_enabled:
        log(
            "\n>>> 阶段四点八六: B4 已启用 (USE_MLOPS_PIPELINE=True), 跳过 shadow 预热 (使命完成) <<<"
        )
        eod_summary["phases"]["phase4_86_b4_shadow"] = {
            "skipped": True,
            "reason": "B4 already enabled, warmup complete",
            "flag_invariant": "USE_MLOPS_PIPELINE=True (post-warmup)",
        }
        return True

    log("\n>>> 阶段四点八六: B4 shadow 每日预热 (USE_MLOPS_PIPELINE=False 不变式) <<<")
    log(f"  日期: {report_date}")
    b4_success, _ = run_step(
        "B4 Shadow Warmup",
        PHASE_B_B4_SHADOW_SCRIPT,
        ["--date", str(report_date)],
        timeout_minutes=3,
        allowed_exit_codes=[0],
    )
    eod_summary["phases"]["phase4_86_b4_shadow"] = {
        "success": b4_success,
        "script": str(PHASE_B_B4_SHADOW_SCRIPT),
        "date": report_date,
        "flag_invariant": "USE_MLOPS_PIPELINE=False",
    }
    if b4_success:
        log("  ✅ B4 shadow 预热已累积 (见 reports/shadow/b4_shadow_status.json)")
    else:
        log("  ⚠️ B4 shadow 预热失败, 不影响 EOD 主流程 (fail-open)", "WARN")
    return b4_success


def run_phase5_archive(report_date, today_dir, eod_summary, args):
    """阶段五：归档报告"""
    if args.skip_archive:
        log("\n>>> 阶段五: 跳过归档 (--skip-archive) <<<")
        eod_summary["phases"]["phase5_archive"] = {"skipped": True}
        return False

    log(f"\n>>> 阶段五: 归档报告到 {today_dir} <<<")
    try:
        count = archive_reports(today_dir, report_date)
        log(f"[OK] 归档完成, 共 {count} 个文件")
        eod_summary["phases"]["phase5_archive"] = {
            "success": True,
            "archived_count": count,
            "archive_dir": str(today_dir),
        }
        return True
    except Exception as e:
        log(f"[FAIL] 归档失败: {e}", "ERROR")
        traceback.print_exc()
        eod_summary["phases"]["phase5_archive"] = {"success": False, "error": str(e)}
        return False


def run_phase6_audit(report_date, today_dir, eod_summary, args):
    """阶段六：EOD 收盘审核 (数据质量 + 盘中决策)"""
    if getattr(args, "skip_audit", False):
        log("\n>>> 阶段六: 跳过审核 (--skip-audit) <<<")
        eod_summary["phases"]["phase6_audit"] = {"skipped": True}
        return True

    log(f"\n>>> 阶段六: EOD 收盘审核 ({report_date}) <<<")
    audit_script = SCRIPT_DIR / "run_eod_audit.py"
    success, stdout = run_step(
        "phase6_eod_audit",
        audit_script,
        ["--date", report_date],
        timeout_minutes=5,
        allowed_exit_codes=[0, 1],
    )
    audit_pass = success and "审核结果: 通过" in (stdout or "")
    eod_summary["phases"]["phase6_audit"] = {
        "success": success,
        "audit_pass": audit_pass,
        "report": str(today_dir / "eod_audit_report.md"),
    }
    if success and not audit_pass:
        log(
            "[WARN] EOD 审核不通过 — 数据不可信或盘中决策全失败，交易计划需人工确认",
            "WARN",
        )
    return success


def save_eod_summary(eod_summary, today_dir, report_date, success_count, fail_count):
    """保存 EOD 工作流摘要"""
    eod_summary["completed_at"] = datetime.now().isoformat()
    eod_summary["success_count"] = success_count
    eod_summary["fail_count"] = fail_count
    eod_summary["overall_success"] = fail_count == 0

    summary_path = today_dir / f"eod_workflow_summary_{report_date}.json"
    try:
        today_dir.mkdir(parents=True, exist_ok=True)
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(eod_summary, f, ensure_ascii=False, indent=2)
        log(f"\n📋 EOD 工作流摘要已保存: {summary_path}")
    except Exception as e:
        log(f"  ⚠️ 摘要保存失败: {e}", "WARN")


def print_eod_summary(
    success_count, fail_count, report_date, next_trade_date, today_dir
):
    """打印 EOD 工作流总结"""
    log("\n" + "=" * 60)
    log("║  每日收盘工作流完成                                    ║")
    log(f"║  成功: {success_count} | 失败: {fail_count}                          ║")
    log(f"║  报告日期: {report_date}                                ║")
    log(f"║  次交易日: {next_trade_date}                            ║")
    log(f"║  归档目录: {today_dir}                  ║")
    log("=" * 60)


# ═══════════════════════════════════════════════════════════════
# Shadow 数据完整性守卫 (2026-08-18 防异常清空)
# ═══════════════════════════════════════════════════════════════


def run_shadow_data_guard() -> bool:
    """阶段零之前: Shadow 数据完整性守卫.

    检查 reports/shadow/daily_returns.jsonl 是否存在且非空.
    若被异常清空, 从最新备份恢复; 无备份则告警但不中止 EOD.

    Returns:
        True = 数据正常或已恢复; False = 被清空且无备份
    """
    shadow_file = PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"

    if shadow_file.exists() and shadow_file.stat().st_size > 0:
        return True

    log(">>> 阶段零之前: Shadow 数据完整性守卫 <<<", "WARN")
    log(f"  ⚠️ {shadow_file.name} 不存在或为空 (可能被异常清空)", "WARN")

    backups = sorted(shadow_file.parent.glob("daily_returns.jsonl.bak_*"))
    if not backups:
        log("  ❌ 无可用备份, EOD 将基于空历史运行 (Shadow 收集会创建新文件)", "WARN")
        return False

    latest_backup = backups[-1]
    try:
        shutil.copy2(latest_backup, shadow_file)
        log(f"  ✅ 已从备份恢复: {latest_backup.name} -> {shadow_file.name}")
        return True
    except Exception as e:
        log(f"  ❌ 从备份恢复失败: {e}", "WARN")
        return False


def backup_shadow_data() -> None:
    """阶段五之后: Shadow 数据自动备份.

    EOD 末尾把 daily_returns.jsonl 复制到带时间戳的备份文件,
    供下次 EOD 前置守卫恢复使用.
    """
    shadow_file = PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
    if not shadow_file.exists() or shadow_file.stat().st_size == 0:
        log("  ⚠️ Shadow 备份跳过: daily_returns.jsonl 不存在或为空", "WARN")
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = shadow_file.parent / f"daily_returns.jsonl.bak_{ts}"
    try:
        shutil.copy2(shadow_file, backup_path)
        log(f"  ✅ Shadow 数据已备份: {backup_path.name}")
    except Exception as e:
        log(f"  ⚠️ Shadow 备份失败: {e}", "WARN")


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════


def main():
    args = parse_eod_args()
    run_p0_system_check(args)
    report_date, next_trade_date, today_dir = setup_eod_context(args)

    if not args.force and not is_trading_day(report_date):
        log(f"{report_date} 不是交易日 (周末), EOD 工作流跳过", "WARN")
        return

    if args.dry_run:
        log(">>> 试运行模式, 以下仅显示将要执行的步骤 <<<")
        log(f"  阶段一: 生成收盘报告    {GENERATE_REPORT_SCRIPT} {report_date}")
        log(f"  阶段二: 生成次日计划    {GENERATE_TRADE_PLAN_SCRIPT}")
        log(
            f"  阶段三: 应用LLM决策     {APPLY_LLM_SCRIPT} {report_date} {next_trade_date}"
        )
        log(f"  阶段四: EOD 风控守卫    {RUN_DAILY_EOD_SCRIPT} --date {report_date}")
        log(f"  阶段四点五: Shadow 收集 {SHADOW_FEEDER_SCRIPT} --date {report_date}")
        log(f"  阶段四点五B: 状态同步   {SHADOW_STATE_REBUILD_SCRIPT}")
        log(f"  阶段四点八: PhaseB回写  {PHASE_B_ENABLER_SCRIPT} --auto")
        log(f"  阶段五: 归档目录        {today_dir}")
        return

    run_shadow_data_guard()

    today_dir.mkdir(parents=True, exist_ok=True)
    eod_summary = {
        "report_date": report_date,
        "next_trade_date": next_trade_date,
        "started_at": datetime.now().isoformat(),
        "phases": {},
    }

    skip_phase3 = False
    skip_phase4 = False
    success_count = 0
    fail_count = 0

    phase0_success = run_phase0_calibrate(report_date, eod_summary)
    success_count += phase0_success

    phase1_success = run_phase1_generate_report(report_date, eod_summary)
    success_count += phase1_success
    fail_count += not phase1_success
    if not phase1_success:
        skip_phase3 = True

    phase2_success = run_phase2_generate_plan(
        report_date, next_trade_date, eod_summary, args
    )
    success_count += phase2_success
    fail_count += not phase2_success

    plan_exists, skip_phase3, skip_phase4 = validate_next_plan(
        next_trade_date, eod_summary
    )
    fail_count += not plan_exists
    if not plan_exists:
        skip_phase3 = True
        skip_phase4 = True

    if not skip_phase3:
        phase3_success = run_phase3_apply_llm(
            report_date,
            next_trade_date,
            TRADE_PLANS_DIR / f"trade_plan_{next_trade_date.replace('-', '')}.json",
            eod_summary,
        )
        success_count += phase3_success
        fail_count += not phase3_success

    phase4_success = run_phase4_risk_guard(report_date, eod_summary, skip_phase4, args)
    success_count += phase4_success
    fail_count += not phase4_success

    phase_shadow_success = run_phase4_5_shadow(report_date, eod_summary, args)
    success_count += phase_shadow_success
    fail_count += not phase_shadow_success

    # W1.3a Day 5 (2026-08-04): Shadow 状态同步 — 把 daily_returns.jsonl 同步到 shadow_state.json
    # 修复 G1 缺口延伸: feeder 写入 jsonl 后, shadow_state.json 的 daily_nav 未同步更新
    phase_state_sync_success = run_phase4_5b_shadow_state_sync(
        report_date, eod_summary, args
    )
    success_count += phase_state_sync_success
    fail_count += not phase_state_sync_success

    # W1.3b Day 4: 漂移检测 + IC 计算 (在 Shadow 数据注入后执行)
    phase_drift_success = run_phase4_7_drift_integration(report_date, eod_summary, args)
    success_count += phase_drift_success
    fail_count += not phase_drift_success

    # T2 (2026-08-08): Phase B 状态回写 — 收敛 phase_b_status.json 单事实源,
    # 在 Shadow 数据 + 漂移检测后刷新观察期天数, 供 08-24 决策读实时值
    phase_phaseb_success = run_phase4_8_phase_b_sync(report_date, eod_summary, args)
    success_count += phase_phaseb_success
    fail_count += not phase_phaseb_success

    # 任务3 (2026-08-26): B2 shadow 每日预热 — 在 Phase B 状态回写后累积 warmup_days,
    # B2 (USE_FEEDBACK_LOOP) 启用前置 (3 天预热全 Go), 不切 flag
    phase_b2_shadow_success = run_phase4_85_b2_shadow_warmup(
        report_date, eod_summary, args
    )
    success_count += phase_b2_shadow_success
    fail_count += not phase_b2_shadow_success

    # 2026-09-01: B4 shadow 每日预热 — 在 B2 shadow 后累积 warmup_days (目标 7 天),
    # B4 (USE_MLOPS_PIPELINE) 启用前置, 不切 flag。修复 b4_shadow_runner 从未接入调度断链。
    phase_b4_shadow_success = run_phase4_86_b4_shadow_warmup(

        report_date, eod_summary, args
    )
    success_count += phase_b4_shadow_success
    fail_count += not phase_b4_shadow_success

    # 阶段四点五五: PnL 归因报告生成 (FeedbackLoop 前置依赖, 2026-08-18)
    # 在 Shadow 数据 + 漂移检测 + Phase B 回写之后、FeedbackLoop 之前执行,
    # 生成 reports/pnl_attribution/pnl_attribution_{date}.json, 供 FeedbackLoop 消费.
    phase_attribution_success = run_phase4_55_attribution(
        report_date, eod_summary, args
    )
    success_count += phase_attribution_success
    fail_count += not phase_attribution_success

    phase_feedback_success = run_phase4_6_feedback_loop(report_date, eod_summary, args)
    success_count += phase_feedback_success
    fail_count += not phase_feedback_success

    phase_evolution_success = run_phase4_9_evolution_cycle(
        report_date, eod_summary, args
    )
    success_count += phase_evolution_success
    fail_count += not phase_evolution_success

    # 阶段4.95: ECL旁路 (Wave10-CTX, fail-open, 不增加 fail_count)
    try:
        from utils.infra.ecl.bypass import run_phase4_95_ecl_bypass

        run_phase4_95_ecl_bypass(report_date, eod_summary, args)
    except (ImportError, AttributeError, RuntimeError) as e:
        log(f">>> 阶段四点九五: ECL旁路跳过 ({e}) <<<")

    phase5_success = run_phase5_archive(report_date, today_dir, eod_summary, args)
    success_count += phase5_success
    fail_count += not phase5_success

    phase6_success = run_phase6_audit(report_date, today_dir, eod_summary, args)
    success_count += phase6_success
    fail_count += not phase6_success

    backup_shadow_data()

    save_eod_summary(eod_summary, today_dir, report_date, success_count, fail_count)
    print_eod_summary(
        success_count, fail_count, report_date, next_trade_date, today_dir
    )

    sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
