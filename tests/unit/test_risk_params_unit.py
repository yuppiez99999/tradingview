"""test_risk_params_unit.py — 风险参数统一访问层单元测试

覆盖要点:
    - get_max_drawdown_limit (正常/越界/解析失败/fail-safe)
    - get_quant_neutral_max_drawdown (同上)
    - get_daily_amount_limit / get_price_protection_pct / get_daily_loss_stop_pct / get_portfolio_drawdown_stop_pct
    - ConfigManager 不可用时回退到兜底常量
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from utils.risk_params import (
    _FALLBACK_DAILY_AMOUNT_LIMIT,
    _FALLBACK_DAILY_LOSS_STOP_PCT,
    _FALLBACK_MAX_DRAWDOWN_LIMIT,
    _FALLBACK_PORTFOLIO_DRAWDOWN_STOP_PCT,
    _FALLBACK_PRICE_PROTECTION_PCT,
    _FALLBACK_QUANT_NEUTRAL_MAX_DRAWDOWN,
    get_daily_amount_limit,
    get_daily_loss_stop_pct,
    get_max_drawdown_limit,
    get_portfolio_drawdown_stop_pct,
    get_price_protection_pct,
    get_quant_neutral_max_drawdown,
)


# ============================================================
# fail-safe: ConfigManager 不可用
# ============================================================


class TestFallback:
    @pytest.mark.unit
    def test_cm_connection_error_returns_fallback(self, monkeypatch):
        """ConfigManager 抛 ConnectionError → 返回兜底常量"""
        mock_cm = MagicMock()
        mock_cm.get_risk_params_config = MagicMock(side_effect=ConnectionError("net down"))
        monkeypatch.setitem(sys.modules, "utils.config_manager", mock_cm)
        assert get_max_drawdown_limit() == _FALLBACK_MAX_DRAWDOWN_LIMIT
        assert get_quant_neutral_max_drawdown() == _FALLBACK_QUANT_NEUTRAL_MAX_DRAWDOWN
        assert get_daily_amount_limit() == _FALLBACK_DAILY_AMOUNT_LIMIT
        assert get_price_protection_pct() == _FALLBACK_PRICE_PROTECTION_PCT
        assert get_daily_loss_stop_pct() == _FALLBACK_DAILY_LOSS_STOP_PCT
        assert get_portfolio_drawdown_stop_pct() == _FALLBACK_PORTFOLIO_DRAWDOWN_STOP_PCT

    @pytest.mark.unit
    def test_cm_exception_returns_fallback(self, monkeypatch):
        """ConfigManager 抛异常 → 返回兜底常量"""
        mock_cm = MagicMock()
        mock_cm.get_risk_params_config = MagicMock(side_effect=RuntimeError("down"))
        monkeypatch.setitem(sys.modules, "utils.config_manager", mock_cm)
        assert get_max_drawdown_limit() == _FALLBACK_MAX_DRAWDOWN_LIMIT

    @pytest.mark.unit
    def test_cm_returns_non_dict(self, monkeypatch):
        """ConfigManager 返回非 dict → 回退"""
        mock_cm = MagicMock()
        mock_cm.get_risk_params_config = MagicMock(return_value="not a dict")
        monkeypatch.setitem(sys.modules, "utils.config_manager", mock_cm)
        assert get_max_drawdown_limit() == _FALLBACK_MAX_DRAWDOWN_LIMIT


# ============================================================
# get_max_drawdown_limit
# ============================================================


class TestGetMaxDrawdownLimit:
    @pytest.mark.unit
    def test_normal_value(self, monkeypatch):
        mock_cm = MagicMock()
        mock_cm.get_risk_params_config = MagicMock(
            return_value={"max_drawdown_limit": 0.20}
        )
        monkeypatch.setitem(sys.modules, "utils.config_manager", mock_cm)
        assert get_max_drawdown_limit() == 0.20

    @pytest.mark.unit
    def test_out_of_range_high(self, monkeypatch):
        mock_cm = MagicMock()
        mock_cm.get_risk_params_config = MagicMock(
            return_value={"max_drawdown_limit": 0.60}  # > 0.50
        )
        monkeypatch.setitem(sys.modules, "utils.config_manager", mock_cm)
        assert get_max_drawdown_limit() == _FALLBACK_MAX_DRAWDOWN_LIMIT

    @pytest.mark.unit
    def test_out_of_range_low(self, monkeypatch):
        mock_cm = MagicMock()
        mock_cm.get_risk_params_config = MagicMock(
            return_value={"max_drawdown_limit": 0.005}  # < 0.01
        )
        monkeypatch.setitem(sys.modules, "utils.config_manager", mock_cm)
        assert get_max_drawdown_limit() == _FALLBACK_MAX_DRAWDOWN_LIMIT

    @pytest.mark.unit
    def test_invalid_type(self, monkeypatch):
        mock_cm = MagicMock()
        mock_cm.get_risk_params_config = MagicMock(
            return_value={"max_drawdown_limit": "abc"}
        )
        monkeypatch.setitem(sys.modules, "utils.config_manager", mock_cm)
        assert get_max_drawdown_limit() == _FALLBACK_MAX_DRAWDOWN_LIMIT

    @pytest.mark.unit
    def test_missing_key(self, monkeypatch):
        mock_cm = MagicMock()
        mock_cm.get_risk_params_config = MagicMock(return_value={})
        monkeypatch.setitem(sys.modules, "utils.config_manager", mock_cm)
        assert get_max_drawdown_limit() == _FALLBACK_MAX_DRAWDOWN_LIMIT


# ============================================================
# get_quant_neutral_max_drawdown
# ============================================================


class TestGetQuantNeutralMaxDrawdown:
    @pytest.mark.unit
    def test_normal_value(self, monkeypatch):
        mock_cm = MagicMock()
        mock_cm.get_risk_params_config = MagicMock(
            return_value={"quant_neutral_max_drawdown": 0.06}
        )
        monkeypatch.setitem(sys.modules, "utils.config_manager", mock_cm)
        assert get_quant_neutral_max_drawdown() == 0.06

    @pytest.mark.unit
    def test_out_of_range(self, monkeypatch):
        mock_cm = MagicMock()
        mock_cm.get_risk_params_config = MagicMock(
            return_value={"quant_neutral_max_drawdown": 0.80}
        )
        monkeypatch.setitem(sys.modules, "utils.config_manager", mock_cm)
        assert get_quant_neutral_max_drawdown() == _FALLBACK_QUANT_NEUTRAL_MAX_DRAWDOWN

    @pytest.mark.unit
    def test_invalid_type(self, monkeypatch):
        mock_cm = MagicMock()
        mock_cm.get_risk_params_config = MagicMock(
            return_value={"quant_neutral_max_drawdown": None}
        )
        monkeypatch.setitem(sys.modules, "utils.config_manager", mock_cm)
        assert get_quant_neutral_max_drawdown() == _FALLBACK_QUANT_NEUTRAL_MAX_DRAWDOWN


# ============================================================
# 其他 getter
# ============================================================


class TestOtherGetters:
    @pytest.mark.unit
    def test_daily_amount_limit(self, monkeypatch):
        mock_cm = MagicMock()
        mock_cm.get_risk_params_config = MagicMock(
            return_value={"daily_amount_limit": 500000}
        )
        monkeypatch.setitem(sys.modules, "utils.config_manager", mock_cm)
        assert get_daily_amount_limit() == 500000

    @pytest.mark.unit
    def test_daily_amount_limit_invalid(self, monkeypatch):
        mock_cm = MagicMock()
        mock_cm.get_risk_params_config = MagicMock(
            return_value={"daily_amount_limit": "abc"}
        )
        monkeypatch.setitem(sys.modules, "utils.config_manager", mock_cm)
        assert get_daily_amount_limit() == _FALLBACK_DAILY_AMOUNT_LIMIT

    @pytest.mark.unit
    def test_price_protection_pct(self, monkeypatch):
        mock_cm = MagicMock()
        mock_cm.get_risk_params_config = MagicMock(
            return_value={"price_protection_pct": 0.05}
        )
        monkeypatch.setitem(sys.modules, "utils.config_manager", mock_cm)
        assert get_price_protection_pct() == 0.05

    @pytest.mark.unit
    def test_daily_loss_stop_pct(self, monkeypatch):
        mock_cm = MagicMock()
        mock_cm.get_risk_params_config = MagicMock(
            return_value={"daily_loss_stop_pct": 0.04}
        )
        monkeypatch.setitem(sys.modules, "utils.config_manager", mock_cm)
        assert get_daily_loss_stop_pct() == 0.04

    @pytest.mark.unit
    def test_portfolio_drawdown_stop_pct(self, monkeypatch):
        mock_cm = MagicMock()
        mock_cm.get_risk_params_config = MagicMock(
            return_value={"portfolio_drawdown_stop_pct": 0.07}
        )
        monkeypatch.setitem(sys.modules, "utils.config_manager", mock_cm)
        assert get_portfolio_drawdown_stop_pct() == 0.07