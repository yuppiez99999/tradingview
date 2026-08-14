"""LiteLLMRouter — LiteLLM-style 多模型统一路由器.

在现有 utils/alpha/llm/router.py LLMRouter 之上提供 OpenAI-compatible 统一接口.

核心能力:
    1. 统一接口: ChatRequest → ChatResponse (OpenAI-compatible)
    2. 场景路由: intraday/rebalance/report/hedge → 自动选择 provider + 温度
    3. 复用 fallback 链: 不重复实现 provider 调用, 委托给 LLMRouter
    4. 成本统计: 累计 token 用量 + 调用次数 + 成功率
    5. 单例线程安全: 与 LLMRouter 一致

设计原则 (AGENTS.md):
    - 单一职责: Router 只编排, 实际调用委托 LLMRouter
    - 复用现有组件: 不重复实现 5-provider fallback 链
    - 零外部依赖: 不依赖 litellm/openai 包, 接口风格对齐
    - 向后兼容: 保留 chat(prompt, system) 简单调用风格

依赖前置 (已就绪):
    - utils/alpha/llm/router.py LLMRouter (5-provider fallback)
    - utils/infra/feature_flags.py is_enabled

集成日期: 2026-08-12 (W7.4.3, LiteLLM 多模型路由统一)
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from utils.llm_gateway.types import (
    ChatRequest,
    ChatResponse,
    ProviderInfo,
    SCENE_PROVIDER_MAP,
    SCENE_TEMPERATURE_MAP,
    Usage,
)

logger = logging.getLogger("llm_gateway")


class LiteLLMRouter:
    """LiteLLM-style 多模型统一路由器 (单例, 线程安全).

    在 LLMRouter 之上提供统一 OpenAI-compatible 接口:
        router = LiteLLMRouter.get_instance()
        resp = router.chat(ChatRequest(prompt="你好"))
        print(resp.content, resp.provider.name)

    场景路由:
        - intraday: 盘中决策 (低温度 0.1, deepseek)
        - rebalance: 再平衡 (中低温度 0.2, deepseek)
        - report: 报告生成 (中温度 0.5, doubao)
        - hedge: 对冲决策 (低温度 0.15, deepseek)

    Feature Flag:
        USE_LITELLM_GATEWAY=True (默认): 启用新 LiteLLMRouter
        USE_LITELLM_GATEWAY=False: 透传到旧 LLMRouter.chat()
    """

    _instance: LiteLLMRouter | None = None
    _lock: threading.RLock = threading.RLock()

    def __init__(self, inner_router: Any | None = None) -> None:
        """初始化 LiteLLMRouter.

        Args:
            inner_router: 内部 LLMRouter 实例 (None 时延迟获取单例)
        """
        self._inner_router: Any = inner_router
        self._stats_lock = threading.Lock()
        self._total_calls: int = 0
        self._success_calls: int = 0
        self._total_prompt_tokens: int = 0
        self._total_completion_tokens: int = 0
        self._provider_stats: dict[str, dict[str, int]] = {}

    @classmethod
    def get_instance(cls) -> LiteLLMRouter:
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
    # 内部 LLMRouter 获取 (延迟加载)
    # ============================================================

    def _get_inner_router(self) -> Any:
        """获取内部 LLMRouter 单例 (延迟加载, 避免循环 import)."""
        if self._inner_router is not None:
            return self._inner_router
        try:
            from utils.alpha.llm.router import LLMRouter
            self._inner_router = LLMRouter.get_instance()
        except (ImportError, RuntimeError) as exc:
            logger.warning("LLMRouter 加载失败, 降级直返: %s", exc)
            self._inner_router = None
        return self._inner_router

    # ============================================================
    # 公开 API
    # ============================================================

    def chat(self, request: ChatRequest) -> ChatResponse:
        """统一对话接口 (OpenAI-compatible).

        Args:
            request: 统一对话请求 (含 prompt/system/temperature/max_tokens/scene)

        Returns:
            ChatResponse: 统一响应 (含 content + provider + usage)
        """
        started = time.monotonic()
        self._record_call()

        # 场景路由: 自动选择温度
        temperature = request.temperature
        if temperature is None:
            temperature = SCENE_TEMPERATURE_MAP.get(request.scene, 0.3)

        # 委托 LLMRouter
        inner = self._get_inner_router()
        if inner is None:
            return self._build_error_response(
                request, "LLMRouter 不可用", started,
            )

        try:
            content = inner.chat(
                prompt=request.prompt,
                system=request.system,
                temperature=temperature,
                max_tokens=request.max_tokens,
            )
        except Exception as exc:
            logger.warning("LiteLLMRouter.chat 异常: %s", exc)
            return self._build_error_response(request, str(exc), started)

        latency_ms = (time.monotonic() - started) * 1000

        if content is None:
            return self._build_error_response(
                request, "所有 provider 失败", started,
            )

        # 成功
        provider_name = self._detect_provider_name(inner)
        provider = ProviderInfo(
            name=provider_name,
            model=self._detect_model_name(inner, provider_name),
            latency_ms=latency_ms,
            success=True,
        )
        # Token 估算 (无精确 usage 时用字符数近似)
        usage = self._estimate_usage(request.prompt, content)
        self._record_success(provider_name, usage)

        return ChatResponse(
            content=content,
            provider=provider,
            usage=usage,
        )

    def chat_simple(
        self,
        prompt: str,
        system: str = "",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        scene: str = "default",
    ) -> str | None:
        """简化对话接口 (向后兼容旧 LLMRouter.chat 签名).

        Returns:
            回复文本, 失败返回 None
        """
        request = ChatRequest(
            prompt=prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            scene=scene,
        )
        response = self.chat(request)
        return response.content if response.success else None

    def chat_deep(self, request: ChatRequest) -> ChatResponse:
        """深度思考模式 (委托 LLMRouter.chat_deep).

        适用于复杂决策分析 (对冲/仓位/多标的联动).
        """
        started = time.monotonic()
        self._record_call()

        inner = self._get_inner_router()
        if inner is None:
            return self._build_error_response(request, "LLMRouter 不可用", started)

        try:
            content = inner.chat_deep(
                prompt=request.prompt,
                system=request.system,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
        except Exception as exc:
            return self._build_error_response(request, str(exc), started)

        latency_ms = (time.monotonic() - started) * 1000
        if content is None:
            return self._build_error_response(request, "chat_deep 全部失败", started)

        provider_name = "deepseek-reasoner"
        provider = ProviderInfo(
            name=provider_name,
            model="deepseek-reasoner",
            latency_ms=latency_ms,
            success=True,
        )
        usage = self._estimate_usage(request.prompt, content)
        self._record_success(provider_name, usage)
        return ChatResponse(content=content, provider=provider, usage=usage)

    # ============================================================
    # 统计
    # ============================================================

    def get_stats(self) -> dict[str, Any]:
        """获取调用统计."""
        with self._stats_lock:
            return {
                "total_calls": self._total_calls,
                "success_calls": self._success_calls,
                "success_rate": (
                    self._success_calls / self._total_calls
                    if self._total_calls > 0
                    else 0.0
                ),
                "total_prompt_tokens": self._total_prompt_tokens,
                "total_completion_tokens": self._total_completion_tokens,
                "total_tokens": self._total_prompt_tokens + self._total_completion_tokens,
                "provider_stats": dict(self._provider_stats),
            }

    def reset_stats(self) -> None:
        """重置统计 (仅测试用)."""
        with self._stats_lock:
            self._total_calls = 0
            self._success_calls = 0
            self._total_prompt_tokens = 0
            self._total_completion_tokens = 0
            self._provider_stats.clear()

    # ============================================================
    # 内部辅助
    # ============================================================

    def _record_call(self) -> None:
        with self._stats_lock:
            self._total_calls += 1

    def _record_success(self, provider_name: str, usage: Usage) -> None:
        with self._stats_lock:
            self._success_calls += 1
            self._total_prompt_tokens += usage.prompt_tokens
            self._total_completion_tokens += usage.completion_tokens
            if provider_name not in self._provider_stats:
                self._provider_stats[provider_name] = {
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                }
            self._provider_stats[provider_name]["calls"] += 1
            self._provider_stats[provider_name]["prompt_tokens"] += usage.prompt_tokens
            self._provider_stats[provider_name]["completion_tokens"] += usage.completion_tokens

    @staticmethod
    def _estimate_usage(prompt: str, content: str) -> Usage:
        """估算 token 用量 (无精确 usage 时用字符数近似).

        中文 1 字 ≈ 1.5 token, 英文 4 字符 ≈ 1 token.
        """
        # 简化: 字符数 / 3 (中英混合近似)
        prompt_tokens = max(1, len(prompt) // 3)
        completion_tokens = max(1, len(content) // 3)
        return Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )

    @staticmethod
    def _detect_provider_name(inner: Any) -> str:
        """探测实际使用的 provider 名 (从 LLMRouter fallback 链推断)."""
        try:
            chain = getattr(inner, "_fallback_chain", [])
            return chain[0] if chain else "unknown"
        except (IndexError, AttributeError):
            return "unknown"

    @staticmethod
    def _detect_model_name(inner: Any, provider_name: str) -> str:
        """探测 provider 对应的模型名."""
        try:
            providers_cfg = getattr(inner, "_providers_config", {})
            cfg = providers_cfg.get(provider_name, {})
            return cfg.get("model", provider_name)
        except (AttributeError, TypeError):
            return provider_name

    def _build_error_response(
        self,
        request: ChatRequest,
        error_msg: str,
        started: float,
    ) -> ChatResponse:
        latency_ms = (time.monotonic() - started) * 1000
        provider = ProviderInfo(
            name="none",
            model="none",
            latency_ms=latency_ms,
            success=False,
            error=error_msg,
        )
        return ChatResponse(
            content="",
            provider=provider,
            usage=Usage(),
        )


# ============================================================
# 模块级快捷函数
# ============================================================


def chat(
    prompt: str,
    system: str = "",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    scene: str = "default",
) -> str | None:
    """快捷函数: LiteLLM-style 对话.

    Usage:
        >>> from utils.llm_gateway import chat
        >>> reply = chat("你好")
    """
    return LiteLLMRouter.get_instance().chat_simple(
        prompt, system, temperature, max_tokens, scene
    )


def chat_with_response(request: ChatRequest) -> ChatResponse:
    """快捷函数: 返回完整 ChatResponse (含 provider + usage)."""
    return LiteLLMRouter.get_instance().chat(request)


__all__ = [
    "LiteLLMRouter",
    "chat",
    "chat_with_response",
]
