"""Directional Change 算法 — 事件驱动择时 (纯 numpy)

DC 算法基于"内在时间"而非物理时间, 在高波动场景下优于固定时间间隔。

算法:
1. 设定阈值 θ (如 1%)
2. 从初始高点/低点开始追踪
3. 价格从高点下跌 θ → 触发下行 DC (DCC-), 记录高点, 切换为追踪低点
4. 价格从低点上涨 θ → 触发上行 DC (DCC+), 记录低点, 切换为追踪高点
5. Overshoot: 超过 θ 后继续延伸, 直到反向 DC 触发

参考:
- Tsang & Zhao (2012) "Directional Change and Event-Based Time"
- 经典理论覆盖度审计 Top 10 #4
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from utils.alpha_factor.base import FactorValue


class DCEventType(Enum):
    """DC 事件类型"""

    UPWARD_DC = "upward_dc"  # 上行 DC: 价格从低点上涨 θ
    DOWNWARD_DC = "downward_dc"  # 下行 DC: 价格从高点下跌 θ
    UPWARD_OS = "upward_overshoot"  # 上行 Overshoot
    DOWNWARD_OS = "downward_overshoot"  # 下行 Overshoot


@dataclass
class DCEvent:
    """Directional Change 事件"""

    timestamp: int  # 时间索引
    price: float
    event_type: DCEventType
    extreme_price: float  # 触发前的极值 (高/低点)
    threshold: float  # θ


def extract_dc_events(
    prices: list[float] | np.ndarray,
    threshold: float = 0.01,
) -> list[DCEvent]:
    """提取 Directional Change 事件序列

    算法:
    1. 初始化: 第一个点同时为高点和低点
    2. 追踪模式: 当前在上行/下行追踪
    3. 上行追踪中: 若价格从低点上涨 θ → 触发上行 DC, 切换下行追踪
    4. 下行追踪中: 若价格从高点下跌 θ → 触发下行 DC, 切换上行追踪

    Args:
        prices: 价格序列
        threshold: DC 阈值 θ (如 0.01 = 1%)

    Returns:
        DC 事件列表 (按时间顺序)
    """
    p = np.asarray(prices, dtype=float)
    n = len(p)
    if n < 3:
        return []

    events: list[DCEvent] = []
    # 初始化: 假设当前在上行追踪 (从第一个点开始)
    # last_low = p[0], last_high = p[0]
    last_low = float(p[0])
    last_high = float(p[0])
    # mode: True = 上行追踪 (找高点), False = 下行追踪 (找低点)
    mode = True  # 先假设上行

    for i in range(1, n):
        pi = float(p[i])
        if mode:
            # 上行追踪: 更新高点
            if pi > last_high:
                last_high = pi
            # 检查是否触发下行 DC (从高点下跌 θ)
            if pi <= last_high * (1 - threshold):
                events.append(
                    DCEvent(
                        timestamp=i,
                        price=pi,
                        event_type=DCEventType.DOWNWARD_DC,
                        extreme_price=last_high,
                        threshold=threshold,
                    )
                )
                last_low = pi
                mode = False
        else:
            # 下行追踪: 更新低点
            if pi < last_low:
                last_low = pi
            # 检查是否触发上行 DC (从低点上涨 θ)
            if pi >= last_low * (1 + threshold):
                events.append(
                    DCEvent(
                        timestamp=i,
                        price=pi,
                        event_type=DCEventType.UPWARD_DC,
                        extreme_price=last_low,
                        threshold=threshold,
                    )
                )
                last_high = pi
                mode = True

    return events


def dc_volatility(
    prices: list[float] | np.ndarray,
    threshold: float = 0.01,
    annualize: bool = True,
) -> float:
    """基于 DC 事件的波动率估计

    DC 频率反映波动率: 高频 = 高波动, 低频 = 低波动。
    公式: σ_DC = θ * sqrt(N_dc / T) * sqrt(252)
    其中 N_dc = DC 事件数, T = 时间长度

    Args:
        prices: 价格序列
        threshold: DC 阈值
        annualize: 是否年化

    Returns:
        DC 波动率; 数据不足返回 0
    """
    p = np.asarray(prices, dtype=float)
    n = len(p)
    if n < 10:
        return 0.0
    events = extract_dc_events(p, threshold)
    n_dc = len(events)
    if n_dc < 2:
        return 0.0
    vol = threshold * np.sqrt(n_dc / n)
    if annualize:
        vol *= np.sqrt(252)
    return float(vol)


class DirectionalChangeExtractor:
    """DC 事件提取器 (有状态, 用于流式数据)

    用法:
        extractor = DirectionalChangeExtractor(threshold=0.01)
        for price in price_stream:
            events = extractor.update(price)
            for ev in events:
                print(ev)
    """

    def __init__(self, threshold: float = 0.01):
        self.threshold = threshold
        self.last_high: float | None = None
        self.last_low: float | None = None
        self.mode: bool = True  # True = 上行追踪
        self.timestamp: int = 0
        self.events: list[DCEvent] = []

    def update(self, price: float) -> list[DCEvent]:
        """输入新价格, 返回本步触发的 DC 事件 (0 或 1 个)

        Args:
            price: 新价格

        Returns:
            本步触发的 DC 事件列表
        """
        new_events: list[DCEvent] = []
        if self.last_high is None or self.last_low is None:
            self.last_high = price
            self.last_low = price
            self.timestamp += 1
            return new_events

        i = self.timestamp
        if self.mode:
            if price > self.last_high:
                self.last_high = price
            if price <= self.last_high * (1 - self.threshold):
                ev = DCEvent(
                    timestamp=i,
                    price=price,
                    event_type=DCEventType.DOWNWARD_DC,
                    extreme_price=self.last_high,
                    threshold=self.threshold,
                )
                new_events.append(ev)
                self.events.append(ev)
                self.last_low = price
                self.mode = False
        else:
            if price < self.last_low:
                self.last_low = price
            if price >= self.last_low * (1 + self.threshold):
                ev = DCEvent(
                    timestamp=i,
                    price=price,
                    event_type=DCEventType.UPWARD_DC,
                    extreme_price=self.last_low,
                    threshold=self.threshold,
                )
                new_events.append(ev)
                self.events.append(ev)
                self.last_high = price
                self.mode = True

        self.timestamp += 1
        return new_events


def compute_dc_factors(
    price_data: dict[str, dict[str, list[float]]],
    threshold: float = 0.01,
) -> dict[str, FactorValue]:
    """DC 因子 3 个

    DC_VOL_60D: 60 日 DC 波动率 (高频=高波动)
    DC_VOL_120D: 120 日 DC 波动率
    DC_TREND_60D: 60 日 DC 趋势信号
        上行 DC 数 - 下行 DC 数, 正=看多, 负=看空

    Args:
        price_data: {symbol: {"closes": [...], ...}}
        threshold: DC 阈值

    Returns:
        {factor_name: FactorValue}
    """
    factors: dict[str, FactorValue] = {}

    vol_60d: dict[str, float] = {}
    vol_120d: dict[str, float] = {}
    trend_60d: dict[str, float] = {}

    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if len(closes) < 60:
            continue
        # 60 日 DC 波动率
        vol_60d[sym] = dc_volatility(closes[-60:], threshold)
        # 120 日 DC 波动率
        if len(closes) >= 120:
            vol_120d[sym] = dc_volatility(closes[-120:], threshold)
        # 60 日 DC 趋势
        events_60 = extract_dc_events(closes[-60:], threshold)
        n_up = sum(1 for e in events_60 if e.event_type == DCEventType.UPWARD_DC)
        n_down = sum(1 for e in events_60 if e.event_type == DCEventType.DOWNWARD_DC)
        trend_60d[sym] = float(n_up - n_down)

    factors["DC_VOL_60D"] = FactorValue(
        name="DC_VOL_60D", category="DirectionalChange", values=vol_60d
    )
    factors["DC_VOL_120D"] = FactorValue(
        name="DC_VOL_120D", category="DirectionalChange", values=vol_120d
    )
    factors["DC_TREND_60D"] = FactorValue(
        name="DC_TREND_60D", category="DirectionalChange", values=trend_60d
    )

    return factors
