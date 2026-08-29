"""T09: daily_workflow 注册 KillSwitch.broker_callback — L2/L3 触发真撤单测试

验证目标:
    1. KillSwitch.set_broker_callback 注册后, execute_kill_switch 真实调用 callback
    2. L2 触发时 callback 接收到 level=2, 执行强平深虚值期权空头动作
    3. L3 触发时 callback 接收到 level=3, 执行红利ETF变现动作
    4. callback 抛异常时返回 executed=False, 不静默通过
    5. 模拟真实 broker 平仓链路: broker.place(BUY_TO_CLOSE / SELL) 被调用
    6. dry_run 模式下不真实下单
    7. broker 未连接时返回失败
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.kill_switch import KillSwitch  # noqa: E402


class BrokerCallbackSimulator:
    """模拟 daily_workflow._execute_kill_switch_callback 的行为."""

    def __init__(self, dry_run=True, broker_connected=True, positions=None):
        self.dry_run = dry_run
        self.broker_connected = broker_connected
        self.positions = positions or {}
        self.calls = []
        self.broker_place_calls = []
        self._mock_broker = MagicMock(name="broker")
        self._mock_broker.place.side_effect = self._mock_place
        self._mock_broker.wait_fill.return_value = {"price": 10.5, "status": "FILLED"}
        self._mock_broker.get_positions.return_value = self.positions

    def _mock_place(self, symbol, quantity, side, order_type="MARKET"):
        self.broker_place_calls.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "side": side,
                "order_type": order_type,
            }
        )
        return f"order-{len(self.broker_place_calls):04d}"

    def __call__(self, level, actions):
        call_record = {
            "level": level,
            "actions": list(actions) if actions else [],
            "dry_run": self.dry_run,
            "broker_connected": self.broker_connected,
        }
        self.calls.append(call_record)

        actions_taken = []
        real_close_executed = False
        critical_note = ""

        if level == 1:
            actions_taken.append(
                {"action": "disable_new_positions", "status": "executed"}
            )
            real_close_executed = True

        elif level == 2:
            actions_taken.append(
                {"action": "cancel_pending_buys", "status": "executed"}
            )
            if self.dry_run:
                actions_taken.append(
                    {
                        "action": "force_close_deep_otm_short",
                        "status": "logged_dry_run",
                    }
                )
                real_close_executed = True
                critical_note = "L2 DRY-RUN"
            else:
                # fail-closed: broker 未连接或无持仓可平必须抛异常
                # (KillSwitch 只看异常, 不读返回的 executed 字段)
                if not self.broker_connected:
                    raise RuntimeError(
                        f"L{level} broker disconnected, cannot force close"
                    )
                close_result = self._execute_real_force_close(level=2)
                if not close_result["success"]:
                    raise RuntimeError(
                        f"L{level} no positions to close: {close_result['detail']}"
                    )
                actions_taken.append(
                    {
                        "action": "force_close_deep_otm_short",
                        "status": "executed",
                        "broker_result": close_result,
                    }
                )
                real_close_executed = True

        elif level == 3:
            if self.dry_run:
                actions_taken.append(
                    {
                        "action": "liquidate_red_etf_10pct",
                        "status": "logged_dry_run",
                    }
                )
                real_close_executed = True
                critical_note = "L3 DRY-RUN"
            else:
                # fail-closed: broker 未连接或无持仓可平必须抛异常
                if not self.broker_connected:
                    raise RuntimeError(
                        f"L{level} broker disconnected, cannot liquidate"
                    )
                close_result = self._execute_real_force_close(level=3)
                if not close_result["success"]:
                    raise RuntimeError(
                        f"L{level} no positions to liquidate: {close_result['detail']}"
                    )
                actions_taken.append(
                    {
                        "action": "liquidate_red_etf_10pct",
                        "status": "executed",
                        "broker_result": close_result,
                    }
                )
                real_close_executed = True
            actions_taken.append({"action": "halt_all_trading", "status": "executed"})

        return {
            "executed": real_close_executed,
            "level": level,
            "actions_taken": actions_taken,
            "critical_note": critical_note,
            "dry_run": self.dry_run,
        }

    def _execute_real_force_close(self, level):
        # 注: broker_connected 检查已在 __call__ 中完成 (fail-closed 抛异常)
        fills = []
        if level >= 2:
            for symbol, pos in self.positions.items():
                if not isinstance(pos, dict):
                    continue
                side = str(pos.get("side", "")).upper()
                if side not in ("SELL", "SHORT"):
                    continue
                qty = int(pos.get("quantity", 0))
                if qty <= 0:
                    continue
                order_id = self._mock_broker.place(
                    symbol=symbol,
                    quantity=qty,
                    side="BUY_TO_CLOSE",
                    order_type="MARKET",
                )
                fill = self._mock_broker.wait_fill(order_id, timeout=60)
                fills.append(
                    {
                        "symbol": symbol,
                        "side": "BUY_TO_CLOSE",
                        "quantity": qty,
                        "fill_price": fill.get("price", 0),
                        "status": "FILLED",
                    }
                )

        if level >= 3:
            RED_ETF_CODES = ["512890", "515180"]  # noqa: N806
            RED_ETF_SELL_PCT = 0.10  # noqa: N806
            for etf_code in RED_ETF_CODES:
                for symbol, pos in self.positions.items():
                    if etf_code not in str(symbol):
                        continue
                    full_shares = int(pos.get("actual_shares", pos.get("quantity", 0)))
                    if full_shares <= 0:
                        continue
                    sell_qty = int(full_shares * RED_ETF_SELL_PCT)
                    sell_qty = (sell_qty // 100) * 100
                    if sell_qty < 100:
                        continue
                    order_id = self._mock_broker.place(
                        symbol=str(symbol),
                        quantity=sell_qty,
                        side="SELL",
                        order_type="MARKET",
                    )
                    fill = self._mock_broker.wait_fill(order_id, timeout=60)
                    fills.append(
                        {
                            "symbol": symbol,
                            "side": "SELL",
                            "quantity": sell_qty,
                            "fill_price": fill.get("price", 0),
                            "status": "FILLED",
                            "rationale": "L3_red_etf_liquidation",
                        }
                    )

        return {
            "success": len(fills) > 0,
            "detail": (
                f"L{level} done: {len(fills)} fills"
                if fills
                else f"L{level} no positions"
            ),
            "broker_action": f"real_close_executed_level_{level}",
            "fills": fills,
        }


class TestT09CallbackRegistration:
    """T09: broker_callback 注册后, L2/L3 触发真实调用 callback."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t09_set_broker_callback_registers_callback(
        self, clean_env, tmp_kill_switch_log
    ):
        """T09: set_broker_callback 后 _broker_callback 不为 None."""
        ks = KillSwitch()
        simulator = BrokerCallbackSimulator(dry_run=True)
        ks.set_broker_callback(simulator)
        assert ks._broker_callback is not None
        assert ks._broker_callback is simulator

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t09_l2_trigger_calls_callback_with_level_2(
        self, clean_env, tmp_kill_switch_log
    ):
        """T09: L2 触发时 callback 被调用, 且 level=2."""
        ks = KillSwitch()
        simulator = BrokerCallbackSimulator(dry_run=True)
        ks.set_broker_callback(simulator)
        ks.config = {
            "level_2": {
                "name": "L2",
                "action": ["force_close_deep_otm_short", "release_liquidity"],
                "auto_execute": True,
            }
        }
        result = ks.execute_kill_switch(level=2)
        assert len(simulator.calls) == 1
        assert simulator.calls[0]["level"] == 2
        assert result["executed"] is True
        assert result["level"] == 2

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t09_l3_trigger_calls_callback_with_level_3(
        self, clean_env, tmp_kill_switch_log
    ):
        """T09: L3 触发时 callback 被调用, 且 level=3."""
        ks = KillSwitch()
        simulator = BrokerCallbackSimulator(dry_run=True)
        ks.set_broker_callback(simulator)
        ks.config = {
            "level_3": {
                "name": "L3",
                "action": ["liquidate_red_etf", "cross_asset_inject"],
                "auto_execute": True,
                "source_etfs": ["512890", "515180"],
            }
        }
        result = ks.execute_kill_switch(level=3)
        assert len(simulator.calls) == 1
        assert simulator.calls[0]["level"] == 3
        assert result["executed"] is True

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t09_l1_trigger_calls_callback_with_level_1(
        self, clean_env, tmp_kill_switch_log
    ):
        """T09: L1 触发时 callback 被调用, 且 level=1."""
        ks = KillSwitch()
        simulator = BrokerCallbackSimulator(dry_run=True)
        ks.set_broker_callback(simulator)
        ks.config = {
            "level_1": {
                "name": "L1",
                "action": ["disable_new_positions"],
                "auto_execute": True,
            }
        }
        result = ks.execute_kill_switch(level=1)
        assert len(simulator.calls) == 1
        assert simulator.calls[0]["level"] == 1
        assert result["executed"] is True


class TestT09RealForceClose:
    """T09: L2/L3 触发时真实调用 broker.place 执行平仓."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t09_l2_live_mode_calls_broker_place_buy_to_close(
        self, clean_env, tmp_kill_switch_log
    ):
        """T09: L2 实盘模式下, broker.place 被调用, side=BUY_TO_CLOSE."""
        positions = {
            "10002568.SH": {"side": "SELL", "quantity": 5, "type": "OPTION"},
            "10002569.SH": {"side": "SELL", "quantity": 3, "type": "OPTION"},
            "510300.SH": {"side": "BUY", "quantity": 10000, "type": "ETF"},
        }
        simulator = BrokerCallbackSimulator(
            dry_run=False, broker_connected=True, positions=positions
        )
        ks = KillSwitch()
        ks.set_broker_callback(simulator)
        ks.config = {
            "level_2": {"name": "L2", "action": ["force_close"], "auto_execute": True}
        }
        result = ks.execute_kill_switch(level=2)

        assert len(simulator.calls) == 1
        assert simulator.calls[0]["level"] == 2
        buy_to_close_calls = [
            c for c in simulator.broker_place_calls if c["side"] == "BUY_TO_CLOSE"
        ]
        assert len(buy_to_close_calls) == 2
        sell_calls = [c for c in simulator.broker_place_calls if c["side"] == "SELL"]
        assert len(sell_calls) == 0
        assert result["executed"] is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t09_l3_live_mode_calls_broker_place_sell_for_red_etf(
        self, clean_env, tmp_kill_switch_log
    ):
        """T09: L3 实盘模式下, 红利ETF被 SELL 10%."""
        positions = {
            "512890.SH": {"actual_shares": 10000, "side": "BUY"},
            "515180.SH": {"actual_shares": 5000, "side": "BUY"},
            "510300.SH": {"actual_shares": 8000, "side": "BUY"},
        }
        simulator = BrokerCallbackSimulator(
            dry_run=False, broker_connected=True, positions=positions
        )
        ks = KillSwitch()
        ks.set_broker_callback(simulator)
        ks.config = {
            "level_3": {
                "name": "L3",
                "action": ["liquidate"],
                "auto_execute": True,
                "source_etfs": ["512890", "515180"],
            }
        }
        result = ks.execute_kill_switch(level=3)

        sell_calls = [c for c in simulator.broker_place_calls if c["side"] == "SELL"]
        assert len(sell_calls) == 2
        sell_512890 = next(c for c in sell_calls if "512890" in c["symbol"])
        assert sell_512890["quantity"] == 1000
        sell_515180 = next(c for c in sell_calls if "515180" in c["symbol"])
        assert sell_515180["quantity"] == 500
        sell_510300 = [c for c in sell_calls if "510300" in c["symbol"]]
        assert len(sell_510300) == 0
        assert result["executed"] is True

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t09_l3_red_etf_quantity_rounded_to_100_shares(
        self, clean_env, tmp_kill_switch_log
    ):
        """T09: L3 红利ETF减仓数量向下取整到 100 股."""
        positions = {"512890.SH": {"actual_shares": 850, "side": "BUY"}}
        simulator = BrokerCallbackSimulator(
            dry_run=False, broker_connected=True, positions=positions
        )
        ks = KillSwitch()
        ks.set_broker_callback(simulator)
        ks.config = {
            "level_3": {
                "name": "L3",
                "action": ["liquidate"],
                "source_etfs": ["512890"],
            }
        }
        result = ks.execute_kill_switch(level=3)

        sell_calls = [c for c in simulator.broker_place_calls if c["side"] == "SELL"]
        assert len(sell_calls) == 0
        assert result["executed"] is False


class TestT09DryRunMode:
    """T09: dry_run 模式下, 仅记录日志不真实下单."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t09_dry_run_l2_no_real_broker_place(self, clean_env, tmp_kill_switch_log):
        """T09: dry_run=True 时, L2 触发不调用 broker.place."""
        simulator = BrokerCallbackSimulator(
            dry_run=True,
            broker_connected=True,
            positions={"10002568.SH": {"side": "SELL", "quantity": 5}},
        )
        ks = KillSwitch()
        ks.set_broker_callback(simulator)
        ks.config = {"level_2": {"name": "L2", "action": ["force_close"]}}
        result = ks.execute_kill_switch(level=2)

        assert len(simulator.broker_place_calls) == 0
        assert len(simulator.calls) == 1
        assert result["executed"] is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t09_dry_run_l3_no_real_broker_place(self, clean_env, tmp_kill_switch_log):
        """T09: dry_run=True 时, L3 触发不调用 broker.place."""
        simulator = BrokerCallbackSimulator(
            dry_run=True,
            broker_connected=True,
            positions={"512890.SH": {"actual_shares": 10000}},
        )
        ks = KillSwitch()
        ks.set_broker_callback(simulator)
        ks.config = {
            "level_3": {
                "name": "L3",
                "action": ["liquidate"],
                "source_etfs": ["512890"],
            }
        }
        result = ks.execute_kill_switch(level=3)

        assert len(simulator.broker_place_calls) == 0
        assert result["executed"] is True


class TestT09BrokerDisconnected:
    """T09: broker 未连接时, 返回 executed=False."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t09_l2_broker_disconnected_returns_executed_false(
        self, clean_env, tmp_kill_switch_log
    ):
        """T09: L2 broker 未连接时, executed=False."""
        simulator = BrokerCallbackSimulator(
            dry_run=False, broker_connected=False, positions={}
        )
        ks = KillSwitch()
        ks.set_broker_callback(simulator)
        ks.config = {"level_2": {"name": "L2", "action": ["force_close"]}}
        result = ks.execute_kill_switch(level=2)

        assert len(simulator.calls) == 1
        assert result["executed"] is False
        actions_str = str(result.get("actions_taken", []))
        assert (
            "broker_callback_executed" in actions_str or "failed" in actions_str.lower()
        )

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t09_l3_broker_disconnected_returns_executed_false(
        self, clean_env, tmp_kill_switch_log
    ):
        """T09: L3 broker 未连接时, executed=False."""
        simulator = BrokerCallbackSimulator(
            dry_run=False, broker_connected=False, positions={}
        )
        ks = KillSwitch()
        ks.set_broker_callback(simulator)
        ks.config = {
            "level_3": {
                "name": "L3",
                "action": ["liquidate"],
                "source_etfs": ["512890"],
            }
        }
        result = ks.execute_kill_switch(level=3)
        assert result["executed"] is False


class TestT09CallbackException:
    """T09: callback 抛异常时, 返回 executed=False."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t09_callback_raises_exception_returns_executed_false(
        self, clean_env, tmp_kill_switch_log
    ):
        """T09: callback 抛 ConnectionError 时, executed=False."""
        ks = KillSwitch()
        failing_callback = MagicMock(side_effect=ConnectionError("broker offline"))
        ks.set_broker_callback(failing_callback)
        ks.config = {"level_2": {"name": "L2", "action": ["force_close"]}}
        result = ks.execute_kill_switch(level=2)

        assert result["executed"] is False
        assert "broker_callback_failed" in str(result.get("actions_taken", []))
        assert "broker offline" in str(result.get("error", ""))
        assert "熔断协议未真正执行" in result.get("critical_note", "")

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t09_callback_timeout_returns_executed_false(
        self, clean_env, tmp_kill_switch_log
    ):
        """T09: callback 抛 TimeoutError 时, executed=False."""
        ks = KillSwitch()
        timeout_callback = MagicMock(side_effect=TimeoutError("broker timeout 60s"))
        ks.set_broker_callback(timeout_callback)
        ks.config = {
            "level_3": {
                "name": "L3",
                "action": ["liquidate"],
                "source_etfs": ["512890"],
            }
        }
        result = ks.execute_kill_switch(level=3)
        assert result["executed"] is False
        assert "broker_callback_failed" in str(result.get("actions_taken", []))


class TestT09EndToEndFlow:
    """T09: 端到端验证 — KillSwitch 触发 → callback → broker.place."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t09_full_flow_l2_trigger_to_broker_place(
        self, clean_env, tmp_kill_switch_log
    ):
        """T09 端到端: L2 → callback → broker.place(BUY_TO_CLOSE) → executed=True."""
        positions = {"10002568.SH": {"side": "SELL", "quantity": 5, "type": "OPTION"}}
        simulator = BrokerCallbackSimulator(
            dry_run=False, broker_connected=True, positions=positions
        )
        ks = KillSwitch()
        ks.set_broker_callback(simulator)
        ks.config = {
            "level_2": {"name": "L2", "action": ["force_close"], "auto_execute": True}
        }
        result = ks.execute_kill_switch(level=2)

        assert len(simulator.calls) == 1
        assert simulator.calls[0]["level"] == 2
        assert len(simulator.broker_place_calls) == 1
        assert simulator.broker_place_calls[0]["side"] == "BUY_TO_CLOSE"
        assert simulator.broker_place_calls[0]["quantity"] == 5
        assert result["executed"] is True
        assert result["level"] == 2

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t09_full_flow_l3_trigger_to_broker_place(
        self, clean_env, tmp_kill_switch_log
    ):
        """T09 端到端: L3 → callback → broker.place(SELL) → executed=True."""
        positions = {
            "512890.SH": {"actual_shares": 10000, "side": "BUY"},
            "515180.SH": {"actual_shares": 10000, "side": "BUY"},
        }
        simulator = BrokerCallbackSimulator(
            dry_run=False, broker_connected=True, positions=positions
        )
        ks = KillSwitch()
        ks.set_broker_callback(simulator)
        ks.config = {
            "level_3": {
                "name": "L3",
                "action": ["liquidate"],
                "auto_execute": True,
                "source_etfs": ["512890", "515180"],
            }
        }
        result = ks.execute_kill_switch(level=3)

        assert len(simulator.calls) == 1
        assert simulator.calls[0]["level"] == 3
        sell_calls = [c for c in simulator.broker_place_calls if c["side"] == "SELL"]
        assert len(sell_calls) == 2
        for call in sell_calls:
            assert call["quantity"] == 1000
        assert result["executed"] is True

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t09_full_flow_l1_only_disables_new_positions(
        self, clean_env, tmp_kill_switch_log
    ):
        """T09: L1 触发只切断开仓权限, 不调用 broker.place."""
        simulator = BrokerCallbackSimulator(
            dry_run=False, broker_connected=True, positions={}
        )
        ks = KillSwitch()
        ks.set_broker_callback(simulator)
        ks.config = {"level_1": {"name": "L1", "action": ["disable_new_positions"]}}
        result = ks.execute_kill_switch(level=1)

        assert len(simulator.broker_place_calls) == 0
        assert len(simulator.calls) == 1
        assert result["executed"] is True


class TestT09RepeatedTrigger:
    """T09: 重复触发 L2/L3 时, callback 每次都被调用."""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t09_repeated_l2_triggers_callback_each_time(
        self, clean_env, tmp_kill_switch_log
    ):
        """T09: 连续触发 3 次 L2, callback 被调用 3 次."""
        simulator = BrokerCallbackSimulator(dry_run=True)
        ks = KillSwitch()
        ks.set_broker_callback(simulator)
        ks.config = {"level_2": {"name": "L2", "action": ["force_close"]}}

        for _ in range(3):
            ks.execute_kill_switch(level=2)

        assert len(simulator.calls) == 3
        for call in simulator.calls:
            assert call["level"] == 2

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t09_mixed_l2_l3_triggers(self, clean_env, tmp_kill_switch_log):
        """T09: 混合触发 L2 → L3 → L2, callback 每次都收到正确 level."""
        simulator = BrokerCallbackSimulator(dry_run=True)
        ks = KillSwitch()
        ks.set_broker_callback(simulator)
        ks.config = {
            "level_2": {"name": "L2", "action": ["force_close"]},
            "level_3": {
                "name": "L3",
                "action": ["liquidate"],
                "source_etfs": ["512890"],
            },
        }

        ks.execute_kill_switch(level=2)
        ks.execute_kill_switch(level=3)
        ks.execute_kill_switch(level=2)

        assert len(simulator.calls) == 3
        assert simulator.calls[0]["level"] == 2
        assert simulator.calls[1]["level"] == 3
        assert simulator.calls[2]["level"] == 2
