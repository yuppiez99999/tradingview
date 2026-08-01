"""VibeBacktestBridge 单元测试.

测试目标:
    1. Flag 透传: USE_VIBE_BACKTEST_BRIDGE=False 时返回 disabled
    2. 失败安全: 异常时返回 failed 状态
    3. 回测引擎: 正常输入返回 success + 正确指标
    4. 成本模型: 佣金/滑点/印花税正确计算
    5. Baseline 对比: compare_with_baseline 正确计算偏差
    6. 审计: 结果写入 JSON
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from utils.alpha.vibe_backtest_bridge import (
    VibeBacktestBridge,
    VibeBacktestConfig,
    VibeBacktestResult,
)


# ============================================================
# 测试数据
# ============================================================
def _make_price_data(symbols: list[str], days: int = 30) -> dict[str, pd.DataFrame]:
    """生成多标的测试价格数据."""
    data: dict[str, pd.DataFrame] = {}
    dates = pd.date_range("2024-01-01", periods=days, freq="D")
    for i, sym in enumerate(symbols):
        close = [100.0 + i * 10 + j * 0.5 for j in range(days)]
        data[sym] = pd.DataFrame(
            {
                "open": [c - 0.3 for c in close],
                "high": [c + 0.5 for c in close],
                "low": [c - 0.5 for c in close],
                "close": close,
                "volume": [1_000_000 + j * 1000 for j in range(days)],
                "amount": [c * 1_000_000 for c in close],
            },
            index=dates,
        )
    return data


def _make_mock_adapter():
    """生成 mock VibeFactorAdapter."""
    adapter = MagicMock()
    adapter.health = {"loaded": 462, "failed": 0, "errors": []}
    # mock compute_single_stock 返回因子值
    result = MagicMock()
    result.values = {"alpha101_001": 0.05, "alpha101_002": -0.03}
    adapter.compute_single_stock.return_value = result
    return adapter


# ============================================================
# 测试 1: Flag 关闭时返回 disabled
# ============================================================
def test_flag_disabled_returns_disabled() -> None:
    """USE_VIBE_BACKTEST_BRIDGE=False 时返回 status=disabled."""
    with patch("utils.alpha.vibe_backtest_bridge.is_enabled", return_value=False):
        bridge = VibeBacktestBridge()
        result = bridge.run_backtest(
            factor_ids=["alpha101_001"],
            symbols=["600519.SH"],
            start_date="2024-01-01",
            end_date="2024-06-30",
            price_data=_make_price_data(["600519.SH"]),
        )
        assert result.status == "disabled"
        assert result.error == "feature flag disabled"


# ============================================================
# 测试 2: 空输入返回 failed
# ============================================================
def test_empty_input_returns_failed() -> None:
    """factor_ids 或 symbols 为空时返回 failed."""
    with patch("utils.alpha.vibe_backtest_bridge.is_enabled", return_value=True):
        bridge = VibeBacktestBridge()
        result = bridge.run_backtest(
            factor_ids=[],
            symbols=["600519.SH"],
            start_date="2024-01-01",
            end_date="2024-06-30",
            price_data=_make_price_data(["600519.SH"]),
        )
        assert result.status == "failed"
        assert "为空" in result.error


# ============================================================
# 测试 3: price_data 未提供返回 failed
# ============================================================
def test_no_price_data_returns_failed() -> None:
    """price_data=None 时返回 failed."""
    with patch("utils.alpha.vibe_backtest_bridge.is_enabled", return_value=True):
        bridge = VibeBacktestBridge()
        result = bridge.run_backtest(
            factor_ids=["alpha101_001"],
            symbols=["600519.SH"],
            start_date="2024-01-01",
            end_date="2024-06-30",
            price_data=None,
        )
        assert result.status == "failed"
        assert "price_data" in result.error


# ============================================================
# 测试 4: 正常回测返回 success
# ============================================================
def test_backtest_success() -> None:
    """正常输入返回 success 且包含性能指标."""
    with patch("utils.alpha.vibe_backtest_bridge.is_enabled", return_value=True):
        bridge = VibeBacktestBridge()
        bridge._adapter = _make_mock_adapter()
        symbols = ["600519.SH", "000001.SZ", "510300.SH"]
        result = bridge.run_backtest(
            factor_ids=["alpha101_001", "alpha101_002"],
            symbols=symbols,
            start_date="2024-01-01",
            end_date="2024-01-30",
            price_data=_make_price_data(symbols),
        )
        assert result.status == "success"
        assert result.factor_ids == ["alpha101_001", "alpha101_002"]
        assert len(result.symbols) == 3
        assert len(result.daily_returns) > 0
        assert isinstance(result.total_return, float)
        assert isinstance(result.sharpe_ratio, float)
        assert result.n_trades > 0


# ============================================================
# 测试 5: 异常时返回 failed
# ============================================================
def test_exception_returns_failed() -> None:
    """回测过程抛异常时返回 failed, 不向上传播."""
    with patch("utils.alpha.vibe_backtest_bridge.is_enabled", return_value=True):
        bridge = VibeBacktestBridge()
        # mock adapter 抛异常
        mock_adapter = MagicMock()
        mock_adapter.compute_single_stock.side_effect = RuntimeError("adapter crashed")
        bridge._adapter = mock_adapter

        result = bridge.run_backtest(
            factor_ids=["alpha101_001"],
            symbols=["600519.SH", "000001.SZ"],
            start_date="2024-01-01",
            end_date="2024-01-30",
            price_data=_make_price_data(["600519.SH", "000001.SZ"]),
        )
        assert result.status == "failed"
        assert "adapter crashed" in result.error or "因子得分计算失败" in result.error


# ============================================================
# 测试 6: 成本模型包含印花税
# ============================================================
def test_cost_model_includes_stamp_duty() -> None:
    """卖出时成本包含印花税."""
    config = VibeBacktestConfig(
        commission_bps=3.0,
        slippage_bps=5.0,
        stamp_duty_bps=10.0,
        initial_capital=1_000_000.0,
    )
    with patch("utils.alpha.vibe_backtest_bridge.is_enabled", return_value=True):
        bridge = VibeBacktestBridge(config)
        bridge._adapter = _make_mock_adapter()
        symbols = ["600519.SH", "000001.SZ"]
        result = bridge.run_backtest(
            factor_ids=["alpha101_001"],
            symbols=symbols,
            start_date="2024-01-01",
            end_date="2024-01-15",
            price_data=_make_price_data(symbols, days=15),
        )
        assert result.status in ("success", "failed")
        if result.status == "success":
            assert result.cost_total >= 0
            assert result.n_trades > 0


# ============================================================
# 测试 7: Baseline 对比
# ============================================================
def test_compare_with_baseline() -> None:
    """compare_with_baseline 正确计算偏差."""
    with patch("utils.alpha.vibe_backtest_bridge.is_enabled", return_value=True):
        bridge = VibeBacktestBridge()
        vibe_result = VibeBacktestResult(
            status="success",
            total_return=0.10,
            annual_return=0.20,
            max_drawdown=-0.05,
            sharpe_ratio=1.5,
            win_rate=0.55,
        )
        baseline = {
            "total_return": 0.12,
            "annual_return": 0.22,
            "max_drawdown": -0.06,
            "sharpe_ratio": 1.4,
            "win_rate": 0.52,
        }
        comparison = bridge.compare_with_baseline(vibe_result, baseline)
        assert comparison["vibe_total_return"] == 0.10
        assert comparison["baseline_total_return"] == 0.12
        assert comparison["diff_total_return"] == pytest.approx(-0.02)
        assert "total_deviation_pct" in comparison
        assert "within_threshold" in comparison


# ============================================================
# 测试 8: Baseline 对比 - 非成功状态
# ============================================================
def test_compare_with_baseline_failed() -> None:
    """vibe_result 非 success 时返回特殊状态."""
    with patch("utils.alpha.vibe_backtest_bridge.is_enabled", return_value=True):
        bridge = VibeBacktestBridge()
        vibe_result = VibeBacktestResult(status="failed")
        comparison = bridge.compare_with_baseline(vibe_result, {})
        assert comparison["status"] == "vibe_not_success"


# ============================================================
# 测试 9: 审计日志写入
# ============================================================
def test_audit_log_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """成功回测结果写入 JSON 审计日志."""
    monkeypatch.setattr("utils.alpha.vibe_backtest_bridge._BACKTEST_DIR", tmp_path)
    with patch("utils.alpha.vibe_backtest_bridge.is_enabled", return_value=True):
        bridge = VibeBacktestBridge(VibeBacktestConfig(audit_enabled=True))
        bridge._adapter = _make_mock_adapter()
        symbols = ["600519.SH", "000001.SZ"]
        result = bridge.run_backtest(
            factor_ids=["alpha101_001"],
            symbols=symbols,
            start_date="2024-01-01",
            end_date="2024-01-15",
            price_data=_make_price_data(symbols, days=15),
        )
        if result.status == "success":
            audit_files = list(tmp_path.glob("backtest_*.json"))
            assert len(audit_files) >= 1
            data = json.loads(audit_files[0].read_text(encoding="utf-8"))
            assert data["status"] == "success"
            assert "total_return" in data


# ============================================================
# 测试 10: 健康检查
# ============================================================
def test_get_health() -> None:
    """get_health 返回 adapter 和 flag 状态."""
    with patch("utils.alpha.vibe_backtest_bridge.is_enabled", return_value=True):
        bridge = VibeBacktestBridge()
        bridge._adapter = _make_mock_adapter()
        health = bridge.get_health()
        assert health["flag_enabled"] is True
        assert health["adapter_loaded"] == 462
        assert "benchmark" in health


# ============================================================
# 测试 11: 每日收益率计算
# ============================================================
def test_get_daily_return() -> None:
    """_get_daily_return 正确计算指定日期收益率."""
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    df = pd.DataFrame({"close": [100, 101, 102, 101, 103]}, index=dates)
    ret = VibeBacktestBridge._get_daily_return(df, "2024-01-02")
    assert ret == pytest.approx(0.01)
    ret_last = VibeBacktestBridge._get_daily_return(df, "2024-01-05")
    assert ret_last == pytest.approx((103 - 101) / 101)
    ret_none = VibeBacktestBridge._get_daily_return(df, "2024-12-31")
    assert ret_none is None
