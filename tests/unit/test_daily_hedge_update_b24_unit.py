# -*- coding: utf-8 -*-
"""daily_hedge_update B2.4 两循环并发化 单元测试

覆盖场景:
1. update_returns 并发拉取多 symbol K 线 + 计算 returns
2. update_returns 部分失败容错 (单 symbol 抛异常不影响其他)
3. update_returns 并发快于串行 (性能验证)
4. run_hedge_decision 并发拉取多 position 实时报价
5. run_hedge_decision 单 position 报价失败回退 est_price
"""
from __future__ import annotations

import builtins
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# 确保 utils 在 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
# daily_hedge_update 依赖:
# - wind_mcp_fetcher (位于 tools/)
# - hedging.hedge_coordinator (位于 v8.3_institutional/src/)
for _sub in ("tools", "v8.3_institutional/src"):
    _p = _PROJECT_ROOT / _sub
    if _p.exists() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# 显式加载根目录的 daily_hedge_update.py (避免 import 到 v8.3_institutional/daily_hedge_update.py 副本)
# 该副本顶部未设置 sys.path, 直接 import 会因找不到 wind_mcp_fetcher 而失败
import importlib.util

_dhu_path = _PROJECT_ROOT / "daily_hedge_update.py"
_spec = importlib.util.spec_from_file_location("daily_hedge_update", _dhu_path)
daily_hedge_update = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(daily_hedge_update)


# ============================================================
# 测试辅助
# ============================================================
def _make_kline_df(days: int = 252) -> pd.DataFrame:
    """生成合成的 K 线 DataFrame (与 _get_historical_kline 返回格式一致)"""
    dates = pd.date_range(end="2025-06-30", periods=days, freq="B")
    rng = np.random.default_rng(seed=42)
    close = 100.0 + np.cumsum(rng.standard_normal(days) * 0.5)
    close = np.maximum(close, 1.0)
    return pd.DataFrame({"close": close}, index=dates)


def _make_positions_json(symbols_qty_pairs):
    """构造 positions.json 内容 (与 config/positions.json 格式一致)"""
    positions = {}
    for i, (code, qty, est_price) in enumerate(symbols_qty_pairs):
        positions[f"slot_{i}"] = {
            "code": code,
            "phase1_shares": qty,
            "est_price": est_price,
        }
    return {"positions": positions}


def _write_positions_file(tmp_path, symbols_qty_pairs):
    """在临时目录写一个 positions.json 文件, 返回路径"""
    pos_file = tmp_path / "positions.json"
    pos_file.write_text(
        json.dumps(_make_positions_json(symbols_qty_pairs), ensure_ascii=False),
        encoding="utf-8",
    )
    return pos_file


# ============================================================
# 1. update_returns 并发拉取
# ============================================================
class TestUpdateReturnsConcurrent:
    """B2.4: update_returns 并发拉取多 symbol K 线"""

    def test_concurrent_fetch_all_symbols(self, tmp_path):
        """所有 symbol 都应被拉取 (并发执行)"""
        pos_path = _write_positions_file(tmp_path, [
            ("600519", 100, 1500.0),
            ("000858", 200, 200.0),
            ("601318", 300, 80.0),
        ])

        kline_df = _make_kline_df()
        call_log = []

        def _mock_get_kline(symbol, days=252):
            call_log.append(symbol)
            return kline_df.copy()

        # 用 real_open 包装, 只对 positions_path 重定向
        real_open = builtins.open

        def _patched_open(path, *args, **kwargs):
            if str(path).endswith("positions.json"):
                return real_open(pos_path, *args, **kwargs)
            return real_open(path, *args, **kwargs)

        with patch("daily_hedge_update._get_historical_kline", side_effect=_mock_get_kline):
            with patch("builtins.open", side_effect=_patched_open):
                with patch.object(pd.DataFrame, "to_json"):
                    returns_df, _ = daily_hedge_update.update_returns()

        # 3 个 symbol 都被拉取
        assert set(call_log) == {"600519", "000858", "601318"}
        # 返回的 returns_df 应包含 3 列
        assert returns_df is not None
        assert set(returns_df.columns) == {"600519", "000858", "601318"}

    def test_partial_failure_does_not_block_others(self, tmp_path):
        """单 symbol 抛异常不影响其他 symbol"""
        pos_path = _write_positions_file(tmp_path, [
            ("600519", 100, 1500.0),
            ("FAIL_SYM", 200, 100.0),
            ("601318", 300, 80.0),
        ])

        kline_df = _make_kline_df()

        def _mock_get_kline(symbol, days=252):
            if symbol == "FAIL_SYM":
                raise RuntimeError("intentional failure")
            return kline_df.copy()

        real_open = builtins.open

        def _patched_open(path, *args, **kwargs):
            if str(path).endswith("positions.json"):
                return real_open(pos_path, *args, **kwargs)
            return real_open(path, *args, **kwargs)

        with patch("daily_hedge_update._get_historical_kline", side_effect=_mock_get_kline):
            with patch("builtins.open", side_effect=_patched_open):
                with patch.object(pd.DataFrame, "to_json"):
                    returns_df, _ = daily_hedge_update.update_returns()

        # 失败 symbol 不在 returns_df
        assert "FAIL_SYM" not in returns_df.columns
        # 成功 symbol 在 returns_df
        assert "600519" in returns_df.columns
        assert "601318" in returns_df.columns

    def test_concurrent_faster_than_serial(self, tmp_path):
        """并发应明显快于串行 (sleep 模拟网络延迟)"""
        pos_path = _write_positions_file(tmp_path, [
            ("600519", 100, 1500.0),
            ("000858", 200, 200.0),
            ("601318", 300, 80.0),
            ("000001", 400, 15.0),
        ])

        def _slow_get_kline(symbol, days=252):
            time.sleep(0.1)  # 模拟 100ms 网络延迟
            return _make_kline_df()

        real_open = builtins.open

        def _patched_open(path, *args, **kwargs):
            if str(path).endswith("positions.json"):
                return real_open(pos_path, *args, **kwargs)
            return real_open(path, *args, **kwargs)

        with patch("daily_hedge_update._get_historical_kline", side_effect=_slow_get_kline):
            with patch("builtins.open", side_effect=_patched_open):
                with patch.object(pd.DataFrame, "to_json"):
                    t0 = time.time()
                    daily_hedge_update.update_returns()
                    elapsed = time.time() - t0

        # 串行: 4 × 0.1s = 0.4s; 并发(8 workers): 应 < 0.25s
        assert elapsed < 0.25, f"并发耗时 {elapsed:.2f}s 过长, 可能未真正并发"


# ============================================================
# 2. run_hedge_decision 并发拉取报价
# ============================================================
class TestRunHedgeDecisionConcurrent:
    """B2.4: run_hedge_decision 并发拉取多 position 报价"""

    def test_concurrent_fetch_all_quotes(self, tmp_path):
        """所有 position 都应被拉取报价"""
        pos_path = _write_positions_file(tmp_path, [
            ("600519", 100, 1500.0),
            ("000858", 200, 200.0),
            ("601318", 300, 80.0),
        ])

        call_log = []

        def _mock_get_quote(wind_code, is_fund=False):
            call_log.append(wind_code)
            return {"price": 100.0}

        mock_plan = {
            "action": "NO_HEDGE", "portfolio_beta": 0.5, "total_hedge_pct": 0.0,
            "total_cost_pct": 0.0, "regime": "normal",
        }
        mock_coord = MagicMock()
        mock_coord.coordinate.return_value = mock_plan

        real_open = builtins.open

        def _patched_open(path, *args, **kwargs):
            if str(path).endswith("positions.json"):
                return real_open(pos_path, *args, **kwargs)
            return real_open(path, *args, **kwargs)

        with patch("daily_hedge_update.wind_get_quote", side_effect=_mock_get_quote):
            with patch("builtins.open", side_effect=_patched_open):
                with patch("daily_hedge_update.HedgeCoordinator", return_value=mock_coord):
                    with patch("os.path.exists", return_value=False):
                        plan = daily_hedge_update.run_hedge_decision()

        # 3 个 symbol 的报价都被拉取
        assert len(call_log) == 3
        # plan 正确返回
        assert plan["action"] == "NO_HEDGE"
        # coordinator.coordinate 被调用, positions/prices 包含全部 3 个
        call_kwargs = mock_coord.coordinate.call_args.kwargs
        assert set(call_kwargs["positions"].keys()) == {"600519", "000858", "601318"}
        assert set(call_kwargs["prices"].keys()) == {"600519", "000858", "601318"}

    def test_quote_failure_fallback_to_est_price(self, tmp_path):
        """单 position 报价失败应回退 est_price"""
        pos_path = _write_positions_file(tmp_path, [
            ("600519", 100, 1500.0),
            ("FAIL_SYM", 200, 250.0),  # 失败, 回退 est_price=250
            ("601318", 300, 80.0),
        ])

        def _mock_get_quote(wind_code, is_fund=False):
            if "FAIL_SYM" in wind_code:
                raise RuntimeError("api fail")
            return {"price": 100.0}

        mock_coord = MagicMock()
        mock_coord.coordinate.return_value = {
            "action": "NO_HEDGE", "portfolio_beta": 0.5,
            "total_hedge_pct": 0.0, "total_cost_pct": 0.0, "regime": "normal",
        }

        real_open = builtins.open

        def _patched_open(path, *args, **kwargs):
            if str(path).endswith("positions.json"):
                return real_open(pos_path, *args, **kwargs)
            return real_open(path, *args, **kwargs)

        with patch("daily_hedge_update.wind_get_quote", side_effect=_mock_get_quote):
            with patch("builtins.open", side_effect=_patched_open):
                with patch("daily_hedge_update.HedgeCoordinator", return_value=mock_coord):
                    with patch("os.path.exists", return_value=False):
                        daily_hedge_update.run_hedge_decision()

        call_kwargs = mock_coord.coordinate.call_args.kwargs
        # 失败 symbol 回退到 est_price=250.0
        assert call_kwargs["prices"]["FAIL_SYM"] == 250.0
        # 成功 symbol 用报价 100.0
        assert call_kwargs["prices"]["600519"] == 100.0
        assert call_kwargs["prices"]["601318"] == 100.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
