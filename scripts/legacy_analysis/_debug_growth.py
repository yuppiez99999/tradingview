# -*- coding: utf-8 -*-
"""调试 600519 profit_growth=0 的原因"""
import baostock as bs

lg = bs.login()
print(f"login: {lg.error_code}")

code = "sh.600519"
year = 2026
quarter = 1
prev_year = year - 1  # 2025

print(f"\n=== 当前季度 {year} Q{quarter} ===")
rs = bs.query_profit_data(code=code, year=year, quarter=quarter)
print(f"  error_code={rs.error_code} error_msg={rs.error_msg}")
rows = []
while rs.next():
    rows.append(rs.get_row_data())
if rows:
    d = dict(zip(rs.fields, rows[0]))
    cur_np = float(d.get("netProfit", 0))
    print(f"  ROE={d.get('roeAvg')} netProfit={cur_np}")

print(f"\n=== 去年同季 {prev_year} Q{quarter} ===")
rs_prev = bs.query_profit_data(code=code, year=prev_year, quarter=quarter)
print(f"  error_code={rs_prev.error_code} error_msg={rs_prev.error_msg}")
print(f"  type(year)={type(year).__name__} type(quarter)={type(quarter).__name__} type(prev_year)={type(prev_year).__name__}")
rows_prev = []
while rs_prev.next():
    rows_prev.append(rs_prev.get_row_data())
if rows_prev:
    d_prev = dict(zip(rs_prev.fields, rows_prev[0]))
    prev_np = float(d_prev.get("netProfit", 0))
    print(f"  ROE={d_prev.get('roeAvg')} netProfit={prev_np}")

    # 计算 profit_growth
    if prev_np > 0 and cur_np != 0:
        growth = (cur_np - prev_np) / abs(prev_np)
        print(f"\n  profit_growth = ({cur_np:.0f} - {prev_np:.0f}) / |{prev_np:.0f}| = {growth:.4f}")
    else:
        print(f"\n  条件不满足: prev_np={prev_np}, cur_np={cur_np}")
else:
    print("  prev year 返回 0 行")

bs.logout()
