"""Triple-Barrier Labeling — López de Prado AFML 第 3 章 (P1 改进, 2026-08-19).

================================================================
三重障碍标注法: 用路径依赖的障碍触碰判定标签, 替代固定持有期标注。

三重障碍:
    1. 上障碍 (止盈): entry_price × (1 + profit_width)
    2. 下障碍 (止损): entry_price × (1 - stop_width)
    3. 垂直障碍 (时间限制): entry 后 N 个 bar

标签判定:
    - 上障碍先触碰 → +1 (盈利)
    - 下障碍先触碰 → -1 (亏损)
    - 垂直障碍先触碰 → 0 (超时) 或触碰时收益的符号

障碍宽度可固定或波动率自适应:
    - 固定: profit_width = 0.02, stop_width = 0.01
    - 自适应: profit_width = volatility × profit_scalar, stop_width = volatility × stop_scalar

参考:
    - López de Prado, "Advances in Financial Machine Learning" Ch.3
    - cairn/Reference/douban-book-summaries-20260819.md §二.1
    - cairn/self-evolution-framework.md §九.2 改进 3

设计原则:
    - 向量化实现 (numpy), 支持批量标注
    - 支持 fixed 和 dynamic (volatility-adjusted) 两种模式
    - 返回标签 + 触碰信息 (哪个障碍/何时触碰/触碰价格)
    - fail-safe: 数据不足返回 NaN
================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("triple_barrier")


class BarrierType(Enum):
    """障碍类型."""

    UPPER = "upper"
    LOWER = "lower"
    VERTICAL = "vertical"
    NONE = "none"


@dataclass
class BarrierEvent:
    """单次障碍触碰事件."""

    timestamp: str
    entry_price: float
    upper_barrier: float
    lower_barrier: float
    vertical_barriers: int
    hit_barrier: BarrierType
    hit_time: int
    hit_price: float
    label: int
    return_at_hit: float


@dataclass
class TripleBarrierConfig:
    """三重障碍配置.

    Attributes:
        profit_taking_width: 上障碍宽度 (固定模式, 如 0.02 = 2%)
        stop_loss_width: 下障碍宽度 (固定模式, 如 0.01 = 1%)
        num_bars: 垂直障碍 (bar 数, 如 5 = 5 个 bar 后超时)
        use_volatility: 是否使用波动率自适应障碍
        profit_taking_scalar: 波动率自适应时上障碍的标量 (如 2.0)
        stop_loss_scalar: 波动率自适应时下障碍的标量 (如 1.0)
        volatility_window: 波动率计算窗口 (如 20)
        vertical_label_mode: 垂直障碍触碰时的标签模式
            "sign" → 触碰时收益的符号
            "zero" → 固定 0
    """

    profit_taking_width: float = 0.02
    stop_loss_width: float = 0.01
    num_bars: int = 5
    use_volatility: bool = False
    profit_taking_scalar: float = 2.0
    stop_loss_scalar: float = 1.0
    volatility_window: int = 20
    vertical_label_mode: str = "sign"


@dataclass
class TripleBarrierResult:
    """三重障碍标注结果."""

    labels: pd.Series
    events: list[BarrierEvent] = field(default_factory=list)
    config: Optional[TripleBarrierConfig] = None

    @property
    def label_counts(self) -> dict[str, int]:
        counts = self.labels.value_counts().to_dict()
        return {
            "profit (+1)": counts.get(1, 0),
            "loss (-1)": counts.get(-1, 0),
            "timeout (0)": counts.get(0, 0),
        }

    @property
    def hit_rate(self) -> dict[str, float]:
        total = len(self.labels)
        if total == 0:
            return {}
        counts = self.label_counts
        return {k: v / total for k, v in counts.items()}


class TripleBarrierLabeler:
    """三重障碍标注器 — López de Prado AFML Ch.3.

    用法:
        # 固定障碍
        labeler = TripleBarrierLabeler(
            config=TripleBarrierConfig(
                profit_taking_width=0.02,
                stop_loss_width=0.01,
                num_bars=5,
            )
        )
        result = labeler.label(prices, events)

        # 波动率自适应障碍
        labeler = TripleBarrierLabeler(
            config=TripleBarrierConfig(
                use_volatility=True,
                profit_taking_scalar=2.0,
                stop_loss_scalar=1.0,
                num_bars=10,
            )
        )
        result = labeler.label(prices, events)
    """

    def __init__(self, config: Optional[TripleBarrierConfig] = None) -> None:
        self.config = config or TripleBarrierConfig()

    def _compute_volatility(self, prices: pd.Series) -> pd.Series:
        """计算滚动波动率 (日收益率的标准差)."""
        returns = prices.pct_change()
        vol = returns.rolling(
            window=self.config.volatility_window, min_periods=1
        ).std()
        vol = vol.fillna(returns.std())
        return vol

    def _get_barrier_widths(
        self,
        entry_price: float,
        volatility: Optional[float] = None,
    ) -> tuple[float, float]:
        """获取上/下障碍宽度.

        Returns:
            (profit_width, stop_width)
        """
        if self.config.use_volatility and volatility is not None and volatility > 0:
            profit_width = volatility * self.config.profit_taking_scalar
            stop_width = volatility * self.config.stop_loss_scalar
        else:
            profit_width = self.config.profit_taking_width
            stop_width = self.config.stop_loss_width
        return profit_width, stop_width

    def label(
        self,
        prices: pd.Series,
        events: pd.DataFrame,
        volatility: Optional[pd.Series] = None,
    ) -> TripleBarrierResult:
        """对事件进行三重障碍标注.

        Args:
            prices: 价格序列 (index = date, value = close price)
            events: 事件 DataFrame, index = entry date, 需包含列:
                - 'side' (可选): 方向 (+1 做多, -1 做空, 默认 +1)
            volatility: 波动率序列 (index = date, value = vol)
                None 时自动计算 (use_volatility=True 时)

        Returns:
            TripleBarrierResult
        """
        config = self.config

        if config.use_volatility and volatility is None:
            volatility = self._compute_volatility(prices)

        price_dates = prices.index
        price_values = prices.values
        n_prices = len(price_values)

        labels: dict[str, int] = {}
        event_list: list[BarrierEvent] = []

        for event_date, event_row in events.iterrows():
            if event_date not in price_dates:
                continue

            entry_idx = price_dates.get_loc(event_date)
            if entry_idx >= n_prices - 1:
                continue

            entry_price = price_values[entry_idx]
            side = event_row.get("side", 1) if isinstance(event_row, pd.Series) else 1

            vol_val = None
            if config.use_volatility and volatility is not None:
                if event_date in volatility.index:
                    vol_val = volatility[event_date]

            profit_width, stop_width = self._get_barrier_widths(
                entry_price, vol_val
            )

            upper_barrier = entry_price * (1 + profit_width * side)
            lower_barrier = entry_price * (1 - stop_width * side)
            vertical_end = min(entry_idx + config.num_bars, n_prices - 1)

            hit_barrier = BarrierType.VERTICAL
            hit_time = config.num_bars
            hit_price = price_values[vertical_end]

            for i in range(entry_idx + 1, vertical_end + 1):
                current_price = price_values[i]

                if side > 0:
                    if current_price >= upper_barrier:
                        hit_barrier = BarrierType.UPPER
                        hit_time = i - entry_idx
                        hit_price = current_price
                        break
                    if current_price <= lower_barrier:
                        hit_barrier = BarrierType.LOWER
                        hit_time = i - entry_idx
                        hit_price = current_price
                        break
                else:
                    if current_price <= upper_barrier:
                        hit_barrier = BarrierType.UPPER
                        hit_time = i - entry_idx
                        hit_price = current_price
                        break
                    if current_price >= lower_barrier:
                        hit_barrier = BarrierType.LOWER
                        hit_time = i - entry_idx
                        hit_price = current_price
                        break

            return_at_hit = (hit_price - entry_price) / entry_price * side

            if hit_barrier == BarrierType.UPPER:
                label = 1
            elif hit_barrier == BarrierType.LOWER:
                label = -1
            else:
                if config.vertical_label_mode == "zero":
                    label = 0
                else:
                    label = int(np.sign(return_at_hit)) if return_at_hit != 0 else 0

            date_str = str(event_date)
            labels[date_str] = label

            event_list.append(
                BarrierEvent(
                    timestamp=date_str,
                    entry_price=float(entry_price),
                    upper_barrier=float(upper_barrier),
                    lower_barrier=float(lower_barrier),
                    vertical_barriers=config.num_bars,
                    hit_barrier=hit_barrier,
                    hit_time=hit_time,
                    hit_price=float(hit_price),
                    label=label,
                    return_at_hit=float(return_at_hit),
                )
            )

        labels_series = pd.Series(labels, name="label")
        labels_series.index.name = "date"

        result = TripleBarrierResult(
            labels=labels_series,
            events=event_list,
            config=config,
        )

        logger.info(
            "Triple-Barrier 标注完成: %d 个事件, %s",
            len(labels_series),
            result.label_counts,
        )
        return result

    def label_simple(
        self,
        prices: pd.Series,
        num_bars: Optional[int] = None,
        profit_width: Optional[float] = None,
        stop_width: Optional[float] = None,
    ) -> TripleBarrierResult:
        """简化标注: 每个价格点都是一个事件.

        Args:
            prices: 价格序列
            num_bars: 垂直障碍 (None 用 config 默认)
            profit_width: 上障碍宽度 (None 用 config 默认)
            stop_width: 下障碍宽度 (None 用 config 默认)

        Returns:
            TripleBarrierResult
        """
        if num_bars is not None:
            self.config.num_bars = num_bars
        if profit_width is not None:
            self.config.profit_taking_width = profit_width
        if stop_width is not None:
            self.config.stop_loss_width = stop_width

        valid_idx = prices.index[: len(prices) - self.config.num_bars]
        events = pd.DataFrame(index=valid_idx)
        events["side"] = 1

        return self.label(prices, events)


def meta_labeling(
    primary_labels: pd.Series,
    prices: pd.Series,
    config: Optional[TripleBarrierConfig] = None,
) -> pd.Series:
    """Meta-Labeling (二级分类器) — López de Prado AFML Ch.3.6.

    用 Triple-Barrier 对主策略信号进行二次标注, 产生:
        - +1: 主策略信号正确, 应执行
        - -1: 主策略信号错误, 应跳过

    用途: 减少假阳性 (主策略说买, meta 说不要买)

    Args:
        primary_labels: 主策略标签 (+1/-1)
        prices: 价格序列
        config: Triple-Barrier 配置

    Returns:
        meta 标签 (+1 应执行, -1 应跳过)
    """
    labeler = TripleBarrierLabeler(config or TripleBarrierConfig())

    events = pd.DataFrame(index=primary_labels.index)
    events["side"] = primary_labels.values

    result = labeler.label(prices, events)

    meta_labels = pd.Series(index=primary_labels.index, dtype=int)
    for event in result.events:
        date = event.timestamp
        if date in primary_labels.index:
            primary = primary_labels[date]
            if primary == event.label:
                meta_labels[date] = 1
            else:
                meta_labels[date] = -1

    valid_count = meta_labels.notna().sum()
    if valid_count > 0:
        accuracy = (meta_labels == 1).sum() / valid_count
        logger.info(
            "Meta-Labeling: %d 个事件, 主策略准确率 %.1f%%",
            valid_count,
            accuracy * 100,
        )

    return meta_labels
