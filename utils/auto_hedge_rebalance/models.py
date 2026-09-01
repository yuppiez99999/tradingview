"""自动对冲再平衡进化系统 — 数据模型与枚举定义。

本模块使用 dataclasses + enum 定义系统全部数据模型，遵循不可变性原则:
所有数据类实例创建后不被原地修改，变更时使用 dataclasses.replace() 创建新实例。

模型清单:
    枚举 (4 个):
        StrategyLevel    — 策略等级状态机 6 档
        CorrectionAction — 纠偏动作 6 档
        HedgeToolType    — 对冲工具类型 5 档
        OptionsStrategy  — 期权策略 3 档

    数据类 (10 个):
        ToolSelection       — 对冲工具选择结果
        FilterResult        — 成本效益过滤结果
        MonitorResult       — 目标达成监控结果
        PrecheckResult      — 策略可行性预检结果
        StrategyState       — 策略状态机当前状态
        TransitionResult    — 状态转移结果
        BreakerStatus       — 熔断器状态
        AutoHedgePlan       — 自动对冲再平衡联合计划
        StrategySwitchEvent — 策略切换事件
        AuditRecord         — 审计日志记录
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# ============================================================================
# 枚举定义
# ============================================================================


class StrategyLevel(StrEnum):
    """策略等级状态机 — 6 档单向降级 + 冷却期升级。

    降级链: NORMAL → MILD_CORRECTION → MODERATE_CORRECTION → SEVERE_CORRECTION
            → CONSERVATIVE_DEFENSE → CIRCUIT_BREAKER
    """

    NORMAL = "NORMAL"
    MILD_CORRECTION = "MILD_CORRECTION"
    MODERATE_CORRECTION = "MODERATE_CORRECTION"
    SEVERE_CORRECTION = "SEVERE_CORRECTION"
    CONSERVATIVE_DEFENSE = "CONSERVATIVE_DEFENSE"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"


class CorrectionAction(StrEnum):
    """纠偏动作 — 按偏离度分级触发。"""

    NONE = "NONE"
    MILD_TUNE = "MILD_TUNE"
    MODERATE_ROTATE = "MODERATE_ROTATE"
    SEVERE_REVIEW = "SEVERE_REVIEW"
    DEFENSE_BOOST = "DEFENSE_BOOST"
    EMERGENCY_LIQUIDATE = "EMERGENCY_LIQUIDATE"


class HedgeToolType(StrEnum):
    """对冲工具类型 — 按市场状态 + Beta 特征选择。"""

    NONE = "NONE"
    INDEX_FUTURES = "INDEX_FUTURES"
    ETF_OPTIONS = "ETF_OPTIONS"
    REVERSE_ETF = "REVERSE_ETF"
    MIXED = "MIXED"


class OptionsStrategy(StrEnum):
    """期权策略 — TAIL_EVENT 状态下选择保护性策略。"""

    PROTECTIVE_PUT = "PROTECTIVE_PUT"
    COLLAR = "COLLAR"
    PUT_SPREAD = "PUT_SPREAD"


# ============================================================================
# 数据类定义
# ============================================================================


@dataclass(frozen=True)
class ToolSelection:
    """对冲工具选择结果。

    由 HedgeToolSelector.select_tools() 生成，含降级标记与决策推理。

    Attributes:
        tool_type: 对冲工具类型 (NONE/INDEX_FUTURES/ETF_OPTIONS/REVERSE_ETF/MIXED)。
        instruments: 选定标的代码列表 (如 ["IF2409", "IC2409"])。
        hedge_ratio: 对冲比例 (0.0-1.0)。
        futures_contracts: 期货合约张数映射 (如 {"IF2409": 2, "IC2409": 1})。
        options_strategy: 期权策略 (仅 tool_type=ETF_OPTIONS 时有效)。
        options_contracts: 期权合约明细列表。
        cost_estimate: 对冲成本估算 (含展期+保证金+权利金)。
        expected_benefit: 预期对冲收益。
        fallback_flags: 降级标记列表 (如 ["期权报价残缺", "降级至期货"])。
        reasoning: 决策推理说明。
    """

    tool_type: HedgeToolType = HedgeToolType.NONE
    instruments: list[str] = field(default_factory=list)
    hedge_ratio: float = 0.0
    futures_contracts: dict[str, int] = field(default_factory=dict)
    options_strategy: OptionsStrategy | None = None
    options_contracts: list[dict[str, Any]] = field(default_factory=list)
    cost_estimate: float = 0.0
    expected_benefit: float = 0.0
    fallback_flags: list[str] = field(default_factory=list)
    reasoning: str = ""


@dataclass(frozen=True)
class FilterResult:
    """成本效益过滤结果。

    由 CostBenefitFilter.filter() 生成，判定对冲方案是否通过成本效益准入。

    Attributes:
        passed: 是否通过准入 (expected_benefit > cost_estimate × threshold)。
        cost_estimate: 对冲成本估算。
        expected_benefit: 预期对冲收益。
        reject_reason: 拒绝原因 (passed=False 时填写)。
    """

    passed: bool = True
    cost_estimate: float = 0.0
    expected_benefit: float = 0.0
    reject_reason: str = ""


@dataclass(frozen=True)
class PrecheckResult:
    """策略可行性预检结果。

    由 TargetMonitor.precheck_strategy_feasibility() 生成，调用回测引擎验证新参数。

    Attributes:
        backtest_annual_return: 回测年化收益率。
        backtest_max_drawdown: 回测最大回撤。
        passed: 是否通过预检 (回测年化≥8% 且回测最大回撤<20%)。
        timeout: 是否超时。
        reason: 说明 (通过/未通过/超时/引擎不可用原因)。
    """

    backtest_annual_return: float = 0.0
    backtest_max_drawdown: float = 0.0
    passed: bool = True
    timeout: bool = False
    reason: str = ""


@dataclass(frozen=True)
class MonitorResult:
    """目标达成监控结果。

    由 TargetMonitor.monitor() 生成，含滚动指标与纠偏建议。

    Attributes:
        rolling_annual_return: 滚动 252 日年化收益率。
        rolling_max_drawdown: 滚动 252 日最大回撤。
        return_deviation: 收益偏离度 (目标 8% - 实际年化)。
        drawdown_margin: 回撤余量 (约束 20% - 实际回撤)。
        sample_insufficient: 样本不足标志 (不足 30 日时为 True)。
        correction_action: 纠偏动作建议。
        precheck_result: 策略可行性预检结果。
    """

    rolling_annual_return: float = 0.0
    rolling_max_drawdown: float = 0.0
    return_deviation: float = 0.0
    drawdown_margin: float = 0.0
    sample_insufficient: bool = False
    correction_action: CorrectionAction = CorrectionAction.NONE
    precheck_result: PrecheckResult | None = None


@dataclass(frozen=True)
class StrategyState:
    """策略状态机当前状态。

    持久化至 config/auto_hedge_rebalance_state.json，跨决策保留。

    Attributes:
        current_level: 当前策略等级。
        last_transition_time: 上次状态转移时间 (ISO8601)。
        cooldown_until: 冷却期截止时间 (ISO8601)。
        pending_switch_event_id: 待审批切换事件 ID。
        level_min_hold_days: 当前等级最小持续交易日数。
    """

    current_level: StrategyLevel = StrategyLevel.NORMAL
    last_transition_time: str = ""
    cooldown_until: str = ""
    pending_switch_event_id: str | None = None
    level_min_hold_days: int = 0


@dataclass(frozen=True)
class StrategySwitchEvent:
    """策略切换事件。

    由 StrategyStateMachine.transition() 生成，需管理员审批后生效。

    Attributes:
        event_id: 事件唯一标识 (UUID)。
        timestamp: 事件时间 (ISO8601)。
        from_level: 原策略等级。
        to_level: 目标策略等级。
        trigger_reason: 触发原因。
        params_before: 切换前参数。
        params_after: 切换后参数。
        approver: 审批人 (未审批时为空)。
        approved: 是否已审批通过。
    """

    event_id: str = ""
    timestamp: str = ""
    from_level: StrategyLevel = StrategyLevel.NORMAL
    to_level: StrategyLevel = StrategyLevel.NORMAL
    trigger_reason: str = ""
    params_before: dict[str, Any] = field(default_factory=dict)
    params_after: dict[str, Any] = field(default_factory=dict)
    approver: str = ""
    approved: bool = False


@dataclass(frozen=True)
class TransitionResult:
    """状态转移结果。

    由 StrategyStateMachine.transition() 返回，含新等级与冷却期状态。

    Attributes:
        new_level: 新策略等级 (转移被阻断时为原等级)。
        new_params: 新参数。
        switch_event: 策略切换事件 (需审批时非空)。
        cooldown_active: 冷却期是否活跃。
        blocked_reason: 阻断原因 (转移被阻断时填写)。
    """

    new_level: StrategyLevel = StrategyLevel.NORMAL
    new_params: dict[str, Any] = field(default_factory=dict)
    switch_event: StrategySwitchEvent | None = None
    cooldown_active: bool = False
    blocked_reason: str = ""


@dataclass(frozen=True)
class BreakerStatus:
    """熔断器状态。

    持久化至 config/auto_hedge_rebalance_state.json，熔断后锁定至管理员解除。

    Attributes:
        active: 熔断是否活跃。
        trigger_reason: 触发原因 (如 "单日跌幅5.1%"、"回撤25.1%")。
        trigger_time: 触发时间 (ISO8601)。
        emergency_action: 紧急保护动作 (如 "清仓高波动至50%")。
    """

    active: bool = False
    trigger_reason: str | None = None
    trigger_time: str | None = None
    emergency_action: str | None = None


@dataclass(frozen=True)
class AutoHedgePlan:
    """自动对冲再平衡联合计划。

    由 AutoHedgeRebalanceEngine.run_eod_decision() 生成，含全部子结果。
    不可变，写入审计日志后归档至 每日报告归档/YYYY-MM-DD/auto_hedge_rebalance_plan.json。

    Attributes:
        timestamp: 决策时间 (ISO8601)。
        joint_plan: 复用存量 HedgeRebalanceIntegrator.JointPlan。
        tool_selection: 对冲工具选择结果。
        filter_result: 成本效益过滤结果。
        monitor: 目标达成监控结果。
        strategy_state: 策略状态机当前状态。
        breaker_status: 熔断器状态。
        degradation_flags: 降级标记汇总列表。
        audit_event_ids: 关联审计事件 ID 列表。
    """

    timestamp: str = ""
    joint_plan: Any | None = None
    tool_selection: ToolSelection | None = None
    filter_result: FilterResult | None = None
    monitor: MonitorResult | None = None
    strategy_state: StrategyState | None = None
    breaker_status: BreakerStatus | None = None
    degradation_flags: list[str] = field(default_factory=list)
    audit_event_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AuditRecord:
    """审计日志记录。

    由 AuditLogger 写入 SQLite 数据库，仅追加不可变。

    Attributes:
        record_id: 记录唯一标识 (UUID)。
        timestamp: 记录时间 (ISO8601)。
        event_type: 事件类型 (strategy_switch/target_deviation/circuit_breaker/degradation_flag)。
        trigger_reason: 触发原因。
        details: 详情 (JSON 序列化)。
        approver: 审批人 (策略切换/熔断解除时填写)。
    """

    record_id: str = ""
    timestamp: str = ""
    event_type: str = ""
    trigger_reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    approver: str = ""


# ============================================================================
# 辅助函数
# ============================================================================


def strategy_level_from_str(value: str) -> StrategyLevel:
    """从字符串安全构造 StrategyLevel 枚举。

    Args:
        value: 枚举字符串值 (如 "NORMAL")。

    Returns:
        对应的 StrategyLevel 枚举值，未知值返回 NORMAL。
    """
    try:
        return StrategyLevel(value)
    except ValueError:
        return StrategyLevel.NORMAL


def correction_action_from_str(value: str) -> CorrectionAction:
    """从字符串安全构造 CorrectionAction 枚举。"""
    try:
        return CorrectionAction(value)
    except ValueError:
        return CorrectionAction.NONE


def hedge_tool_type_from_str(value: str) -> HedgeToolType:
    """从字符串安全构造 HedgeToolType 枚举。"""
    try:
        return HedgeToolType(value)
    except ValueError:
        return HedgeToolType.NONE


def options_strategy_from_str(value: str) -> OptionsStrategy | None:
    """从字符串安全构造 OptionsStrategy 枚举。"""
    if not value:
        return None
    try:
        return OptionsStrategy(value)
    except ValueError:
        return None


__all__ = [
    # 枚举
    "StrategyLevel",
    "CorrectionAction",
    "HedgeToolType",
    "OptionsStrategy",
    # 数据类
    "ToolSelection",
    "FilterResult",
    "PrecheckResult",
    "MonitorResult",
    "StrategyState",
    "StrategySwitchEvent",
    "TransitionResult",
    "BreakerStatus",
    "AutoHedgePlan",
    "AuditRecord",
    # 辅助函数
    "strategy_level_from_str",
    "correction_action_from_str",
    "hedge_tool_type_from_str",
    "options_strategy_from_str",
]
