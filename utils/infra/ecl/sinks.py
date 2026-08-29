"""ECL Sink 协议 + EclEventSink 实现 (CTX-A1 T3).

log_decision 旁路双写 EventStore — 零签名变更, sink 失败静默降级.

设计原则:
    - R4: sink 挂点不改变 log_decision 既有签名与 JSONL 落盘行为;
          sink 失败必须静默降级 (与现有 except 返回 False 风格一致)
    - flag 检查在 EclEventSink.write 内部, 关闭时直接 return True (noop)
    - 注册常驻, 开关即时生效

事件映射规则 (spec_CTX-A1.md §2.3):
    - evaluator_report.vol_regime_suggestion 存在 → regime_shift_suggestion / portfolio
    - 有 public_score/private_score 且 sample_count>0 → strategy_eval / v9_baseline
    - 其余 → noop / orchestrator

依据: docs/集成记录/Wave10/CTX-A/spec_CTX-A1.md §2.1-2.3
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from utils.infra.ecl.event_store import EventStore

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path("data/ecl/ecl_events.db")


@runtime_checkable
class DecisionSink(Protocol):
    """决策 sink 协议 — log_decision 末尾遍历调用."""

    def write(self, record: dict[str, Any]) -> bool:
        """写入一条决策记录.

        Args:
            record: DecisionRecord.to_dict() 的完整字典.

        Returns:
            True=成功或 noop(flag off); False=失败 (禁止抛出).
        """
        ...


def _map_record_to_event(record: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """将 decisions.jsonl 记录映射为 (event_type, subject, payload).

    映射规则 (spec_CTX-A1.md §2.3):
        - evaluator_report.vol_regime_suggestion 存在 → regime_shift_suggestion
        - 有 public_score/private_score 且 sample_count>0 → strategy_eval
        - 其余 → noop
    """
    evaluator_report = record.get("evaluator_report") or {}

    if (
        isinstance(evaluator_report, dict)
        and "vol_regime_suggestion" in evaluator_report
    ):
        suggestion = evaluator_report["vol_regime_suggestion"] or {}
        indicators = suggestion.get("indicators", {})
        regime = suggestion.get("regime", {})
        payload = {
            "action": record.get("action", ""),
            "reason": record.get("reason", ""),
            "indicators": {
                "vix": indicators.get("vix"),
                "realized_vol": indicators.get("realized_vol"),
                "current_drawdown": indicators.get("current_drawdown"),
            },
            "regime": {
                "label": regime.get("label"),
                "confidence": regime.get("confidence"),
            },
            "suggested_weights": suggestion.get("suggested_weights"),
            "multipliers": suggestion.get("multipliers"),
            "constraints_applied": suggestion.get("constraints_applied"),
        }
        return "regime_shift_suggestion", "portfolio", payload

    public_score = record.get("public_score")
    private_score = record.get("private_score")
    sample_count = record.get("sample_count", 0)
    if public_score is not None and private_score is not None and sample_count > 0:
        payload = {
            "action": record.get("action", ""),
            "public_score": public_score,
            "private_score": private_score,
            "reward_hacking_risk": record.get("reward_hacking_risk", 0.0),
            "recommendation": record.get("recommendation", ""),
            "reason": record.get("reason", ""),
            "sample_count": sample_count,
        }
        return "strategy_eval", "v9_baseline", payload

    return (
        "noop",
        "orchestrator",
        {
            "action": record.get("action", ""),
            "reason": record.get("reason", ""),
        },
    )


class EclEventSink:
    """ECL 事件 sink — log_decision 旁路双写 EventStore.

    flag 检查在 write 内部 (USE_ECL_EVENT_LOG), 关闭时直接 return True (noop).
    sink 失败静默降级 (返回 False), 不阻塞主流程.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._store: EventStore | None = None

    def _get_store(self) -> EventStore:
        if self._store is None:
            self._store = EventStore(self._db_path)
        return self._store

    def write(self, record: dict[str, Any]) -> bool:
        """写入一条决策记录到 EventStore.

        flag off 时 noop (return True); 失败时 return False (禁止抛出).
        """
        try:
            from utils.infra.feature_flags import FeatureFlags

            if not FeatureFlags.is_enabled("USE_ECL_EVENT_LOG"):
                return True
        except Exception as e:  # noqa: BLE001 — flag 框架不可用时 noop
            logger.debug("ECL sink flag 检查失败(noop): %s", e)
            return True

        try:
            event_type, subject, payload = _map_record_to_event(record)
            ts = record.get("timestamp")
            store = self._get_store()
            store.append(event_type, subject, payload, ts=ts)
            return True
        except Exception as e:  # noqa: BLE001 — sink 失败静默降级
            logger.warning("ECL sink 写入失败(已降级, 不影响主流程): %s", e)
            return False


_sink_registry: list[DecisionSink] = []


def register_sink(sink: DecisionSink) -> None:
    """注册决策 sink 到全局注册表."""
    _sink_registry.append(sink)


def iter_registered_sinks() -> list[DecisionSink]:
    """返回已注册 sink 列表 (快照)."""
    return list(_sink_registry)


def clear_sinks() -> None:
    """清空注册表 (仅测试用)."""
    _sink_registry.clear()


def ensure_default_sink() -> EclEventSink | None:
    """确保默认 EclEventSink 已注册 (幂等).

    Returns:
        已注册的 EclEventSink 实例, 或 None (注册失败).
    """
    for sink in _sink_registry:
        if isinstance(sink, EclEventSink):
            return sink
    sink = EclEventSink()
    register_sink(sink)
    return sink
