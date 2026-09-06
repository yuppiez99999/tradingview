"""05_01 — 值对象与策略基类单元测试.

覆盖:
    - frozen dataclass 不可变性 (ComboLeg/ComboOrder/ComboResult/ApprovalResult/RollResult)
    - 枚举值正确性 (StrategyType/LegSide/OrderStatus)
    - ComboBase 抽象基类不可实例化
    - OptionChainFetcher DTE 过滤 / BS 降级
    - Greeks 计算性能 (50腿 < 100ms)
"""

from __future__ import annotations

import time
from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from utils.etf_option_combo.combo_base import (
    ApprovalResult,
    ComboBase,
    ComboLeg,
    ComboOrder,
    ComboResult,
    LegSide,
    OptionChainFetcher,
    OrderStatus,
    RollResult,
    StrategyType,
    _generate_expiry_dates,
    _get_expiry_date,
)

pytestmark = pytest.mark.unit


# ============================================================
# 不可变性测试
# ============================================================

class TestComboLegFrozen:
    def test_combo_leg_frozen(self, make_combo_leg):
        """ComboLeg 修改属性抛出 FrozenInstanceError."""
        leg = make_combo_leg()
        with pytest.raises(FrozenInstanceError):
            leg.quantity = 2  # type: ignore[misc]

    def test_combo_leg_field_access(self, make_combo_leg):
        """ComboLeg 字段可读."""
        leg = make_combo_leg(strike=3.15, premium=0.05)
        assert leg.strike == 3.15
        assert leg.premium == 0.05
        assert leg.side == LegSide.SELL
        assert leg.multiplier == 10000

    def test_combo_leg_equality(self, make_combo_leg):
        """同参数 ComboLeg 相等 (值对象语义)."""
        leg1 = make_combo_leg()
        leg2 = make_combo_leg()
        assert leg1 == leg2


class TestComboOrderFrozen:
    def test_combo_order_frozen(self, make_combo_leg):
        """ComboOrder 修改属性抛出 FrozenInstanceError."""
        leg = make_combo_leg()
        order = ComboOrder(
            order_id="test_001",
            strategy_type=StrategyType.COVERED_CALL,
            leg=leg,
            order_type="LIMIT",
            limit_price=0.0525,
            status=OrderStatus.PENDING,
        )
        with pytest.raises(FrozenInstanceError):
            order.status = OrderStatus.FILLED  # type: ignore[misc]

    def test_combo_order_defaults(self, make_combo_leg):
        """ComboOrder 默认值 requires_confirmation=False, reason=''."""
        order = ComboOrder(
            order_id="t1",
            strategy_type=StrategyType.COLLAR,
            leg=make_combo_leg(),
            order_type="LIMIT",
            limit_price=0.05,
            status=OrderStatus.PENDING,
        )
        assert order.requires_confirmation is False
        assert order.reason == ""


class TestComboResultFrozen:
    def test_combo_result_orders_tuple(self, make_combo_leg):
        """ComboResult.orders 为 tuple 不可变."""
        leg = make_combo_leg()
        order = ComboOrder(
            order_id="t1", strategy_type=StrategyType.COVERED_CALL,
            leg=leg, order_type="LIMIT", limit_price=0.05, status=OrderStatus.PENDING,
        )
        result = ComboResult(
            strategy_type=StrategyType.COVERED_CALL,
            underlying="510050.SH",
            orders=(order,),
            greeks=None,  # type: ignore[arg-type]
            net_premium=500.0,
            budget_remaining=1000.0,
            error_code=None,
            error_msg=None,
            generated_at="2026-09-03T12:00:00",
        )
        assert isinstance(result.orders, tuple)
        assert len(result.orders) == 1
        with pytest.raises(FrozenInstanceError):
            result.net_premium = 0.0  # type: ignore[misc]

    def test_combo_result_empty_orders(self):
        """ComboResult 空订单包."""
        result = ComboResult(
            strategy_type=StrategyType.COVERED_CALL,
            underlying="510050.SH",
            orders=(),
            greeks=None,  # type: ignore[arg-type]
            net_premium=0.0,
            budget_remaining=0.0,
            error_code="NO_DATA",
            error_msg="无数据",
            generated_at="2026-09-03T12:00:00",
        )
        assert result.orders == ()
        assert result.error_code == "NO_DATA"


class TestApprovalResultFrozen:
    def test_approval_result_frozen(self):
        """ApprovalResult 不可变."""
        ar = ApprovalResult(
            approved=True,
            orders_approved=(),
            rejected_reason=None,
            risk_flags=(),
            requires_confirmation=False,
        )
        with pytest.raises(FrozenInstanceError):
            ar.approved = False  # type: ignore[misc]
        assert isinstance(ar.orders_approved, tuple)
        assert isinstance(ar.risk_flags, tuple)


class TestRollResultFrozen:
    def test_roll_result_frozen(self):
        """RollResult 不可变."""
        rr = RollResult(
            strategy_instance_id="cc_510050_20260903",
            needs_roll=False,
            close_orders=(),
            open_orders=(),
            roll_cost=0.0,
            expected_benefit=0.0,
            cost_ratio=0.0,
        )
        with pytest.raises(FrozenInstanceError):
            rr.needs_roll = True  # type: ignore[misc]
        assert isinstance(rr.close_orders, tuple)
        assert isinstance(rr.open_orders, tuple)


class TestImmutability:
    def test_immutability_rebuild_new_object(self, make_combo_leg):
        """调仓后 id(原对象)≠id(新对象) — 重建而非原地修改."""
        leg_v1 = make_combo_leg(quantity=1)
        leg_v2 = ComboLeg(
            instrument=leg_v1.instrument,
            underlying=leg_v1.underlying,
            option_type=leg_v1.option_type,
            side=leg_v1.side,
            strike=leg_v1.strike,
            expiry=leg_v1.expiry,
            quantity=2,  # 调仓: 1 -> 2
            multiplier=leg_v1.multiplier,
            premium=leg_v1.premium,
        )
        assert id(leg_v1) != id(leg_v2)
        assert leg_v1.quantity == 1  # 原对象不变
        assert leg_v2.quantity == 2


# ============================================================
# 枚举测试
# ============================================================

class TestEnums:
    def test_strategy_type_enum(self):
        """5 个策略类型枚举值正确."""
        assert StrategyType.COVERED_CALL.value == "covered_call"
        assert StrategyType.COLLAR.value == "collar"
        assert StrategyType.CASH_SECURED_PUT.value == "cash_secured_put"
        assert StrategyType.VERTICAL_SPREAD.value == "vertical_spread"
        assert StrategyType.CALENDAR_SPREAD.value == "calendar_spread"
        assert len(list(StrategyType)) == 5

    def test_leg_side_enum(self):
        assert LegSide.BUY.value == "BUY"
        assert LegSide.SELL.value == "SELL"
        assert len(list(LegSide)) == 2

    def test_order_status_enum(self):
        assert OrderStatus.PENDING.value == "PENDING"
        assert OrderStatus.FILLED.value == "FILLED"
        assert OrderStatus.REJECTED.value == "REJECTED"
        assert OrderStatus.CANCELLED.value == "CANCELLED"
        assert len(list(OrderStatus)) == 4


# ============================================================
# ComboBase 抽象基类测试
# ============================================================

class TestComboBaseAbstract:
    def test_combo_base_abstract_cannot_instantiate(self, chain_fetcher):
        """ComboBase 含抽象方法, 无法实例化 (TypeError)."""
        with pytest.raises(TypeError):
            ComboBase(  # type: ignore[abstract]
                strategy_type=StrategyType.COVERED_CALL,
                config={},
                chain_fetcher=chain_fetcher,
            )

    def test_combo_base_subclass_must_implement(self, chain_fetcher):
        """子类未实现抽象方法时无法实例化."""
        class IncompleteStrategy(ComboBase):
            pass

        with pytest.raises(TypeError):
            IncompleteStrategy(  # type: ignore[abstract]
                strategy_type=StrategyType.COVERED_CALL,
                config={},
                chain_fetcher=chain_fetcher,
            )


# ============================================================
# 到期日工具函数测试
# ============================================================

class TestExpiryDateUtils:
    def test_get_expiry_date_fourth_wednesday(self):
        """中国ETF期权到期日 = 当月第四个周三."""
        # 2026-09: 9月1日是周二, 第一个周三=9月2日, 第四个=9月23日
        exp = _get_expiry_date(2026, 9)
        assert exp == date(2026, 9, 23)
        assert exp.weekday() == 2  # 周三

    def test_get_expiry_date_always_wednesday(self):
        """任意月份到期日均为周三."""
        for year in (2026, 2027):
            for month in range(1, 13):
                exp = _get_expiry_date(year, month)
                assert exp.weekday() == 2, f"{year}-{month} 到期日 {exp} 不是周三"

    def test_generate_expiry_dates_dte_filter(self):
        """_generate_expiry_dates 仅返回 DTE 在范围内的到期日."""
        today = date(2026, 9, 3)
        expiries = _generate_expiry_dates(today, 30, 60)
        for exp in expiries:
            dte = (exp - today).days
            assert 30 <= dte <= 60, f"到期日 {exp} DTE={dte} 越界 [30,60]"


# ============================================================
# OptionChainFetcher 测试
# ============================================================

class TestOptionChainFetcher:
    def test_dte_filter(self, synthetic_option_chain):
        """期权链 DTE ∈ [30, 60]."""
        assert len(synthetic_option_chain) > 0
        for contract in synthetic_option_chain:
            assert 30 <= contract["dte"] <= 60
            assert contract["premium"] > 0
            assert "delta" in contract
            assert "gamma" in contract

    def test_otm_filter_call(self, synthetic_option_chain, fixed_spot_price):
        """CALL 期权链行权价 > 现货价 (OTM)."""
        for contract in synthetic_option_chain:
            assert contract["strike"] > fixed_spot_price

    def test_chain_sorted_by_dte_strike(self, synthetic_option_chain):
        """期权链按 (dte, strike) 升序排列."""
        keys = [(c["dte"], c["strike"]) for c in synthetic_option_chain]
        assert keys == sorted(keys)

    def test_bs_degrade_no_fetcher(self, empty_chain_fetcher, fixed_spot_price):
        """fetcher=None 时返回空列表 (优雅降级)."""
        chain = empty_chain_fetcher.get_option_chain(
            underlying="510050.SH",
            option_type="CALL",
            dte_range=(30, 60),
            otm_range=(0.02, 0.08),
            min_volume=0,
            spot_price=fixed_spot_price,
        )
        assert chain == []

    def test_bs_degrade_failing_fetcher(self, failing_fetcher, fixed_spot_price):
        """fetcher 永远失败时返回空列表 (优雅降级)."""
        fetcher = OptionChainFetcher(fetcher=failing_fetcher, bs_timeout_ms=200)
        chain = fetcher.get_option_chain(
            underlying="510050.SH",
            option_type="CALL",
            dte_range=(30, 60),
            otm_range=(0.02, 0.08),
            min_volume=0,
            spot_price=fixed_spot_price,
        )
        assert chain == []

    def test_no_spot_price_returns_empty(self, chain_fetcher):
        """spot_price=None 且无法获取现货价时返回空列表."""
        chain = chain_fetcher.get_option_chain(
            underlying="UNKNOWN.SH",
            option_type="CALL",
            dte_range=(30, 60),
            spot_price=None,
        )
        assert chain == []

    def test_iv_term_structure(self, chain_fetcher, fixed_spot_price):
        """IV 期限结构返回同行权价不同到期日序列."""
        ts = chain_fetcher.get_iv_term_structure(
            underlying="510050.SH",
            strike=round(fixed_spot_price, 2),
            spot_price=fixed_spot_price,
        )
        assert isinstance(ts, list)
        # 至少有部分到期日
        for item in ts:
            assert "iv" in item
            assert "dte" in item
            assert item["dte"] > 0


# ============================================================
# 性能测试
# ============================================================

class TestGreeksPerformance:
    def test_greeks_calc_performance_50_legs(self, chain_fetcher, fixed_spot_price, make_combo_leg):
        """50 腿 Greeks 计算 < 100ms (批量 _calc_combo_greeks)."""
        from utils.etf_option_combo.combo_base import ComboBase

        # 构造一个完整子类以访问 _calc_combo_greeks
        class DummyStrategy(ComboBase):
            def _select_legs(self, underlying, spot_price, option_chain, spot_position):
                return (None, "DUMMY")

            def _validate_business_rules(self, legs, spot_price):
                return None

        engine = DummyStrategy(
            strategy_type=StrategyType.COVERED_CALL,
            config={},
            chain_fetcher=chain_fetcher,
            greek_manager=None,  # 走空 Greeks 路径
        )

        legs = tuple(make_combo_leg(quantity=1) for _ in range(50))
        t0 = time.perf_counter()
        for _ in range(10):
            greeks = engine._calc_combo_greeks(legs, fixed_spot_price)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert greeks is not None
        assert elapsed_ms < 100, f"50腿Greeks计算 {elapsed_ms:.2f}ms >= 100ms"


class TestPackageInit:
    def test_all_public_symbols_importable(self):
        """__init__.py 11 个公开符号可懒加载导入."""
        import utils.etf_option_combo as pkg

        expected = [
            "ComboOrchestrator", "ComboBase", "CoveredCallEngine", "CollarEngine",
            "CashSecuredPutEngine", "VerticalSpreadEngine", "CalendarSpreadEngine",
            "ComboRiskManager", "ComboStateManager", "ComboBacktest", "OptionChainFetcher",
        ]
        for name in expected:
            assert hasattr(pkg, name), f"缺少公开符号 {name}"
            obj = getattr(pkg, name)
            assert obj is not None, f"{name} 导入为 None"

    def test_unknown_attribute_raises(self):
        """未知属性抛 AttributeError."""
        import utils.etf_option_combo as pkg

        with pytest.raises(AttributeError):
            pkg.NonExistentClass  # noqa: B018 — 故意访问不存在属性触发 __getattr__ 抛错
