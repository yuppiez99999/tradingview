"""测试 akshare API 接口"""
import akshare as ak

print(f"akshare version: {ak.__version__}")
print()

# 测试 1: 股票历史行情
print("=" * 60)
print("Test 1: 股票历史行情 (600519 贵州茅台)")
print("=" * 60)
try:
    df = ak.stock_zh_a_hist(
        symbol="600519",
        period="daily",
        start_date="20240101",
        end_date="20240110",
        adjust="qfq",
    )
    print(f"  rows: {len(df)}")
    print(f"  columns: {list(df.columns)}")
    if len(df) > 0:
        print(f"  first row:\n{df.iloc[0]}")
except Exception as e:
    print(f"  FAILED: {e}")

print()

# 测试 2: ETF 历史行情
print("=" * 60)
print("Test 2: ETF 历史行情 (510300 沪深300ETF)")
print("=" * 60)
try:
    df = ak.fund_etf_hist_em(
        symbol="510300",
        period="daily",
        start_date="20240101",
        end_date="20240110",
        adjust="qfq",
    )
    print(f"  rows: {len(df)}")
    print(f"  columns: {list(df.columns)}")
    if len(df) > 0:
        print(f"  first row:\n{df.iloc[0]}")
except Exception as e:
    print(f"  FAILED: {e}")

print()

# 测试 3: 股票财务指标
print("=" * 60)
print("Test 3: 股票财务指标 (600519)")
print("=" * 60)
try:
    # 个股指标
    df = ak.stock_financial_abstract(symbol="600519")
    print(f"  rows: {len(df)}")
    print(f"  columns: {list(df.columns)[:8]}")
    if len(df) > 0:
        print(f"  first row:\n{df.iloc[0]}")
except Exception as e:
    print(f"  FAILED: {e}")
