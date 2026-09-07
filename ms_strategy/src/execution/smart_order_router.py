"""
v7.5 智能订单路由 (SOR) —— Iceberg + 滑点熔断 + NTP 时间戳

特性:
    - 单笔委托 ≤ 盘口深度 10% (Iceberg)
    - 滑点 > 0.5% 立即撤单
    - 同标的日内累计滑点 > 1.0% 暂停 30 分钟
    - 全市场滑点中位数 > 0.3% 全局降速 50%
    - 每笔订单携带 server_ts + local_ts + ntp_offset
"""
from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from .algo_engine import AlgoEngine, AlgoType
from .ntp_sync import NTPSync

logger = logging.getLogger("v75.execution.sor")


def _utcnow_iso() -> str:
    """当前 UTC 时间戳字符串 (naive isoformat, 与 ntp_sync server_ts UTC 口径一致;
    R3-20260907: 消除 datetime.utcnow 弃用告警并显式标注 UTC)"""
    return datetime.now(UTC).replace(tzinfo=None).isoformat()


# ----------------------------------------------------------------------
# Broker 接口协议 (Protocol)
# ----------------------------------------------------------------------
class BrokerAPI(Protocol):
    """券商接口协议 — 实盘需实现此接口"""

    def get_order_book(self, symbol: str, levels: int = 5) -> dict:
        """获取盘口深度"""
        ...

    def place(self, symbol: str, qty: int, side: str,
              order_type: str = "LIMIT", price: float | None = None) -> str:
        """下单, 返回订单 ID"""
        ...

    def wait_fill(self, order_id: str, timeout: int = 30) -> dict | None:
        """等待成交, 返回 {price, qty, ts}"""
        ...

    def cancel(self, order_id: str) -> bool:
        """撤单"""
        ...

    def get_account_info(self) -> dict:
        """查询账户资金 {available, total, margin} (资金校验/期权保证金检查依赖)"""
        ...


# ----------------------------------------------------------------------
# Mock Broker (回测/演示用)
# ----------------------------------------------------------------------
class MockBroker:
    """模拟券商: 用于回测与单元测试

    维护订单详情字典, wait_fill() 返回 place() 时存储的 symbol/qty/price,
    并模拟 ±0.05% 滑点 (BUY 上浮, SELL 下浮), 保证滑点远低于 0.5% 熔断阈值。
    """

    # 模拟滑点: ±0.05% (远低于 SOR 默认 0.5% 单笔熔断 / 1.0% 日内累计熔断)
    MOCK_SLIPPAGE = 0.0005

    # 限价相对盘口的保护幅度，避免 limit_price 贴盘口导致滑点熔断
    LIMIT_PRICE_BUFFER = 0.0005

    def __init__(self, price_dict: dict[str, float] | None = None):
        self.prices = price_dict or {}
        self.filled_orders: list[dict] = []
        # 订单详情存储: {order_id: {symbol, qty, side, price, order_type, ts}}
        self._orders: dict[str, dict] = {}

    def get_order_book(self, symbol: str, levels: int = 5) -> dict:
        p = float(self.prices.get(symbol, 10.0))
        buf = self.LIMIT_PRICE_BUFFER
        return {
            "bid1": p * (1.0 - buf), "bid1_vol": 5000,
            "ask1": p * (1.0 + buf), "ask1_vol": 5000,
            "bid2": p * (1.0 - buf * 2), "bid2_vol": 8000,
            "ask2": p * (1.0 + buf * 2), "ask2_vol": 8000,
        }

    def place(self, symbol: str, qty: int, side: str,
              order_type: str = "LIMIT", price: float | None = None,
              option_type: str | None = None, strike: float | None = None) -> str:
        oid = f"ORD-{symbol}-{int(time.time() * 1000)}-{qty}"
        # 限价未指定则用盘口价
        if price is None:
            book = self.get_order_book(symbol)
            price = book["ask1"] if side == "BUY" else book["bid1"]
        # 存储订单详情供 wait_fill 使用
        self._orders[oid] = {
            "symbol": symbol,
            "qty": int(qty),
            "side": side,
            "price": float(price),
            "order_type": order_type,
            "option_type": option_type,
            "strike": float(strike) if strike is not None else None,
            "ts": _utcnow_iso(),
        }
        return oid

    def wait_fill(self, order_id: str, timeout: int = 30) -> dict | None:
        # 从存储读取订单详情; 若未找到则返回 None (模拟未成交)
        order = self._orders.get(order_id)
        if order is None:
            return None

        # 模拟滑点: BUY 上浮 +0.05%, SELL 下浮 -0.05%
        slip = self.MOCK_SLIPPAGE
        if order["side"] == "BUY":
            fill_price = order["price"] * (1.0 + slip)
        else:
            fill_price = order["price"] * (1.0 - slip)

        fill = {
            "order_id": order_id,
            "symbol": order["symbol"],
            "price": round(fill_price, 4),
            "qty": int(order["qty"]),
            "side": order["side"],
            "order_type": order.get("order_type", "LIMIT"),
            "option_type": order.get("option_type"),
            "strike": order.get("strike"),
            "ts": _utcnow_iso(),
        }
        self.filled_orders.append(fill)
        return fill

    def cancel(self, order_id: str) -> bool:
        # 撤单时从存储中移除
        self._orders.pop(order_id, None)
        return True

    def get_account_info(self) -> dict:
        """模拟账户资金 (SmartOrderRouter 资金校验/期权保证金依赖, EX-4/EX-12)"""
        return {"available": 1_000_000.0, "total": 1_000_000.0, "margin": 0.0}


# ----------------------------------------------------------------------
# 订单成交数据类
# ----------------------------------------------------------------------
# EX-1 (2026-08-24): SmartOrderRouter 依赖的 broker 核心方法契约 (运行前校验用)
REQUIRED_BROKER_METHODS = ("get_order_book", "place", "wait_fill", "cancel", "get_account_info")


@dataclass
class OrderFill:
    """成交回报"""
    order_id: str
    symbol: str
    side: str
    fill_price: float
    fill_qty: int
    decision_price: float
    slippage: float
    server_ts: datetime
    local_ts: datetime
    ntp_offset: float
    algo: str = "ICEBERG"
    status: str = "FILLED"     # FILLED / PARTIAL / CANCELLED / SLIPPAGE_BREAK


# ----------------------------------------------------------------------
# 智能订单路由
# ----------------------------------------------------------------------
class SmartOrderRouter:
    """v7.5 智能订单路由"""

    def __init__(self,
                 broker: BrokerAPI,
                 ntp: NTPSync | None = None,
                 slippage_break: float = 0.005,
                 daily_slippage_break: float = 0.010,
                 global_slow_threshold: float = 0.003,
                 pause_minutes: int = 30,
                 algo_engine: AlgoEngine | None = None):
        """
        Args:
            broker: 券商接口
            ntp: NTP 同步器 (None 则新建)
            slippage_break: 单笔滑点熔断阈值
            daily_slippage_break: 日内累计滑点熔断
            global_slow_threshold: 全局降速阈值
            pause_minutes: 触发熔断后暂停分钟数
            algo_engine: 拆单算法引擎
        """
        # EX-1 (2026-08-24): 运行时契约校验 — 传入 broker 必须具备 SOR 依赖的全部核心方法,
        # 否则运行中调用会 AttributeError (EX-7 同类问题的系统性防御, fail-fast 而非运行中崩)。
        missing = [m for m in REQUIRED_BROKER_METHODS if not callable(getattr(broker, m, None))]
        if missing:
            raise TypeError(f"EX-1 契约校验失败: broker {type(broker).__name__} 缺失方法 {missing}")

        self.broker = broker
        self.ntp = ntp or NTPSync()
        self.slip_break = float(slippage_break)
        self.daily_slip_break = float(daily_slippage_break)
        self.global_slow = float(global_slow_threshold)
        self.pause_minutes = int(pause_minutes)
        self.algo = algo_engine or AlgoEngine()

        # 状态
        self.slip_per_symbol = defaultdict(float)  # type: ignore[assignment]
        self.slip_pause_until: dict[str, datetime] = {}
        self.global_slowdown = False
        self.fill_history: list[OrderFill] = []

    def execute(self,
                symbol: str,
                target_qty: int,
                side: str,
                decision_price: float,
                algo: AlgoType = AlgoType.ICEBERG,
                window_minutes: int = 5,
                volume_profile=None) -> list[OrderFill]:
        """执行订单

        Args:
            symbol: 标的代码
            target_qty: 目标数量
            side: BUY / SELL
            decision_price: 决策价 (用于计算滑点)
            algo: 拆单算法
            window_minutes: 时间窗口
            volume_profile: VWAP 用的历史成交量

        Returns:
            成交回报列表
        """
        if target_qty <= 0:
            return []

        # 检查是否被暂停
        if self._is_paused(symbol):
            logger.warning("标的 %s 处于暂停状态, 跳过", symbol)
            return [OrderFill(
                order_id="", symbol=symbol, side=side,
                fill_price=0, fill_qty=0,
                decision_price=decision_price,
                slippage=0,
                server_ts=self.ntp.server_ts(),
                local_ts=self.ntp.local_ts(),
                ntp_offset=self.ntp.offset_seconds,
                status="PAUSED")]

        # 获取盘口
        depth = self.broker.get_order_book(symbol, levels=5)

        # ---------- P1-3: 期权熔断检查 ----------
        option_check = self._check_option_risk(symbol, side, target_qty, depth)
        if option_check["blocked"]:
            logger.error("期权风控熔断: %s — %s", symbol, option_check["reason"])
            return [OrderFill(
                order_id="", symbol=symbol, side=side,
                fill_price=0, fill_qty=0,
                decision_price=decision_price,
                slippage=0,
                server_ts=self.ntp.server_ts(),
                local_ts=self.ntp.local_ts(),
                ntp_offset=self.ntp.offset_seconds,
                status="OPTION_BLOCKED")]
        # -------------------------------------------

        # 拆单
        slices = self.algo.split(target_qty, side, algo, depth,
                                 window_minutes, volume_profile)
        if not slices:
            return []

        fills: list[OrderFill] = []
        remaining = target_qty

        for sl in slices:
            if remaining <= 0:
                break

            # 限价
            limit_price = sl.limit_price
            if limit_price is None:
                limit_price = depth.get("bid1") if side == "BUY" \
                              else depth.get("ask1")

            # ---------- 资金校验 (P0修复) ----------
            if side == "BUY":
                account = self.broker.get_account_info()  # type: ignore[misc]
                available = float(account.get("available", 0.0))
                required = sl.quantity * (limit_price or decision_price)
                estimated_cost = required * 1.005  # 手续费预留 0.5%
                if estimated_cost > available:
                    logger.warning(
                        "资金不足, 跳过剩余拆单: 需要 ¥%.0f, 可用 ¥%.0f (symbol=%s, slice=%d/%d)",
                        estimated_cost, available, symbol,
                        fills.__len__() + 1, len(slices)
                    )
                    break
            # -------------------------------------------

            # 下单
            order_id = self.broker.place(
                symbol, sl.quantity, side,
                order_type="LIMIT", price=limit_price)

            # 等待成交
            fill = self.broker.wait_fill(order_id, timeout=30)

            if fill is None:
                self.broker.cancel(order_id)
                logger.warning("订单 %s 超时未成交, 已撤单", order_id)
                continue

            # 计算滑点
            fill_price = float(fill.get("price", limit_price))  # type: ignore[index]
            fill_qty = int(fill.get("qty", sl.quantity))
            slip = abs(fill_price - decision_price) / decision_price \
                if decision_price > 0 else 0.0

            of = OrderFill(
                order_id=order_id,
                symbol=symbol,
                side=side,
                fill_price=fill_price,
                fill_qty=fill_qty,
                decision_price=decision_price,
                slippage=float(slip),
                server_ts=self.ntp.server_ts(),
                local_ts=self.ntp.local_ts(),
                ntp_offset=self.ntp.offset_seconds,
                algo=sl.algo,
                status="FILLED" if slip < self.slip_break else "SLIPPAGE_BREAK",
            )
            fills.append(of)
            self.fill_history.append(of)
            remaining -= fill_qty

            # 滑点熔断
            if slip > self.slip_break:
                logger.warning("滑点熔断: %s 滑点 %.4f > %.4f, 撤单",
                               symbol, slip, self.slip_break)
                self.slip_per_symbol[symbol] += slip
                if self.slip_per_symbol[symbol] > self.daily_slip_break:
                    self.slip_pause_until[symbol] = (
                        self.ntp.server_ts() + timedelta(minutes=self.pause_minutes))
                    logger.error("标的 %s 日内累计滑点 %.4f > %.4f, 暂停 %d 分钟",
                                 symbol, self.slip_per_symbol[symbol],
                                 self.daily_slip_break, self.pause_minutes)
                break

            # 节流 (AlgoEngine 或 AlgoConfig 均可)
            throttle = getattr(self.algo, "throttle",
                               getattr(self.algo, "throttle_seconds", 0))
            if throttle and throttle > 0:
                time.sleep(throttle)

        # 全局滑点检查
        self._check_global_slippage()

        return fills

    # ---------- 算法执行入口 (AlgoEngine 调用) ----------
    # 统一走 execute()，按 algo 映射拆单算法。这些方法是 AlgoEngine.execute_order
    # 的契约依赖，缺失会导致 AttributeError（EX-7 修复）。

    def execute_twap(self, symbol: str, target_qty: int, side: str,
                     decision_price: float, window_minutes: int = 5,
                     volume_profile=None) -> list[OrderFill]:
        """TWAP 执行 — 委托给 execute()"""
        return self.execute(symbol, target_qty, side, decision_price,
                            algo=AlgoType.TWAP, window_minutes=window_minutes,
                            volume_profile=volume_profile)

    def execute_vwap(self, symbol: str, target_qty: int, side: str,
                     decision_price: float, window_minutes: int = 30,
                     volume_profile=None) -> list[OrderFill]:
        """VWAP 执行 — 委托给 execute()"""
        return self.execute(symbol, target_qty, side, decision_price,
                            algo=AlgoType.VWAP, window_minutes=window_minutes,
                            volume_profile=volume_profile)

    def execute_pov(self, symbol: str, target_qty: int, side: str,
                    decision_price: float, participation_rate: float = 0.1,
                    volume_profile=None) -> list[OrderFill]:
        """POV 执行 — 委托给 execute()"""
        return self.execute(symbol, target_qty, side, decision_price,
                            algo=AlgoType.POV, window_minutes=5,
                            volume_profile=volume_profile)

    def _is_paused(self, symbol: str) -> bool:
        until = self.slip_pause_until.get(symbol)
        return until is not None and self.ntp.server_ts() < until

    # P1-3: 期权代码正则
    _OPTION_CODE_PATTERN = re.compile(
        r'^(\d{6})([CP])(\d{2})(\d{2})(M\d{5})\.(SH|SZ)$'
    )

    def _check_option_risk(self, symbol: str, side: str,
                           quantity: int, depth: dict | None = None) -> dict:
        """P1-3: 期权风控熔断检查

        规则:
        1. 期权买方 (BUY + OPEN): 最大亏损 = 权利金, 风险可控
        2. 期权卖方 (SELL + OPEN): 裸卖空 = 无限亏损风险, 必须检查保证金
        3. 平仓 (CLOSE): 风险可控

        Returns:
            {"blocked": bool, "reason": str}
        """
        # 非期权代码, 跳过
        if not self._OPTION_CODE_PATTERN.match(symbol):
            return {"blocked": False, "reason": ""}

        match = self._OPTION_CODE_PATTERN.match(symbol)
        opt_type = "CALL" if match.group(2) == "C" else "PUT"  # type: ignore[misc]
        # 买方: 风险可控 (最大亏损 = 权利金)
        if side == "BUY":
            if depth:
                ask_price = depth.get("ask1", 0)
                max_loss = ask_price * quantity * 10000  # ETF 期权合约单位
                logger.info(
                    "期权买方风险: %s 最大亏损 ¥%.0f (权利金)", symbol, max_loss
                )
            return {"blocked": False, "reason": ""}

        # 卖方: 裸卖空风险
        if side == "SELL":
            account = self.broker.get_account_info()  # type: ignore[misc]
            available = float(account.get("available", 0.0))

            # 估算所需保证金
            if depth:
                underlying_price = (depth.get("bid1", 0) + depth.get("ask1", 0)) / 2
            else:
                underlying_price = 0

            strike_str = match.group(5)[1:]  # type: ignore[misc]
            strike = float(strike_str) / 1000.0

            # 简单保证金估算 (上交所规则)
            if opt_type == "CALL":
                otm_amount = max(0, strike - underlying_price)
                required_margin_per = 0.05 + max(
                    underlying_price * 0.15 - otm_amount,
                    underlying_price * 0.07,
                )
            else:
                otm_amount = max(0, underlying_price - strike)
                required_margin_per = 0.05 + max(
                    underlying_price * 0.15 - otm_amount,
                    strike * 0.07,
                )
            required_margin = required_margin_per * quantity * 10000

            if required_margin > available * 0.80:
                return {
                    "blocked": True,
                    "reason": (
                        f"期权卖方保证金不足: 需要 ¥{required_margin:,.0f}, "
                        f"可用 ¥{available:,.0f} ({opt_type} 卖方裸卖空风险)"
                    ),
                }

            logger.info(
                "期权卖方保证金: %s 需要 ¥%.0f, 可用 ¥%.0f (%.1f%%)",
                symbol, required_margin, available,
                required_margin / available * 100 if available > 0 else 0,
            )

        return {"blocked": False, "reason": ""}

    def _check_global_slippage(self):
        """全局滑点中位数检查"""
        recent = [f.slippage for f in self.fill_history[-50:]]
        if len(recent) < 10:
            return
        import statistics
        med = statistics.median(recent)
        if med > self.global_slow and not self.global_slowdown:
            self.global_slowdown = True
            logger.warning("全局滑点中位数 %.4f > %.4f, 启用降速",
                           med, self.global_slow)
        elif med < self.global_slow * 0.5 and self.global_slowdown:
            self.global_slowdown = False
            logger.info("全局滑点恢复正常 (%.4f), 解除降速", med)

    def snapshot(self) -> dict:
        return {
            "ntp": self.ntp.snapshot(),
            "global_slowdown": self.global_slowdown,
            "paused_symbols": [s for s, t in self.slip_pause_until.items()
                               if t > self.ntp.server_ts()],
            "slip_per_symbol": dict(self.slip_per_symbol),
            "total_fills": len(self.fill_history),
        }
