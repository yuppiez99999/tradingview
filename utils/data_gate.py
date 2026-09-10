"""
数据质量门控 (Data Gate)
=========================

世界顶级量化基金标准：坏数据不交易。
- 数据质量评分 < 阈值 → 暂停新建仓
- 多源价格偏离 > 1% → 告警/阻断
- 数据新鲜度超 SLA → 降级或暂停

用法:
    from utils.data_gate import DataGate, DataGateResult
    gate = DataGate()
    result = gate.check_and_gate(symbol, snapshot)
    if not result.allowed:
        logger.info(result.reasons)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from utils.datetime_utils import now_bj

logger = logging.getLogger("data_gate")


@dataclass
class DataGateResult:
    """数据门控结果"""

    allowed: bool = True
    quality_score: float = 100.0
    freshness_minutes: float = 0.0
    deviation_pct: float = 0.0
    reasons: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "quality_score": round(self.quality_score, 2),
            "freshness_minutes": round(self.freshness_minutes, 2),
            "deviation_pct": round(self.deviation_pct, 4),
            "reasons": self.reasons,
            "meta": self.meta,
        }


class DataGate:
    """数据质量硬拦截"""

    def __init__(
        self,
        min_quality_score: float = 80.0,
        max_price_deviation_pct: float = 1.0,
        max_freshness_minutes: float = 15.0,
        max_macro_freshness_hours: float = 24.0,
        freshness_penalty: float = 25.0,
        critical_penalty: float = 60.0,
    ):
        self.min_quality_score = float(min_quality_score)
        self.max_price_deviation_pct = float(max_price_deviation_pct)
        self.max_freshness_minutes = float(max_freshness_minutes)
        self.max_macro_freshness_hours = float(max_macro_freshness_hours)
        self.freshness_penalty = float(freshness_penalty)
        self.critical_penalty = float(critical_penalty)

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------

    def check_and_gate(
        self,
        symbol: str,
        snapshot: dict[str, Any],
        peers: dict[str, dict[str, Any]] | None = None,
        is_macro: bool = False,
    ) -> DataGateResult:
        """检查单标的数据质量并决定是否允许交易

        Args:
            symbol: 标的代码
            snapshot: 单标数据快照，至少包含 price / quality_score / timestamp / source
            peers: 同源/跨源对比数据 {peer_symbol: {price, timestamp, source}}
            is_macro: 是否为宏观数据

        Returns:
            DataGateResult
        """
        result = DataGateResult()
        peers = peers or {}
        score = 100.0

        # 基础质量分
        raw_score = snapshot.get("quality_score")
        if raw_score is not None:
            try:
                score = float(raw_score)
                result.quality_score = score
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ):  # P2 模块 fail-safe, 待后续精确化
                result.quality_score = score

        # 新鲜度 (仅当提供了 timestamp 时才检查; 缺失元数据不应误判为过期)
        ts = snapshot.get("timestamp")
        if ts is not None:
            freshness_minutes = self._freshness_minutes(ts)
            result.freshness_minutes = freshness_minutes
            if is_macro:
                if freshness_minutes > self.max_macro_freshness_hours * 60:
                    score -= self.critical_penalty
                    result.reasons.append(f"宏观数据过期 {freshness_minutes / 60:.1f}h")
            else:
                if freshness_minutes > self.max_freshness_minutes:
                    score -= self.freshness_penalty
                    result.reasons.append(f"行情数据延迟 {freshness_minutes:.1f} 分钟")

        # 多源偏离
        deviation = self._price_deviation(snapshot, peers)
        result.deviation_pct = deviation
        if deviation > self.max_price_deviation_pct:
            score -= self.critical_penalty
            result.reasons.append(f"跨源价格偏离 {deviation:.2f}%")

        # 异常值/缺数
        if not self._valid_price(snapshot.get("price")):
            score -= self.critical_penalty
            result.reasons.append("价格缺失或异常")

        # 拦截阈值
        score = max(0.0, min(100.0, score))
        result.quality_score = score
        result.allowed = score >= self.min_quality_score and not result.reasons
        if not result.allowed:
            logger.warning(
                "[DataGate] %s 被阻断: %s | score=%.1f", symbol, result.reasons, score
            )
        return result

    # ------------------------------------------------------------
    # 工具函数
    # ------------------------------------------------------------

    def _freshness_minutes(self, timestamp: Any) -> float:
        if not timestamp:
            return 1e9
        try:
            if isinstance(timestamp, str):
                ts = datetime.fromisoformat(timestamp)
            elif isinstance(timestamp, datetime):
                ts = timestamp
            else:
                return 1e9
            return max((now_bj() - ts).total_seconds() / 60.0, 0.0)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):  # P2 模块 fail-safe, 待后续精确化
            return 1e9

    def _price_deviation(
        self, snapshot: dict[str, Any], peers: dict[str, dict[str, Any]]
    ) -> float:
        base_price = snapshot.get("price")
        if base_price is None or not peers:
            return 0.0
        try:
            base_price = float(base_price)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):  # P2 模块 fail-safe, 待后续精确化
            return 0.0
        if base_price <= 0:
            return 0.0

        deviations = []
        for peer in peers.values():
            peer_price = peer.get("price")
            if peer_price is None:
                continue
            try:
                peer_price = float(peer_price)
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ):  # P2 模块 fail-safe, 待后续精确化
                continue
            if peer_price <= 0:
                continue
            deviations.append(abs(peer_price - base_price) / base_price * 100.0)

        return float(max(deviations)) if deviations else 0.0

    def _valid_price(self, price: Any) -> bool:
        if price is None:
            return False
        try:
            v = float(price)
            return math.isfinite(v) and v > 0
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):  # P2 模块 fail-safe, 待后续精确化
            return False
