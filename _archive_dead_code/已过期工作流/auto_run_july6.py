# -*- coding: utf-8 -*-
"""
7月6日 07:00 自动交易计划
======================
用途:
  python auto_run_july6.py --check           # 检查是否到达执行时间
  python auto_run_july6.py --run             # 立即执行
  python auto_run_july6.py --setup-task      # 创建 Windows 定时任务
"""
import os
import sys
import subprocess
import argparse
from datetime import datetime, date

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BASE_DIR)

TARGET_DATE = date(2026, 7, 6)
TARGET_HOUR = 7
TARGET_MINUTE = 0
PYTHON = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"


def check_hedge_status():
    """检查对冲是否开启"""
    try:
        from comprehensive_quant_system_v7 import ComprehensiveQuantSystemV7
        system = ComprehensiveQuantSystemV7()
        config = system.config
        hedge_flags = {
            'enable_futures': config.get('enable_futures', False),
            'enable_options': config.get('enable_options', False),
            'enable_vol_arbitrage': config.get('enable_vol_arbitrage', False),
            'enable_covered_write': config.get('enable_covered_write', False),
            'enable_absolute_return': config.get('enable_absolute_return', False),
        }
        return hedge_flags, all(hedge_flags.values())
    except Exception as e:
        return {}, False


def run_july6_plan(include_hedge: bool = True):
    """运行7月6日交易计划"""
    workflow_path = os.path.join(_BASE_DIR, "july6_trading_workflow.py")
    cmd = [PYTHON, workflow_path, "--date", "2026-07-06"]
    if include_hedge:
        cmd.append("--include-hedge")
    print(f"执行命令: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=_BASE_DIR, capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode == 0


def run_hedge_simulation():
    """运行对冲模拟检查"""
    script = os.path.join(_BASE_DIR, "comprehensive_quant_system_v7.py")
    cmd = [PYTHON, script, "--live"]
    print(f"执行对冲检查: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=_BASE_DIR, capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode == 0


def setup_windows_task():
    """创建 Windows 定时任务: 7月6日 07:00 运行"""
    task_name = "QuantSystem_July6_AutoRun"
    script_path = os.path.join(_BASE_DIR, "auto_run_july6.py")
    cmd_line = f'"{PYTHON}" "{script_path}" --check'
    
    # 删除旧任务（如果存在）
    subprocess.run(
        ["schtasks", "/Delete", "/TN", task_name, "/F"],
        capture_output=True, text=True
    )
    
    # 创建新任务: 2026-07-06 07:00 运行
    # /SC ONCE 表示一次性任务
    # /ST 07:00 表示启动时间
    # /SD 2026/07/06 表示开始日期
    create_cmd = [
        "schtasks", "/Create",
        "/TN", task_name,
        "/TR", cmd_line,
        "/SC", "ONCE",
        "/ST", "07:00",
        "/SD", "2026/07/06",
        "/F"
    ]
    result = subprocess.run(create_cmd, capture_output=True, text=True, encoding='gbk', errors='replace')
    print("创建定时任务:")
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="7月6日 07:00 自动交易计划")
    parser.add_argument("--check", action="store_true", help="检查是否到达执行时间并执行")
    parser.add_argument("--run", action="store_true", help="立即执行交易计划")
    parser.add_argument("--setup-task", action="store_true", help="创建 Windows 定时任务")
    args = parser.parse_args()

    if args.setup_task:
        print("=" * 60)
        print("创建 Windows 定时任务")
        print("=" * 60)
        if setup_windows_task():
            print(f"✅ 定时任务已创建: {TARGET_DATE} {TARGET_HOUR:02d}:{TARGET_MINUTE:02d}")
        else:
            print("❌ 定时任务创建失败")
        return

    if args.run:
        print("=" * 60)
        print("立即执行 7月6日 交易计划")
        print("=" * 60)
        # 1. 检查对冲状态
        hedge_flags, hedge_on = check_hedge_status()
        print("\n对冲状态检查:")
        for k, v in hedge_flags.items():
            print(f"  {k}: {'✅ 开启' if v else '❌ 关闭'}")
        print(f"\n对冲总状态: {'✅ 已开启' if hedge_on else '❌ 未开启'}")
        
        # 2. 运行交易计划
        print("\n" + "=" * 60)
        print("运行交易计划")
        print("=" * 60)
        success = run_july6_plan(include_hedge=hedge_on)
        if success:
            print("\n✅ 交易计划执行成功")
        else:
            print("\n❌ 交易计划执行失败")
        return

    # 默认: 检查是否到达执行时间
    now = datetime.now()
    is_target = (now.date() == TARGET_DATE and 
                 now.hour == TARGET_HOUR and 
                 now.minute >= TARGET_MINUTE)
    
    print("=" * 60)
    print("7月6日 自动交易计划 - 时间检查")
    print("=" * 60)
    print(f"当前时间: {now}")
    print(f"目标时间: {TARGET_DATE} {TARGET_HOUR:02d}:{TARGET_MINUTE:02d}")
    print(f"是否到达执行时间: {'是' if is_target else '否'}")
    
    if is_target:
        print("\n>>> 到达执行时间，开始执行...")
        # 检查对冲状态
        hedge_flags, hedge_on = check_hedge_status()
        print("\n对冲状态检查:")
        for k, v in hedge_flags.items():
            print(f"  {k}: {'✅ 开启' if v else '❌ 关闭'}")
        print(f"\n对冲总状态: {'✅ 已开启' if hedge_on else '❌ 未开启'}")
        
        # 运行交易计划
        print("\n" + "=" * 60)
        print("运行交易计划")
        print("=" * 60)
        success = run_july6_plan(include_hedge=hedge_on)
        if success:
            print("\n✅ 自动执行完成")
        else:
            print("\n❌ 自动执行失败")
    else:
        print("\n未到达执行时间，等待定时任务触发")


if __name__ == "__main__":
    main()
