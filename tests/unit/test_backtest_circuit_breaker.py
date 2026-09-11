"""回测 L1~L4 减仓熔断 + Wind 证据基座 的回归测试.

修复前会失败的口径 (改动回滚即变红):
    1. 配置里的 L1~L4 阶梯没被引擎消费 (regression: 只跑权重加权) → test_cb_ladder_parsed / test_cb_reduces_drawdown
    2. 用**当日**回撤决定**当日**权重 = 前视偏差 → test_no_lookahead_first_crash_day_unaffected
    3. Wind 证据基座缺失/含失败项时静默降级 → test_wind_basis_fails_closed
    4. 无 L1~L4 配置时行为漂移 (老报告不可复现) → test_plain_config_is_unchanged
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(PROJECT_ROOT), str(PROJECT_ROOT / "scripts")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import run_200w_etf_backtest as bt  # noqa: E402

V91_CONFIG = PROJECT_ROOT / "config" / "portfolio_200w_etf_v91.yaml"


def _prices_with_crash() -> pd.DataFrame:
    """权益腿先横盘, 第 10 天暴跌 20% 且继续阴跌; 防御腿全程横盘。"""
    n = 20
    dates = pd.date_range("2021-01-04", periods=n, freq="B")
    eq = np.ones(n)
    eq[10:] = [0.80, 0.76, 0.722, 0.686, 0.652, 0.619, 0.588, 0.559, 0.531, 0.504]
    return pd.DataFrame({"510300": eq, "511260": np.ones(n)}, index=dates)


def _crash_config() -> dict:
    """最小可复现配置: 权益 50% + 防御 50%, 仅一档 L1 (dd≥10% → 权益 20%)。"""
    return {
        "portfolio": {"total_capital": 2_000_000, "equity_exposure_target": 0.5},
        "core_holdings": [{"code": "510300", "weight": 0.5}],
        "ballast_holdings": [{"code": "511260", "weight": 0.5}],
        "circuit_breaker": {
            "levels": [
                {
                    "level": "L1",
                    "triggers": ["权益自高点回撤 ≥ 10%"],
                    "equity_target": 0.20,
                }
            ]
        },
    }


@pytest.fixture(scope="module")
def v91() -> dict:
    if not V91_CONFIG.exists():
        pytest.skip(f"v9.1 配置不存在: {V91_CONFIG}")
    with open(V91_CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_cb_ladder_parsed_from_production_config(v91: dict) -> None:
    """L1~L4 必须从配置被解析出来, 阈值升序、权益目标单调递减。"""
    ladder = bt._resolve_circuit_breaker(v91)
    assert [name for _th, _tgt, name in ladder] == ["L1", "L2", "L3", "L4"]
    thresholds = [th for th, _tgt, _name in ladder]
    assert thresholds == sorted(thresholds) == pytest.approx([0.08, 0.12, 0.15, 0.18])
    targets = [tgt for _th, tgt, _name in ladder]
    assert all(targets[i] > targets[i + 1] for i in range(len(targets) - 1))
    # L1 只暂停加仓、不减权益 → 其目标必须等于基准权益权重 (0.53)
    assert targets[0] == pytest.approx(v91["portfolio"]["equity_exposure_target"])


def test_no_lookahead_first_crash_day_unaffected() -> None:
    """暴跌当日 (信号尚未可得) 的净值必须与无熔断完全一致 —— 权重次日生效。"""
    prices = _prices_with_crash()
    config = _crash_config()
    weights = bt._build_weight_map(config)

    plain = bt._run_backtest(prices, weights, config, "无熔断", circuit_breaker=False)
    managed = bt._run_backtest(prices, weights, config, "有熔断", circuit_breaker=True)

    crash_idx = 10
    for i in range(crash_idx + 1):
        assert managed.equity_curve[i] == pytest.approx(plain.equity_curve[i]), (
            f"第 {i} 日净值被当日回撤影响 → 前视偏差"
        )
    assert managed.equity_curve[-1] > plain.equity_curve[-1]
    assert managed.cb_enabled is True
    assert managed.cb_days_by_level.get("L1", 0) > 0


def test_cb_reduces_drawdown() -> None:
    """L1 触发后权益降至 20% → 最大回撤必须显著小于无熔断。"""
    prices = _prices_with_crash()
    config = _crash_config()
    weights = bt._build_weight_map(config)
    plain = bt._run_backtest(prices, weights, config, "无熔断", circuit_breaker=False)
    managed = bt._run_backtest(prices, weights, config, "有熔断", circuit_breaker=True)
    assert managed.max_drawdown < plain.max_drawdown
    assert plain.max_drawdown > 0.15
    # 权益权重最低应贴近 L1 目标 0.20 / 基准 0.50 = 40%
    assert managed.cb_min_equity_weight == pytest.approx(0.40, abs=1e-6)
    assert managed.cb_turnover_pct > 0


def test_plain_config_is_unchanged() -> None:
    """无 circuit_breaker 的配置必须保持纯权重加权 (老报告可复现, 行为不漂移)。"""
    prices = _prices_with_crash()
    config = _crash_config()
    config.pop("circuit_breaker")
    weights = bt._build_weight_map(config)
    result = bt._run_backtest(prices, weights, config, "无熔断", circuit_breaker=True)
    assert result.cb_enabled is False
    eq_ret = prices["510300"].pct_change().fillna(0.0)
    df_ret = prices["511260"].pct_change().fillna(0.0)
    expected = float(np.prod(1 + 0.5 * eq_ret.iloc[1:] + 0.5 * df_ret.iloc[1:]))
    assert result.equity_curve[-1] == pytest.approx(expected, rel=1e-9)


def test_wind_basis_fails_closed(tmp_path: Path) -> None:
    """Wind 证据基座缺失 → SystemExit; manifest 含失败项 → 拒绝使用。"""
    with pytest.raises(SystemExit):
        bt._load_wind_prices(tmp_path)

    (tmp_path / "manifest.json").write_text(
        json.dumps({"failures": ["513100.SH 取数为空"]}), encoding="utf-8"
    )
    (tmp_path / "wind_ohlc_all.parquet").touch()
    with pytest.raises(SystemExit):
        bt._load_wind_prices(tmp_path)


def test_wind_basis_loads_manifest_instruments(tmp_path: Path) -> None:
    """manifest 声明的标的应被加载为 close 透视表。"""
    dates = pd.date_range("2021-01-04", periods=3, freq="B")
    long = pd.concat(
        [
            pd.DataFrame({"date": dates, "code": "510300", "close": [1.0, 1.1, 1.2]}),
            pd.DataFrame({"date": dates, "code": "518880", "close": [2.0, 2.0, 2.1]}),
        ],
        ignore_index=True,
    )
    long.to_parquet(tmp_path / "wind_ohlc_all.parquet", index=False)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"failures": [], "n_instruments": 2}), encoding="utf-8"
    )
    pivot, manifest = bt._load_wind_prices(tmp_path)
    assert list(pivot.columns) == ["510300", "518880"]
    assert manifest["n_instruments"] == 2


def test_recovery_buffer_is_a_knob_not_hardcoded(v91: dict) -> None:
    """回补缓冲是显式参数: 缓冲越大, 档位切换次数不得增加 (防阈值抖动)。"""
    prices = _prices_with_crash()
    config = copy.deepcopy(_crash_config())
    weights = bt._build_weight_map(config)
    no_buf = bt._run_backtest(
        prices, weights, config, "buf0", circuit_breaker=True, recovery_buffer=0.0
    )
    big_buf = bt._run_backtest(
        prices, weights, config, "buf50", circuit_breaker=True, recovery_buffer=0.5
    )
    assert big_buf.cb_switches <= no_buf.cb_switches
