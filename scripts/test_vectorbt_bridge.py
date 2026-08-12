"""W6.4.1 vectorbt 对照验证脚本。

生成合成 OHLC 数据, 运行 G15 事件驱动引擎 + vectorbt 向量化回测,
验证两者在最终权益上的偏差 <5% (W6.4.1 验收标准)。

运行:
    python scripts/test_vectorbt_bridge.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.backtest.event_driven_engine import EngineSummary
from utils.backtest.vectorbt_bridge import ComparisonReport, VectorBtBridge, generate_ma_cross_signals
from utils.wt_structs import BarData


def generate_synthetic_bars(n_bars: int = 100, seed: int = 42) -> tuple[list[BarData], pd.Series, pd.Series]:
    """生成合成 OHLC 日线数据 (正弦波 + 噪声, 确保 MA 交叉)。

    Returns:
        (bars, closes, opens) — BarData 列表 + 收盘价序列 + 开盘价序列
    """
    rng = np.random.RandomState(seed)

    # 正弦波基础 (周期 20 → 与慢线窗口匹配, 产生交叉)
    t = np.arange(n_bars)
    base = 10.0 + 2.0 * np.sin(2 * np.pi * t / 20.0)
    noise = rng.normal(0, 0.3, n_bars)
    closes_arr = base + noise

    # 开盘价: 前日收盘 + 小 gap
    opens_arr = np.empty(n_bars)
    opens_arr[0] = closes_arr[0]
    opens_arr[1:] = closes_arr[:-1] + rng.normal(0, 0.1, n_bars - 1)

    dates = pd.date_range("2024-01-01", periods=n_bars, freq="B")
    closes = pd.Series(closes_arr, index=dates, dtype=float)
    opens = pd.Series(opens_arr, index=dates, dtype=float)

    bars: list[BarData] = []
    for i in range(n_bars):
        o = float(opens_arr[i])
        c = float(closes_arr[i])
        h = max(o, c) + abs(rng.normal(0, 0.2))
        l = min(o, c) - abs(rng.normal(0, 0.2))
        bars.append(BarData(
            code="TEST.SH",
            exchange="SSE",
            period="1d",
            open=o,
            high=h,
            low=l,
            close=c,
            volume=100_000.0,  # 足够大, 确保 max_participation_rate=1.0 下全量成交
            amount=o * 100_000.0,
            date=int(dates[i].strftime("%Y%m%d")),
            time=0,
        ))

    return bars, closes, opens


def main() -> int:
    print("=" * 70)
    print("W6.4.1 vectorbt 向量化回测对照验证")
    print("=" * 70)

    # 1. 生成合成数据
    n_bars = 120
    bars, closes, opens = generate_synthetic_bars(n_bars=n_bars, seed=42)
    print(f"\n[1] 合成数据: {n_bars} bars, 价格区间 {closes.min():.2f} ~ {closes.max():.2f}")

    # 2. 预览信号
    signals = generate_ma_cross_signals(closes, fast_window=5, slow_window=20)
    n_buy = sum(1 for s in signals if s == "BUY")
    n_sell = sum(1 for s in signals if s == "SELL")
    print(f"[2] MA(5,20) 交叉信号: {n_buy} BUY + {n_sell} SELL + {n_bars - n_buy - n_sell} HOLD")

    # 3. 运行对照
    bridge = VectorBtBridge(
        initial_capital=1_000_000.0,
        commission_rate=0.0003,
        threshold_pct=5.0,
        position_size=1000.0,
    )

    print(f"\n[3] 运行 G15 事件驱动引擎 + vectorbt 向量化回测...")
    report = bridge.run_ma_cross_comparison(
        bars=bars,
        closes=closes,
        opens=opens,
        fast_window=5,
        slow_window=20,
        target_code="TEST.SH",
    )

    # 4. 输出结果
    print(f"\n[4] 对照结果:")
    print(f"  {report.summary()}")

    if report.notes:
        print(f"\n  备注:")
        for note in report.notes:
            print(f"    - {note}")

    # 5. 验收
    print(f"\n[5] 验收门禁 (偏差 <{report.threshold_pct:.1f}%):")
    if report.passed:
        print(f"  ✅ PASS — G15 与 vectorbt 在 MA(5,20) 交叉策略上偏差 "
              f"{report.equity_deviation_pct:.4f}% < {report.threshold_pct:.1f}%")
        return 0
    else:
        print(f"  ❌ FAIL — 偏差 {report.equity_deviation_pct:.4f}% ≥ {report.threshold_pct:.1f}%")
        print(f"  需排查: 信号对齐 / 手续费口径 / 成交价时点")
        return 1


if __name__ == "__main__":
    sys.exit(main())
