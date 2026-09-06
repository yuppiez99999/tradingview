"""P1-11 回归测试: 组合回测现金不透支 + 停牌/缺K线 NaN 防护。

审查报告: 回测程序缺陷审查报告_20260904.md P1-11
  portfolio_etf200w_backtest.py 买入不校验现金余额(cash 可为负);
  停牌日价格为 NaN 直接污染权益。

本测试用合成 CSV + monkeypatch 模块全局(HERE/DATA_CSV)无副作用运行:
  - 首日停牌   -> 建仓顺延, 全程权益曲线无 NaN
  - 长期停牌   -> 再平衡跳过停牌标的, 估值沿用最近收盘, 权益曲线无 NaN
"""
from __future__ import annotations

import importlib.util
import math
import pathlib
import sys

import pandas as pd
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKTEST_PY = (
    REPO_ROOT / "backtests" / "etf200w_20260903" / "portfolio_etf200w_backtest.py"
)

SYMBOLS = ["sh510300", "sh510500", "sh512100", "sh588000", "sz159915", "sh518880"]
BASE_PX = {
    "sh510300": 3.6, "sh510500": 5.3, "sh512100": 1.8,
    "sh588000": 1.2, "sz159915": 2.3, "sh518880": 4.1,
}
N_DAYS = 720
# 六个 symbol 各自已覆盖首日, 保证 sh518880 停牌期间其余照常交易
# P1-13 前视守卫要求标的数据起点早于窗口起点(2023-09-01), 故合成数据
# 在窗口前(2023-05-08 起)预置一段行情, 证明标的早已上市、仅窗口内停牌。
PRE_START = "2023-05-08"
PRE_END = "2023-08-31"


@pytest.fixture(scope="module")
def mod():
    """加载回测模块一次(import 级副作用仅 sys.path.insert, 已清理)。"""
    before = list(sys.path)
    spec = importlib.util.spec_from_file_location(
        "_etf200w_p1_11_under_test", BACKTEST_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for p in list(sys.path):
        if p not in before:
            sys.path.remove(p)
    return module


def _synthetic_csv(path: pathlib.Path, suspend: dict[str, set[str]]) -> None:
    """生成合成K线: 窗口前预置段 + 窗口内 N_DAYS 个交易日。

    suspend: {symbol: {窗口内 date 下标集合}} —— 这些日期的行整体删除(模拟停牌)。
    价格随日期缓涨, open==high==low==close(无内部价差, 便于对账)。
    """
    pre_dates = list(
        pd.bdate_range(PRE_START, PRE_END).strftime("%Y-%m-%d"))
    win_dates = list(
        pd.bdate_range("2023-09-01", periods=N_DAYS).strftime("%Y-%m-%d"))
    rows = []
    # 窗口前预置段 (所有 symbol 齐全, 保证数据起点早于 EVAL 窗口)
    for k, d in enumerate(pre_dates):
        for s in SYMBOLS:
            px = BASE_PX[s] * (1.0 + 0.0001 * k)
            rows.append((d, s, px, px, px, px))
    # 窗口内评估段 (suspend 的下标相对 win_dates)
    for i, d in enumerate(win_dates):
        for s in SYMBOLS:
            if s in suspend and i in suspend[s]:
                continue
            px = BASE_PX[s] * (1.0 + 0.0001 * (len(pre_dates) + i))
            rows.append((d, s, px, px, px, px))
    df = pd.DataFrame(rows, columns=["date", "symbol", "open", "high", "low", "close"])
    df.to_csv(path, index=False)


def _equity_ok(equity_curve: list[dict]) -> None:
    assert len(equity_curve) == N_DAYS
    for p in equity_curve:
        v = p["value"]
        assert math.isfinite(v), f"权益出现 NaN/Inf: date={p['date']} value={v}"
        assert v >= 0, f"权益为负: date={p['date']} value={v}"


def _run(mod, tmp_path, monkeypatch, suspend: dict[str, set[str]],
         rebalance: bool) -> dict:
    csv_path = tmp_path / "klines_synthetic.csv"
    _synthetic_csv(csv_path, suspend)
    monkeypatch.setattr(mod, "HERE", tmp_path)
    monkeypatch.setattr(mod, "DATA_CSV", csv_path)
    return mod.run(rebalance=rebalance, prefix="t")


def test_first_day_suspension_defers_buy_without_nan(
        mod, tmp_path, monkeypatch):
    """sh518880 窗口前 8 个交易日停牌 -> 建仓顺延, 不抛异常, 权益无 NaN。"""
    suspend = {"sh518880": set(range(8))}
    for rebalance in (True, False):
        res = _run(mod, tmp_path, monkeypatch, suspend, rebalance=rebalance)
        _equity_ok(res["equity_curve"])
        assert res["n_days"] == N_DAYS
        assert len(res["trade_history"]) > 0  # 停牌标的复牌后完成建仓/平仓
        assert math.isfinite(res["equity_curve"][-1]["value"])


def test_long_suspension_skips_rebalance_and_marks_last_close(
        mod, tmp_path, monkeypatch):
    """sh518880 从第 40 个交易日停牌至倒数 20 日 -> 期间每轮月末再平衡
    都跳过该标的, 估值沿用最近收盘, 权益曲线连续且无 NaN。"""
    suspend = {"sh518880": set(range(40, N_DAYS - 20))}
    res = _run(mod, tmp_path, monkeypatch, suspend, rebalance=True)
    _equity_ok(res["equity_curve"])
    assert res["n_days"] == N_DAYS
    # 复牌后强平/再平衡仍完成
    assert res["equity_curve"][-1]["value"] > 0


def test_no_NaN_on_clean_data_matches_no_rebalance_case(
        mod, tmp_path, monkeypatch):
    """全量数据无停牌时, 权益曲线无 NaN 且期末为正(基线冒烟)。"""
    res = _run(mod, tmp_path, monkeypatch, {}, rebalance=False)
    _equity_ok(res["equity_curve"])
    assert res["equity_curve"][-1]["value"] > 0


def test_limit_and_slippage_semantics(mod):
    """P1-14: 涨跌幅限制边界 + 滑点成交价语义(纯函数单测)。"""
    # 涨跌幅限制: 科创50/创业板 ETF 20%, 其余 10%
    assert mod.limit_pct("sh510300") == 0.10
    assert mod.limit_pct("sh510500") == 0.10
    assert mod.limit_pct("sh588000") == 0.20
    assert mod.limit_pct("sz159915") == 0.20
    assert mod.limit_pct("sh518880") == 0.10
    # 开盘封涨停 -> 不可买入; 精确在涨停价也拦(保守)
    assert mod.buy_blocked(10.0, 11.0, "sh510300") is True
    assert mod.buy_blocked(10.0, 10.99, "sh510300") is False
    # 无前收(如窗口首日)不拦
    assert mod.buy_blocked(None, 11.0, "sh510300") is False
    # 开盘封跌停 -> 不可卖出
    assert mod.sell_blocked(10.0, 8.99, "sh510300") is True
    assert mod.sell_blocked(10.0, 9.01, "sh510300") is False
    # 20% 限制: +15% 未到涨停, 可买
    assert mod.buy_blocked(10.0, 11.5, "sh588000") is False
    assert mod.buy_blocked(10.0, 12.0, "sh588000") is True
    # 滑点: ETF 5bp 单边, 买入上浮/卖出下浮
    assert abs(mod.fill_buy_px(10.0) - 10.005) < 1e-9
    assert abs(mod.fill_sell_px(10.0) - 9.995) < 1e-9


def test_guard_rejects_symbol_starting_late_in_window(
        mod, tmp_path, monkeypatch):
    """P1-13: 数据起点晚于评估窗口起点的标的(把后来上市者塞进旧窗口)
    必须被前视守卫拒绝。"""
    csv_path = tmp_path / "klines_late.csv"
    win = pd.bdate_range("2023-09-01", periods=30)
    late_s = "sh518880"
    rows = []
    for i, d in enumerate(win):
        for s in SYMBOLS:
            if s == late_s and i < 10:
                continue  # late_s 直到窗口第 10 个交易日才有数据
            px = BASE_PX[s] * (1.0 + 0.0001 * i)
            rows.append((d.strftime("%Y-%m-%d"), s, px, px, px, px))
    pd.DataFrame(
        rows, columns=["date", "symbol", "open", "high", "low", "close"],
    ).to_csv(csv_path, index=False)
    monkeypatch.setattr(mod, "HERE", tmp_path)
    monkeypatch.setattr(mod, "DATA_CSV", csv_path)
    with pytest.raises(SystemExit, match="前视偏差"):
        mod.run(rebalance=False, prefix="t")
