"""G15 事件驱动回测 — 主引擎与事件循环模块。

职责:
    - 串联 StrategyAdapter + MatchingEngine + LatencyModel + OrderQueue
    - 维护持仓/现金/权益曲线
    - 提供 process_event() 单事件入口 和 run() 全量回测入口
    - 产出 EngineSummary (Day 5 再转换为完整 BacktestResult)

事件循环语义 ("next-event" 模型):
    1. 事件到达
    2. 延迟队列中所有订单 remaining_latency -= 1; ≤0 者移入撮合队列
    3. 撮合就绪订单 → 产生 FillEvent → 更新持仓/现金
    4. 标记持仓最新价 → 记录权益点
    5. 分发事件给策略 → 策略可能提交新订单 (进入延迟队列,本事件不撮合)

W6.3.2 新增: 确定性事件时钟 (nautilus_trader 风格)
    event_clock_mode = "MONOTONIC_INDEX"  (默认, 向后兼容): 用 _n_events 单调整数计数
    event_clock_mode = "WALL_CLOCK_NS"         (新语义): 用 event.ts_event 纳秒级时间戳严格排序
        - 强制单调校验: ts_event 递减抛 NonMonotonicTimestampError
        - PendingOrder.ready_ts (纳秒) 替代 remaining_latency (事件数)
        - equity_curve 元素为 (ts_event, equity) 二元组, 可对齐真实交易时间
        - 延迟模型: 当 latency_model 返回值为"纳秒"时, submit_order 时 ready_ts = 当前 ts_event + latency_ns

关键约束:
    - 订单在提交事件 T 不会被撮合 (即使 latency=0),最早在 T+1 事件撮合
    - 这与向量化回测"信号 bar T → 成交 bar T+1"语义对齐
    - FixedLatency(0) 即对应"次日开盘成交"基准

设计原则 (AGENTS.md):
    - 不可变性 (§5.1): 订单状态变更走 OrderQueue (内部用 dataclasses.replace)
    - 单一职责: 引擎只编排,撮合交给 MatchingEngine,延迟交给 LatencyModel
    - 多小文件 (§5.3): 本模块 < 400 行
"""

from __future__ import annotations

import dataclasses
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Union

from utils.backtest.adapters import StrategyAdapter
from utils.backtest.latency_model import FixedLatency, LatencyModel
from utils.backtest.matching_engine import MatchingEngine
from utils.backtest.order_queue import OrderQueue
from utils.wt_hedge_strategy import HedgeStrategy
from utils.wt_structs import BarData, OrderData, TickData

MarketEvent = Union[TickData, BarData]  # noqa: UP007  # 运行时类型别名, py38 兼容


# ============================================================
# W6.3.2 新增: 事件时钟异常 + 时钟模式枚举
# ============================================================


class NonMonotonicTimestampError(ValueError):
    """W6.3.2: 确定性事件时钟单调校验失败。

    当 event_clock_mode="WALL_CLOCK_NS" 时, 如果收到的事件 ts_event
    小于或等于上一事件的 ts_event, 则抛出此异常。

    这是 **前视偏差防护** 的硬门禁 — 时间戳乱序会导致"回测偷看未来数据",
    必须立刻失败, 不得降级 (与 nautilus_trader 的设计一致)。

    Attributes:
        last_ts: 上一事件的 ts_event (纳秒)
        curr_ts: 当前事件的 ts_event (纳秒)
        code: 事件对应的标的代码
    """

    def __init__(self, last_ts: int, curr_ts: int, code: str) -> None:
        self.last_ts = last_ts
        self.curr_ts = curr_ts
        self.code = code
        super().__init__(
            f"NonMonotonicTimestampError: 事件时间戳乱序 (防前视偏差硬门禁) — "
            f"code={code}, last_ts={last_ts}ns, curr_ts={curr_ts}ns, "
            f"delta={curr_ts - last_ts}ns (必须>0)."
        )


class EventClockMode:
    """W6.3.2: 事件时钟模式常量。"""

    MONOTONIC_INDEX = "MONOTONIC_INDEX"  # 默认, 向后兼容 (_n_events 单调递增整数)
    WALL_CLOCK_NS = "WALL_CLOCK_NS"  # 新语义, event.ts_event 纳秒级 UNIX 时间戳


# ============================================================
# 数据类
# ============================================================


@dataclass
class PendingOrder:
    """延迟队列中的待撮合订单 (不可变)。

    双模式字段:
        - MONOTONIC_INDEX 模式: 使用 remaining_latency (剩余事件数)
        - WALL_CLOCK_NS 模式:    使用 ready_ts (就绪时的纳秒级时间戳, 0 表示未设置)
    两者同时存在, 由 EventDrivenEngine 根据 event_clock_mode 选择读取的字段。
    """

    order: OrderData
    # MONOTONIC_INDEX 模式: 剩余多少个事件才可撮合 (0 = 下个事件可撮合)
    remaining_latency: int = 0
    # WALL_CLOCK_NS 模式: 订单就绪时的纳秒级 UNIX 时间戳 (0 = 未设置)
    #     ready_ts = submit_ts_event + estimated_latency_ns
    ready_ts: int = 0


@dataclass
class Position:
    """持仓状态 (引擎内部,可变以简化高频更新)。

    Attributes:
        code: 标的代码
        direction: "LONG" / "SHORT"
        volume: 持仓量 (≥0)
        avg_price: 持仓均价
        last_price: 最新标记价 (用于 mark-to-market)
    """

    code: str
    direction: str
    volume: float = 0.0
    avg_price: float = 0.0
    last_price: float = 0.0

    def apply_fill(self, fill_price: float, fill_volume: float) -> None:
        """应用成交 (LONG 加仓 / SELL 平仓 由引擎调用方决定方向)。

        fill_volume 语义:
            > 0: 加仓 (开仓或增仓)
            < 0: 平仓 (减少持仓)
            = 0: 无操作

        加仓: 累积 volume, 重算 avg_price
        平仓: 减少 volume, avg_price 不变; volume 归零则重置
        """
        if fill_volume == 0:
            return
        new_vol = self.volume + fill_volume
        if new_vol <= 0:
            # 平仓完毕
            self.volume = 0.0
            self.avg_price = 0.0
        elif self.volume == 0:
            # 新建仓 (fill_volume 必为正)
            self.volume = new_vol
            self.avg_price = fill_price
        else:
            # 加仓: 重算均价; 平仓部分: 均价不变
            if fill_volume > 0:
                self.avg_price = (
                    self.avg_price * self.volume + fill_price * fill_volume
                ) / new_vol
            self.volume = new_vol


@dataclass
class EngineSnapshot:
    """引擎状态快照 (不可变,供审计/测试)。

    所有 list/dict 字段均为副本,外部修改不影响引擎内部状态。
    """

    cash: float
    equity: float
    event_clock_mode: str  # W6.3.2: 当前时钟模式
    last_ts_event: (
        int  # W6.3.2: 最新事件 ts_event (WALL_CLOCK_NS 模式下有效; MONOTONIC_INDEX=0)
    )
    n_events_processed: int
    n_orders_submitted: int
    n_orders_filled: int
    n_orders_rejected: int
    pending_latency_count: int
    matchable_queue_count: int
    long_positions: dict
    short_positions: dict
    market_prices: dict
    equity_curve: list  # W6.3.2: MONOTONIC_INDEX=list[float], WALL_CLOCK_NS=list[Tuple[int, float]] (ts_ns, equity)


@dataclass
class EngineSummary:
    """回测汇总结果 (Day 5 转换为完整 BacktestResult)。

    Attributes:
        initial_capital: 初始资金
        final_equity: 最终权益
        total_return: 总收益率
        event_clock_mode: 时钟模式 (W6.3.2 新增, 默认 MONOTONIC_INDEX 向后兼容)
        n_events: 处理事件数
        n_orders_submitted: 提交订单总数
        n_orders_filled: 成交订单数 (含部分成交)
        n_orders_rejected: 拒单数
        equity_curve: 权益曲线
            - MONOTONIC_INDEX: list[float]  (每个事件一个权益点, 索引 = 事件序号)
            - WALL_CLOCK_NS:    list[Tuple[int, float]]  [(ts_event_ns, equity), ...]
        equity_timestamps_ns: list[int]  (W6.3.2: WALL_CLOCK_NS 模式下对应 equity_curve 的时间戳列表,
         MONOTONIC_INDEX=[])
        trade_records: 成交记录列表 (order_id, code, direction, price, volume)
    """

    initial_capital: float
    final_equity: float
    total_return: float
    n_events: int
    n_orders_submitted: int
    n_orders_filled: int
    n_orders_rejected: int
    # W6.3.2 新增 (均加默认值, 保证外部直接构造向后兼容)
    event_clock_mode: str = EventClockMode.MONOTONIC_INDEX
    equity_curve: list = field(default_factory=list)
    equity_timestamps_ns: list = field(default_factory=list)
    trade_records: list = field(default_factory=list)


# ============================================================
# 主引擎
# ============================================================


class EventDrivenEngine:
    """事件驱动回测主引擎 — 串联所有组件,驱动事件循环。

    生命周期:
        engine = EventDrivenEngine(strategy, matcher, latency_model)
        for event in feed:
            engine.process_event(event)
        summary = engine.get_summary()

    W6.3.2 新增 event_clock_mode 参数:
        - "MONOTONIC_INDEX" (默认): 用 _n_events 单调整数做时钟, 向后兼容,
          199 原测试零修改全通过。
        - "WALL_CLOCK_NS": 用 event.ts_event 纳秒级 UNIX 时间戳做时钟,
          强制单调校验 (防前视偏差), 权益曲线带真实时间戳, 延迟按 ready_ts 计算。
          需确保传入的 BarData/TickData 均设置了 ts_event > 0。

    线程安全:
        非线程安全 (回测为单线程模型)
    """

    def __init__(
        self,
        strategy: HedgeStrategy,
        matching_engine: MatchingEngine | None = None,
        latency_model: LatencyModel | None = None,
        initial_capital: float = 1_000_000.0,
        commission_rate: float = 0.0003,
        # W6.3.2 新增: 确定性事件时钟模式
        event_clock_mode: str = EventClockMode.MONOTONIC_INDEX,
    ) -> None:
        """
        Args:
            strategy: 对冲策略实例
            matching_engine: 撮合引擎 (None 时用默认 BAR 模式)
            latency_model: 延迟模型 (None 时用 FixedLatency(0),即次日成交)
            initial_capital: 初始现金
            commission_rate: 手续费率 (简化模型,买卖双边收取)
            event_clock_mode: W6.3.2 — "MONOTONIC_INDEX"(默认, 向后兼容) 或 "WALL_CLOCK_NS"(新语义, 需 ts_event 字段)
        """
        self._matching_engine = matching_engine or MatchingEngine(mode="BAR")
        self._latency_model = latency_model or FixedLatency(latency_ticks=0)
        self._initial_capital = initial_capital
        self._commission_rate = commission_rate

        # W6.3.2: 事件时钟模式校验 + 初始化
        if event_clock_mode not in (
            EventClockMode.MONOTONIC_INDEX,
            EventClockMode.WALL_CLOCK_NS,
        ):
            raise ValueError(
                f"event_clock_mode 必须是 '{EventClockMode.MONOTONIC_INDEX}' 或 "
                f"'{EventClockMode.WALL_CLOCK_NS}', 实际={event_clock_mode!r}"
            )
        self.event_clock_mode: str = event_clock_mode
        # WALL_CLOCK_NS 模式下记录最新 ts_event (用于单调校验 + ready_ts 计算)
        self._last_ts_event: int = 0
        # WALL_CLOCK_NS 模式下默认延迟 = 1 日 = 8.64e13 纳秒 (可覆盖, 仅作 fallback)
        self._default_latency_ns: int = 86_400_000_000_000  # 24h in ns

        # 持仓: code -> Position
        self._long_positions: dict[str, Position] = {}
        self._short_positions: dict[str, Position] = {}

        # 现金与权益
        self._cash: float = initial_capital
        self._equity: float = initial_capital

        # 队列
        self._latency_queue: deque[PendingOrder] = deque()  # 延迟中的订单
        self._matchable_queue: deque[OrderData] = deque()  # 就绪待撮合订单
        self._order_queue = OrderQueue()  # 订单生命周期管理 (活动/完成/撤单)

        # 行情与权益曲线
        self._market_prices: dict[str, float] = {}
        # W6.3.2: 双模式
        #   MONOTONIC_INDEX: [float, float, ...]                 (每个事件一个权益点)
        #   WALL_CLOCK_NS:    [(int_ns, float), (int_ns, float), ...] (时间戳 + 权益)
        self._equity_curve: list = [
            initial_capital
        ]  # 向后兼容初始化 (MONO: [cash], WALL: 首个元素是初始标量, 首事件时 append tuple)
        self._trade_records: list[dict] = []

        # 计数器
        self._n_events = 0
        self._n_submitted = 0
        self._n_filled = 0
        self._n_rejected = 0

        # 最近事件 (供延迟模型参考)
        self._last_event: MarketEvent | None = None

        # 策略适配器 — 把 self.submit_order 注入为 order_submitter
        self._adapter = StrategyAdapter(strategy, order_submitter=self.submit_order)

    # ============================================================
    # 公开 API
    # ============================================================

    def submit_order(self, order: OrderData) -> bool:
        """订单提交入口 — 作为 StrategyAdapter 的 order_submitter。

        计算延迟并放入延迟队列,本事件不会被撮合 (next-event 语义)。

        双模式行为:
            - MONOTONIC_INDEX: PendingOrder.remaining_latency = latency_model 结果 (事件数)
            - WALL_CLOCK_NS:    PendingOrder.ready_ts = 当前 _last_ts_event + latency_ns
                               (latency_model 默认值视为"日数", × _default_latency_ns 转纳秒)
        """
        if order.volume <= 0:
            return False

        raw_latency = self._latency_model.calculate_latency(
            order,
            pending_count=len(self._latency_queue),
            market_event=self._last_event,
        )

        if self.event_clock_mode == EventClockMode.MONOTONIC_INDEX:
            latency = max(0, int(raw_latency))
            pending = PendingOrder(order=order, remaining_latency=latency, ready_ts=0)
        else:  # WALL_CLOCK_NS
            # 延迟模型返回的原始值视为"日数" (FixedLatency.latency_ticks = 0 → 次日成交 = 1 day latency)
            # 与 next-event 语义对齐: 即使 latency_ticks=0, 订单也不会在提交事件撮合
            days_latency = max(1, int(raw_latency) + 1)
            latency_ns = days_latency * self._default_latency_ns
            ready_ts = self._last_ts_event + latency_ns
            pending = PendingOrder(order=order, remaining_latency=0, ready_ts=ready_ts)

        self._latency_queue.append(pending)
        self._n_submitted += 1
        return True

    def process_event(self, event: MarketEvent) -> None:
        """处理单个市场事件 — 事件循环核心。

        步骤:
            0. (WALL_CLOCK_NS 模式) 单调校验 ts_event
            1. 延迟处理 (MONO: remaining_latency 递减; WALL: ready_ts 比对) → 就绪订单入撮合队列
            2. 撮合就绪订单 → 成交回调更新持仓/现金
            3. 标记持仓最新价 → 记录权益点
            4. 分发事件给策略 (新订单进入延迟队列,本事件不撮合)

        W6.3.2: 在 WALL_CLOCK_NS 模式下, 若 event.ts_event <= _last_ts_event,
            抛 NonMonotonicTimestampError (防前视偏差硬门禁)。
        """
        # Step 0: 时钟模式处理 + 单调校验
        if self.event_clock_mode == EventClockMode.WALL_CLOCK_NS:
            curr_ts = getattr(event, "ts_event", 0) or 0
            if curr_ts <= 0:
                raise ValueError(
                    f"event_clock_mode='WALL_CLOCK_NS' 要求所有事件的 ts_event 为正的纳秒级时间戳, "
                    f"收到 code={event.code}, ts_event={curr_ts}"
                )
            if curr_ts <= self._last_ts_event:
                raise NonMonotonicTimestampError(
                    last_ts=self._last_ts_event, curr_ts=curr_ts, code=event.code
                )
            self._last_ts_event = curr_ts
        self._last_event = event
        self._n_events += 1

        # Step 1: 延迟就绪处理 + 就绪订单入撮合队列
        self._drain_latency_queue_to_matchable()

        # Step 2: 撮合就绪订单 (来自历史事件的延迟到期订单)
        self._match_ready_orders(event)

        # Step 3: 更新行情价格 + 标记持仓 + 记录权益
        self._update_market_price(event)
        self._mark_positions(event)
        self._record_equity_point()

        # Step 4: 分发给策略 (新订单进入延迟队列,本事件不撮合)
        if isinstance(event, TickData):
            self._adapter.dispatch_tick(event)
        else:
            self._adapter.dispatch_bar(event)

    def run(self, events: Iterable[MarketEvent]) -> EngineSummary:
        """运行完整回测 — 逐事件处理并返回汇总。

        Args:
            events: 市场事件可迭代对象 (TickData / BarData)

        Returns:
            EngineSummary 汇总结果
        """
        for event in events:
            self.process_event(event)
        return self.get_summary()

    def get_state(self) -> EngineSnapshot:
        """返回引擎状态快照 (不可变副本)。"""
        return EngineSnapshot(
            cash=self._cash,
            equity=self._equity,
            event_clock_mode=self.event_clock_mode,
            last_ts_event=self._last_ts_event,
            n_events_processed=self._n_events,
            n_orders_submitted=self._n_submitted,
            n_orders_filled=self._n_filled,
            n_orders_rejected=self._n_rejected,
            pending_latency_count=len(self._latency_queue),
            matchable_queue_count=len(self._matchable_queue),
            long_positions={
                k: dataclasses.asdict(v) for k, v in self._long_positions.items()
            },
            short_positions={
                k: dataclasses.asdict(v) for k, v in self._short_positions.items()
            },
            market_prices=dict(self._market_prices),
            equity_curve=list(self._equity_curve),
        )

    def get_summary(self) -> EngineSummary:
        """返回回测汇总结果 (兼容两种时钟模式)。"""
        total_return = (self._equity - self._initial_capital) / self._initial_capital

        # 两个模式分支共用同一对变量 (先声明类型, 再按模式填充, 避免 no-redef)
        curve: list[float] = []
        timestamps: list[int] = []
        if self.event_clock_mode == EventClockMode.MONOTONIC_INDEX:
            # 兼容旧语义: equity_curve = list[float], timestamps = []
            curve = list(self._equity_curve)
            timestamps = []
        else:  # WALL_CLOCK_NS
            # 拆分为: equity_curve = list[float], timestamps = list[int]
            # 注意: 首元素是初始标量 (initial_capital), 之后为 (ts, equity) 元组
            for i, item in enumerate(self._equity_curve):
                if i == 0 and isinstance(item, (int, float)):
                    curve.append(float(item))
                    timestamps.append(0)  # 初始点时间戳 = 0 (无事件)
                elif isinstance(item, tuple) and len(item) == 2:
                    timestamps.append(int(item[0]))
                    curve.append(float(item[1]))
                else:
                    # 防御性兼容
                    curve.append(
                        float(item) if not isinstance(item, tuple) else float(item[-1])
                    )
                    timestamps.append(0)

        return EngineSummary(
            initial_capital=self._initial_capital,
            final_equity=self._equity,
            total_return=total_return,
            event_clock_mode=self.event_clock_mode,
            n_events=self._n_events,
            n_orders_submitted=self._n_submitted,
            n_orders_filled=self._n_filled,
            n_orders_rejected=self._n_rejected,
            equity_curve=curve,
            equity_timestamps_ns=timestamps,
            trade_records=list(self._trade_records),
        )

    # ============================================================
    # 内部: 延迟队列管理 (双模式)
    # ============================================================

    def _drain_latency_queue_to_matchable(self) -> None:
        """W6.3.2 统一入口: 把延迟队列中所有就绪订单移入撮合队列。

        - MONOTONIC_INDEX: remaining_latency -= 1, ≤0 者移走 (传统 next-event 语义)
        - WALL_CLOCK_NS:    ready_ts <= self._last_ts_event 者移走 (真实时钟语义)
        """
        if not self._latency_queue:
            return

        still_pending: deque[PendingOrder] = deque()
        while self._latency_queue:
            po = self._latency_queue.popleft()
            ready = False
            if self.event_clock_mode == EventClockMode.MONOTONIC_INDEX:
                new_remaining = po.remaining_latency - 1
                if new_remaining <= 0:
                    ready = True
                else:
                    po = dataclasses.replace(po, remaining_latency=new_remaining)
            else:  # WALL_CLOCK_NS
                if po.ready_ts > 0 and po.ready_ts <= self._last_ts_event:
                    ready = True
            if ready:
                self._matchable_queue.append(po.order)
                self._order_queue.enqueue(po.order)
            else:
                still_pending.append(po)
        self._latency_queue = still_pending

    # ============================================================
    # 内部: 撮合 (无时钟模式依赖, 未修改)
    # ============================================================

    def _match_ready_orders(self, event: MarketEvent) -> None:
        """撮合所有就绪订单 (仅与本事件同 code 的订单)。"""
        if not self._matchable_queue:
            return

        # 筛选与本事件同 code 的订单 (不同 code 的订单保留在队列中)
        same_code: list[OrderData] = []
        other_code: deque[OrderData] = deque()
        while self._matchable_queue:
            order = self._matchable_queue.popleft()
            if order.code == event.code:
                same_code.append(order)
            else:
                other_code.append(order)
        self._matchable_queue = other_code

        if not same_code:
            return

        # 调用撮合引擎
        self._matching_engine.match(
            orders=same_code,
            market_event=event,
            on_fill=self._on_fill,
            on_partial_fill=self._on_partial_fill,
            on_reject=self._on_reject,
        )

    def _on_fill(self, order: OrderData, fill_price: float, fill_volume: float) -> None:
        """全部成交回调 — 更新持仓/现金/订单状态。"""
        self._apply_trade(order, fill_price, fill_volume)
        try:
            self._order_queue.mark_filled(order.order_id, fill_price, fill_volume)
        except KeyError:
            pass  # 订单可能已被其他路径处理
        self._n_filled += 1
        self._trade_records.append(
            {
                "order_id": order.order_id,
                "code": order.code,
                "direction": order.direction,
                "offset": order.offset,
                "price": fill_price,
                "volume": fill_volume,
                "status": "ALL_TRADED",
                "event_index": self._n_events,
                "ts_event_ns": self._last_ts_event,  # W6.3.2: 成交事件时间戳 (两种模式都填充)
            }
        )

    def _on_partial_fill(
        self, order: OrderData, fill_price: float, fill_volume: float
    ) -> None:
        """部分成交回调 — 更新持仓/现金,订单保留在活动队列。"""
        self._apply_trade(order, fill_price, fill_volume)
        try:
            self._order_queue.mark_partial(order.order_id, fill_price, fill_volume)
        except KeyError:
            pass

    def _on_reject(self, order: OrderData, reason: str) -> None:
        """拒单回调 — 标记订单被拒,不计入成交。"""
        try:
            self._order_queue.mark_rejected(order.order_id, reason)
        except KeyError:
            pass
        self._n_rejected += 1
        self._trade_records.append(
            {
                "order_id": order.order_id,
                "code": order.code,
                "direction": order.direction,
                "reason": reason,
                "status": "REJECTED",
                "event_index": self._n_events,
                "ts_event_ns": self._last_ts_event,  # W6.3.2: 拒单事件时间戳
            }
        )

    # ============================================================
    # 内部: 持仓与现金 (无时钟模式依赖, 未修改)
    # ============================================================

    def _apply_trade(
        self, order: OrderData, fill_price: float, fill_volume: float
    ) -> None:
        """应用成交到持仓和现金。

        简化模型:
            - BUY OPEN / BUY CLOSE → 多头加仓 (CLOSE 时减少空头)
            - SELL OPEN / SELL CLOSE → 空头加仓 (CLOSE 时减少多头)

        手续费: 双边收取 commission_rate * fill_value
        """
        fill_value = fill_price * fill_volume
        commission = fill_value * self._commission_rate

        if order.direction == "BUY":
            if order.offset == "OPEN":
                # 多头开仓
                pos = self._long_positions.setdefault(
                    order.code, Position(code=order.code, direction="LONG")
                )
                pos.apply_fill(fill_price, fill_volume)
            else:  # CLOSE / CLOSETODAY
                # 平空头 (独立局部变量, 避免与上方 OPEN 分支的 pos 复用)
                close_short = self._short_positions.get(order.code)
                if close_short is not None:
                    close_short.apply_fill(fill_price, -fill_volume)
            self._cash -= fill_value + commission
        else:  # SELL
            if order.offset == "OPEN":
                # 空头开仓
                pos = self._short_positions.setdefault(
                    order.code, Position(code=order.code, direction="SHORT")
                )
                pos.apply_fill(fill_price, fill_volume)
            else:  # CLOSE / CLOSETODAY
                # 平多头 (独立局部变量, 避免与上方 OPEN 分支的 pos 复用)
                close_long = self._long_positions.get(order.code)
                if close_long is not None:
                    close_long.apply_fill(fill_price, -fill_volume)
            self._cash += fill_value - commission

    def _update_market_price(self, event: MarketEvent) -> None:
        """更新行情价格表。"""
        if isinstance(event, TickData):
            self._market_prices[event.code] = event.price
        else:
            self._market_prices[event.code] = event.close

    def _mark_positions(self, event: MarketEvent) -> None:
        """用当前事件价格标记持仓 (mark-to-market)。"""
        if event.code in self._long_positions:
            self._long_positions[event.code].last_price = self._market_prices[
                event.code
            ]
        if event.code in self._short_positions:
            self._short_positions[event.code].last_price = self._market_prices[
                event.code
            ]

    def _record_equity_point(self) -> None:
        """W6.3.2: 双模式权益记录。

        MONOTONIC_INDEX (默认, 向后兼容):
            self._equity_curve.append(self._equity)  — list[float]
        WALL_CLOCK_NS:
            self._equity_curve.append((last_ts_event, self._equity))  — list[Tuple[int, float]]
            注意: 首元素初始值是标量 initial_capital, 首事件后第一个元素是 (ts, equity) 元组
        """
        long_value = sum(p.volume * p.last_price for p in self._long_positions.values())
        short_value = sum(
            p.volume * p.last_price for p in self._short_positions.values()
        )
        self._equity = self._cash + long_value - short_value

        if self.event_clock_mode == EventClockMode.MONOTONIC_INDEX:
            self._equity_curve.append(self._equity)
        else:  # WALL_CLOCK_NS
            # 初始化时 _equity_curve = [initial_capital] (标量)
            # 从第 2 个元素起写入 (ts, equity) 元组
            self._equity_curve.append((int(self._last_ts_event), float(self._equity)))


__all__ = [
    "EngineSnapshot",
    "EngineSummary",
    "EventClockMode",  # W6.3.2 新增
    "EventDrivenEngine",
    "NonMonotonicTimestampError",  # W6.3.2 新增
    "PendingOrder",
    "Position",
]
