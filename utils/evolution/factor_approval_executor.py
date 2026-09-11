"""因子审批执行器 (FactorApprovalExecutor) — L3 因子类人工审批工单的消费端.

模块整合 8.4 — ARCHITECTURE_自我进化框架 §6.3 (AutoFactorFactory 生产接线骨架)
任务: T3.3 (Phase 3 进化层) — 接线设计 v0 (2026-08-29)

职责:
    消费 EvolutionMemory 中已获人工审批的 L3 因子类工单
    (factor_deploy / factor_retire / factor_generate), 在
    Feature Flag + Guard (含 Kill Switch) 闸门通过后执行对应动作.

接线设计 v0 (四段式):
    A. 提案生成 (FactorScanBridge, 只读) — 未实现 (本骨架外, 仅生成工单)
    B. 路由 (route_proposal → L3 人工审批工单) — 复用编排器, 编排器零改动
    C. 审批执行 (本模块)                    ← 本次交付
    D. 反馈闭环 (monitor 更新 ic_history)    — AutoFactorFactory 内置

三重闸门 (决策路径 fail-close):
    1. HC-1 Feature Flag: USE_AUTO_FACTOR_FACTORY 未启用 → 全部跳过
    2. Guard 防线5: Kill Switch L2/L3 熔断 → 拒绝并回写 rejected
    3. Guard 其余防线 (频率/回滚就绪/影子隔离) → 拒绝并回写 rejected

状态机 (Memory, HC-2 全量审计):
    approved → executed / rejected / failed

设计约束:
    - HC-4: 本模块只消费已人工审批的工单, 不产生提案
    - 影子优先: factor_generate 只产出候选不部署; factor_deploy 的影子
      天数由人工审批流程满足 Guard 防线4 要求
    - 幂等: 同实例重复执行同一工单只处理一次 (内存级, 骨架阶段不持久化)
    - 频率: 人工审批为批处理语义, 不适用单模块 24h=1 的自进化频率限制,
      默认放宽至 guard_daily_evolution_limit=10 (仍是硬防线, 防批量失控)

用法 (骨架阶段不接生产, 供人工触发/测试):
    from utils.evolution.factor_approval_executor import FactorApprovalExecutor

    executor = FactorApprovalExecutor()
    summary = executor.execute_pending_approvals()
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ============================================================
# 常量 (对齐 utils/evolution/memory.py, 补充 generate)
# ============================================================

ACTION_FACTOR_DEPLOY = "factor_deploy"
ACTION_FACTOR_RETIRE = "factor_retire"
ACTION_FACTOR_GENERATE = "factor_generate"

# 本执行器消费的工单动作集合
FACTOR_ACTIONS: frozenset[str] = frozenset(
    {ACTION_FACTOR_DEPLOY, ACTION_FACTOR_RETIRE, ACTION_FACTOR_GENERATE}
)

# 人工审批后的工单状态 (由审批方写入)
STATUS_APPROVED = "approved"

# 层级/状态 (对齐 memory.py 常量值)
LEVEL_L3 = "L3"
STATUS_EXECUTED = "executed"
STATUS_FAILED = "failed"
STATUS_REJECTED = "rejected"

# 因子工单的目标模块
TARGET_MODULE = "auto_factor_factory"

# 审计目标模块 (独立于 auto_factor_factory,
# 避免审计记录占用因子模块的 Guard 频率配额)
AUDIT_MODULE = "factor_approval_executor"

# Feature Flag (与 utils/evolution/auto_factor_factory.FLAG_AUTO_FACTORY 对齐)
FLAG_AUTO_FACTORY = "USE_AUTO_FACTOR_FACTORY"


class FactorApprovalExecutor:
    """L3 因子类人工审批工单执行器.

    Args:
        factory: AutoFactorFactory 实例 (None=懒加载)
        memory: EvolutionMemory 实例 (None=懒加载)
        guard: EvolutionGuard 实例 (None=懒加载, 自动注入 memory + kill_switch)
        kill_switch: KillSwitch 实例 (None=懒加载, 注入 Guard 防线5)
        feature_flag: Feature Flag 名 (默认 USE_AUTO_FACTOR_FACTORY)
        shadow_days_required: L3 影子天数要求 (默认 5, 对齐 Guard 默认)
        guard_daily_evolution_limit: 同模块 24h 进化上限 (默认 10,
            人工审批批处理语义, 不适用默认 1 次/日的自进化频率限制)
        max_generate_per_cycle: 单轮 generate 候选数量上限 (默认 3)
    """

    def __init__(
        self,
        factory: Any | None = None,
        memory: Any | None = None,
        guard: Any | None = None,
        kill_switch: Any | None = None,
        feature_flag: str = FLAG_AUTO_FACTORY,
        shadow_days_required: int = 5,
        guard_daily_evolution_limit: int = 10,
        max_generate_per_cycle: int = 3,
    ) -> None:
        self._factory = factory
        self._memory = memory
        self._guard = guard
        self._kill_switch = kill_switch
        self.feature_flag = feature_flag
        self.shadow_days_required = shadow_days_required
        self.guard_daily_evolution_limit = guard_daily_evolution_limit
        self.max_generate_per_cycle = max_generate_per_cycle

        # 内存级幂等去重 (骨架阶段)
        self._executed_ids: set[str] = set()

        logger.debug(
            "FactorApprovalExecutor 初始化: flag=%s, factory=%s, memory=%s, "
            "guard=%s, kill_switch=%s",
            feature_flag,
            "yes" if factory else "lazy",
            "yes" if memory else "lazy",
            "yes" if guard else "lazy",
            "yes" if kill_switch else "lazy",
        )

    # ============================================================
    # Feature Flag (HC-1)
    # ============================================================

    def _check_feature_flag(self, name: str) -> bool:
        """检查 Feature Flag (HC-1), 检查失败默认禁用 (fail-close)."""
        try:
            from utils.infra.feature_flags import is_enabled

            return bool(is_enabled(name))
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
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.warning("Feature Flag 检查失败, 默认禁用: %s (%s)", name, e)
            return False

    @property
    def enabled(self) -> bool:
        """是否启用 (HC-1)."""
        return self._check_feature_flag(self.feature_flag)

    # ============================================================
    # 组件懒加载
    # ============================================================

    def _get_memory(self) -> Any:
        """懒加载 EvolutionMemory."""
        if self._memory is None:
            from utils.evolution.memory import EvolutionMemory

            self._memory = EvolutionMemory()
        return self._memory

    def _get_kill_switch(self) -> Any | None:
        """懒加载 KillSwitch (失败返回 None, 不阻塞)."""
        if self._kill_switch is None:
            try:
                from utils.kill_switch import KillSwitch

                self._kill_switch = KillSwitch()
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
                # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
                logger.warning("KillSwitch 加载失败, Guard 熔断检查将跳过: %s", e)
                self._kill_switch = None
        return self._kill_switch

    def _get_guard(self) -> Any:
        """懒加载 EvolutionGuard (注入 memory + kill_switch)."""
        if self._guard is None:
            from utils.evolution.guard import EvolutionGuard

            self._guard = EvolutionGuard(
                memory=self._get_memory(),
                kill_switch=self._get_kill_switch(),
                daily_evolution_limit=self.guard_daily_evolution_limit,
                shadow_days_required=self.shadow_days_required,
            )
        return self._guard

    def _get_factory(self) -> Any:
        """懒加载 AutoFactorFactory (注入 memory + guard 做审计与守卫)."""
        if self._factory is None:
            from utils.evolution.auto_factor_factory import AutoFactorFactory

            self._factory = AutoFactorFactory(
                memory=self._get_memory(), guard=self._get_guard()
            )
        return self._factory

    # ============================================================
    # 核心入口
    # ============================================================

    def execute_pending_approvals(
        self, approved_status: str = STATUS_APPROVED
    ) -> dict[str, Any]:
        """扫描并执行所有已批准 (approved) 的 L3 因子类工单.

        Args:
            approved_status: 人工审批后的工单状态 (默认 approved)

        Returns:
            执行摘要:
                checked: 是否真正扫描 (flag 关闭时为 False)
                reason:  flag 关闭时的原因
                executed / rejected / failed / skipped: 各结果计数
                details: 每张工单的结果明细
        """
        if not self.enabled:
            logger.info(
                "Feature Flag 未启用 (%s), 跳过因子工单执行 (HC-1)", self.feature_flag
            )
            return {
                "checked": False,
                "reason": f"feature_flag_disabled: {self.feature_flag}",
                "executed": 0,
                "rejected": 0,
                "failed": 0,
                "skipped": 0,
                "details": [],
            }

        memory = self._get_memory()
        tickets = memory.query(level=LEVEL_L3, status=approved_status)
        factor_tickets = [t for t in tickets if t.action_type in FACTOR_ACTIONS]

        summary: dict[str, Any] = {
            "checked": True,
            "executed": 0,
            "rejected": 0,
            "failed": 0,
            "skipped": 0,
            "details": [],
        }

        for ticket in factor_tickets:
            outcome = self._execute_ticket(ticket)
            summary["details"].append(outcome)
            status = outcome.get("outcome", "skipped")
            if status in summary:
                summary[status] += 1

        logger.info(
            "因子工单执行完成: 命中=%d 执行=%d 拒绝=%d 失败=%d 跳过=%d",
            len(factor_tickets),
            summary["executed"],
            summary["rejected"],
            summary["failed"],
            summary["skipped"],
        )
        return summary

    # ============================================================
    # 单工单执行
    # ============================================================

    def _execute_ticket(self, ticket: Any) -> dict[str, Any]:
        """执行单张工单: 幂等检查 → Guard → 分发执行 → 状态回写.

        Args:
            ticket: MemoryRecord 工单 (action_type ∈ FACTOR_ACTIONS)

        Returns:
            结果明细: {proposal_id, action_type, outcome, reason/result}
        """
        pid = ticket.proposal_id
        action_type = ticket.action_type

        # 幂等: 本轮已处理过的不再执行
        if pid in self._executed_ids:
            return {
                "proposal_id": pid,
                "action_type": action_type,
                "outcome": "skipped",
                "reason": "idempotent_skip",
            }
        self._executed_ids.add(pid)

        # 闸门 2/3: Guard (含 Kill Switch 防线5) — 决策路径 fail-close
        guard = self._get_guard()
        try:
            decision = guard.check_proposal(self._build_proposal(ticket))
        except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError) as e:  # guard 失败 → STATUS_FAILED (fail-closed)
            self._update_status(pid, STATUS_FAILED, {"guard_error": str(e)})
            return {
                "proposal_id": pid,
                "action_type": action_type,
                "outcome": STATUS_FAILED,
                "reason": f"guard_error: {e}",
            }

        if not decision.passed:
            self._update_status(
                pid, STATUS_REJECTED, {"guard_decision": decision.to_dict()}
            )
            return {
                "proposal_id": pid,
                "action_type": action_type,
                "outcome": STATUS_REJECTED,
                "reason": decision.reason,
            }

        # 执行 (影子优先, 异常 → failed, 不向上抛)
        try:
            if action_type == ACTION_FACTOR_RETIRE:
                result = self._exec_retire(ticket)
            elif action_type == ACTION_FACTOR_DEPLOY:
                result = self._exec_deploy(ticket)
            elif action_type == ACTION_FACTOR_GENERATE:
                result = self._exec_generate(ticket)
            else:
                raise ValueError(f"不支持的因子动作: {action_type}")
            result = dict(result)
        except Exception as e:
            logger.exception(
                "因子工单执行异常: pid=%s action=%s err=%s", pid, action_type, e
            )
            self._update_status(pid, STATUS_FAILED, {"error": str(e)})
            return {
                "proposal_id": pid,
                "action_type": action_type,
                "outcome": STATUS_FAILED,
                "reason": str(e),
            }

        self._update_status(pid, STATUS_EXECUTED, result)
        self._audit(
            "execute",
            {"proposal_id": pid, "action_type": action_type, "result": result},
        )
        return {
            "proposal_id": pid,
            "action_type": action_type,
            "outcome": STATUS_EXECUTED,
            "result": result,
        }

    def _build_proposal(self, ticket: Any) -> Any:
        """将 Memory 工单转为 Guard 提案 (L3)."""
        from utils.evolution.guard import EvolutionProposal

        return EvolutionProposal(
            level=LEVEL_L3,
            action_type=ticket.action_type,
            target_module=ticket.target_module or TARGET_MODULE,
            rollback_plan=ticket.rollback_plan
            or f"回滚: {ticket.action_type} 执行不生效",
            shadow_days=self.shadow_days_required,
            trigger_reason=ticket.trigger_reason,
            metadata={"proposal_id": ticket.proposal_id},
        )

    # ============================================================
    # 动作执行 (骨架: 委托 AutoFactorFactory, 均含审计)
    # ============================================================

    def _exec_retire(self, ticket: Any) -> dict[str, Any]:
        """factor_retire: 下线因子.

        指定因子 (ticket.metadata.factor_name) → 公开 retire_factor 包装;
        未指定 → 委托 monitor() 自动淘汰 (IC 衰减判据).
        """
        factory = self._get_factory()
        factor_name = (ticket.metadata or {}).get("factor_name")

        if factor_name:
            ok = factory.retire_factor(factor_name, reason=ticket.trigger_reason)
            return {"retired": [factor_name] if ok else [], "ok": ok}

        suggestions = factory.monitor()
        retired = [s.name for s in suggestions if s.suggest_retire]
        return {"retired": retired, "ok": bool(retired)}

    def _exec_deploy(self, ticket: Any) -> dict[str, Any]:
        """factor_deploy: 部署已验证因子.

        影子天数已由人工审批流程满足 Guard 防线4 要求;
        工单可通过 ticket.metadata.factor_names (列表) 或 factor_name (单个)
        指定部署范围, 未指定则部署全部有效已验证因子 (向后兼容).
        与 _exec_retire 对称: 指定因子 → 仅部署指定; 未指定 → 全量.
        """
        factory = self._get_factory()
        meta = ticket.metadata or {}
        names = meta.get("factor_names")
        if not names and meta.get("factor_name"):
            names = [meta["factor_name"]]

        validated = None
        if names:
            names_set = set(names)
            # 骨架阶段直接读 factory._validated; 后续应在 factory 上暴露公开选择器
            validated = [
                v for v in factory._validated.values()
                if v.name in names_set and v.effective
            ]
            if not validated:
                logger.warning(
                    "工单指定因子 %s 无有效已验证项, 跳过部署", names
                )
                return {"n_deployed": 0, "factors": [], "requested": list(names)}

        deployed = factory.deploy(
            validated=validated,
            generate_code=True,
            register_to_library=True,
        )
        return {
            "n_deployed": len(deployed),
            "factors": [d.name for d in deployed],
        }

    def _exec_generate(self, ticket: Any) -> dict[str, Any]:
        """factor_generate: 影子模式 — 仅 discover + validate, 绝不部署.

        产出候选因子并验证, 结果供人工二次审批 (部署走 factor_deploy 工单).
        """
        factory = self._get_factory()
        candidates = factory.discover()

        validated: list[Any] = []
        if candidates:
            validated = factory.validate(candidates=candidates)

        return {
            "n_candidates": len(candidates),
            "n_validated": len(validated),
            "candidates": [c.name for c in candidates][: self.max_generate_per_cycle],
            "deployed": False,  # 影子模式: 绝不部署
        }

    # ============================================================
    # 审计与状态回写 (HC-2)
    # ============================================================

    def _update_status(
        self, pid: str, status: str, result: dict[str, Any] | None = None
    ) -> None:
        """回写工单状态 (HC-2), 失败仅告警不阻断."""
        try:
            self._get_memory().update_status(pid, status, result)
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
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.warning(
                "工单状态回写失败: pid=%s status=%s err=%s", pid, status, e
            )

    def _audit(self, action: str, details: dict[str, Any]) -> None:
        """审计留痕 (HC-2), 失败仅告警不阻断."""
        try:
            self._get_memory().record(
                {
                    "level": LEVEL_L3,
                    "action_type": action,
                    "target_module": AUDIT_MODULE,
                    "trigger_reason": "factor_approval_executor audit",
                    "rollback_plan": "审计记录, 无需回滚",
                    "status": STATUS_EXECUTED,
                    "result": details,
                }
            )
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
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.debug("审计记录失败: %s", e)
