"""LiteLLM Gateway 统一类型定义 — OpenAI-compatible 接口数据类.

设计原则:
    - 不可变 (frozen=True): 所有响应数据类不可变
    - OpenAI-compatible: 字段命名与 OpenAI API 对齐
    - 零外部依赖: 不依赖 litellm / openai 包

参考:
    - OpenAI Chat Completions API: https://platform.openai.com/docs/api-reference/chat
    - LiteLLM 统一接口规范
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class Usage:
    """Token 用量统计 (OpenAI-compatible)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(frozen=True)
class ProviderInfo:
    """Provider 元信息."""

    name: str           # deepseek/doubao/glm/siliconflow/ollama/omniroute
    model: str          # 实际调用的模型名
    latency_ms: float = 0.0
    success: bool = True
    error: str = ""


@dataclass
class ChatMessage:
    """对话消息 (OpenAI-compatible role/content).

    role: "system" | "user" | "assistant"
    """

    role: str
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class ChatRequest:
    """统一对话请求 (OpenAI-compatible).

    封装 prompt + system + 模型参数, 可从 messages 列表构造.
    """

    prompt: str
    system: str = ""
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    scene: str = "default"  # 场景路由: intraday/rebalance/report/default
    messages: list[ChatMessage] = field(default_factory=list)

    @classmethod
    def from_messages(
        cls,
        messages: list[ChatMessage],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        scene: str = "default",
    ) -> ChatRequest:
        """从 messages 列表构造 (提取 system 与首条 user)."""
        system = ""
        prompt = ""
        for msg in messages:
            if msg.role == "system" and not system:
                system = msg.content
            elif msg.role == "user" and not prompt:
                prompt = msg.content
        return cls(
            prompt=prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            scene=scene,
            messages=list(messages),
        )


@dataclass(frozen=True)
class ChatResponse:
    """统一对话响应 (OpenAI-compatible).

    封装回复文本 + Provider 信息 + 用量统计.
    """

    content: str
    provider: ProviderInfo
    usage: Usage = field(default_factory=Usage)
    raw: Optional[Any] = None  # 原始 provider 返回 (调试用)

    @property
    def success(self) -> bool:
        return self.provider.success

    def as_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "provider": {
                "name": self.provider.name,
                "model": self.provider.model,
                "latency_ms": self.provider.latency_ms,
                "success": self.provider.success,
            },
            "usage": self.usage.as_dict(),
        }


# ============================================================
# 场景路由配置
# ============================================================


# 场景 → 推荐 provider 映射 (与 multi_model_router.py 场景对齐)
SCENE_PROVIDER_MAP: dict[str, str] = {
    "intraday": "deepseek",       # 盘中决策 → 轻量快速
    "rebalance": "deepseek",      # 再平衡 → 深度推理
    "report": "doubao",           # 报告生成 → 创意
    "hedge": "deepseek",          # 对冲决策 → 深度
    "default": "deepseek",        # 默认
}

# 场景 → 温度参数映射
SCENE_TEMPERATURE_MAP: dict[str, float] = {
    "intraday": 0.1,       # 盘中决策低温度 (确定性)
    "rebalance": 0.2,      # 再平衡中低温度
    "report": 0.5,         # 报告生成中温度 (创意)
    "hedge": 0.15,         # 对冲决策低温度
    "default": 0.3,        # 默认
}


__all__ = [
    "Usage",
    "ProviderInfo",
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "SCENE_PROVIDER_MAP",
    "SCENE_TEMPERATURE_MAP",
]
