# -*- coding: utf-8 -*-
"""
自动执行期货(2手)和期权订单
"""
import os
import sys
from pathlib import Path

BASE = Path(r"e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional")
sys.path.insert(0, str(BASE))
os.environ["PYTHONIOENCODING"] = "utf-8"

from weekly_trade_executor import WeeklyTradeExecutor  # noqa: E402


def main():
    print("=" * 80)
    print("自动执行期货(2手)和期权订单")
    print("=" * 80)

    executor = WeeklyTradeExecutor(
        trade_date="2026-07-21",
        session="morning",
        dry_run=False
    )

    if not executor.load_daily_plan():
        print("[ERROR] 未找到当日计划，退出")
        return

    print(f"\n已加载计划: {executor.daily_plan.get('trade_date')}")

    # 提取期货订单 (现在是2手)
    futures_orders = executor._extract_futures_hedge_orders()
    print(f"\n期货订单: {len(futures_orders)} 笔")
    for order in futures_orders:
        print(f"  {order.get('symbol')} {order.get('side')} {order.get('qty')}手")

    # 提取期权订单
    session_orders = executor._get_session_orders()
    options_orders = session_orders.get("options", [])
    print(f"\n期权订单: {len(options_orders)} 笔")
    for order in options_orders:
        print(f"  {order.get('code')} {order.get('direction')} {order.get('contracts')}张")

    # 执行期货订单
    if futures_orders:
        print("\n执行期货订单...")
        futures_results = executor.execute_futures_orders(futures_orders)
        print(f"期货执行结果: {futures_results.get('success')}成功/{futures_results.get('failed')}失败")
    else:
        print("\n无期货订单需要执行")
        futures_results = {"success": 0, "failed": 0}

    # 执行期权订单
    if options_orders:
        print("\n执行期权订单...")
        options_results = executor.execute_options_orders(options_orders)
        print(f"期权执行结果: {options_results.get('success')}成功/{options_results.get('failed')}失败")
        print(f"权利金总额: ¥{options_results.get('total_premium', 0):,.2f}")
    else:
        print("\n无期权订单需要执行")
        options_results = {"success": 0, "failed": 0}

    # 生成报告
    print("\n生成执行报告...")
    executor.execution_results["futures"] = futures_results
    executor.execution_results["options"] = options_results
    executor.execution_results["futures_orders"] = futures_orders

    report = executor.generate_report()
    executor.save_report(report)
    print("报告已保存")

    print("\n" + "=" * 80)
    print("执行完成!")
    print("=" * 80)

if __name__ == "__main__":
    main()
