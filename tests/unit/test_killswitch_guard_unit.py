"""test_killswitch_guard_unit.py — KillSwitch L1 守卫单元测试

覆盖要点:
    - apply_killswitch_l1_filter (can_open=False 过滤 BUY)
    - can_open=True 不过滤
    - ks_result=None 不过滤
    - 无 trades 属性容错
    - 异常容错
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from utils.killswitch_guard import apply_killswitch_l1_filter


class _FakeDecision:
    """模拟含 trades 属性的决策对象"""

    def __init__(self, trades):
        self.trades = trades


# ============================================================
# can_open=False → 过滤 BUY
# ============================================================


class TestL1Filter:
    @pytest.mark.unit
    def test_filter_buy_keep_sell(self):
        decision = _FakeDecision([
            {"side": "BUY", "symbol": "600519"},
            {"side": "SELL", "symbol": "300750"},
            {"side": "BUY", "symbol": "688981"},
        ])
        ks_result = {"can_open": False}
        result = apply_killswitch_l1_filter(decision, ks_result, {})

        assert len(decision.trades) == 1
        assert decision.trades[0]["side"] == "SELL"
        assert result["steps"]["killswitch_l1_filter"]["filtered_buy"] == 2
        assert result["steps"]["killswitch_l1_filter"]["remaining"] == 1

    @pytest.mark.unit
    def test_all_buy_filtered(self):
        decision = _FakeDecision([
            {"side": "BUY", "symbol": "600519"},
            {"side": "BUY", "symbol": "300750"},
        ])
        ks_result = {"can_open": False}
        result = apply_killswitch_l1_filter(decision, ks_result, {})

        assert len(decision.trades) == 0
        assert result["steps"]["killswitch_l1_filter"]["filtered_buy"] == 2

    @pytest.mark.unit
    def test_no_buy_no_filter_log(self):
        """全 SELL → 不记录过滤统计 (before == after)"""
        decision = _FakeDecision([
            {"side": "SELL", "symbol": "600519"},
        ])
        ks_result = {"can_open": False}
        result = apply_killswitch_l1_filter(decision, ks_result, {})

        assert len(decision.trades) == 1
        assert "steps" not in result or "killswitch_l1_filter" not in result.get("steps", {})

    @pytest.mark.unit
    def test_empty_trades(self):
        decision = _FakeDecision([])
        ks_result = {"can_open": False}
        apply_killswitch_l1_filter(decision, ks_result, {})
        assert len(decision.trades) == 0


# ============================================================
# can_open=True / ks_result=None → 不过滤
# ============================================================


class TestNoFilter:
    @pytest.mark.unit
    def test_can_open_true_no_filter(self):
        decision = _FakeDecision([
            {"side": "BUY", "symbol": "600519"},
            {"side": "SELL", "symbol": "300750"},
        ])
        ks_result = {"can_open": True}
        apply_killswitch_l1_filter(decision, ks_result, {})

        assert len(decision.trades) == 2  # 不变

    @pytest.mark.unit
    def test_ks_result_none_no_filter(self):
        decision = _FakeDecision([
            {"side": "BUY", "symbol": "600519"},
        ])
        apply_killswitch_l1_filter(decision, None, {})

        assert len(decision.trades) == 1

    @pytest.mark.unit
    def test_ks_result_missing_can_open_key(self):
        """ks_result 无 can_open 键 → 默认 can_open=True, 不过滤"""
        decision = _FakeDecision([
            {"side": "BUY", "symbol": "600519"},
        ])
        ks_result = {}
        apply_killswitch_l1_filter(decision, ks_result, {})

        assert len(decision.trades) == 1


# ============================================================
# 容错
# ============================================================


class TestErrorHandling:
    @pytest.mark.unit
    def test_no_trades_attribute(self):
        """decision 无 trades 属性 → 不崩溃"""
        decision = MagicMock(spec=[])  # 无 trades 属性
        ks_result = {"can_open": False}
        result = apply_killswitch_l1_filter(decision, ks_result, {})
        # 不抛异常, result 原样返回
        assert result == {}

    @pytest.mark.unit
    def test_trades_not_list(self):
        """trades 不是 list → getattr 返回非 list, 遍历可能失败 → 容错"""
        decision = MagicMock()
        decision.trades = "not a list"
        ks_result = {"can_open": False}
        # 不抛异常
        result = apply_killswitch_l1_filter(decision, ks_result, {})
        assert isinstance(result, dict)

    @pytest.mark.unit
    def test_result_none(self):
        """result=None → setdefault 在 None 上调用会抛 AttributeError → 容错"""
        decision = _FakeDecision([{"side": "BUY"}])
        ks_result = {"can_open": False}
        # result=None 会导致 result.setdefault 抛 AttributeError
        # 但被 except 捕获
        apply_killswitch_l1_filter(decision, ks_result, None)  # type: ignore[arg-type]
        # trades 仍被过滤 (在 setdefault 之前)
        assert len(decision.trades) == 0
