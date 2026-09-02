"""Chaos 灾难演练包 (G2, 2026-09-02).

暴露故障注入器与交易探针, 供 tests/chaos/test_chaos_trading.py 调用。
"""
from __future__ import annotations

from .fault_injector import (
    SCENARIOS,
    ChaosResult,
    ChaosTradingProbe,
    FaultInjector,
    MockBroker,
)

__all__ = [
    "ChaosResult",
    "ChaosTradingProbe",
    "FaultInjector",
    "MockBroker",
    "SCENARIOS",
]
