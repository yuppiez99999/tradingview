"""GLM-5 客户端 — 单一 GLM-5 调用入口 (item 12 GLM-5 路径归一, 2026-09-11).

本模块原为 822 行独立客户端, W7.4.3 重构为 LiteLLMRouter 薄包装;
item 12 第二增量进一步归一: GLM5Client 不再经过 provider 无关的 LiteLLMRouter
fallback 链 (其 deepseek 优先会让 "GLM-5" 客户端实际命中 deepseek), 而是直接
委托 ``utils.alpha.llm.providers.glm.call_glm`` —— 全系统唯一的 GLM-5 实现,
同时被 ``LLMRouter`` 的 glm provider 复用, 实现两条 GLM-5 路径归一.

设计收益:
    - 命名与行为一致: GLM5Client.chat 必然走 GLM-5 (call_glm), 不再被 deepseek 抢占
    - 单一实现: call_glm 同时服务 GLM5Client 与 LLMRouter.glm provider
    - 去除对 LiteLLMRouter / ChatRequest / ChatResponse 的耦合

向后兼容 (公开 API 不变, 20+ 生产消费方无需改动):
    - GLM5Client.chat(message, history, system_prompt, ...) → dict (含 content 字段)
    - GLM5Client.is_ready() → bool (看 GLM_API_KEY 是否就绪)
    - get_glm5_client(**kwargs) → GLM5Client 单例
    - quick_chat(message, **kwargs) → str

旧 mode 参数 (local/api/ollama/local_gguf) 保留为 config 字段但已 deprecated,
实际调用统一走 call_glm (GLM-5 OpenAI 兼容端点).

依赖前置 (已就绪):
    - utils/alpha/llm/providers/glm.py call_glm (单一 GLM-5 实现)
    - utils/alpha/llm/router.py LLMRouter (glm provider 复用同一 call_glm)

归一日期: 2026-09-11 (item 12 GLM-5 路径归一)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# LIT-2.5: FinGPT 备选模型 (可选依赖, 缺失时降级)
try:
    from .fingpt_integration import FinGPTClient, ModelRouter, ModelType, TaskCategory

    _FINGPT_AVAILABLE = True
except ImportError:
    _FINGPT_AVAILABLE = False


# ============================================================
# GLM5Config (向后兼容, mode 字段保留但 deprecated)
# ============================================================


@dataclass
class GLM5Config:
    """GLM-5 配置 (向后兼容, 实际调用走 LiteLLMRouter).

    mode 字段保留但已 deprecated, 实际 provider 路由由 LiteLLMRouter 决定.
    """

    mode: str = "api"  # deprecated: 保留向后兼容, 实际走 LiteLLMRouter
    model_path: str = "ZhipuAI/GLM-5"
    device: str = "auto"
    dtype: str = "float16"
    max_new_tokens: int = 3000
    temperature: float = 0.3
    top_p: float = 0.9

    # API 模式配置 (deprecated, 由 LLMRouter 配置接管)
    api_key: str = ""
    api_base: str = ""
    api_model: str = "Qwen/Qwen3-8B-Instruct-4bit"
    api_model_fallbacks: list[str] = field(default_factory=list)

    # Ollama 模式配置 (deprecated)
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "glm-5"

    # Local GGUF 模式配置 (deprecated)
    gguf_model_path: str = os.environ.get("LOCAL_LLM_MODEL_PATH", "")
    gguf_n_ctx: int = 8192
    gguf_n_gpu_layers: int = 0
    gguf_n_threads: int = 8

    # 系统提示词
    system_prompt: str = (
        "你是一位资深的量化交易分析师和投资顾问。请根据用户提供的信息：\n"
        "1. 用专业、客观的语言进行分析\n"
        "2. 给出数据支撑的结论，避免主观臆断\n"
        "3. 明确风险提示和操作建议\n"
        "4. 回复格式清晰，适合直接用于交易报告"
    )

    def __post_init__(self) -> None:
        # 环境变量覆盖 (向后兼容)
        if os.environ.get("GLM5_MODE"):
            self.mode = os.environ.get("GLM5_MODE", self.mode)
        if not self.api_key:
            self.api_key = os.environ.get("ZHIPUAI_API_KEY", "")


# ============================================================
# GLM5Client — 单一 GLM-5 调用入口 (委托 call_glm)
# ============================================================


class GLM5Client:
    """GLM-5 客户端 — 单一 GLM-5 调用入口 (item 12 GLM-5 路径归一).

    本类不再经过 provider 无关的 LiteLLMRouter fallback 链 (其 deepseek 优先
    会让 "GLM-5" 客户端实际命中 deepseek), 而是直接委托
    ``utils.alpha.llm.providers.glm.call_glm`` —— 全系统唯一的 GLM-5 实现,
    同时被 ``LLMRouter`` 的 glm provider 复用, 实现两路径归一.

    保留旧公开 API 签名向后兼容:
        client = GLM5Client()                      # mode 参数已 deprecated
        resp = client.chat("市场分析")              # -> {"role","content","model","provider",...}
        client.is_ready()                           # -> bool (GLM_API_KEY 是否就绪)
        quick_chat("市场分析")                       # -> str
        get_glm5_client()                           # -> 单例
    """

    def __init__(self, config: GLM5Config | None = None, **kwargs: Any) -> None:
        self.config = config or GLM5Config(**kwargs)
        self._stats_total: int = 0
        self._stats_ok: int = 0
        logger.info(
            "GLM5Client 初始化 (mode=%s, 直连 GLM-5 via call_glm)",
            self.config.mode,
        )

    # ----------------------------------------------------------
    # 单一 GLM-5 实现 (延迟导入, 避免循环依赖)
    # ----------------------------------------------------------

    @staticmethod
    def _call_glm(
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> str | None:
        """委托全系统唯一的 GLM-5 实现 (call_glm)."""
        from utils.alpha.llm.providers.glm import call_glm

        return call_glm(
            prompt=prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            provider_cfg={},
        )

    def _current_model(self) -> str:
        """当前 GLM-5 模型名 (与 call_glm 解析口径一致)."""
        return os.environ.get("GLM_MODEL", "glm-5.2")

    # ----------------------------------------------------------
    # 公开 API (向后兼容)
    # ----------------------------------------------------------

    def chat(
        self,
        message: str,
        history: list[dict[str, str]] | None = None,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """对话接口 (向后兼容旧 API).

        Args:
            message: 用户消息
            history: 历史对话 (deprecated, call_glm 不使用)
            system_prompt: 系统提示词 (None 时用 config.system_prompt)
            temperature: 温度参数
            max_tokens: 最大生成 token 数

        Returns:
            {"role": "assistant", "content": "...", "model": "glm-5.2", "provider": "glm", ...}
            失败时 content 为空字符串
        """
        # 输入验证 (向后兼容)
        if not message or not message.strip():
            raise ValueError("消息内容不能为空")
        if len(message.strip()) > 100000:
            raise ValueError("消息内容不能超过100000字符")
        if temperature is not None and (temperature < 0 or temperature > 2):
            raise ValueError("temperature 参数必须在 0-2 之间")
        if max_tokens is not None and (max_tokens < 1 or max_tokens > 100000):
            raise ValueError("max_tokens 参数必须在 1-100000 之间")

        system = system_prompt or self.config.system_prompt
        temp = temperature if temperature is not None else self.config.temperature
        tokens = max_tokens or self.config.max_new_tokens
        timeout = int(kwargs.get("timeout", 30))

        self._stats_total += 1
        try:
            result = self._call_glm(
                prompt=message,
                system=system,
                temperature=temp,
                max_tokens=tokens,
                timeout=timeout,
            )
        except (ValueError, RuntimeError, OSError, KeyError) as exc:
            logger.warning("GLM5Client.chat 异常: %s", exc)
            return {
                "role": "assistant",
                "content": "",
                "model": "none",
                "error": str(exc),
            }

        if result is None:
            return {
                "role": "assistant",
                "content": "",
                "model": "none",
                "error": "call_glm 返回 None (无 GLM_API_KEY 或调用失败)",
            }
        self._stats_ok += 1
        return {
            "role": "assistant",
            "content": result,
            "model": self._current_model(),
            "provider": "glm",
        }

    def is_ready(self) -> bool:
        """检查 GLM-5 是否就绪 (与 call_glm 前置条件一致).

        Returns:
            True if GLM_API_KEY 已配置
        """
        return bool(os.environ.get("GLM_API_KEY"))

    def test_connection(self) -> dict[str, Any]:
        """测试 GLM-5 连接 (向后兼容).

        Returns:
            {"success": bool, "provider": str, "model": str}
        """
        if not self.is_ready():
            return {"success": False, "error": "GLM_API_KEY 未配置"}
        result = self._call_glm(
            prompt="ping", system="", temperature=0.1, max_tokens=16, timeout=10
        )
        return {
            "success": result is not None,
            "provider": "glm",
            "model": self._current_model(),
        }

    def get_stats(self) -> dict[str, Any]:
        """获取调用统计 (本地累计, 不依赖 LiteLLMRouter).

        Returns:
            {"total_calls": int, "success_calls": int, "success_rate": float}
        """
        return {
            "total_calls": self._stats_total,
            "success_calls": self._stats_ok,
            "success_rate": (
                self._stats_ok / self._stats_total if self._stats_total > 0 else 0.0
            ),
        }


# ============================================================
# 单例 + 快捷函数 (向后兼容)
# ============================================================

_glm5_instance: GLM5Client | None = None


def get_glm5_client(**kwargs: Any) -> GLM5Client:
    """获取 GLM-5 客户端实例 (单例模式, 向后兼容)."""
    global _glm5_instance
    if _glm5_instance is None:
        _glm5_instance = GLM5Client(**kwargs)
    return _glm5_instance


def quick_chat(message: str, **kwargs: Any) -> str:
    """快速对话 (一行代码调用, 向后兼容).

    Returns:
        回复文本 (失败返回空字符串)
    """
    client = get_glm5_client()
    result = client.chat(message, **{k: v for k, v in kwargs.items() if k != "config"})
    return str(result.get("content", ""))


# ============================================================
# LIT-2.5: FinGPT 备选模型集成
# ============================================================


def get_fingpt_client() -> Any:
    """获取 FinGPT 客户端实例 (FinGPT 不可用时返回 None)."""
    if not _FINGPT_AVAILABLE:
        return None
    return FinGPTClient()


def quick_chat_with_fallback(
    message: str, task: str = "daily_report", **kwargs: Any
) -> str:
    """带 FinGPT 备选的快速对话.

    路由策略:
    - 交易/盘中/情绪 → 优先 FinGPT, 回退 GLM-5
    - 研究/报告 → 优先 GLM-5, 回退 FinGPT

    Args:
        message: 消息内容
        task: 任务类型 (intraday/daily_report/research/sentiment/trading)

    Returns:
        回复文本
    """
    if _FINGPT_AVAILABLE:
        router = ModelRouter(
            glm5_available=True,
            fingpt_available=True,
        )
        task_map = {
            "intraday": TaskCategory.INTRADAY,
            "daily_report": TaskCategory.DAILY_REPORT,
            "research": TaskCategory.RESEARCH,
            "sentiment": TaskCategory.SENTIMENT,
            "trading": TaskCategory.TRADING,
        }
        task_cat = task_map.get(task, TaskCategory.DAILY_REPORT)
        model = router.route(task_cat)

        if model == ModelType.FINGPT:
            fingpt = FinGPTClient()
            return fingpt.chat(message, **kwargs)

    return quick_chat(message, **kwargs)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("GLM-5 客户端测试 (LiteLLMRouter 薄包装)")
    client = GLM5Client()
    logger.info("is_ready: %s", client.is_ready())
    result = client.chat("你好，请介绍一下你的能力")
    logger.info("响应: %s", result.get("content", "")[:200])
