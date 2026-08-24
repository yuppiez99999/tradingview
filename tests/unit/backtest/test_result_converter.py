"""result_converter.py 单元测试 — EngineSummary → BacktestResult 转换器。

覆盖矩阵:
    ConversionConfig: 默认值、自定义值
    ResultConverter.convert: 基础转换、日期处理、字段映射
    _calc_daily_returns: 公式正确性 (首值=0, 逐日收益率)
    _calc_metrics: 每个指标公式 (total_return/annual_return/volatility/sharpe/max_dd/calmar/win_rate)
    _generate_dates: 长度、类型、起始日期
    _count_fills: 成交订单统计
    边界情况: 单点 equity_curve、空 trade_records
    日期长度不匹配: 抛出 ValueError
    集成测试: EventDrivenEngine → ResultConverter 端到端
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

# 复用 event_driven_engine 测试中的辅助
from tests.unit.backtest.test_event_driven_engine import (
    _NoopStrategy,
    make_bar,
    make_buy_order,
)
from utils.backtest.event_driven_engine import EngineSummary
from utils.backtest.latency_model import FixedLatency
from utils.backtest.result_converter import (
    RISK_FREE_RATE,
    TRADING_DAYS_PER_YEAR,
    ConversionConfig,
    ResultConverter,
)
from utils.hedge_rebalance_backtest import BacktestResult

# ============================================================
# ConversionConfig 测试
# ============================================================

def test_conversion_config_defaults() -> None:
    """ConversionConfig 默认值与向量化回测常量对齐。"""
    config = ConversionConfig()
    assert config.risk_free_rate == RISK_FREE_RATE
    assert config.trading_days_per_year == TRADING_DAYS_PER_YEAR
    assert config.turnover_window == 20
    assert config.default_start_date == "2021-01-04"


def test_conversion_config_custom() -> None:
    """ConversionConfig 支持自定义值。"""
    config = ConversionConfig(
        risk_free_rate=0.05,
        trading_days_per_year=252,
        turnover_window=10,
        default_start_date="2023-01-01",
    )
    assert config.risk_free_rate == 0.05
    assert config.turnover_window == 10
    assert config.default_start_date == "2023-01-01"


# ============================================================
# ResultConverter.convert 基础测试
# ============================================================

def test_convert_returns_backtest_result() -> None:
    """convert 返回 BacktestResult 实例。"""
    converter = ResultConverter()
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_050_000.0,
        total_return=0.05,
        n_events=10,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=[1_000_000.0 + i * 5000 for i in range(11)],
    )

    result = converter.convert(summary, name="test")

    assert isinstance(result, BacktestResult)
    assert result.name == "test"
    assert result.equity_curve == summary.equity_curve
    assert len(result.daily_returns) == len(summary.equity_curve)


def test_convert_default_name() -> None:
    """未提供 name 时使用默认值。"""
    converter = ResultConverter()
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=1,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=[1_000_000.0, 1_000_000.0],
    )

    result = converter.convert(summary)

    assert result.name == "event_driven"


def test_convert_dates_auto_generated() -> None:
    """未提供 dates 时自动生成,长度与 equity_curve 一致。"""
    converter = ResultConverter()
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=5,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=[1_000_000.0] * 6,
    )

    result = converter.convert(summary)

    assert len(result.dates) == 6
    assert all(isinstance(d, pd.Timestamp) for d in result.dates)


def test_convert_dates_mismatch_raises() -> None:
    """dates 长度与 equity_curve 不匹配时抛出 ValueError。"""
    converter = ResultConverter()
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=3,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=[1_000_000.0] * 4,
    )
    dates = [pd.Timestamp("2021-01-04"), pd.Timestamp("2021-01-05")]  # 只有 2 个

    with pytest.raises(ValueError, match="dates 长度"):
        converter.convert(summary, dates=dates)


def test_convert_n_days_set_correctly() -> None:
    """n_days = len(equity_curve) - 1 (排除初始点)。"""
    converter = ResultConverter()
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=10,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=[1_000_000.0] * 11,
    )

    result = converter.convert(summary)

    assert result.n_days == 10


# ============================================================
# _calc_daily_returns 公式测试
# ============================================================

def test_daily_returns_first_value_zero() -> None:
    """daily_returns[0] = 0.0 (与向量化回测 _rets 一致)。"""
    converter = ResultConverter()
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_100_000.0,
        total_return=0.1,
        n_events=3,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=[1_000_000.0, 1_050_000.0, 1_100_000.0],
    )

    result = converter.convert(summary)

    assert result.daily_returns[0] == 0.0


def test_daily_returns_formula() -> None:
    """daily_returns[i] = (eq[i] - eq[i-1]) / eq[i-1]。"""
    converter = ResultConverter()
    eq = [1_000_000.0, 1_100_000.0, 1_210_000.0]
    summary = EngineSummary(
        initial_capital=eq[0],
        final_equity=eq[-1],
        total_return=0.21,
        n_events=2,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=eq,
    )

    result = converter.convert(summary)

    expected_rets = [0.0, 0.1, 0.1]  # 10% each day
    for actual, expected in zip(result.daily_returns, expected_rets, strict=True):
        assert actual == pytest.approx(expected, rel=1e-6)


def test_daily_returns_zero_prev_protected() -> None:
    """eq[i-1] = 0 时 daily_returns[i] = 0 (防除零)。"""
    converter = ResultConverter()
    summary = EngineSummary(
        initial_capital=0.0,
        final_equity=100.0,
        total_return=0.0,
        n_events=2,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=[0.0, 0.0, 100.0],
    )

    result = converter.convert(summary)

    assert result.daily_returns[1] == 0.0  # prev=0 → 0


# ============================================================
# _calc_metrics 指标公式测试
# ============================================================

def test_total_return_formula() -> None:
    """total_return = (eq[-1] - eq[0]) / eq[0]。"""
    converter = ResultConverter()
    eq = [1_000_000.0, 1_100_000.0]
    summary = EngineSummary(
        initial_capital=eq[0],
        final_equity=eq[-1],
        total_return=0.1,
        n_events=1,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=eq,
    )

    result = converter.convert(summary)

    assert result.total_return == pytest.approx(0.1, rel=1e-6)


def test_annual_return_formula() -> None:
    """annual_return = (1 + total_return) ** (1 / max(n_years, 0.5)) - 1。"""
    converter = ResultConverter()
    # 252 个事件 = 1 年
    eq = [1_000_000.0] + [1_100_000.0] * 252
    summary = EngineSummary(
        initial_capital=eq[0],
        final_equity=eq[-1],
        total_return=0.1,
        n_events=252,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=eq,
    )

    result = converter.convert(summary)

    n_years = 252 / TRADING_DAYS_PER_YEAR  # 1.0
    expected = (1 + 0.1) ** (1 / max(n_years, 0.5)) - 1
    assert result.annual_return == pytest.approx(expected, rel=1e-6)


def test_annual_volatility_formula() -> None:
    """annual_volatility = std(daily_returns) * sqrt(252)。"""
    converter = ResultConverter()
    # 构造已知波动率的序列
    eq = [1_000_000.0]
    for i in range(1, 253):
        # 交替 ±1% 模拟波动
        change = 1.01 if i % 2 == 0 else 0.99
        eq.append(eq[-1] * change)

    summary = EngineSummary(
        initial_capital=eq[0],
        final_equity=eq[-1],
        total_return=(eq[-1] - eq[0]) / eq[0],
        n_events=252,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=eq,
    )

    result = converter.convert(summary)

    rets = np.array(result.daily_returns)
    expected_vol = float(np.std(rets) * np.sqrt(TRADING_DAYS_PER_YEAR))
    assert result.annual_volatility == pytest.approx(expected_vol, rel=1e-6)


def test_sharpe_ratio_formula() -> None:
    """sharpe = (annual_return - RISK_FREE_RATE) / max(annual_volatility, 0.001)。"""
    converter = ResultConverter()
    eq = [1_000_000.0] + [1_100_000.0] * 252
    summary = EngineSummary(
        initial_capital=eq[0],
        final_equity=eq[-1],
        total_return=0.1,
        n_events=252,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=eq,
    )

    result = converter.convert(summary)

    expected_sharpe = (result.annual_return - RISK_FREE_RATE) / max(
        result.annual_volatility, 0.001
    )
    assert result.sharpe_ratio == pytest.approx(expected_sharpe, rel=1e-6)


def test_max_drawdown_formula() -> None:
    """max_drawdown = abs(min((eq - peak) / peak))。"""
    converter = ResultConverter()
    # 构造有回撤的序列: 1M → 1.2M → 0.8M → 1.1M
    eq = [1_000_000.0, 1_200_000.0, 800_000.0, 1_100_000.0]
    summary = EngineSummary(
        initial_capital=eq[0],
        final_equity=eq[-1],
        total_return=0.1,
        n_events=3,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=eq,
    )

    result = converter.convert(summary)

    # peak at 1.2M, trough at 0.8M → dd = (0.8 - 1.2) / 1.2 = -0.333...
    expected_dd = abs((800_000.0 - 1_200_000.0) / 1_200_000.0)
    assert result.max_drawdown == pytest.approx(expected_dd, rel=1e-6)


def test_calmar_ratio_formula() -> None:
    """calmar = annual_return / max(max_drawdown, 0.001)。"""
    converter = ResultConverter()
    eq = [1_000_000.0, 1_200_000.0, 800_000.0, 1_100_000.0]
    summary = EngineSummary(
        initial_capital=eq[0],
        final_equity=eq[-1],
        total_return=0.1,
        n_events=3,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=eq,
    )

    result = converter.convert(summary)

    expected_calmar = result.annual_return / max(result.max_drawdown, 0.001)
    assert result.calmar_ratio == pytest.approx(expected_calmar, rel=1e-6)


def test_win_rate_formula() -> None:
    """win_rate = sum(rets > 0) / max(len(rets), 1)。"""
    converter = ResultConverter()
    # 4 个点: 3 个正收益日, 1 个零收益日 (rets[0]=0)
    eq = [1_000_000.0, 1_010_000.0, 1_020_000.0, 1_030_000.0]
    summary = EngineSummary(
        initial_capital=eq[0],
        final_equity=eq[-1],
        total_return=0.03,
        n_events=3,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=eq,
    )

    result = converter.convert(summary)

    # rets = [0.0, +0.01, +0.01, +0.01] → 3 个 > 0 / 4 总数
    expected_win_rate = 3 / 4
    assert result.win_rate == pytest.approx(expected_win_rate, rel=1e-6)


def test_win_rate_with_losses() -> None:
    """含亏损日的 win_rate 计算。"""
    converter = ResultConverter()
    eq = [1_000_000.0, 1_100_000.0, 900_000.0, 1_050_000.0]
    summary = EngineSummary(
        initial_capital=eq[0],
        final_equity=eq[-1],
        total_return=0.05,
        n_events=3,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=eq,
    )

    result = converter.convert(summary)

    # rets = [0.0, +0.1, -0.18..., +0.16...] → 2 个 > 0 / 4 总数
    rets = result.daily_returns
    expected = sum(1 for r in rets if r > 0) / len(rets)
    assert result.win_rate == pytest.approx(expected, rel=1e-6)


# ============================================================
# 边界情况测试
# ============================================================

def test_convert_single_point_equity_curve() -> None:
    """单点 equity_curve (只有初始资金) → 所有指标为 0。"""
    converter = ResultConverter()
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=0,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=[1_000_000.0],
    )

    result = converter.convert(summary)

    assert result.total_return == 0.0
    assert result.annual_return == 0.0
    assert result.sharpe_ratio == 0.0
    assert result.max_drawdown == 0.0


def test_convert_two_point_equity_curve() -> None:
    """两点 equity_curve → 基础指标可计算。"""
    converter = ResultConverter()
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_100_000.0,
        total_return=0.1,
        n_events=1,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=[1_000_000.0, 1_100_000.0],
    )

    result = converter.convert(summary)

    assert result.total_return == pytest.approx(0.1, rel=1e-6)
    assert len(result.daily_returns) == 2


def test_convert_empty_trade_records() -> None:
    """无成交记录 → trade_count = 0, costs 为零列表。"""
    converter = ResultConverter()
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=3,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=[1_000_000.0] * 4,
        trade_records=[],
    )

    result = converter.convert(summary)

    assert result.trade_count == 0
    assert all(c == 0.0 for c in result.transaction_costs)
    assert all(c == 0.0 for c in result.hedge_costs)


def test_convert_with_fill_records() -> None:
    """含成交记录 → trade_count 正确统计。"""
    converter = ResultConverter()
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=3,
        n_orders_submitted=3,
        n_orders_filled=2,
        n_orders_rejected=1,
        equity_curve=[1_000_000.0] * 4,
        trade_records=[
            {"order_id": "o1", "status": "ALL_TRADED"},
            {"order_id": "o2", "status": "ALL_TRADED"},
            {"order_id": "o3", "status": "REJECTED"},
        ],
    )

    result = converter.convert(summary)

    assert result.trade_count == 2  # 只计 ALL_TRADED


# ============================================================
# _generate_dates 测试
# ============================================================

def test_generate_dates_length() -> None:
    """生成的日期长度 = n_points。"""
    converter = ResultConverter()
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=9,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=[1_000_000.0] * 10,
    )

    result = converter.convert(summary)

    assert len(result.dates) == 10


def test_generate_dates_are_timestamps() -> None:
    """生成的日期是 pd.Timestamp 类型。"""
    converter = ResultConverter()
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=2,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=[1_000_000.0] * 3,
    )

    result = converter.convert(summary)

    assert all(isinstance(d, pd.Timestamp) for d in result.dates)


def test_generate_dates_skip_weekends() -> None:
    """生成的日期跳过周末 (bdate_range)。"""
    converter = ResultConverter()
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=4,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=[1_000_000.0] * 5,
    )

    result = converter.convert(summary)

    # 所有日期都应是工作日 (周一至周五)
    for d in result.dates:
        assert d.weekday() < 5  # 0=Mon, 4=Fri


def test_generate_dates_custom_start() -> None:
    """自定义起始日期生效。"""
    config = ConversionConfig(default_start_date="2023-06-01")
    converter = ResultConverter(config=config)
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=1,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=[1_000_000.0, 1_000_000.0],
    )

    result = converter.convert(summary)

    assert result.dates[0] == pd.Timestamp("2023-06-01")


# ============================================================
# 集成测试: EventDrivenEngine → ResultConverter
# ============================================================

def test_end_to_end_engine_to_backtest_result() -> None:
    """端到端: 引擎运行 → EngineSummary → BacktestResult。"""
    from utils.backtest.event_driven_engine import EventDrivenEngine

    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        latency_model=FixedLatency(0),
        initial_capital=1_000_000.0,
        commission_rate=0.0,
    )
    events = [make_bar(close=100.0 + i) for i in range(10)]

    summary = engine.run(events)

    converter = ResultConverter()
    result = converter.convert(summary, name="e2e_test")

    assert isinstance(result, BacktestResult)
    assert result.name == "e2e_test"
    assert len(result.equity_curve) == 11  # 初始 + 10 事件
    assert result.n_days == 10
    assert result.total_return == 0.0  # 无交易


def test_end_to_end_with_trade() -> None:
    """端到端含交易: 引擎成交 → 转换 → 指标合理。"""
    from utils.backtest.event_driven_engine import EventDrivenEngine

    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        latency_model=FixedLatency(0),
        initial_capital=1_000_000.0,
        commission_rate=0.0,
    )

    # 第一个事件后提交买单
    engine.process_event(make_bar(close=100.0))
    engine.submit_order(make_buy_order(price=100.0, volume=100.0))

    # 后续 9 个事件,价格递增
    for i in range(1, 10):
        engine.process_event(make_bar(close=100.0 + i * 5, low=95.0, open=100.0 + i))

    summary = engine.get_summary()
    converter = ResultConverter()
    result = converter.convert(summary, name="with_trade")

    assert result.trade_count == 1
    assert result.total_return > 0  # 持仓增值
    assert len(result.equity_curve) == 11


def test_metrics_match_manual_calculation() -> None:
    """指标与手动计算一致 (公式验证)。"""
    converter = ResultConverter()
    eq = [1_000_000.0, 1_050_000.0, 1_020_000.0, 1_080_000.0]
    summary = EngineSummary(
        initial_capital=eq[0],
        final_equity=eq[-1],
        total_return=(eq[-1] - eq[0]) / eq[0],
        n_events=3,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=eq,
    )

    result = converter.convert(summary)

    # 手动计算
    eq_arr = np.array(eq)
    rets = np.array([0.0] + [
        (eq[i] - eq[i - 1]) / eq[i - 1] for i in range(1, len(eq))
    ])
    expected_total_return = (eq[-1] - eq[0]) / eq[0]
    n_years = 3 / TRADING_DAYS_PER_YEAR
    expected_annual_return = (1 + expected_total_return) ** (1 / max(n_years, 0.5)) - 1
    expected_vol = float(np.std(rets) * np.sqrt(TRADING_DAYS_PER_YEAR))
    expected_sharpe = (expected_annual_return - RISK_FREE_RATE) / max(expected_vol, 0.001)
    peak = np.maximum.accumulate(eq_arr)
    expected_dd = float(abs(np.min((eq_arr - peak) / peak)))

    assert result.total_return == pytest.approx(expected_total_return, rel=1e-6)
    assert result.annual_return == pytest.approx(expected_annual_return, rel=1e-6)
    assert result.annual_volatility == pytest.approx(expected_vol, rel=1e-6)
    assert result.sharpe_ratio == pytest.approx(expected_sharpe, rel=1e-6)
    assert result.max_drawdown == pytest.approx(expected_dd, rel=1e-6)


def test_converter_does_not_modify_summary() -> None:
    """转换器不修改入参 EngineSummary (不可变性)。"""
    converter = ResultConverter()
    original_curve = [1_000_000.0, 1_100_000.0]
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_100_000.0,
        total_return=0.1,
        n_events=1,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=list(original_curve),
    )

    converter.convert(summary)
    # 再次检查原始数据未被修改
    assert summary.equity_curve == original_curve


# ============================================================
# 手续费分配测试 (按 event_index)
# ============================================================

def test_commission_rate_from_config() -> None:
    """ConversionConfig 的 commission_rate 被用于计算手续费。"""
    config = ConversionConfig(commission_rate=0.0005)
    converter = ResultConverter(config=config)
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=5,
        n_orders_submitted=1,
        n_orders_filled=1,
        n_orders_rejected=0,
        equity_curve=[1_000_000.0] * 6,
        trade_records=[
            {"order_id": "o1", "status": "ALL_TRADED",
             "price": 100.0, "volume": 100.0, "event_index": 3},
        ],
    )

    result = converter.convert(summary)

    expected_commission = 100.0 * 100.0 * 0.0005  # = 5.0
    assert result.transaction_costs[3] == pytest.approx(expected_commission, rel=1e-6)
    assert result.transaction_costs[0] == 0.0
    assert result.total_transaction_cost == pytest.approx(expected_commission, rel=1e-6)


def test_transaction_costs_allocated_by_event_index() -> None:
    """多笔成交按 event_index 分配到不同日期。"""
    converter = ResultConverter()
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=9,
        n_orders_submitted=3,
        n_orders_filled=3,
        n_orders_rejected=0,
        equity_curve=[1_000_000.0] * 10,
        trade_records=[
            {"order_id": "o1", "status": "ALL_TRADED",
             "price": 100.0, "volume": 200.0, "event_index": 1},
            {"order_id": "o2", "status": "ALL_TRADED",
             "price": 110.0, "volume": 150.0, "event_index": 4},
            {"order_id": "o3", "status": "ALL_TRADED",
             "price": 120.0, "volume": 100.0, "event_index": 7},
        ],
    )

    result = converter.convert(summary)

    rate = 0.0003
    # event 1: 100*200*0.0003 = 6.0
    assert result.transaction_costs[1] == pytest.approx(100 * 200 * rate, rel=1e-6)
    # event 4: 110*150*0.0003 = 4.95
    assert result.transaction_costs[4] == pytest.approx(110 * 150 * rate, rel=1e-6)
    # event 7: 120*100*0.0003 = 3.6
    assert result.transaction_costs[7] == pytest.approx(120 * 100 * rate, rel=1e-6)
    # 其他日期为 0
    assert result.transaction_costs[2] == 0.0
    assert result.transaction_costs[3] == 0.0
    assert result.transaction_costs[5] == 0.0
    # 总成本 = 6.0 + 4.95 + 3.6 = 14.55
    total_expected = 100 * 200 * rate + 110 * 150 * rate + 120 * 100 * rate
    assert result.total_transaction_cost == pytest.approx(total_expected, rel=1e-6)


def test_transaction_costs_without_event_index_fallback() -> None:
    """无 event_index 的旧 trade_records 归入第 0 日 (向后兼容)。"""
    converter = ResultConverter()
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=3,
        n_orders_submitted=1,
        n_orders_filled=1,
        n_orders_rejected=0,
        equity_curve=[1_000_000.0] * 4,
        trade_records=[
            {"order_id": "old", "status": "ALL_TRADED",
             "price": 50.0, "volume": 200.0},  # 无 event_index
        ],
    )

    result = converter.convert(summary)

    rate = 0.0003
    expected = 50.0 * 200.0 * rate  # = 3.0
    assert result.transaction_costs[0] == pytest.approx(expected, rel=1e-6)
    assert result.transaction_costs[1] == 0.0


def test_transaction_costs_rejected_not_counted() -> None:
    """REJECTED 订单不计入手续费。"""
    converter = ResultConverter()
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=3,
        n_orders_submitted=2,
        n_orders_filled=1,
        n_orders_rejected=1,
        equity_curve=[1_000_000.0] * 4,
        trade_records=[
            {"order_id": "o1", "status": "ALL_TRADED",
             "price": 100.0, "volume": 100.0, "event_index": 2},
            {"order_id": "o2", "status": "REJECTED",
             "price": 100.0, "volume": 500.0, "event_index": 2},
        ],
    )

    result = converter.convert(summary)

    rate = 0.0003
    expected = 100.0 * 100.0 * rate  # = 3.0 (只有 ALL_TRADED)
    assert result.transaction_costs[2] == pytest.approx(expected, rel=1e-6)
    assert result.total_transaction_cost == pytest.approx(expected, rel=1e-6)


# ============================================================
# yearly_stats 逐年统计测试
# ============================================================

def test_yearly_stats_basic() -> None:
    """逐年统计: 一年 252 天数据, 产出 1 条 yearly_stats。"""
    converter = ResultConverter()
    eq = [1_000_000.0]
    for _i in range(252):
        eq.append(eq[-1] * (1 + 0.001))  # 每日 ~0.1% 收益

    summary = EngineSummary(
        initial_capital=eq[0],
        final_equity=eq[-1],
        total_return=(eq[-1] - eq[0]) / eq[0],
        n_events=252,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=eq,
    )

    result = converter.convert(summary)

    assert len(result.yearly_stats) == 1
    ys = result.yearly_stats[0]
    assert ys["year"] == 2021  # default_start_date 2021-01-04
    assert "return" in ys
    assert "volatility" in ys
    assert "max_drawdown" in ys
    assert "csi300_return" in ys
    assert "market_type" in ys
    # 手动验证年度收益率
    eq_arr = np.array(eq)
    expected_yr = (eq_arr[-1] - eq_arr[0]) / eq_arr[0]
    assert ys["return"] == pytest.approx(expected_yr, rel=1e-6)


def test_yearly_stats_manual_calculation() -> None:
    """逐年统计指标与手动计算一致。"""
    converter = ResultConverter()
    # 253 个点, 2021 全年
    eq = [1_000_000.0]
    for i in range(252):
        eq.append(eq[-1] * (1 + 0.002 * (1 if i % 2 == 0 else -1)))

    summary = EngineSummary(
        initial_capital=eq[0],
        final_equity=eq[-1],
        total_return=(eq[-1] - eq[0]) / eq[0],
        n_events=252,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=eq,
    )

    result = converter.convert(summary)

    assert len(result.yearly_stats) == 1
    ys = result.yearly_stats[0]

    # 手动计算 (与 _calc_yearly_stats 公式一致)
    eq_arr = np.array(eq)
    rets_arr = np.array(result.daily_returns)
    all_idxs = list(range(len(eq)))  # 单年数据, 所有索引
    expected_yr = float((eq_arr[-1] - eq_arr[0]) / eq_arr[0])
    expected_yv = float(np.std(rets_arr[all_idxs]) * np.sqrt(252))
    yeq = eq_arr[all_idxs[0] : all_idxs[-1] + 1]
    peak = np.maximum.accumulate(yeq)
    expected_ydd = float(abs(np.min((yeq - peak) / peak)))

    assert ys["return"] == pytest.approx(expected_yr, rel=1e-6)
    assert ys["volatility"] == pytest.approx(expected_yv, rel=1e-6)
    assert ys["max_drawdown"] == pytest.approx(expected_ydd, rel=1e-6)


def test_yearly_stats_with_csi300() -> None:
    """含 CSI300 数据时正确计算 csi300_return 和 market_type。"""
    converter = ResultConverter()
    eq = [1_000_000.0]
    for _i in range(252):
        eq.append(eq[-1] * (1 + 0.001))

    summary = EngineSummary(
        initial_capital=eq[0],
        final_equity=eq[-1],
        total_return=(eq[-1] - eq[0]) / eq[0],
        n_events=252,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=eq,
    )

    # 构造 CSI300 日收益率: 牛市 (累积 > 15%)
    csi_rets = [0.001] * 253  # 每日 0.1%, 累积 ~25%

    result = converter.convert(summary, csi300_returns=csi_rets)

    assert len(result.yearly_stats) == 1
    ys = result.yearly_stats[0]
    assert ys["market_type"] == "牛市"
    expected_csi = float(np.prod(1 + np.array(csi_rets)) - 1)
    assert ys["csi300_return"] == pytest.approx(expected_csi, rel=1e-6)


def test_yearly_stats_bear_market() -> None:
    """CSI300 熊市时 market_type 正确标记。"""
    converter = ResultConverter()
    eq = [1_000_000.0] * 253  # 平盘

    summary = EngineSummary(
        initial_capital=eq[0],
        final_equity=eq[-1],
        total_return=0.0,
        n_events=252,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=eq,
    )

    # 熊市: 每日 -0.2%, 累积大幅下跌
    csi_rets = [-0.002] * 253

    result = converter.convert(summary, csi300_returns=csi_rets)

    ys = result.yearly_stats[0]
    assert ys["market_type"] == "熊市"
    assert ys["csi300_return"] < -0.05


def test_yearly_stats_min_days_threshold() -> None:
    """同一年份 < 10 个交易日跳过 (不生成 yearly_stat)。"""
    converter = ResultConverter()
    # 只有 5 天数据 (远低于 10 天门槛)
    eq = [1_000_000.0, 1_010_000.0, 1_020_000.0, 1_030_000.0, 1_040_000.0, 1_050_000.0]

    summary = EngineSummary(
        initial_capital=eq[0],
        final_equity=eq[-1],
        total_return=(eq[-1] - eq[0]) / eq[0],
        n_events=5,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=eq,
    )

    result = converter.convert(summary)

    assert result.yearly_stats == []


def test_yearly_stats_no_csi300_defaults() -> None:
    """无 CSI300 数据时, csi300_return=0, market_type="震荡市"。"""
    converter = ResultConverter()
    eq = [1_000_000.0]
    for _i in range(252):
        eq.append(eq[-1] * (1 + 0.0005))

    summary = EngineSummary(
        initial_capital=eq[0],
        final_equity=eq[-1],
        total_return=(eq[-1] - eq[0]) / eq[0],
        n_events=252,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=eq,
    )

    result_no_csi = converter.convert(summary)

    assert len(result_no_csi.yearly_stats) == 1
    ys = result_no_csi.yearly_stats[0]
    assert ys["csi300_return"] == 0.0
    assert ys["market_type"] == "震荡市"


def test_yearly_stats_short_series_no_stats() -> None:
    """极短序列 (2 个点) 不产生 yearly_stats。"""
    converter = ResultConverter()
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_100_000.0,
        total_return=0.1,
        n_events=1,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=[1_000_000.0, 1_100_000.0],
    )

    result = converter.convert(summary)

    assert result.yearly_stats == []


# ============================================================
# 换手率统计测试
# ============================================================

def test_turnover_daily_from_trade_records() -> None:
    """换手率 = sum(price * volume) / equity[event_index]。"""
    converter = ResultConverter()
    eq = [1_000_000.0] * 6
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=5,
        n_orders_submitted=2,
        n_orders_filled=2,
        n_orders_rejected=0,
        equity_curve=eq,
        trade_records=[
            {"order_id": "o1", "status": "ALL_TRADED",
             "price": 100.0, "volume": 500.0, "event_index": 2},
            {"order_id": "o2", "status": "ALL_TRADED",
             "price": 105.0, "volume": 300.0, "event_index": 4},
        ],
    )

    result = converter.convert(summary)

    # event 2: 100*500 / 1_000_000 = 0.05
    assert result.turnover_daily[2] == pytest.approx(0.05, rel=1e-6)
    # event 4: 105*300 / 1_000_000 = 0.0315
    assert result.turnover_daily[4] == pytest.approx(0.0315, rel=1e-6)
    # 其他日期为 0
    assert result.turnover_daily[0] == 0.0
    assert result.turnover_daily[1] == 0.0
    assert result.turnover_daily[3] == 0.0


def test_turnover_daily_same_day_aggregation() -> None:
    """同一 event_index 的多笔成交聚合换手率。"""
    converter = ResultConverter()
    eq = [1_000_000.0] * 4
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=3,
        n_orders_submitted=3,
        n_orders_filled=3,
        n_orders_rejected=0,
        equity_curve=eq,
        trade_records=[
            {"order_id": "o1", "status": "ALL_TRADED",
             "price": 100.0, "volume": 200.0, "event_index": 1},
            {"order_id": "o2", "status": "ALL_TRADED",
             "price": 110.0, "volume": 150.0, "event_index": 1},
            {"order_id": "o3", "status": "ALL_TRADED",
             "price": 120.0, "volume": 100.0, "event_index": 2},
        ],
    )

    result = converter.convert(summary)

    # Day 1: (100*200 + 110*150) / 1_000_000 = (20000+16500)/1e6 = 0.0365
    expected_day1 = (100 * 200 + 110 * 150) / 1_000_000
    assert result.turnover_daily[1] == pytest.approx(expected_day1, rel=1e-6)
    # Day 2: 120*100 / 1_000_000 = 0.012
    assert result.turnover_daily[2] == pytest.approx(0.012, rel=1e-6)


def test_turnover_daily_rejected_not_counted() -> None:
    """REJECTED 订单不计入换手率。"""
    converter = ResultConverter()
    eq = [1_000_000.0] * 4
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=3,
        n_orders_submitted=2,
        n_orders_filled=1,
        n_orders_rejected=1,
        equity_curve=eq,
        trade_records=[
            {"order_id": "o1", "status": "ALL_TRADED",
             "price": 100.0, "volume": 500.0, "event_index": 2},
            {"order_id": "o2", "status": "REJECTED",
             "price": 100.0, "volume": 10000.0, "event_index": 2},
        ],
    )

    result = converter.convert(summary)

    # 只有 ALL_TRADED: 100*500/1e6 = 0.05
    assert result.turnover_daily[2] == pytest.approx(0.05, rel=1e-6)
    assert result.annual_turnover > 0


def test_turnover_daily_fallback_to_day0() -> None:
    """无 event_index 的旧成交归入 Day 0 换手率。"""
    converter = ResultConverter()
    eq = [1_000_000.0] * 3
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=2,
        n_orders_submitted=1,
        n_orders_filled=1,
        n_orders_rejected=0,
        equity_curve=eq,
        trade_records=[
            {"order_id": "old", "status": "ALL_TRADED",
             "price": 80.0, "volume": 250.0},
        ],
    )

    result = converter.convert(summary)

    # 80*250 / 1_000_000 = 0.02, 归入 Day 0
    assert result.turnover_daily[0] == pytest.approx(0.02, rel=1e-6)
    assert result.turnover_daily[1] == 0.0
    assert result.turnover_daily[2] == 0.0


def test_annual_turnover_formula() -> None:
    """annual_turnover = sum(turnover_daily) * (252 / n_points)。"""
    converter = ResultConverter()
    eq = [1_000_000.0] * 11
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=10,
        n_orders_submitted=2,
        n_orders_filled=2,
        n_orders_rejected=0,
        equity_curve=eq,
        trade_records=[
            {"order_id": "o1", "status": "ALL_TRADED",
             "price": 100.0, "volume": 1000.0, "event_index": 3},
            {"order_id": "o2", "status": "ALL_TRADED",
             "price": 100.0, "volume": 1000.0, "event_index": 7},
        ],
    )

    result = converter.convert(summary)

    # sum = 0.1 + 0.1 = 0.2, n_points = 11
    expected_annual = 0.2 * (252.0 / 11)
    assert result.annual_turnover == pytest.approx(expected_annual, rel=1e-6)


def test_window_turnover_formula() -> None:
    """window_turnover = 最近 WINDOW(20) 日换手率均值。"""
    config = ConversionConfig(turnover_window=20)
    converter = ResultConverter(config=config)
    eq = [1_000_000.0] * 30
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=29,
        n_orders_submitted=2,
        n_orders_filled=2,
        n_orders_rejected=0,
        equity_curve=eq,
        trade_records=[
            {"order_id": "o1", "status": "ALL_TRADED",
             "price": 100.0, "volume": 500.0, "event_index": 5},
            {"order_id": "o2", "status": "ALL_TRADED",
             "price": 100.0, "volume": 500.0, "event_index": 25},
        ],
    )

    result = converter.convert(summary)

    td = np.array(result.turnover_daily)
    # window = 20, len(td)=30, td[-20:] 包含 event 25 的 0.05
    expected_window = float(np.sum(td[-20:]) / len(td[-20:]))
    assert result.window_turnover == pytest.approx(expected_window, rel=1e-6)


def test_turnover_no_trades_zero() -> None:
    """无成交 → 换手率全零, annual_turnover=0, window_turnover=0。"""
    converter = ResultConverter()
    summary = EngineSummary(
        initial_capital=1_000_000.0,
        final_equity=1_000_000.0,
        total_return=0.0,
        n_events=10,
        n_orders_submitted=0,
        n_orders_filled=0,
        n_orders_rejected=0,
        equity_curve=[1_000_000.0] * 11,
        trade_records=[],
    )

    result = converter.convert(summary)

    assert all(t == 0.0 for t in result.turnover_daily)
    assert result.annual_turnover == 0.0
    assert result.window_turnover == 0.0


def test_yearly_stats_turnover_field() -> None:
    """yearly_stats 每条记录含 turnover 字段。"""
    converter = ResultConverter()
    eq = [1_000_000.0]
    for _i in range(252):
        eq.append(eq[-1] * (1 + 0.001))

    summary = EngineSummary(
        initial_capital=eq[0],
        final_equity=eq[-1],
        total_return=(eq[-1] - eq[0]) / eq[0],
        n_events=252,
        n_orders_submitted=2,
        n_orders_filled=2,
        n_orders_rejected=0,
        equity_curve=eq,
        trade_records=[
            {"order_id": "o1", "status": "ALL_TRADED",
             "price": 100.0, "volume": 500.0, "event_index": 50},
            {"order_id": "o2", "status": "ALL_TRADED",
             "price": 100.0, "volume": 500.0, "event_index": 200},
        ],
    )

    result = converter.convert(summary)

    assert len(result.yearly_stats) == 1
    ys = result.yearly_stats[0]
    assert "turnover" in ys
    assert isinstance(ys["turnover"], float)
    # 年度换手率 = 该年所有日换手率的均值
    expected_year_td = np.mean([result.turnover_daily[j] for j in range(len(eq))])
    assert ys["turnover"] == pytest.approx(expected_year_td, rel=1e-6)


def test_turnover_with_equity_curve_variation() -> None:
    """权益曲线变化时,换手率按当日权益值计算。"""
    converter = ResultConverter()
    # 权益从 1M 渐增到 2M
    eq = [1_000_000.0 + i * 1000.0 for i in range(11)]
    summary = EngineSummary(
        initial_capital=eq[0],
        final_equity=eq[-1],
        total_return=(eq[-1] - eq[0]) / eq[0],
        n_events=10,
        n_orders_submitted=1,
        n_orders_filled=1,
        n_orders_rejected=0,
        equity_curve=eq,
        trade_records=[
            {"order_id": "o1", "status": "ALL_TRADED",
             "price": 100.0, "volume": 1_000.0, "event_index": 5},
        ],
    )

    result = converter.convert(summary)

    # event 5 的权益值 = 1_005_000
    expected = 100.0 * 1_000.0 / 1_005_000.0
    assert result.turnover_daily[5] == pytest.approx(expected, rel=1e-6)
