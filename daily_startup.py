# -*- coding: utf-8 -*-
"""
v7.5 每日自动启动脚本

功能:
    - 检查是否为交易日
    - 在开盘前10分钟自动启动实时监控调度器
    - 在收盘后自动停止

用法:
    python daily_startup.py                    # 检查并启动
    python daily_startup.py --install          # 安装 Windows 定时任务
    python daily_startup.py --uninstall        # 卸载定时任务
"""
from __future__ import annotations

import os
import sys
import time
import json
import subprocess
import argparse
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PYTHON = r"C:\Program Files\Python38\python.exe"


def is_trading_day(date: datetime = None) -> bool:
    """判断是否为交易日"""
    if date is None:
        date = datetime.now()
    
    weekday = date.weekday()
    if weekday >= 5:
        return False
    
    holidays = [
        "2026-01-01", "2026-01-29", "2026-01-30", "2026-01-31",
        "2026-02-01", "2026-02-02", "2026-04-04", "2026-04-05",
        "2026-05-01", "2026-06-22", "2026-06-23", "2026-06-24",
        "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
        "2026-10-05", "2026-10-06", "2026-10-07",
    ]
    return date.strftime("%Y-%m-%d") not in holidays


def should_start() -> bool:
    """判断是否应该启动监控"""
    now = datetime.now()
    
    if not is_trading_day(now):
        return False
    
    current_time = now.time()
    start_time = dt_time(8, 50)
    end_time = dt_time(15, 30)
    
    return start_time <= current_time <= end_time


def start_scheduler():
    """启动实时监控调度器"""
    cmd = [PYTHON, str(BASE_DIR / "live_scheduler.py")]
    subprocess.Popen(cmd, cwd=str(BASE_DIR))
    print(f"[{datetime.now()}] 实时监控调度器已启动")


def stop_scheduler():
    """停止实时监控调度器"""
    cmd = [PYTHON, str(BASE_DIR / "live_scheduler.py"), "--stop"]
    subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True)
    print(f"[{datetime.now()}] 实时监控调度器已停止")


def install_task():
    """安装 Windows 定时任务"""
    script_path = str(BASE_DIR / "daily_startup.py")
    trigger = "daily"
    start_time = "08:50"
    
    cmd = [
        "schtasks", "/create",
        "/tn", "v7.5_Live_Scheduler",
        "/tr", f'"{PYTHON}" "{script_path}"',
        "/sc", trigger,
        "/st", start_time,
        "/f",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print("定时任务安装成功")
        print("任务名称: v7.5_Live_Scheduler")
        print(f"触发时间: 每日 {start_time}")
    else:
        print(f"定时任务安装失败: {result.stderr}")


def uninstall_task():
    """卸载 Windows 定时任务"""
    cmd = ["schtasks", "/delete", "/tn", "v7.5_Live_Scheduler", "/f"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print("定时任务卸载成功")
    else:
        print(f"定时任务卸载失败: {result.stderr}")


def main():
    parser = argparse.ArgumentParser(description="v7.5 每日自动启动脚本")
    parser.add_argument("--install", action="store_true", help="安装定时任务")
    parser.add_argument("--uninstall", action="store_true", help="卸载定时任务")
    parser.add_argument("--start", action="store_true", help="立即启动")
    parser.add_argument("--stop", action="store_true", help="立即停止")
    args = parser.parse_args()

    if args.install:
        install_task()
        return

    if args.uninstall:
        uninstall_task()
        return

    if args.start:
        start_scheduler()
        return

    if args.stop:
        stop_scheduler()
        return

    if should_start():
        start_scheduler()
    else:
        now = datetime.now()
        print(f"[{now}] 非交易时间或已过收盘时间，跳过启动")


if __name__ == "__main__":
    sys.exit(main())
