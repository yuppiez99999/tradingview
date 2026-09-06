"""P1-12/13/14 回归测试: v5.10 组合回测产物落点、前视守卫、滑点语义。

审查报告: 回测程序缺陷审查报告_20260904.md
  P1-12 export_results 未传 output_dir(产物散落 cwd) —— 由 main() 改动修复,
         本文件以模块可加载 + run 正常收口冒烟覆盖。
  P1-13 数据起点晚于评估窗口的标的(后来上市者拉入旧窗口)须显式失败。
  P1-14 涨跌幅限制/滑点纯函数语义。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pandas as pd
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKTEST_PY = (
    REPO_ROOT / "backtests" / "v510_portfolio_20260903" / "portfolio_v510_backtest.py"
)

SYMBOLS = [
    "sh510300", "sh510500", "sh512100", "sh588000", "sh688041",
    "sz300308", "sz300274", "sz002371", "sh688981", "sh600276",
    "sh603019", "sh600089", "sh600875", "sh601088", "sh600219",
    "sh600019", "sh518880", "sz000792", "sh600900", "sz000858",
    "sh601318", "sh600036", "sz159915",
]


@pytest.fixture(scope="module")
def mod():
    before = list(sys.path)
    spec = importlib.util.spec_from_file_location(
        "_v510_p1_under_test", BACKTEST_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for p in list(sys.path):
        if p not in before:
            sys.path.remove(p)
    return module


def _write_csv(path: pathlib.Path, late_after: dict[str, str]) -> None:
    """构造 klines_qfq.csv: 2023-05 起行情; late_after={symbol: 首次日期}
    的标的从该日起才有数据(模拟数据起点晚于窗口起点)。"""
    dates = list(pd.bdate_range("2023-05-08", periods=420).strftime("%Y-%m-%d"))
    px_base = {s: 3.0 + i * 0.5 for i, s in enumerate(SYMBOLS)}
    rows = []
    for d in dates:
        for s in SYMBOLS:
            if d < late_after.get(s, "0000-00-00"):
                continue
            p = px_base[s] * (1.0 + 0.0002 * dates.index(d))
            rows.append((d, s, p, p, p, p))
    pd.DataFrame(
        rows, columns=["date", "symbol", "open", "high", "low", "close"],
    ).to_csv(path, index=False)


def _run_with(mod, tmp_path, monkeypatch, late_after: dict[str, str]):
    csv_path = tmp_path / "klines_qfq.csv"
    _write_csv(csv_path, late_after)
    monkeypatch.setattr(mod, "HERE", tmp_path)
    return mod.run(rebalance_enabled=False)


def test_normal_run_smoke(mod, tmp_path, monkeypatch):
    """全标的数据起点早于窗口 -> run 正常收口, 期末现金为正。"""
    _, eq, _, _, cal = _run_with(mod, tmp_path, monkeypatch, {})
    assert cal[0] == "2023-09-01"
    assert all(p["value"] >= 0 and p["value"] == p["value"] for p in eq)
    assert eq[-1]["value"] > 0


def test_guard_rejects_symbol_starting_after_window_start(
        mod, tmp_path, monkeypatch):
    """P1-13: sh600036 直到 2023-11 才有数据 -> 前视守卫显式失败。"""
    with pytest.raises(SystemExit, match="前视偏差"):
        _run_with(mod, tmp_path, monkeypatch, {"sh600036": "2023-11-01"})


def test_slippage_and_limit_semantics(mod):
    """P1-14: 股票 10bp / ETF 5bp 滑点; 科创创业板 20% 涨跌幅。"""
    assert abs(mod.fill_buy_px(10.0, "sh600036") - 10.01) < 1e-9       # stock 10bp
    assert abs(mod.fill_sell_px(10.0, "sh600036") - 9.99) < 1e-9
    assert abs(mod.fill_buy_px(10.0, "sh510300") - 10.005) < 1e-9      # etf 5bp
    assert mod.limit_pct("sh600036") == 0.10
    assert mod.limit_pct("sh688041") == 0.20                            # 科创板
    assert mod.limit_pct("sz300308") == 0.20                            # 创业板
    assert mod.limit_pct("sh588000") == 0.20                            # 科创50ETF
    assert mod.limit_pct("sz159915") == 0.20                            # 创业板ETF
    assert mod.buy_blocked(10.0, 10.999, "sh600036") is False           # 10% 未到
    assert mod.buy_blocked(10.0, 11.0, "sh600036") is True              # 10% 涨停价
    assert mod.buy_blocked(10.0, 11.99, "sz300308") is False            # 20% 未到
    assert mod.buy_blocked(10.0, 12.0, "sz300308") is True              # 20% 涨停价
    assert mod.sell_blocked(10.0, 9.0, "sh600036") is True              # 跌停价
    assert mod.sell_blocked(None, 9.0, "sh600036") is False             # 无前收
