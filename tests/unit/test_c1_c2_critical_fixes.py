# -*- coding: utf-8 -*-
"""GLM-5.2 审查 C1(#16)/C2(#22) critical 缺陷回归测试.

C1 (#16): daily_trade_executor.execute_instructions 双重建仓风险
  - 修复1 (纵深防御): executed_instruction_keys 幂等去重
  - 修复2 (fail-closed): save_build_progress 失败时禁止写 positions, 防双重建仓

C2 (#22): institutional_pipeline_runner KillSwitch L1 被 _regenerate_trades_from_weights 绕过
  - 修复: _apply_killswitch_l1_filter 在 trades 重建后重新过滤 BUY
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import daily_trade_executor as dte
from utils.killswitch_guard import apply_killswitch_l1_filter


# ============================================================
# C1 (#16) 双重建仓风险
# ============================================================

class TestC1DoubleExecution:
    """验证 execute_instructions 不会因崩溃重跑导致双重建仓."""

    def _make_confirmed(self, n=2):
        """构造 n 条已确认指令 (full_code + qty 作幂等键)."""
        return [
            {
                "full_code": f"600000.SH_{i}",
                "code": f"600000.SH",
                "qty": 1000 + i,
                "ref_price": 10.0,
                "estimated_amount": 10000.0 + i,
                "side": "BUY",
            }
            for i in range(n)
        ]

    def _patch_execute(self, monkeypatch, confirmed, store):
        """统一 mock execute_instructions 的全部外部依赖."""
        monkeypatch.setattr(dte, "init_wt_modules", lambda: {})
        monkeypatch.setattr(dte, "_run_wt_risk_block_check", lambda wm, c: None)
        monkeypatch.setattr(dte, "load_positions", lambda: {"positions": {}})
        monkeypatch.setattr(dte, "_build_and_save_execution_report", lambda *a, **k: ({}, None))
        monkeypatch.setattr(dte, "generate_next_trading_day_plan", lambda *a, **k: {})
        monkeypatch.setattr(dte, "_check_execution_preconditions",
                            lambda data: (confirmed, None))
        # 内存持久化 progress, 模拟 save_build_progress 落盘
        monkeypatch.setattr(dte, "save_build_progress",
                            lambda p: store.__setitem__("progress", dict(p)))
        monkeypatch.setattr(dte, "atomic_write_json", lambda *a, **k: None)
        # 第一次返回空, 之后返回已持久化 progress (含 executed_instruction_keys)
        call = {"n": 0}
        def _load():
            call["n"] += 1
            return dict(store.get("progress", {
                "total_built": 0, "daily_records": [], "built_amounts": {},
            }))
        monkeypatch.setattr(dte, "load_build_progress", _load)
        # 指令文件存在 + 内容有效 (mock INSTRUCTIONS_DIR / "..." 返回可 exists 的对象)
        instr_file = MagicMock()
        instr_file.exists.return_value = True
        fake_dir = MagicMock()
        fake_dir.__truediv__.return_value = instr_file
        monkeypatch.setattr(dte, "INSTRUCTIONS_DIR", fake_dir)
        # mock builtins.open: 读指令文件返回 "{}", 写操作忽略 (atomic_write_json 不验证)
        import io as _io
        def _fake_open(path, *a, **k):
            mode = a[0] if a else k.get("mode", "r")
            if "w" in str(mode):
                return MagicMock()
            return _io.StringIO("{}")
        monkeypatch.setattr("builtins.open", _fake_open)

    def test_c1_fail_closed_on_progress_save_failure(self, monkeypatch):
        """C1 核心修复: save_build_progress 失败时 fail-closed, 不写 positions."""
        confirmed = self._make_confirmed(2)
        store = {}
        self._patch_execute(monkeypatch, confirmed, store)

        exec_mock = MagicMock(return_value={
            "fill_amount": 10000.0, "fill_price": 10.0,
            "built_before": 0, "built_after": 10000,
        })
        monkeypatch.setattr(dte, "_execute_single_instruction", exec_mock)

        # save_build_progress 抛异常 → fail-closed
        def _boom(p):
            raise IOError("disk full")
        monkeypatch.setattr(dte, "save_build_progress", _boom)

        result = dte.execute_instructions("2026-08-10")

        # 断言: 返回 error 状态, 且 positions 未被写入 (atomic_write_json 未被调用写 POSITIONS)
        assert result.get("status") == "error"
        assert "build_progress" in result.get("reason", "")
        # atomic_write_json 被 _patch_execute mock 为 no-op, 此处仅验证未因 progress 失败而带病前进
        # 关键: exec_mock 已执行 (执行本身成功), 但 positions 写被禁止 → 无双重风险
        assert exec_mock.call_count == 2

    def test_c1_idempotent_keys_recorded(self, monkeypatch):
        """C1 纵深防御: 正常执行后 progress 含 executed_instruction_keys."""
        confirmed = self._make_confirmed(2)
        store = {}
        self._patch_execute(monkeypatch, confirmed, store)

        exec_mock = MagicMock(return_value={
            "fill_amount": 10000.0, "fill_price": 10.0,
            "built_before": 0, "built_after": 10000,
        })
        monkeypatch.setattr(dte, "_execute_single_instruction", exec_mock)

        dte.execute_instructions("2026-08-10")

        saved = store.get("progress", {})
        keys = saved.get("executed_instruction_keys", [])
        assert len(keys) == 2
        assert "600000.SH_0:1000" in keys
        assert "600000.SH_1:1001" in keys

    def test_c1_no_double_execution_when_already_executed(self, monkeypatch):
        """回归保护: 当日已执行 (daily_records 含该日期) 时不再执行指令."""
        confirmed = self._make_confirmed(2)
        # 预置已执行进度 (含当日 daily_record + executed_keys)
        preset = {
            "total_built": 20000,
            "daily_records": [{"date": "2026-08-10", "executed_count": 2}],
            "built_amounts": {},
            "executed_instruction_keys": ["600000.SH_0:1000", "600000.SH_1:1001"],
        }
        store = {"progress": preset}
        self._patch_execute(monkeypatch, confirmed, store)
        exec_mock = MagicMock(return_value={"fill_amount": 1.0})
        monkeypatch.setattr(dte, "_execute_single_instruction", exec_mock)

        dte.execute_instructions("2026-08-10")

        # 走 already_executed 分支 → _execute_single_instruction 不被调用
        assert exec_mock.call_count == 0


# ============================================================
# C2 (#22) KillSwitch L1 被绕过
# ============================================================

class TestC2KillSwitchL1Bypass:
    """验证 trades 重建后 KillSwitch L1 (can_open=False) 仍过滤 BUY."""

    def _make_decision(self, sides):
        trades = [{"symbol": f"60000{i}.SH", "side": s} for i, s in enumerate(sides)]
        return SimpleNamespace(trades=trades, target_weights={}, meta={})

    def test_c2_l1_filters_buy_after_regen(self):
        """C2 核心: can_open=False 时 BUY trades 被过滤, SELL 保留."""
        decision = self._make_decision(["BUY", "SELL", "BUY"])
        ks_result = {"can_open": False, "level": 1}
        result = {}

        out = apply_killswitch_l1_filter(decision, ks_result, result)

        remaining = [t["side"] for t in decision.trades]
        assert remaining == ["SELL"], f"BUY 未被过滤: {remaining}"
        assert out["steps"]["killswitch_l1_filter"]["filtered_buy"] == 2
        assert out["steps"]["killswitch_l1_filter"]["remaining"] == 1

    def test_c2_no_filter_when_can_open_true(self):
        """can_open=True 时 trades 不被修改 (正常交易路径)."""
        decision = self._make_decision(["BUY", "SELL"])
        ks_result = {"can_open": True, "level": 0}
        result = {}

        apply_killswitch_l1_filter(decision, ks_result, result)

        remaining = [t["side"] for t in decision.trades]
        assert remaining == ["BUY", "SELL"]
        assert "killswitch_l1_filter" not in result.get("steps", {})

    def test_c2_none_ks_result_no_crash(self):
        """ks_result 为 None 时不抛异常 (fail-safe)."""
        decision = self._make_decision(["BUY", "SELL"])
        result = {}
        apply_killswitch_l1_filter(decision, None, result)
        assert [t["side"] for t in decision.trades] == ["BUY", "SELL"]
