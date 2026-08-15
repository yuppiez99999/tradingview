"""Data Cleaning Pipeline 单元测试.

被测模块: utils/pipeline/data_cleaning.py
覆盖目标: >=85%
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.pipeline.data_cleaning import DataCleaningPipeline  # noqa: E402
from utils.pipeline.types import DataQualityReport, PipelineStage  # noqa: E402


class TestDetectGaps:
    def test_empty(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        assert pipe._detect_gaps(np.array([])) == 0

    def test_no_gaps(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        assert pipe._detect_gaps(np.array([1.0, 2.0, 3.0])) == 0

    def test_single_gap(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        assert pipe._detect_gaps(np.array([1.0, np.nan, 3.0])) == 1

    def test_multiple_consecutive_gaps(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        assert pipe._detect_gaps(np.array([1.0, np.nan, np.nan, np.nan, 5.0])) == 3

    def test_multiple_separate_gaps(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        assert pipe._detect_gaps(np.array([1.0, np.nan, 3.0, np.nan, np.nan, 6.0])) == 2


class TestCalcQualityScore:
    def test_perfect_score(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        result = pipe._calc_quality_score("A", {}, {}, {}, {})
        assert result["score"] == 100.0

    def test_multi_source_penalty(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        result = pipe._calc_quality_score("A", {"A": {"deviation_pct": 2.0}}, {}, {}, {})
        assert result["score"] < 100.0
        assert "multi_source_penalty" in result["meta"]

    def test_outlier_penalty(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        result = pipe._calc_quality_score("A", {}, {"A": ["flag1"]}, {}, {})
        assert result["score"] == 90.0

    def test_gap_penalty(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        result = pipe._calc_quality_score("A", {}, {}, {"A": {"gap_days": 3}}, {})
        assert result["score"] < 100.0

    def test_gate_penalty(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        result = pipe._calc_quality_score("A", {}, {}, {}, {"A": {"allowed": False}})
        assert result["score"] == 70.0

    def test_all_penalties(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        result = pipe._calc_quality_score(
            "A",
            {"A": {"deviation_pct": 5.0}},
            {"A": ["f1", "f2"]},
            {"A": {"gap_days": 5}},
            {"A": {"allowed": False}},
        )
        assert result["score"] < 100.0
        assert result["score"] >= 0

    def test_score_floor(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        result = pipe._calc_quality_score(
            "A",
            {"A": {"deviation_pct": 100.0}},
            {"A": ["f1"] * 20},
            {"A": {"gap_days": 100}},
            {"A": {"allowed": False}},
        )
        assert result["score"] >= 0


class TestExtractPrice:
    def test_price_key(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        assert pipe._extract_price({"price": 10.5}) == 10.5

    def test_current_key(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        assert pipe._extract_price({"current": 20.0}) == 20.0

    def test_close_key(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        assert pipe._extract_price({"close": 30.0}) == 30.0

    def test_last_price_key(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        assert pipe._extract_price({"last_price": 40.0}) == 40.0

    def test_priority_order(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        assert pipe._extract_price({"price": 1.0, "close": 2.0}) == 1.0

    def test_no_price(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        assert pipe._extract_price({"volume": 100}) is None

    def test_not_dict(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        assert pipe._extract_price("not a dict") is None

    def test_empty_dict(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        assert pipe._extract_price({}) is None


class TestCheckGate:
    def test_no_data_gate(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        pipe._data_gate = None
        assert pipe._check_gate({"A": {}}) == {}

    def test_normal_check(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        mock_gate = MagicMock()
        mock_gate.check_and_gate = MagicMock(return_value=MagicMock(
            allowed=True, quality_score=95.0, reasons=[]
        ))
        pipe._data_gate = mock_gate
        result = pipe._check_gate({"A": {"close": 10}})
        assert result["A"]["allowed"] is True
        assert result["A"]["quality_score"] == 95.0

    def test_gate_exception(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        mock_gate = MagicMock()
        mock_gate.check_and_gate = MagicMock(side_effect=RuntimeError("fail"))
        pipe._data_gate = mock_gate
        result = pipe._check_gate({"A": {"close": 10}})
        assert result == {}


class TestFillGaps:
    def test_no_history(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        with patch.object(pipe, "_get_price_history", return_value=[]):
            result = pipe._fill_gaps({"A": {}})
        assert result == {}

    def test_normal_history(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        with patch.object(pipe, "_get_price_history", return_value=[1.0, 2.0, 3.0]):
            result = pipe._fill_gaps({"A": {}})
        assert "A" in result
        assert result["A"]["n_nan"] == 0
        assert result["A"]["gap_days"] == 0

    def test_with_nan(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        with patch.object(pipe, "_get_price_history", return_value=[1.0, np.nan, 3.0]):
            result = pipe._fill_gaps({"A": {}})
        assert result["A"]["n_nan"] == 1
        assert result["A"]["gap_days"] == 1


class TestRun:
    def test_empty_data(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        pipe.config = MagicMock(report_dir="reports/pipeline")
        pipe._quality_monitor = None
        pipe._data_gate = None
        pipe._report_dir = Path("reports/pipeline")
        with patch.object(pipe, "_load_market_data", return_value={}):
            reports, result = pipe.run(market_data={}, save_report=False)
        assert reports == []
        assert result.success is True

    def test_with_data(self):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        pipe.config = MagicMock(report_dir="reports/pipeline")
        pipe._quality_monitor = None
        pipe._data_gate = None
        pipe._report_dir = Path("reports/pipeline")
        data = {
            "A": {"close": 10.0, "high": 11.0, "low": 9.0, "volume": 1000},
            "B": {"close": 20.0, "high": 21.0, "low": 19.0, "volume": 2000},
        }
        with patch.object(pipe, "_validate_multi_source", return_value={}), \
             patch.object(pipe, "_detect_outliers", return_value={}), \
             patch.object(pipe, "_fill_gaps", return_value={}), \
             patch.object(pipe, "_check_gate", return_value={}), \
             patch.object(pipe, "_save_reports", return_value=[]):
            reports, result = pipe.run(market_data=data, save_report=False)
        assert result.stage == PipelineStage.DATA_CLEANING


class TestSaveReports:
    def test_save_success(self, tmp_path):
        pipe = DataCleaningPipeline.__new__(DataCleaningPipeline)
        pipe._report_dir = tmp_path
        report = DataQualityReport(
            symbol="A", quality_score=95.0, passed=True,
            outlier_flags=[], gap_days=0, multi_source_deviation_pct=0.0,
        )
        paths = pipe._save_reports([report])
        assert len(paths) >= 1
        assert any(p.endswith(".json") for p in paths)
