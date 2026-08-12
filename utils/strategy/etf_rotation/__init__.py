"""W6.4.4 etf-rotation-strategy 三层验证子包。

借鉴 etf-rotation-strategy 的 WFO→VEC→BT 三层验证引擎,
对 ETF 轮动策略做 Walk-Forward 优化 + 交叉验证 + 最终回测。
"""
from utils.strategy.etf_rotation.engine import (
    BTResult,
    ThreeTierETFRotationValidator,
    ThreeTierReport,
    VECResult,
    WFOResult,
    generate_rotation_signals,
)

__all__ = [
    "BTResult",
    "ThreeTierETFRotationValidator",
    "ThreeTierReport",
    "VECResult",
    "WFOResult",
    "generate_rotation_signals",
]
