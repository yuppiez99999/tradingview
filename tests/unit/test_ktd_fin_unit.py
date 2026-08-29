"""
KTD-Fin 记忆控制评估基准 — 单元测试
====================================

测试覆盖:
- MaskStrategy / LeakSeverity 枚举
- MarketDataPoint / AttributionResult / LeakAssessment 数据结构
- DataMasker (4 种掩码策略)
- BarraAttributor (因子归因 + 异常检测)
- MemoryLeakDetector (泄漏检测 + 严重程度)
- KTDFinBenchmark (端到端评估)
- MockAgent

文献: #25 KTD-Fin: Memory-Controlled Benchmark (2026.05)
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.eval.ktd_fin import (
    BARRA_FACTORS,
    AttributionResult,
    BarraAttributor,
    DataMasker,
    KTDFinBenchmark,
    LeakAssessment,
    LeakSeverity,
    MarketDataPoint,
    MaskStrategy,
    MemoryLeakDetector,
    MockAgent,
)

# ============================================================
# 枚举测试
# ============================================================


class TestEnums:
    """枚举测试。"""

    def test_mask_strategies(self):
        assert len(MaskStrategy) == 4
        assert MaskStrategy.ZERO.value == "zero"

    def test_leak_severities(self):
        assert len(LeakSeverity) == 5
        assert LeakSeverity.NONE.value == "none"
        assert LeakSeverity.CRITICAL.value == "critical"

    def test_barra_factors(self):
        assert len(BARRA_FACTORS) == 6
        assert "market" in BARRA_FACTORS
        assert "beta" in BARRA_FACTORS


# ============================================================
# 数据结构测试
# ============================================================


class TestMarketDataPoint:
    """市场数据条目测试。"""

    def test_default_values(self):
        point = MarketDataPoint(date="2025-01-01", ticker="000300.SH")
        assert point.price == 0.0
        assert point.is_future is False

    def test_to_dict(self):
        point = MarketDataPoint(
            date="2025-01-01", ticker="A", price=10.0, is_future=True
        )
        d = point.to_dict()
        assert d["date"] == "2025-01-01"
        assert d["is_future"] is True


class TestAttributionResult:
    """归因结果测试。"""

    def test_default_values(self):
        result = AttributionResult()
        assert result.factor_exposures == {}
        assert result.r_squared == 0.0

    def test_explained_return(self):
        result = AttributionResult(
            factor_exposures={"market": 1.0, "size": 0.5},
            factor_returns={"market": 0.02, "size": 0.01},
        )
        # 1.0 * 0.02 + 0.5 * 0.01 = 0.025
        assert abs(result.explained_return - 0.025) < 1e-6

    def test_to_dict(self):
        result = AttributionResult(total_return=0.05, r_squared=0.8)
        d = result.to_dict()
        assert d["total_return"] == 0.05
        assert d["r_squared"] == 0.8


class TestLeakAssessment:
    """泄漏评估结果测试。"""

    def test_default_values(self):
        assessment = LeakAssessment()
        assert assessment.is_leaked is False
        assert assessment.severity == LeakSeverity.NONE

    def test_to_dict(self):
        assessment = LeakAssessment(
            is_leaked=True,
            severity=LeakSeverity.HIGH,
            sharpe_ratio=3.5,
        )
        d = assessment.to_dict()
        assert d["is_leaked"] is True
        assert d["severity"] == "high"


# ============================================================
# DataMasker 测试
# ============================================================


class TestDataMasker:
    """数据掩码器测试。"""

    def _make_data(self) -> list[MarketDataPoint]:
        return [
            MarketDataPoint(date="2025-01-01", ticker="A", price=10.0, return_pct=0.01),
            MarketDataPoint(date="2025-01-02", ticker="A", price=10.5, return_pct=0.02),
            MarketDataPoint(
                date="2025-01-03",
                ticker="A",
                price=11.0,
                return_pct=0.03,
                is_future=True,
            ),
        ]

    def test_mask_zero_strategy(self):
        """零值掩码。"""
        masker = DataMasker(MaskStrategy.ZERO)
        masked = masker.mask(self._make_data())
        assert len(masked) == 3
        assert masked[2].price == 0.0
        assert masked[2].return_pct == 0.0

    def test_mask_mean_strategy(self):
        """均值掩码。"""
        masker = DataMasker(MaskStrategy.MEAN)
        masked = masker.mask(self._make_data())
        assert len(masked) == 3
        # 未来数据被替换为历史均值
        assert masked[2].price > 0

    def test_mask_drop_strategy(self):
        """丢弃掩码。"""
        masker = DataMasker(MaskStrategy.DROP)
        masked = masker.mask(self._make_data())
        assert len(masked) == 2  # 未来数据被丢弃

    def test_mask_preserves_historical(self):
        """历史数据不被掩码。"""
        masker = DataMasker(MaskStrategy.ZERO)
        data = self._make_data()
        masked = masker.mask(data)
        assert masked[0].price == 10.0
        assert masked[1].price == 10.5

    def test_mask_no_future_data(self):
        """无未来数据时不变。"""
        masker = DataMasker(MaskStrategy.ZERO)
        data = [MarketDataPoint(date="2025-01-01", ticker="A", price=10.0)]
        masked = masker.mask(data)
        assert len(masked) == 1
        assert masked[0].price == 10.0

    def test_get_mask_summary(self):
        """掩码摘要。"""
        masker = DataMasker(MaskStrategy.MEAN)
        data = self._make_data()
        summary = masker.get_mask_summary(data)
        assert summary["total_points"] == 3
        assert summary["masked_points"] == 1
        assert summary["mask_ratio"] > 0


# ============================================================
# BarraAttributor 测试
# ============================================================


class TestBarraAttributor:
    """Barra 风险因子归因测试。"""

    def test_attribute_basic(self):
        """基本归因。"""
        attributor = BarraAttributor()
        returns = [0.01, 0.02, -0.01, 0.03, 0.005]
        exposures = {"market": [1.0] * 5, "size": [0.5] * 5}
        result = attributor.attribute(returns, exposures)
        assert len(result.factor_exposures) == 6
        assert result.total_return > 0

    def test_attribute_empty(self):
        """空收益。"""
        attributor = BarraAttributor()
        result = attributor.attribute([], {})
        assert result.total_return == 0.0

    def test_attribute_all_factors_present(self):
        """所有 6 因子都有暴露。"""
        attributor = BarraAttributor()
        returns = [0.01, 0.02, -0.01]
        exposures = {f: [0.1, 0.2, 0.3] for f in BARRA_FACTORS}
        result = attributor.attribute(returns, exposures)
        for f in BARRA_FACTORS:
            assert f in result.factor_exposures

    def test_detect_anomalies_normal(self):
        """正常暴露无异常。"""
        attributor = BarraAttributor()
        attribution = AttributionResult(
            factor_exposures={"market": 0.5, "size": 0.3},
            factor_returns={"market": 0.01, "size": 0.005},
        )
        anomalies = attributor.detect_anomalies(attribution)
        assert len(anomalies) == 0

    def test_detect_anomalies_overexposed(self):
        """过度暴露检测。"""
        attributor = BarraAttributor()
        attribution = AttributionResult(
            factor_exposures={"market": 3.0, "size": 0.5},
        )
        anomalies = attributor.detect_anomalies(attribution)
        assert any("market" in a for a in anomalies)

    def test_detect_anomalies_abnormal_return(self):
        """异常因子收益检测。"""
        attributor = BarraAttributor()
        attribution = AttributionResult(
            factor_exposures={"market": 0.5},
            factor_returns={"market": 0.1},
        )
        anomalies = attributor.detect_anomalies(attribution)
        assert any("market" in a for a in anomalies)


# ============================================================
# MemoryLeakDetector 测试
# ============================================================


class TestMemoryLeakDetector:
    """记忆泄漏检测器测试。"""

    def test_no_leak(self):
        """无泄漏。"""
        detector = MemoryLeakDetector()
        result = detector.detect(
            original_result={"sharpe_ratio": 1.0, "win_rate": 0.55},
            masked_result={"sharpe_ratio": 1.0, "win_rate": 0.55},
        )
        assert not result.is_leaked
        assert result.severity == LeakSeverity.NONE

    def test_high_sharpe_leak(self):
        """高 Sharpe 泄漏。"""
        detector = MemoryLeakDetector()
        result = detector.detect(
            original_result={"sharpe_ratio": 5.0, "win_rate": 0.55},
            masked_result={"sharpe_ratio": 5.0, "win_rate": 0.55},
        )
        assert result.is_leaked
        assert result.severity in (
            LeakSeverity.MEDIUM,
            LeakSeverity.HIGH,
            LeakSeverity.CRITICAL,
        )

    def test_high_win_rate_leak(self):
        """高胜率泄漏。"""
        detector = MemoryLeakDetector()
        result = detector.detect(
            original_result={"sharpe_ratio": 1.0, "win_rate": 0.90},
            masked_result={"sharpe_ratio": 1.0, "win_rate": 0.90},
        )
        assert result.is_leaked

    def test_decision_diff_leak(self):
        """掩码前后差异大。"""
        detector = MemoryLeakDetector()
        result = detector.detect(
            original_result={"sharpe_ratio": 5.0, "win_rate": 0.55},
            masked_result={"sharpe_ratio": 1.0, "win_rate": 0.55},
        )
        assert result.decision_diff > 2.0
        assert result.is_leaked

    def test_critical_severity(self):
        """严重泄漏。"""
        detector = MemoryLeakDetector()
        result = detector.detect(
            original_result={"sharpe_ratio": 10.0, "win_rate": 0.95},
            masked_result={"sharpe_ratio": 10.0, "win_rate": 0.95},
        )
        assert result.severity == LeakSeverity.CRITICAL

    def test_with_attribution_anomalies(self):
        """带归因异常的泄漏。"""
        detector = MemoryLeakDetector()
        attribution = AttributionResult(
            factor_exposures={"market": 3.0, "size": 2.5, "value": 2.2},
        )
        result = detector.detect(
            original_result={"sharpe_ratio": 1.0, "win_rate": 0.55},
            masked_result={"sharpe_ratio": 1.0, "win_rate": 0.55},
            attribution=attribution,
        )
        assert len(result.suspicious_factors) > 0


# ============================================================
# KTDFinBenchmark 测试
# ============================================================


class TestKTDFinBenchmark:
    """KTD-Fin 基准测试。"""

    def _make_data(self, n: int = 50) -> list[MarketDataPoint]:
        return [
            MarketDataPoint(
                date=f"2025-01-{i+1:02d}",
                ticker="A",
                price=10.0 + i * 0.1,
                return_pct=0.001,
                is_future=i >= n // 2,
            )
            for i in range(n)
        ]

    def test_evaluate_basic(self):
        """基本评估。"""
        benchmark = KTDFinBenchmark()
        agent = MockAgent(sharpe=1.0)
        result = benchmark.evaluate(agent, self._make_data())
        assert isinstance(result, LeakAssessment)

    def test_evaluate_clean_agent(self):
        """干净代理无泄漏。"""
        benchmark = KTDFinBenchmark()
        agent = MockAgent(sharpe=1.0, win_rate=0.55)
        result = benchmark.evaluate(agent, self._make_data())
        assert not result.is_leaked or result.severity == LeakSeverity.NONE

    def test_evaluate_leaky_agent(self):
        """泄漏代理被检测。"""
        benchmark = KTDFinBenchmark()
        agent = MockAgent(sharpe=5.0, win_rate=0.90)
        result = benchmark.evaluate(agent, self._make_data())
        assert result.is_leaked

    def test_get_summary_empty(self):
        """空摘要。"""
        benchmark = KTDFinBenchmark()
        summary = benchmark.get_summary()
        assert summary["n_evaluations"] == 0

    def test_get_summary_with_results(self):
        """有结果的摘要。"""
        benchmark = KTDFinBenchmark()
        agent = MockAgent(sharpe=1.0)
        benchmark.evaluate(agent, self._make_data())
        benchmark.evaluate(agent, self._make_data())
        summary = benchmark.get_summary()
        assert summary["n_evaluations"] == 2
        assert "severity_counts" in summary

    def test_with_factor_exposures(self):
        """带因子暴露的评估。"""
        benchmark = KTDFinBenchmark()
        agent = MockAgent(sharpe=1.0)
        data = self._make_data()
        exposures = {f: [0.1] * 50 for f in BARRA_FACTORS}
        result = benchmark.evaluate(agent, data, exposures)
        assert isinstance(result, LeakAssessment)


# ============================================================
# MockAgent 测试
# ============================================================


class TestMockAgent:
    """Mock 代理测试。"""

    def test_predict_basic(self):
        """基本预测。"""
        agent = MockAgent(sharpe=1.0)
        data = [MarketDataPoint(date="2025-01-01", ticker="A", price=10.0)]
        result = agent.predict(data)
        assert "returns" in result

    def test_predict_empty(self):
        """空数据。"""
        agent = MockAgent()
        result = agent.predict([])
        assert result["returns"] == []

    def test_predict_deterministic(self):
        """相同输入相同输出。"""
        agent = MockAgent(sharpe=1.0)
        data = [
            MarketDataPoint(date=f"2025-01-{i+1:02d}", ticker="A") for i in range(10)
        ]
        r1 = agent.predict(data)
        r2 = agent.predict(data)
        assert r1["returns"] == r2["returns"]


# ============================================================
# 端到端集成测试
# ============================================================


class TestEndToEnd:
    """端到端集成测试。"""

    def test_full_pipeline(self):
        """完整管线: 数据→掩码→代理→归因→泄漏检测。"""
        benchmark = KTDFinBenchmark(mask_strategy=MaskStrategy.MEAN)
        data = [
            MarketDataPoint(
                date=f"2025-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}",
                ticker="000300.SH",
                price=4000.0 + i * 5,
                return_pct=0.001,
                is_future=i >= 50,
            )
            for i in range(100)
        ]
        agent = MockAgent(sharpe=1.5, win_rate=0.55)
        result = benchmark.evaluate(agent, data)
        assert isinstance(result, LeakAssessment)
        assert result.severity in LeakSeverity

    def test_multiple_agents_comparison(self):
        """多代理对比。"""
        benchmark = KTDFinBenchmark()
        data = [
            MarketDataPoint(date=f"2025-01-{i+1:02d}", ticker="A", is_future=i >= 25)
            for i in range(50)
        ]

        clean_agent = MockAgent(sharpe=1.0, win_rate=0.55)
        leaky_agent = MockAgent(sharpe=5.0, win_rate=0.90)

        benchmark.evaluate(clean_agent, data)
        benchmark.evaluate(leaky_agent, data)

        summary = benchmark.get_summary()
        assert summary["n_evaluations"] == 2
