#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
真实券商接口实现
================

提供两类真实接入能力：
1. REST Broker：基于通用 HTTP 的券商接口，可配置 base_url / token / account
2. EasyTrader Broker：若安装 easytrader，则兼容华泰、中信等客户端下单

注意：接入实盘前请先在 config.py / 环境变量中完成鉴权信息配置。
"""

import os
import time
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("RealBroker")


class RealBrokerInterface(ABC):
    """真实券商接口统一抽象"""

    @abstractmethod
    def connect(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_account_info(self) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def place_order(self, code: str, name: str, side: str,
                    quantity: int, limit_price: float,
                    order_type: str = "LIMIT") -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def query_order(self, broker_order_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def query_today_orders(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def query_today_trades(self) -> List[Dict[str, Any]]:
        raise NotImplementedError


class RestBroker(RealBrokerInterface):
    """
    通用 REST 券商接口

    通过配置化 base_url / headers 适配多数提供 REST 交易的券商通道。
    仅作为最小可用接入层，具体字段以你的券商文档为准。
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or self._load_config()
        self.connected = False
        self.session_expire_at = 0.0
        self._today_orders: Dict[str, Dict[str, Any]] = {}
        self._today_trades: List[Dict[str, Any]] = []

    def _load_config(self) -> Dict[str, Any]:
        try:
            import config as cfg_module
            cfg = cfg_module.Config()._config
            api_cfg = getattr(cfg, "api_config", None) or {}
            broker_cfg = api_cfg.get("broker", {})
            return {
                "base_url": broker_cfg.get("base_url", os.getenv("BROKER_BASE_URL", "")),
                "token": broker_cfg.get("token", os.getenv("BROKER_TOKEN", "")),
                "account": broker_cfg.get("account", os.getenv("BROKER_ACCOUNT", "")),
                "enable": broker_cfg.get("enable", False),
                "dry_run": broker_cfg.get("dry_run", False),
            }
        except Exception as e:
            logger.warning(f"加载 broker 配置失败，使用默认值: {e}")
            return {
                "base_url": os.getenv("BROKER_BASE_URL", ""),
                "token": os.getenv("BROKER_TOKEN", ""),
                "account": os.getenv("BROKER_ACCOUNT", ""),
                "enable": False,
                "dry_run": False,
            }

    def _headers(self) -> Dict[str, str]:
        token = self.config.get("token", "")
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}" if token else "",
        }

    def _is_dry_run(self) -> bool:
        return bool(self.config.get("dry_run"))

    def connect(self) -> bool:
        if self._is_dry_run():
            self.connected = True
            self.session_expire_at = time.time() + 3600
            logger.info("[RestBroker] dry-run 模式连接成功")
            return True
        if not self.config.get("enable"):
            logger.error("broker.enable=false，请先在配置中启用并填写 base_url/token/account")
            return False
        if not self.config.get("base_url"):
            logger.error("broker.base_url 为空，无法连接真实券商")
            return False

        try:
            import requests
        except ImportError:
            logger.error("requests 未安装，无法使用 RestBroker；请安装 requests 或改用 MockBroker")
            return False

        try:
            url = self.config["base_url"].rstrip("/") + "/account/info"
            resp = requests.get(url, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                self.connected = True
                self.session_expire_at = time.time() + 3600
                logger.info(f"[RestBroker] 连接成功 account={self.config.get('account')}")
                return True
            logger.error(f"[RestBroker] 连接失败 status={resp.status_code} body={resp.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"[RestBroker] 连接异常: {e}")
            return False

    def disconnect(self) -> bool:
        self.connected = False
        logger.info("[RestBroker] 断开连接")
        return True

    def get_account_info(self) -> Dict[str, Any]:
        if self._is_dry_run():
            return {
                "account": self.config.get("account", "dry_run_account"),
                "total_asset": 5_000_000.0,
                "market_value": 2_000_000.0,
                "available_cash": 3_000_000.0,
                "frozen_cash": 0.0,
                "currency": "CNY",
                "mode": "dry_run",
            }
        if not self.connected:
            return {"error": "broker_not_connected"}
        try:
            import requests
            url = self.config["base_url"].rstrip("/") + "/account/info"
            resp = requests.get(url, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"status={resp.status_code}", "body": resp.text[:200]}
        except Exception as e:
            logger.error(f"[RestBroker] 获取账户信息失败: {e}")
            return {"error": str(e)}

    def get_positions(self) -> List[Dict[str, Any]]:
        if self._is_dry_run():
            return [
                {
                    "code": "IF2607",
                    "name": "沪深300期货",
                    "quantity": 2,
                    "available_quantity": 2,
                    "avg_price": 3200.0,
                    "market_price": 3210.0,
                    "market_value": 964_000.0,
                    "profit": 2_000.0,
                    "account_type": "dry_run",
                },
                {
                    "code": "510300",
                    "name": "华泰柏瑞沪深300ETF",
                    "quantity": 200_000,
                    "available_quantity": 200_000,
                    "avg_price": 3.8,
                    "market_price": 3.82,
                    "market_value": 764_000.0,
                    "profit": 4_000.0,
                    "account_type": "dry_run",
                },
            ]
        if not self.connected:
            return []
        try:
            import requests
            url = self.config["base_url"].rstrip("/") + "/positions"
            resp = requests.get(url, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get("positions", [])
            return []
        except Exception as e:
            logger.error(f"[RestBroker] 获取持仓失败: {e}")
            return []

    def place_order(self, code: str, name: str, side: str,
                    quantity: int, limit_price: float,
                    order_type: str = "LIMIT") -> Dict[str, Any]:
        if self._is_dry_run():
            order_id = f"dry_run_{int(time.time())}"
            record = {
                "broker_order_id": order_id,
                "code": code,
                "name": name,
                "side": side,
                "quantity": quantity,
                "limit_price": limit_price,
                "status": "accepted",
                "created_at": datetime.now().isoformat(),
                "mode": "dry_run",
            }
            self._today_orders[order_id] = record
            logger.info(f"[RestBroker][dry-run] 模拟下单成功: {record}")
            return {"success": True, "broker_order_id": order_id, "status": "accepted", "mode": "dry_run"}
        if not self.connected:
            return {"success": False, "message": "broker_not_connected"}
        try:
            import requests
            url = self.config["base_url"].rstrip("/") + "/orders"
            payload = {
                "account": self.config.get("account"),
                "code": code,
                "name": name,
                "side": side,
                "quantity": quantity,
                "limit_price": limit_price,
                "order_type": order_type,
            }
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=10)
            data = resp.json() if resp.status_code == 200 else {"success": False, "message": resp.text}
            order_id = data.get("broker_order_id") or data.get("order_id") or ""
            if order_id:
                self._today_orders[order_id] = {
                    "broker_order_id": order_id,
                    "code": code,
                    "name": name,
                    "side": side,
                    "quantity": quantity,
                    "limit_price": limit_price,
                    "status": data.get("status", "sent"),
                    "created_at": datetime.now().isoformat(),
                }
            return data
        except Exception as e:
            logger.error(f"[RestBroker] 下单失败: {e}")
            return {"success": False, "message": str(e)}

    def cancel_order(self, broker_order_id: str) -> Dict[str, Any]:
        if self._is_dry_run():
            return {"success": True, "broker_order_id": broker_order_id, "status": "cancelled", "mode": "dry_run"}
        if not self.connected:
            return {"success": False, "message": "broker_not_connected"}
        try:
            import requests
            url = self.config["base_url"].rstrip("/") + f"/orders/{broker_order_id}/cancel"
            resp = requests.post(url, headers=self._headers(), timeout=10)
            return resp.json() if resp.status_code == 200 else {"success": False, "message": resp.text}
        except Exception as e:
            logger.error(f"[RestBroker] 撤单失败: {e}")
            return {"success": False, "message": str(e)}

    def query_order(self, broker_order_id: str) -> Dict[str, Any]:
        if self._is_dry_run():
            record = self._today_orders.get(broker_order_id)
            if record:
                return dict(record)
            return {"broker_order_id": broker_order_id, "status": "accepted", "mode": "dry_run"}
        if not self.connected:
            return {"broker_order_id": broker_order_id, "status": "rejected"}
        try:
            import requests
            url = self.config["base_url"].rstrip("/") + f"/orders/{broker_order_id}"
            resp = requests.get(url, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                return resp.json()
            return {"broker_order_id": broker_order_id, "status": "unknown"}
        except Exception as e:
            logger.error(f"[RestBroker] 查单失败: {e}")
            return {"broker_order_id": broker_order_id, "status": "error", "message": str(e)}

    def query_today_orders(self) -> List[Dict[str, Any]]:
        if self._is_dry_run():
            return list(self._today_orders.values())
        if not self.connected:
            return []
        try:
            import requests
            url = self.config["base_url"].rstrip("/") + "/orders"
            resp = requests.get(url, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get("orders", [])
            return list(self._today_orders.values())
        except Exception as e:
            logger.error(f"[RestBroker] 查询当日订单失败: {e}")
            return list(self._today_orders.values())

    def query_today_trades(self) -> List[Dict[str, Any]]:
        if self._is_dry_run():
            return self._today_trades
        if not self.connected:
            return []
        try:
            import requests
            url = self.config["base_url"].rstrip("/") + "/trades"
            resp = requests.get(url, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get("trades", [])
            return self._today_trades
        except Exception as e:
            logger.error(f"[RestBroker] 查询当日成交失败: {e}")
            return self._today_trades


class EasyTraderBroker(RealBrokerInterface):
    """
    基于 easytrader 的兼容 broker

    需要安装：
        pip install easytrader

    常见客户端：
        - miniqmt
        - ths / 同花顺
        - universal_client / 通用同花顺客户端
        - gj_client / 国金客户端
        - xq / 雪球
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or self._load_config()
        self.client = None
        self.connected = False

    def _load_config(self) -> Dict[str, Any]:
        return {
            "client": os.getenv("EASYTRADER_CLIENT", "universal_client"),
            "enable": os.getenv("EASYTRADER_ENABLE", "false").lower() == "true",
        }

    def _resolve_client(self, client_name: str):
        import easytrader  # type: ignore
        if hasattr(easytrader, client_name):
            return getattr(easytrader, client_name)()
        return easytrader.use(client_name)

    def connect(self) -> bool:
        if not self.config.get("enable"):
            logger.error("easytrader 未启用，请设置 EASYTRADER_ENABLE=true")
            return False
        try:
            client_name = self.config.get("client", "universal_client")
            self.client = self._resolve_client(client_name)
            self.connected = True
            logger.info(f"[EasyTraderBroker] 连接成功 client={client_name}")
            return True
        except Exception as e:
            logger.error(f"[EasyTraderBroker] 连接失败: {e}")
            return False

    def disconnect(self) -> bool:
        self.connected = False
        logger.info("[EasyTraderBroker] 断开连接")
        return True

    def get_account_info(self) -> Dict[str, Any]:
        if not self.connected or not self.client:
            return {"error": "broker_not_connected"}
        try:
            balance = self.client.balance
            return {
                "account_id": getattr(balance, "get", lambda *_: {}).get("资金账号", "") if hasattr(balance, "get") else "",
                "available_cash": float(getattr(balance, "get", lambda *_: {}).get("可用金额", 0) if hasattr(balance, "get") else 0),
                "total_asset": float(getattr(balance, "get", lambda *_: {}).get("总资产", 0) if hasattr(balance, "get") else 0),
                "frozen_cash": float(getattr(balance, "get", lambda *_: {}).get("冻结金额", 0) if hasattr(balance, "get") else 0),
                "market_value": float(getattr(balance, "get", lambda *_: {}).get("股票市值", 0) if hasattr(balance, "get") else 0),
            }
        except Exception as e:
            logger.error(f"[EasyTraderBroker] 获取账户信息失败: {e}")
            return {"error": str(e)}

    def get_positions(self) -> List[Dict[str, Any]]:
        if not self.connected or not self.client:
            return []
        try:
            data = self.client.position
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("data", [])
            return []
        except Exception as e:
            logger.error(f"[EasyTraderBroker] 获取持仓失败: {e}")
            return []

    def place_order(self, code: str, name: str, side: str,
                    quantity: int, limit_price: float,
                    order_type: str = "LIMIT") -> Dict[str, Any]:
        if not self.connected or not self.client:
            return {"success": False, "message": "broker_not_connected"}
        try:
            direction = "buy" if side.upper() == "BUY" else "sell"
            order = self.client.buy if side.upper() == "BUY" else self.client.sell
            result = order(code=code, price=limit_price, amount=quantity)
            return {
                "success": True,
                "broker_order_id": result.get("entrust_no", "") if isinstance(result, dict) else "",
                "code": code,
                "name": name,
                "side": side,
                "quantity": quantity,
                "limit_price": limit_price,
                "status": "sent",
                "message": "easytrader 下单成功",
            }
        except Exception as e:
            logger.error(f"[EasyTraderBroker] 下单失败: {e}")
            return {"success": False, "message": str(e)}

    def cancel_order(self, broker_order_id: str) -> Dict[str, Any]:
        if not self.connected or not self.client:
            return {"success": False, "message": "broker_not_connected"}
        try:
            result = self.client.cancel_entrust(broker_order_id)
            return {
                "success": True,
                "broker_order_id": broker_order_id,
                "status": "cancelled",
                "message": "easytrader 撤单成功",
            }
        except Exception as e:
            logger.error(f"[EasyTraderBroker] 撤单失败: {e}")
            return {"success": False, "message": str(e)}

    def query_order(self, broker_order_id: str) -> Dict[str, Any]:
        if not self.connected or not self.client:
            return {"broker_order_id": broker_order_id, "status": "rejected"}
        try:
            result = self.client.get_entrust(broker_order_id)
            if isinstance(result, dict):
                status = result.get("状态", "unknown")
                return {
                    "broker_order_id": broker_order_id,
                    "status": "filled" if "已成" in status else "cancelled" if "已撤" in status else "rejected" if "废单" in status else "sent",
                    "filled_quantity": int(result.get("成交数量", 0)),
                    "avg_price": float(result.get("成交均价", 0.0) or 0.0),
                }
            return {"broker_order_id": broker_order_id, "status": "unknown"}
        except Exception as e:
            logger.error(f"[EasyTraderBroker] 查单失败: {e}")
            return {"broker_order_id": broker_order_id, "status": "error", "message": str(e)}

    def query_today_orders(self) -> List[Dict[str, Any]]:
        if not self.connected or not self.client:
            return []
        try:
            result = self.client.get_today_entrusts()
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return result.get("data", [])
            return []
        except Exception as e:
            logger.error(f"[EasyTraderBroker] 查询当日订单失败: {e}")
            return []

    def query_today_trades(self) -> List[Dict[str, Any]]:
        if not self.connected or not self.client:
            return []
        try:
            result = self.client.get_today_trades()
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return result.get("data", [])
            return []
        except Exception as e:
            logger.error(f"[EasyTraderBroker] 查询当日成交失败: {e}")
            return []


class IFindBroker(RealBrokerInterface):
    """
    同花顺 iFind API Broker

    支持 iFind 量化平台的 JWT 鉴权，实现：
      - 账户信息查询
      - 持仓查询
      - 下单 / 撤单 / 查单
      - 当日委托 / 当日成交

    配置方式：
      - system_config.json -> api_config.broker
      - 或环境变量 IFIND_BASE_URL / IFIND_TOKEN / IFIND_ACCOUNT
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or self._load_config()
        self.connected = False
        self.session_expire_at = 0.0
        self._today_orders: Dict[str, Dict[str, Any]] = {}
        self._today_trades: List[Dict[str, Any]] = []

    def _load_config(self) -> Dict[str, Any]:
        try:
            import config as cfg_module
            cfg = cfg_module.Config()._config
            api_cfg = getattr(cfg, "api_config", None) or {}
            broker_cfg = api_cfg.get("broker", {})
            return {
                "base_url": broker_cfg.get("base_url", os.getenv("IFIND_BASE_URL", "")),
                "token": broker_cfg.get("token", os.getenv("IFIND_TOKEN", "")),
                "account": broker_cfg.get("account", os.getenv("IFIND_ACCOUNT", "")),
                "enable": broker_cfg.get("enable", False),
                "dry_run": broker_cfg.get("dry_run", False),
            }
        except Exception as e:
            logger.warning(f"加载 iFind 配置失败，使用默认值: {e}")
            return {
                "base_url": os.getenv("IFIND_BASE_URL", ""),
                "token": os.getenv("IFIND_TOKEN", ""),
                "account": os.getenv("IFIND_ACCOUNT", ""),
                "enable": False,
                "dry_run": False,
            }

    def _headers(self) -> Dict[str, str]:
        token = self.config.get("token", "")
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}" if token else "",
        }

    def _is_dry_run(self) -> bool:
        return bool(self.config.get("dry_run"))

    def connect(self) -> bool:
        if self._is_dry_run():
            self.connected = True
            self.session_expire_at = time.time() + 3600
            logger.info("[IFindBroker] dry-run 模式连接成功")
            return True
        if not self.config.get("enable"):
            logger.error("iFind 未启用，请先在配置中启用并填写 base_url/token/account")
            return False
        if not self.config.get("base_url"):
            logger.error("iFind base_url 为空，无法连接")
            return False
        try:
            import requests  # type: ignore
        except ImportError:
            logger.error("requests 未安装，无法使用 IFindBroker")
            return False
        try:
            url = self.config["base_url"].rstrip("/") + "/api/account/info"
            resp = requests.get(url, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                self.connected = True
                self.session_expire_at = time.time() + 3600
                logger.info(f"[IFindBroker] 连接成功 account={self.config.get('account')}")
                return True
            logger.error(f"[IFindBroker] 连接失败 status={resp.status_code} body={resp.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"[IFindBroker] 连接异常: {e}")
            return False

    def disconnect(self) -> bool:
        self.connected = False
        logger.info("[IFindBroker] 断开连接")
        return True

    def get_account_info(self) -> Dict[str, Any]:
        if self._is_dry_run():
            return {
                "account": self.config.get("account", "ifind_dry_run"),
                "total_asset": 5_000_000.0,
                "market_value": 2_000_000.0,
                "available_cash": 3_000_000.0,
                "frozen_cash": 0.0,
                "currency": "CNY",
                "mode": "ifind_dry_run",
            }
        if not self.connected:
            return {"error": "broker_not_connected"}
        try:
            import requests  # type: ignore
            url = self.config["base_url"].rstrip("/") + "/api/account/info"
            resp = requests.get(url, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"status={resp.status_code}", "body": resp.text[:200]}
        except Exception as e:
            logger.error(f"[IFindBroker] 获取账户信息失败: {e}")
            return {"error": str(e)}

    def get_positions(self) -> List[Dict[str, Any]]:
        if self._is_dry_run():
            return [
                {
                    "code": "IF2607",
                    "name": "沪深300期货",
                    "quantity": 2,
                    "available_quantity": 2,
                    "avg_price": 3200.0,
                    "market_price": 3210.0,
                    "market_value": 964_000.0,
                    "profit": 2_000.0,
                    "account_type": "ifind_dry_run",
                },
                {
                    "code": "510300",
                    "name": "华泰柏瑞沪深300ETF",
                    "quantity": 200_000,
                    "available_quantity": 200_000,
                    "avg_price": 3.8,
                    "market_price": 3.82,
                    "market_value": 764_000.0,
                    "profit": 4_000.0,
                    "account_type": "ifind_dry_run",
                },
            ]
        if not self.connected:
            return []
        try:
            import requests  # type: ignore
            url = self.config["base_url"].rstrip("/") + "/api/positions"
            resp = requests.get(url, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get("positions", [])
            return []
        except Exception as e:
            logger.error(f"[IFindBroker] 获取持仓失败: {e}")
            return []

    def place_order(self, code: str, name: str, side: str,
                    quantity: int, limit_price: float,
                    order_type: str = "LIMIT") -> Dict[str, Any]:
        if self._is_dry_run():
            order_id = f"ifind_dry_run_{int(time.time())}"
            record = {
                "broker_order_id": order_id,
                "code": code,
                "name": name,
                "side": side,
                "quantity": quantity,
                "limit_price": limit_price,
                "status": "accepted",
                "created_at": datetime.now().isoformat(),
                "mode": "ifind_dry_run",
            }
            self._today_orders[order_id] = record
            logger.info(f"[IFindBroker][dry-run] 模拟下单成功: {record}")
            return {"success": True, "broker_order_id": order_id, "status": "accepted", "mode": "ifind_dry_run"}
        if not self.connected:
            return {"success": False, "message": "broker_not_connected"}
        try:
            import requests  # type: ignore
            url = self.config["base_url"].rstrip("/") + "/api/orders"
            payload = {
                "account": self.config.get("account"),
                "code": code,
                "name": name,
                "side": side,
                "quantity": quantity,
                "limit_price": limit_price,
                "order_type": order_type,
            }
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=10)
            data = resp.json() if resp.status_code == 200 else {"success": False, "message": resp.text}
            order_id = data.get("broker_order_id") or data.get("order_id") or ""
            if order_id:
                self._today_orders[order_id] = {
                    "broker_order_id": order_id,
                    "code": code,
                    "name": name,
                    "side": side,
                    "quantity": quantity,
                    "limit_price": limit_price,
                    "status": data.get("status", "sent"),
                    "created_at": datetime.now().isoformat(),
                }
            return data
        except Exception as e:
            logger.error(f"[IFindBroker] 下单失败: {e}")
            return {"success": False, "message": str(e)}

    def cancel_order(self, broker_order_id: str) -> Dict[str, Any]:
        if self._is_dry_run():
            return {"success": True, "broker_order_id": broker_order_id, "status": "cancelled", "mode": "ifind_dry_run"}
        if not self.connected:
            return {"success": False, "message": "broker_not_connected"}
        try:
            import requests  # type: ignore
            url = self.config["base_url"].rstrip("/") + f"/api/orders/{broker_order_id}/cancel"
            resp = requests.post(url, headers=self._headers(), timeout=10)
            return resp.json() if resp.status_code == 200 else {"success": False, "message": resp.text}
        except Exception as e:
            logger.error(f"[IFindBroker] 撤单失败: {e}")
            return {"success": False, "message": str(e)}

    def query_order(self, broker_order_id: str) -> Dict[str, Any]:
        if self._is_dry_run():
            record = self._today_orders.get(broker_order_id)
            if record:
                return dict(record)
            return {"broker_order_id": broker_order_id, "status": "accepted", "mode": "ifind_dry_run"}
        if not self.connected:
            return {"broker_order_id": broker_order_id, "status": "rejected"}
        try:
            import requests  # type: ignore
            url = self.config["base_url"].rstrip("/") + f"/api/orders/{broker_order_id}"
            resp = requests.get(url, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                return resp.json()
            return {"broker_order_id": broker_order_id, "status": "unknown"}
        except Exception as e:
            logger.error(f"[IFindBroker] 查单失败: {e}")
            return {"broker_order_id": broker_order_id, "status": "error", "message": str(e)}

    def query_today_orders(self) -> List[Dict[str, Any]]:
        if self._is_dry_run():
            return list(self._today_orders.values())
        if not self.connected:
            return []
        try:
            import requests  # type: ignore
            url = self.config["base_url"].rstrip("/") + "/api/orders"
            resp = requests.get(url, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get("orders", [])
            return list(self._today_orders.values())
        except Exception as e:
            logger.error(f"[IFindBroker] 查询当日订单失败: {e}")
            return list(self._today_orders.values())

    def query_today_trades(self) -> List[Dict[str, Any]]:
        if self._is_dry_run():
            return self._today_trades
        if not self.connected:
            return []
        try:
            import requests  # type: ignore
            url = self.config["base_url"].rstrip("/") + "/api/trades"
            resp = requests.get(url, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get("trades", [])
            return self._today_trades
        except Exception as e:
            logger.error(f"[IFindBroker] 查询当日成交失败: {e}")
            return self._today_trades


def create_broker(broker_type: str = "mock", config: Optional[Dict[str, Any]] = None) -> RealBrokerInterface:
    """
    工厂函数：根据类型创建 broker
      - mock / simulation -> MockBroker
      - rest -> RestBroker
      - easytrader / ht / ths -> EasyTraderBroker
      - ifind -> IFindBroker
    """
    if broker_type in ("mock", "simulation", ""):
        from auto_hedge_executor import MockBroker
        return MockBroker()

    if broker_type in ("rest", "http"):
        return RestBroker(config)

    if broker_type in ("easytrader", "ht", "ths"):
        return EasyTraderBroker(config)

    if broker_type == "ifind":
        return IFindBroker(config)

    raise ValueError(f"不支持的 broker 类型: {broker_type}")
