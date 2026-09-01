"""
v7.5 BrokerAPI — 券商/期货接口抽象层
支持 CTP 期货接口 + Wind 终端股票接口
基于 QUANT_RESEARCH_MEMO_v7.5_INSTITUTIONAL §3
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# ---------- 数据模型 ----------
@dataclass
class OrderBook:
    symbol: str
    bid1: float = 0.0
    bid1_vol: int = 0
    ask1: float = 0.0
    ask1_vol: int = 0
    bid_volumes: list[int] = field(default_factory=lambda: [0]*5)
    ask_volumes: list[int] = field(default_factory=lambda: [0]*5)
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
        self.orders: dict[str, Order] = {}
        self.fills: list[Fill] = []
        self.positions: dict[str, int] = defaultdict(int)
        self.cache: dict[str, Any] = {}
        self._connected = False

    def connect(self) -> bool:
        self._connected = True
        return True

    def disconnect(self) -> bool:
        self._connected = False
        return True

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ---------- 盘口 ----------
    def get_order_book(self, symbol: str, levels: int = 5) -> dict | None:
        raise NotImplementedError

    # ---------- 下单 ----------
    def place(self, symbol: str, qty: int, side: str,
              order_type: str = 'LIMIT', price: float = 0.0,
              ts: str = "") -> Order | None:
        raise NotImplementedError

    # ---------- 撤单 ----------
    def cancel(self, order: Order) -> bool:
        raise NotImplementedError

    # ---------- 等待成交 ----------
    def wait_fill(self, order: Order, timeout: int = 30) -> dict | None:
        raise NotImplementedError

    # ---------- 持仓 ----------
    def get_positions(self) -> dict[str, int]:
        return dict(self.positions)

    # ---------- 成交量 profile（用于 VWAP）----------
    def get_volume_profile(self, symbol: str, window_minutes: int = 30) -> list[float] | None:
        return None

    # ---------- 账户 ----------
    def get_account_info(self) -> dict:
        return {'available': 0, 'total': 0, 'margin': 0}


class SimulatedBroker(BrokerAPI):
    """回测模式：模拟盘口与撮合

    v8.4 T05 (2026-07-28): 接入 Almgren-Chriss 平方根滑点模型
        - 滑点 = σ × η × √(qty / ADV) × vol_scaling
        - 当 set_market_context(symbol, price, adv, volatility) 被调用时启用
        - 未调用时回退到固定 slippage_bps (向后兼容)
    """

    def __init__(self, initial_capital: float = 5_000_000,
                 commission_stock: float = 0.00025,
                 commission_futures: float = 0.000023,
                 slippage_bps: float = 2.0,
                 cost_model: Any | None = None,
                 slippage_coef: float = 0.142):
        super().__init__()
        self.capital = initial_capital
        self.available = initial_capital
        self.commission_stock = commission_stock
        self.commission_futures = commission_futures
        self.slippage_bps = slippage_bps  # 固定滑点 (fallback, 无 ADV 时使用)
        self.slippage_coef = slippage_coef  # Almgren-Chriss 平方根系数 η
        self._prices: dict[str, float] = {}  # 当前价格
        self._volumes: dict[str, int] = {}
        self._volatilities: dict[str, float] = {}  # 日波动率 (T05 新增)
        self._depth_cache: dict[str, list[float]] = {}
        # 外部注入的 CostModel 实例 (可选)
        self._cost_model = cost_model

    def set_price(self, symbol: str, price: float, volume: int = 100000,
                  volatility: float | None = None):
        """设置标的当前价格、成交量和波动率.

        Args:
            symbol: 标的代码
            price: 当前价格
            volume: 日成交量 (ADV), 用于 Almgren-Chriss 滑点计算
            volatility: 日波动率 (0-1), None 时默认 0.02 (2%)
        """
        self._prices[symbol] = price
        self._volumes[symbol] = volume
        self._volatilities[symbol] = volatility if volatility is not None else 0.02

    def set_market_context(self, symbol: str, price: float,
                           adv: int, volatility: float):
        """设置市场上下文 (T05 新增, Almgren-Chriss 滑点必需).

        Args:
            symbol: 标的代码
            price: 当前价格
            adv: 日均成交量 (Average Daily Volume)
            volatility: 日波动率 (如 0.02 = 2%)
        """
        self.set_price(symbol, price, volume=adv, volatility=volatility)

    def _compute_slippage_bps(self, symbol: str, qty: int) -> float:
        """计算滑点 (bps).

        优先使用 Almgren-Chriss 平方根模型 (需要 ADV + 波动率),
        缺失数据时回退到固定 slippage_bps.

        Almgren-Chriss 公式:
            slip_bps = η × σ × √(qty / ADV) × (σ / 0.02) × 10000

        Args:
            symbol: 标的代码
            qty: 委托数量

        Returns:
            滑点 bps (例如 2.0 = 0.02%)
        """
        adv = self._volumes.get(symbol, 0)
        vol = self._volatilities.get(symbol, 0.0)

        # 缺失 ADV 或波动率, 回退到固定滑点
        if adv <= 0 or vol <= 0:
            return self.slippage_bps

        # 参与率
        participation = qty / adv
        if participation <= 0:
            return self.slippage_bps

        # Almgren-Chriss 平方根模型
        # slip_bps = η × σ × √(participation) × 10000
        # (volatility_scaling: 以 2% vol 为基准, 高波动放大滑点)
        vol_scaling = vol / 0.02
        slip_bps = self.slippage_coef * vol * (participation ** 0.5) * vol_scaling * 10000

        # 上限保护: 不超过 100 bps (1%)
        slip_bps = min(slip_bps, 100.0)

        # 下限保护: 不低于固定 slippage_bps 的 10%
        slip_bps = max(slip_bps, self.slippage_bps * 0.1)

        return slip_bps

    def get_order_book(self, symbol: str, levels: int = 5) -> dict | None:
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
              ts: str = "") -> Order | None:
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

    def wait_fill(self, order: Order, timeout: int = 30) -> dict | None:
        price = self._prices.get(order.symbol)
        if price is None:
            return None

        # 模拟滑点 (T05: Almgren-Chriss 平方根模型, 随参与率+波动率缩放)
        slip_bps = self._compute_slippage_bps(order.symbol, order.qty)
        slip = slip_bps / 10000
        fill_price = price * (1 + slip) if order.side == 'BUY' else price * (1 - slip)
        # 成交量上限 (流动性约束)。未设置 volume 时默认 0 → 视为无流动性约束, 全额成交;
        # 否则按 min(委托量, 盘口参与上限) 控制部分成交, 防止无 ADV 数据时静默只成交 1/10 (EX-2)。
        avail_vol = self._volumes.get(order.symbol, 0)
        if avail_vol > 0:
            fill_qty = min(order.qty, avail_vol // 10)
        else:
            fill_qty = order.qty

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

    def get_volume_profile(self, symbol: str, window_minutes: int = 30) -> list[float] | None:
        # 均匀分布
        return [1.0 / window_minutes] * window_minutes

    def get_account_info(self) -> dict:
        return {
            'available': self.available,
            'total': self.capital,
            'margin': 0,
        }
