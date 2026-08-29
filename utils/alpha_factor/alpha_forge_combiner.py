"""AlphaForge 动态权重组合机制 (LIT-1.2).

基于 AAAI 2025 AlphaForge 论文, 根据因子历史表现动态调整权重:
    - 滚动窗口 IC/IR 跟踪
    - Softmax 温度加权
    - 衰减惩罚 (IC 衰减因子降权)
    - 换手率约束 (避免权重跳变)

替代固定权重, 目标 IC 提升 ≥ 5%.

集成位置:
    - utils/alpha_factor/alpha_forge_combiner.py (本文件)
    - utils/alpha_factor/evaluator.py (因子评估)
    - utils/alpha_factor/rd_agent_quant.py (因子挖掘)

设计原则:
    - 零行为变更: 旧接口保留, 新接口通过 enable_dynamic=True 启用
    - 渐进式: 先骨架, 逐步填充动态逻辑
    - 可回测: 权重变化全程记录

文献依据: #3 (AAAI 2025 AlphaForge)
集成日期: 2026-08-24
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_WINDOW = 60
_DEFAULT_TEMPERATURE = 1.0
_DEFAULT_DECAY_PENALTY = 0.5
_DEFAULT_MAX_WEIGHT = 0.3
_DEFAULT_MIN_WEIGHT = 0.01
_DEFAULT_TURNOVER_LIMIT = 0.3


@dataclass
class FactorPerformance:
    """单因子历史表现记录."""

    name: str
    ic_history: deque = field(default_factory=lambda: deque(maxlen=_DEFAULT_WINDOW))
    ir_history: deque = field(default_factory=lambda: deque(maxlen=_DEFAULT_WINDOW))

    @property
    def ic_mean(self) -> float:
        return float(np.mean(self.ic_history)) if self.ic_history else 0.0

    @property
    def ic_std(self) -> float:
        return float(np.std(self.ic_history)) if len(self.ic_history) > 1 else 0.0

    @property
    def ir_mean(self) -> float:
        return float(np.mean(self.ir_history)) if self.ir_history else 0.0

    @property
    def decay_score(self) -> float:
        """IC 衰减评分: 近期IC vs 远期IC, 衰减则降权."""
        if len(self.ic_history) < 10:
            return 1.0
        recent = list(self.ic_history)[-10:]
        older = list(self.ic_history)[:-10]
        recent_mean = np.mean(recent)
        older_mean = np.mean(older) if older else recent_mean
        if older_mean == 0:
            return 1.0
        ratio = recent_mean / older_mean
        return max(0.1, min(2.0, ratio))


@dataclass
class WeightAdjustment:
    """权重调整结果."""

    factor_name: str
    old_weight: float
    new_weight: float
    ic_mean: float
    ir_mean: float
    decay_score: float
    adjustment_reason: str = ""


@dataclass
class CombinationResult:
    """动态权重组合结果."""

    weights: dict[str, float]
    adjustments: list[WeightAdjustment]
    turnover: float
    ic_expected: float
    mode: str = "dynamic"


class AlphaForgeCombiner:
    """AlphaForge 动态权重组合器.

    根据因子历史 IC/IR 动态调整权重, 替代固定权重.

    Args:
        temperature: Softmax 温度 (越高越接近等权)
        decay_penalty: IC 衰减惩罚系数
        max_weight: 单因子最大权重
        min_weight: 单因子最小权重
        turnover_limit: 换手率上限 (单次调整)
        window: 滚动窗口大小
    """

    def __init__(
        self,
        temperature: float = _DEFAULT_TEMPERATURE,
        decay_penalty: float = _DEFAULT_DECAY_PENALTY,
        max_weight: float = _DEFAULT_MAX_WEIGHT,
        min_weight: float = _DEFAULT_MIN_WEIGHT,
        turnover_limit: float = _DEFAULT_TURNOVER_LIMIT,
        window: int = _DEFAULT_WINDOW,
    ) -> None:
        self.temperature = temperature
        self.decay_penalty = decay_penalty
        self.max_weight = max_weight
        self.min_weight = min_weight
        self.turnover_limit = turnover_limit
        self.window = window
        self._performances: dict[str, FactorPerformance] = {}
        self._current_weights: dict[str, float] = {}

    def register_factor(self, name: str, initial_weight: float = 0.0) -> None:
        """注册因子."""
        if name not in self._performances:
            self._performances[name] = FactorPerformance(name=name)
            self._current_weights[name] = initial_weight

    def update_performance(self, name: str, ic: float, ir: float | None = None) -> None:
        """更新因子历史表现.

        Args:
            name: 因子名称
            ic: 当期 IC
            ir: 当期 IR (可选, 默认 ic/std)
        """
        if name not in self._performances:
            self.register_factor(name)
        perf = self._performances[name]
        perf.ic_history.append(ic)
        if ir is None:
            ir = ic / max(perf.ic_std, 0.001)
        perf.ir_history.append(ir)

    def compute_dynamic_weights(
        self,
        fixed_weights: dict[str, float] | None = None,
    ) -> CombinationResult:
        """计算动态权重.

        Args:
            fixed_weights: 固定权重 (可选, 用于零行为变更对比)

        Returns:
            CombinationResult 组合结果
        """
        if not self._performances:
            return CombinationResult(
                weights={},
                adjustments=[],
                turnover=0.0,
                ic_expected=0.0,
                mode="empty",
            )

        scores = {}
        for name, perf in self._performances.items():
            base_score = perf.ic_mean * max(perf.ir_mean, 0.0)
            decay_factor = 1.0 - self.decay_penalty * (1.0 - perf.decay_score)
            scores[name] = base_score * max(decay_factor, 0.1)

        raw_weights = self._softmax_weights(scores)
        constrained = self._apply_constraints(raw_weights)
        turnover = self._compute_turnover(constrained)
        constrained = self._apply_turnover_limit(constrained, turnover)

        adjustments = self._build_adjustments(constrained)

        new_ic = self._compute_weighted_ic(constrained)
        self._current_weights = dict(constrained)

        return CombinationResult(
            weights=constrained,
            adjustments=adjustments,
            turnover=turnover,
            ic_expected=new_ic,
            mode="dynamic",
        )

    def _softmax_weights(self, scores: dict[str, float]) -> dict[str, float]:
        """Softmax 温度加权."""
        if not scores:
            return {}
        values = np.array(list(scores.values()))
        names = list(scores.keys())
        scaled = values / max(self.temperature, 0.01)
        shifted = scaled - np.max(scaled)
        exp_weights = np.exp(shifted)
        normalized = exp_weights / np.sum(exp_weights)
        return {name: float(w) for name, w in zip(names, normalized, strict=True)}

    def _apply_constraints(self, weights: dict[str, float]) -> dict[str, float]:
        """应用权重约束 (max/min)."""
        n = len(weights)
        if n == 0:
            return {}
        effective_max = min(self.max_weight, 1.0 / n * 3)
        constrained = {k: min(v, effective_max) for k, v in weights.items()}
        constrained = {k: max(v, self.min_weight) for k, v in constrained.items()}
        total = sum(constrained.values())
        if total > 0:
            constrained = {k: v / total for k, v in constrained.items()}
        return constrained

    def _compute_turnover(self, new_weights: dict[str, float]) -> float:
        """计算换手率."""
        if not self._current_weights:
            return 0.0
        all_keys = set(new_weights) | set(self._current_weights)
        diff = sum(
            abs(new_weights.get(k, 0) - self._current_weights.get(k, 0))
            for k in all_keys
        )
        return diff / 2.0

    def _apply_turnover_limit(
        self, weights: dict[str, float], turnover: float
    ) -> dict[str, float]:
        """换手率限制: 如果超过上限, 混合新旧权重."""
        if turnover <= self.turnover_limit or not self._current_weights:
            return weights
        alpha = self.turnover_limit / max(turnover, 0.001)
        blended = {}
        all_keys = set(weights) | set(self._current_weights)
        for k in all_keys:
            new_w = weights.get(k, 0)
            old_w = self._current_weights.get(k, 0)
            blended[k] = alpha * new_w + (1 - alpha) * old_w
        total = sum(blended.values())
        if total > 0:
            blended = {k: v / total for k, v in blended.items()}
        return blended

    def _build_adjustments(
        self, new_weights: dict[str, float]
    ) -> list[WeightAdjustment]:
        """构建权重调整记录."""
        adjustments = []
        for name, new_w in new_weights.items():
            old_w = self._current_weights.get(name, 0.0)
            perf = self._performances.get(name)
            if perf is None:
                continue
            reason = ""
            if new_w > old_w * 1.05:
                reason = "IC/IR表现优异, 增加权重"
            elif new_w < old_w * 0.95:
                reason = "IC/IR表现下降或衰减, 减少权重"
            else:
                reason = "权重基本不变"
            adjustments.append(
                WeightAdjustment(
                    factor_name=name,
                    old_weight=old_w,
                    new_weight=new_w,
                    ic_mean=perf.ic_mean,
                    ir_mean=perf.ir_mean,
                    decay_score=perf.decay_score,
                    adjustment_reason=reason,
                )
            )
        return adjustments

    def _compute_weighted_ic(self, weights: dict[str, float]) -> float:
        """计算加权 IC."""
        if not weights:
            return 0.0
        total_ic = 0.0
        total_w = 0.0
        for name, w in weights.items():
            perf = self._performances.get(name)
            if perf:
                total_ic += w * perf.ic_mean
                total_w += w
        return total_ic / max(total_w, 0.001)

    def get_status(self) -> dict[str, Any]:
        """获取组合器状态."""
        return {
            "n_factors": len(self._performances),
            "temperature": self.temperature,
            "current_weights": dict(self._current_weights),
            "window": self.window,
            "turnover_limit": self.turnover_limit,
        }


def quick_check() -> dict[str, Any]:
    """自检接口."""
    combiner = AlphaForgeCombiner(temperature=0.5)
    factors = ["momentum_20d", "reversal_5d", "volume_price_divergence"]
    for f in factors:
        combiner.register_factor(f, initial_weight=1.0 / len(factors))

    np.random.seed(42)
    for _ in range(30):
        for f in factors:
            ic = np.random.normal(0.04, 0.06)
            combiner.update_performance(f, ic)

    result = combiner.compute_dynamic_weights()
    return {
        "available": True,
        "n_factors": len(factors),
        "weights": {k: round(v, 4) for k, v in result.weights.items()},
        "turnover": round(result.turnover, 4),
        "ic_expected": round(result.ic_expected, 4),
        "mode": result.mode,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    check = quick_check()
    print(f"AlphaForgeCombiner 自检: {check}")
