"""test_rebalance_feedback_chain_unit.py — 再平衡→进化反馈链单元测试

覆盖:
  - _write_rebalance_feedback_to_shadow() 回写逻辑
  - daily_returns.jsonl 增量更新 (同日期去重)
  - collect_metrics 读取回写后数据
  - fail-safe: 回写异常不影响再平衡
  - 格式兼容: 与 ShadowRealDataFeeder 格式一致
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest


@pytest.fixture
def feedback_path(tmp_path, monkeypatch):
    """隔离 daily_returns.jsonl 到临时目录."""
    fake_path = tmp_path / "reports" / "shadow" / "daily_returns.jsonl"
    fake_path.parent.mkdir(parents=True, exist_ok=True)

    import etf_option_hedge_rebalancer as mod
    monkeypatch.setattr(mod, "_PROJECT_ROOT", tmp_path)
    return fake_path


@pytest.fixture
def rebalancer(feedback_path):
    from etf_option_hedge_rebalancer import ETFOptionHedgeRebalancer
    r = ETFOptionHedgeRebalancer.__new__(ETFOptionHedgeRebalancer)
    r.target_annual_return = 0.08
    r.target_max_drawdown = 0.15
    return r


@pytest.fixture
def sample_plan():
    from etf_option_hedge_rebalancer import DailyPlan
    return DailyPlan(
        trade_date="2026-08-21",
        execution_summary="再平衡2笔",
        rebalance_orders=[{"action": "BUY", "adjust_value": 10000}],
        evolution_action={"weight_adjustments": {"510300.SH": 1.2}},
        estimated_annual_return=0.08,
    )


@pytest.fixture
def sample_positions():
    return {
        "510300.SH": {"target_weight": 0.15, "daily_return": 0.005},
        "510050.SH": {"target_weight": 0.10, "daily_return": -0.003},
        "510500.SH": {"target_weight": 0.07, "daily_return": 0.008},
    }


@pytest.fixture
def sample_prices():
    return {"510300.SH": 3.85, "510050.SH": 2.95, "510500.SH": 5.62}


# ============================================================
# 回写逻辑测试
# ============================================================

class TestWriteRebalanceFeedback:
    """_write_rebalance_feedback_to_shadow 回写逻辑."""

    @pytest.mark.unit
    def test_writes_valid_jsonl(
        self, rebalancer, sample_plan, sample_positions, sample_prices, feedback_path,
    ):
        rebalancer._write_rebalance_feedback_to_shadow(
            sample_plan, sample_positions, sample_prices,
        )
        assert feedback_path.exists()
        lines = feedback_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["date"] == "2026-08-21"
        assert record["source"] == "rebalance_feedback_v86"
        assert "daily_return" in record
        assert record["rebalance_executed"] is True
        assert record["rebalance_orders_count"] == 1
        assert record["evolution_applied"] is True

    @pytest.mark.unit
    def test_daily_return_calculation(
        self, rebalancer, sample_plan, sample_positions, sample_prices, feedback_path,
    ):
        rebalancer._write_rebalance_feedback_to_shadow(
            sample_plan, sample_positions, sample_prices,
        )
        lines = feedback_path.read_text(encoding="utf-8").strip().split("\n")
        record = json.loads(lines[0])

        expected = (
            0.15 * 0.005 + 0.10 * (-0.003) + 0.07 * 0.008
        ) / (0.15 + 0.10 + 0.07)
        assert abs(record["daily_return"] - expected) < 1e-9

    @pytest.mark.unit
    def test_empty_trade_date_skips(
        self, rebalancer, sample_positions, sample_prices, feedback_path,
    ):
        from etf_option_hedge_rebalancer import DailyPlan
        plan = DailyPlan(trade_date="")
        rebalancer._write_rebalance_feedback_to_shadow(plan, sample_positions, sample_prices)
        assert not feedback_path.exists() or feedback_path.read_text(encoding="utf-8").strip() == ""

    @pytest.mark.unit
    def test_empty_positions_uses_estimated_return(
        self, rebalancer, sample_plan, feedback_path,
    ):
        rebalancer._write_rebalance_feedback_to_shadow(sample_plan, {}, {})
        lines = feedback_path.read_text(encoding="utf-8").strip().split("\n")
        record = json.loads(lines[0])
        expected = 0.08 / 252.0
        assert abs(record["daily_return"] - expected) < 1e-9


# ============================================================
# 增量更新测试 (同日期去重)
# ============================================================

class TestIncrementalUpdate:
    """同日期去重更新, 不同日期追加."""

    @pytest.mark.unit
    def test_same_date_overwrites(
        self, rebalancer, sample_plan, sample_positions, sample_prices, feedback_path,
    ):
        existing = {
            "date": "2026-08-21",
            "daily_return": 0.001,
            "source": "w13a_real_market_feed",
        }
        feedback_path.write_text(json.dumps(existing) + "\n", encoding="utf-8")

        rebalancer._write_rebalance_feedback_to_shadow(
            sample_plan, sample_positions, sample_prices,
        )
        lines = feedback_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["source"] == "rebalance_feedback_v86"

    @pytest.mark.unit
    def test_different_date_appends(
        self, rebalancer, sample_plan, sample_positions, sample_prices, feedback_path,
    ):
        existing = {
            "date": "2026-08-20",
            "daily_return": 0.001,
            "source": "w13a_real_market_feed",
        }
        feedback_path.write_text(json.dumps(existing) + "\n", encoding="utf-8")

        rebalancer._write_rebalance_feedback_to_shadow(
            sample_plan, sample_positions, sample_prices,
        )
        lines = feedback_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        dates = [json.loads(line)["date"] for line in lines]
        assert dates == sorted(dates)

    @pytest.mark.unit
    def test_preserves_existing_records(
        self, rebalancer, sample_plan, sample_positions, sample_prices, feedback_path,
    ):
        existing_records = [
            {"date": "2026-08-19", "daily_return": 0.002, "source": "market"},
            {"date": "2026-08-20", "daily_return": -0.001, "source": "market"},
        ]
        feedback_path.write_text(
            "\n".join(json.dumps(r) for r in existing_records) + "\n",
            encoding="utf-8",
        )

        rebalancer._write_rebalance_feedback_to_shadow(
            sample_plan, sample_positions, sample_prices,
        )
        lines = feedback_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3
        record_19 = json.loads(lines[0])
        assert record_19["source"] == "market"


# ============================================================
# fail-safe 测试
# ============================================================

class TestFailSafe:
    """回写异常时不影响再平衡主流程."""

    @pytest.mark.unit
    def test_permission_error_does_not_raise(
        self, rebalancer, sample_plan, sample_positions, sample_prices,
    ):
        with patch("pathlib.Path.mkdir", side_effect=OSError("权限不足")):
            rebalancer._write_rebalance_feedback_to_shadow(
                sample_plan, sample_positions, sample_prices,
            )

    @pytest.mark.unit
    def test_invalid_positions_does_not_raise(
        self, rebalancer, sample_plan, feedback_path,
    ):
        invalid_positions = {"INVALID": None}
        rebalancer._write_rebalance_feedback_to_shadow(
            sample_plan, invalid_positions, {},
        )

    @pytest.mark.unit
    def test_corrupted_existing_file_does_not_raise(
        self, rebalancer, sample_plan, sample_positions, sample_prices, feedback_path,
    ):
        feedback_path.write_text("corrupted json\n{invalid", encoding="utf-8")
        rebalancer._write_rebalance_feedback_to_shadow(
            sample_plan, sample_positions, sample_prices,
        )
        lines = feedback_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["source"] == "rebalance_feedback_v86"


# ============================================================
# collect_metrics 读取回写后数据
# ============================================================

class TestCollectMetricsReadsFeedback:
    """collect_metrics 能读取回写后的 daily_returns.jsonl."""

    @pytest.mark.unit
    def test_collect_metrics_reads_rebalanced_return(
        self, rebalancer, sample_plan, sample_positions, sample_prices, feedback_path,
    ):
        rebalancer._write_rebalance_feedback_to_shadow(
            sample_plan, sample_positions, sample_prices,
        )

        from utils.alpha.evolution_orchestrator import EvolutionOrchestrator

        with patch("utils.alpha.evolution_orchestrator.DEFAULT_DAILY_RETURNS_PATH", feedback_path):
            with patch.object(EvolutionOrchestrator, "_check_feature_flag", return_value=True):
                orch = EvolutionOrchestrator()
                orch._enabled = True
                orch._evaluator_enabled = True
                metrics = orch.collect_metrics()

        assert not metrics.is_degraded
        assert len(metrics.daily_returns) == 1
        expected = (
            0.15 * 0.005 + 0.10 * (-0.003) + 0.07 * 0.008
        ) / (0.15 + 0.10 + 0.07)
        assert abs(metrics.daily_returns[0] - expected) < 1e-9
        assert metrics.dates[0] == "2026-08-21"


# ============================================================
# 格式兼容测试
# ============================================================

class TestFormatCompatibility:
    """回写记录格式与 ShadowRealDataFeeder 一致."""

    @pytest.mark.unit
    def test_required_fields_present(
        self, rebalancer, sample_plan, sample_positions, sample_prices, feedback_path,
    ):
        rebalancer._write_rebalance_feedback_to_shadow(
            sample_plan, sample_positions, sample_prices,
        )
        record = json.loads(feedback_path.read_text(encoding="utf-8").strip())

        required_fields = [
            "date", "daily_return", "source", "updated_at",
            "symbols_count", "cross_validated", "source_consistency",
        ]
        for field in required_fields:
            assert field in record, f"缺少必需字段: {field}"

    @pytest.mark.unit
    def test_extended_fields_present(
        self, rebalancer, sample_plan, sample_positions, sample_prices, feedback_path,
    ):
        rebalancer._write_rebalance_feedback_to_shadow(
            sample_plan, sample_positions, sample_prices,
        )
        record = json.loads(feedback_path.read_text(encoding="utf-8").strip())

        assert "rebalance_executed" in record
        assert "rebalance_orders_count" in record
        assert "evolution_applied" in record

    @pytest.mark.unit
    def test_json_serializable(
        self, rebalancer, sample_plan, sample_positions, sample_prices, feedback_path,
    ):
        rebalancer._write_rebalance_feedback_to_shadow(
            sample_plan, sample_positions, sample_prices,
        )
        text = feedback_path.read_text(encoding="utf-8").strip()
        record = json.loads(text)
        re_serialized = json.dumps(record, ensure_ascii=False)
        re_parsed = json.loads(re_serialized)
        assert re_parsed == record
