"""
DeepFund 防泄漏评估基准 — 单元测试
==================================

测试覆盖:
- LLMAdapter (mock 模式 + 可用性检测)
- BenchmarkDataLoader (时序切分 + 合成数据)
- TimeLeakageDetector (时序检查 + 信息边界 + 统计异常)
- DeepFundHarness (端到端评估 + 报告生成)
- 数据结构 (Decision/Metrics/LeakageReport 序列化)
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# 确保项目根目录在 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.eval.deepfund_harness import (
    DEFAULT_LLM_REGISTRY,
    DEFAULT_SYMBOLS,
    BenchmarkDataLoader,
    Decision,
    DeepFundHarness,
    EvalReport,
    EvalResult,
    LeakageReport,
    LLMAdapter,
    MarketData,
    Metrics,
    TimeLeakageDetector,
)

# ============================================================
# LLMAdapter 测试
# ============================================================


class TestLLMAdapter:
    """LLM 适配器测试。"""

    def test_mock_mode_always_available(self):
        """mock 模式始终可用。"""
        adapter = LLMAdapter(provider="mock", model="mock-1", mock=True)
        assert adapter.is_available() is True

    def test_mock_chat_returns_valid_json(self):
        """mock chat 返回有效 JSON。"""
        adapter = LLMAdapter(provider="mock", model="mock-1", mock=True)
        response = adapter.chat("test prompt", system="test system")
        data = json.loads(response)
        assert "action" in data
        assert "weight" in data
        assert data["action"] in ("buy", "sell", "hold")
        assert 0.0 <= data["weight"] <= 1.0

    def test_mock_chat_is_deterministic(self):
        """mock chat 相同输入应产生相同输出 (可复现)。"""
        adapter = LLMAdapter(provider="mock", model="mock-1", mock=True)
        r1 = adapter.chat("same prompt", system="same system")
        r2 = adapter.chat("same prompt", system="same system")
        assert r1 == r2

    def test_auto_mock_when_no_api_key(self):
        """无 API key 时自动降级为 mock。"""
        # 确保环境变量未设置
        old_key = os.environ.pop("OPENAI_API_KEY", None)
        try:
            adapter = LLMAdapter(provider="openai", model="gpt-4o")
            assert adapter.mock is True
        finally:
            if old_key is not None:
                os.environ["OPENAI_API_KEY"] = old_key

    def test_different_models_provide_different_name(self):
        """不同模型有不同名称。"""
        a1 = LLMAdapter(provider="openai", model="gpt-4o", mock=True)
        a2 = LLMAdapter(provider="anthropic", model="claude-3", mock=True)
        assert a1.name != a2.name


# ============================================================
# BenchmarkDataLoader 测试
# ============================================================


class TestBenchmarkDataLoader:
    """基准数据加载器测试。"""

    def test_synthetic_prices_generated(self):
        """合成价格数据正确生成。"""
        loader = BenchmarkDataLoader(
            symbols=["000001.SZ"],
            start_date="2024-01-01",
            end_date="2024-01-31",
            seed=42,
        )
        data = loader.load_market_data("2024-01-15")
        assert "000001.SZ" in data.prices
        assert len(data.prices["000001.SZ"]) > 0
        assert all(p > 0 for p in data.prices["000001.SZ"])

    def test_strict_temporal_cutoff(self):
        """严格时序切分: 截止 date 的数据不包含未来。"""
        loader = BenchmarkDataLoader(
            symbols=["000001.SZ"],
            start_date="2024-01-01",
            end_date="2024-01-31",
            seed=42,
        )
        data_early = loader.load_market_data("2024-01-10")
        data_late = loader.load_market_data("2024-01-20")
        # 早期数据应少于晚期数据
        assert len(data_early.prices["000001.SZ"]) < len(data_late.prices["000001.SZ"])

    def test_data_reproducible_with_same_seed(self):
        """相同 seed 产生相同数据 (可复现)。"""
        loader1 = BenchmarkDataLoader(
            symbols=["000001.SZ"],
            start_date="2024-01-01",
            end_date="2024-01-31",
            seed=42,
        )
        loader2 = BenchmarkDataLoader(
            symbols=["000001.SZ"],
            start_date="2024-01-01",
            end_date="2024-01-31",
            seed=42,
        )
        d1 = loader1.load_market_data("2024-01-15")
        d2 = loader2.load_market_data("2024-01-15")
        assert d1.prices["000001.SZ"] == d2.prices["000001.SZ"]

    def test_trading_dates_exclude_weekends(self):
        """交易日排除周末。"""
        loader = BenchmarkDataLoader(
            symbols=["000001.SZ"],
            start_date="2024-01-01",
            end_date="2024-01-31",
            seed=42,
        )
        dates = loader.get_trading_dates()
        from datetime import datetime

        for d in dates:
            dt = datetime.strptime(d, "%Y-%m-%d")
            assert dt.weekday() < 5  # 周一到周五

    def test_cache_works(self):
        """缓存生效 (同一 date 重复加载返回同一对象)。"""
        loader = BenchmarkDataLoader(
            symbols=["000001.SZ"],
            start_date="2024-01-01",
            end_date="2024-01-31",
            seed=42,
        )
        d1 = loader.load_market_data("2024-01-15")
        d2 = loader.load_market_data("2024-01-15")
        assert d1 is d2


# ============================================================
# TimeLeakageDetector 测试
# ============================================================


class TestTimeLeakageDetector:
    """时间穿越检测器测试 (核心创新)。"""

    def test_chronological_order_ok(self):
        """时序正确的决策通过检测。"""
        decisions = [
            Decision(date="2024-01-01", symbol="A", action="buy", weight=0.5),
            Decision(date="2024-01-02", symbol="A", action="hold", weight=0.5),
            Decision(date="2024-01-03", symbol="A", action="sell", weight=0.3),
        ]
        detector = TimeLeakageDetector()
        report = detector.detect(decisions, Metrics())
        assert report.chronological_ok is True
        assert report.is_leaked is False

    def test_chronological_order_violated(self):
        """时序违反 (未来决策混入) 被检测。"""
        decisions = [
            Decision(date="2024-01-03", symbol="A", action="buy", weight=0.5),
            Decision(date="2024-01-01", symbol="A", action="sell", weight=0.3),
        ]
        detector = TimeLeakageDetector()
        report = detector.detect(decisions, Metrics())
        assert report.chronological_ok is False
        assert report.is_leaked is True

    def test_info_boundary_ok(self):
        """推理过程不引用未来日期时通过。"""
        decisions = [
            Decision(
                date="2024-01-05",
                symbol="A",
                action="buy",
                weight=0.5,
                reasoning="基于 2024-01-01 至 2024-01-04 的数据决策",
            ),
        ]
        detector = TimeLeakageDetector()
        report = detector.detect(decisions, Metrics())
        assert report.info_boundary_ok is True

    def test_info_boundary_violated(self):
        """推理过程引用未来日期被检测。"""
        decisions = [
            Decision(
                date="2024-01-05",
                symbol="A",
                action="buy",
                weight=0.5,
                reasoning="我预见了 2024-02-01 的价格将上涨",
            ),
        ]
        detector = TimeLeakageDetector()
        report = detector.detect(decisions, Metrics())
        assert report.info_boundary_ok is False
        assert report.is_leaked is True

    def test_anomalous_sharpe_detected(self):
        """异常高 Sharpe 被检测为疑似泄漏。"""
        decisions = [
            Decision(date=f"2024-01-{i:02d}", symbol="A", action="buy", weight=0.5)
            for i in range(1, 11)
        ]
        metrics = Metrics(sharpe_ratio=4.5, win_rate=0.5)  # 异常高 Sharpe
        detector = TimeLeakageDetector()
        report = detector.detect(decisions, metrics)
        assert report.is_leaked is True
        assert any("Sharpe" in r for r in report.reasons)

    def test_anomalous_win_rate_detected(self):
        """异常高胜率被检测为可疑。"""
        decisions = [
            Decision(date=f"2024-01-{i:02d}", symbol="A", action="buy", weight=0.5)
            for i in range(1, 11)
        ]
        metrics = Metrics(sharpe_ratio=0.5, win_rate=0.9)  # 异常高胜率
        detector = TimeLeakageDetector()
        report = detector.detect(decisions, metrics)
        assert report.is_leaked is True
        assert any("胜率" in r for r in report.reasons)

    def test_normal_metrics_not_flagged(self):
        """正常指标不被标记为泄漏。"""
        decisions = [
            Decision(date=f"2024-01-{i:02d}", symbol="A", action="hold", weight=0.3)
            for i in range(1, 11)
        ]
        metrics = Metrics(sharpe_ratio=0.5, win_rate=0.5)
        detector = TimeLeakageDetector()
        report = detector.detect(decisions, metrics)
        assert report.is_leaked is False

    def test_benchmark_comparison(self):
        """对照组对比: 超越基准 5 倍被检测。"""
        decisions = [
            Decision(date=f"2024-01-{i:02d}", symbol="A", action="buy", weight=0.8)
            for i in range(1, 11)
        ]
        metrics = Metrics(sharpe_ratio=5.0, win_rate=0.5)
        benchmark = Metrics(sharpe_ratio=0.5)  # 随机基准 Sharpe=0.5
        detector = TimeLeakageDetector()
        report = detector.detect(decisions, metrics, benchmark)
        assert report.is_leaked is True
        assert any("5 倍" in r for r in report.reasons)

    def test_empty_decisions_safe(self):
        """空决策列表不崩溃。"""
        detector = TimeLeakageDetector()
        report = detector.detect([], Metrics())
        assert report.is_leaked is False
        assert report.chronological_ok is True


# ============================================================
# DeepFundHarness 端到端测试
# ============================================================


class TestDeepFundHarness:
    """主评估器端到端测试。"""

    @pytest.fixture
    def harness(self):
        return DeepFundHarness(symbols=["000001.SZ", "600000.SH"], seed=42)

    @pytest.fixture
    def mock_adapters(self):
        return [
            LLMAdapter(provider="mock", model=f"mock-{i}", mock=True) for i in range(3)
        ]

    def test_evaluation_completes(self, harness, mock_adapters):
        """评估正常完成。"""
        report = harness.run_evaluation(mock_adapters, "2024-01-01", "2024-02-28")
        assert len(report.results) == 3
        assert report.start_date == "2024-01-01"
        assert report.end_date == "2024-02-28"
        assert report.n_trading_days > 0

    def test_all_results_have_metrics(self, harness, mock_adapters):
        """所有结果都有指标。"""
        report = harness.run_evaluation(mock_adapters, "2024-01-01", "2024-02-28")
        for result in report.results:
            assert result.error is None
            assert result.metrics.n_decisions > 0

    def test_report_serialization(self, harness, mock_adapters):
        """报告可序列化为 dict/JSON。"""
        report = harness.run_evaluation(mock_adapters, "2024-01-01", "2024-02-28")
        data = report.to_dict()
        json_str = json.dumps(data, ensure_ascii=False)
        assert "results" in json_str
        assert "metrics" in json_str

    def test_report_save_to_file(self, harness, mock_adapters):
        """报告保存到文件。"""
        report = harness.run_evaluation(mock_adapters, "2024-01-01", "2024-02-28")
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = harness.save_report(report, tmpdir)
            assert filepath.exists()
            with open(filepath, encoding="utf-8") as f:
                saved = json.load(f)
            assert len(saved["results"]) == 3

    def test_mock_adapters_pass_leakage_check(self, harness, mock_adapters):
        """mock 适配器 (伪随机) 应通过泄漏检测 (非异常表现)。"""
        report = harness.run_evaluation(mock_adapters, "2024-01-01", "2024-03-31")
        for result in report.results:
            # mock 是伪随机, 不应被标记为泄漏
            assert result.leakage.chronological_ok is True
            assert result.leakage.info_boundary_ok is True

    def test_9_llm_registry_complete(self):
        """默认 LLM 注册表包含 9 个模型。"""
        assert len(DEFAULT_LLM_REGISTRY) == 9

    def test_default_symbols_not_empty(self):
        """默认标的列表非空。"""
        assert len(DEFAULT_SYMBOLS) > 0


# ============================================================
# 数据结构测试
# ============================================================


class TestDataStructures:
    """数据结构序列化测试。"""

    def test_decision_creation(self):
        """Decision 创建。"""
        d = Decision(date="2024-01-01", symbol="A", action="buy", weight=0.5)
        assert d.date == "2024-01-01"
        assert d.action == "buy"
        assert d.weight == 0.5

    def test_metrics_defaults(self):
        """Metrics 默认值。"""
        m = Metrics()
        assert m.total_return == 0.0
        assert m.sharpe_ratio == 0.0
        assert m.n_decisions == 0

    def test_leakage_report_defaults(self):
        """LeakageReport 默认值 (无泄漏)。"""
        r = LeakageReport()
        assert r.is_leaked is False
        assert r.p_value == 1.0
        assert r.chronological_ok is True

    def test_eval_report_to_dict(self):
        """EvalReport 序列化。"""
        report = EvalReport(
            start_date="2024-01-01",
            end_date="2024-06-30",
            symbols=["A", "B"],
            results=[EvalResult(llm_name="test", provider="mock", model="mock-1")],
        )
        data = report.to_dict()
        assert data["start_date"] == "2024-01-01"
        assert len(data["results"]) == 1
        assert data["results"][0]["llm_name"] == "test"

    def test_market_data_info_cutoff(self):
        """MarketData 信息截止时点。"""
        md = MarketData(date="2024-01-15", prices={"A": [1.0, 2.0]})
        assert md.info_cutoff() == "2024-01-15"
