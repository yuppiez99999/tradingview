import sys

sys.path.insert(0, r"E:\各种PY程序\15_每日工作流")

import akshare as ak

print("=== 测试 energy_carbon_domestic ===")
df = ak.energy_carbon_domestic()
print(f"数据形状: {df.shape}")
print(f"列名: {df.columns.tolist()}")
print("\n最新5条数据:")
print(df.tail())

# 筛选湖北（全国碳市场主要试点）
print("\n=== 湖北碳市场最新数据 ===")
df_hb = df[df['地点'] == '湖北']
print(df_hb.tail())

# 筛选广东
print("\n=== 广东碳市场最新数据 ===")
df_gz = df[df['地点'] == '广东']
print(df_gz.tail())
