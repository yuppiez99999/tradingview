"""净值回撤型 L0-L4 熔断器单测 — v9.5 缺口②

覆盖:
    T01 L0 正常（dd > -6%, 验收前 45%）
    T02 L0 验收后权益上限 55%
    T03 L1 警戒（dd = -6%, protection=0.2）
    T04 L2 减仓（dd = -10%, allow_new=False）
    T05 L3 强制对冲（dd = -14%, protection=0.7）
    T06 L4 停止（dd = -18%, equity_cap=0, breach_hard=True）
    T07 边界：-5.99% → L0, -6.0% → L1
    T08 边界：-9.99% → L1, -10.0% → L2
    T09 正值回撤自动取负
    T10 to_drawdown_level 互转桥接
    T11 equity_cap() 按级别查询
    T12 自定义级别覆盖
    T13 阈值非递减报 ValueError
    T14 级别数 != 5 报 ValueError
    T15 to_dict 可序列化
    T16 与 DrawdownCircuitBreaker 语义对齐
"""

from __future__ import annotations

import json

import pytest

from utils.drawdown_breaker import DrawdownCircuitBreaker, DrawdownLevel
from utils.etf_option_combo.nav_drawdown_breaker import (
    CnsLevel,
    NavDrawdownBreaker,
    to_drawdown_level,
)


def _breaker() -> NavDrawdownBreaker:
    return NavDrawdownBreaker()


class TestLevelThresholds:
    def test_t01_l0_normal_pre_acceptance(self):
        d = _breaker().evaluate(-0.03)
        assert d.level == "L0"
        assert d.equity_cap_pct == pytest.approx(0.45)
        assert d.allow_new_buy is True
        assert d.allow_open is True
        assert d.protection_ratio == pytest.approx(0.0)

    def test_t02_l0_post_acceptance_cap(self):
        d = _breaker().evaluate(-0.03, acceptance_passed=True)
        assert d.level == "L0"
        assert d.equity_cap_pct == pytest.approx(0.55)

    def test_t03_l1_watch(self):
        d = _breaker().evaluate(-0.06)
        assert d.level == "L1"
        assert d.allow_new_buy is True
        assert d.protection_ratio == pytest.approx(0.2)
        assert "警戒" in d.action

    def test_t04_l2_reduce(self):
        d = _breaker().evaluate(-0.10)
        assert d.level == "L2"
        assert d.allow_new_buy is False
        assert d.allow_open is False
        assert d.protection_ratio == pytest.approx(0.4)

    def test_t05_l3_force_hedge(self):
        d = _breaker().evaluate(-0.14)
        assert d.level == "L3"
        assert d.protection_ratio == pytest.approx(0.7)
        assert d.allow_open is False

    def test_t06_l4_halt(self):
        d = _breaker().evaluate(-0.18)
        assert d.level == "L4"
        assert d.equity_cap_pct == pytest.approx(0.0)
        assert d.breach_hard_limit is True
        assert d.protection_ratio == pytest.approx(1.0)


class TestBoundaries:
    def test_t07_l0_l1_boundary(self):
        b = _breaker()
        assert b.evaluate(-0.0599).level == "L0"
        assert b.evaluate(-0.06).level == "L1"

    def test_t08_l1_l2_boundary(self):
        b = _breaker()
        assert b.evaluate(-0.0999).level == "L1"
        assert b.evaluate(-0.10).level == "L2"

    def test_t09_positive_drawdown_auto_negate(self):
        d = _breaker().evaluate(0.03)
        assert d.drawdown == pytest.approx(-0.03)
        assert d.level == "L0"


class TestInterop:
    def test_t10_to_drawdown_level_mapping(self):
        assert to_drawdown_level("L0") == "NORMAL"
        assert to_drawdown_level("L1") == "WATCH"
        assert to_drawdown_level("L2") == "REDUCE"
        assert to_drawdown_level("L3") == "FORCE_HEDGE"
        assert to_drawdown_level("L4") == "HALT"
        assert to_drawdown_level("XX") == "NORMAL"

    def test_t11_equity_cap_query(self):
        b = _breaker()
        assert b.equity_cap("L0") == pytest.approx(0.45)
        assert b.equity_cap("L0", acceptance_passed=True) == pytest.approx(0.55)
        assert b.equity_cap("L1") == pytest.approx(0.35)
        assert b.equity_cap("L4") == pytest.approx(0.0)

    def test_t15_to_dict_serializable(self):
        d = _breaker().evaluate(-0.08)
        s = json.dumps(d.to_dict(), ensure_ascii=False)
        assert json.loads(s)["level"] == "L1"

    def test_t16_semantic_align_with_drawdown_breaker(self):
        """NavDrawdownBreaker L0-L4 与 DrawdownCircuitBreaker 语义对齐。"""
        nav = _breaker()
        dcb = DrawdownCircuitBreaker()
        for dd in [-0.03, -0.06, -0.10, -0.14, -0.18]:
            nd = nav.evaluate(dd)
            dd_dcb = dcb.evaluate(dd)
            mapped = to_drawdown_level(nd.level)
            if dd <= -0.12:
                assert nd.allow_new_buy is False
                assert dd_dcb.allow_new_buy is False
            assert mapped in {x.value for x in DrawdownLevel}


class TestCustomConfig:
    def test_t12_custom_levels(self):
        custom = (
            CnsLevel("L0", 0.0, 0.40, True, True, 0.0, "ok"),
            CnsLevel("L1", -0.04, 0.30, True, True, 0.1, "watch"),
            CnsLevel("L2", -0.08, 0.20, False, False, 0.3, "reduce"),
            CnsLevel("L3", -0.12, 0.10, False, False, 0.6, "hedge"),
            CnsLevel("L4", -0.16, 0.0, False, False, 1.0, "halt"),
        )
        b = NavDrawdownBreaker(levels=custom)
        assert b.evaluate(-0.05).level == "L1"
        assert b.equity_cap("L0") == pytest.approx(0.40)

    def test_t13_non_decreasing_thresholds_raise(self):
        bad = (
            CnsLevel("L0", 0.0, 0.45, True, True, 0.0, "ok"),
            CnsLevel("L1", -0.04, 0.35, True, True, 0.2, "watch"),
            CnsLevel("L2", -0.03, 0.25, False, False, 0.4, "reduce"),
            CnsLevel("L3", -0.14, 0.15, False, False, 0.7, "hedge"),
            CnsLevel("L4", -1.0, 0.0, False, False, 1.0, "halt"),
        )
        with pytest.raises(ValueError, match="递减"):
            NavDrawdownBreaker(levels=bad)

    def test_t14_wrong_level_count_raise(self):
        bad = (
            CnsLevel("L0", 0.0, 0.45, True, True, 0.0, "ok"),
            CnsLevel("L1", -0.10, 0.35, True, True, 0.2, "watch"),
        )
        with pytest.raises(ValueError, match="5 级"):
            NavDrawdownBreaker(levels=bad)
