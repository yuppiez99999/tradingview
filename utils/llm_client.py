"""
统一 LLM 客户端 (Unified LLM Client)
====================================

P1 重构目标: 收口分散在 utils/glm5_client.py 与 15_每日工作流/llm_client.py
两个客户端的调用入口, 对外暴露稳定的统一签名, 调用方不再感知底层 Provider。

统一对外 API (模块级函数, 兼容旧接口):
  - chat(prompt, system="", temperature=0.3, max_tokens=2000) -> Optional[str]
  - generate_analysis(prompt, temperature=0.3, max_tokens=2000) -> Optional[str]
  - test_connection() -> Dict[str, Any]

内部路由策略:
  1. 优先 utils.glm5_client (GLM-5 多模型路由, 质量最高)
  2. 降级 15_每日工作流.llm_client (DeepSeek→GLM→Ollama 三级链, doubao 已于 08-18 剔除)
  3. 两者均不可用时返回 None / 空 dict (优雅降级, 不抛异常)

兼容性说明:
  - 旧代码 from utils.ai_report_agent 或 import llm_client 的调用点无需改动,
    本模块已提供与 15_每日工作流/llm_client.py 完全一致的 chat/generate_analysis/test_connection。
  - glm5_client.chat 返回 Dict, 本层统一剥出 .content 字符串, 屏蔽签名差异。

P2 成本核算: 本层统一记录每次调用的 token 消耗与成本 (见 _record_usage),
数据落盘 reports/llm_usage.jsonl 供 AICoordinator 汇总。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, cast

from utils.datetime_utils import now_bj

# logger 有多个互斥实现 (utils.logger / stdlib logging), 静态类型以 Any 兼容
logger: Any = None
try:
    from utils.logger import get_logger

    logger = get_logger("llm_client")
except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
    import logging

    logger = logging.getLogger("llm_client")

# 全局配置: 成本单价 (元 / 1K tokens), P2 外置价格表将覆盖此处
_DEFAULT_PRICE_TABLE = {
    "glm-5": {"input": 0.01, "output": 0.03},
    "glm-5-air": {"input": 0.005, "output": 0.015},
    "deepseek-chat": {"input": 0.002, "output": 0.008},
    "deepseek-reasoner": {"input": 0.004, "output": 0.016},
    "default": {"input": 0.01, "output": 0.03},
}

_USAGE_LOG = Path(__file__).resolve().parent.parent / "reports" / "llm_usage.jsonl"


# ============================================================
# 底层客户端加载 (懒加载, 避免无 Key 环境导入报错)
# ============================================================


def _load_glm5():
    """加载主系统 GLM5 客户端, 失败返回 None。"""
    try:
        from utils.glm5_client import get_glm5_client

        return get_glm5_client()
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        OSError,
        RuntimeError,
    ) as e:
        logger.warning(f"统一LLM: glm5_client 加载失败 ({e})")
        return None


def _load_legacy():
    """加载 15_每日工作流 三级降级客户端, 失败返回 None。"""
    try:
        import sys
        from pathlib import Path as _P

        _path = _P(__file__).resolve().parent.parent.parent / "15_每日工作流"
        if _path.exists() and str(_path) not in sys.path:
            sys.path.insert(0, str(_path))
        import llm_client as _legacy

        return _legacy
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        OSError,
        RuntimeError,
    ) as e:
        logger.warning(f"统一LLM: 15_每日工作流/llm_client 加载失败 ({e})")
        return None


_glm5_client = None
_legacy_client = None
_clients_loaded = False


def _ensure_clients():
    global _glm5_client, _legacy_client, _clients_loaded
    if _clients_loaded:
        return
    _glm5_client = _load_glm5()
    _legacy_client = _load_legacy()
    _clients_loaded = True


# ============================================================
# 成本记录 (P2 前置: 落盘 token 用量)
# ============================================================


def _record_usage(
    model: str, prompt_tokens: int, completion_tokens: int, latency_ms: int, source: str
) -> None:
    """记录单次 LLM 调用用量到 reports/llm_usage.jsonl。"""
    price = _DEFAULT_PRICE_TABLE.get(model) or _DEFAULT_PRICE_TABLE["default"]
    cost = (prompt_tokens / 1000.0) * price["input"] + (
        completion_tokens / 1000.0
    ) * price["output"]
    row = {
        "ts": now_bj().isoformat(timespec="seconds"),
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_cny": round(cost, 6),
        "latency_ms": latency_ms,
        "source": source,
    }
    try:
        _USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
        pass  # 用量记录失败不影响主流程


# ============================================================
# 统一对外 API
# ============================================================


def chat(
    prompt: str,
    system: str = "",
    temperature: float = 0.3,
    max_tokens: int = 2000,
    timeout: int | None = None,
) -> str | None:
    """统一对话接口, 返回纯文本 (屏蔽底层 dict/str 差异)。

    优先 GLM5, 降级 15_每日工作流 三级链。

    H2 修复 (2026-09-13): 新增 ``timeout`` 透传 — 原实现不接收超时参数,
    ai_decision 等调用方传入的 timeout 被静默丢弃, 盘中决策链可被单个挂起
    请求无限阻塞。GLM5 底层支持 per-request 超时 (kwargs, 默认 30s);
    legacy 三级链超时由其内部 provider 超时 + MC2 熔断器治理, 不透传。
    """
    _ensure_clients()
    start = time.time()

    # 1. 主路径: GLM5
    if _glm5_client is not None:
        try:
            glm5_kwargs: dict[str, Any] = {}
            if timeout is not None:
                glm5_kwargs["timeout"] = int(timeout)
            resp = _glm5_client.chat(
                message=prompt,
                system_prompt=system or None,
                temperature=temperature,
                max_tokens=max_tokens,
                **glm5_kwargs,
            )
            if isinstance(resp, dict):
                content = resp.get("content")
                model = resp.get("model", "glm-5")
                usage = resp.get("usage") or {}
                _record_usage(
                    model,
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                    int((time.time() - start) * 1000),
                    "glm5",
                )
                if content:
                    return cast(str, content)
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning(f"统一LLM: GLM5 chat 失败 ({e}), 降级")

    # 2. 降级路径: 15_每日工作流 三级链
    if _legacy_client is not None:
        try:
            result = _legacy_client.chat(
                prompt=prompt,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            _record_usage(
                "legacy-chain",
                len(prompt) // 2,
                0,
                int((time.time() - start) * 1000),
                "legacy",
            )
            if result:
                return cast(str, result)
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning(f"统一LLM: legacy chat 失败 ({e})")

    return None


def generate_analysis(
    prompt: str, temperature: float = 0.3, max_tokens: int = 2000
) -> str | None:
    """生成分析文本 (兼容旧接口), 等价于 chat(prompt, 金融分析系统提示)。"""
    return chat(
        prompt=prompt,
        system="你是一个专业的金融分析助手。",
        temperature=temperature,
        max_tokens=max_tokens,
    )


def test_connection() -> dict[str, Any]:
    """测试连接可用性, 返回各底层客户端状态。"""
    _ensure_clients()
    result: dict[str, Any] = {"glm5": False, "legacy": False}
    if _glm5_client is not None:
        try:
            result["glm5"] = bool(_glm5_client.is_ready())
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
            result["glm5"] = False
    if _legacy_client is not None and hasattr(_legacy_client, "test_connection"):
        try:
            legacy_res = _legacy_client.test_connection()
            result["legacy"] = (
                bool(legacy_res.get("success"))
                if isinstance(legacy_res, dict)
                else bool(legacy_res)
            )
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
            result["legacy"] = False
    result["available"] = result["glm5"] or result["legacy"]
    return result


# 便捷别名 (兼容部分调用方使用 quick_chat)
def quick_chat(message: str, **kwargs) -> str:
    return chat(message, **kwargs) or ""


def chat_deep(
    prompt: str,
    system: str = "",
    temperature: float = 0.3,
    max_tokens: int = 4000,
    timeout: int | None = None,
) -> str | None:
    """深度思考模式 (兼容旧接口), 复用统一 chat 并放宽 max_tokens。

    旧 15_每日工作流/llm_client.py 的 chat_deep 使用 DeepSeek R1 推理模型;
    统一层当前路由到 GLM5/三级链, 深度推理由底层 provider 决定。
    """
    return chat(
        prompt=prompt,
        system=system,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
