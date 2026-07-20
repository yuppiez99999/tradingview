# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"E:\各种PY程序\15_每日工作流")

from morning_market_fetcher import generate_markdown_report, fetch_market_data
from datetime import datetime

# 获取市场数据
market_data = fetch_market_data("2026-07-17")

# 生成报告
report = generate_markdown_report(market_data, [], "2026-07-17")

# 提取铜相关部分
lines = report.split('\n')
for i, line in enumerate(lines):
    if '沪铜' in line or 'CU.SHF' in line or '升贴水' in line:
        print(f"{i}: {line}")
