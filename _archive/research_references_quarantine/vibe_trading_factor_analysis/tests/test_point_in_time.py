# -*- coding: utf-8 -*-
"""P0-C1 回归测试：验证 point-in-time 财务对齐消除前视偏差

覆盖:
1. _quarter_disclosure_date 披露日规则
2. _point_in_time_fundamentals 按 as_of 截断季度
3. build_factor_history 在历史时点不使用未来财报 (fail-closed 与截断双路径)
"""
import os
import sys
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest

from adapters.factor_history_builder import (
    _parse_date,
    _quarter_disclosure_date,
    _point_in_time_fundamentals,
    build_factor_history,
)


class _FakeAdapter:
    """记录每次 compute_candidate_factors 收到的 fundamentals_history 季度数"""

    def __init__(self):
        self.calls = []  # 每次调用收到的 (as_of_quarters_per_symbol)

    def compute_candidate_factors(self, price_data=None, fundamentals=None,
                                  benchmark_returns=None, fundamentals_history=None):
        # 记录该时点每个标的用了多少个季度
        qcount = {}
        for sym, hist in (fundamentals_history or {}).items():
            if isinstance(hist, dict):
                qcount[sym] = len(hist.get("quarters", []))
        self.calls.append({"fundamentals_history": fundamentals_history,
                           "qcount": qcount, "fundamentals": fundamentals})
        return _FakePool()


class _FakePool:
    class _Cf:
        def __init__(self, values):
            self.values = values

    def __init__(self):
        self.factors = {"TEST": self._Cf({"600519_SH": 1.0})}
        self.total_candidates = 1


def _mk_history(dates: list[str]) -> dict:
    """构造含 dates 的价格数据"""
    n = len(dates)
    return {"600519_SH": {
        "closes": list(100 + np.arange(n, dtype=float)),
        "volumes": [1.0] * n,
        "highs": list(101 + np.arange(n, dtype=float)),
        "lows": list(99 + np.arange(n, dtype=float)),
        "opens": list(100 + np.arange(n, dtype=float)),
        "dates": dates,
    }}


def _mk_fund_history() -> dict:
    """模拟 2025Q4/2026Q1 两季度（倒序，最新在前）"""
    return {"600519_SH": {
        "quarters": [
            {"year": 2026, "quarter": 1, "roe": 0.20, "gross_margin": 0.90},
            {"year": 2025, "quarter": 4, "roe": 0.30, "gross_margin": 0.91},
        ],
        "n_valid": 2,
    }}


class TestQuarterDisclosure:
    def test_q1_disclosure(self):
        assert _quarter_disclosure_date(2026, 1) == (2026, 4, 30)

    def test_q2_disclosure(self):
        assert _quarter_disclosure_date(2026, 2) == (2026, 8, 31)

    def test_q3_disclosure(self):
        assert _quarter_disclosure_date(2026, 3) == (2026, 10, 31)

    def test_q4_disclosure_next_year(self):
        # 2025Q4 年报最迟 2026/4/30（跨年）
        assert _quarter_disclosure_date(2025, 4) == (2026, 4, 30)


class TestPointInTimeFundamentals:
    def _fh(self):
        return {"A": {"quarters": [
            {"year": 2026, "quarter": 1, "roe": 0.20},
            {"year": 2025, "quarter": 4, "roe": 0.30},
            {"year": 2025, "quarter": 3, "roe": 0.28},
        ]}}

    def test_before_q1_disclosure_only_old_quarters(self):
        # as_of=2026-04-01: 2026Q1(4/30披露) 与 2025Q4年报(次年4/30披露) 均未披露,
        # 只有 2025Q3(2025/10/31披露) 可用 -> 1 季度, latest=2025Q3
        aligned, latest = _point_in_time_fundamentals(self._fh(), (2026, 4, 1))
        qs = aligned["A"]["quarters"]
        assert len(qs) == 1
        assert qs[0]["year"] == 2025 and qs[0]["quarter"] == 3
        assert latest["A"]["roe"] == 0.28

    def test_after_q1_disclosure_uses_latest_quarter(self):
        # as_of=2026-05-01: 2026Q1 已披露
        aligned, latest = _point_in_time_fundamentals(self._fh(), (2026, 5, 1))
        qs = aligned["A"]["quarters"]
        assert qs[0]["year"] == 2026 and qs[0]["quarter"] == 1
        assert len(qs) == 3
        assert latest["A"]["roe"] == 0.20

    def test_parse_date_formats(self):
        assert _parse_date("2026-04-01") == (2026, 4, 1)
        assert _parse_date("20260401") == (2026, 4, 1)
        assert _parse_date("garbage") is None


class TestBuildFactorHistoryPointInTime:
    def _mk_long_dates(self, n=80):
        """生成 n 个连续日期，从 2026-01-15 起（start_t=60 落在披露日 4/30 之前）。"""
        base = datetime.date(2026, 1, 15)
        return [(base + datetime.timedelta(days=i)).isoformat() for i in range(n)]

    def test_no_lookahead_with_dates(self):
        # 集成测试: valid_dates 恒为「最后 N 个交易日」(as_of 均在披露日后),
        # 故此处只验证 dates 被正确使用且季度数不越界; 披露日前/后的精确截断
        # 由 TestPointInTimeFundamentals 单测覆盖。
        dates = self._mk_long_dates(120)
        price = _mk_history(dates)
        adapter = _FakeAdapter()
        build_factor_history(
            adapter=adapter,
            price_data=price,
            fundamentals={},
            history_days=5,
            forward_window=1,
            fundamentals_history=_mk_fund_history(),
            dates=dates,
        )
        assert len(adapter.calls) > 0, "主循环应执行"
        # 任何时点季度数都不超过总季度数 2 (不越界), 且 dates 被使用 (qcount 有值)
        for call in adapter.calls:
            qc = call["qcount"].get("600519_SH", 0)
            assert 1 <= qc <= 2, f"季度数越界: {qc}"

    def test_after_disclosure_uses_two_quarters(self):
        # 日期全部在 2026Q1 披露日 (4/30) 之后, 应能用 2 个季度
        dates = ["2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08",
                 "2026-05-11", "2026-05-12", "2026-05-13", "2026-05-14",
                 "2026-05-15", "2026-05-18", "2026-05-19", "2026-05-20",
                 "2026-05-21", "2026-05-22", "2026-05-25", "2026-05-26"]
        price = _mk_history(dates)
        adapter = _FakeAdapter()
        build_factor_history(
            adapter=adapter,
            price_data=price,
            fundamentals={},
            history_days=5,
            forward_window=1,
            fundamentals_history=_mk_fund_history(),
            dates=dates,
        )
        for call in adapter.calls:
            qc = call["qcount"].get("600519_SH", 0)
            assert 1 <= qc <= 2

    def test_fail_closed_without_dates(self):
        # price_data 不含 dates 字段 (且不传 dates 参数)：QualityTrend 历史 fail-closed，
        # 不传 fundamentals_history，避免用未来季度污染历史时点
        dates = self._mk_long_dates(90)
        price = _mk_history(dates)
        # 移除 dates 字段，模拟旧版 price_data（无日期轴）
        no_dates_price = {s: {k: v for k, v in d.items() if k != "dates"}
                          for s, d in price.items()}
        adapter = _FakeAdapter()
        build_factor_history(
            adapter=adapter,
            price_data=no_dates_price,
            fundamentals={},
            history_days=5,
            forward_window=1,
            fundamentals_history=_mk_fund_history(),
            dates=None,  # 不传
        )
        assert len(adapter.calls) > 0, "主循环应执行"
        for call in adapter.calls:
            assert call["fundamentals_history"] is None  # fail-closed
