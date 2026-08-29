"""EX-1 broker 契约统一回归测试 (2026-08-24)

验证 SmartOrderRouter.__init__ 的运行时契约校验:
- 传入 broker 必须具备 SOR 依赖的全部核心方法 (get_order_book/place/wait_fill/cancel/get_account_info)
- 合法 broker (MockBroker/SimulatedBroker) 通过
- 缺方法的 broker fail-fast 抛 TypeError (EX-7 同类问题系统性防御)
"""

from __future__ import annotations

import pytest

from ms_strategy.src.execution.broker_api import SimulatedBroker
from ms_strategy.src.execution.smart_order_router import MockBroker, SmartOrderRouter


class _FakeNTP:
    """假 NTP, 避免 SmartOrderRouter 构造时联网 (测试环境无外网/NTP 被拦)."""

    def get_offset_ms(self):
        return 0.0


class TestBrokerContractValidation:
    def test_mock_broker_valid(self):
        """EX-1: MockBroker 满足契约."""
        router = SmartOrderRouter(broker=MockBroker({"600519": 1680.0}), ntp=_FakeNTP())
        assert router.broker is not None

    def test_simulated_broker_valid(self):
        """EX-1: SimulatedBroker (broker_api 抽象基类实例) 满足契约."""
        router = SmartOrderRouter(
            broker=SimulatedBroker(initial_capital=1_000_000), ntp=_FakeNTP()
        )
        assert router.broker is not None

    def test_bad_broker_rejected(self):
        """EX-1: 缺方法的 broker fail-fast 抛 TypeError (而非运行中 AttributeError)."""

        class BadBroker:
            pass

        with pytest.raises(TypeError):
            SmartOrderRouter(broker=BadBroker(), ntp=_FakeNTP())

    def test_partial_broker_rejected(self):
        """EX-1: 只有部分方法的 broker 也被拒绝."""

        class PartialBroker:
            def place(self, *a, **k):
                return "id"

        with pytest.raises(TypeError):
            SmartOrderRouter(broker=PartialBroker(), ntp=_FakeNTP())
