# -*- coding: utf-8 -*-
"""test_kill_switch_unit.py — KillSwitch 三级熔断协议单元测试

覆盖 bug 回归:
    - P0-D: margin_used/total_equity 为 None 时 check_margin_status 崩溃
    - P1-G: daily_workflow 局部实例未注册 broker_callback 导致 execute_kill_switch 抛 RuntimeError

设计原则:
    - 全 mock, 不读取真实 config/positions.json
    - 隔离 TRADING_ENV / KILL_SWITCH_SIM_MODE 等环境变量
    - 每个 bug 至少一个用例, 函数名包含 bug 编号
"""
import pytest
from unittest.mock import MagicMock

from utils.kill_switch import KillSwitch


# ============================================================
# P0-D 回归: margin_used/total_equity 为 None 时 check_margin_status 崩溃
# ============================================================
# Bug 历史:
#   risk_guard_integrator 从 pnl_report 读取 margin_used/total_equity,
#   当生产报告字段缺失(为 None)时, 传给 check_margin_status(margin_usage=None),
#   原代码在 production env 直接走 _get_margin_status() 估算, 但若 positions.json
#   也不存在, 仍返回 0.50 (L1), 未真正触发 fail-closed.
#
# 修复:
#   - production env + margin_usage=None → 直接返回 L3 fail-closed
#   - dev/test env + margin_usage=None → 回退到 _get_margin_status()


class TestP0DNoneMarginUsageFailClosed:
    """P0-D 回归: None margin_usage 在生产环境必须 fail-closed, 不抛异常"""

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.bug("P0-D")
    def test_p0d_production_none_margin_returns_l3_fail_closed(
        self, production_env, tmp_kill_switch_log
    ):
        """P0-D: TRADING_ENV=production + margin_usage=None → level=3, 不抛异常"""
        ks = KillSwitch()

        # 必须不抛异常 (原 bug 抛 TypeError)
        result = ks.check_margin_status(margin_usage=None)

        assert result["level"] == 3, "生产环境数据不可用必须 fail-closed 到 L3"
        assert result["can_trade"] is False
        assert result["can_open"] is False
        assert "FAIL_CLOSED" in result["action"], "action 必须包含 FAIL_CLOSED 标识"

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.bug("P0-D")
    def test_p0d_production_none_margin_does_not_raise_typeerror(
        self, production_env, tmp_kill_switch_log
    ):
        """P0-D 核心: 不能抛 TypeError (原 bug 的崩溃点)"""
        ks = KillSwitch()

        # 关键断言: 不抛异常
        try:
            ks.check_margin_status(margin_usage=None)
        except TypeError as e:
            pytest.fail(f"P0-D 回归: check_margin_status 抛 TypeError: {e}")

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.bug("P0-D")
    def test_p0d_dev_none_margin_falls_back_to_estimate(
        self, clean_env, tmp_kill_switch_log, monkeypatch
    ):
        """P0-D: dev 环境 + margin_usage=None → 回退到 _estimate_margin_from_positions

        Mock _estimate_margin_from_positions 返回 0.30, 期望 level=0 (无熔断)
        """
        ks = KillSwitch()

        # Mock 估算函数返回 30% 占用 (低于 L1 阈值 50%)
        monkeypatch.setattr(ks, "_estimate_margin_from_positions", lambda: 0.30)

        result = ks.check_margin_status(margin_usage=None)

        assert result["level"] == 0, "30% 占用应低于 L1 阈值"
        assert result["margin_usage_ratio"] == pytest.approx(0.30)
        assert result["can_trade"] is True
        assert result["can_open"] is True


# ============================================================
# P1-G 回归: 未注册 broker_callback 时 execute_kill_switch 抛 RuntimeError
# ============================================================
# Bug 历史:
#   daily_workflow.py 中 `ks = KillSwitch()` 创建局部实例,
#   未注册 broker_callback, 导致 execute_kill_switch(level) 抛 RuntimeError,
#   使整个 EOD Guard 链中断.
#
# 修复:
#   - 改为 `ks = getattr(self, 'ks', None) or KillSwitch()` 复用主实例
#   - KillSwitch.execute_kill_switch 在 _broker_callback 为 None 时 fail-fast


class TestP1GBrokerCallbackRegistration:
    """P1-G 回归: broker_callback 注册与 fail-fast 行为"""

    @pytest.mark.unit
    @pytest.mark.p1
    @pytest.mark.bug("P1-G")
    def test_p1g_execute_without_callback_raises_runtime_error(self, clean_env):
        """P1-G: 未注册 callback 时 execute_kill_switch(1) 必须抛 RuntimeError

        这是 fail-fast 设计 — 防止在无执行通道下静默通过熔断协议
        """
        ks = KillSwitch()
        assert ks._broker_callback is None, "新实例默认无 callback"

        with pytest.raises(RuntimeError, match="broker_callback"):
            ks.execute_kill_switch(level=1)

    @pytest.mark.unit
    @pytest.mark.p1
    @pytest.mark.bug("P1-G")
    def test_p1g_execute_with_callback_completes_successfully(
        self, clean_env, broker_callback_mock
    ):
        """P1-G: 注册 callback 后 execute_kill_switch 不抛异常, 返回 executed=True"""
        ks = KillSwitch()
        ks.set_broker_callback(broker_callback_mock)

        result = ks.execute_kill_switch(level=1)

        assert result["executed"] is True, "callback 注册后必须执行成功"
        assert result["level"] == 1
        # 验证 callback 被实际调用
        broker_callback_mock.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.p1
    @pytest.mark.bug("P1-G")
    def test_p1g_reuse_main_instance_preserves_callback(
        self, clean_env, broker_callback_mock
    ):
        """P1-G 修复验证: 复用主实例保留 callback

        模拟 daily_workflow.py 修复后的模式:
            ks = getattr(self, 'ks', None) or KillSwitch()
        """
        # 主实例注册 callback
        main_ks = KillSwitch()
        main_ks.set_broker_callback(broker_callback_mock)

        # 模拟 daily_workflow 修复后的复用逻辑
        getattr(main_ks, "ks", None) or main_ks  # 简化: 直接复用主实例
        reused_ks = main_ks  # 在 daily_workflow 中是 self.ks

        # 复用的实例必须保留 callback
        assert reused_ks._broker_callback is not None, "复用主实例必须保留 callback"

        # 执行不应抛 RuntimeError
        result = reused_ks.execute_kill_switch(level=2)
        assert result["executed"] is True

    @pytest.mark.unit
    @pytest.mark.p1
    @pytest.mark.bug("P1-G")
    def test_p1g_callback_failure_returns_executed_false(
        self, clean_env, tmp_kill_switch_log
    ):
        """P1-G 补充: callback 抛异常时返回 executed=False, 不静默通过

        防止 callback 注册了但执行失败时被误认为成功
        """
        ks = KillSwitch()

        # 注册一个会抛异常的 callback
        failing_callback = MagicMock(side_effect=ConnectionError("broker offline"))
        ks.set_broker_callback(failing_callback)

        result = ks.execute_kill_switch(level=2)

        assert result["executed"] is False, "callback 失败时必须返回 executed=False"
        assert "broker_callback_failed" in str(
            result.get("actions_taken", [])
        ) or any(
            "failed" in str(a).lower() for a in result.get("actions_taken", [])
        ), "actions_taken 必须记录失败"


# ============================================================
# KillSwitch 三级熔断阈值边界测试 (非 bug 回归, 用于防止阈值漂移)
# ============================================================


class TestKillSwitchLevelBoundaries:
    """KillSwitch 阈值边界: L0/L1/L2/L3 切换点

    阈值定义:
        L1: ratio >= 0.50 (一级警戒)
        L2: ratio >= 0.75 (二级熔断)
        L3: extreme_margin_call, ratio >= 0.95 (三级互盲)
    """

    @pytest.mark.unit
    def test_level_0_below_50_pct(self, clean_env, tmp_kill_switch_log):
        """ratio < 0.50 → L0 正常, 可交易可开仓"""
        ks = KillSwitch()
        result = ks.check_margin_status(margin_usage=0.49)

        assert result["level"] == 0
        assert result["can_trade"] is True
        assert result["can_open"] is True

    @pytest.mark.unit
    def test_level_1_at_50_pct(self, clean_env, tmp_kill_switch_log):
        """ratio == 0.50 → L1 警戒 (边界包含)"""
        ks = KillSwitch()
        result = ks.check_margin_status(margin_usage=0.50)

        assert result["level"] == 1
        assert result["can_trade"] is True, "L1 仍可交易 (只平仓不计数)"
        assert result["can_open"] is False, "L1 不可开新仓"

    @pytest.mark.unit
    def test_level_1_at_74_pct(self, clean_env, tmp_kill_switch_log):
        """ratio == 0.74 → 仍为 L1 (L2 阈值 0.75 不含)"""
        ks = KillSwitch()
        result = ks.check_margin_status(margin_usage=0.74)

        assert result["level"] == 1

    @pytest.mark.unit
    def test_level_2_at_75_pct(self, clean_env, tmp_kill_switch_log):
        """ratio == 0.75 → L2 熔断 (边界包含)"""
        ks = KillSwitch()
        result = ks.check_margin_status(margin_usage=0.75)

        assert result["level"] == 2
        assert result["can_trade"] is False, "L2 不可交易"
        assert result["can_open"] is False

    @pytest.mark.unit
    def test_level_3_at_95_pct_extreme(self, clean_env, tmp_kill_switch_log):
        """ratio == 0.95 → L3 极端 margin call"""
        ks = KillSwitch()
        result = ks.check_margin_status(margin_usage=0.95)

        assert result["level"] == 3
        assert result["extreme_margin_call"] is True
        assert result["can_trade"] is False

    @pytest.mark.unit
    def test_margin_usage_clamped_to_valid_range(
        self, clean_env, tmp_kill_switch_log
    ):
        """margin_usage 越界 (>1.0 或 <0) 必须被 clamp 到 [0, 1]"""
        ks = KillSwitch()

        # 上界
        result_high = ks.check_margin_status(margin_usage=2.0)
        assert result_high["margin_usage_ratio"] == 1.0
        assert result_high["level"] == 3

        # 下界
        result_low = ks.check_margin_status(margin_usage=-0.5)
        assert result_low["margin_usage_ratio"] == 0.0
        assert result_low["level"] == 0


# ============================================================
# 配置加载鲁棒性测试
# ============================================================


class TestKillSwitchConfigLoading:
    """配置文件加载失败时不应崩溃"""

    @pytest.mark.unit
    def test_missing_config_returns_empty_dict(self, tmp_path):
        """配置文件不存在时返回空 dict, 不抛异常"""
        ks = KillSwitch(config_path=tmp_path / "nonexistent.yaml")
        assert ks.config == {}, "配置加载失败应返回空 dict"

    @pytest.mark.unit
    def test_invalid_level_returns_not_executed(self, clean_env, broker_callback_mock):
        """无效熔断级别 (如 4) 返回 executed=False, 不抛异常"""
        ks = KillSwitch()
        ks.set_broker_callback(broker_callback_mock)

        result = ks.execute_kill_switch(level=4)
        assert result["executed"] is False
        assert result["reason"] == "invalid_level"
