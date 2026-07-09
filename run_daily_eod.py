# -*- coding: utf-8 -*-
"""
盘后报告自动运行入口
====================

每个交易日 16:00 由 Windows Task Scheduler 自动触发,
非交易日自动跳过, 收盘后生成持仓盈亏报告 (含对冲明细 + 持仓明细 + 收益 + 第二天交易计划)。

工作流程:
    1. 检查今日是否为 A 股交易日 (akshare 交易日历)
    2. 如非交易日, 写入跳过日志并退出
    3. 切换到项目根目录, 调用 generate_daily_report.main()
    4. 输出报告路径 (Markdown + JSON)

用法:
    python run_daily_eod.py                 # 今日
    python run_daily_eod.py 2026-07-09       # 指定日期 (用于手动补生成)

注册定时任务 (管理员 PowerShell):
    .\\register_eod_task.ps1
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

# ============================================================
# 路径初始化
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent
os.chdir(SCRIPT_DIR)
sys.path.insert(0, str(SCRIPT_DIR))

# ============================================================
# 日志
# ============================================================
LOG_DIR = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"run_daily_eod_{datetime.now():%Y%m%d}.log"


def log(msg: str) -> None:
    """输出带时间戳的日志"""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + "\n")
    except Exception:
        pass


def main() -> int:
    """主入口: 判断交易日 → 调用 generate_daily_report

    Returns:
        0 = 成功 / 跳过, 非 0 = 错误
    """
    # 支持命令行参数: 指定报告日期
    if len(sys.argv) > 1:
        report_date = sys.argv[1]
    else:
        report_date = datetime.now().strftime('%Y-%m-%d')

    log("=" * 70)
    log(f"盘后报告自动运行入口启动 (报告日期: {report_date})")
    log("=" * 70)

    # 1. 检查交易日
    try:
        from utils.trade_calendar import is_trading_day
        if not is_trading_day(report_date):
            log(f"⏭️  {report_date} 非交易日, 跳过报告生成")
            return 0
        log(f"✅ {report_date} 是交易日, 继续生成报告")
    except Exception as e:
        log(f"⚠️ 交易日历检查失败 ({e}), 继续生成报告 (降级模式)")
        # 不阻止报告生成, 仅警告

    # 2. 调用 generate_daily_report.main()
    try:
        from generate_daily_report import main as gen_report_main
        # 通过 sys.argv 传递报告日期给 generate_daily_report
        sys.argv = ['generate_daily_report.py', report_date]
        report = gen_report_main()
        if report is None:
            log("❌ 报告生成失败 (返回 None)")
            return 1

        # 输出报告路径
        md_path = SCRIPT_DIR / "v7.5_institutional" / "reports" / f"daily_pnl_report_{report_date}.md"
        json_path = SCRIPT_DIR / "v7.5_institutional" / "reports" / f"daily_pnl_report_{report_date}.json"

        log(f"✅ 报告生成成功:")
        log(f"   Markdown: {md_path}")
        log(f"   JSON:     {json_path}")

        # 第二天交易计划摘要
        next_day_plan = report.get('next_day_plan', {})
        if next_day_plan and not next_day_plan.get('error'):
            nd = next_day_plan.get('next_trading_day', '')
            wd = next_day_plan.get('weekday', '')
            phase = next_day_plan.get('phase', {}).get('name_cn', '')
            day_idx = next_day_plan.get('phase', {}).get('day_index', 0)
            daily_capital = next_day_plan.get('stock_etf_account', {}).get('daily_capital', 0)
            log(f"   下一交易日: {nd} ({wd})")
            log(f"   阶段: {phase} (第 {day_idx} 天)")
            log(f"   当日预算: {daily_capital:,.2f} 元")
        return 0

    except Exception as e:
        import traceback
        log(f"❌ 报告生成异常: {e}")
        log(traceback.format_exc())
        return 2


if __name__ == '__main__':
    exit_code = main()
    sys.exit(exit_code)
