"""System Health Score 聚合引擎 (Production Edition T2, 2026-09-02).

五维遥测 → 加权评分 (0-100), 报告侧零侵入:
  model   0.25  reports/drift/integration_{date}.json (IC/ICIR/漂移)
  data    0.20  reports/degradation_log.jsonl (当日降级条目)
  trading 0.15  reports/tca/fills_{date}.jsonl (成交与 TCA 预估覆盖)
  risk    0.20  reports/evolution/vol_regime_weights_{date}.json (regime)
  capital 0.20  output/shadow_account/s12_shadow_state.json (NAV/fail-fast)

降级语义: 某维数据不可得 → 60 分中性值 + degraded=True 显式标记
(fail-open, 观测路径不阻断).

已知噪音 (v1 接受): degradation_log.jsonl 含测试进程产生的条目,
同日多次 pytest 运行会累计, v1 不区分来源.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

DEGRADED_NEUTRAL = 60.0

WEIGHTS: dict[str, float] = {
    "model": 0.25,
    "data": 0.20,
    "trading": 0.15,
    "risk": 0.20,
    "capital": 0.20,
}


@dataclass
class DimensionScore:
    """单维评分: 0-100 分 + 权重 + 降级标记 + 明细."""

    score: float
    weight: float
    degraded: bool
    detail: dict = field(default_factory=dict)


def status_for(total: float) -> str:
    """总分 → 状态色: ≥85 GREEN / ≥70 YELLOW / <70 RED."""
    if total >= 85.0:
        return "GREEN"
    if total >= 70.0:
        return "YELLOW"
    return "RED"


def _degraded(dimension: str, reason: str) -> DimensionScore:
    """数据不可得时的中性降级评分."""
    return DimensionScore(
        score=DEGRADED_NEUTRAL,
        weight=WEIGHTS[dimension],
        degraded=True,
        detail={"reason": reason},
    )
