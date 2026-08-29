"""LLM 多模型路由器 — 终极量化交易系统 8.4 (T2.1).

模块整合 8.4 — ARCHITECTURE §2.3 / ADR-004
任务: T2.1

设计原则:
    1. 5 个 Provider fallback 链 (DeepSeek 优先): deepseek → doubao → glm → siliconflow → ollama
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

import copy
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, cast

# 复用 ConfigManager 4 级优先级 (HC-5)
from utils.config_manager import get_config

# 复用 Feature Flag 框架
from utils.infra.feature_flags import is_enabled

logger = logging.getLogger("llm_router")


def _find_project_root() -> Path:
    """向上查找项目根目录 (通过已知 marker 文件/目录识别).

    比硬编码 parent.parent.parent 更健壮, 文件移动不会失效.
    """
    # 已知的项目 marker (任一存在即视为项目根)
    project_markers = [
        "config",
        "utils",
        "v8.3_institutional",
        "research",
        "tests",
        "requirements.txt",
        "ruff.toml",
        "pytest.ini",
    ]
    current = Path(__file__).resolve().parent
    for _ in range(10):  # 最多向上查找 10 层
        if any((current / m).exists() for m in project_markers):
            return current
        if current.parent == current:  # 到达文件系统根
            break
        current = current.parent
    # 兜底: 返回当前文件向上 3 层 (与旧逻辑一致)
    return Path(__file__).resolve().parent.parent.parent


# 项目根目录
_PROJECT_ROOT = _find_project_root()

# 审计日志目录
_AUDIT_LOG_DIR = _PROJECT_ROOT / "reports" / "llm_router"


# ============================================================
# 异常定义
# ============================================================


def _safe_urlopen(req, timeout=None):
    """安全封装 urllib.request.urlopen — 拒绝非 http/https 协议 (B310)"""
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"拒绝非 HTTP 协议的 URL: {url[:100]}")
    if timeout is not None:
        return _safe_urlopen(req, timeout=timeout)
    return urllib.request.urlopen(req)  # nosec B310  URL已校验为http/https


class LLMRouterError(Exception):
    """LLMRouter 基础异常."""


class AllProvidersFailedError(LLMRouterError):
    """所有 provider 均失败."""

    def __init__(
        self,
        message: str,
        tried_providers: list[str],
        last_error: Exception | None = None,
    ) -> None:
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

    def to_dict(self) -> dict[str, Any]:
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
        ...     logger.info(reply)
    """

    _instance: LLMRouter | None = None
    _lock: threading.RLock = threading.RLock()

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}
        self._settings: dict[str, Any] = {}
        self._providers_config: dict[str, dict[str, Any]] = {}
        self._fallback_chain: list[str] = []
        self._provider_fns: dict[str, ProviderFn] = {}
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
    def get_instance(cls) -> LLMRouter:
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
        use_omniroute = os.environ.get("USE_OMNIROUTE", "True").lower() in (
            "true",
            "1",
            "yes",
        )

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
            self._settings.get(
                "fallback_chain", ["deepseek", "doubao", "glm", "siliconflow", "ollama"]
            )
        )
        if use_omniroute and "omniroute" not in self._fallback_chain:
            self._fallback_chain.insert(0, "omniroute")

        # 超时
        self._default_timeout = int(self._settings.get("default_timeout_seconds", 5))
        self._ollama_timeout = int(self._settings.get("ollama_timeout_seconds", 30))

        # 生成参数
        self._default_temperature = float(
            self._settings.get("default_temperature", 0.3)
        )
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
        self._passthrough_module = self._settings.get(
            "passthrough_module", "llm_client"
        )
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
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str | None:
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
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str | None:
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
            try:
                import importlib

                mod = importlib.import_module(self._passthrough_module)
                fn = getattr(mod, "chat_deep", None)
                if fn:
                    return cast(Optional[str], fn(prompt, system))
                # 没有 chat_deep, 用 chat
                fn = getattr(mod, self._passthrough_function)
                return cast(Optional[str], fn(prompt, system))
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ) as e:  # P2 模块 fail-safe, 待后续精确化
                logger.error("透传 chat_deep 失败: %s", e)
                return None

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

    def test_connection(self) -> dict[str, Any]:
        """连通性探测.

        Returns:
            {
                "providers": {"doubao": True/False, ...},
                "available": "doubao" / "glm" / ...,
                "status": "ok" / "degraded"
            }
        """
        providers_status: dict[str, bool] = {}
        available: str | None = None

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
                except (
                    ValueError,
                    TypeError,
                    KeyError,
                    AttributeError,
                    RuntimeError,
                    OSError,
                    TimeoutError,
                    ConnectionError,
                ):  # P2 模块 fail-safe, 待后续精确化
                    pass

        return {
            "providers": providers_status,
            "available": available,
            "status": "ok" if available else "degraded",
        }

    def list_providers(self) -> list[dict[str, Any]]:
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
                    "timeout_seconds": cfg.get(
                        "timeout_seconds", self._default_timeout
                    ),
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
    ) -> str | None:
        """执行 fallback 链."""
        tried_providers: list[str] = []
        last_error: Exception | None = None

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
                    self._write_audit_log(
                        CallRecord(
                            timestamp=datetime.utcnow().isoformat() + "Z",
                            prompt=prompt,
                            system=system,
                            provider=name,
                            success=True,
                            latency_ms=latency_ms,
                            response_preview=result[:200],
                        )
                    )
                    logger.info(
                        "LLMRouter 调用成功: provider=%s, latency=%.0fms",
                        name,
                        latency_ms,
                    )
                    return result
                # 返回 None (软失败)
                self._write_audit_log(
                    CallRecord(
                        timestamp=datetime.utcnow().isoformat() + "Z",
                        prompt=prompt,
                        system=system,
                        provider=name,
                        success=False,
                        latency_ms=latency_ms,
                        error_type="SoftFailure",
                        error_message="Provider returned None",
                    )
                )
                logger.warning(
                    "LLMRouter provider 软失败: %s (latency=%.0fms)",
                    name,
                    latency_ms,
                )
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ) as e:  # P2 模块 fail-safe, 待后续精确化
                latency_ms = (time.perf_counter() - start_ts) * 1000.0
                last_error = e
                self._write_audit_log(
                    CallRecord(
                        timestamp=datetime.utcnow().isoformat() + "Z",
                        prompt=prompt,
                        system=system,
                        provider=name,
                        success=False,
                        latency_ms=latency_ms,
                        error_type=type(e).__name__,
                        error_message=str(e),
                    )
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
        temperature: float | None,
        max_tokens: int | None,
    ) -> str | None:
        """透传到旧 llm_client (HC-1: flag=False 时的默认行为)."""
        try:
            import importlib

            mod = importlib.import_module(self._passthrough_module)
            fn = getattr(mod, self._passthrough_function, None)
            if fn is None:
                logger.error(
                    "透传失败: %s.%s 不存在",
                    self._passthrough_module,
                    self._passthrough_function,
                )
                return None
            # 旧 llm_client.chat(prompt, system, temperature, max_tokens)
            kwargs: dict[str, Any] = {}
            if temperature is not None:
                kwargs["temperature"] = temperature
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
            return cast(Optional[str], fn(prompt, system, **kwargs))
        except ImportError as e:
            logger.error("透传失败: 无法导入 %s: %s", self._passthrough_module, e)
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
        ) as e:  # P2 模块 fail-safe, 待后续精确化
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
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("审计日志写入失败: %s", e)

    # ============================================================
    # Provider 实现 (OmniRoute P0 + 5 个, DeepSeek 优先)
    # ============================================================
    def _call_omniroute(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> str | None:
        """OmniRoute 网关 (P0 最高优先级) — 290+ provider, 500+ 模型.

        由 OmniRouteClient 内部处理:
            - Feature Flag (USE_OMNIROUTE) 检查
            - 熔断器 (连续失败冷却)
            - Combo 自动路由 (OmniRoute 内部处理)
            - Token 压缩 (OmniRoute 内部处理)
        """
        try:
            from utils.alpha.omni_route_client import OmniRouteClient

            client = OmniRouteClient.get_instance()
            return client.chat(prompt, system, temperature, max_tokens)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # P2 模块 fail-safe
            logger.warning("OmniRoute provider 调用失败 (降级): %s", e)
            return None

    def _call_deepseek(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> str | None:
        """DeepSeek V3 (deepseek-chat) — OpenAI 兼容接口, 主 LLM."""
        cfg = self._providers_config.get("deepseek", {})
        base_url = os.environ.get(
            cfg.get("base_url_env", "DEEPSEEK_BASE_URL"),
            cfg.get("base_url_default", "https://api.deepseek.com"),
        )
        api_key = os.environ.get(cfg.get("api_key_env", "DEEPSEEK_API_KEY"), "")
        if not api_key:
            return None

        model = os.environ.get(
            cfg.get("model_env", "DEEPSEEK_MODEL"),
            cfg.get("model_default", "deepseek-chat"),
        )

        # DeepSeek OpenAI 兼容端点: /v1/chat/completions
        return self._openai_compatible_chat(
            base_url=base_url.rstrip("/") + "/v1",
            api_key=api_key,
            model=model,
            prompt=prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    def _call_deepseek_reasoner(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> str | None:
        """DeepSeek R1 (deepseek-reasoner) 云端推理模型, 用于复杂决策.

        支持 reasoning_content 字段 (思考过程).
        """
        cfg = self._providers_config.get("deepseek", {})
        base_url = os.environ.get(
            cfg.get("base_url_env", "DEEPSEEK_BASE_URL"),
            cfg.get("base_url_default", "https://api.deepseek.com"),
        )
        api_key = os.environ.get(cfg.get("api_key_env", "DEEPSEEK_API_KEY"), "")
        if not api_key:
            return None

        model = os.environ.get(
            cfg.get("reasoner_model_env", "DEEPSEEK_REASONER_MODEL"),
            cfg.get("reasoner_model_default", "deepseek-reasoner"),
        )

        try:
            url = base_url.rstrip("/") + "/v1/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
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
                }
            ).encode("utf-8")

            req = urllib.request.Request(
                url, data=payload, headers=headers, method="POST"
            )
            with _safe_urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode("utf-8"))

            message = body.get("choices", [{}])[0].get("message", {})
            content = message.get("content")
            if not content:
                content = message.get("reasoning_content")
            if isinstance(content, str) and content:
                reasoning = message.get("reasoning_content", "")
                if reasoning and len(reasoning) > 50 and reasoning != content:
                    return f"{content.strip()}\n\n---\n_思考过程：{reasoning.strip()[:500]}_"
                return content.strip()
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
        ) as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("DeepSeek reasoner 调用失败: %s", e)
            return None

    def _call_doubao(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> str | None:
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
            base_url=base_url,
            api_key=api_key,
            model=model,
            prompt=prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    def _call_glm(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> str | None:
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
            base_url=base_url,
            api_key=api_key,
            model=model,
            prompt=prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    def _call_siliconflow(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> str | None:
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
            base_url=base_url,
            api_key=api_key,
            model=model,
            prompt=prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    def _call_ollama(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> str | None:
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
            api_key=api_key,
            model=model,
            prompt=prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    def _call_ollama_deep(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> str | None:
        """Ollama 深度推理模型 (deepseek-r1:14b)."""
        cfg = self._providers_config.get("ollama", {})
        base_url = os.environ.get(
            cfg.get("base_url_env", "OLLAMA_BASE_URL"),
            cfg.get("base_url_default", "http://localhost:11434"),
        )
        cfg.get("api_key_default", "ollama")
        model = os.environ.get(
            cfg.get("deep_model_env", "OLLAMA_DEEP_MODEL"),
            cfg.get("deep_model_default", "deepseek-r1:14b"),
        )

        # 使用原生 /api/chat 端点 (支持 reasoning_content)
        try:
            url = base_url.rstrip("/") + "/api/chat"
            headers = {"Content-Type": "application/json"}
            messages: list[dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            payload = json.dumps(
                {
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    },
                }
            ).encode("utf-8")

            req = urllib.request.Request(
                url, data=payload, headers=headers, method="POST"
            )
            with _safe_urlopen(req, timeout=self._ollama_timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))

            message = body.get("message", {})
            content = message.get("content", "")
            reasoning = message.get("reasoning_content", "")
            if content:
                if reasoning and len(reasoning) > 50:
                    return f"{content.strip()}\n\n---\n_思考过程：{reasoning.strip()[:500]}_"
                return cast(str, content.strip())
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
        ) as e:  # P2 模块 fail-safe, 待后续精确化
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
    ) -> str | None:
        """OpenAI 兼容 chat/completions 调用.

        适用于: 豆包 / GLM / SiliconFlow / Ollama (OpenAI 兼容端点)
        """
        url = base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
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
            }
        ).encode("utf-8")

        # 单 provider 内重试
        last_error: Exception | None = None
        for attempt in range(1 + self._max_retries):
            try:
                req = urllib.request.Request(
                    url, data=payload, headers=headers, method="POST"
                )
                with _safe_urlopen(req, timeout=timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8"))

                content = body.get("choices", [{}])[0].get("message", {}).get("content")
                if not content:
                    content = (
                        body.get("choices", [{}])[0]
                        .get("message", {})
                        .get("reasoning_content")
                    )
                return content if isinstance(content, str) else None
            except urllib.error.HTTPError as e:
                last_error = e
                if e.code in (401, 403):
                    # 认证错误不重试
                    logger.warning("认证失败 (%d), 不重试: %s", e.code, url)
                    return None
                if e.code == 429:
                    # 限流, 等待后重试
                    logger.warning("限流 (429), %ds 后重试", self._retry_delay)
                    if attempt < self._max_retries:
                        time.sleep(self._retry_delay)
                    continue
                if e.code >= 500:
                    # 服务器错误, 重试
                    if attempt < self._max_retries:
                        time.sleep(self._retry_delay)
                    continue
                return None
            except urllib.error.URLError as e:
                last_error = e
                if attempt < self._max_retries:
                    time.sleep(self._retry_delay)
                continue
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ) as e:  # P2 模块 fail-safe, 待后续精确化
                last_error = e
                return None

        logger.warning(
            "OpenAI 兼容调用失败 (重试 %d 次仍失败): %s, last_error=%s",
            self._max_retries,
            url,
            last_error,
        )
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
    """快捷函数: 多模型 fallback 对话.

    Usage:
        >>> from utils.alpha.llm_router import chat
        >>> reply = chat("你好")
    """
    return LLMRouter.get_instance().chat(prompt, system, temperature, max_tokens)


def chat_deep(
    prompt: str,
    system: str = "",
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str | None:
    """快捷函数: 深度思考模式 (主: DeepSeek R1 / 备: Ollama deepseek-r1:14b)."""
    return LLMRouter.get_instance().chat_deep(prompt, system, temperature, max_tokens)


def test_connection() -> dict[str, Any]:
    """快捷函数: 连通性探测."""
    return LLMRouter.get_instance().test_connection()


def list_providers() -> list[dict[str, Any]]:
    """快捷函数: 列出所有 provider (审计用)."""
    return LLMRouter.get_instance().list_providers()


def reload() -> None:
    """快捷函数: 重新加载配置 (热加载)."""
    LLMRouter.get_instance().reload()
