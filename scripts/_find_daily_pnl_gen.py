# -*- coding: utf-8 -*-
"""查找真正生成 daily_pnl_report_*.json 的脚本"""
from pathlib import Path

hits = []
for p in Path('.').rglob('*.py'):
    if not p.is_file():
        continue
    try:
        s = p.read_text(encoding='utf-8', errors='ignore')
        for i, l in enumerate(s.splitlines(), 1):
            ls = l.strip()
            if 'daily_pnl_report_' in ls and '.json' in ls:
                # 只关注实际写入或保存的代码
                if any(k in ls for k in ['open(', 'json.dump', 'json.dump', 'to_json', 'save', 'write', 'with open', 'Path(', 'REPORTS_DIR', 'report_path']):
                    hits.append((str(p), i, ls[:120]))
    except Exception:
        pass

seen = set()
for f, ln, line in hits:
    key = (f, ln)
    if key not in seen:
        seen.add(key)
        print(f"{f}:{ln}: {line}")
