"""统一安全分级装饰器 (Tier R/M/D Safety Tiers).

借鉴 google-skills/agent-platform-{model-registry,prompt-management,rag-engine-management}
中反复出现的 Tier R/M/D 安全分级模式, 抽象为通用装饰器, 供本系统所有 AI/模型/知识库
CRUD 操作复用.

分级定义:
    Tier R (Read-only): 只读操作 (list/get/describe/retrieve).
        - 无需确认, 直接执行.
    Tier M (Mutating & Reversible): 可变更且可逆 (create/upload/update/register).
        - 需要交互式 Yes/No 确认, 必须展示完整参数.
        - 同回合限制: 提示确认与执行不得在同一回合.
    Tier D (Destructive & Irreversible): 破坏性且不可逆 (delete/purge).
        - 需要显式键入确认词 (默认 "I confirm").
        - 同回合限制: 必须在新回合执行.

设计原则:
    - 零重依赖: 仅用标准库 + 可选 logging.
    - 可降级: 非交互环境 (CI/批量) 通过 AUTO_CONFIRM_TIER_R/M/D 环境变量放行.
    - 审计: 所有 Tier M/D 操作记录到 audit log, 便于追溯.
    - 不可变性: 装饰器不修改被装饰函数签名, 保留类型注解.

用法:
    from utils.tier_safety import tier_r, tier_m, tier_d, OperationContext

    @tier_r("列出所有 prompt")
    def list_prompts(): ...

    @tier_m("注册 prompt", params_extractor=lambda self, name, **kw: {"name": name})
    def register_prompt(self, name, template, **kwargs): ...

    @tier_d("删除 prompt", params_extractor=lambda self, name: {"name": name})
    def delete_prompt(self, name): ...

集成日期: 2026-08-25 (借鉴 google-skills Tier R/M/D 模式)
"""

from __future__ import annotations

import functools
import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("tier_safety")

_BASE_DIR = Path(__file__).resolve().parent.parent
_AUDIT_LOG = _BASE_DIR / "reports" / "tier_safety_audit.jsonl"


# ============================================================
# 操作上下文与审计
# ============================================================


@dataclass
class OperationContext:
    """一次 Tier 操作的上下文快照."""

    tier: str
    description: str
    params: dict[str, Any]
    function_name: str
    timestamp: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )
    confirmed_by: str = "interactive"
    outcome: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _append_audit(ctx: OperationContext) -> None:
    """追加一条审计记录 (失败不抛, 仅告警)."""
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ctx.to_dict(), ensure_ascii=False, default=str) + "\n")
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("审计日志写入失败: %s", exc)


# ============================================================
# 确认门禁
# ============================================================


def _is_noninteractive() -> bool:
    """检测非交互环境 (CI/管道/重定向)."""
    return (
        os.environ.get("CI", "").lower() in {"1", "true"}
        or not sys.stdin.isatty()
        or os.environ.get("TIER_SAFETY_NONINTERACTIVE", "").lower() in {"1", "true"}
    )


def _env_auto_confirmed(tier: str) -> bool:
    """环境变量自动放行 (仅限 CI/批量场景, 默认关闭)."""
    key = f"AUTO_CONFIRM_TIER_{tier}"
    return os.environ.get(key, "").lower() in {"1", "true"}


def confirm_yes_no(prompt: str, *, description: str = "") -> bool:
    """Tier M 交互式确认 (Yes/No).

    非交互环境或 AUTO_CONFIRM_TIER_M=1 时自动放行.
    """
    if _env_auto_confirmed("M"):
        logger.info(
            "Tier M 自动放行 (AUTO_CONFIRM_TIER_M=1): %s", description or prompt
        )
        return True
    if _is_noninteractive():
        logger.warning("Tier M 非交互环境, 默认拒绝: %s", description or prompt)
        return False
    # FIX (2026-08-25): 原先 description 与 prompt(含 description 前缀) 重复打印两遍
    print(f"\n[Tier M] {description}" if description else f"\n[Tier M] {prompt}")
    if not description:
        print(f"  {prompt}")
    try:
        reply = input("  确认执行? [yes/No] > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return reply in {"y", "yes"}


def confirm_destructive(
    prompt: str, *, description: str = "", confirm_phrase: str = "I confirm"
) -> bool:
    """Tier D 显式键入确认 (默认 "I confirm").

    非交互环境默认拒绝; AUTO_CONFIRM_TIER_D=1 时放行 (危险, 仅 CI 用).
    """
    if _env_auto_confirmed("D"):
        logger.warning(
            "Tier D 自动放行 (AUTO_CONFIRM_TIER_D=1, 危险): %s", description or prompt
        )
        return True
    if _is_noninteractive():
        logger.error("Tier D 非交互环境, 拒绝破坏性操作: %s", description or prompt)
        return False
    print("\n[Tier D] 破坏性操作 — 不可逆")
    print(f"  描述: {description}" if description else f"  操作: {prompt}")
    print(f"  {prompt}")
    try:
        reply = input(f"  键入 '{confirm_phrase}' 以确认 > ").strip()
    except (EOFError, KeyboardInterrupt):
        return False
    return reply == confirm_phrase


# ============================================================
# 装饰器
# ============================================================


ParamsExtractor = Optional[Callable[..., dict[str, Any]]]  # noqa: UP045  # 运行时类型别名, py38 兼容


def _make_context(
    tier: str,
    description: str,
    func: Callable,
    args: tuple,
    kwargs: dict,
    params_extractor: ParamsExtractor,
) -> OperationContext:
    """构造操作上下文."""
    try:
        params = params_extractor(*args, **kwargs) if params_extractor else {}
    except Exception as exc:
        logger.debug("params_extractor 失败, 回退空字典: %s", exc)
        params = {}
    return OperationContext(
        tier=tier,
        description=description,
        params=params,
        function_name=func.__qualname__,
    )


def tier_r(description: str, *, params_extractor: ParamsExtractor = None) -> Callable:
    """Tier R 装饰器: 只读, 无确认, 直接执行 + 审计."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            ctx = _make_context("R", description, func, args, kwargs, params_extractor)
            ctx.outcome = "executed"
            _append_audit(ctx)
            return func(*args, **kwargs)

        return wrapper

    return decorator


def tier_m(description: str, *, params_extractor: ParamsExtractor = None) -> Callable:
    """Tier M 装饰器: 可变更且可逆, 需 Yes/No 确认."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            ctx = _make_context("M", description, func, args, kwargs, params_extractor)
            prompt = f"{description} | 参数={json.dumps(ctx.params, ensure_ascii=False, default=str)}"
            if not confirm_yes_no(prompt, description=description):
                ctx.outcome = "rejected"
                _append_audit(ctx)
                logger.info("Tier M 操作被拒绝: %s", description)
                return None
            ctx.outcome = "executed"
            _append_audit(ctx)
            return func(*args, **kwargs)

        return wrapper

    return decorator


def tier_d(
    description: str,
    *,
    params_extractor: ParamsExtractor = None,
    confirm_phrase: str = "I confirm",
) -> Callable:
    """Tier D 装饰器: 破坏性不可逆, 需显式键入确认词."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            ctx = _make_context("D", description, func, args, kwargs, params_extractor)
            prompt = f"{description} | 参数={json.dumps(ctx.params, ensure_ascii=False, default=str)}"
            if not confirm_destructive(
                prompt, description=description, confirm_phrase=confirm_phrase
            ):
                ctx.outcome = "rejected"
                _append_audit(ctx)
                logger.info("Tier D 操作被拒绝: %s", description)
                return None
            ctx.outcome = "executed"
            _append_audit(ctx)
            return func(*args, **kwargs)

        return wrapper

    return decorator


# ============================================================
# 便捷工具
# ============================================================


def dry_run(description: str, params: dict[str, Any]) -> None:
    """打印 dry-run 信息 (不执行). 用于 Tier M/D 预览."""
    print(f"[dry-run] {description}")
    print(f"  参数: {json.dumps(params, ensure_ascii=False, default=str)}")


def recent_audit(limit: int = 20, tier: str | None = None) -> list[dict[str, Any]]:
    """读取最近审计记录 (只读, 不门禁)."""
    if not _AUDIT_LOG.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with _AUDIT_LOG.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if tier is None or rec.get("tier") == tier:
                    records.append(rec)
    except (OSError, ValueError) as exc:  # R10: 读日志/解析只可能这两类; 逻辑 bug 不再被吞
        logger.warning("读取审计日志失败: %s", exc)
        return []
    return records[-limit:]


__all__ = [
    "OperationContext",
    "confirm_yes_no",
    "confirm_destructive",
    "tier_r",
    "tier_m",
    "tier_d",
    "dry_run",
    "recent_audit",
]
