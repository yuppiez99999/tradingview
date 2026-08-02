"""测试 baostock 指数接口"""
import baostock as bs

lg = bs.login()
print(f"login: {lg.error_code}")

# 测试指数
test_codes = ["sh.000300", "sh.000016", "sh.000905", "sh.000001"]
for code in test_codes:
    print(f"\n=== Test {code} ===")
    rs = bs.query_history_k_data_plus(
        code,
        "date,code,open,high,low,close,volume,amount",
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
