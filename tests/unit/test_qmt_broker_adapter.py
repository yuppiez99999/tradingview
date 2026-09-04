"""QMT 券商适配器插件单元测试.

覆盖重点 (安全铁律「永不裸实盘」的回归防线):
    - 插件注册: import 后 qmt 出现在 list_supported_brokers()
    - dry-run: 不触碰 xtquant, submit_order 只写审计
    - 负向1: live=True 但 TRADING_ENV≠production → connect() 必须 False
    - 负向2: live=True + TRADING_ENV=production 但 xtquant 缺失 → connect() 必须 False
    - 订单映射: BrokerOrder → QmtBrokerAPI.place 参数正确 (含 VWAP→LIMIT 降级)
    - 撤单映射: v8.3 order_id → QMT 委托编号
    - 持仓/账户: QMT 返回值 → v8.3 契约转换
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.execution import broker_adapters as ba  # noqa: E402
from utils.execution import qmt_broker_adapter as qa  # noqa: E402


class _FakeQmtAPI:
    """QmtBrokerAPI 替身 (记录调用参数, 不触碰 xtquant)."""

    def __init__(self, place_result: Any = None, positions: dict | None = None) -> None:
        self.connected = True
        self.place_calls: list[dict] = []
        self.cancel_calls: list[str] = []
        self.disconnected = False
        self._place_result = place_result
        self._positions = positions or {}

    def connect(self) -> bool:
        self.connected = True
        return True

    def disconnect(self) -> bool:
        self.disconnected = True
        self.connected = False
        return True

    def place(self, symbol, qty, side, order_type="LIMIT", price=0.0, **kw) -> Any:
        self.place_calls.append(
            {
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "order_type": order_type,
                "price": price,
            }
        )
        return self._place_result

    def cancel(self, order: Any) -> bool:
        self.cancel_calls.append(order.order_id)
        return True

    def get_positions(self) -> dict[str, int]:
        return dict(self._positions)

    def get_account_info(self) -> dict:
        return {"available": 100000.0, "total": 500000.0}

    def get_order_book(self, symbol: str) -> Any:
        return None


class _FakePlacedOrder:
    """place() 返回的 Order 替身."""

    def __init__(self, order_id: str) -> None:
        self.order_id = order_id


def _make_adapter(**cfg: Any) -> Any:
    """构造 dry-run 适配器 (审计目录指向临时目录, 不污染 reports/)."""
    config = {
        "live": False,
        "account_id": "test_account",
        "session_id": 123456,
        "audit_log_dir": tempfile.mkdtemp(),
    }
    config.update(cfg)
    return qa.QmtBrokerAdapter(config)


def _make_broker_order(**kw: Any) -> Any:
    params: dict[str, Any] = {
        "symbol": "510300.SH",
        "side": ba.OrderSide.BUY,
        "order_type": ba.OrderType.LIMIT,
        "quantity": 1000,
        "price": 4.5,
    }
    params.update(kw)
    return ba.BrokerOrder(**params)


class TestQmtPluginRegistration(unittest.TestCase):
    """插件注册."""

    def test_qmt_registered_in_registry(self) -> None:
        self.assertIn("qmt", ba.list_supported_brokers())

    def test_create_via_factory(self) -> None:
        adapter = ba.create_broker_adapter("qmt", {"live": False})
        self.assertIsInstance(adapter, qa.QmtBrokerAdapter)

    def test_is_qmt_available_never_raises(self) -> None:
        # 本机 xtquant 未安装 → 应返回 False 且不抛异常
        self.assertIsInstance(qa.is_qmt_available(), bool)

    def test_broker_factory_get_broker_adapter(self) -> None:
        from utils.execution import broker_factory

        adapter = broker_factory.get_broker_adapter("qmt", {"live": False})
        self.assertIsInstance(adapter, qa.QmtBrokerAdapter)
        self.assertFalse(adapter.is_live)


class TestQmtDryRun(unittest.TestCase):
    """dry-run 模式: 不触碰真实券商."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.adapter = _make_adapter(audit_log_dir=self.tmpdir)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_connect_in_dry_run(self) -> None:
        self.assertTrue(self.adapter.connect())
        self.assertIsNone(self.adapter._api, "dry-run 不应建立 QMT 连接")

    def test_submit_order_does_not_reach_qmt(self) -> None:
        fake = _FakeQmtAPI()
        self.adapter._api = fake
        self.adapter.connect()
        order = _make_broker_order()
        self.assertTrue(self.adapter.submit_order(order))
        self.assertEqual(fake.place_calls, [], "dry-run 不得调用 QMT place()")
        self.assertEqual(order.status, ba.OrderStatus.SUBMITTED)

    def test_audit_log_written(self) -> None:
        self.adapter.connect()
        self.adapter.submit_order(_make_broker_order())
        logs = sorted(Path(self.tmpdir).glob("qmt_*.jsonl"))
        self.assertTrue(logs, "dry-run 下单未生成 JSONL 审计日志")


class TestQmtLiveGate(unittest.TestCase):
    """三重门控: 永不裸实盘 (负向测试, 失败即说明出现裸实盘风险)."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self._old_env = os.environ.get("TRADING_ENV")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        if self._old_env is None:
            os.environ.pop("TRADING_ENV", None)
        else:
            os.environ["TRADING_ENV"] = self._old_env

    def test_live_without_production_env_refuses_connect(self) -> None:
        """live=True 但 TRADING_ENV≠production → 必须拒绝连接."""
        os.environ["TRADING_ENV"] = "sim"
        adapter = _make_adapter(live=True, audit_log_dir=self.tmpdir)
        self.assertFalse(adapter.connect(), "缺少 production 环境签名却连接成功")
        self.assertFalse(adapter._connected)

    def test_live_with_production_env_but_no_xtquant_refuses(self) -> None:
        """TRADING_ENV=production 但 xtquant 缺失 → 必须拒绝连接, 不抛异常."""
        os.environ["TRADING_ENV"] = "production"
        adapter = _make_adapter(live=True, audit_log_dir=self.tmpdir)
        if qa.is_qmt_available():
            self.skipTest("xtquant 已安装, 本用例仅校验缺失分支")
        self.assertFalse(adapter.connect(), "xtquant 缺失却连接成功")
        self.assertIsNone(adapter._api)

    def test_submit_order_rejected_when_channel_not_ready(self) -> None:
        """通道未就绪时下单必须被拒 (REJECTED), 而非静默成功."""
        os.environ["TRADING_ENV"] = "sim"
        adapter = _make_adapter(live=True, audit_log_dir=self.tmpdir)
        adapter._connected = True  # 强制绕过 connect, 模拟状态不一致
        order = _make_broker_order()
        self.assertFalse(adapter.submit_order(order))
        self.assertEqual(order.status, ba.OrderStatus.REJECTED)
        self.assertIn("QMT", order.rejection_reason or "")


class TestQmtOrderMapping(unittest.TestCase):
    """订单模型映射 (BrokerOrder → QmtBrokerAPI.place)."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.adapter = _make_adapter(live=True, audit_log_dir=self.tmpdir)
        self.fake = _FakeQmtAPI(place_result=_FakePlacedOrder("QMT-9527"))
        self.adapter._api = self.fake
        self.adapter._connected = True

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_buy_limit_maps_correctly(self) -> None:
        order = _make_broker_order()
        self.assertTrue(self.adapter.submit_order(order))
        call = self.fake.place_calls[0]
        self.assertEqual(call["symbol"], "510300.SH")
        self.assertEqual(call["qty"], 1000)
        self.assertEqual(call["side"], "BUY")
        self.assertEqual(call["order_type"], "LIMIT")
        self.assertEqual(call["price"], 4.5)
        self.assertEqual(order.status, ba.OrderStatus.SUBMITTED)

    def test_qmt_order_id_recorded(self) -> None:
        order = _make_broker_order()
        self.adapter.submit_order(order)
        self.assertEqual(order.tags.get("qmt_order_id"), "QMT-9527")
        self.assertEqual(self.adapter._qmt_order_ids.get(order.order_id), "QMT-9527")

    def test_algo_order_degrades_to_limit(self) -> None:
        order = _make_broker_order(order_type=ba.OrderType.VWAP)
        self.adapter.submit_order(order)
        self.assertEqual(self.fake.place_calls[0]["order_type"], "LIMIT")

    def test_market_order_zero_price(self) -> None:
        order = _make_broker_order(order_type=ba.OrderType.MARKET, price=None)
        self.adapter.submit_order(order)
        self.assertEqual(self.fake.place_calls[0]["order_type"], "MARKET")
        self.assertEqual(self.fake.place_calls[0]["price"], 0.0)

    def test_place_returns_none_marks_rejected(self) -> None:
        self.adapter._api = _FakeQmtAPI(place_result=None)
        order = _make_broker_order()
        self.assertFalse(self.adapter.submit_order(order))
        self.assertEqual(order.status, ba.OrderStatus.REJECTED)

    def test_cancel_maps_to_qmt_order_id(self) -> None:
        order = _make_broker_order()
        self.adapter.submit_order(order)
        self.assertTrue(self.adapter.cancel_order(order.order_id))
        self.assertEqual(self.fake.cancel_calls, ["QMT-9527"])


class TestQmtQueryMapping(unittest.TestCase):
    """查询接口契约转换."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.adapter = _make_adapter(live=True, audit_log_dir=self.tmpdir)
        self.adapter._connected = True

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_positions_converted_to_v83_contract(self) -> None:
        self.adapter._api = _FakeQmtAPI(positions={"510300.SH": 1000, "0": 0})
        self.assertEqual(
            self.adapter.get_positions(), [{"symbol": "510300.SH", "quantity": 1000}]
        )

    def test_account_info_converted(self) -> None:
        self.adapter._api = _FakeQmtAPI()
        info = self.adapter.get_account_info()
        self.assertEqual(info["available"], 100000.0)
        self.assertEqual(info["broker"], "qmt")

    def test_market_data_not_faked(self) -> None:
        """行情未实现时必须显式返回 error, 不得伪造空数据冒充成功."""
        self.adapter._api = _FakeQmtAPI()
        result = self.adapter.get_market_data("510300.SH")
        self.assertEqual(result["data"], [])
        self.assertTrue(result["error"])

    def test_disconnect_clears_state(self) -> None:
        fake = _FakeQmtAPI()
        self.adapter._api = fake
        self.adapter.disconnect()
        self.assertTrue(fake.disconnected)
        self.assertIsNone(self.adapter._api)


if __name__ == "__main__":
    unittest.main()
