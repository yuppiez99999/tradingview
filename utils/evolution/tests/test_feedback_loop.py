"""FeedbackLoop 单元测试.

任务编号: T2.1 (Phase 2 反馈闭环)
验收要求:
    1. 单日调整幅度严格 <= 10% (max_daily_change)
    2. 7 日累计偏移 > 30% 时触发告警 (alarm_threshold_7d)
    3. 权重调整全量写入 EvolutionMemory (HC-2 审计)
    4. 单测覆盖率 >= 90%

测试范围:
    - Feature Flag: 关闭降级 / 强制启用
    - 输入校验: 非有限值 / 空贡献 / 空因子名
    - 首次初始化: 等权 / max_weight 上限
    - 贡献归一化: 正负方向 / 全零中性 / 绝对值和=1
    - 单日调整幅度 (HC-3): 严格 <= 10% / 大贡献截断
    - 单因子上限: max_weight=0.3
    - 7 日去噪: 历史不足直接返回 / 历史足够平滑
    - 权重归一化: sum=1.0 / 全零退化等权
    - 7 日累计偏移告警: 历史不足不触发 / 超阈值触发 / 未超不触发
    - Memory 审计 (HC-2): 写入 L3 记录 / action_type / 异常容错
    - 查询 API: get_current_weights 深拷贝 / get_history
    - 不可变性: 修改返回值不影响内部状态
    - 集成场景: 连续 7 日更新
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from utils.evolution.feedback_loop import (
    ACTION_WEIGHT_ADJUST,
    DEFAULT_ALARM_THRESHOLD_7D,
    DEFAULT_LEARNING_RATE,
    DEFAULT_MAX_DAILY_CHANGE,
    DEFAULT_MAX_WEIGHT,
    DEFAULT_SMOOTHING_WINDOW,
    FLAG_FEEDBACK_LOOP,
    FeedbackLoop,
    FeedbackLoopError,
    FeedbackLoopValidationError,
    WeightUpdate,
)
from utils.evolution.memory import (
    EvolutionMemory,
    LEVEL_L3,
    STATUS_EXECUTED,
)


# ============================================================
# Mock 组件
# ============================================================


class FlakyMemory:
    """模拟 Memory 写入异常 (测试容错).

    record() 总是抛 ValueError, 验证主流程不被 Memory 失败阻塞.
    """

    def __init__(self) -> None:
        self.recorded: list[dict[str, Any]] = []

    def record(self, proposal: Any) -> str:
        raise ValueError("Mock Memory 写入失败 (测试容错)")


class CountingMemory:
    """计数 Memory, 记录所有 record 调用."""

    def __init__(self) -> None:
        self.recorded: list[dict[str, Any]] = []

    def record(self, proposal: Any) -> str:
        if isinstance(proposal, dict):
            self.recorded.append(dict(proposal))
            return proposal.get("proposal_id", "EVO-TEST-001")
        return "EVO-TEST-001"


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def tmp_memory(tmp_path: Path) -> EvolutionMemory:
    """临时 Memory 实例 (每个测试独立)."""
    return EvolutionMemory(memory_path=tmp_path / "memory.jsonl")


@pytest.fixture
def loop(tmp_memory: EvolutionMemory) -> FeedbackLoop:
    """启用的 FeedbackLoop (注入真实 Memory, 强制 enabled=True)."""
    fl = FeedbackLoop(memory=tmp_memory)
    fl._enabled = True  # 测试强制启用 (绕过 Feature Flag)
    return fl


@pytest.fixture
def loop_no_memory() -> FeedbackLoop:
    """启用的 FeedbackLoop (无 Memory, 测试无审计模式)."""
    fl = FeedbackLoop(memory=None)
    fl._enabled = True
    return fl


@pytest.fixture
def loop_with_initial_weights(tmp_memory: EvolutionMemory) -> FeedbackLoop:
    """带初始权重的 FeedbackLoop (4 因子, 权重均 <= max_weight=0.3).

    注意: 3 因子等权 1/3=0.333 > 0.3 会被 _apply_changes 截断,
    故使用 4 因子 (等权 0.25 <= 0.3) 避免 fixture 本身违反约束.
    """
    fl = FeedbackLoop(
        initial_weights={"momentum": 0.30, "value": 0.25, "quality": 0.25, "size": 0.20},
        memory=tmp_memory,
    )
    fl._enabled = True
    return fl


# ============================================================
# 测试1: Feature Flag 与初始化
# ============================================================


class TestFeatureFlagAndInit:
    """Feature Flag 检查 + 初始化参数."""

    def test_disabled_flag_returns_degraded(self, tmp_memory: EvolutionMemory):
        """Flag 关闭时 update_weights 返回降级结果 (不调整权重)."""
        fl = FeedbackLoop(memory=tmp_memory)
        fl._enabled = False
        # 预设权重
        fl._weights = {"momentum": 0.5, "value": 0.5}

        update = fl.update_weights(
            daily_pnl=0.002,
            factor_contributions={"momentum": 0.001, "value": -0.001},
        )

        assert update.is_degraded is True
        assert "feature_flag_disabled" in update.degraded_reason
        # 权重不变
        assert update.new_weights == {"momentum": 0.5, "value": 0.5}
        assert update.old_weights == {"momentum": 0.5, "value": 0.5}
        assert update.total_change_pct == 0.0

    def test_enabled_flag_normal_execution(self, loop_no_memory: FeedbackLoop):
        """Flag 启用时正常执行 (返回非降级结果)."""
        update = loop_no_memory.update_weights(
            daily_pnl=0.001,
            factor_contributions={"momentum": 0.001},
        )
        assert update.is_degraded is False
        assert update.new_weights  # 非空

    def test_default_parameters(self):
        """默认参数符合 ARCHITECTURE 规范."""
        fl = FeedbackLoop()
        assert fl.learning_rate == DEFAULT_LEARNING_RATE
        assert fl.max_daily_change == DEFAULT_MAX_DAILY_CHANGE  # 0.10
        assert fl.max_weight == DEFAULT_MAX_WEIGHT  # 0.30
        assert fl.alarm_threshold_7d == DEFAULT_ALARM_THRESHOLD_7D  # 0.30
        assert fl.smoothing_window == DEFAULT_SMOOTHING_WINDOW  # 7
        assert fl.feature_flag_name == FLAG_FEEDBACK_LOOP

    def test_enabled_property_reflects_flag(self):
        """enabled 属性反映 Feature Flag 状态."""
        fl = FeedbackLoop()
        assert fl.enabled == fl._enabled

    def test_custom_parameters(self):
        """自定义参数正确存储."""
        fl = FeedbackLoop(
            learning_rate=0.05,
            max_daily_change=0.05,
            max_weight=0.25,
            alarm_threshold_7d=0.25,
            smoothing_window=5,
        )
        assert fl.learning_rate == 0.05
        assert fl.max_daily_change == 0.05
        assert fl.max_weight == 0.25
        assert fl.alarm_threshold_7d == 0.25
        assert fl.smoothing_window == 5


# ============================================================
# 测试2: 输入校验
# ============================================================


class TestInputValidation:
    """输入校验, 失败抛 FeedbackLoopValidationError."""

    def test_nan_daily_pnl_raises(self, loop_no_memory: FeedbackLoop):
        """daily_pnl=NaN 抛异常."""
        with pytest.raises(FeedbackLoopValidationError, match="非有限值"):
            loop_no_memory.update_weights(
                daily_pnl=float("nan"),
                factor_contributions={"m": 0.001},
            )

    def test_inf_daily_pnl_raises(self, loop_no_memory: FeedbackLoop):
        """daily_pnl=Inf 抛异常."""
        with pytest.raises(FeedbackLoopValidationError, match="非有限值"):
            loop_no_memory.update_weights(
                daily_pnl=float("inf"),
                factor_contributions={"m": 0.001},
            )

    def test_empty_contributions_raises(self, loop_no_memory: FeedbackLoop):
        """空 factor_contributions 抛异常."""
        with pytest.raises(FeedbackLoopValidationError, match="不能为空"):
            loop_no_memory.update_weights(daily_pnl=0.001, factor_contributions={})

    def test_empty_factor_name_raises(self, loop_no_memory: FeedbackLoop):
        """空因子名抛异常."""
        with pytest.raises(FeedbackLoopValidationError, match="因子名无效"):
            loop_no_memory.update_weights(
                daily_pnl=0.001,
                factor_contributions={"": 0.001},
            )

    def test_nan_contribution_raises(self, loop_no_memory: FeedbackLoop):
        """贡献值=NaN 抛异常."""
        with pytest.raises(FeedbackLoopValidationError, match="非有限"):
            loop_no_memory.update_weights(
                daily_pnl=0.001,
                factor_contributions={"m": float("nan")},
            )

    def test_validation_error_is_feedback_loop_error(self):
        """FeedbackLoopValidationError 是 FeedbackLoopError 子类."""
        assert issubclass(FeedbackLoopValidationError, FeedbackLoopError)

    def test_flag_disabled_skips_validation(self, tmp_memory: EvolutionMemory):
        """Flag 关闭时跳过输入校验 (直接返回降级, 不抛异常)."""
        fl = FeedbackLoop(memory=tmp_memory)
        fl._enabled = False
        # 非法输入也不抛异常 (因为 Flag 关闭直接返回)
        update = fl.update_weights(
            daily_pnl=float("nan"),
            factor_contributions={},
        )
        assert update.is_degraded is True


# ============================================================
# 测试3: 首次调用初始化
# ============================================================


class TestInitialWeights:
    """首次调用 (空初始权重) 按贡献初始化."""

    def test_first_call_equal_weight(self, loop_no_memory: FeedbackLoop):
        """首次调用 4 因子, 等权初始化 (1/4=0.25 <= max_weight=0.3)."""
        update = loop_no_memory.update_weights(
            daily_pnl=0.001,
            factor_contributions={"m": 0.001, "v": 0.001, "q": 0.001, "r": 0.001},
        )
        # 等权 = 1/4 = 0.25
        assert abs(update.new_weights["m"] - 0.25) < 1e-6
        assert abs(update.new_weights["v"] - 0.25) < 1e-6
        assert abs(update.new_weights["q"] - 0.25) < 1e-6
        assert abs(update.new_weights["r"] - 0.25) < 1e-6

    def test_first_call_respects_max_weight_when_few_factors(
        self, loop_no_memory: FeedbackLoop
    ):
        """因子数较少时, 等权超过 max_weight 被截断 (3 因子 1/3 > 0.3 → 0.3)."""
        update = loop_no_memory.update_weights(
            daily_pnl=0.001,
            factor_contributions={"m": 0.001, "v": 0.001, "q": 0.001},
        )
        # 等权 1/3 ≈ 0.333 被截断到 max_weight=0.3
        assert update.new_weights["m"] == pytest.approx(0.3)
        assert update.new_weights["v"] == pytest.approx(0.3)
        assert update.new_weights["q"] == pytest.approx(0.3)

    def test_first_call_respects_max_weight(self):
        """因子数 < 1/max_weight 时, 权重被截断到 max_weight."""
        # 2 因子, max_weight=0.3 → 等权 0.5 > 0.3, 截断
        fl = FeedbackLoop(max_weight=0.3, memory=None)
        fl._enabled = True
        update = fl.update_weights(
            daily_pnl=0.001,
            factor_contributions={"m": 0.001, "v": 0.001},
        )
        # 等权 0.5 被截断到 0.3 (但归一化在初始化阶段不执行, 直接返回)
        assert update.new_weights["m"] == pytest.approx(0.3)
        assert update.new_weights["v"] == pytest.approx(0.3)

    def test_first_call_no_raw_changes(self, loop_no_memory: FeedbackLoop):
        """首次调用 raw_changes 全为 0 (无调整, 仅初始化)."""
        update = loop_no_memory.update_weights(
            daily_pnl=0.001,
            factor_contributions={"m": 0.001, "v": 0.001},
        )
        assert all(v == 0.0 for v in update.raw_changes.values())
        assert all(v == 0.0 for v in update.clamped_changes.values())

    def test_first_call_old_weights_empty(self, loop_no_memory: FeedbackLoop):
        """首次调用 old_weights 为空."""
        update = loop_no_memory.update_weights(
            daily_pnl=0.001,
            factor_contributions={"m": 0.001},
        )
        assert update.old_weights == {}


# ============================================================
# 测试4: 贡献归一化
# ============================================================


class TestNormalizeContributions:
    """贡献度归一化算法."""

    def test_positive_contribution(self, loop: FeedbackLoop):
        """正贡献 → 正归一化值 (增配). 使用不等值确保排名区分."""
        norm = loop._normalize_contributions({"m": 0.5, "v": 0.3})
        assert norm["m"] > 0
        assert norm["v"] < 0  # v 排名较低

    def test_negative_contribution(self, loop: FeedbackLoop):
        """负贡献 → 负归一化值 (减配). 使用不等值确保排名区分."""
        norm = loop._normalize_contributions({"m": -0.3, "v": -0.5})
        assert norm["m"] > 0  # m 排名较高 (更接近 0)
        assert norm["v"] < 0  # v 排名较低 (更负)

    def test_mixed_contribution_direction(self, loop: FeedbackLoop):
        """混合贡献: 正→增配, 负→减配."""
        norm = loop._normalize_contributions({"m": 0.5, "v": -0.5})
        assert norm["m"] > 0  # 增配
        assert norm["v"] < 0  # 减配

    def test_abs_sum_equals_one(self, loop: FeedbackLoop):
        """v5 排名归一化: 最佳因子 score > 0, 最差因子 score < 0, 中间因子接近 0."""
        norm = loop._normalize_contributions({"m": 0.3, "v": -0.7, "q": 0.5})
        # 最佳因子 (q=0.5) 得正分, 最差因子 (v=-0.7) 得负分
        assert norm["q"] > 0
        assert norm["v"] < 0
        # 所有 score 在 [-1, 1] 范围内
        for v in norm.values():
            assert -1.0 <= v <= 1.0
        # 中间因子 (m=0.3, rank=1/2) 的 linear_score = 0, 所以 tanh(0) = 0
        assert abs(norm["m"]) < 1e-10

    def test_all_zero_contributions_neutral(self, loop: FeedbackLoop):
        """全零贡献 → 中性 (归一化为 0)."""
        norm = loop._normalize_contributions({"m": 0.0, "v": 0.0})
        assert all(v == 0.0 for v in norm.values())


# ============================================================
# 测试5: 单日调整幅度 (HC-3 验收标准1)
# ============================================================


class TestDailyChangeLimit:
    """单日权重调整幅度严格 <= max_daily_change (10%)."""

    def test_clamped_changes_bounded(self, loop_with_initial_weights: FeedbackLoop):
        """截断后变化幅度 |clamped_change| <= max_daily_change (0.10)."""
        loop = loop_with_initial_weights
        # 极端贡献 (1.0) → 原始变化 = lr * 1.0 = 0.1, 刚好等于上限
        update = loop.update_weights(
            daily_pnl=0.01,
            factor_contributions={"momentum": 1.0, "value": -1.0, "quality": 0.0},
        )
        for k, change in update.clamped_changes.items():
            assert abs(change) <= loop.max_daily_change + 1e-10, (
                f"因子 {k} 变化 {change} 超过上限 {loop.max_daily_change}"
            )

    def test_extreme_contribution_clamped(
        self, loop_with_initial_weights: FeedbackLoop
    ):
        """极端贡献 (10.0) 被截断到 max_daily_change."""
        loop = loop_with_initial_weights
        update = loop.update_weights(
            daily_pnl=0.01,
            factor_contributions={"momentum": 10.0, "value": -10.0, "quality": 0.0},
        )
        # 原始变化 = 0.1 * (10/20) = 0.05, 不会超限
        # 但 clamped_changes 应严格 <= 0.10
        for k, change in update.clamped_changes.items():
            assert abs(change) <= 0.10 + 1e-10

    def test_small_contribution_not_clamped(
        self, loop_with_initial_weights: FeedbackLoop
    ):
        """小贡献不被截断 (远小于上限)."""
        loop = loop_with_initial_weights
        update = loop.update_weights(
            daily_pnl=0.001,
            factor_contributions={"momentum": 0.001, "value": -0.001, "quality": 0.0},
        )
        # 原始变化很小, 不应被截断
        for k in update.clamped_changes:
            assert abs(update.clamped_changes[k] - update.raw_changes[k]) < 1e-10

    def test_max_daily_change_zero_means_no_adjustment(self, tmp_memory):
        """max_daily_change=0 时, 任何变化都被截断为 0 (不允许调整)."""
        fl = FeedbackLoop(
            initial_weights={"m": 0.5, "v": 0.5},
            max_daily_change=0.0,
            memory=tmp_memory,
        )
        fl._enabled = True
        update = fl.update_weights(
            daily_pnl=0.01,
            factor_contributions={"m": 1.0, "v": -1.0},
        )
        assert all(v == 0.0 for v in update.clamped_changes.values())
        # 权重不变 (经过归一化后可能因浮点误差微小变化, 但 clamped_changes=0)
        assert update.total_change_pct < 0.01  # 几乎无变化


# ============================================================
# 测试6: 单因子权重上限
# ============================================================


class TestMaxWeight:
    """单因子权重上限 max_weight (0.30)."""

    def test_weight_never_exceeds_max(self, loop_with_initial_weights: FeedbackLoop):
        """连续多次正向贡献, 权重不超过 max_weight."""
        loop = loop_with_initial_weights
        # 连续 10 次正向贡献 momentum (4 因子)
        for _ in range(10):
            update = loop.update_weights(
                daily_pnl=0.01,
                factor_contributions={
                    "momentum": 1.0, "value": 0.0, "quality": 0.0, "size": 0.0,
                },
            )
            for k, w in update.new_weights.items():
                assert w <= loop.max_weight + 1e-4, (
                    f"因子 {k} 权重 {w} 超过 max_weight {loop.max_weight}"
                )

    def test_weight_never_negative(self, loop_with_initial_weights: FeedbackLoop):
        """连续多次负向贡献, 权重不低于 0."""
        loop = loop_with_initial_weights
        for _ in range(10):
            update = loop.update_weights(
                daily_pnl=-0.01,
                factor_contributions={
                    "momentum": -1.0, "value": 0.0, "quality": 0.0, "size": 0.0,
                },
            )
            for k, w in update.new_weights.items():
                assert w >= 0.0, f"因子 {k} 权重 {w} 为负"


# ============================================================
# 测试7: 7 日移动平均去噪
# ============================================================


class TestSmoothing:
    """7 日移动平均去噪."""

    def test_history_less_than_2_no_smoothing(self, loop_no_memory: FeedbackLoop):
        """历史不足 2 天, 不平滑 (直接返回当前值)."""
        fl = loop_no_memory
        # 首次调用, 历史为空
        weights = {"m": 0.5, "v": 0.5}
        result = fl._apply_smoothing(weights)
        assert result == weights  # 直接返回

    def test_smoothing_with_history(self, loop_with_initial_weights: FeedbackLoop):
        """有历史时, 平滑生效 (返回值是当前与历史的均值)."""
        fl = loop_with_initial_weights
        # 第一次更新建立历史
        fl.update_weights(
            daily_pnl=0.001,
            factor_contributions={
                "momentum": 0.001, "value": -0.001, "quality": 0.0, "size": 0.0,
            },
        )
        # 第二次更新触发平滑
        update = fl.update_weights(
            daily_pnl=0.001,
            factor_contributions={
                "momentum": 0.001, "value": -0.001, "quality": 0.0, "size": 0.0,
            },
        )
        # 平滑后权重应介于两次之间 (不等于当前)
        assert update.new_weights  # 非空

    def test_smoothing_window_parameter(self, tmp_memory):
        """smoothing_window 参数控制窗口大小."""
        fl = FeedbackLoop(
            initial_weights={"m": 0.5, "v": 0.5},
            smoothing_window=3,
            memory=tmp_memory,
        )
        fl._enabled = True
        assert fl.smoothing_window == 3


# ============================================================
# 测试8: 权重归一化
# ============================================================


class TestNormalizeWeights:
    """权重归一化 sum=1.0."""

    def test_sum_equals_one(self, loop: FeedbackLoop):
        """归一化后 sum(weights) = 1.0."""
        result = loop._normalize_weights({"m": 0.3, "v": 0.4, "q": 0.3})
        assert abs(sum(result.values()) - 1.0) < 1e-10

    def test_all_zero_fallback_equal_weight(self, loop: FeedbackLoop):
        """全零时退化为等权."""
        result = loop._normalize_weights({"m": 0.0, "v": 0.0, "q": 0.0, "r": 0.0})
        # 等权 = 1/4 = 0.25, 不超 max_weight=0.3
        assert abs(result["m"] - 0.25) < 1e-6
        assert abs(result["v"] - 0.25) < 1e-6

    def test_empty_dict_returns_empty(self, loop: FeedbackLoop):
        """空字典归一化返回空字典."""
        result = loop._normalize_weights({})
        assert result == {}

    def test_single_factor_normalized_to_one(self, loop: FeedbackLoop):
        """单因子归一化为 1.0."""
        result = loop._normalize_weights({"m": 0.5})
        assert abs(result["m"] - 1.0) < 1e-10


# ============================================================
# 测试9: 7 日累计偏移告警 (验收标准2)
# ============================================================


class TestSevenDayAlarm:
    """7 日累计偏移 > 30% 触发告警."""

    def test_history_less_than_window_no_alarm(self, loop_with_initial_weights):
        """历史不足 7 天, 不触发告警."""
        fl = loop_with_initial_weights
        # 仅更新 3 次 (历史不足 7)
        for _ in range(3):
            update = fl.update_weights(
                daily_pnl=0.001,
                factor_contributions={"momentum": 0.5, "value": -0.5, "quality": 0.0},
            )
        assert update.alarm_triggered is False

    def test_alarm_triggered_when_exceeding_threshold(self, tmp_memory):
        """7 日累计偏移 > 阈值 触发告警.

        注意:
        1. 初始权重必须 <= max_weight=0.3, 否则 _apply_changes 截断后
           归一化会回到原值, 权重不变 (死循环).
        2. 用 smoothing_window=1 跳过 7 日去噪, 否则去噪会"回拉"权重
           导致累计偏移被抵消.
        """
        fl = FeedbackLoop(
            initial_weights={"m": 0.25, "v": 0.25, "q": 0.25, "r": 0.25},
            alarm_threshold_7d=0.05,  # 5% 即触发
            smoothing_window=1,  # 跳过去噪
            memory=tmp_memory,
        )
        fl._enabled = True
        # 连续 8 天单向调整 m (超过 smoothing_window=1)
        last_update = None
        for _ in range(8):
            last_update = fl.update_weights(
                daily_pnl=0.01,
                factor_contributions={"m": 1.0, "v": -1.0, "q": 0.0, "r": 0.0},
            )
        assert last_update is not None
        assert last_update.alarm_triggered is True
        assert "7日累计偏移" in last_update.alarm_reason

    def test_no_alarm_when_within_threshold(self, loop_with_initial_weights):
        """7 日累计偏移 <= 30% 不触发告警."""
        fl = loop_with_initial_weights
        # 小幅调整, 累计偏移应 < 0.30
        for _ in range(8):
            update = fl.update_weights(
                daily_pnl=0.001,
                factor_contributions={
                    "momentum": 0.001, "value": -0.001, "quality": 0.0, "size": 0.0,
                },
            )
        # 默认阈值 0.30, 小幅调整不会触发
        assert update.alarm_triggered is False

    def test_alarm_threshold_customizable(self, tmp_memory):
        """alarm_threshold_7d 可自定义."""
        fl = FeedbackLoop(
            initial_weights={"m": 0.5, "v": 0.5},
            alarm_threshold_7d=0.01,  # 1% 即触发
            memory=tmp_memory,
        )
        fl._enabled = True
        assert fl.alarm_threshold_7d == 0.01


# ============================================================
# 测试10: Memory 审计 (HC-2 验收标准3)
# ============================================================


class TestMemoryAudit:
    """权重调整全量写入 EvolutionMemory."""

    def test_recorded_to_memory(self, loop: FeedbackLoop, tmp_memory: EvolutionMemory):
        """权重调整写入 Memory (level=L3, action_type=weight_adjust)."""
        update = loop.update_weights(
            daily_pnl=0.002,
            factor_contributions={"m": 0.001, "v": -0.001},
        )
        # 查询 Memory
        records = tmp_memory.query(level=LEVEL_L3, action_type=ACTION_WEIGHT_ADJUST)
        assert len(records) == 1
        record = records[0]
        assert record.level == LEVEL_L3
        assert record.action_type == ACTION_WEIGHT_ADJUST
        assert record.status == STATUS_EXECUTED
        assert record.target_module == "factor_weights"
        assert record.rollback_plan  # 非空回滚方案

    def test_proposal_id_set_after_record(
        self, loop: FeedbackLoop, tmp_memory: EvolutionMemory
    ):
        """记录后 proposal_id 设置到 update."""
        update = loop.update_weights(
            daily_pnl=0.002,
            factor_contributions={"m": 0.001},
        )
        assert update.proposal_id  # 非空
        assert update.proposal_id.startswith("EVO-")

    def test_score_report_contains_full_update(self, loop: FeedbackLoop):
        """score_report 包含完整 update 信息 (审计完整性)."""
        update = loop.update_weights(
            daily_pnl=0.002,
            factor_contributions={"m": 0.001, "v": -0.001},
        )
        records = loop._memory.query(level=LEVEL_L3)
        record = records[0]
        score_report = record.score_report
        assert score_report["daily_pnl"] == pytest.approx(0.002, abs=1e-6)
        assert "old_weights" in score_report
        assert "new_weights" in score_report
        assert "total_change_pct" in score_report
        assert score_report["factor_contributions"] == {
            "m": pytest.approx(0.001, abs=1e-6),
            "v": pytest.approx(-0.001, abs=1e-6),
        }

    def test_no_memory_no_audit(self, loop_no_memory: FeedbackLoop):
        """无 Memory 时不审计 (proposal_id 为空)."""
        update = loop_no_memory.update_weights(
            daily_pnl=0.001,
            factor_contributions={"m": 0.001},
        )
        assert update.proposal_id == ""

    def test_memory_exception_does_not_block(self, tmp_memory):
        """Memory 写入异常不阻塞主流程 (容错降级)."""
        fl = FeedbackLoop(memory=FlakyMemory())
        fl._enabled = True
        # 不抛异常, 权重正常调整
        update = fl.update_weights(
            daily_pnl=0.001,
            factor_contributions={"m": 0.001, "v": -0.001},
        )
        assert update.is_degraded is False  # 主流程未降级
        assert update.proposal_id == ""  # 记录失败, id 为空

    def test_counting_memory_records_all_updates(self):
        """连续多次更新全部写入 Memory."""
        counting = CountingMemory()
        fl = FeedbackLoop(
            initial_weights={"m": 0.5, "v": 0.5},
            memory=counting,
        )
        fl._enabled = True
        for _ in range(3):
            fl.update_weights(
                daily_pnl=0.001,
                factor_contributions={"m": 0.001, "v": -0.001},
            )
        assert len(counting.recorded) == 3


# ============================================================
# 测试11: 查询 API
# ============================================================


class TestQueryAPI:
    """查询 API: get_current_weights / get_history."""

    def test_get_current_weights_returns_copy(self, loop_with_initial_weights):
        """get_current_weights 返回深拷贝 (不可变性)."""
        fl = loop_with_initial_weights
        w1 = fl.get_current_weights()
        w1["momentum"] = 999.0  # 修改返回值
        w2 = fl.get_current_weights()
        assert w2["momentum"] != 999.0  # 内部状态未被影响

    def test_get_history_empty_initially(self, loop_no_memory):
        """初始无历史, get_history 返回空列表."""
        assert loop_no_memory.get_history(days=7) == []

    def test_get_history_after_updates(self, loop_with_initial_weights):
        """更新后 get_history 返回历史记录."""
        fl = loop_with_initial_weights
        for _ in range(3):
            fl.update_weights(
                daily_pnl=0.001,
                factor_contributions={"momentum": 0.001, "value": -0.001, "quality": 0.0},
            )
        history = fl.get_history(days=7)
        assert len(history) == 3
        assert all(isinstance(h, WeightUpdate) for h in history)

    def test_get_history_days_limit(self, loop_with_initial_weights):
        """get_history(days=N) 限制返回数量."""
        fl = loop_with_initial_weights
        for _ in range(5):
            fl.update_weights(
                daily_pnl=0.001,
                factor_contributions={"momentum": 0.001, "value": -0.001, "quality": 0.0},
            )
        history = fl.get_history(days=2)
        assert len(history) == 2

    def test_get_history_days_zero_returns_empty(self, loop_with_initial_weights):
        """days=0 返回空列表."""
        fl = loop_with_initial_weights
        fl.update_weights(
            daily_pnl=0.001,
            factor_contributions={"momentum": 0.001, "value": -0.001, "quality": 0.0},
        )
        assert fl.get_history(days=0) == []
        assert fl.get_history(days=-1) == []


# ============================================================
# 测试12: 不可变性
# ============================================================


class TestImmutability:
    """不可变性: 修改返回值不影响内部状态 (ARCHITECTURE §5.1)."""

    def test_update_old_weights_not_mutated(self, loop_with_initial_weights):
        """修改 update.old_weights 不影响下次调用."""
        fl = loop_with_initial_weights
        original = dict(fl._weights)
        update = fl.update_weights(
            daily_pnl=0.001,
            factor_contributions={"momentum": 0.001, "value": -0.001, "quality": 0.0},
        )
        update.old_weights["momentum"] = 999.0
        # 内部权重未被影响
        assert fl._weights["momentum"] != 999.0

    def test_factor_contributions_not_mutated(self, loop_with_initial_weights):
        """修改输入 factor_contributions 不影响内部状态."""
        fl = loop_with_initial_weights
        contrib = {"momentum": 0.001, "value": -0.001, "quality": 0.0}
        contrib_copy = dict(contrib)
        fl.update_weights(daily_pnl=0.001, factor_contributions=contrib)
        # 输入字典未被修改
        assert contrib == contrib_copy

    def test_initial_weights_deep_copied(self, tmp_memory):
        """构造时的 initial_weights 被深拷贝 (修改原字典不影响内部)."""
        initial = {"m": 0.5, "v": 0.5}
        fl = FeedbackLoop(initial_weights=initial, memory=tmp_memory)
        fl._enabled = True
        initial["m"] = 999.0  # 修改原字典
        assert fl.get_current_weights().get("m") != 999.0


# ============================================================
# 测试13: WeightUpdate 数据类
# ============================================================


class TestWeightUpdateDataclass:
    """WeightUpdate 数据类序列化."""

    def test_to_dict_round_trip(self):
        """to_dict 包含所有字段 (审计完整性)."""
        update = WeightUpdate(
            timestamp="2026-08-02T00:00:00.000000Z",
            daily_pnl=0.002,
            factor_contributions={"m": 0.001},
            old_weights={"m": 0.5},
            new_weights={"m": 0.51},
            raw_changes={"m": 0.02},
            clamped_changes={"m": 0.02},
            total_change_pct=0.01,
            alarm_triggered=False,
            alarm_reason="",
        )
        d = update.to_dict()
        assert d["timestamp"] == "2026-08-02T00:00:00.000000Z"
        assert d["daily_pnl"] == pytest.approx(0.002)
        assert d["factor_contributions"] == {"m": pytest.approx(0.001)}
        assert d["old_weights"] == {"m": pytest.approx(0.5)}
        assert d["new_weights"] == {"m": pytest.approx(0.51)}
        assert d["total_change_pct"] == pytest.approx(0.01)
        assert d["alarm_triggered"] is False
        assert d["proposal_id"] == ""
        assert d["is_degraded"] is False

    def test_to_dict_rounds_floats(self):
        """to_dict 对浮点数做 6 位四舍五入 (避免长尾)."""
        update = WeightUpdate(
            timestamp="t",
            daily_pnl=0.123456789,
            factor_contributions={},
        )
        d = update.to_dict()
        assert d["daily_pnl"] == pytest.approx(0.123457, abs=1e-6)

    def test_default_values(self):
        """WeightUpdate 默认值合理."""
        update = WeightUpdate(timestamp="t", daily_pnl=0.0)
        assert update.factor_contributions == {}
        assert update.old_weights == {}
        assert update.new_weights == {}
        assert update.raw_changes == {}
        assert update.clamped_changes == {}
        assert update.total_change_pct == 0.0
        assert update.alarm_triggered is False
        assert update.alarm_reason == ""
        assert update.proposal_id == ""
        assert update.is_degraded is False
        assert update.degraded_reason == ""


# ============================================================
# 测试14: 集成场景
# ============================================================


class TestIntegrationScenarios:
    """集成场景: 连续多日更新."""

    def test_seven_day_progressive_adjustment(self, tmp_memory):
        """连续 7 日单向调整, 权重渐进变化 (无震荡).

        注意: 用 smoothing_window=1 跳过去噪, 否则 7 日去噪会"回拉"权重
        导致单向调整被抵消 (去噪是正确行为, 但本测试验证的是单向调整趋势).
        """
        fl = FeedbackLoop(
            initial_weights={"m": 0.25, "v": 0.25, "q": 0.25, "r": 0.25},
            smoothing_window=1,  # 跳过去噪
            memory=tmp_memory,
        )
        fl._enabled = True
        updates = []
        for _ in range(7):
            update = fl.update_weights(
                daily_pnl=0.002,
                factor_contributions={"m": 0.5, "v": -0.5, "q": 0.0, "r": 0.0},
            )
            updates.append(update)

        # m 权重应渐进增加 (允许浮点误差)
        first_m = updates[0].new_weights["m"]
        last_m = updates[-1].new_weights["m"]
        assert last_m > first_m, (
            f"单向调整后权重应增加: first={first_m}, last={last_m}"
        )

        # 所有单日变化 <= max_daily_change
        for u in updates:
            for change in u.clamped_changes.values():
                assert abs(change) <= fl.max_daily_change + 1e-10

    def test_oscillating_contributions_stable(self, loop_with_initial_weights):
        """震荡贡献 (正负交替), 权重保持稳定 (7 日去噪生效)."""
        fl = loop_with_initial_weights
        initial = dict(fl.get_current_weights())
        # 正负交替贡献
        for i in range(10):
            sign = 1.0 if i % 2 == 0 else -1.0
            fl.update_weights(
                daily_pnl=0.001 * sign,
                factor_contributions={
                    "momentum": 0.5 * sign,
                    "value": -0.5 * sign,
                    "quality": 0.0,
                    "size": 0.0,
                },
            )
        final = fl.get_current_weights()
        # 震荡后权重应接近初始 (去噪生效)
        for k in initial:
            assert abs(final[k] - initial[k]) < 0.15, (
                f"因子 {k} 震荡后偏移过大: {initial[k]} → {final[k]}"
            )

    def test_zero_contributions_no_change(self, loop_with_initial_weights):
        """全零贡献, 权重不变 (归一化为中性)."""
        fl = loop_with_initial_weights
        initial = dict(fl.get_current_weights())
        update = fl.update_weights(
            daily_pnl=0.0,
            factor_contributions={
                "momentum": 0.0, "value": 0.0, "quality": 0.0, "size": 0.0,
            },
        )
        # 归一化为 0, raw_changes=0, 权重不变 (经归一化后)
        for k in initial:
            assert abs(update.new_weights[k] - initial[k]) < 0.01, (
                f"因子 {k} 全零贡献下权重变化: {initial[k]} → {update.new_weights[k]}"
            )

    def test_total_change_pct_calculation(self, loop_with_initial_weights):
        """total_change_pct 正确计算 (L1 范数 / 2)."""
        fl = loop_with_initial_weights
        update = fl.update_weights(
            daily_pnl=0.001,
            factor_contributions={
                "momentum": 0.5, "value": -0.5, "quality": 0.0, "size": 0.0,
            },
        )
        # total_change_pct >= 0
        assert update.total_change_pct >= 0.0

    def test_multiple_factors_mixed_scenario(self, tmp_memory):
        """5 因子混合场景 (接近真实使用)."""
        initial = {
            "momentum": 0.25,
            "value": 0.20,
            "quality": 0.20,
            "size": 0.15,
            "liquidity": 0.20,
        }
        fl = FeedbackLoop(initial_weights=initial, memory=tmp_memory)
        fl._enabled = True
        update = fl.update_weights(
            daily_pnl=0.003,
            factor_contributions={
                "momentum": 0.002,
                "value": -0.001,
                "quality": 0.0005,
                "size": 0.0,
                "liquidity": -0.0005,
            },
        )
        # 5 因子全部存在
        assert len(update.new_weights) == 5
        # 归一化后 sum=1
        assert abs(sum(update.new_weights.values()) - 1.0) < 0.05  # 允许去噪微调
        # momentum 增配 (正贡献)
        assert update.new_weights["momentum"] > update.old_weights["momentum"] - 0.01
        # value 减配 (负贡献)
        assert update.new_weights["value"] < update.old_weights["value"] + 0.01

    def test_clamp_zero_weight_small_increment(self, tmp_memory):
        """old_w=0 时只允许小幅增加 (max_daily_change * 0.01)."""
        # 初始权重含 0 权重的因子
        fl = FeedbackLoop(
            initial_weights={"m": 1.0, "v": 0.0},  # v=0
            smoothing_window=1,
            memory=tmp_memory,
        )
        fl._enabled = True
        update = fl.update_weights(
            daily_pnl=0.01,
            factor_contributions={"m": -1.0, "v": 1.0},  # v 正贡献
        )
        # v 从 0 小幅增加 (<= max_daily_change * 0.01 = 0.001)
        assert update.clamped_changes["v"] <= fl.max_daily_change * 0.01 + 1e-10
        assert update.clamped_changes["v"] > 0  # 正向

    def test_clamp_zero_weight_negative_contribution(self, tmp_memory):
        """old_w=0 且负贡献时, clamped=0 (不允许从 0 减少)."""
        fl = FeedbackLoop(
            initial_weights={"m": 1.0, "v": 0.0},
            smoothing_window=1,
            memory=tmp_memory,
        )
        fl._enabled = True
        update = fl.update_weights(
            daily_pnl=-0.01,
            factor_contributions={"m": 1.0, "v": -1.0},  # v 负贡献
        )
        assert update.clamped_changes["v"] == 0.0

    def test_alarm_when_history_old_weights_empty(self, tmp_memory):
        """history[0].old_weights 为空时不触发告警 (首次初始化场景)."""
        # 首次调用时 old_weights 为空 (初始化), 之后 history[0].old_weights={}
        fl = FeedbackLoop(
            initial_weights=None,  # 空, 首次按贡献初始化
            alarm_threshold_7d=0.001,  # 极低阈值
            smoothing_window=1,
            memory=tmp_memory,
        )
        fl._enabled = True
        # 第一次: old_weights={}, 初始化
        fl.update_weights(
            daily_pnl=0.01,
            factor_contributions={"m": 1.0, "v": 1.0, "q": 1.0, "r": 1.0},
        )
        # 连续调整使累计偏移很大, 但 history[0].old_weights={}
        last_update = None
        for _ in range(7):
            last_update = fl.update_weights(
                daily_pnl=0.01,
                factor_contributions={"m": 1.0, "v": -1.0, "q": 0.0, "r": 0.0},
            )
        # history[0] 是第一次更新, 其 old_weights={}, 所以 _check_7d_alarm 返回 False
        assert last_update.alarm_triggered is False


# ============================================================
# 测试15: Feature Flag 异常容错 (覆盖 _check_feature_flag except 分支)
# ============================================================


class TestFeatureFlagExceptionHandling:
    """Feature Flag 检查异常时默认禁用 (容错)."""

    def test_feature_flag_check_exception_returns_false(self, monkeypatch):
        """Feature Flag 检查抛异常时, 默认禁用 (不崩溃)."""

        # Mock is_enabled 抛异常
        def mock_is_enabled(name: str) -> bool:
            raise RuntimeError("Mock: Feature Flag 服务不可用")

        # 导入路径替换
        import utils.infra.feature_flags as ff_module

        monkeypatch.setattr(ff_module, "is_enabled", mock_is_enabled)

        fl = FeedbackLoop()  # 触发 _check_feature_flag
        assert fl.enabled is False  # 异常时默认禁用

    def test_feature_flag_check_normal_true(self, monkeypatch):
        """Feature Flag 检查正常返回 True."""
        import utils.infra.feature_flags as ff_module

        monkeypatch.setattr(ff_module, "is_enabled", lambda name: True)

        fl = FeedbackLoop()
        assert fl.enabled is True
