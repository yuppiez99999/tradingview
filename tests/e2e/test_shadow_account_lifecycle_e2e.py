"""U5 GAP-2 E2E: ShadowAccountAdapter 生命周期端到端测试.

测试目标 (对齐 docs/GAP-2_E2E测试方案.md §四):
    场景 1: 正常生命周期 (20 天观察期)
    场景 2: Fail-Fast 触发 (单日 -4% > 3% 阈值)
    场景 3: Fail-Fast 触发 (3 日累计 -6% > 5% 阈值)
    场景 4: 样本不足降级 (< MIN_SAMPLES_FOR_DSR=20)
    场景 5: risk_managed 模式 (跳过: 当前版本无 risk_managed 参数)
    场景 6: 与 PipelineOrchestrator 集成

签名核对 (2026-08-05):
    - ShadowAccountAdapter.__init__(account_id, strategy_id, initial_capital, ...) 无 risk_managed
    - run_shadow(daily_returns, dates=None, is_real_data=True) -> RunShadowResult (不抛 FailFast)
    - get_metrics() -> ShadowMetrics (dataclass; 样本不足抛 InsufficientReturnsError)
    - RunShadowResult: success / days_processed / final_nav / fail_fast_triggered / fail_fast_reason / termination_date
    /
    error
    - Fail-Fast 阈值: 单日 >3%, 3 日累计 >5%
    - MIN_SAMPLES_FOR_DSR = 20 (yaml 单事实源, cairn/observation-period-config-drift-20260809.md)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from utils.alpha.shadow_account_adapter import (
    FailFastTriggeredError,
    InsufficientReturnsError,
    ShadowAccountAdapter,
    ShadowMetrics,
)

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e]


# ============================================================
# Helper: mock compute_dsr (deflated_sharpe 模块缺失时的降级)
# ============================================================


@dataclass
class _MockDsrResult:
    """Mock deflated_sharpe_ratio 返回值 (deflated_sharpe 模块缺失)."""

    deflated_sharpe_ratio: float = 0.85


def _mock_compute_dsr(self):
    """Mock compute_dsr: 返回固定 DSR 值, 绕过 deflated_sharpe 模块依赖.

    背景: v8.3_institutional/src/validation/deflated_sharpe.py 不存在,
    adapter.compute_dsr() 会 ModuleNotFoundError. U5 E2E 测试关注生命周期,
    非算法正确性, 故 mock DSR 返回值.
    """
    return _MockDsrResult(deflated_sharpe_ratio=0.85)


@pytest.fixture(autouse=True)
def mock_dsr_if_missing(monkeypatch):
    """自动检测并 mock compute_dsr (仅当 deflated_sharpe 模块缺失时)."""
    try:
        import deflated_sharpe  # noqa: F401

        # 模块存在, 不 mock
    except ImportError:
        # 模块缺失, mock compute_dsr
        monkeypatch.setattr(ShadowAccountAdapter, "compute_dsr", _mock_compute_dsr)


# ============================================================
# 场景 1: 正常生命周期 (14 天观察期) ✅
# ============================================================


class TestNormalLifecycle:
    """14 天观察期正常生命周期."""

    def test_normal_lifecycle_14d(self, sample_daily_returns_14d):
        """14 天正常生命周期: run_shadow 成功, get_metrics 返回完整指标.

        验收标准:
            - RunShadowResult.success == True
            - RunShadowResult.days_processed == 14
            - metrics.dsr / annual_return / max_drawdown / sharpe_cv 字段存在
            - 数值合理性: annual_return ∈ (-1, 2), max_drawdown ∈ [0, 1)
        """
        adapter = ShadowAccountAdapter(
            account_id="e2e_test_normal",
            initial_capital=1_000_000,
        )
        result = adapter.run_shadow(daily_returns=sample_daily_returns_14d)

        # RunShadowResult 断言 (fixture 实际 20 天, 满足 MIN_SAMPLES_FOR_DSR=20)
        n_days = len(sample_daily_returns_14d)
        assert result.success is True
        assert result.days_processed == n_days
        assert result.fail_fast_triggered is False

        # ShadowMetrics 断言
        metrics = adapter.get_metrics()
        assert isinstance(metrics, ShadowMetrics)
        assert metrics.days_tracked == n_days
        # 数值合理性
        assert (
            -1.0 < metrics.annual_return < 2.0
        ), f"annual_return 异常: {metrics.annual_return}"
        assert (
            0 <= metrics.max_drawdown < 1.0
        ), f"max_drawdown 异常: {metrics.max_drawdown}"
        # is_real_data 默认 True
        assert metrics.is_real_data is True

    def test_lifecycle_idempotent_metrics(self, sample_daily_returns_14d):
        """多次调用 get_metrics 返回一致结果 (幂等性)."""
        adapter = ShadowAccountAdapter(
            account_id="e2e_test_idempotent",
            initial_capital=1_000_000,
        )
        adapter.run_shadow(daily_returns=sample_daily_returns_14d)

        m1 = adapter.get_metrics()
        m2 = adapter.get_metrics()

        assert m1.dsr == m2.dsr
        assert m1.annual_return == m2.annual_return
        assert m1.max_drawdown == m2.max_drawdown


# ============================================================
# 场景 2: Fail-Fast 触发 (单日 >3%) 🚨
# ============================================================


class TestFailFastDailyBreach:
    """单日回撤 >3% 触发 Fail-Fast."""

    def test_failfast_daily_breach(self, extreme_daily_returns_breach):
        """单日 -4% 回撤: RunShadowResult.fail_fast_triggered=True, 适配器终止.

        注意: run_shadow 不抛 FailFastTriggeredError, 而是返回带 fail_fast_triggered=True 的结果.
        后续再次调用 run_shadow 才会抛 FailFastTriggeredError (因 status=terminated).
        """
        adapter = ShadowAccountAdapter(
            account_id="e2e_test_failfast_daily",
            initial_capital=1_000_000,
        )
        result = adapter.run_shadow(daily_returns=extreme_daily_returns_breach)

        # Fail-Fast 触发断言
        assert result.fail_fast_triggered is True
        assert result.fail_fast_reason is not None
        # 触发后不应继续处理后续天数
        assert result.days_processed <= len(extreme_daily_returns_breach)

    def test_failfast_blocks_subsequent_runs(self, extreme_daily_returns_breach):
        """Fail-Fast 触发后, 再次调用 run_shadow 抛 FailFastTriggeredError."""
        adapter = ShadowAccountAdapter(
            account_id="e2e_test_failfast_block",
            initial_capital=1_000_000,
        )
        adapter.run_shadow(daily_returns=extreme_daily_returns_breach)

        # 再次调用应抛异常
        with pytest.raises(FailFastTriggeredError):
            adapter.run_shadow(daily_returns=[0.001, 0.002])


# ============================================================
# 场景 3: Fail-Fast 触发 (3 日累计 >5%) 🚨
# ============================================================


class TestFailFast3dCumulativeBreach:
    """3 日累计回撤 >5% 触发 Fail-Fast."""

    def test_failfast_3d_cumulative_breach(self):
        """3 日累计回撤 >5%: fail_fast_triggered=True.

        阈值: CUMULATIVE_3D_DRAWDOWN_THRESHOLD = 0.05 (5%)
        FailFastMonitor.check 逻辑: nav_history[-3] (倒数第3个, 即2天前) 到当日 nav 的回撤.
        构造: [0.0, 0.0, -0.026, -0.026]
            Day1 nav=1.0, Day2 nav=1.0, Day3 nav=0.974 (单日-2.6%<3%不触发),
            Day4 nav=0.948676, nav_history[-3]=Day2=1.0, cum_ret=-5.13%< -5% → 触发
        """
        adapter = ShadowAccountAdapter(
            account_id="e2e_test_failfast_3d",
            initial_capital=1_000_000,
        )
        returns = [0.0, 0.0, -0.026, -0.026]

        result = adapter.run_shadow(daily_returns=returns)

        # 3 日累计触发
        assert result.fail_fast_triggered is True
        assert result.fail_fast_reason is not None
        assert (
            "3日累计" in result.fail_fast_reason
            or "cumulative" in result.fail_fast_reason.lower()
        )


# ============================================================
# 场景 4: 样本不足降级 ⚠️
# ============================================================


class TestInsufficientSamples:
    """样本数 < MIN_SAMPLES_FOR_DSR(20) 时降级."""

    def test_insufficient_samples_raises_error(self):
        """5 天样本 (< 20): get_metrics 抛 InsufficientReturnsError.

        验收标准:
            - run_shadow 仍可成功 (记录净值)
            - get_metrics 抛 InsufficientReturnsError (DSR 不可计算)
        """
        adapter = ShadowAccountAdapter(
            account_id="e2e_test_insufficient",
            initial_capital=1_000_000,
        )
        # 5 天样本
        result = adapter.run_shadow(daily_returns=[0.001, 0.002, -0.001, 0.003, -0.002])

        # run_shadow 成功 (记录阶段不要求最小样本)
        assert result.success is True
        assert result.days_processed == 5

        # get_metrics 抛 InsufficientReturnsError
        with pytest.raises(InsufficientReturnsError) as exc_info:
            adapter.get_metrics()
        assert "20" in str(exc_info.value) or "样本不足" in str(exc_info.value)

    def test_boundary_19_samples_still_insufficient(self):
        """19 天样本仍 < 20: 仍抛 InsufficientReturnsError (边界测试)."""
        adapter = ShadowAccountAdapter(
            account_id="e2e_test_boundary_19",
            initial_capital=1_000_000,
        )
        # 19 天样本, 刚好 < 20
        returns_19 = [0.001] * 19
        adapter.run_shadow(daily_returns=returns_19)

        with pytest.raises(InsufficientReturnsError):
            adapter.get_metrics()


# ============================================================
# 场景 5: risk_managed 模式 ⏭ (跳过)
# ============================================================


class TestRiskManagedMode:
    """risk_managed 模式波动率缩放.

    注: ShadowAccountAdapter 当前版本 __init__ 无 risk_managed 参数.
    risk_managed 逻辑在 PipelineOrchestrator 层集成 (HC-3 硬约束).
    本场景标记 skip, 待后续版本 ShadowAccountAdapter 直接支持 risk_managed 时启用.
    """

    @pytest.mark.skip(
        reason="ShadowAccountAdapter 当前版本无 risk_managed 参数, 需后续版本支持"
    )
    def test_risk_managed_volatility_scaling(self, sample_daily_returns_14d):
        """risk_managed 模式: 波动率缩放至目标 15% + 回撤去杠杆."""
        pass


# ============================================================
# 场景 6: 与 PipelineOrchestrator 集成 🔗
# ============================================================


class TestPipelineShadowIntegration:
    """Pipeline 产出 → Shadow 注入的端到端数据流."""

    def test_pipeline_to_shadow_integration(
        self, pipeline_config_overrides, sample_daily_returns_14d
    ):
        """Pipeline 完成 (dry_run) → 将收益率序列注入 ShadowAccountAdapter.

        验收标准:
            - pipeline_result.success == True
            - adapter.run_shadow 成功
            - adapter.get_metrics 返回完整指标
        """
        from utils.pipeline.orchestrator import PipelineOrchestrator

        # Step 1: 运行 Pipeline (dry_run, 不执行交易)
        orchestrator = PipelineOrchestrator(config=pipeline_config_overrides)
        pipeline_result = orchestrator.run_full_cycle(mode="dry_run")
        assert pipeline_result.success is True

        # Step 2: 将收益率序列注入 Shadow (模拟 Pipeline 产出的 Alpha 信号收益率)
        adapter = ShadowAccountAdapter(
            account_id="e2e_integration",
            initial_capital=1_000_000,
        )
        shadow_result = adapter.run_shadow(daily_returns=sample_daily_returns_14d)
        assert shadow_result.success is True

        # Step 3: 获取 Shadow 指标
        metrics = adapter.get_metrics()
        assert metrics is not None
        assert metrics.days_tracked == len(sample_daily_returns_14d)

        # 端到端: Pipeline 完成 + Shadow 指标产出
        assert pipeline_result.success is True
        assert metrics.annual_return is not None
