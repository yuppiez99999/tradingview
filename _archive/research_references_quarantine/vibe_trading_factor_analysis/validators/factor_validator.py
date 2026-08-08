"""Vibe-Trading 候选因子验证器（Gate 2: IC 稳定性）
自包含实现，仅依赖 numpy。与 utils/alpha_evaluator.py 逻辑对齐但独立运行。
"""
from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger("vibe_factor_validator")


@dataclass
class Gate2Result:
    """单因子 Gate 2 验证结果"""
    factor_name: str
    ic_mean: float = 0.0
    ic_ir: float = 0.0
    decay_score: float = 0.0
    sample_count: int = 0
    gate_2_pass: bool = False
    reason: str = ""


@dataclass
class Gate2Report:
    """Gate 2 批量验证报告"""
    report_date: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FactorValidator:
    """候选因子 IC 稳定性验证器（Gate 2）"""
    IC_IR_ALIVE = 0.3
    DECAY_THRESHOLD = 0.6
    MIN_SAMPLES = 10

    def __init__(self, config: dict[str, Any] | None = None):
        c = config or {}
        self.ic_ir_alive = float(c.get("ic_ir_alive", self.IC_IR_ALIVE))
        self.decay_threshold = float(c.get("decay_threshold", self.DECAY_THRESHOLD))
        self.min_samples = int(c.get("min_samples", self.MIN_SAMPLES))

    def validate(self, factor_values, forward_returns_history, factor_name="candidate"):
        """factor_values={symbol:value}; forward_returns_history=[{symbol:ret}...]"""
        ics = self._rolling_ic(factor_values, forward_returns_history)
        r = Gate2Result(factor_name=factor_name, sample_count=len(ics))
        if len(ics) < self.min_samples:
            r.reason = f"samples {len(ics)} < {self.min_samples}"
            return r
        arr = np.array(ics, dtype=float)
        r.ic_mean = float(np.mean(arr))
        std = float(np.std(arr))
        r.ic_ir = float(abs(r.ic_mean) / std) if std > 1e-12 else 0.0
        r.decay_score = self._decay(arr)
        r.gate_2_pass = r.ic_ir >= self.ic_ir_alive and r.decay_score < self.decay_threshold
        r.reason = "pass" if r.gate_2_pass else f"ic_ir={r.ic_ir:.3f} decay={r.decay_score:.3f}"
        return r

    def _rolling_ic(self, fv, hist):
        """计算滚动 IC 序列"""
        out = []
        for fr in hist:
            common = [s for s in fv if s in fr and math.isfinite(fv[s])]
            if len(common) < 5:
                continue
            x = np.array([fv[s] for s in common], dtype=float)
            y = np.array([fr[s] for s in common], dtype=float)
            if np.std(x) < 1e-12 or np.std(y) < 1e-12:
                out.append(0.0)
                continue
            c = float(np.corrcoef(x, y)[0, 1])
            out.append(c if math.isfinite(c) else 0.0)
        return out

    def _decay(self, ic_arr):
        """衰减评分：0=健康, 1=完全失效"""
        if len(ic_arr) < 2:
            return 0.0
        avg_abs = float(np.mean(np.abs(ic_arr)))
        if avg_abs < 1e-12:
            return 1.0
        return max(0.0, min(1.0, 1.0 - abs(float(ic_arr[-1])) / avg_abs))
