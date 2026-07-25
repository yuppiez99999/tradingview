# -*- coding: utf-8 -*-
"""诊断 baostock 财务接口返回数据"""
import baostock as bs

lg = bs.login()
print(f"login: {lg.error_code} {lg.error_msg}")

# 测试 600519 贵州茅台
code = "sh.600519"

# 尝试多个季度找可用数据
print("\n=== query_profit_data 多季度测试 ===")
for year in [2025, 2024, 2023]:
    for q in [1, 2, 3, 4]:
        rs = bs.query_profit_data(code=code, year=year, quarter=q)
        if rs.error_code != '0':
            continue
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if rows:
            d = dict(zip(rs.fields, rows[0]))
            print(f"  {year} Q{q}: fields={rs.fields}")
            print(f"    roeAvg={d.get('roeAvg')} | npMargin={d.get('npMargin')} | gpMargin={d.get('gpMargin')}")
            print(f"    netProfit={d.get('netProfit')} | epsTTM={d.get('epsTTM')}")
            break
    else:
        continue
    break

print("\n=== query_balance_data 多季度测试 ===")
for year in [2025, 2024, 2023]:
    for q in [1, 2, 3, 4]:
        rs = bs.query_balance_data(code=code, year=year, quarter=q)
        if rs.error_code != '0':
            continue
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if rows:
            d = dict(zip(rs.fields, rows[0]))
            print(f"  {year} Q{q}: fields={rs.fields}")
            print(f"    liabilityToAsset={d.get('liabilityToAsset')} | currentRatio={d.get('currentRatio')}")
            break
    else:
        continue
    break

# 顺便测试其他股票确认接口稳定
print("\n=== 其他股票测试 000333 ===")
for year in [2024, 2023]:
    for q in [3, 4, 2, 1]:
        rs = bs.query_profit_data(code="sz.000333", year=year, quarter=q)
        if rs.error_code != '0':
            continue
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if rows:
            d = dict(zip(rs.fields, rows[0]))
            print(f"  000333 {year} Q{q}: roeAvg={d.get('roeAvg')} gpMargin={d.get('gpMargin')}")
            break
    else:
        continue
    break

bs.logout()
