# -*- coding: utf-8 -*-
"""
每日自动执行交易计划 (模拟执行 + 人工确认)
==========================================

目标:
  - 2026-12-31 前完成 400 万股票ETF建仓
  - 每个交易日自动生成交易指令
  - 盘前生成指令 → 人工确认 → 盘后模拟执行 → 更新持仓状态

执行流程:
  1. 盘前 09:00 — generate_instructions()
     - 检查交易日/建仓期
     - 2026-07-13起: 每个交易日固定20万
     - 2026-07-10~07-12: 智能分批 (ETF信号日5万、无信号日1万、弱信号日2万)
     - 四重风控: 单日上限20万、价格保护带±3%、熔断停止(-3%/-5%)
     - 生成 trade_instructions/YYYY-MM-DD_instructions.json + .md
  2. 人工确认 — 修改 JSON 中的 confirm: true (默认 false)
  3. 盘后 15:30 — execute_instructions()
     - 读取已确认指令
     - 模拟执行 (SimulatedBroker)
     - 更新 positions.json 的 shares/avg_cost/est_price 和 build_progress.json
     - 生成执行报告
  4. 收盘后自动生成下一交易日计划 — generate_next_trading_day_plan()
     - 自动计算下一个交易日
     - 生成下一交易日的交易指令
     - 实现无缝衔接的自动化交易流程

使用方式:
  # 盘前生成指令
  python daily_trade_executor.py pre-market

  # 盘后执行已确认指令
  python daily_trade_executor.py post-market

  # 收盘后自动执行 + 生成下一交易日计划 (推荐)
  python daily_trade_executor.py post-market-auto

  # 查看建仓进度
  python daily_trade_executor.py progress

  # 指定日期
  python daily_trade_executor.py pre-market --date 2026-07-10
  python daily_trade_executor.py post-market-auto --date 2026-07-13
"""
import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, date
from typing import Dict, List, Optional, Any

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 项目根目录
PROJECT_ROOT = Path(__file__).parent
POSITIONS_FILE = PROJECT_ROOT / "config" / "positions.json"
TRADE_PLAN_FILE = PROJECT_ROOT / "v7.5_institutional" / "trade_plans" / "auto_trade_plan_500w_2026-2030.json"
INSTRUCTIONS_DIR = PROJECT_ROOT / "trade_instructions"
PROGRESS_FILE = PROJECT_ROOT / "trade_instructions" / "build_progress.json"

def normalize_code(code):
    """将任意格式股票代码规范为带交易所后缀(.SH/.SZ/.BJ)格式, 避免同一标的被记成裸码与带后缀两种键。"""
    if not code:
        return code
    c = str(code).strip().upper()
    if c.endswith((".SH", ".SZ", ".BJ")):
        return c
    if c.startswith(("6", "5", "9")):
        return c + ".SH"
    if c.startswith(("0", "3", "1")):
        return c + ".SZ"
    if c.startswith(("8", "4")):
        return c + ".BJ"
    return c

