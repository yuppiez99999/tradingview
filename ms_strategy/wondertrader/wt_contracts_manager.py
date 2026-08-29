"""
WonderTrader 风格合约管理器

参考 wtpy/ContractsManager.py 设计。
统一管理全市场合约规格(股票/ETF/期货/期权),替代硬编码。
"""
from __future__ import annotations

import json
from pathlib import Path

from .wt_structs import ContractData

# 默认合约规格库 (A股+股指期货)
DEFAULT_CONTRACTS = {
    # 股指期货
    "IF.CFFEX": ContractData(
        code="IF", exchange="CFFEX", name="沪深300股指期货",
        product_class="FUTURE", contract_multiplier=300.0,
        price_tick=0.2, margin_rate=0.12,
        commission_rate=0.000023, stamp_duty=0.0, min_commission=0.0,
    ),
    "IC.CFFEX": ContractData(
        code="IC", exchange="CFFEX", name="中证500股指期货",
        product_class="FUTURE", contract_multiplier=200.0,
        price_tick=0.2, margin_rate=0.14,
        commission_rate=0.000023, stamp_duty=0.0, min_commission=0.0,
    ),
    "IM.CFFEX": ContractData(
        code="IM", exchange="CFFEX", name="中证1000股指期货",
        product_class="FUTURE", contract_multiplier=200.0,
        price_tick=0.2, margin_rate=0.14,
        commission_rate=0.000023, stamp_duty=0.0, min_commission=0.0,
    ),
    "IH.CFFEX": ContractData(
        code="IH", exchange="CFFEX", name="上证50股指期货",
        product_class="FUTURE", contract_multiplier=300.0,
        price_tick=0.2, margin_rate=0.12,
        commission_rate=0.000023, stamp_duty=0.0, min_commission=0.0,
    ),
    # 主力ETF
    "510300.SH": ContractData(
        code="510300.SH", exchange="SSE", name="沪深300ETF华泰柏瑞",
        product_class="ETF", contract_multiplier=1.0,
        price_tick=0.001, margin_rate=1.0,
        commission_rate=0.0003, stamp_duty=0.0, min_commission=5.0,
    ),
    "510050.SH": ContractData(
        code="510050.SH", exchange="SSE", name="上证50ETF华夏",
        product_class="ETF", contract_multiplier=1.0,
        price_tick=0.001, margin_rate=1.0,
        commission_rate=0.0003, stamp_duty=0.0, min_commission=5.0,
    ),
    "588000.SH": ContractData(
        code="588000.SH", exchange="SSE", name="科创50ETF华夏",
        product_class="ETF", contract_multiplier=1.0,
        price_tick=0.001, margin_rate=1.0,
        commission_rate=0.0003, stamp_duty=0.0, min_commission=5.0,
    ),
    # 默认股票模板
    "STOCK.DEFAULT": ContractData(
        code="DEFAULT", exchange="SSE", name="A股默认",
        product_class="STOCK", contract_multiplier=1.0,
        price_tick=0.01, margin_rate=1.0,
        commission_rate=0.0003, stamp_duty=0.001, min_commission=5.0,
    ),
}


class ContractsManager:
    """合约规格管理器

    统一管理所有合约规格,支持:
    1. 从 JSON 文件加载
    2. 动态注册新合约
    3. 按代码/交易所查询
    4. 计算交易成本(手续费+印花税+保证金)
    """

    def __init__(self, contracts_file: str | None = None):
        self._contracts: dict[str, ContractData] = {}
        # 加载默认合约
        for code, contract in DEFAULT_CONTRACTS.items():
            self._contracts[code] = contract
        # 从文件加载额外合约
        if contracts_file:
            self.load_from_file(contracts_file)

    def load_from_file(self, file_path: str) -> bool:
        """从 JSON 文件加载合约规格"""
        p = Path(file_path)
        if not p.exists():
            return False
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        for code, spec in data.items():
            self._contracts[code] = ContractData(
                code=code,
                exchange=spec.get("exchange", "SSE"),
                name=spec.get("name", code),
                product_class=spec.get("product_class", "STOCK"),
                contract_multiplier=spec.get("contract_multiplier", 1.0),
                price_tick=spec.get("price_tick", 0.01),
                margin_rate=spec.get("margin_rate", 1.0),
                commission_rate=spec.get("commission_rate", 0.0003),
                stamp_duty=spec.get("stamp_duty", 0.0),
                min_commission=spec.get("min_commission", 5.0),
            )
        return True

    def get_contract(self, code: str) -> ContractData:
        """获取合约规格, 未找到则返回默认股票模板"""
        if code in self._contracts:
            return self._contracts[code]
        # 尝试去掉后缀
        base_code = code.split(".")[0]
        if f"{base_code}.CFFEX" in self._contracts:
            return self._contracts[f"{base_code}.CFFEX"]
        return self._contracts.get("STOCK.DEFAULT", DEFAULT_CONTRACTS["STOCK.DEFAULT"])

    def register_contract(self, contract: ContractData) -> None:
        """注册新合约"""
        key = f"{contract.code}.{contract.exchange}" if "." not in contract.code else contract.code
        self._contracts[key] = contract

    def list_contracts(self, exchange: str | None = None,
                      product_class: str | None = None) -> list[ContractData]:
        """列出合约, 可按交易所/品种过滤"""
        result = []
        for c in self._contracts.values():
            if exchange and c.exchange != exchange:
                continue
            if product_class and c.product_class != product_class:
                continue
            result.append(c)
        return result

    def calc_commission(self, code: str, amount: float, direction: str = "BUY") -> float:
        """计算手续费"""
        c = self.get_contract(code)
        commission = amount * c.commission_rate
        # 卖出收印花税
        stamp = amount * c.stamp_duty if direction == "SELL" else 0
        return max(commission, c.min_commission) + stamp

    def calc_margin(self, code: str, amount: float) -> float:
        """计算保证金占用"""
        c = self.get_contract(code)
        return amount * c.margin_rate

    def calc_contract_value(self, code: str, price: float, volume: float) -> float:
        """计算合约名义价值 = 价格 × 数量 × 乘数"""
        c = self.get_contract(code)
        return price * volume * c.contract_multiplier


# 单例
_instance: ContractsManager | None = None


def get_contracts_manager() -> ContractsManager:
    """获取合约管理器单例"""
    global _instance
    if _instance is None:
        _instance = ContractsManager()
    return _instance


__all__ = [
    "DEFAULT_CONTRACTS",
    "ContractData",
    "ContractsManager",
    "get_contracts_manager",
]
