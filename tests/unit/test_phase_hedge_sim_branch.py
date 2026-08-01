"""v8.4 phase_hedge sim_mode 分支 + _execute_sim_hedge_orders 测试.

验证:
    1. _execute_sim_hedge_orders 正确拆分对冲订单到 futures/options/stock
    2. SHORT_FUTURES → sim_engine.execute_futures_orders
    3. PUT_SPREAD/BUY_PUT → sim_engine.execute_options_orders
    4. SAFE_HAVEN_ALLOC → sim_engine.execute_stock_orders
    5. DOWNGRADE_TO_PUT_SPREAD → 仅记录跳过
    6. 未知 action → SKIP_UNKNOWN_ACTION
    7. 返回结构与原 executed_orders 对齐
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_V83_DIR = _PROJECT_ROOT / "v8.3_institutional"
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_V83_DIR))


class MockSimEngine:
    """模拟 SimExecutionEngine"""

    def __init__(self, delta=0.0):
        self._greek = {"delta": delta, "gamma": 0, "theta": 0, "vega": 0}
        self.router = MagicMock()
        self.calls = []

    def execute_futures_orders(self, orders, session="day"):
        self.calls.append(("futures", orders, session))
        return [{"status": "FILLED", "symbol": o["symbol"], "qty": o["qty"],
                 "price": o["price"]} for o in orders]

    def execute_options_orders(self, orders, session="day"):
        self.calls.append(("options", orders, session))
        return [{"status": "FILLED", "symbol": o["symbol"], "qty": o["qty"],
                 "price": o["price"]} for o in orders]

    def execute_stock_orders(self, orders, session="day"):
        self.calls.append(("stock", orders, session))
        return [{"status": "FILLED", "symbol": o["symbol"], "qty": o["qty"],
                 "price": o["price"]} for o in orders]

    def get_greek_exposure(self):
        return self._greek


def _make_workflow(sim_engine=None, mock_prices=None):
    """创建简化 DailyWorkflow 实例 (跳过 __init__)"""
    try:
        from daily_workflow import DailyWorkflow
    except Exception as e:
        pytest.skip(f"DailyWorkflow 导入失败 (依赖缺失): {e}")
    wf = DailyWorkflow.__new__(DailyWorkflow)
    wf.sim_engine = sim_engine or MockSimEngine()
    wf.config = MagicMock()
    wf.config.MOCK_PRICES = mock_prices or {"518880": 5.85}
    return wf


class TestExecuteSimHedgeOrders:
    """_execute_sim_hedge_orders 方法测试"""

    def test_short_futures_routed_to_futures_orders(self):
        """SHORT_FUTURES → sim_engine.execute_futures_orders"""
        wf = _make_workflow()
        orders = [
            {"action": "SHORT_FUTURES", "instrument": "IF", "contracts": 3,
             "futures_price": 4200, "hedge_type": "BETA", "notional": 3780000},
        ]
        result = wf._execute_sim_hedge_orders(orders)
        # 验证 sim_engine.execute_futures_orders 被调用
        call_types = [c[0] for c in wf.sim_engine.calls]
        assert "futures" in call_types
        # 验证返回结构
        assert len(result) == 1
        assert result[0]["action"] == "SHORT_FUTURES"
        assert result[0]["status"] == "FILLED"
        assert result[0]["contracts"] == 3
        assert result[0]["instrument"] == "IF"

    def test_put_spread_routed_to_options_orders(self):
        """PUT_SPREAD → sim_engine.execute_options_orders"""
        wf = _make_workflow()
        orders = [
            {"action": "PUT_SPREAD", "hedge_type": "TAIL",
             "budget": 50000, "budget_allocation": {"510300": 30000, "588000": 20000},
             "contracts": 0, "vix": 25},
        ]
        result = wf._execute_sim_hedge_orders(orders)
        call_types = [c[0] for c in wf.sim_engine.calls]
        assert "options" in call_types
        # 应有 2 笔期权订单 (按 budget_allocation 拆分)
        options_orders = [c[1] for c in wf.sim_engine.calls if c[0] == "options"][0]
        assert len(options_orders) == 2
        # 验证返回结构
        filled = [r for r in result if r.get("status") == "FILLED"]
        assert len(filled) == 2
        assert all(r["action"] == "PUT_SPREAD" for r in filled)

    def test_safe_haven_alloc_routed_to_stock_orders(self):
        """SAFE_HAVEN_ALLOC → sim_engine.execute_stock_orders"""
        wf = _make_workflow(mock_prices={"518880": 5.85})
        orders = [
            {"action": "SAFE_HAVEN_ALLOC", "hedge_type": "CORR",
             "gold_value": 100000, "gold_etf": "518880"},
        ]
        result = wf._execute_sim_hedge_orders(orders)
        call_types = [c[0] for c in wf.sim_engine.calls]
        assert "stock" in call_types
        filled = [r for r in result if r.get("status") == "FILLED"]
        assert len(filled) == 1
        assert filled[0]["instrument"] == "518880"

    def test_downgrade_skipped(self):
        """DOWNGRADE_TO_PUT_SPREAD → 仅记录跳过"""
        wf = _make_workflow()
        orders = [
            {"action": "DOWNGRADE_TO_PUT_SPREAD", "hedge_type": "TAIL",
             "reason": "成本超限"},
        ]
        result = wf._execute_sim_hedge_orders(orders)
        assert len(result) == 1
        assert result[0]["status"] == "DOWNGRADED"
        # 不应调用任何 sim_engine 方法
        assert len(wf.sim_engine.calls) == 0

    def test_unknown_action_skipped(self):
        """未知 action → SKIP_UNKNOWN_ACTION"""
        wf = _make_workflow()
        orders = [
            {"action": "UNKNOWN_ACTION", "hedge_type": "X"},
        ]
        result = wf._execute_sim_hedge_orders(orders)
        assert len(result) == 1
        assert result[0]["status"] == "SKIP_UNKNOWN_ACTION"

    def test_empty_orders_returns_empty(self):
        """空订单列表 → 返回空列表"""
        wf = _make_workflow()
        result = wf._execute_sim_hedge_orders([])
        assert result == []
        assert len(wf.sim_engine.calls) == 0

    def test_mixed_orders_routed_correctly(self):
        """混合订单正确路由到三类 broker"""
        wf = _make_workflow(mock_prices={"518880": 5.85})
        orders = [
            {"action": "SHORT_FUTURES", "instrument": "IF", "contracts": 2,
             "futures_price": 4200, "hedge_type": "BETA"},
            {"action": "PUT_SPREAD", "hedge_type": "TAIL",
             "budget": 30000, "budget_allocation": {"510300": 30000},
             "contracts": 0, "vix": 22},
            {"action": "SAFE_HAVEN_ALLOC", "hedge_type": "CORR",
             "gold_value": 50000, "gold_etf": "518880"},
            {"action": "DOWNGRADE_TO_PUT_SPREAD", "reason": "test"},
        ]
        result = wf._execute_sim_hedge_orders(orders)
        # 3 笔成交 + 1 笔跳过
        filled = [r for r in result if r.get("status") == "FILLED"]
        skipped = [r for r in result if r.get("status") == "DOWNGRADED"]
        assert len(filled) == 3
        assert len(skipped) == 1
        # 验证三类 broker 都被调用
        call_types = set(c[0] for c in wf.sim_engine.calls)
        assert "futures" in call_types
        assert "options" in call_types
        assert "stock" in call_types

    def test_short_futures_zero_contracts_skipped(self):
        """SHORT_FUTURES contracts=0 → SKIP"""
        wf = _make_workflow()
        orders = [
            {"action": "SHORT_FUTURES", "instrument": "IF", "contracts": 0,
             "futures_price": 4200},
        ]
        result = wf._execute_sim_hedge_orders(orders)
        assert len(result) == 1
        assert result[0]["status"] == "SKIP_NO_PRICE_OR_QTY"

    def test_futures_execution_exception_handled(self):
        """期货执行异常不崩溃, 返回 FAILED"""
        broken_sim = MockSimEngine()
        broken_sim.execute_futures_orders = MagicMock(side_effect=RuntimeError("broken"))
        wf = _make_workflow(sim_engine=broken_sim)
        orders = [
            {"action": "SHORT_FUTURES", "instrument": "IF", "contracts": 3,
             "futures_price": 4200},
        ]
        result = wf._execute_sim_hedge_orders(orders)
        assert len(result) == 1
        assert result[0]["status"] == "FAILED"
        assert "error" in result[0]

    def test_returned_orders_have_required_fields(self):
        """返回的成交记录包含必需字段"""
        wf = _make_workflow()
        orders = [
            {"action": "SHORT_FUTURES", "instrument": "IF", "contracts": 3,
             "futures_price": 4200, "hedge_type": "BETA", "notional": 3780000},
        ]
        result = wf._execute_sim_hedge_orders(orders)
        r = result[0]
        # 与原 executed_orders 结构对齐
        assert "type" in r
        assert "action" in r
        assert "instrument" in r
        assert "side" in r
        assert "contracts" in r
        assert "price" in r
        assert "status" in r
        assert "fill_record" in r
