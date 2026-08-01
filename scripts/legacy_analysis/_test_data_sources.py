"""测试备选数据源"""

# 测试 baostock
print("=" * 60)
print("Test 1: baostock")
print("=" * 60)
try:
    import baostock as bs
    lg = bs.login()
    print(f"  login result: {lg.error_code} {lg.error_msg}")
    if lg.error_code == '0':
        rs = bs.query_history_k_data_plus(
            "sh.600519",
            "date,code,open,high,low,close,volume",
            start_date='2024-01-01',
            end_date='2024-01-10',
            frequency="d",
            adjustflag="2"
        )
        print(f"  query result: {rs.error_code} {rs.error_msg}")
        if rs.error_code == '0':
            rows = []
            while (rs.error_code == '0') and rs.next():
                rows.append(rs.get_row_data())
            print(f"  rows: {len(rows)}")
            if rows:
                print(f"  first: {rows[0]}")
        bs.logout()
except Exception as e:
    print(f"  FAILED: {type(e).__name__}: {e}")

print()

# 测试 efinance
print("=" * 60)
print("Test 2: efinance")
print("=" * 60)
try:
    import efinance as ef
    # 股票数据
    df = ef.stock.get_quote_history('600519', beg='20240101', end='20240110')
    print(f"  stock rows: {len(df)}")
    if len(df) > 0:
        print(f"  columns: {list(df.columns)}")
        print(f"  first: {df.iloc[0].to_dict()}")
except Exception as e:
    print(f"  FAILED: {type(e).__name__}: {e}")

print()

# 测试 efinance ETF
print("=" * 60)
print("Test 3: efinance ETF (510300)")
print("=" * 60)
try:
    import efinance as ef
    df = ef.stock.get_quote_history('510300', beg='20240101', end='20240110')
    print(f"  ETF rows: {len(df)}")
    if len(df) > 0:
        print(f"  first: {df.iloc[0].to_dict()}")
except Exception as e:
    print(f"  FAILED: {type(e).__name__}: {e}")
