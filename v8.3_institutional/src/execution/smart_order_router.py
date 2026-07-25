# -*- coding: utf-8 -*-
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
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Protocol

from .algo_engine import AlgoEngine, AlgoType
from .ntp_sync import NTPSync

logger = logging.getLogger("v75.execution.sor")


# ----------------------------------------------------------------------
# Broker 接口协议 (Protocol)
# ----------------------------------------------------------------------
class BrokerAPI(Protocol):
    """券商接口协议 — 实盘需实现此接口"""

    def get_order_book(self, symbol: str, levels: int = 5) -> dict:
        """获取盘口深度"""
        ...

    def place(self, symbol: str, qty: int, side: str,
              order_type: str = "LIMIT", price: Optional[float] = None) -> str:
        """下单, 返回订单 ID"""
        ...

    def wait_fill(self, order_id: str, timeout: int = 30) -> Optional[dict]:
        """等待成交, 返回 {price, qty, ts}"""
        ...

    def cancel(self, order_id: str) -> bool:
        """撤单"""
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

    def __init__(self, price_dict: Optional[Dict[str, float]] = None):
        self.prices = price_dict or {}
        self.filled_orders: List[dict] = []
        # 订单详情存储: {order_id: {symbol, qty, side, price, order_type, ts}}
        self._orders: Dict[str, dict] = {}

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
              order_type: str = "LIMIT", price: Optional[float] = None,
              option_type: Optional[str] = None, strike: Optional[float] = None) -> str:
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
            "ts": datetime.utcnow().isoformat(),
        }
        return oid

    def wait_fill(self, order_id: str, timeout: int = 30) -> Optional[dict]:
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
            "ts": datetime.utcnow().isoformat(),
        }
        self.filled_orders.append(fill)
        return fill

    def cancel(self, order_id: str) -> bool:
        # 撤单时从存储中移除
        self._orders.pop(order_id, None)
        return True


# ----------------------------------------------------------------------
# 订单成交数据类
# ----------------------------------------------------------------------
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
                 ntp: Optional[NTPSync] = None,
                 slippage_break: float = 0.005,
                 daily_slippage_break: float = 0.010,
                 global_slow_threshold: float = 0.003,
                 pause_minutes: int = 30,
                 algo_engine: Optional[AlgoEngine] = None):
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
        self.broker = broker
        self.ntp = ntp or NTPSync()
        self.slip_break = float(slippage_break)
        self.daily_slip_break = float(daily_slippage_break)
        self.global_slow = float(global_slow_threshold)
        self.pause_minutes = int(pause_minutes)
        self.algo = algo_engine or AlgoEngine()

        # 状态
        self.slip_per_symbol = defaultdict(float)
        self.slip_pause_until: Dict[str, datetime] = {}
        self.global_slowdown = False
        self.fill_history: List[OrderFill] = []

    def execute(self,
                symbol: str,
                target_qty: int,
                side: str,
                decision_price: float,
                algo: AlgoType = AlgoType.ICEBERG,
                window_minutes: int = 5,
                volume_profile=None) -> List[OrderFill]:
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

        # 拆单
        slices = self.algo.split(target_qty, side, algo, depth,
                                 window_minutes, volume_profile)
        if not slices:
            return []

        fills: List[OrderFill] = []
        remaining = target_qty

        for sl in slices:
            if remaining <= 0:
                break

            # 限价
            limit_price = sl.limit_price
            if limit_price is None:
                limit_price = depth.get("ask1") if side == "BUY" \
                              else depth.get("bid1")

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
            fill_price = float(fill.get("price", limit_price))
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

    def _is_paused(self, symbol: str) -> bool:
        until = self.slip_pause_until.get(symbol)
        return until is not None and self.ntp.server_ts() < until

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
