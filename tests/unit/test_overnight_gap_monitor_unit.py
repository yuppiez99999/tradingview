# -*- coding: utf-8 -*-
"""test_overnight_gap_monitor_unit.py — 隔夜跳空监控器单元测试

覆盖 bug 回归:
    - BUG#1: FAIL_CLOSED_PCT=-0.04 误触发 L3 全局平仓

设计原则:
    - 全 mock 外部数据源 (ExternalDataManager / cache), 测试纯逻辑
    - 重点验证 fail-closed 行为: 数据不可用时只触发 L2 (禁止开仓), 不触发 L3 (强制平仓)
    - 每个 bug 至少一个用例, 函数名包含 bug 编号
"""
import pytest

from utils.overnight_gap_monitor import OvernightGapMonitor


# ============================================================
# BUG#1 回归: fail-closed 默认值不应误触发 L3 全局平仓
# ============================================================
# Bug 历史 (v8.6.7 修复):
#   原 FAIL_CLOSED_PCT = -0.04
#   -0.04 <= sp500_l3_threshold(-0.03) → 触发 L3 (全局平仓)
#   但数据源不可用 ≠ 极端行情, 可能只是网络故障/接口封禁/非交易日
#
# 修复:
#   FAIL_CLOSED_PCT = -0.02
#   -0.02 > sp500_l3(-0.03) → 不触发 L3
#   -0.02 <= sp500_l2(-0.02) → 触发 L2 (禁止开仓, 保守保护)


class TestBUG1FailClosedNoFalseL3:
    """BUG#1 回归: fail-closed 不能误触发 L3"""

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.bug("BUG#1")
    def test_bug1_fail_closed_pct_is_negative_002_not_negative_004(self):
        """BUG#1 配置验证: FAIL_CLOSED_PCT 必须为 -0.02, 不能是 -0.04

        -0.04 会误触发 L3 (因 -0.04 <= sp500_l3=-0.03)
        -0.02 只触发 L2 (因 -0.02 > sp500_l3=-0.03, 但 -0.02 <= sp500_l2=-0.02)
        """
        ogm = OvernightGapMonitor()

        assert ogm.fail_closed_pct == -0.02, (
            f"FAIL_CLOSED_PCT 应为 -0.02 (触发 L2), 实际: {ogm.fail_closed_pct}. "
            f"若为 -0.04 会误触发 L3 全局平仓 (BUG#1)"
        )

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.bug("BUG#1")
    def test_bug1_fail_closed_does_not_trigger_l3(self, mock_all_external_sources_unavailable):
        """BUG#1 核心: 数据源全部不可用时不能触发 L3 全局平仓

        应触发 L2 (禁止开仓, 保守保护)
        """
        ogm = OvernightGapMonitor()
        risk = ogm.evaluate_overnight_risk()

        assert risk["level"] != 3, (
            "fail-closed 不能触发 L3 全局平仓 (数据源不可用 ≠ 极端行情)"
        )
        assert risk["level"] == 2, "fail-closed 应触发 L2 (禁止开仓)"
        assert risk["can_open"] is False, "L2 时不可开新仓"
        assert risk["can_trade"] is True, "L2 时仍可交易 (允许平仓)"
        assert risk["data_source"] == "fail_closed"

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.bug("BUG#1")
    def test_bug1_fail_closed_does_not_clear_orders_in_plan(
        self, mock_all_external_sources_unavailable, sample_trade_plan
    ):
        """BUG#1 端到端: fail-closed 时 apply_to_plan 不能清空所有订单

        L3 会清空 morning_orders/afternoon_orders, 这是过度反应
        L2 应只过滤 BUY 订单, 保留 SELL 平仓订单
        """
        ogm = OvernightGapMonitor()
        risk = ogm.evaluate_overnight_risk()

        # 应用到交易计划
        plan = ogm.apply_to_plan(sample_trade_plan, risk)

        # L2 时不应清空所有订单 (L3 才清空)
        morning = plan["execution_plan"]["morning_orders"]
        afternoon = plan["execution_plan"]["afternoon_orders"]

        # L2 应保留 SELL 订单, 过滤 BUY 订单
        assert len(morning) > 0 or len(afternoon) > 0, (
            "L2 fail-closed 不能清空所有订单 (BUG#1: 原本误触发 L3 会清空)"
        )

        # 验证: BUY 被过滤, SELL 保留
        for order in morning + afternoon:
            assert order["direction"] != "BUY", "L2 应过滤所有 BUY 订单"

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.bug("BUG#1")
    def test_bug1_fail_closed_pct_above_l3_threshold(self):
        """BUG#1 数学验证: fail_closed_pct > sp500_l3_threshold

        -0.02 > -0.03 (条件成立 → 不触发 L3)
        """
        ogm = OvernightGapMonitor()

        assert ogm.fail_closed_pct > ogm.sp500_l3, (
            f"fail_closed_pct({ogm.fail_closed_pct}) 必须 > sp500_l3({ogm.sp500_l3}), "
            f"否则会误触发 L3 全局平仓 (BUG#1)"
        )


# ============================================================
# S&P500 阈值边界测试 (L1/L2/L3 切换)
# ============================================================


class TestSP500LevelBoundaries:
    """S&P500 跌幅 → 级别映射边界测试"""

    @pytest.mark.unit
    def test_sp500_to_level_normal(self):
        """跌幅 0% / 正涨幅 → L0 正常"""
        ogm = OvernightGapMonitor()
        assert ogm._sp500_to_level(0.0) == 0
        assert ogm._sp500_to_level(0.01) == 0  # 上涨 1%
        assert ogm._sp500_to_level(-0.005) == 0  # 跌 0.5% (未到 L1 阈值 -1%)

    @pytest.mark.unit
    def test_sp500_to_level_l1_at_minus_1pct(self):
        """跌幅 -1% → L1 预警 (边界包含)"""
        ogm = OvernightGapMonitor()
        assert ogm._sp500_to_level(-0.01) == 1

    @pytest.mark.unit
    def test_sp500_to_level_l2_at_minus_2pct(self):
        """跌幅 -2% → L2 熔断 (边界包含)"""
        ogm = OvernightGapMonitor()
        assert ogm._sp500_to_level(-0.02) == 2

    @pytest.mark.unit
    def test_sp500_to_level_l3_at_minus_3pct(self):
        """跌幅 -3% → L3 全局平仓 (边界包含)"""
        ogm = OvernightGapMonitor()
        assert ogm._sp500_to_level(-0.03) == 3

    @pytest.mark.unit
    def test_sp500_to_level_extreme_drop(self):
        """跌幅 -5% → L3 (远超阈值)"""
        ogm = OvernightGapMonitor()
        assert ogm._sp500_to_level(-0.05) == 3


# ============================================================
# ADR 偏离度边界测试
# ============================================================


class TestADRLevelBoundaries:
    """ADR 偏离度 → 级别映射边界测试"""

    @pytest.mark.unit
    def test_adr_to_level_normal(self):
        """ADR 偏离 < 2% → L0"""
        ogm = OvernightGapMonitor()
        assert ogm._adr_to_level(0.0) == 0
        assert ogm._adr_to_level(0.01) == 0  # 1%
        assert ogm._adr_to_level(0.019) == 0  # 1.9% (未到 L1)

    @pytest.mark.unit
    def test_adr_to_level_l1_at_2pct(self):
        """ADR 偏离 2% → L1"""
        ogm = OvernightGapMonitor()
        assert ogm._adr_to_level(0.02) == 1

    @pytest.mark.unit
    def test_adr_to_level_l2_at_4pct(self):
        """ADR 偏离 4% → L2"""
        ogm = OvernightGapMonitor()
        assert ogm._adr_to_level(0.04) == 2

    @pytest.mark.unit
    def test_adr_to_level_l3_at_6pct(self):
        """ADR 偏离 6% → L3"""
        ogm = OvernightGapMonitor()
        assert ogm._adr_to_level(0.06) == 3

    @pytest.mark.unit
    def test_adr_negative_deviation_treated_same(self):
        """ADR 负偏离 (下跌家数多) 与正偏离同等处理 (用 abs)"""
        ogm = OvernightGapMonitor()
        assert ogm._adr_to_level(-0.04) == ogm._adr_to_level(0.04)


# ============================================================
# apply_to_plan 行为测试 (L1/L2/L3)
# ============================================================


class TestApplyToPlan:
    """apply_to_plan 各级别动作验证"""

    @pytest.mark.unit
    def test_l3_clears_all_orders_and_halts_trading(self, sample_trade_plan):
        """L3: 清空所有订单 + halt_all_trading=True"""
        ogm = OvernightGapMonitor()
        risk = {
            "level": 3,
            "sp500_change_pct": -0.035,
            "adr_deviation_pct": 0.0,
            "trigger": "sp500_drop_-3.50%",
            "data_source": "external_data",
        }

        plan = ogm.apply_to_plan(sample_trade_plan, risk)

        assert plan["execution_plan"]["morning_orders"] == []
        assert plan["execution_plan"]["afternoon_orders"] == []
        assert plan["market_state"]["halt_all_trading"] is True
        assert plan["market_state"]["circuit_level"] == "CRITICAL"
        assert plan["risk_guard"]["overnight_gap"]["level"] == 3
        assert plan["risk_guard"]["overnight_gap"]["action"] == "HALT_ALL_TRADING"

    @pytest.mark.unit
    def test_l2_filters_buy_keeps_sell(self, sample_trade_plan):
        """L2: 过滤 BUY 订单, 保留 SELL 订单

        sample_trade_plan 含:
            morning: BUY 588080, SELL 512880
            afternoon: BUY 510050
        L2 后期望:
            morning: SELL 512880
            afternoon: 空
        """
        ogm = OvernightGapMonitor()
        risk = {
            "level": 2,
            "sp500_change_pct": -0.025,
            "adr_deviation_pct": 0.0,
            "trigger": "sp500_drop_-2.50%",
            "data_source": "external_data",
        }

        plan = ogm.apply_to_plan(sample_trade_plan, risk)

        morning = plan["execution_plan"]["morning_orders"]
        afternoon = plan["execution_plan"]["afternoon_orders"]

        # 只剩 SELL 订单
        assert len(morning) == 1
        assert morning[0]["direction"] == "SELL"
        assert morning[0]["symbol"] == "512880.SH"
        assert afternoon == [], "afternoon 的 BUY 订单应被过滤"

        assert plan["market_state"]["spot_build_allowed"] is False
        assert plan["market_state"]["circuit_level"] == "WARNING"
        assert plan["risk_guard"]["overnight_gap"]["level"] == 2

    @pytest.mark.unit
    def test_l1_only_marks_no_order_modification(self, sample_trade_plan):
        """L1: 仅标记 risk_guard, 不修改订单"""
        ogm = OvernightGapMonitor()
        risk = {
            "level": 1,
            "sp500_change_pct": -0.015,
            "adr_deviation_pct": 0.0,
            "trigger": "sp500_drop_-1.50%",
            "data_source": "external_data",
        }

        original_morning = list(sample_trade_plan["execution_plan"]["morning_orders"])
        original_afternoon = list(sample_trade_plan["execution_plan"]["afternoon_orders"])

        plan = ogm.apply_to_plan(sample_trade_plan, risk)

        # 订单不变
        assert plan["execution_plan"]["morning_orders"] == original_morning
        assert plan["execution_plan"]["afternoon_orders"] == original_afternoon

        # 但 risk_guard 被标记
        assert plan["risk_guard"]["overnight_gap"]["level"] == 1
        assert plan["risk_guard"]["overnight_gap"]["action"] == "WARNING"

    @pytest.mark.unit
    def test_l0_normal_no_modification(self, sample_trade_plan):
        """L0: 正常状态, 仅写入 NORMAL 标记"""
        ogm = OvernightGapMonitor()
        risk = {
            "level": 0,
            "sp500_change_pct": -0.005,
            "adr_deviation_pct": 0.0,
            "trigger": "none",
            "data_source": "external_data",
        }

        plan = ogm.apply_to_plan(sample_trade_plan, risk)

        assert plan["risk_guard"]["overnight_gap"]["level"] == 0
        assert plan["risk_guard"]["overnight_gap"]["action"] == "NORMAL"


# ============================================================
# 自定义阈值构造测试
# ============================================================


class TestCustomThresholds:
    """构造函数自定义阈值参数"""

    @pytest.mark.unit
    def test_custom_l2_l3_thresholds_override_defaults(self):
        """自定义阈值覆盖默认值"""
        ogm = OvernightGapMonitor(
            sp500_l2_threshold=-0.015,
            sp500_l3_threshold=-0.025,
            adr_l2_threshold=0.03,
            adr_l3_threshold=0.05,
            fail_closed_pct=-0.015,
        )

        assert ogm.sp500_l2 == -0.015
        assert ogm.sp500_l3 == -0.025
        assert ogm.adr_l2 == 0.03
        assert ogm.adr_l3 == 0.05
        assert ogm.fail_closed_pct == -0.015

    @pytest.mark.unit
    def test_custom_fail_closed_still_above_l3(self):
        """自定义 fail_closed_pct 也必须 > sp500_l3 (避免 BUG#1 复发)"""
        ogm = OvernightGapMonitor(
            sp500_l3_threshold=-0.025,
            fail_closed_pct=-0.02,  # > -0.025 ✓
        )

        assert ogm.fail_closed_pct > ogm.sp500_l3


# ============================================================
# Mock fixture: 模拟所有外部数据源不可用 (本文件专用, 不污染 conftest)
# ============================================================


@pytest.fixture
def mock_all_external_sources_unavailable(monkeypatch):
    """mock ExternalDataManager + cache 文件, 强制走 fail-closed 路径

    1. ExternalDataManager.get_global_stock → 返回 None
    2. cache/external_data/overnight_gap_latest.json → 不存在
    """
    # Layer 1: ExternalDataManager 不可用
    def _raise_or_none(*args, **kwargs):
        raise ConnectionError("ExternalDataSource unavailable (mocked)")

    try:
        monkeypatch.setattr(
            "utils.overnight_gap_monitor.OvernightGapMonitor._fetch_via_external_source",
            lambda self: (None, None, False),
        )
    except (AttributeError, ImportError):
        pass

    # Layer 2: cache 不可用
    try:
        monkeypatch.setattr(
            "utils.overnight_gap_monitor.OvernightGapMonitor._fetch_via_cache",
            lambda self: (None, None, False),
        )
    except (AttributeError, ImportError):
        pass
