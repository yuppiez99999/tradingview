"""alpha_evaluator 单元测试 — Alpha 因子验证闭环全分支覆盖"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.alpha_evaluator import (  # noqa: E402
    AlphaEvaluationReport,
    AlphaEvaluator,
    FactorEvaluation,
)


class FakeFactorValue:
    def __init__(self, values, category="test"):
        self.values = values
        self.category = category


class FakeFactorLibraryResult:
    def __init__(self, factors):
        self.factors = factors


def _make_factors(names, n_symbols=10, category="momentum"):
    factors = {}
    for name in names:
        np.random.seed(hash(name) % 2**32)
        values = {f"S{i:03d}": float(np.random.randn()) for i in range(n_symbols)}
        factors[name] = FakeFactorValue(values, category)
    return factors


def _make_forward_returns(n_symbols=10, seed=99):
    np.random.seed(seed)
    return {f"S{i:03d}": float(np.random.randn() * 0.01) for i in range(n_symbols)}


@pytest.fixture
def isolated_history(tmp_path, monkeypatch):
    history_file = tmp_path / "factor_history.jsonl"
    monkeypatch.setattr(AlphaEvaluator, "HISTORY_FILE", history_file)
    return history_file


@pytest.fixture
def evaluator(tmp_path, isolated_history):
    return AlphaEvaluator(report_dir=tmp_path)


class TestFactorEvaluationDataclass:
    def test_defaults(self):
        e = FactorEvaluation(factor_name="MOM", category="momentum")
        assert e.factor_name == "MOM"
        assert e.category == "momentum"
        assert e.ic_1d == 0.0
        assert e.ic_5d == 0.0
        assert e.ic_20d == 0.0
        assert e.ic_ir == 0.0
        assert e.turnover == 0.0
        assert e.decay_score == 0.0
        assert e.status == "active"
        assert e.last_update == ""

    def test_custom(self):
        e = FactorEvaluation(
            factor_name="X",
            category="c",
            ic_1d=0.1,
            ic_ir=0.5,
            status="degraded",
        )
        assert e.ic_1d == 0.1
        assert e.ic_ir == 0.5
        assert e.status == "degraded"


class TestAlphaEvaluationReportToDict:
    def test_to_dict(self):
        r = AlphaEvaluationReport(
            report_date="2026-08-14",
            total_factors=3,
            active_factors=2,
            degraded_factors=1,
            dead_factors=0,
            evaluations=[{"factor_name": "A"}],
            summary="active=2, degraded=1, dead=0",
        )
        d = r.to_dict()
        assert d["report_date"] == "2026-08-14"
        assert d["total_factors"] == 3
        assert d["active_factors"] == 2
        assert d["degraded_factors"] == 1
        assert d["dead_factors"] == 0
        assert d["evaluations"] == [{"factor_name": "A"}]
        assert d["summary"] == "active=2, degraded=1, dead=0"

    def test_to_dict_defaults(self):
        r = AlphaEvaluationReport(report_date="2026-08-14")
        d = r.to_dict()
        assert d["total_factors"] == 0
        assert d["evaluations"] == []


class TestAlphaEvaluatorInit:
    def test_creates_report_dir(self, tmp_path, isolated_history):
        report_dir = tmp_path / "alpha_reports"
        e = AlphaEvaluator(report_dir=report_dir)
        assert report_dir.exists()
        assert e.report_dir == report_dir

    def test_default_report_dir(self, isolated_history, monkeypatch):
        from utils.alpha_evaluator import REPORT_DIR
        e = AlphaEvaluator()
        assert e.report_dir == REPORT_DIR

    def test_loads_empty_history(self, evaluator):
        assert evaluator._history == {}


class TestEvaluateAllBasic:
    def test_empty_factors(self, evaluator):
        result = evaluator.evaluate_all(FakeFactorLibraryResult({}))
        assert result.total_factors == 0
        assert result.active_factors == 0
        assert result.degraded_factors == 0
        assert result.dead_factors == 0
        assert result.evaluations == []

    def test_factor_with_empty_values_skipped(self, evaluator):
        factors = {"EMPTY": FakeFactorValue({}, "test")}
        result = evaluator.evaluate_all(FakeFactorLibraryResult(factors))
        assert result.total_factors == 0

    def test_multiple_factors(self, evaluator):
        factors = _make_factors(["MOM_20D", "REV_5D", "VOL_10D"])
        forward = _make_forward_returns()
        result = evaluator.evaluate_all(FakeFactorLibraryResult(factors), forward)
        assert result.total_factors == 3
        assert result.active_factors + result.degraded_factors + result.dead_factors == 3

    def test_no_forward_returns(self, evaluator):
        factors = _make_factors(["MOM_20D"])
        result = evaluator.evaluate_all(FakeFactorLibraryResult(factors))
        assert result.total_factors == 1
        assert result.evaluations[0]["ic_1d"] == 0.0

    def test_report_saved(self, evaluator):
        factors = _make_factors(["MOM_20D"])
        forward = _make_forward_returns()
        result = evaluator.evaluate_all(FakeFactorLibraryResult(factors), forward)
        report_path = evaluator.report_dir / result.report_date / "alpha_evaluation.json"
        assert report_path.exists()
        with open(report_path, encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["report_date"] == result.report_date
        assert saved["total_factors"] == 1

    def test_history_appended(self, evaluator):
        factors = _make_factors(["MOM_20D"])
        forward = _make_forward_returns()
        evaluator.evaluate_all(FakeFactorLibraryResult(factors), forward)
        assert "MOM_20D" in evaluator._history
        assert len(evaluator._history["MOM_20D"]) == 1


class TestComputeIcs:
    def test_normal_correlation(self, evaluator):
        factor_values = {f"S{i:03d}": float(i) for i in range(10)}
        forward = {f"S{i:03d}": float(i) * 0.01 for i in range(10)}
        ic_1d, ic_5d, ic_20d = evaluator._compute_ics(factor_values, forward)
        assert ic_1d > 0.99
        assert ic_5d > 0.99
        assert ic_20d > 0.99

    def test_less_than_five_common(self, evaluator):
        factor_values = {f"S{i:03d}": float(i) for i in range(4)}
        forward = {f"S{i:03d}": float(i) for i in range(4)}
        assert evaluator._compute_ics(factor_values, forward) == (0.0, 0.0, 0.0)

    def test_zero_std_factor(self, evaluator):
        factor_values = {f"S{i:03d}": 1.0 for i in range(10)}
        forward = {f"S{i:03d}": float(i) for i in range(10)}
        assert evaluator._compute_ics(factor_values, forward) == (0.0, 0.0, 0.0)

    def test_zero_std_returns(self, evaluator):
        factor_values = {f"S{i:03d}": float(i) for i in range(10)}
        forward = {f"S{i:03d}": 1.0 for i in range(10)}
        assert evaluator._compute_ics(factor_values, forward) == (0.0, 0.0, 0.0)

    def test_non_finite_factor_values_excluded(self, evaluator):
        factor_values = {f"S{i:03d}": float(i) for i in range(10)}
        factor_values["S000"] = float("inf")
        forward = {f"S{i:03d}": float(i) for i in range(10)}
        ic_1d, _, _ = evaluator._compute_ics(factor_values, forward)
        assert math.isfinite(ic_1d)

    def test_negative_correlation(self, evaluator):
        factor_values = {f"S{i:03d}": float(i) for i in range(10)}
        forward = {f"S{i:03d}": -float(i) for i in range(10)}
        ic_1d, _, _ = evaluator._compute_ics(factor_values, forward)
        assert ic_1d < -0.99


class TestComputeIcIr:
    def test_less_than_five_history(self, evaluator):
        evaluator._history["X"] = [{"ic_1d": 0.1}]
        assert evaluator._compute_ic_ir("X", 0.2) == 0.2

    def test_no_history(self, evaluator):
        assert evaluator._compute_ic_ir("X", 0.3) == 0.3

    def test_five_history_zero_std(self, evaluator):
        evaluator._history["X"] = [{"ic_1d": 0.1}] * 5
        assert evaluator._compute_ic_ir("X", 0.1) == 0.0

    def test_five_history_normal(self, evaluator):
        evaluator._history["X"] = [
            {"ic_1d": 0.1},
            {"ic_1d": 0.2},
            {"ic_1d": 0.3},
            {"ic_1d": 0.4},
            {"ic_1d": 0.5},
        ]
        result = evaluator._compute_ic_ir("X", 0.3)
        assert result > 0


class TestComputeTurnover:
    def test_less_than_two_history(self, evaluator):
        evaluator._history["X"] = [{"ic_1d": 0.1}]
        assert evaluator._compute_turnover("X", {}) == 0.0

    def test_no_history(self, evaluator):
        assert evaluator._compute_turnover("X", {}) == 0.0

    def test_two_history(self, evaluator):
        evaluator._history["X"] = [{"ic_1d": 0.1}, {"ic_1d": 0.2}]
        assert evaluator._compute_turnover("X", {}) == 0.25


class TestDecayCheck:
    def test_less_than_ten_history(self, evaluator):
        evaluator._history["X"] = [{"ic_1d": 0.1}] * 5
        assert evaluator._decay_check("X", 0.1) == 0.0

    def test_no_history(self, evaluator):
        assert evaluator._decay_check("X", 0.1) == 0.0

    def test_ten_history_normal(self, evaluator):
        evaluator._history["X"] = [{"ic_1d": 0.1 + i * 0.01} for i in range(10)]
        result = evaluator._decay_check("X", 0.1)
        assert 0.0 <= result <= 1.0

    def test_avg_abs_near_zero(self, evaluator):
        evaluator._history["X"] = [{"ic_1d": 0.0}] * 10
        assert evaluator._decay_check("X", 0.1) == 1.0

    def test_ic_equal_avg(self, evaluator):
        evaluator._history["X"] = [{"ic_1d": 0.1}] * 10 + [{"ic_1d": 0.1}]
        result = evaluator._decay_check("X", 0.1)
        assert abs(result) < 1e-9


class TestClassifyStatus:
    def test_active(self, evaluator):
        assert evaluator._classify_status(0.5, 0.1) == "active"

    def test_dead_low_ic_ir(self, evaluator):
        assert evaluator._classify_status(-0.1, 0.1) == "dead"

    def test_dead_high_decay(self, evaluator):
        assert evaluator._classify_status(0.5, 0.9) == "dead"

    def test_degraded_low_ic_ir(self, evaluator):
        assert evaluator._classify_status(0.2, 0.1) == "degraded"

    def test_degraded_high_decay(self, evaluator):
        assert evaluator._classify_status(0.5, 0.7) == "degraded"

    def test_boundary_ic_ir_dead(self, evaluator):
        assert evaluator._classify_status(0.0, 0.1) == "degraded"

    def test_boundary_decay_dead(self, evaluator):
        assert evaluator._classify_status(0.5, 0.86) == "dead"

    def test_boundary_decay_dead_exact_085_is_degraded(self, evaluator):
        assert evaluator._classify_status(0.5, 0.85) == "degraded"

    def test_boundary_decay_degraded(self, evaluator):
        assert evaluator._classify_status(0.5, 0.61) == "degraded"

    def test_boundary_decay_degraded_exact_06_is_active(self, evaluator):
        assert evaluator._classify_status(0.5, 0.6) == "active"

    def test_boundary_ic_ir_alive(self, evaluator):
        assert evaluator._classify_status(0.3, 0.1) == "active"


class TestAppendHistory:
    def test_append_new_factor(self, evaluator):
        evaluator._append_history("X", {"ic_1d": 0.1})
        assert evaluator._history["X"] == [{"ic_1d": 0.1}]

    def test_append_existing_factor(self, evaluator):
        evaluator._append_history("X", {"ic_1d": 0.1})
        evaluator._append_history("X", {"ic_1d": 0.2})
        assert len(evaluator._history["X"]) == 2

    def test_trim_to_120(self, evaluator):
        for i in range(130):
            evaluator._append_history("X", {"ic_1d": float(i)})
        assert len(evaluator._history["X"]) == 120
        assert evaluator._history["X"][0]["ic_1d"] == 10.0
        assert evaluator._history["X"][-1]["ic_1d"] == 129.0


class TestLoadHistory:
    def test_file_not_exists(self, tmp_path, monkeypatch):
        monkeypatch.setattr(AlphaEvaluator, "HISTORY_FILE", tmp_path / "no_exist.jsonl")
        e = AlphaEvaluator(report_dir=tmp_path)
        assert e._history == {}

    def test_loads_existing_file(self, tmp_path, monkeypatch):
        history_file = tmp_path / "factor_history.jsonl"
        records = [
            {"factor_name": "A", "ic_1d": 0.1},
            {"factor_name": "A", "ic_1d": 0.2},
            {"factor_name": "B", "ic_1d": 0.3},
        ]
        with open(history_file, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        monkeypatch.setattr(AlphaEvaluator, "HISTORY_FILE", history_file)
        e = AlphaEvaluator(report_dir=tmp_path)
        assert len(e._history["A"]) == 2
        assert len(e._history["B"]) == 1

    def test_skips_blank_lines(self, tmp_path, monkeypatch):
        history_file = tmp_path / "factor_history.jsonl"
        with open(history_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({"factor_name": "A", "ic_1d": 0.1}) + "\n")
            f.write("\n")
            f.write(json.dumps({"factor_name": "B", "ic_1d": 0.2}) + "\n")
        monkeypatch.setattr(AlphaEvaluator, "HISTORY_FILE", history_file)
        e = AlphaEvaluator(report_dir=tmp_path)
        assert "A" in e._history
        assert "B" in e._history

    def test_skips_no_factor_name(self, tmp_path, monkeypatch):
        history_file = tmp_path / "factor_history.jsonl"
        with open(history_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({"ic_1d": 0.1}) + "\n")
        monkeypatch.setattr(AlphaEvaluator, "HISTORY_FILE", history_file)
        e = AlphaEvaluator(report_dir=tmp_path)
        assert e._history == {}

    def test_corrupt_file_fail_safe(self, tmp_path, monkeypatch):
        history_file = tmp_path / "factor_history.jsonl"
        with open(history_file, "w", encoding="utf-8") as f:
            f.write("not valid json\n")
        monkeypatch.setattr(AlphaEvaluator, "HISTORY_FILE", history_file)
        e = AlphaEvaluator(report_dir=tmp_path)
        assert e._history == {}


class TestSaveReport:
    def test_save_success(self, evaluator):
        report = AlphaEvaluationReport(
            report_date="2026-08-14",
            total_factors=1,
            evaluations=[{"factor_name": "A"}],
            summary="test",
        )
        evaluator._save_report(report)
        path = evaluator.report_dir / "2026-08-14" / "alpha_evaluation.json"
        assert path.exists()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["total_factors"] == 1

    def test_save_failure_fail_safe(self, tmp_path, isolated_history, monkeypatch):
        e = AlphaEvaluator(report_dir=tmp_path)

        def raise_open(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("builtins.open", raise_open)
        report = AlphaEvaluationReport(report_date="2026-08-14")
        e._save_report(report)


class TestBuildSummary:
    def test_summary_format(self, evaluator):
        assert evaluator._build_summary(3, 2, 1) == "active=3, degraded=2, dead=1"

    def test_summary_zeros(self, evaluator):
        assert evaluator._build_summary(0, 0, 0) == "active=0, degraded=0, dead=0"


class TestEvaluateAllStatusClassification:
    def test_dead_factor_via_low_ic_ir(self, evaluator):
        np.random.seed(1)
        factor_values = {f"S{i:03d}": float(np.random.randn()) for i in range(10)}
        forward = {k: -v for k, v in factor_values.items()}
        factors = {"BAD": FakeFactorValue(factor_values, "test")}
        result = evaluator.evaluate_all(FakeFactorLibraryResult(factors), forward)
        assert result.total_factors == 1

    def test_history_accumulation_across_runs(self, evaluator):
        factors = _make_factors(["MOM"])
        forward = _make_forward_returns()
        evaluator.evaluate_all(FakeFactorLibraryResult(factors), forward)
        evaluator.evaluate_all(FakeFactorLibraryResult(factors), forward)
        assert len(evaluator._history["MOM"]) == 2


class TestEvaluateAllFiniteGuards:
    def test_inf_ic_guard(self, evaluator, monkeypatch):
        monkeypatch.setattr(evaluator, "_compute_ics", lambda *a: (float("inf"), 0.0, 0.0))
        factors = {"X": FakeFactorValue({f"S{i}": 1.0 for i in range(10)}, "t")}
        result = evaluator.evaluate_all(FakeFactorLibraryResult(factors), {})
        assert result.evaluations[0]["ic_1d"] == 0.0

    def test_nan_ic_guard(self, evaluator, monkeypatch):
        monkeypatch.setattr(evaluator, "_compute_ics", lambda *a: (float("nan"), 0.0, 0.0))
        factors = {"X": FakeFactorValue({f"S{i}": 1.0 for i in range(10)}, "t")}
        result = evaluator.evaluate_all(FakeFactorLibraryResult(factors), {})
        assert result.evaluations[0]["ic_1d"] == 0.0

    def test_inf_ic_ir_guard(self, evaluator, monkeypatch):
        monkeypatch.setattr(evaluator, "_compute_ics", lambda *a: (0.1, 0.0, 0.0))
        monkeypatch.setattr(evaluator, "_compute_ic_ir", lambda *a: float("inf"))
        factors = {"X": FakeFactorValue({f"S{i}": 1.0 for i in range(10)}, "t")}
        result = evaluator.evaluate_all(FakeFactorLibraryResult(factors), {})
        assert result.evaluations[0]["ic_ir"] == 0.0

    def test_nan_turnover_guard(self, evaluator, monkeypatch):
        monkeypatch.setattr(evaluator, "_compute_ics", lambda *a: (0.1, 0.0, 0.0))
        monkeypatch.setattr(evaluator, "_compute_turnover", lambda *a: float("nan"))
        factors = {"X": FakeFactorValue({f"S{i}": 1.0 for i in range(10)}, "t")}
        result = evaluator.evaluate_all(FakeFactorLibraryResult(factors), {})
        assert result.evaluations[0]["turnover"] == 0.0

    def test_inf_decay_guard(self, evaluator, monkeypatch):
        monkeypatch.setattr(evaluator, "_compute_ics", lambda *a: (0.1, 0.0, 0.0))
        monkeypatch.setattr(evaluator, "_decay_check", lambda *a: float("inf"))
        factors = {"X": FakeFactorValue({f"S{i}": 1.0 for i in range(10)}, "t")}
        result = evaluator.evaluate_all(FakeFactorLibraryResult(factors), {})
        assert result.evaluations[0]["decay_score"] == 0.0


class TestEvaluateAllWithHistory:
    def test_ic_ir_with_sufficient_history(self, tmp_path, isolated_history):
        history_file = tmp_path / "factor_history.jsonl"
        records = [
            {"factor_name": "MOM", "ic_1d": 0.05 + i * 0.01, "ic_ir": 0.4}
            for i in range(6)
        ]
        with open(history_file, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        e = AlphaEvaluator(report_dir=tmp_path)
        factors = _make_factors(["MOM"])
        forward = _make_forward_returns()
        result = e.evaluate_all(FakeFactorLibraryResult(factors), forward)
        assert result.total_factors == 1

    def test_turnover_with_sufficient_history(self, tmp_path, isolated_history):
        history_file = tmp_path / "factor_history.jsonl"
        records = [
            {"factor_name": "MOM", "ic_1d": 0.1},
            {"factor_name": "MOM", "ic_1d": 0.2},
        ]
        with open(history_file, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        e = AlphaEvaluator(report_dir=tmp_path)
        factors = _make_factors(["MOM"])
        forward = _make_forward_returns()
        result = e.evaluate_all(FakeFactorLibraryResult(factors), forward)
        assert result.evaluations[0]["turnover"] == 0.25

    def test_decay_with_sufficient_history(self, tmp_path, isolated_history):
        history_file = tmp_path / "factor_history.jsonl"
        records = [
            {"factor_name": "MOM", "ic_1d": 0.1} for _ in range(10)
        ]
        with open(history_file, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        e = AlphaEvaluator(report_dir=tmp_path)
        factors = _make_factors(["MOM"])
        forward = _make_forward_returns()
        result = e.evaluate_all(FakeFactorLibraryResult(factors), forward)
        assert result.evaluations[0]["decay_score"] >= 0.0
