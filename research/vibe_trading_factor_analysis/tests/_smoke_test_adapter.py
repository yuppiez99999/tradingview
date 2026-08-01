"""VibeTradingFactorAdapter 烟雾测试"""
import os
import sys

# 添加路径
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, project_root)

# 添加适配器模块路径
adapter_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, adapter_dir)
sys.path.insert(0, os.path.join(adapter_dir, "adapters"))

try:
    from adapters.vibe_trading_factor_adapter import (
        FACTOR_ORIGIN,
        VibeTradingFactorAdapter,
    )
    print("[OK] 模块导入成功")
    print("[OK] FACTOR_ORIGIN = " + FACTOR_ORIGIN)

    # 健康检查
    adapter = VibeTradingFactorAdapter()
    health = adapter.health_check()
    print("[OK] 健康检查: status=" + health["status"])
    print("[OK] utils_available=" + str(health["utils_available"]))
    print("[OK] 支持因子类别数: " + str(len(health["supported_categories"])))
    print("[OK] 正交性阈值: " + str(health["ortho_threshold"]))

    # 构造测试数据
    import numpy as np
    np.random.seed(42)
    price_data = {}
    fundamentals = {}
    symbols = ["600519", "000858", "601318", "600036", "000001"]
    for sym in symbols:
        closes = list(100 * np.cumprod(1 + np.random.normal(0.0005, 0.02, 100)))
        vols = list(np.random.randint(1000000, 10000000, 100))
        highs = [c * (1 + abs(np.random.normal(0, 0.01))) for c in closes]
        lows = [c * (1 - abs(np.random.normal(0, 0.01))) for c in closes]
        price_data[sym] = {"closes": closes, "volumes": vols, "highs": highs, "lows": lows}
        fundamentals[sym] = {
            "pe": float(np.random.uniform(10, 50)),
            "pb": float(np.random.uniform(1, 10)),
            "ps": float(np.random.uniform(1, 20)),
            "roe": float(np.random.uniform(0.05, 0.30)),
            "gross_margin": float(np.random.uniform(0.1, 0.5)),
            "debt_to_equity": float(np.random.uniform(0.1, 1.5)),
            "market_cap": float(np.random.uniform(1e9, 1e11)),
            "revenue_growth": float(np.random.uniform(-0.1, 0.4)),
            "profit_growth": float(np.random.uniform(-0.1, 0.5)),
        }

    # 测试候选因子计算
    pool = adapter.compute_candidate_factors(price_data, fundamentals)
    print("[OK] 候选因子计算完成: total=" + str(pool.total_candidates))
    print("[OK] 批次ID: " + pool.batch_id)
    print("[OK] 因子列表:")
    for fname, fval in pool.factors.items():
        print("    - " + fname + " (" + fval.category + "): " + str(len(fval.values)) + " symbols")

    # 测试现有因子加载
    existing = adapter.load_existing_factors(price_data, fundamentals)
    print("[OK] 现有因子加载: " + str(len(existing)) + " 个")

    # 测试正交性检查
    ortho_results = adapter.check_orthogonality(pool, existing)
    print("[OK] 正交性检查完成: " + str(len(ortho_results)) + " 个候选因子")

    # 筛选通过正交性的因子
    qualified = adapter.filter_orthogonal_factors(pool, ortho_results)
    print("[OK] 通过正交性的因子: " + str(len(qualified)) + " 个")
    print("")
    print("=" * 60)
    print("[SUCCESS] 烟雾测试全部通过")
    print("=" * 60)

except SyntaxError as e:
    print("[FAIL] 语法错误: " + str(e))
    import traceback
    traceback.print_exc()
except Exception as e:
    print("[ERROR] " + str(e))
    import traceback
    traceback.print_exc()
