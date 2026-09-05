"""ComboStateManager 单元测试 — 原子写入/向前兼容/幂等性/预算分账."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.etf_option_combo.combo_state import _STATE_VERSION, ComboStateManager

pytestmark = pytest.mark.unit


class TestStateManagerBasic:
    def test_init_creates_empty_state(self, tmp_path):
        """新文件初始化为空 schema."""
        mgr = ComboStateManager(state_path=tmp_path / "s1.json")
        assert mgr._state["config_version"] == _STATE_VERSION
        assert mgr._state["strategy_instances"] == {}
        assert mgr._state["budgets"] == {}

    def test_save_and_load(self, tmp_path):
        """保存后重新加载一致."""
        path = tmp_path / "s2.json"
        mgr1 = ComboStateManager(state_path=path)
        mgr1.save_strategy_instance("cc_510050_20260903", {"strategy_type": "covered_call"})
        mgr2 = ComboStateManager(state_path=path)
        inst = mgr2.get_strategy_instance("cc_510050_20260903")
        assert inst is not None
        assert inst["strategy_type"] == "covered_call"


class TestStateManagerIdempotency:
    def test_save_strategy_instance_idempotent(self, tmp_path):
        """同 instance_id 重复 save 不产生重复记录."""
        mgr = ComboStateManager(state_path=tmp_path / "s3.json")
        for _ in range(3):
            mgr.save_strategy_instance("cc_510050_20260903", {"net_premium": 500.0})
        assert len(mgr._state["strategy_instances"]) == 1
        assert mgr._state["strategy_instances"]["cc_510050_20260903"]["net_premium"] == 500.0


class TestStateManagerBudget:
    def test_update_budget_income(self, tmp_path):
        """正权利金计入收入."""
        mgr = ComboStateManager(state_path=tmp_path / "s4.json")
        mgr.update_budget("covered_call", 1000.0)
        budget = mgr.get_budget("covered_call")
        assert budget["ytd_income"] == 1000.0
        assert budget["ytd_expense"] == 0.0

    def test_update_budget_expense(self, tmp_path):
        """负权利金计入支出."""
        mgr = ComboStateManager(state_path=tmp_path / "s5.json")
        mgr.update_budget("collar", -500.0)
        budget = mgr.get_budget("collar")
        assert budget["ytd_expense"] == 500.0
        assert budget["ytd_income"] == 0.0

    def test_update_budget_accumulation(self, tmp_path):
        """多次更新累加."""
        mgr = ComboStateManager(state_path=tmp_path / "s6.json")
        mgr.update_budget("covered_call", 1000.0)
        mgr.update_budget("covered_call", 500.0)
        mgr.update_budget("covered_call", -200.0)
        budget = mgr.get_budget("covered_call")
        assert budget["ytd_income"] == 1500.0
        assert budget["ytd_expense"] == 200.0

    def test_get_budget_unknown_strategy(self, tmp_path):
        """未知策略返回零预算."""
        mgr = ComboStateManager(state_path=tmp_path / "s7.json")
        budget = mgr.get_budget("unknown")
        assert budget == {"ytd_income": 0.0, "ytd_expense": 0.0}


class TestStateManagerMigration:
    def test_migrate_v0_to_v1(self, tmp_path):
        """v0 schema (无 config_version) 迁移至 v1.0."""
        path = tmp_path / "s8.json"
        old_state = {
            "instances": {"cc_001": {"strategy_type": "covered_call"}},
            "budgets": {"covered_call": {"ytd_income": 100.0, "ytd_expense": 0.0}},
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(old_state, f)

        mgr = ComboStateManager(state_path=path)
        assert mgr._state["config_version"] == _STATE_VERSION
        assert "strategy_instances" in mgr._state
        assert "cc_001" in mgr._state["strategy_instances"]

    def test_load_corrupted_file(self, tmp_path):
        """损坏文件返回空 schema."""
        path = tmp_path / "s9.json"
        with open(path, "w", encoding="utf-8") as f:
            f.write("{invalid json}")
        mgr = ComboStateManager(state_path=path)
        assert mgr._state["config_version"] == _STATE_VERSION
        assert mgr._state["strategy_instances"] == {}


class TestStateManagerAtomicWrite:
    def test_atomic_write_no_tmp_residue(self, tmp_path):
        """原子写入后无 .tmp 残留."""
        path = tmp_path / "s10.json"
        mgr = ComboStateManager(state_path=path)
        mgr.save_strategy_instance("cc_001", {"x": 1})
        assert not Path(str(path) + ".tmp").exists()
        assert path.exists()

    def test_clear_all(self, tmp_path):
        """clear_all 清空状态."""
        mgr = ComboStateManager(state_path=tmp_path / "s11.json")
        mgr.save_strategy_instance("cc_001", {"x": 1})
        mgr.update_budget("covered_call", 100.0)
        assert mgr.clear_all() is True
        assert mgr._state["strategy_instances"] == {}
        assert mgr._state["budgets"] == {}
