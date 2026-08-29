"""theoretical_metrics 单元测试 — §八.3 P0 改进验证 (2026-08-19).

测试覆盖:
    1. LyapunovStabilityMeter — 稳定/失稳/临界场景
    2. FeedbackPhaseAnalyzer — 安全/振荡风险场景
    3. VariationSelectionBalancer — 健康/枯竭/膨胀场景
"""

from __future__ import annotations

import math

from utils.alpha.theoretical_metrics import (
    FeedbackPhaseAnalyzer,
    LyapunovStabilityMeter,
    LyapunovState,
    VariationSelectionBalancer,
)

# ============================================================
# LyapunovStabilityMeter 测试
# ============================================================


class TestLyapunovStabilityMeter:
    """Lyapunov 稳定性度量器测试."""

    def test_converging_system_is_stable(self):
        """收敛系统 (偏差递减) 应判定为稳定."""
        meter = LyapunovStabilityMeter(
            ic_target=0.05,
            return_target=0.0,
            stability_threshold=-0.05,
            min_consecutive_days=3,
        )

        deviations = [0.10, 0.08, 0.06, 0.04, 0.03, 0.02, 0.01]
        for i, dev in enumerate(deviations):
            meter.update(
                timestamp=f"2026-08-{i+1:02d}",
                ic=0.05 + dev,
                daily_return=0.0,
                drift_score=dev * 0.5,
            )

        summary = meter.assess_stability()
        assert (
            summary.mean_exponent < 0
        ), f"收敛系统 λ 应 < 0, 实际 {summary.mean_exponent}"
        assert summary.is_system_stable, f"收敛系统应稳定: {summary.assessment}"

    def test_diverging_system_is_unstable(self):
        """发散系统 (偏差递增) 应判定为失稳."""
        meter = LyapunovStabilityMeter(
            ic_target=0.05,
            stability_threshold=-0.05,
            min_consecutive_days=3,
        )

        deviations = [0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64]
        for i, dev in enumerate(deviations):
            meter.update(
                timestamp=f"2026-08-{i+1:02d}",
                ic=0.05 + dev,
                daily_return=dev,
                drift_score=dev,
            )

        summary = meter.assess_stability()
        assert (
            summary.mean_exponent > 0
        ), f"发散系统 λ 应 > 0, 实际 {summary.mean_exponent}"
        assert not summary.is_system_stable

    def test_constant_system_is_marginal(self):
        """恒定偏差系统应判定为临界稳定 (λ ≈ 0)."""
        meter = LyapunovStabilityMeter(
            ic_target=0.05,
            stability_threshold=-0.05,
        )

        for i in range(10):
            meter.update(
                timestamp=f"2026-08-{i+1:02d}",
                ic=0.08,
                daily_return=0.001,
                drift_score=0.05,
            )

        summary = meter.assess_stability()
        assert (
            abs(summary.mean_exponent) < 0.5
        ), f"恒定系统 λ 应 ≈ 0, 实际 {summary.mean_exponent}"

    def test_lyapunov_value_non_negative(self):
        """Lyapunov 值应非负."""
        meter = LyapunovStabilityMeter()
        v = meter.compute_lyapunov_value(ic=0.03, daily_return=0.0, drift_score=0.0)
        assert v >= 0

    def test_lyapunov_value_zero_at_target(self):
        """在目标轨道上 V 应接近 0."""
        meter = LyapunovStabilityMeter(ic_target=0.05, return_target=0.01)
        v = meter.compute_lyapunov_value(ic=0.05, daily_return=0.01, drift_score=0.0)
        assert v < 1e-8

    def test_stability_label(self):
        """稳定性标签应正确."""
        state = LyapunovState(
            timestamp="2026-08-19",
            lyapunov_value=0.01,
            lyapunov_exponent=-0.5,
            is_stable=True,
        )
        assert state.stability_label == "渐近稳定"

        state2 = LyapunovState(
            timestamp="2026-08-19",
            lyapunov_value=0.01,
            lyapunov_exponent=0.5,
            is_stable=False,
        )
        assert state2.stability_label == "失稳"

    def test_save_report(self, tmp_path):
        """报告保存应成功."""
        meter = LyapunovStabilityMeter()
        for i in range(5):
            meter.update(
                timestamp=f"2026-08-{i+1:02d}",
                ic=0.03 + 0.01 * (0.8**i),
                daily_return=0.0,
            )
        path = tmp_path / "lyapunov_report.json"
        meter.save_report(path)
        assert path.exists()
        assert path.stat().st_size > 0

    def test_insufficient_samples(self):
        """样本不足时应返回相应评估."""
        meter = LyapunovStabilityMeter()
        meter.update(timestamp="2026-08-19", ic=0.03, daily_return=0.0)
        summary = meter.assess_stability()
        assert "样本不足" in summary.assessment


# ============================================================
# FeedbackPhaseAnalyzer 测试
# ============================================================


class TestFeedbackPhaseAnalyzer:
    """反馈延迟相位分析器测试."""

    def test_short_delay_is_safe(self):
        """短延迟应判定为安全."""
        analyzer = FeedbackPhaseAnalyzer(
            retrain_period_hours=720.0,
            safety_margin=0.2,
        )

        measurement = analyzer.measure(
            timestamp="2026-08-19",
            detection_delay_hours=1.0,
            retrain_delay_hours=4.0,
            validation_delay_hours=2.0,
        )

        assert measurement.total_delay_hours == 7.0
        assert measurement.phase_margin > 0, "短延迟相位裕度应 > 0"
        assert not measurement.oscillation_risk

        summary = analyzer.assess_phase()
        assert summary.is_oscillation_safe

    def test_long_delay_oscillation_risk(self):
        """长延迟应判定为振荡风险."""
        analyzer = FeedbackPhaseAnalyzer(
            retrain_period_hours=48.0,
            safety_margin=0.2,
        )

        measurement = analyzer.measure(
            timestamp="2026-08-19",
            detection_delay_hours=12.0,
            retrain_delay_hours=24.0,
            validation_delay_hours=12.0,
        )

        assert measurement.total_delay_hours == 48.0
        assert measurement.phase_margin < 0, "长延迟相位裕度应 < 0"
        assert measurement.oscillation_risk

    def test_phase_margin_computation(self):
        """相位裕度计算应正确."""
        analyzer = FeedbackPhaseAnalyzer(retrain_period_hours=360.0)
        margin = analyzer.compute_phase_margin(total_delay_hours=10.0)
        expected = math.pi - 10.0 * (2.0 * math.pi / 360.0)
        assert abs(margin - expected) < 1e-10

    def test_zero_delay_max_margin(self):
        """零延迟应有最大相位裕度 (π)."""
        analyzer = FeedbackPhaseAnalyzer()
        margin = analyzer.compute_phase_margin(total_delay_hours=0.0)
        assert abs(margin - math.pi) < 1e-10

    def test_save_report(self, tmp_path):
        """报告保存应成功."""
        analyzer = FeedbackPhaseAnalyzer()
        analyzer.measure(timestamp="2026-08-19")
        analyzer.measure(timestamp="2026-08-20")
        path = tmp_path / "phase_report.json"
        analyzer.save_report(path)
        assert path.exists()

    def test_empty_measurements(self):
        """无测量数据时应返回相应评估."""
        analyzer = FeedbackPhaseAnalyzer()
        summary = analyzer.assess_phase()
        assert "无测量数据" in summary.assessment


# ============================================================
# VariationSelectionBalancer 测试
# ============================================================


class TestVariationSelectionBalancer:
    """变异-选择平衡器测试."""

    def test_healthy_balance(self):
        """健康平衡场景."""
        balancer = VariationSelectionBalancer()

        state = balancer.update(
            timestamp="2026-08-19",
            factors_generated=10,
            factors_passed=3,
            time_window_days=1.0,
        )

        assert state.variation_rate == 10.0
        assert state.pass_rate == 0.3
        assert state.balance_index > 0
        assert state.balance_status in ("健康", "枯竭", "膨胀")

    def test_depletion_risk(self):
        """枯竭风险 (门禁太严, 通过率极低)."""
        balancer = VariationSelectionBalancer()

        for i in range(10):
            balancer.update(
                timestamp=f"2026-08-{i+1:02d}",
                factors_generated=5,
                factors_passed=0,
                time_window_days=1.0,
            )

        summary = balancer.assess_overall()
        assert not summary.is_balanced
        assert "枯竭" in summary.assessment

    def test_bloat_risk(self):
        """膨胀风险 (门禁太松, 通过率极高)."""
        balancer = VariationSelectionBalancer()

        for i in range(10):
            balancer.update(
                timestamp=f"2026-08-{i+1:02d}",
                factors_generated=100,
                factors_passed=95,
                time_window_days=1.0,
            )

        summary = balancer.assess_overall()
        assert "膨胀" in summary.assessment or not summary.is_balanced

    def test_balance_index_computation(self):
        """平衡指数计算应正确."""
        balancer = VariationSelectionBalancer()
        b = balancer.compute_balance_index(
            variation_rate=10.0,
            pass_rate=0.3,
            selection_pressure=0.7,
        )
        expected = (10.0 * 0.3) / (10.0 + 0.7)
        assert abs(b - expected) < 1e-10

    def test_zero_variation(self):
        """零变异速率应返回平衡指数 0."""
        balancer = VariationSelectionBalancer()
        b = balancer.compute_balance_index(
            variation_rate=0.0,
            pass_rate=0.0,
            selection_pressure=1.0,
        )
        assert b == 0.0

    def test_assess_balance_labels(self):
        """平衡状态标签应正确."""
        balancer = VariationSelectionBalancer()
        assert balancer.assess_balance(0.05) == "枯竭"
        assert balancer.assess_balance(0.3) == "健康"
        assert balancer.assess_balance(0.6) == "膨胀"

    def test_save_report(self, tmp_path):
        """报告保存应成功."""
        balancer = VariationSelectionBalancer()
        balancer.update(
            timestamp="2026-08-19",
            factors_generated=10,
            factors_passed=3,
        )
        path = tmp_path / "balance_report.json"
        balancer.save_report(path)
        assert path.exists()

    def test_empty_history(self):
        """无历史数据时应返回相应评估."""
        balancer = VariationSelectionBalancer()
        summary = balancer.assess_overall()
        assert "无历史数据" in summary.assessment

    def test_custom_selection_pressure(self):
        """自定义选择压力应生效."""
        balancer = VariationSelectionBalancer()
        state = balancer.update(
            timestamp="2026-08-19",
            factors_generated=10,
            factors_passed=5,
            custom_selection_pressure=0.9,
        )
        assert state.selection_pressure == 0.9
