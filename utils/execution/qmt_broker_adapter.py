"""QMT 券商适配器插件 — 挂在 broker_adapters._ADAPTER_REGISTRY 上.

桥接两套既有体系 (不重复造轮子):
    外壳 = v8.3 ``_BaseLiveAdapter``  (dry-run / 日交易限额 / 熔断 / JSONL 审计)
    内核 = v8.4 ``QmtBrokerAPI``      (xtquant.xttrader orderStock 真实下单)

为什么需要桥接:
    broker_factory.get_broker() 返回的是 v8.4 ``BrokerAPI`` 体系实例;
    而 ``broker_adapters`` 注册表是 v8.3 ``BrokerAdapter`` 体系 (带风控+审计外壳).
    两者订单模型不同 (BrokerOrder vs Order), 本模块完成模型映射, 使 QMT 真实下单
    能力也能复用 v8.3 的风控前置与审计日志.

安全铁律 (永不裸实盘):
    真实下单需**同时**满足三重门控, 否则连接失败并降级:
      1. config.live = True                      (适配器级开关)
      2. TRADING_ENV = production                (环境级开关, 与 broker_factory 口径一致)
      3. xtquant 已安装 + QMT 终端 connect() 成功 (物理条件)
    任一不满足 → _do_connect() 返回 False, 基类 connect() 记录审计并置 _connected=False,
    绝不抛异常阻断主链路.

用法:
    from utils.execution.qmt_broker_adapter import QmtBrokerAdapter
    adapter = QmtBrokerAdapter({"live": False})   # dry-run 演练
    adapter.connect()
    adapter.submit_order(order)                   # BrokerOrder (v8.3 模型)

环境依赖:
    pip install xtquant (未安装时本模块仍可 import, 只是无法连接)
"""

from __future__ import annotations

import logging
import os
from typing import Any

from utils.execution import broker_adapters as _ba
from utils.execution.broker_adapters import (
    BrokerAdapterError,
    _BaseLiveAdapter,
    register_broker_adapter,
)

logger = logging.getLogger(__name__)


def _status(name: str) -> Any:
    """动态取 v8.3 OrderStatus 枚举成员.

    必须走模块属性而非 ``from ... import OrderStatus``:
    broker_adapters 的基类是**延迟加载**的, 模块级 import 会拿到加载前的 ``None``
    占位符 (``OrderStatus: Any = None``), 后续 _load_base_adapter_classes() 成功
    赋值也不会回填到本模块的局部名字.
    """
    enum_obj = getattr(_ba, "OrderStatus", None)
    if enum_obj is None:
        return None
    return getattr(enum_obj, name, None)


def is_qmt_available() -> bool:
    """xtquant 是否已安装 (纯探测, 不建立连接、无副作用)."""
    try:
        from ms_strategy.src.execution.qmt_broker import XTQUANT_AVAILABLE

        return bool(XTQUANT_AVAILABLE)
    except (ImportError, AttributeError, ValueError, OSError, RuntimeError):
        return False


class QmtBrokerAdapter(_BaseLiveAdapter):
    """QMT (迅投 xtquant) 实盘适配器.

    配置 (config):
        live            bool  实盘开关 (默认 False = dry-run, 强烈建议保持)
        account_id      str   资金账号 (缺省读环境变量 QMT_ACCOUNT_ID)
        session_id      int   QMT 终端会话 id (缺省读 QMT_SESSION_ID)
        account_type    str   STOCK / FUTURE / CREDIT (默认 STOCK)
        path            str   QMT 客户端 userdata 路径 (缺省读 QMT_PATH)
        daily_trade_limit / circuit_breaker_threshold / audit_log_dir → 见基类

    下单映射 (BrokerOrder → QmtBrokerAPI.place):
        side       BUY/SELL          → side
        order_type LIMIT/MARKET      → "LIMIT"/"MARKET"; VWAP/TWAP 降级为 LIMIT 并告警
        price      None              → 0.0 (QMT 侧解释为最新价)
    """

    # VWAP/TWAP 是执行算法而非交易所报价类型, QMT place() 不支持 → 降级
    _ALGO_FALLBACK = "LIMIT"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__("qmt", config)
        self.account_id = str(
            config.get("account_id", os.environ.get("QMT_ACCOUNT_ID", ""))
        )
        self.session_id = int(
            config.get("session_id", os.environ.get("QMT_SESSION_ID", 0) or 0)
        )
        self.account_type = str(config.get("account_type", "STOCK"))
        self.qmt_path = str(config.get("path", os.environ.get("QMT_PATH", "")))
        # v8.4 QmtBrokerAPI 实例 (连接成功后非 None)
        self._api: Any = None
        # BrokerOrder.order_id → QMT 委托编号 (撤单时需要)
        self._qmt_order_ids: dict[str, str] = {}

    # ------------------------------------------------------------
    # 钩子: 连接 (三重门控在此生效)
    # ------------------------------------------------------------
    def _do_connect(self) -> bool:
        """连接 QMT 终端. 三重门控任一不满足 → 返回 False (不抛异常)."""
        if os.environ.get("TRADING_ENV", "sim").lower() != "production":
            logger.error(
                "[qmt] 拒绝连接: TRADING_ENV≠production (当前=%s); "
                "实盘下单须显式设置 TRADING_ENV=production",
                os.environ.get("TRADING_ENV", "sim"),
            )
            return False

        try:
            from ms_strategy.src.execution.qmt_broker import (
                XTQUANT_AVAILABLE,
                QmtBrokerAPI,
            )
        except (ImportError, ValueError, OSError, RuntimeError) as exc:
            logger.error("[qmt] QmtBrokerAPI 导入失败 (xtquant 未安装?): %s", exc)
            return False

        if not XTQUANT_AVAILABLE:
            logger.error(
                "[qmt] xtquant 未安装, 无法连接 QMT (pip install xtquant); "
                "请继续使用 SimulatedBroker 或 RemoteQmtBroker"
            )
            return False
        if not self.account_id:
            logger.error("[qmt] 缺 account_id (config.account_id 或 QMT_ACCOUNT_ID)")
            return False

        try:
            api = QmtBrokerAPI(
                account_id=self.account_id,
                session_id=self.session_id,
                account_type=self.account_type,
                path=self.qmt_path,
            )
            if not api.connect():
                logger.error(
                    "[qmt] QMT connect() 失败: %s",
                    getattr(api, "_connection_error", ""),
                )
                return False
            self._api = api
            logger.info(
                "[qmt] QMT 已连接 (account=%s, type=%s)",
                self.account_id,
                self.account_type,
            )
            return True
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
        ) as exc:  # noqa: BLE001  # 券商 API 边界, fail-safe
            # 券商 API 边界: 数据/类型/字段/属性/运行时/IO 异常
            logger.exception("[qmt] QMT 连接异常: %s", exc)
            self._api = None
            return False

    def _do_disconnect(self) -> None:
        if self._api is not None:
            try:
                self._api.disconnect()
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
            ) as exc:  # noqa: BLE001  # 券商 API 边界, fail-safe
                logger.warning("[qmt] QMT 断开异常: %s", exc)
            finally:
                self._api = None
                self._qmt_order_ids.clear()
        logger.info("[qmt] QMT 已断开")

    # ------------------------------------------------------------
    # 钩子: 下单 / 撤单
    # ------------------------------------------------------------
    def _do_submit_order(self, order: Any) -> bool:
        """提交订单到 QMT (BrokerOrder → QmtBrokerAPI.place)."""
        if self._api is None:
            order.status = _status("REJECTED")
            order.rejection_reason = "QMT 通道未就绪 (未连接或 xtquant 缺失)"
            return False

        side = str(getattr(order.side, "value", order.side) or "").upper()
        order_type = str(
            getattr(order.order_type, "value", order.order_type) or ""
        ).upper()
        if order_type in ("VWAP", "TWAP"):
            # 执行算法需由上层算法路由拆单, 交易所侧只接受报价类型
            logger.warning(
                "[qmt] 订单 %s 使用 %s, QMT 侧降级为 %s (算法拆单请在上层完成)",
                order.order_id,
                order_type,
                self._ALGO_FALLBACK,
            )
            order_type = self._ALGO_FALLBACK

        try:
            placed = self._api.place(
                symbol=order.symbol,
                qty=int(order.quantity),
                side=side,
                order_type=order_type,
                price=float(order.price or 0.0),
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
        ) as exc:  # noqa: BLE001  # 券商 API 边界, fail-safe
            order.status = _status("ERROR")
            order.rejection_reason = f"QMT 下单异常: {exc}"
            logger.exception("[qmt] 下单异常: %s", exc)
            return False

        if placed is None:
            order.status = _status("REJECTED")
            order.rejection_reason = "QMT place() 返回 None (QMT 侧拒单或未连接)"
            return False

        # 记录映射: v8.3 order_id → QMT 委托编号 (撤单必需)
        qmt_id = str(getattr(placed, "order_id", ""))
        if qmt_id:
            self._qmt_order_ids[str(order.order_id)] = qmt_id
            order.tags["qmt_order_id"] = qmt_id
        order.status = _status("SUBMITTED")
        return True

    def _do_cancel_order(self, order_id: str) -> bool:
        """撤单 (order_id 为 v8.3 BrokerOrder.order_id, 内部映射为 QMT 委托编号)."""
        if self._api is None:
            return False
        qmt_id = self._qmt_order_ids.get(str(order_id), str(order_id))
        try:
            from ms_strategy.src.execution.broker_api import Order

            # QmtBrokerAPI.cancel 仅使用 order.order_id, 其余字段占位即可
            stub = Order(
                order_id=qmt_id,
                symbol="",
                qty=0,
                side="",
                order_type="",
                price=0.0,
            )
            return bool(self._api.cancel(stub))
        except (
            ImportError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
        ) as exc:  # noqa: BLE001  # 券商 API 边界, fail-safe
            logger.exception("[qmt] 撤单异常: %s", exc)
            return False

    # ------------------------------------------------------------
    # 钩子: 查询
    # ------------------------------------------------------------
    def _do_get_positions(self) -> list[dict]:
        """查询持仓: QMT {code: volume} → v8.3 [{symbol, quantity}]."""
        if self._api is None:
            return []
        try:
            raw = self._api.get_positions() or {}
            return [
                {"symbol": code, "quantity": int(vol)}
                for code, vol in raw.items()
                if int(vol) > 0
            ]
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
        ) as exc:  # noqa: BLE001  # 券商 API 边界, fail-safe
            logger.exception("[qmt] 查询持仓异常: %s", exc)
            return []

    def _do_get_account_info(self) -> dict:
        """查询资金账户 (QMT 返回 available/total/frozen/margin/market_value)."""
        if self._api is None:
            return {"broker": "qmt", "ready": False}
        try:
            info = dict(self._api.get_account_info() or {})
            info["account_id"] = self.account_id
            info["account_type"] = self.account_type
            return info
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
        ) as exc:  # noqa: BLE001  # 券商 API 边界, fail-safe
            logger.exception("[qmt] 查询账户异常: %s", exc)
            return {"broker": "qmt", "error": str(exc)}

    def _do_get_market_data(
        self, symbol: str, period: str = "1d", count: int = 100
    ) -> dict:
        """行情未实现: QmtBrokerAPI 无 K 线接口, 行情走统一数据层 (utils.data_provider).

        明确返回 error 而非伪造数据 — 避免上层把空数据当成功.
        """
        return {
            "symbol": symbol,
            "data": [],
            "error": (
                "QMT adapter 不提供行情; 请使用 utils.data_provider.MarketDataProvider "
                "(Wind MCP→通达信→AKShare→新浪 优先链)"
            ),
            "period": period,
            "count": count,
        }

    # ------------------------------------------------------------
    # 基类钩子: 市价单估价 (用于日交易限额估算, 见基类 _pre_trade_check)
    # ------------------------------------------------------------
    def _get_reference_price(self, symbol: str) -> float | None:
        """用盘口中间价估算市价单金额, 取不到则 None (基类按限额上限保守计入)."""
        if self._api is None:
            return None
        try:
            book = self._api.get_order_book(symbol)
            if not book:
                return None
            bid = float(getattr(book, "bid1", 0.0) or 0.0)
            ask = float(getattr(book, "ask1", 0.0) or 0.0)
            if bid > 0 and ask > 0:
                return (bid + ask) / 2.0
            return bid or ask or None
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError):
            return None


# 插件自注册: import 本模块即挂入 _ADAPTER_REGISTRY (单向依赖, 无循环导入)
register_broker_adapter("qmt", QmtBrokerAdapter)


__all__ = ["QmtBrokerAdapter", "is_qmt_available", "BrokerAdapterError"]
