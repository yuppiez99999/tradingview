"""测试 baostock ETF 数据"""
import baostock as bs

lg = bs.login()
print(f"login: {lg.error_code}")

# 测试不同的 ETF 代码
test_codes = ["sh.510300", "sh.510050", "sh.588000", "sh.518880", "sz.159919", "sz.159915"]
for code in test_codes:
    print(f"\n=== Test {code} ===")
    rs = bs.query_history_k_data_plus(
        code,
        "date,code,open,high,low,close,volume,amount",
        start_date='2024-12-01',
        end_date='2024-12-10',
        frequency="d",
        adjustflag="2"
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
