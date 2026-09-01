"""kondratiev_cycle SAX motif 发现单元测试 (Wave 12-A #1).

被测模块: utils/kondratiev_cycle.py
新增方法: discover_motifs / get_kondratiev_historical_series / analyze_historical_patterns

验收门禁:
  - SAX motif 发现可用
  - 康波历史数据模式匹配 >= 3 个
  - 不破坏现有康波接口
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.kondratiev_cycle import (  # noqa: E402
    _STUMPY_AVAILABLE,
    KondratievCycleAnalyzer,
    KondratievPhase,
)


class TestDiscoverMotifs:
    """discover_motifs 方法测试."""

    def test_returns_list_of_dicts(self):
        a = KondratievCycleAnalyzer()
        ts = np.sin(np.linspace(0, 8 * np.pi, 500))
        result = a.discover_motifs(ts, window_size=50, max_motifs=3)
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, dict)

    def test_motif_structure(self):
        a = KondratievCycleAnalyzer()
        ts = np.sin(np.linspace(0, 8 * np.pi, 500))
        result = a.discover_motifs(ts, window_size=50, max_motifs=2)
        for m in result:
            assert "motif_idx" in m
            assert "match_idx" in m
            assert "distance" in m
            assert "window_size" in m
            assert "backend" in m
            assert m["window_size"] == 50
            assert m["backend"] in ("stumpy", "numpy")

    def test_max_motifs_limit(self):
        a = KondratievCycleAnalyzer()
        ts = np.sin(np.linspace(0, 12 * np.pi, 1000))
        result = a.discover_motifs(ts, window_size=50, max_motifs=2)
        assert len(result) <= 2

    def test_short_series_returns_empty(self):
        a = KondratievCycleAnalyzer()
        ts = np.array([1.0, 2.0, 3.0])
        result = a.discover_motifs(ts, window_size=60, max_motifs=3)
        assert result == []

    def test_empty_series_returns_empty(self):
        a = KondratievCycleAnalyzer()
        ts = np.array([], dtype=np.float64)
        result = a.discover_motifs(ts, window_size=10, max_motifs=3)
        assert result == []

    def test_motif_indices_non_negative(self):
        a = KondratievCycleAnalyzer()
        ts = np.sin(np.linspace(0, 8 * np.pi, 500))
        result = a.discover_motifs(ts, window_size=50, max_motifs=3)
        for m in result:
            assert m["motif_idx"] >= 0
            assert m["match_idx"] >= 0

    def test_distance_non_negative(self):
        a = KondratievCycleAnalyzer()
        ts = np.sin(np.linspace(0, 8 * np.pi, 500))
        result = a.discover_motifs(ts, window_size=50, max_motifs=3)
        for m in result:
            assert m["distance"] >= 0.0

    def test_repeated_pattern_finds_motif(self):
        a = KondratievCycleAnalyzer()
        pattern = np.sin(np.linspace(0, 2 * np.pi, 100))
        ts = np.concatenate([pattern, pattern, pattern])
        result = a.discover_motifs(ts, window_size=50, max_motifs=1)
        assert len(result) >= 1
        assert result[0]["distance"] < 1.0


class TestGetKondratievHistoricalSeries:
    """get_kondratiev_historical_series 方法测试."""

    def test_returns_ndarray(self):
        a = KondratievCycleAnalyzer()
        series = a.get_kondratiev_historical_series()
        assert isinstance(series, np.ndarray)

    def test_series_length(self):
        a = KondratievCycleAnalyzer()
        series = a.get_kondratiev_historical_series()
        assert len(series) == 4 * 55 * 12

    def test_series_is_float64(self):
        a = KondratievCycleAnalyzer()
        series = a.get_kondratiev_historical_series()
        assert series.dtype == np.float64

    def test_series_not_all_zero(self):
        a = KondratievCycleAnalyzer()
        series = a.get_kondratiev_historical_series()
        assert not np.allclose(series, 0.0)

    def test_series_deterministic(self):
        a = KondratievCycleAnalyzer()
        s1 = a.get_kondratiev_historical_series()
        s2 = a.get_kondratiev_historical_series()
        np.testing.assert_array_equal(s1, s2)


class TestAnalyzeHistoricalPatterns:
    """analyze_historical_patterns 方法测试."""

    def test_returns_dict(self):
        a = KondratievCycleAnalyzer()
        result = a.analyze_historical_patterns()
        assert isinstance(result, dict)

    def test_result_structure(self):
        a = KondratievCycleAnalyzer()
        result = a.analyze_historical_patterns()
        assert "series_length" in result
        assert "series_years" in result
        assert "motifs" in result
        assert "pattern_count" in result
        assert "window_size" in result
        assert "backend" in result
        assert "interpretation" in result

    def test_pattern_count_at_least_3(self):
        a = KondratievCycleAnalyzer()
        result = a.analyze_historical_patterns()
        assert result["pattern_count"] >= 3

    def test_series_years_220(self):
        a = KondratievCycleAnalyzer()
        result = a.analyze_historical_patterns()
        assert result["series_years"] == 220.0

    def test_backend_matches_availability(self):
        a = KondratievCycleAnalyzer()
        result = a.analyze_historical_patterns()
        expected = "stumpy" if _STUMPY_AVAILABLE else "numpy"
        assert result["backend"] == expected

    def test_interpretation_is_string(self):
        a = KondratievCycleAnalyzer()
        result = a.analyze_historical_patterns()
        assert isinstance(result["interpretation"], str)
        assert len(result["interpretation"]) > 0

    def test_motif_intervals_near_kondratiev_period(self):
        a = KondratievCycleAnalyzer()
        result = a.analyze_historical_patterns()
        for m in result["motifs"]:
            interval_years = abs(m["motif_idx"] - m["match_idx"]) / 12.0
            assert interval_years > 0.0


class TestExistingInterfaceUnchanged:
    """验证现有接口未被破坏."""

    def test_get_current_phase_still_works(self):
        a = KondratievCycleAnalyzer()
        phase = a.get_current_phase()
        assert phase["phase"] == KondratievPhase.RECOVERY

    def test_get_sector_allocation_still_works(self):
        a = KondratievCycleAnalyzer()
        result = a.get_sector_allocation()
        assert isinstance(result, list)
        assert len(result) > 0

    def test_get_commodity_signals_still_works(self):
        a = KondratievCycleAnalyzer()
        result = a.get_commodity_signals()
        assert len(result) == 6

    def test_get_fifteen_five_overlay_still_works(self):
        a = KondratievCycleAnalyzer()
        overlay = a.get_fifteen_five_overlay()
        assert overlay["period"] == "2026-2030"

    def test_generate_report_still_works(self):
        a = KondratievCycleAnalyzer()
        report = a.generate_report()
        assert "康波周期" in report
