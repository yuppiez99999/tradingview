# -*- coding: utf-8 -*-
"""wt_contracts_manager 单元测试 — WonderTrader 合约管理器"""
import json

import pytest

from utils.wt_contracts_manager import (
    DEFAULT_CONTRACTS,
    ContractsManager,
    get_contracts_manager,
)
from utils.wt_structs import ContractData


class TestDefaultContracts:
    def test_has_if_cffex(self):
        assert "IF.CFFEX" in DEFAULT_CONTRACTS

    def test_has_stock_default(self):
        assert "STOCK.DEFAULT" in DEFAULT_CONTRACTS

    def test_has_future_default(self):
        assert "FUTURE.DEFAULT" in DEFAULT_CONTRACTS

    def test_has_option_default(self):
        assert "OPTION.DEFAULT" in DEFAULT_CONTRACTS

    def test_if_multiplier(self):
        assert DEFAULT_CONTRACTS["IF.CFFEX"].contract_multiplier == 300.0

    def test_etf_exists(self):
        assert "510300.SH" in DEFAULT_CONTRACTS
        assert DEFAULT_CONTRACTS["510300.SH"].product_class == "ETF"


class TestContractsManagerInit:
    def test_defaults_loaded(self):
        cm = ContractsManager()
        assert cm.get_contract("IF.CFFEX").code == "IF"
        assert cm.get_contract("510300.SH").code == "510300.SH"

    def test_empty(self):
        cm = ContractsManager()
        assert len(cm._contracts) > 0


class TestLoadFromFile:
    def test_nonexistent_file(self):
        cm = ContractsManager()
        assert cm.load_from_file("/nonexistent.json") is False

    def test_valid_file(self, tmp_path):
        data = {
            "CUSTOM.SH": {
                "exchange": "SSE",
                "name": "自定义",
                "product_class": "STOCK",
                "contract_multiplier": 1.0,
                "price_tick": 0.01,
                "margin_rate": 1.0,
                "commission_rate": 0.0005,
                "stamp_duty": 0.001,
                "min_commission": 5.0,
            }
        }
        f = tmp_path / "contracts.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        cm = ContractsManager()
        assert cm.load_from_file(str(f)) is True
        c = cm.get_contract("CUSTOM.SH")
        assert c.name == "自定义"
        assert c.commission_rate == 0.0005

    def test_init_with_file(self, tmp_path):
        data = {"XYZ.SH": {"exchange": "SSE", "name": "XYZ"}}
        f = tmp_path / "c.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        cm = ContractsManager(str(f))
        c = cm.get_contract("XYZ.SH")
        assert c.name == "XYZ"


class TestGetContract:
    def test_exact_match(self):
        cm = ContractsManager()
        c = cm.get_contract("IF.CFFEX")
        assert c.code == "IF"
        assert c.exchange == "CFFEX"

    def test_futures_fallback(self):
        cm = ContractsManager()
        c = cm.get_contract("IF2507.CFFEX")
        assert c.code == "IF"

    def test_futures_default_fallback(self):
        cm = ContractsManager()
        c = cm.get_contract("rb2510.SHF")
        assert c.product_class == "FUTURE"

    def test_stock_default(self):
        cm = ContractsManager()
        c = cm.get_contract("600519.SH")
        assert c.product_class == "STOCK"

    def test_etf_match(self):
        cm = ContractsManager()
        c = cm.get_contract("510300.SH")
        assert c.product_class == "ETF"

    def test_unknown_code(self):
        cm = ContractsManager()
        c = cm.get_contract("UNKNOWN.XYZ")
        assert c is not None


class TestRegisterContract:
    def test_register_new(self):
        cm = ContractsManager()
        c = ContractData(
            code="999999", exchange="SSE", name="测试",
            product_class="STOCK", contract_multiplier=1.0,
            price_tick=0.01, margin_rate=1.0,
            commission_rate=0.0003, stamp_duty=0.0, min_commission=5.0,
        )
        cm.register_contract(c)
        assert cm.get_contract("999999.SSE").name == "测试"

    def test_register_with_dot_code(self):
        cm = ContractsManager()
        c = ContractData(
            code="ABC.DE", exchange="UNKNOWN", name="带点",
            product_class="STOCK", contract_multiplier=1.0,
            price_tick=0.01, margin_rate=1.0,
            commission_rate=0.0003, stamp_duty=0.0, min_commission=5.0,
        )
        cm.register_contract(c)
        assert cm.get_contract("ABC.DE").name == "带点"


class TestListContracts:
    def test_all(self):
        cm = ContractsManager()
        result = cm.list_contracts()
        assert len(result) > 0

    def test_filter_exchange(self):
        cm = ContractsManager()
        result = cm.list_contracts(exchange="CFFEX")
        assert all(c.exchange == "CFFEX" for c in result)
        assert len(result) > 0

    def test_filter_product_class(self):
        cm = ContractsManager()
        result = cm.list_contracts(product_class="FUTURE")
        assert all(c.product_class == "FUTURE" for c in result)
        assert len(result) > 0

    def test_filter_both(self):
        cm = ContractsManager()
        result = cm.list_contracts(exchange="SSE", product_class="ETF")
        assert all(c.exchange == "SSE" and c.product_class == "ETF" for c in result)

    def test_no_match(self):
        cm = ContractsManager()
        result = cm.list_contracts(exchange="NONEXISTENT")
        assert result == []


class TestCalcCommission:
    def test_buy(self):
        cm = ContractsManager()
        comm = cm.calc_commission("510300.SH", 10000.0, "BUY")
        c = cm.get_contract("510300.SH")
        expected = max(10000.0 * c.commission_rate, c.min_commission)
        assert comm == pytest.approx(expected)

    def test_sell_with_stamp_duty(self):
        cm = ContractsManager()
        comm = cm.calc_commission("600519.SH", 10000.0, "SELL")
        c = cm.get_contract("600519.SH")
        expected = max(10000.0 * c.commission_rate, c.min_commission) + 10000.0 * c.stamp_duty
        assert comm == pytest.approx(expected)

    def test_min_commission(self):
        cm = ContractsManager()
        comm = cm.calc_commission("510300.SH", 100.0, "BUY")
        c = cm.get_contract("510300.SH")
        assert comm >= c.min_commission

    def test_futures(self):
        cm = ContractsManager()
        comm = cm.calc_commission("IF.CFFEX", 1000000.0, "BUY")
        c = cm.get_contract("IF.CFFEX")
        expected = max(1000000.0 * c.commission_rate, c.min_commission)
        assert comm == pytest.approx(expected)


class TestCalcMargin:
    def test_stock(self):
        cm = ContractsManager()
        margin = cm.calc_margin("600519.SH", 10000.0)
        assert margin == pytest.approx(10000.0)

    def test_futures(self):
        cm = ContractsManager()
        margin = cm.calc_margin("IF.CFFEX", 1000000.0)
        c = cm.get_contract("IF.CFFEX")
        assert margin == pytest.approx(1000000.0 * c.margin_rate)


class TestCalcContractValue:
    def test_stock(self):
        cm = ContractsManager()
        val = cm.calc_contract_value("600519.SH", 1800.0, 100)
        assert val == pytest.approx(1800.0 * 100 * 1.0)

    def test_futures(self):
        cm = ContractsManager()
        val = cm.calc_contract_value("IF.CFFEX", 4000.0, 10)
        assert val == pytest.approx(4000.0 * 10 * 300.0)


class TestGetContractsManager:
    def test_singleton(self):
        cm1 = get_contracts_manager()
        cm2 = get_contracts_manager()
        assert cm1 is cm2

    def test_returns_manager(self):
        cm = get_contracts_manager()
        assert isinstance(cm, ContractsManager)