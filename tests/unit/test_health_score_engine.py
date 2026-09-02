"""Health Score 聚合引擎单测 (Production Edition T2, 2026-09-02)."""
from __future__ import annotations

import json
from pathlib import Path

from utils.health.score_engine import (
    DEGRADED_NEUTRAL,
    WEIGHTS,
    DimensionScore,
    score_data,
    score_model,
    score_risk,
    score_trading,
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


def _write_degradation_log(root: Path, records: list[dict]) -> None:
    d = root / "reports"
    d.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    (d / "degradation_log.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _deg(ts: str, scope: str = "config_manager") -> dict:
    return {"ts": ts, "scope": scope, "key": "k", "default": "d", "reason": "r"}


class TestScoreData:
    DATE = "2026-09-01"

    def test_missing_log_degraded(self, tmp_path):
        d = score_data(tmp_path, self.DATE)
        assert d.degraded is True
        assert d.score == 60.0
        assert d.weight == 0.20

    def test_zero_entries_full_score(self, tmp_path):
        _write_degradation_log(tmp_path, [])
        d = score_data(tmp_path, self.DATE)
        assert d.degraded is False
        assert d.score == 100.0

    def test_other_dates_ignored(self, tmp_path):
        _write_degradation_log(tmp_path, [_deg("2026-08-31T10:00:00")])
        assert score_data(tmp_path, self.DATE).score == 100.0

    def test_one_or_two_entries_80(self, tmp_path):
        _write_degradation_log(
            tmp_path,
            [_deg(f"{self.DATE}T10:00:00"), _deg(f"{self.DATE}T11:00:00")],
        )
        assert score_data(tmp_path, self.DATE).score == 80.0

    def test_three_to_five_entries_60(self, tmp_path):
        _write_degradation_log(
            tmp_path,
            [_deg(f"{self.DATE}T10:0{i}:00") for i in range(3)],
        )
        assert score_data(tmp_path, self.DATE).score == 60.0

    def test_six_plus_entries_40(self, tmp_path):
        _write_degradation_log(
            tmp_path,
            [_deg(f"{self.DATE}T10:0{i}:00") for i in range(6)],
        )
        assert score_data(tmp_path, self.DATE).score == 40.0

    def test_detail_has_scopes(self, tmp_path):
        _write_degradation_log(
            tmp_path, [_deg(f"{self.DATE}T10:00:00", scope="z_mod"), _deg(f"{self.DATE}T10:01:00", scope="a_mod")]
        )
        d = score_data(tmp_path, self.DATE)
        assert d.detail["entries"] == 2
        assert d.detail["scopes"] == ["a_mod", "z_mod"]

    def test_corrupt_lines_skipped(self, tmp_path):
        d = tmp_path / "reports"
        d.mkdir(parents=True)
        (d / "degradation_log.jsonl").write_text(
            "{bad\n" + json.dumps(_deg(f"{self.DATE}T10:00:00")) + "\n", encoding="utf-8"
        )
        assert score_data(tmp_path, self.DATE).score == 80.0


def _write_tca(root: Path, date: str, records: list[dict]) -> None:
    d = root / "reports" / "tca"
    d.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    (d / f"fills_{date}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fill(estimate) -> dict:
    return {
        "type": "fill",
        "fill": {"symbol": "510300.SH", "side": "BUY", "shares": 100},
        "estimate": estimate,
    }


class TestScoreTrading:
    DATE = "2026-09-01"

    def test_missing_file_degraded(self, tmp_path):
        d = score_trading(tmp_path, self.DATE)
        assert d.degraded is True
        assert d.score == 60.0
        assert d.weight == 0.15

    def test_no_fills_degraded(self, tmp_path):
        _write_tca(tmp_path, self.DATE, [])
        assert score_trading(tmp_path, self.DATE).degraded is True

    def test_full_estimate_coverage(self, tmp_path):
        _write_tca(
            tmp_path, self.DATE,
            [_fill({"cost_bps": 5.0}), _fill({"cost_bps": 6.0})],
        )
        d = score_trading(tmp_path, self.DATE)
        assert d.score == 100.0
        assert d.detail == {"fills": 2, "estimate_coverage": 1.0}

    def test_zero_estimate_coverage(self, tmp_path):
        _write_tca(tmp_path, self.DATE, [_fill(None), _fill(None)])
        assert score_trading(tmp_path, self.DATE).score == 70.0

    def test_mixed_coverage_below_half(self, tmp_path):
        _write_tca(tmp_path, self.DATE, [_fill({"cost_bps": 5.0}), _fill(None), _fill(None)])
        d = score_trading(tmp_path, self.DATE)
        assert d.score == 70.0
        assert d.detail["estimate_coverage"] == round(1 / 3, 4)


def _write_vol_regime(root: Path, date: str, payload: dict) -> None:
    d = root / "reports" / "evolution"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"vol_regime_weights_{date}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _vol(label: str, **over) -> dict:
    p = {
        "regime": {"label": label, "confidence": 0.85},
        "degraded": False,
        "observation_phase": False,
    }
    p.update(over)
    return p


class TestScoreRisk:
    DATE = "2026-09-01"

    def test_missing_file_degraded(self, tmp_path):
        d = score_risk(tmp_path, self.DATE)
        assert d.degraded is True
        assert d.score == 60.0
        assert d.weight == 0.20

    def test_bull_100(self, tmp_path):
        _write_vol_regime(tmp_path, self.DATE, _vol("bull"))
        assert score_risk(tmp_path, self.DATE).score == 100.0

    def test_bear_80(self, tmp_path):
        _write_vol_regime(tmp_path, self.DATE, _vol("bear"))
        assert score_risk(tmp_path, self.DATE).score == 80.0

    def test_sideways_95(self, tmp_path):
        _write_vol_regime(tmp_path, self.DATE, _vol("sideways"))
        assert score_risk(tmp_path, self.DATE).score == 95.0

    def test_unknown_label_90(self, tmp_path):
        _write_vol_regime(tmp_path, self.DATE, _vol("turbulent"))
        assert score_risk(tmp_path, self.DATE).score == 90.0

    def test_regime_degraded_flag_50(self, tmp_path):
        _write_vol_regime(tmp_path, self.DATE, _vol("bull", degraded=True))
        assert score_risk(tmp_path, self.DATE).score == 50.0

    def test_corrupt_json_degraded(self, tmp_path):
        d = tmp_path / "reports" / "evolution"
        d.mkdir(parents=True)
        (d / f"vol_regime_weights_{self.DATE}.json").write_text("{bad", encoding="utf-8")
        assert score_risk(tmp_path, self.DATE).degraded is True
