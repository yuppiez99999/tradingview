#!/usr/bin/env python3
"""反向验证: 用真实 ShadowRealDataFeeder._cross_validate_internal 路径验证白名单修复.

构造 Mock 价格数据 (含 159915 涨 22% / 159919 涨 22% / 600519 涨 22% 等),
直接调用真实的数据清洗代码路径, 证明 market_rules.py 的白名单修复
确实在主清洗流水线中生效.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 先 monkey-patch 价格拉取, 再 import
from utils.alpha import shadow_real_data_feeder as srdf  # noqa: E402

# Mock 价格表: symbol -> (prev_close, target_close)
# 故意构造 22% 涨跌场景, 区分 20cm 应豁免 / 10cm 应异常
_MOCK_PRICES: dict[str, tuple[float, float]] = {
    # === 20cm 创业板 ETF (白名单) - 涨 22%, 应被豁免 ===
    "159915.SZ": (1.0000, 1.2200),  # +22%
    "159977.SZ": (1.0000, 1.2500),  # +25%
    "159952.SZ": (1.0000, 1.2800),  # +28%
    # === 20cm 创业板股票 (代码段) - 涨 22%, 应被豁免 ===
    "300750.SZ": (100.00, 122.00),  # 宁德时代 +22%
    "688981.SH": (50.00, 62.50),    # 中芯国际 +25%
    # === 20cm 科创板 ETF (代码段) - 涨 22%, 应被豁免 ===
    "588000.SH": (1.0000, 1.2200),  # 科创50ETF +22%
    # === 10cm ETF (159 段非白名单) - 涨 22%, 应异常 ===
    "159919.SZ": (1.0000, 1.2200),  # 嘉实沪深300ETF +22%
    # === 10cm 股票 - 涨 22%, 应异常 ===
    "600519.SH": (1000.00, 1220.00),  # 贵州茅台 +22%
    # === 10cm 股票 - 跌 22%, 应异常 ===
    "601318.SH": (50.00, 39.00),    # 中国平安 -22%
    # === 正常波动 - 不应被标记 ===
    "600519.SH_dup": (100.00, 105.00),  # +5%
    "000001.SZ": (10.00, 10.50),     # 平安银行 +5%
    # === 20cm 真异常 - 涨 35%, 应异常 ===
    "300059.SZ": (10.00, 13.50),    # 东方财富 +35%
    # === 20cm 创业板 ETF (白名单) 真异常 - 跌 32%, 应异常 ===
    "159915_dup": (1.0000, 0.6800),  # -32%
}


def _mock_fetch(self, symbol: str, date: str) -> tuple[Any, Any]:
    """替代真实 _fetch_symbol_prices (bound method 形式).

    调用方: self._fetch_symbol_prices(symbol, date)
    所以这里接收 (self, symbol, date) 三个位置参数.
    """
    return _MOCK_PRICES.get(symbol, (None, None))


def main() -> int:
    print("=" * 80)
    print("反向验证: 真实清洗代码路径 + 白名单修复效果")
    print("=" * 80)

    # monkey-patch ShadowRealDataFeeder._fetch_symbol_prices
    srdf.ShadowRealDataFeeder._fetch_symbol_prices = _mock_fetch  # type: ignore

    # 构造 ShadowRealDataFeeder 实例 (绕过 __init__ 的数据源初始化)
    feeder = srdf.ShadowRealDataFeeder.__new__(srdf.ShadowRealDataFeeder)
    # 补 _cross_validate_internal 用到的最小属性集
    feeder._provider = type("MockProvider", (), {"name": "mock", "source_tag": "mock"})()
    feeder._collect_sources_used = lambda providers: ["mock"]  # type: ignore

    target_weights = {sym: 1.0 / len(_MOCK_PRICES) for sym in _MOCK_PRICES}
    date = "2026-08-11"

    print(f"\nMock 标的数: {len(_MOCK_PRICES)}")
    print(f"测试日期: {date}")
    print()

    # 调用真实清洗代码路径
    result = feeder._cross_validate_internal(
        date=date,
        primary_return=0.001,  # 组合层面 0.1% (无异常)
        target_weights=target_weights,
    )

    notes = result.notes or ""
    print("=" * 80)
    print("清洗代码返回的 notes:")
    print("=" * 80)
    print(notes)
    print()

    # 解析 abnormal 标的清单
    import re as re_mod
    abnormal_syms: list[tuple[str, str, str]] = []
    if "abnormal:" in notes:
        tail = notes.split("abnormal:", 1)[1].strip()
        # 字符类含大小写字母+数字+下划线+点, 以匹配 159915_dup 这种带后缀的测试 key
        for m in re_mod.finditer(r"([A-Za-z0-9._]+)\(ret=([+\-\d.]+%),thr=(\d+)%\)", tail):
            abnormal_syms.append((m.group(1), m.group(2), m.group(3)))

    # 验证断言
    print("=" * 80)
    print("断言检查 (白名单修复在清洗代码路径中是否生效)")
    print("=" * 80)

    abnormal_set = {s[0] for s in abnormal_syms}
    assertions = [
        # 期望被豁免 (20cm 板 + 涨跌 20%-30%)
        ("159915.SZ 易方达创业板ETF +22% 应被豁免 (白名单生效)",
         "159915.SZ" not in abnormal_set),
        ("159977.SZ 国泰创业板ETF +25% 应被豁免 (白名单生效)",
         "159977.SZ" not in abnormal_set),
        ("159952.SZ 广发创业板ETF +28% 应被豁免 (白名单生效)",
         "159952.SZ" not in abnormal_set),
        ("300750.SZ 宁德时代 +22% 应被豁免 (代码段)",
         "300750.SZ" not in abnormal_set),
        ("688981.SH 中芯国际 +25% 应被豁免 (代码段)",
         "688981.SH" not in abnormal_set),
        ("588000.SH 科创50ETF +22% 应被豁免 (代码段)",
         "588000.SH" not in abnormal_set),
        # 期望仍异常 (10cm 板 + 涨跌 ≥ 20%)
        ("159919.SZ 嘉实沪深300ETF +22% 应异常 (非白名单 159 段)",
         "159919.SZ" in abnormal_set),
        ("600519.SH 贵州茅台 +22% 应异常 (10cm 板)",
         "600519.SH" in abnormal_set),
        ("601318.SH 中国平安 -22% 应异常 (10cm 板)",
         "601318.SH" in abnormal_set),
        # 期望仍异常 (20cm 板 + 涨跌 ≥ 30%)
        ("300059.SZ 东方财富 +35% 应异常 (20cm 超阈值)",
         "300059.SZ" in abnormal_set),
        ("159915_dup 创业板ETF -32% 应异常 (20cm 真异常)",
         "159915_dup" in abnormal_set),
        # 期望未被误判 (10cm 板 + 涨跌 < 20%)
        ("600519.SH_dup +5% 不应被标记 (正常波动)",
         "600519.SH_dup" not in abnormal_set),
        ("000001.SZ 平安银行 +5% 不应被标记 (正常波动)",
         "000001.SZ" not in abnormal_set),
    ]

    all_pass = True
    for desc, ok in assertions:
        mark = "✓ PASS" if ok else "✗ FAIL"
        print(f"  {mark}  {desc}")
        if not ok:
            all_pass = False

    # 打印被标记为异常的标的 (确认阈值显示)
    print("\n" + "-" * 80)
    print(f"清洗代码标记的异常标的 ({len(abnormal_syms)} 个):")
    print("-" * 80)
    for sym, ret, thr in sorted(abnormal_syms):
        print(f"  {sym:20} ret={ret:>8}  thr=±{thr}%")

    print("\n" + "=" * 80)
    print(f"结论: {'全部断言通过 ✓' if all_pass else '存在失败断言 ✗'}")
    print("=" * 80)

    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
