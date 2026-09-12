"""OmniRoute 网关适配层 — 终极量化交易系统 8.4.

将 OmniRoute (开源 AI 网关, 290+ provider, 500+ 模型)
封装为与现有 LLM 架构兼容的适配层。

OmniRoute 核心价值:
    1. 一个端点接入 290+ 提供商, 500+ 模型
    2. Combo 自动路由链 — 配额耗尽/故障时无缝切换
    3. 18 种路由策略 (成本优先/速度优先/质量优先/Fusion)
    4. Token 压缩 15-95% (RTK + Caveman 双层)
    5. Quota-Share 团队配额公平调度
    6. 本地部署, 数据不过第三方 (MIT 开源)

部署方式:
    npm install -g omniroute
    omniroute serve          # 默认 http://localhost:20128
    # 仪表板: http://localhost:20128/dashboard

API 兼容:
    OmniRoute 提供 OpenAI 兼容 /v1/chat/completions 端点
    只需将 base_url 指向 OmniRoute, model 设为 "auto" 即可

Feature Flag:
    USE_OMNIROUTE=False (默认, 完全不影响现有功能)

Usage:
    >>> from utils.alpha.omni_route_client import OmniRouteClient, chat, chat_deep
    >>> client = OmniRouteClient.get_instance()
    >>> reply = client.chat("分析一下当前市场趋势")
    >>> if reply:
    ...     logger.info(reply)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from utils.safe_url import safe_urlopen

logger = logging.getLogger("omni_route_client")

# ============================================================
# 配置常量
# ============================================================

# OmniRoute 默认配置 (可通过环境变量覆盖)
DEFAULT_BASE_URL = "http://localhost:20128"
DEFAULT_MODEL = "auto"  # OmniRoute 自动选择最优模型
DEFAULT_DEEP_MODEL = "auto"  # 深度思考模式也用 auto
DEFAULT_TIMEOUT = 30  # 超时秒数
DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 2000
DEFAULT_MAX_TOKENS_DEEP = 4000

# 健康检查
HEALTH_CHECK_ENDPOINT = "/health"
HEALTH_CHECK_TIMEOUT = 5

# 熔断配置 (避免 OmniRoute 挂掉时反复尝试)
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 3
CIRCUIT_BREAKER_COOLDOWN_SEC = 60  # 1 分钟冷却


# ============================================================
# 异常定义
# ============================================================


def _safe_urlopen(req, timeout=None):
    """兼容入口: 转发到全项目唯一收口实现 `utils.safe_url.safe_urlopen`.

    2026-09-12 (Issue #30 二次复扫) 修两个问题:
    ① 原 `timeout is not None` 分支递归调用**自己**而非 urllib —— 真会
       死循环/触发 RecursionError (本模块此前无调用方, 缺陷长期潜伏);
    ② 这是本文件内的**第三份** scheme 校验副本, 现统一委托收口实现。
    """
    return safe_urlopen(req, timeout=timeout)


class OmniRouteError(Exception):
    """OmniRoute 基础异常."""


class OmniRouteNotAvailableError(OmniRouteError):
    """OmniRoute 不可用 (未启动/配置错误)."""


class OmniRouteCircuitOpenError(OmniRouteError):
    """熔断器打开 (连续失败后冷却期)."""


# ============================================================
# OmniRouteClient 主类
# ============================================================


class OmniRouteClient:
    """OmniRoute 网关客户端 (单例, 线程安全).

    封装 OmniRoute 的 OpenAI 兼容 API, 提供:
        - chat(): 普通对话 (model="auto")
        - chat_deep(): 深度思考模式
        - health_check(): 连通性探测
        - list_models(): 获取可用模型列表

    设计原则:
        - Feature Flag 控制: USE_OMNIROUTE=False 时所有调用返回 None
        - 熔断器: 连续失败 N 次后进入冷却期, 避免浪费时间
        - 完全向后兼容: 不修改任何现有代码路径
    """

    _instance: OmniRouteClient | None = None
    _lock: threading.RLock = threading.RLock()

    def __init__(self) -> None:
        # 从环境变量加载配置
        self._base_url = os.environ.get("OMNIROUTE_BASE_URL", DEFAULT_BASE_URL).rstrip(
            "/"
        )
        self._model = os.environ.get("OMNIROUTE_MODEL", DEFAULT_MODEL)
        self._deep_model = os.environ.get("OMNIROUTE_DEEP_MODEL", DEFAULT_DEEP_MODEL)
        self._timeout = int(os.environ.get("OMNIROUTE_TIMEOUT", str(DEFAULT_TIMEOUT)))
        self._default_temperature = float(
            os.environ.get("OMNIROUTE_TEMPERATURE", str(DEFAULT_TEMPERATURE))
        )
        self._default_max_tokens = int(
            os.environ.get("OMNIROUTE_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))
        )
        self._default_max_tokens_deep = int(
            os.environ.get("OMNIROUTE_MAX_TOKENS_DEEP", str(DEFAULT_MAX_TOKENS_DEEP))
        )

        # Feature Flag
        self._feature_flag = os.environ.get("USE_OMNIROUTE", "True").lower() in (
            "true",
            "1",
            "yes",
        )

        # 熔断器状态
        self._failure_count = 0
        self._circuit_open_since: float | None = None
        self._circuit_lock = threading.Lock()

        logger.info(
            "OmniRouteClient 初始化: base_url=%s, model=%s, enabled=%s",
            self._base_url,
            self._model,
            self._feature_flag,
        )

    @classmethod
    def get_instance(cls) -> OmniRouteClient:
        """获取单例 (线程安全)."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例 (仅测试用)."""
        with cls._lock:
            cls._instance = None

    # ============================================================
    # 公共 API
    # ============================================================

    @property
    def enabled(self) -> bool:
        """是否启用 OmniRoute (Feature Flag)."""
        return self._feature_flag

    def chat(
        self,
        prompt: str,
        system: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str | None:
        """普通对话 (通过 OmniRoute 路由).

        Args:
            prompt: 用户提示词
            system: 系统提示词 (可选)
            temperature: 温度参数 (默认 0.3)
            max_tokens: 最大 token 数 (默认 2000)

        Returns:
            AI 回复文本, 失败/未启用时返回 None
        """
        if not self._check_ready():
            return None

        temp = temperature if temperature is not None else self._default_temperature
        tokens = max_tokens if max_tokens is not None else self._default_max_tokens

        return self._call_omniroute(
            model=self._model,
            prompt=prompt,
            system=system,
            temperature=temp,
            max_tokens=tokens,
            timeout=self._timeout,
        )

    def chat_deep(
        self,
        prompt: str,
        system: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str | None:
        """深度思考模式 (长上下文, 高 token 上限).

        Args:
            prompt: 用户提示词
            system: 系统提示词
            temperature: 温度参数 (默认 0.3)
            max_tokens: 最大 token 数 (默认 4000)

        Returns:
            AI 回复文本 (含思考过程摘要), 失败时返回 None
        """
        if not self._check_ready():
            return None

        temp = temperature if temperature is not None else self._default_temperature
        tokens = max_tokens if max_tokens is not None else self._default_max_tokens_deep

        return self._call_omniroute(
            model=self._deep_model,
            prompt=prompt,
            system=system,
            temperature=temp,
            max_tokens=tokens,
            timeout=self._timeout * 2,  # 深度模式加倍超时
        )

    def health_check(self) -> dict[str, Any]:
        """连通性探测.

        Returns:
            {
                "enabled": True/False,
                "available": True/False,
                "base_url": "http://localhost:20128",
                "latency_ms": 12.3,
                "error": "..." (失败时)
            }
        """
        result: dict[str, Any] = {
            "enabled": self._feature_flag,
            "available": False,
            "base_url": self._base_url,
            "latency_ms": 0.0,
            "error": "",
        }

        if not self._feature_flag:
            result["error"] = "feature_flag_disabled (USE_OMNIROUTE=False)"
            return result

        start_ts = time.perf_counter()
        try:
            url = self._base_url + HEALTH_CHECK_ENDPOINT
            req = urllib.request.Request(url, method="GET")
            with _safe_urlopen(req, timeout=HEALTH_CHECK_TIMEOUT) as resp:
                if 200 <= resp.status < 300:
                    result["available"] = True
                    result["latency_ms"] = (time.perf_counter() - start_ts) * 1000.0
                else:
                    result["error"] = f"HTTP {resp.status}"
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # noqa: BLE001
            result["error"] = f"{type(e).__name__}: {str(e)[:200]}"

        return result

    def list_models(self) -> list[dict[str, Any]]:
        """获取 OmniRoute 可用模型列表.

        Returns:
            模型信息列表, 失败时返回空列表
        """
        if not self._check_ready():
            return []

        try:
            url = self._base_url + "/v1/models"
            req = urllib.request.Request(url, method="GET")
            with _safe_urlopen(req, timeout=self._timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return body.get("data", [])
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # noqa: BLE001
            logger.warning("OmniRoute list_models 失败: %s", e)
            return []

    # ============================================================
    # 内部: 就绪检查 + 熔断器
    # ============================================================

    def _check_ready(self) -> bool:
        """检查是否就绪 (Feature Flag + 熔断器).

        Returns:
            True 表示可以发送请求
        """
        if not self._feature_flag:
            return False

        # 检查熔断器
        with self._circuit_lock:
            if self._circuit_open_since is not None:
                elapsed = time.time() - self._circuit_open_since
                if elapsed < CIRCUIT_BREAKER_COOLDOWN_SEC:
                    logger.debug(
                        "OmniRoute 熔断器打开中 (已冷却 %.0fs/%.0fs)",
                        elapsed,
                        CIRCUIT_BREAKER_COOLDOWN_SEC,
                    )
                    return False
                # 冷却期结束, 重置熔断器 (半开状态)
                logger.info("OmniRoute 熔断器冷却期结束, 重置")
                self._circuit_open_since = None
                self._failure_count = 0

        return True

    def _record_success(self) -> None:
        """记录成功, 重置熔断器计数."""
        with self._circuit_lock:
            self._failure_count = 0
            self._circuit_open_since = None

    def _record_failure(self) -> None:
        """记录失败, 触发熔断器检查."""
        with self._circuit_lock:
            self._failure_count += 1
            if self._failure_count >= CIRCUIT_BREAKER_FAILURE_THRESHOLD:
                if self._circuit_open_since is None:
                    logger.warning(
                        "OmniRoute 连续失败 %d 次, 熔断器打开 (冷却 %ds)",
                        self._failure_count,
                        CIRCUIT_BREAKER_COOLDOWN_SEC,
                    )
                    self._circuit_open_since = time.time()

    # ============================================================
    # 内部: 核心调用
    # ============================================================

    def _call_omniroute(
        self,
        model: str,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> str | None:
        """调用 OmniRoute 的 OpenAI 兼容 /chat/completions 端点.

        Returns:
            回复文本, 失败时返回 None
        """
        try:
            url = self._base_url + "/v1/chat/completions"
            headers = {
                "Content-Type": "application/json",
                # OmniRoute 本身不需要 API Key, 但为了兼容 OpenAI SDK 可以传任意值
                "Authorization": "Bearer omniroute",
            }

            messages: list[dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            payload = json.dumps(
                {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": False,
                }
            ).encode("utf-8")

            start_ts = time.perf_counter()
            req = urllib.request.Request(
                url, data=payload, headers=headers, method="POST"
            )
            with _safe_urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))

            latency_ms = (time.perf_counter() - start_ts) * 1000.0

            # 解析响应 (OpenAI 兼容格式)
            choice = body.get("choices", [{}])[0]
            message = choice.get("message", {})
            content = message.get("content")

            # 某些模型返回 reasoning_content
            if not content:
                content = message.get("reasoning_content")

            if isinstance(content, str) and content:
                self._record_success()
                logger.info(
                    "OmniRoute 调用成功: model=%s, latency=%.0fms, tokens_in=%d, tokens_out=%d",
                    model,
                    latency_ms,
                    body.get("usage", {}).get("prompt_tokens", 0),
                    body.get("usage", {}).get("completion_tokens", 0),
                )

                # 如果有 reasoning_content, 附加上
                reasoning = message.get("reasoning_content", "")
                if reasoning and len(reasoning) > 50 and reasoning != content:
                    return f"{content.strip()}\n\n---\n_思考过程：{reasoning.strip()[:500]}_"
                return content.strip()

            logger.warning("OmniRoute 返回空内容: %s", str(body)[:300])
            self._record_failure()
            return None

        except urllib.error.HTTPError as e:
            logger.warning("OmniRoute HTTP 错误: %d %s", e.code, str(e)[:200])
            self._record_failure()
            return None
        except urllib.error.URLError as e:
            logger.warning("OmniRoute 连接失败: %s", str(e)[:200])
            self._record_failure()
            return None
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # noqa: BLE001
            logger.warning("OmniRoute 调用异常: %s: %s", type(e).__name__, str(e)[:200])
            self._record_failure()
            return None


# ============================================================
# 模块级快捷函数 (推荐业务代码使用)
# ============================================================


def chat(
    prompt: str,
    system: str = "",
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str | None:
    """快捷函数: 通过 OmniRoute 对话.

    Usage:
        >>> from utils.alpha.omni_route_client import chat
        >>> reply = chat("分析市场趋势")
    """
    return OmniRouteClient.get_instance().chat(prompt, system, temperature, max_tokens)


def chat_deep(
    prompt: str,
    system: str = "",
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str | None:
    """快捷函数: 通过 OmniRoute 深度思考模式."""
    return OmniRouteClient.get_instance().chat_deep(
        prompt, system, temperature, max_tokens
    )


def health_check() -> dict[str, Any]:
    """快捷函数: OmniRoute 连通性探测."""
    return OmniRouteClient.get_instance().health_check()


def is_available() -> bool:
    """快捷函数: 检查 OmniRoute 是否可用 (启用 + 连通)."""
    hc = health_check()
    return hc.get("available", False)
