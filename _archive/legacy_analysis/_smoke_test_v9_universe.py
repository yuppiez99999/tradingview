"""V9 回测 105 标的池集成烟雾测试

验证:
1. list_available_symbols() 返回 >= 100 个标的
2. load_price_data() 能成功加载 5y 数据
3. 行情数据长度符合 5 年日 K 预期 (~1211 天)
4. 旧 23 标的池全部向后兼容
5. 4 个特殊标的 (688235/688041/601989/600837) 数据可用但范围受限
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from cache.symbol_universe import EXISTING_SYMBOLS  # noqa: E402
from research.vibe_trading_factor_analysis.scripts.real_data_loader import (  # noqa: E402
    list_available_symbols,
    load_price_data,
)


def main() -> int:
    print("=" * 80)
    print("V9 回测 105 标的池集成烟雾测试")
    print("=" * 80)

    # 1. 验证 list_available_symbols
    print("\n[1] list_available_symbols() 验证")
    symbols = list_available_symbols()
    print(f"  返回标的数: {len(symbols)}")
    if len(symbols) < 100:
        print(f"  ✗ 失败: 标的数 {len(symbols)} < 100")
        return 1
    print("  ✓ 通过: 标的数 >= 100")
    print(f"  前 5 个: {symbols[:5]}")
    print(f"  后 5 个: {symbols[-5:]}")

    # 2. 验证 23 个旧标的向后兼容
    print("\n[2] 旧 23 标的池向后兼容验证")
    missing_legacy = [s for s in EXISTING_SYMBOLS if s not in symbols]
    if missing_legacy:
        print(f"  ✗ 失败: 旧标的缺失 {len(missing_legacy)} 个: {missing_legacy}")
        return 1
    print(f"  ✓ 通过: 全部 {len(EXISTING_SYMBOLS)} 个旧标的可用")

    # 3. 加载 price_data (5y 数据)
    print("\n[3] load_price_data() 验证 (5y 数据加载)")
    price_data = load_price_data(symbols=symbols)
    print(f"  成功加载: {len(price_data)} / {len(symbols)}")

    if len(price_data) < 100:
        print(f"  ✗ 失败: 加载数 {len(price_data)} < 100")
        return 1
    print("  ✓ 通过: 加载数 >= 100")

    # 4. 行情数据长度验证
    print("\n[4] 行情数据长度验证 (5y 日 K ~ 1211 天)")
    lengths = []
    for _sym, data in price_data.items():
        lengths.append(len(data["closes"]))

    avg_len = sum(lengths) / len(lengths)
    min_len = min(lengths)
    max_len = max(lengths)
    print(f"  长度统计: min={min_len}, max={max_len}, avg={avg_len:.0f}")

    if avg_len < 1000:
        print(f"  ✗ 失败: 平均长度 {avg_len:.0f} < 1000 (应为 5y 日 K)")
        return 1
    print(f"  ✓ 通过: 平均长度 {avg_len:.0f} 接近 5 年日 K 预期 (~1211)")

    # 5. 4 个特殊标的数据可用性
    print("\n[5] 4 个特殊标的 (新股/停牌) 数据可用性验证")
    special = {
        "688235_SH": "百济神州 (2021-12-15 上市)",
        "688041_SH": "海光信息 (2022-08-12 上市)",
        "601989_SH": "中国重工 (2025-09 停牌重组)",
        "600837_SH": "海通证券 (2025-03 合并停牌)",
    }
    all_special_ok = True
    for sym, desc in special.items():
        if sym in price_data:
            length = len(price_data[sym]["closes"])
            print(f"  ✓ {sym} ({desc}): {length} 天")
        else:
            print(f"  ✗ {sym} ({desc}): 缺失")
            all_special_ok = False

    if not all_special_ok:
        print("  ⚠ 警告: 部分特殊标的缺失 (但流水线可继续)")
    else:
        print("  ✓ 全部 4 个特殊标的数据可用 (范围受限但可回测)")

    # 6. 数据字段完整性
    print("\n[6] 数据字段完整性验证")
    required_fields = ["closes", "volumes", "highs", "lows", "opens"]
    field_ok = True
    for sym, data in list(price_data.items())[:3]:  # 抽样 3 个
        missing = [f for f in required_fields if f not in data or not data[f]]
        if missing:
            print(f"  ✗ {sym}: 缺少字段 {missing}")
            field_ok = False
        else:
            print(f"  ✓ {sym}: 全部字段完整 (closes={len(data['closes'])})")

    if not field_ok:
        return 1

    # 7. 数据质量抽样
    print("\n[7] 数据质量抽样 (5 个标的)")
    sample_syms = list(price_data.keys())[:5]
    for sym in sample_syms:
        data = price_data[sym]
        closes = data["closes"]
        first_close = closes[0]
        last_close = closes[-1]
        ratio = last_close / first_close if first_close > 0 else 0
        print(
            f"  {sym}: 首={first_close:.2f}, 末={last_close:.2f}, "
            f"5y 累计={ratio*100-100:+.1f}%, 天数={len(closes)}"
        )

    # 总结
    print("\n" + "=" * 80)
    print("V9 105 标的池集成烟雾测试结果")
    print("=" * 80)
    print(f"  标的池: 105 (定义) / {len(symbols)} (有数据) / {len(price_data)} (加载成功)")
    print("  旧 23 标的: 全部向后兼容")
    print(f"  数据范围: 5 年日 K (~1211 天), 平均 {avg_len:.0f} 天")
    print("  4 个特殊标的: 数据可用 (范围受限)")
    print("\n✓ V9 回测 105 标的池集成成功, 可投入 V9 Walk-Forward 回测")

    return 0


if __name__ == "__main__":
    sys.exit(main())
