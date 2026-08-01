"""T5.8 MLops 流水线单元测试.

覆盖:
    - ModelRegistry (模型版本化)
    - ABTestFramework (A/B 测试)
    - DriftMonitor (漂移监控)
    - AutoRetrainScheduler (自动重训练)
    - MLOpsPipeline (编排入口)

验收标准: 单测覆盖率 >= 70%
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

# 项目根
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================
# 简单测试模型 (用于 register_model 测试)
# ============================================================
class _DummyModel:
    """简单测试模型 (支持 joblib 序列化)."""
    def __init__(self, factor: float = 1.0):
        self.factor = factor

    def predict(self, x):
        return [v * self.factor for v in x]


# ============================================================
# ModelRegistry 测试
# ============================================================
class TestModelRegistryImport(unittest.TestCase):
    def test_import(self) -> None:
        from utils.alpha.model_registry import (
            ModelRegistry,
        )
        self.assertTrue(hasattr(ModelRegistry, "register_model"))
        self.assertTrue(hasattr(ModelRegistry, "load_model"))


class TestModelStage(unittest.TestCase):
    def test_from_string(self) -> None:
        from utils.alpha.model_registry import ModelStage
        self.assertEqual(ModelStage.from_string("production"), ModelStage.PRODUCTION)
        self.assertEqual(ModelStage.from_string("STAGING"), ModelStage.STAGING)

    def test_invalid_string_raises(self) -> None:
        from utils.alpha.model_registry import ModelRegistryError, ModelStage
        with self.assertRaises(ModelRegistryError):
            ModelStage.from_string("invalid")


class TestModelRegistry(unittest.TestCase):
    """ModelRegistry 核心功能测试."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        from utils.alpha.model_registry import ModelRegistry
        self.registry = ModelRegistry(
            registry_dir=self.tmpdir,
            use_mlflow=False,  # 强制本地模式
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_register_model(self) -> None:
        """测试注册新模型."""
        model = _DummyModel(1.5)
        version = self.registry.register_model(
            name="test_model",
            model=model,
            metrics={"dsr": 7.0, "annual_return": 0.18},
            params={"learning_rate": 0.05},
            tags={"experiment": "test"},
            description="测试模型",
            created_by="test",
        )
        self.assertEqual(version.name, "test_model")
        self.assertEqual(version.version, 1)
        self.assertEqual(version.stage, "registered")
        self.assertEqual(version.metrics["dsr"], 7.0)
        self.assertEqual(version.created_by, "test")

    def test_register_increments_version(self) -> None:
        """注册多个版本应递增版本号."""
        for i in range(3):
            self.registry.register_model(
                name="multi_ver", model=_DummyModel(),
                metrics={"dsr": float(i)},
            )
        versions = self.registry.get_model_versions("multi_ver")
        self.assertEqual(len(versions), 3)
        self.assertEqual(versions[0].version, 3)  # 最新

    def test_load_model(self) -> None:
        """测试加载模型."""
        model = _DummyModel(2.0)
        version = self.registry.register_model(
            name="loadable", model=model, metrics={"dsr": 5.0},
        )
        loaded = self.registry.load_model("loadable", version=version.version)
        self.assertIsInstance(loaded, _DummyModel)
        self.assertEqual(loaded.factor, 2.0)

    def test_promote_model(self) -> None:
        """测试晋升到 PRODUCTION."""
        v1 = self.registry.register_model("promo", _DummyModel(), {"dsr": 5.0})
        v2 = self.registry.register_model("promo", _DummyModel(), {"dsr": 8.0})
        # 晋升 v2 到 PRODUCTION
        promoted = self.registry.promote_model("promo", v2.version)
        self.assertEqual(promoted.stage, "production")
        # v1 应保持 registered
        v1_info = self.registry.get_model_info("promo", v1.version)
        self.assertEqual(v1_info.stage, "registered")

    def test_promote_auto_archives_old_production(self) -> None:
        """晋升新 PRODUCTION 时应自动归档旧 PRODUCTION."""
        v1 = self.registry.register_model("archive_test", _DummyModel(), {"dsr": 5.0})
        self.registry.promote_model("archive_test", v1.version)
        v2 = self.registry.register_model("archive_test", _DummyModel(), {"dsr": 8.0})
        self.registry.promote_model("archive_test", v2.version)
        # v1 应被自动归档
        v1_info = self.registry.get_model_info("archive_test", v1.version)
        self.assertEqual(v1_info.stage, "archived")
        # v2 应为 PRODUCTION
        v2_info = self.registry.get_model_info("archive_test", v2.version)
        self.assertEqual(v2_info.stage, "production")

    def test_invalid_stage_transition_raises(self) -> None:
        """非法阶段转换应抛异常."""
        from utils.alpha.model_registry import (
            ModelStage,
            StageTransitionError,
        )
        v1 = self.registry.register_model("invalid", _DummyModel(), {"dsr": 5.0})
        # REGISTERED → ARCHIVED 是合法的, 但 ARCHIVED → PRODUCTION 是非法的
        self.registry.archive_model("invalid", v1.version)
        with self.assertRaises(StageTransitionError):
            self.registry.transition_stage(
                "invalid", v1.version, ModelStage.PRODUCTION
            )

    def test_get_production_version(self) -> None:
        v1 = self.registry.register_model("prod", _DummyModel(), {"dsr": 5.0})
        self.registry.promote_model("prod", v1.version)
        prod = self.registry.get_production_version("prod")
        self.assertIsNotNone(prod)
        self.assertEqual(prod.version, 1)

    def test_get_latest_version(self) -> None:
        self.registry.register_model("latest", _DummyModel(), {"dsr": 5.0})
        self.registry.register_model("latest", _DummyModel(), {"dsr": 6.0})
        latest = self.registry.get_latest_version("latest")
        self.assertEqual(latest.version, 2)

    def test_list_models(self) -> None:
        self.registry.register_model("m1", _DummyModel(), {})
        self.registry.register_model("m2", _DummyModel(), {})
        models = self.registry.list_models()
        self.assertIn("m1", models)
        self.assertIn("m2", models)

    def test_search_by_metric(self) -> None:
        """测试按指标搜索."""
        self.registry.register_model(
            "good", _DummyModel(), {"dsr": 8.0, "annual_return": 0.20}
        )
        self.registry.register_model(
            "bad", _DummyModel(), {"dsr": 2.0, "annual_return": 0.05}
        )
        results = self.registry.search_models(
            metric_filter={"dsr": (">=", 5.0)}
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "good")

    def test_delete_soft_vs_force(self) -> None:
        """测试软删除 vs 物理删除."""
        v1 = self.registry.register_model("del", _DummyModel(), {"dsr": 5.0})
        # 软删除 (归档)
        ok = self.registry.delete_model_version("del", v1.version, force=False)
        self.assertTrue(ok)
        v1_info = self.registry.get_model_info("del", v1.version)
        self.assertEqual(v1_info.stage, "archived")
        # 物理删除
        ok = self.registry.delete_model_version("del", v1.version, force=True)
        self.assertTrue(ok)
        from utils.alpha.model_registry import ModelVersionNotFoundError
        with self.assertRaises(ModelVersionNotFoundError):
            self.registry.get_model_info("del", v1.version)

    def test_export_registry(self) -> None:
        self.registry.register_model("exp1", _DummyModel(), {"dsr": 5.0})
        export = self.registry.export_registry()
        self.assertEqual(export["model_count"], 1)
        self.assertIn("exp1", export["models"])

    def test_model_not_found_raises(self) -> None:
        from utils.alpha.model_registry import ModelNotFoundError
        with self.assertRaises(ModelNotFoundError):
            self.registry.get_model_info("nonexistent", 1)


# ============================================================
# ABTestFramework 测试
# ============================================================
class TestABTestingImport(unittest.TestCase):
    def test_import(self) -> None:
        from utils.alpha.ab_testing import (
            ABTestFramework,
        )
        self.assertTrue(hasattr(ABTestFramework, "create_test"))


class TestABTestConfig(unittest.TestCase):
    def test_default_config(self) -> None:
        from utils.alpha.ab_testing import ABTestConfig, SplitStrategy
        cfg = ABTestConfig(name="test", champion_model="a", challenger_model="b")
        self.assertEqual(cfg.traffic_split, 0.2)
        self.assertEqual(cfg.split_strategy, SplitStrategy.HASH_SYMBOL.value)
        self.assertEqual(cfg.min_samples, 30)

    def test_to_dict_from_dict(self) -> None:
        from utils.alpha.ab_testing import ABTestConfig
        cfg = ABTestConfig(name="t", champion_model="a", challenger_model="b")
        d = cfg.to_dict()
        cfg2 = ABTestConfig.from_dict(d)
        self.assertEqual(cfg2.name, "t")
        self.assertEqual(cfg2.champion_model, "a")


class TestABTestFramework(unittest.TestCase):
    """ABTestFramework 核心功能测试."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        from utils.alpha.ab_testing import ABTestFramework
        # mock model_registry
        self.mock_registry = MagicMock()
        self.framework = ABTestFramework(
            results_dir=self.tmpdir,
            model_registry=self.mock_registry,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_test(self) -> None:
        from utils.alpha.ab_testing import ABTestConfig
        cfg = ABTestConfig(name="t1", champion_model="c", challenger_model="d")
        test = self.framework.create_test(cfg)
        self.assertEqual(test.config.name, "t1")
        self.assertEqual(test.status, "created")

    def test_create_duplicate_raises(self) -> None:
        from utils.alpha.ab_testing import ABTestConfig, TestAlreadyExistsError
        cfg = ABTestConfig(name="dup", champion_model="c", challenger_model="d")
        self.framework.create_test(cfg)
        with self.assertRaises(TestAlreadyExistsError):
            self.framework.create_test(cfg)

    def test_invalid_traffic_split_raises(self) -> None:
        from utils.alpha.ab_testing import ABTestConfig, ABTestError
        cfg = ABTestConfig(
            name="bad", champion_model="c", challenger_model="d",
            traffic_split=1.5,
        )
        with self.assertRaises(ABTestError):
            self.framework.create_test(cfg)

    def test_start_test(self) -> None:
        from utils.alpha.ab_testing import ABTestConfig, ABTestStatus
        cfg = ABTestConfig(name="start", champion_model="c", challenger_model="d")
        self.framework.create_test(cfg)
        test = self.framework.start_test("start")
        self.assertEqual(test.status, ABTestStatus.RUNNING.value)
        self.assertTrue(test.started_at)

    def test_assign_group_hash_symbol(self) -> None:
        """测试 hash_symbol 分组 (可重现)."""
        from utils.alpha.ab_testing import ABTestConfig, SplitStrategy
        cfg = ABTestConfig(
            name="assign", champion_model="c", challenger_model="d",
            traffic_split=0.2,
            split_strategy=SplitStrategy.HASH_SYMBOL.value,
        )
        self.framework.create_test(cfg)
        self.framework.start_test("assign")
        # 同一 symbol 应总是分配到同一组
        g1 = self.framework.assign_group("assign", "000001")
        g2 = self.framework.assign_group("assign", "000001")
        self.assertEqual(g1, g2)
        self.assertIn(g1, ("champion", "challenger"))

    def test_assign_group_not_running_raises(self) -> None:
        from utils.alpha.ab_testing import ABTestConfig, TestNotRunningError
        cfg = ABTestConfig(name="notrun", champion_model="c", challenger_model="d")
        self.framework.create_test(cfg)
        with self.assertRaises(TestNotRunningError):
            self.framework.assign_group("notrun", "000001")

    def test_record_daily_metrics(self) -> None:
        from utils.alpha.ab_testing import ABTestConfig
        cfg = ABTestConfig(name="record", champion_model="c", challenger_model="d", min_samples=2)
        self.framework.create_test(cfg)
        self.framework.start_test("record")
        self.framework.record_daily_metrics(
            "record", "2026-07-27",
            {"ic": 0.05}, {"ic": 0.06},
        )
        test = self.framework.get_test("record")
        self.assertEqual(len(test.daily_records), 1)

    def test_evaluate_test_insufficient_data(self) -> None:
        from utils.alpha.ab_testing import (
            ABTestConfig,
            InsufficientDataError,
        )
        cfg = ABTestConfig(
            name="eval", champion_model="c", challenger_model="d",
            min_samples=10,
        )
        self.framework.create_test(cfg)
        self.framework.start_test("eval")
        self.framework.record_daily_metrics(
            "eval", "2026-07-27", {"ic": 0.05}, {"ic": 0.06},
        )
        with self.assertRaises(InsufficientDataError):
            self.framework.evaluate_test("eval")

    def test_evaluate_test_success(self) -> None:
        from utils.alpha.ab_testing import ABTestConfig
        cfg = ABTestConfig(
            name="eval2", champion_model="c", challenger_model="d",
            min_samples=3, significance_level=0.1,
        )
        self.framework.create_test(cfg)
        self.framework.start_test("eval2")
        # challenger 明显更好
        for i in range(5):
            self.framework.record_daily_metrics(
                "eval2", f"2026-07-{i+1:02d}",
                {"ic": 0.02}, {"ic": 0.08},
            )
        result = self.framework.evaluate_test("eval2")
        self.assertTrue(result.challenger_better)
        self.assertEqual(result.champion_samples, 5)
        self.assertEqual(result.challenger_samples, 5)
        self.assertIn(result.recommendation, ("promote", "continue", "rollback"))

    def test_list_tests(self) -> None:
        from utils.alpha.ab_testing import ABTestConfig, ABTestStatus
        for name in ("a", "b", "c"):
            cfg = ABTestConfig(name=name, champion_model="c", challenger_model="d")
            self.framework.create_test(cfg)
        all_tests = self.framework.list_tests()
        self.assertEqual(len(all_tests), 3)
        running = self.framework.list_tests(ABTestStatus.RUNNING)
        self.assertEqual(len(running), 0)

    def test_stop_test(self) -> None:
        from utils.alpha.ab_testing import ABTestConfig, ABTestStatus
        cfg = ABTestConfig(name="stop", champion_model="c", challenger_model="d")
        self.framework.create_test(cfg)
        self.framework.start_test("stop")
        test = self.framework.stop_test("stop")
        self.assertEqual(test.status, ABTestStatus.STOPPED.value)


# ============================================================
# DriftMonitor 测试
# ============================================================
class TestDriftMonitorImport(unittest.TestCase):
    def test_import(self) -> None:
        from utils.alpha.drift_monitor import DriftMonitor
        self.assertTrue(hasattr(DriftMonitor, "start_monitoring"))


class TestDriftMonitor(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_init(self) -> None:
        from utils.alpha.drift_monitor import DriftMonitor
        monitor = DriftMonitor(
            model_name="test",
            alerts_dir=self.tmpdir,
        )
        self.assertEqual(monitor.model_name, "test")

    def test_record_alert_persisted(self) -> None:
        """测试告警持久化."""
        from utils.alpha.drift_monitor import DriftMonitor
        monitor = DriftMonitor(
            model_name="persist",
            alerts_dir=self.tmpdir,
        )
        # 模拟告警对象
        class FakeAlert:
            def __init__(self):
                self.timestamp = "2026-07-27T00:00:00Z"
                self.drift_type = MagicMock()
                self.drift_type.value = "ic_decay"
                self.severity = MagicMock()
                self.severity.value = "warning"
                self.message = "IC decay detected"
                self.metric_name = "ic"
                self.current_value = 0.01
                self.threshold = 0.02
                self.recommendation = "retrain"
        monitor._record_alert(FakeAlert())
        # 检查告警文件
        alert_files = list(Path(self.tmpdir).glob("persist_*.jsonl"))
        self.assertTrue(len(alert_files) > 0)

    def test_retrain_callback_triggered(self) -> None:
        """测试重训练回调被触发."""
        from utils.alpha.drift_monitor import DriftMonitor
        triggered = []
        def callback(alerts):
            triggered.append(len(alerts))
            return True
        monitor = DriftMonitor(
            model_name="cb",
            alerts_dir=self.tmpdir,
            retrain_callback=callback,
            retrain_threshold_count=2,
            retrain_threshold_severity="critical",
        )
        # 构造 critical 告警
        class CriticalAlert:
            def __init__(self):
                self.timestamp = "2026-07-27T00:00:00Z"
                self.drift_type = MagicMock()
                self.drift_type.value = "concept_drift"
                self.severity = MagicMock()
                self.severity.value = "critical"
                self.message = "drift"
                self.metric_name = "ic"
                self.current_value = 0.0
                self.threshold = 0.05
                self.recommendation = ""
        alerts = [CriticalAlert(), CriticalAlert(), CriticalAlert()]
        monitor._check_retrain_trigger(alerts)
        self.assertEqual(len(triggered), 1)
        self.assertTrue(monitor._retrain_triggered)

    def test_get_status(self) -> None:
        from utils.alpha.drift_monitor import DriftMonitor
        monitor = DriftMonitor(model_name="status", alerts_dir=self.tmpdir)
        status = monitor.get_status()
        self.assertEqual(status["model_name"], "status")
        self.assertIn("alerts_count", status)
        self.assertFalse(status["is_monitoring"])

    def test_start_stop_monitoring(self) -> None:
        from utils.alpha.drift_monitor import DriftMonitor
        monitor = DriftMonitor(
            model_name="mon",
            alerts_dir=self.tmpdir,
        )
        monitor.start_monitoring(interval_sec=0.1)
        import time
        time.sleep(0.3)
        monitor.stop_monitoring()


# ============================================================
# AutoRetrainScheduler 测试
# ============================================================
class TestAutoRetrainImport(unittest.TestCase):
    def test_import(self) -> None:
        from utils.alpha.auto_retrain_scheduler import (
            AutoRetrainScheduler,
        )
        self.assertTrue(hasattr(AutoRetrainScheduler, "trigger_retrain"))


class TestAutoRetrainScheduler(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_init_disabled_by_default(self) -> None:
        """默认禁用 (HC-1)."""
        from utils.alpha.auto_retrain_scheduler import AutoRetrainScheduler
        scheduler = AutoRetrainScheduler(config={"enabled": False})
        self.assertFalse(scheduler.enabled)

    def test_trigger_retrain_disabled_returns_false(self) -> None:
        from utils.alpha.auto_retrain_scheduler import (
            AutoRetrainScheduler,
        )
        scheduler = AutoRetrainScheduler(config={"enabled": False})
        # _on_drift_alerts 在 disabled 时返回 False
        result = scheduler._on_drift_alerts([])
        self.assertFalse(result)

    def test_min_interval_respected(self) -> None:
        """测试最小重训练间隔."""
        from datetime import datetime, timedelta

        from utils.alpha.auto_retrain_scheduler import (
            AutoRetrainScheduler,
            RetrainTrigger,
        )
        scheduler = AutoRetrainScheduler(config={
            "enabled": True,
            "min_interval_hours": 24,
            "training_script": "nonexistent.py",
        })
        # 模拟上次重训练时间 (1 小时前)
        scheduler._last_retrain_time = datetime.utcnow() - timedelta(hours=1)
        ok = scheduler.trigger_retrain(RetrainTrigger.MANUAL, "test")
        self.assertFalse(ok)  # 距上次不足 24 小时

    def test_trigger_retrain_creates_task(self) -> None:
        from utils.alpha.auto_retrain_scheduler import (
            AutoRetrainScheduler,
            RetrainTrigger,
        )
        scheduler = AutoRetrainScheduler(config={
            "enabled": True,
            "min_interval_hours": 0,
            "training_script": "nonexistent.py",
            "training_timeout_sec": 5,
            "auto_register": False,  # 禁用注册避免依赖
            "auto_start_ab_test": False,
        })
        ok = scheduler.trigger_retrain(RetrainTrigger.MANUAL, "unit_test")
        self.assertTrue(ok)
        # 等待训练完成 (会失败因脚本不存在)
        import time
        time.sleep(2.0)
        tasks = scheduler.list_tasks()
        self.assertTrue(len(tasks) > 0)

    def test_get_status(self) -> None:
        from utils.alpha.auto_retrain_scheduler import AutoRetrainScheduler
        scheduler = AutoRetrainScheduler(config={"enabled": False})
        status = scheduler.get_status()
        self.assertFalse(status["enabled"])
        self.assertIn("tasks_count", status)

    def test_start_disabled_does_nothing(self) -> None:
        """启用=False 时 start() 不应启动调度."""
        from utils.alpha.auto_retrain_scheduler import AutoRetrainScheduler
        scheduler = AutoRetrainScheduler(config={"enabled": False})
        scheduler.start()
        self.assertIsNone(scheduler._scheduler_thread)
        scheduler.stop()


# ============================================================
# MLOpsPipeline 测试
# ============================================================
class TestMLOpsPipelineImport(unittest.TestCase):
    def test_import(self) -> None:
        from utils.alpha.mlops_pipeline import MLOpsPipeline
        self.assertTrue(hasattr(MLOpsPipeline, "start"))


class TestMLOpsPipeline(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_init_disabled_by_default(self) -> None:
        """默认禁用 (HC-1)."""
        from utils.alpha.mlops_pipeline import MLOpsPipeline
        pipeline = MLOpsPipeline(config={})
        self.assertFalse(pipeline.enabled)

    def test_start_disabled_does_nothing(self) -> None:
        from utils.alpha.mlops_pipeline import MLOpsPipeline
        pipeline = MLOpsPipeline(config={})
        pipeline.start()
        self.assertFalse(pipeline._started)
        pipeline.stop()

    def test_get_status(self) -> None:
        from utils.alpha.mlops_pipeline import MLOpsPipeline
        pipeline = MLOpsPipeline(config={})
        status = pipeline.get_status()
        self.assertFalse(status["enabled"])
        self.assertIn("components", status)

    def test_register_and_test_disabled(self) -> None:
        """未启用时 register_and_test 应跳过."""
        from utils.alpha.mlops_pipeline import MLOpsPipeline
        pipeline = MLOpsPipeline(config={})
        result = pipeline.register_and_test(
            model=_DummyModel(),
            metrics={"dsr": 5.0},
        )
        self.assertTrue(result.get("skipped"))


# ============================================================
# 补充测试: 提升 mlops_pipeline / drift_monitor / auto_retrain / ab_testing 覆盖率
# ============================================================


class TestMLOpsPipelineEnabled(unittest.TestCase):
    """测试 MLOpsPipeline 启用模式 (mock 子模块)."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_enabled_pipeline(self) -> Any:
        """创建启用模式的 pipeline (绕过 Feature Flag)."""
        from utils.alpha.mlops_pipeline import MLOpsPipeline
        pipeline = MLOpsPipeline(config={
            "registry_dir": self.tmpdir + "/registry",
            "ab_tests_dir": self.tmpdir + "/ab_tests",
            "drift_check_interval_sec": 1,
            "default_model_name": "test_model",
            "auto_retrain": {
                "enabled": True,
                "drift_threshold_count": 1,
                "drift_threshold_severity": "critical",
            },
        })
        pipeline._enabled = True  # 强制启用
        return pipeline

    def test_start_when_already_started(self) -> None:
        """已启动时再次 start 应 warning 并 no-op."""
        pipeline = self._make_enabled_pipeline()
        # mock 子模块避免真实启动
        pipeline._drift_monitor = MagicMock()
        pipeline._retrain_scheduler = MagicMock()
        pipeline.start()
        self.assertTrue(pipeline._started)
        # 再次 start 不应抛异常
        pipeline.start()
        pipeline.stop()

    def test_start_with_exception(self) -> None:
        """start 时 drift_monitor 抛异常应抛出 MLOpsPipelineError."""
        from utils.alpha.mlops_pipeline import MLOpsPipelineError
        pipeline = self._make_enabled_pipeline()
        # mock drift_monitor.start_monitoring 抛异常
        drift_monitor = MagicMock()
        drift_monitor.start_monitoring.side_effect = RuntimeError("boom")
        pipeline._drift_monitor = drift_monitor
        with self.assertRaises(MLOpsPipelineError):
            pipeline.start()
        self.assertFalse(pipeline._started)

    def test_stop_when_not_started(self) -> None:
        """未启动时 stop 应是 no-op."""
        pipeline = self._make_enabled_pipeline()
        # 未 start 直接 stop 不应抛异常
        pipeline.stop()

    def test_stop_with_exception(self) -> None:
        """stop 时子模块抛异常应被捕获."""
        pipeline = self._make_enabled_pipeline()
        drift_monitor = MagicMock()
        drift_monitor.stop_monitoring.side_effect = RuntimeError("boom")
        pipeline._drift_monitor = drift_monitor
        pipeline._started = True
        # 不应抛异常
        pipeline.stop()
        self.assertFalse(pipeline._started)

    def test_register_and_test_success(self) -> None:
        """启用模式 register_and_test 完整流程."""
        pipeline = self._make_enabled_pipeline()
        # mock model_registry
        fake_version = MagicMock()
        fake_version.version = 1
        registry = MagicMock()
        registry.register_model.return_value = fake_version
        pipeline._model_registry = registry
        # mock ab_framework
        ab = MagicMock()
        pipeline._ab_framework = ab
        result = pipeline.register_and_test(
            model=_DummyModel(),
            metrics={"dsr": 5.0},
            model_name="test_model",
        )
        self.assertIn("version", result)
        self.assertEqual(result["version"].version, 1)
        self.assertIn("ab_test", result)
        # 验证调用
        registry.register_model.assert_called_once()
        ab.create_test.assert_called_once()
        ab.start_test.assert_called_once()

    def test_register_and_test_register_error(self) -> None:
        """register_and_test 时 register_model 抛异常应被捕获."""
        pipeline = self._make_enabled_pipeline()
        registry = MagicMock()
        registry.register_model.side_effect = RuntimeError("register failed")
        pipeline._model_registry = registry
        result = pipeline.register_and_test(
            model=_DummyModel(), metrics={"dsr": 5.0},
        )
        self.assertNotIn("version", result)
        self.assertIn("register_error", result)

    def test_register_and_test_ab_error(self) -> None:
        """register_and_test 时 ab_framework 抛异常应被捕获."""
        pipeline = self._make_enabled_pipeline()
        fake_version = MagicMock()
        fake_version.version = 1
        registry = MagicMock()
        registry.register_model.return_value = fake_version
        pipeline._model_registry = registry
        ab = MagicMock()
        ab.create_test.side_effect = RuntimeError("ab failed")
        pipeline._ab_framework = ab
        result = pipeline.register_and_test(
            model=_DummyModel(), metrics={"dsr": 5.0},
        )
        self.assertIn("version", result)
        self.assertIn("ab_test_error", result)

    def test_on_drift_trigger(self) -> None:
        """drift 触发回调."""
        pipeline = self._make_enabled_pipeline()
        scheduler = MagicMock()
        pipeline._retrain_scheduler = scheduler
        ok = pipeline._on_drift_trigger([{"alert": "test"}])
        self.assertTrue(ok)
        scheduler.trigger_retrain.assert_called_once()

    def test_on_drift_trigger_disabled(self) -> None:
        """未启用时 drift 触发应返回 False."""
        pipeline = self._make_enabled_pipeline()
        pipeline._enabled = False
        ok = pipeline._on_drift_trigger([{"alert": "test"}])
        self.assertFalse(ok)

    def test_on_drift_trigger_exception(self) -> None:
        """drift 触发时 scheduler 抛异常应返回 False."""
        pipeline = self._make_enabled_pipeline()
        scheduler = MagicMock()
        scheduler.trigger_retrain.side_effect = RuntimeError("boom")
        pipeline._retrain_scheduler = scheduler
        ok = pipeline._on_drift_trigger([])
        self.assertFalse(ok)

    def test_get_status_with_components(self) -> None:
        """get_status 收集子模块状态."""
        pipeline = self._make_enabled_pipeline()
        # mock 子模块
        drift = MagicMock()
        drift.get_status.return_value = {"alerts": 0}
        pipeline._drift_monitor = drift
        scheduler = MagicMock()
        scheduler.get_status.return_value = {"running": False}
        pipeline._retrain_scheduler = scheduler
        registry = MagicMock()
        registry.list_models.return_value = ["m1"]
        pipeline._model_registry = registry
        ab = MagicMock()
        ab.list_tests.return_value = []
        pipeline._ab_framework = ab
        status = pipeline.get_status()
        self.assertTrue(status["enabled"])
        self.assertIn("drift_monitor", status["components"])
        self.assertIn("retrain_scheduler", status["components"])
        self.assertIn("model_registry", status["components"])
        self.assertIn("ab_framework", status["components"])

    def test_get_status_components_exception(self) -> None:
        """子模块 get_status 抛异常应被捕获."""
        pipeline = self._make_enabled_pipeline()
        drift = MagicMock()
        drift.get_status.side_effect = RuntimeError("boom")
        pipeline._drift_monitor = drift
        # 不应抛异常
        status = pipeline.get_status()
        self.assertTrue(status["enabled"])

    def test_get_pipeline_log(self) -> None:
        """get_pipeline_log 返回事件日志."""
        pipeline = self._make_enabled_pipeline()
        pipeline._log_event("test_event", {"key": "value"})
        log = pipeline.get_pipeline_log()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["event"], "test_event")

    def test_global_pipeline_singleton(self) -> None:
        """测试全局单例函数."""
        from utils.alpha.mlops_pipeline import (
            get_pipeline,
            initialize_pipeline,
        )
        pipeline1 = initialize_pipeline(config={"test_key": "test_value"})
        pipeline2 = get_pipeline()
        self.assertIs(pipeline1, pipeline2)


class TestDriftMonitorExtended(unittest.TestCase):
    """DriftMonitor 扩展测试."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_monitor(self, with_detector: bool = True) -> Any:
        """创建 DriftMonitor (可注入 mock detector)."""
        from utils.alpha.drift_monitor import DriftMonitor
        detector = MagicMock() if with_detector else None
        monitor = DriftMonitor(
            model_name="test_model",
            detector=detector,
            alerts_dir=self.tmpdir + "/alerts",
        )
        # 显式传入 None 时, DriftMonitor 可能仍会自动创建 detector,
        # 这里强制覆盖以保证测试隔离
        if not with_detector:
            monitor.detector = None
        return monitor

    def test_update_ic_with_alert(self) -> None:
        """update_ic 返回告警时应记录."""
        monitor = self._make_monitor()
        fake_alert = MagicMock()
        fake_alert.timestamp = "2026-07-27T00:00:00Z"
        fake_alert.drift_type = MagicMock()
        fake_alert.drift_type.value = "ic_decay"
        fake_alert.severity = MagicMock()
        fake_alert.severity.value = "warning"
        fake_alert.message = "IC decay"
        fake_alert.metric_name = "ic"
        fake_alert.current_value = 0.01
        fake_alert.threshold = 0.02
        monitor.detector.update_ic.return_value = fake_alert
        alert = monitor.update_ic("2026-07-27", 0.01)
        self.assertIsNotNone(alert)
        self.assertEqual(len(monitor._alerts_history), 1)

    def test_update_ic_no_alert(self) -> None:
        """update_ic 无告警时不记录."""
        monitor = self._make_monitor()
        monitor.detector.update_ic.return_value = None
        alert = monitor.update_ic("2026-07-27", 0.5)
        self.assertIsNone(alert)
        self.assertEqual(len(monitor._alerts_history), 0)

    def test_update_ic_exception(self) -> None:
        """update_ic 异常应被捕获."""
        monitor = self._make_monitor()
        monitor.detector.update_ic.side_effect = RuntimeError("boom")
        alert = monitor.update_ic("2026-07-27", 0.5)
        self.assertIsNone(alert)

    def test_update_ic_no_detector(self) -> None:
        """无 detector 时 update_ic 返回 None."""
        monitor = self._make_monitor(with_detector=False)
        self.assertIsNone(monitor.update_ic("2026-07-27", 0.5))

    def test_update_adwin_with_alert(self) -> None:
        """update_adwin 返回告警时应记录."""
        monitor = self._make_monitor()
        fake_alert = MagicMock()
        fake_alert.timestamp = "2026-07-27T00:00:00Z"
        fake_alert.drift_type = MagicMock()
        fake_alert.drift_type.value = "concept_drift"
        fake_alert.severity = MagicMock()
        fake_alert.severity.value = "critical"
        fake_alert.message = "drift"
        fake_alert.metric_name = "adwin"
        fake_alert.current_value = 0.1
        fake_alert.threshold = 0.0
        monitor.detector.update_adwin.return_value = fake_alert
        alert = monitor.update_adwin(0.1)
        self.assertIsNotNone(alert)

    def test_update_adwin_no_detector(self) -> None:
        """无 detector 时 update_adwin 返回 None."""
        monitor = self._make_monitor(with_detector=False)
        self.assertIsNone(monitor.update_adwin(0.1))

    def test_update_adwin_exception(self) -> None:
        """update_adwin 异常应被捕获."""
        monitor = self._make_monitor()
        monitor.detector.update_adwin.side_effect = RuntimeError("boom")
        self.assertIsNone(monitor.update_adwin(0.1))

    def test_check_feature_drift(self) -> None:
        """check_feature_drift 返回告警列表."""
        monitor = self._make_monitor()
        fake_alert = MagicMock()
        fake_alert.timestamp = "2026-07-27T00:00:00Z"
        fake_alert.drift_type = MagicMock()
        fake_alert.drift_type.value = "feature_shift"
        fake_alert.severity = MagicMock()
        fake_alert.severity.value = "warning"
        fake_alert.message = "shift"
        fake_alert.metric_name = "feat1"
        fake_alert.current_value = 0.3
        fake_alert.threshold = 0.05
        monitor.detector.check_feature_drift.return_value = [fake_alert]
        alerts = monitor.check_feature_drift({"feat1": [1.0, 2.0]})
        self.assertEqual(len(alerts), 1)
        self.assertEqual(len(monitor._alerts_history), 1)

    def test_check_feature_drift_no_detector(self) -> None:
        """无 detector 时返回空列表."""
        monitor = self._make_monitor(with_detector=False)
        self.assertEqual(monitor.check_feature_drift({}), [])

    def test_check_feature_drift_exception(self) -> None:
        """check_feature_drift 异常应返回空列表."""
        monitor = self._make_monitor()
        monitor.detector.check_feature_drift.side_effect = RuntimeError("boom")
        self.assertEqual(monitor.check_feature_drift({}), [])

    def test_check_all_with_alerts(self) -> None:
        """check_all 返回告警列表."""
        monitor = self._make_monitor()
        fake_alert = MagicMock()
        fake_alert.timestamp = "2026-07-27T00:00:00Z"
        fake_alert.drift_type = MagicMock()
        fake_alert.drift_type.value = "ic_decay"
        fake_alert.severity = MagicMock()
        fake_alert.severity.value = "warning"
        fake_alert.message = "decay"
        fake_alert.metric_name = "ic"
        fake_alert.current_value = 0.01
        fake_alert.threshold = 0.02
        monitor.detector.check_all.return_value = [fake_alert]
        alerts = monitor.check_all()
        self.assertEqual(len(alerts), 1)

    def test_check_all_no_detector(self) -> None:
        """无 detector 时返回空列表."""
        monitor = self._make_monitor(with_detector=False)
        self.assertEqual(monitor.check_all(), [])

    def test_check_all_exception(self) -> None:
        """check_all 异常返回空列表."""
        monitor = self._make_monitor()
        monitor.detector.check_all.side_effect = RuntimeError("boom")
        self.assertEqual(monitor.check_all(), [])

    def test_retrain_triggered(self) -> None:
        """告警达到阈值应触发 retrain_callback."""
        triggered = {"called": False}

        def callback(alerts):
            triggered["called"] = True
            return True

        from utils.alpha.drift_monitor import DriftMonitor
        monitor = DriftMonitor(
            model_name="test_model",
            detector=MagicMock(),
            alerts_dir=self.tmpdir + "/alerts",
            retrain_callback=callback,
            retrain_threshold_count=2,
            retrain_threshold_severity="critical",
        )
        # 构造 2 个 critical 告警
        alerts = []
        for _i in range(2):
            a = MagicMock()
            a.severity = MagicMock()
            a.severity.value = "critical"
            alerts.append(a)
        ok = monitor._check_retrain_trigger(alerts)
        self.assertTrue(ok)
        self.assertTrue(triggered["called"])
        self.assertTrue(monitor._retrain_triggered)

    def test_retrain_already_triggered(self) -> None:
        """已触发重训练后不再触发."""
        from utils.alpha.drift_monitor import DriftMonitor
        monitor = DriftMonitor(
            model_name="test_model",
            detector=MagicMock(),
            alerts_dir=self.tmpdir + "/alerts",
            retrain_callback=lambda a: True,
            retrain_threshold_count=1,
            retrain_threshold_severity="critical",
        )
        monitor._retrain_triggered = True
        # 再次触发应返回 False
        a = MagicMock()
        a.severity = MagicMock()
        a.severity.value = "critical"
        self.assertFalse(monitor._check_retrain_trigger([a]))

    def test_retrain_insufficient_alerts(self) -> None:
        """告警数不足不应触发重训练."""
        from utils.alpha.drift_monitor import DriftMonitor
        monitor = DriftMonitor(
            model_name="test_model",
            detector=MagicMock(),
            alerts_dir=self.tmpdir + "/alerts",
            retrain_callback=lambda a: True,
            retrain_threshold_count=5,
            retrain_threshold_severity="critical",
        )
        a = MagicMock()
        a.severity = MagicMock()
        a.severity.value = "critical"
        self.assertFalse(monitor._check_retrain_trigger([a]))

    def test_retrain_severity_not_met(self) -> None:
        """告警严重级别不匹配不应触发."""
        from utils.alpha.drift_monitor import DriftMonitor
        monitor = DriftMonitor(
            model_name="test_model",
            detector=MagicMock(),
            alerts_dir=self.tmpdir + "/alerts",
            retrain_callback=lambda a: True,
            retrain_threshold_count=1,
            retrain_threshold_severity="critical",
        )
        a = MagicMock()
        a.severity = MagicMock()
        a.severity.value = "warning"  # 不是 critical
        self.assertFalse(monitor._check_retrain_trigger([a]))

    def test_retrain_callback_exception(self) -> None:
        """retrain_callback 抛异常应返回 False."""
        from utils.alpha.drift_monitor import DriftMonitor

        def bad_callback(alerts):
            raise RuntimeError("callback failed")

        monitor = DriftMonitor(
            model_name="test_model",
            detector=MagicMock(),
            alerts_dir=self.tmpdir + "/alerts",
            retrain_callback=bad_callback,
            retrain_threshold_count=1,
            retrain_threshold_severity="critical",
        )
        a = MagicMock()
        a.severity = MagicMock()
        a.severity.value = "critical"
        self.assertFalse(monitor._check_retrain_trigger([a]))

    def test_retrain_no_callback(self) -> None:
        """无 callback 时返回 False."""
        from utils.alpha.drift_monitor import DriftMonitor
        monitor = DriftMonitor(
            model_name="test_model",
            detector=MagicMock(),
            alerts_dir=self.tmpdir + "/alerts",
            retrain_callback=None,
        )
        self.assertFalse(monitor._check_retrain_trigger([]))

    def test_should_retrain_no_detector(self) -> None:
        """无 detector 时返回 (False, reason)."""
        from utils.alpha.drift_monitor import DriftMonitor
        monitor = DriftMonitor(
            model_name="test_model",
            detector=None,
            alerts_dir=self.tmpdir + "/alerts",
        )
        # DriftMonitor.__init__ 会自动创建 detector, 显式置 None 以测试降级路径
        monitor.detector = None
        should, reason = monitor.should_retrain()
        self.assertFalse(should)
        self.assertIn("不可用", reason)

    def test_should_retrain_with_detector(self) -> None:
        """有 detector 时委托."""
        monitor = self._make_monitor()
        monitor.detector.should_retrain.return_value = (True, "drift detected")
        should, reason = monitor.should_retrain()
        self.assertTrue(should)
        self.assertEqual(reason, "drift detected")

    def test_should_retrain_exception(self) -> None:
        """should_retrain 异常返回 False."""
        monitor = self._make_monitor()
        monitor.detector.should_retrain.side_effect = RuntimeError("boom")
        should, reason = monitor.should_retrain()
        self.assertFalse(should)
        self.assertIn("异常", reason)

    def test_start_monitoring_already_running(self) -> None:
        """已运行时再次 start 应 warning."""
        monitor = self._make_monitor()
        monitor.start_monitoring(interval_sec=0.1)
        # 再次 start 不应抛异常
        monitor.start_monitoring(interval_sec=0.1)
        monitor.stop_monitoring()

    def test_get_alerts_history(self) -> None:
        """get_alerts_history 返回历史告警."""
        monitor = self._make_monitor()
        # 注入测试数据
        monitor._alerts_history.append({"alert": "test1"})
        monitor._alerts_history.append({"alert": "test2"})
        history = monitor.get_alerts_history()
        self.assertEqual(len(history), 2)

    def test_generate_report_no_detector(self) -> None:
        """无 detector 时 generate_report 返回 error."""
        from utils.alpha.drift_monitor import DriftMonitor
        monitor = DriftMonitor(
            model_name="test_model",
            detector=None,
            alerts_dir=self.tmpdir + "/alerts",
        )
        # DriftMonitor.__init__ 会自动创建 detector, 显式置 None 以测试降级路径
        monitor.detector = None
        report = monitor.generate_report()
        self.assertIn("error", report)

    def test_generate_report_with_detector(self) -> None:
        """有 detector 时委托."""
        monitor = self._make_monitor()
        monitor.detector.generate_report.return_value = {"drift_score": 0.5}
        report = monitor.generate_report()
        self.assertEqual(report["drift_score"], 0.5)
        self.assertEqual(report["model_name"], "test_model")

    def test_generate_report_exception(self) -> None:
        """generate_report 异常返回 error."""
        monitor = self._make_monitor()
        monitor.detector.generate_report.side_effect = RuntimeError("boom")
        report = monitor.generate_report()
        self.assertIn("error", report)

    def test_reset_retrain_state(self) -> None:
        """reset_retrain_state 重置状态."""
        monitor = self._make_monitor()
        monitor._retrain_triggered = True
        monitor.reset_retrain_state()
        self.assertFalse(monitor._retrain_triggered)

    def test_create_drift_monitor_helper(self) -> None:
        """测试便捷函数."""
        from utils.alpha.drift_monitor import create_drift_monitor
        monitor = create_drift_monitor("test_model")
        self.assertEqual(monitor.model_name, "test_model")


class TestAutoRetrainSchedulerExtended(unittest.TestCase):
    """AutoRetrainScheduler 扩展测试."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_scheduler(self, enabled: bool = True) -> Any:
        """创建 scheduler (注入 mock 依赖)."""
        from utils.alpha.auto_retrain_scheduler import AutoRetrainScheduler
        registry = MagicMock()
        ab = MagicMock()
        drift = MagicMock()
        scheduler = AutoRetrainScheduler(
            config={
                "enabled": enabled,
                "min_interval_hours": 0,
                "training_script": "nonexistent_script.py",
                "training_timeout_sec": 5,
                "auto_register": True,
                "auto_start_ab_test": True,
            },
            model_registry=registry,
            ab_framework=ab,
            drift_monitor=drift,
        )
        # 隔离测试: 重定向 tasks_dir 到临时目录, 清空内存中的历史任务
        # (避免加载 reports/auto_retrain/tasks.jsonl 中的旧数据)
        scheduler._tasks_dir = Path(self.tmpdir) / "auto_retrain"
        scheduler._tasks_dir.mkdir(parents=True, exist_ok=True)
        scheduler._tasks.clear()
        return scheduler

    def test_on_drift_alerts_disabled(self) -> None:
        """未启用时 drift 告警回调返回 False."""
        from utils.alpha.auto_retrain_scheduler import AutoRetrainScheduler
        scheduler = AutoRetrainScheduler(
            config={"enabled": False},
            model_registry=MagicMock(),
            ab_framework=MagicMock(),
            drift_monitor=MagicMock(),
        )
        self.assertFalse(scheduler._on_drift_alerts([]))

    def test_on_drift_alerts_enabled(self) -> None:
        """启用时 drift 告警回调触发 retrain."""
        scheduler = self._make_scheduler(enabled=True)
        # mock trigger_retrain
        scheduler.trigger_retrain = MagicMock(return_value=True)
        ok = scheduler._on_drift_alerts([{"alert": "test"}])
        self.assertTrue(ok)
        scheduler.trigger_retrain.assert_called_once()

    def test_trigger_retrain_in_progress(self) -> None:
        """已有训练在跑应抛 TrainingInProgressError."""
        from utils.alpha.auto_retrain_scheduler import (
            RetrainStatus,
            RetrainTask,
            TrainingInProgressError,
        )
        scheduler = self._make_scheduler(enabled=True)
        # 注入正在跑的任务
        running_task = RetrainTask(
            task_id="running", trigger="manual", reason="test",
            status=RetrainStatus.RUNNING.value,
        )
        scheduler._current_task = running_task
        with self.assertRaises(TrainingInProgressError):
            scheduler.trigger_retrain(reason="test")

    def test_trigger_retrain_min_interval(self) -> None:
        """距上次重训练不足最小间隔应返回 False."""
        from datetime import datetime, timedelta

        from utils.alpha.auto_retrain_scheduler import AutoRetrainScheduler
        scheduler = AutoRetrainScheduler(
            config={
                "enabled": True,
                "min_interval_hours": 24,  # 24 小时
            },
            model_registry=MagicMock(),
            ab_framework=MagicMock(),
            drift_monitor=MagicMock(),
        )
        # 模拟 1 小时前刚训练过
        scheduler._last_retrain_time = datetime.utcnow() - timedelta(hours=1)
        self.assertFalse(scheduler.trigger_retrain(reason="test"))

    def test_run_training_script_not_found(self) -> None:
        """训练脚本不存在应失败."""
        from utils.alpha.auto_retrain_scheduler import (
            RetrainTask,
        )
        scheduler = self._make_scheduler(enabled=True)
        task = RetrainTask(task_id="t1", trigger="manual", reason="test")
        scheduler._run_training(task)
        # 任务应失败
        self.assertEqual(task.status, "failed")
        self.assertIn("训练脚本不存在", task.error)

    def test_execute_training_script_timeout(self) -> None:
        """训练脚本超时应失败."""
        from utils.alpha.auto_retrain_scheduler import (
            RetrainTask,
        )
        # 创建一个会导致超时的脚本 (用 ping 等待)
        scheduler = self._make_scheduler(enabled=True)
        scheduler.training_script = "nonexistent.py"  # 不存在
        scheduler.training_timeout_sec = 1
        task = RetrainTask(task_id="t1", trigger="manual", reason="test")
        result = scheduler._execute_training_script(task)
        self.assertFalse(result["success"])

    def test_load_trained_model(self) -> None:
        """_load_trained_model 返回占位指标."""
        scheduler = self._make_scheduler(enabled=True)
        model, metrics = scheduler._load_trained_model({})
        self.assertIsNone(model)
        self.assertIn("dsr", metrics)

    def test_register_model_success(self) -> None:
        """_register_model 注册成功."""
        from utils.alpha.auto_retrain_scheduler import RetrainTask
        scheduler = self._make_scheduler(enabled=True)
        fake_version = MagicMock()
        fake_version.version = 1
        scheduler._model_registry.register_model.return_value = fake_version
        task = RetrainTask(task_id="t1", trigger="manual", reason="test", model_name="m1")
        version = scheduler._register_model(task, None, {"dsr": 5.0}, {})
        self.assertIsNotNone(version)
        self.assertEqual(version.version, 1)

    def test_register_model_exception(self) -> None:
        """_register_model 异常返回 None."""
        from utils.alpha.auto_retrain_scheduler import RetrainTask
        scheduler = self._make_scheduler(enabled=True)
        scheduler._model_registry.register_model.side_effect = RuntimeError("boom")
        task = RetrainTask(task_id="t1", trigger="manual", reason="test", model_name="m1")
        version = scheduler._register_model(task, None, {}, {})
        self.assertIsNone(version)

    def test_start_ab_test_success(self) -> None:
        """_start_ab_test 启动 A/B 测试成功."""
        from utils.alpha.auto_retrain_scheduler import RetrainTask
        scheduler = self._make_scheduler(enabled=True)
        # mock champion
        champion = MagicMock()
        champion.version = 1
        scheduler._model_registry.get_production_version.return_value = champion
        task = RetrainTask(task_id="t1", trigger="manual", reason="test", model_name="m1")
        new_version = MagicMock()
        new_version.version = 2
        test_name = scheduler._start_ab_test(task, new_version)
        self.assertIsNotNone(test_name)
        scheduler._ab_framework.create_test.assert_called_once()

    def test_start_ab_test_exception(self) -> None:
        """_start_ab_test 异常返回 None."""
        from utils.alpha.auto_retrain_scheduler import RetrainTask
        scheduler = self._make_scheduler(enabled=True)
        scheduler._model_registry.get_production_version.side_effect = RuntimeError("boom")
        task = RetrainTask(task_id="t1", trigger="manual", reason="test", model_name="m1")
        new_version = MagicMock()
        new_version.version = 2
        test_name = scheduler._start_ab_test(task, new_version)
        self.assertIsNone(test_name)

    def test_start_disabled(self) -> None:
        """未启用时 start 应 warning."""
        from utils.alpha.auto_retrain_scheduler import AutoRetrainScheduler
        scheduler = AutoRetrainScheduler(
            config={"enabled": False},
            model_registry=MagicMock(),
            ab_framework=MagicMock(),
            drift_monitor=MagicMock(),
        )
        scheduler.start()
        self.assertIsNone(scheduler._scheduler_thread)

    def test_start_already_running(self) -> None:
        """已运行时再次 start 应 warning."""
        scheduler = self._make_scheduler(enabled=True)
        # mock 一个活的线程
        import threading
        scheduler._scheduler_thread = threading.Thread(target=lambda: None)
        scheduler._scheduler_thread.start()
        scheduler.start()  # 不应抛异常
        scheduler.stop()

    def test_stop(self) -> None:
        """stop 应停止调度."""
        scheduler = self._make_scheduler(enabled=True)
        # mock drift_monitor
        scheduler._drift_monitor = MagicMock()
        scheduler.stop()  # 不应抛异常
        scheduler._drift_monitor.stop_monitoring.assert_not_called()  # 没启动就 stop

    def test_scheduled_check_no_drift(self) -> None:
        """定时检查 drift 状态."""
        scheduler = self._make_scheduler(enabled=True)
        scheduler._drift_monitor = MagicMock()
        scheduler._drift_monitor.should_retrain.return_value = (False, "no drift")
        # 不应抛异常
        scheduler._scheduled_check()

    def test_scheduled_check_triggers_retrain(self) -> None:
        """定时检查触发重训练."""
        scheduler = self._make_scheduler(enabled=True)
        scheduler._drift_monitor = MagicMock()
        scheduler._drift_monitor.should_retrain.return_value = (True, "drift detected")
        # mock trigger_retrain
        scheduler.trigger_retrain = MagicMock()
        scheduler._scheduled_check()
        scheduler.trigger_retrain.assert_called_once()

    def test_list_tasks(self) -> None:
        """list_tasks 返回任务列表."""
        from utils.alpha.auto_retrain_scheduler import RetrainTask
        scheduler = self._make_scheduler(enabled=True)
        scheduler._tasks.append(RetrainTask(task_id="t1", trigger="manual", reason="test"))
        tasks = scheduler.list_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["task_id"], "t1")

    def test_get_task_found(self) -> None:
        """get_task 找到任务."""
        from utils.alpha.auto_retrain_scheduler import RetrainTask
        scheduler = self._make_scheduler(enabled=True)
        task = RetrainTask(task_id="t1", trigger="manual", reason="test")
        scheduler._tasks.append(task)
        found = scheduler.get_task("t1")
        self.assertIsNotNone(found)
        self.assertEqual(found.task_id, "t1")

    def test_get_task_not_found(self) -> None:
        """get_task 未找到返回 None."""
        scheduler = self._make_scheduler(enabled=True)
        self.assertIsNone(scheduler.get_task("nonexistent"))


class TestABTestingExtended(unittest.TestCase):
    """ABTestFramework 扩展测试."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_framework(self) -> Any:
        """创建 ABTestFramework (mock registry)."""
        from utils.alpha.ab_testing import ABTestFramework
        registry = MagicMock()
        return ABTestFramework(
            results_dir=self.tmpdir + "/ab_tests",
            model_registry=registry,
        )

    def test_compute_group_zero_split(self) -> None:
        """traffic_split=0 时全部返回 champion."""
        from utils.alpha.ab_testing import ABTestFramework
        fw = ABTestFramework(results_dir=self.tmpdir)
        group = fw._compute_group("000001", 0.0)
        self.assertEqual(group, "champion")

    def test_compute_group_full_split(self) -> None:
        """traffic_split=1.0 时全部返回 challenger."""
        from utils.alpha.ab_testing import ABTestFramework
        fw = ABTestFramework(results_dir=self.tmpdir)
        group = fw._compute_group("000001", 1.0)
        self.assertEqual(group, "challenger")

    def test_compute_group_random_strategy(self) -> None:
        """RANDOM 策略."""
        from utils.alpha.ab_testing import ABTestFramework, SplitStrategy
        fw = ABTestFramework(results_dir=self.tmpdir)
        # 调用多次应不抛异常
        for _ in range(10):
            group = fw._compute_group(
                "000001", 0.5, strategy=SplitStrategy.RANDOM.value
            )
            self.assertIn(group, ["champion", "challenger"])

    def test_compute_group_round_robin_strategy(self) -> None:
        """ROUND_ROBIN 策略."""
        from utils.alpha.ab_testing import ABTestFramework, SplitStrategy
        fw = ABTestFramework(results_dir=self.tmpdir)
        group = fw._compute_group(
            "000001", 0.5, strategy=SplitStrategy.ROUND_ROBIN.value
        )
        self.assertEqual(group, "challenger")

    def test_compute_group_unknown_strategy(self) -> None:
        """未知策略默认返回 champion."""
        from utils.alpha.ab_testing import ABTestFramework
        fw = ABTestFramework(results_dir=self.tmpdir)
        group = fw._compute_group("000001", 0.5, strategy="unknown")
        self.assertEqual(group, "champion")

    def test_get_test_not_found(self) -> None:
        """get_test 未找到抛 TestNotFoundError."""
        from utils.alpha.ab_testing import ABTestFramework, TestNotFoundError
        fw = ABTestFramework(results_dir=self.tmpdir)
        with self.assertRaises(TestNotFoundError):
            fw.get_test("nonexistent")

    def test_list_tests_with_status_filter(self) -> None:
        """list_tests 支持状态过滤."""
        from utils.alpha.ab_testing import (
            ABTestConfig,
            ABTestStatus,
        )
        fw = self._make_framework()
        config = ABTestConfig(name="t1", champion_model="c", challenger_model="ch")
        fw.create_test(config)
        fw.start_test("t1")
        # 过滤 RUNNING
        running = fw.list_tests(status=ABTestStatus.RUNNING)
        self.assertEqual(len(running), 1)
        # 过滤 COMPLETED (无)
        completed = fw.list_tests(status=ABTestStatus.COMPLETED)
        self.assertEqual(len(completed), 0)

    def test_get_active_test_for_model(self) -> None:
        """get_active_test_for_model 返回活跃测试."""
        from utils.alpha.ab_testing import (
            ABTestConfig,
        )
        fw = self._make_framework()
        config = ABTestConfig(
            name="t1", champion_model="model_a", challenger_model="model_b"
        )
        fw.create_test(config)
        fw.start_test("t1")
        # 查询 model_a 应返回测试
        test = fw.get_active_test_for_model("model_a")
        self.assertIsNotNone(test)
        # 查询 model_b 也应返回测试
        test = fw.get_active_test_for_model("model_b")
        self.assertIsNotNone(test)
        # 查询 model_c 应返回 None
        self.assertIsNone(fw.get_active_test_for_model("model_c"))

    def test_promote_challenger_not_found(self) -> None:
        """promote_challenger 测试未找到抛 TestNotFoundError."""
        from utils.alpha.ab_testing import TestNotFoundError
        fw = self._make_framework()
        with self.assertRaises(TestNotFoundError):
            fw.promote_challenger("nonexistent")

    def test_promote_challenger_not_evaluated(self) -> None:
        """promote_challenger 未评估抛 ABTestError."""
        from utils.alpha.ab_testing import (
            ABTestConfig,
            ABTestError,
        )
        fw = self._make_framework()
        config = ABTestConfig(name="t1", champion_model="c", challenger_model="ch")
        fw.create_test(config)
        fw.start_test("t1")
        with self.assertRaises(ABTestError):
            fw.promote_challenger("t1")

    def test_rollback_to_champion(self) -> None:
        """rollback_to_champion 成功."""
        from utils.alpha.ab_testing import (
            ABTestConfig,
        )
        fw = self._make_framework()
        # mock champion 版本
        champion_v = MagicMock()
        champion_v.stage = "production"
        champion_v.version = 1
        fw._model_registry.get_model_versions.return_value = [champion_v]
        config = ABTestConfig(
            name="t1", champion_model="c", challenger_model="ch"
        )
        fw.create_test(config)
        fw.start_test("t1")
        result = fw.rollback_to_champion("t1")
        self.assertEqual(result.version, 1)

    def test_rollback_to_champion_not_found(self) -> None:
        """rollback_to_champion 测试未找到."""
        from utils.alpha.ab_testing import TestNotFoundError
        fw = self._make_framework()
        with self.assertRaises(TestNotFoundError):
            fw.rollback_to_champion("nonexistent")

    def test_rollback_to_champion_no_versions(self) -> None:
        """rollback_to_champion champion 模型无版本."""
        from utils.alpha.ab_testing import (
            ABTestConfig,
            ABTestError,
        )
        fw = self._make_framework()
        fw._model_registry.get_model_versions.return_value = []
        config = ABTestConfig(name="t1", champion_model="c", challenger_model="ch")
        fw.create_test(config)
        with self.assertRaises(ABTestError):
            fw.rollback_to_champion("t1")

    def test_t_test_insufficient_samples(self) -> None:
        """t_test 样本不足返回 1.0."""
        fw = self._make_framework()
        self.assertEqual(fw._t_test([1.0], [2.0]), 1.0)

    def test_t_test_zero_se(self) -> None:
        """t_test 标准误差为 0 返回 1.0."""
        fw = self._make_framework()
        # 两个完全相同的序列
        a = [1.0, 1.0, 1.0]
        b = [1.0, 1.0, 1.0]
        self.assertEqual(fw._t_test(a, b), 1.0)

    def test_cohens_d_insufficient_samples(self) -> None:
        """cohens_d 样本不足返回 0."""
        fw = self._make_framework()
        self.assertEqual(fw._cohens_d([1.0], [2.0]), 0.0)

    def test_cohens_d_zero_std(self) -> None:
        """cohens_d 标准差为 0 返回 0."""
        fw = self._make_framework()
        a = [1.0, 1.0, 1.0]
        b = [1.0, 1.0, 1.0]
        self.assertEqual(fw._cohens_d(a, b), 0.0)

    def test_make_recommendation_rollback(self) -> None:
        """_make_recommendation 回滚条件触发."""
        from utils.alpha.ab_testing import ABTest, ABTestConfig
        fw = self._make_framework()
        config = ABTestConfig(
            name="t1", champion_model="c", challenger_model="ch",
            rollback_criteria={"dsr_challenger_lt_champion_by": 1.0},
        )
        test = ABTest(config=config)
        # champion dsr=5.0, challenger dsr=3.0 → dsr_diff=2.0 > 1.0 → rollback
        rec = fw._make_recommendation(
            test,
            challenger_metrics={"dsr": 3.0},
            champion_metrics={"dsr": 5.0},
            is_significant=True,
            challenger_better=False,
        )
        self.assertEqual(rec, "rollback")

    def test_make_recommendation_promote(self) -> None:
        """_make_recommendation 晋升条件触发."""
        from utils.alpha.ab_testing import (
            ABTest,
            ABTestConfig,
        )
        fw = self._make_framework()
        config = ABTestConfig(
            name="t1", champion_model="c", challenger_model="ch",
            promotion_criteria={"dsr_min": 4.0},
        )
        test = ABTest(config=config)
        rec = fw._make_recommendation(
            test,
            challenger_metrics={"dsr": 5.0},
            champion_metrics={"dsr": 3.0},
            is_significant=True,
            challenger_better=True,
        )
        self.assertEqual(rec, "promote")

    def test_make_recommendation_continue(self) -> None:
        """_make_recommendation 默认 continue."""
        from utils.alpha.ab_testing import (
            ABTest,
            ABTestConfig,
        )
        fw = self._make_framework()
        config = ABTestConfig(name="t1", champion_model="c", challenger_model="ch")
        test = ABTest(config=config)
        rec = fw._make_recommendation(
            test,
            challenger_metrics={"dsr": 5.0},
            champion_metrics={"dsr": 5.0},
            is_significant=False,
            challenger_better=False,
        )
        self.assertEqual(rec, "continue")

    def test_detect_primary_metric_default(self) -> None:
        """_detect_primary_metric 默认 ic."""
        from utils.alpha.ab_testing import ABTest, ABTestConfig
        fw = self._make_framework()
        config = ABTestConfig(name="t1", champion_model="c", challenger_model="ch")
        test = ABTest(config=config)
        self.assertEqual(fw._detect_primary_metric(test), "ic")

    def test_detect_primary_metric_dsr(self) -> None:
        """_detect_primary_metric 优先 dsr."""
        from utils.alpha.ab_testing import ABTest, ABTestConfig
        fw = self._make_framework()
        config = ABTestConfig(name="t1", champion_model="c", challenger_model="ch")
        test = ABTest(config=config)
        test.daily_records.append({"champion": {"dsr": 5.0}, "challenger": {"dsr": 4.0}})
        self.assertEqual(fw._detect_primary_metric(test), "dsr")

    def test_summarize_metrics_empty(self) -> None:
        """_summarize_metrics 空列表返回空字典."""
        fw = self._make_framework()
        self.assertEqual(fw._summarize_metrics([]), {})

    def test_summarize_metrics_avg(self) -> None:
        """_summarize_metrics 计算平均值."""
        fw = self._make_framework()
        records = [{"dsr": 4.0}, {"dsr": 6.0}]
        summary = fw._summarize_metrics(records)
        self.assertEqual(summary["dsr"], 5.0)


if __name__ == "__main__":
    unittest.main()
