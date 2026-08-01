# -*- coding: utf-8 -*-
"""检查单股 fundamentals 详情"""
import json
from pathlib import Path

fund_dir = Path(r"e:\各种PY程序\28-终极量化交易系统8.4\cache\fundamentals")

for sym in ["600519_SH", "000333_SZ", "600276_SH", "601989_SH"]:
    fp = fund_dir / f"{sym}_latest.json"
    if not fp.exists():
        print(f"{sym}: 文件不存在")
        continue
    with open(fp, "r", encoding="utf-8") as f:
        d = json.load(f)
    print(f"\n{sym}:")
    print(f"  report_year={d.get('report_year')} Q{d.get('report_quarter')}")
    print(f"  roe={d.get('roe')} | net_profit={d.get('net_profit')}")
    print(f"  profit_growth={d.get('profit_growth', 'MISSING')}")
    print(f"  revenue_growth={d.get('revenue_growth', 'MISSING')}")
