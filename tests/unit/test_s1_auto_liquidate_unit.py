"""S-1 止损自动平仓三授权口径回归 (Issue #13, 2026-09-12)

覆盖三个授权口径与安全不变量:

口径 1 · 授权范围 —— 仅 stop_loss / 仅已持仓 / 仅减仓不反手 / 单标的单日限次
口径 2 · 与 auto_10 风险预算 —— 豁免必须可审计 (写入 exempted_from)
口径 3 · 失败处置 —— 重试上限 + 达上限升级人工 + 保持阻断

安全不变量: 默认关闭零指令 / scope 漂移 fail-closed / 不足一手不生成 0 股单。
"""

from __future__ import annotations

import pytest

from executor.stop_loss_liquidation import (
    build_liquidation_instructions,
    is_auto_liquidate_enabled,
    record_exec_attempt,
    validate_authorization,
)
from utils.risk_thresholds import get_stop_loss_auto_liquidate_config

pytestmark = pytest.mark.unit


def _cfg(**overrides) -> dict:
    cfg = dict(get_stop_loss_auto_liquidate_config())
    cfg.update(overrides)
    return cfg


_POSITIONS = {
    "600519": {
        "avg_cost": 100.0,
        "total_shares": 500,
        "est_price": 90.0,
        "name": "贵州茅台",
    }
}
_TRIGGERED = [
    {
        "code": "600519",
        "name": "贵州茅台",
        "action": "stop_loss",
        "current_price": 90.0,
        "pnl_pct": -0.10,
    }
]


class TestDefaultOff:
    """默认未授权 —— 不产生任何自动下单路径。"""

    def test_config_defaults_to_disabled(self):
        assert get_stop_loss_auto_liquidate_config()["enabled"] is False

    def test_disabled_yields_no_instructions(self):
        instructions, skipped = build_liquidation_instructions(
            _TRIGGERED, _POSITIONS, _cfg(enabled=False)
        )
        assert instructions == []
        assert skipped[0]["reason"].startswith("auto_liquidate 未授权")
        assert is_auto_liquidate_enabled(_cfg()) is False


class TestAuthorizationScope:
    """口径 1 · 授权范围。"""

    def test_stop_loss_generates_reduce_only_sell(self):
        instructions, skipped = build_liquidation_instructions(
            _TRIGGERED, _POSITIONS, _cfg(enabled=True)
        )
        assert not skipped
        assert len(instructions) == 1
        inst = instructions[0]
        assert inst["full_code"] == "600519"
        assert inst["action"] == "SELL"
        # 仅减仓不反手: 数量 <= 持仓数量
        assert inst["qty"] == 500

    def test_take_profit_not_liquidated(self):
        triggered = [{**_TRIGGERED[0], "action": "take_profit"}]
        instructions, skipped = build_liquidation_instructions(
            triggered, _POSITIONS, _cfg(enabled=True)
        )
        assert instructions == []
        assert "授权范围外" in skipped[0]["reason"]

    def test_take_profit_requires_explicit_allow(self):
        triggered = [{**_TRIGGERED[0], "action": "take_profit"}]
        instructions, _ = build_liquidation_instructions(
            triggered, _POSITIONS, _cfg(enabled=True, allow_take_profit=True)
        )
        assert len(instructions) == 1

    def test_held_positions_only_skips_unknown_code(self):
        triggered = [{**_TRIGGERED[0], "code": "000001"}]
        instructions, skipped = build_liquidation_instructions(
            triggered, _POSITIONS, _cfg(enabled=True)
        )
        assert instructions == []
        assert "无持仓记录" in skipped[0]["reason"]

    def test_reduce_only_never_exceeds_holding(self):
        cfg = _cfg(enabled=True)
        instructions, _ = build_liquidation_instructions(_TRIGGERED, _POSITIONS, cfg)
        assert instructions[0]["qty"] <= 500

    def test_per_symbol_daily_limit(self):
        triggered = [{**_TRIGGERED[0], "liquidations_today": {"600519": 1}}]
        instructions, skipped = build_liquidation_instructions(
            triggered, _POSITIONS, _cfg(enabled=True)
        )
        assert instructions == []
        assert "单日平仓次数已达上限" in skipped[0]["reason"]

    def test_insufficient_lot_records_skipped_not_zero_order(self):
        positions = {"600519": {**_POSITIONS["600519"], "total_shares": 50}}
        instructions, skipped = build_liquidation_instructions(
            _TRIGGERED, positions, _cfg(enabled=True)
        )
        assert instructions == []
        assert "不足一手" in skipped[0]["reason"]


class TestFailClosed:
    """授权口径非法 → 拒绝生成平仓单 (fail-closed), 不静默放行。"""

    def test_scope_drift_rejected(self):
        ok, reason = validate_authorization(_cfg(enabled=True, scope="all"))
        assert ok is False
        assert "scope" in reason

    def test_scope_drift_yields_no_instructions(self):
        instructions, skipped = build_liquidation_instructions(
            _TRIGGERED, _POSITIONS, _cfg(enabled=True, scope="all")
        )
        assert instructions == []
        assert "scope" in skipped[0]["reason"]

    def test_negative_retries_rejected(self):
        ok, reason = validate_authorization(_cfg(enabled=True, max_exec_retries=-1))
        assert ok is False
        assert "max_exec_retries" in reason

    def test_reduce_only_disabled_rejected(self):
        ok, reason = validate_authorization(_cfg(enabled=True, reduce_only=False))
        assert ok is False
        assert "reduce_only" in reason

    def test_missing_price_rejected(self):
        triggered = [{**_TRIGGERED[0], "current_price": 0}]
        instructions, skipped = build_liquidation_instructions(
            triggered, _POSITIONS, _cfg(enabled=True)
        )
        assert instructions == []
        assert "触发价不可用" in skipped[0]["reason"]


class TestBudgetExemption:
    """口径 2 · 与 auto_10 风险预算的关系 —— 豁免必须可审计。"""

    def test_exemptions_recorded_in_authorization_block(self):
        instructions, _ = build_liquidation_instructions(
            _TRIGGERED, _POSITIONS, _cfg(enabled=True)
        )
        auth = instructions[0]["authorization"]
        assert "daily_quota" in auth["exempted_from"]
        assert "single_trade_limit" in auth["exempted_from"]

    def test_no_exemption_when_disabled_in_config(self):
        cfg = _cfg(
            enabled=True,
            exempt_from_daily_quota=False,
            exempt_from_single_trade_limit=False,
        )
        instructions, _ = build_liquidation_instructions(_TRIGGERED, _POSITIONS, cfg)
        assert instructions[0]["authorization"]["exempted_from"] == []

    def test_authorization_block_is_auditable(self):
        instructions, _ = build_liquidation_instructions(
            _TRIGGERED, _POSITIONS, _cfg(enabled=True)
        )
        auth = instructions[0]["authorization"]
        assert auth["scope"] == "stop_loss_only"
        assert auth["reduce_only"] is True
        assert auth["trigger_action"] == "stop_loss"
        assert auth["per_symbol_daily_limit"] == 1
        assert auth["authorized_by"].endswith("auto_liquidate")


class TestFailureHandling:
    """口径 3 · 失败处置 —— 重试上限 + 升级人工。"""

    def test_retry_then_escalate_at_limit(self):
        inst, _ = build_liquidation_instructions(_TRIGGERED, _POSITIONS, _cfg(enabled=True))
        state: dict = {}
        r1 = record_exec_attempt(inst[0], {"status": "FAILED", "fill_qty": 0}, state)
        assert r1["escalated"] is False and r1["retries_left"] == 1
        r2 = record_exec_attempt(inst[0], {"status": "FAILED", "fill_qty": 0}, state)
        assert r2["escalated"] is True and r2["retries_left"] == 0

    def test_partial_fill_not_treated_as_success(self):
        inst, _ = build_liquidation_instructions(_TRIGGERED, _POSITIONS, _cfg(enabled=True))
        state: dict = {}
        r = record_exec_attempt(inst[0], {"status": "PARTIAL", "fill_qty": 200}, state)
        assert r["escalated"] is False
        assert "部分成交" in r["reason"]

    def test_success_resets_nothing_and_reports_ok(self):
        inst, _ = build_liquidation_instructions(_TRIGGERED, _POSITIONS, _cfg(enabled=True))
        state: dict = {}
        r = record_exec_attempt(inst[0], {"status": "FILLED", "fill_qty": 500}, state)
        assert r["reason"] == "ok" and r["escalated"] is False

    def test_escalate_disabled_never_escalates(self):
        instructions, _ = build_liquidation_instructions(
            _TRIGGERED, _POSITIONS, _cfg(enabled=True, max_exec_retries=0, escalate_on_failure=False)
        )
        state: dict = {}
        r = record_exec_attempt(instructions[0], {"status": "FAILED", "fill_qty": 0}, state)
        assert r["escalated"] is False


class TestHostWiring:
    """主链挂载点: 未授权路径与迁出前逐字段一致; 授权路径生成平仓单。"""

    def test_blocked_shape_unchanged_when_disabled(self):
        from executor.stop_loss_liquidation import handle_stop_loss_events

        result = handle_stop_loss_events(
            _TRIGGERED, _POSITIONS, _cfg(enabled=False), block_on_trigger=True
        )
        assert result["status"] == "blocked"
        assert result["blocked_reason"] == "stop_loss_triggered (S-1)"
        assert result["stop_loss_alerts"] == _TRIGGERED
        assert "阻断执行" in result["reason"]

    def test_no_block_when_block_on_trigger_false(self):
        from executor.stop_loss_liquidation import handle_stop_loss_events

        assert (
            handle_stop_loss_events(
                _TRIGGERED, _POSITIONS, _cfg(enabled=False), block_on_trigger=False
            )
            is None
        )

    def test_no_event_when_nothing_triggered(self):
        from executor.stop_loss_liquidation import handle_stop_loss_events

        assert handle_stop_loss_events([], _POSITIONS, _cfg(enabled=True)) is None

    def test_authorized_path_returns_instructions(self):
        from executor.stop_loss_liquidation import handle_stop_loss_events

        result = handle_stop_loss_events(_TRIGGERED, _POSITIONS, _cfg(enabled=True))
        assert result["status"] == "auto_liquidate"
        assert len(result["instructions"]) == 1
        assert result["auto_liquidate"]["scope"] == "stop_loss_only"

    def test_authorized_but_all_skipped_still_blocks(self):
        """授权了但无可执行单 (如不足一手) —— 不得静默变"已处置"。"""
        from executor.stop_loss_liquidation import handle_stop_loss_events

        positions = {"600519": {**_POSITIONS["600519"], "total_shares": 50}}
        result = handle_stop_loss_events(_TRIGGERED, positions, _cfg(enabled=True))
        assert result["status"] == "blocked"
        assert result["auto_liquidate_skipped"]

    def test_executor_fn_success_acknowledges_and_does_not_block(self):
        from executor.stop_loss_liquidation import handle_stop_loss_events
        from utils.wt_risk_control import StopLossManager

        manager = StopLossManager(stop_loss_pct=0.08, take_profit_pct=0.15)
        manager.set_stop_loss("600519", 100.0, 500)
        # 真实顺序: _run_stop_loss_check 先把状态机推到 pending, 再进入本处置入口
        assert manager.check_stop_loss("600519", 90.0)[0] == "stop_loss"
        result = handle_stop_loss_events(
            _TRIGGERED,
            _POSITIONS,
            _cfg(enabled=True),
            sl_manager=manager,
            executor_fn=lambda inst: {"status": "FILLED", "fill_qty": inst["qty"]},
        )
        assert result["status"] == "auto_liquidate"
        assert result["attempts"][0]["reason"] == "ok"
        # 平仓成功 → 状态机转终态 (triggered_stop_loss), 不再重复告警
        assert manager.get_stop_loss_status()["600519"]["status"] == "triggered_stop_loss"
        assert manager.check_stop_loss("600519", 90.0)[0] == "none"
        # 且状态机保留确认轨迹 (可审计)
        assert manager.get_stop_loss_status()["600519"]["acknowledge_note"] == "auto_liquidate 成功"

    def test_executor_fn_failure_blocks_and_escalates(self):
        from executor.stop_loss_liquidation import handle_stop_loss_events

        result = handle_stop_loss_events(
            _TRIGGERED,
            _POSITIONS,
            _cfg(enabled=True, max_exec_retries=1),
            executor_fn=lambda inst: {"status": "REJECTED", "fill_qty": 0},
        )
        assert result["status"] == "blocked"
        assert result["blocked_reason"] == "auto_liquidate_failed (S-1 口径 3)"
        assert result["attempts"][0]["escalated"] is True

    def test_executor_fn_exception_counted_as_failure(self):
        from executor.stop_loss_liquidation import handle_stop_loss_events

        def _boom(_inst):
            raise RuntimeError("broker down")

        result = handle_stop_loss_events(
            _TRIGGERED, _POSITIONS, _cfg(enabled=True, max_exec_retries=1), executor_fn=_boom
        )
        assert result["status"] == "blocked"
        assert result["attempts"][0]["reason"] != "ok"
