"""triple_barrier 单元测试 — López de Prado AFML Ch.3 (2026-08-19).

测试覆盖:
    1. 固定障碍标注 — 止盈/止损/超时
    2. 波动率自适应障碍
    3. 做空方向
    4. Meta-Labeling
    5. 边界条件
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from utils.backtest.triple_barrier import (
    BarrierType,
    TripleBarrierConfig,
    TripleBarrierLabeler,
    meta_labeling,
)

# ============================================================
# 固定障碍标注测试
# ============================================================

class TestFixedBarrierLabeling:
    """固定障碍标注测试."""

    def test_upper_barrier_hit(self):
        """价格触及上障碍 → 标签 +1."""
        prices = pd.Series(
            [100, 101, 103, 105, 102],
            index=pd.date_range("2026-08-01", periods=5, freq="B"),
        )
        events = pd.DataFrame(index=[prices.index[0]])
        events["side"] = 1

        labeler = TripleBarrierLabeler(
            TripleBarrierConfig(
                profit_taking_width=0.02,
                stop_loss_width=0.01,
                num_bars=4,
            )
        )
        result = labeler.label(prices, events)

        assert len(result.labels) == 1
        assert result.labels.iloc[0] == 1, "触及上障碍应标签 +1"
        assert result.events[0].hit_barrier == BarrierType.UPPER

    def test_lower_barrier_hit(self):
        """价格触及下障碍 → 标签 -1."""
        prices = pd.Series(
            [100, 99, 98, 96, 97],
            index=pd.date_range("2026-08-01", periods=5, freq="B"),
        )
        events = pd.DataFrame(index=[prices.index[0]])
        events["side"] = 1

        labeler = TripleBarrierLabeler(
            TripleBarrierConfig(
                profit_taking_width=0.05,
                stop_loss_width=0.02,
                num_bars=4,
            )
        )
        result = labeler.label(prices, events)

        assert result.labels.iloc[0] == -1, "触及下障碍应标签 -1"
        assert result.events[0].hit_barrier == BarrierType.LOWER

    def test_vertical_barrier_hit_zero_mode(self):
        """价格未触及上下障碍, 垂直障碍触碰 → 标签 0 (zero 模式)."""
        prices = pd.Series(
            [100, 100.5, 100.3, 100.2, 100.1],
            index=pd.date_range("2026-08-01", periods=5, freq="B"),
        )
        events = pd.DataFrame(index=[prices.index[0]])
        events["side"] = 1

        labeler = TripleBarrierLabeler(
            TripleBarrierConfig(
                profit_taking_width=0.05,
                stop_loss_width=0.05,
                num_bars=4,
                vertical_label_mode="zero",
            )
        )
        result = labeler.label(prices, events)

        assert result.labels.iloc[0] == 0, "垂直障碍 zero 模式应标签 0"
        assert result.events[0].hit_barrier == BarrierType.VERTICAL

    def test_vertical_barrier_hit_sign_mode(self):
        """垂直障碍触碰, sign 模式 → 标签为收益符号."""
        prices = pd.Series(
            [100, 101, 102, 103, 104],
            index=pd.date_range("2026-08-01", periods=5, freq="B"),
        )
        events = pd.DataFrame(index=[prices.index[0]])
        events["side"] = 1

        labeler = TripleBarrierLabeler(
            TripleBarrierConfig(
                profit_taking_width=0.10,
                stop_loss_width=0.10,
                num_bars=4,
                vertical_label_mode="sign",
            )
        )
        result = labeler.label(prices, events)

        assert result.labels.iloc[0] == 1, "正收益 sign 模式应标签 +1"

    def test_multiple_events(self):
        """多个事件应全部标注."""
        dates = pd.date_range("2026-08-01", periods=20, freq="B")
        prices = pd.Series(np.random.RandomState(42).normal(100, 2, 20).cumsum() + 100, index=dates)

        event_dates = dates[:10]
        events = pd.DataFrame(index=event_dates)
        events["side"] = 1

        labeler = TripleBarrierLabeler(
            TripleBarrierConfig(num_bars=5, profit_taking_width=0.03, stop_loss_width=0.02)
        )
        result = labeler.label(prices, events)

        assert len(result.labels) == 10
        assert all(label in (-1, 0, 1) for label in result.labels.values)

    def test_label_counts(self):
        """标签计数应正确."""
        dates = pd.date_range("2026-08-01", periods=30, freq="B")
        rng = np.random.RandomState(123)
        prices = pd.Series(100 + rng.randn(30).cumsum(), index=dates)

        events = pd.DataFrame(index=dates[:25])
        events["side"] = 1

        labeler = TripleBarrierLabeler(
            TripleBarrierConfig(num_bars=3, profit_taking_width=0.02, stop_loss_width=0.02)
        )
        result = labeler.label(prices, events)

        counts = result.label_counts
        total = counts["profit (+1)"] + counts["loss (-1)"] + counts["timeout (0)"]
        assert total == len(result.labels)


# ============================================================
# 波动率自适应障碍测试
# ============================================================

class TestVolatilityAdjustedBarrier:
    """波动率自适应障碍测试."""

    def test_volatility_adjusted_labeling(self):
        """波动率自适应障碍应正常标注."""
        dates = pd.date_range("2026-08-01", periods=50, freq="B")
        rng = np.random.RandomState(42)
        returns = rng.normal(0, 0.02, 50)
        prices = pd.Series(100 * np.exp(returns.cumsum()), index=dates)

        events = pd.DataFrame(index=dates[:40])
        events["side"] = 1

        labeler = TripleBarrierLabeler(
            TripleBarrierConfig(
                use_volatility=True,
                profit_taking_scalar=2.0,
                stop_loss_scalar=1.0,
                num_bars=5,
                volatility_window=10,
            )
        )
        result = labeler.label(prices, events)

        assert len(result.labels) > 0
        assert all(label in (-1, 0, 1) for label in result.labels.values)

    def test_higher_volatility_wider_barriers(self):
        """高波动率应产生更宽的障碍."""
        labeler = TripleBarrierLabeler(
            TripleBarrierConfig(
                use_volatility=True,
                profit_taking_scalar=2.0,
                stop_loss_scalar=1.0,
            )
        )

        low_vol_widths = labeler._get_barrier_widths(100, volatility=0.01)
        high_vol_widths = labeler._get_barrier_widths(100, volatility=0.05)

        assert high_vol_widths[0] > low_vol_widths[0], "高波动率上障碍应更宽"
        assert high_vol_widths[1] > low_vol_widths[1], "高波动率下障碍应更宽"


# ============================================================
# 做空方向测试
# ============================================================

class TestShortSideLabeling:
    """做空方向标注测试."""

    def test_short_upper_barrier_hit(self):
        """做空时价格下跌触及上障碍 (止盈) → 标签 +1."""
        prices = pd.Series(
            [100, 99, 97, 95, 96],
            index=pd.date_range("2026-08-01", periods=5, freq="B"),
        )
        events = pd.DataFrame(index=[prices.index[0]])
        events["side"] = -1

        labeler = TripleBarrierLabeler(
            TripleBarrierConfig(
                profit_taking_width=0.02,
                stop_loss_width=0.01,
                num_bars=4,
            )
        )
        result = labeler.label(prices, events)

        assert result.labels.iloc[0] == 1, "做空止盈应标签 +1"
        assert result.events[0].hit_barrier == BarrierType.UPPER

    def test_short_lower_barrier_hit(self):
        """做空时价格上涨触及下障碍 (止损) → 标签 -1."""
        prices = pd.Series(
            [100, 101, 103, 105, 104],
            index=pd.date_range("2026-08-01", periods=5, freq="B"),
        )
        events = pd.DataFrame(index=[prices.index[0]])
        events["side"] = -1

        labeler = TripleBarrierLabeler(
            TripleBarrierConfig(
                profit_taking_width=0.05,
                stop_loss_width=0.02,
                num_bars=4,
            )
        )
        result = labeler.label(prices, events)

        assert result.labels.iloc[0] == -1, "做空止损应标签 -1"
        assert result.events[0].hit_barrier == BarrierType.LOWER


# ============================================================
# Meta-Labeling 测试
# ============================================================

class TestMetaLabeling:
    """Meta-Labeling (二级分类器) 测试."""

    def test_meta_labeling_basic(self):
        """Meta-Labeling 基本功能."""
        dates = pd.date_range("2026-08-01", periods=20, freq="B")
        prices = pd.Series(
            [100, 102, 101, 103, 105, 104, 106, 108, 107, 109,
             111, 110, 112, 114, 113, 115, 117, 116, 118, 120],
            index=dates,
        )

        primary = pd.Series([1, 1, -1, 1, 1, -1, 1, 1, -1, 1], index=dates[:10])

        meta = meta_labeling(primary, prices)

        assert len(meta) > 0
        assert all(label in (-1, 1) for label in meta.dropna().values)

    def test_meta_labeling_all_correct(self):
        """主策略全正确时 meta 应全 +1."""
        dates = pd.date_range("2026-08-01", periods=10, freq="B")
        prices = pd.Series(
            [100, 102, 103, 105, 106, 108, 109, 111, 112, 114],
            index=dates,
        )

        primary = pd.Series([1, 1, 1, 1, 1], index=dates[:5])

        meta = meta_labeling(
            primary, prices,
            TripleBarrierConfig(profit_taking_width=0.02, stop_loss_width=0.05, num_bars=4)
        )

        valid = meta.dropna()
        assert len(valid) > 0
        assert all(v == 1 for v in valid.values), "全正确策略 meta 应全 +1"


# ============================================================
# 边界条件测试
# ============================================================

class TestEdgeCases:
    """边界条件测试."""

    def test_empty_events(self):
        """空事件应返回空标签."""
        prices = pd.Series([100, 101, 102], index=pd.date_range("2026-08-01", periods=3))
        events = pd.DataFrame(index=pd.DatetimeIndex([]))
        events["side"] = []

        labeler = TripleBarrierLabeler()
        result = labeler.label(prices, events)

        assert len(result.labels) == 0

    def test_event_not_in_prices(self):
        """事件日期不在价格序列中应跳过."""
        prices = pd.Series([100, 101, 102], index=pd.date_range("2026-08-01", periods=3))
        events = pd.DataFrame(index=pd.DatetimeIndex(["2026-09-01"]))
        events["side"] = 1

        labeler = TripleBarrierLabeler()
        result = labeler.label(prices, events)

        assert len(result.labels) == 0

    def test_event_at_last_price(self):
        """事件在最后一个价格点应跳过 (无后续价格)."""
        prices = pd.Series([100, 101, 102], index=pd.date_range("2026-08-01", periods=3))
        events = pd.DataFrame(index=[prices.index[-1]])
        events["side"] = 1

        labeler = TripleBarrierLabeler()
        result = labeler.label(prices, events)

        assert len(result.labels) == 0

    def test_label_simple(self):
        """label_simple 应对每个价格点标注."""
        dates = pd.date_range("2026-08-01", periods=20, freq="B")
        prices = pd.Series(100 + np.arange(20), index=dates)

        labeler = TripleBarrierLabeler(
            TripleBarrierConfig(num_bars=3, profit_taking_width=0.02, stop_loss_width=0.02)
        )
        result = labeler.label_simple(prices)

        assert len(result.labels) > 0
        assert all(label in (-1, 0, 1) for label in result.labels.values)

    def test_hit_rate(self):
        """hit_rate 应是各标签的比例."""
        dates = pd.date_range("2026-08-01", periods=30, freq="B")
        rng = np.random.RandomState(456)
        prices = pd.Series(100 + rng.randn(30).cumsum() * 2, index=dates)

        events = pd.DataFrame(index=dates[:25])
        events["side"] = 1

        labeler = TripleBarrierLabeler(
            TripleBarrierConfig(num_bars=3, profit_taking_width=0.02, stop_loss_width=0.02)
        )
        result = labeler.label(prices, events)

        rates = result.hit_rate
        total_rate = sum(rates.values())
        assert abs(total_rate - 1.0) < 1e-10 or total_rate == 0.0

    def test_barrier_event_fields(self):
        """BarrierEvent 应包含完整字段."""
        prices = pd.Series(
            [100, 103, 101],
            index=pd.date_range("2026-08-01", periods=3, freq="B"),
        )
        events = pd.DataFrame(index=[prices.index[0]])
        events["side"] = 1

        labeler = TripleBarrierLabeler(
            TripleBarrierConfig(profit_taking_width=0.02, stop_loss_width=0.05, num_bars=2)
        )
        result = labeler.label(prices, events)

        event = result.events[0]
        assert event.entry_price == 100.0
        assert event.upper_barrier > 100.0
        assert event.lower_barrier < 100.0
        assert event.hit_time > 0
        assert event.hit_barrier in (BarrierType.UPPER, BarrierType.LOWER, BarrierType.VERTICAL)
