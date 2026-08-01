# -*- coding: utf-8 -*-
"""LLMRouter 主类 — 薄外壳 + fallback 链 + 审计调度.

从原 `utils/alpha/llm_router.py` 拆出 (B3.4.3), 仅保留:
    - 配置加载 (走 ConfigManager, 满足 HC-5)
    - Provider 注册机制
    - Fallback 链执行 (_chat_with_fallback)
    - Feature Flag 透传调度 (HC-1)
    - 审计日志调度
    - 连通性探测 / provider 列表
    - `_call_*` 薄代理方法 (委托到 providers/, 保持测试兼容)

实际 provider 调用逻辑已迁移到 `utils/alpha/llm/providers/`。
"""

from __future__ import annotations

import copy
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 复用 ConfigManager 4 级优先级 (HC-5)
from utils.config_manager import get_config

# 复用 Feature Flag 框架
from utils.infra.feature_flags import is_enabled

# 子模块
from utils.alpha.llm.audit import write_audit_log
from utils.alpha.llm.base import (
    AllProvidersFailedError,
    CallRecord,
    ProviderFn,
    _AUDIT_LOG_DIR,
    _PROJECT_ROOT,
)
from utils.alpha.llm.passthrough import passthrough_deep_to_legacy, passthrough_to_legacy
from utils.alpha.llm.providers import (
    call_deepseek,
    call_deepseek_reasoner,
    call_doubao,
    call_glm,
    call_ollama,
    call_ollama_deep,
    call_omniroute,
    call_siliconflow,
)

logger = logging.getLogger("llm_router")


class LLMRouter:
    """多模型 LLM 路由器 (单例, 线程安全).

    5 个 Provider fallback 链 (DeepSeek 优先):
        1. deepseek (DeepSeek V3/R1) — 主 LLM, 所有 AI 决策默认走此通道
        2. doubao (豆包 Speed, 火山引擎 Ark) — 备 1
        3. glm (智谱 GLM-5) — 备 2
        4. siliconflow (SiliconFlow) — 备 3
        5. ollama (Ollama 本地) — 兜底

    Feature Flag:
        USE_LLM_REPORT_ANALYZER=False (默认): 透传到旧 llm_client.chat() (HC-1)
        USE_LLM_REPORT_ANALYZER=True: 启用新 LLMRouter

    Usage:
        >>> router = LLMRouter.get_instance()
        >>> reply = router.chat("你好")
        >>> if reply:
        ...     print(reply)
    """

    _instance: Optional["LLMRouter"] = None
    _lock: threading.RLock = threading.RLock()

    def __init__(self) -> None:
        self._config: Dict[str, Any] = {}
        self._settings: Dict[str, Any] = {}
        self._providers_config: Dict[str, Dict[str, Any]] = {}
        self._fallback_chain: List[str] = []
        self._provider_fns: Dict[str, ProviderFn] = {}
        self._audit_log_dir: Path = _AUDIT_LOG_DIR
        self._audit_log_enabled: bool = True
        self._feature_flag_name: str = "USE_LLM_REPORT_ANALYZER"
        self._passthrough_module: str = "llm_client"
        self._passthrough_function: str = "chat"
        self._default_timeout: int = 5
        self._ollama_timeout: int = 30
        self._default_temperature: float = 0.3
        self._default_max_tokens: int = 2000
        self._max_retries: int = 1
        self._retry_delay: float = 1.0
        self._silent_fallback: bool = True

        self._load_config()
        self._register_default_providers()

    @classmethod
    def get_instance(cls) -> "LLMRouter":
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
    # 配置加载 (走 ConfigManager, 满足 HC-5)
    # ============================================================
    def _load_config(self) -> None:
        """从 ConfigManager 加载 llm_router.yaml (4 级优先级).

        注意: 使用 deepcopy 避免修改 ConfigManager 缓存的字典 (测试安全).
        """
        # 检查 OmniRoute Feature Flag (独立于 llm_router 的 flag)
        use_omniroute = os.environ.get("USE_OMNIROUTE", "True").lower() in ("true", "1", "yes")

        cfg = get_config("llm_router", default={})
        if not cfg:
            logger.warning("llm_router.yaml 未找到, 使用默认配置")
            self._settings = {}
            self._providers_config = {}
            chain = ["deepseek", "doubao", "glm", "siliconflow", "ollama"]
            if use_omniroute:
                chain.insert(0, "omniroute")
            self._fallback_chain = chain
            return

        # 深拷贝, 避免测试中修改 _providers_config 污染 ConfigManager 缓存
        cfg = copy.deepcopy(cfg)
        self._config = cfg
        self._settings = cfg.get("settings", {}) or {}
        self._providers_config = cfg.get("providers", {}) or {}

        # fallback 链
        self._fallback_chain = list(
            self._settings.get("fallback_chain", ["deepseek", "doubao", "glm", "siliconflow", "ollama"])
        )
        if use_omniroute and "omniroute" not in self._fallback_chain:
            self._fallback_chain.insert(0, "omniroute")

        # 超时
        self._default_timeout = int(self._settings.get("default_timeout_seconds", 5))
        self._ollama_timeout = int(self._settings.get("ollama_timeout_seconds", 30))

        # 生成参数
        self._default_temperature = float(self._settings.get("default_temperature", 0.3))
        self._default_max_tokens = int(self._settings.get("default_max_tokens", 2000))

        # 审计日志
        self._audit_log_enabled = bool(self._settings.get("audit_log_enabled", True))
        audit_dir_str = self._settings.get("audit_log_dir", "reports/llm_router")
        self._audit_log_dir = Path(audit_dir_str)
        if not self._audit_log_dir.is_absolute():
            self._audit_log_dir = _PROJECT_ROOT / self._audit_log_dir

        # 重试
        self._max_retries = int(self._settings.get("max_retries", 1))
        self._retry_delay = float(self._settings.get("retry_delay_seconds", 1.0))

        # 静默降级
        self._silent_fallback = bool(self._settings.get("silent_fallback", True))

        # Feature Flag
        self._feature_flag_name = self._settings.get("feature_flag_name", "USE_LLM_REPORT_ANALYZER")
        self._passthrough_module = self._settings.get("passthrough_module", "llm_client")
        self._passthrough_function = self._settings.get("passthrough_function", "chat")

        logger.info(
            "LLMRouter 配置加载: chain=%s, timeout=%ds/%ds, flag=%s, audit=%s",
            self._fallback_chain,
            self._default_timeout,
            self._ollama_timeout,
            self._feature_flag_name,
            self._audit_log_enabled,
        )

    def reload(self) -> None:
        """重新加载配置 (热加载)."""
        with self._lock:
            self._load_config()
            self._register_default_providers()
            logger.info("LLMRouter 重新加载完成")

    # ============================================================
    # Provider 注册
    # ============================================================
    def register_provider(self, name: str, provider_fn: ProviderFn) -> None:
        """注册 provider 函数.

        Args:
            name: provider 名称 (如 "doubao", "glm")
            provider_fn: 调用函数 (prompt, system, temperature, max_tokens, timeout) -> Optional[str]
        """
        with self._lock:
            self._provider_fns[name] = provider_fn
            logger.debug("Provider 注册: %s", name)

    def _register_default_providers(self) -> None:
        """注册默认 provider (OmniRoute P0 优先, 然后 DeepSeek 优先)."""
        self._provider_fns = {}
        # OmniRoute: P0 最高优先级 (Feature Flag 控制, 默认关闭)
        self.register_provider("omniroute", self._call_omniroute)
        self.register_provider("deepseek", self._call_deepseek)
        self.register_provider("doubao", self._call_doubao)
        self.register_provider("glm", self._call_glm)
        self.register_provider("siliconflow", self._call_siliconflow)
        self.register_provider("ollama", self._call_ollama)

    # ============================================================
    # 公开 API
    # ============================================================
    def chat(
        self,
        prompt: str,
        system: str = "",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Optional[str]:
        """多模型 fallback 对话.

        Feature Flag 透传 (HC-1):
            - USE_LLM_REPORT_ANALYZER=False: 透传到旧 llm_client.chat()
            - USE_LLM_REPORT_ANALYZER=True: 启用新 LLMRouter

        Args:
            prompt: 用户提示词
            system: 系统提示词 (可选)
            temperature: 温度参数 (默认 0.3)
            max_tokens: 最大 token 数 (默认 2000)

        Returns:
            AI 回复文本, 全部失败时返回 None (silent_fallback=True) 或抛异常

        Raises:
            AllProvidersFailedError: 所有 provider 失败且 silent_fallback=False
        """
        # Feature Flag 透传 (HC-1)
        if not is_enabled(self._feature_flag_name):
            return passthrough_to_legacy(
                prompt, system, temperature, max_tokens,
                passthrough_module=self._passthrough_module,
                passthrough_function=self._passthrough_function,
            )

        # flag 开启: 走新路由
        temp = temperature if temperature is not None else self._default_temperature
        tokens = max_tokens if max_tokens is not None else self._default_max_tokens

        return self._chat_with_fallback(prompt, system, temp, tokens)

    def chat_deep(
        self,
        prompt: str,
        system: str = "",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Optional[str]:
        """深度思考模式 (主: DeepSeek R1 / 备: Ollama deepseek-r1:14b).

        适用于复杂决策分析 (对冲/仓位/多标的联动).

        Args:
            prompt: 用户提示词
            system: 系统提示词
            temperature: 温度参数 (默认 0.3)
            max_tokens: 最大 token 数 (默认 4000)

        Returns:
            AI 回复文本 (含推理过程), 失败时降级到普通 chat
        """
        if not is_enabled(self._feature_flag_name):
            # 透传: 尝试旧 llm_client.chat_deep
            return passthrough_deep_to_legacy(
                prompt, system,
                passthrough_module=self._passthrough_module,
                passthrough_function=self._passthrough_function,
            )

        # 深度模式: 优先 DeepSeek R1 云端推理
        temp = temperature if temperature is not None else self._default_temperature
        tokens = max_tokens if max_tokens is not None else 4000

        result = self._call_deepseek_reasoner(prompt, system, temp, tokens)
        if result:
            return result

        # 备用: Ollama deep model (本地 deepseek-r1:14b)
        result = self._call_ollama_deep(prompt, system, temp, tokens)
        if result:
            return result

        # 兜底降级到普通 chat
        logger.info("chat_deep 降级到普通 chat")
        return self.chat(prompt, system, temp, tokens)

    def test_connection(self) -> Dict[str, Any]:
        """连通性探测.

        Returns:
            {
                "providers": {"doubao": True/False, ...},
                "available": "doubao" / "glm" / ...,
                "status": "ok" / "degraded"
            }
        """
        providers_status: Dict[str, bool] = {}
        available: Optional[str] = None

        for name in self._fallback_chain:
            provider_cfg = self._providers_config.get(name, {})
            if not provider_cfg.get("enabled", True):
                providers_status[name] = False
                continue

            # 检查 API Key 是否存在
            api_key_env = provider_cfg.get("api_key_env", "")
            if api_key_env:
                has_key = bool(os.environ.get(api_key_env, ""))
            else:
                has_key = True  # Ollama 不需要 key

            providers_status[name] = has_key

            if has_key and available is None:
                # 尝试 ping
                try:
                    fn = self._provider_fns.get(name)
                    if fn:
                        result = fn("ping", "", 0.1, 10, self._default_timeout)
                        if result is not None:
                            available = name
                except Exception:  # P2 模块 fail-safe, 待后续精确化
                    pass

        return {
            "providers": providers_status,
            "available": available,
            "status": "ok" if available else "degraded",
        }

    def list_providers(self) -> List[Dict[str, Any]]:
        """列出所有已注册 provider (审计用)."""
        result = []
        for name in self._fallback_chain:
            cfg = self._providers_config.get(name, {})
            api_key_env = cfg.get("api_key_env", "")
            has_key = bool(os.environ.get(api_key_env, "")) if api_key_env else True
            result.append(
                {
                    "name": name,
                    "display_name": cfg.get("name", name),
                    "enabled": cfg.get("enabled", True),
                    "api_key_configured": has_key,
                    "timeout_seconds": cfg.get("timeout_seconds", self._default_timeout),
                    "in_fallback_chain": name in self._fallback_chain,
                }
            )
        return result

    # ============================================================
    # 内部: fallback 链执行
    # ============================================================
    def _chat_with_fallback(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> Optional[str]:
        """执行 fallback 链."""
        tried_providers: List[str] = []
        last_error: Optional[Exception] = None

        for name in self._fallback_chain:
            provider_cfg = self._providers_config.get(name, {})
            if not provider_cfg.get("enabled", True):
                logger.debug("Provider 已禁用, 跳过: %s", name)
                continue

            tried_providers.append(name)
            fn = self._provider_fns.get(name)
            if fn is None:
                logger.warning("Provider 函数未注册: %s", name)
                continue

            # 超时
            timeout = provider_cfg.get("timeout_seconds", self._default_timeout)
            if name == "ollama":
                timeout = self._ollama_timeout

            # 调用
            start_ts = time.perf_counter()
            try:
                result = fn(prompt, system, temperature, max_tokens, timeout)
                latency_ms = (time.perf_counter() - start_ts) * 1000.0

                if result is not None:
                    # 成功
                    write_audit_log(
                        CallRecord(
                            timestamp=datetime.utcnow().isoformat() + "Z",
                            prompt=prompt,
                            system=system,
                            provider=name,
                            success=True,
                            latency_ms=latency_ms,
                            response_preview=result[:200],
                        ),
                        audit_log_dir=self._audit_log_dir,
                        enabled=self._audit_log_enabled,
                    )
                    logger.info(
                        "LLMRouter 调用成功: provider=%s, latency=%.0fms",
                        name,
                        latency_ms,
                    )
                    return result
                else:
                    # 返回 None (软失败)
                    write_audit_log(
                        CallRecord(
                            timestamp=datetime.utcnow().isoformat() + "Z",
                            prompt=prompt,
                            system=system,
                            provider=name,
                            success=False,
                            latency_ms=latency_ms,
                            error_type="SoftFailure",
                            error_message="Provider returned None",
                        ),
                        audit_log_dir=self._audit_log_dir,
                        enabled=self._audit_log_enabled,
                    )
                    logger.warning(
                        "LLMRouter provider 软失败: %s (latency=%.0fms)",
                        name,
                        latency_ms,
                    )
            except Exception as e:  # P2 模块 fail-safe, 待后续精确化
                latency_ms = (time.perf_counter() - start_ts) * 1000.0
                last_error = e
                write_audit_log(
                    CallRecord(
                        timestamp=datetime.utcnow().isoformat() + "Z",
                        prompt=prompt,
                        system=system,
                        provider=name,
                        success=False,
                        latency_ms=latency_ms,
                        error_type=type(e).__name__,
                        error_message=str(e),
                    ),
                    audit_log_dir=self._audit_log_dir,
                    enabled=self._audit_log_enabled,
                )
                logger.warning(
                    "LLMRouter provider 异常: %s (%s: %s), latency=%.0fms",
                    name,
                    type(e).__name__,
                    str(e)[:100],
                    latency_ms,
                )
                continue

        # 所有 provider 失败
        if self._silent_fallback:
            logger.warning(
                "LLMRouter 所有 provider 失败 (静默降级返回 None): tried=%s",
                tried_providers,
            )
            return None
        else:
            raise AllProvidersFailedError(
                f"所有 provider 失败: tried={tried_providers}, last_error={last_error}",
                tried_providers=tried_providers,
                last_error=last_error,
            )

    # ============================================================
    # Provider 薄代理方法 (委托到 providers/, 保持测试兼容)
    # ============================================================
    # 说明: 测试文件直接调用 router._call_doubao() 等方法,
    # 所以这些方法必须保留, 但实现只是一行委托。
    # ============================================================
    def _call_omniroute(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> Optional[str]:
        """代理: OmniRoute 网关 (P0 最高优先级)."""
        return call_omniroute(prompt, system, temperature, max_tokens, timeout)

    def _call_deepseek(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> Optional[str]:
        """代理: DeepSeek V3 (deepseek-chat) — 主 LLM."""
        cfg = self._providers_config.get("deepseek", {})
        return call_deepseek(
            prompt, system, temperature, max_tokens, timeout, cfg,
            max_retries=self._max_retries,
            retry_delay=self._retry_delay,
        )

    def _call_deepseek_reasoner(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> Optional[str]:
        """代理: DeepSeek R1 (deepseek-reasoner) 云端推理模型."""
        cfg = self._providers_config.get("deepseek", {})
        return call_deepseek_reasoner(prompt, system, temperature, max_tokens, cfg)

    def _call_doubao(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> Optional[str]:
        """代理: 豆包 Speed (火山引擎 Ark) — OpenAI 兼容接口."""
        cfg = self._providers_config.get("doubao", {})
        return call_doubao(
            prompt, system, temperature, max_tokens, timeout, cfg,
            max_retries=self._max_retries,
            retry_delay=self._retry_delay,
        )

    def _call_glm(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> Optional[str]:
        """代理: 智谱 GLM-5 — OpenAI 兼容接口."""
        cfg = self._providers_config.get("glm", {})
        return call_glm(
            prompt, system, temperature, max_tokens, timeout, cfg,
            max_retries=self._max_retries,
            retry_delay=self._retry_delay,
        )

    def _call_siliconflow(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> Optional[str]:
        """代理: SiliconFlow — OpenAI 兼容接口."""
        cfg = self._providers_config.get("siliconflow", {})
        return call_siliconflow(
            prompt, system, temperature, max_tokens, timeout, cfg,
            max_retries=self._max_retries,
            retry_delay=self._retry_delay,
        )

    def _call_ollama(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> Optional[str]:
        """代理: Ollama 本地 — OpenAI 兼容接口."""
        cfg = self._providers_config.get("ollama", {})
        return call_ollama(
            prompt, system, temperature, max_tokens, timeout, cfg,
            max_retries=self._max_retries,
            retry_delay=self._retry_delay,
        )

    def _call_ollama_deep(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> Optional[str]:
        """代理: Ollama 深度推理模型 (deepseek-r1:14b)."""
        cfg = self._providers_config.get("ollama", {})
        return call_ollama_deep(
            prompt, system, temperature, max_tokens, cfg,
            ollama_timeout=self._ollama_timeout,
        )

    def _openai_compatible_chat(
        self,
        base_url: str,
        api_key: str,
        model: str,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> Optional[str]:
        """代理: OpenAI 兼容 chat/completions 调用 (保持测试兼容)."""
        from utils.alpha.llm.openai_compat import openai_compatible_chat

        return openai_compatible_chat(
            base_url=base_url,
            api_key=api_key,
            model=model,
            prompt=prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=self._max_retries,
            retry_delay=self._retry_delay,
        )


# ============================================================
# 模块级快捷函数 (推荐业务代码使用)
# ============================================================


def chat(
    prompt: str,
    system: str = "",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Optional[str]:
    """快捷函数: 多模型 fallback 对话.

    Usage:
        >>> from utils.alpha.llm_router import chat
        >>> reply = chat("你好")
    """
    return LLMRouter.get_instance().chat(prompt, system, temperature, max_tokens)


def chat_deep(
    prompt: str,
    system: str = "",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Optional[str]:
    """快捷函数: 深度思考模式 (主: DeepSeek R1 / 备: Ollama deepseek-r1:14b)."""
    return LLMRouter.get_instance().chat_deep(prompt, system, temperature, max_tokens)


def test_connection() -> Dict[str, Any]:
    """快捷函数: 连通性探测."""
    return LLMRouter.get_instance().test_connection()


def list_providers() -> List[Dict[str, Any]]:
    """快捷函数: 列出所有 provider (审计用)."""
    return LLMRouter.get_instance().list_providers()


def reload() -> None:
    """快捷函数: 重新加载配置 (热加载)."""
    LLMRouter.get_instance().reload()


__all__ = [
    "LLMRouter",
    "chat",
    "chat_deep",
    "test_connection",
    "list_providers",
    "reload",
]
