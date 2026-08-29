"""
ai_decision.providers — 统一 Provider 抽象层 + 优雅降级
======================================================

本模块是"后期接入 API 即可用, 无 Key 时自动降级"这一核心约束的承载者.

Provider 体系:
  - BaseProvider:     抽象基类, 统一 generate(prompt, system, timeout) -> str|None
  - LlmClientProvider: 适配现有 15_每日工作流/llm_client.py (DeepSeek/GLM/Ollama)
  - MoonshotProvider:  Kimi3 (Moonshot) OpenAI 兼容 /chat/completions
  - ClaudeProvider:    Claude OpenAI 兼容代理 /chat/completions
  - MockProvider:      零网络依赖, 基于规则 (动量/估值) 返回结构化投资语言, 保证全链路可跑

get_active_provider(role): 按 ai_decision.yaml 角色映射 + 环境变量探测 Key,
  缺失即回退 MockProvider. 单模型失败 (超时/异常) 返回 None, 不得抛出到主链路.

关键铁律:
  - 所有调用统一 try/except -> None
  - 降级链路: 真实模型 -> MockProvider -> 规则兜底 (hold + 低置信度)
  - 不修改 llm_client.py 现有接口, 仅做调用方
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

logger = logging.getLogger("ai_decision.providers")

# GPT 占位 Provider 内联定义 (避免循环导入)


# ============================================================
# 抽象基类
# ============================================================


class BaseProvider(ABC):
    """所有 Provider 的抽象基类"""

    role: str = ""
    model_name: str = ""
    is_mock: bool = False

    @abstractmethod
    def generate(self, prompt: str, system: str = "", timeout: int = 30) -> str | None:
        """返回模型生成文本; 任何失败 (网络/超时/Key 缺失) 返回 None"""
        raise NotImplementedError


# ============================================================
# 适配现有 llm_client.py
# ============================================================


def _ollama_reachable(
    host: str = "localhost", port: int = 11434, timeout: float = 0.5
) -> bool:
    """快速 socket 探活，避免 Ollama 服务未启动时盲等超时"""
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (TimeoutError, ConnectionRefusedError, OSError):
        return False


class LlmClientProvider(BaseProvider):
    """适配现有 15_每日工作流/llm_client.py

    复用其多 Provider 降级 chat()/chat_deep() 能力, 内部已处理缺 Key 返回 None.
    """

    def __init__(self, preferred: str = "deepseek", role: str = "") -> None:
        self.preferred = preferred
        self.role = role
        self.model_name = f"llm_client:{preferred}"
        self._available = self._detect_available()

    def _detect_available(self) -> bool:
        """探测 llm_client 是否可用 (检查对应 API Key 环境变量)"""
        # Ollama 本地服务: 快速 socket 探活，避免盲信"始终可用"
        if self.preferred == "ollama":
            return _ollama_reachable()
        env_map = {
            "deepseek": "DEEPSEEK_API_KEY",
            "glm": "GLM_API_KEY",
            "hy3": "HY3_API_KEY",
            "qianfan": "QIANFAN_API_KEY",
            "doubao": "DOUBAO_API_KEY",
        }
        key_env = env_map.get(self.preferred)
        if key_env:
            return bool(os.environ.get(key_env))
        return False  # 未知 provider 保守返回 False

    @property
    def available(self) -> bool:
        return self._available

    @property
    def is_mock(self) -> bool:
        return False

    def generate(self, prompt: str, system: str = "", timeout: int = 30) -> str | None:
        try:
            # P1 统一层: 优先从 utils.llm_client 导入 (收口 GLM5 + 三级降级链)
            try:
                from utils.llm_client import chat, chat_deep
            except (ImportError, ModuleNotFoundError):
                import sys
                from pathlib import Path

                _wf_dir = str(Path(__file__).resolve().parent.parent / "15_每日工作流")
                if _wf_dir not in sys.path:
                    sys.path.insert(0, _wf_dir)
                from llm_client import chat, chat_deep
        except (
            ImportError,
            ModuleNotFoundError,
            OSError,
        ) as exc:  # pragma: no cover - 导入失败兜底
            logger.warning("llm_client 不可用: %s", exc)
            return None
        try:
            # llm_client 的 chat/chat_deep 内部已做多级降级
            # (DeepSeek → GLM → Ollama API → Ollama CLI)
            # 不接受 provider/timeout 参数, 全部按 temperature=0.3 调用
            if system:
                resp = chat_deep(
                    prompt, system=system, temperature=0.3, max_tokens=4000
                )
            else:
                resp = chat(prompt, system="", temperature=0.3, max_tokens=2000)
            if isinstance(resp, str) and resp.strip():
                return resp
            return None
        except (
            RuntimeError,
            ValueError,
            TypeError,
            OSError,
            ConnectionError,
            TimeoutError,
        ) as exc:
            # llm_client 内部多级降级仍可能抛: 网络/超时/JSON 解析/响应格式异常
            logger.warning("LlmClientProvider(%s) 调用失败: %s", self.preferred, exc)
            return None


# ============================================================
# Moonshot (Kimi3) OpenAI 兼容
# ============================================================


class MoonshotProvider(BaseProvider):
    """Kimi3 (Moonshot) OpenAI 兼容 /chat/completions"""

    def __init__(
        self,
        api_key_env: str = "MOONSHOT_API_KEY",
        base_url: str = "https://api.moonshot.cn/v1",
        model: str = "moonshot-v1-8k",
        role: str = "",
    ) -> None:
        self.api_key_env = api_key_env
        self.base_url = base_url
        self.model = model
        self.role = role
        self.model_name = f"moonshot:{model}"
        self._key = os.environ.get(api_key_env)

    @property
    def available(self) -> bool:
        return bool(self._key)

    def generate(self, prompt: str, system: str = "", timeout: int = 30) -> str | None:
        if not self.available:
            return None
        try:
            import requests

            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": 1500,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            return text if isinstance(text, str) and text.strip() else None
        except (
            ImportError,
            OSError,
            ConnectionError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
            RuntimeError,
        ) as exc:
            # requests 抛 OSError 子类 (ConnectionError/Timeout/HTTPError);
            # JSON 解析失败抛 ValueError; data["choices"][0] 解析失败抛 KeyError/TypeError
            logger.warning("MoonshotProvider 调用失败: %s", exc)
            return None


# ============================================================
# Claude OpenAI 兼容代理
# ============================================================


class ClaudeProvider(BaseProvider):
    """Claude OpenAI 兼容代理 /chat/completions (可指向任意 OpenAI 兼容网关)"""

    def __init__(
        self,
        api_key_env: str = "CLAUDE_API_KEY",
        base_url: str = "https://api.anthropic.com/v1",
        model: str = "claude-sonnet-4-20250514",
        role: str = "",
    ) -> None:
        self.api_key_env = api_key_env
        self.base_url = base_url
        self.model = model
        self.role = role
        self.model_name = f"claude:{model}"
        self._key = os.environ.get(api_key_env)

    @property
    def available(self) -> bool:
        return bool(self._key)

    def generate(self, prompt: str, system: str = "", timeout: int = 30) -> str | None:
        if not self.available:
            return None
        try:
            import requests

            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": 1500,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            return text if isinstance(text, str) and text.strip() else None
        except (
            ImportError,
            OSError,
            ConnectionError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
            RuntimeError,
        ) as exc:
            logger.warning("ClaudeProvider 调用失败: %s", exc)
            return None


# ============================================================
# GptProvider — OpenAI 兼容 (盘中研判占位, 可扩展)
# ============================================================


class GptProvider(BaseProvider):
    """OpenAI 兼容 GPT Provider (占位, 可扩展)

    走 OpenAI /chat/completions 兼容接口, 缺失 API Key 时 available=False,
    由 get_active_provider 自动降级 Mock. 用户后期在 .env 填入 OPENAI_API_KEY 即可启用.
    """

    def __init__(
        self,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o",
        role: str = "",
    ) -> None:
        self.api_key_env = api_key_env
        self.base_url = base_url
        self.model = model
        self.role = role
        self.model_name = f"gpt:{model}"
        self._key = os.environ.get(api_key_env)

    @property
    def available(self) -> bool:
        return bool(self._key)

    def generate(self, prompt: str, system: str = "", timeout: int = 30) -> str | None:
        if not self.available:
            return None
        try:
            import requests

            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": 1500,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            return text if isinstance(text, str) and text.strip() else None
        except (
            ImportError,
            OSError,
            ConnectionError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
            RuntimeError,
        ) as exc:
            logger.warning("GptProvider 调用失败: %s", exc)
            return None


# ============================================================
# MockProvider — 零依赖规则兜底
# ============================================================


class MockProvider(BaseProvider):
    """MockProvider: 无任何 API Key 时保证全链路可跑

    基于规则 (动量/估值/新闻情绪) 生成结构化投资语言, 输出与真实模型
    相同的 prompt 语义槽位, 便于后续无缝切换.
    """

    is_mock = True

    def __init__(self, role: str = "mock", bias: float = 0.0) -> None:
        self.role = role
        self.model_name = f"mock:{role}"
        self.bias = bias  # 角色倾向 (bull=+0.2, bear=-0.2, judge=0)

    @property
    def available(self) -> bool:
        return True  # Mock 永远可用, 是无 Key 时的兜底

    def generate(self, prompt: str, system: str = "", timeout: int = 30) -> str | None:
        try:
            return self._rule_based_response(prompt)
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            RuntimeError,
        ) as exc:  # pragma: no cover
            logger.debug("MockProvider 规则生成异常: %s", exc)
            return None

    def _rule_based_response(self, prompt: str) -> str:
        """从 prompt 中抽取行情上下文 (rag_context 注入的 change_pct/pe 等),
        以规则生成结构化观点. prompt 已包含上下文, 这里做轻量解析.
        """
        p = prompt.lower()
        # 抽取 change_pct (涨跌幅)
        change = 0.0
        import re

        m = re.search(r"涨跌幅[^\d\-]+([\-]?\d+(?:\.\d+)?)", prompt)
        if m:
            try:
                change = float(m.group(1))
            except ValueError:
                change = 0.0
        # 抽取 PE
        pe = 0.0
        mp = re.search(r"pe[^\d\-]+([\-]?\d+(?:\.\d+)?)", p)
        if mp:
            try:
                pe = float(mp.group(1))
            except ValueError:
                pe = 0.0

        if self.role == "bull":
            change * 0.05 + self.bias
            if change > 0:
                return (
                    f"看多观点: 当前价格动量向上 (涨跌幅 {change:.2f}%), "
                    "技术形态偏强, 建议逢低建仓. 置信度中等偏高."
                )
            return (
                f"看多观点: 尽管短期回调 (涨跌幅 {change:.2f}%), 但估值具备长期吸引力, "
                "维持结构性看多. 置信度中等."
            )
        if self.role == "bear":
            -change * 0.05 + self.bias
            if change < 0:
                return (
                    f"看空观点: 价格动量向下 (涨跌幅 {change:.2f}%), 下行风险释放未尽, "
                    "建议减仓规避. 置信度中等偏高."
                )
            return (
                f"看空观点: 虽短期反弹, 但估值偏高 (PE={pe:.1f}) 且宏观不确定性大, "
                "维持谨慎看空. 置信度中等."
            )
        # judge
        if change > 1:
            return "裁决: 多方动量占优, 但需警惕追高风险, 建议偏多但控制仓位."
        if change < -1:
            return "裁决: 空方动量占优, 但存在超跌反弹可能, 建议偏空但避免裸卖空."
        return "裁决: 多空信号不明确, 建议观望持有, 等待更清晰催化."


# ============================================================
# 工厂: 按角色 + 环境变量探测可用 Provider
# ============================================================

# 角色 -> (真实 Provider 工厂, 缺省真实后端标识)
_ROLE_BACKENDS: dict[str, str] = {
    "signal": "deepseek",  # DeepSeek = 信号计算/代码
    "compliance": "ollama",  # Ollama 本地 = 合规审计 (qwen2.5:3b 兜底)
    "research": "moonshot",  # Kimi3 = 研报图表多模态
    "reasoning": "claude",  # Claude = 深度推理/风控
    "intraday": "gpt",  # GPT = 盘中研判 (占位)
    "bull": "deepseek",
    "bear": "ollama",  # Ollama 本地 = 看空辩论
    "judge": "claude",
}


def get_active_provider(
    role: str, role_backends: dict[str, str] | None = None
) -> BaseProvider:
    """按角色返回可用 Provider; 缺失 Key 自动降级到 MockProvider

    Args:
        role: 角色名 (bull/bear/judge/signal/...)
        role_backends: 可选覆盖 _ROLE_BACKENDS 的角色->后端映射
    Returns:
        可用的真实 Provider 或 MockProvider
    """
    backends = role_backends or _ROLE_BACKENDS
    backend = backends.get(role, "deepseek")

    # 尝试构造真实 Provider 并验证 Key
    try:
        if (
            backend == "deepseek"
            or backend == "glm"
            or backend in ("qianfan", "doubao", "hy3", "ollama")
        ):
            prov = LlmClientProvider(preferred=backend, role=role)
            if prov.available:
                return prov
            logger.debug("LlmClientProvider(%s/%s) 无 Key, 降级 Mock", backend, role)
        if backend == "moonshot":
            prov = MoonshotProvider(role=role)
            if prov.available:
                return prov
        elif backend == "claude":
            prov = ClaudeProvider(role=role)
            if prov.available:
                return prov
        elif backend == "gpt":
            if GptProvider is not None:
                prov = GptProvider(role=role)
                if prov.available:
                    return prov
    except (
        ImportError,
        AttributeError,
        TypeError,
        ValueError,
        OSError,
        RuntimeError,
    ) as exc:
        # Provider 构造可能抛: 导入失败/属性缺失/参数错误/环境读取错误
        logger.debug("构造真实 Provider(%s) 失败, 降级 Mock: %s", backend, exc)

    # 降级到 Mock; 角色决定 bias
    bias = 0.0
    if role == "bull":
        bias = 0.15
    elif role == "bear":
        bias = -0.15
    return MockProvider(role=role, bias=bias)
