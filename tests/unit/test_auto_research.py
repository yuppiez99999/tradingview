"""AutoFactorResearch 闭环回归测试.

验证:
  1) Reviewer 计算的是真实 IC, 而非 rd_agent_quant 旧骨架的硬编码 0.04
  2) 含结构的数据: 引擎能区分"有效/无效"因子 (接受部分, 拒绝部分)
  3) 纯噪声数据: 引擎不通过任何因子 (不橡皮图章)
  4) ML 组合阶段在 LightGBM 可用时产出增量 IC
  5) Wind MCP 真实数据接入: 转换 + run_cycle_on_wind 端到端 (mock 拉取, 无网络)
"""

import logging

import numpy as np
import pytest

from utils.alpha_factor.auto_research import (
    AutoFactorResearch,
    _records_to_price_data,
    _synthetic_price_data,
    fetch_price_data_via_wind,
    quick_check,
)

# 抑制表达式引擎逐切片日志刷屏
logging.getLogger("alpha_factor.expression_engine").setLevel(logging.WARNING)


# ---- Wind MCP 字段格式 fixtures (模拟 Wind 返回的中文/英文列名) ----
def _fake_kline_records(n=120, cn=True, seed=3):
    """模拟 Wind MCP get_stock_kline 返回的 K 线记录列表."""
    rng = np.random.default_rng(seed)
    cols = (
        ["时间", "开盘价", "最高价", "最低价", "收盘价", "成交量"]
        if cn
        else ["date", "open", "high", "low", "close", "volume"]
    )
    recs = []
    price = 10.0
    for i in range(n):
        ret = rng.normal(0, 0.02)
        o = price
        c = max(1.0, o * (1 + ret))
        h = max(o, c) * (1 + abs(rng.normal(0, 0.005)))
        lo = min(o, c) * (1 - abs(rng.normal(0, 0.005)))
        recs.append(
            {
                cols[0]: f"2024-01-{i%28+1:02d}",
                cols[1]: round(o, 2),
                cols[2]: round(h, 2),
                cols[3]: round(lo, 2),
                cols[4]: round(c, 2),
                cols[5]: int(rng.integers(1e5, 5e5)),
            }
        )
        price = c
    return recs


def test_records_to_price_data_cn_columns():
    recs = _fake_kline_records(n=120, cn=True)
    pd = _records_to_price_data(recs, "X")
    assert pd is not None
    assert len(pd["closes"]) == 120
    assert len(pd["opens"]) == len(pd["highs"]) == len(pd["lows"]) == len(pd["volumes"])
    assert all(isinstance(v, float) for v in pd["closes"])
    # 缺失 O/H/L 时仍有兜底值
    recs2 = [
        {"收盘价": 12.3, "时间": "2024-01-01"},
        {"收盘价": 9.9, "时间": "2024-01-02"},
    ]
    pd2 = _records_to_price_data(recs2, "Y")
    assert pd2 and pd2["closes"] == [12.3, 9.9] and pd2["opens"] == [12.3, 9.9]


def test_records_to_price_data_en_columns():
    recs = _fake_kline_records(n=60, cn=False)
    pd = _records_to_price_data(recs, "X")
    assert pd is not None and len(pd["closes"]) == 60


def test_fetch_via_wind_mock(monkeypatch):
    """mock 掉 wind_get_kline, 验证转换 + fail-open 路径 (无需真实网络)."""
    recs_map = {
        "600036.SH": _fake_kline_records(n=150, cn=True, seed=1),
        "000001.SZ": _fake_kline_records(n=150, cn=True, seed=2),
        "BAD.SH": [],  # 无数据 → 应被跳过
    }
    # 构造一个最小 wind_mcp_fetcher 模块并注入 sys.modules
    import sys
    import types

    mod = types.ModuleType("tools.wind_mcp_fetcher")

    def _fake(windcode, days=300, is_fund=False, adjust=1):
        return recs_map.get(windcode, [])

    mod.wind_get_kline = _fake
    mod.KLINE_ADJUST_QFQ = 1
    sys.modules["tools.wind_mcp_fetcher"] = mod

    out = fetch_price_data_via_wind(["600036.SH", "000001.SZ", "BAD.SH"], days=150)
    assert set(out.keys()) == {"600036.SH", "000001.SZ"}, "BAD.SH 应被跳过"
    assert len(out["600036.SH"]["closes"]) == 150


def test_run_cycle_on_wind_mock(monkeypatch):
    """run_cycle_on_wind 在 mock 数据上应端到端跑通且数据驱动."""
    recs_map = {
        "600036.SH": _fake_kline_records(n=200, cn=True, seed=11),
        "000001.SZ": _fake_kline_records(n=200, cn=True, seed=22),
    }
    import sys
    import types

    mod = types.ModuleType("tools.wind_mcp_fetcher")
    mod.wind_get_kline = lambda code, **kw: recs_map.get(code, [])
    mod.KLINE_ADJUST_QFQ = 1
    sys.modules["tools.wind_mcp_fetcher"] = mod

    ar = AutoFactorResearch(use_llm=False)
    report, price_data = ar.run_cycle_on_wind(["600036.SH", "000001.SZ"], days=200)
    assert len(price_data) == 2
    assert report.n_proposed == 6
    assert report.n_implemented == 6
    assert not any(
        abs(v.ic_mean - 0.04) < 1e-9 for v in report.reviews.values()
    )  # 非硬编码


def _noise_price_data(n_symbols=24, n_days=200, seed=7):
    """纯随机游走价格 (无截面结构) — 任何因子 IC 应≈0."""
    rng = np.random.default_rng(seed)
    pd: dict = {}
    for s in range(n_symbols):
        closes = [float(rng.uniform(8, 60))]
        vols = [float(rng.uniform(1e6, 5e6))]
        for _ in range(1, n_days):
            closes.append(max(0.5, closes[-1] * (1 + rng.normal(0, 0.02))))
            vols.append(float(rng.uniform(1e6, 5e6)))
        highs = [c * (1 + abs(rng.normal(0, 0.01))) for c in closes]
        lows = [c * (1 - abs(rng.normal(0, 0.01))) for c in closes]
        opens = [closes[i - 1] if i > 0 else closes[0] for i in range(n_days)]
        pd[f"N{s:03d}"] = {
            "closes": closes,
            "opens": opens,
            "highs": highs,
            "lows": lows,
            "volumes": vols,
        }
    return pd


def test_quick_check_not_hardcoded():
    chk = quick_check()
    assert chk["n_implemented"] > 0, "Developer 未实现任何因子"
    assert chk["n_proposed"] == 6, "应提案 6 个规则模板"
    assert not chk["is_old_hardcode"], "Reviewer 仍输出硬编码 0.04"
    # 真实 IC 应是多值 (数据驱动), 而非全相等
    ic = chk["ic_values"]
    assert len(set(round(v, 6) for v in ic)) > 1, "所有 IC 相同, 疑似未真实评估"


def test_structured_data_distinguishes_factors():
    """含动量结构的数据: 应接受部分且拒绝部分 (证明真评估)."""
    price_data = _synthetic_price_data(seed=42)
    ar = AutoFactorResearch(use_llm=False)
    report = ar.run_cycle(price_data)
    assert 1 <= report.n_accepted <= 5, f"接受数异常: {report.n_accepted}"
    assert report.n_rejected >= 1, "应至少拒绝 1 个无效因子"
    assert report.n_accepted + report.n_rejected == 6


def test_noise_rejected():
    """纯噪声数据: 引擎不通过任何因子 (不橡皮图章)."""
    price_data = _noise_price_data()
    ar = AutoFactorResearch(use_llm=False)
    report = ar.run_cycle(price_data)
    assert report.n_accepted == 0, f"噪声数据不应有接受因子, 实际 {report.n_accepted}"
    # 但引擎应正常跑完 (无崩溃)
    assert report.n_proposed == 6 and report.n_implemented == 6


def test_ml_combo_runs_on_accepted():
    """接受因子后 ML 组合阶段应产出结果 (LightGBM 或退化)."""
    price_data = _synthetic_price_data(seed=42)
    ar = AutoFactorResearch(use_llm=False)
    ar.run_cycle(price_data)
    combo = ar.combine_accepted()
    assert combo["status"] in ("ok", "skip")
    if combo["status"] == "ok":
        assert "combined_ic" in combo
        assert combo["n_factors"] >= 2


def test_llm_path_fails_open():
    """use_llm=True 但 GLM-5 不可用时, 应降级到规则模板 (不崩溃)."""
    price_data = _synthetic_price_data(seed=42)
    ar = AutoFactorResearch(use_llm=True)
    report = ar.run_cycle(price_data)
    # 即使 LLM 不可用, 规则模板仍应被评估
    assert report.n_proposed >= 6


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
