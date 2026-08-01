"""T1.6 core.py 策略注册表单元测试.

验收标准 (TASK_模块整合.md T1.6):
    1. StrategyRegistry.register(name, strategy_class) 注册策略
    2. StrategyRegistry.get(name) 获取策略
    3. StrategyRegistry.list_strategies() 返回所有已注册策略
    4. @track_performance 记录每策略 PnL/Sharpe/回撤
    5. 单测覆盖率 >= 80%

测试场景:
    - TestStrategyRegistryBasic: 基本注册/获取/查询
    - TestStrategyRegistryLifecycle: 注册/卸载/覆盖生命周期
    - TestStrategyRegistryActive: 启用/禁用状态
    - TestPerformanceRecord: 性能记录维护
    - TestTrackPerformanceDecorator: 装饰器行为
    - TestStrategyRegistrySingleton: 单例模式
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# 项目根目录加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.infra.core import (
    StrategyAlreadyRegisteredError,
    StrategyNotFoundError,
    StrategyRegistry,
    registry,
    track_performance,
)


# ============================================================
# 测试用策略类
# ============================================================
class DummyStrategy:
    """测试用策略 (无参构造)."""
    name = "dummy"

    def __init__(self) -> None:
        self.created = True

    def generate_signals(self, context: dict) -> dict:
        return {"signal": "buy", "context": context}


class AnotherStrategy:
    """另一个测试用策略."""
    def __init__(self) -> None:
        self.name = "another"


class FailingStrategy:
    """构造会抛异常的策略."""
    def __init__(self) -> None:
        raise RuntimeError("intentional failure")


# ============================================================
# 测试类
# ============================================================
class TestStrategyRegistryBasic(unittest.TestCase):
    """基本注册/获取/查询测试."""

    def setUp(self) -> None:
        StrategyRegistry.reset_instance()
        # 重新获取单例
        self.reg = StrategyRegistry.get_instance()

    def tearDown(self) -> None:
        StrategyRegistry.reset_instance()

    def test_register_and_get(self) -> None:
        """测试: register + get 完整流程."""
        self.reg.register("dummy", DummyStrategy, metadata={"capital": 1_000_000})
        instance = self.reg.get("dummy")
        self.assertIsInstance(instance, DummyStrategy)
        self.assertTrue(instance.created)

    def test_get_returns_cached_instance(self) -> None:
        """测试: get() 首次实例化后缓存."""
        self.reg.register("dummy", DummyStrategy)
        inst1 = self.reg.get("dummy")
        inst2 = self.reg.get("dummy")
        self.assertIs(inst1, inst2)  # 同一对象

    def test_get_class_without_instantiation(self) -> None:
        """测试: get_class() 不触发实例化."""
        self.reg.register("dummy", DummyStrategy)
        cls = self.reg.get_class("dummy")
        self.assertIs(cls, DummyStrategy)

    def test_get_unregistered_raises(self) -> None:
        """测试: 获取未注册策略抛 StrategyNotFoundError."""
        with self.assertRaises(StrategyNotFoundError):
            self.reg.get("not_registered")

    def test_is_registered(self) -> None:
        """测试: is_registered 查询."""
        self.assertFalse(self.reg.is_registered("dummy"))
        self.reg.register("dummy", DummyStrategy)
        self.assertTrue(self.reg.is_registered("dummy"))

    def test_list_strategies(self) -> None:
        """测试: list_strategies 返回所有策略名."""
        self.assertEqual(self.reg.list_strategies(), [])
        self.reg.register("dummy", DummyStrategy)
        self.reg.register("another", AnotherStrategy)
        self.assertEqual(set(self.reg.list_strategies()), {"dummy", "another"})

    def test_get_metadata(self) -> None:
        """测试: get_metadata 返回元数据."""
        self.reg.register("dummy", DummyStrategy, metadata={
            "description": "test strategy",
            "version": "2.0.0",
            "author": "tester",
            "capital": 500_000,
            "max_weight": 0.30,
            "min_weight": 0.10,
        })
        meta = self.reg.get_metadata("dummy")
        self.assertEqual(meta.name, "dummy")
        self.assertEqual(meta.description, "test strategy")
        self.assertEqual(meta.version, "2.0.0")
        self.assertEqual(meta.author, "tester")
        self.assertEqual(meta.capital, 500_000)
        self.assertEqual(meta.max_weight, 0.30)
        self.assertEqual(meta.min_weight, 0.10)


class TestStrategyRegistryLifecycle(unittest.TestCase):
    """注册/卸载/覆盖生命周期测试."""

    def setUp(self) -> None:
        StrategyRegistry.reset_instance()
        self.reg = StrategyRegistry.get_instance()

    def tearDown(self) -> None:
        StrategyRegistry.reset_instance()

    def test_register_duplicate_raises(self) -> None:
        """测试: 重复注册抛 StrategyAlreadyRegisteredError."""
        self.reg.register("dummy", DummyStrategy)
        with self.assertRaises(StrategyAlreadyRegisteredError):
            self.reg.register("dummy", AnotherStrategy)

    def test_register_overwrite_allowed(self) -> None:
        """测试: overwrite=True 允许覆盖."""
        self.reg.register("dummy", DummyStrategy)
        self.reg.register("dummy", AnotherStrategy, overwrite=True)
        instance = self.reg.get("dummy")
        self.assertIsInstance(instance, AnotherStrategy)

    def test_unregister(self) -> None:
        """测试: unregister 卸载策略."""
        self.reg.register("dummy", DummyStrategy)
        self.assertTrue(self.reg.unregister("dummy"))
        self.assertFalse(self.reg.is_registered("dummy"))
        self.assertFalse(self.reg.unregister("dummy"))  # 二次卸载返回 False

    def test_unregister_clears_instance_cache(self) -> None:
        """测试: unregister 清空实例缓存."""
        self.reg.register("dummy", DummyStrategy)
        _ = self.reg.get("dummy")  # 触发实例化
        self.reg.unregister("dummy")
        # 重新注册后, 实例缓存应已清空
        self.reg.register("dummy", AnotherStrategy)
        instance = self.reg.get("dummy")
        self.assertIsInstance(instance, AnotherStrategy)

    def test_get_instantiation_failure_propagates(self) -> None:
        """测试: 策略实例化失败抛原始异常."""
        self.reg.register("failing", FailingStrategy)
        with self.assertRaises(RuntimeError):
            self.reg.get("failing")


class TestStrategyRegistryActive(unittest.TestCase):
    """启用/禁用状态测试."""

    def setUp(self) -> None:
        StrategyRegistry.reset_instance()
        self.reg = StrategyRegistry.get_instance()

    def tearDown(self) -> None:
        StrategyRegistry.reset_instance()

    def test_default_active_on_register(self) -> None:
        """测试: 注册后默认启用."""
        self.reg.register("dummy", DummyStrategy)
        self.assertIn("dummy", self.reg.list_active())

    def test_set_inactive(self) -> None:
        """测试: set_inactive 禁用策略."""
        self.reg.register("dummy", DummyStrategy)
        self.reg.set_inactive("dummy")
        self.assertNotIn("dummy", self.reg.list_active())

    def test_set_active(self) -> None:
        """测试: set_active 重新启用."""
        self.reg.register("dummy", DummyStrategy)
        self.reg.set_inactive("dummy")
        self.reg.set_active("dummy")
        self.assertIn("dummy", self.reg.list_active())

    def test_set_active_unregistered_raises(self) -> None:
        """测试: 启用未注册策略抛异常."""
        with self.assertRaises(StrategyNotFoundError):
            self.reg.set_active("not_registered")

    def test_list_active_subset_of_list_strategies(self) -> None:
        """测试: list_active 是 list_strategies 的子集."""
        self.reg.register("dummy", DummyStrategy)
        self.reg.register("another", AnotherStrategy)
        self.reg.set_inactive("dummy")
        active = set(self.reg.list_active())
        all_strategies = set(self.reg.list_strategies())
        self.assertTrue(active.issubset(all_strategies))
        self.assertIn("another", active)
        self.assertNotIn("dummy", active)


class TestPerformanceRecord(unittest.TestCase):
    """性能记录维护测试."""

    def setUp(self) -> None:
        StrategyRegistry.reset_instance()
        self.reg = StrategyRegistry.get_instance()

    def tearDown(self) -> None:
        StrategyRegistry.reset_instance()

    def test_performance_record_initial_state(self) -> None:
        """测试: 性能记录初始状态."""
        self.reg.register("dummy", DummyStrategy)
        perf = self.reg.get_performance("dummy")
        self.assertEqual(perf.call_count, 0)
        self.assertEqual(perf.errors_count, 0)
        self.assertEqual(perf.avg_latency_ms, 0.0)

    def test_record_call_increments_count(self) -> None:
        """测试: _record_call 增加调用计数."""
        self.reg.register("dummy", DummyStrategy)
        self.reg._record_call("dummy", latency_ms=10.5, success=True)
        self.reg._record_call("dummy", latency_ms=20.0, success=True)
        perf = self.reg.get_performance("dummy")
        self.assertEqual(perf.call_count, 2)
        self.assertAlmostEqual(perf.total_latency_ms, 30.5)
        self.assertAlmostEqual(perf.avg_latency_ms, 15.25)

    def test_record_call_tracks_errors(self) -> None:
        """测试: _record_call 记录异常."""
        self.reg.register("dummy", DummyStrategy)
        self.reg._record_call("dummy", latency_ms=5.0, success=False, error_msg="RuntimeError: test")
        perf = self.reg.get_performance("dummy")
        self.assertEqual(perf.errors_count, 1)
        self.assertIn("test", perf.last_error)

    def test_update_metrics(self) -> None:
        """测试: update_metrics 更新业务指标."""
        self.reg.register("dummy", DummyStrategy)
        self.reg.update_metrics("dummy", {
            "pnl_total": 10000.0,
            "sharpe_ratio": 1.5,
            "max_drawdown": 0.08,
            "ic_ir": 0.42,
        })
        perf = self.reg.get_performance("dummy")
        self.assertEqual(perf.pnl_total, 10000.0)
        self.assertEqual(perf.sharpe_ratio, 1.5)
        self.assertEqual(perf.max_drawdown, 0.08)
        self.assertEqual(perf.ic_ir, 0.42)

    def test_performance_to_dict(self) -> None:
        """测试: to_dict 序列化."""
        self.reg.register("dummy", DummyStrategy)
        self.reg._record_call("dummy", latency_ms=10.0, success=True)
        perf = self.reg.get_performance("dummy")
        d = perf.to_dict()
        self.assertEqual(d["strategy_name"], "dummy")
        self.assertEqual(d["call_count"], 1)
        self.assertIn("avg_latency_ms", d)
        self.assertIn("sharpe_ratio", d)


class TestTrackPerformanceDecorator(unittest.TestCase):
    """@track_performance 装饰器测试."""

    def setUp(self) -> None:
        StrategyRegistry.reset_instance()
        self.reg = StrategyRegistry.get_instance()

    def tearDown(self) -> None:
        StrategyRegistry.reset_instance()

    def test_decorator_flag_off_passthrough(self) -> None:
        """测试: flag 关闭时装饰器零开销透传."""
        self.reg.register("dummy", DummyStrategy)

        call_count = {"n": 0}

        @track_performance(strategy_name="dummy")
        def my_func(x: int) -> int:
            call_count["n"] += 1
            return x * 2

        # mock flag 为 False
        with patch("utils.infra.feature_flags.FeatureFlags.get_instance") as mock_get:
            mock_flags = MagicMock()
            mock_flags.is_enabled.return_value = False
            mock_get.return_value = mock_flags

            result = my_func(5)
            self.assertEqual(result, 10)
            self.assertEqual(call_count["n"], 1)

            # 验证: flag 关闭时不记录性能
            perf = self.reg.get_performance("dummy")
            self.assertEqual(perf.call_count, 0)

    def test_decorator_flag_on_records_call(self) -> None:
        """测试: flag 开启时记录调用."""
        self.reg.register("dummy", DummyStrategy)

        @track_performance(strategy_name="dummy")
        def my_func(x: int) -> int:
            return x * 2

        # mock flag 为 True
        with patch("utils.infra.feature_flags.FeatureFlags.get_instance") as mock_get:
            mock_flags = MagicMock()
            mock_flags.is_enabled.return_value = True
            mock_get.return_value = mock_flags

            result = my_func(5)
            self.assertEqual(result, 10)
            perf = self.reg.get_performance("dummy")
            self.assertEqual(perf.call_count, 1)
            self.assertGreater(perf.total_latency_ms, 0)

    def test_decorator_records_errors(self) -> None:
        """测试: 装饰器记录异常."""
        self.reg.register("dummy", DummyStrategy)

        @track_performance(strategy_name="dummy")
        def my_func() -> None:
            raise ValueError("test error")

        with patch("utils.infra.feature_flags.FeatureFlags.get_instance") as mock_get:
            mock_flags = MagicMock()
            mock_flags.is_enabled.return_value = True
            mock_get.return_value = mock_flags

            with self.assertRaises(ValueError):
                my_func()
            perf = self.reg.get_performance("dummy")
            self.assertEqual(perf.call_count, 1)
            self.assertEqual(perf.errors_count, 1)
            self.assertIn("test error", perf.last_error)

    def test_decorator_preserves_function_metadata(self) -> None:
        """测试: 装饰器保留原函数元数据 (functools.wraps)."""
        self.reg.register("dummy", DummyStrategy)

        @track_performance(strategy_name="dummy")
        def my_func(x: int) -> int:
            """My function docstring."""
            return x

        self.assertEqual(my_func.__name__, "my_func")
        self.assertEqual(my_func.__doc__, "My function docstring.")

    def test_decorator_does_not_swallow_exceptions(self) -> None:
        """测试: 装饰器不吞异常."""
        self.reg.register("dummy", DummyStrategy)

        @track_performance(strategy_name="dummy")
        def my_func() -> None:
            raise RuntimeError("should propagate")

        with patch("utils.infra.feature_flags.FeatureFlags.get_instance") as mock_get:
            mock_flags = MagicMock()
            mock_flags.is_enabled.return_value = True
            mock_get.return_value = mock_flags

            with self.assertRaises(RuntimeError):
                my_func()


class TestStrategyRegistrySingleton(unittest.TestCase):
    """单例模式测试.

    注意: 模块级 registry 在 import 时已创建, reset_instance() 不会同步更新它.
    因此本测试类不调用 reset_instance(), 仅验证单例语义.
    """

    def test_get_instance_returns_same_object(self) -> None:
        """测试: get_instance 返回同一对象."""
        reg1 = StrategyRegistry.get_instance()
        reg2 = StrategyRegistry.get_instance()
        self.assertIs(reg1, reg2)

    def test_reset_instance_creates_new(self) -> None:
        """测试: reset_instance 后 get_instance 返回新对象."""
        reg1 = StrategyRegistry.get_instance()
        StrategyRegistry.reset_instance()
        reg2 = StrategyRegistry.get_instance()
        self.assertIsNot(reg1, reg2)
        # 清理: 让后续测试拿到干净单例
        StrategyRegistry.reset_instance()

    def test_module_level_registry_is_singleton(self) -> None:
        """测试: 模块级 registry 是 StrategyRegistry 实例.

        注意: 模块级 registry 在 import 时创建, 后续 reset_instance() 不会同步更新它.
        因此这里只验证类型, 不验证身份一致性 (身份一致性在无 reset 场景下成立).
        """
        self.assertIsInstance(registry, StrategyRegistry)


class TestStrategyRegistrySnapshot(unittest.TestCase):
    """snapshot 与 get_state 测试."""

    def setUp(self) -> None:
        StrategyRegistry.reset_instance()
        self.reg = StrategyRegistry.get_instance()

    def tearDown(self) -> None:
        StrategyRegistry.reset_instance()

    def test_get_state(self) -> None:
        """测试: get_state 返回完整状态."""
        self.reg.register("dummy", DummyStrategy, metadata={"capital": 1_000_000})
        state = self.reg.get_state("dummy")
        self.assertEqual(state["name"], "dummy")
        self.assertEqual(state["class"], "DummyStrategy")
        self.assertEqual(state["capital"], 1_000_000)
        self.assertTrue(state["is_active"])
        self.assertIn("performance", state)
        self.assertIn("call_count", state["performance"])

    def test_snapshot(self) -> None:
        """测试: snapshot 返回所有策略状态."""
        self.reg.register("dummy", DummyStrategy)
        self.reg.register("another", AnotherStrategy)
        snap = self.reg.snapshot()
        self.assertEqual(set(snap.keys()), {"dummy", "another"})
        self.assertIn("name", snap["dummy"])
        self.assertIn("performance", snap["dummy"])

    def test_clear(self) -> None:
        """测试: clear 清空所有注册."""
        self.reg.register("dummy", DummyStrategy)
        self.reg.register("another", AnotherStrategy)
        self.assertEqual(len(self.reg.list_strategies()), 2)
        self.reg.clear()
        self.assertEqual(len(self.reg.list_strategies()), 0)


class TestRegisterDecorator(unittest.TestCase):
    """类装饰器 register_decorator 测试."""

    def setUp(self) -> None:
        StrategyRegistry.reset_instance()

    def tearDown(self) -> None:
        StrategyRegistry.reset_instance()

    def test_register_decorator_registers_class(self) -> None:
        """测试: @StrategyRegistry.register_decorator 注册类."""
        @StrategyRegistry.register_decorator("decorated_strategy", metadata={"capital": 200_000})
        class DecoratedStrategy:
            def __init__(self) -> None:
                self.name = "decorated"

        reg = StrategyRegistry.get_instance()
        self.assertTrue(reg.is_registered("decorated_strategy"))
        instance = reg.get("decorated_strategy")
        self.assertEqual(instance.name, "decorated")
        meta = reg.get_metadata("decorated_strategy")
        self.assertEqual(meta.capital, 200_000)


if __name__ == "__main__":
    unittest.main()
