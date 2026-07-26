# -*- coding: utf-8 -*-
"""LLM 多模型路由器 — 终极量化交易系统 8.4 (T2.1).

模块整合 8.4 — ARCHITECTURE §2.3 / ADR-004
任务: T2.1

设计原则:
    1. 4 个 Provider fallback 链: doubao → glm → siliconflow → ollama
    2. 5 秒超时 (云 API) + 静默降级 (HC-2 主路径不阻塞)
    3. 审计日志: 每次调用记录到 reports/llm_router/calls_{date}.jsonl
    4. Feature Flag 透传: USE_LLM_REPORT_ANALYZER=False 时透传到旧 llm_client (HC-1)
    5. ConfigManager 4 级优先级解析 (HC-5)

API:
    from utils.alpha.llm_router import LLMRouter, chat, chat_deep

    # 推荐: 使用快捷函数
    reply = chat("你好")
    deep_reply = chat_deep("复杂决策分析...")

    # 或使用类 (需要自定义配置时)
    router = LLMRouter.get_instance()
    reply = router.chat(prompt="你好", system="你是金融分析师")

硬约束:
    - HC-1: flag=False 时必须透传到旧路径, 行为完全等价
    - HC-2: 主路径调用延迟 <5s (云 API 超时)
    - HC-5: 配置走 ConfigManager 4 级优先级
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# 复用 ConfigManager 4 级优先级 (HC-5)
from utils.config_manager import get_config

# 复用 Feature Flag 框架
from utils.infra.feature_flags import FeatureFlags, is_enabled

logger = logging.getLogger("llm_router")

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 审计日志目录
_AUDIT_LOG_DIR = _PROJECT_ROOT / "reports" / "llm_router"


# ============================================================
# 异常定义
# ============================================================

class LLMRouterError(Exception):
    """LLMRouter 基础异常."""


class AllProvidersFailedError(LLMRouterError):
    """所有 provider 均失败."""

    def __init__(self, message: str, tried_providers: List[str],
                 last_error: Optional[Exception] = None) -> None:
        super().__init__(message)
        self.tried_providers = tried_providers
        self.last_error = last_error


class ProviderNotConfiguredError(LLMRouterError):
    """Provider 未配置 (缺少 API Key)."""


# ============================================================
# 类型定义
# ============================================================

# Provider 调用函数签名: (prompt, system, temperature, max_tokens, timeout) -> Optional[str]
ProviderFn = Callable[..., Optional[str]]


class CallRecord:
    """单次调用记录 (审计日志用)."""

    def __init__(
        self,
        timestamp: str,
        prompt: str,
        system: str,
        provider: str,
        success: bool,
        latency_ms: float,
        error_type: str = "",
        error_message: str = "",
        response_preview: str = "",
    ) -> None:
        self.timestamp = timestamp
        self.prompt = prompt
        self.system = system
        self.provider = provider
        self.success = success
        self.latency_ms = latency_ms
        self.error_type = error_type
        self.error_message = error_message
        self.response_preview = response_preview

    def to_dict(self) -> Dict[str, Any]:
        """转为字典 (JSONL 序列化)."""
        return {
            "timestamp": self.timestamp,
            "prompt_preview": self.prompt[:200],
            "system_preview": self.system[:100],
            "provider": self.provider,
            "success": self.success,
            "latency_ms": round(self.latency_ms, 2),
            "error_type": self.error_type,
            "error_message": self.error_message[:500],
            "response_preview": self.response_preview[:200],
        }


# ============================================================
# LLMRouter 主类
# ============================================================

class LLMRouter:
    """多模型 LLM 路由器 (单例, 线程安全).

    4 个 Provider fallback 链:
        1. doubao (豆包 Speed, 火山引擎 Ark) — 主
        2. glm (智谱 GLM-5) — 备 1
        3. siliconflow (SiliconFlow) — 备 2
        4. ollama (Ollama 本地) — 兜底

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
        """从 ConfigManager 加载 llm_router.yaml (4 级优先级)."""
        cfg = get_config("llm_router", default={})
        if not cfg:
            logger.warning("llm_router.yaml 未找到, 使用默认配置")
            self._settings = {}
            self._providers_config = {}
            self._fallback_chain = ["doubao", "glm", "siliconflow", "ollama"]
            return

        self._config = cfg
        self._settings = cfg.get("settings", {}) or {}
        self._providers_config = cfg.get("providers", {}) or {}

        # fallback 链
        self._fallback_chain = list(self._settings.get("fallback_chain", [
            "doubao", "glm", "siliconflow", "ollama"
        ]))

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
        self._feature_flag_name = self._settings.get(
            "feature_flag_name", "USE_LLM_REPORT_ANALYZER"
        )
        self._passthrough_module = self._settings.get("passthrough_module", "llm_client")
        self._passthrough_function = self._settings.get("passthrough_function", "chat")

        logger.info(
            "LLMRouter 配置加载: chain=%s, timeout=%ds/%ds, flag=%s, audit=%s",
            self._fallback_chain, self._default_timeout, self._ollama_timeout,
            self._feature_flag_name, self._audit_log_enabled,
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
        """注册默认 4 个 provider."""
        self._provider_fns = {}
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
            return self._passthrough_to_legacy(prompt, system, temperature, max_tokens)

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
        """深度思考模式 (使用 Ollama deepseek-r1:14b).

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
            try:
                import importlib
                mod = importlib.import_module(self._passthrough_module)
                fn = getattr(mod, "chat_deep", None)
                if fn:
                    return fn(prompt, system)
                # 没有 chat_deep, 用 chat
                fn = getattr(mod, self._passthrough_function)
                return fn(prompt, system)
            except Exception as e:
                logger.error("透传 chat_deep 失败: %s", e)
                return None

        # 深度模式: 优先 Ollama deep model
        temp = temperature if temperature is not None else self._default_temperature
        tokens = max_tokens if max_tokens is not None else 4000

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
                except Exception:
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
            result.append({
                "name": name,
                "display_name": cfg.get("name", name),
                "enabled": cfg.get("enabled", True),
                "api_key_configured": has_key,
                "timeout_seconds": cfg.get("timeout_seconds", self._default_timeout),
                "in_fallback_chain": name in self._fallback_chain,
            })
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
                    self._write_audit_log(CallRecord(
                        timestamp=datetime.utcnow().isoformat() + "Z",
                        prompt=prompt,
                        system=system,
                        provider=name,
                        success=True,
                        latency_ms=latency_ms,
                        response_preview=result[:200],
                    ))
                    logger.info(
                        "LLMRouter 调用成功: provider=%s, latency=%.0fms",
                        name, latency_ms,
                    )
                    return result
                else:
                    # 返回 None (软失败)
                    self._write_audit_log(CallRecord(
                        timestamp=datetime.utcnow().isoformat() + "Z",
                        prompt=prompt,
                        system=system,
                        provider=name,
                        success=False,
                        latency_ms=latency_ms,
                        error_type="SoftFailure",
                        error_message="Provider returned None",
                    ))
                    logger.warning(
                        "LLMRouter provider 软失败: %s (latency=%.0fms)",
                        name, latency_ms,
                    )
            except Exception as e:
                latency_ms = (time.perf_counter() - start_ts) * 1000.0
                last_error = e
                self._write_audit_log(CallRecord(
                    timestamp=datetime.utcnow().isoformat() + "Z",
                    prompt=prompt,
                    system=system,
                    provider=name,
                    success=False,
                    latency_ms=latency_ms,
                    error_type=type(e).__name__,
                    error_message=str(e),
                ))
                logger.warning(
                    "LLMRouter provider 异常: %s (%s: %s), latency=%.0fms",
                    name, type(e).__name__, str(e)[:100], latency_ms,
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
    # Feature Flag 透传 (HC-1)
    # ============================================================
    def _passthrough_to_legacy(
        self,
        prompt: str,
        system: str,
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> Optional[str]:
        """透传到旧 llm_client (HC-1: flag=False 时的默认行为)."""
        try:
            import importlib
            mod = importlib.import_module(self._passthrough_module)
            fn = getattr(mod, self._passthrough_function, None)
            if fn is None:
                logger.error(
                    "透传失败: %s.%s 不存在",
                    self._passthrough_module, self._passthrough_function,
                )
                return None
            # 旧 llm_client.chat(prompt, system, temperature, max_tokens)
            kwargs: Dict[str, Any] = {}
            if temperature is not None:
                kwargs["temperature"] = temperature
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
            return fn(prompt, system, **kwargs)
        except ImportError as e:
            logger.error("透传失败: 无法导入 %s: %s", self._passthrough_module, e)
            return None
        except Exception as e:
            logger.error("透传调用失败: %s", e)
            return None

    # ============================================================
    # 审计日志
    # ============================================================
    def _write_audit_log(self, record: CallRecord) -> None:
        """写入审计日志 (JSONL 格式, 按日切分)."""
        if not self._audit_log_enabled:
            return

        try:
            self._audit_log_dir.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now().strftime("%Y-%m-%d")
            log_file = self._audit_log_dir / f"calls_{date_str}.jsonl"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("审计日志写入失败: %s", e)

    # ============================================================
    # Provider 实现 (4 个)
    # ============================================================
    def _call_doubao(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> Optional[str]:
        """豆包 Speed (火山引擎 Ark) — OpenAI 兼容接口."""
        cfg = self._providers_config.get("doubao", {})
        base_url = os.environ.get(
            cfg.get("base_url_env", "DOUBAO_SPEED_BASE_URL"),
            cfg.get("base_url_default", "https://ark.cn-beijing.volces.com/api/v3"),
        )
        api_key = os.environ.get(cfg.get("api_key_env", "VOLCENGINE_API_KEY"), "")
        if not api_key:
            return None

        # 优先 endpoint_id, 否则用 model
        endpoint_env = cfg.get("endpoint_id_env", "DOUBAO_ENDPOINT_ID")
        model = os.environ.get(endpoint_env, "") or os.environ.get(
            cfg.get("model_env", "DOUBAO_SPEED_MODEL"),
            cfg.get("model_default", "doubao-1-5-pro-32k-250115"),
        )

        return self._openai_compatible_chat(
            base_url=base_url, api_key=api_key, model=model,
            prompt=prompt, system=system,
            temperature=temperature, max_tokens=max_tokens, timeout=timeout,
        )

    def _call_glm(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> Optional[str]:
        """智谱 GLM-5 — OpenAI 兼容接口."""
        cfg = self._providers_config.get("glm", {})
        base_url = os.environ.get(
            cfg.get("base_url_env", "GLM_BASE_URL"),
            cfg.get("base_url_default", "https://open.bigmodel.cn/api/paas/v4"),
        )
        api_key = os.environ.get(cfg.get("api_key_env", "GLM_API_KEY"), "")
        if not api_key:
            return None

        model = os.environ.get(
            cfg.get("model_env", "GLM_MODEL"),
            cfg.get("model_default", "glm-5.2"),
        )

        return self._openai_compatible_chat(
            base_url=base_url, api_key=api_key, model=model,
            prompt=prompt, system=system,
            temperature=temperature, max_tokens=max_tokens, timeout=timeout,
        )

    def _call_siliconflow(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> Optional[str]:
        """SiliconFlow — OpenAI 兼容接口."""
        cfg = self._providers_config.get("siliconflow", {})
        base_url = os.environ.get(
            cfg.get("base_url_env", "SILICONFLOW_BASE_URL"),
            cfg.get("base_url_default", "https://api.siliconflow.cn/v1"),
        )
        api_key = os.environ.get(cfg.get("api_key_env", "SILICONFLOW_API_KEY"), "")
        if not api_key:
            return None

        model = os.environ.get(
            cfg.get("model_env", "SILICONFLOW_MODEL"),
            cfg.get("model_default", "Qwen/Qwen2.5-7B-Instruct"),
        )

        return self._openai_compatible_chat(
            base_url=base_url, api_key=api_key, model=model,
            prompt=prompt, system=system,
            temperature=temperature, max_tokens=max_tokens, timeout=timeout,
        )

    def _call_ollama(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> Optional[str]:
        """Ollama 本地 — OpenAI 兼容接口 (Ollama 兼容 OpenAI API)."""
        cfg = self._providers_config.get("ollama", {})
        base_url = os.environ.get(
            cfg.get("base_url_env", "OLLAMA_BASE_URL"),
            cfg.get("base_url_default", "http://localhost:11434"),
        )
        # Ollama 不需要 api_key, 但 OpenAI 兼容接口需要一个占位
        api_key = os.environ.get(
            cfg.get("api_key_env", ""),
            cfg.get("api_key_default", "ollama"),
        )

        model = os.environ.get(
            cfg.get("model_env", "OLLAMA_MODEL"),
            cfg.get("model_default", "qwen2.5:7b"),
        )

        # Ollama 的 OpenAI 兼容端点: /v1/chat/completions
        return self._openai_compatible_chat(
            base_url=base_url.rstrip("/") + "/v1",
            api_key=api_key, model=model,
            prompt=prompt, system=system,
            temperature=temperature, max_tokens=max_tokens, timeout=timeout,
        )

    def _call_ollama_deep(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> Optional[str]:
        """Ollama 深度推理模型 (deepseek-r1:14b)."""
        cfg = self._providers_config.get("ollama", {})
        base_url = os.environ.get(
            cfg.get("base_url_env", "OLLAMA_BASE_URL"),
            cfg.get("base_url_default", "http://localhost:11434"),
        )
        api_key = cfg.get("api_key_default", "ollama")
        model = os.environ.get(
            cfg.get("deep_model_env", "OLLAMA_DEEP_MODEL"),
            cfg.get("deep_model_default", "deepseek-r1:14b"),
        )

        # 使用原生 /api/chat 端点 (支持 reasoning_content)
        try:
            url = base_url.rstrip("/") + "/api/chat"
            headers = {"Content-Type": "application/json"}
            messages: List[Dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            payload = json.dumps({
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            }).encode("utf-8")

            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self._ollama_timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))

            message = body.get("message", {})
            content = message.get("content", "")
            reasoning = message.get("reasoning_content", "")
            if content:
                if reasoning and len(reasoning) > 50:
                    return f"{content.strip()}\n\n---\n_思考过程：{reasoning.strip()[:500]}_"
                return content.strip()
            return None
        except Exception as e:
            logger.warning("Ollama deep 调用失败: %s", e)
            return None

    # ============================================================
    # 通用 OpenAI 兼容接口
    # ============================================================
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
        """OpenAI 兼容 chat/completions 调用.

        适用于: 豆包 / GLM / SiliconFlow / Ollama (OpenAI 兼容端点)
        """
        url = base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        messages: List[Dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = json.dumps({
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode("utf-8")

        # 单 provider 内重试
        last_error: Optional[Exception] = None
        for attempt in range(1 + self._max_retries):
            try:
                req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8"))

                content = body.get("choices", [{}])[0].get("message", {}).get("content")
                if not content:
                    content = body.get("choices", [{}])[0].get("message", {}).get("reasoning_content")
                return content if isinstance(content, str) else None
            except urllib.error.HTTPError as e:
                last_error = e
                if e.code in (401, 403):
                    # 认证错误不重试
                    logger.warning("认证失败 (%d), 不重试: %s", e.code, url)
                    return None
                elif e.code == 429:
                    # 限流, 等待后重试
                    logger.warning("限流 (429), %ds 后重试", self._retry_delay)
                    if attempt < self._max_retries:
                        time.sleep(self._retry_delay)
                    continue
                elif e.code >= 500:
                    # 服务器错误, 重试
                    if attempt < self._max_retries:
                        time.sleep(self._retry_delay)
                    continue
                else:
                    return None
            except urllib.error.URLError as e:
                last_error = e
                if attempt < self._max_retries:
                    time.sleep(self._retry_delay)
                continue
            except Exception as e:
                last_error = e
                return None

        logger.warning(
            "OpenAI 兼容调用失败 (重试 %d 次仍失败): %s, last_error=%s",
            self._max_retries, url, last_error,
        )
        return None


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
    """快捷函数: 深度思考模式 (Ollama deepseek-r1:14b)."""
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
