"""DQC 检查点实现.

检查点分布:
    P1 source_integrity — 源头完整性 (Phase 3)
    P2 cache_quality     — 缓存质量 (Phase 1, 当前实现)
    P3 factor_quality    — 因子质量 (Phase 2, 接入 DriftMonitor)
    P4 sample_quality    — 样本质量 (Phase 3)
    P5 prediction_quality — 预测质量 (Phase 3)
"""

from __future__ import annotations

from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate, run_p2_gate
from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate, run_p3_gate

__all__ = [
    "P2CacheQualityGate",
    "run_p2_gate",
    "P3FactorQualityGate",
    "run_p3_gate",
]
