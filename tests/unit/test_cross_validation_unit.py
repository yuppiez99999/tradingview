"""cross_validation 单元测试 — 多源交叉校验全分支覆盖

被测模块: utils/data/cross_validation.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.data.cross_validation import (
    CrossSourceValidator,
    ValidationResult,
)  # noqa: E402

_NAN = float("nan")


class TestValidationResultStr:
    def test_str_ok(self):
        r = ValidationResult(
            symbol="600519",
            ok=True,
            median=1700.5,
            max_rel_deviation=0.001,
            message="交叉校验通过",
        )
        s = str(r)
        assert "[600519]" in s
        assert "ok=True" in s
        assert "median=1700.5000" in s
        assert "max_dev=0.1000%" in s
        assert "交叉校验通过" in s

    def test_str_fail(self):
        r = ValidationResult(
            symbol="000001",
            ok=False,
            median=10.0,
            max_rel_deviation=0.5,
            message="bad",
        )
        s = str(r)
        assert "ok=False" in s
        assert "max_dev=50.0000%" in s
        assert "bad" in s

    def test_str_defaults(self):
        r = ValidationResult(symbol="X", ok=True)
        s = str(r)
        assert "median=0.0000" in s
        assert "max_dev=0.0000%" in s


class TestCheckFiltering:
    def test_none_filtering(self):
        v = CrossSourceValidator(min_sources=2)
        r = v.check("S1", {"a": 100.0, "b": 100.0, "c": None})
        assert r.ok is True
        assert "c" not in r.sources
        assert len(r.sources) == 2

    def test_nan_filtering(self):
        v = CrossSourceValidator(min_sources=2)
        r = v.check("S1", {"a": 100.0, "b": 100.0, "c": _NAN})
        assert r.ok is True
        assert "c" not in r.sources

    def test_zero_filtering(self):
        v = CrossSourceValidator(min_sources=2)
        r = v.check("S1", {"a": 100.0, "b": 100.0, "c": 0.0})
        assert r.ok is True
        assert "c" not in r.sources

    def test_all_invalid_sources_insufficient(self):
        v = CrossSourceValidator(min_sources=2)
        r = v.check("S1", {"a": None, "b": 0, "c": _NAN})
        assert r.ok is True
        assert "源数不足" in r.message
        assert len(r.sources) == 0


class TestCheckSkip:
    def test_insufficient_sources_skip(self):
        v = CrossSourceValidator(max_rel_deviation=0.01, min_sources=2)
        r = v.check("S1", {"wind": 100.0})
        assert r.ok is True
        assert r.median == 0.0
        assert "源数不足" in r.message
        assert r.max_rel_deviation == 0.0

    def test_insufficient_after_filtering(self):
        v = CrossSourceValidator(min_sources=2)
        r = v.check("S1", {"a": 100.0, "b": None, "c": 0, "d": _NAN})
        assert r.ok is True
        assert "源数不足" in r.message

    def test_min_sources_three(self):
        v = CrossSourceValidator(max_rel_deviation=0.01, min_sources=3)
        r = v.check("S1", {"a": 100.0, "b": 100.0})
        assert r.ok is True
        assert "源数不足" in r.message


class TestCheckMedian:
    def test_odd_median_three_sources(self):
        v = CrossSourceValidator(min_sources=2)
        r = v.check("S1", {"a": 100.0, "b": 300.0, "c": 200.0})
        assert r.median == 200.0

    def test_even_median_two_sources(self):
        v = CrossSourceValidator(min_sources=2)
        r = v.check("S1", {"a": 100.0, "b": 200.0})
        assert r.median == 150.0

    def test_even_median_four_sources(self):
        v = CrossSourceValidator(min_sources=2)
        r = v.check("S1", {"a": 100.0, "b": 200.0, "c": 300.0, "d": 400.0})
        assert r.median == 250.0


class TestCheckDeviation:
    def test_no_deviation(self):
        v = CrossSourceValidator(max_rel_deviation=0.01, min_sources=2)
        r = v.check("S1", {"wind": 100.0, "tdx": 100.0})
        assert r.ok is True
        assert r.median == 100.0
        assert r.max_rel_deviation == 0.0
        assert r.message == "交叉校验通过"

    def test_deviation_within_threshold(self):
        v = CrossSourceValidator(max_rel_deviation=0.01, min_sources=2)
        r = v.check("S1", {"wind": 100.0, "tdx": 100.5})
        assert r.ok is True
        assert r.max_rel_deviation == pytest.approx(0.5 / 100.5)
        assert r.worst_pair == ("wind", "tdx")

    def test_deviation_exceeds_threshold(self):
        v = CrossSourceValidator(max_rel_deviation=0.01, min_sources=2)
        r = v.check("S1", {"wind": 100.0, "tdx": 110.0})
        assert r.ok is False
        assert r.max_rel_deviation == pytest.approx(10.0 / 110.0)
        assert "多源偏离超阈值" in r.message

    def test_worst_pair_three_sources(self):
        v = CrossSourceValidator(max_rel_deviation=0.4, min_sources=2)
        r = v.check("S1", {"a": 100.0, "b": 110.0, "c": 200.0})
        assert r.ok is False
        assert r.max_rel_deviation == pytest.approx(0.5)
        assert set(r.worst_pair) == {"a", "c"}

    def test_negative_values(self):
        v = CrossSourceValidator(max_rel_deviation=0.01, min_sources=2)
        r = v.check("S1", {"a": -100.0, "b": -100.0})
        assert r.ok is True
        assert r.median == -100.0

    def test_negative_values_deviation(self):
        v = CrossSourceValidator(max_rel_deviation=0.01, min_sources=2)
        r = v.check("S1", {"a": -100.0, "b": -110.0})
        assert r.ok is False
        assert r.max_rel_deviation == pytest.approx(10.0 / 110.0)


class TestCheckBatch:
    def test_batch_empty(self):
        v = CrossSourceValidator()
        assert v.check_batch({}) == {}

    def test_batch_multiple(self):
        v = CrossSourceValidator(max_rel_deviation=0.01, min_sources=2)
        batch = {
            "S1": {"a": 100.0, "b": 100.0},
            "S2": {"a": 100.0, "b": 110.0},
            "S3": {"a": 100.0},
        }
        result = v.check_batch(batch)
        assert set(result.keys()) == {"S1", "S2", "S3"}
        assert result["S1"].ok is True
        assert result["S2"].ok is False
        assert result["S3"].ok is True
        assert "源数不足" in result["S3"].message

    def test_batch_preserves_symbols(self):
        v = CrossSourceValidator(min_sources=2)
        batch = {"AAA": {"a": 1.0, "b": 1.0}, "BBB": {"a": 2.0, "b": 2.0}}
        result = v.check_batch(batch)
        assert result["AAA"].median == 1.0
        assert result["BBB"].median == 2.0
