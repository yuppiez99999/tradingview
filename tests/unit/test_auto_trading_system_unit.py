"""auto_trading_system 单元测试 — 自动交易系统."""

from __future__ import annotations

from utils.auto_trading_system import AutoTradingSystem


class TestDefaultEtfCodes:
    def test_codes(self):
        assert "510300" in AutoTradingSystem.DEFAULT_ETF_CODES
        assert "510500" in AutoTradingSystem.DEFAULT_ETF_CODES
        assert "588000" in AutoTradingSystem.DEFAULT_ETF_CODES
        assert "159915" in AutoTradingSystem.DEFAULT_ETF_CODES
        assert len(AutoTradingSystem.DEFAULT_ETF_CODES) >= 5


class TestInit:
    def test_basic(self):
        system = AutoTradingSystem(total_capital=1000000)
        assert system.monitor_interval == 30
        assert system.is_running is False
        assert system.stats["cycles_completed"] == 0
        assert system.stats["errors"] == 0
        assert system.stats["start_time"] is None

    def test_default_capital(self):
        system = AutoTradingSystem()
        assert system.stats["cycles_completed"] == 0


class TestStop:
    def test_basic(self):
        system = AutoTradingSystem(total_capital=500000)
        system.is_running = True
        system.stop()
        assert system.is_running is False

    def test_not_running(self):
        system = AutoTradingSystem(total_capital=500000)
        system.stop()
        assert system.is_running is False


class TestCheckShadowAccount:
    def test_basic(self):
        system = AutoTradingSystem(total_capital=500000)
        result = system._check_shadow_account()
        assert result is True


class TestCheckKillSwitch:
    def test_basic(self):
        system = AutoTradingSystem(total_capital=500000)
        result = system._check_kill_switch()
        assert result is True


class TestRunAsync:
    def test_starts_thread(self):
        system = AutoTradingSystem(total_capital=500000)
        thread = system.run_async(duration_seconds=1)
        assert thread.is_alive()
        system.stop()
        thread.join(timeout=5)
