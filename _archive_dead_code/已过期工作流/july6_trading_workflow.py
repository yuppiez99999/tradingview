# -*- coding: utf-8 -*-
"""
7月6日交易计划 Workflow
=======================
用法:
  python july6_trading_workflow.py                        # 生成7月6日交易计划
  python july6_trading_workflow.py --check-only           # 仅检查计划状态
  python july6_trading_workflow.py --include-hedge        # 包含对冲方案
"""
import os
import sys
import json
from datetime import date

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BASE_DIR)

from build_plan_executor import BuildPlanExecutor
from comprehensive_quant_system_v7 import (
    ComprehensiveQuantSystemV7,
    EnhancedFuturesHedge,
    ProtectiveOptionsHedge,
    MarketRegime,
)


def run(target_date: date = date(2026, 7, 6), include_hedge: bool = False):
    executor = BuildPlanExecutor()
    phase_summary, phase_idx, status = executor.get_active_phase(target_date)
    print("=" * 60)
    print("7月6日交易计划 Workflow")
    print("=" * 60)
    print(f"日期: {target_date}")
    print(f"阶段: {phase_summary['name'] if phase_summary else 'N/A'} ({status})")
    if phase_summary:
        print(f"阶段资金: {phase_summary['capital_amount']:,.0f} 元 ({phase_summary['capital_ratio']*100:.0f}%)")

    sheet = executor.generate_daily_orders(target_date)
    total_amount = sum(o.est_amount for o in sheet.morning_orders + sheet.afternoon_orders)
    print(f"当日计划金额: {total_amount:,.0f} 元")
    print(f"上午订单: {len(sheet.morning_orders)} 笔")
    print(f"下午订单: {len(sheet.afternoon_orders)} 笔")
    print(f"暂停标的: {len(sheet.paused_orders)} 个")

    md_path, json_path = executor.save_trade_sheet(sheet)
    print(f"Markdown: {md_path}")
    print(f"JSON: {json_path}")

    # 关联研报附件到日期目录
    pdf_src = r"E:\各种PY程序\每日报告归档\交易计划\长江电力分红再投资5年回报测算与对比分析.pdf"
    if os.path.isfile(pdf_src):
        date_str = target_date.strftime("%Y-%m-%d")
        out_dir = os.path.join(os.path.dirname(md_path), date_str)
        os.makedirs(out_dir, exist_ok=True)
        pdf_dst = os.path.join(out_dir, os.path.basename(pdf_src))
        if not os.path.exists(pdf_dst):
            try:
                import shutil
                shutil.copy2(pdf_src, pdf_dst)
                print(f"研报附件: {pdf_dst}")
            except Exception as e:
                print(f"研报附件复制失败: {e}")

    if include_hedge:
        print("\n" + "=" * 60)
        print("v7.0 对冲方案")
        print("=" * 60)
        system = ComprehensiveQuantSystemV7(total_capital=5_000_000)
        print(f"系统版本: v7.0")
        print(f"总资金: 5,000,000 元")
        print(f"股票组合: 4,000,000 元 (80%)")
        print(f"对冲/低风险: 1,000,000 元 (20%)")
        print(f"建仓金额: 2,250,000 元 (45%)")
        print(f"现金缓冲: 1,750,000 元 (35%)")
        print(f"市场状态: {MarketRegime.SIDEWAYS.value} (建仓首日默认震荡)")
        print(f"对冲比率: 20% (尾部保护)")
        print(f"保护策略: Delta对冲 + 保护性看跌期权")

    if sheet.warnings:
        print(f"\n警告 ({len(sheet.warnings)}):")
        for w in sheet.warnings[:5]:
            print(f"  - {w}")

    print("\n" + "=" * 60)
    print("READY FOR EXECUTION ON MONDAY, JULY 6, 2026")
    print("=" * 60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="7月6日交易计划 Workflow")
    parser.add_argument("--date", default="2026-07-06", help="目标日期")
    parser.add_argument("--check-only", action="store_true", help="仅检查计划状态")
    parser.add_argument("--include-hedge", action="store_true", help="包含对冲方案")
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date)
    if args.check_only:
        executor = BuildPlanExecutor()
        phase_summary, phase_idx, status = executor.get_active_phase(target_date)
        print(f"日期: {target_date}")
        print(f"阶段: {phase_summary['name'] if phase_summary else 'N/A'} ({status})")
        sys.exit(0)

    run(target_date=target_date, include_hedge=args.include_hedge)
