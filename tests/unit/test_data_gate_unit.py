"""data_gate 单元测试.

被测模块: utils/data_gate.py
覆盖目标: >=90%

测试数据质量门控: 基础质量分、新鲜度(行情/宏观)、多源价格偏离、
价格异常、score clamp、DataGateResult.to_dict。
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

from utils.datetime_utils import now_bj

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.data_gate import DataGate, DataGateResult  # noqa: E402


class DataGateResultTest:
    """DataGateResult dataclass 测试."""

    def test_default_values(self):
        r = DataGateResult()
        assert r.allowed is True
        assert r.quality_score == 100.0
        assert r.freshness_minutes == 0.0
        assert r.deviation_pct == 0.0
        assert r.reasons == []
        assert r.meta == {}

    def test_to_dict_default(self):
        r = DataGateResult()
        d = r.to_dict()
        assert d == {
            "allowed": True,
            "quality_score": 100.0,
            "freshness_minutes": 0.0,
            "deviation_pct": 0.0,
            "reasons": [],
            "meta": {},
        }

    def test_to_dict_rounding(self):
        r = DataGateResult(
            allowed=False,
            quality_score=88.888,
            freshness_minutes=5.5555,
            deviation_pct=1.23456,
            reasons=["x"],
            meta={"k": "v"},
        )
        d = r.to_dict()
        assert d["allowed"] is False
        assert d["quality_score"] == 88.89
        assert d["freshness_minutes"] == 5.56
        assert d["deviation_pct"] == 1.2346
        assert d["reasons"] == ["x"]
        assert d["meta"] == {"k": "v"}

    def test_meta_and_reasons_are_independent_instances(self):
        r1 = DataGateResult()
        r2 = DataGateResult()
        r1.reasons.append("a")
        r1.meta["x"] = 1
        assert r2.reasons == []
        assert r2.meta == {}


class DataGateTest:
    """DataGate 主类测试."""

    # ------ __init__ ------
    def test_init_defaults(self):
        g = DataGate()
        assert g.min_quality_score == 80.0
        assert g.max_price_deviation_pct == 1.0
        assert g.max_freshness_minutes == 15.0
        assert g.max_macro_freshness_hours == 24.0
        assert g.freshness_penalty == 25.0
        assert g.critical_penalty == 60.0

    def test_init_custom_params_coerced_to_float(self):
        g = DataGate(
            min_quality_score=90,
            max_price_deviation_pct=0.5,
            max_freshness_minutes=10,
            max_macro_freshness_hours=12,
            freshness_penalty=30,
            critical_penalty=50,
        )
        assert g.min_quality_score == 90.0
        assert g.max_price_deviation_pct == 0.5
        assert g.max_freshness_minutes == 10.0
        assert g.max_macro_freshness_hours == 12.0
        assert g.freshness_penalty == 30.0
        assert g.critical_penalty == 50.0

    # ------ check_and_gate: 全部正常 ------
    def test_check_all_clean(self):
        g = DataGate()
        now = now_bj()
        snap = {"price": 10.0, "quality_score": 95, "timestamp": now, "source": "A"}
        r = g.check_and_gate("600519", snap)
        assert r.allowed is True
        assert r.reasons == []
        assert r.quality_score == 95.0
        assert r.freshness_minutes >= 0.0

    # ------ 基础质量分异常 ------
    def test_quality_score_invalid_string_keeps_default_100(self):
        g = DataGate()
        snap = {"price": 10.0, "quality_score": "abc"}
        r = g.check_and_gate("X", snap)
        # 异常时 score 保持 100, 且无 reasons → allowed
        assert r.quality_score == 100.0
        assert r.allowed is True

    def test_quality_score_none_keeps_default(self):
        g = DataGate()
        snap = {"price": 10.0}
        r = g.check_and_gate("X", snap)
        assert r.quality_score == 100.0
        assert r.allowed is True

    def test_quality_score_low_blocks(self):
        g = DataGate(min_quality_score=80.0)
        snap = {"price": 10.0, "quality_score": 50}
        r = g.check_and_gate("X", snap)
        # score=50 < 80, 但 reasons 空 → allowed = (50>=80) and not [] = False
        assert r.allowed is False
        assert r.quality_score == 50.0

    # ------ 新鲜度 ------
    def test_freshness_no_timestamp_skipped(self):
        g = DataGate()
        snap = {"price": 10.0}
        r = g.check_and_gate("X", snap)
        assert r.freshness_minutes == 0.0
        assert r.allowed is True

    def test_freshness_recent_quote_allowed(self):
        g = DataGate()
        now = now_bj()
        snap = {"price": 10.0, "timestamp": now}
        r = g.check_and_gate("X", snap)
        assert r.freshness_minutes < 1.0
        assert r.allowed is True

    def test_freshness_stale_quote_blocked(self):
        g = DataGate()
        old = now_bj() - timedelta(minutes=30)
        snap = {"price": 10.0, "timestamp": old}
        r = g.check_and_gate("X", snap)
        assert r.freshness_minutes >= 30.0
        assert r.allowed is False
        assert any("行情数据延迟" in x for x in r.reasons)

    def test_freshness_stale_macro_blocked(self):
        g = DataGate()
        old = now_bj() - timedelta(hours=30)
        snap = {"price": 10.0, "timestamp": old}
        r = g.check_and_gate("X", snap, is_macro=True)
        assert r.allowed is False
        assert any("宏观数据过期" in x for x in r.reasons)

    def test_freshness_macro_fresh_allowed(self):
        g = DataGate()
        now = now_bj()
        snap = {"price": 10.0, "timestamp": now}
        r = g.check_and_gate("X", snap, is_macro=True)
        assert r.allowed is True

    def test_freshness_macro_boundary_just_at_limit(self):
        # 边界: freshness_minutes == max_macro_freshness_hours * 60 不触发 (> 才触发)
        g = DataGate(max_macro_freshness_hours=1.0)
        # 1小时前 - 几秒, freshness < 60 分钟 → 不触发
        ts = now_bj() - timedelta(minutes=59, seconds=50)
        snap = {"price": 10.0, "timestamp": ts}
        r = g.check_and_gate("X", snap, is_macro=True)
        assert r.allowed is True

    # ------ 多源偏离 ------
    def test_deviation_no_peers(self):
        g = DataGate()
        snap = {"price": 10.0}
        r = g.check_and_gate("X", snap, peers={})
        assert r.deviation_pct == 0.0
        assert r.allowed is True

    def test_deviation_within_threshold(self):
        g = DataGate()
        snap = {"price": 10.0}
        peers = {"p1": {"price": 10.05}}
        r = g.check_and_gate("X", snap, peers=peers)
        assert r.deviation_pct == pytest.approx(0.5)
        assert r.allowed is True

    def test_deviation_exceeds_threshold_blocked(self):
        g = DataGate()
        snap = {"price": 10.0}
        peers = {"p1": {"price": 11.5}}
        r = g.check_and_gate("X", snap, peers=peers)
        assert r.deviation_pct == pytest.approx(15.0)
        assert r.allowed is False
        assert any("跨源价格偏离" in x for x in r.reasons)

    def test_deviation_multiple_peers_takes_max(self):
        g = DataGate()
        snap = {"price": 10.0}
        peers = {"p1": {"price": 10.1}, "p2": {"price": 12.0}}
        r = g.check_and_gate("X", snap, peers=peers)
        assert r.deviation_pct == pytest.approx(20.0)

    # ------ 价格异常 ------
    def test_price_missing_blocked(self):
        g = DataGate()
        r = g.check_and_gate("X", {})
        assert r.allowed is False
        assert any("价格缺失" in x for x in r.reasons)

    def test_price_zero_blocked(self):
        g = DataGate()
        r = g.check_and_gate("X", {"price": 0})
        assert r.allowed is False

    def test_price_negative_blocked(self):
        g = DataGate()
        r = g.check_and_gate("X", {"price": -5.0})
        assert r.allowed is False

    def test_price_nan_blocked(self):
        g = DataGate()
        r = g.check_and_gate("X", {"price": float("nan")})
        assert r.allowed is False

    def test_price_inf_blocked(self):
        g = DataGate()
        r = g.check_and_gate("X", {"price": float("inf")})
        assert r.allowed is False

    def test_price_invalid_string_blocked(self):
        g = DataGate()
        r = g.check_and_gate("X", {"price": "abc"})
        assert r.allowed is False

    # ------ score clamp ------
    def test_score_clamped_to_zero(self):
        g = DataGate()
        old = now_bj() - timedelta(hours=100)
        snap = {"price": 10.0, "timestamp": old, "quality_score": 10}
        peers = {"p1": {"price": 20.0}}
        r = g.check_and_gate("X", snap, peers=peers, is_macro=True)
        assert r.quality_score == 0.0
        assert r.allowed is False
        assert len(r.reasons) >= 2

    def test_score_clamped_to_hundred(self):
        # quality_score > 100 应被 clamp 到 100
        g = DataGate()
        snap = {"price": 10.0, "quality_score": 150}
        r = g.check_and_gate("X", snap)
        assert r.quality_score == 100.0

    # ------ to_dict via check_and_gate ------
    def test_result_to_dict_after_check(self):
        g = DataGate()
        r = g.check_and_gate("X", {"price": 10.0})
        d = r.to_dict()
        assert "allowed" in d
        assert "quality_score" in d
        assert isinstance(d["reasons"], list)

    # ------ _freshness_minutes 直接测试 ------
    def test_freshness_minutes_none(self):
        g = DataGate()
        assert g._freshness_minutes(None) == 1e9

    def test_freshness_minutes_empty_string(self):
        g = DataGate()
        assert g._freshness_minutes("") == 1e9

    def test_freshness_minutes_iso_string(self):
        g = DataGate()
        now = now_bj()
        result = g._freshness_minutes(now.isoformat())
        assert 0.0 <= result < 1.0

    def test_freshness_minutes_datetime(self):
        g = DataGate()
        result = g._freshness_minutes(now_bj())
        assert 0.0 <= result < 1.0

    def test_freshness_minutes_int_returns_large(self):
        g = DataGate()
        assert g._freshness_minutes(12345) == 1e9

    def test_freshness_minutes_list_returns_large(self):
        g = DataGate()
        assert g._freshness_minutes([]) == 1e9

    def test_freshness_minutes_invalid_string(self):
        g = DataGate()
        assert g._freshness_minutes("not-a-date") == 1e9

    def test_freshness_minutes_future_clamped_to_zero(self):
        g = DataGate()
        future = now_bj() + timedelta(days=1)
        assert g._freshness_minutes(future) == 0.0

    # ------ _price_deviation 直接测试 ------
    def test_price_deviation_no_base_price(self):
        g = DataGate()
        assert g._price_deviation({}, {"p": {"price": 10}}) == 0.0

    def test_price_deviation_no_peers(self):
        g = DataGate()
        assert g._price_deviation({"price": 10}, {}) == 0.0

    def test_price_deviation_base_invalid_string(self):
        g = DataGate()
        assert g._price_deviation({"price": "abc"}, {"p": {"price": 10}}) == 0.0

    def test_price_deviation_base_zero(self):
        g = DataGate()
        assert g._price_deviation({"price": 0}, {"p": {"price": 10}}) == 0.0

    def test_price_deviation_base_negative(self):
        g = DataGate()
        assert g._price_deviation({"price": -5}, {"p": {"price": 10}}) == 0.0

    def test_price_deviation_peer_none_skipped(self):
        g = DataGate()
        assert g._price_deviation({"price": 10}, {"p": {"price": None}}) == 0.0

    def test_price_deviation_peer_invalid_skipped(self):
        g = DataGate()
        assert g._price_deviation({"price": 10}, {"p": {"price": "x"}}) == 0.0

    def test_price_deviation_peer_zero_skipped(self):
        g = DataGate()
        assert g._price_deviation({"price": 10}, {"p": {"price": 0}}) == 0.0

    def test_price_deviation_peer_negative_skipped(self):
        g = DataGate()
        assert g._price_deviation({"price": 10}, {"p": {"price": -5}}) == 0.0

    def test_price_deviation_normal(self):
        g = DataGate()
        d = g._price_deviation({"price": 10}, {"p": {"price": 11}})
        assert d == pytest.approx(10.0)

    def test_price_deviation_mixed_peers_takes_valid_max(self):
        g = DataGate()
        d = g._price_deviation(
            {"price": 10},
            {"p1": {"price": 11}, "p2": {"price": None}, "p3": {"price": "x"}},
        )
        assert d == pytest.approx(10.0)

    # ------ _valid_price 直接测试 ------
    def test_valid_price_none(self):
        g = DataGate()
        assert g._valid_price(None) is False

    def test_valid_price_normal(self):
        g = DataGate()
        assert g._valid_price(10.0) is True
        assert g._valid_price(0.001) is True

    def test_valid_price_zero(self):
        g = DataGate()
        assert g._valid_price(0) is False

    def test_valid_price_negative(self):
        g = DataGate()
        assert g._valid_price(-1.0) is False

    def test_valid_price_nan(self):
        g = DataGate()
        assert g._valid_price(float("nan")) is False

    def test_valid_price_inf(self):
        g = DataGate()
        assert g._valid_price(float("inf")) is False

    def test_valid_price_neg_inf(self):
        g = DataGate()
        assert g._valid_price(float("-inf")) is False

    def test_valid_price_invalid_string(self):
        g = DataGate()
        assert g._valid_price("abc") is False

    def test_valid_price_string_numeric(self):
        g = DataGate()
        assert g._valid_price("10.5") is True
