#!/usr/bin/env python3
"""多源交叉校验 (Cross-Source Validation) — 数据管道"接入层"增强 (G14 架构升级).

对应量化铁律: 糟糕的数据比没有数据更危险, 数据必须经过多源交叉校验.
现有 DataLayer 已实现多源"回退" (failover), 本模块补上"交叉比对" (consistency check):
  对同标的同字段的多源报价, 计算相对偏离, 超阈值告警 (数据质量风险).

用法:
  validator = CrossSourceValidator(max_rel_deviation=0.01)
  result = validator.check("600519", {"wind": 1700.5, "tdx": 1701.2, "akshare": 1699.8})
  if not result.ok:
      alert(f"数据分歧: {result}")
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """单标的多源校验结果."""

    symbol: str
    ok: bool
    sources: dict[str, float] = field(default_factory=dict)
    median: float = 0.0
    max_rel_deviation: float = 0.0
    worst_pair: tuple = ("", "")
    message: str = ""

    def __str__(self) -> str:
        return (
            f"[{self.symbol}] ok={self.ok} median={self.median:.4f} "
            f"max_dev={self.max_rel_deviation:.4%} {self.message}"
        )


class CrossSourceValidator:
    """多源报价一致性校验器."""

    def __init__(self, max_rel_deviation: float = 0.01, min_sources: int = 2):
        """
        Args:
            max_rel_deviation: 允许的最大相对偏离 (默认 1%, 即两源报价相差 >1% 告警).
            min_sources: 最少需要多少源才做校验 (不足则跳过, 视为 ok).
        """
        self.max_rel_deviation = max_rel_deviation
        self.min_sources = min_sources

    def check(self, symbol: str, quotes: dict[str, float]) -> ValidationResult:
        """对单标的多源报价做一致性校验."""
        valid = {k: float(v) for k, v in quotes.items() if v is not None and v == v and v != 0}
        result = ValidationResult(symbol=symbol, ok=True, sources=valid)
        if len(valid) < self.min_sources:
            result.message = f"源数不足 ({len(valid)}<{self.min_sources}), 跳过交叉校验"
            return result

        vals = list(valid.values())
        median = sorted(vals)[len(vals) // 2] if len(vals) % 2 else (
            sorted(vals)[len(vals) // 2 - 1] + sorted(vals)[len(vals) // 2]
        ) / 2
        result.median = median

        # 计算相对偏离最大的源对
        worst_dev = 0.0
        worst_pair: tuple = ("", "")
        names = list(valid.keys())
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = valid[names[i]], valid[names[j]]
                base = max(abs(a), abs(b), 1e-9)
                dev = abs(a - b) / base
                if dev > worst_dev:
                    worst_dev = dev
                    worst_pair = (names[i], names[j])
        result.max_rel_deviation = worst_dev
        result.worst_pair = worst_pair

        if worst_dev > self.max_rel_deviation:
            result.ok = False
            result.message = (
                f"多源偏离超阈值 ({worst_dev:.4%} > {self.max_rel_deviation:.4%}), "
                f"源对={worst_pair} 值={valid[worst_pair[0]]:.4f}/{valid[worst_pair[1]]:.4f}"
            )
            logger.warning("[CrossSource] %s", result)
        else:
            result.message = "交叉校验通过"
        return result

    def check_batch(self, batch: dict[str, dict[str, float]]) -> dict[str, ValidationResult]:
        """批量校验, 返回 {symbol: ValidationResult}."""
        return {sym: self.check(sym, quotes) for sym, quotes in batch.items()}
