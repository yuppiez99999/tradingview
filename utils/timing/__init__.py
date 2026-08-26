"""择时算法模块 — Directional Change (事件驱动)

Directional Change (DC) 算法基于"内在时间"而非物理时间,
在 A 股高波动场景下优于固定时间间隔采样。

核心概念:
- DC 事件: 价格相对最近极值变动超过阈值 θ 时触发
- 上行 DC (DCC+): 价格从低点上涨 θ
- 下行 DC (DCC-): 价格从高点下跌 θ
- Overshoot (OS): 超过 θ 后继续延伸的部分
- 内在时间: DC 事件序列构成的时间轴

应用:
1. 择时信号: 上行 DC → 看多, 下行 DC → 看空
2. 波动率估计: DC 频率反映波动率 (高频=高波动)
3. 事件驱动回测: 在 DC 事件点采样而非每日

参考:
- Tsang, Zhao & Stenger (2012) "Directional Change and Event-Based Time"
- Golub et al. (2018) "High-Frequency Trading with Directional Change"
- 经典理论覆盖度审计 cairn/classic-theory-coverage-20260819.md Top 10 #4
"""

from __future__ import annotations

from utils.timing.directional_change import (
    DCEvent,
    DCEventType,
    DirectionalChangeExtractor,
    compute_dc_factors,
    dc_volatility,
    extract_dc_events,
)

__all__ = [
    "DCEvent",
    "DCEventType",
    "DirectionalChangeExtractor",
    "compute_dc_factors",
    "dc_volatility",
    "extract_dc_events",
]
