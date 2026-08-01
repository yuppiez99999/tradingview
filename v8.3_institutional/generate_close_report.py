# -*- coding: utf-8 -*-
"""
生成 2026-07-21 收盘报告（含市场跟踪指标）
"""
import sys
import os
from pathlib import Path

BASE = Path(r"e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional")
sys.path.insert(0, str(BASE))
os.environ["PYTHONIOENCODING"] = "utf-8"

from weekly_trade_executor import WeeklyTradeExecutor  # noqa: E402

def main():
    print("=" * 80)
    print("生成 2026-07-21 收盘报告")
    print("=" * 80)
    
    executor = WeeklyTradeExecutor(
        trade_date="2026-07-21",
        session="all",
        dry_run=False
    )
    
    # 加载计划
    if not executor.load_daily_plan():
        print("[ERROR] 未找到当日计划，退出")
        return
    
    if not executor.load_weekly_plan():
        print("[WARN] 未找到周计划，继续生成日报")
    
    # 加载今天的执行结果
    executor.execution_results = {
        "stock": {"success": 0, "failed": 0, "total_amount": 0, "orders": []},
        "futures": {"success": 1, "failed": 0, "total_amount": 600000, "orders": []},
        "futures_orders": [
            {"symbol": "IF2609", "side": "SELL_OPEN", "qty": 2, "price": 3000.0, "note": "LLM建议: 增加期货对冲合约数量至2手IF"}
        ],
        "options": {"success": 10, "failed": 0, "total_premium": 0, "orders": []},
    }
    
    # 生成报告
    report = executor.generate_report()
    
    # 保存报告
    report_file = executor.save_report(report)
    
    print(f"\n报告已生成: {report_file}")
    print("\n" + "=" * 80)
    print("报告预览:")
    print("=" * 80)
    print(report[:2000])
    print("...")
    print("=" * 80)

if __name__ == "__main__":
    main()
