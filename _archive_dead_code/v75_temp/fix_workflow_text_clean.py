#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补充修正 daily_workflow.py 中仍残留的历史硬编码文案，
使其完全对齐 2026 交易计划/2026 plan alignment sweep.
"""

import os
from pathlib import Path

WF_PATH = Path(r"e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional\daily_workflow.py")
text = WF_PATH.read_text(encoding="utf-8")

replaces = {
    "28 标的": "计划标的",
    "28": "计划",
    "400 万 (80%)": "300万 (60%)",
    "100 万 (20%)": "200万 (40%)",
    "22.5% / P2 20% / P3 15% / P4 10% (累计 67.5%)": "35% / P2 30% / P3 20% / P4 15%",
    "棉花期货 (200万期权对冲)": "对冲配置",
    "2026 年交易计划 28 标的执行汇总": "2026 年交易计划执行汇总",
}

for old, new in replaces.items():
    if old in text:
        text = text.replace(old, new)

WF_PATH.write_text(text, encoding="utf-8")
print("已清理残留历史硬编码文案")
