"""Phase B 关键路径性能基准测试 (pytest-benchmark).

测试 P0 链路关键模块性能:
    - KillSwitchManager 风控检查
    - TradingCalendar 交易日历判断
    - ObservabilityEvent 事件 Schema 序列化
    - StructuredLogger 结构化日志输出

运行: pytest tests/perf/test_phase_b_benchmark.py --benchmark-only
"""

import pytest

from utils.observability import OrderEvent, RiskEvent
from utils.observability.structured_logger import get_structured_logger


@pytest.fixture
def kill_switch():
    from utils.risk.kill_switch_manager import KillSwitchManager

    return KillSwitchManager()


@pytest.fixture
def trading_calendar():
    from utils.execution.automated_execution_system import TradingCalendar

    return TradingCalendar()


@pytest.fixture
def structured_logger():
    return get_structured_logger("benchmark")


class TestPhaseBBenchmark:
    """Phase B 关键路径性能基准."""

    def test_kill_switch_check(self, benchmark, kill_switch):
        """KillSwitch 风控检查基准."""
        benchmark(kill_switch.can_open_new_position)

    def test_trading_calendar_is_trading_day(self, benchmark, trading_calendar):
        """交易日历判断基准."""
        from datetime import datetime

        test_date = datetime(2026, 8, 20)
        benchmark(trading_calendar.is_trading_day, test_date)

    def test_order_event_serialization(self, benchmark):
        """OrderEvent Schema 序列化基准."""

        def create_order():
            return OrderEvent(
                order_id="123",
                symbol="510300",
                side="buy",
                qty=100,
                price=4.5,
            )

        benchmark(create_order)

    def test_risk_event_serialization(self, benchmark):
        """RiskEvent Schema 序列化基准."""

        def create_risk():
            return RiskEvent(
                risk_level="caution",
                trigger="volatility",
                action="reduce",
            )

        benchmark(create_risk)

    def test_order_event_json_dump(self, benchmark):
        """OrderEvent JSON 序列化基准."""
        evt = OrderEvent(
            order_id="123",
            symbol="510300",
            side="buy",
            qty=100,
            price=4.5,
        )
        benchmark(evt.model_dump_json)

    def test_structured_logger_info(self, benchmark, structured_logger):
        """结构化日志 info 输出基准."""

        def log_event():
            structured_logger.info(
                "benchmark_test",
                order_id="123",
                symbol="510300",
                qty=100,
            )

        benchmark(log_event)
