"""跨层因果链构建器 — 三层面自我进化 Stage 2.

模块整合 8.4 — ARCHITECTURE_三层面进化 §第2阶段
输入: 三层诊断器产出的 List[RootCause]
输出: List[CausalChain] 跨层因果链 (ops → code → strategy 等)

设计原则:
    1. 规则驱动: 基于 RootCause.category + layer 匹配规则, 不使用 LLM (确定性)
    2. 不可变: 返回 CausalChain(frozen=True)
    3. 容错降级: 规则匹配失败返回空列表, 不阻塞
    4. 至少 2 节点: 单节点不成链 (退化为单点根因)
    5. 置信度衰减: 链 confidence = min(节点 confidence) * 0.9 (跨层推断降 10%)

因果链规则 (来自 ARCHITECTURE_三层面进化 §第2阶段):
    规则 1: 数据源失效链 (ops → code → strategy)
        ops.datasource_fail + code.system_check_fail(C3.*) + strategy.drift_alert
        描述: "数据源失败 → 代码层 C3 检查失败 → 策略层漂移告警"

    规则 2: PIT 违规链 (code → strategy)
        code.pit_violation + strategy.anti_cheat_low / reward_hacking_risk_high
        描述: "代码层 PIT 违规 → 策略层反作弊风险高"

    规则 3: Flag 变更链 (ops → code)
        ops.flag_instability + code.system_check_fail(C2.*)
        描述: "Flag 频繁变更 → 代码层 C2 配置检查失败"

    规则 4: 数据质量链 (ops → strategy)
        ops.data_quality_low / data_quality_stale + strategy.drift_health_low
        描述: "数据质量低 → 策略层漂移健康度低"

    规则 5: 风控事件链 (ops → strategy)
        ops.risk_event_burst + strategy.private_score_low / strategy_rollback_recommended
        描述: "风控事件爆发 → 策略层 Private Score 低"

    规则 6: 数据源冗余链 (ops → strategy)
        ops.datasource_redundancy_low + strategy.observation_insufficient
        描述: "数据源冗余度低 → 策略层观察期数据不足"

用法:
    from utils.alpha.causal_chain import CausalChainBuilder
    builder = CausalChainBuilder()
    chains = builder.build(causes)
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.root_cause import (  # noqa: E402
    LAYER_CODE,
    LAYER_OPS,
    LAYER_STRATEGY,
    CausalChain,
    RootCause,
)

# ============================================================
# 常量
# ============================================================

# 跨层推断置信度衰减系数
CROSS_LAYER_CONFIDENCE_DECAY = 0.9


class CausalChainBuilder:
    """跨层因果链构建器 (规则驱动, 确定性).

    接口: build(causes) -> List[CausalChain]

    设计:
        1. 规则驱动: 每条规则是一个函数, 返回 Optional[CausalChain]
        2. 规则匹配: 基于 RootCause.category + layer 过滤节点
        3. 因果顺序: 按 ops → code → strategy 排列节点
        4. 至少 2 节点: 单节点不成链
        5. 置信度: min(节点 confidence) * CROSS_LAYER_CONFIDENCE_DECAY
    """

    def __init__(self) -> None:
        self._rules: list[Callable[[list[RootCause], str], CausalChain | None]] = [
            self._rule_datasource_failure_chain,
            self._rule_pit_violation_chain,
            self._rule_flag_change_chain,
            self._rule_data_quality_chain,
            self._rule_risk_event_chain,
            self._rule_datasource_redundancy_chain,
        ]

    # ============================================================
    # 核心: 构建因果链
    # ============================================================
    def build(self, causes: list[RootCause]) -> list[CausalChain]:
        """从根因列表构建跨层因果链.

        Args:
            causes: 三层诊断器产出的根因列表

        Returns:
            List[CausalChain] 跨层因果链列表 (可能为空)
        """
        if not causes or len(causes) < 2:
            return []

        now = datetime.now(UTC).isoformat()
        chains: list[CausalChain] = []

        for rule_fn in self._rules:
            try:
                chain = rule_fn(causes, now)
                if chain is not None and len(chain.nodes) >= 2:
                    chains.append(chain)
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ) as e:
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.warning("因果链规则 %s 执行失败 (跳过): %s", rule_fn.__name__, e)
                continue

        logger.info(
            "因果链构建完成: %d 条规则触发, %d 条因果链",
            len(self._rules),
            len(chains),
        )
        return chains

    # ============================================================
    # 规则 1: 数据源失效链 (ops → code → strategy)
    # ============================================================
    def _rule_datasource_failure_chain(
        self, causes: list[RootCause], now: str
    ) -> CausalChain | None:
        """数据源失效链: ops.datasource_fail + code.C3.* + strategy.drift_alert.

        场景: Wind MCP 失败 → 代码层 C3 检查失败 → 策略层 IC 衰减/漂移
        """
        ops_nodes = [
            c
            for c in causes
            if c.layer == LAYER_OPS and c.category == "datasource_fail"
        ]
        code_nodes = [
            c
            for c in causes
            if c.layer == LAYER_CODE
            and c.category == "system_check_fail"
            and str(c.evidence.get("check_code", "")).startswith("C3")
        ]
        strategy_nodes = [
            c
            for c in causes
            if c.layer == LAYER_STRATEGY and c.category == "drift_alert"
        ]

        if not ops_nodes or not code_nodes:
            return None

        # 组装节点 (按因果顺序: ops → code → strategy)
        nodes: list[RootCause] = [ops_nodes[0], code_nodes[0]]
        if strategy_nodes:
            nodes.append(strategy_nodes[0])

        confidence = min(n.confidence for n in nodes) * CROSS_LAYER_CONFIDENCE_DECAY
        return CausalChain(
            chain_id=f"chain-datasource_failure-{now}",
            nodes=nodes,
            confidence=confidence,
            description=(
                "数据源失败 (ops) → 代码层 C3 检查失败 (code) → "
                "策略层漂移告警 (strategy): 数据源中断导致代码层连通性检查失败, "
                "进一步引发策略层模型漂移"
            ),
        )

    # ============================================================
    # 规则 2: PIT 违规链 (code → strategy)
    # ============================================================
    def _rule_pit_violation_chain(
        self, causes: list[RootCause], now: str
    ) -> CausalChain | None:
        """PIT 违规链: code.pit_violation + strategy.anti_cheat_low/rh_risk_high.

        场景: 代码层未来函数违规 → 策略层反作弊风险高
        """
        code_nodes = [
            c for c in causes if c.layer == LAYER_CODE and c.category == "pit_violation"
        ]
        # code_diagnoser 可能不直接诊断 pit_violation, 但 strategy_diagnoser 会
        # 因此也检查 strategy 层的 pit_violation
        code_nodes.extend(
            [
                c
                for c in causes
                if c.layer == LAYER_STRATEGY and c.category == "pit_violation"
            ]
        )
        strategy_nodes = [
            c
            for c in causes
            if c.layer == LAYER_STRATEGY
            and c.category in ("anti_cheat_low", "reward_hacking_risk_high")
        ]

        if not code_nodes or not strategy_nodes:
            return None

        nodes: list[RootCause] = [code_nodes[0], strategy_nodes[0]]
        confidence = min(n.confidence for n in nodes) * CROSS_LAYER_CONFIDENCE_DECAY
        return CausalChain(
            chain_id=f"chain-pit_violation-{now}",
            nodes=nodes,
            confidence=confidence,
            description=(
                "PIT 违规 (code) → 反作弊风险高 (strategy): 代码层未来函数违规"
                "导致策略层 reward hacking 风险升高, 评估结果不可信"
            ),
        )

    # ============================================================
    # 规则 3: Flag 变更链 (ops → code)
    # ============================================================
    def _rule_flag_change_chain(
        self, causes: list[RootCause], now: str
    ) -> CausalChain | None:
        """Flag 变更链: ops.flag_instability + code.C2.*.

        场景: Flag 频繁变更 → 代码层 C2 配置检查失败
        """
        ops_nodes = [
            c
            for c in causes
            if c.layer == LAYER_OPS
            and c.category in ("flag_instability", "flag_stability_low")
        ]
        code_nodes = [
            c
            for c in causes
            if c.layer == LAYER_CODE
            and c.category == "system_check_fail"
            and str(c.evidence.get("check_code", "")).startswith("C2")
        ]

        if not ops_nodes or not code_nodes:
            return None

        nodes: list[RootCause] = [ops_nodes[0], code_nodes[0]]
        confidence = min(n.confidence for n in nodes) * CROSS_LAYER_CONFIDENCE_DECAY
        return CausalChain(
            chain_id=f"chain-flag_change-{now}",
            nodes=nodes,
            confidence=confidence,
            description=(
                "Flag 频繁变更 (ops) → 配置检查失败 (code): Flag 变更"
                "导致代码层 C2 环境变量/配置检查失败"
            ),
        )

    # ============================================================
    # 规则 4: 数据质量链 (ops → strategy)
    # ============================================================
    def _rule_data_quality_chain(
        self, causes: list[RootCause], now: str
    ) -> CausalChain | None:
        """数据质量链: ops.data_quality_* + strategy.drift_health_low.

        场景: 数据质量低 → 策略层漂移健康度低
        """
        ops_nodes = [
            c
            for c in causes
            if c.layer == LAYER_OPS
            and c.category
            in (
                "data_quality_low",
                "data_quality_stale",
                "data_quality_no_monitoring",
            )
        ]
        strategy_nodes = [
            c
            for c in causes
            if c.layer == LAYER_STRATEGY
            and c.category in ("drift_health_low", "drift_alert")
        ]

        if not ops_nodes or not strategy_nodes:
            return None

        nodes: list[RootCause] = [ops_nodes[0], strategy_nodes[0]]
        confidence = min(n.confidence for n in nodes) * CROSS_LAYER_CONFIDENCE_DECAY
        return CausalChain(
            chain_id=f"chain-data_quality-{now}",
            nodes=nodes,
            confidence=confidence,
            description=(
                "数据质量低 (ops) → 策略漂移 (strategy): 数据质量报告过期/分数低"
                "导致策略层模型漂移健康度下降"
            ),
        )

    # ============================================================
    # 规则 5: 风控事件链 (ops → strategy)
    # ============================================================
    def _rule_risk_event_chain(
        self, causes: list[RootCause], now: str
    ) -> CausalChain | None:
        """风控事件链: ops.risk_event_burst + strategy.private_score_low/rollback.

        场景: 风控事件爆发 → 策略层 Private Score 低/建议回滚
        """
        ops_nodes = [
            c
            for c in causes
            if c.layer == LAYER_OPS
            and c.category in ("risk_event_burst", "risk_event_rate_high")
        ]
        strategy_nodes = [
            c
            for c in causes
            if c.layer == LAYER_STRATEGY
            and c.category
            in (
                "private_score_low",
                "strategy_rollback_recommended",
            )
        ]

        if not ops_nodes or not strategy_nodes:
            return None

        nodes: list[RootCause] = [ops_nodes[0], strategy_nodes[0]]
        confidence = min(n.confidence for n in nodes) * CROSS_LAYER_CONFIDENCE_DECAY
        return CausalChain(
            chain_id=f"chain-risk_event-{now}",
            nodes=nodes,
            confidence=confidence,
            description=(
                "风控事件爆发 (ops) → 策略表现差 (strategy): 风控事件频繁"
                "导致策略层 Private Score 下降, 可能需要回滚"
            ),
        )

    # ============================================================
    # 规则 6: 数据源冗余链 (ops → strategy)
    # ============================================================
    def _rule_datasource_redundancy_chain(
        self, causes: list[RootCause], now: str
    ) -> CausalChain | None:
        """数据源冗余链: ops.datasource_redundancy_low + strategy.observation_insufficient.

        场景: 数据源冗余度低 → 策略层观察期数据不足
        """
        ops_nodes = [
            c
            for c in causes
            if c.layer == LAYER_OPS and c.category == "datasource_redundancy_low"
        ]
        strategy_nodes = [
            c
            for c in causes
            if c.layer == LAYER_STRATEGY and c.category == "observation_insufficient"
        ]

        if not ops_nodes or not strategy_nodes:
            return None

        nodes: list[RootCause] = [ops_nodes[0], strategy_nodes[0]]
        confidence = min(n.confidence for n in nodes) * CROSS_LAYER_CONFIDENCE_DECAY
        return CausalChain(
            chain_id=f"chain-datasource_redundancy-{now}",
            nodes=nodes,
            confidence=confidence,
            description=(
                "数据源冗余度低 (ops) → 观察期数据不足 (strategy): "
                "数据源可用数少导致策略层观察期数据不足, 无法有效评估"
            ),
        )
