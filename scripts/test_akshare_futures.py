import sys

sys.path.insert(0, r"E:\各种PY程序\15_每日工作流")

import akshare as ak

print("=== 测试 akshare 动力煤期货最新价格 ===")
df = ak.futures_zh_daily_sina(symbol="ZC0")
print(f"数据形状: {df.shape}")
print("最新数据:")
print(df.tail(1))

print("\n=== 测试 akshare 沪铜期货最新价格 ===")
df = ak.futures_zh_daily_sina(symbol="CU0")
print(f"数据形状: {df.shape}")
print("最新数据:")
print(df.tail(1))

# 测试其他 symbol
print("\n=== 测试 akshare 动力煤连续合约 ===")
try:
    df = ak.futures_zh_daily_sina(symbol="ZC2509")
    print(f"数据形状: {df.shape}")
    print("最新数据:")
    print(df.tail(1))
except Exception as e:
    print(f"失败: {e}")
