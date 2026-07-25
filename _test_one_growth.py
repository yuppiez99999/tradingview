# -*- coding: utf-8 -*-
"""强制重下 600519 测试 profit_growth"""
import sys
import logging
sys.path.insert(0, r"e:\各种PY程序\28-终极量化交易系统8.4")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s | %(message)s")

import baostock as bs
bs.login()

from cache.data_downloader import download_fundamentals
r = download_fundamentals("600519_SH", skip_if_exists=False, force_refresh=True)
if r:
    print(f"\n=== 结果 ===")
    print(f"profit_growth = {r.get('profit_growth')}")
    print(f"revenue_growth = {r.get('revenue_growth')}")
    print(f"report_year = {r.get('report_year')} Q{r.get('report_quarter')}")
    print(f"net_profit = {r.get('net_profit')}")
else:
    print("download_fundamentals 返回 None")

bs.logout()
