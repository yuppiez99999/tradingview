"""
v7.5 BrokerAPI — 券商/期货接口抽象层
支持 CTP 期货接口 + Wind 终端股票接口
基于 QUANT_RESEARCH_MEMO_v7.5_INSTITUTIONAL §3
"""
import time
import logging
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from collections import defaultdict

logger = logging.getLogger(__name__)


# ---------- 数据模型 ----------
@dataclass
class OrderBook:
    symbol: str
    bid1: float = 0.0
    bid1_vol: int = 0
    ask1: float = 0.0
    ask1_vol: int = 0
    bid_volumes: List[int] = field(default_factory=lambda: [0]*5)
    ask_volumes: List[int] = field(default_factory=lambda: [0]*5)
    total_volume: int = 0
    timestamp: str = ""


@dataclass
class Order:
    order_id: str
    symbol: str
    qty: int
    side: str
    order_type: str
    price: float
    status: str = "PENDING"
    filled_qty: int = 0
    avg_price: float = 0.0
    ts: str = ""


@dataclass
class Fill:
    fill_id: str
    order_id: str
    symbol: str
    qty: int
    price: float
    side: str
    ts: str


class BrokerAPI:
    """
    v7.5 券商接口抽象基类

    实盘使用 Wind / CTP 子类，回测使用 SimulatedBroker 子类。
    """

    def __init__(self):
        self.orders: Dict[str, Order] = {}
        self.fills: List[Fill] = []
        self.positions: Dict[str, int] = defaultdict(int)
        self.cache: Dict[str, Any] = {}
        self._connected = False

    def connect(self) -> bool:
        self._connected = True
        return True

    def disconnect(self) -> bool:
        self._connected = True
        return True

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ---------- 盘口 ----------
    def get_order_book(self, symbol: str, levels: int = 5) -> Optional[dict]:
        raise NotImplementedError

    # ---------- 下单 ----------
    def place(self, symbol: str, qty: int, side: str,
              order_type: str = 'LIMIT', price: float = 0.0,
              ts: str = "") -> Optional[Order]:
        raise NotImplementedError

    # ---------- 撤单 ----------
    def cancel(self, order: Order) -> bool:
        raise NotImplementedError

    # ---------- 等待成交 ----------
    def wait_fill(self, order: Order, timeout: int = 30) -> Optional[dict]:
        raise NotImplementedError

    # ---------- 持仓 ----------
    def get_positions(self) -> Dict[str, int]:
        return dict(self.positions)

    # ---------- 成交量 profile（用于 VWAP）----------
    def get_volume_profile(self, symbol: str, window_minutes: int = 30) -> Optional[List[float]]:
        return None

    # ---------- 账户 ----------
    def get_account_info(self) -> dict:
        return {'available': 0, 'total': 0, 'margin': 0}


class SimulatedBroker(BrokerAPI):
    """回测模式：模拟盘口与撮合"""

    def __init__(self, initial_capital: float = 5_000_000,
                 commission_stock: float = 0.00025,
                 commission_futures: float = 0.000023,
                 slippage_bps: float = 2.0):
        super().__init__()
        self.capital = initial_capital
        self.available = initial_capital
        self.commission_stock = commission_stock
        self.commission_futures = commission_futures
        self.slippage_bps = slippage_bps  # 模拟滑点 bps
        self._prices: Dict[str, float] = {}  # 当前价格
        self._volumes: Dict[str, int] = {}
        self._depth_cache: Dict[str, List[float]] = {}

    def set_price(self, symbol: str, price: float, volume: int = 100000):
        self._prices[symbol] = price
        self._volumes[symbol] = volume

    def get_order_book(self, symbol: str, levels: int = 5) -> Optional[dict]:
        price = self._prices.get(symbol)
        if price is None:
            return None
        vol = self._volumes.get(symbol, 100000)
        return {
            'symbol': symbol,
            'bid1': price * 0.9999,
            'bid1_vol': vol // 2,
            'ask1': price * 1.0001,
            'ask1_vol': vol // 2,
            'total_volume': vol,
            'bid_volumes': [vol // (i+2) for i in range(5)],
            'ask_volumes': [vol // (i+2) for i in range(5)],
        }

    def place(self, symbol: str, qty: int, side: str,
              order_type: str = 'LIMIT', price: float = 0.0,
              ts: str = "") -> Optional[Order]:
        order_id = f"SIM-{uuid.uuid4().hex[:8]}"
        order = Order(
            order_id=order_id,
            symbol=symbol,
            qty=qty,
            side=side,
            order_type=order_type,
            price=price,
            ts=ts or datetime.now().isoformat()
        )
        self.orders[order_id] = order
        return order

    def cancel(self, order: Order) -> bool:
        if order.order_id in self.orders:
            order.status = "CANCELLED"
            self.orders[order.order_id] = order
            return True
        return False

    def wait_fill(self, order: Order, timeout: int = 30) -> Optional[dict]:
        price = self._prices.get(order.symbol)
        if price is None:
            return None

        # 模拟滑点
        slip = self.slippage_bps / 10000
        fill_price = price * (1 + slip) if order.side == 'BUY' else price * (1 - slip)
        fill_qty = min(order.qty, self._volumes.get(order.symbol, order.qty) // 10)

        fill = Fill(
            fill_id=f"FILL-{uuid.uuid4().hex[:8]}",
            order_id=order.order_id,
            symbol=order.symbol,
            qty=fill_qty,
            price=fill_price,
            side=order.side,
            ts=datetime.now().isoformat()
        )
        self.fills.append(fill)

        order.filled_qty = fill_qty
        order.avg_price = fill_price
        order.status = "FILLED"

        # 更新持仓
        delta = fill_qty if order.side == 'BUY' else -fill_qty
        self.positions[order.symbol] = self.positions.get(order.symbol, 0) + delta

        # 更新资金
        cost = fill_qty * fill_price
        if order.side == 'BUY':
            self.available -= cost
        else:
            self.available += cost

        return {
            'order_id': order.order_id,
            'fill_id': fill.fill_id,
            'symbol': order.symbol,
            'qty': fill_qty,
            'price': fill_price,
            'side': order.side,
            'ts': fill.ts
        }

    def get_volume_profile(self, symbol: str, window_minutes: int = 30) -> Optional[List[float]]:
        # 均匀分布
        return [1.0 / window_minutes] * window_minutes

    def get_account_info(self) -> dict:
        return {
            'available': self.available,
            'total': self.capital,
            'margin': 0,
        }
