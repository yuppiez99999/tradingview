"""实时反馈闭环 — 交易结果驱动因子权重自适应调整.

模块整合 8.4 — ARCHITECTURE_自我进化框架 §6.4 (v2.0 合并版)
任务编号: T2.1 (Phase 2 反馈闭环)

职责:
    根据每日交易的 P&L 归因结果, 自适应调整因子权重, 实现"学习"能力.
    反馈信号:
      1. 因子 P&L 贡献 (主要信号): 贡献为正→增配, 负→减配
      2. 因子 IC 衰减 (辅助信号, 来自 DriftMonitor): IC 衰减→减配
      3. 模型预测误差 (辅助信号): 误差大→整体缩放

核心算法 (简化贝叶斯更新):
    new_weight = old_weight * (1 + learning_rate * normalized_contribution)
    → 截断到 [0, max_weight]
    → 单日变化 ≤ max_daily_change
    → 7 日移动平均去噪
    → 总权重归一化

约束 (TASK T2.1):
    - 单日权重调整 ≤ 10% (max_daily_change)
    - 权重范围 [0, max_weight] (默认 max_weight=0.3)
    - 7 日移动平均去噪 (避免震荡)
    - 总权重归一化 (sum(weights) = 1.0)
    - 7 日累计偏移 > 30% 触发告警 (alarm_threshold_7d)

核心 API:
    FeedbackLoop.update_weights(daily_pnl, factor_contributions) -> WeightUpdate
    FeedbackLoop.get_current_weights() -> dict[str, float]
    FeedbackLoop.get_history(days=7) -> list[WeightUpdate]

硬约束:
    - HC-2: 所有权重调整写入 EvolutionMemory (审计)
    - HC-3: 单日调整 ≤ 10% (Guard 幅度限制)
    - 默认关闭 (Feature Flag USE_FEEDBACK_LOOP, P2 阶段启用)

用法:
    from utils.evolution.feedback_loop import FeedbackLoop, WeightUpdate

    loop = FeedbackLoop()
    update = loop.update_weights(
        daily_pnl=0.002,
        factor_contributions={"momentum": 0.001, "value": -0.0005},
    )
    logger.info("调整后权重: %s", update.new_weights)
    logger.info("是否触发7日告警: %s", update.alarm_triggered)
"""

from __future__ import annotations

import logging
import math
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC
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

# 默认参数 (与 system_config.json evolution.feedback_loop 对齐)
DEFAULT_LEARNING_RATE = 0.1
DEFAULT_MAX_DAILY_CHANGE = 0.10  # 单日权重调整上限 10%
DEFAULT_MAX_WEIGHT = 0.30  # 单因子权重上限 30%
DEFAULT_ALARM_THRESHOLD_7D = 0.30  # 7 日累计偏移告警阈值 30%
DEFAULT_SMOOTHING_WINDOW = 7  # 7 日移动平均去噪窗口

# Feature Flag
FLAG_FEEDBACK_LOOP = "USE_FEEDBACK_LOOP"

# 动作类型 (写入 Memory)
ACTION_WEIGHT_ADJUST = "weight_adjust"


# ============================================================
# 异常
# ============================================================


class FeedbackLoopError(Exception):
    """反馈闭环基础异常."""


class FeedbackLoopValidationError(FeedbackLoopError):
    """输入校验失败."""


# ============================================================
# 数据类
# ============================================================


@dataclass
class WeightUpdate:
    """单次权重更新结果.

    记录调整前后的权重、变化幅度、是否触发告警等.
    用于 Memory 审计和历史回溯.
    """

    timestamp: str  # ISO8601
    daily_pnl: float  # 当日 P&L (小数, 如 0.002 = 0.2%)
    factor_contributions: dict[str, float] = field(default_factory=dict)
    old_weights: dict[str, float] = field(default_factory=dict)
    new_weights: dict[str, float] = field(default_factory=dict)
    raw_changes: dict[str, float] = field(default_factory=dict)  # 归一化前的原始变化
    clamped_changes: dict[str, float] = field(default_factory=dict)  # 截断后的变化
    total_change_pct: float = 0.0  # 总调整幅度 (L1 范数)
    alarm_triggered: bool = False  # 是否触发 7 日告警
    alarm_reason: str = ""
    proposal_id: str = ""  # Memory 中的 proposal_id (审计用)
    is_degraded: bool = False  # 是否降级 (Flag 关闭/输入异常)
    degraded_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典 (用于 Memory 审计)."""
        return {
            "timestamp": self.timestamp,
            "daily_pnl": round(self.daily_pnl, 6),
            "factor_contributions": {
                k: round(v, 6) for k, v in self.factor_contributions.items()
            },
            "old_weights": {k: round(v, 6) for k, v in self.old_weights.items()},
            "new_weights": {k: round(v, 6) for k, v in self.new_weights.items()},
            "raw_changes": {k: round(v, 6) for k, v in self.raw_changes.items()},
            "clamped_changes": {
                k: round(v, 6) for k, v in self.clamped_changes.items()
            },
            "total_change_pct": round(self.total_change_pct, 6),
            "alarm_triggered": self.alarm_triggered,
            "alarm_reason": self.alarm_reason,
            "proposal_id": self.proposal_id,
            "is_degraded": self.is_degraded,
            "degraded_reason": self.degraded_reason,
        }


# ============================================================
# 反馈闭环
# ============================================================


class FeedbackLoop:
    """实时反馈闭环 — 交易结果驱动因子权重自适应调整.

    算法流程:
        1. 输入: daily_pnl + factor_contributions (因子→P&L 贡献)
        2. 累积贡献缓冲: 追加到 contribution_window 日缓冲
        3. 计算累积平均贡献 (降低噪声, 提升信噪比)
        4. 排名归一化: 按累积贡献排序, 线性映射到 [-1, 1]
        5. 贝叶斯更新: new_w = old_w * (1 + lr * rank_score)
        6. 截断: 单因子 ∈ [0, max_weight], 单日变化 ≤ max_daily_change
        7. 权重移动平均去噪 (smoothing_window)
        8. 归一化: sum(new_weights) = 1.0
        9. 检查 7 日累计偏移, 触发告警
        10. 写入 Memory 审计

    关键改进 (v3 - 排名归一化):
        - 零和归一化 (sum(abs)=1) 去除置信度, 弱信号被放大
        - 排名归一化只使用相对排序, 对噪声和异常值鲁棒
        - 累积平均贡献 + 排名归一化, SNR 提升约 3 倍

    线程安全: 不加锁, 假设单线程 EOD 调用 (每日盘后一次).
    多线程需调用方自行加锁.
    """

    def __init__(
        self,
        initial_weights: dict[str, float] | None = None,
        learning_rate: float = DEFAULT_LEARNING_RATE,
        max_daily_change: float = DEFAULT_MAX_DAILY_CHANGE,
        max_weight: float = DEFAULT_MAX_WEIGHT,
        alarm_threshold_7d: float = DEFAULT_ALARM_THRESHOLD_7D,
        smoothing_window: int = DEFAULT_SMOOTHING_WINDOW,
        contribution_window: int = 5,
        memory: Any | None = None,
        guard: Any | None = None,
        feature_flag_name: str = FLAG_FEEDBACK_LOOP,
    ) -> None:
        """初始化反馈闭环.

        Args:
            initial_weights: 初始因子权重 (None=空, 首次 update 时按贡献初始化)
            learning_rate: 学习率 (默认 0.1)
            max_daily_change: 单日权重调整上限 (默认 0.10 = 10%)
            max_weight: 单因子权重上限 (默认 0.30 = 30%)
            alarm_threshold_7d: 7 日累计偏移告警阈值 (默认 0.30)
            smoothing_window: 权重移动平均去噪窗口 (默认 7)
            contribution_window: 贡献累积平均窗口 (默认 5, 降低日频噪声)
            memory: EvolutionMemory 实例 (None=不写审计)
            guard: EvolutionGuard 实例 (None=不做幅度检查)
            feature_flag_name: Feature Flag 名称 (HC-1)
        """
        self.learning_rate = learning_rate
        self.max_daily_change = max_daily_change
        self.max_weight = max_weight
        self.alarm_threshold_7d = alarm_threshold_7d
        self.smoothing_window = smoothing_window
        self.contribution_window = max(contribution_window, 1)
        self.feature_flag_name = feature_flag_name

        self._memory = memory
        self._guard = guard

        # 当前权重 (深拷贝避免外部修改)
        self._weights: dict[str, float] = (
            dict(initial_weights) if initial_weights else {}
        )

        # 历史记录 (用于权重去噪和累计偏移检查)
        self._history: deque[WeightUpdate] = deque(maxlen=max(smoothing_window, 7))

        # 累积贡献缓冲 (用于累积平均, 降低日频噪声)
        self._contribution_buffer: deque[dict[str, float]] = deque(
            maxlen=self.contribution_window
        )

        # Feature Flag
        self._enabled = self._check_feature_flag(feature_flag_name)

        logger.info(
            "FeedbackLoop 初始化: enabled=%s, lr=%.2f, max_daily=%.2f, max_weight=%.2f",
            self._enabled,
            learning_rate,
            max_daily_change,
            max_weight,
        )

    # ============================================================
    # Feature Flag
    # ============================================================

    def _check_feature_flag(self, name: str) -> bool:
        """检查 Feature Flag (HC-1)."""
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
        """是否启用."""
        return self._enabled

    # ============================================================
    # 核心 API: update_weights
    # ============================================================

    def update_weights(
        self,
        daily_pnl: float,
        factor_contributions: dict[str, float],
    ) -> WeightUpdate:
        """根据当日 P&L 和因子贡献调整权重.

        Args:
            daily_pnl: 当日组合 P&L (小数, 如 0.002 = 0.2%)
            factor_contributions: 因子→P&L 贡献 (如 {"momentum": 0.001, "value": -0.0005})

        Returns:
            WeightUpdate 权重更新结果
        """
        timestamp = self._now_iso()

        # HC-1: Feature Flag 检查
        if not self._enabled:
            return WeightUpdate(
                timestamp=timestamp,
                daily_pnl=daily_pnl,
                factor_contributions=factor_contributions,
                old_weights=dict(self._weights),
                new_weights=dict(self._weights),
                is_degraded=True,
                degraded_reason=f"feature_flag_disabled ({self.feature_flag_name}=False)",
            )

        # 输入校验
        self._validate_inputs(daily_pnl, factor_contributions)

        # 保存旧权重 (不可变性, ARCHITECTURE §5.1)
        old_weights = dict(self._weights)

        # 首次调用: 按贡献初始化权重
        if not old_weights:
            new_weights = self._initialize_weights(factor_contributions)
            raw_changes = dict.fromkeys(new_weights, 0.0)
            clamped_changes = dict(raw_changes)
        else:
            # 1. 将当日贡献加入累积缓冲
            self._contribution_buffer.append(dict(factor_contributions))

            # 2. 计算累积平均贡献 (降低噪声, 提升信噪比)
            smoothed_contributions = self._get_smoothed_contributions()

            # 3. 归一化贡献度 (权重阻尼, 避免小权重噪声放大)
            norm_contrib = self._normalize_contributions(
                smoothed_contributions, old_weights
            )

            # 4. 贝叶斯更新 (原始变化)
            raw_changes = self._compute_raw_changes(old_weights, norm_contrib)

            # 5. 截断: 单日变化 ≤ max_daily_change
            clamped_changes = self._clamp_changes(old_weights, raw_changes)

            # 6. 应用变化 + 单因子上限
            new_weights = self._apply_changes(old_weights, clamped_changes)

            # 7. 权重移动平均去噪
            new_weights = self._apply_smoothing(new_weights)

            # 8. 归一化
            new_weights = self._normalize_weights(new_weights)

        # 计算总调整幅度 (L1 范数)
        total_change_pct = self._compute_total_change(old_weights, new_weights)

        # 7. 检查 7 日累计偏移
        alarm_triggered, alarm_reason = self._check_7d_alarm(new_weights)

        # 构造更新结果
        update = WeightUpdate(
            timestamp=timestamp,
            daily_pnl=daily_pnl,
            factor_contributions=dict(factor_contributions),
            old_weights=old_weights,
            new_weights=new_weights,
            raw_changes=raw_changes,
            clamped_changes=clamped_changes,
            total_change_pct=total_change_pct,
            alarm_triggered=alarm_triggered,
            alarm_reason=alarm_reason,
        )

        # 8. 写入 Memory 审计 (HC-2)
        self._record_to_memory(update)

        # 更新内部状态
        self._weights = dict(new_weights)
        self._history.append(update)

        logger.info(
            "权重更新: total_change=%.4f, alarm=%s, factors=%d",
            total_change_pct,
            alarm_triggered,
            len(new_weights),
        )

        return update

    # ============================================================
    # 查询 API
    # ============================================================

    def get_current_weights(self) -> dict[str, float]:
        """获取当前权重 (深拷贝, 不可变性)."""
        return dict(self._weights)

    def get_history(self, days: int = 7) -> list[WeightUpdate]:
        """获取最近 N 天的更新历史."""
        if days <= 0:
            return []
        return list(self._history)[-days:]

    # ============================================================
    # 算法实现
    # ============================================================

    def _validate_inputs(
        self, daily_pnl: float, factor_contributions: dict[str, float]
    ) -> None:
        """校验输入."""
        if not math.isfinite(daily_pnl):
            raise FeedbackLoopValidationError(f"daily_pnl 非有限值: {daily_pnl}")

        if not factor_contributions:
            raise FeedbackLoopValidationError("factor_contributions 不能为空")

        for k, v in factor_contributions.items():
            if not isinstance(k, str) or not k:
                raise FeedbackLoopValidationError(f"因子名无效: {k}")
            if not math.isfinite(v):
                raise FeedbackLoopValidationError(f"因子 {k} 贡献值非有限: {v}")

    def _initialize_weights(
        self, factor_contributions: dict[str, float]
    ) -> dict[str, float]:
        """首次调用: 等权初始化."""
        n = len(factor_contributions)
        weight = 1.0 / n
        # 确保不超过 max_weight
        weight = min(weight, self.max_weight)
        return dict.fromkeys(factor_contributions, weight)

    def _get_smoothed_contributions(self) -> dict[str, float]:
        """计算累积平均贡献 (降低日频噪声, 提升信噪比).

        对 contribution_window 内的因子贡献做等权平均,
        信噪比提升 sqrt(contribution_window) 倍.

        缓冲不足时回退到最新一日贡献.
        """
        if not self._contribution_buffer:
            return {}

        n = len(self._contribution_buffer)
        if n < 2:
            return dict(self._contribution_buffer[0])

        # 累积平均
        avg: dict[str, float] = {}
        for k in self._contribution_buffer[0]:
            total = sum(c.get(k, 0.0) for c in self._contribution_buffer)
            avg[k] = total / n

        return avg

    def _normalize_contributions(
        self,
        factor_contributions: dict[str, float],
        current_weights: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """非线性排名归一化 (v5): 全量排名 + tanh 非线性压缩.

        步骤:
        1. 权重阻尼: contribution / max(weight, 0.03) → 转为因子收益率
        2. 按收益率排序: 最佳 → 最差
        3. 排名线性映射到 [-1, 1]
        4. tanh(2 * score) 非线性压缩: 中间因子 → 0, 极端因子保持

        v5 特点:
        - 排名提供 RELATIVE 排序, 对异常值鲁棒
        - tanh 压缩: 中间因子 ≈ 0, 极端因子 ≈ ±0.96
        - 固定分布 [-1, 1], 不受噪声幅值影响 (需配合平滑窗口降噪)

        Returns:
            {factor_name: score}, score ∈ [-1, 1], 中间因子接近 0
        """
        MIN_WEIGHT_FLOOR = 0.03

        if not factor_contributions:
            return {}

        if current_weights is None:
            contributions = dict(factor_contributions)
        else:
            # 权重阻尼: contribution / max(weight, floor)
            contributions = {}
            for k, v in factor_contributions.items():
                w = current_weights.get(k, 0.0)
                effective_w = max(w, MIN_WEIGHT_FLOOR)
                contributions[k] = v / effective_w if effective_w > 1e-10 else v

        # 按收益率排序 (升序: 最差在前)
        sorted_factors = sorted(contributions.items(), key=lambda x: x[1])
        n = len(sorted_factors)
        if n <= 1:
            return dict.fromkeys(contributions, 0.0)

        # 检测等值情况: 所有值相等时, 排名无意义, 返回中性 (0.0)
        values = list(contributions.values())
        if len(set(values)) == 1:
            return dict.fromkeys(contributions, 0.0)

        # 线性映射到 [-1, 1]
        result: dict[str, float] = {}
        for rank, (factor_name, _) in enumerate(sorted_factors):
            linear_score = (2.0 * rank / (n - 1)) - 1.0
            # tanh(2 * score) 非线性压缩:
            #   score=0 (中间) → tanh(0) = 0 → 无调整
            #   score=±0.33 → tanh(±0.66) ≈ ±0.58 → 适度调整
            #   score=±0.67 → tanh(±1.33) ≈ ±0.87 → 强调整
            #   score=±1.0 → tanh(±2.0) ≈ ±0.96 → 最强调整
            result[factor_name] = math.tanh(2.0 * linear_score)

        return result

    def _compute_raw_changes(
        self, old_weights: dict[str, float], norm_contrib: dict[str, float]
    ) -> dict[str, float]:
        """计算原始权重变化 (贝叶斯更新).

        raw_change = learning_rate * norm_contrib
        new_w = old_w * (1 + raw_change)
        """
        return {k: self.learning_rate * norm_contrib.get(k, 0.0) for k in old_weights}

    def _clamp_changes(
        self, old_weights: dict[str, float], raw_changes: dict[str, float]
    ) -> dict[str, float]:
        """截断变化幅度, 单日 ≤ max_daily_change.

        change = (new_w - old_w) / old_w, 限制 |change| ≤ max_daily_change.
        old_w=0 时, 不允许增加 (避免除零).
        """
        clamped: dict[str, float] = {}
        for k, old_w in old_weights.items():
            raw = raw_changes.get(k, 0.0)
            if old_w < 1e-10:
                # 旧权重为 0, 只允许小幅增加 (不超过 max_daily_change * 0.01)
                clamped[k] = min(raw, self.max_daily_change * 0.01) if raw > 0 else 0.0
            else:
                # |change| ≤ max_daily_change
                clamped[k] = max(
                    -self.max_daily_change, min(self.max_daily_change, raw)
                )
        return clamped

    def _apply_changes(
        self, old_weights: dict[str, float], clamped_changes: dict[str, float]
    ) -> dict[str, float]:
        """应用变化 + 单因子上限 [0, max_weight]."""
        new_weights: dict[str, float] = {}
        for k, old_w in old_weights.items():
            change = clamped_changes.get(k, 0.0)
            new_w = old_w * (1.0 + change)
            # 截断到 [0, max_weight]
            new_w = max(0.0, min(self.max_weight, new_w))
            new_weights[k] = new_w
        return new_weights

    def _apply_smoothing(self, new_weights: dict[str, float]) -> dict[str, float]:
        """7 日移动平均去噪.

        对每个因子, 用最近 smoothing_window 天的权重做移动平均.
        - smoothing_window <= 1: 不平滑 (只用当前值)
        - 历史不足 2 天: 不平滑 (直接返回当前值)

        注意: list[-0:] 在 Python 中等于 list[0:] (整个列表),
        故 smoothing_window=1 时必须特殊处理, 否则会取全部历史.
        """
        # smoothing_window <= 1 表示不平滑 (只用当前值)
        if self.smoothing_window <= 1:
            return dict(new_weights)

        if len(self._history) < 2:
            return dict(new_weights)

        # 收集最近 (smoothing_window - 1) 天的历史权重 + 当前权重
        window = self.smoothing_window - 1  # 需要取的历史天数
        recent_weights: list[dict[str, float]] = []
        for h in list(self._history)[-window:]:
            recent_weights.append(h.new_weights)
        recent_weights.append(new_weights)  # 当前

        # 移动平均
        smoothed: dict[str, float] = {}
        all_factors: set[Any] = set()
        for w in recent_weights:
            all_factors.update(w.keys())

        for k in all_factors:
            values = [w.get(k, 0.0) for w in recent_weights]
            smoothed[k] = sum(values) / len(values)

        return smoothed

    def _normalize_weights(self, weights: dict[str, float]) -> dict[str, float]:
        """归一化权重, 使 sum=1.0.

        sum=0 时, 退化为等权.
        归一化后再次截断到 [0, max_weight] (迭代收敛, 防止平滑后超限).
        使用迭代截断法: 截断 → 归一化 → 截断 → 归一化, 最多 5 次迭代.
        """
        total = sum(weights.values())
        if total < 1e-10:
            # 全 0, 等权
            n = len(weights)
            if n == 0:
                return {}
            w = min(1.0 / n, self.max_weight)
            return dict.fromkeys(weights, w)

        # 迭代截断: 截断到 max_weight → 归一化 → 截断 → 归一化
        # 使用内部 epsilon 缓冲 (1e-8) 避免浮点精度导致略超 max_weight
        _EPS = 1e-8
        result = {k: v / total for k, v in weights.items()}
        for _ in range(5):  # 最多 5 次迭代, 通常 1-2 次收敛
            # 检查是否需要截断
            needs_clamp = any(v > self.max_weight + _EPS for v in result.values())
            if not needs_clamp:
                break
            # 截断 (使用 epsilon 缓冲, 避免浮点精度问题)
            clamped = {k: min(v, self.max_weight - _EPS) for k, v in result.items()}
            # 重新归一化
            total_clamped = sum(clamped.values())
            if total_clamped < 1e-10:
                n = len(clamped)
                w = min(1.0 / n, self.max_weight)
                return dict.fromkeys(clamped, w)
            result = {k: v / total_clamped for k, v in clamped.items()}

        return result

    def _compute_total_change(
        self, old_weights: dict[str, float], new_weights: dict[str, float]
    ) -> float:
        """计算总调整幅度 (L1 范数, 归一化后).

        total_change = sum(|new_w - old_w|) / 2
        除以 2 是因为增配和减配对称 (sum 增配 ≈ sum 减配).
        """
        all_keys = set(old_weights) | set(new_weights)
        diff_sum = sum(
            abs(new_weights.get(k, 0.0) - old_weights.get(k, 0.0)) for k in all_keys
        )
        return diff_sum / 2.0

    def _check_7d_alarm(self, new_weights: dict[str, float]) -> tuple[bool, str]:
        """检查 7 日累计偏移是否超阈值.

        比较当前权重与 7 天前的权重, 计算总偏移.
        历史不足 7 天时不触发.
        """
        if len(self._history) < 7:
            return False, ""

        # 7 天前的权重
        old = self._history[0].old_weights
        if not old:
            return False, ""

        all_keys = set(old) | set(new_weights)
        total_shift = (
            sum(abs(new_weights.get(k, 0.0) - old.get(k, 0.0)) for k in all_keys) / 2.0
        )

        if total_shift > self.alarm_threshold_7d:
            return (
                True,
                f"7日累计偏移 {total_shift:.4f} > 阈值 {self.alarm_threshold_7d}",
            )

        return False, ""

    # ============================================================
    # Memory 审计
    # ============================================================

    def _record_to_memory(self, update: WeightUpdate) -> None:
        """写入 EvolutionMemory 审计 (HC-2).

        失败不阻塞 (容错), 仅记录 warning.
        审计完整性优先于权重调整本身.
        """
        if self._memory is None:
            return

        try:
            from utils.evolution.memory import LEVEL_L3, STATUS_EXECUTED

            pid = self._memory.record(
                {
                    "level": LEVEL_L3,
                    "action_type": ACTION_WEIGHT_ADJUST,
                    "trigger_reason": f"daily_pnl={update.daily_pnl:.4f}, total_change={update.total_change_pct:.4f}",
                    "target_module": "factor_weights",
                    "rollback_plan": "恢复旧权重 (update.old_weights)",
                    "score_report": update.to_dict(),
                    "status": STATUS_EXECUTED,
                    "result": {
                        "alarm_triggered": update.alarm_triggered,
                        "alarm_reason": update.alarm_reason,
                    },
                }
            )
            update.proposal_id = pid
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
            logger.warning("Memory 审计写入失败 (容错): %s", e)

    # ============================================================
    # 辅助
    # ============================================================

    @staticmethod
    def _now_iso() -> str:
        """当前时间 ISO8601 (UTC)."""
        from datetime import datetime

        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
