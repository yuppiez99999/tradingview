"""
金融工程闭环流水线单元测试

覆盖模块:
  - types.py       : 数据结构定义
  - config.py      : 配置加载器
  - orchestrator.py: 流水线编排器
  - data_cleaning.py / alpha_pipeline.py / backtest_gate.py / execution_pipeline.py / risk_monitor.py

验收标准:
  1. PipelineConfig 默认值正确
  2. YAML 配置加载正确
  3. PipelineOrchestrator 初始化正常
  4. dry_run 模式完整闭环可通过
  5. 各模块独立 run 方法正常
  6. 异常处理 fail-closed 正确
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from utils.pipeline.alpha_pipeline import AlphaPipeline
from utils.pipeline.backtest_gate import BacktestGate
from utils.pipeline.config import get_pipeline_config, load_pipeline_config
from utils.pipeline.data_cleaning import DataCleaningPipeline
from utils.pipeline.execution_pipeline import ExecutionPipeline
from utils.pipeline.orchestrator import PipelineOrchestrator, np_mean
from utils.pipeline.risk_monitor import RiskMonitor
from utils.pipeline.types import (
    AlphaSignalResult,
    BacktestGateResult,
    DataQualityReport,
    ExecutionResult,
    PipelineConfig,
    PipelineResult,
    PipelineStage,
    RiskAlert,
)

# ============================================================
# 1. Types 测试
# ============================================================

class TestPipelineConfig:
    """PipelineConfig 默认值与字段"""

    def test_default_values(self):
        config = PipelineConfig()
        assert config.mode == "auto"
        assert config.interval_minutes == 15
        assert config.data_cleaning_enabled is True
        assert config.alpha_enabled is False
        assert config.backtest_gate_enabled is False
        assert config.execution_enabled is False
        assert config.risk_monitor_enabled is True
        # 新增的执行字段
        assert config.execution_dry_run is True
        assert config.execution_target_exposure == 0.95
        assert config.execution_max_order_value == 500_000.0
        assert config.execution_default_algo == "twap"

    def test_config_fields_overridable(self):
        config = PipelineConfig(mode="dry_run", alpha_enabled=True, execution_dry_run=False)
        assert config.mode == "dry_run"
        assert config.alpha_enabled is True
        assert config.execution_dry_run is False


class TestPipelineResult:
    """PipelineResult 序列化"""

    def test_default_creation(self):
        now = datetime.now()
        result = PipelineResult(stage=PipelineStage.COMPLETED, success=True, started_at=now)
        assert result.stage == PipelineStage.COMPLETED
        assert result.success is True
        assert result.duration_ms == 0.0
        assert result.error is None

    def test_to_dict(self):
        now = datetime.now()
        result = PipelineResult(
            stage=PipelineStage.COMPLETED,
            success=True,
            started_at=now,
            completed_at=now,
            duration_ms=123.45,
            metrics={"n_stocks": 10},
        )
        d = result.to_dict()
        assert d["stage"] == "completed"
        assert d["success"] is True
        assert d["duration_ms"] == 123.45
        assert d["metrics"]["n_stocks"] == 10


class TestDataQualityReport:
    def test_default_creation(self):
        r = DataQualityReport(symbol="000001", quality_score=85.0)
        assert r.symbol == "000001"
        assert r.quality_score == 85.0
        assert r.passed is True
        assert r.outlier_flags == []
        assert r.gap_days == 0


class TestAlphaSignalResult:
    def test_default_creation(self):
        r = AlphaSignalResult()
        assert r.signals == {}
        assert r.n_stocks == 0
        assert r.model_name == ""


class TestBacktestGateResult:
    def test_default_creation(self):
        r = BacktestGateResult()
        assert r.passed is False
        assert r.ic == 0.0
        assert r.rejection_reason is None

    def test_passed_validation(self):
        r = BacktestGateResult(ic=0.05, dsr=1.5, sharpe=1.2, passed=True)
        assert r.passed is True
        assert r.ic == 0.05


class TestExecutionResult:
    def test_default_creation(self):
        r = ExecutionResult()
        assert r.dry_run is True
        assert r.total_orders == 0
        assert r.fill_rate == 0.0

    def test_with_orders(self):
        r = ExecutionResult(
            total_orders=10, filled_orders=8, failed_orders=2,
            total_amount=1_000_000, filled_amount=800_000,
            fill_rate=0.8, dry_run=True,
        )
        assert r.fill_rate == 0.8
        assert r.filled_orders == 8


class TestRiskAlert:
    def test_default_creation(self):
        r = RiskAlert()
        assert r.level == 0
        assert r.source == ""


# ============================================================
# 2. Config 加载测试
# ============================================================

class TestConfigLoading:
    """配置加载器测试"""

    def test_load_from_yaml(self, tmp_path: Path):
        """验证嵌套 YAML 格式正确加载"""
        yaml_path = tmp_path / "test_pipeline.yaml"
        yaml_path.write_text("""
mode: dry_run
interval_minutes: 30
data_cleaning:
  enabled: true
  min_quality_score: 90.0
alpha:
  enabled: true
  model: lightgbm
backtest_gate:
  enabled: true
  min_ic: 0.05
execution:
  enabled: true
  dry_run: true
  target_exposure: 0.90
  max_order_value: 200000.0
  default_algo: vwap
risk_monitor:
  enabled: true
""", encoding="utf-8")
        config = load_pipeline_config(yaml_path)
        assert config.mode == "dry_run"
        assert config.interval_minutes == 30
        assert config.min_quality_score == 90.0
        assert config.alpha_enabled is True
        assert config.alpha_model == "lightgbm"
        assert config.backtest_gate_enabled is True
        assert config.min_ic == 0.05
        assert config.execution_enabled is True
        assert config.execution_dry_run is True
        assert config.execution_target_exposure == 0.90
        assert config.execution_max_order_value == 200_000.0
        assert config.execution_default_algo == "vwap"

    def test_load_from_nonexistent_file(self):
        """文件不存在时使用默认值"""
        config = load_pipeline_config("/nonexistent/path.yaml")
        assert config.mode == "auto"
        assert config.alpha_enabled is False

    def test_get_pipeline_config_lazy(self):
        """懒加载正常"""
        # 重置全局状态
        from utils.pipeline import config as cfg_module
        cfg_module._config_loaded = False
        cfg_module._pipeline_config = None
        c = get_pipeline_config()
        assert isinstance(c, PipelineConfig)

    def test_invalid_yaml_fallback(self, tmp_path: Path):
        """YAML 格式错误时回退默认值"""
        bad_path = tmp_path / "bad.yaml"
        bad_path.write_text("{{{invalid_yaml}}", encoding="utf-8")
        config = load_pipeline_config(bad_path)
        assert config.mode == "auto"


# ============================================================
# 3. np_mean 工具函数测试
# ============================================================

class TestNpMean:
    def test_empty_list(self):
        assert np_mean([]) == 0.0

    def test_single_value(self):
        assert np_mean([42.0]) == 42.0

    def test_multiple_values(self):
        assert np_mean([1.0, 2.0, 3.0]) == 2.0

    def test_custom_default(self):
        assert np_mean([], default=-1.0) == -1.0


# ============================================================
# 4. PipelineOrchestrator 测试
# ============================================================

class TestPipelineOrchestrator:
    """编排器核心测试"""

    def test_initialization(self):
        orchestrator = PipelineOrchestrator()
        status = orchestrator.get_status()
        assert status["current_stage"] == "idle"
        assert status["run_count"] == 0
        assert status["error_count"] == 0

    def test_initialization_with_config(self):
        config = PipelineConfig(mode="dry_run")
        orchestrator = PipelineOrchestrator(config=config)
        assert orchestrator.config.mode == "dry_run"

    def test_get_status_format(self):
        orchestrator = PipelineOrchestrator()
        status = orchestrator.get_status()
        assert "current_stage" in status
        assert "last_run_at" in status
        assert "run_count" in status
        assert "error_count" in status
        assert "last_success" in status

    def test_np_mean_util(self):
        assert np_mean([10, 20, 30]) == 20.0
        assert np_mean([]) == 0.0

    def test_run_data_cleaning_only(self):
        """数据清洗单阶段运行（无数据时应返回空列表）"""
        orchestrator = PipelineOrchestrator()
        reports, result = orchestrator.run_data_cleaning_only()
        assert isinstance(reports, list)
        assert isinstance(result, PipelineResult)

    def test_run_alpha_only(self):
        """Alpha 单阶段运行"""
        orchestrator = PipelineOrchestrator()
        signal, result = orchestrator.run_alpha_only()
        assert isinstance(result, PipelineResult)

    def test_run_execution_only(self):
        """执行单阶段运行（无 signal 时优雅跳过）"""
        orchestrator = PipelineOrchestrator()
        exec_result, result = orchestrator.run_execution_only(dry_run=True)
        assert isinstance(exec_result, ExecutionResult)
        assert isinstance(result, PipelineResult)


# ============================================================
# 5. dry_run 完整闭环测试
# ============================================================

class TestPipelineDryRun:
    """dry_run 模式完整闭环"""

    def test_full_cycle_dry_run(self):
        """dry_run 完整闭环可正常完成"""
        config = PipelineConfig(
            mode="dry_run",
            data_cleaning_enabled=True,
            alpha_enabled=True,
            backtest_gate_enabled=True,
            execution_enabled=True,
            risk_monitor_enabled=True,
            execution_dry_run=True,
        )
        orchestrator = PipelineOrchestrator(config=config)
        result = orchestrator.run_full_cycle(mode="dry_run")
        # dry_run 模式下数据清洗可能无数据，但不会崩溃
        assert isinstance(result, PipelineResult)
        # 即使某些阶段跳过，最终状态应为 COMPLETED 或特定阶段
        assert result.stage in (
            PipelineStage.COMPLETED,
            PipelineStage.DATA_CLEANING,
            PipelineStage.ALPHA_GENERATION,
            PipelineStage.BACKTEST_GATE,
            PipelineStage.EXECUTION,
        )

    def test_full_cycle_risk_only(self):
        """仅风控模式"""
        config = PipelineConfig(
            mode="dry_run",
            data_cleaning_enabled=False,
            alpha_enabled=False,
            backtest_gate_enabled=False,
            execution_enabled=False,
            risk_monitor_enabled=True,
        )
        orchestrator = PipelineOrchestrator(config=config)
        result = orchestrator.run_full_cycle(mode="dry_run")
        assert isinstance(result, PipelineResult)

    def test_full_cycle_all_disabled(self):
        """全部禁用时也应正常完成"""
        config = PipelineConfig(
            mode="dry_run",
            data_cleaning_enabled=False,
            alpha_enabled=False,
            backtest_gate_enabled=False,
            execution_enabled=False,
            risk_monitor_enabled=False,
        )
        orchestrator = PipelineOrchestrator(config=config)
        result = orchestrator.run_full_cycle(mode="dry_run")
        assert isinstance(result, PipelineResult)


# ============================================================
# 6. 异常处理测试
# ============================================================

class TestPipelineErrorHandling:
    """异常处理与 fail-closed"""

    def test_orchestrator_exception_caught(self):
        """编排器内部异常应被捕获并返回 FAILED 状态"""
        config = PipelineConfig(
            data_cleaning_enabled=True,
            alpha_enabled=True,
            backtest_gate_enabled=True,
            execution_enabled=True,
        )
        orchestrator = PipelineOrchestrator(config=config)

        # 模拟数据清洗阶段抛异常
        original_method = orchestrator._data_cleaning.run
        try:
            orchestrator._data_cleaning.run = MagicMock(side_effect=RuntimeError("模拟异常"))
            result = orchestrator.run_full_cycle(mode="dry_run")
            assert result.success is False
            assert result.stage == PipelineStage.FAILED
        finally:
            orchestrator._data_cleaning.run = original_method

    def test_run_full_cycle_empty_data(self):
        """空数据不应崩溃"""
        orchestrator = PipelineOrchestrator()
        # 注入空数据
        orchestrator._data_cleaning.run = MagicMock(return_value=([], PipelineResult(
            stage=PipelineStage.DATA_CLEANING, success=True, started_at=datetime.now(),
        )))
        result = orchestrator.run_full_cycle(mode="dry_run")
        assert isinstance(result, PipelineResult)


# ============================================================
# 7. PipelineStage 枚举测试
# ============================================================

class TestPipelineStage:
    def test_all_stages(self):
        assert PipelineStage.IDLE.value == "idle"
        assert PipelineStage.DATA_CLEANING.value == "data_cleaning"
        assert PipelineStage.ALPHA_GENERATION.value == "alpha_generation"
        assert PipelineStage.BACKTEST_GATE.value == "backtest_gate"
        assert PipelineStage.EXECUTION.value == "execution"
        assert PipelineStage.RISK_MONITOR.value == "risk_monitor"
        assert PipelineStage.COMPLETED.value == "completed"
        assert PipelineStage.FAILED.value == "failed"

    def test_stage_uniqueness(self):
        values = [s.value for s in PipelineStage]
        assert len(values) == len(set(values))


# ============================================================
# 8. 模块导入测试
# ============================================================

class TestPipelineImports:
    """__init__.py 导出测试"""

    def test_all_exports_available(self):
        from utils.pipeline import (
            AlphaPipeline,
            AlphaSignalResult,
            BacktestGate,
            BacktestGateResult,
            DataCleaningPipeline,
            DataQualityReport,
            ExecutionPipeline,
            ExecutionResult,
            PipelineConfig,
            PipelineOrchestrator,
            PipelineResult,
            PipelineStage,
            PipelineStatus,
            RiskAlert,
            RiskMonitor,
        )
        # 验证所有导出类可调用
        assert PipelineOrchestrator is not None
        assert PipelineStatus is not None
        assert DataCleaningPipeline is not None
        assert DataQualityReport is not None
        assert AlphaPipeline is not None
        assert AlphaSignalResult is not None
        assert BacktestGate is not None
        assert BacktestGateResult is not None
        assert ExecutionPipeline is not None
        assert ExecutionResult is not None
        assert RiskMonitor is not None
        assert RiskAlert is not None
        assert PipelineResult is not None
        assert PipelineStage is not None
        assert PipelineConfig is not None

    def test_module_import_works(self):
        """python -c "import utils.pipeline" 不报错"""
        import importlib

        import utils.pipeline
        importlib.reload(utils.pipeline)
        assert hasattr(utils.pipeline, "PipelineOrchestrator")


# ============================================================
# 9. DataCleaningPipeline 深度测试
# ============================================================

class TestDataCleaningPipeline:
    """数据清洗流水线深度测试"""

    def test_init_with_default_config(self):
        pipeline = DataCleaningPipeline()
        assert pipeline.config is not None

    def test_init_with_custom_config(self):
        config = PipelineConfig(min_quality_score=90.0)
        pipeline = DataCleaningPipeline(config=config)
        assert pipeline.config.min_quality_score == 90.0

    def test_run_with_empty_data(self):
        pipeline = DataCleaningPipeline()
        reports, result = pipeline.run(market_data={})
        assert len(reports) == 0
        assert result.success is True
        assert result.metrics["n_symbols"] == 0

    def test_run_with_none_data(self):
        pipeline = DataCleaningPipeline()
        reports, result = pipeline.run(market_data=None)
        assert isinstance(reports, list)
        assert isinstance(result, PipelineResult)

    def test_run_with_mock_data(self, tmp_path):
        """使用模拟数据测试清洗流程"""
        # 准备模拟数据
        mock_data = {
            "000001": {"price": 100.0, "volume": 1000000},
            "000002": {"price": 50.0, "volume": 500000},
        }
        config = PipelineConfig(
            report_dir=str(tmp_path),
            multi_source_check=False,
            min_quality_score=80.0,
        )
        pipeline = DataCleaningPipeline(config=config)
        reports, result = pipeline.run(market_data=mock_data)
        # 应正常返回 2 个报告
        assert len(reports) == 2
        assert result.success is True
        assert result.metrics["n_symbols"] == 2

    def test_calc_quality_score_perfect(self):
        """质量评分 - 完美数据应得 100 分"""
        pipeline = DataCleaningPipeline()
        score_info = pipeline._calc_quality_score(
            "000001", {}, {}, {}, {}
        )
        assert score_info["score"] == 100.0

    def test_calc_quality_score_with_outliers(self):
        """质量评分 - 异常值扣分"""
        pipeline = DataCleaningPipeline()
        score_info = pipeline._calc_quality_score(
            "000001",
            {},
            {"000001": ["z_score=3.5"]},
            {},
            {},
        )
        assert score_info["score"] == 90.0  # 100 - 10

    def test_calc_quality_score_with_gaps(self):
        """质量评分 - 缺失值扣分"""
        pipeline = DataCleaningPipeline()
        score_info = pipeline._calc_quality_score(
            "000001",
            {},
            {},
            {"000001": {"gap_days": 3}},
            {},
        )
        assert score_info["score"] == 85.0  # 100 - 15

    def test_calc_quality_score_below_zero(self):
        """质量评分不低于 0"""
        pipeline = DataCleaningPipeline()
        score_info = pipeline._calc_quality_score(
            "000001",
            {"000001": {"deviation_pct": 10.0}},
            {"000001": ["z_score=3.5", "iqr_outlier"]},
            {"000001": {"gap_days": 10}},
            {"000001": {"allowed": False}},
        )
        assert score_info["score"] >= 0.0

    def test_extract_price_from_dict(self):
        pipeline = DataCleaningPipeline()
        price = pipeline._extract_price({"price": 123.45})
        assert price == 123.45

    def test_extract_price_from_alternative_keys(self):
        pipeline = DataCleaningPipeline()
        price = pipeline._extract_price({"current": 67.89})
        assert price == 67.89

    def test_extract_price_none(self):
        pipeline = DataCleaningPipeline()
        price = pipeline._extract_price({"name": "test"})
        assert price is None

    def test_detect_gaps_no_gaps(self):
        pipeline = DataCleaningPipeline()
        import numpy as np
        prices = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        assert pipeline._detect_gaps(prices) == 0

    def test_detect_gaps_with_gaps(self):
        pipeline = DataCleaningPipeline()
        import numpy as np
        prices = np.array([1.0, np.nan, np.nan, 4.0, 5.0])
        assert pipeline._detect_gaps(prices) == 2

    def test_detect_gaps_empty(self):
        pipeline = DataCleaningPipeline()
        import numpy as np
        assert pipeline._detect_gaps(np.array([])) == 0


# ============================================================
# 10. AlphaPipeline 深度测试
# ============================================================

class TestAlphaPipeline:
    """Alpha 信号流水线深度测试"""

    def test_init_with_default_config(self):
        pipeline = AlphaPipeline()
        assert pipeline.config is not None

    def test_init_with_custom_config(self):
        config = PipelineConfig(alpha_model="lightgbm")
        pipeline = AlphaPipeline(config=config)
        assert pipeline.config.alpha_model == "lightgbm"

    def test_run_with_disabled_alpha(self):
        """Alpha 禁用时也应正常返回"""
        config = PipelineConfig(alpha_enabled=False)
        pipeline = AlphaPipeline(config=config)
        signal, result = pipeline.run()
        assert isinstance(signal, AlphaSignalResult)
        assert result.success is True

    def test_run_local_factors(self):
        """本地因子回退应返回有效信号结果"""
        pipeline = AlphaPipeline()
        signal = pipeline._run_local_factors(symbols=["000001", "000002"])
        assert isinstance(signal, AlphaSignalResult)
        assert signal.model_name == "local_factors"
        assert signal.n_stocks >= 0

    def test_run_local_factors_empty_symbols(self):
        pipeline = AlphaPipeline()
        signal = pipeline._run_local_factors(symbols=[])
        assert signal.n_stocks == 0
        assert signal.signals == {}

    def test_normalize_signals_empty(self):
        pipeline = AlphaPipeline()
        assert pipeline._normalize_signals({}) == {}

    def test_normalize_signals_basic(self):
        pipeline = AlphaPipeline()
        signals = {"A": 100.0, "B": -50.0, "C": 0.0}
        normalized = pipeline._normalize_signals(signals)
        assert len(normalized) == 3
        # 信号应在 [-1, 1] 范围内
        for v in normalized.values():
            assert -1.0 <= v <= 1.0

    def test_normalize_signals_uniform(self):
        pipeline = AlphaPipeline()
        signals = {"A": 1.0, "B": 1.0, "C": 1.0}
        normalized = pipeline._normalize_signals(signals)
        for v in normalized.values():
            assert v == 1.0

    def test_check_retrain_needed_first_time(self):
        """首次运行需要重训"""
        pipeline = AlphaPipeline()
        assert pipeline._check_retrain_needed() is True

    def test_select_model_auto_default(self):
        pipeline = AlphaPipeline()
        model = pipeline._select_model()
        assert model in ("lightgbm", "transformer", "auto")


# ============================================================
# 11. BacktestGate 深度测试
# ============================================================

class TestBacktestGate:
    """回测验证网关深度测试"""

    def test_init_with_default_config(self):
        gate = BacktestGate()
        assert gate.config is not None

    def test_init_with_custom_config(self):
        config = PipelineConfig(min_ic=0.05, min_dsr=1.5)
        gate = BacktestGate(config=config)
        assert gate.config.min_ic == 0.05
        assert gate.config.min_dsr == 1.5

    def test_run_with_none_signal(self):
        gate = BacktestGate()
        gate_result, result = gate.run(None)
        assert gate_result.passed is False
        assert gate_result.rejection_reason == "无信号输入"

    def test_run_with_empty_signal(self):
        gate = BacktestGate()
        signal = AlphaSignalResult()
        gate_result, result = gate.run(signal)
        assert gate_result.passed is False
        assert gate_result.rejection_reason == "无信号输入"

    def test_run_with_signals(self):
        """有信号时应正常执行验证流程"""
        gate = BacktestGate()
        signal = AlphaSignalResult(
            signals={"A": 0.5, "B": 0.3, "C": 0.2},
            model_name="test",
            n_stocks=3,
        )
        gate_result, result = gate.run(signal, save_report=False)
        # 至少不崩溃
        assert isinstance(gate_result, BacktestGateResult)
        assert isinstance(result, PipelineResult)

    def test_estimate_ic_with_signals(self):
        gate = BacktestGate()
        signal = AlphaSignalResult(signals={"A": 0.5, "B": 0.3, "C": 0.2})
        ic = gate._estimate_ic(signal)
        # 未 mock _load_close_prices 时 fail-closed 返回 0.0
        assert ic >= 0.0

    def test_estimate_ic_empty(self):
        gate = BacktestGate()
        signal = AlphaSignalResult()
        assert gate._estimate_ic(signal) == 0.0

    def test_estimate_sharpe_with_signals(self):
        gate = BacktestGate()
        signal = AlphaSignalResult(signals={"A": 0.5, "B": 0.3, "C": 0.2})
        sharpe = gate._estimate_sharpe(signal)
        assert isinstance(sharpe, float)
        assert -1.0 <= sharpe <= 3.0

    def test_estimate_sharpe_empty(self):
        gate = BacktestGate()
        signal = AlphaSignalResult()
        assert gate._estimate_sharpe(signal) == 0.0

    def test_final_judgment_all_pass(self):
        gate = BacktestGate()
        gate_result = BacktestGateResult(
            ic=0.05, dsr=1.5, sharpe=1.2,
            walk_forward_passed=True, stress_test_passed=True,
        )
        result = gate._final_judgment(gate_result)
        assert result.passed is True

    def test_final_judgment_ic_fail(self):
        gate = BacktestGate()
        gate_result = BacktestGateResult(
            ic=0.01, dsr=1.5, sharpe=1.2,
            walk_forward_passed=True, stress_test_passed=True,
        )
        result = gate._final_judgment(gate_result)
        assert result.passed is False
        assert "IC" in result.rejection_reason

    def test_final_judgment_dsr_fail(self):
        gate = BacktestGate()
        gate_result = BacktestGateResult(
            ic=0.05, dsr=0.5, sharpe=0.3,
            walk_forward_passed=True, stress_test_passed=True,
        )
        result = gate._final_judgment(gate_result)
        assert result.passed is False
        assert "DSR" in result.rejection_reason

    def test_estimate_max_drawdown_default(self):
        gate = BacktestGate()
        # 生产代码改为真实计算, 传 None 会 AttributeError; 空信号返回 0.0
        assert gate._estimate_max_drawdown(AlphaSignalResult()) == 0.0


# ============================================================
# 12. ExecutionPipeline 深度测试
# ============================================================

class TestExecutionPipeline:
    """执行流水线深度测试"""

    def test_init_with_default_config(self):
        pipeline = ExecutionPipeline()
        assert pipeline.config is not None

    def test_init_with_custom_config(self):
        config = PipelineConfig(execution_dry_run=True, execution_default_algo="vwap")
        pipeline = ExecutionPipeline(config=config)
        assert pipeline.config.execution_dry_run is True
        assert pipeline.config.execution_default_algo == "vwap"

    def test_run_with_none_signal(self):
        """signal_result 为 None 时应优雅跳过"""
        pipeline = ExecutionPipeline()
        exec_result, result = pipeline.run(None, dry_run=True)
        assert exec_result.total_orders == 0
        assert result.success is True
        assert result.metrics.get("skip_reason") == "no_signal"

    def test_run_with_empty_signal(self):
        pipeline = ExecutionPipeline()
        signal = AlphaSignalResult()
        exec_result, result = pipeline.run(signal, dry_run=True)
        assert isinstance(exec_result, ExecutionResult)
        assert isinstance(result, PipelineResult)

    def test_run_with_signals_dry_run(self):
        """有信号时 dry_run 应生成订单"""
        pipeline = ExecutionPipeline()
        pipeline.config.execution_max_order_value = 10_000_000  # 确保订单不被金额限制过滤
        signal = AlphaSignalResult(
            signals={"000001": 0.8, "000002": 0.5, "000003": 0.3},
            model_name="test",
            n_stocks=3,
        )
        exec_result, result = pipeline.run(signal, dry_run=True)
        assert exec_result.total_orders > 0
        assert exec_result.dry_run is True
        assert result.success is True

    def test_generate_target_positions(self):
        pipeline = ExecutionPipeline()
        signal = AlphaSignalResult(signals={"A": 0.8, "B": 0.5, "C": 0.3})
        positions = pipeline._generate_target_positions(signal)
        assert len(positions) == 3
        # 权重应归一化到 target_exposure
        total_weight = sum(positions.values())
        assert abs(total_weight - pipeline.config.execution_target_exposure) < 0.01

    def test_generate_target_positions_no_long(self):
        """无多头信号时应返回空"""
        pipeline = ExecutionPipeline()
        signal = AlphaSignalResult(signals={"A": -0.5, "B": -0.3})
        positions = pipeline._generate_target_positions(signal)
        assert positions == {}

    def test_generate_orders_basic(self):
        pipeline = ExecutionPipeline()
        pipeline.config.execution_max_order_value = 10_000_000  # 确保订单不被金额限制过滤
        target = {"A": 0.5, "B": 0.3}
        current = {"A": 0.2, "B": 0.1}
        orders = pipeline._generate_orders(target, current)
        assert len(orders) == 2

    def test_generate_orders_no_change(self):
        """无变化时应返回空"""
        pipeline = ExecutionPipeline()
        target = {"A": 0.5}
        current = {"A": 0.5}
        orders = pipeline._generate_orders(target, current)
        assert len(orders) == 0

    def test_generate_orders_amount_limit(self):
        """金额超限订单应被过滤"""
        pipeline = ExecutionPipeline()
        # 设置极小的金额上限
        pipeline.config.execution_max_order_value = 1.0
        target = {"A": 0.5}
        current = {"A": 0.0}
        # 总资产 500 万 (positions.json meta.total_capital), diff 0.5 * 5M = 2.5M > 1
        orders = pipeline._generate_orders(target, current)
        # 2.5M > 1.0，所以被过滤
        assert len(orders) == 0

    def test_execute_orders_empty(self):
        pipeline = ExecutionPipeline()
        result = pipeline._execute_orders([], dry_run=True, confirmation_token=None, batch_id="test")
        assert result.total_orders == 0
        assert result.filled_orders == 0

    def test_execute_orders_dry_run(self):
        pipeline = ExecutionPipeline()
        orders = [{"symbol": "A", "direction": "BUY", "amount": 10000, "algo": "twap"}]
        result = pipeline._execute_orders(orders, dry_run=True, confirmation_token=None, batch_id="test")
        assert result.total_orders == 1
        assert result.filled_orders == 1
        assert result.fill_rate == 1.0

    def test_execute_orders_live_no_token(self):
        """实盘无 token 应拒绝"""
        pipeline = ExecutionPipeline()
        orders = [{"symbol": "A", "direction": "BUY", "amount": 10000, "algo": "twap"}]
        result = pipeline._execute_orders(orders, dry_run=False, confirmation_token=None, batch_id="test")
        assert result.total_orders == 1
        assert result.filled_orders == 0
        assert result.failed_orders == 1
        assert len(result.errors) > 0


# ============================================================
# 13. RiskMonitor 深度测试
# ============================================================

class TestRiskMonitor:
    """风控监控深度测试"""

    def test_init_with_default_config(self):
        monitor = RiskMonitor()
        assert monitor.config is not None

    def test_init_with_custom_config(self):
        config = PipelineConfig(
            max_daily_drawdown=0.03,
            kill_switch_l1_margin=0.40,
        )
        monitor = RiskMonitor(config=config)
        assert monitor.config.max_daily_drawdown == 0.03
        assert monitor.config.kill_switch_l1_margin == 0.40

    def test_run_check_returns_result(self):
        monitor = RiskMonitor()
        result = monitor.run_check()
        assert isinstance(result, PipelineResult)
        # 风控不阻断流水线
        assert result.stage == PipelineStage.RISK_MONITOR

    def test_get_latest_alert_initial(self):
        monitor = RiskMonitor()
        assert monitor.get_latest_alert() is None

    def test_get_alert_history_initial(self):
        monitor = RiskMonitor()
        assert monitor.get_alert_history() == []

    def test_check_overnight_gap_returns_none(self):
        monitor = RiskMonitor()
        assert monitor._check_overnight_gap() is None

    def test_handle_alert_stores_alert(self):
        monitor = RiskMonitor()
        alert = RiskAlert(level=1, source="test", message="测试告警")
        monitor._handle_alert(alert)
        assert monitor.get_latest_alert() is alert
        assert len(monitor.get_alert_history()) == 1


# ============================================================
# 14. Config 环境变量覆盖测试
# ============================================================

class TestConfigEnvOverrides:
    """配置环境变量覆盖"""

    def test_env_override_mode(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_MODE", "dry_run")
        from utils.pipeline import config as cfg_module
        cfg_module._config_loaded = False
        cfg_module._pipeline_config = None
        loaded = cfg_module.load_pipeline_config()
        assert loaded.mode == "dry_run"

    def test_env_override_alpha_enabled(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_ALPHA_ENABLED", "true")
        from utils.pipeline import config as cfg_module
        cfg_module._config_loaded = False
        cfg_module._pipeline_config = None
        loaded = cfg_module.load_pipeline_config()
        assert loaded.alpha_enabled is True

    def test_env_override_execution_enabled(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_EXECUTION_ENABLED", "true")
        from utils.pipeline import config as cfg_module
        cfg_module._config_loaded = False
        cfg_module._pipeline_config = None
        loaded = cfg_module.load_pipeline_config()
        assert loaded.execution_enabled is True
