"""test_grayscale_protection_unit.py — 灰度阶段初期绝对回撤保护 (Issue #13: S-2)

巡检事实: should_rollback 的 2σ 分支要求 daily_pnl_series >= 5, 且回滚后
consecutive_losses 归零 → 刚进入 auto_10 的前 5 个交易日**没有任何自动回滚保护**。
本测试锁定新增的"与样本数无关"的绝对回撤阈值分支。
"""

from __future__ import annotations

import pytest

from ai_decision.grayscale_state import GrayscaleState
from utils.risk_thresholds import get_portfolio_protection_config


def _auto_state() -> GrayscaleState:
    gs = GrayscaleState()
    gs.stage = "auto_10"
    return gs


class TestInitialStageProtection:
    @pytest.mark.unit
    def test_no_rollback_without_loss(self):
        gs = _auto_state()
        assert gs.should_rollback() == (False, "")

    @pytest.mark.unit
    def test_absolute_drawdown_triggers_without_history(self):
        """S-2 核心回归: 零历史样本 + 累计亏损超阈值 → 必须回滚。"""
        cfg = get_portfolio_protection_config()
        gs = _auto_state()
        gs.cumulative_pnl = -(cfg["initial_drawdown_stop_pct"] + 0.001)
        should_rb, reason = gs.should_rollback()
        assert should_rb is True
        assert "绝对阈值" in reason

    @pytest.mark.unit
    def test_absolute_drawdown_boundary_not_triggered(self):
        cfg = get_portfolio_protection_config()
        gs = _auto_state()
        gs.cumulative_pnl = -cfg["initial_drawdown_stop_pct"] / 2
        assert gs.should_rollback()[0] is False

    @pytest.mark.unit
    def test_shadow_and_paper_never_rollback(self):
        cfg = get_portfolio_protection_config()
        for stage in ("shadow", "paper"):
            gs = GrayscaleState()
            gs.stage = stage
            gs.cumulative_pnl = -(cfg["initial_drawdown_stop_pct"] * 10)
            assert gs.should_rollback()[0] is False

    @pytest.mark.unit
    def test_advance_grayscale_rolls_back_on_absolute_loss(self):
        """端到端: advance_grayscale 在阶段初期也应触发回滚。"""
        gs = _auto_state()
        gs.save = lambda: None  # 不落盘
        cfg = get_portfolio_protection_config()
        res = gs.advance_grayscale(
            daily_pnl=-(cfg["initial_drawdown_stop_pct"] + 0.01),
            daily_decision_count=1,
        )
        assert res["rollback_triggered"] is True
        assert "绝对阈值" in res["rollback_reason"]

    @pytest.mark.unit
    def test_two_sigma_branch_still_active_with_enough_samples(self):
        """不回归: 样本充足时 2σ 分支仍然生效。"""
        gs = _auto_state()
        gs.daily_pnl_series = [0.01] * 20 + [-0.05]  # 最后一天显著偏离
        gs.cumulative_pnl = -0.001  # 未触及绝对阈值
        should_rb, reason = gs.should_rollback()
        assert should_rb is True
        assert "2σ" in reason
