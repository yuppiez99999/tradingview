# -*- coding: utf-8 -*-
"""test_risk_guard_integrator_unit.py — 风控守卫集成器单元测试

覆盖 bug 回归:
    - BUG#4: guard_kill_switch 中 L2 被当作 L3 处理 (误清空所有订单)
    - P0-D 集成: pnl_report margin_used=None 时 fallback 到 _estimate_margin_from_positions

设计原则:
    - 全 mock KillSwitch / DrawdownController 等依赖, 不触发真实文件 IO
    - 重点验证 level 字符串归一化 (L0/L1/L2/L3/OK) 和订单过滤逻辑
"""
import pytest
from unittest.mock import MagicMock

from utils.risk_guard_integrator import RiskGuardIntegrator


# ============================================================
# 辅助 fixture
# ============================================================

@pytest.fixture
def integrator(tmp_path, monkeypatch):
    """隔离 LOGS_DIR / REPORTS_DIR / TRADE_PLANS_DIR 的 RiskGuardIntegrator 实例"""
    monkeypatch.setattr(
        "utils.risk_guard_integrator.LOGS_DIR", tmp_path / "logs"
    )
    monkeypatch.setattr(
        "utils.risk_guard_integrator.REPORTS_DIR", tmp_path / "reports"
    )
    monkeypatch.setattr(
        "utils.risk_guard_integrator.TRADE_PLANS_DIR", tmp_path / "trade_plans"
    )
    return RiskGuardIntegrator(report_date="2026-07-21")


@pytest.fixture
def mock_kill_switch_module(integrator, monkeypatch):
    """mock utils.kill_switch.KillSwitch, 提供可配置 check_margin_status 行为

    Returns:
        (mock_class, mock_instance) — 测试可配置 mock_instance.check_margin_status.return_value
    """
    mock_instance = MagicMock(name="KillSwitch_instance")
    # 默认 _estimate_margin_from_positions 返回 0.30 (L0)
    mock_instance._estimate_margin_from_positions.return_value = 0.30
    # 默认 check_margin_status 返回 L0 正常
    mock_instance.check_margin_status.return_value = {
        "level": 0,
        "level_name": "正常",
        "margin_usage_ratio": 0.30,
        "actions": [],
        "auto_execute": False,
        "can_trade": True,
        "can_open": True,
        "action": "正常",
    }
    mock_instance.check_concentration.return_value = {"level": "OK"}

    mock_class = MagicMock(name="KillSwitch_class", return_value=mock_instance)

    # Patch import inside guard_kill_switch
    import sys
    sys.modules["utils.kill_switch"] = MagicMock(KillSwitch=mock_class)
    return mock_class, mock_instance


# ============================================================
# BUG#4 回归: L2 被当作 L3 处理 (误清空所有订单)
# ============================================================
# Bug 历史 (v8.6.7 修复):
#   原代码: if not margin_status['can_trade']:  # 清空所有订单
#   但 can_trade = (level < 2), 所以 L2 时 can_trade=False
#   → L2 被误判为 L3, 清空所有订单 (应只过滤 BUY)
#
# 修复:
#   - 用 ks_level_int 判断, 不用 can_trade
#   - L3 (>=3): 清空所有
#   - L2 (==2): 过滤 BUY 保留 SELL
#   - L1 (==1): 仅预警


class TestBUG4L2NotTreatedAsL3:
    """BUG#4 回归: L2 不能被误判为 L3"""

    @pytest.mark.unit
    @pytest.mark.p1
    @pytest.mark.bug("BUG#4")
    def test_bug4_l2_filters_buy_keeps_sell_not_clears_all(
        self, integrator, mock_kill_switch_module, sample_trade_plan, sample_pnl_report_full
    ):
        """BUG#4 核心: L2 时只能过滤 BUY, 不能清空所有订单

        sample_trade_plan 含:
            morning: BUY 588080, SELL 512880
            afternoon: BUY 510050
        L2 后期望:
            morning: SELL 512880 (保留)
            afternoon: 空 (BUY 被过滤)
        """
        _mock_class, mock_instance = mock_kill_switch_module
        # 配置 KillSwitch 返回 L2 (can_trade=False 是 BUG#4 触发条件)
        mock_instance.check_margin_status.return_value = {
            "level": 2,
            "level_name": "二级熔断线",
            "margin_usage_ratio": 0.75,
            "actions": ["force_close_deep_otm_short"],
            "auto_execute": True,
            "can_trade": False,   # ← BUG#4 触发条件: L2 时 can_trade 也为 False
            "can_open": False,
            "action": "L2熔断",
        }

        plan = integrator.guard_kill_switch(sample_pnl_report_full, sample_trade_plan)

        morning = plan["execution_plan"]["morning_orders"]
        afternoon = plan["execution_plan"]["afternoon_orders"]

        # L2 应保留 SELL, 过滤 BUY
        assert len(morning) == 1, "L2 应保留 1 个 SELL 订单 (BUG#4: 原本会清空所有)"
        assert morning[0]["direction"] == "SELL"
        assert afternoon == [], "L2 应过滤 afternoon 的 BUY 订单"

        # 不应触发 halt_all_trading (L3 才触发)
        assert plan["market_state"].get("circuit_level") == "WARNING", \
            "L2 应为 WARNING, 不是 CRITICAL"

    @pytest.mark.unit
    @pytest.mark.p1
    @pytest.mark.bug("BUG#4")
    def test_bug4_l3_clears_all_orders(
        self, integrator, mock_kill_switch_module, sample_trade_plan, sample_pnl_report_full
    ):
        """BUG#4 对比: L3 时确实清空所有订单 (正确行为)"""
        _mock_class, mock_instance = mock_kill_switch_module
        mock_instance.check_margin_status.return_value = {
            "level": 3,
            "level_name": "三级互盲机制",
            "margin_usage_ratio": 0.95,
            "actions": ["liquidate_red_etf"],
            "auto_execute": True,
            "can_trade": False,
            "can_open": False,
            "action": "L3熔断",
        }

        plan = integrator.guard_kill_switch(sample_pnl_report_full, sample_trade_plan)

        # L3 清空所有订单
        assert plan["execution_plan"]["morning_orders"] == []
        assert plan["execution_plan"]["afternoon_orders"] == []
        assert plan["market_state"]["circuit_level"] == "CRITICAL"
        assert plan["market_state"]["build_allowed"] is False

    @pytest.mark.unit
    @pytest.mark.p1
    @pytest.mark.bug("BUG#4")
    def test_bug4_l1_only_marks_no_order_modification(
        self, integrator, mock_kill_switch_module, sample_trade_plan, sample_pnl_report_full
    ):
        """BUG#4 边界: L1 时不应修改订单, 只标记 WATCH"""
        _mock_class, mock_instance = mock_kill_switch_module
        mock_instance.check_margin_status.return_value = {
            "level": 1,
            "level_name": "一级警戒线",
            "margin_usage_ratio": 0.55,
            "actions": ["disable_new_positions"],
            "auto_execute": True,
            "can_trade": True,   # L1 时 can_trade=True
            "can_open": False,
            "action": "L1警戒",
        }

        original_morning = list(sample_trade_plan["execution_plan"]["morning_orders"])
        original_afternoon = list(sample_trade_plan["execution_plan"]["afternoon_orders"])

        plan = integrator.guard_kill_switch(sample_pnl_report_full, sample_trade_plan)

        # 订单不变
        assert plan["execution_plan"]["morning_orders"] == original_morning
        assert plan["execution_plan"]["afternoon_orders"] == original_afternoon
        # circuit_level 应为 WATCH (最低优先级)
        assert plan["market_state"].get("circuit_level") == "WATCH"

    @pytest.mark.unit
    @pytest.mark.p1
    @pytest.mark.bug("BUG#4")
    def test_bug4_string_level_l2_normalized_correctly(
        self, integrator, mock_kill_switch_module, sample_trade_plan, sample_pnl_report_full
    ):
        """BUG#4 边界: 字符串 'L2' 也能正确归一化为 2, 不被当作 L3"""
        _mock_class, mock_instance = mock_kill_switch_module
        # 模拟降级模式返回字符串 level
        mock_instance.check_margin_status.return_value = {
            "level": "L2",  # 字符串形式
            "margin_usage_ratio": 0.70,
            "actions": [],
            "can_trade": False,
            "can_open": False,
            "action": "MARGIN_LIMIT",
        }

        plan = integrator.guard_kill_switch(sample_pnl_report_full, sample_trade_plan)

        # 字符串 "L2" 也应只过滤 BUY, 不清空所有
        morning = plan["execution_plan"]["morning_orders"]
        assert len(morning) == 1, "字符串 L2 也应保留 SELL 订单"
        assert morning[0]["direction"] == "SELL"


# ============================================================
# P0-D 集成: pnl_report margin_used=None 时 fallback
# ============================================================


class TestP0DFallbackInGuardKillSwitch:
    """P0-D 集成: pnl_report margin_used/total_equity 为 None 时正确 fallback"""

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.bug("P0-D")
    def test_p0d_none_margin_falls_back_to_estimate(
        self, integrator, mock_kill_switch_module, sample_trade_plan, sample_pnl_report_broken_p0d
    ):
        """P0-D: margin_used=None, total_equity=None → 调用 _estimate_margin_from_positions"""
        _mock_class, mock_instance = mock_kill_switch_module
        mock_instance._estimate_margin_from_positions.return_value = 0.40
        mock_instance.check_margin_status.return_value = {
            "level": 0, "margin_usage_ratio": 0.40,
            "can_trade": True, "can_open": True, "action": "正常",
        }

        # sample_pnl_report_broken_p0d 含 margin_used=None, total_equity=None
        plan = integrator.guard_kill_switch(sample_pnl_report_broken_p0d, sample_trade_plan)

        # 必须调用 _estimate_margin_from_positions
        mock_instance._estimate_margin_from_positions.assert_called_once()
        # 不应抛异常 (原 bug 抛 TypeError)
        assert plan is not None

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.bug("P0-D")
    def test_p0d_estimate_failure_uses_conservative_050(
        self, integrator, mock_kill_switch_module, sample_trade_plan, sample_pnl_report_broken_p0d
    ):
        """P0-D: _estimate_margin_from_positions 抛异常时使用保守值 0.50"""
        _mock_class, mock_instance = mock_kill_switch_module
        mock_instance._estimate_margin_from_positions.side_effect = Exception("positions.json missing")
        mock_instance.check_margin_status.return_value = {
            "level": 1, "margin_usage_ratio": 0.50,
            "can_trade": True, "can_open": False, "action": "L1警戒",
        }

        # 不应抛异常, 使用保守值 0.50
        integrator.guard_kill_switch(sample_pnl_report_broken_p0d, sample_trade_plan)

        # check_margin_status 应被调用, 参数为 0.50
        mock_instance.check_margin_status.assert_called_once()
        called_arg = mock_instance.check_margin_status.call_args[0][0]
        assert called_arg == 0.50, "保守值应为 0.50"


# ============================================================
# _extract_underlying_code 测试 (对冲去重底层代码识别)
# ============================================================


class TestExtractUnderlyingCode:
    """_extract_underlying_code: 从 instrument 名称提取 6 位底层代码"""

    @pytest.mark.unit
    def test_direct_code_extraction(self, integrator):
        """直接 6 位代码: '510050 Put' → '510050'"""
        assert integrator._extract_underlying_code("510050 Put") == "510050"
        assert integrator._extract_underlying_code("588080 Put") == "588080"
        assert integrator._extract_underlying_code("510300") == "510300"

    @pytest.mark.unit
    def test_chinese_name_mapping(self, integrator):
        """中文名称映射: '上证50ETF' → '510050'"""
        assert integrator._extract_underlying_code("上证50") == "510050"
        assert integrator._extract_underlying_code("科创50ETF") == "588080"
        assert integrator._extract_underlying_code("沪深300") == "510300"

    @pytest.mark.unit
    def test_empty_input_returns_none(self, integrator):
        """空输入返回 None"""
        assert integrator._extract_underlying_code("") is None
        assert integrator._extract_underlying_code(None) is None

    @pytest.mark.unit
    def test_unknown_code_returns_none(self, integrator):
        """无法识别的代码返回 None"""
        assert integrator._extract_underlying_code("UNKNOWN_ETF") is None
        assert integrator._extract_underlying_code("random_string") is None


# ============================================================
# _deduplicate_put_orders 测试
# ============================================================


class TestDeduplicatePutOrders:
    """PUT 订单去重逻辑"""

    @pytest.mark.unit
    def test_no_duplicate_no_modification(self, integrator):
        """无重复时订单不变"""
        plan = {
            "put_protection_orders": [
                {"underlying": "510050", "contracts": 60}
            ],
            "hedge_execution": {
                "options_orders": [
                    {"instrument": "588080 Put", "contracts": 25}
                ],
                "futures_orders": [{"instrument": "IF"}],
            },
        }
        original_options = list(plan["hedge_execution"]["options_orders"])

        integrator._deduplicate_put_orders(plan)

        # 510050 (put_protection) vs 588080 (hedge_execution) 不重复
        assert plan["hedge_execution"]["options_orders"] == original_options

    @pytest.mark.unit
    def test_duplicate_underlying_removed_from_hedge(self, integrator):
        """同一底层重复时, 从 hedge_execution.options_orders 剔除"""
        plan = {
            "put_protection_orders": [
                {"underlying": "510050", "contracts": 60}  # ProtectivePutEngine 已覆盖
            ],
            "hedge_execution": {
                "options_orders": [
                    {"instrument": "510050 Put", "contracts": 30, "order_id": "HEDGE_PUT_1"},  # 重复
                    {"instrument": "588080 Put", "contracts": 25, "order_id": "HEDGE_PUT_2"},  # 保留
                ],
                "futures_orders": [{"instrument": "IF"}],
            },
            "risk_guard": {},
        }

        integrator._deduplicate_put_orders(plan)

        # 510050 Put 应被剔除, 只剩 588080 Put
        assert len(plan["hedge_execution"]["options_orders"]) == 1
        assert plan["hedge_execution"]["options_orders"][0]["instrument"] == "588080 Put"

        # risk_guard 应记录去重信息
        assert "put_hedge_dedup" in plan["risk_guard"]
        assert plan["risk_guard"]["put_hedge_dedup"]["removed_count"] == 1
        assert "510050" in plan["risk_guard"]["put_hedge_dedup"]["removed"]

    @pytest.mark.unit
    def test_no_put_protection_no_dedup(self, integrator):
        """put_protection_orders 为空时不触发去重"""
        plan = {
            "put_protection_orders": [],
            "hedge_execution": {
                "options_orders": [{"instrument": "510050 Put"}],
                "futures_orders": [],
            },
        }
        original_options = list(plan["hedge_execution"]["options_orders"])

        integrator._deduplicate_put_orders(plan)

        assert plan["hedge_execution"]["options_orders"] == original_options
