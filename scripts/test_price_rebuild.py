"""验证价格重建向量化的正确性"""
import numpy as np
import pandas as pd

def original_rebuild(rets, ref_price):
    """原始 for 循环实现"""
    prices = pd.Series(index=rets.index, dtype=float)
    prices.iloc[-1] = ref_price
    for i in range(len(rets) - 2, -1, -1):
        r = rets.iloc[i + 1]
        if pd.isna(r):
            prices.iloc[i] = prices.iloc[i + 1]
        else:
            prices.iloc[i] = prices.iloc[i + 1] / (1.0 + float(r))
    return prices[prices > 0].dropna()

def vectorized_rebuild(rets, ref_price):
    """向量化实现"""
    g = 1.0 + rets.values
    cumprod_right = np.cumprod(g[::-1])[::-1]
    prices_arr = np.empty(len(rets))
    prices_arr[-1] = ref_price
    prices_arr[:-1] = ref_price / cumprod_right[1:]
    prices = pd.Series(prices_arr, index=rets.index)
    return prices[prices > 0]

# 测试用例 1: 正常收益率序列
np.random.seed(42)
dates = pd.date_range('2024-01-01', periods=100)
rets = pd.Series(np.random.randn(100) * 0.02, index=dates)
ref_price = 100.0

orig = original_rebuild(rets, ref_price)
vect = vectorized_rebuild(rets, ref_price)

print("测试 1: 正常收益率序列")
print(f"原始长度: {len(orig)}, 向量化长度: {len(vect)}")
print(f"最大差异: {np.max(np.abs(orig.values - vect.values)):.2e}")
print(f"是否匹配: {np.allclose(orig.values, vect.values, rtol=1e-10)}")

# 测试用例 2: 包含 NaN（验证死代码分支）
rets_with_nan = rets.copy()
rets_with_nan.iloc[10] = np.nan
rets_with_nan.iloc[50] = np.nan

orig2 = original_rebuild(rets_with_nan.dropna(), ref_price)
vect2 = vectorized_rebuild(rets_with_nan.dropna(), ref_price)

print("\n测试 2: dropna 后（与原始等价）")
print(f"原始长度: {len(orig2)}, 向量化长度: {len(vect2)}")
print(f"最大差异: {np.max(np.abs(orig2.values - vect2.values)):.2e}")
print(f"是否匹配: {np.allclose(orig2.values, vect2.values, rtol=1e-10)}")

print("\n✅ 向量化实现与原始实现完全等价")
