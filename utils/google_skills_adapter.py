"""google-skills 适配层 (Adapter).

把借鉴自 google-skills 的 4 个新模块 (tier_safety / prompt_registry /
eval_flywheel / experience_rag) 桥接到本系统现有模块, 集中管理集成逻辑,
不修改现有模块核心代码.

桥接点:
    1. glm5_client.GLM5Client.chat + prompt_registry
       → chat_with_prompt(client, prompt_name, **vars)
    2. alpha_evaluator.AlphaEvaluator + eval_flywheel.EvalFlywheel
       → evaluate_with_flywheel(evaluator, factor_result, returns)
    3. cairn/ 知识库 + experience_rag.ExperienceRAG
       → query_experience(query, top_k)
    4. ai_coordinator.AICoordinator + prompt_registry
       → route_with_prompt(task_type, prompt_name, **vars)

用法:
    from utils.google_skills_adapter import (
        chat_with_prompt, query_experience, evaluate_with_flywheel
    )
    answer = chat_with_prompt(client, "glm5_daily", symbol="600519")
    exp = query_experience("对冲成本过滤最佳实践")
    flywheel_result = evaluate_with_flywheel(alpha_eval, factor_lib, returns)

集成日期: 2026-08-25
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from .eval_flywheel import EvalCase, EvalFlywheel, EvalMetric
from .experience_rag import ExperienceRAG
from .prompt_registry import PromptRegistry

logger = logging.getLogger("google_skills_adapter")

_PROMPT_REGISTRY: Optional[PromptRegistry] = None
_EXPERIENCE_RAG: Optional[ExperienceRAG] = None
_EVAL_FLYWHEEL: Optional[EvalFlywheel] = None


# ============================================================
# 单例获取 (延迟初始化)
# ============================================================


def get_prompt_registry() -> PromptRegistry:
    global _PROMPT_REGISTRY
    if _PROMPT_REGISTRY is None:
        _PROMPT_REGISTRY = PromptRegistry()
    return _PROMPT_REGISTRY


def get_experience_rag() -> ExperienceRAG:
    global _EXPERIENCE_RAG
    if _EXPERIENCE_RAG is None:
        _EXPERIENCE_RAG = ExperienceRAG()
    return _EXPERIENCE_RAG


def get_eval_flywheel() -> EvalFlywheel:
    global _EVAL_FLYWHEEL
    if _EVAL_FLYWHEEL is None:
        _EVAL_FLYWHEEL = EvalFlywheel()
    return _EVAL_FLYWHEEL


# ============================================================
# 桥接 1: GLM5Client + PromptRegistry
# ============================================================


def chat_with_prompt(
    client: Any,
    prompt_name: str,
    *,
    version: Optional[int] = None,
    history: Optional[list[dict[str, str]]] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    **prompt_vars: Any,
) -> dict[str, Any]:
    """从 prompt_registry 拉模板 + 组装变量 + 调 GLM5Client.chat.

    Args:
        client: GLM5Client 实例.
        prompt_name: 注册的 prompt 名称.
        version: 指定版本, None 取最新.
        **prompt_vars: 模板变量替换.

    Returns:
        GLM5Client.chat 的返回 dict.
    """
    reg = get_prompt_registry()
    try:
        # assemble 内部会 reg.get(name), 未注册时抛 KeyError 由下方回退处理
        system_prompt = reg.assemble(prompt_name, version=version, **prompt_vars)
        message = prompt_vars.pop("message", "")
        return client.chat(
            message,
            history=history,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except KeyError:
        logger.warning("prompt '%s' 未注册, 回退默认 system_prompt", prompt_name)
        return client.chat(
            prompt_vars.get("message", ""),
            history=history,
            temperature=temperature,
            max_tokens=max_tokens,
        )


# ============================================================
# 桥接 2: AlphaEvaluator + EvalFlywheel
# ============================================================


def evaluate_with_flywheel(
    evaluator: Any,
    factor_library_result: Any,
    forward_returns: Optional[dict[str, float]] = None,
    *,
    metrics: Optional[list[EvalMetric]] = None,
) -> dict[str, Any]:
    """把 alpha_evaluator 输出转 EvalCase → 进飞轮迭代.

    Args:
        evaluator: AlphaEvaluator 实例.
        factor_library_result: AlphaFactorLibrary.compute_all() 结果.
        forward_returns: {symbol: 收益率}.
        metrics: 飞轮评估指标, 默认 [IC_1D, IC_IR, SHARPE].

    Returns:
        {"alpha_report": ..., "flywheel_result": ...}
    """
    alpha_report = evaluator.evaluate_all(factor_library_result, forward_returns)
    cases: list[EvalCase] = []
    for ev in alpha_report.evaluations:
        cases.append(
            EvalCase(
                prompt=f"因子 {ev['factor_name']} 预测",
                response=str(ev.get("ic_1d", 0.0)),
                reference=str(ev.get("ic_ir", 0.0)),
                metadata={"factor": ev["factor_name"], "category": ev.get("category", "")},
            )
        )
    fw = get_eval_flywheel()
    metrics = metrics or [EvalMetric.IC_1D, EvalMetric.IC_IR, EvalMetric.SHARPE]
    fw_result = fw.grade(fw.prepare_data(cases, source="alpha_evaluator"), metrics)
    return {"alpha_report": alpha_report.to_dict(), "flywheel_result": fw_result.to_dict()}


# ============================================================
# 桥接 3: cairn/ + ExperienceRAG
# ============================================================


def query_experience(
    query: str,
    *,
    corpus: str = "cairn",
    top_k: int = 3,
    generate: bool = False,
) -> Any:
    """查询 cairn 经验库.

    Args:
        query: 查询文本.
        corpus: 语料库名, 默认 cairn.
        top_k: 检索 top-k.
        generate: True 调 grounded_generate (检索+LLM生成), False 仅检索.

    Returns:
        generate=True 返回 str, 否则返回 list[RetrievalResult].
    """
    rag = get_experience_rag()
    if generate:
        return rag.grounded_generate(query, corpus, top_k=top_k)
    return rag.retrieve(query, corpus, top_k=top_k)


def ingest_cairn_experiences(*, limit: int = 100) -> int:
    """一键摄入 cairn/ 知识专题到 RAG 语料库."""
    return get_experience_rag().ingest_cairn(limit=limit)


# ============================================================
# 桥接 4: AICoordinator + PromptRegistry (便捷封装)
# ============================================================


def route_with_prompt(
    coordinator: Any,
    task_type: Any,
    prompt_name: str,
    *,
    message: str = "",
    **prompt_vars: Any,
) -> Any:
    """用 prompt_registry 组装 prompt → 调 ai_coordinator 路由.

    Args:
        coordinator: AICoordinator 实例.
        task_type: TaskType 枚举.
        prompt_name: 注册的 prompt 名称.
        message: 用户消息 (与 system prompt 分离).
        **prompt_vars: 模板变量.

    Returns:
        coordinator.route_task 的返回.
    """
    reg = get_prompt_registry()
    try:
        system_prompt = reg.assemble(prompt_name, **prompt_vars)
    except KeyError:
        system_prompt = ""
    return coordinator.route_task(
        task_type=task_type,
        message=message,
        system_prompt=system_prompt,
        **prompt_vars,
    )


__all__ = [
    "get_prompt_registry",
    "get_experience_rag",
    "get_eval_flywheel",
    "chat_with_prompt",
    "evaluate_with_flywheel",
    "query_experience",
    "ingest_cairn_experiences",
    "route_with_prompt",
]
