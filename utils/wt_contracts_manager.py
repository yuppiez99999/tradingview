# -*- coding: utf-8 -*-
"""
WonderTrader 风格合约管理器

参考 wtpy/ContractsManager.py 设计。
统一管理全市场合约规格(股票/ETF/期货/期权),替代硬编码。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional, List
from dataclasses import asdict

from .wt_structs import ContractData


# 默认合约规格库 (A股+股指期货+期权)
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
    # 期货默认模板 (商品期货回退)
    "FUTURE.DEFAULT": ContractData(
        code="FUTURE", exchange="CFFEX", name="期货默认",
        product_class="FUTURE", contract_multiplier=10.0,
        price_tick=1.0, margin_rate=0.10,
        commission_rate=0.0001, stamp_duty=0.0, min_commission=0.0,
    ),
    # 期权默认模板
    "OPTION.DEFAULT": ContractData(
        code="OPTION", exchange="SSE", name="期权默认",
        product_class="OPTION", contract_multiplier=10000.0,
        price_tick=0.0001, margin_rate=0.15,
        commission_rate=0.0001, stamp_duty=0.0, min_commission=5.0,
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

    def __init__(self, contracts_file: Optional[str] = None):
        self._contracts: Dict[str, ContractData] = {}
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
        with open(p, "r", encoding="utf-8") as f:
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
        """获取合约规格, 按品种类型智能回退

        优先级: 精确匹配 → 期货品种匹配 → 期权品种匹配 → 股票/ETF 默认模板
        """
        if code in self._contracts:
            return self._contracts[code]

        # 提取 base_code 和 exchange
        parts = code.split(".")
        base_code = parts[0] if len(parts) >= 1 else code
        exchange = parts[-1] if len(parts) >= 2 else ""

        # 期货: 去掉月份数字匹配品种 (如 IF2507.CFFEX → IF.CFFEX)
        import re
        if exchange in ("CFFEX", "SHF", "DCE", "ZCE", "GFEX", "CZCE"):
            # 提取品种代码 (IF2507 → IF, rb2507 → rb, sc2507 → sc)
            product_match = re.match(r'([A-Za-z]+)', base_code)
            if product_match:
                product = product_match.group(1)
                full_key = f"{product}.{exchange}"
                if full_key in self._contracts:
                    return self._contracts[full_key]
            # 回退到期货默认模板
            return self._contracts.get("FUTURE.DEFAULT",
                DEFAULT_CONTRACTS.get("FUTURE.DEFAULT",
                    DEFAULT_CONTRACTS["STOCK.DEFAULT"]))

        # 期权: 按交易所 + 标的前缀匹配 (QMT使用 .SH/.SZ)
        # 仅当 base_code 包含期权特征 (C/P + 月份) 时才进入期权回退
        if exchange in ("SH", "SZ", "SSE", "SZSE"):
            import re
            opt_match = re.match(r'^(\d{6})([CP])(\d{4})(M\d{5})$', base_code)
            if opt_match:
                underlying = base_code[:6]
                for key, ct in self._contracts.items():
                    if ct.product_class == "OPTION" and ct.code.startswith(underlying[:3]):
                        return ct
                return self._contracts.get("OPTION.DEFAULT",
                    DEFAULT_CONTRACTS.get("OPTION.DEFAULT",
                        DEFAULT_CONTRACTS["STOCK.DEFAULT"]))

        # 股票/ETF: 默认模板
        return self._contracts.get("STOCK.DEFAULT",
            DEFAULT_CONTRACTS.get("STOCK.DEFAULT",
                DEFAULT_CONTRACTS["STOCK.DEFAULT"]))

    def register_contract(self, contract: ContractData) -> None:
        """注册新合约"""
        key = f"{contract.code}.{contract.exchange}" if "." not in contract.code else contract.code
        self._contracts[key] = contract

    def list_contracts(self, exchange: Optional[str] = None,
                      product_class: Optional[str] = None) -> List[ContractData]:
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
_instance: Optional[ContractsManager] = None


def get_contracts_manager() -> ContractsManager:
    """获取合约管理器单例"""
    global _instance
    if _instance is None:
        _instance = ContractsManager()
    return _instance


__all__ = [
    "ContractsManager", "ContractData", "DEFAULT_CONTRACTS",
    "get_contracts_manager",
]
