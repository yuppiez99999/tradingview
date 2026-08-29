"""utils.contracts — 统一合约代码解析 + 类型安全 + 合约注册表 (W6.3.3 Step 0~2)。

公开接口:
    parse_symbol()   — 统一解析入口 (替代 3+ 处本地 secid/contract 实现)
    to_eastmoney_secid() / to_wind_code() — 便捷函数
    SymbolInfo / SymbolParseError — 数据类 + 异常
    NewType: AShareCode6 / WindCode / EastMoneySecId / FuturesContractCode / ExchangeCode / ProductCode
    ContractSpec / ContractRegistry / default_registry — 合约规格注册表 (Step 2)
"""

from utils.contracts.registry import ContractRegistry, ContractSpec, default_registry
from utils.contracts.symbols import (
    EXCHANGES,
    FUTURES_CODE_PATTERN,
    AShareCode6,
    EastMoneySecId,
    ExchangeCode,
    FuturesContractCode,
    ProductCode,
    SymbolInfo,
    SymbolParseError,
    WindCode,
    normalize_exchange,
    parse_symbol,
    to_eastmoney_secid,
    to_wind_code,
)

__all__ = [
    "AShareCode6",
    "ContractRegistry",
    "ContractSpec",
    "EastMoneySecId",
    "ExchangeCode",
    "EXCHANGES",
    "FuturesContractCode",
    "FUTURES_CODE_PATTERN",
    "ProductCode",
    "SymbolInfo",
    "SymbolParseError",
    "WindCode",
    "default_registry",
    "normalize_exchange",
    "parse_symbol",
    "to_eastmoney_secid",
    "to_wind_code",
]
