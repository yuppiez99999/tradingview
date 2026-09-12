"""
QMT 实盘券商接口 (QmtBrokerAPI)

基于 xtquant.xttrader 的 passorder 异步下单机制:
- passorder() 返回 order_id 后立即返回, 订单状态异步更新
- 通过 get_trade_detail_data() 轮询确认成交
- 通过回调函数 on_order_update / on_trade 处理状态变更
- 支持撤单、资金查询、持仓查询

与 SimulatedBroker 的区别:
- SimulatedBroker: 同步、立即成交、回测用
- QmtBrokerAPI: 异步、轮询确认、实盘用

用法:
    broker = QmtBrokerAPI(account_id="800123456", session_id=123456)
    broker.connect()
    order = broker.place("510300.SH", 1000, "BUY", price=4.500)
    fill = broker.wait_fill(order, timeout=30)  # 轮询等待成交
    account = broker.get_account_info()  # 查询资金
"""
from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .broker_api import BrokerAPI, Fill, Order

logger = logging.getLogger(__name__)

# 尝试导入 xtquant, 不可用时降级为 None
try:
    from xtquant import xtconstant, xtdata, xttrader
    XTQUANT_AVAILABLE = True
except ImportError:
    XTQUANT_AVAILABLE = False
    logger.warning("xtquant 未安装, QmtBrokerAPI 将不可用")


# ============================================================
# QMT 订单状态常量
# ============================================================

# passorder 返回的 order_id 状态映射
QMT_ORDER_STATUS = {
    48: "NOT_REPORTED",    # 未报
    49: "REPORTED",        # 待报
    50: "REPORTED",        # 已报
    51: "REPORTED",        # 已报待撤
    52: "PART_CANCELLED",  # 部成待撤
    53: "PART_TRADED",     # 部成
    54: "CANCELLED",       # 已撤
    55: "ALL_TRADED",      # 已成
    56: "ALL_TRADED",      # 已成
    57: "CANCELLED",       # 已撤
    58: "CANCELLED",       # 已撤
}


class QmtBrokerAPI(BrokerAPI):
    """QMT 实盘交易接口

    封装 xtquant.xttrader.XtQuantTrader 的 passorder 异步下单:
    1. connect() → 连接 QMT 终端
    2. place()  → 调用 passorder(), 返回 order_id
    3. wait_fill() → 轮询 get_trade_detail_data() 等待成交
    4. cancel() → 调用 cancel_order()
    """

    ACCOUNT_TYPE_STOCK = "STOCK"
    ACCOUNT_TYPE_FUTURE = "FUTURE"
    ACCOUNT_TYPE_CREDIT = "CREDIT"

    def __init__(
        self,
        account_id: str = "",
        session_id: int = 0,
        account_type: str = "STOCK",
        path: str = "",
        # 资金校验
        min_cash_buffer: float = 5000.0,  # 最低资金缓冲 5000元
        # 委托参数
        default_order_type: int = 1101,   # 1101=股票买入, 1102=股票卖出
        quick_trade: int = 2,             # 0=普通, 1=立即, 2=市价
    ):
        super().__init__()
        if not XTQUANT_AVAILABLE:
            raise RuntimeError("xtquant 未安装, 无法使用 QmtBrokerAPI")

        self.account_id = account_id
        self.session_id = session_id
        self.account_type = account_type
        self.path = path
        self.min_cash_buffer = float(min_cash_buffer)
        self.default_order_type = int(default_order_type)
        self.quick_trade = int(quick_trade)

        # QMT 客户端
        self._xt_trader: xttrader.XtQuantTrader | None = None
        self._callbacks: dict[str, Callable] = {}  # order_id → callback
        self._pending_orders: dict[str, Order] = {}  # order_id → Order

        # 资金/持仓缓存
        self._account_cache: dict[str, Any] = {}
        self._position_cache: dict[str, dict[str, Any]] = {}
        self._last_account_update: float = 0.0

        # 连接状态
        self._connected = False
        self._connection_error: str = ""

    # ------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------

    def connect(self) -> bool:
        """连接 QMT 终端"""
        if not XTQUANT_AVAILABLE:
            self._connection_error = "xtquant 未安装"
            return False

        try:
            self._xt_trader = xttrader.XtQuantTrader(
                self.path, self.session_id, self.account_type
            )
            # 注册回调
            self._xt_trader.register_callback(self._on_xt_callback)
            # 启动交易线程
            self._xt_trader.start()
            # 建立连接
            connect_result = self._xt_trader.connect()
            if connect_result != 0:
                self._connection_error = f"连接失败: code={connect_result}"
                logger.error(self._connection_error)
                return False

            # 订阅账户
            self._xt_trader.subscribe(self.account_id)
            self._connected = True
            logger.info("QMT 连接成功: account=%s, session=%d",
                        self.account_id, self.session_id)
            return True

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as exc:  # noqa: E501

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            self._connection_error = str(exc)
            logger.error("QMT 连接异常: %s", exc, exc_info=True)
            return False

    def disconnect(self) -> bool:
        """断开 QMT 连接并清理所有状态

        P0-7 FIX: 原代码仅停止 _xt_trader 但未清理 _callbacks / _pending_orders
        等缓存, 重连后旧 callback 可能触发订单状态错乱。
        """
        self._connected = False
        if self._xt_trader:
            try:
                self._xt_trader.stop()
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):  # noqa: E501
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                pass
        # P0-7 FIX: 清理所有缓存状态, 保证重连后状态机干净
        self._callbacks.clear()
        self._pending_orders.clear()
        self._account_cache.clear()
        self._position_cache.clear()
        self._last_account_update = 0.0
        self._connection_error = ""
        self._xt_trader = None
        return True

    @property
    def is_connected(self) -> bool:
        return self._connected and self._xt_trader is not None

    # ------------------------------------------------------------
    # QMT 回调
    # ------------------------------------------------------------

    def _on_xt_callback(self, data: Any):
        """QMT 回调处理 (订单状态变更 / 成交回报)"""
        try:
            data_type = data.get("type", "")
            if data_type == "order":
                self._on_order_update(data)
            elif data_type == "trade":
                self._on_trade(data)
            elif data_type == "account":
                self._on_account_update(data)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as exc:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error("QMT 回调处理异常: %s", exc, exc_info=True)

    def _on_order_update(self, data: dict):
        """订单状态变更回调"""
        order_id = str(data.get("order_id", ""))
        status = data.get("status", 0)
        status_str = QMT_ORDER_STATUS.get(status, f"UNKNOWN_{status}")

        if order_id in self._pending_orders:
            self._pending_orders[order_id].status = status_str

        # 触发用户回调
        cb = self._callbacks.get(order_id)
        if cb:
            try:
                cb(data)
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):  # noqa: E501
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                pass

        # 已终态则清理回调
        if status_str in ("ALL_TRADED", "CANCELLED"):
            self._callbacks.pop(order_id, None)

    def _on_trade(self, data: dict):
        """成交回报回调"""
        fill = Fill(
            fill_id=str(data.get("trade_id", uuid.uuid4().hex[:8])),
            order_id=str(data.get("order_id", "")),
            symbol=str(data.get("code", "")),
            qty=int(data.get("volume", 0)),
            price=float(data.get("price", 0.0)),
            side=str(data.get("direction", "BUY")),
            ts=now_bj().isoformat(),
        )
        self.fills.append(fill)

    def _on_account_update(self, data: dict):
        """账户资金更新回调"""
        self._account_cache = data
        self._last_account_update = time.time()

    # ------------------------------------------------------------
    # 下单 (passorder)
    # ------------------------------------------------------------

    def place(self, symbol: str, qty: int, side: str,
              order_type: str = "LIMIT", price: float = 0.0,
              ts: str = "", callback: Callable | None = None) -> Order | None:
        """QMT 异步下单

        Args:
            symbol: 标的代码 (如 "510300.SH")
            qty: 数量
            side: "BUY" / "SELL"
            order_type: "LIMIT" / "MARKET" / "FAK" / "FOK"
            price: 限价 (0=市价)
            ts: 时间戳
            callback: 成交回调 fn(data)

        Returns:
            Order 对象 (order_id 为 QMT 返回的委托编号)
        """
        if not self.is_connected:
            logger.error("QMT 未连接, 无法下单")
            return None

        try:
            # EX-5 修复 (P0): 纠正 orderStock 参数语义错位。
            # 官方签名: orderStock(account, stock_code, order_type, order_volume,
            #                      price_type, price, strategy_name, order_remark)
            #  - order_type (第3参) = 买卖方向 (xtconstant.STOCK_BUY/STOCK_SELL)
            #  - price_type (第5参) = 报价类型 (xtconstant.FIX_PRICE/LATEST_PRICE/MARKET_*)
            # 原代码把价格类型传给 orderType、买卖方向传给 priceType, 两处互换;
            # account 传字符串而非 StockAccount 对象; price=-1 无对应报价类型。
            # 注意: xtquant 未安装时本方法不可达 (XTQUANT_AVAILABLE 门控), 修复不改变门控。

            # 买卖方向 (order_type, 第3参)
            qt_direction = xtconstant.STOCK_BUY if side == "BUY" else xtconstant.STOCK_SELL

            # 报价类型 (price_type, 第5参)
            if order_type == "MARKET" or price <= 0:
                qt_price_type = xtconstant.LATEST_PRICE
            elif order_type == "FAK":
                qt_price_type = xtconstant.MARKET_SZ_INSTBUSI_RESTCANCEL
            elif order_type == "FOK":
                qt_price_type = xtconstant.MARKET_SZ_FULL_OR_CANCEL
            else:
                qt_price_type = xtconstant.FIX_PRICE

            # account 应为 StockAccount 对象 (按官方 API 要求)
            try:
                from xtquant.xttype import StockAccount
                qt_account = StockAccount(self.account_id)
            except Exception:  # noqa: BLE001  # 构造失败则退回字符串, 由 QMT 侧校验
                logger.warning("[QmtBrokerAPI] StockAccount 构造失败, 回退字符串 account")
                qt_account = self.account_id

            # 调用 orderStock (异步下单)
            order_id = self._xt_trader.orderStock(  # type: ignore[union-attr]
                account=qt_account,
                stockCode=symbol,
                orderType=qt_direction,
                orderVolume=qty,
                priceType=qt_price_type,
                price=price if price > 0 else 0.0,
                strategyName="v7.5",
                orderRemark="",
            )

            order = Order(
                order_id=str(order_id),
                symbol=symbol,
                qty=qty,
                side=side,
                order_type=order_type,
                price=price,
                status="REPORTED",
                ts=ts or now_bj().isoformat(),
            )
            self.orders[order_id] = order
            self._pending_orders[str(order_id)] = order

            if callback:
                self._callbacks[str(order_id)] = callback

            logger.info("QMT 下单: %s %s %s qty=%d price=%.4f order_id=%s",
                        symbol, side, order_type, qty, price, order_id)
            return order

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as exc:  # noqa: E501

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error("QMT 下单异常: %s %s qty=%d: %s",
                        symbol, side, qty, exc, exc_info=True)
            return None

    # ------------------------------------------------------------
    # 撤单
    # ------------------------------------------------------------

    def cancel(self, order: Order) -> bool:
        """撤单"""
        if not self.is_connected:
            return False
        try:
            result = self._xt_trader.cancelOrder(  # type: ignore[union-attr]
            self.account_id, order.order_id
            )
            return result == 0
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as exc:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error("撤单异常: %s: %s", order.order_id, exc)
            return False

    # ------------------------------------------------------------
    # 等待成交 (轮询 get_trade_detail_data)
    # ------------------------------------------------------------

    def wait_fill(self, order: Order, timeout: int = 30) -> dict | None:
        """轮询等待订单成交

        每 0.5 秒查询一次 get_trade_detail_data, 直到:
        - 全部成交 (status=56) → 返回 fill dict
        - 已撤/已拒 (status=57/58) → 返回 None
        - 超时 → 撤单并返回 None

        Args:
            order: 订单对象
            timeout: 超时秒数

        Returns:
            {"order_id", "fill_id", "symbol", "qty", "price", "side", "ts"} 或 None
        """
        if not self.is_connected:
            return None

        deadline = time.time() + timeout
        poll_interval = 0.5

        while time.time() < deadline:
            # 查询订单状态
            detail = self._query_order_detail(order.order_id)
            if detail is None:
                time.sleep(poll_interval)
                continue

            status = detail.get("status", 0)
            traded_qty = detail.get("traded_volume", 0)

            # 全部成交
            if status in (55, 56) and traded_qty > 0:
                return self._build_fill_dict(order, detail)

            # 已撤/已拒
            if status in (57, 58):
                logger.warning("订单 %s 已撤/已拒: status=%d",
                             order.order_id, status)
                return None

            time.sleep(poll_interval)

        # 超时撤单
        logger.warning("订单 %s 等待超时 (%.0fs), 自动撤单",
                     order.order_id, timeout)
        self.cancel(order)
        return None

    def _query_order_detail(self, order_id: str) -> dict | None:
        """查询单笔订单详情"""
        try:
            all_orders = self._xt_trader.queryOrder(self.account_id)  # type: ignore[union-attr]
            for o in (all_orders or []):
                if str(o.order_id) == str(order_id):
                    return {
                        "order_id": str(o.order_id),
                        "status": int(getattr(o, "order_status", 0)),
                        "traded_volume": int(getattr(o, "traded_volume", 0)),
                        "traded_price": float(getattr(o, "traded_price", 0.0)),
                        "code": str(getattr(o, "stock_code", "")),
                    }
            return None
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as exc:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error("查询订单 %s 异常: %s", order_id, exc)
            return None

    def _build_fill_dict(self, order: Order, detail: dict) -> dict:
        """从 QMT 订单详情构建成交回报"""
        fill_price = float(detail.get("traded_price", order.price))
        fill_qty = int(detail.get("traded_volume", order.qty))

        return {
            "order_id": order.order_id,
            "fill_id": f"QMT-{order.order_id}",
            "symbol": order.symbol,
            "qty": fill_qty,
            "price": fill_price,
            "side": order.side,
            "ts": now_bj().isoformat(),
        }

    # ------------------------------------------------------------
    # 盘口
    # ------------------------------------------------------------

    def get_order_book(self, symbol: str, levels: int = 5) -> dict | None:
        """获取盘口 (从 xtdata 获取)"""
        try:
            tick = xtdata.get_full_tick([symbol])
            if symbol not in tick:
                return None
            t = tick[symbol]
            return {
                "symbol": symbol,
                "bid1": float(t.get("bidPrice", [0])[0]) if t.get("bidPrice") else 0.0,
                "bid1_vol": int(t.get("bidVol", [0])[0]) if t.get("bidVol") else 0,
                "ask1": float(t.get("askPrice", [0])[0]) if t.get("askPrice") else 0.0,
                "ask1_vol": int(t.get("askVol", [0])[0]) if t.get("askVol") else 0,
                "total_volume": int(t.get("volume", 0)),
                "bid_volumes": [int(v) for v in (t.get("bidVol", []) or [])[:levels]],
                "ask_volumes": [int(v) for v in (t.get("askVol", []) or [])[:levels]],
            }
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as exc:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error("获取盘口 %s 异常: %s", symbol, exc)
            return None

    # ------------------------------------------------------------
    # 账户/持仓
    # ------------------------------------------------------------

    def get_account_info(self) -> dict:
        """查询账户资金 (带 5 秒缓存)"""
        now = time.time()
        if now - self._last_account_update < 5.0 and self._account_cache:
            return dict(self._account_cache)

        try:
            if self.is_connected:
                asset = self._xt_trader.queryAsset(self.account_id)  # type: ignore[union-attr]
                if asset:
                    self._account_cache = {
                        "available": float(getattr(asset, "available", 0.0)),
                        "total": float(getattr(asset, "total_asset", 0.0)),
                        "frozen": float(getattr(asset, "frozen", 0.0)),
                        "margin": float(getattr(asset, "margin", 0.0)),
                        "market_value": float(getattr(asset, "market_value", 0.0)),
                    }
                    self._last_account_update = now
                    return dict(self._account_cache)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as exc:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error("查询账户异常: %s", exc)

        return {"available": 0.0, "total": 0.0, "frozen": 0.0, "margin": 0.0}

    def get_positions(self) -> dict[str, int]:
        """查询持仓"""
        try:
            if self.is_connected:
                positions = self._xt_trader.queryPosition(self.account_id)  # type: ignore[union-attr]
                result = {}
                for p in (positions or []):
                    code = str(getattr(p, "stock_code", ""))
                    vol = int(getattr(p, "volume", 0))
                    if code and vol > 0:
                        result[code] = vol
                return result
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as exc:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error("查询持仓异常: %s", exc)
        return {}

    def get_available_funds(self) -> float:
        """获取可用资金 (含缓冲校验)"""
        account = self.get_account_info()
        available = float(account.get("available", 0.0))
        return max(0.0, available - self.min_cash_buffer)

    def check_funds_sufficient(self, required_amount: float) -> tuple[bool, str]:
        """检查资金是否充足

        Returns:
            (is_sufficient, reason)
        """
        available = self.get_available_funds()
        if available >= required_amount:
            return True, ""
        return False, (
            f"资金不足: 需要 ¥{required_amount:,.0f}, "
            f"可用 ¥{available:,.0f} (缓冲 ¥{self.min_cash_buffer:,.0f})"
        )

    # ------------------------------------------------------------
    # 成交量 profile
    # ------------------------------------------------------------

    def get_volume_profile(self, symbol: str, window_minutes: int = 30) -> list[float] | None:
        """获取历史成交量分布 (用于 VWAP)"""
        try:
            # 取最近 5 天 1 分钟 K 线
            bars = xtdata.get_market_data(
                field_list=["volume"],
                stock_list=[symbol],
                period="1m",
                count=window_minutes * 5,
            )
            if bars is None or symbol not in bars.get("volume", {}):
                return None
            vol = bars["volume"][symbol]
            total = float(vol.sum())
            if total <= 0:
                return [1.0 / window_minutes] * window_minutes
            profile = [float(v) / total for v in vol[-window_minutes:]]
            return profile
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as exc:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error("获取成交量 profile %s 异常: %s", symbol, exc)
            return None


__all__ = [
    "QMT_ORDER_STATUS",
    "XTQUANT_AVAILABLE",
    "QmtBrokerAPI",
]
