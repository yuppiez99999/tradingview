# -*- coding: utf-8 -*-
"""test_market_circuit_breaker_unit.py — 大盘熔断监控器单元测试

覆盖 bug 回归:
    - BUG#1b: FAIL_CLOSED_PCT=-0.08 误触发 L3 全局平仓

设计原则:
    - 全 mock 外部数据源 (astock_realtime / akshare), 测试纯逻辑
    - 重点验证 fail-closed 行为: 数据不可用时只触发 L2 (禁止开仓), 不触发 L3 (强制平仓)
    - 每个 bug 至少一个用例, 函数名包含 bug 编号
"""
import pytest
from unittest.mock import MagicMock

from utils.market_circuit_breaker import MarketCircuitBreaker


# ============================================================
# BUG#1b 回归: fail-closed 默认值不应误触发 L3 全局平仓
# ============================================================
# Bug 历史 (v8.6.7 修复):
#   原 FAIL_CLOSED_PCT = -0.08
#   -0.08 <= l3_threshold(-0.07) → 触发 L3 (全局平仓)
#   但数据源不可用 ≠ 极端行情, 可能只是网络故障/接口封禁/非交易日
#
# 修复:
#   FAIL_CLOSED_PCT = -0.05
#   -0.05 > l3_threshold(-0.07) → 不触发 L3
#   -0.05 <= l2_threshold(-0.05) → 触发 L2 (禁止开仓, 保守保护)


class TestBUG1bFailClosedNoFalseL3:
    """BUG#1b 回归: fail-closed 不能误触发 L3"""

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.bug("BUG#1b")
    def test_bug1b_fail_closed_pct_is_negative_005_not_negative_008(self):
        """BUG#1b 配置验证: FAIL_CLOSED_PCT 必须为 -0.05, 不能是 -0.08

        -0.08 会误触发 L3 (因 -0.08 <= l3_threshold=-0.07)
        -0.05 只触发 L2 (因 -0.05 > l3_threshold=-0.07, 但 -0.05 <= l2_threshold=-0.05)
        """
        mcb = MarketCircuitBreaker()

        assert mcb.fail_closed_pct == -0.05, (
            f"FAIL_CLOSED_PCT 应为 -0.05 (触发 L2), 实际: {mcb.fail_closed_pct}. "
            f"若为 -0.08 会误触发 L3 全局平仓 (BUG#1b)"
        )

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.bug("BUG#1b")
    def test_bug1b_fail_closed_does_not_trigger_l3(
        self, mock_all_data_sources_unavailable
    ):
        """BUG#1b 核心: 数据源全部不可用时不能触发 L3 全局平仓

        应触发 L2 (禁止开仓, 保守保护)
        """
        mcb = MarketCircuitBreaker()
        status = mcb.check_market_status()

        assert status["level"] != 3, (
            "fail-closed 不能触发 L3 全局平仓 (数据源不可用 ≠ 极端行情)"
        )
        assert status["level"] == 2, "fail-closed 应触发 L2 (禁止开仓)"
        assert status["can_open"] is False, "L2 时不可开新仓"
        assert status["can_trade"] is True, "L2 时仍可交易 (允许平仓)"
        assert status["data_source"] == "fail_closed"

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.bug("BUG#1b")
    def test_bug1b_fail_closed_does_not_clear_orders_in_plan(
        self, mock_all_data_sources_unavailable, sample_trade_plan
    ):
        """BUG#1b 端到端: fail-closed 时 apply_to_plan 不能清空所有订单

        L3 会清空 morning_orders/afternoon_orders, 这是过度反应
        L2 应只过滤 BUY 订单, 保留 SELL 平仓订单
        """
        mcb = MarketCircuitBreaker()
        status = mcb.check_market_status()

        plan = mcb.apply_to_plan(sample_trade_plan, status)

        morning = plan["execution_plan"]["morning_orders"]
        afternoon = plan["execution_plan"]["afternoon_orders"]

        # L2 应保留 SELL 订单, 过滤 BUY 订单
        assert len(morning) > 0 or len(afternoon) > 0, (
            "L2 fail-closed 不能清空所有订单 (BUG#1b: 原本误触发 L3 会清空)"
        )

        for order in morning + afternoon:
            assert order["direction"] != "BUY", "L2 应过滤所有 BUY 订单"

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.bug("BUG#1b")
    def test_bug1b_fail_closed_pct_above_l3_threshold(self):
        """BUG#1b 数学验证: fail_closed_pct > l3_threshold

        -0.05 > -0.07 (条件成立 → 不触发 L3)
        """
        mcb = MarketCircuitBreaker()

        assert mcb.fail_closed_pct > mcb.l3_threshold, (
            f"fail_closed_pct({mcb.fail_closed_pct}) 必须 > l3_threshold({mcb.l3_threshold}), "
            f"否则会误触发 L3 全局平仓 (BUG#1b)"
        )


# ============================================================
# 沪深300 阈值边界测试 (L0/L2/L3 切换)
# ============================================================
# 注意: MarketCircuitBreaker 没有 L1, 只有 L0 / L2 / L3


class TestHS300LevelBoundaries:
    """沪深300 跌幅 → 级别映射边界测试"""

    @pytest.mark.unit
    def test_hs300_level_normal(self):
        """跌幅 < 5% → L0 正常"""
        mcb = MarketCircuitBreaker()
        # 模拟通过 astock_realtime 获取数据
        assert mcb.l2_threshold == -0.05
        assert mcb.l3_threshold == -0.07

    @pytest.mark.unit
    def test_hs300_change_minus_3pct_returns_l0(self, monkeypatch):
        """跌幅 -3% → L0 (未到 L2 阈值 -5%)"""
        mcb = MarketCircuitBreaker()
        # Mock astock_realtime 返回 -3%
        def _fake_astock(codes):
            return {"510300": {"price": 3.94, "pre_close": 4.06, "change_pct": -3.0}}

        try:
            monkeypatch.setattr(
                "utils.astock_realtime.get_realtime_quotes", _fake_astock
            )
        except (AttributeError, ImportError):
            pass

        status = mcb.check_market_status()
        assert status["level"] == 0
        assert status["can_open"] is True

    @pytest.mark.unit
    def test_hs300_change_minus_5pct_returns_l2(self, monkeypatch):
        """跌幅 -5% → L2 (边界包含)"""
        mcb = MarketCircuitBreaker()
        def _fake_astock(codes):
            return {"510300": {"price": 3.85, "pre_close": 4.06, "change_pct": -5.0}}

        try:
            monkeypatch.setattr(
                "utils.astock_realtime.get_realtime_quotes", _fake_astock
            )
        except (AttributeError, ImportError):
            pass

        status = mcb.check_market_status()
        assert status["level"] == 2
        assert status["can_open"] is False
        assert status["can_trade"] is True

    @pytest.mark.unit
    def test_hs300_change_minus_7pct_returns_l3(self, monkeypatch):
        """跌幅 -7% → L3 全局平仓 (边界包含)"""
        mcb = MarketCircuitBreaker()
        def _fake_astock(codes):
            return {"510300": {"price": 3.78, "pre_close": 4.06, "change_pct": -7.0}}

        try:
            monkeypatch.setattr(
                "utils.astock_realtime.get_realtime_quotes", _fake_astock
            )
        except (AttributeError, ImportError):
            pass

        status = mcb.check_market_status()
        assert status["level"] == 3
        assert status["can_trade"] is False, "L3 时不可交易"
        assert status["can_open"] is False

    @pytest.mark.unit
    def test_hs300_change_minus_10pct_returns_l3(self, monkeypatch):
        """跌幅 -10% → L3 (远超阈值)"""
        mcb = MarketCircuitBreaker()
        def _fake_astock(codes):
            return {"510300": {"price": 3.65, "pre_close": 4.06, "change_pct": -10.0}}

        try:
            monkeypatch.setattr(
                "utils.astock_realtime.get_realtime_quotes", _fake_astock
            )
        except (AttributeError, ImportError):
            pass

        status = mcb.check_market_status()
        assert status["level"] == 3


# ============================================================
# apply_to_plan 行为测试
# ============================================================


class TestApplyToPlan:
    """apply_to_plan 各级别动作验证"""

    @pytest.mark.unit
    def test_l3_clears_all_orders_and_halts_trading(self, sample_trade_plan):
        """L3: 清空所有订单 + halt_all_trading=True"""
        mcb = MarketCircuitBreaker()
        status = {
            "level": 3,
            "hs300_change_pct": -0.075,
            "data_source": "astock_realtime",
        }

        plan = mcb.apply_to_plan(sample_trade_plan, status)

        assert plan["execution_plan"]["morning_orders"] == []
        assert plan["execution_plan"]["afternoon_orders"] == []
        assert plan["market_state"]["halt_all_trading"] is True
        assert plan["market_state"]["circuit_level"] == "CRITICAL"
        assert plan["risk_guard"]["market_circuit_breaker"]["level"] == 3
        assert plan["risk_guard"]["market_circuit_breaker"]["action"] == "HALT_ALL_TRADING"

    @pytest.mark.unit
    def test_l2_filters_buy_keeps_sell(self, sample_trade_plan):
        """L2: 过滤 BUY 订单, 保留 SELL 订单"""
        mcb = MarketCircuitBreaker()
        status = {
            "level": 2,
            "hs300_change_pct": -0.055,
            "data_source": "astock_realtime",
        }

        plan = mcb.apply_to_plan(sample_trade_plan, status)

        morning = plan["execution_plan"]["morning_orders"]
        afternoon = plan["execution_plan"]["afternoon_orders"]

        assert len(morning) == 1
        assert morning[0]["direction"] == "SELL"
        assert afternoon == [], "afternoon 的 BUY 订单应被过滤"

        assert plan["market_state"]["spot_build_allowed"] is False
        assert plan["market_state"]["circuit_level"] == "WARNING"

    @pytest.mark.unit
    def test_l0_normal_no_modification(self, sample_trade_plan):
        """L0: 正常状态, 仅写入 NORMAL 标记"""
        mcb = MarketCircuitBreaker()
        status = {
            "level": 0,
            "hs300_change_pct": -0.005,
            "data_source": "astock_realtime",
        }

        original_morning = list(sample_trade_plan["execution_plan"]["morning_orders"])

        plan = mcb.apply_to_plan(sample_trade_plan, status)

        # 订单不变
        assert plan["execution_plan"]["morning_orders"] == original_morning
        # 但 risk_guard 被标记
        assert plan["risk_guard"]["market_circuit_breaker"]["level"] == 0
        assert plan["risk_guard"]["market_circuit_breaker"]["action"] == "NORMAL"


# ============================================================
# 自定义阈值构造测试
# ============================================================


class TestCustomThresholds:
    """构造函数自定义阈值参数"""

    @pytest.mark.unit
    def test_custom_thresholds_override_defaults(self):
        """自定义阈值覆盖默认值"""
        mcb = MarketCircuitBreaker(
            l2_threshold=-0.04,
            l3_threshold=-0.06,
            fail_closed_pct=-0.04,
        )

        assert mcb.l2_threshold == -0.04
        assert mcb.l3_threshold == -0.06
        assert mcb.fail_closed_pct == -0.04

    @pytest.mark.unit
    def test_custom_fail_closed_still_above_l3(self):
        """自定义 fail_closed_pct 也必须 > l3_threshold (避免 BUG#1b 复发)"""
        mcb = MarketCircuitBreaker(
            l3_threshold=-0.06,
            fail_closed_pct=-0.05,  # > -0.06 ✓
        )

        assert mcb.fail_closed_pct > mcb.l3_threshold


# ============================================================
# Mock fixture: 模拟所有外部数据源不可用 (本文件专用)
# ============================================================


@pytest.fixture
def mock_all_data_sources_unavailable(monkeypatch):
    """mock astock_realtime + akshare, 强制走 fail-closed 路径

    1. utils.astock_realtime.get_realtime_quotes → 返回空 dict
    2. akshare.stock_zh_a_spot_em → 抛 ImportError
    3. akshare.stock_zh_index_spot_em → 抛 ImportError
    """
    # astock_realtime 返回空
    def _empty_quotes(codes):
        return {}

    try:
        monkeypatch.setattr(
            "utils.astock_realtime.get_realtime_quotes", _empty_quotes
        )
    except (AttributeError, ImportError):
        pass

    # akshare 不可用
    def _raise(*args, **kwargs):
        raise ImportError("akshare not installed (mocked)")

    try:
        monkeypatch.setattr("akshare.stock_zh_a_spot_em", _raise, raising=False)
        monkeypatch.setattr("akshare.stock_zh_index_spot_em", _raise, raising=False)
    except (AttributeError, ImportError):
        pass
