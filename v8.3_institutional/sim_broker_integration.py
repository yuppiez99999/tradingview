# -*- coding: utf-8 -*-
"""
v7.5 模拟盘接入层
==================

统一抽象:
    - TradingSessionCalendar : 交易日历 + A股/期货交易时段 + 夜盘窗口
    - SimBroker              : 模拟盘券商/期货公司接口
      * SimStockBroker       : 股票模拟盘
      * SimFuturesBroker     : 期货模拟盘（含夜盘）
    - SimBrokerRouter        : 路由订单到对应模拟盘
    - PositionSync           : 持仓/资金同步
    - SimExecutionEngine     : 模拟盘执行引擎（替代 MockBroker）

设计原则:
    1. 完全按交易日执行，节假日/周末自动跳过
    2. 股票仅在日盘时段交易
    3. 期货支持日盘 + 夜盘
    4. 同一 session 内不重复执行
    5. 执行后持久化持仓与资金快照
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dtime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("v75.sim_broker")

# ============================================================
# 1. 交易日历与交易时段
# ============================================================

# 2026 年中国期货夜盘规则（常见品种）
# 夜盘时间：21:00-23:00（有色金属/贵金属/原油等）
#           21:00-23:30（部分品种）
#           暂无夜盘：农产品、黑色系、国债等
FUTURES_NIGHT_SESSIONS = {
    # 有夜盘的品种
    "CU": {"name": "沪铜", "night_start": "21:00", "night_end": "23:00"},
    "AL": {"name": "沪铝", "night_start": "21:00", "night_end": "23:00"},
    "ZN": {"name": "沪锌", "night_start": "21:00", "night_end": "23:00"},
    "NI": {"name": "沪镍", "night_start": "21:00", "night_end": "23:00"},
    "PB": {"name": "沪铅", "night_start": "21:00", "night_end": "23:00"},
    "SN": {"name": "沪锡", "night_start": "21:00", "night_end": "23:00"},
    "AG": {"name": "沪银", "night_start": "21:00", "night_end": "23:00"},
    "AU": {"name": "沪金", "night_start": "21:00", "night_end": "23:00"},
    "SC": {"name": "原油", "night_start": "21:00", "night_end": "23:00"},
    "LU": {"name": "低硫燃油", "night_start": "21:00", "night_end": "23:00"},
    "FU": {"name": "燃油", "night_start": "21:00", "night_end": "23:00"},
    "RB": {"name": "螺纹钢", "night_start": "21:00", "night_end": "23:00"},
    "HC": {"name": "热轧卷板", "night_start": "21:00", "night_end": "23:00"},
    "I": {"name": "铁矿石", "night_start": "21:00", "night_end": "23:00"},
    "J": {"name": "焦炭", "night_start": "21:00", "night_end": "23:00"},
    "JM": {"name": "焦煤", "night_start": "21:00", "night_end": "23:00"},
    "CF": {"name": "棉花", "night_start": "21:00", "night_end": "23:00"},
    "TA": {"name": "PTA", "night_start": "21:00", "night_end": "23:00"},
    "MA": {"name": "甲醇", "night_start": "21:00", "night_end": "23:00"},
    "FG": {"name": "玻璃", "night_start": "21:00", "night_end": "23:00"},
    "SA": {"name": "纯碱", "night_start": "21:00", "night_end": "23:00"},
    "SP": {"name": "纸浆", "night_start": "21:00", "night_end": "23:00"},
    "UR": {"name": "尿素", "night_start": "21:00", "night_end": "23:00"},
    "NR": {"name": "20号胶", "night_start": "21:00", "night_end": "23:00"},
    "SS": {"name": "不锈钢", "night_start": "21:00", "night_end": "23:00"},
    "A": {"name": "豆一", "night_start": "21:00", "night_end": "23:00"},
    "B": {"name": "豆二", "night_start": "21:00", "night_end": "23:00"},
    "M": {"name": "豆粕", "night_start": "21:00", "night_end": "23:00"},
    "Y": {"name": "豆油", "night_start": "21:00", "night_end": "23:00"},
    "P": {"name": "棕榈油", "night_start": "21:00", "night_end": "23:00"},
    "C": {"name": "玉米", "night_start": "21:00", "night_end": "23:00"},
    "CS": {"name": "淀粉", "night_start": "21:00", "night_end": "23:00"},
    "JD": {"name": "鸡蛋", "night_start": "21:00", "night_end": "23:00"},
    "V": {"name": "PVC", "night_start": "21:00", "night_end": "23:00"},
    "PP": {"name": "聚丙烯", "night_start": "21:00", "night_end": "23:00"},
    "L": {"name": "塑料", "night_start": "21:00", "night_end": "23:00"},
    "EB": {"name": "苯乙烯", "night_start": "21:00", "night_end": "23:00"},
    "PG": {"name": "LPG", "night_start": "21:00", "night_end": "23:00"},
    "RR": {"name": "粳米", "night_start": "21:00", "night_end": "23:00"},
    "RS": {"name": "油菜籽", "night_start": "21:00", "night_end": "23:00"},
    "OI": {"name": "菜油", "night_start": "21:00", "night_end": "23:00"},
    "RM": {"name": "菜粕", "night_start": "21:00", "night_end": "23:00"},
    "AP": {"name": "苹果", "night_start": "21:00", "night_end": "23:00"},
    "CJ": {"name": "红枣", "night_start": "21:00", "night_end": "23:00"},
    "SF": {"name": "硅铁", "night_start": "21:00", "night_end": "23:00"},
    "SM": {"name": "锰硅", "night_start": "21:00", "night_end": "23:00"},
    "IC": {"name": "中证500股指", "night_start": "21:00", "night_end": "23:00"},
    "IF": {"name": "沪深300股指", "night_start": "21:00", "night_end": "23:00"},
    "IH": {"name": "上证50股指", "night_start": "21:00", "night_end": "23:00"},
    "IM": {"name": "中证1000股指", "night_start": "21:00", "night_end": "23:00"},
    "T": {"name": "10年期国债", "night_start": "21:00", "night_end": "23:00"},
    "TF": {"name": "5年期国债", "night_start": "21:00", "night_end": "23:00"},
    "TS": {"name": "2年期国债", "night_start": "21:00", "night_end": "23:00"},
    # 无夜盘或夜盘不常见
    "ZC": {"name": "动力煤", "night_start": None, "night_end": None},
}

# ER4 修复: 节假日列表委托给 utils.trade_calendar (akshare 动态获取)
# 原 HOLIDAYS_2026 仅含 2026 假期, 2027 年后所有节假日会被误判为交易日
# 现统一走 akshare 动态日历, 自动覆盖任意年份, 失败时回退到 2026 硬编码列表
HOLIDAYS_2026 = set()  # 保留变量名向后兼容, 实际不再使用
_PROJECT_ROOT = Path(__file__).resolve().parent.parent  # 项目根目录 (utils/ 在此层级)
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
try:
    from utils.trade_calendar import is_trading_day as _dyn_is_trading_day
    _DYNAMIC_CALENDAR_AVAILABLE = True
    logger.info("[ER4] 节假日判断已委托给 utils.trade_calendar (akshare 动态获取)")
except ImportError:
    _DYNAMIC_CALENDAR_AVAILABLE = False
    # 回退: 保留 2026 硬编码列表 (仅 2026 年有效)
    HOLIDAYS_2026 = {
        date(2026, 1, 1),
        date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
        date(2026, 2, 19), date(2026, 2, 20), date(2026, 2, 23),
        date(2026, 4, 6), date(2026, 4, 7),
        date(2026, 5, 4), date(2026, 5, 5),
        date(2026, 6, 19), date(2026, 6, 22),
        date(2026, 9, 25),
        date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7),
        date(2026, 10, 8),
    }
    logger.warning("[ER4] utils.trade_calendar 不可用, 回退到 2026 硬编码假期列表 "
                   "(2027+ 年节假日将无法识别)")


class SessionType(str, Enum):
    """交易时段类型"""
    DAY = "day"          # 日盘
    NIGHT = "night"      # 夜盘
    CLOSED = "closed"    # 休市


@dataclass
class TradingSession:
    """交易时段定义"""
    name: str
    start: dtime
    end: dtime
    market: str  # 'stock' | 'futures' | 'both'
    session_type: SessionType = SessionType.DAY


class TradingSessionCalendar:
    """交易日历 + 交易时段判断

    支持:
        - A股交易日判断（周末+节假日）
        - 期货交易日判断（节假日，夜盘前一交易日白天的延续）
        - 当前是否在交易时段内
        - 当前 session 类型
    """

    def __init__(self, holidays: Optional[set] = None):
        self.holidays = holidays or HOLIDAYS_2026

        # A股交易时段
        self.stock_sessions = [
            TradingSession("STOCK_OPEN",  dtime(9, 30), dtime(9, 45),  "stock"),
            TradingSession("STOCK_MORN",  dtime(9, 45), dtime(11, 30), "stock"),
            TradingSession("STOCK_NOON",  dtime(13, 0),  dtime(14, 30), "stock"),
            TradingSession("STOCK_CLOSE", dtime(14, 30), dtime(15, 0),  "stock"),
        ]

        # 期货日盘交易时段（同一时段）
        self.futures_day_sessions = [
            TradingSession("FUTURES_DAY_OPEN",  dtime(9, 0),  dtime(9, 15), "futures"),
            TradingSession("FUTURES_DAY_MORN",  dtime(9, 15), dtime(11, 30), "futures"),
            TradingSession("FUTURES_DAY_NOON",  dtime(13, 30), dtime(15, 0), "futures"),
            TradingSession("FUTURES_DAY_AFTER", dtime(15, 0),  dtime(15, 30), "futures"),
        ]

    def is_trading_day(self, d: Optional[date] = None) -> bool:
        """是否为交易日（A股+期货通用）

        ER4 修复: 优先委托给 utils.trade_calendar (akshare 动态获取),
        覆盖任意年份的节假日; 动态日历不可用时回退到 self.holidays 硬编码列表。
        """
        if d is None:
            d = date.today()

        # ER4 修复: 优先使用动态日历
        if _DYNAMIC_CALENDAR_AVAILABLE:
            try:
                return _dyn_is_trading_day(d.strftime('%Y-%m-%d'))
            except Exception as e:
                logger.warning(
                    f"[ER4] 动态日历查询失败 (date={d}), 回退到硬编码列表: {e}"
                )

        # 回退: 周末判断
        if d.weekday() >= 5:
            return False

        # 回退: 硬编码节假日列表 (仅 2026 年有效)
        if d in self.holidays:
            return False

        return True

    def is_futures_trading_day(self, d: Optional[date] = None) -> bool:
        """是否为期货交易日（与A股一致，节假日休市）"""
        return self.is_trading_day(d)

    def get_current_session(self, now: Optional[datetime] = None) -> Tuple[SessionType, Optional[TradingSession]]:
        """获取当前交易时段

        Returns:
            (session_type, session)
        """
        if now is None:
            now = datetime.now()
        t = now.time()

        # 检查夜盘（21:00-23:30）
        if dtime(21, 0) <= t <= dtime(23, 30):
            return SessionType.NIGHT, TradingSession(
                "FUTURES_NIGHT", dtime(21, 0), dtime(23, 30), "futures", SessionType.NIGHT
            )

        # 检查日盘
        for session in self.stock_sessions + self.futures_day_sessions:
            if session.start <= t <= session.end:
                return SessionType.DAY, session

        return SessionType.CLOSED, None

    def is_market_open(self, now: Optional[datetime] = None) -> bool:
        """当前是否开市中"""
        session_type, _ = self.get_current_session(now)
        return session_type != SessionType.CLOSED

    def get_next_session_start(self, d: Optional[date] = None) -> Tuple[date, Optional[dtime]]:
        """获取下一交易日/下一时段开始时间"""
        if d is None:
            d = date.today()
        n = 1
        while n <= 10:
            nd = d + timedelta(days=n)
            if self.is_trading_day(nd):
                return nd, dtime(9, 30)
            n += 1
        return d + timedelta(days=n), dtime(9, 30)

    def get_trading_days(self, start: date, end: date) -> List[date]:
        """获取日期范围内的交易日列表"""
        days = []
        d = start
        while d <= end:
            if self.is_trading_day(d):
                days.append(d)
            d += timedelta(days=1)
        return days


# ============================================================
# 2. 模拟盘券商/期货接口
# ============================================================

@dataclass
class SimAccount:
    """模拟盘账户"""
    account_id: str
    total_capital: float
    available_cash: float
    frozen_cash: float = 0.0
    positions: Dict[str, Dict] = field(default_factory=dict)
    daily_pnl: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    last_updated: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "total_capital": self.total_capital,
            "available_cash": self.available_cash,
            "frozen_cash": self.frozen_cash,
            "positions": self.positions,
            "daily_pnl": self.daily_pnl,
            "created_at": self.created_at.isoformat(),
            "last_updated": self.last_updated.isoformat(),
        }


class SimBrokerBase:
    """模拟盘基类"""

    def __init__(self, account: SimAccount):
        self.account = account
        self._lock = threading.RLock()
        self._order_counter = 0
        self._fills: List[Dict] = []

    def _next_order_id(self) -> str:
        with self._lock:
            self._order_counter += 1
            return f"SIM-{datetime.now():%Y%m%d}-{self._order_counter:06d}"

    def place_order(self, symbol: str, qty: int, side: str,
                    price: float = 0.0, order_type: str = "LIMIT",
                    session: str = "day") -> Dict:
        """下单

        Args:
            symbol: 标的代码
            qty: 数量（股票：股；期货：手）
            side: BUY / SELL / SELL_SHORT
            price: 限价（0表示市价）
            order_type: LIMIT / MARKET
            session: day / night

        Returns:
            {order_id, symbol, qty, side, price, status, timestamp}
        """
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        raise NotImplementedError

    def get_positions(self) -> Dict[str, Dict]:
        """获取当前持仓"""
        with self._lock:
            return dict(self.account.positions)

    def get_account(self) -> SimAccount:
        """获取账户信息"""
        return self.account

    def _record_fill(self, fill: Dict) -> None:
        with self._lock:
            self._fills.append(fill)

    def get_fills(self, session: Optional[str] = None) -> List[Dict]:
        """获取成交记录"""
        with self._lock:
            if session:
                return [f for f in self._fills if f.get("session") == session]
            return list(self._fills)


class SimStockBroker(SimBrokerBase):
    """股票模拟盘

    规则:
        - 仅日盘交易（9:30-15:00）
        - 最小交易单位：100股
        - 仅允许做多（T+1）
        - 涨跌停板检查：10%（ST 5%）
        - 市价单按最新价成交
        - 限价单：买一<=price<=卖一 立即成交，否则挂单
    """

    def __init__(self, account: SimAccount, price_provider=None):
        super().__init__(account)
        self.price_provider = price_provider
        self._pending_orders: Dict[str, Dict] = {}

    def place_order(self, symbol: str, qty: int, side: str,
                    price: float = 0.0, order_type: str = "LIMIT",
                    session: str = "day") -> Dict:
        if session == "night":
            return {"order_id": "", "status": "REJECTED",
                    "reason": "股票不支持夜盘交易"}

        if side not in ("BUY", "SELL"):
            return {"order_id": "", "status": "REJECTED",
                    "reason": f"股票不支持 {side}"}

        if qty <= 0 or qty % 100 != 0:
            return {"order_id": "", "status": "REJECTED",
                    "reason": f"股票数量必须为100的整数倍: {qty}"}

        order_id = self._next_order_id()
        order = {
            "order_id": order_id,
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "price": float(price) if price else 0.0,
            "order_type": order_type,
            "status": "PENDING",
            "timestamp": datetime.now().isoformat(),
            "session": session,
            "market": "stock",
        }
        self._pending_orders[order_id] = order
        return order

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self._pending_orders:
            self._pending_orders[order_id]["status"] = "CANCELLED"
            return True
        return False

    def get_positions(self) -> Dict[str, Dict]:
        with self._lock:
            return dict(self.account.positions)

    def get_account(self) -> SimAccount:
        return self.account


class SimFuturesBroker(SimBrokerBase):
    """期货模拟盘

    规则:
        - 支持日盘 + 夜盘
        - 日盘：9:00-11:30, 13:30-15:00
        - 夜盘：21:00-23:00/23:30（按品种）
        - 最小交易单位：1手
        - 允许开多/开空
        - 保证金制度
        - 涨跌停板检查（一般±7%~±10%）
        - 市价单按最新价成交
        - 限价单：买一<=price<=卖一 立即成交
    """

    def __init__(self, account: SimAccount, price_provider=None,
                 margin_rates: Optional[Dict[str, float]] = None):
        super().__init__(account)
        self.price_provider = price_provider
        self.margin_rates = margin_rates or {
            "IF": 0.12, "IC": 0.14, "IM": 0.15,
            "CU": 0.10, "AL": 0.10, "ZN": 0.12,
            "AU": 0.10, "AG": 0.12, "SC": 0.15,
            "RB": 0.13, "I": 0.14, "J": 0.15,
            "CF": 0.12, "TA": 0.10, "MA": 0.12,
        }
        self._pending_orders: Dict[str, Dict] = {}
        self._night_session_info = FUTURES_NIGHT_SESSIONS

    def _get_night_session(self, symbol: str) -> Optional[Tuple[dtime, dtime]]:
        """获取品种夜盘时段"""
        # 提取品种代码（去掉月份）
        code = symbol[:2] if len(symbol) >= 2 else symbol
        info = self._night_session_info.get(code)
        if info and info.get("night_start"):
            start = datetime.strptime(info["night_start"], "%H:%M").time()
            end = datetime.strptime(info["night_end"], "%H:%M").time()
            return start, end
        return None

    def place_order(self, symbol: str, qty: int, side: str,
                    price: float = 0.0, order_type: str = "LIMIT",
                    session: str = "day") -> Dict:
        # 检查夜盘权限
        if session == "night":
            night_info = self._get_night_session(symbol)
            if not night_info:
                return {"order_id": "", "status": "REJECTED",
                        "reason": f"{symbol} 无夜盘交易"}

        # 检查保证金
        margin_rate = self.margin_rates.get(symbol[:2], 0.12)
        if price <= 0:
            return {"order_id": "", "status": "REJECTED",
                    "reason": "期货限价价格必须大于0"}

        margin = price * qty * margin_rate
        if margin > self.account.available_cash:
            return {"order_id": "", "status": "REJECTED",
                    "reason": f"保证金不足: 需要{margin:.0f}, 可用{self.account.available_cash:.0f}"}

        order_id = self._next_order_id()
        order = {
            "order_id": order_id,
            "symbol": symbol,
            "qty": qty,
            "side": side,  # BUY_OPEN / SELL_OPEN / BUY_CLOSE / SELL_CLOSE
            "price": float(price),
            "order_type": order_type,
            "status": "PENDING",
            "timestamp": datetime.now().isoformat(),
            "session": session,
            "market": "futures",
            "margin": margin,
        }
        self._pending_orders[order_id] = order
        return order

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self._pending_orders:
            self._pending_orders[order_id]["status"] = "CANCELLED"
            return True
        return False

    def get_positions(self) -> Dict[str, Dict]:
        with self._lock:
            return dict(self.account.positions)

    def get_account(self) -> SimAccount:
        return self.account


class SimBrokerRouter:
    """模拟盘路由：自动识别股票/期货/期权，路由到对应模拟盘"""

    def __init__(self,
                 stock_broker: SimStockBroker,
                 futures_broker: SimFuturesBroker,
                 calendar: TradingSessionCalendar = None,
                 options_broker=None):
        self.stock_broker = stock_broker
        self.futures_broker = futures_broker
        self.options_broker = options_broker  # 可选，SimOptionsBroker 实例
        self.calendar = calendar or TradingSessionCalendar()

    def route(self, order: Dict, session: Optional[str] = None) -> Dict:
        """路由订单到对应模拟盘

        Args:
            order: {symbol, qty, side, price, order_type, ...}
            session: 'day' / 'night' / None(自动判断)

        Returns:
            执行结果
        """
        symbol = order.get("symbol", "")
        market = self._detect_market(symbol)

        # 自动判断 session
        if session is None:
            session_type, _ = self.calendar.get_current_session()
            session = "night" if session_type == SessionType.NIGHT else "day"

        if market == "stock":
            return self.stock_broker.place_order(
                symbol=symbol,
                qty=int(order.get("qty", 0)),
                side=order.get("side", "BUY"),
                price=float(order.get("price", 0)),
                order_type=order.get("order_type", "LIMIT"),
                session=session,
            )
        elif market == "options" and self.options_broker is not None:
            return self.options_broker.place_order(
                symbol=symbol,
                qty=int(order.get("qty", 0)),
                side=order.get("side", "BUY_OPEN"),
                price=float(order.get("price", 0)),
                order_type=order.get("order_type", "LIMIT"),
                session=session,
                underlying_price=float(order.get("underlying_price", 0)),
                option_type=order.get("option_type"),
                strike=order.get("strike"),
            )
        else:
            return self.futures_broker.place_order(
                symbol=symbol,
                qty=int(order.get("qty", 0)),
                side=order.get("side", "BUY_OPEN"),
                price=float(order.get("price", 0)),
                order_type=order.get("order_type", "LIMIT"),
                session=session,
            )

    def _detect_market(self, symbol: str) -> str:
        """识别标的所属市场：stock / futures / options"""
        s = str(symbol).strip()

        # 先去掉常见 A 股前缀
        clean = s
        for prefix in ("sh", "sz", "SH", "SZ", "bj", "BJ"):
            if clean.startswith(prefix) and len(clean) > len(prefix):
                clean = clean[len(prefix):]
                break

        # 期权合约识别（优先于期货）
        # ETF期权: 510050C2506M03200, 510300P2506M04000
        for prefix in ("510050", "510300", "510500", "159919", "588080"):
            if s.startswith(prefix) and ("C" in s or "P" in s):
                return "options"
        # 股指期权: IO2506-C-3900, MO2506-P-6000
        upper = s.upper()
        for prefix in ("IO", "MO", "HO"):
            if upper.startswith(prefix) and "-" in s:
                return "options"

        # 股票/ETF/基金：6 位数字
        if len(clean) == 6 and clean.isdigit():
            return "stock"

        # 期货：字母+数字（如 CU2406, IF2506）
        if any(c.isalpha() for c in clean) and any(c.isdigit() for c in clean):
            return "futures"

        return "stock"

    def get_broker(self, symbol: str):
        """根据标的获取对应 broker"""
        market = self._detect_market(symbol)
        if market == "stock":
            return self.stock_broker
        elif market == "options" and self.options_broker is not None:
            return self.options_broker
        return self.futures_broker


# ============================================================
# 3. 持仓/资金同步
# ============================================================

class PositionSync:
    """持仓与资金同步

    职责:
        1. 每日开盘前同步前日收盘持仓
        2. 记录当日成交明细
        3. 日末生成持仓快照 + 资金曲线
        4. 支持导出到 positions.json / 每日报告
    """

    def __init__(self, router: SimBrokerRouter, snapshot_dir: Optional[Path] = None):
        self.router = router
        self.snapshot_dir = snapshot_dir or Path("sim_snapshots")
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def sync_from_brokers(self) -> Dict:
        """从模拟盘同步当前持仓与资金"""
        stock_positions = self.router.stock_broker.get_positions()
        futures_positions = self.router.futures_broker.get_positions()
        stock_account = self.router.stock_broker.get_account()
        futures_account = self.router.futures_broker.get_account()

        with self._lock:
            return {
                "timestamp": datetime.now().isoformat(),
                "stock": {
                    "positions": stock_positions,
                    "account": stock_account.to_dict(),
                },
                "futures": {
                    "positions": futures_positions,
                    "account": futures_account.to_dict(),
                },
                "combined": {
                    "total_capital": stock_account.total_capital + futures_account.total_capital,
                    "available_cash": stock_account.available_cash + futures_account.available_cash,
                    "positions": {**stock_positions, **futures_positions},
                },
            }

    def save_daily_snapshot(self, trade_date: Optional[str] = None) -> Path:
        """保存当日持仓快照"""
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y-%m-%d")

        data = self.sync_from_brokers()
        path = self.snapshot_dir / f"positions_{trade_date.replace('-', '')}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        logger.info("持仓快照已保存: %s", path)
        return path

    def load_positions(self, trade_date: str) -> Dict:
        """加载指定日期持仓快照"""
        path = self.snapshot_dir / f"positions_{trade_date.replace('-', '')}.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def update_positions_from_fills(self, fills: List[Dict]) -> None:
        """根据成交记录更新持仓"""
        with self._lock:
            for fill in fills:
                symbol = fill.get("symbol", "")
                qty = int(fill.get("qty", 0))
                side = fill.get("side", "")
                price = float(fill.get("price", 0))
                market = self.router._detect_market(symbol)

                if market == "stock":
                    positions = self.router.stock_broker.account.positions
                else:
                    positions = self.router.futures_broker.account.positions

                if symbol not in positions:
                    positions[symbol] = {"qty": 0, "avg_price": 0.0, "market_value": 0.0}

                pos = positions[symbol]
                if side in ("BUY", "BUY_OPEN"):
                    # 开多
                    total_cost = pos["qty"] * pos["avg_price"] + qty * price
                    pos["qty"] += qty
                    pos["avg_price"] = total_cost / pos["qty"] if pos["qty"] > 0 else 0.0
                elif side in ("SELL", "SELL_CLOSE"):
                    # 平仓/卖出
                    pos["qty"] -= qty
                    if pos["qty"] <= 0:
                        pos["qty"] = 0
                        pos["avg_price"] = 0.0

                pos["market_value"] = pos["qty"] * price if price > 0 else 0.0
                pos["last_update"] = datetime.now().isoformat()

    def get_daily_pnl(self, prev_positions: Dict) -> Dict:
        """计算当日盈亏"""
        current = self.sync_from_brokers()
        current_positions = current.get("combined", {}).get("positions", {})

        pnl = {}
        for symbol, pos in current_positions.items():
            prev = prev_positions.get(symbol, {})
            prev_qty = prev.get("qty", 0)
            prev_price = prev.get("avg_price", 0.0)
            curr_price = pos.get("avg_price", 0.0)
            if prev_qty > 0 and curr_price > 0:
                pnl[symbol] = {
                    "prev_qty": prev_qty,
                    "prev_price": prev_price,
                    "curr_price": curr_price,
                    "unrealized_pnl": (curr_price - prev_price) * prev_qty,
                    "return_pct": (curr_price - prev_price) / prev_price if prev_price > 0 else 0.0,
                }
        return pnl


# ============================================================
# 4. 模拟盘执行引擎
# ============================================================

class SimExecutionEngine:
    """模拟盘执行引擎

    替代 daily_workflow.py 中的 MockBroker 执行，
    支持:
        - 股票日盘执行
        - 期货日盘 + 夜盘执行
        - 期权模拟盘执行（ETF期权/股指期权）
        - 按交易日自动调度
        - 持仓同步
    """

    def __init__(self,
                 stock_broker: SimStockBroker,
                 futures_broker: SimFuturesBroker,
                 calendar: TradingSessionCalendar = None,
                 price_provider=None,
                 options_broker=None):
        self.router = SimBrokerRouter(stock_broker, futures_broker, calendar,
                                      options_broker=options_broker)
        self.calendar = calendar or TradingSessionCalendar()
        self.price_provider = price_provider
        self.position_sync = PositionSync(self.router)
        self.options_broker = options_broker
        self._executed_sessions: set = set()  # 防止同一 session 重复执行

    def is_trading_day(self, d: Optional[date] = None) -> bool:
        return self.calendar.is_trading_day(d)

    def execute_stock_orders(self, orders: List[Dict], session: str = "day") -> List[Dict]:
        """执行股票订单

        Args:
            orders: 订单列表
            session: 'day' / 'night'

        Returns:
            成交记录列表
        """
        if not self.is_trading_day():
            return [{"status": "SKIP", "reason": "非交易日"}]

        fills = []
        for order in orders:
            result = self.router.route(order, session=session)
            if result.get("status") == "PENDING":
                # 模拟成交
                fill = self._simulate_fill(order, market="stock", session=session)
                fills.append(fill)
            else:
                fills.append(result)
        return fills

    def execute_futures_orders(self, orders: List[Dict], session: str = "day") -> List[Dict]:
        """执行期货订单（支持夜盘）

        Args:
            orders: 订单列表
            session: 'day' / 'night'

        Returns:
            成交记录列表
        """
        if not self.calendar.is_futures_trading_day():
            return [{"status": "SKIP", "reason": "非期货交易日"}]

        fills = []
        for order in orders:
            result = self.router.route(order, session=session)
            if result.get("status") == "PENDING":
                fill = self._simulate_fill(order, market="futures", session=session)
                fills.append(fill)
            else:
                fills.append(result)
        return fills

    def execute_options_orders(self, orders: List[Dict], session: str = "day") -> List[Dict]:
        """执行期权订单

        Args:
            orders: 期权订单列表
            session: 'day' / 'night'

        Returns:
            成交记录列表
        """
        if not self.options_broker:
            return [{"status": "SKIP", "reason": "期权模拟盘未初始化"}]

        if not self.calendar.is_trading_day():
            return [{"status": "SKIP", "reason": "非交易日"}]

        fills = []
        for order in orders:
            result = self.router.route(order, session=session)
            if result.get("status") in ("FILLED",):
                fills.append(result)
            elif result.get("status") == "PENDING":
                fill = self._simulate_fill(order, market="options", session=session)
                fills.append(fill)
            else:
                fills.append(result)
        return fills

    def get_greek_exposure(self) -> Dict[str, float]:
        """获取期权组合希腊字母暴露"""
        if self.options_broker and hasattr(self.options_broker, "get_greek_exposure"):
            return self.options_broker.get_greek_exposure()
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}

    def _simulate_fill(self, order: Dict, market: str, session: str) -> Dict:
        """模拟成交（简化版）"""
        symbol = order.get("symbol", "")
        qty = int(order.get("qty", 0))
        side = order.get("side", "BUY")
        price = float(order.get("price", 0))

        # 获取最新价格（简化：使用订单价格）
        if price <= 0:
            price = 10.0  # 默认价格

        # 模拟滑点：±0.05%
        slippage = 0.0005
        if side in ("BUY", "BUY_OPEN"):
            fill_price = price * (1 + slippage)
        else:
            fill_price = price * (1 - slippage)

        fill = {
            "order_id": f"FILL-{symbol}-{int(time.time()*1000)}",
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "price": round(fill_price, 4),
            "amount": round(qty * fill_price, 2),
            "slippage_pct": slippage,
            "status": "FILLED",
            "session": session,
            "market": market,
            "timestamp": datetime.now().isoformat(),
        }

        # 更新持仓
        self.position_sync.update_positions_from_fills([fill])
        return fill

    def get_session_key(self, session: str, d: Optional[date] = None) -> str:
        """生成 session 唯一键（用于去重）"""
        if d is None:
            d = date.today()
        return f"{d.isoformat()}-{session}"

    def mark_session_executed(self, session: str, d: Optional[date] = None) -> None:
        """标记 session 已执行"""
        key = self.get_session_key(session, d)
        self._executed_sessions.add(key)

    def is_session_executed(self, session: str, d: Optional[date] = None) -> bool:
        """检查 session 是否已执行"""
        key = self.get_session_key(session, d)
        return key in self._executed_sessions
