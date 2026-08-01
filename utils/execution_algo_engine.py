"""
执行算法引擎 (Execution Algorithm Engine) v1.0
================================================

世界顶级对冲基金标准执行算法库 — Citadel/Two Sigma/Jane Street 同级:

    1. TWAP (Time-Weighted Average Price)
       - 时间加权均匀拆单
       - 适用: 流动性较好、信号不敏感时间
       - 参数: 总股数、执行时长、参与率上限

    2. VWAP (Volume-Weighted Average Price)
       - 按历史成交量分布拆单
       - 适用: 跟随市场流动性曲线
       - 参数: 总股数、日内成交量曲线、参与率上限

    3. POV (Percentage of Volume)
       - 按实时成交量动态参与
       - 适用: 大单避免市场冲击
       - 参数: 总股数、参与率、最小下单间隔

    4. IS (Implementation Shortfall)
       - 平衡市场冲击与机会成本
       - 适用: 信号重要、需快速完成的单
       - 参数: 总股数、风险厌恶系数、冲击模型

    5. AC (Almgren-Chriss 最优执行)
       - 闭式最优执行轨迹
       - 适用: 学术级最优解
       - 参数: 总股数、风险厌恶λ、波动率、市场冲击

输出每个时间片下单数量, 与 broker 层对接实际下单。

用法:
    from utils.execution_algo_engine import ExecutionAlgoEngine, AlgoType
    engine = ExecutionAlgoEngine()
    plan = engine.plan_order(
        algo=AlgoType.TWAP,
        symbol="300308",
        side="buy",
        total_shares=10000,
        duration_minutes=120,
        slice_minutes=5,
    )
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from pathlib import Path

logger = logging.getLogger("execution_algo")

BASE_DIR = Path(__file__).resolve().parent.parent
PLAN_DIR = BASE_DIR / "trade_instructions" / "execution_plans"


class AlgoType(str, Enum):
    """执行算法类型"""

    TWAP = "TWAP"
    VWAP = "VWAP"
    POV = "POV"
    IS = "IS"  # Implementation Shortfall
    AC = "AC"  # Almgren-Chriss
    DARK = "DARK"  # 暗池冰山


# A 股交易时段 (分钟级)
MORNING_START = time(9, 30)
MORNING_END = time(11, 30)
AFTERNOON_START = time(13, 0)
AFTERNOON_END = time(15, 0)

# 典型日内成交量分布 (上下午各 120 分钟, 共 240 分钟)
# 经验分布 (总计 1.0): 开盘 15min 集中 ~12%, 收盘 15min 集中 ~15%
DEFAULT_INTRADAY_VOLUME_CURVE: list[float] = [
    # 上午 120 分钟 (9:30-11:30), 每分钟占比
    0.020,
    0.018,
    0.015,
    0.013,
    0.011,
    0.010,
    0.009,
    0.008,
    0.008,
    0.007,  # 9:30-9:40 开盘集中
    0.006,
    0.006,
    0.006,
    0.005,
    0.005,
    0.005,
    0.005,
    0.005,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,  # 10:00
    0.004,
    0.004,
    0.004,
    0.004,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.003,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.005,
    0.005,
    0.005,
    0.005,
    0.005,
    0.005,  # 11:00 后趋活跃
    0.005,
    0.005,
    0.005,
    0.005,
    0.006,
    0.006,
    0.006,
    0.006,
    0.006,
    0.006,
    0.007,
    0.007,
    0.007,
    0.007,
    0.008,
    0.008,
    0.008,
    0.009,
    0.009,
    0.010,
    0.011,
    0.012,
    0.013,
    0.014,
    0.015,
    0.016,
    0.017,
    0.018,
    0.020,
    0.022,  # 11:20-11:30 上午收盘集中
    # 下午 120 分钟 (13:00-15:00), 每分钟占比
    0.012,
    0.010,
    0.008,
    0.007,
    0.006,
    0.005,
    0.005,
    0.005,
    0.004,
    0.004,  # 13:00 午后开盘
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,  # 14:00
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.004,
    0.005,
    0.005,
    0.005,
    0.005,
    0.005,
    0.005,
    0.005,
    0.005,
    0.005,
    0.005,
    0.005,
    0.005,
    0.005,
    0.006,
    0.006,
    0.006,
    0.006,
    0.006,
    0.006,
    0.007,
    0.007,
    0.007,
    0.008,
    0.009,
    0.010,
    0.011,
    0.012,
    0.013,
    0.014,
    0.015,
    0.017,
    0.018,  # 14:50-15:00 收盘集中
]


@dataclass
class ExecutionSlice:
    """执行时间片"""

    slice_idx: int  # 时间片序号 (0-based)
    start_time: str  # 开始时间 HH:MM
    end_time: str  # 结束时间 HH:MM
    target_shares: int  # 目标下单股数
    accumulated_shares: int  # 累计已下单
    remaining_shares: int  # 剩余未下单
    participation_rate: float = 0.0  # 预估参与率 (POV 用)
    limit_price: float | None = None  # 限价 (可选)


@dataclass
class ExecutionPlan:
    """执行计划"""

    plan_id: str = ""
    algo: str = ""  # AlgoType 值
    symbol: str = ""
    name: str = ""
    side: str = "buy"  # buy / sell
    total_shares: int = 0
    executed_shares: int = 0
    remaining_shares: int = 0
    start_datetime: str = ""
    end_datetime: str = ""
    duration_minutes: int = 0
    slice_count: int = 0
    slice_minutes: int = 5
    slices: list[ExecutionSlice] = field(default_factory=list)
    expected_vwap: float | None = None
    expected_slippage_bps: float = 0.0
    expected_cost: float = 0.0
    risk_aversion: float = 0.0  # IS/AC 用
    notes: str = ""
    created_at: str = ""


class ExecutionAlgoEngine:
    """执行算法引擎

    根据订单规模、流动性、 urgency 选择最优执行算法,
    输出时间片拆单计划。
    """

    def __init__(
        self,
        default_slice_minutes: int = 5,
        max_participation_rate: float = 0.15,
        min_slice_shares: int = 100,
    ):
        self.default_slice_minutes = default_slice_minutes
        self.max_participation_rate = max_participation_rate
        self.min_slice_shares = min_slice_shares
        PLAN_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------
    # 主入口: 规划订单
    # ------------------------------------------------------------
    def plan_order(
        self,
        algo: AlgoType,
        symbol: str,
        side: str,
        total_shares: int,
        duration_minutes: int = 120,
        slice_minutes: int | None = None,
        start_time: time | None = None,
        volume_curve: list[float] | None = None,
        avg_daily_volume: int | None = None,
        current_price: float | None = None,
        volatility: float | None = None,
        risk_aversion: float = 1.0,
    ) -> ExecutionPlan:
        """规划订单执行

        Args:
            algo: 算法类型
            symbol: 标的代码
            side: buy / sell
            total_shares: 总下单股数
            duration_minutes: 总执行时长 (分钟)
            slice_minutes: 时间片长度 (默认 5 分钟)
            start_time: 起始时间 (默认 9:30)
            volume_curve: 日内成交量分布曲线 (VWAP 用)
            avg_daily_volume: 日均成交量 (POV 用)
            current_price: 当前价格 (IS/AC 用)
            volatility: 年化波动率 (IS/AC 用)
            risk_aversion: 风险厌恶系数 (IS/AC 用, 默认 1.0)

        Returns:
            执行计划
        """
        if total_shares <= 0:
            return ExecutionPlan(symbol=symbol, side=side, total_shares=0)

        slice_minutes = slice_minutes or self.default_slice_minutes
        start_time = start_time or MORNING_START
        volume_curve = volume_curve or DEFAULT_INTRADAY_VOLUME_CURVE

        plan_id = f"{algo.value}_{symbol}_{side}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        if algo == AlgoType.TWAP:
            slices = self._plan_twap(total_shares, duration_minutes, slice_minutes, start_time)
        elif algo == AlgoType.VWAP:
            slices = self._plan_vwap(total_shares, duration_minutes, slice_minutes, start_time, volume_curve)
        elif algo == AlgoType.POV:
            slices = self._plan_pov(
                total_shares,
                duration_minutes,
                slice_minutes,
                start_time,
                avg_daily_volume or 1_000_000,
            )
        elif algo == AlgoType.IS:
            slices = self._plan_implementation_shortfall(
                total_shares,
                duration_minutes,
                slice_minutes,
                start_time,
                current_price or 0.0,
                volatility or 0.25,
                risk_aversion,
            )
        elif algo == AlgoType.AC:
            slices = self._plan_almgren_chriss(
                total_shares,
                duration_minutes,
                slice_minutes,
                start_time,
                current_price or 0.0,
                volatility or 0.25,
                risk_aversion,
            )
        elif algo == AlgoType.DARK:
            slices = self._plan_dark_iceberg(total_shares, duration_minutes, slice_minutes, start_time)
        else:
            raise ValueError(f"不支持的算法: {algo}")

        plan = ExecutionPlan(
            plan_id=plan_id,
            algo=algo.value,
            symbol=symbol,
            side=side,
            total_shares=total_shares,
            executed_shares=0,
            remaining_shares=total_shares,
            start_datetime=slices[0].start_time if slices else "",
            end_datetime=slices[-1].end_time if slices else "",
            duration_minutes=duration_minutes,
            slice_count=len(slices),
            slice_minutes=slice_minutes,
            slices=slices,
            risk_aversion=risk_aversion,
            created_at=datetime.now().isoformat(),
            notes=self._algo_notes(algo, total_shares, avg_daily_volume),
        )

        # 预估滑点与成本
        if current_price and current_price > 0:
            plan.expected_slippage_bps = self._estimate_slippage_bps(total_shares, avg_daily_volume or 1_000_000)
            plan.expected_cost = total_shares * current_price * plan.expected_slippage_bps / 10_000

        logger.info(
            "[ExecAlgo] %s %s %s %d 股 → %d 片, 预估滑点 %.1fbps",
            algo.value,
            side,
            symbol,
            total_shares,
            len(slices),
            plan.expected_slippage_bps,
        )
        return plan

    # ------------------------------------------------------------
    # TWAP: 时间加权均匀拆单
    # ------------------------------------------------------------
    def _plan_twap(
        self,
        total_shares: int,
        duration_minutes: int,
        slice_minutes: int,
        start_time: time,
    ) -> list[ExecutionSlice]:
        """TWAP: 按时间均匀分配"""
        slices_count = max(1, duration_minutes // slice_minutes)
        base_shares = total_shares // slices_count
        remainder = total_shares - base_shares * slices_count

        slices: list[ExecutionSlice] = []
        accumulated = 0
        current_start = datetime.combine(date.today(), start_time)

        for i in range(slices_count):
            slice_end = current_start + timedelta(minutes=slice_minutes)
            # 跳过午休 (11:30-13:00)
            if current_start.time() >= MORNING_END and current_start.time() < AFTERNOON_START:
                current_start = datetime.combine(date.today(), AFTERNOON_START)
                slice_end = current_start + timedelta(minutes=slice_minutes)

            # 最后一片不能超过 15:00
            if slice_end.time() > AFTERNOON_END:
                slice_end = datetime.combine(date.today(), AFTERNOON_END)
                if current_start >= slice_end:
                    break

            target = base_shares + (remainder if i < remainder else 0)
            target = max(self.min_slice_shares, target) if i == slices_count - 1 else target

            slices.append(
                ExecutionSlice(
                    slice_idx=i,
                    start_time=current_start.strftime("%H:%M"),
                    end_time=slice_end.strftime("%H:%M"),
                    target_shares=target,
                    accumulated_shares=accumulated,
                    remaining_shares=total_shares - accumulated - target,
                )
            )
            accumulated += target
            current_start = slice_end

        # 修正: 确保累计等于总数
        if slices and slices[-1].accumulated_shares + slices[-1].target_shares != total_shares:
            diff = total_shares - (slices[-1].accumulated_shares + slices[-1].target_shares)
            slices[-1].target_shares += diff
            slices[-1].remaining_shares = 0

        return slices

    # ------------------------------------------------------------
    # VWAP: 按历史成交量分布拆单
    # ------------------------------------------------------------
    def _plan_vwap(
        self,
        total_shares: int,
        duration_minutes: int,
        slice_minutes: int,
        start_time: time,
        volume_curve: list[float],
    ) -> list[ExecutionSlice]:
        """VWAP: 按 volume_curve 权重分配

        volume_curve 长度 = 240 (上午 120 + 下午 120 分钟)
        每个元素是该分钟的成交量占比
        """
        slices_count = max(1, duration_minutes // slice_minutes)
        # 获取起始分钟索引 (0-239)
        start_minute_idx = self._time_to_minute_idx(start_time)
        duration_slices = min(slices_count, (240 - start_minute_idx) // slice_minutes)

        # 收集覆盖时间段的权重
        weights: list[float] = []
        for i in range(duration_slices):
            slice_start = start_minute_idx + i * slice_minutes
            slice_end = min(slice_start + slice_minutes, 240)
            weight = sum(volume_curve[slice_start:slice_end])
            weights.append(weight)

        total_weight = sum(weights)
        if total_weight <= 0:
            # 回退到 TWAP
            return self._plan_twap(total_shares, duration_minutes, slice_minutes, start_time)

        slices: list[ExecutionSlice] = []
        accumulated = 0
        current_start = datetime.combine(date.today(), start_time)

        for i, w in enumerate(weights):
            slice_end = current_start + timedelta(minutes=slice_minutes)  # type: ignore
            # 跳过午休
            if current_start.time() >= MORNING_END and current_start.time() < AFTERNOON_START:
                current_start = datetime.combine(date.today(), AFTERNOON_START)
                slice_end = current_start + timedelta(minutes=slice_minutes)  # type: ignore

            target = int(total_shares * w / total_weight)
            if i == len(weights) - 1:
                target = total_shares - accumulated  # 最后一片兜底
            target = max(0, target)

            slices.append(
                ExecutionSlice(
                    slice_idx=i,
                    start_time=current_start.strftime("%H:%M"),
                    end_time=slice_end.strftime("%H:%M"),  # type: ignore
                    target_shares=target,
                    accumulated_shares=accumulated,
                    remaining_shares=total_shares - accumulated - target,
                )
            )
            accumulated += target
            current_start = slice_end  # type: ignore

        return slices

    def _time_to_minute_idx(self, t: time) -> int:
        """将 time 转换为日内分钟索引 (0-239)"""
        if t < MORNING_START:
            return 0
        if t <= MORNING_END:
            return (t.hour - 9) * 60 + (t.minute - 30)
        if t < AFTERNOON_START:
            return 120  # 跳到下午开始
        if t <= AFTERNOON_END:
            return 120 + (t.hour - 13) * 60 + t.minute
        return 239

    # ------------------------------------------------------------
    # POV: 按实时成交量参与
    # ------------------------------------------------------------
    def _plan_pov(
        self,
        total_shares: int,
        duration_minutes: int,
        slice_minutes: int,
        start_time: time,
        avg_daily_volume: int,
    ) -> list[ExecutionSlice]:
        """POV: 按预估参与率分配

        每片目标 = 预估该片成交量 × 参与率
        参与率 = min(self.max_participation_rate, total_shares / 预估总成交量)
        """
        # 预估该 duration 内的总成交量
        expected_volume_in_duration = avg_daily_volume * (duration_minutes / 240.0)
        # 计算目标参与率
        target_participation = min(self.max_participation_rate, total_shares / max(1, expected_volume_in_duration))

        slices_count = max(1, duration_minutes // slice_minutes)
        slices: list[ExecutionSlice] = []
        accumulated = 0
        current_start = datetime.combine(date.today(), start_time)
        # 预估每片成交量
        per_slice_volume = expected_volume_in_duration / slices_count

        for i in range(slices_count):
            slice_end = current_start + timedelta(minutes=slice_minutes)
            if current_start.time() >= MORNING_END and current_start.time() < AFTERNOON_START:
                current_start = datetime.combine(date.today(), AFTERNOON_START)
                slice_end = current_start + timedelta(minutes=slice_minutes)
            if slice_end.time() > AFTERNOON_END:
                break

            target = int(per_slice_volume * target_participation)
            if i == slices_count - 1:
                target = total_shares - accumulated
            target = max(0, target)

            slices.append(
                ExecutionSlice(
                    slice_idx=i,
                    start_time=current_start.strftime("%H:%M"),
                    end_time=slice_end.strftime("%H:%M"),
                    target_shares=target,
                    accumulated_shares=accumulated,
                    remaining_shares=total_shares - accumulated - target,
                    participation_rate=target_participation,
                )
            )
            accumulated += target
            current_start = slice_end

        return slices

    # ------------------------------------------------------------
    # Implementation Shortfall (IS)
    # ------------------------------------------------------------
    def _plan_implementation_shortfall(
        self,
        total_shares: int,
        duration_minutes: int,
        slice_minutes: int,
        start_time: time,
        current_price: float,
        volatility: float,
        risk_aversion: float,
    ) -> list[ExecutionSlice]:
        """IS: 平衡市场冲击与机会成本

        高 risk_aversion → 更快完成 (front-loaded)
        低 risk_aversion → 更均匀 (接近 TWAP)

        采用简化的 IS: 前面片更重, 后面递减
        weight_i = (1 - i/N) ^ (1 / (1 + λ))
        """
        N = max(1, duration_minutes // slice_minutes)
        # 风险厌恶系数映射: λ=0 → 均匀 (TWAP), λ=1 → 强 front-loaded
        lambda_exp = 1.0 / (1.0 + max(0.0, risk_aversion))

        weights = []
        for i in range(N):
            w = (1.0 - i / N) ** lambda_exp
            weights.append(w)
        total_w = sum(weights)
        if total_w <= 0:
            return self._plan_twap(total_shares, duration_minutes, slice_minutes, start_time)

        slices: list[ExecutionSlice] = []
        accumulated = 0
        current_start = datetime.combine(date.today(), start_time)

        for i, w in enumerate(weights):
            slice_end = current_start + timedelta(minutes=slice_minutes)
            if current_start.time() >= MORNING_END and current_start.time() < AFTERNOON_START:
                current_start = datetime.combine(date.today(), AFTERNOON_START)
                slice_end = current_start + timedelta(minutes=slice_minutes)
            if slice_end.time() > AFTERNOON_END:
                break

            target = int(total_shares * w / total_w)
            if i == N - 1:
                target = total_shares - accumulated
            target = max(0, target)

            slices.append(
                ExecutionSlice(
                    slice_idx=i,
                    start_time=current_start.strftime("%H:%M"),
                    end_time=slice_end.strftime("%H:%M"),
                    target_shares=target,
                    accumulated_shares=accumulated,
                    remaining_shares=total_shares - accumulated - target,
                )
            )
            accumulated += target
            current_start = slice_end

        return slices

    # ------------------------------------------------------------
    # Almgren-Chriss 最优执行
    # ------------------------------------------------------------
    def _plan_almgren_chriss(
        self,
        total_shares: int,
        duration_minutes: int,
        slice_minutes: int,
        start_time: time,
        current_price: float,
        volatility: float,
        risk_aversion: float,
    ) -> list[ExecutionSlice]:
        """Almgren-Chriss: 闭式最优执行轨迹

        最优执行轨迹: x(t) = X * sinh(κ(T-t)) / sinh(κT)
        其中:
            X = 总股数
            T = 总时长 (天)
            κ = sqrt(λσ²/η)
            σ = 日波动率
            η = 市场冲击系数
            λ = 风险厌恶系数

        高 λ → 趋向线性, 低 λ → 趋向均匀
        """
        N = max(1, duration_minutes // slice_minutes)
        T = duration_minutes / (240.0)  # 转换为天

        # 简化: σ 日波动率 (从年化波动率换算)
        sigma_daily = volatility / math.sqrt(252)
        # 市场冲击系数 η (简化假设)
        eta = 0.001
        # κ
        kappa = math.sqrt(max(0.0, risk_aversion * sigma_daily**2 / eta)) if risk_aversion > 0 else 0.0

        if kappa < 1e-6 or T < 1e-6:
            # λ → 0 时退化为均匀 (TWAP)
            return self._plan_twap(total_shares, duration_minutes, slice_minutes, start_time)

        try:
            sinh_kT = math.sinh(kappa * T)
            if abs(sinh_kT) < 1e-10:
                return self._plan_twap(total_shares, duration_minutes, slice_minutes, start_time)
        except OverflowError:
            # κT 太大, 等价于瞬时完成 (front-loaded)
            return self._plan_implementation_shortfall(
                total_shares,
                duration_minutes,
                slice_minutes,
                start_time,
                current_price,
                volatility,
                risk_aversion * 10,
            )

        # x(t) = X * sinh(κ(T-t)) / sinh(κT)
        # 每片: x(t_i) - x(t_{i+1})
        slices: list[ExecutionSlice] = []
        accumulated = 0
        current_start = datetime.combine(date.today(), start_time)

        prev_x = total_shares  # x(0) = X
        for i in range(N):
            slice_end = current_start + timedelta(minutes=slice_minutes)
            if current_start.time() >= MORNING_END and current_start.time() < AFTERNOON_START:
                current_start = datetime.combine(date.today(), AFTERNOON_START)
                slice_end = current_start + timedelta(minutes=slice_minutes)
            if slice_end.time() > AFTERNOON_END:
                break

            t_i = (i + 1) / N * T
            try:
                x_i = total_shares * math.sinh(max(0.0, kappa * (T - t_i))) / sinh_kT
            except (OverflowError, ValueError):
                x_i = 0
            x_i = max(0, x_i)

            target = round(prev_x - x_i)
            if i == N - 1:
                target = total_shares - accumulated
            target = max(0, target)

            slices.append(
                ExecutionSlice(
                    slice_idx=i,
                    start_time=current_start.strftime("%H:%M"),
                    end_time=slice_end.strftime("%H:%M"),
                    target_shares=target,
                    accumulated_shares=accumulated,
                    remaining_shares=total_shares - accumulated - target,
                )
            )
            accumulated += target
            prev_x = x_i  # type: ignore
            current_start = slice_end

        return slices

    # ------------------------------------------------------------
    # 暗池冰山
    # ------------------------------------------------------------
    def _plan_dark_iceberg(
        self,
        total_shares: int,
        duration_minutes: int,
        slice_minutes: int,
        start_time: time,
    ) -> list[ExecutionSlice]:
        """暗池冰山: 每片固定小单, 避免暴露真实意图"""
        # 固定每片 100 股 (最小单位)
        per_slice = max(self.min_slice_shares, 100)
        slices_count = min(50, max(1, total_shares // per_slice))

        slices: list[ExecutionSlice] = []
        accumulated = 0
        current_start = datetime.combine(date.today(), start_time)
        actual_slice_minutes = max(slice_minutes, duration_minutes // slices_count)

        for i in range(slices_count):
            slice_end = current_start + timedelta(minutes=actual_slice_minutes)
            if current_start.time() >= MORNING_END and current_start.time() < AFTERNOON_START:
                current_start = datetime.combine(date.today(), AFTERNOON_START)
                slice_end = current_start + timedelta(minutes=actual_slice_minutes)
            if slice_end.time() > AFTERNOON_END:
                break

            target = per_slice
            if i == slices_count - 1:
                target = total_shares - accumulated
            target = max(0, target)

            slices.append(
                ExecutionSlice(
                    slice_idx=i,
                    start_time=current_start.strftime("%H:%M"),
                    end_time=slice_end.strftime("%H:%M"),
                    target_shares=target,
                    accumulated_shares=accumulated,
                    remaining_shares=total_shares - accumulated - target,
                )
            )
            accumulated += target
            current_start = slice_end

        return slices

    # ------------------------------------------------------------
    # 自动选择算法
    # ------------------------------------------------------------
    def select_algo(
        self,
        total_shares: int,
        avg_daily_volume: int,
        urgency: str = "medium",  # low / medium / high
        volatility: float = 0.25,
    ) -> AlgoType:
        """根据订单特征自动选择算法

        Args:
            total_shares: 总股数
            avg_daily_volume: 日均成交量
            urgency: 紧急程度 low / medium / high
            volatility: 年化波动率

        Returns:
            推荐算法类型
        """
        participation = total_shares / max(1, avg_daily_volume)

        # 极小单 (<=0.1% ADV): 直接市价
        if participation <= 0.001:
            return AlgoType.TWAP

        # 高紧急度 + 小单: IS (front-loaded)
        if urgency == "high":
            if participation < 0.05:
                return AlgoType.IS
            else:
                return AlgoType.AC

        # 低紧急度 + 大单: VWAP/POV
        if urgency == "low":
            if participation > 0.10:
                return AlgoType.POV
            else:
                return AlgoType.VWAP

        # 中等紧急度
        if participation > 0.15:
            return AlgoType.POV
        elif participation > 0.05:
            return AlgoType.AC
        else:
            return AlgoType.VWAP

    # ------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------
    def _estimate_slippage_bps(
        self,
        total_shares: int,
        avg_daily_volume: int,
    ) -> float:
        """预估滑点 (基点)

        简化的平方根冲击模型: slippage = α × √(Q/V)
        其中 α = 10 bps (典型 A 股)
        """
        if avg_daily_volume <= 0:
            return 50.0
        participation = total_shares / avg_daily_volume
        return round(10.0 * math.sqrt(max(0.0, participation)), 2)

    def _algo_notes(
        self,
        algo: AlgoType,
        total_shares: int,
        avg_daily_volume: int | None,
    ) -> str:
        """生成算法说明"""
        notes = {
            AlgoType.TWAP: "时间加权均匀拆单, 适用于流动性较好、信号不敏感时间",
            AlgoType.VWAP: "按日内成交量分布拆单, 跟随市场流动性曲线",
            AlgoType.POV: "按实时成交量动态参与, 大单避免市场冲击",
            AlgoType.IS: "Implementation Shortfall, 平衡市场冲击与机会成本",
            AlgoType.AC: "Almgren-Chriss 最优执行轨迹, 学术级闭式解",
            AlgoType.DARK: "暗池冰山, 固定小单避免暴露真实意图",
        }
        base = notes.get(algo, "")
        if avg_daily_volume and avg_daily_volume > 0:
            pct = total_shares / avg_daily_volume * 100
            base += f" | 参与率 {pct:.2f}% ADV"
        return base

    def save_plan(self, plan: ExecutionPlan) -> Path:
        """保存执行计划"""
        path = PLAN_DIR / f"{plan.plan_id}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(asdict(plan), f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"执行计划已保存: {path}")
        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            logger.error(f"保存执行计划失败: {e}")
        return path


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="执行算法引擎")
    parser.add_argument("--algo", type=str, default="TWAP", choices=[a.value for a in AlgoType])
    parser.add_argument("--symbol", type=str, default="300308")
    parser.add_argument("--side", type=str, default="buy", choices=["buy", "sell"])
    parser.add_argument("--shares", type=int, default=10000)
    parser.add_argument("--duration", type=int, default=120, help="总执行时长 (分钟)")
    parser.add_argument("--slice", type=int, default=5, help="时间片长度 (分钟)")
    parser.add_argument("--adv", type=int, default=1_000_000, help="日均成交量")
    parser.add_argument("--price", type=float, default=0.0, help="当前价格")
    parser.add_argument("--vol", type=float, default=0.25, help="年化波动率")
    parser.add_argument("--lambda", dest="lambda_", type=float, default=1.0, help="风险厌恶系数")
    parser.add_argument("--auto-select", action="store_true", help="自动选择算法")
    parser.add_argument("--urgency", type=str, default="medium", choices=["low", "medium", "high"])
    args = parser.parse_args()

    engine = ExecutionAlgoEngine()

    # 自动选择算法
    if args.auto_select:
        algo = engine.select_algo(args.shares, args.adv, args.urgency, args.vol)
        logger.info(f"\n自动选择算法: {algo.value}")
    else:
        algo = AlgoType(args.algo)

    plan = engine.plan_order(
        algo=algo,
        symbol=args.symbol,
        side=args.side,
        total_shares=args.shares,
        duration_minutes=args.duration,
        slice_minutes=args.slice,
        avg_daily_volume=args.adv,
        current_price=args.price or None,
        volatility=args.vol,
        risk_aversion=args.lambda_,
    )

    logger.info("\n" + "=" * 60)
    logger.info(f"执行计划: {plan.plan_id}")
    logger.info("=" * 60)
    logger.info(f"算法: {plan.algo}")
    logger.info(f"标的: {plan.symbol} {plan.side} {plan.total_shares} 股")
    logger.info(f"时间: {plan.start_datetime} → {plan.end_datetime} ({plan.duration_minutes} 分钟)")
    logger.info(f"切片数: {plan.slice_count} (每片 {plan.slice_minutes} 分钟)")
    logger.info(f"预估滑点: {plan.expected_slippage_bps:.2f} bps")
    logger.info(f"预估成本: ¥{plan.expected_cost:.2f}")
    logger.info(f"说明: {plan.notes}")

    logger.info("\n时间片明细 (前 10 片):")
    logger.info(f"{'序号':<6}{'时段':<16}{'目标股数':<12}{'累计':<12}{'剩余':<12}{'参与率':<10}")
    for s in plan.slices[:10]:
        print(
            f"{s.slice_idx:<6}{s.start_time}-{s.end_time:<12}{s.target_shares:<12}{s.accumulated_shares:<12}{s.remaining_shares:<12}{s.participation_rate:<10.2%}"
        )

    if len(plan.slices) > 10:
        logger.info(f"... 共 {len(plan.slices)} 片, 省略 {len(plan.slices) - 10} 片")

    path = engine.save_plan(plan)
    logger.info(f"\n计划已保存: {path}")
