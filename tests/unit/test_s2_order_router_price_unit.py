"""test_s2_order_router_price_unit.py — 队列消费限价口径 (Issue #13: S-2)

巡检事实 (沙箱实测): 模拟/回测路径下, 若切片价缺失, 原实现直接回退"持仓参考价"
(est_price/last_price), 与本次执行计划的限价脱钩 —— 实测决策价 1700 元、参考价
999 元时, 成交价用的是 999 元 (静默产生与决策不一致的成交价)。

另: `isinstance(price, (int, float))` 会把 Decimal 判为非法类型 → 错误回退参考价。

本测试锁定 S-2 修复后的回退顺序: 切片价 → 执行计划限价 → 参考价 (兜底 + 告警)。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from utils.execution.order_router import OrderRouter, _to_positive_float


class TestToPositiveFloat:
    @pytest.mark.unit
    def test_accepts_decimal_and_numeric_string(self):
        assert _to_positive_float(Decimal("1700.5")) == pytest.approx(1700.5)
        assert _to_positive_float("12.3") == pytest.approx(12.3)
        assert _to_positive_float(10) == pytest.approx(10.0)

    @pytest.mark.unit
    def test_rejects_invalid_values(self):
        assert _to_positive_float(None) is None
        assert _to_positive_float(0) is None
        assert _to_positive_float(-5) is None
        assert _to_positive_float("abc") is None
        assert _to_positive_float(True) is None


def _plan(**overrides) -> dict:
    plan = {
        "symbol": "600519",
        "side": "BUY",
        "qty": 100,
        "limit_price": 1700.0,
        "price_missing": False,
        "slices": [
            {
                "slice_id": 1,
                "size": 100,
                "instrument": "600519",
                "direction": "buy",
                "price": 1700.0,
            }
        ],
        "num_slices": 1,
    }
    plan.update(overrides)
    return plan


def _drain(router: OrderRouter) -> list[dict]:
    router.process_execution_queue()
    return [o.get("execution_result") for o in router.active_orders.values()]


class TestQueuePriceResolution:
    @pytest.mark.unit
    def test_slice_price_wins(self):
        router = OrderRouter()
        router._get_reference_price = lambda s: 999.0
        router.route_order(_plan(), "normal")
        results = _drain(router)
        assert results[0]["success"] is True
        # 1700 × (1 + 2bp 滑点) ≈ 1700.34, 不能用 999 参考价
        assert results[0]["average_price"] == pytest.approx(1700.34, abs=0.05)

    @pytest.mark.unit
    def test_plan_limit_price_beats_reference_price(self):
        """S-2 核心回归: 切片无价时用执行计划限价, 不用持仓参考价。"""
        router = OrderRouter()
        router._get_reference_price = lambda s: 999.0
        plan = _plan()
        del plan["slices"][0]["price"]
        router.route_order(plan, "normal")
        results = _drain(router)
        assert results[0]["success"] is True
        assert results[0]["average_price"] == pytest.approx(1700.34, abs=0.05)

    @pytest.mark.unit
    def test_decimal_slice_price_is_accepted(self):
        """Decimal 价格不得被误判为非法类型而回退参考价。"""
        router = OrderRouter()
        router._get_reference_price = lambda s: 999.0
        plan = _plan()
        plan["slices"][0]["price"] = Decimal("1700")
        router.route_order(plan, "normal")
        results = _drain(router)
        assert results[0]["success"] is True
        assert results[0]["average_price"] == pytest.approx(1700.34, abs=0.05)

    @pytest.mark.unit
    def test_falls_back_to_reference_price_when_no_price_anywhere(self):
        """完全无价 → 参考价兜底 (既有行为保留, 但会告警)。"""
        router = OrderRouter()
        router._get_reference_price = lambda s: 999.0
        plan = _plan(limit_price=None)
        del plan["slices"][0]["price"]
        router.route_order(plan, "normal")
        results = _drain(router)
        assert results[0]["success"] is True
        assert results[0]["average_price"] == pytest.approx(999.2, abs=0.05)

    @pytest.mark.unit
    def test_zero_price_still_rejected_without_reference(self):
        """BUG-E4 语义不变: 无任何有效价且无参考价 → 拒绝, 不生成 0 价成交。"""
        router = OrderRouter()
        router._get_reference_price = lambda s: None
        plan = _plan(limit_price=0)
        plan["slices"][0]["price"] = 0
        router.route_order(plan, "normal")
        _drain(router)
        # 订单重试 3 次后应被 abandon (不静默丢单, 且从未产生 0 价成交)
        orders = list(router.active_orders.values())
        assert len(orders) == 1
        assert orders[0]["status"] == "abandoned"
        assert "参考价格" in orders[0]["error"]
