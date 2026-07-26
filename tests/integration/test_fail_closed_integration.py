# -*- coding: utf-8 -*-
"""test_fail_closed_integration.py — fail-closed 保守保护机制集成测试

5 条关键链路 #3: 数据源不可用 → fail-closed → L2 (禁止开仓, 不清仓)

设计原则:
    - 模拟所有外部数据源不可用 (网络故障 / 接口封禁 / 非交易日)
    - 验证 fail-closed 行为统一: 触发 L2 (禁止开仓), 不触发 L3 (强制平仓)
    - 覆盖 OvernightGapMonitor + MarketCircuitBreaker 两个 Guard
"""
import pytest
from unittest.mock import MagicMock

from utils.overnight_gap_monitor import OvernightGapMonitor
from utils.market_circuit_breaker import MarketCircuitBreaker


@pytest.fixture
def all_data_sources_down(monkeypatch):
    """模拟所有外部数据源全部不可用

    1. astock_realtime.get_realtime_quotes → 返回空 dict
    2. akshare.* → 抛 ImportError
    3. ExternalDataManager → 抛 ConnectionError
    4. cache 文件不存在
    """
    def _empty_quotes(codes):
        return {}

    try:
        monkeypatch.setattr(
            "utils.astock_realtime.get_realtime_quotes", _empty_quotes
        )
    except (AttributeError, ImportError):
        pass

    def _raise(*args, **kwargs):
        raise ImportError("akshare not installed (mocked)")

    try:
        monkeypatch.setattr("akshare.stock_zh_a_spot_em", _raise, raising=False)
        monkeypatch.setattr("akshare.stock_zh_index_spot_em", _raise, raising=False)
    except (AttributeError, ImportError):
        pass

    # OvernightGapMonitor: ExternalDataSource + cache 都不可用
    try:
        monkeypatch.setattr(
            "utils.overnight_gap_monitor.OvernightGapMonitor._fetch_via_external_source",
            lambda self: (None, None, False),
        )
        monkeypatch.setattr(
            "utils.overnight_gap_monitor.OvernightGapMonitor._fetch_via_cache",
            lambda self: (None, None, False),
        )
    except (AttributeError, ImportError):
        pass


# ============================================================
# 集成测试: fail-closed 统一原则
# ============================================================


class TestFailClosedPrinciple:
    """fail-closed 保守保护机制集成测试

    核心原则:
        - 数据源不可用 ≠ 极端行情 (可能只是网络故障)
        - 触发 L2 (禁止开仓) 是正确的保守响应
        - 触发 L3 (强制平仓) 是过度反应, 禁止
    """

    @pytest.mark.integration
    @pytest.mark.p0
    @pytest.mark.bug("BUG#1")
    def test_overnight_gap_fail_closed_returns_l2_not_l3(self, all_data_sources_down):
        """OvernightGapMonitor: 数据源全不可用 → L2 (不是 L3)"""
        ogm = OvernightGapMonitor()
        risk = ogm.evaluate_overnight_risk()

        assert risk["level"] == 2, "fail-closed 应触发 L2"
        assert risk["level"] != 3, "fail-closed 不能触发 L3 (BUG#1)"
        assert risk["data_source"] == "fail_closed"
        assert risk["can_open"] is False, "禁止开仓"
        assert risk["can_trade"] is True, "允许平仓"

    @pytest.mark.integration
    @pytest.mark.p0
    @pytest.mark.bug("BUG#1b")
    def test_market_circuit_breaker_fail_closed_returns_l2_not_l3(self, all_data_sources_down):
        """MarketCircuitBreaker: 数据源全不可用 → L2 (不是 L3)"""
        mcb = MarketCircuitBreaker()
        status = mcb.check_market_status()

        assert status["level"] == 2, "fail-closed 应触发 L2"
        assert status["level"] != 3, "fail-closed 不能触发 L3 (BUG#1b)"
        assert status["data_source"] == "fail_closed"
        assert status["can_open"] is False
        assert status["can_trade"] is True

    @pytest.mark.integration
    def test_fail_closed_preserves_sell_orders(self, all_data_sources_down, sample_trade_plan):
        """fail-closed 触发 L2 时, 必须保留 SELL 订单 (允许平仓)

        sample_trade_plan 含:
            morning: BUY 588080, SELL 512880
            afternoon: BUY 510050
        L2 后期望:
            morning: SELL 512880 (保留)
            afternoon: 空 (BUY 被过滤)
        """
        ogm = OvernightGapMonitor()
        risk = ogm.evaluate_overnight_risk()
        plan = ogm.apply_to_plan(sample_trade_plan, risk)

        morning = plan["execution_plan"]["morning_orders"]
        afternoon = plan["execution_plan"]["afternoon_orders"]

        # SELL 订单必须保留
        assert any(o["direction"] == "SELL" for o in morning), \
            "fail-closed L2 必须保留 SELL 订单 (允许平仓)"
        # BUY 订单必须被过滤
        for order in morning + afternoon:
            assert order["direction"] != "BUY", "fail-closed L2 应过滤所有 BUY"

    @pytest.mark.integration
    def test_fail_closed_does_not_halt_all_trading(self, all_data_sources_down, sample_trade_plan):
        """fail-closed 不能触发 halt_all_trading (L3 才能触发)"""
        ogm = OvernightGapMonitor()
        risk = ogm.evaluate_overnight_risk()
        plan = ogm.apply_to_plan(sample_trade_plan, risk)

        # halt_all_trading 只能由 L3 触发, fail-closed (L2) 不能
        assert plan["market_state"].get("halt_all_trading") is not True, \
            "fail-closed L2 不能触发 halt_all_trading (L3 才能触发)"
        assert plan["market_state"]["circuit_level"] == "WARNING", \
            "fail-closed 应为 WARNING, 不是 CRITICAL"


# ============================================================
# 集成测试: 真实极端行情 (非 fail-closed) 应触发 L3
# ============================================================


class TestRealExtremeMarketTriggersL3:
    """对比测试: 真实极端行情 (有数据) 应触发 L3

    区别于 fail-closed (无数据 → L2 保守保护)
    真实极端行情 (有数据且跌幅超阈值) 应触发 L3 (全局平仓)
    """

    @pytest.mark.integration
    def test_real_sp500_crash_3pct_triggers_l3(self, monkeypatch):
        """真实 S&P500 跌 3% (有数据) → L3 全局平仓"""
        # Mock ExternalDataSource 返回 -3% (真实极端行情)
        monkeypatch.setattr(
            "utils.overnight_gap_monitor.OvernightGapMonitor._fetch_via_external_source",
            lambda self: (-0.03, 0.0, True),
        )

        ogm = OvernightGapMonitor()
        risk = ogm.evaluate_overnight_risk()

        # 真实跌幅 -3% 应触发 L3 (与 fail-closed 的 -2% 区分)
        assert risk["level"] == 3
        assert risk["data_source"] == "external_data"
        assert risk["can_trade"] is False

    @pytest.mark.integration
    def test_real_hs300_crash_7pct_triggers_l3(self, monkeypatch):
        """真实沪深300 跌 7% (有数据) → L3 全局平仓"""
        def _crash_quotes(codes):
            return {"510300": {"price": 3.78, "pre_close": 4.06, "change_pct": -7.0}}

        try:
            monkeypatch.setattr(
                "utils.astock_realtime.get_realtime_quotes", _crash_quotes
            )
        except (AttributeError, ImportError):
            pass

        mcb = MarketCircuitBreaker()
        status = mcb.check_market_status()

        assert status["level"] == 3
        assert status["data_source"] == "astock_realtime"
        assert status["can_trade"] is False
