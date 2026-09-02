"""真实归因输入构建器单元测试 (v8.7 G1 修复回归测试, 2026-09-02).

防复发目标
---------
``run_phase4_55_attribution`` 曾把硬编码合成数据 (基准 = 组合收益 x 0.8、
因子/行业收益 = 组合收益 x 固定系数、trading_costs 恒为 0) 喂给归因引擎,
导致报告每天产出**确定但错误**的结论。本测试套件锁定:

1. 各维度输入必须来自真实数据 (注入的行情面板 / 成交回报), 不得由组合收益推导
2. 基准不可得时 fail-closed (不产出、不回落合成值)
3. 因子信号严格无前视
4. 不可得因子不捏造
5. 源码层不得重新引入合成系数

所有用例均注入 mock provider, **不访问网络**。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from utils.attribution.real_inputs_builder import (
    COMMISSION_RATE,
    DEFAULT_BENCHMARK_CODE,
    FEE_RATE,
    MIN_COMMISSION,
    MIN_SYMBOLS_FOR_FACTOR,
    STAMP_DUTY_RATE,
    RealAttributionInputs,
    _long_short,
    build_real_attribution_inputs,
)

EOD_REPORT = "2026-09-01"  # 周二 (交易日)


# ==================================================================
# 工具
# ==================================================================


def _make_panel(data: dict[str, list[float]], end: str = EOD_REPORT) -> pd.DataFrame:
    """按给定收盘序列构造面板, index 为截至 end 的工作日."""
    n = len(next(iter(data.values())))
    dates = pd.date_range(end=end, periods=n, freq="B")
    return pd.DataFrame(data, index=dates)


def _provider(panel: pd.DataFrame | None):
    """注入用 provider, 返回固定面板."""

    def _p(codes, days):  # noqa: ANN001, ANN202 - 测试替身
        return panel, "mock"

    return _p


def _positions() -> list[dict]:
    return [
        {"code": "A", "weight": 0.5, "sector": "科技"},
        {"code": "B", "weight": 0.5, "sector": "科技"},
        {"code": "C", "weight": 1.0, "sector": "消费"},
    ]


# ==================================================================
# 1. 防复发: 输入不得由组合收益合成推导
# ==================================================================


def test_benchmark_is_real_not_synthetic_multiple():
    """修复前必红: 旧代码基准 = 组合收益 x 0.8, 与真实面板值必然不等.

    此处真实基准收益 = +2%, 组合收益设为 +10% (0.8 倍应为 +8%)。
    """
    panel = _make_panel(
        {
            "A": [100.0, 100.0],
            "B": [100.0, 100.0],
            "C": [100.0, 100.0],
            DEFAULT_BENCHMARK_CODE: [100.0, 102.0],  # +2%
        }
    )
    inputs = build_real_attribution_inputs(
        report_date=EOD_REPORT,
        positions=_positions(),
        portfolio_ret=0.10,
        price_provider=_provider(panel),
    )
    assert inputs.benchmark_returns == pytest.approx([0.02])
    # 反例守卫: 绝不等于组合收益的 0.8 倍
    assert inputs.benchmark_returns[0] != pytest.approx(0.10 * 0.8)


def test_sector_returns_not_derived_from_portfolio_return():
    """行业收益必须由持仓真实收益加权得到, 而非组合收益 x 固定系数."""
    panel = _make_panel(
        {
            "A": [100.0, 110.0],  # +10%
            "B": [100.0, 100.0],  # 0%
            "C": [100.0, 90.0],  # -10%
            DEFAULT_BENCHMARK_CODE: [100.0, 100.0],
        }
    )
    inputs = build_real_attribution_inputs(
        report_date=EOD_REPORT,
        positions=_positions(),
        portfolio_ret=0.05,
        price_provider=_provider(panel),
    )
    # 科技: (0.5*0.10 + 0.5*0.0) / 1.0 = 0.05 ; 消费: -0.10
    assert inputs.sector_returns["科技"] == pytest.approx([0.05])
    assert inputs.sector_returns["消费"] == pytest.approx([-0.10])
    # 旧合成口径为 portfolio_ret x 0.4 = 0.02, 必须不等
    assert inputs.sector_returns["科技"][0] != pytest.approx(0.05 * 0.4)


def test_source_contains_no_synthetic_coefficients():
    """源码层防复发: EOD 归因阶段不得重新出现合成系数."""
    src = (
        Path(__file__).resolve().parents[2]
        / "15_每日工作流"
        / "run_daily_eod_workflow.py"
    ).read_text(encoding="utf-8")
    start = src.find("def run_phase4_55_attribution")
    assert start > 0, "未找到 run_phase4_55_attribution"
    body = src[start : start + 6000]
    assert "portfolio_ret * 0.8" not in body
    assert "portfolio_ret * 0.3" not in body
    assert '"momentum": [portfolio_ret' not in body
    assert "build_real_attribution_inputs" in body


# ==================================================================
# 2. fail-closed: 数据不可得时不产出、不捏造
# ==================================================================


def test_price_panel_unavailable_marks_degraded():
    inputs = build_real_attribution_inputs(
        report_date=EOD_REPORT,
        positions=_positions(),
        portfolio_ret=0.01,
        price_provider=_provider(None),
    )
    assert not inputs.benchmark_available
    assert inputs.benchmark_returns == []
    assert inputs.sector_returns == {}
    assert inputs.factor_returns == {}
    assert any("price_panel_unavailable" in r for r in inputs.degraded_reasons)


def test_benchmark_missing_column_is_fails_closed():
    """面板缺少基准列 -> 基准不可得, 且不得用其他数据替代."""
    panel = _make_panel({"A": [100.0, 110.0], "B": [100.0, 100.0], "C": [100.0, 90.0]})
    inputs = build_real_attribution_inputs(
        report_date=EOD_REPORT,
        positions=_positions(),
        portfolio_ret=0.05,
        price_provider=_provider(panel),
    )
    assert not inputs.benchmark_available
    assert any("benchmark_return_unavailable" in r for r in inputs.degraded_reasons)
    # 行业维度仍可得 (fail-open 观测), 但基准绝不捏造
    assert inputs.sector_returns


def test_stale_data_rejected():
    """数据陈旧 (> MAX_DATA_LAG_DAYS) -> 判为不可得, 不用旧收益冒充当日."""
    dates = pd.date_range(end="2026-08-20", periods=2, freq="B")  # 距 09-01 已 12 天
    panel = pd.DataFrame(
        {
            "A": [100.0, 110.0],
            "B": [100.0, 100.0],
            "C": [100.0, 90.0],
            DEFAULT_BENCHMARK_CODE: [100.0, 102.0],
        },
        index=dates,
    )
    inputs = build_real_attribution_inputs(
        report_date=EOD_REPORT,
        positions=_positions(),
        portfolio_ret=0.05,
        price_provider=_provider(panel),
    )
    assert not inputs.benchmark_available
    assert inputs.sector_returns == {}


# ==================================================================
# 3. 风格因子: 真实截面 + 无前视
# ==================================================================


def _build_factor_panel(n_sym: int = 6):
    """构造可控面板: 过去 20 日动量随序号递增, 当日收益随序号递增.

    symbol i: 过去 20 日收益 = i * 2%, 当日收益 = i * 1%
    """
    n_points = 24  # 需 >= FACTOR_WINDOW + 2
    data: dict[str, list[float]] = {DEFAULT_BENCHMARK_CODE: [100.0] * n_points}
    for i in range(n_sym):
        start = 100.0
        end_past = 100.0 * (1 + i * 0.02)
        past = [
            start + (end_past - start) * k / (n_points - 2)
            for k in range(n_points - 1)
        ]
        last = past[-1] * (1 + i * 0.01)
        data[f"S{i}"] = past + [last]
    return _make_panel(data)


def test_momentum_factor_is_real_long_short():
    panel = _build_factor_panel()
    codes = [f"S{i}" for i in range(6)]
    pos = [{"code": c, "weight": 1.0, "sector": "科技"} for c in codes]
    inputs = build_real_attribution_inputs(
        report_date=EOD_REPORT,
        positions=pos,
        portfolio_ret=0.0,
        price_provider=_provider(panel),
    )
    # 动量最高组 (i=5, 当日 +5%) - 最低组 (i=0, 当日 0%) = 0.05
    assert "momentum" in inputs.factor_returns
    assert inputs.factor_returns["momentum"][0] == pytest.approx(0.05, abs=1e-6)


def test_volatility_factor_is_low_minus_high():
    """A股低波动异象口径: 低波动组 - 高波动组."""
    panel = _build_factor_panel()
    codes = [f"S{i}" for i in range(6)]
    pos = [{"code": c, "weight": 1.0, "sector": "科技"} for c in codes]
    inputs = build_real_attribution_inputs(
        report_date=EOD_REPORT,
        positions=pos,
        portfolio_ret=0.0,
        price_provider=_provider(panel),
    )
    assert "volatility" in inputs.factor_returns
    # i=0 波动最小 (当日 0%), i=5 波动最大 (当日 +5%) -> 0 - 0.05
    assert inputs.factor_returns["volatility"][0] == pytest.approx(-0.05, abs=1e-6)


def _build_panel_custom(past_rets: list[float], day_rets: list[float], n_points: int = 24):
    """自定义面板: past_rets[i] = 标的 i 过去窗口收益, day_rets[i] = 归因日收益."""
    assert len(past_rets) == len(day_rets) == MIN_SYMBOLS_FOR_FACTOR
    data: dict[str, list[float]] = {DEFAULT_BENCHMARK_CODE: [100.0] * n_points}
    for i in range(MIN_SYMBOLS_FOR_FACTOR):
        end_past = 100.0 * (1 + past_rets[i])
        past = [
            100.0 + (end_past - 100.0) * k / (n_points - 2)
            for k in range(n_points - 1)
        ]
        last = past[-1] * (1 + day_rets[i])
        data[f"S{i}"] = past + [last]
    return _make_panel(data)


def test_factor_signal_has_no_lookahead():
    """归因日的极端收益不得进入分组信号 (信号只用归因日之前的收盘).

    构造: S5 的过去窗口收益最高 (信号应排第一), 但归因日暴跌 -10%。
    若信号无前视, S5 仍应落在动量多头组, 因子 = day_ret[S5] - day_ret[S0]
    = -0.10 - 0.0 = -0.10。若回归导致信号把当日暴跌计入, S5 会跌出多头组,
    因子值将随之改变 —— 此断言锁定"信号严格无前视"。
    """
    panel = _build_panel_custom(
        past_rets=[0.0, 0.02, 0.04, 0.06, 0.08, 0.10],
        day_rets=[0.0, 0.01, 0.02, 0.03, 0.04, -0.10],
    )
    codes = [f"S{i}" for i in range(MIN_SYMBOLS_FOR_FACTOR)]
    pos = [{"code": c, "weight": 1.0, "sector": "科技"} for c in codes]
    inputs = build_real_attribution_inputs(
        report_date=EOD_REPORT,
        positions=pos,
        portfolio_ret=0.0,
        price_provider=_provider(panel),
    )
    # S5 多头组 (过去信号最高, 当日 -10%), S0 空头组 (当日 0%)
    assert inputs.factor_returns["momentum"][0] == pytest.approx(-0.10, abs=1e-6)


def test_unavailable_factors_are_not_fabricated():
    """估值/成长/盈利质量/流动性无数据源 -> 明确降级, 绝不捏造数值."""
    panel = _build_factor_panel()
    codes = [f"S{i}" for i in range(6)]
    pos = [{"code": c, "weight": 1.0, "sector": "科技"} for c in codes]
    inputs = build_real_attribution_inputs(
        report_date=EOD_REPORT,
        positions=pos,
        portfolio_ret=0.0,
        price_provider=_provider(panel),
    )
    for f in ("valuation", "growth", "earnings_quality", "liquidity"):
        assert f not in inputs.factor_returns
    assert any(
        "factors_unavailable_not_fabricated" in r for r in inputs.degraded_reasons
    )


def test_insufficient_symbols_skips_factors():
    """截面标的不足时跳过因子, 不用少量标的硬算."""
    panel = _make_panel(
        {
            "A": [100.0, 110.0],
            "B": [100.0, 100.0],
            DEFAULT_BENCHMARK_CODE: [100.0, 100.0],
        }
    )
    pos = [
        {"code": "A", "weight": 0.5, "sector": "科技"},
        {"code": "B", "weight": 0.5, "sector": "科技"},
    ]
    inputs = build_real_attribution_inputs(
        report_date=EOD_REPORT,
        positions=pos,
        portfolio_ret=0.0,
        price_provider=_provider(panel),
    )
    assert inputs.factor_returns == {}
    assert any("factor_cross_section_insufficient" in r for r in inputs.degraded_reasons)


def test_long_short_helper_respects_quantile():
    signal = {f"S{i}": float(i) for i in range(10)}
    day_ret = {f"S{i}": float(i) / 100 for i in range(10)}
    # n = int(10 * 0.3) = 3 -> top = S9,S8,S7 ; bottom = S2,S1,S0
    got = _long_short(signal, day_ret, descending=True)
    expect = (0.09 + 0.08 + 0.07) / 3 - (0.02 + 0.01 + 0.0) / 3
    assert got == pytest.approx(expect)


# ==================================================================
# 4. 交易成本: 成交回报事实源
# ==================================================================


def test_trading_costs_from_fills_buy(monkeypatch):
    import utils.execution.fills_store as fs

    monkeypatch.setattr(
        fs,
        "load_day",
        lambda date, strategies=None: [
            {"filled_qty": 1000, "avg_price": 10.0, "side": "BUY"}
        ],
    )
    panel = _make_panel(
        {
            "A": [100.0, 100.0],
            "B": [100.0, 100.0],
            "C": [100.0, 100.0],
            DEFAULT_BENCHMARK_CODE: [100.0, 100.0],
        }
    )
    inputs = build_real_attribution_inputs(
        report_date=EOD_REPORT,
        positions=_positions(),
        portfolio_ret=0.0,
        price_provider=_provider(panel),
    )
    notional = 1000 * 10.0
    expect = max(notional * COMMISSION_RATE, MIN_COMMISSION) + notional * FEE_RATE
    assert inputs.trading_costs == pytest.approx(expect)
    assert inputs.data_sources["trading_cost"] == "fills_store"


def test_trading_costs_includes_stamp_duty_on_sell(monkeypatch):
    import utils.execution.fills_store as fs

    monkeypatch.setattr(
        fs,
        "load_day",
        lambda date, strategies=None: [
            {"filled_qty": 1000, "avg_price": 10.0, "side": "SELL"}
        ],
    )
    panel = _make_panel(
        {
            "A": [100.0, 100.0],
            "B": [100.0, 100.0],
            "C": [100.0, 100.0],
            DEFAULT_BENCHMARK_CODE: [100.0, 100.0],
        }
    )
    inputs = build_real_attribution_inputs(
        report_date=EOD_REPORT,
        positions=_positions(),
        portfolio_ret=0.0,
        price_provider=_provider(panel),
    )
    notional = 1000 * 10.0
    expect = (
        max(notional * COMMISSION_RATE, MIN_COMMISSION)
        + notional * FEE_RATE
        + notional * STAMP_DUTY_RATE
    )
    assert inputs.trading_costs == pytest.approx(expect)


def test_trading_costs_zero_when_no_fills_is_legitimate(monkeypatch):
    """无成交时成本为 0 是正确的, 但必须可追溯 (区别于旧的硬编码 0)."""
    import utils.execution.fills_store as fs

    monkeypatch.setattr(fs, "load_day", lambda date, strategies=None: [])
    panel = _make_panel(
        {
            "A": [100.0, 100.0],
            "B": [100.0, 100.0],
            "C": [100.0, 100.0],
            DEFAULT_BENCHMARK_CODE: [100.0, 100.0],
        }
    )
    inputs = build_real_attribution_inputs(
        report_date=EOD_REPORT,
        positions=_positions(),
        portfolio_ret=0.0,
        price_provider=_provider(panel),
    )
    assert inputs.trading_costs == 0.0
    assert inputs.data_sources["trading_cost"] == "no_fills"


def test_trading_costs_unavailable_is_flagged(monkeypatch):
    import utils.execution.fills_store as fs

    def _boom(date, strategies=None):
        raise RuntimeError("fills store down")

    monkeypatch.setattr(fs, "load_day", _boom)
    panel = _make_panel(
        {
            "A": [100.0, 100.0],
            "B": [100.0, 100.0],
            "C": [100.0, 100.0],
            DEFAULT_BENCHMARK_CODE: [100.0, 100.0],
        }
    )
    inputs = build_real_attribution_inputs(
        report_date=EOD_REPORT,
        positions=_positions(),
        portfolio_ret=0.0,
        price_provider=_provider(panel),
    )
    assert inputs.trading_costs == 0.0
    assert "trading_cost_unavailable" in inputs.degraded_reasons


# ==================================================================
# 5. 契约与降级标记
# ==================================================================


def test_to_engine_kwargs_shape():
    inputs = RealAttributionInputs(
        benchmark_returns=[0.01],
        market_returns=[0.01],
        factor_returns={"momentum": [0.02]},
        sector_returns={"科技": [0.03]},
        trading_costs=12.5,
        funding_cost=0.0,
    )
    kw = inputs.to_engine_kwargs()
    assert set(kw) == {
        "benchmark_returns",
        "market_returns",
        "factor_returns",
        "sector_returns",
        "trading_costs",
        "funding_cost",
    }
    assert kw["trading_costs"] == 12.5


def test_known_limitations_are_declared():
    """已知保留局限必须显式声明, 避免被误读为真实暴露."""
    panel = _make_panel(
        {
            "A": [100.0, 110.0],
            "B": [100.0, 100.0],
            "C": [100.0, 90.0],
            DEFAULT_BENCHMARK_CODE: [100.0, 102.0],
        }
    )
    inputs = build_real_attribution_inputs(
        report_date=EOD_REPORT,
        positions=_positions(),
        portfolio_ret=0.0,
        price_provider=_provider(panel),
    )
    joined = " ".join(inputs.degraded_reasons)
    assert "style_exposures_are_sector_proxies" in joined
    assert "funding_cost_not_modeled" in joined
    assert inputs.funding_cost == 0.0


def test_no_position_codes_short_circuits():
    inputs = build_real_attribution_inputs(
        report_date=EOD_REPORT,
        positions=[{"weight": 1.0, "sector": "科技"}],
        portfolio_ret=0.0,
        price_provider=_provider(None),
    )
    assert "no_position_codes" in inputs.degraded_reasons
    assert not inputs.benchmark_available


def test_low_sector_coverage_flagged():
    """多数持仓取不到行情 -> 标记覆盖率不足, 而非静默用少数标的代表全行业."""
    panel = _make_panel(
        {
            "A": [100.0, 110.0],  # 仅 A 有数据
            DEFAULT_BENCHMARK_CODE: [100.0, 100.0],
        }
    )
    pos = [
        {"code": "A", "weight": 0.1, "sector": "科技"},
        {"code": "B", "weight": 0.45, "sector": "科技"},
        {"code": "C", "weight": 0.45, "sector": "消费"},
    ]
    inputs = build_real_attribution_inputs(
        report_date=EOD_REPORT,
        positions=pos,
        portfolio_ret=0.0,
        price_provider=_provider(panel),
    )
    assert any("sector_price_coverage_low" in r for r in inputs.degraded_reasons)
