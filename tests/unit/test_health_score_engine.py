"""Health Score 聚合引擎单测 (Production Edition T2, 2026-09-02)."""
from __future__ import annotations

import json
from pathlib import Path

from utils.health.score_engine import (
    DEGRADED_NEUTRAL,
    WEIGHTS,
    DimensionScore,
    score_model,
    status_for,
)


class TestEngineSkeleton:
    def test_weights_sum_to_one(self):
        assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9
        assert set(WEIGHTS) == {"model", "data", "trading", "risk", "capital"}

    def test_status_thresholds(self):
        assert status_for(85.0) == "GREEN"
        assert status_for(100.0) == "GREEN"
        assert status_for(84.9) == "YELLOW"
        assert status_for(70.0) == "YELLOW"
        assert status_for(69.9) == "RED"
        assert status_for(0.0) == "RED"

    def test_degraded_neutral_is_60(self):
        assert DEGRADED_NEUTRAL == 60.0

    def test_dimension_score_fields(self):
        d = DimensionScore(score=80.0, weight=0.2, degraded=True, detail={"k": 1})
        assert d.score == 80.0
        assert d.weight == 0.2
        assert d.degraded is True
        assert d.detail == {"k": 1}


def _write_drift(root: Path, date: str, payload: dict) -> None:
    d = root / "reports" / "drift"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"integration_{date}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


class TestScoreModel:
    DATE = "2026-09-01"

    def _good(self, **over):
        p = {
            "date": self.DATE,
            "skipped": False,
            "error": None,
            "ic_degradation": 0.1,
            "alerts": [],
            "delayed_metrics": {"ic": 0.05, "rank_ic": 0.06, "ic_ir": 0.8},
        }
        p.update(over)
        return p

    def test_missing_file_degraded(self, tmp_path):
        d = score_model(tmp_path, self.DATE)
        assert d.degraded is True
        assert d.score == 60.0
        assert d.weight == 0.25

    def test_low_degradation_no_alerts_full_score(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good())
        d = score_model(tmp_path, self.DATE)
        assert d.degraded is False
        assert d.score == 100.0
        assert d.detail["ic"] == 0.05

    def test_medium_degradation(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good(ic_degradation=0.5))
        assert score_model(tmp_path, self.DATE).score == 85.0

    def test_high_degradation(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good(ic_degradation=0.88))
        assert score_model(tmp_path, self.DATE).score == 70.0

    def test_alerts_deduct_with_floor(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good(alerts=["a", "b", "c"]))
        assert score_model(tmp_path, self.DATE).score == 70.0  # 100-30

    def test_alerts_floor_zero(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good(alerts=[str(i) for i in range(12)]))
        assert score_model(tmp_path, self.DATE).score == 0.0

    def test_skipped_is_degraded(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good(skipped=True))
        d = score_model(tmp_path, self.DATE)
        assert d.degraded is True
        assert d.score == 60.0

    def test_error_is_degraded(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good(error="boom"))
        assert score_model(tmp_path, self.DATE).degraded is True

    def test_corrupt_json_is_degraded(self, tmp_path):
        d = tmp_path / "reports" / "drift"
        d.mkdir(parents=True)
        (d / f"integration_{self.DATE}.json").write_text("{bad", encoding="utf-8")
        assert score_model(tmp_path, self.DATE).degraded is True
