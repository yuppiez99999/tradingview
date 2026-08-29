"""理论度量模块 — §八.3 三个后续方向实现 (P0 改进, 2026-08-19).

================================================================
控制论理论指导下的三个定量度量:

1. LyapunovStabilityMeter — 超稳定性定量度量
   - Lyapunov 函数 V(x) 度量系统偏离目标轨道的程度
   - 离散 Lyapunov 指数 λ = log(V(t+1)/V(t))
   - λ < 0 → 渐近稳定; λ ≈ 0 → 临界稳定; λ > 0 → 失稳

2. FeedbackPhaseAnalyzer — 反馈延迟相位分析
   - DriftMonitor→重训→验证 的总延迟 vs 系统频率
   - 相位裕度 = π - 延迟 × 角频率
   - 相位裕度 > 0 → 反馈收敛; < 0 → 振荡风险

3. VariationSelectionBalancer — 变异速率与选择压力平衡
   - 变异速率 = AutoFactorFactory 生成速率 (因子/天)
   - 选择压力 = 回测门禁严格度 (1 - 通过率)
   - 平衡指数 B = 变异 × 通过率 / (变异 + 选择)
   - B ∈ [0.1, 0.5] → 健康; B < 0.1 → 枯竭; B > 0.5 → 膨胀

参考:
    - 理论: cairn/self-evolution-framework.md §八.3
    - 控制论: cairn/Reference/控制论与科学方法论_金观涛.pdf
    - Ashby 超稳定系统: cairn/Reference/douban-book-summaries-20260819.md §二.6

设计原则:
    - HC-1: 不切任何 Feature Flag
    - HC-4: 只读评估, 不修改生产状态
    - fail-safe: 度量失败返回 None, 不阻塞主流程
================================================================
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("theoretical_metrics")


# ============================================================
# 1. Lyapunov 稳定性定量度量
# ============================================================


@dataclass
class LyapunovState:
    """单个时间点的 Lyapunov 状态."""

    timestamp: str
    lyapunov_value: float
    lyapunov_exponent: float
    is_stable: bool
    deviation_components: dict[str, float] = field(default_factory=dict)

    @property
    def stability_label(self) -> str:
        if self.lyapunov_exponent < -0.1:
            return "渐近稳定"
        if self.lyapunov_exponent < 0:
            return "稳定"
        if self.lyapunov_exponent < 0.1:
            return "临界稳定"
        return "失稳"


@dataclass
class LyapunovSummary:
    """Lyapunov 稳定性汇总报告."""

    states: list[LyapunovState] = field(default_factory=list)
    mean_exponent: float = 0.0
    max_exponent: float = 0.0
    stable_ratio: float = 0.0
    is_system_stable: bool = False
    assessment: str = ""

    def to_dict(self) -> dict:
        return {
            "mean_exponent": self.mean_exponent,
            "max_exponent": self.max_exponent,
            "stable_ratio": self.stable_ratio,
            "is_system_stable": self.is_system_stable,
            "assessment": self.assessment,
            "state_count": len(self.states),
        }


class LyapunovStabilityMeter:
    """Lyapunov 稳定性定量度量器 — §八.3 方向 1.

    Lyapunov 函数 V(x) 度量系统偏离目标轨道的程度:
        V = w_ic × |IC - IC_target|² + w_ret × |return - return_target|² + w_drift × drift_score²

    离散 Lyapunov 指数:
        λ = log(V(t+1) / V(t))
        λ < 0 → 渐近稳定 (偏差在收敛)
        λ ≈ 0 → 临界稳定
        λ > 0 → 失稳 (偏差在发散)

    用途:
        - Phase B B1 的退出指标: 连续 N 天 λ < 0 才算超稳定
        - 量化判断策略进化是否发散
    """

    def __init__(
        self,
        ic_target: float = 0.03,
        return_target: float = 0.0,
        w_ic: float = 0.4,
        w_return: float = 0.3,
        w_drift: float = 0.3,
        stability_threshold: float = -0.05,
        min_consecutive_days: int = 5,
    ) -> None:
        """初始化 Lyapunov 度量器.

        Args:
            ic_target: 目标 IC (默认 0.03)
            return_target: 目标收益 (默认 0.0, 即不偏离)
            w_ic: IC 偏差权重
            w_return: 收益偏差权重
            w_drift: 漂移分数权重
            stability_threshold: 稳定性判定阈值 (λ < 此值视为稳定)
            min_consecutive_days: 判定超稳定所需连续稳定天数
        """
        self.ic_target = ic_target
        self.return_target = return_target
        self.w_ic = w_ic
        self.w_return = w_return
        self.w_drift = w_drift
        self.stability_threshold = stability_threshold
        self.min_consecutive_days = min_consecutive_days
        self._history: list[LyapunovState] = []
        self._prev_v: Optional[float] = None

    def compute_lyapunov_value(
        self,
        ic: float,
        daily_return: float,
        drift_score: float = 0.0,
    ) -> float:
        """计算 Lyapunov 函数值 V(x).

        Args:
            ic: 当前 IC 值
            daily_return: 当日收益
            drift_score: 漂移分数 (0+, 越大漂移越严重)

        Returns:
            V 值 (>= 0, 越小越接近目标轨道)
        """
        ic_deviation = (ic - self.ic_target) ** 2
        return_deviation = (daily_return - self.return_target) ** 2
        drift_deviation = drift_score**2

        v = (
            self.w_ic * ic_deviation
            + self.w_return * return_deviation
            + self.w_drift * drift_deviation
        )
        return max(v, 1e-10)

    def update(
        self,
        timestamp: str,
        ic: float,
        daily_return: float,
        drift_score: float = 0.0,
    ) -> LyapunovState:
        """更新 Lyapunov 状态.

        Args:
            timestamp: 时间戳 (YYYY-MM-DD)
            ic: 当前 IC 值
            daily_return: 当日收益
            drift_score: 漂移分数

        Returns:
            LyapunovState
        """
        v = self.compute_lyapunov_value(ic, daily_return, drift_score)

        if self._prev_v is not None and self._prev_v > 1e-10:
            exponent = math.log(v / self._prev_v)
        else:
            exponent = 0.0

        is_stable = exponent < self.stability_threshold

        state = LyapunovState(
            timestamp=timestamp,
            lyapunov_value=v,
            lyapunov_exponent=exponent,
            is_stable=is_stable,
            deviation_components={
                "ic_deviation": abs(ic - self.ic_target),
                "return_deviation": abs(daily_return - self.return_target),
                "drift_deviation": drift_score,
            },
        )

        self._history.append(state)
        self._prev_v = v
        return state

    def assess_stability(self) -> LyapunovSummary:
        """评估系统整体稳定性.

        Returns:
            LyapunovSummary
        """
        if len(self._history) < 2:
            return LyapunovSummary(
                assessment="样本不足 (需 ≥2 个时间点)",
            )

        exponents = [s.lyapunov_exponent for s in self._history[1:]]
        mean_exp = float(np.mean(exponents))
        max_exp = float(np.max(exponents))
        stable_count = sum(1 for s in self._history[1:] if s.is_stable)
        stable_ratio = stable_count / len(self._history[1:])

        consecutive = 0
        max_consecutive = 0
        for s in self._history[1:]:
            if s.is_stable:
                consecutive += 1
                max_consecutive = max(max_consecutive, consecutive)
            else:
                consecutive = 0

        is_stable = (
            mean_exp < self.stability_threshold
            and max_consecutive >= self.min_consecutive_days
            and stable_ratio >= 0.6
        )

        if is_stable:
            assessment = (
                f"系统超稳定: 平均 λ={mean_exp:.4f} < {self.stability_threshold}, "
                f"连续稳定 {max_consecutive} 天 ≥ {self.min_consecutive_days}, "
                f"稳定占比 {stable_ratio:.1%} ≥ 60%"
            )
        elif mean_exp < 0:
            assessment = (
                f"系统渐近稳定: 平均 λ={mean_exp:.4f} < 0, "
                f"但未达超稳定标准 (连续 {max_consecutive} 天, 占比 {stable_ratio:.1%})"
            )
        elif mean_exp < 0.1:
            assessment = f"系统临界稳定: 平均 λ={mean_exp:.4f} ≈ 0, 需观察"
        else:
            assessment = f"系统失稳: 平均 λ={mean_exp:.4f} > 0, 偏差在发散"

        return LyapunovSummary(
            states=list(self._history),
            mean_exponent=mean_exp,
            max_exponent=max_exp,
            stable_ratio=stable_ratio,
            is_system_stable=is_stable,
            assessment=assessment,
        )

    def save_report(self, path: Path) -> None:
        """保存 Lyapunov 报告到 JSON."""
        summary = self.assess_stability()
        data = {
            "summary": summary.to_dict(),
            "states": [
                {
                    "timestamp": s.timestamp,
                    "lyapunov_value": s.lyapunov_value,
                    "lyapunov_exponent": s.lyapunov_exponent,
                    "is_stable": s.is_stable,
                    "stability_label": s.stability_label,
                    "deviation_components": s.deviation_components,
                }
                for s in summary.states
            ],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("Lyapunov 报告已保存: %s", path)


# ============================================================
# 2. 反馈延迟相位分析
# ============================================================


@dataclass
class FeedbackDelayMeasurement:
    """单次反馈延迟测量."""

    timestamp: str
    detection_delay_hours: float
    retrain_delay_hours: float
    validation_delay_hours: float
    total_delay_hours: float
    retrain_period_hours: float
    phase_margin: float
    oscillation_risk: bool

    @property
    def total_delay_days(self) -> float:
        return self.total_delay_hours / 24.0


@dataclass
class PhaseAnalysisSummary:
    """相位分析汇总报告."""

    measurements: list[FeedbackDelayMeasurement] = field(default_factory=list)
    mean_total_delay: float = 0.0
    mean_phase_margin: float = 0.0
    oscillation_risk_ratio: float = 0.0
    is_oscillation_safe: bool = False
    assessment: str = ""

    def to_dict(self) -> dict:
        return {
            "mean_total_delay_hours": self.mean_total_delay,
            "mean_phase_margin_rad": self.mean_phase_margin,
            "mean_phase_margin_deg": math.degrees(self.mean_phase_margin),
            "oscillation_risk_ratio": self.oscillation_risk_ratio,
            "is_oscillation_safe": self.is_oscillation_safe,
            "assessment": self.assessment,
            "measurement_count": len(self.measurements),
        }


class FeedbackPhaseAnalyzer:
    """反馈延迟相位分析器 — §八.3 方向 2.

    控制论中, 反馈延迟与系统频率的关系决定稳定性:
        - 角频率 ω = 2π / T (T = 重训周期)
        - 相位裕度 = π - 延迟 × ω
        - 相位裕度 > 0 → 反馈收敛
        - 相位裕度 < 0 → 振荡风险 (反馈相位差超过 180°)

    延迟组成:
        1. 检测延迟: DriftMonitor 从漂移发生到检测到的时间
        2. 重训延迟: AutoRetrainScheduler 从触发到训练完成的时间
        3. 验证延迟: FeedbackLoop 从回测开始到出结果的时间
    """

    def __init__(
        self,
        retrain_period_hours: float = 720.0,
        safety_margin: float = 0.2,
    ) -> None:
        """初始化相位分析器.

        Args:
            retrain_period_hours: 重训周期 (小时, 默认 720 = 30 天)
            safety_margin: 安全裕度 (相位裕度需 > 此值才算安全)
        """
        self.retrain_period_hours = retrain_period_hours
        self.safety_margin = safety_margin
        self._measurements: list[FeedbackDelayMeasurement] = []

    def compute_phase_margin(
        self,
        total_delay_hours: float,
        retrain_period_hours: Optional[float] = None,
    ) -> float:
        """计算相位裕度.

        Args:
            total_delay_hours: 总延迟 (小时)
            retrain_period_hours: 重训周期 (小时)

        Returns:
            相位裕度 (弧度, > 0 安全, < 0 振荡风险)
        """
        period = retrain_period_hours or self.retrain_period_hours
        if period <= 0:
            return math.pi

        angular_freq = 2.0 * math.pi / period
        phase_margin = math.pi - total_delay_hours * angular_freq
        return phase_margin

    def measure(
        self,
        timestamp: str,
        detection_delay_hours: float = 1.0,
        retrain_delay_hours: float = 4.0,
        validation_delay_hours: float = 2.0,
        retrain_period_hours: Optional[float] = None,
    ) -> FeedbackDelayMeasurement:
        """测量一次反馈延迟.

        Args:
            timestamp: 时间戳
            detection_delay_hours: 检测延迟 (默认 1 小时)
            retrain_delay_hours: 重训延迟 (默认 4 小时)
            validation_delay_hours: 验证延迟 (默认 2 小时)
            retrain_period_hours: 重训周期 (None 用默认)

        Returns:
            FeedbackDelayMeasurement
        """
        total_delay = (
            detection_delay_hours + retrain_delay_hours + validation_delay_hours
        )
        period = retrain_period_hours or self.retrain_period_hours
        phase_margin = self.compute_phase_margin(total_delay, period)
        oscillation_risk = phase_margin < 0

        measurement = FeedbackDelayMeasurement(
            timestamp=timestamp,
            detection_delay_hours=detection_delay_hours,
            retrain_delay_hours=retrain_delay_hours,
            validation_delay_hours=validation_delay_hours,
            total_delay_hours=total_delay,
            retrain_period_hours=period,
            phase_margin=phase_margin,
            oscillation_risk=oscillation_risk,
        )

        self._measurements.append(measurement)
        if oscillation_risk:
            logger.warning(
                "反馈振荡风险: 总延迟 %.1f 小时, 相位裕度 %.1f° (< 0)",
                total_delay,
                math.degrees(phase_margin),
            )
        return measurement

    def assess_phase(self) -> PhaseAnalysisSummary:
        """评估反馈相位整体状况.

        Returns:
            PhaseAnalysisSummary
        """
        if not self._measurements:
            return PhaseAnalysisSummary(assessment="无测量数据")

        total_delays = [m.total_delay_hours for m in self._measurements]
        phase_margins = [m.phase_margin for m in self._measurements]
        risk_count = sum(1 for m in self._measurements if m.oscillation_risk)

        mean_delay = float(np.mean(total_delays))
        mean_margin = float(np.mean(phase_margins))
        risk_ratio = risk_count / len(self._measurements)

        is_safe = mean_margin > self.safety_margin and risk_ratio < 0.1

        if is_safe:
            assessment = (
                f"反馈相位安全: 平均相位裕度 {math.degrees(mean_margin):.1f}° "
                f"> {math.degrees(self.safety_margin):.1f}°, "
                f"振荡风险占比 {risk_ratio:.1%} < 10%"
            )
        elif mean_margin > 0:
            assessment = (
                f"反馈相位临界: 平均相位裕度 {math.degrees(mean_margin):.1f}° > 0 "
                f"但未达安全裕度, 振荡风险占比 {risk_ratio:.1%}"
            )
        else:
            assessment = (
                f"反馈相位危险: 平均相位裕度 {math.degrees(mean_margin):.1f}° < 0, "
                f"振荡风险占比 {risk_ratio:.1%}, 需减少延迟或增大重训周期"
            )

        return PhaseAnalysisSummary(
            measurements=list(self._measurements),
            mean_total_delay=mean_delay,
            mean_phase_margin=mean_margin,
            oscillation_risk_ratio=risk_ratio,
            is_oscillation_safe=is_safe,
            assessment=assessment,
        )

    def save_report(self, path: Path) -> None:
        """保存相位分析报告到 JSON."""
        summary = self.assess_phase()
        data = {
            "summary": summary.to_dict(),
            "measurements": [
                {
                    "timestamp": m.timestamp,
                    "detection_delay_hours": m.detection_delay_hours,
                    "retrain_delay_hours": m.retrain_delay_hours,
                    "validation_delay_hours": m.validation_delay_hours,
                    "total_delay_hours": m.total_delay_hours,
                    "phase_margin_deg": math.degrees(m.phase_margin),
                    "oscillation_risk": m.oscillation_risk,
                }
                for m in summary.measurements
            ],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("相位分析报告已保存: %s", path)


# ============================================================
# 3. 变异速率与选择压力平衡
# ============================================================


@dataclass
class VariationSelectionState:
    """单个时间点的变异-选择状态."""

    timestamp: str
    variation_rate: float
    selection_pressure: float
    pass_rate: float
    balance_index: float
    balance_status: str


@dataclass
class BalanceSummary:
    """变异-选择平衡汇总报告."""

    states: list[VariationSelectionState] = field(default_factory=list)
    mean_balance_index: float = 0.0
    balance_status_distribution: dict[str, float] = field(default_factory=dict)
    is_balanced: bool = False
    assessment: str = ""

    def to_dict(self) -> dict:
        return {
            "mean_balance_index": self.mean_balance_index,
            "balance_status_distribution": self.balance_status_distribution,
            "is_balanced": self.is_balanced,
            "assessment": self.assessment,
            "state_count": len(self.states),
        }


class VariationSelectionBalancer:
    """变异速率与选择压力平衡器 — §八.3 方向 3.

    进化理论中, 变异速率 (mutation rate) 与选择压力 (selection pressure)
    需要平衡:
        - 变异 >> 选择 → 因子库膨胀 (生成多但大多无用)
        - 选择 >> 变异 → 因子库枯竭 (门禁太严无新因子存活)

    平衡指数:
        B = variation_rate × pass_rate / (variation_rate + selection_pressure)

    健康范围:
        B ∈ [0.1, 0.5] → 健康
        B < 0.1 → 枯竭 (需放松门禁或增加生成)
        B > 0.5 → 膨胀 (需收紧门禁或减少生成)
    """

    BLOAT_THRESHOLD = 0.5
    DEPLETION_THRESHOLD = 0.1

    def __init__(
        self,
        healthy_range: tuple[float, float] = (0.1, 0.5),
    ) -> None:
        """初始化平衡器.

        Args:
            healthy_range: 平衡指数健康范围 (默认 (0.1, 0.5))
        """
        self.healthy_low, self.healthy_high = healthy_range
        self._history: list[VariationSelectionState] = []

    def compute_balance_index(
        self,
        variation_rate: float,
        pass_rate: float,
        selection_pressure: float,
    ) -> float:
        """计算平衡指数.

        Args:
            variation_rate: 变异速率 (因子生成数/天)
            pass_rate: 门禁通过率 (0-1)
            selection_pressure: 选择压力 (1 - pass_rate, 或自定义)

        Returns:
            平衡指数 B
        """
        if variation_rate + selection_pressure <= 0:
            return 0.0
        return (variation_rate * pass_rate) / (variation_rate + selection_pressure)

    def assess_balance(self, balance_index: float) -> str:
        """评估平衡状态.

        Args:
            balance_index: 平衡指数

        Returns:
            状态字符串 ("健康"/"枯竭"/"膨胀")
        """
        if balance_index < self.DEPLETION_THRESHOLD:
            return "枯竭"
        if balance_index > self.BLOAT_THRESHOLD:
            return "膨胀"
        return "健康"

    def update(
        self,
        timestamp: str,
        factors_generated: int,
        factors_passed: int,
        time_window_days: float = 1.0,
        custom_selection_pressure: Optional[float] = None,
    ) -> VariationSelectionState:
        """更新变异-选择状态.

        Args:
            timestamp: 时间戳
            factors_generated: 时间窗口内生成的因子数
            factors_passed: 时间窗口内通过门禁的因子数
            time_window_days: 时间窗口 (天)
            custom_selection_pressure: 自定义选择压力 (None 则用 1 - pass_rate)

        Returns:
            VariationSelectionState
        """
        if time_window_days <= 0:
            time_window_days = 1.0

        variation_rate = factors_generated / time_window_days
        pass_rate = factors_passed / factors_generated if factors_generated > 0 else 0.0
        selection_pressure = (
            custom_selection_pressure
            if custom_selection_pressure is not None
            else (1.0 - pass_rate)
        )

        balance_index = self.compute_balance_index(
            variation_rate, pass_rate, selection_pressure
        )
        status = self.assess_balance(balance_index)

        state = VariationSelectionState(
            timestamp=timestamp,
            variation_rate=variation_rate,
            selection_pressure=selection_pressure,
            pass_rate=pass_rate,
            balance_index=balance_index,
            balance_status=status,
        )

        self._history.append(state)
        if status != "健康":
            logger.warning(
                "变异-选择%s: B=%.4f (变异=%.2f/天, 通过率=%.1f%%, 选择压力=%.2f)",
                status,
                balance_index,
                variation_rate,
                pass_rate * 100,
                selection_pressure,
            )
        return state

    def assess_overall(self) -> BalanceSummary:
        """评估整体平衡状况.

        Returns:
            BalanceSummary
        """
        if not self._history:
            return BalanceSummary(assessment="无历史数据")

        indices = [s.balance_index for s in self._history]
        mean_b = float(np.mean(indices))

        status_counts: dict[str, int] = {"健康": 0, "枯竭": 0, "膨胀": 0}
        for s in self._history:
            status_counts[s.balance_status] = status_counts.get(s.balance_status, 0) + 1

        total = len(self._history)
        status_dist = {k: v / total for k, v in status_counts.items()}

        healthy_ratio = status_dist.get("健康", 0.0)
        is_balanced = healthy_ratio >= 0.7

        if is_balanced:
            assessment = (
                f"变异-选择平衡: 平均 B={mean_b:.4f}, "
                f"健康占比 {healthy_ratio:.1%} ≥ 70%"
            )
        else:
            depletion_ratio = status_dist.get("枯竭", 0.0)
            bloat_ratio = status_dist.get("膨胀", 0.0)
            if depletion_ratio > bloat_ratio:
                assessment = (
                    f"因子库枯竭风险: 平均 B={mean_b:.4f} < {self.DEPLETION_THRESHOLD}, "
                    f"枯竭占比 {depletion_ratio:.1%}, 建议放松门禁或增加生成"
                )
            else:
                assessment = (
                    f"因子库膨胀风险: 平均 B={mean_b:.4f} > {self.BLOAT_THRESHOLD}, "
                    f"膨胀占比 {bloat_ratio:.1%}, 建议收紧门禁或减少生成"
                )

        return BalanceSummary(
            states=list(self._history),
            mean_balance_index=mean_b,
            balance_status_distribution=status_dist,
            is_balanced=is_balanced,
            assessment=assessment,
        )

    def save_report(self, path: Path) -> None:
        """保存平衡分析报告到 JSON."""
        summary = self.assess_overall()
        data = {
            "summary": summary.to_dict(),
            "states": [
                {
                    "timestamp": s.timestamp,
                    "variation_rate": s.variation_rate,
                    "selection_pressure": s.selection_pressure,
                    "pass_rate": s.pass_rate,
                    "balance_index": s.balance_index,
                    "balance_status": s.balance_status,
                }
                for s in summary.states
            ],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("变异-选择平衡报告已保存: %s", path)
