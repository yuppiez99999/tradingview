#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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

import os
import sys
import json
import shutil
import subprocess
import argparse
import traceback
from pathlib import Path
from datetime import datetime, timedelta

# 强制 UTF-8 输出, 解决 GBK 编码问题
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
PROJECT_ROOT = SCRIPT_DIR.parent  # e:\各种PY程序\28-终极量化交易系统8.4
ARCHIVE_DIR = PROJECT_ROOT / "每日报告归档"  # 归档到项目根目录下
# C8 修复: 不再硬编码 Python 解释器路径, 优先使用环境变量或当前解释器
VENV_PYTHON = os.environ.get("QUANT_PYTHON") or sys.executable

# 关键脚本路径
GENERATE_REPORT_SCRIPT = PROJECT_ROOT / "generate_daily_report.py"
GENERATE_TRADE_PLAN_SCRIPT = PROJECT_ROOT / "v8.3_institutional" / "generate_daily_trade_plan.py"
APPLY_LLM_SCRIPT = PROJECT_ROOT / "tools" / "apply_llm_decisions_to_plan.py"
RUN_DAILY_EOD_SCRIPT = PROJECT_ROOT / "run_daily_eod.py"
DAILY_WORKFLOW_SCRIPT = PROJECT_ROOT / "v8.3_institutional" / "daily_workflow.py"
# 阶段零: 年化收益预测校准 (生成 portfolio_return_projection.json, 供阶段一报告引用)
CALIBRATE_PROJECTION_SCRIPT = PROJECT_ROOT / "v8.3_institutional" / "calibrate_returns_projection.py"
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
    print(line)
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


def run_step(name: str, script: Path, args: list, timeout_minutes: int = 30) -> tuple:
    """
    运行一个工作流步骤
    返回 (success: bool, stdout: str)
    """
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
        env.pop('PYTHONHOME', None)
        env.pop('PYTHONPATH', None)
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUTF8'] = '1'

        # M3 修复: 使用 CREATE_NEW_PROCESS_GROUP 创建独立进程组, 便于超时后 taskkill /T 彻底清理孙进程
        creationflags = 0
        if sys.platform == 'win32':
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
            stdout_preview = stdout_preview[:5000] + f"\n... [截断, 共{len(result.stdout)}字符]"
        if stdout_preview:
            log(f"STDOUT:\n{stdout_preview}")

        if result.stderr:
            stderr_preview = result.stderr[:3000]
            if len(result.stderr) > 3000:
                stderr_preview += f"\n... [截断, 共{len(result.stderr)}字符]"
            log(f"STDERR:\n{stderr_preview}", "WARN")

        if result.returncode == 0:
            log(f"[OK] {name} 执行成功 (exit_code=0)")
            return True, result.stdout or ""
        else:
            log(f"[FAIL] {name} 执行失败 (exit_code={result.returncode})", "ERROR")
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
                    date_compact in fname or
                    date_dash in fname or
                    fname.endswith(f"_{date_compact}.md") or
                    fname.endswith(f"_{date_compact}.json") or
                    fname.endswith(f"_{date_dash}.md") or
                    fname.endswith(f"_{date_dash}.json")
                )

                # C10 修复: 使用 fnmatch 进行通配符匹配 (原 startswith 把 * 当字面量, 永不命中)
                # 额外检查: EOD 守卫报告 / 对冲执行单
                if not is_target_report:
                    is_target_report = any(
                        fnmatch.fnmatch(fname, pat)
                        for pat in ["eod_guard_report_*", "hedge_execution_fill_*",
                                   "eod_guard_report_*.md", "hedge_execution_fill_*.json"]
                    ) and date_dash in fname

                if is_target_report:
                    dest = today_dir / fname
                    # 仅当目标不存在或源文件更新时复制
                    if not dest.exists() or file_path.stat().st_mtime > dest.stat().st_mtime:
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
        with open(plan_path, "r", encoding="utf-8") as f:
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


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="每日收盘工作流 (15:30 收盘后运行)")
    parser.add_argument("--date", type=str, default=None,
                        help="报告日期 YYYY-MM-DD (默认今天)")
    parser.add_argument("--dry-run", action="store_true",
                        help="试运行模式, 不实际执行")
    parser.add_argument("--force", action="store_true",
                        help="强制运行, 跳过交易日检查")
    parser.add_argument("--skip-risk-guard", action="store_true",
                        help="跳过阶段四 (EOD 四 Guard 风控链)")
    parser.add_argument("--skip-archive", action="store_true",
                        help="跳过阶段五 (报告归档)")
    parser.add_argument("--skip-generate-plan", action="store_true",
                        help="跳过阶段二 (生成次日交易计划, 使用已存在的计划)")
    parser.add_argument("--skip-shadow", action="store_true",
                        help="跳过阶段四点五 (Phase 10 Shadow 数据收集, 观察期专用)")
    parser.add_argument("--skip-system-check", action="store_true",
                        help="跳过 P0 启动自检 (仅紧急情况使用,默认每次启动都自检)")
    args = parser.parse_args()

    # ═══════════════════════════════════════════════════════════════
    # P0 启动自检 (v8.6.12) — 在任何业务逻辑之前拦截错误
    # 自检失败立即退出,阻止 bug 传播到下游工作流
    # ═══════════════════════════════════════════════════════════════
    if not args.skip_system_check:
        try:
            # 显式将项目根加入 sys.path (此脚本位于 15_每日工作流/ 子目录)
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from utils.system_check import assert_system_ready
            assert_system_ready()  # 失败时 sys.exit(1)
        except SystemExit:
            raise
        except Exception as e:
            print(f"[P0 自检] 异常 (容错通过): {e}", file=sys.stderr)

    # 确定报告日期
    if args.date:
        report_date = args.date
    else:
        report_date = datetime.now().strftime("%Y-%m-%d")

    next_trade_date = get_next_trading_day(report_date)
    today_dir = ARCHIVE_DIR / report_date

    log("╔" + "═" * 60 + "╗")
    log("║  每日收盘工作流启动 (EOD Workflow)                       ║")
    log(f"║  报告日期: {report_date}                                  ║")
    log(f"║  次交易日: {next_trade_date}                              ║")
    log(f"║  执行时间: {datetime.now().strftime('%H:%M:%S')}                 ║")
    log(f"║  模式: {'强制' if args.force else '标准'}                               ║")
    log("╚" + "═" * 60 + "╝")

    # 交易日检查 (EOD 必须在交易日运行)
    if not args.force and not is_trading_day(report_date):
        log(f"{report_date} 不是交易日 (周末), EOD 工作流跳过", "WARN")
        log("如需强制运行, 请使用 --force 参数")
        return

    if args.dry_run:
        log(">>> 试运行模式, 以下仅显示将要执行的步骤 <<<")
        log(f"  阶段一: 生成收盘报告    {GENERATE_REPORT_SCRIPT} {report_date}")
        log(f"  阶段二: 生成次日计划    {GENERATE_TRADE_PLAN_SCRIPT}")
        log(f"  阶段三: 应用LLM决策     {APPLY_LLM_SCRIPT} {report_date} {next_trade_date}")
        log(f"  阶段四: EOD 风控守卫    {RUN_DAILY_EOD_SCRIPT} --date {report_date}")
        log(f"  阶段四点五: Shadow 收集 {DAILY_WORKFLOW_SCRIPT} --phase shadow_monitor --date {report_date}")
        log(f"  阶段五: 归档目录        {today_dir}")
        return

    # 创建归档目录
    today_dir.mkdir(parents=True, exist_ok=True)

    success_count = 0
    fail_count = 0
    eod_summary = {
        "report_date": report_date,
        "next_trade_date": next_trade_date,
        "started_at": datetime.now().isoformat(),
        "phases": {},
    }

    # M10 修复: 关键失败标志 — 阶段失败阻断依赖阶段, 防止级联错误
    # 依赖关系: 阶段三依赖阶段一(AI建议)+阶段二(计划); 阶段四依赖阶段二(计划)
    # 阶段五(归档)始终执行, 保留已生成的证据
    skip_phase3 = False  # 阶段一失败 → 跳过阶段三 (无 AI 建议可灌入)
    skip_phase4 = False  # 阶段二失败且无计划 → 跳过阶段四 (无计划可更新风控字段)

    # ─────────────────────────────────────────────────────────
    # 阶段零: 年化收益预测校准 (生成 portfolio_return_projection.json)
    # ─────────────────────────────────────────────────────────
    # 供阶段一报告引用真实测算值, 替代旧代码硬编码 0.1072 等假数据
    # 非关键阶段: 失败不阻断后续流程 (报告将显示 "待测算")
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
            success_count += 1
            log("  ✅ portfolio_return_projection.json 已生成, 报告将引用真实测算值")
        else:
            log("  ⚠️ 年化收益校准失败, 报告预期绩效将显示 '待测算'", "WARN")
    else:
        log(f"  ⚠️ 校准脚本不存在: {CALIBRATE_PROJECTION_SCRIPT}", "WARN")
        eod_summary["phases"]["phase0_calibrate_projection"] = {
            "skipped": True,
            "reason": "script not found",
        }

    # ─────────────────────────────────────────────────────────
    # 阶段一: 生成收盘盈亏报告 (含 DeepSeek AI 决策建议)
    # ─────────────────────────────────────────────────────────
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
        success_count += 1
        # 验证报告是否包含 AI 建议
        report_json = REPORTS_DIR_V83 / f"daily_pnl_report_{report_date}.json"
        if report_json.exists():
            try:
                with open(report_json, "r", encoding="utf-8") as f:
                    report_data = json.load(f)
                ai_recs = report_data.get("ai_recommendations", [])
                log(f"  ✅ 报告包含 {len(ai_recs)} 条 AI 决策建议")
                for i, rec in enumerate(ai_recs, 1):
                    log(f"     {i}. {rec[:80]}{'...' if len(rec) > 80 else ''}")
                eod_summary["phases"]["phase1_generate_report"]["ai_recs_count"] = len(ai_recs)
                eod_summary["phases"]["phase1_generate_report"]["ai_recommendations"] = ai_recs
            except Exception as e:
                log(f"  ⚠️ 读取报告 AI 建议失败: {e}", "WARN")
    else:
        fail_count += 1
        # M10 修复: 阶段一失败 → 阶段三无 AI 建议可灌入, 标记跳过
        skip_phase3 = True
        log("收盘报告生成失败 [关键失败] — 阶段三 (LLM决策灌入) 将跳过, 因无 AI 建议可用", "ERROR")
        eod_summary["phases"]["phase1_generate_report"]["critical"] = True
        eod_summary["phases"]["phase1_generate_report"]["skip_downstream"] = ["phase3_apply_llm"]

    # ─────────────────────────────────────────────────────────
    # 阶段二: 生成次日交易计划 (基础计划, 待 LLM 决策覆盖)
    # ─────────────────────────────────────────────────────────
    if not args.skip_generate_plan:
        log("\n>>> 阶段二: 生成次日交易计划 <<<")
        phase2_success, _ = run_step(
            "次日交易计划生成",
            GENERATE_TRADE_PLAN_SCRIPT,
            [],
            timeout_minutes=10,
        )
        eod_summary["phases"]["phase2_generate_plan"] = {
            "success": phase2_success,
            "script": str(GENERATE_TRADE_PLAN_SCRIPT),
        }
        if phase2_success:
            success_count += 1
        else:
            fail_count += 1
            log("次日交易计划生成失败, 尝试使用已存在的计划", "WARN")
    else:
        log("\n>>> 阶段二: 跳过 (使用已存在的次日交易计划) <<<")
        eod_summary["phases"]["phase2_generate_plan"] = {"skipped": True}

    # 验证次日计划是否存在
    next_plan_filename = f"trade_plan_{next_trade_date.replace('-', '')}.json"
    next_plan_path = TRADE_PLANS_DIR / next_plan_filename
    if not next_plan_path.exists():
        # M10 修复: 无次日计划 → 阶段三/四无法执行 (无计划可灌入/更新风控)
        # 这是级联错误的关键节点: 计划缺失会导致后续阶段全部空转
        skip_phase3 = True
        skip_phase4 = True
        log(f"[FAIL] 次日交易计划不存在: {next_plan_path} [关键失败]", "ERROR")
        log("请确认 generate_daily_trade_plan.py 已运行, 或使用 --skip-generate-plan 仅在计划已存在时使用", "ERROR")
        log("阶段三 (LLM决策灌入) 和 阶段四 (风控守卫) 将跳过, 因无计划可操作", "ERROR")
        fail_count += 1
        eod_summary["phases"]["phase2_generate_plan"] = eod_summary["phases"].get(
            "phase2_generate_plan", {}
        )
        eod_summary["phases"]["phase2_generate_plan"]["plan_exists"] = False
        eod_summary["phases"]["phase2_generate_plan"]["critical"] = True
        eod_summary["phases"]["phase2_generate_plan"]["skip_downstream"] = [
            "phase3_apply_llm", "phase4_risk_guard"
        ]
    else:
        log(f"  ✅ 次日交易计划已就绪: {next_plan_path.name}")

    # ─────────────────────────────────────────────────────────
    # 阶段三: 应用 DeepSeek 决策到次日交易计划
    # ─────────────────────────────────────────────────────────
    # M10 修复: 阶段一失败 (无AI建议) 或 阶段二失败 (无计划) 时跳过
    if skip_phase3:
        log("\n>>> 阶段三: 跳过 (前置关键阶段失败, 无 AI 建议/计划可用) <<<", "WARN")
        eod_summary["phases"]["phase3_apply_llm"] = {
            "skipped": True,
            "skip_reason": "upstream critical failure (phase1 or phase2)",
        }
    elif next_plan_path.exists():
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
            success_count += 1
            # 验证 LLM 决策是否成功写入
            verify_result = verify_llm_overrides_applied(next_plan_path)
            eod_summary["phases"]["phase3_apply_llm"]["verification"] = verify_result
            if verify_result.get("applied"):
                # v8.6.13 P1 FIX: 用 `or {}` 兜底 None, 避免 "..." in None 抛 TypeError
                overrides = verify_result.get("overrides") or {}
                log("  ✅ LLM 决策已写入次日计划:")
                if "futures_if_contracts" in overrides:
                    log(f"     - IF 期货空头: {overrides['futures_if_contracts']} 手")
                if "put_protection" in overrides:
                    log(f"     - Put 保护: {len(overrides['put_protection'])} 个标的")
                if "build_sequence" in overrides:
                    log(f"     - 建仓顺序: {overrides['build_sequence']}")
                if "stop_loss_adjustments" in overrides:
                    log(f"     - 止损调整: {len(overrides['stop_loss_adjustments'])} 个标的")
                if "position_adjustments" in overrides:
                    log(f"     - 仓位调整: {len(overrides['position_adjustments'])} 个标的")
            else:
                log(f"  ⚠️ LLM 决策未写入: {verify_result.get('reason', '未知原因')}", "WARN")
        else:
            fail_count += 1
            # M10 修复: 阶段三失败为非关键 — 计划仍可用 (无 LLM 覆盖), 阶段四可继续
            log("LLM 决策应用失败 [非关键] — 次日计划仍可使用 (无 LLM 覆盖), 继续阶段四", "WARN")

    # ─────────────────────────────────────────────────────────
    # 阶段四: 执行 EOD 四 Guard 风控链
    # ─────────────────────────────────────────────────────────
    # M10 修复: 阶段二失败 (无计划) 时跳过 — 风控守卫需更新计划 risk_guard 字段
    if skip_phase4:
        log("\n>>> 阶段四: 跳过 (前置关键阶段失败, 无计划可更新风控字段) <<<", "WARN")
        eod_summary["phases"]["phase4_risk_guard"] = {
            "skipped": True,
            "skip_reason": "upstream critical failure (no trade plan available)",
        }
    elif not args.skip_risk_guard:
        log("\n>>> 阶段四: 执行 EOD 四 Guard 风控链 <<<")
        phase4_success, _ = run_step(
            "EOD 四 Guard 风控守卫",
            RUN_DAILY_EOD_SCRIPT,
            ["--date", report_date],
            timeout_minutes=10,
        )
        eod_summary["phases"]["phase4_risk_guard"] = {
            "success": phase4_success,
            "script": str(RUN_DAILY_EOD_SCRIPT),
        }
        if phase4_success:
            success_count += 1
        else:
            fail_count += 1
            # M10 修复: 阶段四失败为非关键 — 次日计划仍可使用 (缺少风控更新), 继续归档
            log("EOD 风控守卫执行失败 [非关键] — 次日计划仍可使用, 但缺少风控更新, 继续归档", "WARN")
    else:
        log("\n>>> 阶段四: 跳过 EOD 风控守卫 (--skip-risk-guard) <<<")
        eod_summary["phases"]["phase4_risk_guard"] = {"skipped": True}

    # ─────────────────────────────────────────────────────────
    # 阶段四点五: Phase 10 Shadow 数据收集 (观察期自动写入)
    # ─────────────────────────────────────────────────────────
    # 调用 daily_workflow.py --phase shadow_monitor
    # 计算当日影子账户净值并写入 reports/shadow/daily_returns.jsonl
    # 失败为非关键 — 不影响归档和次日交易, 但观察期会缺一天数据
    if not args.skip_shadow:
        log("\n>>> 阶段四点五: Phase 10 Shadow 数据收集 (观察期) <<<")
        phase_shadow_success, _ = run_step(
            "Phase 10 Shadow Monitor",
            DAILY_WORKFLOW_SCRIPT,
            ["--phase", "shadow_monitor", "--date", report_date],
            timeout_minutes=5,
        )
        eod_summary["phases"]["phase4_5_shadow_monitor"] = {
            "success": phase_shadow_success,
            "script": str(DAILY_WORKFLOW_SCRIPT),
        }
        if phase_shadow_success:
            success_count += 1
        else:
            fail_count += 1
            log("Phase 10 Shadow 数据收集失败 [非关键] — 观察期数据可能缺失, 不影响次日交易", "WARN")
    else:
        log("\n>>> 阶段四点五: 跳过 Shadow 数据收集 (--skip-shadow) <<<")
        eod_summary["phases"]["phase4_5_shadow_monitor"] = {"skipped": True}

    # ─────────────────────────────────────────────────────────
    # 阶段五: 归档所有报告
    # ─────────────────────────────────────────────────────────
    if not args.skip_archive:
        log(f"\n>>> 阶段五: 归档报告到 {today_dir} <<<")
        try:
            count = archive_reports(today_dir, report_date)
            log(f"[OK] 归档完成, 共 {count} 个文件")
            eod_summary["phases"]["phase5_archive"] = {
                "success": True,
                "archived_count": count,
                "archive_dir": str(today_dir),
            }
            success_count += 1
        except Exception as e:
            log(f"[FAIL] 归档失败: {e}", "ERROR")
            traceback.print_exc()
            eod_summary["phases"]["phase5_archive"] = {"success": False, "error": str(e)}
            fail_count += 1
    else:
        log("\n>>> 阶段五: 跳过归档 (--skip-archive) <<<")
        eod_summary["phases"]["phase5_archive"] = {"skipped": True}

    # ─────────────────────────────────────────────────────────
    # 生成 EOD 工作流摘要报告
    # ─────────────────────────────────────────────────────────
    eod_summary["completed_at"] = datetime.now().isoformat()
    eod_summary["success_count"] = success_count
    eod_summary["fail_count"] = fail_count
    eod_summary["overall_success"] = (fail_count == 0)

    summary_path = today_dir / f"eod_workflow_summary_{report_date}.json"
    try:
        today_dir.mkdir(parents=True, exist_ok=True)
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(eod_summary, f, ensure_ascii=False, indent=2)
        log(f"\n📋 EOD 工作流摘要已保存: {summary_path}")
    except Exception as e:
        log(f"  ⚠️ 摘要保存失败: {e}", "WARN")

    # ─────────────────────────────────────────────────────────
    # 总结
    # ─────────────────────────────────────────────────────────
    log("\n" + "=" * 60)
    log("║  每日收盘工作流完成                                    ║")
    log(f"║  成功: {success_count} | 失败: {fail_count}                          ║")
    log(f"║  报告日期: {report_date}                                ║")
    log(f"║  次交易日: {next_trade_date}                            ║")
    log(f"║  归档目录: {today_dir}                  ║")
    log("=" * 60)

    # 退出码: 全部成功=0, 部分失败=1
    sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
