"""
券商实盘接入架构设计
===================

核心目标:
1. 模拟盘阶段: 完整运行对冲逻辑,记录所有信号但不实际下单
2. 实盘过渡: 通过券商API桥接层实现无缝切换
3. 数据一致性: 模拟盘和实盘使用相同的数据结构和执行流程

支持的券商API:
- 华泰证券 (HTSecure API)
- 国泰君安 (UTRADER API)
- 中泰证券 (XTP API)
- 东方财富 (EasyTrader API)
- 同花顺 (iFinD API)

扩展性设计:
- BrokerAdapter抽象基类,每个券商实现具体适配器
- 统一订单生命周期管理
- 实时行情和历史数据双通道
- 风控前置检查(实盘模式)
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger("broker_adapter")


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    VWAP = "VWAP"
    TWAP = "TWAP"


class OrderStatus(Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIAL_FILL = "PARTIAL_FILL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


class BrokerOrder:
    """统一订单对象"""

    def __init__(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: int,
        price: float | None = None,
        strategy: str = "P0_HEDGE",
        tags: dict[str, str] | None = None,
    ):
        self.order_id = f"{strategy}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{symbol}_{side.value}"
        self.symbol = symbol
        self.side = side
        self.order_type = order_type
        self.quantity = quantity
        self.price = price
        self.strategy = strategy
        self.tags = tags or {}
        self.status = OrderStatus.PENDING
        self.filled_quantity = 0
        self.avg_fill_price = 0.0
        self.submitted_at = datetime.now()
        self.filled_at = None
        self.rejection_reason = None
        self.execution_details: list[Any] = []

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": self.quantity,
            "price": self.price,
            "strategy": self.strategy,
            "tags": self.tags,
            "status": self.status.value,
            "filled_quantity": self.filled_quantity,
            "avg_fill_price": self.avg_fill_price,
            "submitted_at": self.submitted_at.isoformat(),
            "filled_at": self.filled_at.isoformat() if self.filled_at else None,
            "rejection_reason": self.rejection_reason,
            "execution_details": self.execution_details,
        }


class BrokerAdapter(ABC):
    """券商适配器抽象基类"""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.api_client = None
        self.order_log: list[Any] = []
        self.fill_log: list[Any] = []
        self.error_log: list[Any] = []
        self.is_live = False

    @abstractmethod
    def connect(self) -> bool:
        """建立API连接"""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """断开API连接"""
        pass

    @abstractmethod
    def get_market_data(
        self, symbol: str, period: str = "1d", count: int = 100
    ) -> dict:
        """获取市场数据"""
        pass

    @abstractmethod
    def submit_order(self, order: BrokerOrder) -> bool:
        """提交订单"""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        pass

    @abstractmethod
    def get_positions(self) -> list[dict]:
        """查询持仓"""
        pass

    @abstractmethod
    def get_account_info(self) -> dict:
        """查询账户信息"""
        pass

    def log_order(self, order: BrokerOrder, event: str) -> None:
        """记录订单事件"""
        event_record = {
            "timestamp": datetime.now().isoformat(),
            "order_id": order.order_id,
            "event": event,
            "details": order.to_dict(),
        }
        self.order_log.append(event_record)
        logger.info(
            f"[{event}] {order.order_id}: {order.symbol} {order.side.value} {order.quantity}"
        )


class SimulatedBroker(BrokerAdapter):
    """
    模拟盘券商适配器

    功能:
    1. 接收真实行情数据
    2. 模拟订单撮合(基于真实成交价+滑点模型)
    3. 完整记录订单生命周期
    4. 不实际发送任何订单到交易所
    5. 支持回测和历史信号验证
    """

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.is_live = False
        self.simulation_settings = {
            "slippage_model": "PERCENTAGE",  # PERCENTAGE or LEVELS
            "slippage_pct": 0.001,  # 1bp滑点
            "fill_probability": 0.95,  # 95%成交率
            "delay_ms": 100,  # 100ms延迟模拟
            "commission_rate": 0.00025,  # 佣金万2.5
            "stamp_tax": 0.0005,  # 印花税(卖出)
            "transfer_fee": 0.00001,  # 过户费
        }
        self.pending_orders: list[Any] = []
        self.filled_orders: list[Any] = []
        self.position_book: dict[str, Any] = {}
        self.account_balance = config.get("initial_balance", 50000000)  # 初始5000万

    def connect(self) -> bool:
        logger.info("[SIMULATED] 模拟盘环境已连接")
        logger.info(f"  初始资金: ¥{self.account_balance:,.0f}")
        logger.info(f"  滑点模型: {self.simulation_settings['slippage_model']}")
        logger.info(
            f"  成交率: {self.simulation_settings['fill_probability'] * 100:.0f}%"
        )
        return True

    def disconnect(self) -> None:
        logger.info("[SIMULATED] 模拟盘环境已断开")

    def get_market_data(
        self, symbol: str, period: str = "1d", count: int = 100
    ) -> dict:
        """
        获取真实行情数据(通过Wind或其他数据源)
        """
        try:
            # TODO: 集成Wind MCP或AKShare获取真实行情
            # 示例返回结构
            return {
                "symbol": symbol,
                "period": period,
                "data": [
                    {
                        "datetime": "2026-07-23 09:30:00",
                        "open": 10.50,
                        "high": 10.55,
                        "low": 10.48,
                        "close": 10.52,
                        "volume": 1500000,
                        "amount": 15780000,
                    }
                ]
                * count,
                "source": "WIND",
            }
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.error(f"获取行情失败: {e}")
            return {"symbol": symbol, "data": [], "error": str(e)}

    def submit_order(self, order: BrokerOrder) -> bool:
        """
        模拟订单提交

        流程:
        1. 风控检查(模拟盘仅记录,不拦截)
        2. 加入待撮合队列
        3. 异步撮合(基于真实成交价+滑点)
        4. 更新持仓和资金
        """
        try:
            self.log_order(order, "SUBMITTED")

            # 模拟滑点计算
            market_data = self.get_market_data(order.symbol)
            if not market_data.get("data"):
                logger.warning("无行情数据,使用限价单模拟")
                order.status = OrderStatus.SUBMITTED
                self.pending_orders.append(order)
                return True

            last_price = market_data["data"][-1]["close"]

            if order.order_type == OrderType.LIMIT and order.price:
                fill_price = order.price
            else:
                # 市价单使用收盘价+滑点
                slippage = last_price * self.simulation_settings["slippage_pct"]
                fill_price = (
                    last_price + slippage
                    if order.side == OrderSide.BUY
                    else last_price - slippage
                )

            # 模拟成交概率
            import random

            if random.random() < self.simulation_settings["fill_probability"]:
                # 成交
                order.status = OrderStatus.FILLED
                order.filled_quantity = order.quantity
                order.avg_fill_price = fill_price
                order.filled_at = datetime.now()

                # 计算费用
                commission = (
                    fill_price
                    * order.quantity
                    * self.simulation_settings["commission_rate"]
                )
                stamp_tax = (
                    commission * 0
                    if order.side == OrderSide.BUY
                    else fill_price
                    * order.quantity
                    * self.simulation_settings["stamp_tax"]
                )
                transfer_fee = (
                    fill_price
                    * order.quantity
                    * self.simulation_settings["transfer_fee"]
                )
                total_cost = commission + stamp_tax + transfer_fee

                # 更新资金
                if order.side == OrderSide.BUY:
                    self.account_balance -= fill_price * order.quantity + total_cost
                else:
                    self.account_balance += fill_price * order.quantity - total_cost

                # 更新持仓
                if order.symbol not in self.position_book:
                    self.position_book[order.symbol] = {"long": 0, "short": 0}

                if order.side == OrderSide.BUY:
                    self.position_book[order.symbol]["long"] += order.quantity
                else:
                    self.position_book[order.symbol]["short"] += order.quantity

                self.filled_orders.append(order)
                self.log_order(order, "FILLED")

                logger.info(
                    f"[成交] {order.symbol} {order.side.value} "
                    f"{order.quantity}股 @ ¥{fill_price:.3f} "
                    f"(费用: ¥{total_cost:.2f})"
                )

                return True
            else:
                # 未成交
                order.status = OrderStatus.REJECTED
                order.rejection_reason = "模拟未成交"
                self.log_order(order, "REJECTED")
                return False

        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            order.status = OrderStatus.ERROR
            order.rejection_reason = str(e)
            self.log_order(order, "ERROR")
            logger.error(f"订单提交失败: {e}")
            return False

    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        for order in self.pending_orders:
            if order.order_id == order_id:
                order.status = OrderStatus.CANCELLED
                self.pending_orders.remove(order)
                self.log_order(order, "CANCELLED")
                return True
        return False

    def get_positions(self) -> list[dict]:
        """查询持仓"""
        positions = []
        for symbol, pos in self.position_book.items():
            if pos["long"] > 0 or pos["short"] > 0:
                market_data = self.get_market_data(symbol)
                current_price = (
                    market_data["data"][-1]["close"] if market_data.get("data") else 0
                )

                positions.append(
                    {
                        "symbol": symbol,
                        "long": pos["long"],
                        "short": pos["short"],
                        "current_price": current_price,
                        "market_value_long": pos["long"] * current_price,
                        "market_value_short": pos["short"] * current_price,
                    }
                )
        return positions

    def get_account_info(self) -> dict:
        """查询账户信息"""
        return {
            "account_id": "SIMULATED_001",
            "balance": self.account_balance,
            "positions_count": len(self.position_book),
            "pending_orders_count": len(self.pending_orders),
            "filled_orders_count": len(self.filled_orders),
            "simulation_settings": self.simulation_settings,
        }

    def generate_simulation_report(self) -> dict:
        """生成模拟盘报告"""
        return {
            "report_date": datetime.now().isoformat(),
            "account_info": self.get_account_info(),
            "positions": self.get_positions(),
            "filled_orders": [o.to_dict() for o in self.filled_orders],
            "performance_summary": {
                "total_trades": len(self.filled_orders),
                "win_rate": sum(
                    1
                    for o in self.filled_orders
                    if o.side == OrderSide.SELL and o.avg_fill_price > o.price
                )
                / max(len(self.filled_orders), 1),
                "total_commission": sum(
                    o.avg_fill_price
                    * o.filled_quantity
                    * self.simulation_settings["commission_rate"]
                    for o in self.filled_orders
                ),
            },
        }


class LiveBrokerAdapter(BrokerAdapter):
    """
    实盘券商适配器(待实现)

    支持的券商:
    - 华泰证券 (HTSecure)
    - 中泰证券 (XTP)
    - 国泰君安 (UTRADER)

    安全特性:
    1. 所有订单必须通过风控前置检查
    2. 订单提交需二次确认(数字签名)
    3. 每日限额控制(单日最大交易额)
    4. 异常交易自动熔断(5分钟内跌幅>3%暂停交易)
    5. 操作日志全量审计(不可篡改)
    """

    def __init__(self, broker_name: str, config: dict[str, Any]):
        super().__init__(config)
        self.broker_name = broker_name
        self.is_live = True
        self.risk_checks_enabled = True
        self.daily_trade_limit = config.get(
            "daily_trade_limit", 10000000
        )  # 日交易额1000万
        self.circuit_breaker_threshold = 0.03  # 3%熔断
        self.api_client = None

    def connect(self) -> bool:
        """
        连接券商API

        TODO: 根据具体券商实现连接逻辑
        例如:
        - 华泰: 使用HTSecure SDK
        - 中泰: 使用XTP API
        """
        logger.warning(f"[LIVE] 连接到{self.broker_name}实盘环境")
        # TODO: 实际API连接代码
        return False

    def submit_order(self, order: BrokerOrder) -> bool:
        """
        实盘订单提交

        流程:
        1. 风控前置检查(仓位、额度、熔断)
        2. 订单序列化
        3. 数字签名
        4. 发送到券商API
        5. 等待确认
        6. 记录审计日志
        """
        if self.risk_checks_enabled:
            if not self.pre_trade_check(order):
                logger.error(f"风控检查未通过: {order.order_id}")
                return False

        # TODO: 实际订单提交
        logger.info(f"[LIVE] 提交订单到{self.broker_name}: {order.to_dict()}")
        return False

    def pre_trade_check(self, order: BrokerOrder) -> bool:
        """风控前置检查"""
        # 1. 检查日交易额限制
        # 2. 检查持仓集中度
        # 3. 检查熔断状态
        # 4. 检查资金充足性
        return True  # TODO: 实际检查逻辑

    def disconnect(self) -> None:
        if self.api_client:
            self.api_client.close()
        logger.warning("[LIVE] 断开实盘连接")


class BrokerFactory:
    """券商适配器工厂"""

    _adapters = {
        "simulated": SimulatedBroker,
        "huatai": None,  # TODO: 实现华泰适配器
        "xtp": None,  # TODO: 实现中泰XTP适配器
        "guotai": None,  # TODO: 实现国泰君安适配器
    }

    @classmethod
    def create(cls, broker_type: str, config: dict[str, Any]) -> BrokerAdapter:
        """创建券商适配器"""
        adapter_class = cls._adapters.get(broker_type)
        if not adapter_class:
            raise ValueError(f"不支持的券商类型: {broker_type}")
        return adapter_class(config)


# 全局券商实例
broker_instance: BrokerAdapter | None = None


def initialize_broker(
    mode: str = "simulated", config: dict | None = None
) -> BrokerAdapter:
    """
    初始化券商适配器

    Args:
        mode: "simulated" 或 "live"
        config: 配置字典
    """
    global broker_instance

    if config is None:
        config = {}

    config["mode"] = mode

    broker_instance = BrokerFactory.create(mode, config)
    broker_instance.connect()

    logger.info(f"券商适配器已初始化: {mode}模式")
    return broker_instance


def get_broker() -> BrokerAdapter:
    """获取全局券商适配器实例"""
    if broker_instance is None:
        return initialize_broker("simulated")
    return broker_instance


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # 测试模拟盘
    logger.info("=" * 80)
    logger.info("测试模拟盘环境")
    logger.info("=" * 80)

    sim_broker = SimulatedBroker({"initial_balance": 50000000})
    sim_broker.connect()

    # 提交测试订单
    test_order = BrokerOrder(
        symbol="510050P",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=10,
        price=0.500,
        strategy="TEST",
    )

    result = sim_broker.submit_order(test_order)
    logger.info(f"\n订单结果: {result}")
    logger.info(
        f"订单详情: {json.dumps(test_order.to_dict(), indent=2, ensure_ascii=False)}"
    )

    # 生成模拟报告
    report = sim_broker.generate_simulation_report()
    logger.info("\n模拟盘报告:")
    logger.info(json.dumps(report, indent=2, ensure_ascii=False))
