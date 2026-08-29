"""智能订单路由器 v1.0

模仿 Citadel Securities / Jane Street / Tower Research 的智能路由系统

核心能力:
1. 多场所寻优 — 在多个交易所/暗池中选择最优执行场所
2. 流动性感知 — 实时盘口深度/价差/报价不平衡
3. 反贪吃 (Anti-Gaming) — 检测做市商套利, 避免被钓鱼
4. 拆单隐蔽 — 冰山订单 + 随机化 (防订单流分析)
5. 最优执行场所评分 — 综合成本/速度/风险评分

参考:
- Kissell (2013) "The Science of Algorithmic Trading" Ch.12
- Foucault, Pagano & Röell (2005) "Limit Order Book"

A股适配:
- 主要场所: 上交所主板/科创板/北交所, 深交所主板/创业板
- 暗池替代: 大宗交易系统 / 协议转让
- 盘口数据: Level-2 行情 (10 档买卖盘)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# ============================================================
# 数据结构
# ============================================================


@dataclass
class Venue:
    """交易场所"""

    name: str  # 场所名 (SSE_MAIN / SZSE_MAIN / DARK_POOL_1)
    venue_type: str  # EXCHANGE / DARK_POOL / BLOCK_TRADE
    # 优势
    liquidity_score: float = 0.5  # 流动性评分 [0, 1]
    cost_score: float = 0.5  # 成本评分 [0, 1] (越高越省)
    speed_score: float = 0.5  # 速度评分
    anonymity_score: float = 0.0  # 匿名性评分 (暗池高)
    # 状态
    available: bool = True
    # 费用
    commission_bps: float = 2.5  # 佣金 (bps)
    fee_bps: float = 0.5  # 规费 (bps)


@dataclass
class OrderBookSnapshot:
    """盘口快照"""

    venue_name: str
    timestamp: str
    # 5 档买卖盘
    bid_prices: list[float] = field(default_factory=list)
    bid_sizes: list[float] = field(default_factory=list)
    ask_prices: list[float] = field(default_factory=list)
    ask_sizes: list[float] = field(default_factory=list)
    # 最新成交
    last_price: float = 0.0
    # 累计成交量
    cumulative_volume: float = 0.0


@dataclass
class VenueScore:
    """场所评分"""

    venue_name: str
    total_score: float  # 总评分 [0, 1]
    # 分项评分
    liquidity_score: float
    cost_score: float
    speed_score: float
    anonymity_score: float
    # 预期执行参数
    expected_fill_price: float
    expected_fill_ratio: float  # 预期成交率
    expected_cost_bps: float  # 预期成本 (bps)
    # 推荐分配
    recommended_shares: float = 0.0
    recommended_split_ratio: float = 0.0


@dataclass
class RoutingDecision:
    """路由决策"""

    symbol: str
    side: str  # BUY / SELL
    total_shares: float
    # 分配到各场所的子订单
    allocations: list[VenueScore] = field(default_factory=list)
    # 主场所
    primary_venue: str = ""
    # 备用场所 (主场所故障时切换)
    fallback_venues: list[str] = field(default_factory=list)
    # 反贪吃标记
    gaming_detected: bool = False
    gaming_risk_score: float = 0.0  # [0, 1]
    # 元数据
    strategy: str = "SMART"  # SMART / TWAP_SPLIT / ICEBERG / DARK_FIRST
    metadata: dict[str, Any] = field(default_factory=dict)


# ============================================================
# 智能订单路由器
# ============================================================


class SmartOrderRouter:
    """智能订单路由器

    用法:
        router = SmartOrderRouter()
        router.register_venue(Venue(name="SSE_MAIN", venue_type="EXCHANGE", ...))
        decision = router.route(
            symbol="600519",
            side="BUY",
            total_shares=10000,
            order_books={"SSE_MAIN": book1, "SZSE_MAIN": book2},
        )
    """

    # A股主要场所默认配置
    DEFAULT_VENUES = [
        Venue(
            name="SSE_MAIN",
            venue_type="EXCHANGE",
            liquidity_score=0.95,
            cost_score=0.85,
            speed_score=0.95,
            anonymity_score=0.1,
            commission_bps=2.5,
            fee_bps=0.5,
        ),
        Venue(
            name="SZSE_MAIN",
            venue_type="EXCHANGE",
            liquidity_score=0.92,
            cost_score=0.85,
            speed_score=0.92,
            anonymity_score=0.1,
            commission_bps=2.5,
            fee_bps=0.5,
        ),
        Venue(
            name="BLOCK_TRADE",
            venue_type="BLOCK_TRADE",
            liquidity_score=0.7,
            cost_score=0.95,  # 大宗交易成本更低
            speed_score=0.5,
            anonymity_score=0.8,
            commission_bps=1.5,
            fee_bps=0.3,
        ),
        Venue(
            name="DARK_POOL",
            venue_type="DARK_POOL",
            liquidity_score=0.4,
            cost_score=0.9,
            speed_score=0.4,
            anonymity_score=1.0,
            commission_bps=2.0,
            fee_bps=0.4,
        ),
    ]

    def __init__(
        self,
        venues: list[Venue] | None = None,
        # 评分权重
        w_liquidity: float = 0.35,
        w_cost: float = 0.30,
        w_speed: float = 0.20,
        w_anonymity: float = 0.15,
        # 反贪吃阈值
        gaming_threshold: float = 0.7,
        # 冰山订单参数
        iceberg_visible_ratio: float = 0.1,  # 可见比例 10%
        # 最小分配比例
        min_allocation_ratio: float = 0.05,
        seed: int = 42,
    ):
        self.venues: dict[str, Venue] = {
            v.name: v for v in (venues or self.DEFAULT_VENUES)
        }
        self.w_liq = float(w_liquidity)
        self.w_cost = float(w_cost)
        self.w_speed = float(w_speed)
        self.w_anon = float(w_anonymity)
        self.gaming_threshold = float(gaming_threshold)
        self.iceberg_ratio = float(iceberg_visible_ratio)
        self.min_alloc = float(min_allocation_ratio)
        self.rng = np.random.default_rng(seed)

        # 权重归一化
        total_w = self.w_liq + self.w_cost + self.w_speed + self.w_anon
        if total_w > 0:
            self.w_liq /= total_w
            self.w_cost /= total_w
            self.w_speed /= total_w
            self.w_anon /= total_w

    # ------------------------------------------------------------
    # 场所管理
    # ------------------------------------------------------------

    def register_venue(self, venue: Venue) -> None:
        """注册交易场所"""
        self.venues[venue.name] = venue
        logger.info("[Router] 注册场所: %s (%s)", venue.name, venue.venue_type)

    def update_venue_status(self, name: str, available: bool) -> None:
        """更新场所可用性"""
        if name in self.venues:
            self.venues[name].available = available
            logger.info(
                "[Router] 场所 %s 状态: %s", name, "可用" if available else "不可用"
            )

    # ------------------------------------------------------------
    # 主路由入口
    # ------------------------------------------------------------

    def route(
        self,
        symbol: str,
        side: str,
        total_shares: float,
        order_books: dict[str, OrderBookSnapshot] | None = None,
        strategy: str = "SMART",
        max_venues: int = 3,
        min_shares_per_venue: float = 100.0,
    ) -> RoutingDecision:
        """主路由入口

        Args:
            symbol: 标的代码
            side: BUY / SELL
            total_shares: 总股数
            order_books: 各场所盘口快照 {venue_name: OrderBookSnapshot}
            strategy: SMART / TWAP_SPLIT / ICEBERG / DARK_FIRST / LIQUIDITY_FIRST
            max_venues: 最大场所数
            min_shares_per_venue: 每场所最小分配股数

        Returns:
            RoutingDecision
        """
        order_books = order_books or {}

        # 1) 场所评分
        scores = self._score_venues(side, total_shares, order_books)

        # 2) 选择策略
        if strategy == "DARK_FIRST":
            scores = [s for s in scores if self.venues.get(s.venue_name).venue_type in ("DARK_POOL", "BLOCK_TRADE")]  # type: ignore
            if not scores:
                strategy = "SMART"
                scores = self._score_venues(side, total_shares, order_books)
        elif strategy == "LIQUIDITY_FIRST":
            scores = sorted(scores, key=lambda s: s.liquidity_score, reverse=True)
        elif strategy == "ICEBERG":
            # 冰山: 主场所为主, 拆小单
            pass

        # 3) 选 top N
        top_scores = scores[:max_venues]

        # 4) 分配股数
        if strategy == "ICEBERG":
            allocations = self._allocate_iceberg(
                top_scores, total_shares, min_shares_per_venue
            )
        else:
            allocations = self._allocate_proportional(
                top_scores, total_shares, min_shares_per_venue
            )

        # 5) 反贪吃检测
        gaming = self._detect_gaming(order_books, side)
        gaming_risk = gaming[1] if gaming else 0.0

        # 6) 主场所 / 备用场所
        primary = allocations[0].venue_name if allocations else ""
        fallback = [a.venue_name for a in allocations[1:]]

        return RoutingDecision(
            symbol=symbol,
            side=side.upper(),
            total_shares=total_shares,
            allocations=allocations,
            primary_venue=primary,
            fallback_venues=fallback,
            gaming_detected=gaming[0] if gaming else False,
            gaming_risk_score=gaming_risk,
            strategy=strategy,
            metadata={
                "num_venues_evaluated": len(scores),
                "num_venues_selected": len(allocations),
            },
        )

    # ------------------------------------------------------------
    # 场所评分
    # ------------------------------------------------------------

    def _score_venues(
        self,
        side: str,
        total_shares: float,
        order_books: dict[str, OrderBookSnapshot],
    ) -> list[VenueScore]:
        """评分所有场所"""
        scores: list[VenueScore] = []
        for name, venue in self.venues.items():
            if not venue.available:
                continue
            book = order_books.get(name)

            # 流动性评分: 优先用盘口数据
            if book and book.bid_sizes and book.ask_sizes:
                # 盘口深度 (前 5 档总和)
                if side.upper() == "BUY":
                    depth = sum(book.ask_sizes)
                else:
                    depth = sum(book.bid_sizes)
                # 覆盖率: 盘口能覆盖多少订单
                coverage = min(depth / max(total_shares, 1.0), 1.0)
                liq_score = 0.5 * venue.liquidity_score + 0.5 * coverage
            else:
                liq_score = venue.liquidity_score

            # 成本评分: 佣金 + 规费 + 价差
            if book and book.bid_prices and book.ask_prices:
                spread_bps = (
                    (book.ask_prices[0] - book.bid_prices[0])
                    / max(
                        book.last_price
                        or (book.ask_prices[0] + book.bid_prices[0]) / 2,
                        1e-6,
                    )
                    * 10000
                )
                # 价差越大, 成本越高, cost_score 越低
                spread_penalty = min(spread_bps / 20.0, 1.0)  # 20bps 满惩罚
                cost_score = venue.cost_score * (1 - spread_penalty * 0.5)
            else:
                cost_score = venue.cost_score
                spread_bps = 5.0

            total_fee_bps = venue.commission_bps + venue.fee_bps + spread_bps / 2
            expected_price = (
                book.ask_prices[0]
                if (book and book.ask_prices) and side.upper() == "BUY"
                else (
                    book.bid_prices[0]
                    if (book and book.bid_prices) and side.upper() == "SELL"
                    else (book.last_price if book else 0.0)
                )
            )

            # 综合评分
            total_score = (
                self.w_liq * liq_score
                + self.w_cost * cost_score
                + self.w_speed * venue.speed_score
                + self.w_anon * venue.anonymity_score
            )

            # 预期成交率 (盘口能吃多少)
            if book:
                if side.upper() == "BUY":
                    avail_shares = sum(book.ask_sizes)
                else:
                    avail_shares = sum(book.bid_sizes)
                fill_ratio = min(avail_shares / max(total_shares, 1.0), 1.0)
            else:
                fill_ratio = 0.5

            scores.append(
                VenueScore(
                    venue_name=name,
                    total_score=total_score,
                    liquidity_score=liq_score,
                    cost_score=cost_score,
                    speed_score=venue.speed_score,
                    anonymity_score=venue.anonymity_score,
                    expected_fill_price=expected_price,
                    expected_fill_ratio=fill_ratio,
                    expected_cost_bps=total_fee_bps,
                )
            )

        # 按总分排序
        scores.sort(key=lambda s: s.total_score, reverse=True)
        return scores

    # ------------------------------------------------------------
    # 分配策略
    # ------------------------------------------------------------

    def _allocate_proportional(
        self,
        scores: list[VenueScore],
        total_shares: float,
        min_shares: float,
    ) -> list[VenueScore]:
        """按评分比例分配"""
        if not scores:
            return []
        # softmax 分配 (高分场所占比大)
        temps = np.array([s.total_score for s in scores])
        temps = np.clip(temps, 0.01, 1.0)
        # logit transform + softmax
        logits = np.log(temps / (1 - temps + 1e-6) + 1e-6)
        weights = np.exp(logits - logits.max())
        weights = weights / weights.sum()

        allocated = []
        for score, w in zip(scores, weights, strict=True):
            shares = total_shares * w
            if shares < min_shares:
                continue
            score.recommended_shares = round(shares, 0)
            score.recommended_split_ratio = float(w)
            allocated.append(score)

        # 修正: 把被过滤的份额重新分配给主场所
        allocated_total = sum(s.recommended_shares for s in allocated)
        if allocated and allocated_total < total_shares:
            residual = total_shares - allocated_total
            allocated[0].recommended_shares += residual

        return allocated

    def _allocate_iceberg(
        self,
        scores: list[VenueScore],
        total_shares: float,
        min_shares: float,
    ) -> list[VenueScore]:
        """冰山订单分配: 主场所可见 10%, 其余分小单"""
        if not scores:
            return []
        # 主场所: 可见部分
        visible = max(total_shares * self.iceberg_ratio, min_shares)
        scores[0].recommended_shares = round(visible, 0)
        scores[0].recommended_split_ratio = self.iceberg_ratio
        # 其余分到其他场所
        remaining = total_shares - visible
        if remaining > 0 and len(scores) > 1:
            # 均分到其他场所
            per_venue = remaining / (len(scores) - 1)
            for s in scores[1:]:
                if per_venue < min_shares:
                    continue
                s.recommended_shares = round(per_venue, 0)
                s.recommended_split_ratio = (1 - self.iceberg_ratio) / (len(scores) - 1)
        return scores

    # ------------------------------------------------------------
    # 反贪吃检测
    # ------------------------------------------------------------

    def _detect_gaming(
        self,
        order_books: dict[str, OrderBookSnapshot],
        side: str,
    ) -> tuple[bool, float]:
        """检测做市商套利 (Quote Stuffing / Spoofing)

        简化启发式:
        - 报价不平衡度 > 0.8 (一侧报价明显多)
        - 价差异常小 (< 1bp)
        """
        if not order_books:
            return False, 0.0

        risk_scores: list[float] = []
        for _name, book in order_books.items():
            if not book.bid_sizes or not book.ask_sizes:
                continue
            bid_total = sum(book.bid_sizes)
            ask_total = sum(book.ask_sizes)
            total = bid_total + ask_total
            if total <= 0:
                continue
            # 不平衡度 [0, 1]
            imbalance = abs(bid_total - ask_total) / total
            # 价差异常
            spread_bps = 0.0
            if book.bid_prices and book.ask_prices and book.last_price > 0:
                spread_bps = (
                    (book.ask_prices[0] - book.bid_prices[0]) / book.last_price * 10000
                )
            spread_anomaly = 1.0 if spread_bps < 1.0 else 0.0

            # 综合风险
            risk = 0.6 * imbalance + 0.4 * spread_anomaly
            risk_scores.append(risk)

        if not risk_scores:
            return False, 0.0

        avg_risk = float(np.mean(risk_scores))
        return avg_risk > self.gaming_threshold, avg_risk

    # ------------------------------------------------------------
    # 摘要
    # ------------------------------------------------------------

    def summarize_decision(self, decision: RoutingDecision) -> dict[str, Any]:
        """生成路由决策摘要"""
        return {
            "symbol": decision.symbol,
            "side": decision.side,
            "total_shares": decision.total_shares,
            "strategy": decision.strategy,
            "primary_venue": decision.primary_venue,
            "num_venues": len(decision.allocations),
            "allocations": [
                {
                    "venue": a.venue_name,
                    "shares": a.recommended_shares,
                    "ratio": a.recommended_split_ratio,
                    "score": a.total_score,
                    "expected_cost_bps": a.expected_cost_bps,
                    "expected_fill_price": a.expected_fill_price,
                }
                for a in decision.allocations
            ],
            "gaming_detected": decision.gaming_detected,
            "gaming_risk": decision.gaming_risk_score,
        }
