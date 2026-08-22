"""TradingAgents 桥接客户端 — 28 系统侧 HTTP 适配器

核心功能:
    通过 HTTP 调用 TradingAgents 桥接微服务 (28_bridge.py), 在 Python 3.8 环境
    中使用 TradingAgents (Python 3.10+) 的多 Agent 决策能力.

设计原则:
    1. 懒加载: 只在首次调用时检测微服务, 不影响系统启动
    2. 优雅降级: 微服务不可用时回退到本地 finance_agent_orchestrator
    3. 超时保护: 默认 120s 超时 (多 Agent 推理耗时较长)
    4. 单例模式: 避免重复连接检测

降级链:
    TradingAgents 微服务 (Python 3.10+)
        ↓ (不可用时降级)
    本地 finance_agent_orchestrator (5 Agent 投票)
        ↓ (不可用时降级)
    中性决策 (HOLD, confidence=0)

用法:
    from utils.tradingagents_bridge import TradingAgentsBridge

    bridge = TradingAgentsBridge()
    result = bridge.analyze("AAPL", "2026-08-01")
    # result = {"action": "BUY", "confidence": 0.7, "reasoning": "...", "source": "tradingagents"}

启动微服务 (在 Python 3.10+ 环境中):
    py -3.11 ${TRADINGAGENTS_BRIDGE_PATH} --port 8490
    (默认: <项目根>/../TradingAgents/28_bridge.py, 可通过 TRADINGAGENTS_BRIDGE_PATH 环境变量覆盖)

作者: 28 系统 PM
日期: 2026-08-01
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("tradingagents_bridge")

# ============================================================
# 配置
# ============================================================

# 微服务地址 (可通过环境变量覆盖)
_DEFAULT_HOST = os.environ.get("TRADINGAGENTS_BRIDGE_HOST", "127.0.0.1")
_DEFAULT_PORT = int(os.environ.get("TRADINGAGENTS_BRIDGE_PORT", "8490"))
_DEFAULT_TIMEOUT = int(os.environ.get("TRADINGAGENTS_BRIDGE_TIMEOUT", "120"))  # 多Agent推理慢
_HEALTH_CHECK_CACHE_SEC = 30  # 健康检查缓存 30 秒, 避免频繁探测


# ============================================================
# 桥接客户端
# ============================================================


class TradingAgentsBridge:
    """TradingAgents 微服务 HTTP 客户端.

    通过 HTTP 调用 28_bridge.py 暴露的 API, 获取多 Agent 决策结果.
    微服务不可用时自动降级到本地 orchestrator.

    Attributes:
        host: 微服务地址
        port: 微服务端口
        timeout: 请求超时 (秒)
        _available: 微服务可用性缓存
        _last_check: 上次健康检查时间戳

    Usage:
        >>> bridge = TradingAgentsBridge()
        >>> if bridge.is_available():
        ...     result = bridge.analyze("AAPL", "2026-08-01")
    """

    def __init__(
        self,
        host: str = _DEFAULT_HOST,
        port: int = _DEFAULT_PORT,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        """初始化桥接客户端.

        Args:
            host: 微服务地址
            port: 微服务端口
            timeout: HTTP 请求超时秒数
        """
        self.host = host
        self.port = port
        self.timeout = timeout
        self._base_url = f"http://{host}:{port}"
        self._available: bool | None = None
        self._last_check: float = 0.0

    # ------------------------------------------------------------
    # 可用性检测
    # ------------------------------------------------------------

    @property
    def base_url(self) -> str:
        """微服务基础 URL."""
        return self._base_url

    def is_available(self, force_check: bool = False) -> bool:
        """检测 TradingAgents 微服务是否可用.

        带缓存的健康检查, 避免每次调用都发 HTTP 请求.

        Args:
            force_check: 是否强制重新检测 (忽略缓存)

        Returns:
            微服务是否可用
        """
        now = time.time()
        # 缓存有效期内的检测结果
        if (
            not force_check
            and self._available is not None
            and (now - self._last_check) < _HEALTH_CHECK_CACHE_SEC
        ):
            return self._available

        # 快速端口探测 (比 HTTP 请求更快失败)
        if not self._check_port():
            self._available = False
            self._last_check = now
            logger.debug(
                f"TradingAgents 微服务端口不可达: {self.host}:{self.port}"
            )
            return False

        # HTTP 健康检查
        try:
            resp = self._http_get("/health", timeout=5)
            self._available = (
                resp is not None
                and resp.get("status") == "ok"
                and resp.get("tradingagents_available", False) is True
            )
            self._last_check = now
            if self._available:
                logger.info(
                    f"TradingAgents 微服务可用: {self._base_url} "
                    f"(Python: {resp.get('python_version', '?')[:20]})"
                )
            else:
                logger.warning(
                    f"TradingAgents 微服务在线但框架未加载: {resp}"
                )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            self._available = False
            self._last_check = now
            logger.debug(f"TradingAgents 健康检查失败: {e}")

        return self._available

    def _check_port(self) -> bool:
        """快速 TCP 端口探测.

        Returns:
            端口是否可连接
        """
        try:
            with socket.create_connection(
                (self.host, self.port), timeout=2
            ):
                return True
        except (TimeoutError, ConnectionRefusedError, OSError):
            return False

    # ------------------------------------------------------------
    # API 调用
    # ------------------------------------------------------------

    def get_analysts(self) -> list[str]:
        """获取可用分析师列表.

        Returns:
            分析师名称列表, 失败返回空列表
        """
        if not self.is_available():
            return []
        resp = self._http_get("/analysts", timeout=5)
        if resp and "analysts" in resp:
            return resp["analysts"]
        return []

    def analyze(
        self,
        ticker: str,
        date: str | None = None,
        analysts: list[str] | None = None,
    ) -> dict[str, Any]:
        """调用 TradingAgents 多 Agent 分析.

        Args:
            ticker: 股票代码 (如 "AAPL", "600519.SH")
            date: 分析日期 "YYYY-MM-DD", 默认今天
            analysts: 指定分析师列表, 默认使用全部

        Returns:
            标准化决策结果:
            {
                "action": "BUY"|"SELL"|"HOLD",
                "confidence": float,       # [0, 1]
                "reasoning": str,
                "state_summary": dict,     # 各分析师报告摘要
                "source": "tradingagents",
                "ticker": str,
                "date": str,
                "timestamp": str,
            }
            微服务不可用时返回降级结果 (source="fallback" 或 "neutral")
        """
        if not ticker:
            return self._neutral_result(ticker, date, "缺少 ticker 参数")

        if date is None:
            date = time.strftime("%Y-%m-%d")

        # 检查微服务可用性
        if not self.is_available():
            logger.info("TradingAgents 微服务不可用, 降级到本地 orchestrator")
            return self._fallback_to_local(ticker, date)

        # 调用微服务
        payload: dict[str, Any] = {"ticker": ticker, "date": date}
        if analysts:
            payload["analysts"] = analysts

        try:
            resp = self._http_post("/analyze", payload, timeout=self.timeout)
            if resp and "decision" in resp:
                decision = resp["decision"]
                result = {
                    "action": decision.get("action", "HOLD"),
                    "confidence": float(decision.get("confidence", 0.5)),
                    "reasoning": decision.get("reasoning", ""),
                    "state_summary": resp.get("state_summary", {}),
                    "source": "tradingagents",
                    "ticker": resp.get("ticker", ticker),
                    "date": resp.get("date", date),
                    "timestamp": resp.get("timestamp", ""),
                }
                logger.info(
                    f"TradingAgents 决策: {ticker} → {result['action']} "
                    f"(confidence={result['confidence']:.0%})"
                )
                return result
            # 微服务返回异常
            logger.warning(f"TradingAgents 微服务返回异常: {resp}")
            return self._fallback_to_local(ticker, date)
        except URLError as e:
            logger.warning(f"TradingAgents 微服务请求超时/失败: {e}")
            return self._fallback_to_local(ticker, date)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"TradingAgents 分析异常: {e}", exc_info=True)
            return self._fallback_to_local(ticker, date)

    # ------------------------------------------------------------
    # 降级链
    # ------------------------------------------------------------

    def _fallback_to_local(self, ticker: str, date: str) -> dict[str, Any]:
        """降级到本地 finance_agent_orchestrator.

        Args:
            ticker: 股票代码
            date: 分析日期

        Returns:
            本地 orchestrator 的决策结果, 或中性决策
        """
        try:
            from utils.finance_agent_orchestrator import FinanceAgentOrchestrator

            orchestrator = FinanceAgentOrchestrator()
            consensus = orchestrator.orchestrate(ticker, context={"date": date})
            return {
                "action": consensus.action.upper(),
                "confidence": consensus.confidence,
                "reasoning": f"本地 orchestrator 降级决策 (veto={consensus.veto})",
                "state_summary": {},
                "source": "fallback_local",
                "ticker": ticker,
                "date": date,
                "timestamp": consensus.timestamp,
            }
        except ImportError:
            logger.warning("本地 finance_agent_orchestrator 不可用, 返回中性决策")
            return self._neutral_result(ticker, date, "本地 orchestrator 不可用")
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"本地 orchestrator 降级失败: {e}", exc_info=True)
            return self._neutral_result(ticker, date, f"降级异常: {e}")

    def _neutral_result(
        self, ticker: str, date: str, reason: str = ""
    ) -> dict[str, Any]:
        """返回中性决策 (兜底).

        Args:
            ticker: 股票代码
            date: 分析日期
            reason: 中性原因

        Returns:
            中性决策结果
        """
        return {
            "action": "HOLD",
            "confidence": 0.0,
            "reasoning": f"中性决策: {reason}" if reason else "中性决策",
            "state_summary": {},
            "source": "neutral",
            "ticker": ticker,
            "date": date or time.strftime("%Y-%m-%d"),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    # ------------------------------------------------------------
    # HTTP 工具
    # ------------------------------------------------------------

    def _http_get(
        self, path: str, timeout: int | None = None
    ) -> dict[str, Any] | None:
        """HTTP GET 请求.

        Args:
            path: 请求路径
            timeout: 超时秒数

        Returns:
            JSON 响应字典, 失败返回 None
        """
        url = f"{self._base_url}{path}"
        req = Request(url, method="GET")
        try:
            with urlopen(req, timeout=timeout or self.timeout) as resp:  # nosec B310  # TradingAgents API 合法请求
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.debug(f"HTTP GET {path} 失败: {e}")
            return None

    def _http_post(
        self, path: str, payload: dict[str, Any], timeout: int | None = None
    ) -> dict[str, Any] | None:
        """HTTP POST 请求.

        Args:
            path: 请求路径
            payload: 请求体 (JSON)
            timeout: 超时秒数

        Returns:
            JSON 响应字典, 失败返回 None
        """
        url = f"{self._base_url}{path}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urlopen(req, timeout=timeout or self.timeout) as resp:  # nosec B310
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except URLError as e:
            logger.warning("TradingAgents POST 请求失败 [%s]: %s", path, e)
            return None
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("TradingAgents POST 响应解析失败 [%s]: %s", path, e)
            return None


# ============================================================
# 单例便捷函数
# ============================================================

_default_bridge: TradingAgentsBridge | None = None


def get_bridge(
    host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT
) -> TradingAgentsBridge:
    """获取默认桥接客户端单例.

    Args:
        host: 微服务地址
        port: 微服务端口

    Returns:
        TradingAgentsBridge 实例
    """
    global _default_bridge
    if _default_bridge is None:
        _default_bridge = TradingAgentsBridge(host=host, port=port)
    return _default_bridge


def analyze(
    ticker: str,
    date: str | None = None,
    analysts: list[str] | None = None,
) -> dict[str, Any]:
    """便捷函数: 调用 TradingAgents 多 Agent 分析.

    Args:
        ticker: 股票代码
        date: 分析日期
        analysts: 指定分析师列表

    Returns:
        标准化决策结果
    """
    return get_bridge().analyze(ticker, date, analysts)


def is_available() -> bool:
    """便捷函数: 检测微服务是否可用.

    Returns:
        微服务是否可用
    """
    return get_bridge().is_available()
