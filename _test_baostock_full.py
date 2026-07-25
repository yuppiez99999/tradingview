"""测试 baostock 完整接口"""
import baostock as bs
import pandas as pd

lg = bs.login()
print(f"login: {lg.error_code} {lg.error_msg}")

# 测试 1: ETF 数据 (510300)
print("\n" + "=" * 60)
print("Test 1: 510300 ETF 历史 K 线")
print("=" * 60)
rs = bs.query_history_k_data_plus(
    "sh.510300",
    "date,code,open,high,low,close,volume,amount",
    start_date='2024-01-01',
    end_date='2024-01-10',
    frequency="d",
    adjustflag="2"  # 前复权
)
print(f"  query: {rs.error_code} {rs.error_msg}")
rows = []
while (rs.error_code == '0') and rs.next():
    rows.append(rs.get_row_data())
print(f"  rows: {len(rows)}")
if rows:
    print(f"  first: {rows[0]}")

# 测试 2: 财务数据 (季频盈利能力)
print("\n" + "=" * 60)
print("Test 2: 600519 季频盈利能力 (roe/roa/gross_profit_margin)")
print("=" * 60)
rs = bs.query_profit_data(code="sh.600519", year=2024, quarter=3)
print(f"  query: {rs.error_code} {rs.error_msg}")
rows = []
while (rs.error_code == '0') and rs.next():
    rows.append(rs.get_row_data())
print(f"  rows: {len(rows)}")
if rows:
    print(f"  fields: {rs.fields}")
    print(f"  first: {rows[0]}")

# 测试 3: 财务数据 (季频偿债能力)
print("\n" + "=" * 60)
print("Test 3: 600519 季频偿债能力 (debt_to_assets)")
print("=" * 60)
rs = bs.query_balance_data(code="sh.600519", year=2024, quarter=3)
print(f"  query: {rs.error_code} {rs.error_msg}")
rows = []
while (rs.error_code == '0') and rs.next():
    rows.append(rs.get_row_data())
print(f"  rows: {len(rows)}")
if rows:
    print(f"  fields: {rs.fields}")
    print(f"  first: {rows[0]}")

# 测试 4: 估值数据 (PE/PB)
print("\n" + "=" * 60)
print("Test 4: 600519 估值数据 (PE/PB)")
print("=" * 60)
rs = bs.query_history_k_data_plus(
    "sh.600519",
    "date,code,close,peTTM,pbMRQ,psTTM,pcfNcfTTM",
    start_date='2024-12-01',
    end_date='2024-12-10',
    frequency="d"
)
print(f"  query: {rs.error_code} {rs.error_msg}")
rows = []
while (rs.error_code == '0') and rs.next():
    rows.append(rs.get_row_data())
print(f"  rows: {len(rows)}")
if rows:
    print(f"  first: {rows[0]}")

bs.logout()
print("\nlogout: success")
