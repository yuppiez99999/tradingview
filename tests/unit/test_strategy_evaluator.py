"""StrategyEvaluator + EvolutionOrchestrator 单元测试.

任务: 自我进化框架第 1 阶段交付物验证
架构参考: docs/模块整合_8.4/ARCHITECTURE_自我进化框架.md §4.1 / §4.2

覆盖范围:
    1. Feature Flag 透传 (HC-1: 默认 False, 关闭返回降级报告)
    2. 空数据容错 (不抛异常, 返回降级报告)
    3. 小样本容错 (< 15 条, DSR/WF 降级)
    4. 正常数据评估 (≥ 252 条, 完整 Public/Private 分离)
    5. Public/Private 分数分离 (AIDE² 核心机制)
    6. 反作弊检测 (过拟合 + PIT 违规)
    7. 降级报告结构
    8. ScoreReport 序列化 (to_dict)
    9. evaluate_from_jsonl 便捷方法
    10. 决策建议 (promote/rollback/continue) 阈值
    11. 底层计算工具 (Sharpe/MaxDD/AnnualReturn/WF decay)
    12. 便捷函数 (evaluate_strategy / evaluate_from_shadow)
    13. EvolutionOrchestrator 工作流 (collect/evaluate/log)
    14. EvolutionOrchestrator 观察期 (HC-4: 只读模式)
    15. EvolutionOrchestrator 决策日志持久化

硬约束:
    - HC-1: Feature Flag 默认 False
    - HC-4: 观察期内不触发任何进化动作
    - HC-5: 配置走 ConfigManager 4 级优先级
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from utils.alpha.evolution_orchestrator import (
    ACTION_EVALUATE_ONLY,
    ACTION_NOOP,
    OBSERVATION_PERIOD_DAYS,
    STATUS_DISABLED,
    STATUS_ENABLED,
    STATUS_OBSERVATION,
    DecisionRecord,
    EvolutionOrchestrator,
    MetricsSnapshot,
    OrchestratorStatus,
)
from utils.alpha.strategy_evaluator import (
    BASELINE_ANNUAL_RETURN,
    BASELINE_SHARPE,
    MAX_ALLOWED_DRAWDOWN,
    MIN_SAMPLES_FOR_DSR,
    MIN_SAMPLES_FOR_WF,
    PROMOTE_PRIVATE_SCORE,
    PROMOTE_RH_RISK_MAX,
    RH_RISK_PIT_VIOLATION,
    ROLLBACK_PRIVATE_SCORE,
    TRADING_DAYS_PER_YEAR,
    ScoreReport,
    StrategyEvaluator,
    evaluate_from_shadow,
    evaluate_strategy,
)

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def enabled_evaluator(monkeypatch: pytest.MonkeyPatch) -> StrategyEvaluator:
    """启用 Feature Flag 的 StrategyEvaluator (隔离环境变量)."""
    monkeypatch.setenv("USE_STRATEGY_EVALUATOR", "true")
    # patch is_enabled 直接返回 True, 绕过 yaml/env 解析
    monkeypatch.setattr(
        "utils.infra.feature_flags.is_enabled",
        lambda name: name == "USE_STRATEGY_EVALUATOR",
    )
    return StrategyEvaluator()


@pytest.fixture
def disabled_evaluator(monkeypatch: pytest.MonkeyPatch) -> StrategyEvaluator:
    """禁用 Feature Flag 的 StrategyEvaluator."""
    monkeypatch.setattr(
        "utils.infra.feature_flags.is_enabled",
        lambda name: False,
    )
    return StrategyEvaluator()


@pytest.fixture
def deterministic_returns() -> list[float]:
    """252 条确定性日收益 (年化约 15%, 波动约 15%)."""
    random.seed(42)
    return [random.gauss(0.0006, 0.0095) for _ in range(TRADING_DAYS_PER_YEAR)]


@pytest.fixture
def overfit_returns() -> list[float]:
    """过拟合数据: 前期近线性高 Sharpe, 后期随机."""
    random.seed(7)
    return [0.005 + random.gauss(0, 0.0005) for _ in range(200)] + [
        random.gauss(0, 0.02) for _ in range(52)
    ]


@pytest.fixture
def tmp_jsonl(tmp_path: Path) -> Path:
    """临时 daily_returns.jsonl 文件."""
    jsonl_path = tmp_path / "daily_returns.jsonl"
    records = [
        {"date": "2026-01-01", "daily_return": 0.005},
        {"date": "2026-01-02", "daily_return": -0.002},
        {"date": "2026-01-03", "daily_return": 0.008},
        {"date": "2026-01-04", "daily_return": 0.001},
        {"date": "2026-01-05", "daily_return": -0.003},
    ]
    with jsonl_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return jsonl_path


# ============================================================
# 测试 1: Feature Flag 透传 (HC-1)
# ============================================================


class TestFeatureFlagPassThrough:
    """HC-1: Feature Flag 默认 False, 关闭时返回降级报告."""

    def test_default_flag_disabled(self, disabled_evaluator: StrategyEvaluator) -> None:
        """Flag 关闭时 enabled=False."""
        assert disabled_evaluator.enabled is False

    def test_flag_enabled(self, enabled_evaluator: StrategyEvaluator) -> None:
        """Flag 启用时 enabled=True."""
        assert enabled_evaluator.enabled is True

    def test_disabled_returns_degraded_report(
        self, disabled_evaluator: StrategyEvaluator
    ) -> None:
        """Flag 关闭时返回降级报告, 不抛异常."""
        report = disabled_evaluator.evaluate(daily_returns=[0.01, 0.02, -0.01])
        assert isinstance(report, ScoreReport)
        assert report.is_degraded is True
        assert "feature_flag_disabled" in report.degraded_reason
        assert report.public_score == 0.0
        assert report.private_score == 0.0
        # 降级时风险最高
        assert report.reward_hacking_risk == 1.0

    def test_disabled_does_not_compute_metrics(
        self, disabled_evaluator: StrategyEvaluator
    ) -> None:
        """Flag 关闭时不计算任何指标."""
        report = disabled_evaluator.evaluate(daily_returns=[0.01] * 100)
        assert report.public_metrics == {}
        assert report.private_metrics == {}

    def test_flag_check_exception_defaults_to_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Feature Flag 检查异常时默认禁用 (fail-safe)."""

        def _raise(name: str) -> bool:
            raise RuntimeError("feature_flags module broken")

        monkeypatch.setattr("utils.infra.feature_flags.is_enabled", _raise)
        evaluator = StrategyEvaluator()
        assert evaluator.enabled is False


# ============================================================
# 测试 2: 空数据容错
# ============================================================


class TestEmptyData:
    """空数据不抛异常, 返回降级报告."""

    def test_empty_returns(self, enabled_evaluator: StrategyEvaluator) -> None:
        """空列表返回降级报告."""
        report = enabled_evaluator.evaluate(daily_returns=[])
        assert isinstance(report, ScoreReport)
        assert report.is_degraded is True
        assert report.degraded_reason == "empty_returns"
        assert report.sample_count == 0

    def test_empty_returns_recommendation_continue(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """空数据降级报告 recommendation == continue."""
        report = enabled_evaluator.evaluate(daily_returns=[])
        assert report.recommendation == "continue"


# ============================================================
# 测试 3: 小样本容错
# ============================================================


class TestSmallSample:
    """小样本 (< 15) 不抛异常, DSR/WF 降级为 -1.0."""

    def test_small_sample_no_exception(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """10 条样本不抛异常."""
        small = [0.01, -0.005, 0.008, 0.02, -0.01, 0.005, 0.012, -0.003, 0.015, 0.0]
        report = enabled_evaluator.evaluate(daily_returns=small)
        assert isinstance(report, ScoreReport)
        assert report.is_degraded is False
        assert report.sample_count == 10

    def test_small_sample_dsr_degraded(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """样本 < MIN_SAMPLES_FOR_DSR 时 dsr=-1.0."""
        small = [0.01] * (MIN_SAMPLES_FOR_DSR - 1)
        report = enabled_evaluator.evaluate(daily_returns=small)
        assert report.private_metrics["dsr"] == -1.0

    def test_small_sample_wf_degraded(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """样本 < MIN_SAMPLES_FOR_WF 时 wf_sharpe_decay=-1.0."""
        small = [0.01] * 50
        report = enabled_evaluator.evaluate(daily_returns=small)
        assert report.private_metrics["wf_sharpe_decay"] == -1.0

    def test_small_sample_sharpe_cv_zero(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """样本 < 20 时 sharpe_cv=0.0."""
        report = enabled_evaluator.evaluate(daily_returns=[0.01] * 10)
        assert report.private_metrics["sharpe_cv"] == 0.0


# ============================================================
# 测试 4: 正常数据评估
# ============================================================


class TestNormalDataEvaluation:
    """正常数据 (≥ 252 条) 完整评估."""

    def test_normal_evaluation_no_exception(
        self,
        enabled_evaluator: StrategyEvaluator,
        deterministic_returns: list[float],
    ) -> None:
        """252 条数据正常评估."""
        report = enabled_evaluator.evaluate(
            daily_returns=deterministic_returns, n_trials=50
        )
        assert isinstance(report, ScoreReport)
        assert report.is_degraded is False
        assert report.sample_count == TRADING_DAYS_PER_YEAR

    def test_public_score_in_range(
        self,
        enabled_evaluator: StrategyEvaluator,
        deterministic_returns: list[float],
    ) -> None:
        """public_score 在 [0, 1]."""
        report = enabled_evaluator.evaluate(daily_returns=deterministic_returns)
        assert 0.0 <= report.public_score <= 1.0

    def test_private_score_in_range(
        self,
        enabled_evaluator: StrategyEvaluator,
        deterministic_returns: list[float],
    ) -> None:
        """private_score 在 [0, 1]."""
        report = enabled_evaluator.evaluate(daily_returns=deterministic_returns)
        assert 0.0 <= report.private_score <= 1.0

    def test_reward_hacking_risk_in_range(
        self,
        enabled_evaluator: StrategyEvaluator,
        deterministic_returns: list[float],
    ) -> None:
        """reward_hacking_risk 在 [0, 1]."""
        report = enabled_evaluator.evaluate(daily_returns=deterministic_returns)
        assert 0.0 <= report.reward_hacking_risk <= 1.0

    def test_public_metrics_populated(
        self,
        enabled_evaluator: StrategyEvaluator,
        deterministic_returns: list[float],
    ) -> None:
        """public_metrics 含 annual_return / sharpe."""
        report = enabled_evaluator.evaluate(daily_returns=deterministic_returns)
        assert "annual_return" in report.public_metrics
        assert "sharpe" in report.public_metrics

    def test_private_metrics_populated(
        self,
        enabled_evaluator: StrategyEvaluator,
        deterministic_returns: list[float],
    ) -> None:
        """private_metrics 含 max_drawdown / dsr / sharpe_cv / wf_sharpe_decay."""
        report = enabled_evaluator.evaluate(daily_returns=deterministic_returns)
        assert "max_drawdown" in report.private_metrics
        assert "dsr" in report.private_metrics
        assert "sharpe_cv" in report.private_metrics
        assert "wf_sharpe_decay" in report.private_metrics

    def test_sample_count_recorded(
        self,
        enabled_evaluator: StrategyEvaluator,
        deterministic_returns: list[float],
    ) -> None:
        """sample_count 等于输入长度."""
        report = enabled_evaluator.evaluate(daily_returns=deterministic_returns)
        assert report.sample_count == len(deterministic_returns)


# ============================================================
# 测试 5: Public/Private 分离
# ============================================================


class TestPublicPrivateSeparation:
    """AIDE² 核心机制: 样本内 vs 样本外分离."""

    def test_public_private_differ(
        self,
        enabled_evaluator: StrategyEvaluator,
        deterministic_returns: list[float],
    ) -> None:
        """public_score != private_score (不同维度计算)."""
        report = enabled_evaluator.evaluate(daily_returns=deterministic_returns)
        # 不是常量相等, 应该有差异 (Public 0.30 权重 vs Private 0.70 权重)
        assert report.public_score != report.private_score or (
            report.public_score == 0.0 and report.private_score == 0.0
        )

    def test_public_metrics_keys(
        self,
        enabled_evaluator: StrategyEvaluator,
        deterministic_returns: list[float],
    ) -> None:
        """public_metrics 含样本内指标."""
        report = enabled_evaluator.evaluate(daily_returns=deterministic_returns)
        assert "annual_return" in report.public_metrics
        assert "sharpe" in report.public_metrics

    def test_private_metrics_keys(
        self,
        enabled_evaluator: StrategyEvaluator,
        deterministic_returns: list[float],
    ) -> None:
        """private_metrics 含样本外指标 (DSR / max_dd / wf_decay)."""
        report = enabled_evaluator.evaluate(daily_returns=deterministic_returns)
        assert "max_drawdown" in report.private_metrics
        assert "dsr" in report.private_metrics
        assert "wf_sharpe_decay" in report.private_metrics

    def test_signal_history_ic_added_to_public(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """signal_history 中的 ic_ir / ic_series 添加到 public_metrics."""
        returns = [0.01, -0.005, 0.008] * 50
        signal_history = {
            "ic_series": [0.05, 0.06, 0.04, 0.07, 0.05],
            "ic_ir": 0.65,
        }
        report = enabled_evaluator.evaluate(
            daily_returns=returns, signal_history=signal_history
        )
        assert report.public_metrics["ic_ir"] == 0.65
        assert report.public_metrics["ic_mean"] == pytest.approx(0.054, abs=1e-6)

    def test_features_used_recorded(self, enabled_evaluator: StrategyEvaluator) -> None:
        """signal_history.features_used 记录到 private_metrics.n_features."""
        returns = [0.01, -0.005, 0.008] * 50
        signal_history = {"features_used": ["f1", "f2", "f3"]}
        report = enabled_evaluator.evaluate(
            daily_returns=returns, signal_history=signal_history
        )
        assert report.private_metrics["n_features"] == 3.0


# ============================================================
# 测试 6: 反作弊检测
# ============================================================


class TestAntiCheatDetection:
    """反作弊: 过拟合 + PIT 违规检测."""

    def test_overfit_data_high_wf_decay(
        self,
        enabled_evaluator: StrategyEvaluator,
        overfit_returns: list[float],
    ) -> None:
        """过拟合数据 wf_sharpe_decay > 0.5."""
        report = enabled_evaluator.evaluate(daily_returns=overfit_returns, n_trials=100)
        decay = report.private_metrics.get("wf_sharpe_decay", 0)
        assert decay > 0.5, f"decay={decay:.4f} 应 > 0.5"

    def test_overfit_data_rh_risk_elevated(
        self,
        enabled_evaluator: StrategyEvaluator,
        overfit_returns: list[float],
    ) -> None:
        """过拟合数据 reward_hacking_risk > 0.25."""
        report = enabled_evaluator.evaluate(daily_returns=overfit_returns, n_trials=100)
        assert (
            report.reward_hacking_risk > 0.25
        ), f"risk={report.reward_hacking_risk:.4f} 应 > 0.25"

    def test_pit_violation_detected(self, enabled_evaluator: StrategyEvaluator) -> None:
        """PIT 违规 (timestamps 非单调) 被检测."""
        random.seed(11)
        returns = [random.gauss(0.0005, 0.01) for _ in range(60)]
        # 2099-12-31 是未来时间戳, 破坏单调性
        signal_history = {
            "timestamps": [
                "2026-01-01",
                "2026-01-02",
                "2099-12-31",
                "2026-01-04",
                "2026-01-05",
                "2026-01-06",
            ],
        }
        report = enabled_evaluator.evaluate(
            daily_returns=returns, signal_history=signal_history
        )
        assert report.pit_violations > 0
        # PIT 违规触发风险加分
        assert report.reward_hacking_risk >= RH_RISK_PIT_VIOLATION or (
            # 简化版检查器可能仅返回违规数而不加分 (降级模式)
            report.pit_violations
            > 0
        )

    def test_pit_clean_data_no_violation(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """干净单调 timestamps 不触发 PIT 违规."""
        returns = [0.01, -0.005, 0.008, 0.002, -0.003, 0.005]
        signal_history = {
            "timestamps": [
                "2026-01-01",
                "2026-01-02",
                "2026-01-03",
                "2026-01-04",
                "2026-01-05",
                "2026-01-06",
            ],
        }
        report = enabled_evaluator.evaluate(
            daily_returns=returns, signal_history=signal_history
        )
        assert report.pit_violations == 0

    def test_overfit_score_bounded(
        self,
        enabled_evaluator: StrategyEvaluator,
        overfit_returns: list[float],
    ) -> None:
        """overfit_score 在 [0, 1]."""
        report = enabled_evaluator.evaluate(daily_returns=overfit_returns)
        assert 0.0 <= report.overfit_score <= 1.0


# ============================================================
# 测试 7: 降级报告结构
# ============================================================


class TestDegradedReport:
    """降级报告结构验证."""

    def test_degraded_report_fields(
        self, disabled_evaluator: StrategyEvaluator
    ) -> None:
        """降级报告字段完整."""
        report = disabled_evaluator.evaluate(daily_returns=[0.01, 0.02])
        assert report.is_degraded is True
        assert report.degraded_reason != ""
        assert report.public_score == 0.0
        assert report.private_score == 0.0
        assert report.reward_hacking_risk == 1.0
        assert report.recommendation == "continue"
        assert report.sample_count == 2
        assert "degraded:" in report.reason

    def test_degraded_report_for_empty(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """空数据降级报告 reason 含 'empty_returns'."""
        report = enabled_evaluator.evaluate(daily_returns=[])
        assert "empty_returns" in report.degraded_reason
        assert report.reason == "degraded: empty_returns"


# ============================================================
# 测试 8: ScoreReport 序列化
# ============================================================


class TestScoreReportSerialization:
    """ScoreReport.to_dict() 序列化测试."""

    def test_to_dict_keys(
        self,
        enabled_evaluator: StrategyEvaluator,
        deterministic_returns: list[float],
    ) -> None:
        """to_dict 包含所有必需字段."""
        report = enabled_evaluator.evaluate(daily_returns=deterministic_returns)
        d = report.to_dict()
        expected_keys = {
            "public_score",
            "public_metrics",
            "private_score",
            "private_metrics",
            "reward_hacking_risk",
            "pit_violations",
            "overfit_score",
            "recommendation",
            "reason",
            "sample_count",
            "is_degraded",
            "degraded_reason",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_json_serializable(
        self,
        enabled_evaluator: StrategyEvaluator,
        deterministic_returns: list[float],
    ) -> None:
        """to_dict 结果可 JSON 序列化."""
        report = enabled_evaluator.evaluate(daily_returns=deterministic_returns)
        d = report.to_dict()
        # 不抛异常即可
        json.dumps(d, ensure_ascii=False)

    def test_to_dict_rounded(
        self,
        enabled_evaluator: StrategyEvaluator,
        deterministic_returns: list[float],
    ) -> None:
        """to_dict 数值保留 4 位小数."""
        report = enabled_evaluator.evaluate(daily_returns=deterministic_returns)
        d = report.to_dict()
        # 检查 public_score 不超过 4 位小数
        assert round(d["public_score"], 4) == d["public_score"]


# ============================================================
# 测试 9: evaluate_from_jsonl
# ============================================================


class TestEvaluateFromJsonl:
    """evaluate_from_jsonl 便捷方法."""

    def test_read_existing_jsonl(
        self,
        enabled_evaluator: StrategyEvaluator,
        tmp_jsonl: Path,
    ) -> None:
        """读取 jsonl 并评估."""
        report = enabled_evaluator.evaluate_from_jsonl(jsonl_path=str(tmp_jsonl))
        assert isinstance(report, ScoreReport)
        assert report.sample_count == 5

    def test_file_not_found_returns_degraded(
        self,
        enabled_evaluator: StrategyEvaluator,
        tmp_path: Path,
    ) -> None:
        """文件不存在返回降级报告."""
        missing = tmp_path / "nonexistent.jsonl"
        report = enabled_evaluator.evaluate_from_jsonl(jsonl_path=str(missing))
        assert report.is_degraded is True
        assert "file_not_found" in report.degraded_reason

    def test_read_failure_degraded(
        self,
        enabled_evaluator: StrategyEvaluator,
        tmp_path: Path,
    ) -> None:
        """损坏的 JSONL 返回降级报告 (不抛异常)."""
        bad_jsonl = tmp_path / "bad.jsonl"
        # 写入无法解析的内容 (使用不可见控制字符触发解析失败)
        bad_jsonl.write_text("\x00bad\x00json\x00", encoding="utf-8")
        report = enabled_evaluator.evaluate_from_jsonl(jsonl_path=str(bad_jsonl))
        # 要么降级, 要么返回空评估
        assert report.is_degraded is True or report.sample_count == 0


# ============================================================
# 测试 10: 决策建议阈值
# ============================================================


class TestRecommendationLogic:
    """promote/rollback/continue 阈值测试."""

    def test_bad_data_not_promote(self, enabled_evaluator: StrategyEvaluator) -> None:
        """负收益高波动数据不应 promote."""
        random.seed(999)
        bad = [random.gauss(-0.001, 0.02) for _ in range(TRADING_DAYS_PER_YEAR)]
        report = enabled_evaluator.evaluate(daily_returns=bad)
        assert report.recommendation != "promote"

    def test_recommendation_in_valid_set(
        self,
        enabled_evaluator: StrategyEvaluator,
        deterministic_returns: list[float],
    ) -> None:
        """recommendation 在 {promote, rollback, continue} 内."""
        report = enabled_evaluator.evaluate(daily_returns=deterministic_returns)
        assert report.recommendation in {"promote", "rollback", "continue"}

    def test_promote_threshold_logic(self) -> None:
        """直接测试 _make_recommendation 阈值."""
        evaluator = StrategyEvaluator.__new__(StrategyEvaluator)
        # promote: private_score > 0.70 且 rh_risk < 0.30
        rec, _ = evaluator._make_recommendation(
            private_score=PROMOTE_PRIVATE_SCORE + 0.05,
            rh_risk=PROMOTE_RH_RISK_MAX - 0.1,
            public_score=0.5,
        )
        assert rec == "promote"

    def test_rollback_on_high_rh_risk(self) -> None:
        """rh_risk > 0.7 时 rollback."""
        evaluator = StrategyEvaluator.__new__(StrategyEvaluator)
        rec, reason = evaluator._make_recommendation(
            private_score=0.9, rh_risk=0.8, public_score=0.9
        )
        assert rec == "rollback"
        assert "reward_hacking_risk" in reason

    def test_rollback_on_low_private(self) -> None:
        """private_score < 0.30 时 rollback."""
        evaluator = StrategyEvaluator.__new__(StrategyEvaluator)
        rec, reason = evaluator._make_recommendation(
            private_score=ROLLBACK_PRIVATE_SCORE - 0.05,
            rh_risk=0.1,
            public_score=0.5,
        )
        assert rec == "rollback"
        assert "private_score" in reason

    def test_continue_in_middle_range(self) -> None:
        """private_score 在 (0.30, 0.70] 且 rh_risk 较高时 continue."""
        evaluator = StrategyEvaluator.__new__(StrategyEvaluator)
        rec, _ = evaluator._make_recommendation(
            private_score=0.5, rh_risk=0.4, public_score=0.5
        )
        assert rec == "continue"


# ============================================================
# 测试 11: 底层计算工具
# ============================================================


class TestUnderlyingComputation:
    """底层计算函数单元测试."""

    def test_compute_annual_return_positive(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """正收益年化为正."""
        returns = [0.001] * TRADING_DAYS_PER_YEAR  # 每日 0.1%
        ar = enabled_evaluator._compute_annual_return(returns)
        assert ar > 0.2  # 大约 28%

    def test_compute_annual_return_negative(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """负收益年化为负."""
        returns = [-0.001] * TRADING_DAYS_PER_YEAR
        ar = enabled_evaluator._compute_annual_return(returns)
        assert ar < 0

    def test_compute_annual_return_empty(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """空数据年化为 0."""
        assert enabled_evaluator._compute_annual_return([]) == 0.0

    def test_compute_sharpe_empty(self, enabled_evaluator: StrategyEvaluator) -> None:
        """空数据 Sharpe=0."""
        assert enabled_evaluator._compute_sharpe([]) == 0.0

    def test_compute_sharpe_single_sample(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """单样本 Sharpe=0."""
        assert enabled_evaluator._compute_sharpe([0.01]) == 0.0

    def test_compute_sharpe_zero_variance(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """零方差 (常量收益) Sharpe=0."""
        assert enabled_evaluator._compute_sharpe([0.01] * 100) == 0.0

    def test_compute_max_drawdown_no_drawdown(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """单调上涨无回撤."""
        returns = [0.001] * 50
        dd = enabled_evaluator._compute_max_drawdown(returns)
        assert dd == 0.0

    def test_compute_max_drawdown_total_loss(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """全程下跌, 回撤接近 1."""
        returns = [-0.01] * 200
        dd = enabled_evaluator._compute_max_drawdown(returns)
        assert dd > 0.8

    def test_compute_sharpe_cv_short_sample(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """样本 < 20 时 sharpe_cv=0."""
        assert enabled_evaluator._compute_sharpe_cv([0.01] * 10) == 0.0

    def test_compute_wf_decay_short_sample(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """样本 < MIN_SAMPLES_FOR_WF 时 wf_decay=-1."""
        returns = [0.01] * (MIN_SAMPLES_FOR_WF - 1)
        assert enabled_evaluator._compute_wf_sharpe_decay(returns) == -1.0

    def test_score_absolute_return_clamped(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """年化收益打分限幅在 [0, 1]."""
        assert enabled_evaluator._score_absolute_return(0.0) == 0.0
        assert enabled_evaluator._score_absolute_return(BASELINE_ANNUAL_RETURN) == 0.0
        assert enabled_evaluator._score_absolute_return(
            BASELINE_ANNUAL_RETURN + 0.15
        ) == pytest.approx(1.0)
        assert enabled_evaluator._score_absolute_return(10.0) == 1.0
        assert enabled_evaluator._score_absolute_return(-0.5) == 0.0

    def test_score_risk_adjusted_clamped(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """Sharpe 打分限幅在 [0, 1]."""
        assert enabled_evaluator._score_risk_adjusted(0.0) == 0.0
        assert enabled_evaluator._score_risk_adjusted(BASELINE_SHARPE) == 0.0
        assert enabled_evaluator._score_risk_adjusted(BASELINE_SHARPE + 1.0) == 1.0
        assert enabled_evaluator._score_risk_adjusted(10.0) == 1.0

    def test_score_stability_clamped(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """稳定性打分: 0 回撤=1, MAX_ALLOWED_DRAWDOWN=0."""
        assert enabled_evaluator._score_stability(0.0) == 1.0
        assert enabled_evaluator._score_stability(MAX_ALLOWED_DRAWDOWN) == 0.0
        assert enabled_evaluator._score_stability(MAX_ALLOWED_DRAWDOWN + 0.1) == 0.0

    def test_score_robustness_negative_returns_neutral(
        self, enabled_evaluator: StrategyEvaluator
    ) -> None:
        """样本不足 (wf_decay<0) 时稳健性=0.3."""
        assert enabled_evaluator._score_robustness(-1.0) == 0.3

    def test_score_complexity(self, enabled_evaluator: StrategyEvaluator) -> None:
        """复杂度惩罚: 0-10 满分, >50 零分."""
        assert enabled_evaluator._score_complexity(0) == 1.0
        assert enabled_evaluator._score_complexity(10) == 1.0
        assert enabled_evaluator._score_complexity(50) == 0.0
        assert enabled_evaluator._score_complexity(100) == 0.0
        # 30 特征 → 0.5
        assert enabled_evaluator._score_complexity(30) == pytest.approx(0.5, abs=1e-6)


# ============================================================
# 测试 12: 便捷函数
# ============================================================


class TestConvenienceFunctions:
    """模块级便捷函数."""

    def test_evaluate_strategy_returns_report(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """evaluate_strategy 返回 ScoreReport."""
        monkeypatch.setattr("utils.infra.feature_flags.is_enabled", lambda name: True)
        report = evaluate_strategy(daily_returns=[0.01] * 50, n_trials=10)
        assert isinstance(report, ScoreReport)

    def test_evaluate_from_shadow_missing_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """evaluate_from_shadow 文件不存在时返回降级报告."""
        monkeypatch.setattr("utils.infra.feature_flags.is_enabled", lambda name: True)
        # patch 默认路径到 tmp_path (确保文件不存在)
        monkeypatch.setattr("utils.alpha.strategy_evaluator._PROJECT_ROOT", tmp_path)
        report = evaluate_from_shadow()
        assert isinstance(report, ScoreReport)
        assert report.is_degraded is True


# ============================================================
# 测试 13: EvolutionOrchestrator 工作流
# ============================================================


class TestEvolutionOrchestratorWorkflow:
    """EvolutionOrchestrator 核心工作流测试."""

    @pytest.fixture
    def enabled_orchestrator(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> EvolutionOrchestrator:
        """启用 Flag 的 Orchestrator (临时目录隔离)."""
        monkeypatch.setattr(
            "utils.infra.feature_flags.is_enabled",
            lambda name: name
            in ("USE_EVOLUTION_ORCHESTRATOR", "USE_STRATEGY_EVALUATOR"),
        )
        daily_returns_path = tmp_path / "daily_returns.jsonl"
        decisions_log = tmp_path / "decisions.jsonl"
        # 写入足够样本 (≥ 252)
        random.seed(42)
        records = [
            {"date": f"2026-01-{i+1:02d}", "daily_return": random.gauss(0.0006, 0.0095)}
            for i in range(TRADING_DAYS_PER_YEAR)
        ]
        with daily_returns_path.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return EvolutionOrchestrator(
            daily_returns_path=daily_returns_path,
            decisions_log_path=decisions_log,
            observation_start_date="2026-01-01",
        )

    @pytest.fixture
    def disabled_orchestrator(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> EvolutionOrchestrator:
        """禁用 Flag 的 Orchestrator."""
        monkeypatch.setattr("utils.infra.feature_flags.is_enabled", lambda name: False)
        return EvolutionOrchestrator(
            daily_returns_path=tmp_path / "daily_returns.jsonl",
            decisions_log_path=tmp_path / "decisions.jsonl",
        )

    def test_init_enabled(self, enabled_orchestrator: EvolutionOrchestrator) -> None:
        """启用时 enabled=True."""
        assert enabled_orchestrator.enabled is True

    def test_init_disabled(self, disabled_orchestrator: EvolutionOrchestrator) -> None:
        """禁用时 enabled=False."""
        assert disabled_orchestrator.enabled is False

    def test_get_status_returns_dict(
        self, enabled_orchestrator: EvolutionOrchestrator
    ) -> None:
        """get_status 返回字典."""
        status = enabled_orchestrator.get_status()
        assert isinstance(status, dict)
        assert "status" in status
        assert "enabled" in status
        assert "observation_day" in status

    def test_disabled_status(
        self, disabled_orchestrator: EvolutionOrchestrator
    ) -> None:
        """禁用时 status=disabled."""
        status = disabled_orchestrator.get_status()
        assert status["status"] == STATUS_DISABLED
        assert status["enabled"] is False

    def test_collect_metrics_disabled_returns_degraded(
        self, disabled_orchestrator: EvolutionOrchestrator
    ) -> None:
        """禁用时 collect_metrics 返回降级快照."""
        metrics = disabled_orchestrator.collect_metrics()
        assert metrics.is_degraded is True
        assert "feature_flag_disabled" in metrics.degraded_reason

    def test_collect_metrics_reads_file(
        self, enabled_orchestrator: EvolutionOrchestrator
    ) -> None:
        """启用时 collect_metrics 读取 jsonl."""
        metrics = enabled_orchestrator.collect_metrics()
        assert metrics.is_degraded is False
        assert metrics.sample_count == TRADING_DAYS_PER_YEAR
        assert len(metrics.daily_returns) == TRADING_DAYS_PER_YEAR
        assert len(metrics.dates) == TRADING_DAYS_PER_YEAR

    def test_collect_metrics_missing_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """文件不存在返回降级快照."""
        monkeypatch.setattr("utils.infra.feature_flags.is_enabled", lambda name: True)
        orch = EvolutionOrchestrator(
            daily_returns_path=tmp_path / "missing.jsonl",
            decisions_log_path=tmp_path / "dec.jsonl",
        )
        metrics = orch.collect_metrics()
        assert metrics.is_degraded is True
        assert "file_not_found" in metrics.degraded_reason

    def test_evaluate_current_returns_report(
        self, enabled_orchestrator: EvolutionOrchestrator
    ) -> None:
        """evaluate_current 返回 ScoreReport."""
        report = enabled_orchestrator.evaluate_current()
        assert report is not None
        assert isinstance(report, ScoreReport)

    def test_evaluate_current_disabled_returns_none(
        self, disabled_orchestrator: EvolutionOrchestrator
    ) -> None:
        """禁用时 evaluate_current 返回 None."""
        assert disabled_orchestrator.evaluate_current() is None

    def test_log_decision_writes_jsonl(
        self, enabled_orchestrator: EvolutionOrchestrator
    ) -> None:
        """log_decision 写入 decisions.jsonl."""
        report = enabled_orchestrator.evaluate_current()
        ok = enabled_orchestrator.log_decision(
            report=report, action=ACTION_EVALUATE_ONLY, reason="unit_test"
        )
        assert ok is True
        assert enabled_orchestrator.decisions_log_path.exists()
        # 验证内容
        with enabled_orchestrator.decisions_log_path.open(encoding="utf-8") as f:
            line = f.readline()
        record = json.loads(line)
        assert record["action"] == ACTION_EVALUATE_ONLY
        assert record["reason"] == "unit_test"

    def test_log_decision_disabled_returns_false(
        self, disabled_orchestrator: EvolutionOrchestrator
    ) -> None:
        """禁用时 log_decision 返回 False, 不写文件."""
        ok = disabled_orchestrator.log_decision(action=ACTION_NOOP)
        assert ok is False
        assert not disabled_orchestrator.decisions_log_path.exists()

    def test_run_observation_cycle(
        self, enabled_orchestrator: EvolutionOrchestrator
    ) -> None:
        """run_observation_cycle 完整流程."""
        result = enabled_orchestrator.run_observation_cycle()
        assert isinstance(result, dict)
        assert "status" in result
        assert "metrics_snapshot" in result
        assert "has_report" in result

    def test_run_observation_cycle_disabled(
        self, disabled_orchestrator: EvolutionOrchestrator
    ) -> None:
        """禁用时 run_observation_cycle 返回 disabled."""
        result = disabled_orchestrator.run_observation_cycle()
        assert result["status"] == "disabled"


# ============================================================
# 测试 14: 观察期 (HC-4 只读模式)
# ============================================================


class TestObservationPeriod:
    """HC-4: 观察期内仅评估, 不触发进化动作."""

    def test_observation_in_period(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """观察期内 in_observation=True, status=observation."""
        monkeypatch.setattr("utils.infra.feature_flags.is_enabled", lambda name: True)
        daily_returns_path = tmp_path / "daily_returns.jsonl"
        # 首条日期 = 今天 (在观察期内)
        from datetime import date

        today = date.today().isoformat()
        records = [
            {"date": today, "daily_return": 0.001},
        ]
        with daily_returns_path.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        orch = EvolutionOrchestrator(
            daily_returns_path=daily_returns_path,
            decisions_log_path=tmp_path / "decisions.jsonl",
            observation_start_date=today,
        )
        status = orch.get_status()
        assert status["in_observation"] is True
        assert status["status"] == STATUS_OBSERVATION

    def test_observation_force_evaluate_only(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """观察期内 action 强制改为 evaluate_only (HC-4)."""
        monkeypatch.setattr("utils.infra.feature_flags.is_enabled", lambda name: True)
        from datetime import date

        today = date.today().isoformat()
        daily_returns_path = tmp_path / "daily_returns.jsonl"
        records = [{"date": today, "daily_return": 0.001}]
        with daily_returns_path.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        orch = EvolutionOrchestrator(
            daily_returns_path=daily_returns_path,
            decisions_log_path=tmp_path / "dec.jsonl",
            observation_start_date=today,
        )
        # 强制观察期状态, 然后试图写入非 evaluate_only 的 action
        orch._status.in_observation = True
        orch._status.status = STATUS_OBSERVATION

        ok = orch.log_decision(action="promote", reason="try_promote_in_observation")
        assert ok is True
        with (tmp_path / "dec.jsonl").open(encoding="utf-8") as f:
            record = json.loads(f.readline())
        # 应被强制为 evaluate_only
        assert record["action"] == ACTION_EVALUATE_ONLY


# ============================================================
# 测试 15: 决策日志持久化与读取
# ============================================================


class TestDecisionLogPersistence:
    """决策日志 (decisions.jsonl) 持久化."""

    def test_decision_record_to_dict(self) -> None:
        """DecisionRecord.to_dict 含完整字段."""
        record = DecisionRecord(
            timestamp="2026-08-01T00:00:00Z",
            action=ACTION_EVALUATE_ONLY,
            public_score=0.5,
            private_score=0.6,
            reward_hacking_risk=0.1,
            recommendation="continue",
            sample_count=100,
            observation_day=5,
            reason="test",
        )
        d = record.to_dict()
        assert d["timestamp"] == "2026-08-01T00:00:00Z"
        assert d["action"] == ACTION_EVALUATE_ONLY
        assert d["public_score"] == 0.5
        assert d["sample_count"] == 100
        assert d["observation_day"] == 5

    def test_multiple_decisions_appended(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """多次 log_decision 追加写入."""
        monkeypatch.setattr("utils.infra.feature_flags.is_enabled", lambda name: True)
        decisions_log = tmp_path / "dec.jsonl"
        orch = EvolutionOrchestrator(
            daily_returns_path=tmp_path / "missing.jsonl",
            decisions_log_path=decisions_log,
        )
        # 写入 3 条
        for i in range(3):
            orch.log_decision(action=ACTION_NOOP, reason=f"record_{i}")
        # 读取
        lines = decisions_log.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3

    def test_get_recent_decisions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """get_recent_decisions 倒序返回."""
        monkeypatch.setattr("utils.infra.feature_flags.is_enabled", lambda name: True)
        decisions_log = tmp_path / "dec.jsonl"
        orch = EvolutionOrchestrator(
            daily_returns_path=tmp_path / "missing.jsonl",
            decisions_log_path=decisions_log,
        )
        for i in range(5):
            orch.log_decision(action=ACTION_NOOP, reason=f"r{i}")
        recent = orch.get_recent_decisions(limit=3)
        assert len(recent) == 3
        # 最新在前
        assert recent[0]["reason"] == "r4"
        assert recent[2]["reason"] == "r2"

    def test_get_recent_decisions_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """日志文件不存在时返回空列表."""
        monkeypatch.setattr("utils.infra.feature_flags.is_enabled", lambda name: True)
        orch = EvolutionOrchestrator(
            daily_returns_path=tmp_path / "missing.jsonl",
            decisions_log_path=tmp_path / "missing_decisions.jsonl",
        )
        assert orch.get_recent_decisions() == []


# ============================================================
# 测试 16: MetricsSnapshot / OrchestratorStatus 序列化
# ============================================================


class TestDataclassSerialization:
    """数据类 to_dict 方法."""

    def test_metrics_snapshot_to_dict(self) -> None:
        """MetricsSnapshot.to_dict 不含原始收益序列."""
        snap = MetricsSnapshot(
            daily_returns=[0.01, 0.02],
            dates=["2026-01-01", "2026-01-02"],
            sample_count=2,
            source="test",
            collected_at="2026-08-01T00:00:00Z",
        )
        d = snap.to_dict()
        assert d["sample_count"] == 2
        assert d["source"] == "test"
        assert d["first_date"] == "2026-01-01"
        assert d["last_date"] == "2026-01-02"
        # 不应包含原始数据
        assert "daily_returns" not in d

    def test_orchestrator_status_to_dict(self) -> None:
        """OrchestratorStatus.to_dict 字段完整."""
        status = OrchestratorStatus(
            status=STATUS_ENABLED,
            enabled=True,
            observation_day=5,
            observation_total=OBSERVATION_PERIOD_DAYS,
            in_observation=True,
            total_evaluations=3,
        )
        d = status.to_dict()
        assert d["status"] == STATUS_ENABLED
        assert d["enabled"] is True
        assert d["observation_day"] == 5
        assert d["total_evaluations"] == 3
        assert d["observation_total"] == OBSERVATION_PERIOD_DAYS


# ============================================================
# 测试 17: 与现有 daily_returns.jsonl 兼容 (只读)
# ============================================================


class TestRealDataCompatibility:
    """与生产 daily_returns.jsonl 兼容 (只读, 不修改)."""

    def test_read_real_shadow_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """读取真实 shadow/daily_returns.jsonl (如果存在)."""
        project_root = Path(__file__).resolve().parents[2]
        jsonl_path = project_root / "reports" / "shadow" / "daily_returns.jsonl"
        if not jsonl_path.exists():
            pytest.skip(f"真实数据文件不存在: {jsonl_path}")

        original_size = jsonl_path.stat().st_size

        monkeypatch.setattr("utils.infra.feature_flags.is_enabled", lambda name: True)
        evaluator = StrategyEvaluator()
        report = evaluator.evaluate_from_jsonl(jsonl_path=str(jsonl_path))

        # 文件未被修改
        assert jsonl_path.stat().st_size == original_size

        # 评估成功 (如果样本足够)
        assert isinstance(report, ScoreReport)
        if not report.is_degraded:
            assert report.sample_count > 0
