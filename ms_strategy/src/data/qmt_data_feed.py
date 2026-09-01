"""
QMT 实时行情数据源 (QmtDataFeed)

基于 xtquant.xtdata 的行情订阅与断线重连:
- subscribe_quote() 全量行情订阅 + Tick 回调
- 内存价格缓存 (deque 限制 1000 条/标的)
- 心跳检测 + 自动重连 (30s 超时)
- 多品种场景下内存泄漏防护

与 SimulatedBroker.set_price() 的区别:
- SimulatedBroker: 手动注入价格, 回测用
- QmtDataFeed: 实时推送, 实盘用

用法:
    feed = QmtDataFeed(symbols=["510300.SH", "IF2507.CFFEX"])
    feed.connect()
    while True:
        price = feed.get_price("510300.SH")
        if price:
            # 执行交易逻辑
            pass
        time.sleep(0.1)
"""
from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable

logger = logging.getLogger(__name__)

# 尝试导入 xtquant
try:
    from xtquant import xtdata
    XTDATA_AVAILABLE = True
except ImportError:
    XTDATA_AVAILABLE = False
    logger.warning("xtquant 未安装, QmtDataFeed 将不可用")


# 每个标的最大缓存 Tick 数 (防止内存泄漏)
MAX_TICK_CACHE_PER_SYMBOL = 1000
# 心跳超时 (秒)
HEARTBEAT_TIMEOUT = 30
# 重连间隔 (秒)
RECONNECT_INTERVAL = 10
# 重连最大次数
MAX_RECONNECT_ATTEMPTS = 5


class QmtDataFeed:
    """QMT 实时行情数据源

    特性:
    1. 全量订阅 + Tick 回调
    2. 内存价格缓存 (deque 限制)
    3. 断线检测 + 自动重连
    4. 多标的并发管理
    """

    def __init__(self, symbols: list[str] | None = None):
        """
        Args:
            symbols: 初始订阅标的列表
        """
        self._symbols: set[str] = set(symbols or [])
        self._prices: dict[str, float] = {}  # symbol → 最新价
        self._price_ts: dict[str, float] = {}  # symbol → 最近有效价时间戳 (DT-1 stale 校验)
        self._opens: dict[str, float] = {}   # symbol → 开盘价
        self._highs: dict[str, float] = {}   # symbol → 最高价
        self._lows: dict[str, float] = {}    # symbol → 最低价
        self._volumes: dict[str, int] = {}   # symbol → 成交量
        self._tick_cache: dict[str, deque] = {}  # symbol → deque(tick_dict)

        # 连接状态
        self._connected = False
        self._last_heartbeat: float = 0.0
        self._reconnect_count: int = 0
        self._total_ticks: int = 0
        self._connection_error: str = ""

        # 回调
        self._on_tick_callbacks: list[Callable] = []  # 通用 Tick 回调
        self._on_disconnect_callbacks: list[Callable] = []

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def symbol_count(self) -> int:
        return len(self._symbols)

    @property
    def total_ticks_received(self) -> int:
        return self._total_ticks

    # ------------------------------------------------------------
    # 订阅管理
    # ------------------------------------------------------------

    def add_symbols(self, symbols: list[str]) -> int:
        """动态添加订阅标的"""
        new_count = 0
        for s in symbols:
            if s not in self._symbols:
                self._symbols.add(s)
                self._tick_cache[s] = deque(maxlen=MAX_TICK_CACHE_PER_SYMBOL)
                new_count += 1
        if new_count > 0 and self._connected:
            self._resubscribe()
        return new_count

    def remove_symbols(self, symbols: list[str]) -> int:
        """动态移除订阅标的 (释放内存)"""
        removed = 0
        for s in symbols:
            if s in self._symbols:
                self._symbols.discard(s)
                self._prices.pop(s, None)
                self._price_ts.pop(s, None)
                self._tick_cache.pop(s, None)
                removed += 1
        return removed

    # ------------------------------------------------------------
    # 连接与重连
    # ------------------------------------------------------------

    def connect(self) -> bool:
        """连接 xtdata 并订阅行情"""
        if not XTDATA_AVAILABLE:
            self._connection_error = "xtquant 未安装"
            logger.error(self._connection_error)
            return False

        try:
            # 下载历史数据 (可选, 加速策略初始化)
            for symbol in list(self._symbols):
                xtdata.download_history_data(symbol, period="1d")

            # 订阅全量行情
            self._resubscribe()

            self._connected = True
            self._last_heartbeat = time.time()
            self._reconnect_count = 0
            logger.info("QmtDataFeed 连接成功: %d 个标的已订阅", len(self._symbols))
            return True

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as exc:  # noqa: E501

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            self._connection_error = str(exc)
            self._connected = False
            logger.error("QmtDataFeed 连接失败: %s", exc, exc_info=True)
            return False

    def _resubscribe(self):
        """重新订阅所有标的的行情"""
        symbols_list = list(self._symbols)
        if not symbols_list:
            return

        # 订阅全量 Tick
        xtdata.subscribe_whole_quote(
            symbols_list,
            callback=self._on_tick,
        )

    def disconnect(self):
        """断开连接, 释放资源"""
        self._connected = False
        try:
            symbols_list = list(self._symbols)
            if symbols_list:
                xtdata.unsubscribe_quote(symbols_list)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        logger.info("QmtDataFeed 已断开")

    # ------------------------------------------------------------
    # Tick 回调
    # ------------------------------------------------------------

    def _on_tick(self, data: list[dict]):
        """Tick 数据回调 (xtdata 推送)

        Args:
            data: [{"code": "510300.SH", "lastPrice": 4.500, ...}, ...]
        """
        try:
            for tick in (data or []):
                code = tick.get("code", "")
                if not code:
                    continue

                # DT-2: 过滤无效价 (lastPrice 缺失/<=0 不缓存为真实价, 防止消费方拿到 0.0 误判)
                last_price = float(tick.get("lastPrice", 0.0) or 0.0)
                if last_price <= 0:
                    continue
                # 更新缓存 + 记录数据时间戳 (供 get_price stale 校验, DT-1)
                self._prices[code] = last_price
                self._price_ts[code] = time.time()
                self._opens[code] = float(tick.get("open", 0.0))
                self._highs[code] = float(tick.get("high", 0.0))
                self._lows[code] = float(tick.get("low", 0.0))
                self._volumes[code] = int(tick.get("volume", 0))

                # 存入 Tick 缓存 (deque 自动限制大小)
                cache = self._tick_cache.get(code)
                if cache is not None:
                    cache.append({
                        "price": self._prices[code],
                        "volume": tick.get("volume", 0),
                        "amount": tick.get("amount", 0.0),
                        "time": tick.get("time", 0),
                    })

                self._total_ticks += 1

            # 更新心跳
            self._last_heartbeat = time.time()

            # 触发通用回调
            for cb in self._on_tick_callbacks:
                try:
                    cb(data)
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):  # noqa: E501
                    # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                    pass

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as exc:  # noqa: E501

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error("Tick 回调异常: %s", exc, exc_info=True)

    def on_tick(self, callback: Callable):
        """注册 Tick 回调

        Usage:
            @feed.on_tick
            def my_handler(data):
                for tick in data:
                    logger.info(tick["code"], tick["lastPrice"])
        """
        self._on_tick_callbacks.append(callback)
        return callback

    def on_disconnect(self, callback: Callable):
        """注册断线回调"""
        self._on_disconnect_callbacks.append(callback)
        return callback

    # ------------------------------------------------------------
    # 断线检测与重连
    # ------------------------------------------------------------

    def check_connection(self) -> bool:
        """断线检测 + 自动重连

        应在主循环中频繁调用 (如每秒 1 次)

        Returns:
            True = 连接正常, False = 已断开
        """
        if not self._connected:
            return False

        now = time.time()
        elapsed = now - self._last_heartbeat

        if elapsed > HEARTBEAT_TIMEOUT:
            logger.warning(
                "QmtDataFeed 心跳超时 %.0fs (阈值 %.0fs), 尝试重连...",
                elapsed, HEARTBEAT_TIMEOUT
            )
            self._connected = False

            # 触发断线回调
            for cb in self._on_disconnect_callbacks:
                try:
                    cb()
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):  # noqa: E501
                    # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                    pass

            # 自动重连
            return self._try_reconnect()

        return True

    def _try_reconnect(self) -> bool:
        """尝试重连"""
        if self._reconnect_count >= MAX_RECONNECT_ATTEMPTS:
            logger.error(
                "QmtDataFeed 重连失败: 已达最大重试次数 %d",
                MAX_RECONNECT_ATTEMPTS
            )
            return False

        self._reconnect_count += 1
        logger.info("QmtDataFeed 重连尝试 %d/%d...",
                   self._reconnect_count, MAX_RECONNECT_ATTEMPTS)

        time.sleep(RECONNECT_INTERVAL)

        try:
            self._resubscribe()
            self._connected = True
            self._last_heartbeat = time.time()
            self._reconnect_count = 0
            logger.info("QmtDataFeed 重连成功")
            return True
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as exc:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error("QmtDataFeed 重连失败: %s", exc)
            return self._try_reconnect()  # 递归重试

    # ------------------------------------------------------------
    # 数据查询
    # ------------------------------------------------------------

    def get_price(self, symbol: str, max_age: float = 0.0) -> float | None:
        """获取最新价 (可选 stale 校验)

        Args:
            symbol: 标的代码
            max_age: 最大允许数据年龄 (秒)。>0 时若最近有效价时间戳距今超过该值
                视为陈旧数据, 返回 None (DT-1/Q4: 陈旧数据必须显式标记而非静默返回旧价)。

        Returns:
            float 有效价; 无数据/陈旧数据时返回 None
        """
        price = self._prices.get(symbol)
        if price is None:
            return None
        if max_age > 0:
            ts = self._price_ts.get(symbol, 0.0)
            if ts <= 0 or time.time() - ts > max_age:
                logger.warning(
                    "[STALE] %s 价格已过期 (age=%.0fs > max_age=%.0fs), 返回 None",
                    symbol, time.time() - ts, max_age,
                )
                return None
        return price

    def get_price_ts(self, symbol: str) -> float | None:
        """获取最近有效价时间戳 (epoch 秒), 供调用方判断数据新鲜度"""
        return self._price_ts.get(symbol)

    def get_ohlc(self, symbol: str) -> dict[str, float]:
        """获取 OHLC"""
        return {
            "open": self._opens.get(symbol, 0.0),
            "high": self._highs.get(symbol, 0.0),
            "low": self._lows.get(symbol, 0.0),
            "close": self._prices.get(symbol, 0.0),
        }

    def get_volume(self, symbol: str) -> int:
        """获取成交量"""
        return self._volumes.get(symbol, 0)

    def get_all_prices(self) -> dict[str, float]:
        """获取所有订阅标的的最新价格"""
        return dict(self._prices)

    def get_tick_history(self, symbol: str, n: int = 100) -> list[dict]:
        """获取最近 n 个 Tick"""
        cache = self._tick_cache.get(symbol)
        if cache is None:
            return []
        return list(cache)[-n:]

    def get_historical_data(self, symbol: str, period: str = "1d",
                            count: int = 252) -> dict | None:
        """获取历史 K 线数据"""
        try:
            data = xtdata.get_market_data(
                field_list=["open", "high", "low", "close", "volume"],
                stock_list=[symbol],
                period=period,
                count=count,
            )
            return data
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as exc:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error("获取历史数据 %s 异常: %s", symbol, exc)
            return None

    # ------------------------------------------------------------
    # 诊断
    # ------------------------------------------------------------

    def get_status(self) -> dict:
        """获取连接状态诊断信息"""
        return {
            "connected": self._connected,
            "symbols_count": len(self._symbols),
            "total_ticks": self._total_ticks,
            "last_heartbeat": self._last_heartbeat,
            "seconds_since_heartbeat": time.time() - self._last_heartbeat,
            "reconnect_count": self._reconnect_count,
            "connection_error": self._connection_error,
            "cache_sizes": {
                s: len(self._tick_cache.get(s, []))
                for s in list(self._symbols)[:10]
            },
        }

    def __repr__(self) -> str:
        return (f"QmtDataFeed(connected={self._connected}, "
                f"symbols={len(self._symbols)}, ticks={self._total_ticks})")


__all__ = [
    "HEARTBEAT_TIMEOUT",
    "MAX_TICK_CACHE_PER_SYMBOL",
    "XTDATA_AVAILABLE",
    "QmtDataFeed",
]
