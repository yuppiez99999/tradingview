"""P0-HIGH 批修复单元测试 (2026-09-13)

覆盖:
  1. T+1 可卖约束 (utils/execution/t1_constraint.py) — 冻结计算/截断/执行器最终闸;
  2. 止损平仓 T+1 截断 (executor/stop_loss_liquidation._build_one_instruction);
  3. 再平衡 validate_order T+1 拒单;
  4. 价格保护带消费 (executor/premarket.check_price_band_violation);
  5. 信号降级显式标记 (adjust_allocation_by_signal);
  6. hedge 状态回写锁内重读 (_mutate_hedge_state 纯函数 + _update_positions_state 锁语义)。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import utils.execution.t1_constraint as t1  # noqa: E402
from executor.premarket import adjust_allocation_by_signal, check_price_band_violation  # noqa: E402
from executor.stop_loss_liquidation import _build_one_instruction  # noqa: E402
from utils.execution.rebalance_execution_orders import validate_order  # noqa: E402


# ============================================================
# 1. T+1 约束
# ============================================================
class TestT1Constraint:
    def test_frozen_sums_today_buys_only(self, monkeypatch):
        class _FakeStore:
            def load_day(self, date, strategies=None):
                assert date == "2026-09-13"
                return [
                    {"symbol": "600000", "side": "BUY", "filled_qty": 500},
                    {"symbol": "600000", "side": "SELL", "filled_qty": 300},  # 卖出不计
                    {"symbol": "600519", "side": "BUY", "filled_qty": 100},  # 其他标的
                    {"symbol": "600000.SH", "side": "BUY", "filled_qty": 200},  # 后缀归一
                ]

        import utils.execution.fills_store as fs

        monkeypatch.setattr(fs.FillsStore, "load_day", lambda self, date=None, strategies=None: _FakeStore().load_day(date))
        frozen = t1.get_t1_frozen_qty("600000.SH", "2026-09-13")
        assert frozen == 700

    def test_frozen_fail_open_zero_on_error(self, monkeypatch):
        import utils.execution.fills_store as fs

        def _boom(self, date=None, strategies=None):
            raise OSError("store down")

        monkeypatch.setattr(fs.FillsStore, "load_day", _boom)
        assert t1.get_t1_frozen_qty("600000.SH") == 0

    def test_clamp_sell_quantity(self, monkeypatch):
        monkeypatch.setattr(t1, "get_t1_frozen_qty", lambda s, d=None: 550)
        # 持仓 2000, 冻结 550 → 可卖 1450; 请求 2000 → 截断 1450 (整手)
        clamped, available, frozen = t1.clamp_sell_quantity("600000", 2000, 2000)
        assert (clamped, available, frozen) == (1400, 1450, 550)

    def test_clamp_instruction_final_gate(self, monkeypatch):
        monkeypatch.setattr(t1, "get_t1_frozen_qty", lambda s, d=None: 1000)
        inst = {"full_code": "600000.SH", "qty": 1000}
        positions = {"600000.SH": {"shares": 1500}}
        clamped, note = t1.clamp_instruction_sell_qty(inst, positions)
        assert clamped == 500 and note is not None  # 当日买入全冻结

    def test_clamp_instruction_ok_no_note(self, monkeypatch):
        monkeypatch.setattr(t1, "get_t1_frozen_qty", lambda s, d=None: 0)
        inst = {"full_code": "600000.SH", "qty": 1000}
        positions = {"600000.SH": {"shares": 1500}}
        clamped, note = t1.clamp_instruction_sell_qty(inst, positions)
        assert clamped == 1000 and note is None

    def test_clamp_instruction_fail_open_on_error(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(t1, "clamp_sell_quantity", _boom)
        inst = {"full_code": "600000.SH", "qty": 1000}
        clamped, note = t1.clamp_instruction_sell_qty(inst, {})
        assert clamped == 1000 and "fail-open" in note


# ============================================================
# 2. 止损平仓 T+1 截断
# ============================================================
_CFG = {
    "enabled": True,
    "scope": "stop_loss_only",
    "reduce_only": True,
    "max_liquidations_per_symbol_per_day": 1,
    "max_exec_retries": 1,
}


class TestAutoLiquidateT1:
    def _trigger(self):
        return {"code": "600000.SH", "name": "浦发银行", "action": "stop_loss", "current_price": 9.0}

    def test_liquidation_clamped_by_t1(self, monkeypatch):
        monkeypatch.setattr(t1, "get_t1_frozen_qty", lambda s, d=None: 1000)
        positions = {"600000.SH": {"shares": 2000}}
        inst, skip = _build_one_instruction(self._trigger(), positions, _CFG, {}, 1)
        # 冻结 1000 → 可卖 1000 → 整手 1000 (原为 2000)
        assert inst is not None and inst["qty"] == 1000

    def test_liquidation_all_frozen_skipped_with_reason(self, monkeypatch):
        monkeypatch.setattr(t1, "get_t1_frozen_qty", lambda s, d=None: 2000)
        positions = {"600000.SH": {"shares": 2000}}
        inst, skip = _build_one_instruction(self._trigger(), positions, _CFG, {}, 1)
        assert inst is None
        assert "T+1" in skip and "冻结" in skip

    def test_liquidation_unfrozen_unchanged(self, monkeypatch):
        monkeypatch.setattr(t1, "get_t1_frozen_qty", lambda s, d=None: 0)
        positions = {"600000.SH": {"shares": 2000}}
        inst, _skip = _build_one_instruction(self._trigger(), positions, _CFG, {}, 1)
        assert inst is not None and inst["qty"] == 2000


# ============================================================
# 3. 再平衡 validate_order T+1
# ============================================================
class TestValidateOrderT1:
    def test_sell_exceeding_available_rejected(self, monkeypatch):
        monkeypatch.setattr(t1, "get_t1_frozen_qty", lambda s, d=None: 800)
        # 持仓 2000, 冻结 800 → 可卖 1200; 请求 1300 → 拒单
        result = validate_order("600000", "SELL", 1300, 10.0, {"600000": 2000})
        assert result["valid"] is False
        assert any("T+1" in e for e in result["errors"])

    def test_sell_within_available_passes(self, monkeypatch):
        monkeypatch.setattr(t1, "get_t1_frozen_qty", lambda s, d=None: 800)
        result = validate_order("600000", "SELL", 1200, 10.0, {"600000": 2000})
        assert result["valid"] is True

    def test_buy_not_t1_checked(self, monkeypatch):
        monkeypatch.setattr(t1, "get_t1_frozen_qty", lambda s, d=None: 2000)
        result = validate_order("600000", "BUY", 1000, 10.0, {"600000": 0})
        assert not any("T+1" in e for e in result["errors"])


# ============================================================
# 4. 价格保护带消费
# ============================================================
class TestPriceBandConsumption:
    def test_buy_above_band_skipped(self):
        inst = {"full_code": "600000.SH", "action": "BUY", "max_buy_price": 10.30, "min_buy_price": 9.70}
        result = check_price_band_violation(inst, False, 10.31, {"built_amounts": {}})
        assert result is not None and result["status"] == "SKIPPED"
        assert result["reason"] == "exec_price_above_buy_band"

    def test_sell_below_band_skipped(self):
        inst = {"full_code": "600000.SH", "action": "SELL", "max_buy_price": 10.30, "min_buy_price": 9.70}
        result = check_price_band_violation(inst, True, 9.69, None)
        assert result is not None and result["reason"] == "exec_price_below_sell_band"

    def test_within_band_passes(self):
        inst = {"full_code": "600000.SH", "action": "BUY", "max_buy_price": 10.30, "min_buy_price": 9.70}
        assert check_price_band_violation(inst, False, 10.00, None) is None

    def test_no_band_fields_passes(self):
        assert check_price_band_violation({"full_code": "600000.SH"}, False, 99.0, None) is None

    def test_bad_band_fields_fail_open(self):
        inst = {"full_code": "600000.SH", "max_buy_price": "not-a-number"}
        assert check_price_band_violation(inst, False, 99.0, None) is None


# ============================================================
# 5. 信号降级标记
# ============================================================
class TestSignalDegraded:
    def test_no_data_marked_degraded(self):
        allocated, tag = adjust_allocation_by_signal(
            100000, {"direction": "NEUTRAL", "confidence": 0.0, "method": "no_data"}, 200000
        )
        assert (allocated, tag) == (100000, "signal_degraded")

    def test_error_marked_degraded(self):
        _allocated, tag = adjust_allocation_by_signal(
            100000, {"direction": "UP", "confidence": 0.9, "signal_strength": 0.8, "method": "error"}, 200000
        )
        assert tag == "signal_degraded"

    def test_real_signal_still_sized(self):
        allocated, tag = adjust_allocation_by_signal(
            100000, {"direction": "UP", "confidence": 0.8, "signal_strength": 0.6, "method": "lstm"}, 200000
        )
        # strong_buy = base×1.3 但受单标的上限 daily_budget×0.30 = 60000 约束
        assert tag == "strong_buy" and allocated == 60000


# ============================================================
# 6. hedge 状态回写
# ============================================================
class TestHedgeMutateState:
    def test_mutate_marks_filled(self):
        from hedge_order_executor import _mutate_hedge_state

        positions_data = {
            "positions": {"600000.SH": {"shares": 100}},
            "hedge_positions": {
                "active_orders": {
                    "put_protection": [{"order_id": "P1", "instrument": "IO2512-P-3900", "direction": "BUY_PUT", "status": "PENDING"}]
                }
            },
        }
        fills = [
            {
                "status": "FILLED",
                "order_id": "P1",
                "instrument": "IO2512-P-3900",
                "direction": "BUY_PUT",
                "contracts": 1,
                "premium_total": 12000.0,
            }
        ]
        new_data = _mutate_hedge_state(positions_data, fills, "2026-09-13")
        assert new_data["hedge_positions"]["active_orders"]["put_protection"][0]["status"] == "FILLED"
        # 不可变性: 输入未被修改
        assert positions_data["hedge_positions"]["active_orders"]["put_protection"][0]["status"] == "PENDING"
        assert new_data["hedge_positions"]["last_hedge_execution"]["date"] == "2026-09-13"
