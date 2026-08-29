"""execution 模块 2026-08-24 审查回归测试 — 修复 EX-2/EX-7/EX-8/EX-11/EX-12

覆盖:
    EX-7  AlgoEngine.execute_order 调用 execute_twap/vwap/pov (此前 AttributeError)
    EX-11 AlgoEngine.execute_order 在 sor=None 时 fail-open 返回空
    EX-12 MockBroker 实现 get_account_info (SmartOrderRouter 资金校验依赖)
    EX-2  SimulatedBroker 未设 volume 时全额成交 (此前静默只成交 1/10)
    EX-8  post_execution_review IS 带方向 (买入有利=负, 不利=正)
"""

from __future__ import annotations

from ms_strategy.src.execution.algo_engine import AlgoEngine
from ms_strategy.src.execution.broker_api import Order, SimulatedBroker
from ms_strategy.src.execution.post_execution_review import (
    ExecutionReviewer,
    FillRecord,
)
from ms_strategy.src.execution.smart_order_router import MockBroker, SmartOrderRouter


class _MockNTP:
    offset_seconds = 0.001

    def server_ts(self):
        from datetime import datetime

        return datetime.utcnow()

    def local_ts(self):
        from datetime import datetime

        return datetime.utcnow()

    def get_offset(self):
        return self.offset_seconds

    def sync(self):
        return True

    def is_healthy(self):
        return True

    def snapshot(self):
        return {"offset": self.offset_seconds}


# ---- EX-7 + EX-12: AlgoEngine.execute_order 走 SOR 算法执行 ----
class TestAlgoEngineExecuteOrder:
    def test_execute_twap_succeeds(self):
        """EX-7: TWAP 执行不再 AttributeError, 能产出成交回报."""
        broker = MockBroker({"600519": 1680.0})
        sor = SmartOrderRouter(broker=broker, ntp=_MockNTP())
        ae = AlgoEngine(sor=sor)
        fills = ae.execute_order("600519", 800, "SELL", 1680.0, algo="TWAP")
        assert isinstance(fills, list)
        # 卖出无资金校验, 应至少成交 1 片
        assert len(fills) >= 1

    def test_execute_vwap_succeeds(self):
        """EX-7: VWAP 执行正常."""
        broker = MockBroker({"600519": 1680.0})
        sor = SmartOrderRouter(broker=broker, ntp=_MockNTP())
        ae = AlgoEngine(sor=sor)
        fills = ae.execute_order("600519", 800, "SELL", 1680.0, algo="VWAP")
        assert len(fills) >= 1

    def test_execute_pov_succeeds(self):
        """EX-7: POV 执行正常."""
        broker = MockBroker({"600519": 1680.0})
        sor = SmartOrderRouter(broker=broker, ntp=_MockNTP())
        ae = AlgoEngine(sor=sor)
        fills = ae.execute_order("600519", 800, "SELL", 1680.0, algo="POV")
        assert len(fills) >= 1

    def test_execute_order_without_sor(self):
        """EX-11: sor=None 时 fail-open 返回空列表而非 AttributeError."""
        ae = AlgoEngine()
        result = ae.execute_order("600519", 100, "BUY", 1680.0)
        assert result == []

    def test_mockbroker_has_get_account_info(self):
        """EX-12: MockBroker 实现 get_account_info (SOR 资金校验依赖)."""
        broker = MockBroker({"600519": 1680.0})
        account = broker.get_account_info()
        assert "available" in account


# ---- EX-2: SimulatedBroker 未设 volume 时全额成交 ----
class TestSimulatedBrokerVolume:
    def test_no_volume_full_fill(self):
        """EX-2: 只设价格未设 volume 时应全额成交 (修复前静默 1/10)."""
        broker = SimulatedBroker()
        broker.set_price("600519", 1680.0)
        order = Order(
            order_id="o1",
            symbol="600519",
            qty=1000,
            side="BUY",
            order_type="LIMIT",
            price=1680.0,
        )
        fill = broker.wait_fill(order)
        assert fill is not None
        assert fill["qty"] == 1000

    def test_with_volume_limits_fill(self):
        """EX-2: 设置 volume 后按流动性约束部分成交."""
        broker = SimulatedBroker()
        broker.set_price("600519", 1680.0, volume=5000)
        order = Order(
            order_id="o2",
            symbol="600519",
            qty=1000,
            side="BUY",
            order_type="LIMIT",
            price=1680.0,
        )
        fill = broker.wait_fill(order)
        assert fill is not None
        # 参与上限 = volume // 10 = 500
        assert fill["qty"] == 500


# ---- EX-8: post_execution_review IS 带方向 ----
class TestImplementationShortfallDirection:
    def _review_buy(self, fill_price: float) -> float:
        r = ExecutionReviewer()
        fills = [
            FillRecord(
                symbol="600519",
                side="BUY",
                quantity=100,
                fill_price=fill_price,
                decision_price=10.0,
                arrival_price=10.0,
                order_id="o1",
            )
        ]
        report = r.review(fills)
        return report.order_summaries[0].implementation_shortfall

    def test_buy_disadvantage_positive(self):
        """EX-8: 买入成交价高于决策价 → 正 IS (成本增加/不利)."""
        assert self._review_buy(10.1) > 0

    def test_buy_advantage_negative(self):
        """EX-8: 买入成交价低于决策价 → 负 IS (成交更优/有利)."""
        assert self._review_buy(9.9) < 0
