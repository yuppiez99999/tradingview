"""test_trading_env_unit.py — 交易环境配置单元测试

覆盖要点:
    - TradingEnv.is_valid / level
    - get_trading_env (环境变量/默认)
    - get_trading_env_config (production/shadow/development)
    - assert_production_fail_closed (production 抛/development 不抛)
    - TradingEnvConfig.__str__
"""
from __future__ import annotations

import pytest

from utils.trading_env import (
    TradingEnv,
    assert_production_fail_closed,
    get_trading_env,
    get_trading_env_config,
)

# ============================================================
# TradingEnv 枚举
# ============================================================


class TestTradingEnv:
    @pytest.mark.unit
    def test_is_valid_production(self):
        assert TradingEnv.is_valid("production") is True

    @pytest.mark.unit
    def test_is_valid_shadow(self):
        assert TradingEnv.is_valid("shadow") is True

    @pytest.mark.unit
    def test_is_valid_development(self):
        assert TradingEnv.is_valid("development") is True

    @pytest.mark.unit
    def test_is_valid_invalid(self):
        assert TradingEnv.is_valid("staging") is False
        assert TradingEnv.is_valid("") is False

    @pytest.mark.unit
    def test_level(self):
        assert TradingEnv.level("development") == 0
        assert TradingEnv.level("shadow") == 1
        assert TradingEnv.level("production") == 2
        assert TradingEnv.level("unknown") == 0


# ============================================================
# get_trading_env
# ============================================================


class TestGetTradingEnv:
    @pytest.mark.unit
    def test_env_var_production(self, monkeypatch):
        monkeypatch.setenv("TRADING_ENV", "production")
        assert get_trading_env() == "production"

    @pytest.mark.unit
    def test_env_var_shadow(self, monkeypatch):
        monkeypatch.setenv("TRADING_ENV", "shadow")
        assert get_trading_env() == "shadow"

    @pytest.mark.unit
    def test_env_var_development(self, monkeypatch):
        monkeypatch.setenv("TRADING_ENV", "development")
        assert get_trading_env() == "development"

    @pytest.mark.unit
    def test_env_var_uppercase(self, monkeypatch):
        monkeypatch.setenv("TRADING_ENV", "PRODUCTION")
        assert get_trading_env() == "production"

    @pytest.mark.unit
    def test_env_var_invalid_falls_back(self, monkeypatch):
        """无效环境变量 → 回退到 .env 或默认"""
        monkeypatch.setenv("TRADING_ENV", "staging")
        # 可能回退到 .env 文件中的值或默认 development
        result = get_trading_env()
        assert TradingEnv.is_valid(result)

    @pytest.mark.unit
    def test_no_env_var(self, monkeypatch):
        """无环境变量 → 回退到 .env 或默认 development"""
        monkeypatch.delenv("TRADING_ENV", raising=False)
        result = get_trading_env()
        assert TradingEnv.is_valid(result)


# ============================================================
# get_trading_env_config
# ============================================================


class TestGetTradingEnvConfig:
    @pytest.mark.unit
    def test_production_config(self, monkeypatch):
        monkeypatch.setenv("TRADING_ENV", "production")
        cfg = get_trading_env_config()
        assert cfg.env == "production"
        assert cfg.is_prod is True
        assert cfg.is_shadow is False
        assert cfg.is_dev is False
        assert cfg.fail_closed is True
        assert cfg.allow_real_orders is True
        assert cfg.shadow_capital_pct == 1.0

    @pytest.mark.unit
    def test_shadow_config(self, monkeypatch):
        monkeypatch.setenv("TRADING_ENV", "shadow")
        cfg = get_trading_env_config()
        assert cfg.env == "shadow"
        assert cfg.is_prod is False
        assert cfg.is_shadow is True
        assert cfg.fail_closed is True
        assert cfg.allow_real_orders is False
        assert cfg.shadow_capital_pct == 0.1

    @pytest.mark.unit
    def test_development_config(self, monkeypatch):
        monkeypatch.setenv("TRADING_ENV", "development")
        cfg = get_trading_env_config()
        assert cfg.env == "development"
        assert cfg.is_dev is True
        assert cfg.fail_closed is False
        assert cfg.allow_real_orders is False
        assert cfg.shadow_capital_pct == 0.0

    @pytest.mark.unit
    def test_config_str(self, monkeypatch):
        monkeypatch.setenv("TRADING_ENV", "production")
        cfg = get_trading_env_config()
        s = str(cfg)
        assert "production" in s
        assert "fail_closed" in s


# ============================================================
# assert_production_fail_closed
# ============================================================


class TestAssertFailClosed:
    @pytest.mark.unit
    def test_production_raises(self, monkeypatch):
        """production 环境 → 抛 RuntimeError"""
        monkeypatch.setenv("TRADING_ENV", "production")
        with pytest.raises(RuntimeError, match="FAIL-CLOSED"):
            assert_production_fail_closed(ValueError("test error"), "test context")

    @pytest.mark.unit
    def test_shadow_raises(self, monkeypatch):
        """shadow 环境 → 抛 RuntimeError (fail_closed=True)"""
        monkeypatch.setenv("TRADING_ENV", "shadow")
        with pytest.raises(RuntimeError, match="FAIL-CLOSED"):
            assert_production_fail_closed(ValueError("test error"), "test context")

    @pytest.mark.unit
    def test_development_no_raise(self, monkeypatch):
        """development 环境 → 不抛异常 (fail-open)"""
        monkeypatch.setenv("TRADING_ENV", "development")
        # 不抛异常
        assert_production_fail_closed(ValueError("test error"), "test context")

    @pytest.mark.unit
    def test_production_exception_chain(self, monkeypatch):
        """production 环境 → RuntimeError 链式异常"""
        monkeypatch.setenv("TRADING_ENV", "production")
        original = ValueError("original")
        with pytest.raises(RuntimeError) as exc_info:
            assert_production_fail_closed(original, "ctx")
        assert exc_info.value.__cause__ is original
