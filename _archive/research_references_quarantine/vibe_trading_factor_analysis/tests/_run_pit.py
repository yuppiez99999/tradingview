# -*- coding: utf-8 -*-
"""直接运行 point-in-time 核心逻辑验证 (绕过 pytest 输出管道限制)"""
import os
import sys
import datetime

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from adapters.factor_history_builder import (
    _parse_date,
    _quarter_disclosure_date,
    _point_in_time_fundamentals,
    build_factor_history,
)


class _FakeAdapter:
    def __init__(self):
        self.calls = []

    def compute_candidate_factors(self, price_data=None, fundamentals=None,
                                  benchmark_returns=None, fundamentals_history=None):
        qcount = {}
        for sym, hist in (fundamentals_history or {}).items():
            if isinstance(hist, dict):
                qcount[sym] = len(hist.get("quarters", []))
        self.calls.append({"qcount": qcount, "fundamentals_history": fundamentals_history})
        return _FakePool()


class _FakePool:
    class _Cf:
        def __init__(self, values):
            self.values = values

    def __init__(self):
        self.factors = {"TEST": self._Cf({"600519_SH": 1.0})}
        self.total_candidates = 1


def _mk_history(dates):
    n = len(dates)
    return {"600519_SH": {
        "closes": list(100 + np.arange(n, dtype=float)),
        "volumes": [1.0] * n,
        "highs": list(101 + np.arange(n, dtype=float)),
        "lows": list(99 + np.arange(n, dtype=float)),
        "opens": list(100 + np.arange(n, dtype=float)),
        "dates": dates,
    }}


def _mk_fund_history():
    return {"600519_SH": {
        "quarters": [
            {"year": 2026, "quarter": 1, "roe": 0.20, "gross_margin": 0.90},
            {"year": 2025, "quarter": 4, "roe": 0.30, "gross_margin": 0.91},
        ],
        "n_valid": 2,
    }}


# === 1. 披露日规则 ===
assert _quarter_disclosure_date(2026, 1) == (2026, 4, 30)
assert _quarter_disclosure_date(2026, 2) == (2026, 8, 31)
assert _quarter_disclosure_date(2026, 3) == (2026, 10, 31)
assert _quarter_disclosure_date(2025, 4) == (2026, 4, 30)
print("✓ 披露日规则 OK")

# === 2. point-in-time 截断 ===
fh = {"A": {"quarters": [
    {"year": 2026, "quarter": 1, "roe": 0.20},
    {"year": 2025, "quarter": 4, "roe": 0.30},
    {"year": 2025, "quarter": 3, "roe": 0.28},
]}}
al1, lat1 = _point_in_time_fundamentals(fh, (2026, 4, 1))
assert len(al1["A"]["quarters"]) == 1 and al1["A"]["quarters"][0]["quarter"] == 3
assert lat1["A"]["roe"] == 0.28
al2, lat2 = _point_in_time_fundamentals(fh, (2026, 5, 1))
assert len(al2["A"]["quarters"]) == 3 and al2["A"]["quarters"][0]["quarter"] == 1
assert lat2["A"]["roe"] == 0.20
print("✓ point-in-time 截断 OK")

# === 3. build_factor_history 集成校验 (dates 正确传递 + 季度数不越界) ===
# 说明: valid_dates 恒为「最后 N 个交易日」(此处为最后 5 天, as_of 均在披露日后),
# 因此本集成测试只验证 dates 被正确使用且季度数不越界; 披露日前/后的精确截断
# 由单元测试 _point_in_time_fundamentals 覆盖 (见下方 === 2 === 已通过)。
base = datetime.date(2026, 1, 15)
dates = [(base + datetime.timedelta(days=i)).isoformat() for i in range(120)]
price = _mk_history(dates)
adapter = _FakeAdapter()
build_factor_history(adapter=adapter, price_data=price, fundamentals={},
                     history_days=5, forward_window=1,
                     fundamentals_history=_mk_fund_history(), dates=dates)
assert len(adapter.calls) > 0, "主循环应执行"
# 任何时点季度数都不超过总季度数 2 (不越界), 且 dates 被使用 (qcount 有值)
for c in adapter.calls:
    qc = c["qcount"].get("600519_SH", 0)
    assert 1 <= qc <= 2, f"季度数越界: {qc}"
print(f"✓ build_factor_history 集成校验 OK (calls={len(adapter.calls)}, 季度数不越界)")

# === 4. fail-closed (price_data 无 dates, 且不传 dates 参数) ===
# 构造不含 dates 字段的 price_data, 验证 QualityTrend 历史 fail-closed (不传 fundamentals_history)
no_dates_price = {}
for sym, data in price.items():
    no_dates_price[sym] = {k: v for k, v in data.items() if k != "dates"}
adapter2 = _FakeAdapter()
build_factor_history(adapter=adapter2, price_data=no_dates_price, fundamentals={},
                     history_days=5, forward_window=1,
                     fundamentals_history=_mk_fund_history(), dates=None)
assert len(adapter2.calls) > 0, "主循环应执行"
assert all(c["fundamentals_history"] is None for c in adapter2.calls), \
    f"fail-closed 应传 None, 实际={[c['qcount'] for c in adapter2.calls]}"
print("✓ fail-closed (price_data 无 dates) OK")

# === 5. 日期解析 ===
assert _parse_date("2026-04-01") == (2026, 4, 1)
assert _parse_date("20260401") == (2026, 4, 1)
assert _parse_date("garbage") is None
print("✓ 日期解析 OK")

print("\n全部 P0-C1 回归测试通过")
