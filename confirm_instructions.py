# -*- coding: utf-8 -*-
"""批量确认指令 (测试用)"""
import json
from pathlib import Path

p = Path("trade_instructions/2026-07-10_instructions.json")
d = json.load(open(p, 'r', encoding='utf-8'))
count = 0
for i in d['instructions']:
    i['confirm'] = True
    count += 1
json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print(f"Updated {count} instructions to confirm=true")
