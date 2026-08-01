"""T5.7 实盘券商直连适配器 + 故障切换管理器单元测试.

覆盖:
    - ThsBrokerAdapter (dry-run + live 模式)
    - XueqiuBrokerAdapter (dry-run + live 模式)
    - BrokerHealthTracker (健康状态追踪)
    - BrokerFailoverManager (故障切换)

验收标准: 单测覆盖率 >= 70%
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# 项目根
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


class TestBrokerAdaptersImport(unittest.TestCase):
    """测试模块可导入."""

    def test_import_broker_adapters(self) -> None:
        from utils.execution import broker_adapters
        self.assertTrue(hasattr(broker_adapters, "ThsBrokerAdapter"))
        self.assertTrue(hasattr(broker_adapters, "XueqiuBrokerAdapter"))
        self.assertTrue(hasattr(broker_adapters, "create_broker_adapter"))
        self.assertTrue(hasattr(broker_adapters, "list_supported_brokers"))

    def test_import_broker_failover(self) -> None:
        from utils.execution import broker_failover
        self.assertTrue(hasattr(broker_failover, "BrokerFailoverManager"))
        self.assertTrue(hasattr(broker_failover, "BrokerHealthTracker"))
        self.assertTrue(hasattr(broker_failover, "BrokerHealthState"))


class TestThsBrokerAdapterDryRun(unittest.TestCase):
    """测试 THS adapter dry-run 模式."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.config = {
            "live": False,  # dry-run
            "mode": "ifind",
            "account": "test_account",
            "password": "test_password",
            "audit_log_dir": self.tmpdir,
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_dry_run_connect(self) -> None:
        """dry-run 模式连接应成功."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter(self.config)
        self.assertTrue(adapter.connect())
        self.assertTrue(adapter._connected)
        self.assertFalse(adapter.is_live)

    def test_dry_run_submit_order(self) -> None:
        """dry-run 模式提交订单应记录但不实际下单."""
        from utils.execution.broker_adapters import (
            BrokerOrder,
            OrderSide,
            OrderType,
            ThsBrokerAdapter,
        )
        adapter = ThsBrokerAdapter(self.config)
        adapter.connect()
        order = BrokerOrder(
            symbol="000001",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=100,
            price=10.5,
            strategy="TEST",
        )
        ok = adapter.submit_order(order)
        self.assertTrue(ok)

    def test_dry_run_disconnect(self) -> None:
        """dry-run 模式断开连接."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter(self.config)
        adapter.connect()
        adapter.disconnect()
        self.assertFalse(adapter._connected)

    def test_dry_run_get_account_info(self) -> None:
        """dry-run 模式查询账户信息."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter(self.config)
        adapter.connect()
        info = adapter.get_account_info()
        self.assertEqual(info["broker"], "ths")
        self.assertEqual(info["mode"], "dry-run")

    def test_not_connected_raises(self) -> None:
        """未连接就下单应抛 BrokerNotConnectedError."""
        from utils.execution.broker_adapters import (
            BrokerNotConnectedError,
            BrokerOrder,
            OrderSide,
            OrderType,
            ThsBrokerAdapter,
        )
        adapter = ThsBrokerAdapter(self.config)
        # 未连接
        order = BrokerOrder(
            symbol="000001", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=100, price=10.5,
        )
        with self.assertRaises(BrokerNotConnectedError):
            adapter.submit_order(order)

    def test_audit_log_written(self) -> None:
        """审计日志应被写入 JSONL 文件."""
        from utils.execution.broker_adapters import (
            BrokerOrder,
            OrderSide,
            OrderType,
            ThsBrokerAdapter,
        )
        adapter = ThsBrokerAdapter(self.config)
        adapter.connect()
        order = BrokerOrder(
            symbol="000001", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=100, price=10.5,
        )
        adapter.submit_order(order)
        # 检查审计日志文件存在
        audit_files = list(Path(self.tmpdir).glob("ths_*.jsonl"))
        self.assertTrue(len(audit_files) > 0)
        # 检查内容 (读取最后一条记录)
        with open(audit_files[0], encoding="utf-8") as f:
            lines = f.readlines()
        record = json.loads(lines[-1])
        self.assertEqual(record["broker"], "ths")
        self.assertEqual(record["event"], "dry_run_submit")


class TestXueqiuBrokerAdapterDryRun(unittest.TestCase):
    """测试雪球 adapter dry-run 模式."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.config = {
            "live": False,
            "mode": "portfolio",
            "cookies": "test_cookies",
            "portfolio_code": "ZH123456",
            "audit_log_dir": self.tmpdir,
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_dry_run_connect(self) -> None:
        from utils.execution.broker_adapters import XueqiuBrokerAdapter
        adapter = XueqiuBrokerAdapter(self.config)
        self.assertTrue(adapter.connect())

    def test_dry_run_submit_order(self) -> None:
        from utils.execution.broker_adapters import (
            BrokerOrder,
            OrderSide,
            OrderType,
            XueqiuBrokerAdapter,
        )
        adapter = XueqiuBrokerAdapter(self.config)
        adapter.connect()
        order = BrokerOrder(
            symbol="000001", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=100, price=10.5,
        )
        ok = adapter.submit_order(order)
        self.assertTrue(ok)

    def test_broker_mode_requires_broker_param(self) -> None:
        """broker 模式必须配置 broker 参数."""
        from utils.execution.broker_adapters import (
            BrokerOrder,
            OrderSide,
            OrderStatus,
            OrderType,
            XueqiuBrokerAdapter,
        )
        config = dict(self.config)
        config["mode"] = "broker"
        config["broker"] = ""
        adapter = XueqiuBrokerAdapter(config)
        adapter.connect()  # dry-run
        # live 模式测试 (mock _do_connect)
        adapter.is_live = True
        adapter._connected = True
        adapter._session = {"mode": "broker"}
        order = BrokerOrder(
            symbol="000001", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=100, price=10.5,
        )
        # 应被拒绝 (缺 broker 参数)
        ok = adapter.submit_order(order)
        self.assertFalse(ok)
        self.assertEqual(order.status, OrderStatus.REJECTED)


class TestBrokerFactory(unittest.TestCase):
    """测试 broker 工厂函数."""

    def test_create_ths(self) -> None:
        from utils.execution.broker_adapters import (
            ThsBrokerAdapter,
            create_broker_adapter,
        )
        adapter = create_broker_adapter("ths", {"live": False})
        self.assertIsInstance(adapter, ThsBrokerAdapter)

    def test_create_xueqiu(self) -> None:
        from utils.execution.broker_adapters import (
            XueqiuBrokerAdapter,
            create_broker_adapter,
        )
        adapter = create_broker_adapter("xueqiu", {"live": False})
        self.assertIsInstance(adapter, XueqiuBrokerAdapter)

    def test_create_invalid_type_raises(self) -> None:
        from utils.execution.broker_adapters import create_broker_adapter
        with self.assertRaises(ValueError):
            create_broker_adapter("invalid_broker", {})

    def test_list_supported_brokers(self) -> None:
        from utils.execution.broker_adapters import list_supported_brokers
        brokers = list_supported_brokers()
        self.assertIn("ths", brokers)
        self.assertIn("xueqiu", brokers)
        self.assertEqual(len(brokers), 2)

    def test_register_custom_adapter(self) -> None:
        """测试动态注册 adapter."""
        from utils.execution.broker_adapters import (
            _BaseLiveAdapter,
            list_supported_brokers,
            register_broker_adapter,
        )
        # 创建自定义 adapter
        class CustomAdapter(_BaseLiveAdapter):
            def __init__(self, config):
                super().__init__("custom", config)
            def _do_connect(self): return True
            def _do_submit_order(self, order): return True
            def _do_cancel_order(self, order_id): return True
            def _do_get_positions(self): return []
            def _do_get_account_info(self): return {}
            def _do_get_market_data(self, s, p, c): return {}
        register_broker_adapter("custom", CustomAdapter)
        brokers = list_supported_brokers()
        self.assertIn("custom", brokers)

    def test_register_invalid_adapter_raises(self) -> None:
        """注册非 _BaseLiveAdapter 子类应抛 TypeError."""
        from utils.execution.broker_adapters import register_broker_adapter
        with self.assertRaises(TypeError):
            register_broker_adapter("invalid", object)


class TestBrokerHealthTracker(unittest.TestCase):
    """测试 broker 健康状态追踪器."""

    def test_initial_state_unknown(self) -> None:
        from utils.execution.broker_failover import (
            BrokerHealthState,
            BrokerHealthTracker,
        )
        tracker = BrokerHealthTracker("test")
        self.assertEqual(tracker.state, BrokerHealthState.UNKNOWN)
        self.assertTrue(tracker.is_healthy)  # 初始允许尝试

    def test_record_success_becomes_healthy(self) -> None:
        from utils.execution.broker_failover import (
            BrokerHealthState,
            BrokerHealthTracker,
        )
        tracker = BrokerHealthTracker("test", window_size=5)
        for _ in range(5):
            tracker.record_success()
        self.assertEqual(tracker.state, BrokerHealthState.HEALTHY)
        self.assertTrue(tracker.is_healthy)

    def test_record_failure_becomes_unhealthy(self) -> None:
        from utils.execution.broker_failover import (
            BrokerHealthState,
            BrokerHealthTracker,
        )
        tracker = BrokerHealthTracker(
            "test", window_size=10,
            healthy_threshold=0.9, degraded_threshold=0.7,
        )
        # 连续失败触发降级
        for _ in range(5):
            tracker.record_failure()
        # 连续失败 >= max_consecutive_failures 时立即 UNHEALTHY
        self.assertEqual(tracker.state, BrokerHealthState.UNHEALTHY)
        self.assertFalse(tracker.is_healthy)

    def test_success_rate_calculation(self) -> None:
        from utils.execution.broker_failover import BrokerHealthTracker
        tracker = BrokerHealthTracker("test", window_size=10)
        # 8 成功 + 2 失败 = 0.8
        for _ in range(8):
            tracker.record_success()
        for _ in range(2):
            tracker.record_failure()
        self.assertAlmostEqual(tracker.success_rate, 0.8, places=2)

    def test_window_size_limit(self) -> None:
        """滑动窗口大小限制."""
        from utils.execution.broker_failover import BrokerHealthTracker
        tracker = BrokerHealthTracker("test", window_size=5)
        for _ in range(10):
            tracker.record_success()
        # 只保留最近 5 次
        self.assertEqual(len(tracker._results), 5)

    def test_to_dict(self) -> None:
        from utils.execution.broker_failover import BrokerHealthTracker
        tracker = BrokerHealthTracker("test")
        d = tracker.to_dict()
        self.assertEqual(d["broker_name"], "test")
        self.assertIn("state", d)
        self.assertIn("success_rate", d)


class TestBrokerFailoverManager(unittest.TestCase):
    """测试故障切换管理器."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.config = {
            "brokers": [
                {
                    "name": "primary", "type": "ths", "priority": 1,
                    "config": {"live": False, "audit_log_dir": self.tmpdir},
                },
                {
                    "name": "secondary", "type": "xueqiu", "priority": 2,
                    "config": {"live": False, "audit_log_dir": self.tmpdir},
                },
            ],
            "health_check": {
                "window_size": 10,
                "healthy_threshold": 0.9,
                "degraded_threshold": 0.7,
            },
            "failover": {
                "auto_failover": True,
                "max_failover_count": 3,
                "recovery_check_interval_sec": 1,  # 测试用短间隔
            },
            "audit_log_dir": self.tmpdir,
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_init_selects_initial_broker(self) -> None:
        from utils.execution.broker_failover import BrokerFailoverManager
        mgr = BrokerFailoverManager(self.config)
        self.assertEqual(mgr.get_active_broker_name(), "primary")
        mgr.stop()

    def test_no_brokers_raises(self) -> None:
        from utils.execution.broker_failover import (
            BrokerConfigError,
            BrokerFailoverManager,
        )
        with self.assertRaises(BrokerConfigError):
            BrokerFailoverManager({"brokers": []})

    def test_invalid_broker_type_raises(self) -> None:
        from utils.execution.broker_failover import (
            BrokerConfigError,
            BrokerFailoverManager,
        )
        config = {
            "brokers": [{
                "name": "bad", "type": "invalid_type",
                "priority": 1, "config": {},
            }],
        }
        with self.assertRaises(BrokerConfigError):
            BrokerFailoverManager(config)

    def test_get_active_broker(self) -> None:
        from utils.execution.broker_failover import BrokerFailoverManager
        mgr = BrokerFailoverManager(self.config)
        broker = mgr.get_active_broker()
        self.assertIsNotNone(broker)
        mgr.stop()

    def test_record_result_updates_tracker(self) -> None:
        from utils.execution.broker_failover import BrokerFailoverManager
        mgr = BrokerFailoverManager(self.config)
        mgr.record_result("primary", True)
        mgr.record_result("primary", True)
        status = mgr.get_status()
        primary = next(b for b in status["brokers"] if b["name"] == "primary")
        self.assertGreater(primary["success_rate"], 0)
        mgr.stop()

    def test_force_failover(self) -> None:
        """测试强制故障切换."""
        from utils.execution.broker_failover import BrokerFailoverManager
        mgr = BrokerFailoverManager(self.config)
        self.assertEqual(mgr.get_active_broker_name(), "primary")
        ok = mgr.force_failover(target_broker="secondary", reason="test")
        self.assertTrue(ok)
        self.assertEqual(mgr.get_active_broker_name(), "secondary")
        mgr.stop()

    def test_auto_failover_on_failures(self) -> None:
        """测试连续失败触发自动故障切换."""
        from utils.execution.broker_failover import BrokerFailoverManager
        mgr = BrokerFailoverManager(self.config)
        # 连续失败触发降级
        for _ in range(5):
            mgr.record_result("primary", False)
        # 应自动切换到 secondary
        self.assertEqual(mgr.get_active_broker_name(), "secondary")
        mgr.stop()

    def test_failover_limit(self) -> None:
        """测试故障切换次数上限."""
        config = dict(self.config)
        config["failover"] = {
            "auto_failover": True,
            "max_failover_count": 1,
            "recovery_check_interval_sec": 1,
        }
        from utils.execution.broker_failover import BrokerFailoverManager
        mgr = BrokerFailoverManager(config)
        # 第一次切换 primary -> secondary
        for _ in range(5):
            mgr.record_result("primary", False)
        self.assertEqual(mgr._failover_count, 1)
        # secondary 也失败, 应不再切换 (达到上限)
        for _ in range(5):
            mgr.record_result("secondary", False)
        self.assertEqual(mgr._failover_count, 1)
        mgr.stop()

    def test_get_status(self) -> None:
        from utils.execution.broker_failover import BrokerFailoverManager
        mgr = BrokerFailoverManager(self.config)
        status = mgr.get_status()
        self.assertIn("active_broker", status)
        self.assertIn("failover_count", status)
        self.assertIn("brokers", status)
        self.assertEqual(len(status["brokers"]), 2)
        mgr.stop()

    def test_audit_log_written(self) -> None:
        """故障切换审计日志应被写入."""
        from utils.execution.broker_failover import BrokerFailoverManager
        mgr = BrokerFailoverManager(self.config)
        mgr.force_failover("secondary", reason="test_audit")
        # 检查审计日志
        audit_files = list(Path(self.tmpdir).glob("failover_*.jsonl"))
        self.assertTrue(len(audit_files) > 0)
        # 读取最后一条记录 (前面可能有 initial_broker_selected 等记录)
        with open(audit_files[0], encoding="utf-8") as f:
            lines = f.readlines()
        # 找到 force_failover 事件
        force_failover_records = [
            json.loads(line) for line in lines
            if json.loads(line).get("event") == "force_failover"
        ]
        self.assertTrue(len(force_failover_records) > 0)
        record = force_failover_records[-1]
        self.assertEqual(record["event"], "force_failover")
        self.assertEqual(record["from"], "primary")
        self.assertEqual(record["to"], "secondary")
        mgr.stop()

    def test_start_stop(self) -> None:
        """测试启动和停止."""
        from utils.execution.broker_failover import BrokerFailoverManager
        mgr = BrokerFailoverManager(self.config)
        mgr.start()
        self.assertFalse(mgr._stopped)
        mgr.stop()
        self.assertTrue(mgr._stopped)


class TestBrokerFailoverGlobal(unittest.TestCase):
    """测试全局故障切换管理器."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        from utils.execution.broker_failover import shutdown_failover_manager
        shutdown_failover_manager()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_initialize_and_get(self) -> None:
        from utils.execution.broker_failover import (
            get_failover_manager,
            initialize_failover_manager,
        )
        config = {
            "brokers": [
                {"name": "p", "type": "ths", "priority": 1,
                 "config": {"live": False, "audit_log_dir": self.tmpdir}},
            ],
            "audit_log_dir": self.tmpdir,
        }
        mgr = initialize_failover_manager(config)
        self.assertIs(get_failover_manager(), mgr)
        mgr.stop()

    def test_get_without_init_raises(self) -> None:
        from utils.execution.broker_failover import (
            get_failover_manager,
            shutdown_failover_manager,
        )
        shutdown_failover_manager()
        with self.assertRaises(RuntimeError):
            get_failover_manager()


# ============================================================
# 补充测试: 提升 broker_adapters.py 覆盖率到 70%+
# ============================================================


class TestThsLiveModeMocked(unittest.TestCase):
    """测试 THS adapter 实盘模式 (mock _do_* 方法)."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.config = {
            "live": True,  # 实盘模式
            "mode": "ifind",
            "account": "test_account",
            "password": "test_password",
            "audit_log_dir": self.tmpdir,
            "daily_trade_limit": 1_000_000,
            "circuit_breaker_threshold": 0.03,
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_live_connect_success(self) -> None:
        """实盘连接成功."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter(self.config)
        # mock _do_connect 返回 True
        adapter._do_connect = lambda: True  # type: ignore
        self.assertTrue(adapter.connect())
        self.assertTrue(adapter._connected)
        self.assertTrue(adapter.is_live)

    def test_live_connect_failure(self) -> None:
        """实盘连接失败."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter(self.config)
        adapter._do_connect = lambda: False  # type: ignore
        self.assertFalse(adapter.connect())
        self.assertFalse(adapter._connected)

    def test_live_connect_exception(self) -> None:
        """实盘连接抛异常应被捕获."""
        from utils.execution.broker_adapters import ThsBrokerAdapter

        def raise_exc() -> bool:
            raise RuntimeError("connect failed")
        adapter = ThsBrokerAdapter(self.config)
        adapter._do_connect = raise_exc  # type: ignore
        self.assertFalse(adapter.connect())
        self.assertFalse(adapter._connected)

    def test_live_submit_order_success(self) -> None:
        """实盘下单成功."""
        from utils.execution.broker_adapters import (
            BrokerOrder,
            OrderSide,
            OrderStatus,
            OrderType,
            ThsBrokerAdapter,
        )
        adapter = ThsBrokerAdapter(self.config)
        adapter._do_connect = lambda: True  # type: ignore
        adapter.connect()
        # mock _do_submit_order
        def _submit(order):
            order.status = OrderStatus.SUBMITTED
            return True
        adapter._do_submit_order = _submit  # type: ignore
        order = BrokerOrder(
            symbol="000001", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=100, price=10.5,
        )
        self.assertTrue(adapter.submit_order(order))
        self.assertEqual(order.status, OrderStatus.SUBMITTED)
        # 验证日交易额累计
        self.assertAlmostEqual(adapter._daily_trade_amount, 100 * 10.5)

    def test_live_submit_order_exception(self) -> None:
        """实盘下单异常应被捕获."""
        from utils.execution.broker_adapters import (
            BrokerOrder,
            OrderSide,
            OrderStatus,
            OrderType,
            ThsBrokerAdapter,
        )
        adapter = ThsBrokerAdapter(self.config)
        adapter._do_connect = lambda: True  # type: ignore
        adapter.connect()

        def _submit(order):
            raise RuntimeError("submit failed")
        adapter._do_submit_order = _submit  # type: ignore
        order = BrokerOrder(
            symbol="000001", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=100, price=10.5,
        )
        self.assertFalse(adapter.submit_order(order))
        self.assertEqual(order.status, OrderStatus.ERROR)
        self.assertIn("submit failed", order.rejection_reason or "")

    def test_live_cancel_order_success(self) -> None:
        """实盘撤单."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter(self.config)
        adapter._do_connect = lambda: True  # type: ignore
        adapter._do_cancel_order = lambda oid: True  # type: ignore
        adapter.connect()
        self.assertTrue(adapter.cancel_order("order_001"))

    def test_live_cancel_order_exception(self) -> None:
        """实盘撤单异常."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter(self.config)
        adapter._do_connect = lambda: True  # type: ignore

        def _cancel(oid: str) -> bool:
            raise RuntimeError("cancel failed")
        adapter._do_cancel_order = _cancel  # type: ignore
        adapter.connect()
        self.assertFalse(adapter.cancel_order("order_001"))

    def test_live_get_positions(self) -> None:
        """实盘查询持仓."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter(self.config)
        adapter._do_connect = lambda: True  # type: ignore
        adapter._do_get_positions = lambda: [{"symbol": "000001", "qty": 100}]  # type: ignore
        adapter.connect()
        positions = adapter.get_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["symbol"], "000001")

    def test_live_get_positions_exception(self) -> None:
        """实盘查询持仓异常应返回空列表."""
        from utils.execution.broker_adapters import ThsBrokerAdapter

        def _get_positions() -> list:
            raise RuntimeError("query failed")
        adapter = ThsBrokerAdapter(self.config)
        adapter._do_connect = lambda: True  # type: ignore
        adapter._do_get_positions = _get_positions  # type: ignore
        adapter.connect()
        self.assertEqual(adapter.get_positions(), [])

    def test_live_get_account_info(self) -> None:
        """实盘查询账户信息."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter(self.config)
        adapter._do_connect = lambda: True  # type: ignore
        adapter._do_get_account_info = lambda: {"balance": 1000000}  # type: ignore
        adapter.connect()
        info = adapter.get_account_info()
        self.assertEqual(info["broker"], "ths")
        self.assertEqual(info["balance"], 1000000)
        self.assertIn("daily_trade_amount", info)

    def test_live_get_account_info_exception(self) -> None:
        """实盘查询账户异常."""
        from utils.execution.broker_adapters import ThsBrokerAdapter

        def _get_info() -> dict:
            raise RuntimeError("info failed")
        adapter = ThsBrokerAdapter(self.config)
        adapter._do_connect = lambda: True  # type: ignore
        adapter._do_get_account_info = _get_info  # type: ignore
        adapter.connect()
        info = adapter.get_account_info()
        self.assertEqual(info["broker"], "ths")
        self.assertIn("error", info)

    def test_live_get_market_data(self) -> None:
        """实盘获取行情."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter(self.config)
        adapter._do_connect = lambda: True  # type: ignore
        adapter._do_get_market_data = lambda s, p, c: {"symbol": s, "data": [{"close": 10.5}]}  # type: ignore
        adapter.connect()
        data = adapter.get_market_data("000001")
        self.assertEqual(data["symbol"], "000001")
        self.assertEqual(len(data["data"]), 1)

    def test_live_get_market_data_exception(self) -> None:
        """实盘获取行情异常."""
        from utils.execution.broker_adapters import ThsBrokerAdapter

        def _get_md(s: str, p: str, c: int) -> dict:
            raise RuntimeError("md failed")
        adapter = ThsBrokerAdapter(self.config)
        adapter._do_connect = lambda: True  # type: ignore
        adapter._do_get_market_data = _get_md  # type: ignore
        adapter.connect()
        data = adapter.get_market_data("000001")
        self.assertIn("error", data)

    def test_dry_run_cancel_order(self) -> None:
        """dry-run 撤单."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        cfg = dict(self.config)
        cfg["live"] = False
        adapter = ThsBrokerAdapter(cfg)
        adapter.connect()
        self.assertTrue(adapter.cancel_order("order_001"))

    def test_dry_run_get_positions_returns_empty(self) -> None:
        """dry-run 查询持仓返回空列表."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        cfg = dict(self.config)
        cfg["live"] = False
        adapter = ThsBrokerAdapter(cfg)
        adapter.connect()
        self.assertEqual(adapter.get_positions(), [])

    def test_dry_run_disconnect(self) -> None:
        """dry-run disconnect."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        cfg = dict(self.config)
        cfg["live"] = False
        adapter = ThsBrokerAdapter(cfg)
        adapter.connect()
        adapter.disconnect()
        self.assertFalse(adapter._connected)

    def test_disconnect_not_connected_no_op(self) -> None:
        """未连接时 disconnect 是 no-op."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        cfg = dict(self.config)
        cfg["live"] = False
        adapter = ThsBrokerAdapter(cfg)
        # 未连接直接 disconnect 不应抛异常
        adapter.disconnect()
        self.assertFalse(adapter._connected)

    def test_disconnect_with_exception(self) -> None:
        """disconnect 时 _do_disconnect 抛异常应被捕获."""
        from utils.execution.broker_adapters import ThsBrokerAdapter

        def _disconnect() -> None:
            raise RuntimeError("disconnect failed")
        adapter = ThsBrokerAdapter(self.config)
        adapter._do_connect = lambda: True  # type: ignore
        adapter._do_disconnect = _disconnect  # type: ignore
        adapter.connect()
        # 不应抛异常
        adapter.disconnect()
        self.assertFalse(adapter._connected)

    def test_get_positions_not_connected_raises(self) -> None:
        """未连接查询持仓应抛异常."""
        from utils.execution.broker_adapters import (
            BrokerNotConnectedError,
            ThsBrokerAdapter,
        )
        adapter = ThsBrokerAdapter(self.config)
        with self.assertRaises(BrokerNotConnectedError):
            adapter.get_positions()

    def test_get_account_info_not_connected_raises(self) -> None:
        """未连接查询账户应抛异常."""
        from utils.execution.broker_adapters import (
            BrokerNotConnectedError,
            ThsBrokerAdapter,
        )
        adapter = ThsBrokerAdapter(self.config)
        with self.assertRaises(BrokerNotConnectedError):
            adapter.get_account_info()

    def test_get_market_data_not_connected_raises(self) -> None:
        """未连接获取行情应抛异常."""
        from utils.execution.broker_adapters import (
            BrokerNotConnectedError,
            ThsBrokerAdapter,
        )
        adapter = ThsBrokerAdapter(self.config)
        with self.assertRaises(BrokerNotConnectedError):
            adapter.get_market_data("000001")

    def test_cancel_order_not_connected_raises(self) -> None:
        """未连接撤单应抛异常."""
        from utils.execution.broker_adapters import (
            BrokerNotConnectedError,
            ThsBrokerAdapter,
        )
        adapter = ThsBrokerAdapter(self.config)
        with self.assertRaises(BrokerNotConnectedError):
            adapter.cancel_order("order_001")


class TestThsIfindConnectPaths(unittest.TestCase):
    """测试 THS iFinD 模式连接路径."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_ifind_connect_no_credentials(self) -> None:
        """iFinD 模式缺凭证返回 False."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "ifind", "audit_log_dir": self.tmpdir,
        })
        # account 和 password 都为空
        self.assertFalse(adapter._connect_ifind())

    def test_ifind_connect_with_credentials(self) -> None:
        """iFinD 模式有凭证应返回 True (TODO 实际接入)."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "ifind", "account": "acc",
            "password": "pwd", "audit_log_dir": self.tmpdir,
        })
        self.assertTrue(adapter._connect_ifind())
        self.assertIsNotNone(adapter._api_client)

    def test_gui_connect_no_client_path(self) -> None:
        """GUI 模式缺 client_path 返回 False."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "gui", "audit_log_dir": self.tmpdir,
        })
        self.assertFalse(adapter._connect_gui())

    def test_gui_connect_with_client_path(self) -> None:
        """GUI 模式有 client_path 返回 True."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "gui", "client_path": "C:\\ths\\client.exe",
            "audit_log_dir": self.tmpdir,
        })
        self.assertTrue(adapter._connect_gui())
        self.assertIsNotNone(adapter._gui_client)

    def test_invalid_mode(self) -> None:
        """不支持的 mode 应返回 False."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "invalid_mode", "audit_log_dir": self.tmpdir,
        })
        self.assertFalse(adapter._do_connect())

    def test_do_disconnect_clears_clients(self) -> None:
        """_do_disconnect 应清空 api_client 和 gui_client."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "ifind", "account": "acc",
            "password": "pwd", "audit_log_dir": self.tmpdir,
        })
        adapter._connect_ifind()
        self.assertIsNotNone(adapter._api_client)
        adapter._do_disconnect()
        self.assertIsNone(adapter._api_client)

    def test_do_submit_order_ifind(self) -> None:
        """iFinD 模式下单."""
        from utils.execution.broker_adapters import (
            BrokerOrder,
            OrderSide,
            OrderStatus,
            OrderType,
            ThsBrokerAdapter,
        )
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "ifind", "account": "acc",
            "password": "pwd", "audit_log_dir": self.tmpdir,
        })
        adapter._connect_ifind()
        order = BrokerOrder(
            symbol="000001", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=100, price=10.5,
        )
        self.assertTrue(adapter._do_submit_order(order))
        self.assertEqual(order.status, OrderStatus.SUBMITTED)

    def test_do_submit_order_gui(self) -> None:
        """GUI 模式下单."""
        from utils.execution.broker_adapters import (
            BrokerOrder,
            OrderSide,
            OrderStatus,
            OrderType,
            ThsBrokerAdapter,
        )
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "gui", "client_path": "C:\\ths\\client.exe",
            "audit_log_dir": self.tmpdir,
        })
        adapter._connect_gui()
        order = BrokerOrder(
            symbol="000001", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=100, price=10.5,
        )
        self.assertTrue(adapter._do_submit_order(order))
        self.assertEqual(order.status, OrderStatus.SUBMITTED)

    def test_do_submit_order_no_client(self) -> None:
        """未连接 api_client/gui_client 时下单应被拒绝."""
        from utils.execution.broker_adapters import (
            BrokerOrder,
            OrderSide,
            OrderStatus,
            OrderType,
            ThsBrokerAdapter,
        )
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "ifind", "audit_log_dir": self.tmpdir,
        })
        order = BrokerOrder(
            symbol="000001", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=100, price=10.5,
        )
        self.assertFalse(adapter._do_submit_order(order))
        self.assertEqual(order.status, OrderStatus.REJECTED)

    def test_do_cancel_order_ifind(self) -> None:
        """iFinD 模式撤单."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "ifind", "account": "acc",
            "password": "pwd", "audit_log_dir": self.tmpdir,
        })
        adapter._connect_ifind()
        self.assertTrue(adapter._do_cancel_order("order_001"))

    def test_do_cancel_order_gui(self) -> None:
        """GUI 模式撤单."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "gui", "client_path": "C:\\ths\\client.exe",
            "audit_log_dir": self.tmpdir,
        })
        adapter._connect_gui()
        self.assertTrue(adapter._do_cancel_order("order_001"))

    def test_do_cancel_order_no_client(self) -> None:
        """未连接时撤单返回 False."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "ifind", "audit_log_dir": self.tmpdir,
        })
        self.assertFalse(adapter._do_cancel_order("order_001"))

    def test_do_get_positions_empty(self) -> None:
        """未连接时查询持仓返回空列表."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "ifind", "audit_log_dir": self.tmpdir,
        })
        self.assertEqual(adapter._do_get_positions(), [])

    def test_do_get_positions_with_client(self) -> None:
        """已连接时查询持仓返回空列表 (TODO 实际接入)."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "ifind", "account": "acc",
            "password": "pwd", "audit_log_dir": self.tmpdir,
        })
        adapter._connect_ifind()
        self.assertEqual(adapter._do_get_positions(), [])

    def test_do_get_account_info_no_client(self) -> None:
        """未连接时查询账户返回 ready=False."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "ifind", "audit_log_dir": self.tmpdir,
        })
        info = adapter._do_get_account_info()
        self.assertFalse(info["ready"])

    def test_do_get_account_info_with_client(self) -> None:
        """已连接时查询账户返回 ready=True."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "ifind", "account": "acc",
            "password": "pwd", "audit_log_dir": self.tmpdir,
        })
        adapter._connect_ifind()
        info = adapter._do_get_account_info()
        self.assertTrue(info["ready"])
        self.assertEqual(info["account"], "acc")

    def test_do_get_market_data_no_client(self) -> None:
        """未连接时获取行情返回错误."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "ifind", "audit_log_dir": self.tmpdir,
        })
        data = adapter._do_get_market_data("000001", "1d", 100)
        self.assertIn("error", data)

    def test_do_get_market_data_with_client(self) -> None:
        """已连接时获取行情返回空数据 (TODO 实际接入)."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "ifind", "account": "acc",
            "password": "pwd", "audit_log_dir": self.tmpdir,
        })
        adapter._connect_ifind()
        data = adapter._do_get_market_data("000001", "1d", 100)
        self.assertEqual(data["source"], "ifind")


class TestXueqiuLiveModeMocked(unittest.TestCase):
    """测试雪球 adapter 实盘模式."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_connect_no_cookies(self) -> None:
        """缺 cookies 返回 False."""
        from utils.execution.broker_adapters import XueqiuBrokerAdapter
        adapter = XueqiuBrokerAdapter({
            "live": True, "mode": "portfolio", "audit_log_dir": self.tmpdir,
        })
        self.assertFalse(adapter._do_connect())

    def test_connect_with_cookies(self) -> None:
        """有 cookies 应连接成功."""
        from utils.execution.broker_adapters import XueqiuBrokerAdapter
        adapter = XueqiuBrokerAdapter({
            "live": True, "mode": "portfolio", "cookies": "test_cookies",
            "portfolio_code": "ZH123456", "audit_log_dir": self.tmpdir,
        })
        self.assertTrue(adapter._do_connect())
        self.assertIsNotNone(adapter._session)

    def test_do_disconnect_clears_session(self) -> None:
        """disconnect 清空 session."""
        from utils.execution.broker_adapters import XueqiuBrokerAdapter
        adapter = XueqiuBrokerAdapter({
            "live": True, "mode": "portfolio", "cookies": "test_cookies",
            "portfolio_code": "ZH123456", "audit_log_dir": self.tmpdir,
        })
        adapter._do_connect()
        adapter._do_disconnect()
        self.assertIsNone(adapter._session)

    def test_submit_order_portfolio_mode(self) -> None:
        """组合模式下单."""
        from utils.execution.broker_adapters import (
            BrokerOrder,
            OrderSide,
            OrderStatus,
            OrderType,
            XueqiuBrokerAdapter,
        )
        adapter = XueqiuBrokerAdapter({
            "live": True, "mode": "portfolio", "cookies": "test_cookies",
            "portfolio_code": "ZH123456", "audit_log_dir": self.tmpdir,
        })
        adapter._do_connect()
        order = BrokerOrder(
            symbol="000001", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=100, price=10.5,
        )
        self.assertTrue(adapter._do_submit_order(order))
        self.assertEqual(order.status, OrderStatus.SUBMITTED)

    def test_submit_order_broker_mode(self) -> None:
        """券商模式下单."""
        from utils.execution.broker_adapters import (
            BrokerOrder,
            OrderSide,
            OrderStatus,
            OrderType,
            XueqiuBrokerAdapter,
        )
        adapter = XueqiuBrokerAdapter({
            "live": True, "mode": "broker", "cookies": "test_cookies",
            "broker": "东方财富", "audit_log_dir": self.tmpdir,
        })
        adapter._do_connect()
        order = BrokerOrder(
            symbol="000001", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=100, price=10.5,
        )
        self.assertTrue(adapter._do_submit_order(order))
        self.assertEqual(order.status, OrderStatus.SUBMITTED)

    def test_submit_order_no_session(self) -> None:
        """未建立 session 时下单应被拒绝."""
        from utils.execution.broker_adapters import (
            BrokerOrder,
            OrderSide,
            OrderStatus,
            OrderType,
            XueqiuBrokerAdapter,
        )
        adapter = XueqiuBrokerAdapter({
            "live": True, "mode": "portfolio", "cookies": "test_cookies",
            "audit_log_dir": self.tmpdir,
        })
        order = BrokerOrder(
            symbol="000001", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=100, price=10.5,
        )
        self.assertFalse(adapter._do_submit_order(order))
        self.assertEqual(order.status, OrderStatus.REJECTED)

    def test_submit_order_unknown_mode(self) -> None:
        """未知 mode 应被拒绝."""
        from utils.execution.broker_adapters import (
            BrokerOrder,
            OrderSide,
            OrderStatus,
            OrderType,
            XueqiuBrokerAdapter,
        )
        adapter = XueqiuBrokerAdapter({
            "live": True, "mode": "unknown", "cookies": "test_cookies",
            "audit_log_dir": self.tmpdir,
        })
        adapter._do_connect()
        order = BrokerOrder(
            symbol="000001", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=100, price=10.5,
        )
        self.assertFalse(adapter._do_submit_order(order))
        self.assertEqual(order.status, OrderStatus.REJECTED)

    def test_cancel_order_no_session(self) -> None:
        """未建立 session 时撤单返回 False."""
        from utils.execution.broker_adapters import XueqiuBrokerAdapter
        adapter = XueqiuBrokerAdapter({
            "live": True, "mode": "portfolio", "cookies": "test_cookies",
            "audit_log_dir": self.tmpdir,
        })
        self.assertFalse(adapter._do_cancel_order("order_001"))

    def test_cancel_order_with_session(self) -> None:
        """已建立 session 时撤单返回 True."""
        from utils.execution.broker_adapters import XueqiuBrokerAdapter
        adapter = XueqiuBrokerAdapter({
            "live": True, "mode": "portfolio", "cookies": "test_cookies",
            "portfolio_code": "ZH123456", "audit_log_dir": self.tmpdir,
        })
        adapter._do_connect()
        self.assertTrue(adapter._do_cancel_order("order_001"))

    def test_get_positions_no_session(self) -> None:
        """未建立 session 时查询持仓返回空列表."""
        from utils.execution.broker_adapters import XueqiuBrokerAdapter
        adapter = XueqiuBrokerAdapter({
            "live": True, "mode": "portfolio", "cookies": "test_cookies",
            "audit_log_dir": self.tmpdir,
        })
        self.assertEqual(adapter._do_get_positions(), [])

    def test_get_positions_with_session(self) -> None:
        """已建立 session 时查询持仓返回空列表."""
        from utils.execution.broker_adapters import XueqiuBrokerAdapter
        adapter = XueqiuBrokerAdapter({
            "live": True, "mode": "portfolio", "cookies": "test_cookies",
            "portfolio_code": "ZH123456", "audit_log_dir": self.tmpdir,
        })
        adapter._do_connect()
        self.assertEqual(adapter._do_get_positions(), [])

    def test_get_account_info_no_session(self) -> None:
        """未建立 session 时查询账户返回 ready=False."""
        from utils.execution.broker_adapters import XueqiuBrokerAdapter
        adapter = XueqiuBrokerAdapter({
            "live": True, "mode": "portfolio", "cookies": "test_cookies",
            "audit_log_dir": self.tmpdir,
        })
        info = adapter._do_get_account_info()
        self.assertFalse(info["ready"])

    def test_get_account_info_with_session(self) -> None:
        """已建立 session 时查询账户返回 ready=True."""
        from utils.execution.broker_adapters import XueqiuBrokerAdapter
        adapter = XueqiuBrokerAdapter({
            "live": True, "mode": "portfolio", "cookies": "test_cookies",
            "portfolio_code": "ZH123456", "audit_log_dir": self.tmpdir,
        })
        adapter._do_connect()
        info = adapter._do_get_account_info()
        self.assertTrue(info["ready"])
        self.assertEqual(info["portfolio_code"], "ZH123456")

    def test_get_market_data_no_session(self) -> None:
        """未建立 session 时获取行情返回错误."""
        from utils.execution.broker_adapters import XueqiuBrokerAdapter
        adapter = XueqiuBrokerAdapter({
            "live": True, "mode": "portfolio", "cookies": "test_cookies",
            "audit_log_dir": self.tmpdir,
        })
        data = adapter._do_get_market_data("000001", "1d", 100)
        self.assertIn("error", data)

    def test_get_market_data_with_session(self) -> None:
        """已建立 session 时获取行情返回 source=xueqiu."""
        from utils.execution.broker_adapters import XueqiuBrokerAdapter
        adapter = XueqiuBrokerAdapter({
            "live": True, "mode": "portfolio", "cookies": "test_cookies",
            "portfolio_code": "ZH123456", "audit_log_dir": self.tmpdir,
        })
        adapter._do_connect()
        data = adapter._do_get_market_data("000001", "1d", 100)
        self.assertEqual(data["source"], "xueqiu")


class TestPreTradeCheck(unittest.TestCase):
    """测试风控前置检查."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_dry_run_skips_check(self) -> None:
        """dry-run 模式应跳过风控检查."""
        from utils.execution.broker_adapters import ThsBrokerAdapter
        adapter = ThsBrokerAdapter({
            "live": False, "mode": "ifind", "audit_log_dir": self.tmpdir,
        })
        # dry-run 模式 _pre_trade_check 总是返回 True
        from utils.execution.broker_adapters import (
            BrokerOrder,
            OrderSide,
            OrderType,
        )
        order = BrokerOrder(
            symbol="000001", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=10000, price=1000.0,  # 巨额
        )
        self.assertTrue(adapter._pre_trade_check(order))

    def test_daily_limit_exceeded(self) -> None:
        """超出单日限额应被拒绝."""
        from utils.execution.broker_adapters import (
            BrokerOrder,
            OrderSide,
            OrderStatus,
            OrderType,
            ThsBrokerAdapter,
        )
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "ifind", "account": "acc",
            "password": "pwd", "audit_log_dir": self.tmpdir,
            "daily_trade_limit": 1000.0,  # 1000元限额
        })
        order = BrokerOrder(
            symbol="000001", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=100, price=10.5,  # 1050元
        )
        self.assertFalse(adapter._pre_trade_check(order))
        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertIn("单日交易限额", order.rejection_reason or "")

    def test_circuit_breaker_triggered(self) -> None:
        """熔断状态应拒绝下单."""
        from utils.execution.broker_adapters import (
            BrokerOrder,
            OrderSide,
            OrderStatus,
            OrderType,
            ThsBrokerAdapter,
        )
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "ifind", "account": "acc",
            "password": "pwd", "audit_log_dir": self.tmpdir,
            "daily_trade_limit": 1_000_000,
        })
        # mock _is_circuit_broken 返回 True
        adapter._is_circuit_broken = lambda: True  # type: ignore
        order = BrokerOrder(
            symbol="000001", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=100, price=10.5,
        )
        self.assertFalse(adapter._pre_trade_check(order))
        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertIn("熔断", order.rejection_reason or "")

    def test_daily_limit_resets_next_day(self) -> None:
        """日交易额应跨日重置."""
        from utils.execution.broker_adapters import (
            BrokerOrder,
            OrderSide,
            OrderType,
            ThsBrokerAdapter,
        )
        adapter = ThsBrokerAdapter({
            "live": True, "mode": "ifind", "account": "acc",
            "password": "pwd", "audit_log_dir": self.tmpdir,
            "daily_trade_limit": 1_000_000,
        })
        # 模拟昨天已交易
        adapter._daily_trade_date = "2020-01-01"
        adapter._daily_trade_amount = 999_999.0
        order = BrokerOrder(
            symbol="000001", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=100, price=10.5,
        )
        # 今日下单应重置 (不走单日限额分支)
        self.assertTrue(adapter._pre_trade_check(order))
        # _daily_trade_date 应被更新为今天
        from datetime import datetime as _dt
        self.assertEqual(adapter._daily_trade_date, _dt.utcnow().strftime("%Y-%m-%d"))


class TestFactoryEdgeCases(unittest.TestCase):
    """测试工厂函数边界情况."""

    def test_create_with_none_config(self) -> None:
        """config=None 应使用默认空字典."""
        from utils.execution.broker_adapters import (
            ThsBrokerAdapter,
            create_broker_adapter,
        )
        adapter = create_broker_adapter("ths", None)
        self.assertIsInstance(adapter, ThsBrokerAdapter)

    def test_create_uppercase_type(self) -> None:
        """broker_type 大写应能识别."""
        from utils.execution.broker_adapters import (
            ThsBrokerAdapter,
            create_broker_adapter,
        )
        adapter = create_broker_adapter("THS", {"live": False})
        self.assertIsInstance(adapter, ThsBrokerAdapter)

    def test_register_invalid_adapter_raises(self) -> None:
        """注册非 _BaseLiveAdapter 子类应抛 TypeError."""
        from utils.execution.broker_adapters import register_broker_adapter

        class NotAnAdapter:
            pass
        with self.assertRaises(TypeError):
            register_broker_adapter("invalid", NotAnAdapter)  # type: ignore


if __name__ == "__main__":
    unittest.main()
