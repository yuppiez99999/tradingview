"""
冲突检测插件 — 把 ai_coordinator.py resolve_conflicts() 多数投票抽成插件

原 resolve_conflicts() 逻辑 (ai_coordinator.py:330-391):
    1. 收集所有标的
    2. 对每个标的, 收集各 source 的 action
    3. 统计 buy/sell/hold count
    4. has_conflict = buy_count > 0 and sell_count > 0
    5. 多数投票: buy 最多 → BUY, sell 最多 → SELL, 否则 → HOLD
    6. confidence = max(buy, sell, hold) / len(actions)

抽成 MajorityVotePlugin, 返回结构与旧 resolve_conflicts 完全一致 (向后兼容).
"""
from __future__ import annotations

from typing import Any

from .base import ConflictContext, ConflictDetectionPlugin, ConflictResult


class MajorityVotePlugin(ConflictDetectionPlugin):
    """多数投票冲突检测 — 对应原 resolve_conflicts() 全部逻辑

    对每个标的统计 BUY/SELL/HOLD 票数, 多数投票决定 resolved_action.
    has_conflict = 同时存在 BUY 和 SELL.
    """

    @property
    def name(self) -> str:
        return "majority_vote"

    @property
    def priority(self) -> int:
        return 50

    def can_handle(self, context: ConflictContext) -> bool:
        return bool(context.decisions_by_source)

    def handle(self, context: ConflictContext) -> ConflictResult:
        decisions_by_source = context.decisions_by_source

        all_tickers: set[str] = set()
        for decisions in decisions_by_source.values():
            all_tickers.update(decisions.keys())

        resolved: dict[str, dict[str, Any]] = {}
        for ticker in all_tickers:
            actions: dict[str, str] = {}
            for source, decisions in decisions_by_source.items():
                if ticker in decisions:
                    actions[source] = decisions[ticker]

            buy_count = sum(1 for a in actions.values() if a == "BUY")
            sell_count = sum(1 for a in actions.values() if a == "SELL")
            hold_count = sum(1 for a in actions.values() if a == "HOLD")

            has_conflict = buy_count > 0 and sell_count > 0

            if buy_count > sell_count and buy_count > hold_count:
                resolved_action = "BUY"
                confidence = buy_count / len(actions)
            elif sell_count > buy_count and sell_count > hold_count:
                resolved_action = "SELL"
                confidence = sell_count / len(actions)
            else:
                resolved_action = "HOLD"
                confidence = max(buy_count, sell_count, hold_count) / len(actions)

            resolved[ticker] = {
                "actions": actions,
                "conflict": has_conflict,
                "resolved_action": resolved_action,
                "confidence": round(confidence, 2),
                "buy_votes": buy_count,
                "sell_votes": sell_count,
                "hold_votes": hold_count,
            }

        return ConflictResult(resolved=resolved)


class WeightedVotePlugin(ConflictDetectionPlugin):
    """加权投票冲突检测 — MajorityVotePlugin 的扩展版

    按 context.source_weights 对各 source 加权, 权重缺失默认 1.0.
    当 source_weights 非空时优先于 MajorityVotePlugin (priority 更高).
    """

    @property
    def name(self) -> str:
        return "weighted_vote"

    @property
    def priority(self) -> int:
        return 60

    def can_handle(self, context: ConflictContext) -> bool:
        return bool(context.decisions_by_source) and bool(context.source_weights)

    def handle(self, context: ConflictContext) -> ConflictResult:
        decisions_by_source = context.decisions_by_source
        weights = context.source_weights

        all_tickers: set[str] = set()
        for decisions in decisions_by_source.values():
            all_tickers.update(decisions.keys())

        resolved: dict[str, dict[str, Any]] = {}
        for ticker in all_tickers:
            actions: dict[str, str] = {}
            for source, decisions in decisions_by_source.items():
                if ticker in decisions:
                    actions[source] = decisions[ticker]

            buy_weight = sum(weights.get(s, 1.0) for s, a in actions.items() if a == "BUY")
            sell_weight = sum(weights.get(s, 1.0) for s, a in actions.items() if a == "SELL")
            hold_weight = sum(weights.get(s, 1.0) for s, a in actions.items() if a == "HOLD")
            total_weight = buy_weight + sell_weight + hold_weight

            buy_count = sum(1 for a in actions.values() if a == "BUY")
            sell_count = sum(1 for a in actions.values() if a == "SELL")
            hold_count = sum(1 for a in actions.values() if a == "HOLD")
            has_conflict = buy_count > 0 and sell_count > 0

            if total_weight <= 0:
                resolved_action = "HOLD"
                confidence = 0.0
            elif buy_weight > sell_weight and buy_weight > hold_weight:
                resolved_action = "BUY"
                confidence = buy_weight / total_weight
            elif sell_weight > buy_weight and sell_weight > hold_weight:
                resolved_action = "SELL"
                confidence = sell_weight / total_weight
            else:
                resolved_action = "HOLD"
                confidence = hold_weight / total_weight if total_weight > 0 else 0.0

            resolved[ticker] = {
                "actions": actions,
                "conflict": has_conflict,
                "resolved_action": resolved_action,
                "confidence": round(confidence, 2),
                "buy_votes": buy_count,
                "sell_votes": sell_count,
                "hold_votes": hold_count,
            }

        return ConflictResult(resolved=resolved)


def create_default_conflict_plugins() -> list[ConflictDetectionPlugin]:
    """创建默认冲突检测插件 (按 priority 降序)

    Returns:
        [WeightedVote, MajorityVote]
    """
    return [WeightedVotePlugin(), MajorityVotePlugin()]
