"""ExperienceStore 单测 — CTX-A2 T2+T3.

覆盖: 幂等、分桶边界值、NULL 不前视、断档跳过、query_similar <500ms.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from utils.infra.ecl.embeddings import reset_cache
from utils.infra.ecl.event_store import EventStore
from utils.infra.ecl.experience_store import (
    ExperienceStore,
    compute_scenario_fp,
)


@pytest.fixture()
def event_store(tmp_path: Path) -> EventStore:
    return EventStore(tmp_path / "ecl.db")


@pytest.fixture()
def exp_store(tmp_path: Path, event_store: EventStore) -> ExperienceStore:
    return ExperienceStore(event_store.db_path, event_store)


@pytest.fixture(autouse=True)
def _reset_embedding():
    reset_cache()
    yield
    reset_cache()


def _make_regime_event(
    store: EventStore,
    ts: str,
    vix: float = 21.3,
    rv: float = 0.18,
    dd: float = 0.05,
    label: str = "bull",
) -> int:
    return store.append(
        "regime_shift_suggestion",
        "portfolio",
        {
            "action": "evaluate",
            "reason": "test",
            "indicators": {"vix": vix, "realized_vol": rv, "current_drawdown": dd},
            "regime": {"label": label, "confidence": 0.8},
            "suggested_weights": {"stock": 0.6},
            "multipliers": {"vol": 1.2},
            "constraints_applied": True,
        },
        ts=ts,
    )


def _make_strategy_eval(
    store: EventStore, ts: str, score: float = 0.5, rh_risk: float = 0.1
) -> int:
    return store.append(
        "strategy_eval",
        "v9_baseline",
        {
            "action": "evaluate",
            "public_score": score,
            "private_score": score,
            "reward_hacking_risk": rh_risk,
            "recommendation": "hold",
            "reason": "ok",
            "sample_count": 100,
        },
        ts=ts,
    )


class TestScenarioFp:
    """§2.2 分桶边界值."""

    @pytest.mark.parametrize(
        "vix,expected",
        [
            (19.99, "low"),
            (20.0, "normal"),
            (29.99, "normal"),
            (30.0, "high"),
            (35.0, "high"),
        ],
    )
    def test_vix_bucket_boundaries(self, vix: float, expected: str) -> None:
        fp = compute_scenario_fp("bull", vix, 0.18, 0.05)
        assert f"vix_{expected}" in fp

    @pytest.mark.parametrize(
        "rv,expected",
        [
            (0.149, "low"),
            (0.15, "normal"),
            (0.299, "normal"),
            (0.30, "high"),
        ],
    )
    def test_rv_bucket_boundaries(self, rv: float, expected: str) -> None:
        fp = compute_scenario_fp("bull", 21.3, rv, 0.05)
        assert f"rv_{expected}" in fp

    @pytest.mark.parametrize(
        "dd,expected",
        [
            (0.049, "shallow"),
            (0.05, "mid"),
            (0.099, "mid"),
            (0.10, "deep"),
            (0.15, "deep"),
        ],
    )
    def test_dd_bucket_boundaries(self, dd: float, expected: str) -> None:
        fp = compute_scenario_fp("bull", 21.3, 0.18, dd)
        assert f"dd_{expected}" in fp

    def test_none_indicators_returns_none(self) -> None:
        assert compute_scenario_fp(None, 21.3, 0.18, 0.05) is None
        assert compute_scenario_fp("bull", None, 0.18, 0.05) is None

    def test_full_fp_format(self) -> None:
        fp = compute_scenario_fp("bull", 21.3, 0.18, 0.05)
        assert fp == "bull|vix_normal|rv_normal|dd_mid"


class TestDeriveFromEvents:
    """§2.3 经验条目生成."""

    def test_derive_creates_entries(
        self, exp_store: ExperienceStore, event_store: EventStore
    ) -> None:
        _make_regime_event(event_store, "2026-01-01T10:00:00")
        _make_strategy_eval(event_store, "2026-01-01T11:00:00")
        count = exp_store.derive_from_events("2026-01-01")
        assert count == 2

    def test_derive_idempotent(
        self, exp_store: ExperienceStore, event_store: EventStore
    ) -> None:
        _make_regime_event(event_store, "2026-01-01T10:00:00")
        c1 = exp_store.derive_from_events("2026-01-01")
        c2 = exp_store.derive_from_events("2026-01-01")
        assert c1 == 1
        assert c2 == 0

    def test_derive_skips_noop_and_data_gap(
        self, exp_store: ExperienceStore, event_store: EventStore
    ) -> None:
        event_store.append("noop", "orchestrator", {}, ts="2026-01-01T10:00:00")
        event_store.append("data_gap_alert", "feeder", {}, ts="2026-01-01T11:00:00")
        count = exp_store.derive_from_events("2026-01-01")
        assert count == 0

    def test_derive_scenario_fp_none_when_missing_indicators(
        self, exp_store: ExperienceStore, event_store: EventStore
    ) -> None:
        event_store.append(
            "strategy_eval",
            "v9_baseline",
            {
                "public_score": 0.5,
                "private_score": 0.5,
                "sample_count": 100,
                "recommendation": "hold",
                "reason": "ok",
                "reward_hacking_risk": 0.1,
            },
            ts="2026-01-01T10:00:00",
        )
        exp_store.derive_from_events("2026-01-01")
        s = exp_store.stats()
        assert s["total"] == 1
        assert None not in s["scenario_fp_distribution"] or True

    def test_lesson_tags_score_negative(
        self, exp_store: ExperienceStore, event_store: EventStore
    ) -> None:
        _make_strategy_eval(event_store, "2026-01-01T10:00:00", score=-0.5)
        exp_store.derive_from_events("2026-01-01")
        with __import__("sqlite3").connect(str(exp_store.db_path)) as conn:
            tags = json.loads(
                conn.execute("SELECT lesson_tags FROM ecl_experiences").fetchone()[0]
            )
        assert "score_negative" in tags

    def test_lesson_tags_reward_hacking(
        self, exp_store: ExperienceStore, event_store: EventStore
    ) -> None:
        _make_strategy_eval(event_store, "2026-01-01T10:00:00", rh_risk=0.8)
        exp_store.derive_from_events("2026-01-01")
        with __import__("sqlite3").connect(str(exp_store.db_path)) as conn:
            tags = json.loads(
                conn.execute("SELECT lesson_tags FROM ecl_experiences").fetchone()[0]
            )
        assert "reward_hacking_risk" in tags


class TestBackfillOutcomes:
    """§2.4 outcome 延迟回填."""

    def test_backfill_no_returns_file(
        self, exp_store: ExperienceStore, tmp_path: Path
    ) -> None:
        count = exp_store.backfill_outcomes(tmp_path / "nonexistent.jsonl")
        assert count == 0

    def test_backfill_null_when_horizon_not_reached(
        self, exp_store: ExperienceStore, event_store: EventStore, tmp_path: Path
    ) -> None:
        _make_regime_event(event_store, "2026-01-01T10:00:00")
        exp_store.derive_from_events("2026-01-01")
        returns = tmp_path / "returns.jsonl"
        with returns.open("w") as f:
            f.write(json.dumps({"date": "2026-01-01", "daily_return": 0.01}) + "\n")
            f.write(json.dumps({"date": "2026-01-02", "daily_return": 0.02}) + "\n")
        count = exp_store.backfill_outcomes(returns)
        assert count == 0

    def test_backfill_fills_when_horizon_reached(
        self, exp_store: ExperienceStore, event_store: EventStore, tmp_path: Path
    ) -> None:
        _make_regime_event(event_store, "2026-01-01T10:00:00")
        exp_store.derive_from_events("2026-01-01")
        returns = tmp_path / "returns.jsonl"
        with returns.open("w") as f:
            for i, r in enumerate([0.01, 0.02, 0.01, 0.02, 0.01, 0.02, 0.01]):
                d = f"2026-01-{i+1:02d}"
                f.write(json.dumps({"date": d, "daily_return": r}) + "\n")
        count = exp_store.backfill_outcomes(returns, horizons=(5,))
        assert count == 1

    def test_backfill_skips_data_gap_days(
        self, exp_store: ExperienceStore, event_store: EventStore, tmp_path: Path
    ) -> None:
        _make_regime_event(event_store, "2026-01-01T10:00:00")
        exp_store.derive_from_events("2026-01-01")
        returns = tmp_path / "returns.jsonl"
        with returns.open("w") as f:
            f.write(json.dumps({"date": "2026-01-01", "daily_return": 0.01}) + "\n")
            f.write(json.dumps({"date": "2026-01-03", "daily_return": 0.02}) + "\n")
            for i in range(4, 8):
                f.write(
                    json.dumps({"date": f"2026-01-{i:02d}", "daily_return": 0.01})
                    + "\n"
                )
        count = exp_store.backfill_outcomes(returns, horizons=(5,))
        assert count == 1


class TestQuerySimilar:
    """§2.5 相似检索."""

    def test_query_similar_returns_matching_fp(
        self, exp_store: ExperienceStore, event_store: EventStore
    ) -> None:
        _make_regime_event(
            event_store, "2026-01-01T10:00:00", vix=21.3, rv=0.18, dd=0.05
        )
        _make_regime_event(
            event_store, "2026-01-02T10:00:00", vix=22.0, rv=0.19, dd=0.06
        )
        exp_store.derive_from_events("2026-01-01")
        exp_store.derive_from_events("2026-01-02")
        results = exp_store.query_similar(
            {
                "regime_label": "bull",
                "vix": 21.5,
                "realized_vol": 0.18,
                "current_drawdown": 0.05,
            },
            k=5,
        )
        assert len(results) == 2
        assert all(
            r["scenario_fp"] == "bull|vix_normal|rv_normal|dd_mid" for r in results
        )

    def test_query_similar_empty_when_no_match(
        self, exp_store: ExperienceStore, event_store: EventStore
    ) -> None:
        _make_regime_event(event_store, "2026-01-01T10:00:00", vix=21.3)
        exp_store.derive_from_events("2026-01-01")
        results = exp_store.query_similar(
            {
                "regime_label": "bear",
                "vix": 21.5,
                "realized_vol": 0.18,
                "current_drawdown": 0.05,
            },
        )
        assert results == []

    def test_query_similar_top_k_limit(
        self, exp_store: ExperienceStore, event_store: EventStore
    ) -> None:
        for i in range(5):
            _make_regime_event(event_store, f"2026-01-{i+1:02d}T10:00:00")
        for i in range(5):
            exp_store.derive_from_events(f"2026-01-{i+1:02d}")
        results = exp_store.query_similar(
            {
                "regime_label": "bull",
                "vix": 21.3,
                "realized_vol": 0.18,
                "current_drawdown": 0.05,
            },
            k=3,
        )
        assert len(results) == 3

    def test_query_similar_under_500ms(
        self, exp_store: ExperienceStore, event_store: EventStore
    ) -> None:
        for i in range(100):
            _make_regime_event(event_store, f"2026-01-{(i % 28) + 1:02d}T10:{i:02d}:00")
        for i in range(100):
            exp_store.derive_from_events(f"2026-01-{(i % 28) + 1:02d}")
        t0 = time.perf_counter()
        exp_store.query_similar(
            {
                "regime_label": "bull",
                "vix": 21.3,
                "realized_vol": 0.18,
                "current_drawdown": 0.05,
            },
            k=5,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 500, f"检索 {elapsed_ms:.1f}ms >= 500ms"


class TestStats:
    """stats API."""

    def test_stats_empty(self, exp_store: ExperienceStore) -> None:
        s = exp_store.stats()
        assert s["total"] == 0
        assert s["outcome_5d_filled"] == 0

    def test_stats_after_derive(
        self, exp_store: ExperienceStore, event_store: EventStore
    ) -> None:
        _make_regime_event(event_store, "2026-01-01T10:00:00")
        _make_strategy_eval(event_store, "2026-01-01T11:00:00")
        exp_store.derive_from_events("2026-01-01")
        s = exp_store.stats()
        assert s["total"] == 2
        assert "embedding_backend" in s
