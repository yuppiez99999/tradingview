"""
v7.6 Backtest 模块 — Walk-Forward, CPCV, 成本模型, 绩效指标, 压力场景
"""

from .walk_forward import WalkForward
from .cost_model import CostModel, AlmgrenChrissCost
from .metrics import PerformanceMetrics, DeflatedSharpeRatio
from .scenario_lib import ScenarioLibrary, STRESS_SCENARIOS
from .cpcv import CPCVCrossValidator, PurgedKFold

__all__ = [
    "WalkForward",
    "CostModel", "AlmgrenChrissCost",
    "PerformanceMetrics", "DeflatedSharpeRatio",
    "ScenarioLibrary", "STRESS_SCENARIOS",
    "CPCVCrossValidator", "PurgedKFold",
]
