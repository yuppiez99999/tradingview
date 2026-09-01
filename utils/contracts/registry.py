"""W6.3.3 Step 2: 合约注册表 (ContractRegistry) — 统一查询乘数/保证金/到期日等元数据。

设计目标:
    - 整合 managers.SUPPORTED_COMMODITIES (8 商品) + directional_futures_trader.CONTRACT_SPECS
      (CU/AU/T 详) + 4 股指期货 (IF/IC/IH/IM), 消除全系统硬编码表分裂
    - 单例模式: 全局唯一实例, 避免重复 init
    - 不可变 ContractSpec: frozen dataclass, 防运行时篡改
    - 为 directional_futures_trader / gamma_engine / hedge_execution_engine 等处
      ~10 个 `# type: ignore` (spec["multiplier"] 之类) 提供类型安全查询入口

使用方式:
    from utils.contracts.registry import default_registry, ContractSpec

    spec = default_registry.lookup("CU")
    # → ContractSpec(product="CU", name="沪铜期货", exchange="SHFE",
    #                multiplier=5, margin_rate=0.09, tick_size=10, ...)

    # 按完整合约代码查询 (自动提取 product)
    spec = default_registry.lookup_by_symbol("CU2508.SHFE")

    # 检查是否支持
    if default_registry.is_supported("CU"):
        ...

向后兼容:
    - 旧代码 spec = CONTRACT_SPECS["CU"]; spec["multiplier"] 仍可继续使用
      (Step 3 才迁移调用方, 本步只提供新入口)
    - default_registry 实例在模块导入时初始化, 无需显式调用 init()

设计原则 (AGENTS.md):
    - 不可变性 (§5.1): ContractSpec 为 frozen dataclass
    - 单一职责: 本模块只做元数据查询, 不做行情/交易
    - 多小文件 (§5.3): 本模块 < 400 行
"""

from __future__ import annotations

from dataclasses import dataclass

from utils.contracts.symbols import SymbolParseError, parse_symbol

# ============================================================
# ContractSpec 数据类 (不可变)
# ============================================================


@dataclass(frozen=True)
class ContractSpec:
    """单个合约品种的规格说明 (不可变)。

    Attributes:
        product: 品种代码 "CU" / "IF" / "T"
        name: 中文名 "沪铜期货" / "沪深300股指期货"
        exchange: 规范化交易所 "SHFE" / "CFFEX" / "INE" / "DCE"
        multiplier: 合约乘数 (每手对应的标的数量, 如 CU=5 吨/手)
        margin_rate: 保证金率 (小数, 如 0.09 = 9%)
        tick_size: 最小变动价位 (元, 如 CU=10 元/吨)
        price_unit: 报价单位 "元/吨" / "元/克" / "元" (点数)
        unit: 计量单位 "吨" / "克" / "桶" (来自 managers.SUPPORTED_COMMODITIES)
        asset_type: "COMMODITY_FUTURE" / "INDEX_FUTURE" / "BOND_FUTURE"
        purpose: 用途标签 "new_energy_demand" / "safe_haven" / "rate_directional"
        default_direction: 默认方向 "long" / "short"
    """

    product: str
    name: str
    exchange: str
    multiplier: float
    margin_rate: float
    tick_size: float
    price_unit: str = ""
    unit: str = ""
    asset_type: str = "COMMODITY_FUTURE"
    purpose: str = ""
    default_direction: str = "long"


# ============================================================
# 内置合约规格表 (合并 3 个来源)
# ============================================================
# 来源 1: directional_futures_trader.CONTRACT_SPECS (CU/AU/T, 详)
# 来源 2: managers.SUPPORTED_COMMODITIES (CU/AU/AG/SC/I/RB/M/Y, 仅 name/exchange/unit)
# 来源 3: 股指期货 IF/IC/IH/IM (公开信息: 乘数 300/200/300/200, 保证金 12/14/12/14)
#
# 重叠品种 (CU/AU) 以 directional_futures_trader 为准 (含 multiplier/margin_rate/tick_size)
# 其余商品 (AG/I/RB/M/Y/SC) 补充公开信息

_BUILTIN_SPECS: list[ContractSpec] = [
    # --- 来源 1: directional_futures_trader.CONTRACT_SPECS (3 条, 详) ---
    ContractSpec(
        product="CU",
        name="沪铜期货",
        exchange="SHFE",
        multiplier=5,
        margin_rate=0.09,
        tick_size=10,
        price_unit="元/吨",
        unit="吨",
        asset_type="COMMODITY_FUTURE",
        purpose="new_energy_demand",
        default_direction="long",
    ),
    ContractSpec(
        product="AU",
        name="黄金期货",
        exchange="SHFE",
        multiplier=1000,
        margin_rate=0.06,
        tick_size=0.02,
        price_unit="元/克",
        unit="克",
        asset_type="COMMODITY_FUTURE",
        purpose="safe_haven",
        default_direction="long",
    ),
    ContractSpec(
        product="T",
        name="10年国债期货",
        exchange="CFFEX",
        multiplier=10_000,
        margin_rate=0.02,
        tick_size=0.005,
        price_unit="元",
        unit="",
        asset_type="BOND_FUTURE",
        purpose="rate_directional",
        default_direction="short",
    ),
    # --- 来源 2 补充: managers.SUPPORTED_COMMODITIES 中未在来源 1 出现的品种 ---
    ContractSpec(
        product="AG",
        name="白银期货",
        exchange="SHFE",
        multiplier=15,
        margin_rate=0.07,
        tick_size=1,
        price_unit="元/千克",
        unit="千克",
        asset_type="COMMODITY_FUTURE",
        purpose="precious_metal",
        default_direction="long",
    ),
    ContractSpec(
        product="SC",
        name="原油期货",
        exchange="INE",
        multiplier=1000,
        margin_rate=0.10,
        tick_size=0.1,
        price_unit="元/桶",
        unit="桶",
        asset_type="COMMODITY_FUTURE",
        purpose="energy",
        default_direction="long",
    ),
    ContractSpec(
        product="I",
        name="铁矿石期货",
        exchange="DCE",
        multiplier=100,
        margin_rate=0.10,
        tick_size=0.5,
        price_unit="元/吨",
        unit="吨",
        asset_type="COMMODITY_FUTURE",
        purpose="steel_chain",
        default_direction="long",
    ),
    ContractSpec(
        product="RB",
        name="螺纹钢期货",
        exchange="SHFE",
        multiplier=10,
        margin_rate=0.09,
        tick_size=1,
        price_unit="元/吨",
        unit="吨",
        asset_type="COMMODITY_FUTURE",
        purpose="construction",
        default_direction="long",
    ),
    ContractSpec(
        product="M",
        name="豆粕期货",
        exchange="DCE",
        multiplier=10,
        margin_rate=0.07,
        tick_size=1,
        price_unit="元/吨",
        unit="吨",
        asset_type="COMMODITY_FUTURE",
        purpose="agriculture",
        default_direction="long",
    ),
    ContractSpec(
        product="Y",
        name="豆油期货",
        exchange="DCE",
        multiplier=10,
        margin_rate=0.07,
        tick_size=2,
        price_unit="元/吨",
        unit="吨",
        asset_type="COMMODITY_FUTURE",
        purpose="agriculture",
        default_direction="long",
    ),
    # --- 来源 3: 股指期货 (IF/IC/IH/IM) ---
    ContractSpec(
        product="IF",
        name="沪深300股指期货",
        exchange="CFFEX",
        multiplier=300,
        margin_rate=0.12,
        tick_size=0.2,
        price_unit="点",
        unit="",
        asset_type="INDEX_FUTURE",
        purpose="index_hedge",
        default_direction="long",
    ),
    ContractSpec(
        product="IC",
        name="中证500股指期货",
        exchange="CFFEX",
        multiplier=200,
        margin_rate=0.14,
        tick_size=0.2,
        price_unit="点",
        unit="",
        asset_type="INDEX_FUTURE",
        purpose="index_hedge",
        default_direction="long",
    ),
    ContractSpec(
        product="IH",
        name="上证50股指期货",
        exchange="CFFEX",
        multiplier=300,
        margin_rate=0.12,
        tick_size=0.2,
        price_unit="点",
        unit="",
        asset_type="INDEX_FUTURE",
        purpose="index_hedge",
        default_direction="long",
    ),
    ContractSpec(
        product="IM",
        name="中证1000股指期货",
        exchange="CFFEX",
        multiplier=200,
        margin_rate=0.14,
        tick_size=0.2,
        price_unit="点",
        unit="",
        asset_type="INDEX_FUTURE",
        purpose="index_hedge",
        default_direction="long",
    ),
]


# ============================================================
# ContractRegistry 单例
# ============================================================


class ContractRegistry:
    """合约规格注册表 (单例)。

    线程安全:
        非线程安全 (初始化后只读, 回测/策略为单线程模型)
    """

    def __init__(self) -> None:
        self._specs: dict[str, ContractSpec] = {}
        for spec in _BUILTIN_SPECS:
            self._specs[spec.product.upper()] = spec

    def register(self, spec: ContractSpec) -> None:
        """注册新合约规格 (运行时扩展用)。

        Args:
            spec: 合约规格实例

        Raises:
            ValueError: product 已存在 (避免覆盖内置规格)
        """
        key = spec.product.upper()
        if key in self._specs:
            raise ValueError(
                f"ContractRegistry: 品种 {key!r} 已存在, 不可覆盖 (如需更新请用 update 方法)"
            )
        self._specs[key] = spec

    def update(self, spec: ContractSpec) -> None:
        """更新已有合约规格 (覆盖)。

        与 register 的区别: 允许覆盖已存在的规格, 用于动态调整保证金率等场景。
        """
        self._specs[spec.product.upper()] = spec

    def lookup(self, product: str) -> ContractSpec | None:
        """按品种代码查询合约规格。

        Args:
            product: 品种代码 "CU" / "IF" / "T" (大小写不敏感)

        Returns:
            ContractSpec 或 None (未注册)
        """
        return self._specs.get(product.upper())

    def lookup_by_symbol(self, wind_code: str) -> ContractSpec | None:
        """按完整合约代码查询 (如 "CU2508.SHFE" → 查 "CU")。

        自动从 wind_code 中提取品种代码 (字母前缀), 然后查 registry。

        Args:
            wind_code: Wind 风格合约代码 "CU2508.SHFE" / "IF2507.CFFEX"

        Returns:
            ContractSpec 或 None (未注册或解析失败)
        """
        try:
            info = parse_symbol(wind_code, hint_asset="future", strict=True)
        except SymbolParseError:
            return None
        if info.futures_year is None:
            return None
        return self.lookup(info.product)

    def is_supported(self, product: str) -> bool:
        """检查品种是否已注册。"""
        return product.upper() in self._specs

    def all_products(self) -> list[str]:
        """返回所有已注册品种代码列表 (排序后)。"""
        return sorted(self._specs.keys())

    def all_specs(self) -> list[ContractSpec]:
        """返回所有已注册规格列表 (按 product 排序)。"""
        return [self._specs[k] for k in sorted(self._specs.keys())]

    def count(self) -> int:
        """返回已注册品种数。"""
        return len(self._specs)


# ============================================================
# 默认单例 (模块导入时初始化)
# ============================================================

default_registry = ContractRegistry()


__all__ = [
    "ContractSpec",
    "ContractRegistry",
    "default_registry",
]
