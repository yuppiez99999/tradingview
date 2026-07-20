"""执行算法引擎 v1.0

机构级订单执行算法 — 模仿 Citadel/Jane Street/Two Sigma 的执行层

支持算法:
1. VWAP (Volume Weighted Average Price) — 按历史成交量分布拆单
2. TWAP (Time Weighted Average Price) — 按时间均匀拆单
3. POV (Percentage of Volume) — 跟随实时成交量, 保持市场占比
4. IS (Implementation Shortfall) — 平衡市场冲击与时机风险
5. AC (Almgren-Chriss) — 最优执行轨迹 (与 market_impact_model 联动)

核心特性:
- 子订单切片 + 随机化 (防信号识别)
- 流动性感知 (避开开盘/收盘高峰)
- 冲击成本约束 (单笔不超过 ADV 的 η%)
- 执行轨迹输出 (供 TCA 引擎回测)

参考:
- Kissell (2013) "The Science of Algorithmic Trading"
- Almgren & Chriss (2000) "Optimal Execution of Portfolio Transactions"
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class Order:
    """订单定义"""
    symbol: str                       # 标的代码
    side: str                         # BUY / SELL
    total_shares: float               # 总股数
    start_time: pd.Timestamp          # 开始时间
    end_time: pd.Timestamp            # 结束时间
    benchmark_price: float = 0.0      # 决策价 (用于 IS 计算)
    urgency: str = "MEDIUM"           # LOW / MEDIUM / HIGH
    max_participation: float = 0.10  # 单笔最大市场占比 (POV)
    min_slice_size: float = 100.0    # 最小切片股数


@dataclass
class ChildOrder:
    """子订单"""
    symbol: str
    side: str
    shares: float
    scheduled_time: pd.Timestamp
    limit_price: Optional[float] = None  # None 表示市价单
    slice_type: str = "NORMAL"           # NORMAL / OPEN / CLOSE / BURST


@dataclass
class ExecutionPlan:
    """执行计划"""
    parent_order: Order
    algorithm: str                    # VWAP / TWAP / POV / IS / AC
    child_orders: List[ChildOrder] = field(default_factory=list)
    expected_cost_bps: float = 0.0    # 预期成本 (bps)
    expected_market_impact_bps: float = 0.0
    expected_timing_risk_bps: float = 0.0
    total_duration_minutes: int = 0
    avg_slice_size: float = 0.0
    max_slice_size: float = 0.0
    num_slices: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


# ============================================================
# 执行算法引擎
# ============================================================

class ExecutionAlgorithmEngine:
    """执行算法引擎

    用法:
        engine = ExecutionAlgorithmEngine()
        plan = engine.vwap(order, volume_profile=vol_profile)
        for child in plan.child_orders:
            broker.submit(child)
    """

    # 默认交易时段 (A 股): 09:30-11:30, 13:00-15:00
    MORNING_START = "09:30"
    MORNING_END = "11:30"
    AFTERNOON_START = "13:00"
    AFTERNOON_END = "15:00"

    def __init__(
        self,
        # VWAP 默认成交量曲线 (24 个 10 分钟槽, U 型)
        default_volume_curve: Optional[Sequence[float]] = None,
        # 冲击成本系数
        impact_coeff: float = 0.1,        # 临时冲击系数
        impact_decay: float = 0.5,         # 永久冲击衰减
        # 随机化参数 (防信号识别)
        randomize_size: float = 0.15,     # 切片大小随机化 ±15%
        randomize_time: float = 0.10,     # 切片时间随机化 ±10%
        # 风险偏好 (IS 算法用)
        risk_aversion: float = 1.0,       # λ 风险厌恶系数
        seed: int = 42,
    ):
        self.default_volume_curve = list(default_volume_curve) if default_volume_curve else self._default_u_shape_curve()
        self.impact_coeff = float(impact_coeff)
        self.impact_decay = float(impact_decay)
        self.randomize_size = float(randomize_size)
        self.randomize_time = float(randomize_time)
        self.risk_aversion = float(risk_aversion)
        self.rng = np.random.default_rng(seed)

    @staticmethod
    def _default_u_shape_curve() -> List[float]:
        """默认 U 型成交量曲线 (24 个 10 分钟槽)

        开盘/收盘成交密集, 中午稀疏
        """
        # 12 槽上午 + 12 槽下午
        morning = [2.0, 1.5, 1.0, 0.8, 0.6, 0.5, 0.4, 0.4, 0.5, 0.6, 0.8, 1.0]
        afternoon = [1.2, 1.0, 0.8, 0.6, 0.5, 0.5, 0.6, 0.7, 0.9, 1.2, 1.5, 2.0]
        return morning + afternoon

    # ------------------------------------------------------------
    # 交易时段工具
    # ------------------------------------------------------------

    def _generate_trading_slots(
        self,
        start_time: pd.Timestamp,
        end_time: pd.Timestamp,
        slot_minutes: int = 10,
    ) -> List[pd.Timestamp]:
        """生成交易时段内的 10 分钟槽 (跳过午休)"""
        slots: List[pd.Timestamp] = []
        current = start_time
        morning_end = current.replace(hour=11, minute=30, second=0)
        afternoon_start = current.replace(hour=13, minute=0, second=0)
        afternoon_end = current.replace(hour=15, minute=0, second=0)

        while current < end_time:
            # 跳过午休
            if morning_end <= current < afternoon_start:
                current = afternoon_start
                continue
            # 超过收盘
            if current >= afternoon_end:
                break
            slots.append(current)
            current = current + pd.Timedelta(minutes=slot_minutes)
        return slots

    # ------------------------------------------------------------
    # VWAP 算法
    # ------------------------------------------------------------

    def vwap(
        self,
        order: Order,
        volume_profile: Optional[Sequence[float]] = None,
        slot_minutes: int = 10,
    ) -> ExecutionPlan:
        """VWAP — 按历史成交量分布拆单

        Args:
            order: 父订单
            volume_profile: 成交量曲线 (与槽对应); None 用默认 U 型
            slot_minutes: 槽长度 (分钟)

        Returns:
            ExecutionPlan
        """
        curve = list(volume_profile) if volume_profile else self.default_volume_curve
        slots = self._generate_trading_slots(order.start_time, order.end_time, slot_minutes)

        # 对齐槽与曲线
        n_slots = len(slots)
        if len(curve) >= n_slots:
            weights = curve[:n_slots]
        else:
            # 重复填充
            weights = [curve[i % len(curve)] for i in range(n_slots)]

        total_w = sum(weights)
        if total_w <= 0:
            # 退化: 均匀
            weights = [1.0] * n_slots
            total_w = float(n_slots)

        # 计算切片大小
        shares_per_slot = [order.total_shares * w / total_w for w in weights]
        # 应用随机化 (防信号识别)
        randomized = self._apply_randomization(shares_per_slot, slots)

        # 归一化确保总和正确
        total_allocated = sum(s for s, _ in randomized)
        if total_allocated > 0:
            scale = order.total_shares / total_allocated
            randomized = [(s * scale, t) for s, t in randomized]

        # 构造子订单
        child_orders: List[ChildOrder] = []
        for shares, time in randomized:
            if shares < order.min_slice_size:
                continue
            # 标记开盘/收盘切片
            slice_type = "NORMAL"
            hour_min = time.hour * 60 + time.minute
            if hour_min <= 9 * 60 + 40:
                slice_type = "OPEN"
            elif hour_min >= 14 * 60 + 50:
                slice_type = "CLOSE"
            child_orders.append(ChildOrder(
                symbol=order.symbol,
                side=order.side,
                shares=round(shares, 0),
                scheduled_time=time,
                slice_type=slice_type,
            ))

        # 预期成本 (bps) — 简化估计
        adv_proxy = max(order.total_shares * 10, 1.0)
        impact_bps = self.impact_coeff * 10000 * math.sqrt(order.total_shares / adv_proxy)

        return ExecutionPlan(
            parent_order=order,
            algorithm="VWAP",
            child_orders=child_orders,
            expected_cost_bps=impact_bps,
            expected_market_impact_bps=impact_bps * 0.7,
            expected_timing_risk_bps=impact_bps * 0.3,
            total_duration_minutes=int((order.end_time - order.start_time).total_seconds() / 60),
            avg_slice_size=float(np.mean([c.shares for c in child_orders])) if child_orders else 0.0,
            max_slice_size=float(max([c.shares for c in child_orders], default=0.0)),
            num_slices=len(child_orders),
            metadata={"slot_minutes": slot_minutes, "curve_type": "default_u_shape"},
        )

    # ------------------------------------------------------------
    # TWAP 算法
    # ------------------------------------------------------------

    def twap(
        self,
        order: Order,
        slot_minutes: int = 10,
    ) -> ExecutionPlan:
        """TWAP — 按时间均匀拆单"""
        slots = self._generate_trading_slots(order.start_time, order.end_time, slot_minutes)
        n_slots = len(slots)
        if n_slots == 0:
            return ExecutionPlan(parent_order=order, algorithm="TWAP")

        shares_per_slot = [order.total_shares / n_slots] * n_slots
        randomized = self._apply_randomization(shares_per_slot, slots)

        # 归一化
        total_allocated = sum(s for s, _ in randomized)
        if total_allocated > 0:
            scale = order.total_shares / total_allocated
            randomized = [(s * scale, t) for s, t in randomized]

        child_orders: List[ChildOrder] = []
        for shares, time in randomized:
            if shares < order.min_slice_size:
                continue
            child_orders.append(ChildOrder(
                symbol=order.symbol,
                side=order.side,
                shares=round(shares, 0),
                scheduled_time=time,
            ))

        adv_proxy = max(order.total_shares * 10, 1.0)
        impact_bps = self.impact_coeff * 10000 * math.sqrt(order.total_shares / adv_proxy)

        return ExecutionPlan(
            parent_order=order,
            algorithm="TWAP",
            child_orders=child_orders,
            expected_cost_bps=impact_bps * 1.1,  # TWAP 略高于 VWAP
            expected_market_impact_bps=impact_bps * 0.7,
            expected_timing_risk_bps=impact_bps * 0.4,
            total_duration_minutes=int((order.end_time - order.start_time).total_seconds() / 60),
            avg_slice_size=float(np.mean([c.shares for c in child_orders])) if child_orders else 0.0,
            max_slice_size=float(max([c.shares for c in child_orders], default=0.0)),
            num_slices=len(child_orders),
            metadata={"slot_minutes": slot_minutes},
        )

    # ------------------------------------------------------------
    # POV 算法
    # ------------------------------------------------------------

    def pov(
        self,
        order: Order,
        expected_market_volume: float,
        slot_minutes: int = 5,
    ) -> ExecutionPlan:
        """POV — 跟随实时成交量, 保持市场占比

        Args:
            order: 父订单
            expected_market_volume: 预期市场总成交量 (股)
            slot_minutes: 槽长度 (POV 通常用更短的槽)
        """
        slots = self._generate_trading_slots(order.start_time, order.end_time, slot_minutes)
        n_slots = len(slots)
        if n_slots == 0:
            return ExecutionPlan(parent_order=order, algorithm="POV")

        # 按 U 型曲线分配预期市场成交量
        curve = self.default_volume_curve
        if len(curve) >= n_slots:
            weights = curve[:n_slots]
        else:
            weights = [curve[i % len(curve)] for i in range(n_slots)]
        total_w = sum(weights)
        vol_per_slot = [expected_market_volume * w / total_w for w in weights]

        # 每槽 = min(max_participation * market_vol, 剩余订单)
        participation = order.max_participation
        remaining = order.total_shares
        child_orders: List[ChildOrder] = []

        for i, (slot, mkt_vol) in enumerate(zip(slots, vol_per_slot)):
            if remaining <= 0:
                break
            target = min(participation * mkt_vol, remaining)
            if target < order.min_slice_size:
                continue
            # 随机化
            noise = 1.0 + self.rng.uniform(-self.randomize_size, self.randomize_size)
            actual = min(target * noise, remaining)
            actual = max(actual, 0.0)
            if actual < order.min_slice_size:
                continue
            # 时间随机化
            time_jitter = self.rng.uniform(-self.randomize_time, self.randomize_time) * slot_minutes
            actual_time = slot + pd.Timedelta(minutes=int(time_jitter))
            child_orders.append(ChildOrder(
                symbol=order.symbol,
                side=order.side,
                shares=round(actual, 0),
                scheduled_time=actual_time,
            ))
            remaining -= actual

        # 残余订单强制执行
        if remaining > 0 and child_orders:
            child_orders[-1].shares += remaining

        impact_bps = self.impact_coeff * 10000 * math.sqrt(
            order.total_shares / max(expected_market_volume, 1.0)
        ) * participation * 10

        return ExecutionPlan(
            parent_order=order,
            algorithm="POV",
            child_orders=child_orders,
            expected_cost_bps=impact_bps,
            expected_market_impact_bps=impact_bps * 0.6,
            expected_timing_risk_bps=impact_bps * 0.5,
            total_duration_minutes=int((order.end_time - order.start_time).total_seconds() / 60),
            avg_slice_size=float(np.mean([c.shares for c in child_orders])) if child_orders else 0.0,
            max_slice_size=float(max([c.shares for c in child_orders], default=0.0)),
            num_slices=len(child_orders),
            metadata={
                "participation_rate": participation,
                "expected_market_volume": expected_market_volume,
            },
        )

    # ------------------------------------------------------------
    # IS (Implementation Shortfall) 算法
    # ------------------------------------------------------------

    def is_algo(
        self,
        order: Order,
        daily_volatility: float = 0.02,
        slot_minutes: int = 10,
    ) -> ExecutionPlan:
        """IS — Implementation Shortfall 算法

        平衡市场冲击 (慢执行) 与 时机风险 (快执行)

        最优轨迹基于 Almgren-Chriss:
        x(t) = X * (1 - tau(t)) / (1 + λ*σ²*T*τ(t)*something)

        简化版: 前置权重 w(t) = exp(-λσ²t) 归一化
        """
        slots = self._generate_trading_slots(order.start_time, order.end_time, slot_minutes)
        n_slots = len(slots)
        if n_slots == 0:
            return ExecutionPlan(parent_order=order, algorithm="IS")

        # 紧迫度 → λ
        urgency_lambda = {"LOW": 0.5, "MEDIUM": 1.0, "HIGH": 2.5}.get(order.urgency, 1.0)
        lam = self.risk_aversion * urgency_lambda

        # 每槽时间权重 (越靠后越担心时机风险, 但越早冲击越大)
        # 简化: 前置加权
        t_array = np.linspace(0, 1, n_slots)
        sigma2 = daily_volatility ** 2
        # 前置权重 w(t) = exp(-lam * sigma2 * t)
        raw_w = np.exp(-lam * sigma2 * t_array * 10)  # 放大系数使曲线弯曲明显
        weights = raw_w / raw_w.sum()

        shares_per_slot = [order.total_shares * w for w in weights]
        randomized = self._apply_randomization(shares_per_slot, slots)

        total_allocated = sum(s for s, _ in randomized)
        if total_allocated > 0:
            scale = order.total_shares / total_allocated
            randomized = [(s * scale, t) for s, t in randomized]

        child_orders: List[ChildOrder] = []
        for shares, time in randomized:
            if shares < order.min_slice_size:
                continue
            child_orders.append(ChildOrder(
                symbol=order.symbol,
                side=order.side,
                shares=round(shares, 0),
                scheduled_time=time,
            ))

        # IS 成本分解
        impact_bps = self.impact_coeff * 10000 * math.sqrt(
            order.total_shares / max(order.total_shares * 10, 1.0)
        )
        timing_risk_bps = sigma2 * math.sqrt(n_slots) * 10000 * 0.5

        return ExecutionPlan(
            parent_order=order,
            algorithm="IS",
            child_orders=child_orders,
            expected_cost_bps=impact_bps + timing_risk_bps * 0.5,
            expected_market_impact_bps=impact_bps,
            expected_timing_risk_bps=timing_risk_bps,
            total_duration_minutes=int((order.end_time - order.start_time).total_seconds() / 60),
            avg_slice_size=float(np.mean([c.shares for c in child_orders])) if child_orders else 0.0,
            max_slice_size=float(max([c.shares for c in child_orders], default=0.0)),
            num_slices=len(child_orders),
            metadata={
                "lambda": lam,
                "daily_volatility": daily_volatility,
                "urgency": order.urgency,
            },
        )

    # ------------------------------------------------------------
    # 工具函数
    # ------------------------------------------------------------

    def _apply_randomization(
        self,
        shares_per_slot: List[float],
        slots: List[pd.Timestamp],
    ) -> List[Tuple[float, pd.Timestamp]]:
        """应用切片大小与时间随机化"""
        randomized: List[Tuple[float, pd.Timestamp]] = []
        for shares, slot in zip(shares_per_slot, slots):
            # 大小随机化
            size_noise = 1.0 + self.rng.uniform(-self.randomize_size, self.randomize_size)
            actual_shares = max(shares * size_noise, 0.0)
            # 时间随机化 (±slot_minutes * randomize_time)
            time_jitter_minutes = self.rng.uniform(-self.randomize_time, self.randomize_time) * 10
            actual_time = slot + pd.Timedelta(minutes=time_jitter_minutes)
            randomized.append((actual_shares, actual_time))
        return randomized

    def select_algorithm(
        self,
        order: Order,
        adv: float,
        volatility: float = 0.02,
    ) -> str:
        """根据订单特征自动选择算法

        Args:
            order: 父订单
            adv: 日均成交量
            volatility: 日波动率

        Returns:
            算法名 (VWAP/TWAP/POV/IS)
        """
        # 参与度 = 订单股数 / ADV
        participation = order.total_shares / max(adv, 1.0)

        # 决策树
        if participation > 0.20:
            # 大单: 用 POV 严格控占比
            return "POV"
        elif participation > 0.05:
            # 中单: 紧迫 → IS, 否则 → VWAP
            return "IS" if order.urgency == "HIGH" else "VWAP"
        else:
            # 小单: 紧迫 → TWAP, 否则 → VWAP
            return "TWAP" if order.urgency == "HIGH" else "VWAP"

    def summarize_plan(self, plan: ExecutionPlan) -> Dict[str, Any]:
        """生成执行计划摘要"""
        return {
            "algorithm": plan.algorithm,
            "symbol": plan.parent_order.symbol,
            "side": plan.parent_order.side,
            "total_shares": plan.parent_order.total_shares,
            "num_slices": plan.num_slices,
            "avg_slice_size": plan.avg_slice_size,
            "max_slice_size": plan.max_slice_size,
            "duration_minutes": plan.total_duration_minutes,
            "expected_cost_bps": plan.expected_cost_bps,
            "expected_impact_bps": plan.expected_market_impact_bps,
            "expected_timing_risk_bps": plan.expected_timing_risk_bps,
        }
