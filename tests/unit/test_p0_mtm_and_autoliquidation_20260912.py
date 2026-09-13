"""P0 修复批次单元测试 (2026-09-12)

覆盖三项 P0 修复的新增逻辑:
  1. P0-MTM 盯市价解析 (utils/execution/mark_to_market.py):
     实时价优先 / est_price 回退 / 离线跳过 / 覆盖率统计;
  2. 盯市接入止损检查 (executor/stop_loss_check.py): 盯市价触发止损, est_price 不会;
  3. S-1 口径 2/3 平仓执行闭环 (executor/stop_loss_liquidation.py):
     execute_liquidation_instructions 幂等/重试/升级 + build_unresolved_block_result。
"""

from __future__ import annotations

from executor.stop_loss_check import run_stop_loss_check
from executor.stop_loss_liquidation import (
    build_unresolved_block_result,
    execute_liquidation_instructions,
)
from utils.execution.mark_to_market import (
    fetch_mark_price_map,
    resolve_position_mark_price,
    summarize_mark_coverage,
)


# ============================================================
# 1. mark_to_market
# ============================================================
class TestResolvePositionMarkPrice:
    def test_realtime_price_takes_priority(self):
        item = {"est_price": 10.0}
        marks = {"600000": {"price": 8.8, "source": "eastmoney"}}
        price, source = resolve_position_mark_price("600000.SH", item, marks)
        assert price == 8.8
        assert source == "realtime"

    def test_fallback_to_est_price_when_mark_missing(self):
        item = {"est_price": 10.0}
        price, source = resolve_position_mark_price("600000.SH", item, {})
        assert price == 10.0
        assert source == "est_price"

    def test_unavailable_when_both_missing(self):
        price, source = resolve_position_mark_price("600000.SH", {}, None)
        assert price == 0.0
        assert source == "unavailable"

    def test_non_positive_mark_falls_back(self):
        item = {"est_price": 10.0}
        marks = {"600000": {"price": 0, "source": "eastmoney"}}
        price, source = resolve_position_mark_price("600000", item, marks)
        assert price == 10.0
        assert source == "est_price"


class TestFetchMarkPriceMap:
    def test_offline_mode_returns_empty_without_network(self, monkeypatch):
        monkeypatch.setenv("QUANT_OFFLINE", "1")
        assert fetch_mark_price_map(["600000", "000001.SZ"]) == {}

    def test_code_normalization_and_bad_quotes_filtered(self, monkeypatch):
        # conftest 为单元测试默认设 QUANT_OFFLINE=1, 此处显式关闭以测行情解析路径
        import utils.runtime_mode as rm

        monkeypatch.setattr(rm, "is_offline", lambda: False)
        import utils.astock_realtime as rt

        def _fake_quotes(codes, use_cache=True):
            return {
                "600000": {"price": 8.8, "source": "eastmoney"},
                "000001": {"price": 0, "source": "eastmoney"},  # 无效价 → 过滤
            }

        monkeypatch.setattr(rt, "get_realtime_quotes", _fake_quotes)
        marks = fetch_mark_price_map(["600000.SH", "000001.SZ"])
        assert set(marks.keys()) == {"600000"}
        assert marks["600000"]["price"] == 8.8

    def test_quotes_exception_returns_empty(self, monkeypatch):
        import utils.runtime_mode as rm

        monkeypatch.setattr(rm, "is_offline", lambda: False)
        import utils.astock_realtime as rt

        def _boom(codes, use_cache=True):
            raise OSError("network down")

        monkeypatch.setattr(rt, "get_realtime_quotes", _boom)
        assert fetch_mark_price_map(["600000"]) == {}

    def test_coverage_summary(self):
        marks = {"600000": {"price": 8.8, "source": "eastmoney"}}
        rt, total = summarize_mark_coverage(marks, ["600000.SH", "000001.SZ"])
        assert rt == 1
        assert total == 2


# ============================================================
# 2. 盯市接入止损检查
# ============================================================
class _FakeSLManager:
    """最小 StopLossManager 替身: 8% 止损 / 15% 止盈"""

    def __init__(self):
        self.stop_loss_orders = {}

    def set_stop_loss(self, code, avg_cost, qty):
        self.stop_loss_orders[code] = {"avg_cost": avg_cost, "qty": qty}

    def check_stop_loss(self, code, price):
        avg = self.stop_loss_orders[code]["avg_cost"]
        if price <= avg * 0.92:
            return "stop_loss", {"stop_price": round(avg * 0.92, 4)}
        if price >= avg * 1.15:
            return "take_profit", {"take_profit_price": round(avg * 1.15, 4)}
        return "none", None


class TestRunStopLossCheckWithMarkPrice:
    def _positions(self):
        return {
            "600000.SH": {
                "name": "浦发银行",
                "avg_cost": 10.0,
                "shares": 1000,
                "est_price": 10.0,  # 陈旧成交价 (账面无亏损)
            }
        }

    def test_mark_price_triggers_stop_loss_that_est_price_hides(self):
        # 核心场景: 真实市价已跌 12% (应触发 8% 止损), 但 est_price 停留在成本价
        marks = {"600000": {"price": 8.8, "source": "eastmoney"}}
        triggered = run_stop_loss_check(
            {"stop_loss_manager": _FakeSLManager()}, self._positions(), mark_prices=marks
        )
        assert len(triggered) == 1
        assert triggered[0]["action"] == "stop_loss"
        assert triggered[0]["current_price"] == 8.8
        assert triggered[0]["price_source"] == "realtime"

    def test_missing_mark_falls_back_to_est_price(self):
        triggered = run_stop_loss_check(
            {"stop_loss_manager": _FakeSLManager()}, self._positions(), mark_prices={}
        )
        assert triggered == []  # est_price=成本价 → 不触发 (旧行为, 回退不劣化)

    def test_manager_unavailable_marker_preserved(self):
        triggered = run_stop_loss_check({}, self._positions(), mark_prices=None)
        assert triggered[0]["code"] == "__manager_unavailable"


# ============================================================
# 3. S-1 口径 2/3 平仓执行闭环
# ============================================================
def _liq_instruction(qty=1000, ref=9.0):
    return {
        "full_code": "600000.SH",
        "code": "600000.SH",
        "name": "浦发银行",
        "action": "SELL",
        "qty": qty,
        "ref_price": ref,
        "estimated_amount": round(qty * ref, 2),
        "confirm": True,
        "reason": "stop_loss_auto_liquidate (S-1 口径 1~3)",
        "authorization": {
            "scope": "stop_loss_only",
            "reduce_only": True,
            "max_exec_retries": 2,
            "escalate_on_failure": True,
        },
    }


class TestExecuteLiquidationInstructions:
    def test_filled_records_key_and_acks_state_machine(self):
        executed_keys: set[str] = set()
        acked = []

        class _SLM:
            def acknowledge_stop_loss(self, code, note=""):
                acked.append(code)

        attempts, results = execute_liquidation_instructions(
            [_liq_instruction()],
            executor_fn=lambda inst: {"status": "FILLED", "qty": inst["qty"], "fill_amount": 0.0},
            executed_keys=executed_keys,
            sl_manager=_SLM(),
        )
        assert len(executed_keys) == 1
        assert attempts[0]["reason"] == "ok"
        assert len(results) == 1
        assert acked == ["600000"]

    def test_skipped_does_not_record_key_so_retry_possible(self):
        executed_keys: set[str] = set()
        attempts, results = execute_liquidation_instructions(
            [_liq_instruction()],
            executor_fn=lambda inst: {"status": "SKIPPED", "qty": 0, "fill_amount": 0.0},
            executed_keys=executed_keys,
            sl_manager=None,
        )
        assert executed_keys == set()  # 不落键 → 下轮可重试
        assert results == []  # SKIPPED 不进执行报告
        assert attempts[0]["reason"] != "ok"

    def test_executor_exception_counts_as_failure_not_raise(self):
        def _boom(inst):
            raise RuntimeError("boom")

        attempts, _ = execute_liquidation_instructions(
            [_liq_instruction()],
            executor_fn=_boom,
            executed_keys=set(),
            sl_manager=None,
        )
        assert attempts[0]["reason"] != "ok"

    def test_already_executed_key_skips(self):
        executed_keys: set[str] = set()
        # 先构造键: FILLED 一次
        execute_liquidation_instructions(
            [_liq_instruction()],
            executor_fn=lambda inst: {"status": "FILLED", "qty": inst["qty"], "fill_amount": 0.0},
            executed_keys=executed_keys,
            sl_manager=None,
        )
        calls = []

        def _spy(inst):
            calls.append(inst)
            return {"status": "FILLED", "qty": inst["qty"], "fill_amount": 0.0}

        attempts, results = execute_liquidation_instructions(
            [_liq_instruction()],
            executor_fn=_spy,
            executed_keys=executed_keys,
            sl_manager=None,
        )
        assert calls == []  # 幂等跳过, 不再执行
        assert attempts == [] and results == []


class TestBuildUnresolvedBlockResult:
    def test_all_ok_returns_none(self):
        assert build_unresolved_block_result([{"code": "600000.SH", "reason": "ok"}]) is None
        assert build_unresolved_block_result([]) is None

    def test_unresolved_builds_blocked_result(self):
        result = build_unresolved_block_result(
            [{"code": "600000.SH", "reason": "ok"}, {"code": "000001.SZ", "reason": "平仓单未成交", "escalated": True}]
        )
        assert result is not None
        assert result["status"] == "blocked"
        assert "1 笔平仓未完成" in result["reason"]
        assert "000001.SZ" in result["reason"]
        assert len(result["auto_liquidate_attempts"]) == 2
