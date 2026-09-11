"""test_risk_thresholds_unit.py — 风控阈值单一事实源 (Issue #13: S-1/S-2/P1-3)

覆盖要点:
    - 四段阈值从 config/risk_thresholds.yaml 正常解析
    - 文件缺失 → 安全默认 + from_file=False (fail-open 不抛异常)
    - 段级缺键 → 逐键补默认并记录 missing_keys
    - 类型强制 (int/float/bool)
    - 未知段 → KeyError (编程错误显式失败)
    - 与消费方默认值同值 (防漂移)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from utils import risk_thresholds
from utils.risk_thresholds import (
    DEFAULT_L2_EXECUTION,
    DEFAULT_STOP_LOSS,
    describe_sources,
    get_default_portfolio_value,
    get_factor_validation_config,
    get_max_single_pct,
    get_portfolio_protection_config,
    get_stop_loss_config,
    get_stop_loss_pct,
    get_take_profit_pct,
    resolve_config,
)


class TestResolveConfig:
    @pytest.mark.unit
    def test_all_sections_from_file(self):
        """四段阈值均应来自版本库内的唯一事实源。"""
        sources = describe_sources()
        assert set(sources) == {
            "stop_loss",
            "portfolio_protection",
            "l2_execution",
            "factor_validation",
        }
        for section, desc in sources.items():
            assert "config/risk_thresholds.yaml" in desc, (
                f"{section} 未从单一事实源读取: {desc}"
            )

    @pytest.mark.unit
    def test_missing_file_falls_back_to_defaults(self):
        """配置不可用 → 安全默认 + from_file=False, 不抛异常 (fail-open)。"""
        with patch.object(risk_thresholds, "get_config", return_value={}):
            cfg, source = resolve_config("stop_loss")
        assert cfg == DEFAULT_STOP_LOSS
        assert source.from_file is False
        assert set(source.missing_keys) == set(DEFAULT_STOP_LOSS)

    @pytest.mark.unit
    def test_partial_section_fills_missing_keys(self):
        """段内缺键 → 逐键补默认并记录 missing_keys。"""
        partial = {"stop_loss": {"stop_loss_pct": 0.10}}
        with patch.object(risk_thresholds, "get_config", return_value=partial):
            cfg, source = resolve_config("stop_loss")
        assert cfg["stop_loss_pct"] == 0.10
        assert cfg["take_profit_pct"] == DEFAULT_STOP_LOSS["take_profit_pct"]
        assert "take_profit_pct" in source.missing_keys
        assert "stop_loss_pct" not in source.missing_keys

    @pytest.mark.unit
    def test_type_coercion(self):
        """YAML 常见 str 数值应被强制为目标类型。"""
        raw = {"l2_execution": {"max_single_pct": "0.07", "default_portfolio_value": 3e6}}
        with patch.object(risk_thresholds, "get_config", return_value=raw):
            cfg, _ = resolve_config("l2_execution")
        assert cfg["max_single_pct"] == pytest.approx(0.07)
        assert isinstance(cfg["max_single_pct"], float)
        assert cfg["default_portfolio_value"] == 3_000_000

    @pytest.mark.unit
    def test_invalid_value_keeps_default(self):
        """不可解析的值 → 保留默认并告警, 不抛异常。"""
        raw = {"l2_execution": {"max_single_pct": "not-a-number"}}
        with patch.object(risk_thresholds, "get_config", return_value=raw):
            cfg, _ = resolve_config("l2_execution")
        assert cfg["max_single_pct"] == DEFAULT_L2_EXECUTION["max_single_pct"]

    @pytest.mark.unit
    def test_unknown_section_raises(self):
        """未知段属编程错误, 应显式失败。"""
        with pytest.raises(KeyError):
            resolve_config("no_such_section")

    @pytest.mark.unit
    def test_bool_coercion(self):
        raw = {"stop_loss": {"block_on_trigger": 0}}
        with patch.object(risk_thresholds, "get_config", return_value=raw):
            cfg, _ = resolve_config("stop_loss")
        assert cfg["block_on_trigger"] is False


class TestTypedAccessors:
    @pytest.mark.unit
    def test_stop_loss_accessors(self):
        assert get_stop_loss_pct() == get_stop_loss_config()["stop_loss_pct"]
        assert get_take_profit_pct() == get_stop_loss_config()["take_profit_pct"]
        # S-1 口径: 止损 8% / 止盈 15% (与熔断线 3%/5% 同源可审计)
        assert get_stop_loss_pct() == pytest.approx(0.08)
        assert get_take_profit_pct() == pytest.approx(0.15)

    @pytest.mark.unit
    def test_l2_accessors(self):
        # S-2 口径: 真实组合 200 万, 单笔 5%
        assert get_max_single_pct() == pytest.approx(0.05)
        assert get_default_portfolio_value() == pytest.approx(2_000_000.0)

    @pytest.mark.unit
    def test_portfolio_protection_defaults(self):
        cfg = get_portfolio_protection_config()
        assert cfg["initial_drawdown_stop_pct"] == pytest.approx(0.05)
        assert cfg["initial_min_samples"] >= 1

    @pytest.mark.unit
    def test_factor_validation_defaults(self):
        cfg = get_factor_validation_config()
        assert cfg["min_samples"] == 60
        assert cfg["ic_effective_threshold"] == pytest.approx(0.03)
        assert cfg["ir_effective_threshold"] == pytest.approx(0.5)
        assert cfg["score_mode"] == "signed"


class TestConsumerAlignment:
    """消费方默认值与配置同值 (防两处漂移)。"""

    @pytest.mark.unit
    def test_factor_validator_aligned_with_config(self):
        from utils.factor_research.factor_discovery import FactorValidator

        cfg = get_factor_validation_config()
        assert FactorValidator.MIN_SAMPLES == cfg["min_samples"]
        assert (
            FactorValidator.IC_EFFECTIVE_THRESHOLD == cfg["ic_effective_threshold"]
        )
        assert FactorValidator.IR_EFFECTIVE_THRESHOLD == cfg["ir_effective_threshold"]
        assert FactorValidator.SCORE_MODE == cfg["score_mode"]

    @pytest.mark.unit
    def test_defaults_cover_all_sections(self):
        for section, defaults in risk_thresholds._DEFAULTS_BY_SECTION.items():
            cfg, _ = resolve_config(section)
            assert set(cfg) == set(defaults), f"{section} 默认键集不一致"
