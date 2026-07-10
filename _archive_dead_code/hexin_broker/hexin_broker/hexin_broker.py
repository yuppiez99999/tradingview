"""
v7.5 同花顺客户端自动化 — 主适配类

实现 BrokerAPI 接口，将同花顺客户端自动化包装为统一券商接口。
SmartOrderRouter 可直接使用此类，无需任何修改。

特性:
    - 自动检测同花顺股票/期货客户端
    - 支持模拟交易模式
    - 异常时自动回退到 SimulatedBroker
"""

import logging
import time
from typing import Optional, Dict, List, Any

from .hexin_config import (
    ENABLE_CLIENT_AUTOMATION,
    SIMULATION_MODE_FALLBACK,
)
from .stock_trader import HexinStockTrader, StockOrderRequest
from .futures_trader import HexinFuturesTrader, FuturesOrderRequest
from src.execution.broker_api import BrokerAPI, Order, Fill

logger = logging.getLogger(__name__)


class HexinBroker(BrokerAPI):
    """同花顺客户端自动化适配器

    实现 BrokerAPI 接口，底层通过 pywinauto 操控同花顺客户端。
    """

    def __init__(self, enable_client: bool = True, fallback_to_sim: bool = True):
        super().__init__()
        self.enable_client = enable_client and ENABLE_CLIENT_AUTOMATION
        self.fallback_to_sim = fallback_to_sim and SIMULATION_MODE_FALLBACK

        self.stock_trader = HexinStockTrader()
        self.futures_trader = HexinFuturesTrader()
        self._connected = False
        self._use_client = False

    # ========== 连接管理 ==========

    def connect(self) -> bool:
        """尝试连接同花顺客户端，失败则使用模拟盘"""
        if not self.enable_client:
            logger.info("客户端自动化已禁用，使用模拟盘")
            return self._init_fallback()

        try:
            stock_ok = self.stock_trader.connect()
            futures_ok = self.futures_trader.connect()

            if stock_ok or futures_ok:
                self._connected = True
                self._use_client = True
                logger.info("同花顺客户端已连接 (stock=%s, futures=%s)", stock_ok, futures_ok)
                return True
        except Exception as e:
            logger.warning("连接同花顺客户端失败: %s", e)

        if self.fallback_to_sim:
            return self._init_fallback()
        return False

    def disconnect(self) -> bool:
        """断开连接"""
        self._connected = False
        self._use_client = False
        return True

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ========== 下单 ==========

    def place(self, symbol: str, qty: int, side: str,
              order_type: str = 'LIMIT', price: float = 0.0,
              ts: str = "", **kwargs) -> Optional[Order]:
        """统一下单接口（股票+期货）"""
        if self._use_client:
            return self._place_via_client(symbol, qty, side, order_type, price, ts)
        else:
            return self._place_via_sim(symbol, qty, side, order_type, price, ts)

    def _place_via_client(self, symbol: str, qty: int, side: str,
                          order_type: str, price: float, ts: str) -> Optional[Order]:
        """通过同花顺客户端下单"""
        market = self._detect_market(symbol)
        try:
            if market == "stock":
                req = StockOrderRequest(
                    symbol=symbol, qty=qty, side=side,
                    price=price, order_type=order_type
                )
                result = self.stock_trader.place_order(req)
            else:
                # 期货
                req = FuturesOrderRequest(
                    symbol=symbol, qty=qty, side=side,
                    price=price, order_type=order_type
                )
                result = self.futures_trader.place_order(req)

            order_id = f"HEXIN-{int(time.time()*1000)}"
            order = Order(
                order_id=order_id,
                symbol=symbol,
                qty=qty,
                side=side,
                order_type=order_type,
                price=price,
                status="PENDING",
                ts=ts or time.strftime("%Y-%m-%dT%H:%M:%S")
            )
            self.orders[order_id] = order
            return order
        except Exception as e:
            logger.error("同花顺客户端下单失败: %s", e)
            if self.fallback_to_sim:
                return self._place_via_sim(symbol, qty, side, order_type, price, ts)
            return None

    def _place_via_sim(self, symbol: str, qty: int, side: str,
                       order_type: str, price: float, ts: str) -> Optional[Order]:
        """通过模拟盘下单（兜底）"""
        from src.execution.broker_api import SimulatedBroker
        if not hasattr(self, '_sim_broker'):
            self._sim_broker = SimulatedBroker()
        return self._sim_broker.place(symbol, qty, side, order_type, price, ts)

    # ========== 撤单 ==========

    def cancel(self, order: Order) -> bool:
        """撤单"""
        if self._use_client:
            market = self._detect_market(order.symbol)
            try:
                if market == "stock":
                    return self.stock_trader.cancel_order(order.order_id)
                else:
                    return self.futures_trader.cancel_order(order.order_id)
            except Exception as e:
                logger.error("同花顺客户端撤单失败: %s", e)
                return False
        return False

    # ========== 成交回报 ==========

    def wait_fill(self, order: Order, timeout: int = 30) -> Optional[dict]:
        """等待成交"""
        if self._use_client:
            return self._wait_fill_via_client(order, timeout)
        return self._sim_broker.wait_fill(order, timeout) if hasattr(self, '_sim_broker') else None

    def _wait_fill_via_client(self, order: Order, timeout: int = 30) -> Optional[dict]:
        """通过同花顺客户端真实界面确认成交"""
        import time as _time
        from pywinauto.keyboard import send_keys

        symbol = order.symbol
        market = self._detect_market(symbol)
        deadline = _time.time() + timeout
        last_err = None

        while _time.time() < deadline:
            try:
                if market == "stock":
                    orders = self.stock_trader.query_orders(symbol=symbol, side=order.side)
                else:
                    orders = self.futures_trader.query_orders(symbol=symbol, side=order.side)

                matched = None
                for item in orders:
                    code = str(item.get("code", ""))
                    if symbol and code and (symbol in code or code in symbol):
                        matched = item
                        break

                if matched:
                    status = str(matched.get("status", ""))
                    if any(key in status for key in ["已成交", "已成", "全部成交", "FILLED", "PARTIAL"]):
                        fill = {
                            'order_id': order.order_id,
                            'fill_id': f"FILL-{int(_time.time()*1000)}",
                            'symbol': order.symbol,
                            'qty': order.qty,
                            'price': order.price,
                            'side': order.side,
                            'ts': _time.strftime("%Y-%m-%dT%H:%M:%S")
                        }
                        self.fills.append(Fill(
                            fill_id=fill['fill_id'],
                            order_id=fill['order_id'],
                            symbol=fill['symbol'],
                            qty=fill['qty'],
                            price=fill['price'],
                            side=fill['side'],
                            ts=fill['ts']
                        ))
                        return fill
            except Exception as e:
                last_err = e

            _time.sleep(2)

        if last_err:
            logger.warning("wait_fill 客户端校验异常: %s", last_err)
        return None

    # ========== 持仓与账户 ==========

    def get_positions(self) -> Dict[str, int]:
        """获取当前持仓"""
        if self._use_client:
            positions = {}
            try:
                stock_pos = self.stock_trader.query_positions()
                positions.update(stock_pos)
                futures_pos = self.futures_trader.query_positions()
                positions.update(futures_pos)
            except Exception as e:
                logger.error("查询同花顺持仓失败: %s", e)
            return positions
        return self._sim_broker.get_positions() if hasattr(self, '_sim_broker') else {}

    def get_account_info(self) -> dict:
        """获取账户信息"""
        if self._use_client:
            account = {"available": 0.0, "total": 0.0, "margin": 0.0}
            try:
                stock_acc = self.stock_trader.query_account()
                account.update(stock_acc)
                futures_acc = self.futures_trader.query_account()
                # 合并期货账户信息
                account["margin"] = futures_acc.get("margin", account.get("margin", 0.0))
            except Exception as e:
                logger.error("查询同花顺账户失败: %s", e)
            return account
        return self._sim_broker.get_account_info() if hasattr(self, '_sim_broker') else {}

    def get_volume_profile(self, symbol: str, window_minutes: int = 30) -> Optional[List[float]]:
        """成交量 profile"""
        return None

    # ========== 内部方法 ==========

    def _init_fallback(self) -> bool:
        """初始化回退模拟盘"""
        from src.execution.broker_api import SimulatedBroker
        self._sim_broker = SimulatedBroker()
        self._connected = True
        self._use_client = False
        logger.info("已回退到模拟盘模式")
        return True

    @staticmethod
    def _detect_market(symbol: str) -> str:
        """识别股票/期货"""
        s = str(symbol).strip().upper()
        # 股票：6位数字
        clean = s
        for prefix in ("SH", "SZ", "BJ"):
            if clean.startswith(prefix) and len(clean) > len(prefix):
                clean = clean[len(prefix):]
                break
        if len(clean) == 6 and clean.isdigit():
            return "stock"
        # 期货：字母+数字
        if any(c.isalpha() for c in clean) and any(c.isdigit() for c in clean):
            return "futures"
        return "stock"
