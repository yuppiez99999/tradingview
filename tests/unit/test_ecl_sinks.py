"""ECL Sink 单测 — CTX-A1 T3.

覆盖: 映射规则、flag off noop、sink 失败静默降级、注册表.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from utils.infra.ecl.sinks import (
    EclEventSink,
    _map_record_to_event,
    clear_sinks,
    ensure_default_sink,
    iter_registered_sinks,
    register_sink,
)


class TestRecordMapping:
    """§2.3 事件映射规则."""

    def test_regime_shift_suggestion(self) -> None:
        record = {
            "timestamp": "2026-01-01T10:00:00",
            "action": "evaluate",
            "evaluator_report": {
                "vol_regime_suggestion": {
                    "indicators": {
                        "vix": 21.3,
                        "realized_vol": 0.18,
                        "current_drawdown": 0.05,
                    },
                    "regime": {"label": "bull", "confidence": 0.8},
                    "suggested_weights": {"stock": 0.6},
                    "multipliers": {"vol": 1.2},
                    "constraints_applied": True,
                }
            },
        }
        et, sub, payload = _map_record_to_event(record)
        assert et == "regime_shift_suggestion"
        assert sub == "portfolio"
        assert payload["indicators"]["vix"] == 21.3
        assert payload["regime"]["label"] == "bull"

    def test_strategy_eval(self) -> None:
        record = {
            "timestamp": "2026-01-01T10:00:00",
            "action": "evaluate",
            "public_score": 0.65,
            "private_score": 0.70,
            "sample_count": 100,
            "reward_hacking_risk": 0.1,
            "recommendation": "hold",
            "reason": "ok",
        }
        et, sub, payload = _map_record_to_event(record)
        assert et == "strategy_eval"
        assert sub == "v9_baseline"
        assert payload["public_score"] == 0.65

    def test_noop(self) -> None:
        record = {"timestamp": "2026-01-01T10:00:00", "action": "evaluate_only"}
        et, sub, payload = _map_record_to_event(record)
        assert et == "noop"
        assert sub == "orchestrator"

    def test_strategy_eval_requires_sample_count_gt_zero(self) -> None:
        record = {
            "timestamp": "2026-01-01T10:00:00",
            "action": "evaluate",
            "public_score": 0.65,
            "private_score": 0.70,
            "sample_count": 0,
        }
        et, _, _ = _map_record_to_event(record)
        assert et == "noop"

    def test_regime_takes_precedence_over_strategy_eval(self) -> None:
        record = {
            "timestamp": "2026-01-01T10:00:00",
            "action": "evaluate",
            "public_score": 0.65,
            "private_score": 0.70,
            "sample_count": 100,
            "evaluator_report": {
                "vol_regime_suggestion": {"indicators": {}, "regime": {}}
            },
        }
        et, _, _ = _map_record_to_event(record)
        assert et == "regime_shift_suggestion"


class TestEclEventSink:
    """EclEventSink flag 控制 + 静默降级."""

    def test_flag_off_returns_true_noop(self, tmp_path: Path) -> None:
        sink = EclEventSink(db_path=tmp_path / "ecl.db")
        with patch(
            "utils.infra.feature_flags.FeatureFlags.is_enabled", return_value=False
        ):
            result = sink.write(
                {"timestamp": "2026-01-01T10:00:00", "action": "evaluate"}
            )
        assert result is True

    def test_flag_on_writes_event(self, tmp_path: Path) -> None:
        sink = EclEventSink(db_path=tmp_path / "ecl.db")
        record = {
            "timestamp": "2026-01-01T10:00:00",
            "action": "evaluate",
            "public_score": 0.65,
            "private_score": 0.70,
            "sample_count": 100,
            "recommendation": "hold",
            "reason": "ok",
            "reward_hacking_risk": 0.1,
        }
        with patch(
            "utils.infra.feature_flags.FeatureFlags.is_enabled", return_value=True
        ):
            result = sink.write(record)
        assert result is True
        assert sink._store is not None
        assert sink._store.count() == 1

    def test_sink_failure_silent_degradation(self, tmp_path: Path) -> None:
        sink = EclEventSink(db_path=tmp_path / "ecl.db")
        with patch(
            "utils.infra.feature_flags.FeatureFlags.is_enabled", return_value=True
        ):
            with patch.object(
                sink, "_get_store", side_effect=RuntimeError("db locked")
            ):
                result = sink.write({"timestamp": "2026-01-01T10:00:00"})
        assert result is False

    def test_sink_never_raises(self, tmp_path: Path) -> None:
        sink = EclEventSink(db_path=tmp_path / "ecl.db")
        with patch(
            "utils.infra.feature_flags.FeatureFlags.is_enabled",
            side_effect=Exception("boom"),
        ):
            result = sink.write({"timestamp": "2026-01-01T10:00:00"})
        assert result is True


class TestSinkRegistry:
    """全局注册表."""

    def setup_method(self) -> None:
        clear_sinks()

    def teardown_method(self) -> None:
        clear_sinks()

    def test_register_and_iter(self) -> None:
        sink = EclEventSink(db_path=Path("data/test_ecl.db"))
        register_sink(sink)
        assert sink in iter_registered_sinks()

    def test_ensure_default_sink_idempotent(self) -> None:
        s1 = ensure_default_sink()
        s2 = ensure_default_sink()
        assert s1 is s2

    def test_iter_returns_snapshot(self) -> None:
        sink = EclEventSink(db_path=Path("data/test_ecl.db"))
        register_sink(sink)
        sinks1 = iter_registered_sinks()
        clear_sinks()
        assert len(sinks1) == 1
        assert len(iter_registered_sinks()) == 0
