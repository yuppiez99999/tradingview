"""G7 boost: ms_strategy/src/execution/algo_engine.py 单元测试.

覆盖 AlgoEngine 时段判断/TWAP/VWAP/POV/ICEBERG 拆单/批量执行/日末重置全部公开接口,
包括配置加载失败、非交易时段、VWAP 无 profile 回退、未知算法等异常分支.
"""
from __future__ import annotations

import sys
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from ms_strategy.src.execution.algo_engine import (  # noqa: E402
    AlgoEngine,
    AlgoType,
    TradingSession,
)

# ============================================================
# 1. 构造与配置加载
# ============================================================


class TestInit:
    def test_default_sessions(self):
        engine = AlgoEngine()
        assert len(engine.sessions) == 4
        assert engine.sessions[0].name == "OPEN"
        assert engine.algo_cfg.twap_slice_minutes == 1

    def test_with_sor(self):
        sor = MagicMock()
        engine = AlgoEngine(sor=sor)
        assert engine.sor is sor

    def test_load_config_success(self, tmp_path):
        cfg = tmp_path / "exec.yaml"
        cfg.write_text(
            "trading_windows:\n"
            "  - session: OPEN\n"
            "    start: '9:30'\n"
            "    end: '9:45'\n"
            "    algo: TWAP\n"
            "  - session: MORN\n"
            "    start: '9:45'\n"
            "    end: '11:30'\n"
            "    algo: VWAP\n"
            "sor:\n"
            "  iceberg_pct_of_depth: 0.15\n"
            "  max_attempts: 10\n"
            "slippage:\n"
            "  per_trade_break: 0.008\n"
            "  daily_break: 0.015\n"
            "  global_slow_threshold: 0.004\n",
            encoding="utf-8",
        )
        engine = AlgoEngine(config_path=str(cfg))
        assert len(engine.sessions) == 2
        assert engine.sessions[0].name == "OPEN"
        assert engine.sessions[1].algo == "VWAP"
        assert engine.algo_cfg.iceberg_pct_of_depth == 0.15
        assert engine.algo_cfg.max_attempts == 10
        assert engine.algo_cfg.per_trade_break == 0.008

    def test_load_config_file_not_found(self):
        engine = AlgoEngine(config_path="/nonexistent/exec.yaml")
        assert len(engine.sessions) == 4

    def test_set_sor(self):
        engine = AlgoEngine()
        sor = MagicMock()
        engine.set_sor(sor)
        assert engine.sor is sor


# ============================================================
# 2. 时段判断
# ============================================================


class TestSessionDetection:
    def test_get_current_session_in_open(self):
        engine = AlgoEngine()
        s = engine.get_current_session(dtime(9, 35))
        assert s is not None
        assert s.name == "OPEN"

    def test_get_current_session_in_morn(self):
        engine = AlgoEngine()
        s = engine.get_current_session(dtime(10, 0))
        assert s is not None
        assert s.name == "MORN"

    def test_get_current_session_outside(self):
        engine = AlgoEngine()
        assert engine.get_current_session(dtime(8, 0)) is None

    def test_get_current_session_default_now(self):
        engine = AlgoEngine()
        s = engine.get_current_session()
        assert s is None or isinstance(s, TradingSession)

    def test_is_trading_hours_true(self):
        engine = AlgoEngine()
        fake_dt = MagicMock()
        fake_dt.now.return_value = datetime(2026, 1, 1, 10, 0)
        with patch("ms_strategy.src.execution.algo_engine.datetime", fake_dt):
            assert engine.is_trading_hours() is True

    def test_is_trading_hours_false(self):
        engine = AlgoEngine()
        fake_dt = MagicMock()
        fake_dt.now.return_value = datetime(2026, 1, 1, 3, 0)
        with patch("ms_strategy.src.execution.algo_engine.datetime", fake_dt):
            assert engine.is_trading_hours() is False

    def test_is_futures_trading_hours_day(self):
        engine = AlgoEngine()
        fake_dt = MagicMock()
        fake_dt.now.return_value = datetime(2026, 1, 1, 10, 0)
        with patch("ms_strategy.src.execution.algo_engine.datetime", fake_dt):
            assert engine.is_futures_trading_hours() is True

    def test_is_futures_trading_hours_night(self):
        engine = AlgoEngine()
        fake_dt = MagicMock()
        fake_dt.now.return_value = datetime(2026, 1, 1, 22, 0)
        with patch("ms_strategy.src.execution.algo_engine.datetime", fake_dt):
            assert engine.is_futures_trading_hours() is True

    def test_is_futures_trading_hours_false(self):
        engine = AlgoEngine()
        fake_dt = MagicMock()
        fake_dt.now.return_value = datetime(2026, 1, 1, 3, 0)
        with patch("ms_strategy.src.execution.algo_engine.datetime", fake_dt):
            assert engine.is_futures_trading_hours() is False

    def test_get_asset_sessions_stock(self):
        engine = AlgoEngine()
        assert len(engine.get_asset_sessions("STOCK")) == 4

    def test_get_asset_sessions_future(self):
        engine = AlgoEngine()
        assert len(engine.get_asset_sessions("FUTURE")) == 6

    def test_get_asset_sessions_futures(self):
        engine = AlgoEngine()
        assert len(engine.get_asset_sessions("FUTURES")) == 6


# ============================================================
# 3. 拆单算法 (内部方法)
# ============================================================


class TestSplitInternal:
    def test_split_iceberg_with_depth(self):
        engine = AlgoEngine()
        depth = {"bid1_vol": 100, "ask1_vol": 200, "bid1": 10.0, "ask1": 10.1}
        now = datetime(2026, 1, 1, 10, 0)
        slices = engine._split_iceberg(500, depth, now)
        assert sum(s.quantity for s in slices) == 500
        assert all(s.price == pytest.approx(10.05) for s in slices)

    def test_split_iceberg_no_depth(self):
        engine = AlgoEngine()
        now = datetime(2026, 1, 1, 10, 0)
        slices = engine._split_iceberg(500, None, now)
        assert sum(s.quantity for s in slices) == 500
        assert all(s.price is None for s in slices)

    def test_split_twap_normal(self):
        engine = AlgoEngine()
        now = datetime(2026, 1, 1, 10, 0)
        slices = engine._split_twap(600, 30, now)
        assert sum(s.quantity for s in slices) == 600

    def test_split_twap_remainder(self):
        engine = AlgoEngine()
        now = datetime(2026, 1, 1, 10, 0)
        slices = engine._split_twap(1000, 30, now)
        assert sum(s.quantity for s in slices) == 1000
        assert slices[-1].quantity == 170

    def test_split_vwap_normal(self):
        engine = AlgoEngine()
        now = datetime(2026, 1, 1, 10, 0)
        profile = [100.0, 200.0, 300.0, 200.0, 100.0]
        slices = engine._split_vwap(900, profile, now)
        assert slices is not None
        assert sum(s.quantity for s in slices) == 900

    def test_split_vwap_empty_profile(self):
        engine = AlgoEngine()
        now = datetime(2026, 1, 1, 10, 0)
        assert engine._split_vwap(1000, [], now) is None

    def test_split_vwap_none_profile(self):
        engine = AlgoEngine()
        now = datetime(2026, 1, 1, 10, 0)
        assert engine._split_vwap(1000, None, now) is None

    def test_split_vwap_zero_total_vol(self):
        engine = AlgoEngine()
        now = datetime(2026, 1, 1, 10, 0)
        assert engine._split_vwap(1000, [0.0, 0.0, 0.0], now) is None

    def test_split_pov(self):
        engine = AlgoEngine()
        now = datetime(2026, 1, 1, 10, 0)
        slices = engine._split_pov(1000, 0.1, now)
        assert sum(s.quantity for s in slices) == 1000
        assert len(slices) == 10


# ============================================================
# 4. split 入口
# ============================================================


class TestSplit:
    def test_zero_qty(self):
        engine = AlgoEngine()
        assert engine.split(0, "BUY") == []
        assert engine.split(-1, "BUY") == []

    def test_iceberg(self):
        engine = AlgoEngine()
        slices = engine.split(500, "BUY", AlgoType.ICEBERG,
                              depth={"bid1_vol": 100, "ask1_vol": 200})
        assert sum(s.quantity for s in slices) == 500

    def test_twap(self):
        engine = AlgoEngine()
        slices = engine.split(1000, "BUY", AlgoType.TWAP, window_minutes=30)
        assert sum(s.quantity for s in slices) == 1000

    def test_vwap_with_profile(self):
        engine = AlgoEngine()
        slices = engine.split(900, "BUY", AlgoType.VWAP,
                              volume_profile=[100, 200, 300, 200, 100])
        assert sum(s.quantity for s in slices) == 900

    def test_vwap_no_profile_fallback_twap(self):
        engine = AlgoEngine()
        slices = engine.split(1000, "BUY", AlgoType.VWAP, volume_profile=None)
        assert len(slices) > 0
        assert sum(s.quantity for s in slices) == 1000

    def test_pov(self):
        engine = AlgoEngine()
        slices = engine.split(1000, "BUY", AlgoType.POV, participation_rate=0.1)
        assert sum(s.quantity for s in slices) == 1000

    def test_unknown_algo_default(self):
        engine = AlgoEngine()
        fake_algo = MagicMock()
        slices = engine.split(1000, "BUY", fake_algo)
        assert len(slices) == 1
        assert slices[0].quantity == 1000


# ============================================================
# 5. execute_order / execute_batch / end_of_day
# ============================================================


class TestExecuteOrder:
    def test_twap(self):
        sor = MagicMock()
        sor.execute_twap.return_value = [{"fill": 1}]
        engine = AlgoEngine(sor=sor)
        result = engine.execute_order("510300.SH", 1000, "BUY", 4.5, algo="TWAP")
        assert result == [{"fill": 1}]
        sor.execute_twap.assert_called_once()

    def test_vwap(self):
        sor = MagicMock()
        sor.execute_vwap.return_value = [{"fill": 1}]
        engine = AlgoEngine(sor=sor)
        result = engine.execute_order("X", 1000, "BUY", 4.5, algo="VWAP")
        assert result == [{"fill": 1}]
        sor.execute_vwap.assert_called_once()

    def test_pov(self):
        sor = MagicMock()
        sor.execute_pov.return_value = [{"fill": 1}]
        engine = AlgoEngine(sor=sor)
        result = engine.execute_order("X", 1000, "BUY", 4.5, algo="POV")
        assert result == [{"fill": 1}]
        sor.execute_pov.assert_called_once()

    def test_unknown_algo(self):
        sor = MagicMock()
        sor.execute.return_value = [{"fill": 1}]
        engine = AlgoEngine(sor=sor)
        result = engine.execute_order("X", 1000, "BUY", 4.5, algo="UNKNOWN")
        assert result == [{"fill": 1}]
        sor.execute.assert_called_once()

    def test_algo_none_no_session(self):
        sor = MagicMock()
        sor.execute_twap.return_value = []
        engine = AlgoEngine(sor=sor)
        fake_dt = MagicMock()
        fake_dt.now.return_value = datetime(2026, 1, 1, 3, 0)
        with patch("ms_strategy.src.execution.algo_engine.datetime", fake_dt):
            result = engine.execute_order("X", 1000, "BUY", 4.5)
        assert result == []
        sor.execute_twap.assert_called_once_with("X", 1000, "BUY", 4.5)

    def test_algo_none_with_session(self):
        sor = MagicMock()
        sor.execute_vwap.return_value = [{"f": 1}]
        engine = AlgoEngine(sor=sor)
        fake_dt = MagicMock()
        fake_dt.now.return_value = datetime(2026, 1, 1, 10, 0)
        with patch("ms_strategy.src.execution.algo_engine.datetime", fake_dt):
            result = engine.execute_order("X", 1000, "BUY", 4.5)
        assert result == [{"f": 1}]
        sor.execute_vwap.assert_called_once()


class TestExecuteBatch:
    def test_batch(self):
        sor = MagicMock()
        sor.execute_twap.return_value = [{"f": 1}]
        engine = AlgoEngine(sor=sor)
        orders = [
            {"symbol": "X", "qty": 1000, "side": "BUY", "decision_price": 4.5, "algo": "TWAP"},
            {"symbol": "Y", "qty": 500, "side": "SELL", "decision_price": 6.0, "algo": "TWAP"},
        ]
        fills = engine.execute_batch(orders)
        assert len(fills) == 2
        assert sor.execute_twap.call_count == 2

    def test_batch_empty(self):
        sor = MagicMock()
        engine = AlgoEngine(sor=sor)
        assert engine.execute_batch([]) == []


class TestEndOfDay:
    def test_end_of_day(self):
        sor = MagicMock()
        engine = AlgoEngine(sor=sor)
        engine.end_of_day()
        sor.reset_daily_slip.assert_called_once()
