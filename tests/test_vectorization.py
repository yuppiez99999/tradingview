"""验证所有向量化优化的正确性"""

import numpy as np
import pandas as pd

print("=" * 60)
print("向量化优化验证测试")
print("=" * 60)

# ============================================================================
# 测试 1: qlib_data_bridge.py - DataFrame 转换
# ============================================================================
print("\n[测试 1] qlib_data_bridge.py - DataFrame 向量化转换")
print("-" * 60)


def original_dataframe_to_qlib(df):
    """原始 iterrows 实现"""
    records = []
    for ts, row in df.iterrows():
        records.append(
            {
                "date": ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts),
                "open": float(row.get("open", 0) or 0),
                "high": float(row.get("high", 0) or 0),
                "low": float(row.get("low", 0) or 0),
                "close": float(row.get("close", 0) or 0),
                "volume": float(row.get("volume", 0) or 0),
                "amount": float(row.get("amount", row.get("volume", 0) or 0)),
            }
        )
    return records


def vectorized_dataframe_to_qlib(df):
    """向量化实现"""
    idx = df.index
    dates = [
        ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts) for ts in idx
    ]

    opens = (
        pd.to_numeric(df.get("open", pd.Series(0, index=idx)), errors="coerce")
        .fillna(0)
        .to_numpy(dtype=float)
    )
    highs = (
        pd.to_numeric(df.get("high", pd.Series(0, index=idx)), errors="coerce")
        .fillna(0)
        .to_numpy(dtype=float)
    )
    lows = (
        pd.to_numeric(df.get("low", pd.Series(0, index=idx)), errors="coerce")
        .fillna(0)
        .to_numpy(dtype=float)
    )
    closes = (
        pd.to_numeric(df.get("close", pd.Series(0, index=idx)), errors="coerce")
        .fillna(0)
        .to_numpy(dtype=float)
    )
    volumes = (
        pd.to_numeric(df.get("volume", pd.Series(0, index=idx)), errors="coerce")
        .fillna(0)
        .to_numpy(dtype=float)
    )

    if "amount" in df.columns:
        amounts = (
            pd.to_numeric(df["amount"], errors="coerce").fillna(0).to_numpy(dtype=float)
        )
    else:
        amounts = volumes.copy()

    records = [
        {
            "date": dates[i],
            "open": float(opens[i]),
            "high": float(highs[i]),
            "low": float(lows[i]),
            "close": float(closes[i]),
            "volume": float(volumes[i]),
            "amount": float(amounts[i]),
        }
        for i in range(len(df))
    ]
    return records


# 创建测试数据
np.random.seed(42)
dates = pd.date_range("2024-01-01", periods=100)
test_df = pd.DataFrame(
    {
        "open": np.random.randn(100) * 10 + 100,
        "high": np.random.randn(100) * 10 + 105,
        "low": np.random.randn(100) * 10 + 95,
        "close": np.random.randn(100) * 10 + 100,
        "volume": np.random.randint(1000, 10000, 100),
    },
    index=dates,
)

orig_records = original_dataframe_to_qlib(test_df)
vect_records = vectorized_dataframe_to_qlib(test_df)

# 验证结果
assert len(orig_records) == len(vect_records), "记录数量不匹配"
for i in range(len(orig_records)):
    for key in ["date", "open", "high", "low", "close", "volume", "amount"]:
        assert (
            orig_records[i][key] == vect_records[i][key]
        ), f"字段 {key} 在第 {i} 行不匹配"

print(f"✓ 记录数量: {len(orig_records)}")
print("✓ 所有字段完全匹配")
print("✓ 测试通过")

# ============================================================================
# 测试 2: rebuild_etf_prices_from_returns.py - 价格重建
# ============================================================================
print("\n[测试 2] rebuild_etf_prices_from_returns.py - 反向递推价格重建")
print("-" * 60)


def original_price_rebuild(rets, ref_price):
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


def vectorized_price_rebuild(rets, ref_price):
    """向量化实现"""
    g = 1.0 + rets.values
    cumprod_right = np.cumprod(g[::-1])[::-1]
    prices_arr = np.empty(len(rets))
    prices_arr[-1] = ref_price
    prices_arr[:-1] = ref_price / cumprod_right[1:]
    prices = pd.Series(prices_arr, index=rets.index)
    return prices[prices > 0]


# 测试用例 1: 正常收益率
rets = pd.Series(np.random.randn(100) * 0.02, index=dates)
ref_price = 100.0

orig_prices = original_price_rebuild(rets, ref_price)
vect_prices = vectorized_price_rebuild(rets, ref_price)

assert len(orig_prices) == len(vect_prices), "价格序列长度不匹配"
max_diff = np.max(np.abs(orig_prices.values - vect_prices.values))
assert max_diff < 1e-10, f"价格差异过大: {max_diff}"

print(f"✓ 测试用例 1 (正常收益率): 长度={len(orig_prices)}, 最大差异={max_diff:.2e}")

# 测试用例 2: 极端收益率
rets_extreme = pd.Series([0.1, -0.05, 0.02, -0.01, 0.03] * 20, index=dates[:100])
orig_extreme = original_price_rebuild(rets_extreme, ref_price)
vect_extreme = vectorized_price_rebuild(rets_extreme, ref_price)

max_diff_extreme = np.max(np.abs(orig_extreme.values - vect_extreme.values))
assert max_diff_extreme < 1e-10, f"极端收益率测试失败: {max_diff_extreme}"

print(
    f"✓ 测试用例 2 (极端收益率): 长度={len(orig_extreme)}, 最大差异={max_diff_extreme:.2e}"
)
print("✓ 测试通过")

# ============================================================================
# 测试 3: risk_budget_optimizer.py - 因子检查
# ============================================================================
print("\n[测试 3] risk_budget_optimizer.py - 因子暴露检查")
print("-" * 60)


def original_factor_check(active_factor, max_factor_exposure):
    """原始 for 循环实现"""
    factor_violations = []
    for k in range(len(active_factor)):
        if abs(active_factor[k]) > max_factor_exposure:
            factor_violations.append(f"factor_{k}={active_factor[k]:.3f}")
    return factor_violations


def vectorized_factor_check(active_factor, max_factor_exposure):
    """向量化实现"""
    violated_mask = np.abs(active_factor) > max_factor_exposure
    violated_indices = np.where(violated_mask)[0]
    factor_violations = [f"factor_{k}={active_factor[k]:.3f}" for k in violated_indices]
    return factor_violations


# 测试数据
active_factor = np.array([0.1, 0.5, -0.3, 0.8, -0.2, 0.15])
max_exposure = 0.4

orig_violations = original_factor_check(active_factor, max_exposure)
vect_violations = vectorized_factor_check(active_factor, max_exposure)

assert (
    orig_violations == vect_violations
), f"因子违反列表不匹配: {orig_violations} vs {vect_violations}"

print(f"✓ 因子数量: {len(active_factor)}")
print(f"✓ 违反阈值: {max_exposure}")
print(f"✓ 检测到违反: {len(orig_violations)} 个")
print(f"✓ 违反列表: {orig_violations}")
print("✓ 测试通过")

# ============================================================================
# 测试 4: qlib_*_train.py - 字典构建
# ============================================================================
print("\n[测试 4] qlib_*_train.py - 信号字典构建")
print("-" * 60)


def original_signal_dict(latest_data):
    """原始 iterrows 实现"""
    latest_signals = {}
    for _, row in latest_data.iterrows():
        latest_signals[row["instrument"]] = float(row["signal"])
    return latest_signals


def vectorized_signal_dict(latest_data):
    """向量化实现"""
    return dict(
        zip(latest_data["instrument"], latest_data["signal"].astype(float), strict=True)
    )


# 测试数据
test_data = pd.DataFrame(
    {
        "instrument": ["000001.SZ", "000002.SZ", "600000.SH", "600036.SH"],
        "signal": [0.123, -0.456, 0.789, -0.012],
        "date": ["2024-01-15"] * 4,
    }
)

orig_dict = original_signal_dict(test_data)
vect_dict = vectorized_signal_dict(test_data)

assert orig_dict == vect_dict, f"字典不匹配: {orig_dict} vs {vect_dict}"

print(f"✓ 标的数量: {len(test_data)}")
print(f"✓ 字典内容: {vect_dict}")
print("✓ 测试通过")

# ============================================================================
# 性能对比
# ============================================================================
print("\n" + "=" * 60)
print("性能对比测试")
print("=" * 60)

import time  # noqa: E402

# 测试 qlib_data_bridge 性能
large_df = pd.DataFrame(
    {
        "open": np.random.randn(1000) * 10 + 100,
        "high": np.random.randn(1000) * 10 + 105,
        "low": np.random.randn(1000) * 10 + 95,
        "close": np.random.randn(1000) * 10 + 100,
        "volume": np.random.randint(1000, 10000, 1000),
    },
    index=pd.date_range("2020-01-01", periods=1000),
)

start = time.perf_counter()
for _ in range(10):
    original_dataframe_to_qlib(large_df)
orig_time = time.perf_counter() - start

start = time.perf_counter()
for _ in range(10):
    vectorized_dataframe_to_qlib(large_df)
vect_time = time.perf_counter() - start

print("\n[qlib_data_bridge] 1000 行 DataFrame 转换 (10 次平均)")
print(f"  原始: {orig_time/10*1000:.2f} ms")
print(f"  优化: {vect_time/10*1000:.2f} ms")
print(f"  加速: {orig_time/vect_time:.1f}x")

# 测试价格重建性能
large_rets = pd.Series(
    np.random.randn(500) * 0.02, index=pd.date_range("2020-01-01", periods=500)
)

start = time.perf_counter()
for _ in range(100):
    original_price_rebuild(large_rets, 100.0)
orig_time = time.perf_counter() - start

start = time.perf_counter()
for _ in range(100):
    vectorized_price_rebuild(large_rets, 100.0)
vect_time = time.perf_counter() - start

print("\n[rebuild_prices] 500 行价格重建 (100 次平均)")
print(f"  原始: {orig_time/100*1000:.2f} ms")
print(f"  优化: {vect_time/100*1000:.2f} ms")
print(f"  加速: {orig_time/vect_time:.1f}x")

# ============================================================================
# 总结
# ============================================================================
print("\n" + "=" * 60)
print("✅ 所有测试通过！向量化优化验证成功")
print("=" * 60)
