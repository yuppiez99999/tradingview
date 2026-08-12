"""W6.3.3 Step 2: utils.contracts.registry 单元测试。

覆盖:
    - 内置 13 个品种注册正确 (CU/AU/T/AG/SC/I/RB/M/Y + IF/IC/IH/IM)
    - 与 directional_futures_trader.CONTRACT_SPECS 对齐 (CU/AU/T 3 个详)
    - 与 managers.SUPPORTED_COMMODITIES 对齐 (8 商品 name/exchange/unit)
    - lookup 大小写不敏感
    - lookup_by_symbol 提取 product 前缀
    - is_supported / all_products / count
    - register (新品种) / update (覆盖) / register 冲突抛错
    - ContractSpec 不可变 (frozen)
    - default_registry 单例
"""
from __future__ import annotations

import pytest

from utils.contracts.registry import (
    ContractRegistry,
    ContractSpec,
    default_registry,
)


# ============================================================
# 1. 内置品种注册正确
# ============================================================


class TestBuiltinSpecs:
    def test_count_13_builtins(self) -> None:
        """内置 13 个品种: 9 商品 + 4 股指。"""
        assert default_registry.count() == 13

    def test_all_products_sorted(self) -> None:
        products = default_registry.all_products()
        assert products == sorted(products)
        assert "CU" in products
        assert "IF" in products
        assert "T" in products

    def test_cu_spec_correct(self) -> None:
        spec = default_registry.lookup("CU")
        assert spec is not None
        assert spec.product == "CU"
        assert spec.name == "沪铜期货"
        assert spec.exchange == "SHFE"
        assert spec.multiplier == 5
        assert spec.margin_rate == 0.09
        assert spec.tick_size == 10
        assert spec.price_unit == "元/吨"
        assert spec.unit == "吨"
        assert spec.asset_type == "COMMODITY_FUTURE"

    def test_au_spec_correct(self) -> None:
        spec = default_registry.lookup("AU")
        assert spec is not None
        assert spec.name == "黄金期货"
        assert spec.multiplier == 1000
        assert spec.margin_rate == 0.06

    def test_t_bond_future(self) -> None:
        spec = default_registry.lookup("T")
        assert spec is not None
        assert spec.exchange == "CFFEX"
        assert spec.asset_type == "BOND_FUTURE"
        assert spec.multiplier == 10_000

    def test_sc_ine(self) -> None:
        """难点 §2.1: SC 原油 INE 必须在注册表中。"""
        spec = default_registry.lookup("SC")
        assert spec is not None
        assert spec.exchange == "INE"
        assert spec.name == "原油期货"

    def test_index_futures(self) -> None:
        for p, mult in [("IF", 300), ("IC", 200), ("IH", 300), ("IM", 200)]:
            spec = default_registry.lookup(p)
            assert spec is not None
            assert spec.multiplier == mult
            assert spec.exchange == "CFFEX"
            assert spec.asset_type == "INDEX_FUTURE"


# ============================================================
# 2. 与 directional_futures_trader.CONTRACT_SPECS 对齐 (CU/AU/T)
# ============================================================


class TestAlignWithDirectionalFuturesTrader:
    """确保 registry 的 CU/AU/T 与 directional_futures_trader.CONTRACT_SPECS 一致。"""

    def test_cu_au_t_align(self) -> None:
        from utils.directional_futures_trader import CONTRACT_SPECS

        for product in ("CU", "AU", "T"):
            old_spec = CONTRACT_SPECS[product]
            new_spec = default_registry.lookup(product)
            assert new_spec is not None
            assert new_spec.name == old_spec["name"]
            assert new_spec.exchange == old_spec["exchange"]
            assert new_spec.multiplier == old_spec["multiplier"]
            assert new_spec.margin_rate == old_spec["margin_rate"]
            assert new_spec.tick_size == old_spec["tick_size"]
            assert new_spec.price_unit == old_spec["price_unit"]
            assert new_spec.purpose == old_spec["purpose"]
            assert new_spec.default_direction == old_spec["default_direction"]


# ============================================================
# 3. 与 managers.SUPPORTED_COMMODITIES 对齐 (8 商品)
# ============================================================


class TestAlignWithManagers:
    """确保 registry 的 8 个商品 name/exchange/unit 与 managers 表一致。"""

    def test_8_commodities_align(self) -> None:
        from utils.attribution.managers import SUPPORTED_COMMODITIES

        for item in SUPPORTED_COMMODITIES:
            spec = default_registry.lookup(item["code"])
            assert spec is not None, f"品种 {item['code']} 未注册"
            assert item["name"] in spec.name
            assert spec.exchange == item["exchange"]
            assert spec.unit == item["unit"]


# ============================================================
# 4. lookup 大小写不敏感
# ============================================================


class TestCaseInsensitive:
    def test_lowercase_lookup(self) -> None:
        assert default_registry.lookup("cu") is not None
        assert default_registry.lookup("if") is not None

    def test_mixed_case(self) -> None:
        assert default_registry.lookup("Cu") is not None
        assert default_registry.lookup("If") is not None


# ============================================================
# 5. lookup_by_symbol
# ============================================================


class TestLookupBySymbol:
    def test_wind_code_futures(self) -> None:
        spec = default_registry.lookup_by_symbol("CU2508.SHFE")
        assert spec is not None
        assert spec.product == "CU"

    def test_wind_code_index(self) -> None:
        spec = default_registry.lookup_by_symbol("IF2507.CFFEX")
        assert spec is not None
        assert spec.product == "IF"
        assert spec.multiplier == 300

    def test_wind_code_ine(self) -> None:
        spec = default_registry.lookup_by_symbol("SC2509.INE")
        assert spec is not None
        assert spec.product == "SC"

    def test_non_future_returns_none(self) -> None:
        """股票代码不是期货 → None。"""
        assert default_registry.lookup_by_symbol("600519.SH") is None

    def test_unregistered_future_returns_none(self) -> None:
        """未注册的期货品种 → None (即使格式合法)。"""
        # 假设 "ZZ" 是未注册的品种
        assert default_registry.lookup_by_symbol("ZZ2509.CFFEX") is None

    def test_invalid_code_returns_none(self) -> None:
        assert default_registry.lookup_by_symbol("invalid_code") is None


# ============================================================
# 6. is_supported / all_products / count
# ============================================================


class TestQueryMethods:
    def test_is_supported_true(self) -> None:
        assert default_registry.is_supported("CU") is True
        assert default_registry.is_supported("IF") is True

    def test_is_supported_false(self) -> None:
        assert default_registry.is_supported("UNKNOWN") is False
        assert default_registry.is_supported("ZZ") is False

    def test_all_specs_count_matches(self) -> None:
        specs = default_registry.all_specs()
        assert len(specs) == default_registry.count()

    def test_all_specs_sorted(self) -> None:
        specs = default_registry.all_specs()
        products = [s.product for s in specs]
        assert products == sorted(products)


# ============================================================
# 7. register / update
# ============================================================


class TestRegisterUpdate:
    def test_register_new(self) -> None:
        reg = ContractRegistry()  # 独立实例, 不污染 default
        new_spec = ContractSpec(
            product="ZZ", name="测试品种", exchange="CFFEX",
            multiplier=100, margin_rate=0.10, tick_size=0.5,
        )
        reg.register(new_spec)
        assert reg.is_supported("ZZ")
        assert reg.lookup("ZZ").multiplier == 100

    def test_register_duplicate_raises(self) -> None:
        reg = ContractRegistry()
        dup = ContractSpec(
            product="CU", name="覆盖铜", exchange="SHFE",
            multiplier=999, margin_rate=0.50, tick_size=1,
        )
        with pytest.raises(ValueError, match="已存在"):
            reg.register(dup)

    def test_update_overrides(self) -> None:
        reg = ContractRegistry()
        updated = ContractSpec(
            product="CU", name="沪铜期货(调整)", exchange="SHFE",
            multiplier=5, margin_rate=0.15, tick_size=10,  # margin 调整
        )
        reg.update(updated)
        assert reg.lookup("CU").margin_rate == 0.15
        assert reg.lookup("CU").name == "沪铜期货(调整)"


# ============================================================
# 8. ContractSpec 不可变
# ============================================================


class TestImmutability:
    def test_spec_frozen(self) -> None:
        spec = default_registry.lookup("CU")
        with pytest.raises((AttributeError, Exception)):
            spec.multiplier = 999  # type: ignore[misc]

    def test_spec_is_hashable(self) -> None:
        """frozen dataclass 应可哈希 (可作 dict key)。"""
        spec = default_registry.lookup("CU")
        d = {spec: "test"}
        assert d[spec] == "test"


# ============================================================
# 9. default_registry 单例
# ============================================================


class TestDefaultRegistry:
    def test_default_is_contract_registry(self) -> None:
        assert isinstance(default_registry, ContractRegistry)

    def test_default_has_builtins(self) -> None:
        """default_registry 在模块导入时已初始化 13 个内置品种。"""
        assert default_registry.count() >= 13
