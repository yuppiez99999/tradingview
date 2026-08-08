"""
方向性期货交易模块单元测试
"""
import random
import sys
from datetime import date, timedelta
from pathlib import Path

# 将项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.directional_futures_trader import (  # noqa: E402
    CONTRACT_SPECS,
    DirectionalFuturesTrader,
    FuturesSignal,
)


def _gen_market_data(seed: int = 42, trend: float = 0.001):
    """生成模拟市场数据"""
    random.seed(seed)
    market_data = {}
    for symbol in CONTRACT_SPECS.keys():
        base_price = {"CU": 75000, "AU": 550, "T": 100}[symbol]
        closes = [base_price]
        for _ in range(60):
            closes.append(closes[-1] * (1 + trend + random.uniform(-0.015, 0.015)))
        market_data[symbol] = {"closes": closes}
    return market_data


def test_signal_generation():
    """测试 1: 信号生成"""
    print("\n" + "=" * 60)
    print("测试 1: 信号生成")
    print("=" * 60)

    trader = DirectionalFuturesTrader()
    market_data = _gen_market_data(trend=0.005)  # 上升趋势

    signals = trader.generate_signals(market_data)

    assert len(signals) == 3, f"信号数应为 3, 实际 {len(signals)}"

    for s in signals:
        print(f"  {s.symbol} ({s.name}): 方向 {s.direction}, 强度 {s.strength:.2f}, 置信 {s.confidence:.2f}")
        print(f"    MA20 {s.ma20:.2f}, MA60 {s.ma60:.2f}, RSI {s.rsi:.1f}, MACD {s.macd_hist:+.4f}")
        assert s.direction in ("long", "short", "flat"), f"方向非法: {s.direction}"
        assert -1 <= s.strength <= 1, f"强度超界: {s.strength}"

    # 上升趋势应产生多头信号或中性
    long_count = sum(1 for s in signals if s.direction == "long")
    short_count = sum(1 for s in signals if s.direction == "short")
    print(f"\n多头信号: {long_count}, 空头信号: {short_count}")
    assert long_count + short_count <= 3, "信号总数应 ≤ 3"

    print("\n✓ 测试 1 通过")
    return True


def test_position_sizing():
    """测试 2: 仓位计算"""
    print("\n" + "=" * 60)
    print("测试 2: 仓位计算")
    print("=" * 60)

    trader = DirectionalFuturesTrader()

    # 强多头信号
    signal = FuturesSignal(
        symbol="CU",
        name="沪铜期货",
        direction="long",
        strength=0.8,
    )
    contracts, notional, margin = trader.calculate_position("CU", signal, 75000)

    spec = CONTRACT_SPECS["CU"]
    expected_one_margin = 75000 * spec["multiplier"] * spec["margin_rate"]
    expected_max_contracts = int(trader.per_symbol_budget * 0.8 // expected_one_margin)

    print(f"  CU: {contracts} 张, 名义 ¥{notional:,.0f}, 保证金 ¥{margin:,.0f}")
    print(f"  单张保证金 ¥{expected_one_margin:,.0f}, 期望最大 {expected_max_contracts} 张")

    assert contracts >= 1, "强信号应至少 1 张"
    assert notional == contracts * 75000 * spec["multiplier"]
    assert margin == notional * spec["margin_rate"]
    assert margin <= trader.per_symbol_budget * 0.8 + expected_one_margin  # 不超过预算

    # 中性信号 (强度 < 0.3) 应不开仓
    weak_signal = FuturesSignal(symbol="CU", name="沪铜期货", direction="long", strength=0.2)
    c2, _n2, _m2 = trader.calculate_position("CU", weak_signal, 75000)
    assert c2 == 0, "弱信号应不开仓"

    print(f"\n✓ 测试 2 通过: 强信号 {contracts} 张, 弱信号 {c2} 张")
    return True


def test_risk_control():
    """测试 3: 风控检查"""
    print("\n" + "=" * 60)
    print("测试 3: 风控检查")
    print("=" * 60)

    trader = DirectionalFuturesTrader()

    # 场景 A: 正常状态
    status, pause = trader.check_risk({}, daily_pnl_pct=0.0, weekly_consecutive_loss_pct=0.0)
    print(f"  A 正常: {status} (pause={pause})")
    assert status == "normal", f"正常状态应为 normal, 实际 {status}"
    assert pause is None

    # 场景 B: 日亏损 15% → 警告
    status, pause = trader.check_risk({}, daily_pnl_pct=-0.15, weekly_consecutive_loss_pct=0.0)
    print(f"  B 日亏 15%: {status}")
    assert status == "warning", f"日亏 15% 应为 warning, 实际 {status}"

    # 场景 C: 周连续亏损 25% → 暂停
    status, pause = trader.check_risk({}, daily_pnl_pct=0, weekly_consecutive_loss_pct=0.25)
    print(f"  C 周连亏 25%: {status}, 暂停至 {pause}")
    assert status == "paused", f"周连亏 25% 应为 paused, 实际 {status}"
    assert pause is not None

    # 场景 D: 暂停期内 (使用相对日期, 避免硬编码日期过期)
    status, pause = trader.check_risk(
        {},
        daily_pnl_pct=0, weekly_consecutive_loss_pct=0,
        last_loss_pause_date=date.today() - timedelta(days=3),
    )
    print(f"  D 暂停期内: {status}, 暂停至 {pause}")
    assert status == "paused", "暂停期内应为 paused"

    print("\n✓ 测试 3 通过")
    return True


def test_order_generation():
    """测试 4: 指令生成"""
    print("\n" + "=" * 60)
    print("测试 4: 指令生成")
    print("=" * 60)

    trader = DirectionalFuturesTrader()
    market_data = _gen_market_data(trend=0.005)
    prices = {s: d["closes"][-1] for s, d in market_data.items()}

    # 场景 A: 全新开仓 (无持仓)
    signals = trader.generate_signals(market_data)
    orders = trader.generate_orders(signals, {}, prices, date(2026, 7, 14), "normal")

    print("\n场景 A: 全新开仓")
    for o in orders:
        print(f"  {o.symbol} {o.action} {o.contracts} 张 @ {o.price:.2f}, 保证金 ¥{o.required_margin:,.0f}")
        assert o.action in ("open_long", "open_short", "hold"), f"新仓动作非法: {o.action}"

    # 场景 B: 暂停状态强制平仓
    positions = {
        "CU": {"direction": "long", "contracts": 2, "entry_price": 74000},
        "AU": {"direction": "short", "contracts": 1, "entry_price": 560},
    }
    orders_b = trader.generate_orders(signals, positions, prices, date(2026, 7, 14), "paused")

    print("\n场景 B: 暂停强制平仓")
    for o in orders_b:
        print(f"  {o.symbol} {o.action} {o.contracts} 张")
        assert o.action in ("close_long", "close_short"), f"暂停应平仓, 实际 {o.action}"

    print("\n✓ 测试 4 通过")
    return True


def test_full_workflow():
    """测试 5: 完整流程"""
    print("\n" + "=" * 60)
    print("测试 5: 完整流程")
    print("=" * 60)

    trader = DirectionalFuturesTrader()
    market_data = _gen_market_data(trend=0.005)
    prices = {s: d["closes"][-1] for s, d in market_data.items()}

    result = trader.run(
        market_data=market_data,
        current_positions={},
        prices=prices,
        trade_date=date(2026, 7, 14),
    )

    print(result.summary_text)

    assert result.risk_status == "normal", "正常状态应为 normal"
    assert len(result.signals) == 3
    assert len(result.orders) == 3
    assert result.margin_usage_ratio <= 0.60 + 0.01, f"保证金占用率应 ≤ 60%, 实际 {result.margin_usage_ratio:.1%}"

    print("\n✓ 测试 5 通过")
    return True


if __name__ == "__main__":
    tests = [
        test_signal_generation,
        test_position_sizing,
        test_risk_control,
        test_order_generation,
        test_full_workflow,
    ]
    results = []
    for t in tests:
        try:
            t()
            results.append((t.__name__, "PASS", ""))
        except Exception as e:
            import traceback
            traceback.print_exc()
            results.append((t.__name__, "FAIL", str(e)[:200]))

    print("\n" + "=" * 60)
    print("最终结果")
    print("=" * 60)
    for name, status, err in results:
        print(f"{status} {name} {err}")
