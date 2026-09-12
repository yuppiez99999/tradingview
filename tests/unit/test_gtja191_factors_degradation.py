"""SC-5 回归 (2026-09-11 代码质量审查): GTJA191 死链必须显式降级.

审查结论 (P2-3):
    ``utils/gtja191_factors.py`` 的所有方法都直接调用 ``self._adapter`` 的动态因子
    接口 (list_factors / compute_single_stock / compute_one_factor), 而
    ``VibeTradingAdapter`` **并未实现**这些方法 —— 运行期抛出的
    ``AttributeError: 'VibeTradingAdapter' object has no attribute ...``
    被包装成「未知异常」, 降级原因不可追溯。

修复后契约:
    - 构造期探测能力: ``available`` / ``missing_adapter_methods`` / ``unavailable_reason``
    - 计算类方法 (compute / compute_series / factor_ids / list_by_theme):
      能力不足时抛 :class:`GTJA191UnavailableError` (决策路径 fail-close)
    - 观测类方法 (get_formula / get_info): 返回空值并在内容里带明确原因 (fail-open)
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.gtja191_factors import (  # noqa: E402
    GTJA191Factors,
    GTJA191UnavailableError,
)


class _BareAdapter:
    """模拟当前 VibeTradingAdapter: 无任何动态因子接口."""


class _FullAdapter:
    """模拟接口齐备的适配器."""

    def list_factors(self, zoo=None, theme=None):
        return ["gtja191_001", "gtja191_005"]

    def compute_single_stock(self, df, factor_ids=None, zoo=None):
        return SimpleNamespace(values={"gtja191_001": 1.23})

    def compute_one_factor(self, df, alpha_id):
        return pd.Series([1.0, 2.0])

    def get_meta(self, alpha_id):
        return SimpleNamespace(
            formula="CORR(...)",
            alpha_id=alpha_id,
            themes=["momentum"],
            columns_required=["close"],
            min_warmup_bars=6,
            decay_horizon=3,
            notes="",
        )


def _factor_calc(adapter, monkeypatch) -> GTJA191Factors:
    monkeypatch.setattr("utils.gtja191_factors.get_adapter", lambda *a, **k: adapter)
    return GTJA191Factors()


class TestCapabilityProbe:
    def test_missing_methods_are_detected(self, monkeypatch):
        calc = _factor_calc(_BareAdapter(), monkeypatch)

        assert calc.available is False
        assert set(calc.missing_adapter_methods) == {
            "list_factors",
            "compute_single_stock",
            "compute_one_factor",
        }
        assert "list_factors" in calc.unavailable_reason

    def test_full_adapter_is_available(self, monkeypatch):
        calc = _factor_calc(_FullAdapter(), monkeypatch)

        assert calc.available is True
        assert calc.missing_adapter_methods == []
        assert calc.unavailable_reason == ""

    def test_real_adapter_capability_is_reported(self):
        """真实默认适配器的能力必须如实报告 (二值, 不允许含糊)。"""
        calc = GTJA191Factors()

        assert isinstance(calc.available, bool)
        if not calc.available:
            assert calc.unavailable_reason


class TestFailCloseOnCompute:
    """SC-5 核心: 计算类方法必须抛 GTJA191UnavailableError, 而不是 AttributeError。"""

    def test_compute_raises_typed_error(self, monkeypatch):
        calc = _factor_calc(_BareAdapter(), monkeypatch)
        df = pd.DataFrame({"close": [1.0, 2.0, 3.0], "volume": [10, 20, 30]})

        with pytest.raises(GTJA191UnavailableError) as exc:
            calc.compute(df)

        assert "未实现" in str(exc.value)
        # 修复前此处抛的是 AttributeError (未绑定到能力缺失语义)
        assert not isinstance(exc.value, AttributeError)

    def test_compute_series_raises_typed_error(self, monkeypatch):
        calc = _factor_calc(_BareAdapter(), monkeypatch)
        df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})

        with pytest.raises(GTJA191UnavailableError):
            calc.compute_series(df, "gtja191_001")

    def test_factor_ids_raises_typed_error(self, monkeypatch):
        calc = _factor_calc(_BareAdapter(), monkeypatch)

        with pytest.raises(GTJA191UnavailableError):
            _ = calc.factor_ids

    def test_list_by_theme_raises_typed_error(self, monkeypatch):
        calc = _factor_calc(_BareAdapter(), monkeypatch)

        with pytest.raises(GTJA191UnavailableError):
            calc.list_by_theme("momentum")


class TestFailOpenOnObservation:
    """观测路径: 不可用时返回空值 + 明确原因, 不抛异常。"""

    def test_get_formula_returns_empty(self, monkeypatch):
        calc = _factor_calc(_BareAdapter(), monkeypatch)

        assert calc.get_formula("gtja191_001") == ""

    def test_get_info_returns_reason(self, monkeypatch):
        calc = _factor_calc(_BareAdapter(), monkeypatch)

        info = calc.get_info("gtja191_001")

        assert "error" in info
        assert "未实现" in info["error"]


class TestFullAdapterStillWorks:
    def test_compute_returns_values(self, monkeypatch):
        calc = _factor_calc(_FullAdapter(), monkeypatch)
        df = pd.DataFrame({"close": [1.0, 2.0, 3.0], "volume": [10, 20, 30]})

        assert calc.compute(df) == {"gtja191_001": 1.23}
        assert calc.factor_ids == ["gtja191_001", "gtja191_005"]
        assert calc.get_formula("gtja191_001") == "CORR(...)"

    def test_empty_df_returns_empty_without_error(self, monkeypatch):
        calc = _factor_calc(_BareAdapter(), monkeypatch)

        assert calc.compute(pd.DataFrame()) == {}
