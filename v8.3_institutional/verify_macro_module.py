#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
十五五规划/康波评分接入验证脚本
"""

import sys
from pathlib import Path

BASE_DIR = Path(r"e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional")
sys.path.insert(0, str(BASE_DIR / "src" / "macro"))

from macro_policy_scoring import score_macro_policy, FIFTEEN_FIVE_DIRECTIONS, KONDRATIEV_STYLE_WEIGHTS  # noqa: E402

symbols = [
    "sz510300", "sz515180", "sh600089", "sz588000", "sh688041",
    "sz300308", "sz002371", "sh688981", "sh603019", "sz000425",
    "sh600276", "sh600900", "sh601088", "sz518880"
]

result = score_macro_policy(symbols)

print("十五五方向数量:", len(FIFTEEN_FIVE_DIRECTIONS))
print("康波风格权重:", len(KONDRATIEV_STYLE_WEIGHTS))
print("\n标的宏观评分:")
for symbol, item in result.items():
    print(f"{symbol}: 十五五={item.fifteen_five_score:.2f} | 康波={item.kondratiev_score:.2f} | 综合={item.combined_score:.2f} | {item.fifteen_five_note} / {item.kondratiev_note}")
