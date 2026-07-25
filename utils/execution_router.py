# -*- coding: utf-8 -*-
"""
执行路由引擎 (Execution Router)
====================================

世界顶级量化基金标准：执行即 alpha。
- 根据信号 urgency / confidence 动态选择执行算法
- 自动路由 IS / AC / TWAP / VWAP
- 实现短差（Implementation Shortfall）复盘

用法:
    from utils.execution_router import ExecutionRouter, ExecutionPlan
    router = ExecutionRouter()
    plan = router.route(order, signal, market_state)
    review = router.review(planned_order, executed_order)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

import numpy as np

from utils.execution_algo_engine import ExecutionAlgoEngine

logger = logging.getLogger("execution_router")


@dataclass
class ExecutionPlan:
    """执行计划"""
    symbol: str
    algorithm: str
    urgency: str
    estimated_slippage_bps: float = 0.0
    estimated_duration_minutes: int = 60
    slices: int = 1
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "algorithm": self.algorithm,
            "urgency": self.urgency,
            "estimated_slippage_bps": round(self.estimated_slippage_bps, 4),
            "estimated_duration_minutes": self.estimated_duration_minutes,
            "slices": self.slices,
            "meta": self.meta,
        }


@dataclass
class ExecutionReview:
    """执行复盘"""
    symbol: str
    planned_price: float
    executed_price: float
    planned_slippage_bps: float
    actual_slippage_bps: float
    shortfall_bps: float
    within_tolerance: bool
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class ExecutionRouter:
    """执行路由引擎"""

    def __init__(
        self,
        shortfall_tolerance_bps: float = 8.0,
        review_save_dir: Optional[str] = None,
    ):
        self.shortfall_tolerance_bps = float(shortfall_tolerance_bps)
        self.review_save_dir = review_save_dir or "reports/execution"
        self._algo = ExecutionAlgoEngine()

    # ------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------

    def route(self, order: Dict[str, Any], signal: Optional[Dict[str, Any]], market_state: Optional[Dict[str, Any]] = None) -> ExecutionPlan:
        """根据订单、信号、市场状态选择执行算法

        Args:
            order: {symbol, quantity, side, notional}
            signal: {strength, confidence, urgency?}
            market_state: {volatility, liquidity, spread}

        Returns:
            ExecutionPlan
        """
        market_state = market_state or {}
        symbol = order.get("symbol", "")
        notional = float(order.get("notional", 0))
        confidence = float((signal or {}).get("confidence", 0.5))
        strength = float((signal or {}).get("strength", 0.0))
        vol = float(market_state.get("volatility", 0.02))

        urgency = self._urgency(confidence, strength, notional)
        algo = self._select_algorithm(urgency, notional, vol)

        slippage = self._estimate_slippage(algo, notional, vol)
        duration = self._duration(algo, notional)
        slices = self._slices(algo)

        return ExecutionPlan(
            symbol=symbol,
            algorithm=algo,
            urgency=urgency,
            estimated_slippage_bps=slippage,
            estimated_duration_minutes=duration,
            slices=slices,
            meta={
                "confidence": confidence,
                "strength": strength,
                "notional": notional,
                "volatility": vol,
            },
        )

    # ------------------------------------------------------------
    # 复盘
    # ------------------------------------------------------------

    def review(self, planned: Dict[str, Any], executed: Dict[str, Any]) -> ExecutionReview:
        """实现短差复盘

        Args:
            planned: {symbol, price, slippage_bps}
            executed: {symbol, price, slippage_bps}

        Returns:
            ExecutionReview
        """
        planned_price = float(planned.get("price", 0))
        executed_price = float(executed.get("price", 0))
        planned_slippage = float(planned.get("slippage_bps", 0))
        # 修复 BUG-E1: 从 executed 字典读取实际滑点 (原代码误用 planned.get)
        actual_slippage = float(executed.get("slippage_bps", 0))

        if planned_price > 1e-9:
            shortfall_bps = (executed_price - planned_price) / planned_price * 10000.0
        else:
            shortfall_bps = 0.0

        within = abs(shortfall_bps) <= self.shortfall_tolerance_bps
        review = ExecutionReview(
            symbol=planned.get("symbol", ""),
            planned_price=planned_price,
            executed_price=executed_price,
            planned_slippage_bps=planned_slippage,
            actual_slippage_bps=actual_slippage,
            shortfall_bps=shortfall_bps,
            within_tolerance=within,
        )
        self._save_review(review)
        if not within:
            logger.warning("[ExecutionRouter] 实现短差超限: %s bps > %.1f", shortfall_bps, self.shortfall_tolerance_bps)
        return review

    # ------------------------------------------------------------
    # 内部策略
    # ------------------------------------------------------------

    def _urgency(self, confidence: float, strength: float, notional: float) -> str:
        if confidence >= 0.7 and abs(strength) >= 0.5:
            return "high"
        if confidence >= 0.5 and notional >= 200_000:
            return "medium"
        return "low"

    def _select_algorithm(self, urgency: str, notional: float, vol: float) -> str:
        if urgency == "high" or notional >= 500_000:
            return "IS"
        if notional >= 100_000 or vol >= 0.03:
            return "VWAP"
        return "TWAP"

    def _estimate_slippage(self, algorithm: str, notional: float, vol: float) -> float:
        base = max(notional / 1_000_000.0, 0.1)
        if algorithm == "IS":
            return float(base * vol * 10000.0 * 1.2)
        if algorithm == "VWAP":
            return float(base * vol * 10000.0)
        return float(base * vol * 10000.0 * 0.6)

    def _duration(self, algorithm: str, notional: float) -> int:
        if algorithm == "IS":
            return 15
        if algorithm == "VWAP":
            return 90
        return 240

    def _slices(self, algorithm: str) -> int:
        if algorithm == "IS":
            return 3
        if algorithm == "VWAP":
            return 12
        return 24

    # ------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------

    def _save_review(self, review: ExecutionReview) -> None:
        import json
        from pathlib import Path
        save_dir = Path(self.review_save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        path = save_dir / f"{datetime.now():%Y-%m-%d}.jsonl"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(review.__dict__, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error("[ExecutionRouter] 保存执行复盘失败: %s", e)
