# -*- coding: utf-8 -*-
"""测试 baostock 不同季度的数据可用性"""
import baostock as bs

lg = bs.login()
print(f"login: {lg.error_code}")

# 测试 600519 各季度
code = "sh.600519"
for year in [2026, 2025, 2024, 2023]:
    for q in [1, 2, 3, 4]:
        rs = bs.query_profit_data(code=code, year=year, quarter=q)
        if rs.error_code != '0':
            print(f"  {year} Q{q}: ERROR {rs.error_msg}")
            continue
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if rows:
            d = dict(zip(rs.fields, rows[0]))
            roe = d.get('roeAvg', '')
            np = d.get('netProfit', '')
            print(f"  {year} Q{q}: ROE={roe} netProfit={np}")
        else:
            print(f"  {year} Q{q}: 0 rows")

bs.logout()
