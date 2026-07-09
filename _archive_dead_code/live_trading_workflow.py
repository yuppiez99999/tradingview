#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实盘交易自动化工作流 - Live Trading Workflow v1.0
==================================================
生产级每日自动交易引擎，支持交易日 7:00 AM 自动触发。

设计原则：
  1. 接口抽象 - 券商/数据/通知均通过抽象基类定义，当前提供模拟实现
  2. 渐进接入 - 接入实盘时只需替换对应的接口实现，核心流程不变
  3. 故障隔离 - 每个阶段独立执行，单点故障不阻断整体流程
  4. 状态持久化 - 订单/持仓/执行历史全部落盘，可审计可追溯

架构：
  ┌─────────────────────────────────────────────────────────┐
  │                   LiveTradingWorkflow                     │
  ├─────────────────────────────────────────────────────────┤
  │  Scheduler (APScheduler / Windows Task / Cron)           │
  │  ├─ 07:00  PreMarketPhase  盘前检查 + 风控 + 计划生成    │
  │  ├─ 09:25  AuctionPhase    集合竞价数据 + 订单调整       │
  │  ├─ 09:30  ExecutionAM     上午批次执行 (09:30-10:30)   │
  │  ├─ 11:30  MiddayCheck     上午成交确认 + 偏差检查       │
  │  ├─ 14:00  ExecutionPM     下午批次执行 (14:00-14:30)   │
  │  └─ 15:00  PostMarket      盘后清算 + 报告 + 通知       │
  ├─────────────────────────────────────────────────────────┤
  │  可替换接口层 (Strategy Pattern)                         │
  │  ├─ BrokerInterface      券商下单/撤单/查持仓           │
  │  ├─ MarketDataProvider   实时行情/集合竞价/K线           │
  │  ├─ NotificationService  微信/钉钉/邮件通知              │
  │  └─ PositionStore        持仓/订单状态持久化              │
  └─────────────────────────────────────────────────────────┘

用法：
  python live_trading_workflow.py                  # 常驻进程，每天7:00自动运行
  python live_trading_workflow.py --once           # 立即执行一次（测试用）
  python live_trading_workflow.py --mode dry-run   # 干跑模式（生成指令但不执行）
  python live_trading_workflow.py --date 2026-07-06  # 指定日期执行
"""

import os
import sys
import json
import signal
import logging
import threading
import traceback
from abc import ABC, abstractmethod
from datetime import datetime, date, time, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict
from enum import Enum

# ---- 路径设置 ----
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# ---- 日志 ----
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(
            os.path.join(LOG_DIR, f"live_trading_{datetime.now().strftime('%Y%m%d')}.log"),
            encoding='utf-8'
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("LiveTrading")

# ---- 保护性看跌管理器 (可选模块) ----
try:
    from protective_put_manager import ProtectivePutManager
    HAS_PUT_MANAGER = True
except ImportError:
    HAS_PUT_MANAGER = False
    logger.info("protective_put_manager 不可用，保护性看跌功能将跳过")

# ---- v7.1.2 第三方整合模块 ----
try:
    from utils.alert_service import AlertEvaluator, evaluate_risk_alerts, SUPPORTED_ALERT_TYPES
    from utils.phase_decision_guardrail import apply_phase_decision_guardrails, apply_emergency_guardrail
    from utils.market_context_guardrail import apply_daily_market_context_guardrail, build_market_context_from_risk_state
    from utils.semantic_backtest import SemanticBacktestEngine, EvaluationConfig
    HAS_V712_MODULES = True
except ImportError:
    HAS_V712_MODULES = False
    logger.info("v7.1.2 第三方整合模块不可用，相关功能将跳过")


# ================================================================
# 枚举定义
# ================================================================

class WorkflowPhase(Enum):
    PRE_MARKET = "pre_market"       # 07:00 盘前准备
    AUCTION = "auction"             # 09:25 集合竞价
    EXECUTION_AM = "execution_am"   # 09:30 上午执行
    MIDDAY = "midday"               # 11:30 午间检查
    EXECUTION_PM = "execution_pm"   # 14:00 下午执行
    POST_MARKET = "post_market"     # 15:00 盘后清算


class PhaseStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class OrderStatus(Enum):
    PENDING = "pending"         # 待发送
    SENT = "sent"               # 已发送至券商
    PARTIAL_FILLED = "partial"  # 部分成交
    FILLED = "filled"           # 全部成交
    CANCELLED = "cancelled"     # 已撤单
    REJECTED = "rejected"       # 被拒绝
    EXPIRED = "expired"         # 过期


class RiskLevel(Enum):
    NORMAL = (0, "正常", 1.0)
    YELLOW = (1, "黄色预警", 0.50)
    ORANGE = (2, "橙色预警", 0.0)
    RED = (3, "红色预警", 0.0)
    EXTREME = (4, "极端", 0.0)

    def __init__(self, level: int, name: str, multiplier: float):
        self.level = level
        self.level_name = name
        self.capital_multiplier = multiplier


# ================================================================
# 数据类
# ================================================================

@dataclass
class LiveOrder:
    """实盘订单"""
    order_id: str
    code: str
    name: str
    side: str          # BUY / SELL
    quantity: int
    limit_price: float
    session: str       # morning / afternoon
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: int = 0
    avg_price: float = 0.0
    sent_at: Optional[str] = None
    filled_at: Optional[str] = None
    broker_order_id: Optional[str] = None
    error_message: Optional[str] = None
    style: str = ""
    risk_level: str = ""


@dataclass
class LivePosition:
    """持仓"""
    code: str
    name: str
    quantity: int
    avg_cost: float
    current_price: float = 0.0
    market_value: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    style: str = ""
    risk: str = ""


@dataclass
class PhaseResult:
    """阶段执行结果"""
    phase: WorkflowPhase
    status: PhaseStatus
    started_at: str
    completed_at: str = ""
    duration_seconds: float = 0.0
    data: Dict = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class DailyExecutionReport:
    """每日执行报告"""
    trade_date: str
    workflow_version: str = "1.0"
    phases: List[PhaseResult] = field(default_factory=list)
    risk_level: str = "NORMAL"
    capital_multiplier: float = 1.0
    total_orders: int = 0
    filled_orders: int = 0
    partial_orders: int = 0
    rejected_orders: int = 0
    total_amount: float = 0.0
    total_commission: float = 0.0
    positions_snapshot: List[Dict] = field(default_factory=list)
    alerts: List[str] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""


# ================================================================
# 抽象接口层 (可替换实现)
# ================================================================

class BrokerInterface(ABC):
    """
    券商接口抽象基类

    接入实盘时实现此接口：
      - MockBroker: 当前模拟实现（打印日志，模拟成交）
      - HuataiBroker: 华泰证券 API
      - CITICBroker: 中信证券 API
      - EastMoneyBroker: 东方财富 API
      - IBBroker: Interactive Brokers
    """

    @abstractmethod
    def connect(self) -> bool:
        """建立券商连接，返回是否成功"""
        ...

    @abstractmethod
    def disconnect(self) -> bool:
        """断开券商连接"""
        ...

    @abstractmethod
    def get_account_info(self) -> Dict:
        """获取账户信息（可用资金、总资产、持仓等）"""
        ...

    @abstractmethod
    def get_positions(self) -> List[Dict]:
        """获取当前持仓列表"""
        ...

    @abstractmethod
    def place_order(self, code: str, name: str, side: str,
                    quantity: int, limit_price: float,
                    order_type: str = "LIMIT") -> Dict:
        """下单，返回订单确认信息"""
        ...

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> Dict:
        """撤单"""
        ...

    @abstractmethod
    def query_order(self, broker_order_id: str) -> Dict:
        """查询订单状态"""
        ...

    @abstractmethod
    def query_today_orders(self) -> List[Dict]:
        """查询当日所有订单"""
        ...

    @abstractmethod
    def query_today_trades(self) -> List[Dict]:
        """查询当日所有成交"""
        ...


class MarketDataProvider(ABC):
    """
    市场数据接口抽象基类

    接入实盘时实现此接口：
      - MockMarketData: 当前模拟实现
      - WindDataProvider: Wind 金融终端
      - TushareProvider: Tushare Pro
      - EastMoneyProvider: 东方财富行情
      - ExchangeDirectProvider: 交易所直连
    """

    @abstractmethod
    def get_realtime_quote(self, codes: List[str]) -> Dict[str, Dict]:
        """获取实时行情（最新价、涨跌幅、成交量等）"""
        ...

    @abstractmethod
    def get_auction_data(self, codes: List[str]) -> Dict[str, Dict]:
        """获取集合竞价数据（09:25 产生）"""
        ...

    @abstractmethod
    def get_market_indices(self) -> Dict[str, float]:
        """获取市场指数（上证、深证、创业、科创50等）"""
        ...

    @abstractmethod
    def get_market_breadth(self) -> Dict:
        """获取市场宽度（涨跌家数、涨停跌停等）"""
        ...

    @abstractmethod
    def get_sector_flow(self) -> Dict:
        """获取板块资金流向"""
        ...

    @abstractmethod
    def get_margin_data(self) -> Dict:
        """获取两融数据"""
        ...


class NotificationService(ABC):
    """
    通知服务抽象基类

    接入实盘时实现此接口：
      - LogNotification: 当前仅日志实现
      - WeChatNotification: 微信企业号/公众号推送
      - DingTalkNotification: 钉钉机器人
      - EmailNotification: 邮件通知
      - SMSNotification: 短信通知
    """

    @abstractmethod
    def send_alert(self, title: str, content: str, level: str = "info") -> bool:
        """发送告警通知（紧急事件立即推送）"""
        ...

    @abstractmethod
    def send_daily_report(self, report: DailyExecutionReport) -> bool:
        """发送每日执行报告"""
        ...

    @abstractmethod
    def send_trade_confirmation(self, order: LiveOrder) -> bool:
        """发送成交确认"""
        ...


class PositionStore(ABC):
    """
    持仓/订单持久化抽象基类

    接入实盘时实现此接口：
      - JSONLPositionStore: 当前JSONL文件实现
      - SQLitePositionStore: SQLite 轻量级数据库
      - PostgreSQLPositionStore: PostgreSQL 生产级
    """

    @abstractmethod
    def save_order(self, order: LiveOrder) -> bool:
        """保存订单"""
        ...

    @abstractmethod
    def update_order(self, order_id: str, updates: Dict) -> bool:
        """更新订单状态"""
        ...

    @abstractmethod
    def get_orders(self, trade_date: str) -> List[Dict]:
        """获取指定日期的订单"""
        ...

    @abstractmethod
    def save_positions(self, positions: List[LivePosition]) -> bool:
        """保存持仓快照"""
        ...

    @abstractmethod
    def get_latest_positions(self) -> List[Dict]:
        """获取最新持仓"""
        ...

    @abstractmethod
    def save_execution_report(self, report: DailyExecutionReport) -> bool:
        """保存执行报告"""
        ...

    @abstractmethod
    def get_execution_history(self, days: int = 30) -> List[Dict]:
        """获取执行历史"""
        ...


# ================================================================
# 模拟实现 (Mock Implementations - 后期替换为真实券商/数据)
# ================================================================

class MockBroker(BrokerInterface):
    """模拟券商接口 - 用于开发和测试"""

    def __init__(self):
        self.connected = False
        self._order_counter = 0

    def connect(self) -> bool:
        self.connected = True
        logger.info("[MockBroker] 连接成功（模拟）")
        return True

    def disconnect(self) -> bool:
        self.connected = False
        logger.info("[MockBroker] 断开连接（模拟）")
        return True

    def get_account_info(self) -> Dict:
        return {
            "account_id": "MOCK_001",
            "available_cash": 3_250_000.0,
            "total_asset": 5_000_000.0,
            "frozen_cash": 0.0,
            "market_value": 1_750_000.0,
        }

    def get_positions(self) -> List[Dict]:
        return []  # 模拟空仓

    def place_order(self, code: str, name: str, side: str,
                    quantity: int, limit_price: float,
                    order_type: str = "LIMIT") -> Dict:
        self._order_counter += 1
        broker_id = f"MOCK_{datetime.now().strftime('%H%M%S')}_{self._order_counter:04d}"
        logger.info(f"[MockBroker] 下单: {code} {name} {side} {quantity}股 @{limit_price:.3f} -> {broker_id}")
        return {
            "success": True,
            "broker_order_id": broker_id,
            "code": code,
            "name": name,
            "side": side,
            "quantity": quantity,
            "limit_price": limit_price,
            "status": "sent",
            "message": "模拟下单成功",
        }

    def cancel_order(self, broker_order_id: str) -> Dict:
        logger.info(f"[MockBroker] 撤单: {broker_order_id}")
        return {"success": True, "broker_order_id": broker_order_id, "status": "cancelled"}

    def query_order(self, broker_order_id: str) -> Dict:
        # 模拟全部成交
        return {
            "broker_order_id": broker_order_id,
            "status": "filled",
            "filled_quantity": 0,  # 将在调用方填充
            "avg_price": 0.0,
        }

    def query_today_orders(self) -> List[Dict]:
        return []

    def query_today_trades(self) -> List[Dict]:
        return []


class MockMarketData(MarketDataProvider):
    """模拟市场数据 - 用于开发和测试"""

    def get_realtime_quote(self, codes: List[str]) -> Dict[str, Dict]:
        """返回模拟实时行情"""
        # 从建仓计划中读取预估价格作为模拟数据
        quotes = {}
        try:
            plan_path = os.path.join(BASE_DIR, "500万建仓计划_20260706.json")
            if os.path.exists(plan_path):
                import json as _json
                with open(plan_path, 'r', encoding='utf-8') as f:
                    plan = _json.load(f)
                position_plan = plan.get("position_plan", {})
                for code in codes:
                    if code in position_plan:
                        info = position_plan[code]
                        price = float(info.get("est_price", 0))
                        quotes[code] = {
                            "code": code,
                            "name": info.get("name", code),
                            "latest_price": price,
                            "open": price * 0.998,
                            "high": price * 1.005,
                            "low": price * 0.995,
                            "volume": 10_000_000,
                            "change_pct": 0.0,
                            "bid1": price * 0.999,
                            "ask1": price * 1.001,
                        }
        except Exception as e:
            logger.warning(f"[MockMarketData] 读取建仓计划失败: {e}")

        logger.info(f"[MockMarketData] 返回 {len(quotes)}/{len(codes)} 只标的模拟行情")
        return quotes

    def get_auction_data(self, codes: List[str]) -> Dict[str, Dict]:
        """返回模拟集合竞价数据"""
        quotes = self.get_realtime_quote(codes)
        auction = {}
        for code, q in quotes.items():
            auction[code] = {
                "code": code,
                "auction_price": q["latest_price"],
                "auction_volume": int(q["volume"] * 0.05),  # 模拟5%集合竞价量
                "pre_close": q["latest_price"] * 0.998,
                "upper_limit": q["latest_price"] * 1.10,
                "lower_limit": q["latest_price"] * 0.90,
            }
        return auction

    def get_market_indices(self) -> Dict[str, float]:
        return {
            "shanghai": 3350.0,
            "shenzhen": 10800.0,
            "chinext": 2150.0,
            "star_50": 1050.0,
            "csi_300": 3950.0,
            "csi_500": 5850.0,
        }

    def get_market_breadth(self) -> Dict:
        return {
            "up_count": 2100,
            "down_count": 1800,
            "flat_count": 500,
            "limit_up": 45,
            "limit_down": 12,
            "total_volume_billion": 850.0,
        }

    def get_sector_flow(self) -> Dict:
        return {
            "high_end_manufacturing": 12.5,
            "defensive": 5.2,
            "resources": -3.1,
            "finance": 8.7,
        }

    def get_margin_data(self) -> Dict:
        return {
            "total_balance_billion": 1520.0,
            "daily_change_billion": 5.2,
            "change_pct_5d": 0.02,
            "change_pct_20d": 0.05,
        }


class LogNotification(NotificationService):
    """日志通知 - 仅输出到日志和控制台"""

    def send_alert(self, title: str, content: str, level: str = "info") -> bool:
        prefix = {"info": "[INFO]", "warning": "[WARN]", "critical": "[CRITICAL]"}.get(level, "[INFO]")
        logger.info(f"NOTIFICATION {prefix} {title}: {content}")
        return True

    def send_daily_report(self, report: DailyExecutionReport) -> bool:
        logger.info(f"========== 每日执行报告 {report.trade_date} ==========")
        logger.info(f"  风险等级: {report.risk_level}")
        logger.info(f"  订单: {report.total_orders} 总 / {report.filled_orders} 成交 / {report.partial_orders} 部分成交")
        logger.info(f"  金额: {report.total_amount:,.0f} 元")
        logger.info(f"  告警: {len(report.alerts)} 条")
        if report.alerts:
            for a in report.alerts:
                logger.info(f"    - {a}")
        logger.info(f"========================================================")
        return True

    def send_trade_confirmation(self, order: LiveOrder) -> bool:
        logger.info(f"TRADE CONFIRMED: {order.code} {order.name} "
                     f"{order.side} {order.filled_qty}/{order.quantity}股 @{order.avg_price:.3f}")
        return True


class JSONLPositionStore(PositionStore):
    """JSONL文件持久化 - 轻量级实现"""

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or os.path.join(BASE_DIR, "data", "live_trading")
        os.makedirs(self.base_dir, exist_ok=True)

    def _get_date_path(self, trade_date: str) -> str:
        date_dir = os.path.join(self.base_dir, trade_date.replace("-", ""))
        os.makedirs(date_dir, exist_ok=True)
        return date_dir

    def _append_jsonl(self, filepath: str, data: Dict):
        with open(filepath, 'a', encoding='utf-8') as f:
            f.write(json.dumps(data, ensure_ascii=False, default=str) + '\n')

    def save_order(self, order: LiveOrder) -> bool:
        try:
            date_dir = self._get_date_path(order.order_id[:10] if len(order.order_id) > 10
                                           else datetime.now().strftime("%Y%m%d"))
            filepath = os.path.join(date_dir, "orders.jsonl")
            self._append_jsonl(filepath, asdict(order))
            return True
        except Exception as e:
            logger.error(f"[JSONLStore] 保存订单失败: {e}")
            return False

    def update_order(self, order_id: str, updates: Dict) -> bool:
        # JSONL 不可原地更新，追加更新记录
        try:
            date_dir = self._get_date_path(order_id[:10] if len(order_id) > 10
                                           else datetime.now().strftime("%Y%m%d"))
            filepath = os.path.join(date_dir, "order_updates.jsonl")
            self._append_jsonl(filepath, {"order_id": order_id, "updates": updates,
                                          "timestamp": datetime.now().isoformat()})
            return True
        except Exception as e:
            logger.error(f"[JSONLStore] 更新订单失败: {e}")
            return False

    def get_orders(self, trade_date: str) -> List[Dict]:
        try:
            date_dir = self._get_date_path(trade_date.replace("-", ""))
            filepath = os.path.join(date_dir, "orders.jsonl")
            if not os.path.exists(filepath):
                return []
            orders = []
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        orders.append(json.loads(line))
            return orders
        except Exception as e:
            logger.error(f"[JSONLStore] 读取订单失败: {e}")
            return []

    def save_positions(self, positions: List[LivePosition]) -> bool:
        try:
            today_dir = self._get_date_path(datetime.now().strftime("%Y%m%d"))
            filepath = os.path.join(today_dir, "positions.json")
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump([asdict(p) for p in positions], f, ensure_ascii=False, indent=2)
            # 同时更新最新持仓
            latest_path = os.path.join(self.base_dir, "latest_positions.json")
            with open(latest_path, 'w', encoding='utf-8') as f:
                json.dump([asdict(p) for p in positions], f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"[JSONLStore] 保存持仓失败: {e}")
            return False

    def get_latest_positions(self) -> List[Dict]:
        try:
            latest_path = os.path.join(self.base_dir, "latest_positions.json")
            if not os.path.exists(latest_path):
                return []
            with open(latest_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[JSONLStore] 读取最新持仓失败: {e}")
            return []

    def save_execution_report(self, report: DailyExecutionReport) -> bool:
        try:
            date_dir = self._get_date_path(report.trade_date.replace("-", ""))
            filepath = os.path.join(date_dir, "execution_report.json")
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(asdict(report), f, ensure_ascii=False, indent=2, default=str)
            return True
        except Exception as e:
            logger.error(f"[JSONLStore] 保存执行报告失败: {e}")
            return False

    def get_execution_history(self, days: int = 30) -> List[Dict]:
        history = []
        try:
            for i in range(days):
                d = date.today() - timedelta(days=i)
                date_dir = os.path.join(self.base_dir, d.strftime("%Y%m%d"))
                filepath = os.path.join(date_dir, "execution_report.json")
                if os.path.exists(filepath):
                    with open(filepath, 'r', encoding='utf-8') as f:
                        history.append(json.load(f))
        except Exception as e:
            logger.error(f"[JSONLStore] 读取执行历史失败: {e}")
        return history


# ================================================================
# 交易日历 (可替换为动态获取)
# ================================================================

# A股2026年节假日（与 trading_workflow.py 保持一致）
CN_HOLIDAYS_2026: set = {
    date(2026, 1, 1),                                                    # 元旦
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),            # 春节
    date(2026, 2, 19), date(2026, 2, 20),
    date(2026, 4, 6),                                                    # 清明
    date(2026, 5, 1), date(2026, 5, 4), date(2026, 5, 5),               # 劳动节
    date(2026, 6, 19),                                                   # 端午
    date(2026, 9, 25),                                                   # 中秋
    date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5),            # 国庆
    date(2026, 10, 6), date(2026, 10, 7),
}


def is_trading_day(d: Optional[date] = None) -> bool:
    """判断是否为A股交易日"""
    if d is None:
        d = date.today()
    if d.weekday() >= 5:  # 周六日
        return False
    if d in CN_HOLIDAYS_2026:
        return False
    return True


# ================================================================
# 主工作流引擎
# ================================================================

class LiveTradingWorkflow:
    """
    实盘交易自动化工作流

    完整的交易日自动化流水线：
      07:00 → PreMarketPhase:  盘前检查 + 风控 + 计划生成
      09:25 → AuctionPhase:    集合竞价数据获取 + 订单调整
      09:30 → ExecutionAM:     上午批次执行 (09:30-10:30)
      11:30 → MiddayCheck:     上午成交确认 + 偏差检查
      14:00 → ExecutionPM:     下午批次执行 (14:00-14:30)
      15:00 → PostMarketPhase: 盘后清算 + 报告 + 通知

    接口可替换：
      - broker:      BrokerInterface     (默认 MockBroker)
      - market_data: MarketDataProvider  (默认 MockMarketData)
      - notifier:    NotificationService (默认 LogNotification)
      - store:       PositionStore       (默认 JSONLPositionStore)
    """

    # 各阶段执行时间窗口
    PHASE_SCHEDULE = {
        WorkflowPhase.PRE_MARKET:   time(7, 0),
        WorkflowPhase.AUCTION:      time(9, 25),
        WorkflowPhase.EXECUTION_AM: time(9, 30),
        WorkflowPhase.MIDDAY:       time(11, 30),
        WorkflowPhase.EXECUTION_PM: time(14, 0),
        WorkflowPhase.POST_MARKET:  time(15, 0),
    }

    def __init__(self,
                 broker: Optional[BrokerInterface] = None,
                 market_data: Optional[MarketDataProvider] = None,
                 notifier: Optional[NotificationService] = None,
                 store: Optional[PositionStore] = None,
                 mode: str = "live"):
        """
        Args:
            broker: 券商接口实现
            market_data: 市场数据接口实现
            notifier: 通知服务实现
            store: 持久化存储实现
            mode: 运行模式 - "live"(实盘), "dry-run"(干跑), "backtest"(回测)
        """
        self.mode = mode
        self.trade_date = date.today()

        # 依赖注入 - 接口实现
        self.broker = broker or MockBroker()
        self.market_data = market_data or MockMarketData()
        self.notifier = notifier or LogNotification()
        self.store = store or JSONLPositionStore()

        # 运行状态
        self.running = False
        self._shutdown_requested = False
        self._current_phase: Optional[WorkflowPhase] = None

        # 今日订单和持仓
        self.orders: List[LiveOrder] = []
        self.positions: List[LivePosition] = []
        self.daily_report = DailyExecutionReport(trade_date="")

        # 系统组件（懒加载）
        self._build_executor = None
        self._risk_manager = None
        self._put_manager = None   # 保护性看跌管理器(懒加载)
        self._order_counter = 0

        # 注册信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    # ---------------------------------------------------------------
    # 信号处理
    # ---------------------------------------------------------------

    def _signal_handler(self, signum, frame):
        logger.info(f"收到信号 {signum}，准备安全退出...")
        self._shutdown_requested = True
        if self.broker and hasattr(self.broker, 'disconnect'):
            self.broker.disconnect()

    # ---------------------------------------------------------------
    # 订单管理
    # ---------------------------------------------------------------

    def _next_order_id(self) -> str:
        self._order_counter += 1
        return f"ORD_{self.trade_date.strftime('%Y%m%d')}_{self._order_counter:04d}"

    # ---------------------------------------------------------------
    # 阶段执行器
    # ---------------------------------------------------------------

    def _run_phase(self, phase: WorkflowPhase, func, *args, **kwargs) -> PhaseResult:
        """统一的阶段执行包装器"""
        self._current_phase = phase
        started = datetime.now()
        result = PhaseResult(
            phase=phase,
            status=PhaseStatus.RUNNING,
            started_at=started.isoformat(),
        )

        try:
            logger.info(f"{'='*60}")
            logger.info(f"PHASE: {phase.value} - {started.strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"{'='*60}")

            success, data = func(*args, **kwargs)

            completed = datetime.now()
            result.completed_at = completed.isoformat()
            result.duration_seconds = (completed - started).total_seconds()

            if success:
                result.status = PhaseStatus.SUCCESS
                result.data = data or {}
                logger.info(f"[PASS] {phase.value} 完成 ({result.duration_seconds:.1f}s)")
            else:
                result.status = PhaseStatus.FAILED
                result.errors = data.get("errors", []) if isinstance(data, dict) else [str(data)]
                logger.warning(f"[FAIL] {phase.value} 失败")

        except Exception as e:
            completed = datetime.now()
            result.completed_at = completed.isoformat()
            result.duration_seconds = (completed - started).total_seconds()
            result.status = PhaseStatus.FAILED
            result.errors.append(f"{type(e).__name__}: {str(e)}")
            logger.error(f"[ERROR] {phase.value} 异常: {traceback.format_exc()}")

        self.daily_report.phases.append(result)
        return result

    # ---------------------------------------------------------------
    # Phase 0: 盘前准备 (07:00)
    # ---------------------------------------------------------------

    def phase_pre_market(self) -> Tuple[bool, Dict]:
        """
        盘前准备阶段 (07:00 执行)

        1. 交易日确认
        2. 文件完整性检查
        3. 券商连接
        4. 账户状态检查
        5. 市场状态获取（行情/广度/资金流向/两融）
        6. EnhancedRiskManager v7.1 综合风控评估
        7. 生成当日交易计划
        8. 风控级别判定 → 资金倍率
        """
        result = {}

        # 1. 交易日确认
        if not is_trading_day(self.trade_date):
            logger.info(f"{self.trade_date} 非交易日，跳过")
            return False, {"reason": "non_trading_day"}

        logger.info(f"交易日确认: {self.trade_date} 为交易日")

        # 2. 文件完整性检查
        plan_file = os.path.join(BASE_DIR, "500万建仓计划_20260706.json")
        if not os.path.exists(plan_file):
            logger.warning("建仓计划文件不存在，尝试生成...")
            result["plan_file_missing"] = True
        else:
            result["plan_file"] = plan_file

        # 3. 券商连接
        if not self.broker.connect():
            self.notifier.send_alert("券商连接失败", "无法连接券商交易接口", "critical")
            return False, {"error": "broker_connection_failed"}

        # 4. 账户状态
        account = self.broker.get_account_info()
        logger.info(f"账户状态: 可用资金={account.get('available_cash', 0):,.0f}, "
                     f"总资产={account.get('total_asset', 0):,.0f}")
        result["account"] = account

        # 5. 市场状态获取
        market_state = self._build_market_state()
        result["market_state"] = market_state

        # 6. EnhancedRiskManager v7.1 综合风控
        risk_result = self._run_risk_assessment(market_state)
        result["risk_assessment"] = risk_result

        # 7. 生成交易计划
        plan_result = self._generate_trading_plan(risk_result)
        result["trading_plan"] = plan_result

        # 8. 风控级别判定
        risk_level = risk_result.get("risk_level", RiskLevel.NORMAL)
        capital_multiplier = risk_result.get("capital_multiplier", 1.0)
        self.daily_report.risk_level = risk_level.level_name
        self.daily_report.capital_multiplier = capital_multiplier

        logger.info(f"盘前风控: {risk_level.level_name} (L{risk_level.level}) "
                     f"资金倍率={capital_multiplier:.0%}")

        # 极端情况发送告警
        if risk_level.level >= 3:
            self.notifier.send_alert(
                f"风控{risk_level.level_name}",
                f"市场状态触发{risk_level.level_name}，资金倍率={capital_multiplier}\n"
                f"VIX={market_state.get('vix_proxy')}, "
                f"ETF信号={market_state.get('etf_signal')}, "
                f"宏观热度={market_state.get('macro_heat')}",
                "critical" if risk_level.level >= 4 else "warning"
            )

        # 9. 保护性看跌期权建议
        put_result = self._generate_put_recommendations(market_state, risk_result)
        result["protective_put"] = put_result
        if put_result and put_result.get("status") == "generated":
            rec = put_result.get("recommendation")
            self.daily_report.alerts.append(
                f"保护Put: {put_result.get('regime', '').upper()} "
                f"{put_result.get('order_count', 0)}笔 "
                f"权利金{put_result.get('total_cost', 0)/10000:.2f}万"
            )

        # 10. v7.1.2: 决策护栏校验（大盘环境 + 时段决策）
        if HAS_V712_MODULES:
            try:
                guardrail_tags = []
                plan_orders = plan_result.get("orders", [])
                emergency = risk_result.get("risk_level", RiskLevel.NORMAL).level

                # 构建决策信号供护栏校验
                simulated_decision = {
                    "decision": "buy" if plan_orders else "hold",
                    "position_recommendation": (
                        "aggressive" if emergency == 0 and len(plan_orders) > 3
                        else "moderate" if len(plan_orders) > 0
                        else "none"
                    ),
                    "sentiment_score": 50 + min(emergency * 10, 30),
                    "operation_advice": (
                        f"盘前计划生成{len(plan_orders)}笔买入订单"
                        if plan_orders else "今日无交易计划"
                    ),
                }

                # 大盘环境护栏
                market_ctx = market_state.get("_market_context")
                if market_ctx:
                    ctx_tags = apply_daily_market_context_guardrail(
                        simulated_decision, market_ctx, "zh"
                    )
                    guardrail_tags.extend(ctx_tags)

                # 时段决策护栏
                phase_tags = apply_phase_decision_guardrails(
                    simulated_decision,
                    market_phase_summary={
                        "phase": "pre_market",
                        "description": "盘前准备阶段(07:00-09:15)",
                        "is_trading": False,
                    },
                    analysis_context_pack_overview="实盘交易盘前风险评估",
                    report_language="zh",
                )
                guardrail_tags.extend(phase_tags)

                # 紧急协议护栏
                if emergency >= 1:
                    emergency_tags = apply_emergency_guardrail(
                        emergency, simulated_decision, "zh"
                    )
                    guardrail_tags.extend(emergency_tags)

                result["guardrail_tags"] = guardrail_tags
                if guardrail_tags:
                    logger.info(
                        f"[GUARDRAIL] 盘前护栏: {len(guardrail_tags)}个标签 - "
                        f"{', '.join(guardrail_tags[:5])}"
                    )

            except Exception as e:
                logger.warning(f"[GUARDRAIL] 护栏校验异常: {e}")
                result["guardrail_error"] = str(e)

        return True, result

    def _build_market_state(self) -> Dict:
        """构建综合市场状态"""
        state = {}

        # 指数数据
        indices = self.market_data.get_market_indices()
        state["indices"] = indices

        # 市场宽度
        breadth = self.market_data.get_market_breadth()
        state["breadth"] = breadth

        # 板块资金流向
        try:
            sector_flow = self.market_data.get_sector_flow()
            state["sector_flow"] = sector_flow
        except Exception:
            pass

        # 两融数据
        try:
            margin = self.market_data.get_margin_data()
            state["margin"] = margin
        except Exception:
            pass

        # 模拟的VIX代理和收益率（从真实市场数据替换）
        state["vix_proxy"] = 22
        state["index_return_5d"] = 0.01
        state["index_return_20d"] = 0.03
        state["volatility"] = 0.18
        state["margin_balance_change"] = 0.02
        state["volume_ratio"] = 1.05

        # ETF 资金流向（v7.1）
        state["etf_signal"] = "neutral"
        state["macro_heat"] = 50
        state["macro_regime"] = "中性"

        # 尝试调用 v7.1 模块
        try:
            from utils.etf_flow_monitor import ETFFlowMonitor
            etf = ETFFlowMonitor()
            flow_data = etf.detect_signals({})
            plan = etf.generate_trading_plan(flow_data)
            state["etf_signal"] = plan.get("overall_signal", "neutral")
            state["etf_net_flow"] = plan.get("net_flow_billion", 0)
        except Exception as e:
            logger.debug(f"ETF流向检测跳过: {e}")

        try:
            from utils.real_economy_indicator import RealEconomyIndicator
            macro = RealEconomyIndicator()
            sample_data = {
                "corrugated_paper": 2500, "recycled_paper": 1800,
                "cement": 420, "rebar": 3800, "copper": 72000,
                "aluminum": 18500, "baijiu": 280
            }
            score = macro.calculate_score(sample_data)
            regime, mult = macro.to_risk_signal(score)
            state["macro_heat"] = score
            state["macro_regime"] = regime
        except Exception as e:
            logger.debug(f"宏观指标检测跳过: {e}")

        # ---- v7.1.2: 构建标准化大盘环境上下文（供护栏使用） ----
        if HAS_V712_MODULES:
            try:
                risk_state = {
                    "vix_proxy": state.get("vix_proxy", 22),
                    "emergency_level": 0,
                    "etf_signal": state.get("etf_signal", "neutral"),
                    "macro_heat": state.get("macro_heat", 50),
                    "macro_regime": state.get("macro_regime", "中性"),
                    "index_return_20d": state.get("index_return_20d", 0.03),
                    "stop_loss_triggered": 0,
                }
                market_context = build_market_context_from_risk_state(risk_state)
                state["_market_context"] = market_context
                state["_risk_state"] = risk_state
            except Exception as e:
                logger.debug(f"市场上下文构建跳过: {e}")

        return state

    def _run_risk_assessment(self, market_state: Dict) -> Dict:
        """运行 EnhancedRiskManager v7.1 综合风控"""
        result = {"risk_level": RiskLevel.NORMAL, "capital_multiplier": 1.0, "alerts": []}

        try:
            from enhanced_risk_manager import EnhancedRiskManager
            rm = EnhancedRiskManager()

            # 构建当前持仓数据
            positions_list = self.broker.get_positions()
            current_positions = {}
            for p in positions_list:
                current_positions[p.get("code", p.get("symbol", ""))] = {
                    "quantity": p.get("quantity", 0),
                    "cost": p.get("avg_cost", 0),
                    "current_price": p.get("current_price", 0),
                }

            market_state["current_positions"] = current_positions

            # 运行完整风控周期
            risk_cycle = rm.run_risk_management_cycle(
                market_data=market_state,
                portfolio_data={
                    "total_value": 5_000_000,
                    "positions": current_positions,
                },
                performance_data={},
            )

            # 解析风控决策
            emergency = risk_cycle.get("emergency_level", 0)
            multiplier = risk_cycle.get("capital_multiplier", 1.0)
            alerts = risk_cycle.get("alerts", [])

            if emergency >= 4:
                result["risk_level"] = RiskLevel.EXTREME
            elif emergency >= 3:
                result["risk_level"] = RiskLevel.RED
            elif emergency >= 2:
                result["risk_level"] = RiskLevel.ORANGE
            elif emergency >= 1:
                result["risk_level"] = RiskLevel.YELLOW
            else:
                result["risk_level"] = RiskLevel.NORMAL

            result["capital_multiplier"] = multiplier
            result["alerts"] = alerts
            result["risk_cycle"] = risk_cycle

            # 止损检查
            stop_loss_result = rm.assess_stop_loss(current_positions) if hasattr(rm, 'assess_stop_loss') else {}
            result["stop_loss"] = stop_loss_result

            # ---- v7.1.2: 多通道预警评估 ----
            if HAS_V712_MODULES:
                try:
                    risk_alerts = evaluate_risk_alerts(market_state, emergency)
                    result["v712_alerts"] = risk_alerts
                    if risk_alerts.get("triggered", 0) > 0:
                        logger.warning(
                            f"[ALERT_SVC] 风险预警: {risk_alerts['triggered']}/{risk_alerts['total']} 触发"
                        )
                        result["alerts"].extend(risk_alerts.get("messages", []))
                except Exception as e:
                    logger.debug(f"[ALERT_SVC] 预警评估跳过: {e}")

        except ImportError:
            logger.info("EnhancedRiskManager 不可用，使用简化风控")
            # 简化风控逻辑
            vix = market_state.get("vix_proxy", 20)
            if vix > 40:
                result["risk_level"] = RiskLevel.RED
            elif vix > 35:
                result["risk_level"] = RiskLevel.ORANGE
            elif vix > 30:
                result["risk_level"] = RiskLevel.YELLOW
        except Exception as e:
            logger.error(f"风控评估异常: {e}")
            result["error"] = str(e)

        return result

    def _generate_trading_plan(self, risk_result: Dict) -> Dict:
        """生成当日交易计划"""
        plan_result = {"orders": [], "day_capital": 0}
        risk_level = risk_result.get("risk_level", RiskLevel.NORMAL)

        # 红色及以上暂停建仓
        if risk_level.level >= 2 and risk_level.capital_multiplier == 0:
            logger.warning(f"风控等级 {risk_level.level_name}，今日暂停建仓")
            plan_result["status"] = "suspended"
            plan_result["reason"] = f"风控等级: {risk_level.level_name}"
            return plan_result

        try:
            from build_plan_executor import BuildPlanExecutor
            executor = BuildPlanExecutor()
            sheet = executor.generate_daily_orders(
                target_date=self.trade_date,
                capital_multiplier=risk_level.capital_multiplier,
            )

            # 转换为 LiveOrder
            for o in sheet.morning_orders:
                order = LiveOrder(
                    order_id=self._next_order_id(),
                    code=o.code,
                    name=o.name,
                    side="BUY",
                    quantity=o.shares,
                    limit_price=o.limit_price,
                    session="morning",
                    style=o.style,
                    risk_level=o.risk,
                )
                plan_result["orders"].append(order)
                self.orders.append(order)

            for o in sheet.afternoon_orders:
                order = LiveOrder(
                    order_id=self._next_order_id(),
                    code=o.code,
                    name=o.name,
                    side="BUY",
                    quantity=o.shares,
                    limit_price=o.limit_price,
                    session="afternoon",
                    style=o.style,
                    risk_level=o.risk,
                )
                plan_result["orders"].append(order)
                self.orders.append(order)

            plan_result["day_capital"] = sheet.day_capital
            plan_result["phase_name"] = sheet.phase_name
            plan_result["warnings"] = sheet.warnings
            plan_result["paused_count"] = len(sheet.paused_orders)

            logger.info(f"交易计划生成: {len(plan_result['orders'])} 笔订单, "
                         f"金额={sheet.day_capital:,.0f}")

        except Exception as e:
            logger.error(f"交易计划生成失败: {e}")
            plan_result["error"] = str(e)

        return plan_result

    def _generate_put_recommendations(self, market_state: Dict,
                                       risk_result: Dict) -> Optional[Dict]:
        """
        生成保护性看跌期权建议

        基于当前市场状态和组合敞口，计算需要对冲的 Delta 敞口，
        给出 Put 期权买入建议。仅在以下条件触发：
          - 紧急等级 >= 1（黄色预警及以上）
          - VIX 代理 > 25
          - 压力测试黑天鹅损失 > 45%

        Returns:
            Dict 包含 PutRecommendation 数据，或 None 表示不需要
        """
        if not HAS_PUT_MANAGER:
            return None

        try:
            # 获取组合敞口信息
            account = self.broker.get_account_info()
            total_asset = account.get("total_asset", 5_000_000)
            market_value = account.get("market_value", 0)
            deployed = market_value if market_value > 0 else total_asset * 0.3

            # 构建风格权重（从持仓中推断）
            positions_raw = self.broker.get_positions()
            style_weights = self._infer_style_weights(positions_raw)

            # 初始化管理器
            if self._put_manager is None:
                self._put_manager = ProtectivePutManager(
                    portfolio_value=total_asset,
                    hedge_budget_pct=0.025
                )

            # 决定是否需要对冲
            risk_level = risk_result.get("risk_level", RiskLevel.NORMAL)
            emergency = risk_level.level
            vix = market_state.get("vix_proxy", 22)

            # 获取压力测试结果
            risk_cycle = risk_result.get("risk_cycle", {})
            stress_results = risk_cycle.get("stress_test", {})
            black_swan_loss = abs(stress_results.get(
                "black_swan", stress_results.get("forward_black_swan",
                market_state.get("stress_black_swan_loss", 0.45)
            )))
            if isinstance(black_swan_loss, str):
                try:
                    black_swan_loss = abs(float(black_swan_loss.replace("%", "")) / 100)
                except ValueError:
                    black_swan_loss = 0.50
            
            # 互联网泡沫场景损失（科技股估值崩塌）
            internet_bubble_loss = abs(stress_results.get(
                "internet_bubble_2000",
                market_state.get("stress_internet_bubble_loss", 0.45)
            ))
            if isinstance(internet_bubble_loss, str):
                try:
                    internet_bubble_loss = abs(float(internet_bubble_loss.replace("%", "")) / 100)
                except ValueError:
                    internet_bubble_loss = 0.45

            # 判断是否需要生成对冲建议
            need_hedge = (
                emergency >= 1 or
                vix > 25 or
                black_swan_loss > 0.45 or
                internet_bubble_loss > 0.40 or
                deployed > total_asset * 0.5   # 敞口超过50%自动触发
            )

            if not need_hedge:
                need_check, reason = self._put_manager.should_roll()
                if not need_check:
                    logger.info(f"保护性看跌: 市场平稳，已存在对冲未到期，跳过 "
                                f"(emergency={emergency}, VIX={vix}, deployed={deployed/10000:.0f}万)")
                    return {"status": "skipped", "reason": reason, "recommendation": None}
                # 需要展期 → 继续生成新建议

            # 构建 market_state（适配 ProtectivePutManager 接口）
            put_market_state = {
                "vix_proxy": vix,
                "volatility": market_state.get("volatility", 0.18),
                "emergency_level": emergency,
                "stress_black_swan_loss": black_swan_loss,
                "index_return_20d": market_state.get("index_return_20d", 0),
                "index_levels": {
                    "510050": market_state.get("indices", {}).get("shanghai", 3350) / 1047,
                    "510300": market_state.get("indices", {}).get("csi_300", 3950) / 878,
                    "588000": market_state.get("indices", {}).get("star_50", 1050) / 875,
                },
            }

            # 构建组合敞口
            portfolio_exposure = {
                "total_deployed": deployed,
                "style_weights": style_weights,
                "high_beta_exposure": deployed * style_weights.get("high_end", 0.3),
            }

            # 生成建议
            recommendation = self._put_manager.generate_recommendation(
                put_market_state, portfolio_exposure
            )

            # 格式化报告
            markdown = self._put_manager.format_recommendation_markdown(recommendation)

            # 日志输出
            logger.info(f"保护性看跌: {recommendation.summary}")
            logger.info(f"  订单数: {len(recommendation.orders)}, "
                        f"总权利金: {recommendation.hedge_budget_used/10000:.2f}万")

            # 保存 Markdown 报告到文件
            report_path = self._save_put_report(markdown)

            # 紧急情况触发告警
            if emergency >= 2:
                self.notifier.send_alert(
                    f"保护性看跌建议 - {recommendation.risk_assessment.get('regime', 'N/A').upper()}",
                    f"对冲等级: {recommendation.risk_assessment.get('regime', 'N/A').upper()}\n"
                    f"订单: {len(recommendation.orders)} 笔\n"
                    f"总权利金: {recommendation.hedge_budget_used/10000:.1f}万\n"
                    f"保护名义: {sum(o.protection_notional for o in recommendation.orders)/10000:.0f}万",
                    "warning" if emergency >= 3 else "info"
                )

            return {
                "status": "generated",
                "recommendation": recommendation,
                "markdown_report": markdown,
                "report_path": report_path,
                "order_count": len(recommendation.orders),
                "total_cost": recommendation.hedge_budget_used,
                "regime": recommendation.risk_assessment.get("regime", "unknown"),
            }

        except Exception as e:
            logger.error(f"保护性看跌建议生成失败: {e}")
            import traceback as _tb
            logger.debug(_tb.format_exc())
            return {"status": "error", "error": str(e)}

    def _save_put_report(self, markdown: str) -> str:
        """保存保护性看跌 Markdown 报告到文件"""
        reports_dir = os.path.join(BASE_DIR, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        filename = f"protective_put_{self.trade_date.strftime('%Y%m%d')}.md"
        filepath = os.path.join(reports_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(markdown)
        logger.info(f"保护性看跌报告已保存: {filepath}")
        return filepath

    def _infer_style_weights(self, positions: List[Dict]) -> Dict[str, float]:
        """从持仓推断风格权重分布"""
        weights = {"high_end": 0.3, "tech": 0.2, "etf": 0.25, "defensive": 0.15, "other": 0.10}
        if not positions:
            return weights

        # 风格映射关键词
        style_keywords = {
            "high_end": ["制造", "装备", "军工", "半导体", "芯片", "高端"],
            "tech": ["科技", "软件", "AI", "科创", "创新"],
            "etf": ["ETF", "指数", "300", "500", "50"],
            "defensive": ["消费", "医药", "食品", "饮料", "银行"],
        }

        total_value = sum(
            p.get("market_value",
                  p.get("quantity", 0) * p.get("current_price", 0))
            for p in positions
        )

        if total_value == 0:
            return weights

        style_values = {k: 0.0 for k in weights}
        for p in positions:
            name = p.get("name", "")
            value = p.get("market_value",
                          p.get("quantity", 0) * p.get("current_price", 0))

            matched = False
            for style, keywords in style_keywords.items():
                if any(kw in name for kw in keywords):
                    style_values[style] += value
                    matched = True
                    break
            if not matched:
                style_values["other"] += value

        # 归一化
        for k in style_values:
            weights[k] = style_values[k] / total_value if total_value > 0 else 0

        return weights

    # ---------------------------------------------------------------
    # Phase 1: 集合竞价 (09:25)
    # ---------------------------------------------------------------

    def phase_auction(self) -> Tuple[bool, Dict]:
        """
        集合竞价阶段 (09:25 执行)

        1. 获取集合竞价数据
        2. 检查价格偏离（偏离>10%暂停执行）
        3. 调整限价
        4. 最终风控确认
        """
        result = {"adjusted_orders": 0, "paused_codes": []}

        if not self.orders:
            logger.info("无待执行订单，跳过集合竞价调整")
            return True, result

        codes = list(set(o.code for o in self.orders))
        auction_data = self.market_data.get_auction_data(codes)

        for order in self.orders:
            if order.code in auction_data:
                auction = auction_data[order.code]
                auction_price = auction.get("auction_price", 0)

                if auction_price > 0 and order.limit_price > 0:
                    deviation = (auction_price - order.limit_price / 1.008) / (order.limit_price / 1.008)

                    # 偏离>10% 暂停
                    if abs(deviation) > 0.10:
                        order.status = OrderStatus.CANCELLED
                        order.error_message = f"集合竞价偏离 {deviation:.1%} > 10%"
                        result["paused_codes"].append({
                            "code": order.code,
                            "name": order.name,
                            "deviation": f"{deviation:.1%}",
                        })
                        logger.warning(f"暂停 {order.code} {order.name}: 竞价偏离 {deviation:.1%}")
                        continue

                    # 偏离>3% 调整限价
                    if abs(deviation) > 0.03:
                        order.limit_price = round(auction_price * 1.005, 3)  # 竞价价+0.5%
                        result["adjusted_orders"] += 1
                        logger.info(f"调整 {order.code} 限价 -> {order.limit_price:.3f} (竞价: {auction_price:.3f})")

        active_orders = [o for o in self.orders if o.status == OrderStatus.PENDING]
        logger.info(f"集合竞价完成: {len(active_orders)} 笔有效, "
                     f"{result['adjusted_orders']} 笔调价, {len(result['paused_codes'])} 笔暂停")

        # 所有订单都暂停
        if not active_orders and self.orders:
            self.notifier.send_alert("订单全部暂停", "集合竞价偏离过大，所有订单暂停", "warning")

        return True, result

    # ---------------------------------------------------------------
    # Phase 2: 上午批次执行 (09:30-10:30)
    # ---------------------------------------------------------------

    def phase_execution_am(self) -> Tuple[bool, Dict]:
        """上午批次执行 (09:30 开始)"""
        return self._execute_session("morning")

    # ---------------------------------------------------------------
    # Phase 3: 午间检查 (11:30)
    # ---------------------------------------------------------------

    def phase_midday_check(self) -> Tuple[bool, Dict]:
        """
        午间检查 (11:30)

        1. 确认上午成交
        2. 检查未成交订单
        3. 偏差分析
        4. 决定下午是否调整
        """
        result = {"am_filled": 0, "am_partial": 0, "am_pending": 0, "am_rejected": 0}

        morning_orders = [o for o in self.orders if o.session == "morning"]
        for order in morning_orders:
            if order.status == OrderStatus.FILLED:
                result["am_filled"] += 1
            elif order.status == OrderStatus.PARTIAL_FILLED:
                result["am_partial"] += 1
            elif order.status == OrderStatus.PENDING or order.status == OrderStatus.SENT:
                result["am_pending"] += 1
            elif order.status == OrderStatus.REJECTED:
                result["am_rejected"] += 1

        # 偏差检查：上午成交比例
        total_am = len(morning_orders)
        fill_rate = result["am_filled"] / total_am if total_am > 0 else 0

        logger.info(f"上午成交: {result['am_filled']}/{total_am} 全部成交, "
                     f"{result['am_partial']} 部分成交, {result['am_pending']} 未成交, "
                     f"成交率={fill_rate:.0%}")

        if fill_rate < 0.3 and total_am > 5:
            self.notifier.send_alert(
                "上午成交率偏低",
                f"上午仅成交 {result['am_filled']}/{total_am} 笔 ({fill_rate:.0%})，请检查市场流动性",
                "warning"
            )

        # 保存持仓快照
        self._save_positions_snapshot()

        # 保存订单状态
        for order in morning_orders:
            self.store.save_order(order)

        return True, result

    # ---------------------------------------------------------------
    # Phase 4: 下午批次执行 (14:00-14:30)
    # ---------------------------------------------------------------

    def phase_execution_pm(self) -> Tuple[bool, Dict]:
        """下午批次执行 (14:00 开始)"""
        return self._execute_session("afternoon")

    # ---------------------------------------------------------------
    # Phase 5: 盘后清算 (15:00)
    # ---------------------------------------------------------------

    def phase_post_market(self) -> Tuple[bool, Dict]:
        """
        盘后清算阶段 (15:00 执行)

        1. 确认全天成交
        2. 汇总持仓
        3. 资金归集（剩余资金→短融ETF）
        4. 生成执行报告
        5. 发送通知
        6. 持久化所有数据
        """
        result = {}

        # 1. 全天成交统计
        filled = [o for o in self.orders if o.status == OrderStatus.FILLED]
        partial = [o for o in self.orders if o.status == OrderStatus.PARTIAL_FILLED]
        rejected = [o for o in self.orders if o.status == OrderStatus.REJECTED]

        self.daily_report.total_orders = len(self.orders)
        self.daily_report.filled_orders = len(filled)
        self.daily_report.partial_orders = len(partial)
        self.daily_report.rejected_orders = len(rejected)

        total_amount = sum(o.filled_qty * o.avg_price
                          for o in self.orders
                          if o.status in (OrderStatus.FILLED, OrderStatus.PARTIAL_FILLED))
        self.daily_report.total_amount = total_amount

        # 估算佣金（万2.5）
        self.daily_report.total_commission = total_amount * 0.00025

        logger.info(f"全天成交: {len(filled)} 全部成交 / {len(partial)} 部分成交 / {len(rejected)} 拒绝")
        logger.info(f"成交金额: {total_amount:,.0f} 元, 佣金: {self.daily_report.total_commission:,.0f} 元")

        # 2. 保存最终仓位快照
        self._save_positions_snapshot()

        # 3. 持久化所有订单
        for order in self.orders:
            self.store.save_order(order)

        # 4. 资金归集建议
        account = self.broker.get_account_info()
        available_cash = account.get("available_cash", 0)
        if available_cash > 10_000:
            result["cash_management"] = {
                "available_cash": available_cash,
                "suggestion": f"建议将 {available_cash:,.0f} 元闲置资金转入短融ETF(511360)",
            }
            logger.info(f"资金归集: 可用 {available_cash:,.0f} 元，建议转短融ETF")

        # 4.5. v7.1.2: 盘后语义回测分析
        if HAS_V712_MODULES and self.orders:
            try:
                backtest_engine = SemanticBacktestEngine(EvaluationConfig())
                backtest_results = []

                for order in self.orders:
                    outcome = (
                        "positive" if order.status == OrderStatus.FILLED
                        else "negative" if order.status == OrderStatus.REJECTED
                        else "ambiguous"
                    )
                    advice = (
                        f"{'买入' if order.side == 'BUY' else '卖出'}{order.name}({order.code})"
                        f"{order.quantity}股@{order.limit_price:.3f}"
                    )
                    eval_result = backtest_engine.evaluate_single(
                        advice=advice,
                        actual_outcome=outcome,
                    )
                    backtest_results.append(eval_result)

                if backtest_results:
                    summary_stats = backtest_engine.compute_summary(backtest_results)
                    result["semantic_backtest"] = {
                        "total_orders": len(backtest_results),
                        "direction_correct": summary_stats.get("direction_correct", 0),
                        "direction_accuracy": summary_stats.get("direction_accuracy", 0),
                        "stop_loss_events": len([
                            o for o in self.orders
                            if o.status == OrderStatus.REJECTED
                        ]),
                        "take_profit_events": len([
                            o for o in self.orders
                            if o.status == OrderStatus.FILLED and o.pnl_pct > 0
                        ]),
                    }
                    logger.info(
                        f"[SEMANTIC_BT] 盘后语义回测: {len(backtest_results)}笔订单, "
                        f"方向准确率={summary_stats.get('direction_accuracy', 0):.1%}"
                    )

                    # 保存回测报告
                    try:
                        report_dir = os.path.join(BASE_DIR, "data", "live_trading",
                                                  self.trade_date.strftime("%Y%m%d"))
                        os.makedirs(report_dir, exist_ok=True)
                        bt_path = os.path.join(report_dir, "semantic_backtest.json")
                        with open(bt_path, 'w', encoding='utf-8') as f:
                            json.dump({
                                "date": self.trade_date.strftime("%Y-%m-%d"),
                                "summary": result["semantic_backtest"],
                                "orders_evaluated": len(backtest_results),
                            }, f, ensure_ascii=False, indent=2)
                    except Exception:
                        pass

            except Exception as e:
                logger.warning(f"[SEMANTIC_BT] 盘后语义回测异常: {e}")
                result["semantic_backtest_error"] = str(e)

        # 5. 生成并保存执行报告
        self.daily_report.trade_date = self.trade_date.strftime("%Y-%m-%d")
        self.daily_report.completed_at = datetime.now().isoformat()
        self.store.save_execution_report(self.daily_report)

        # 6. 发送通知
        self.notifier.send_daily_report(self.daily_report)
        if rejected:
            rejected_codes = [f"{o.code} {o.name}" for o in rejected]
            self.notifier.send_alert(
                "订单被拒绝",
                f"{len(rejected)} 笔订单被拒绝: {', '.join(rejected_codes)}",
                "warning"
            )

        result["report"] = asdict(self.daily_report)
        return True, result

    # ---------------------------------------------------------------
    # 通用执行会话
    # ---------------------------------------------------------------

    def _execute_session(self, session: str) -> Tuple[bool, Dict]:
        """
        执行指定时间段的订单批次

        Args:
            session: "morning" 或 "afternoon"
        """
        session_orders = [o for o in self.orders
                         if o.session == session and o.status == OrderStatus.PENDING]
        result = {"session": session, "executed": 0, "failed": 0, "skipped": 0}

        if not session_orders:
            logger.info(f"{session} 批次: 无待执行订单")
            return True, result

        logger.info(f"{session} 批次: 开始执行 {len(session_orders)} 笔订单")

        if self.mode == "dry-run":
            logger.info(f"[DRY-RUN] 干跑模式，模拟执行 {len(session_orders)} 笔订单")
            for order in session_orders:
                order.status = OrderStatus.FILLED
                order.filled_qty = order.quantity
                order.avg_price = order.limit_price
                order.filled_at = datetime.now().isoformat()
                order.broker_order_id = f"DRYRUN_{order.order_id}"
                result["executed"] += 1
            return True, result

        # 实盘/模拟模式：逐笔执行
        for order in session_orders:
            try:
                # 通过券商接口下单
                resp = self.broker.place_order(
                    code=order.code,
                    name=order.name,
                    side=order.side,
                    quantity=order.quantity,
                    limit_price=order.limit_price,
                )

                if resp.get("success", False):
                    order.status = OrderStatus.SENT
                    order.sent_at = datetime.now().isoformat()
                    order.broker_order_id = resp.get("broker_order_id")
                    result["executed"] += 1

                    # 模拟成交（实际应通过回调或轮询获取成交状态）
                    order.status = OrderStatus.FILLED
                    order.filled_qty = order.quantity
                    order.avg_price = order.limit_price
                    order.filled_at = datetime.now().isoformat()

                    self.notifier.send_trade_confirmation(order)
                    self.store.save_order(order)
                else:
                    order.status = OrderStatus.REJECTED
                    order.error_message = resp.get("message", "未知错误")
                    result["failed"] += 1
                    logger.warning(f"订单被拒: {order.code} {order.name} - {order.error_message}")

            except Exception as e:
                order.status = OrderStatus.REJECTED
                order.error_message = str(e)
                result["failed"] += 1
                logger.error(f"下单异常 {order.code}: {e}")

        logger.info(f"{session} 批次完成: {result['executed']} 成功, {result['failed']} 失败")
        return True, result

    def _save_positions_snapshot(self):
        """保存当前持仓快照"""
        try:
            positions_raw = self.broker.get_positions()
            if positions_raw:
                positions = []
                for p in positions_raw:
                    pos = LivePosition(
                        code=p.get("code", p.get("symbol", "")),
                        name=p.get("name", ""),
                        quantity=p.get("quantity", 0),
                        avg_cost=p.get("avg_cost", 0),
                        current_price=p.get("current_price", 0),
                        market_value=p.get("market_value", 0),
                        pnl=p.get("pnl", 0),
                        pnl_pct=p.get("pnl_pct", 0),
                    )
                    positions.append(pos)
                self.positions = positions
                self.store.save_positions(positions)
                self.daily_report.positions_snapshot = [asdict(p) for p in positions]
                logger.info(f"持仓快照: {len(positions)} 只标的")
        except Exception as e:
            logger.error(f"保存持仓快照失败: {e}")

    # ---------------------------------------------------------------
    # 主执行循环
    # ---------------------------------------------------------------

    def run(self, once: bool = False) -> int:
        """
        运行完整工作流

        Args:
            once: True=立即执行一次, False=启动常驻调度

        Returns:
            0=成功, 1=失败, 2=跳过(非交易日)
        """
        self.running = True
        self.daily_report.started_at = datetime.now().isoformat()
        self.daily_report.trade_date = self.trade_date.strftime("%Y-%m-%d")

        logger.info("=" * 70)
        logger.info(f"  实盘交易工作流 v1.0 启动 - {self.trade_date}")
        logger.info(f"  模式: {self.mode}")
        logger.info(f"  券商: {type(self.broker).__name__}")
        logger.info(f"  行情: {type(self.market_data).__name__}")
        logger.info(f"  通知: {type(self.notifier).__name__}")
        logger.info(f"  存储: {type(self.store).__name__}")
        logger.info("=" * 70)

        if once:
            return self._execute_daily_workflow()

        # 常驻调度模式
        return self._run_scheduler()

    def _execute_daily_workflow(self) -> int:
        """执行单日完整工作流"""
        if self._shutdown_requested:
            return 1

        # Phase 0: 盘前准备
        result = self._run_phase(WorkflowPhase.PRE_MARKET, self.phase_pre_market)
        if result.status == PhaseStatus.FAILED:
            # 非交易日跳过不算失败
            if result.data and result.data.get("reason") == "non_trading_day":
                logger.info("非交易日，工作流正常退出")
                return 2
            logger.error("盘前准备失败，终止工作流")
            return 1

        # 极端风控：直接终止
        risk_data = result.data.get("risk_assessment", {})
        risk_level = risk_data.get("risk_level", RiskLevel.NORMAL)
        if risk_level.level >= 4:
            logger.critical(f"触发极端风控({risk_level.level_name})，终止当日所有交易")
            self._run_phase(WorkflowPhase.POST_MARKET, self.phase_post_market)
            return 1

        # Phase 1: 集合竞价
        self._run_phase(WorkflowPhase.AUCTION, self.phase_auction)

        # Phase 2: 上午执行（红色及以上暂停）
        if risk_level.level < 3:
            self._run_phase(WorkflowPhase.EXECUTION_AM, self.phase_execution_am)
        else:
            logger.warning(f"风控{risk_level.level_name}，跳过上午执行")
            self.daily_report.alerts.append(f"风控{risk_level.level_name}，上午批次跳过")

        # Phase 3: 午间检查
        self._run_phase(WorkflowPhase.MIDDAY, self.phase_midday_check)

        # Phase 4: 下午执行（橙色及以上暂停）
        if risk_level.level < 2:
            self._run_phase(WorkflowPhase.EXECUTION_PM, self.phase_execution_pm)
        else:
            logger.warning(f"风控{risk_level.level_name}，跳过下午执行")
            self.daily_report.alerts.append(f"风控{risk_level.level_name}，下午批次跳过")

        # Phase 5: 盘后清算
        self._run_phase(WorkflowPhase.POST_MARKET, self.phase_post_market)

        # 断开券商
        self.broker.disconnect()

        # 汇总
        success_count = sum(1 for p in self.daily_report.phases if p.status == PhaseStatus.SUCCESS)
        fail_count = sum(1 for p in self.daily_report.phases if p.status == PhaseStatus.FAILED)
        total = len(self.daily_report.phases)

        logger.info("=" * 70)
        logger.info(f"  工作流完成: {success_count}/{total} 成功, {fail_count} 失败")
        logger.info(f"  订单: {self.daily_report.total_orders} 总 / "
                     f"{self.daily_report.filled_orders} 成交 / {self.daily_report.partial_orders} 部分成交")
        logger.info(f"  风险: {self.daily_report.risk_level}")
        logger.info(f"  报告: data/live_trading/{self.trade_date.strftime('%Y%m%d')}/")
        logger.info("=" * 70)

        return 0 if fail_count == 0 else 1

    def _run_scheduler(self) -> int:
        """常驻调度循环 - 每天按时间表触发各阶段"""
        logger.info("启动常驻调度模式，等待交易日 7:00...")

        try:
            import schedule
            HAS_SCHEDULE = True
        except ImportError:
            logger.info("schedule 库未安装，使用简化的时间轮询模式")
            HAS_SCHEDULE = False
            try:
                import time as _time
            except ImportError:
                _time = None

        if HAS_SCHEDULE:
            # 使用 schedule 库
            schedule.every().day.at("07:00").do(self._trigger_daily_workflow)

            logger.info("调度规则: 每日 07:00 自动执行完整工作流")
            logger.info("按 Ctrl+C 停止")

            while not self._shutdown_requested:
                schedule.run_pending()
                import time as _time
                _time.sleep(60)  # 每分钟检查一次
        else:
            # 简化轮询
            logger.info("调度规则: 每日 07:00 自动触发（轮询模式）")
            logger.info("按 Ctrl+C 停止")

            today_triggered = False
            while not self._shutdown_requested:
                now = datetime.now()
                current_time = now.time()

                # 每天 7:00-7:01 触发
                if (time(7, 0) <= current_time <= time(7, 1)
                        and is_trading_day(now.date())
                        and not today_triggered):
                    self.trade_date = now.date()
                    self._trigger_daily_workflow()
                    today_triggered = True

                # 第二天重置触发标志
                if current_time >= time(0, 0) and current_time <= time(0, 1):
                    today_triggered = False

                import time as _time
                _time.sleep(30)

        return 0

    def _trigger_daily_workflow(self):
        """调度器触发的每日工作流（在新线程中执行，避免阻塞调度器）"""
        self.trade_date = date.today()
        self.daily_report = DailyExecutionReport(
            trade_date=self.trade_date.strftime("%Y-%m-%d"),
            started_at=datetime.now().isoformat(),
        )
        self.orders = []
        self._order_counter = 0

        thread = threading.Thread(target=self._execute_daily_workflow, daemon=True)
        thread.start()
        logger.info(f"工作流线程已启动 (thread={thread.name})")

    # ---------------------------------------------------------------
    # 状态查询接口
    # ---------------------------------------------------------------

    def get_status(self) -> Dict:
        """获取当前运行状态"""
        return {
            "running": self.running,
            "trade_date": self.trade_date.strftime("%Y-%m-%d"),
            "mode": self.mode,
            "current_phase": self._current_phase.value if self._current_phase else None,
            "orders_total": len(self.orders),
            "orders_filled": sum(1 for o in self.orders if o.status == OrderStatus.FILLED),
            "orders_pending": sum(1 for o in self.orders if o.status == OrderStatus.PENDING),
            "positions_count": len(self.positions),
            "last_report": self.daily_report.trade_date if self.daily_report.trade_date else None,
        }

    def get_today_summary(self) -> Optional[Dict]:
        """获取今日执行摘要"""
        if not self.daily_report.trade_date:
            return None
        return asdict(self.daily_report)


# ================================================================
# CLI 入口
# ================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="实盘交易自动化工作流 v1.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
运行模式:
  python live_trading_workflow.py                  # 常驻进程，每天7:00自动运行
  python live_trading_workflow.py --once           # 立即执行一次完整工作流（测试用）
  python live_trading_workflow.py --mode dry-run   # 干跑模式（生成指令但不执行）
  python live_trading_workflow.py --date 2026-07-06  # 指定日期执行
  python live_trading_workflow.py --status          # 查询历史执行状态

接入实盘:
  1. 实现 BrokerInterface（券商API）
  2. 实现 MarketDataProvider（行情数据源）
  3. 实现 NotificationService（通知渠道）
  4. 实现 PositionStore（数据库存储）
  5. 在 __init__ 或 CLI 中注入真实实现
        """
    )
    parser.add_argument("--once", action="store_true",
                        help="立即执行一次（不启动常驻调度）")
    parser.add_argument("--mode", type=str, default="live",
                        choices=["live", "dry-run", "backtest"],
                        help="运行模式 (默认: live)")
    parser.add_argument("--date", type=str, default=None,
                        help="指定交易日期 YYYY-MM-DD (默认: 今日)")
    parser.add_argument("--status", action="store_true",
                        help="查询历史执行状态")
    parser.add_argument("--days", type=int, default=7,
                        help="状态查询时显示最近N天 (默认: 7)")

    args = parser.parse_args()

    # 查询历史状态
    if args.status:
        store = JSONLPositionStore()
        history = store.get_execution_history(days=args.days)
        if not history:
            print("暂无执行历史记录")
        else:
            print(f"\n最近 {len(history)} 条执行记录:\n")
            print(f"{'日期':<12} {'风险等级':<10} {'订单(总/成/部/拒)':<20} {'金额':>12}")
            print("-" * 60)
            for h in history:
                risk = h.get("risk_level", "?")
                t = h.get("total_orders", 0)
                f = h.get("filled_orders", 0)
                p = h.get("partial_orders", 0)
                r = h.get("rejected_orders", 0)
                amt = h.get("total_amount", 0)
                print(f"{h.get('trade_date', '?'):<12} {risk:<10} {t}/{f}/{p}/{r:<20} {amt:>12,.0f}")
        sys.exit(0)

    # 创建并运行工作流
    workflow = LiveTradingWorkflow(mode=args.mode)

    if args.date:
        try:
            workflow.trade_date = datetime.strptime(args.date, "%Y-%m-%d").date()
            logger.info(f"指定交易日期: {workflow.trade_date}")
        except ValueError:
            logger.error(f"日期格式错误: {args.date} (需为 YYYY-MM-DD)")
            sys.exit(1)

    exit_code = workflow.run(once=args.once)
    sys.exit(exit_code)
