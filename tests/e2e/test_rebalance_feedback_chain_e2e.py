"""test_rebalance_feedback_chain_e2e.py — 再平衡→进化反馈链端到端测试

验证完整闭环:
    再平衡执行 → _write_rebalance_feedback_to_shadow → daily_returns.jsonl
    → 下一轮 collect_metrics 读取 → 进化评估消费再平衡后收益

场景:
    1. 完整反馈链: 再平衡→回写→collect_metrics 读取
    2. 多日累积: 连续再平衡→多日 daily_returns→collect_metrics 读全部
    3. 覆盖语义: 市场行情先写入→再平衡回写覆盖→进化读再平衡后收益
    4. 反馈链闭合: 再平衡后收益影响下一轮进化决策
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def isolated_feedback_path(tmp_path, monkeypatch):
    """隔离 daily_returns.jsonl 到临时目录."""
    fake_path = tmp_path / "reports" / "shadow" / "daily_returns.jsonl"
    fake_path.parent.mkdir(parents=True, exist_ok=True)

    import etf_option_hedge_rebalancer as mod
    monkeypatch.setattr(mod, "_PROJECT_ROOT", tmp_path)
    return fake_path


@pytest.fixture
def rebalancer(isolated_feedback_path):
    from etf_option_hedge_rebalancer import ETFOptionHedgeRebalancer
    r = ETFOptionHedgeRebalancer.__new__(ETFOptionHedgeRebalancer)
    r.target_annual_return = 0.08
    r.target_max_drawdown = 0.15
    return r


def _make_plan(trade_date, positions_count=3, evolution_applied=True):
    from etf_option_hedge_rebalancer import DailyPlan
    return DailyPlan(
        trade_date=trade_date,
        execution_summary=f"再平衡{positions_count}笔",
        rebalance_orders=[{"action": "BUY", "adjust_value": 10000} for _ in range(positions_count)],
        evolution_action={"weight_adjustments": {"510300.SH": 1.2}} if evolution_applied else {},
        estimated_annual_return=0.08,
    )


def _make_positions(daily_return=0.005):
    return {
        "510300.SH": {"target_weight": 0.15, "daily_return": daily_return},
        "510050.SH": {"target_weight": 0.10, "daily_return": daily_return * 0.5},
        "510500.SH": {"target_weight": 0.07, "daily_return": -daily_return * 0.3},
    }


# ============================================================
# 场景1: 完整反馈链
# ============================================================

class TestFullFeedbackChain:
    """再平衡→回写→collect_metrics 读取完整链路."""

    def test_rebalance_to_collect_metrics_round_trip(
        self, rebalancer, isolated_feedback_path,
    ):
        plan = _make_plan("2026-08-21")
        positions = _make_positions(0.005)
        prices = {"510300.SH": 3.85, "510050.SH": 2.95, "510500.SH": 5.62}

        rebalancer._write_rebalance_feedback_to_shadow(plan, positions, prices)

        assert isolated_feedback_path.exists()
        record = json.loads(isolated_feedback_path.read_text(encoding="utf-8").strip())
        assert record["source"] == "rebalance_feedback_v86"
        assert record["rebalance_executed"] is True

        from utils.alpha.evolution_orchestrator import EvolutionOrchestrator

        with patch("utils.alpha.evolution_orchestrator.DEFAULT_DAILY_RETURNS_PATH", isolated_feedback_path):
            with patch.object(EvolutionOrchestrator, "_check_feature_flag", return_value=True):
                orch = EvolutionOrchestrator()
                orch._enabled = True
                orch._evaluator_enabled = True
                metrics = orch.collect_metrics()

        assert not metrics.is_degraded
        assert len(metrics.daily_returns) == 1
        assert metrics.dates[0] == "2026-08-21"

    def test_feedback_record_has_rebalance_markers(
        self, rebalancer, isolated_feedback_path,
    ):
        plan = _make_plan("2026-08-21", positions_count=5, evolution_applied=True)
        positions = _make_positions(0.003)
        prices = {"510300.SH": 3.85, "510050.SH": 2.95, "510500.SH": 5.62}

        rebalancer._write_rebalance_feedback_to_shadow(plan, positions, prices)

        record = json.loads(isolated_feedback_path.read_text(encoding="utf-8").strip())
        assert record["rebalance_executed"] is True
        assert record["rebalance_orders_count"] == 5
        assert record["evolution_applied"] is True


# ============================================================
# 场景2: 多日累积
# ============================================================

class TestMultiDayAccumulation:
    """连续多日再平衡→多日 daily_returns→collect_metrics 读全部."""

    def test_three_days_accumulation(
        self, rebalancer, isolated_feedback_path,
    ):
        prices = {"510300.SH": 3.85, "510050.SH": 2.95, "510500.SH": 5.62}
        for i, date in enumerate(["2026-08-19", "2026-08-20", "2026-08-21"]):
            plan = _make_plan(date)
            positions = _make_positions(0.001 * (i + 1))
            rebalancer._write_rebalance_feedback_to_shadow(plan, positions, prices)

        lines = isolated_feedback_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3
        dates = [json.loads(line)["date"] for line in lines]
        assert dates == ["2026-08-19", "2026-08-20", "2026-08-21"]

        from utils.alpha.evolution_orchestrator import EvolutionOrchestrator

        with patch("utils.alpha.evolution_orchestrator.DEFAULT_DAILY_RETURNS_PATH", isolated_feedback_path):
            with patch.object(EvolutionOrchestrator, "_check_feature_flag", return_value=True):
                orch = EvolutionOrchestrator()
                orch._enabled = True
                orch._evaluator_enabled = True
                metrics = orch.collect_metrics()

        assert len(metrics.daily_returns) == 3
        assert len(metrics.dates) == 3
        assert metrics.sample_count == 3


# ============================================================
# 场景3: 覆盖语义
# ============================================================

class TestOverwriteSemantics:
    """市场行情先写入→再平衡回写覆盖→进化读再平衡后收益."""

    def test_rebalance_overwrites_market_data(
        self, rebalancer, isolated_feedback_path,
    ):
        market_record = {
            "date": "2026-08-21",
            "daily_return": 0.001,
            "source": "w13a_real_market_feed",
            "updated_at": "2026-08-21T15:30:00",
            "symbols_count": 14,
            "cross_validated": False,
            "source_consistency": "high",
        }
        isolated_feedback_path.write_text(
            json.dumps(market_record) + "\n", encoding="utf-8",
        )

        plan = _make_plan("2026-08-21")
        positions = _make_positions(0.008)
        prices = {"510300.SH": 3.85, "510050.SH": 2.95, "510500.SH": 5.62}
        rebalancer._write_rebalance_feedback_to_shadow(plan, positions, prices)

        lines = isolated_feedback_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["source"] == "rebalance_feedback_v86"
        assert record["daily_return"] != 0.001

    def test_unmodified_dates_preserved(
        self, rebalancer, isolated_feedback_path,
    ):
        market_records = [
            {"date": "2026-08-19", "daily_return": 0.002, "source": "market"},
            {"date": "2026-08-20", "daily_return": -0.001, "source": "market"},
        ]
        isolated_feedback_path.write_text(
            "\n".join(json.dumps(r) for r in market_records) + "\n",
            encoding="utf-8",
        )

        plan = _make_plan("2026-08-21")
        positions = _make_positions(0.005)
        prices = {"510300.SH": 3.85, "510050.SH": 2.95, "510500.SH": 5.62}
        rebalancer._write_rebalance_feedback_to_shadow(plan, positions, prices)

        lines = isolated_feedback_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3
        assert json.loads(lines[0])["source"] == "market"
        assert json.loads(lines[1])["source"] == "market"
        assert json.loads(lines[2])["source"] == "rebalance_feedback_v86"


# ============================================================
# 场景4: 反馈链闭合
# ============================================================

class TestFeedbackLoopClosure:
    """再平衡后收益影响下一轮进化决策."""

    def test_rebalanced_return_consumed_by_evolution(
        self, rebalancer, isolated_feedback_path,
    ):
        plan = _make_plan("2026-08-21")
        positions = _make_positions(0.012)
        prices = {"510300.SH": 3.85, "510050.SH": 2.95, "510500.SH": 5.62}
        rebalancer._write_rebalance_feedback_to_shadow(plan, positions, prices)

        from utils.alpha.evolution_orchestrator import EvolutionOrchestrator

        with patch("utils.alpha.evolution_orchestrator.DEFAULT_DAILY_RETURNS_PATH", isolated_feedback_path):
            with patch.object(EvolutionOrchestrator, "_check_feature_flag", return_value=True):
                orch = EvolutionOrchestrator()
                orch._enabled = True
                orch._evaluator_enabled = True
                metrics = orch.collect_metrics()

        assert not metrics.is_degraded
        assert len(metrics.daily_returns) == 1

        rebalanced_return = metrics.daily_returns[0]
        assert rebalanced_return != 0.0

    def test_evolution_applied_flag_in_feedback(
        self, rebalancer, isolated_feedback_path,
    ):
        plan_with_evolution = _make_plan("2026-08-21", evolution_applied=True)
        positions = _make_positions(0.005)
        prices = {"510300.SH": 3.85, "510050.SH": 2.95, "510500.SH": 5.62}
        rebalancer._write_rebalance_feedback_to_shadow(plan_with_evolution, positions, prices)

        record = json.loads(isolated_feedback_path.read_text(encoding="utf-8").strip())
        assert record["evolution_applied"] is True

        plan_without_evolution = _make_plan("2026-08-22", evolution_applied=False)
        rebalancer._write_rebalance_feedback_to_shadow(plan_without_evolution, positions, prices)

        lines = isolated_feedback_path.read_text(encoding="utf-8").strip().split("\n")
        record_22 = json.loads(lines[1])
        assert record_22["evolution_applied"] is False

    def test_feedback_chain_does_not_break_on_errors(
        self, rebalancer, isolated_feedback_path,
    ):
        from etf_option_hedge_rebalancer import DailyPlan
        plan = DailyPlan(trade_date="2026-08-21")

        with patch("pathlib.Path.mkdir", side_effect=OSError("模拟权限错误")):
            rebalancer._write_rebalance_feedback_to_shadow(plan, {}, {})

        assert True
