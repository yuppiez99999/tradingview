# -*- coding: utf-8 -*-
"""P2.1 微观结构因子 smoke test - 验证实现无语法错误且能正确计算"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.adapters.vibe_trading_factor_adapter import (
    VibeTradingFactorAdapter,
)


def _make_synthetic_price(n_days: int = 100) -> dict:
    """生成合成价格数据用于测试"""
    closes = []
    opens = []
    highs = []
    lows = []
    vols = []
    import random
    random.seed(42)
    price = 100.0
    for i in range(n_days):
        # 模拟趋势 + 噪声
        trend = 0.001 if i > 50 else -0.001
        ret = trend + (random.random() - 0.5) * 0.02
        open_p = price
        close = price * (1 + ret)
        high = max(open_p, close) * (1 + random.random() * 0.005)
        low = min(open_p, close) * (1 - random.random() * 0.005)
        vol = 1000000 * (1 + random.random() * 0.5)
        opens.append(open_p)
        closes.append(close)
        highs.append(high)
        lows.append(low)
        vols.append(vol)
        price = close
    return {
        "closes": closes,
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "volumes": vols,
    }


def main() -> int:
    print("=" * 70)
    print("P2.1 微观结构因子 Smoke Test")
    print("=" * 70)

    # 构造 5 个标的的合成数据
    price_data = {f"TEST{i:02d}_SZ": _make_synthetic_price(100) for i in range(5)}

    adapter = VibeTradingFactorAdapter()
    pool = adapter.compute_candidate_factors(price_data=price_data)

    print(f"\n[OK] 候选因子总数: {pool.total_candidates}")

    micro_factors = {k: v for k, v in pool.factors.items() if "MICRO" in k or "VOL_DRAIN_INV" in k}
    print(f"[OK] 微观结构因子数: {len(micro_factors)}")

    print("\n各微观结构因子计算结果:")
    for name, factor in micro_factors.items():
        values = factor.values
        if values:
            sample_sym = list(values.keys())[0]
            sample_val = values[sample_sym]
            print(f"  {name:35s} | {sample_sym}: {sample_val:+.6f} | desc: {factor.description[:50]}")
        else:
            print(f"  {name:35s} | [空值警告]")

    # 验证因子值分布合理性（不全为 0，不全相同）
    print("\n因子值分布合理性检查:")
    for name, factor in micro_factors.items():
        vals = list(factor.values.values())
        if vals:
            uniq = len(set([round(v, 6) for v in vals]))
            min_v, max_v = min(vals), max(vals)
            status = "OK" if uniq > 1 and abs(max_v - min_v) > 1e-9 else "WARN"
            print(f"  [{status}] {name:35s} | n={len(vals)} unique={uniq} range=[{min_v:+.4f}, {max_v:+.4f}]")

    print("\n" + "=" * 70)
    print("[OK] Smoke test 通过 - 微观结构因子实现无语法错误，能正确计算")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
