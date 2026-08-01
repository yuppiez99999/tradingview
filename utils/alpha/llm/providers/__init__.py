# -*- coding: utf-8 -*-
"""LLM Provider 实现集合 — 终极量化交易系统 8.4 (B3.4.3).

6 个 Provider:
    - omniroute    OmniRoute 网关 (P0, 默认开启, 290+ provider 500+ 模型)
    - deepseek     DeepSeek V3 (deepseek-chat) + R1 (deepseek-reasoner)
    - doubao       豆包 Speed (火山引擎 Ark, OpenAI 兼容)
    - glm          智谱 GLM-5 (OpenAI 兼容)
    - siliconflow  SiliconFlow (OpenAI 兼容)
    - ollama       Ollama 本地 (OpenAI 兼容端点 + 原生 /api/chat)

每个 provider 函数签名:
    (prompt, system, temperature, max_tokens, timeout, providers_config, ...) -> Optional[str]

其中 `providers_config` 是该 provider 的配置字典 (从 llm_router.yaml 加载)。
"""

from utils.alpha.llm.providers.deepseek import call_deepseek, call_deepseek_reasoner
from utils.alpha.llm.providers.doubao import call_doubao
from utils.alpha.llm.providers.glm import call_glm
from utils.alpha.llm.providers.ollama import call_ollama, call_ollama_deep
from utils.alpha.llm.providers.omniroute import call_omniroute
from utils.alpha.llm.providers.siliconflow import call_siliconflow

__all__ = [
    "call_omniroute",
    "call_deepseek",
    "call_deepseek_reasoner",
    "call_doubao",
    "call_glm",
    "call_siliconflow",
    "call_ollama",
    "call_ollama_deep",
]
