"""DQC 指标实现 — 六维分类.

维度分布:
    completeness.py  — C-01 ~ C-06 (完整性)
    timeliness.py    — T-01 ~ T-05 (时效性)
    accuracy.py      — A-01 ~ A-06 (准确性)
    uniqueness.py    — U-01 ~ U-03 (唯一性)
    consistency.py   — X-01 ~ X-05 (一致性, Phase 2 实现, 跨源校验)
    distribution.py  — F-01 ~ F-04 (分布稳定性, Phase 2 实现, 复用 DriftMonitor.compute_psi)
"""

from __future__ import annotations

from utils.dqc.metrics.accuracy import check_accuracy
from utils.dqc.metrics.completeness import check_completeness
from utils.dqc.metrics.consistency import check_consistency
from utils.dqc.metrics.distribution import check_distribution_drift
from utils.dqc.metrics.timeliness import check_timeliness
from utils.dqc.metrics.uniqueness import check_uniqueness

__all__ = [
    "check_completeness",
    "check_timeliness",
    "check_accuracy",
    "check_uniqueness",
    "check_consistency",
    "check_distribution_drift",
]
