"""test_kill_switch_protocol_integration.py — KillSwitch 三级熔断协议端到端集成测试

5 条关键链路 #2: margin_usage → KillSwitch.check_margin_status → execute_kill_switch → broker_callback

设计原则:
    - 使用真实 KillSwitch 实例 (非 mock), 验证完整协议链路
    - Mock broker_callback (避免真实交易接口)
    - 验证 L1/L2/L3 → 动作映射 → broker 调用参数
"""

import pytest

from utils.kill_switch import KillSwitch


@pytest.fixture
def real_kill_switch_with_callback(
    clean_env, tmp_kill_switch_log, broker_callback_mock
):
    """真实 KillSwitch 实例 + mock broker_callback

    与 daily_workflow.py 集成模式一致:
        ks = KillSwitch()
        ks.set_broker_callback(callback)
    """
    ks = KillSwitch()
    ks.set_broker_callback(broker_callback_mock)
    return ks, broker_callback_mock


# ============================================================
# 集成测试: 三级熔断协议端到端
# ============================================================


class TestKillSwitchProtocolEndToEnd:
    """KillSwitch 三级熔断协议集成测试"""

    @pytest.mark.integration
    def test_l1_protocol_watch_mode(
        self, real_kill_switch_with_callback, sample_trade_plan
    ):
        """L1 协议: 50% 保证金 → 预警 + 防守模式, 不平仓"""
        ks, callback = real_kill_switch_with_callback

        status = ks.check_margin_status(margin_usage=0.55)
        assert status["level"] == 1

        result = ks.execute_kill_switch(level=1)

        # L1 应执行成功
        assert result["executed"] is True
        # broker_callback 应被调用
        callback.assert_called_once()
        # 调用参数: level=1, actions=list
        call_args = callback.call_args
        assert call_args[0][0] == 1, "callback 第一参数应为 level=1"

    @pytest.mark.integration
    def test_l2_protocol_force_close_otm(
        self, real_kill_switch_with_callback, sample_trade_plan
    ):
        """L2 协议: 75% 保证金 → 强平深虚值期权空头"""
        ks, _callback = real_kill_switch_with_callback

        status = ks.check_margin_status(margin_usage=0.78)
        assert status["level"] == 2

        result = ks.execute_kill_switch(level=2)

        assert result["executed"] is True
        # actions_taken 应包含 force_close_deep_otm_short
        actions_str = str(result["actions_taken"])
        assert "force_close_deep_otm_short" in actions_str

    @pytest.mark.integration
    def test_l3_protocol_liquidate_etf(
        self, real_kill_switch_with_callback, sample_trade_plan
    ):
        """L3 协议: 95% 保证金 → 变现 10% 红利 ETF"""
        ks, _callback = real_kill_switch_with_callback

        status = ks.check_margin_status(margin_usage=0.96)
        assert status["level"] == 3
        assert status["extreme_margin_call"] is True

        result = ks.execute_kill_switch(level=3)

        assert result["executed"] is True
        actions_str = str(result["actions_taken"])
        assert "liquidate_red_etf" in actions_str
        assert "cross_asset_inject" in actions_str

    @pytest.mark.integration
    def test_protocol_logging_persists_to_jsonl(
        self, real_kill_switch_with_callback, tmp_kill_switch_log
    ):
        """熔断事件必须写入 kill_switch_events.jsonl (审计追踪)"""
        ks, _ = real_kill_switch_with_callback

        # 触发 L1
        ks.check_margin_status(margin_usage=0.55)

        # 日志文件应有内容
        assert tmp_kill_switch_log.exists()
        content = tmp_kill_switch_log.read_text(encoding="utf-8")
        assert len(content) > 0, "kill_switch_events.jsonl 必须记录熔断事件"

        # 应为 JSON Lines 格式
        lines = content.strip().split("\n")
        assert len(lines) >= 1
        import json

        for line in lines:
            event = json.loads(line)  # 不抛异常
            assert "level" in event
            assert "timestamp" in event

    @pytest.mark.integration
    def test_protocol_full_chain_margin_to_broker(self, real_kill_switch_with_callback):
        """完整链路: margin_usage → check → execute → broker_callback

        验证 daily_workflow.py 集成模式的端到端正确性
        """
        ks, callback = real_kill_switch_with_callback

        # 模拟 daily_workflow 集成调用
        # 1. 检查保证金 (传入真实 margin_usage)
        status = ks.check_margin_status(margin_usage=0.80)  # L2
        assert status["level"] == 2

        # 2. 根据级别执行
        if status["level"] >= 1:
            result = ks.execute_kill_switch(status["level"])
            assert result["executed"] is True

        # 3. 验证 broker 接收到正确参数
        callback.assert_called_once()
        level_passed, actions_passed = callback.call_args[0]
        assert level_passed == 2
        assert isinstance(actions_passed, list)
