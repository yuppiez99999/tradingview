"""进化守卫 — 五道硬约束, 防止进化失控.

模块整合 8.4 — ARCHITECTURE_自我进化框架 §6.6 (v2.0 合并版)
任务编号: T1.3 (Phase 1 防御层加固)

职责:
    对每一个进化动作施加五道硬约束, 不通过则拒绝 (或截断).
    所有进化提案必须先经 Guard.check_proposal() 检查, 再由 Orchestrator 执行.

五道防线 (ARCHITECTURE §6.6):
    | 防线 | 约束 | 触发动作 |
    |------|------|----------|
    | 1. 频率限制 | 同模块 24h 内进化 <= 1 次 | 拒绝超频提案 |
    | 2. 幅度限制 | 单次权重调整 <= 10% | 截断超额调整 |
    | 3. 回滚就绪 | 所有进化动作必须先准备回滚 | 拒绝无回滚方案 |
    | 4. 影子隔离 | L3 进化必须先在影子账户跑 5 日 | 拒绝直接生产部署 |
    | 5. 熔断冻结 | Kill Switch 触发时冻结所有进化 | 立即冻结 + 通知 |

熔断规则 (ARCHITECTURE §8.4):
    - Kill Switch L2 触发 → 冻结 L2/L3 进化 24h, 人工解除
    - Kill Switch L3 触发 → 冻结所有进化 24h, 人工解除
    - L1 触发不冻结进化 (L1 是防守模式, 进化仍可尝试修复)

用法:
    from utils.evolution.guard import EvolutionGuard, EvolutionProposal
    from utils.evolution.memory import EvolutionMemory

    guard = EvolutionGuard(memory=EvolutionMemory())
    proposal = EvolutionProposal(
        level="L2",
        action_type="retrain",
        target_module="lgb_trainer",
        weight_change=0.05,
        rollback_plan="回滚至基线",
    )
    decision = guard.check_proposal(proposal)
    if decision.passed:
        ...  # 执行进化
    else:
        logger.warning("提案被拒: %s (%s)", decision.reason, decision.violated_defense)

硬约束:
    - HC-2: 所有 Guard 决策应写入 Memory 审计 (由调用方负责, Guard 本身不写)
    - HC-3: 无回滚方案的 L2/L3 提案必须拒绝
    - HC-5: 配置走 ConfigManager 4 级优先级
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================
# 常量
# ============================================================

# 五道防线编号
DEFENSE_FREQUENCY = 1
DEFENSE_MAGNITUDE = 2
DEFENSE_ROLLBACK = 3
DEFENSE_SHADOW = 4
DEFENSE_KILL_SWITCH = 5

# 防线名称 (用于决策结果)
DEFENSE_NAMES = {
    DEFENSE_FREQUENCY: "frequency",
    DEFENSE_MAGNITUDE: "magnitude",
    DEFENSE_ROLLBACK: "rollback_ready",
    DEFENSE_SHADOW: "shadow_isolation",
    DEFENSE_KILL_SWITCH: "kill_switch_frozen",
}

# 默认阈值 (ARCHITECTURE §6.6 + §8.2)
DEFAULT_DAILY_EVOLUTION_LIMIT = 1        # 同模块 24h 进化上限
DEFAULT_MAX_WEIGHT_CHANGE = 0.10         # 单次权重调整上限 (10%)
DEFAULT_SHADOW_DAYS_REQUIRED = 5         # 影子账户最短运行天数
DEFAULT_KILL_SWITCH_FREEZE_HOURS = 24    # 熔断冻结时长

# Kill Switch 冻结级别 (ARCHITECTURE §8.4)
# L2 触发 → 冻结 L2/L3 进化
# L3 触发 → 冻结所有进化 (L1/L2/L3)
# L1 触发 → 不冻结进化 (L1 是防守模式)
KILL_SWITCH_L2 = 2
KILL_SWITCH_L3 = 3

# 进化层级 (与 memory.py 保持一致, 避免循环导入此处重新定义)
LEVEL_L1 = "L1"
LEVEL_L2 = "L2"
LEVEL_L3 = "L3"


# ============================================================
# 异常
# ============================================================


class GuardError(Exception):
    """进化守卫基础异常."""


class GuardValidationError(GuardError):
    """提案字段校验失败."""


# ============================================================
# 数据类
# ============================================================


@dataclass
class EvolutionProposal:
    """进化提案 (Guard 检查的输入).

    所有字段都是可选的 (除了 level/action_type/target_module),
    Guard 根据提案的 level 决定哪些防线需要检查.
    """

    level: str  # L1 / L2 / L3
    action_type: str  # retrain / weight_adjust / factor_deploy / fix / ...
    target_module: str  # 目标模块
    weight_change: float = 0.0  # 权重调整幅度 (绝对值, 防线2)
    rollback_plan: str = ""  # 回滚方案 (防线3, L2/L3 必填)
    shadow_days: int = 0  # 影子账户已运行天数 (防线4, L3 必填 >= 5)
    trigger_reason: str = ""  # 触发原因 (仅用于审计)
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """校验提案字段, 失败抛 GuardValidationError."""
        if self.level not in (LEVEL_L1, LEVEL_L2, LEVEL_L3):
            raise GuardValidationError(
                f"level 必须是 {LEVEL_L1}/{LEVEL_L2}/{LEVEL_L3}, 实际: {self.level}"
            )
        if not self.action_type:
            raise GuardValidationError("action_type 不能为空")
        if not self.target_module:
            raise GuardValidationError("target_module 不能为空")


@dataclass
class GuardDecision:
    """Guard 决策结果.

    passed=True 时表示通过所有防线, 可执行进化.
    passed=False 时表示被拒绝, violated_defense 标明违反的防线编号.
    """

    passed: bool  # 是否通过所有防线
    reason: str  # 原因说明
    violated_defense: int = 0  # 违反的防线编号 (0=通过)
    violated_defense_name: str = ""  # 违反的防线名称
    truncated_weight_change: float | None = None  # 截断后的权重 (防线2截断时, 否则 None)
    original_weight_change: float = 0.0  # 原始权重 (审计用)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典 (用于审计日志)."""
        return {
            "passed": self.passed,
            "reason": self.reason,
            "violated_defense": self.violated_defense,
            "violated_defense_name": self.violated_defense_name,
            "truncated_weight_change": self.truncated_weight_change,
            "original_weight_change": self.original_weight_change,
        }


# ============================================================
# 进化守卫
# ============================================================


class EvolutionGuard:
    """进化守卫 — 五道硬约束, 防止进化失控.

    所有进化提案必须先经 check_proposal() 检查, 再执行.
    Guard 本身是无状态的 (状态来自 Memory + KillSwitch), 可安全单例化.
    """

    def __init__(
        self,
        memory: Any | None = None,
        kill_switch: Any | None = None,
        daily_evolution_limit: int = DEFAULT_DAILY_EVOLUTION_LIMIT,
        max_weight_change: float = DEFAULT_MAX_WEIGHT_CHANGE,
        shadow_days_required: int = DEFAULT_SHADOW_DAYS_REQUIRED,
        kill_switch_freeze_hours: int = DEFAULT_KILL_SWITCH_FREEZE_HOURS,
    ) -> None:
        """初始化进化守卫.

        Args:
            memory: EvolutionMemory 实例 (用于频率检查, None=跳过频率检查)
            kill_switch: KillSwitch 实例 (用于熔断检查, None=跳过熔断检查)
            daily_evolution_limit: 同模块 24h 进化上限 (默认 1)
            max_weight_change: 单次权重调整上限 (默认 0.10)
            shadow_days_required: 影子账户最短运行天数 (默认 5)
            kill_switch_freeze_hours: 熔断冻结时长 (默认 24h)
        """
        self.memory = memory
        self.kill_switch = kill_switch
        self.daily_evolution_limit = daily_evolution_limit
        self.max_weight_change = max_weight_change
        self.shadow_days_required = shadow_days_required
        self.kill_switch_freeze_hours = kill_switch_freeze_hours

        logger.debug(
            "EvolutionGuard 初始化: memory=%s, kill_switch=%s, "
            "daily_limit=%d, max_weight=%.2f, shadow_days=%d",
            "yes" if memory else "no",
            "yes" if kill_switch else "no",
            daily_evolution_limit,
            max_weight_change,
            shadow_days_required,
        )

    # ============================================================
    # 核心方法: check_proposal
    # ============================================================

    def check_proposal(self, proposal: EvolutionProposal) -> GuardDecision:
        """检查提案是否通过所有五道防线.

        检查顺序 (短路: 任一防线失败立即返回):
            1. 频率限制 — 同模块 24h 内进化 <= daily_evolution_limit
            2. 幅度限制 — weight_change <= max_weight_change (超幅截断, 不拒绝)
            3. 回滚就绪 — L2/L3 必须有 rollback_plan
            4. 影子隔离 — L3 必须 shadow_days >= shadow_days_required
            5. 熔断冻结 — Kill Switch L2/L3 触发时冻结

        Args:
            proposal: 进化提案

        Returns:
            GuardDecision (passed=True 通过, False 被拒/截断)

        Raises:
            GuardValidationError: 提案字段校验失败
        """
        # 字段校验
        proposal.validate()

        original_weight = proposal.weight_change

        # 防线 1: 频率限制
        ok, reason = self._check_frequency(proposal)
        if not ok:
            return GuardDecision(
                passed=False,
                reason=reason,
                violated_defense=DEFENSE_FREQUENCY,
                violated_defense_name=DEFENSE_NAMES[DEFENSE_FREQUENCY],
                original_weight_change=original_weight,
            )

        # 防线 2: 幅度限制 (超幅截断, 不拒绝)
        truncated = self._check_magnitude(proposal)
        if truncated is not None:
            # 截断后继续检查后续防线, 但在最终决策中标注截断
            proposal = EvolutionProposal(
                level=proposal.level,
                action_type=proposal.action_type,
                target_module=proposal.target_module,
                weight_change=truncated,  # 使用截断后的值
                rollback_plan=proposal.rollback_plan,
                shadow_days=proposal.shadow_days,
                trigger_reason=proposal.trigger_reason,
                metadata=proposal.metadata,
            )

        # 防线 3: 回滚就绪
        ok, reason = self._check_rollback_ready(proposal)
        if not ok:
            return GuardDecision(
                passed=False,
                reason=reason,
                violated_defense=DEFENSE_ROLLBACK,
                violated_defense_name=DEFENSE_NAMES[DEFENSE_ROLLBACK],
                original_weight_change=original_weight,
            )

        # 防线 4: 影子隔离
        ok, reason = self._check_shadow_isolation(proposal)
        if not ok:
            return GuardDecision(
                passed=False,
                reason=reason,
                violated_defense=DEFENSE_SHADOW,
                violated_defense_name=DEFENSE_NAMES[DEFENSE_SHADOW],
                original_weight_change=original_weight,
            )

        # 防线 5: 熔断冻结
        ok, reason = self._check_kill_switch_frozen(proposal)
        if not ok:
            return GuardDecision(
                passed=False,
                reason=reason,
                violated_defense=DEFENSE_KILL_SWITCH,
                violated_defense_name=DEFENSE_NAMES[DEFENSE_KILL_SWITCH],
                original_weight_change=original_weight,
            )

        # 全部通过 (可能含截断)
        if truncated is not None:
            return GuardDecision(
                passed=True,
                reason=f"通过所有防线 (权重已截断 {original_weight:.4f} -> {truncated:.4f})",
                truncated_weight_change=truncated,
                original_weight_change=original_weight,
            )

        return GuardDecision(
            passed=True,
            reason="通过所有防线",
            original_weight_change=original_weight,
        )

    # ============================================================
    # 五道防线实现
    # ============================================================

    def _check_frequency(self, proposal: EvolutionProposal) -> tuple[bool, str]:
        """防线 1: 频率限制 — 同模块 24h 内进化 <= daily_evolution_limit.

        Returns:
            (passed, reason)
        """
        if self.memory is None:
            # 无 Memory 时跳过 (容错, 但记录 warning)
            logger.warning("防线1 频率检查跳过: 未注入 Memory")
            return True, "跳过 (无 Memory)"

        try:
            # 查询同模块 24h 内的提案
            since = self._iso_hours_ago(24)
            recent = self.memory.query(
                target_module=proposal.target_module,
                since=since,
            )
            # 排除被拒绝的提案 (被拒不算占用频率配额)
            active_count = sum(1 for r in recent if r.status != "rejected")
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            # Memory 查询失败不应阻塞进化 (容错), 但记录 warning
            logger.warning("防线1 频率检查异常 (容错放行): %s", e)
            return True, f"跳过 (Memory 查询异常: {e})"

        if active_count >= self.daily_evolution_limit:
            return False, (
                f"超频: 模块 {proposal.target_module} 24h 内已有 "
                f"{active_count} 次进化 (上限 {self.daily_evolution_limit})"
            )

        return True, "频率正常"

    def _check_magnitude(self, proposal: EvolutionProposal) -> float | None:
        """防线 2: 幅度限制 — weight_change <= max_weight_change.

        超幅时截断 (不拒绝), 返回截断后的值.
        未超幅返回 None.

        Returns:
            截断后的权重值 (超幅时), 或 None (未超幅)
        """
        # 负权重取绝对值比较 (允许减仓)
        abs_change = abs(proposal.weight_change)

        if abs_change <= self.max_weight_change:
            return None  # 未超幅

        # 截断: 保留符号, 限制绝对值
        sign = 1.0 if proposal.weight_change >= 0 else -1.0
        truncated = sign * self.max_weight_change

        logger.warning(
            "防线2 幅度截断: 模块 %s 权重 %.4f -> %.4f (上限 %.4f)",
            proposal.target_module,
            proposal.weight_change,
            truncated,
            self.max_weight_change,
        )
        return truncated

    def _check_rollback_ready(self, proposal: EvolutionProposal) -> tuple[bool, str]:
        """防线 3: 回滚就绪 — L2/L3 必须有 rollback_plan.

        Returns:
            (passed, reason)
        """
        # L1 是自动修复, 无需回滚方案 (修复失败不影响生产)
        if proposal.level == LEVEL_L1:
            return True, "L1 无需回滚方案"

        if not proposal.rollback_plan.strip():
            return False, (
                f"{proposal.level} 级进化必须提供 rollback_plan (HC-3 回滚就绪)"
            )

        return True, "回滚方案就绪"

    def _check_shadow_isolation(self, proposal: EvolutionProposal) -> tuple[bool, str]:
        """防线 4: 影子隔离 — L3 必须 shadow_days >= shadow_days_required.

        L1/L2 不强制影子 (L1 修复配置, L2 模型重训有 A/B Test 兜底).

        Returns:
            (passed, reason)
        """
        if proposal.level != LEVEL_L3:
            return True, f"{proposal.level} 无需影子验证"

        if proposal.shadow_days < self.shadow_days_required:
            return False, (
                f"L3 级进化必须先在影子账户运行 {self.shadow_days_required} 日 "
                f"(实际 {proposal.shadow_days} 日)"
            )

        return True, f"影子验证通过 ({proposal.shadow_days} 日)"

    def _check_kill_switch_frozen(self, proposal: EvolutionProposal) -> tuple[bool, str]:
        """防线 5: 熔断冻结 — Kill Switch L2/L3 触发时冻结.

        熔断规则 (ARCHITECTURE §8.4):
            - L2 触发 → 冻结 L2/L3 进化 24h
            - L3 触发 → 冻结所有进化 (L1/L2/L3) 24h
            - L1 触发 → 不冻结进化

        Returns:
            (passed, reason)
        """
        if self.kill_switch is None:
            # 无 KillSwitch 时跳过 (容错)
            logger.warning("防线5 熔断检查跳过: 未注入 KillSwitch")
            return True, "跳过 (无 KillSwitch)"

        try:
            # 查询最近 freeze_hours 内的熔断事件
            events = self.kill_switch.get_event_history(
                days=max(1, self.kill_switch_freeze_hours // 24 + 1)
            )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.warning("防线5 熔断检查异常 (容错放行): %s", e)
            return True, f"跳过 (KillSwitch 查询异常: {e})"

        cutoff_ts = datetime.now(UTC).timestamp() - self.kill_switch_freeze_hours * 3600

        for event in events:
            # 解析事件级别和时间
            event_level = event.get("level")
            event_ts_str = event.get("timestamp", "")

            if not event_ts_str or event_level is None:
                continue

            try:
                event_ts = self._parse_ts(event_ts_str)
            except (ValueError, TypeError):
                continue

            if event_ts < cutoff_ts:
                continue  # 超过冻结期

            # L3 熔断 → 冻结所有进化
            if event_level == KILL_SWITCH_L3:
                return False, (
                    f"Kill Switch L3 已触发 (于 {event_ts_str}), "
                    f"冻结所有进化 {self.kill_switch_freeze_hours}h, 需人工解除"
                )

            # L2 熔断 → 冻结 L2/L3 进化 (L1 仍允许)
            if event_level == KILL_SWITCH_L2 and proposal.level in (LEVEL_L2, LEVEL_L3):
                return False, (
                    f"Kill Switch L2 已触发 (于 {event_ts_str}), "
                    f"冻结 {proposal.level} 进化 {self.kill_switch_freeze_hours}h, 需人工解除"
                )

        return True, "无熔断冻结"

    # ============================================================
    # 辅助方法
    # ============================================================

    @staticmethod
    def _iso_hours_ago(hours: int) -> str:
        """返回 hours 小时前的 ISO8601 时间戳 (UTC, 带 Z 后缀).

        用于 Memory.query(since=...) 的频率检查.
        """
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        return cutoff.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    @staticmethod
    def _parse_ts(ts_str: str) -> float:
        """解析 ISO8601 时间戳为 Unix 时间戳.

        兼容带 Z 后缀和不带后缀两种格式.
        """
        # 兼容 Z 后缀
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
