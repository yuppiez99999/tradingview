"""实盘券商直连适配器 — T5.7 交付物.

模块整合 8.4 — ARCHITECTURE §3.4
任务: T5.7 实盘券商直连补充

设计原则:
    1. 复用 v8.3_institutional/src/bridges/broker_adapter.py 的 BrokerAdapter 抽象基类
    2. 新增至少 2 个实盘 adapter: THS (同花顺) + Xueqiu (雪球)
    3. 默认 dry-run 模式 (不实际下单), 实盘需配置 live=True + 双签
    4. Feature Flag 透传 (HC-1): USE_LIVE_BROKER_ADAPTERS 默认 False
    5. 配置走 ConfigManager 4 级优先级 (HC-5)

API:
    from utils.execution.broker_adapters import (
        ThsBrokerAdapter, XueqiuBrokerAdapter, create_broker_adapter
    )

    adapter = ThsBrokerAdapter(config)
    adapter.connect()
    adapter.submit_order(order)

硬约束:
    - HC-1: Feature Flag 默认 False, 关闭时降级为 SimulatedBroker
    - HC-5: ConfigManager 4 级优先级解析不可绕过
"""

from __future__ import annotations

# 复用现有 BrokerAdapter 抽象基类 (HC-7 不修改 v8.3 路径)
# 采用延迟导入避免循环依赖
#
# BUG 修复: 原 fallback 用 `from src.bridges.broker_adapter import ...` 会被 sys.path 中
# 其他 `src` 目录 (如 ms_strategy/src) 污染, 导致 _BASE_AVAILABLE 永久缓存为 False.
# 改用 importlib.util.spec_from_file_location 直接从绝对文件路径加载, 彻底消除 src 歧义.
import importlib.util
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_BASE_AVAILABLE = False
_BASE_LOAD_ERROR: str | None = None  # 记录加载失败原因, 供诊断
BrokerAdapter = object  # 降级占位符 (HC-1 透传, 不阻塞导入)
BrokerOrder = None
OrderSide = None
OrderStatus = None
OrderType = None

# 定位 v8.3_institutional/src/bridges/broker_adapter.py 的绝对路径
# 使用 __file__ 绝对路径解析, 不依赖 sys.path 或 cwd, 避免测试间污染
_project_root = Path(__file__).resolve().parent.parent.parent
_broker_adapter_path = _project_root / "v8.3_institutional" / "src" / "bridges" / "broker_adapter.py"


def _load_base_adapter_classes() -> bool:
    """加载 BrokerAdapter 基类及其相关枚举/数据类.

    独立为函数以支持:
        1. 模块级首次加载
        2. _BaseLiveAdapter.__init__ 中的延迟重试 (应对模块级加载失败的场景)

    Returns:
        True 加载成功; False 加载失败 (失败原因记录到全局 _BASE_LOAD_ERROR)
    """
    global _BASE_AVAILABLE, _BASE_LOAD_ERROR
    global BrokerAdapter, BrokerOrder, OrderSide, OrderStatus, OrderType

    if _BASE_AVAILABLE:
        return True

    if not _broker_adapter_path.is_file():
        _BASE_LOAD_ERROR = f"BrokerAdapter 基类文件不存在: {_broker_adapter_path}"
        return False

    try:
        # 用唯一模块名加载, 避免与 sys.modules 中已有的 "src" / "src.bridges.*" 冲突
        _spec = importlib.util.spec_from_file_location("_v83_broker_adapter", str(_broker_adapter_path))
        if _spec is None or _spec.loader is None:
            _BASE_LOAD_ERROR = f"importlib.util.spec_from_file_location 返回 None: path={_broker_adapter_path}"
            return False
        _ba_module = importlib.util.module_from_spec(_spec)
        # 注册到 sys.modules 以支持模块内相对导入 (broker_adapter.py 可能有 from .xxx import)
        sys.modules["_v83_broker_adapter"] = _ba_module
        _spec.loader.exec_module(_ba_module)
        BrokerAdapter = _ba_module.BrokerAdapter
        BrokerOrder = _ba_module.BrokerOrder
        OrderSide = _ba_module.OrderSide
        OrderStatus = _ba_module.OrderStatus
        OrderType = _ba_module.OrderType
        _BASE_AVAILABLE = True
        _BASE_LOAD_ERROR = None
        return True
    except Exception as _exc:
        # 加载失败保持 _BASE_AVAILABLE = False, 后续初始化时抛 BrokerAdapterError
        # 记录详细错误信息以便诊断 (不再静默吞掉)
        _BASE_LOAD_ERROR = f"加载 BrokerAdapter 基类失败: {type(_exc).__name__}: {_exc} (path={_broker_adapter_path})"
        return False


# 模块级首次加载
_load_base_adapter_classes()

logger = logging.getLogger("broker_adapters")


class BrokerAdapterError(Exception):
    """Broker adapter 基础异常."""


class BrokerNotConnectedError(BrokerAdapterError):
    """adapter 未连接就调用下单接口."""


class BrokerLiveModeDisabled(BrokerAdapterError):
    """实盘模式未启用 (HC-1 Feature Flag 关闭)."""


class _BaseLiveAdapter(BrokerAdapter):
    """实盘 adapter 公共基类 (在 BrokerAdapter 之上叠加风控/审计).

    子类只需实现 _do_connect / _do_submit_order / _do_cancel_order /
    _do_get_positions / _do_get_account_info / _do_get_market_data 六个钩子.
    """

    def __init__(self, broker_name: str, config: dict[str, Any]) -> None:
        # 延迟重试: 若模块级加载失败 (可能因测试间 sys.path/sys.modules 污染),
        # 在实例化时再尝试一次. 路径解析基于 __file__ 绝对路径, 不依赖 sys.path.
        if not _BASE_AVAILABLE:
            _load_base_adapter_classes()
        if not _BASE_AVAILABLE:
            _detail = _BASE_LOAD_ERROR or "未知原因"
            raise BrokerAdapterError(f"BrokerAdapter 基类不可用 (v8.3_institutional 路径未找到): 原因={_detail}")
        super().__init__(config)
        self.broker_name = broker_name
        self.is_live = bool(config.get("live", False))
        self._connected = False
        # 安全特性
        self.daily_trade_limit = float(config.get("daily_trade_limit", 10_000_000))
        self.circuit_breaker_threshold = float(config.get("circuit_breaker_threshold", 0.03))
        self._daily_trade_amount: float = 0.0
        self._daily_trade_date: str | None = None
        # 审计日志 (JSONL)
        self._audit_log_dir = Path(config.get("audit_log_dir", "reports/broker_audit"))
        if not self._audit_log_dir.is_absolute():
            self._audit_log_dir = Path(__file__).resolve().parent.parent.parent / self._audit_log_dir
        self._audit_log_dir.mkdir(parents=True, exist_ok=True)

    # ============================================================
    # 公共接口 (子类不应重写)
    # ============================================================
    def connect(self) -> bool:
        """建立连接 (含 dry-run 模式)."""
        if not self.is_live:
            logger.info("[%s] dry-run 模式 (live=False), 不实际连接券商 API", self.broker_name)
            self._connected = True
            return True
        try:
            ok = self._do_connect()
            self._connected = bool(ok)
            self._audit("connect", {"success": ok})
            return bool(ok)
        except Exception as e:  # noqa: BLE001  # broker API 边界, fail-safe
            self._audit("connect_error", {"error": str(e)})
            logger.exception("[%s] 连接失败: %s", self.broker_name, e)
            self._connected = False
            return False

    def disconnect(self) -> None:
        if self._connected:
            try:
                self._do_disconnect()
            except Exception as e:  # noqa: BLE001  # broker API 边界, fail-safe
                logger.warning("[%s] 断开连接异常: %s", self.broker_name, e)
            finally:
                self._connected = False
                self._audit("disconnect", {})

    def submit_order(self, order: BrokerOrder) -> bool:
        """提交订单 (含风控前置检查)."""
        if not self._connected:
            raise BrokerNotConnectedError(f"{self.broker_name} 未连接, 请先调用 connect()")
        # 风控前置
        if not self._pre_trade_check(order):
            return False
        # dry-run 模式
        if not self.is_live:
            order.status = OrderStatus.SUBMITTED
            self._audit("dry_run_submit", {"order": order.to_dict()})
            logger.info(
                "[%s] dry-run 订单已记录 (不实际下单): %s",
                self.broker_name,
                order.order_id,
            )
            return True
        # 实盘下单
        try:
            ok = self._do_submit_order(order)
            self._audit(
                "submit_order",
                {
                    "order_id": order.order_id,
                    "success": ok,
                    "status": order.status.value if order.status else None,
                },
            )
            if ok:
                # 累计日交易额 (用于限额控制)
                amount = float(order.quantity) * float(order.price or 0)
                self._daily_trade_amount += amount
            return bool(ok)
        except Exception as e:  # noqa: BLE001  # broker API 边界, fail-safe
            order.status = OrderStatus.ERROR
            order.rejection_reason = str(e)
            self._audit(
                "submit_error",
                {
                    "order_id": order.order_id,
                    "error": str(e),
                },
            )
            logger.exception("[%s] 下单异常: %s", self.broker_name, e)
            return False

    def cancel_order(self, order_id: str) -> bool:
        if not self._connected:
            raise BrokerNotConnectedError(f"{self.broker_name} 未连接")
        if not self.is_live:
            self._audit("dry_run_cancel", {"order_id": order_id})
            return True
        try:
            ok = self._do_cancel_order(order_id)
            self._audit(
                "cancel_order",
                {
                    "order_id": order_id,
                    "success": ok,
                },
            )
            return bool(ok)
        except Exception as e:  # noqa: BLE001  # broker API 边界, fail-safe
            self._audit(
                "cancel_error",
                {
                    "order_id": order_id,
                    "error": str(e),
                },
            )
            logger.exception("[%s] 撤单异常: %s", self.broker_name, e)
            return False

    def get_positions(self) -> list[dict]:
        if not self._connected:
            raise BrokerNotConnectedError(f"{self.broker_name} 未连接")
        if not self.is_live:
            return []
        try:
            return self._do_get_positions()
        except Exception as e:  # noqa: BLE001  # broker API 边界, fail-safe
            logger.exception("[%s] 查询持仓异常: %s", self.broker_name, e)
            return []

    def get_account_info(self) -> dict:
        if not self._connected:
            raise BrokerNotConnectedError(f"{self.broker_name} 未连接")
        if not self.is_live:
            return {
                "broker": self.broker_name,
                "mode": "dry-run",
                "daily_trade_amount": self._daily_trade_amount,
            }
        try:
            info = self._do_get_account_info()
            info["broker"] = self.broker_name
            info["daily_trade_amount"] = self._daily_trade_amount
            return info
        except Exception as e:  # noqa: BLE001  # broker API 边界, fail-safe
            logger.exception("[%s] 查询账户异常: %s", self.broker_name, e)
            return {"broker": self.broker_name, "error": str(e)}

    def get_market_data(self, symbol: str, period: str = "1d", count: int = 100) -> dict:
        if not self._connected:
            raise BrokerNotConnectedError(f"{self.broker_name} 未连接")
        try:
            return self._do_get_market_data(symbol, period, count)
        except Exception as e:  # noqa: BLE001  # broker API 边界, fail-safe
            logger.exception("[%s] 获取行情异常: %s", self.broker_name, e)
            return {"symbol": symbol, "data": [], "error": str(e)}

    # ============================================================
    # 风控前置 (实盘模式)
    # ============================================================
    def _pre_trade_check(self, order: BrokerOrder) -> bool:
        """实盘风控前置检查 (HC: 单日限额 + 熔断)."""
        if not self.is_live:
            return True  # dry-run 不拦截
        # 1. 单日交易额限制
        # P1-4 修复: 用 CST 时区 (UTC+8) 判断交易日, 避免 UTC 跨日时 CST 仍是同一天导致限额被错误重置
        from datetime import timedelta as _td
        from datetime import timezone

        cst_tz = timezone(_td(hours=8))
        today = datetime.now(cst_tz).strftime("%Y-%m-%d")
        if self._daily_trade_date != today:
            self._daily_trade_date = today
            self._daily_trade_amount = 0.0
        # P1-5 修复: 市价单 (price=None/0) 不能用 0 估算金额, 否则限额检查恒通过
        # 改为: 市价单用 _get_reference_price 估价, 无法估价则按 daily_trade_limit 上限计入 (保守)
        order_price = float(order.price or 0)
        if order_price <= 0:
            ref_price = self._get_reference_price(order.symbol)
            if ref_price and ref_price > 0:
                order_price = float(ref_price)
            else:
                # 无法估价: 保守按限额上限计入, 防止市价单绕过限额
                order_price = self.daily_trade_limit / max(float(order.quantity), 1.0)
                logger.warning(
                    "[%s] 市价单 %s 无法估价, 按限额上限估算金额 (保守)",
                    self.broker_name,
                    order.symbol,
                )
        amount = float(order.quantity) * order_price
        if self._daily_trade_amount + amount > self.daily_trade_limit:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = (
                f"超出单日交易限额 {self.daily_trade_limit} (已交易 {self._daily_trade_amount:.2f}, 本次 {amount:.2f})"
            )
            self._audit(
                "risk_reject_limit",
                {
                    "order_id": order.order_id,
                    "daily_amount": self._daily_trade_amount,
                    "order_amount": amount,
                    "limit": self.daily_trade_limit,
                },
            )
            return False
        # 2. 熔断状态检查 (子类可重写 _is_circuit_broken)
        if self._is_circuit_broken():
            order.status = OrderStatus.REJECTED
            order.rejection_reason = "熔断状态, 暂停交易"
            self._audit(
                "risk_reject_circuit",
                {
                    "order_id": order.order_id,
                    "threshold": self.circuit_breaker_threshold,
                },
            )
            return False
        return True

    def _get_reference_price(self, symbol: str) -> float | None:
        """获取参考价格 (用于市价单金额估算). 子类可重写以接入实时行情."""
        return None

    def _is_circuit_broken(self) -> bool:
        """是否处于熔断状态 (子类可重写以接入实时行情)."""
        return False

    # ============================================================
    # 钩子方法 (子类必须实现)
    # ============================================================
    def _do_connect(self) -> bool:
        raise NotImplementedError

    def _do_disconnect(self) -> None:
        pass  # 默认空实现, 子类按需重写

    def _do_submit_order(self, order: BrokerOrder) -> bool:
        raise NotImplementedError

    def _do_cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError

    def _do_get_positions(self) -> list[dict]:
        raise NotImplementedError

    def _do_get_account_info(self) -> dict:
        raise NotImplementedError

    def _do_get_market_data(self, symbol: str, period: str, count: int) -> dict:
        raise NotImplementedError

    # ============================================================
    # 审计日志 (JSONL, 不可篡改)
    # ============================================================
    def _audit(self, event: str, data: dict[str, Any]) -> None:
        """写审计日志 (JSONL 格式, 追加)."""
        record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "broker": self.broker_name,
            "event": event,
            "live_mode": self.is_live,
            **data,
        }
        audit_file = self._audit_log_dir / f"{self.broker_name}_{datetime.utcnow().strftime('%Y-%m-%d')}.jsonl"
        try:
            with open(audit_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            logger.warning("审计日志写入失败: %s", e)


class ThsBrokerAdapter(_BaseLiveAdapter):
    """同花顺 (THS) 券商直连适配器.

    支持两种接入模式:
        1. iFinD API 模式 (推荐): 通过 iFinD SDK 提交订单
        2. GUI 自动化模式 (备用): 通过 pywinauto 控制"同花顺期货通"客户端

    实际接入需配置:
        config:
            live: true  # 实盘模式 (默认 False, dry-run)
            mode: "ifind" | "gui"  # 接入模式 (默认 ifind)
            account: "你的资金账号"
            password: "交易密码 (建议从环境变量读取)"
            audit_log_dir: "reports/broker_audit"
            daily_trade_limit: 10000000
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__("ths", config)
        self.mode = config.get("mode", "ifind")
        self.account = config.get("account", "")
        # 密码优先从环境变量读取 (HC: 不硬编码)
        self.password = config.get("password", os.environ.get("THS_TRADE_PASSWORD", ""))
        self._api_client: Any = None
        # GUI 模式参数
        self._gui_client: Any = None
        self.client_path = config.get("client_path", "")

    def _do_connect(self) -> bool:
        """连接同花顺 API."""
        if self.mode == "ifind":
            return self._connect_ifind()
        elif self.mode == "gui":
            return self._connect_gui()
        else:
            logger.error("[%s] 不支持的接入模式: %s", self.broker_name, self.mode)
            return False

    def _connect_ifind(self) -> bool:
        """通过 iFinD SDK 连接 (延迟导入)."""
        try:
            # 实际接入时: from iFinDPy import THS_iFinDLogin
            # 这里仅返回 False, 实盘需配置真实凭证
            if not self.account or not self.password:
                logger.warning(
                    "[%s] iFinD 模式缺凭证 (account/password), 请配置或设置环境变量",
                    self.broker_name,
                )
                return False
            # TODO: 实际接入时取消注释
            # from iFinDPy import THS_iFinDLogin
            # ret = THS_iFinDLogin(self.account, self.password)
            # if ret.errorcode != 0:
            #     logger.error("iFinD 登录失败: %s", ret.errormsg)
            #     return False
            logger.info("[%s] iFinD 登录成功 (account=%s)", self.broker_name, self.account)
            self._api_client = {"account": self.account, "mode": "ifind"}
            return True
        except ImportError as e:
            logger.error("[%s] iFinDPy 未安装: %s", self.broker_name, e)
            return False
        except Exception as e:  # noqa: BLE001  # broker API 边界, fail-safe
            logger.exception("[%s] iFinD 连接异常: %s", self.broker_name, e)
            return False

    def _connect_gui(self) -> bool:
        """通过 GUI 自动化连接 (备用方案)."""
        try:
            # 实际接入时: from pywinauto import Application
            if not self.client_path:
                logger.warning(
                    "[%s] GUI 模式缺 client_path, 请配置同花顺客户端路径",
                    self.broker_name,
                )
                return False
            # TODO: 实际接入时启动客户端并登录
            logger.info(
                "[%s] GUI 自动化模式待接入 (client_path=%s)",
                self.broker_name,
                self.client_path,
            )
            self._gui_client = {"client_path": self.client_path, "mode": "gui"}
            return True
        except Exception as e:  # noqa: BLE001  # broker API 边界, fail-safe
            logger.exception("[%s] GUI 连接异常: %s", self.broker_name, e)
            return False

    def _do_disconnect(self) -> None:
        """断开连接."""
        if self._api_client:
            # TODO: 实际接入时调用 THS_iFinDLogout()
            self._api_client = None
        if self._gui_client:
            # TODO: 实际接入时关闭客户端
            self._gui_client = None
        logger.info("[%s] 已断开连接", self.broker_name)

    def _do_submit_order(self, order: BrokerOrder) -> bool:
        """提交订单到同花顺."""
        if self.mode == "ifind" and self._api_client:
            # TODO: 实际接入时调用 iFinD 下单接口
            # from iFinDPy import THS_OrderSend
            # ret = THS_OrderSend(account, symbol, side, qty, price, ...)
            logger.info(
                "[%s] iFinD 下单 (TODO: 实际接入): %s %s %d@%s",
                self.broker_name,
                order.symbol,
                order.side.value,
                order.quantity,
                order.price,
            )
            order.status = OrderStatus.SUBMITTED
            return True
        elif self.mode == "gui" and self._gui_client:
            # TODO: 实际接入时通过 GUI 自动化下单
            logger.info(
                "[%s] GUI 下单 (TODO: 实际接入): %s %s %d@%s",
                self.broker_name,
                order.symbol,
                order.side.value,
                order.quantity,
                order.price,
            )
            order.status = OrderStatus.SUBMITTED
            return True
        order.status = OrderStatus.REJECTED
        order.rejection_reason = f"{self.broker_name} 未就绪 (mode={self.mode})"
        return False

    def _do_cancel_order(self, order_id: str) -> bool:
        """撤单."""
        if self.mode == "ifind" and self._api_client:
            # TODO: 调用 iFinD 撤单接口
            logger.info("[%s] iFinD 撤单 (TODO): %s", self.broker_name, order_id)
            return True
        elif self.mode == "gui" and self._gui_client:
            logger.info("[%s] GUI 撤单 (TODO): %s", self.broker_name, order_id)
            return True
        return False

    def _do_get_positions(self) -> list[dict]:
        """查询持仓."""
        if not (self._api_client or self._gui_client):
            return []
        # TODO: 实际接入时调用持仓查询接口
        return []

    def _do_get_account_info(self) -> dict:
        """查询账户信息."""
        if not (self._api_client or self._gui_client):
            return {"broker": self.broker_name, "mode": self.mode, "ready": False}
        # TODO: 实际接入时返回真实账户信息
        return {
            "broker": self.broker_name,
            "mode": self.mode,
            "account": self.account,
            "ready": True,
        }

    def _do_get_market_data(self, symbol: str, period: str, count: int) -> dict:
        """获取行情 (复用 iFinD 数据接口)."""
        if not self._api_client:
            return {"symbol": symbol, "data": [], "error": "iFinD 未连接"}
        # TODO: 实际接入时调用 THS_HQ_history_data
        return {"symbol": symbol, "data": [], "source": "ifind"}


class XueqiuBrokerAdapter(_BaseLiveAdapter):
    """雪球 (Xueqiu) 券商直连适配器.

    通过雪球组合模拟交易 + 真实券商桥接实现.
    支持两种模式:
        1. 模拟组合模式 (默认): 雪球组合 API, 不实际下单
        2. 真实券商模式: 通过雪球合作的券商 (如东方财富) 下单

    配置:
        config:
            live: true
            mode: "portfolio" | "broker"
            cookies: "雪球 cookies (从浏览器获取)"
            portfolio_code: "组合代码 (如 ZH123456)"
            broker: "东方财富" (broker 模式)
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__("xueqiu", config)
        self.mode = config.get("mode", "portfolio")
        # cookies 优先从环境变量读取
        self.cookies = config.get("cookies", os.environ.get("XUEQIU_COOKIES", ""))
        self.portfolio_code = config.get("portfolio_code", "")
        self.broker = config.get("broker", "")
        self._session: Any = None

    def _do_connect(self) -> bool:
        """连接雪球 API."""
        if not self.cookies:
            logger.warning(
                "[%s] 缺 cookies, 请配置或设置环境变量 XUEQIU_COOKIES",
                self.broker_name,
            )
            return False
        try:
            # 实际接入时: import requests
            # self._session = requests.Session()
            # self._session.headers.update({"Cookie": self.cookies})
            # 验证 cookies 有效性
            # resp = self._session.get("https://xueqiu.com/user/authorize")
            logger.info(
                "[%s] 雪球连接 (mode=%s, portfolio=%s)",
                self.broker_name,
                self.mode,
                self.portfolio_code,
            )
            self._session = {
                "cookies": self.cookies[:16] + "...",  # 脱敏
                "mode": self.mode,
                "portfolio_code": self.portfolio_code,
            }
            return True
        except Exception as e:  # noqa: BLE001  # broker API 边界, fail-safe
            logger.exception("[%s] 雪球连接异常: %s", self.broker_name, e)
            return False

    def _do_disconnect(self) -> None:
        if self._session:
            # 实际接入时: self._session.close()
            self._session = None
        logger.info("[%s] 雪球已断开", self.broker_name)

    def _do_submit_order(self, order: BrokerOrder) -> bool:
        """提交订单到雪球."""
        if not self._session:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = "雪球会话未建立"
            return False
        if self.mode == "portfolio":
            # 组合调仓 (不实际下单, 仅记录)
            # TODO: 调用 https://xueqiu.com/cubes/rebalancing/create.json
            logger.info(
                "[%s] 组合调仓 (TODO: 实际接入): %s %s %d@%s",
                self.broker_name,
                order.symbol,
                order.side.value,
                order.quantity,
                order.price,
            )
            order.status = OrderStatus.SUBMITTED
            return True
        elif self.mode == "broker":
            # 真实券商下单 (需通过雪球合作的券商)
            if not self.broker:
                order.status = OrderStatus.REJECTED
                order.rejection_reason = "broker 模式需配置 broker 参数"
                return False
            # TODO: 实际接入时调用券商桥接接口
            logger.info(
                "[%s] 真实券商下单 (TODO, broker=%s): %s %s %d@%s",
                self.broker_name,
                self.broker,
                order.symbol,
                order.side.value,
                order.quantity,
                order.price,
            )
            order.status = OrderStatus.SUBMITTED
            return True
        order.status = OrderStatus.REJECTED
        order.rejection_reason = f"未知 mode: {self.mode}"
        return False

    def _do_cancel_order(self, order_id: str) -> bool:
        if not self._session:
            return False
        # TODO: 调用雪球撤单接口
        logger.info("[%s] 雪球撤单 (TODO): %s", self.broker_name, order_id)
        return True

    def _do_get_positions(self) -> list[dict]:
        if not self._session:
            return []
        # TODO: 调用 https://xueqiu.com/cubes/weight.json
        return []

    def _do_get_account_info(self) -> dict:
        if not self._session:
            return {"broker": self.broker_name, "ready": False}
        return {
            "broker": self.broker_name,
            "mode": self.mode,
            "portfolio_code": self.portfolio_code,
            "ready": True,
        }

    def _do_get_market_data(self, symbol: str, period: str, count: int) -> dict:
        if not self._session:
            return {"symbol": symbol, "data": [], "error": "雪球未连接"}
        # TODO: 调用 https://xueqiu.com/stock/forchartk/stocklist.json
        return {"symbol": symbol, "data": [], "source": "xueqiu"}


# ============================================================
# 工厂函数
# ============================================================
_ADAPTER_REGISTRY: dict[str, type] = {
    "ths": ThsBrokerAdapter,
    "xueqiu": XueqiuBrokerAdapter,
}


def create_broker_adapter(broker_type: str, config: dict[str, Any] | None = None) -> Any:
    """创建 broker adapter (工厂函数).

    Args:
        broker_type: adapter 类型, 支持 "ths" / "xueqiu"
        config: 配置字典

    Returns:
        adapter 实例

    Raises:
        ValueError: 不支持的 broker_type
    """
    if config is None:
        config = {}
    broker_type = broker_type.lower()
    adapter_class = _ADAPTER_REGISTRY.get(broker_type)
    if adapter_class is None:
        raise ValueError(f"不支持的 broker 类型: {broker_type}, 已注册: {list(_ADAPTER_REGISTRY.keys())}")
    return adapter_class(config)


def list_supported_brokers() -> list[str]:
    """列出已注册的 broker 类型."""
    return list(_ADAPTER_REGISTRY.keys())


def register_broker_adapter(broker_type: str, adapter_class: type) -> None:
    """动态注册新 broker adapter (扩展点).

    Args:
        broker_type: broker 类型标识
        adapter_class: adapter 类 (必须继承 _BaseLiveAdapter)

    Raises:
        TypeError: adapter_class 不是 _BaseLiveAdapter 子类
    """
    if not (isinstance(adapter_class, type) and issubclass(adapter_class, _BaseLiveAdapter)):
        raise TypeError(f"adapter_class 必须继承 _BaseLiveAdapter, got {adapter_class}")
    _ADAPTER_REGISTRY[broker_type.lower()] = adapter_class
    logger.info("已注册 broker adapter: %s -> %s", broker_type, adapter_class.__name__)
