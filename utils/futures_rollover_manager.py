"""
期货主力合约识别 + 换月管理 (Futures Rollover Manager)

QMT 关键规则:
1. 不可交易 .IDX (指数) 或连续合约 (如 IF00) — 必须指定具体月份 (如 IF2507.CFFEX)
2. 股指期货当月合约在第三个周五到期, 到期前 3 天应换月
3. 商品期货主力合约换月规则各异 (成交量最大原则)

核心功能:
- get_active_contract(): 获取当前主力合约代码
- detect_rollover_need(): 检测是否需要换月
- generate_roll_orders(): 生成换月订单 (平旧合约 + 开新合约)
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

# W6.3.3 Step 1: 统一入口 (保留 FUTURES_CODE_PATTERN 别名兼容 import)
from utils.contracts.symbols import SymbolParseError, parse_symbol

logger = logging.getLogger(__name__)


# ============================================================
# 股指期货到期日计算
# ============================================================


def _get_third_friday(year: int, month: int) -> date:
    """计算指定年月的第三个周五 (股指期货到期日)"""
    # 第一个周五
    first_day = date(year, month, 1)
    days_to_friday = (4 - first_day.weekday()) % 7
    first_friday = first_day + timedelta(days=days_to_friday)
    # 第三个周五 = 第一个周五 + 14 天
    third_friday = first_friday + timedelta(days=14)
    return third_friday


def _get_futures_expiry(product: str, month_str: str, year: int) -> date | None:
    """获取期货合约到期日

    Args:
        product: 品种代码 (IF/IC/IM/IH)
        month_str: 月份字符串 (如 "07")
        year: 年份

    Returns:
       到期日, 或 None (商品期货暂不支持)
    """
    month = int(month_str)
    if product in ("IF", "IC", "IM", "IH"):
        return _get_third_friday(year, month)
    # 商品期货: 暂不支持自动计算, 返回 None
    return None


# ============================================================
# 主力合约规则
# ============================================================

# 股指期货换月提前天数 (到期前 N 天开始换月)
ROLLOVER_DAYS_BEFORE_EXPIRY = 3

# 期货代码格式: {PRODUCT}{YY}{MM}.{EXCHANGE}
# W6.3.3 Step 1: 已迁移为统一入口 utils.contracts.symbols.FUTURES_CODE_PATTERN
# (此处从 symbols 模块 re-export, 保留原有名字以兼容外部 import + 旧行为)
# 原本地正则 r"^([A-Za-z]+)(\d{2})(\d{2})\.(CFFEX|SHF|DCE|ZCE|GFEX|CZCE)$"
# 已升级为: 新增 INE/SHFE/CZCE 完整支持 + 仍兼容 SHF/ZCE 旧写法


class FuturesRolloverManager:
    """期货主力合约识别与换月管理

    用法:
        mgr = FuturesRolloverManager()
        active = mgr.get_active_contract("IF", "CFFEX")
        # → "IF2507.CFFEX"

        if mgr.detect_rollover_need("IF2507.CFFEX"):
            old, new = mgr.generate_roll_orders("IF2507.CFFEX", 3)
            # → ("IF2507.CFFEX", "IF2508.CFFEX")
    """

    def __init__(self, rollover_days_before: int = ROLLOVER_DAYS_BEFORE_EXPIRY):
        self.rollover_days = int(rollover_days_before)

    # ------------------------------------------------------------
    # 主力合约识别
    # ------------------------------------------------------------

    def get_active_contract(self, product: str, exchange: str = "CFFEX") -> str:
        """获取当前应交易的主力合约

        Args:
            product: 品种代码 (如 "IF")
            exchange: 交易所 (如 "CFFEX")

        Returns:
            完整合约代码, 如 "IF2507.CFFEX"

        规则:
        - 股指期货: 到期前 3 天换月, 否则交易当月合约
        - 商品期货: 交易下月合约 (简化)
        """
        today = date.today()
        current_year = today.year % 100
        current_month = today.month

        if exchange == "CFFEX" and product in ("IF", "IC", "IM", "IH"):
            # 股指期货: 当月合约在第三个周五到期
            expiry_this_month = _get_third_friday(today.year, current_month)
            days_to_expiry = (expiry_this_month - today).days

            if days_to_expiry <= self.rollover_days:
                # 换月: 交易下月合约
                next_month = current_month % 12 + 1
                next_year = today.year + (1 if current_month == 12 else 0)
                month_str = f"{next_month:02d}"
                year_str = f"{next_year % 100:02d}"
            else:
                month_str = f"{current_month:02d}"
                year_str = f"{current_year:02d}"
        else:
            # 商品期货: 默认交易下月合约
            next_month = current_month % 12 + 1
            next_year = today.year + (1 if current_month == 12 else 0)
            month_str = f"{next_month:02d}"
            year_str = f"{next_year % 100:02d}"

        return f"{product}{year_str}{month_str}.{exchange}"

    # ------------------------------------------------------------
    # 换月检测
    # ------------------------------------------------------------

    def detect_rollover_need(self, contract_code: str) -> tuple[bool, str]:
        """检测是否需要换月

        Args:
            contract_code: 当前持仓合约, 如 "IF2507.CFFEX"

        Returns:
            (need_rollover, reason)
        """
        parsed = self._parse_contract(contract_code)
        if parsed is None:
            return False, f"无法解析合约代码: {contract_code}"

        product, year_str, month_str, _exchange = parsed
        year = 2000 + int(year_str)
        int(month_str)

        expiry = _get_futures_expiry(product, month_str, year)
        if expiry is None:
            return False, f"商品期货 {product} 暂不支持自动换月检测"

        today = date.today()
        days_to_expiry = (expiry - today).days

        if days_to_expiry <= self.rollover_days:
            return True, (
                f"{contract_code} 距到期 ({expiry}) 仅 {days_to_expiry} 天, <= {self.rollover_days} 天, 需要换月"
            )

        return False, ""

    # ------------------------------------------------------------
    # 换月订单生成
    # ------------------------------------------------------------

    def generate_roll_orders(
        self,
        old_contract: str,
        quantity: int,
        direction: str = "SHORT",
    ) -> tuple[str, str, list[dict]]:
        """生成换月订单

        Args:
            old_contract: 旧合约代码 (如 "IF2507.CFFEX")
            quantity: 持仓数量
            direction: 持仓方向 (LONG/SHORT)

        Returns:
            (old_contract, new_contract, orders) — 平旧合约订单 + 开新合约订单
        """
        parsed = self._parse_contract(old_contract)
        if parsed is None:
            raise ValueError(f"无法解析合约代码: {old_contract}")

        product, _, _, exchange = parsed
        new_contract = self.get_active_contract(product, exchange)

        if new_contract == old_contract:
            logger.info("换月: 主力合约仍是 %s, 无需换月", old_contract)
            return old_contract, new_contract, []

        # 生成订单
        close_side = "BUY" if direction == "SHORT" else "SELL"
        open_side = "SELL" if direction == "SHORT" else "BUY"

        orders = [
            {
                "symbol": old_contract,
                "side": close_side,
                "offset": "CLOSE",  # 平仓
                "quantity": quantity,
                "description": f"换月—平旧合约 {old_contract}",
            },
            {
                "symbol": new_contract,
                "side": open_side,
                "offset": "OPEN",  # 开仓
                "quantity": quantity,
                "description": f"换月—开新合约 {new_contract}",
            },
        ]

        logger.info(
            "换月订单: %s %s %d 手 → %s %s %d 手",
            old_contract,
            "平仓",
            quantity,
            new_contract,
            "开仓",
            quantity,
        )

        return old_contract, new_contract, orders

    # ------------------------------------------------------------
    # 合约代码解析
    # ------------------------------------------------------------

    def _parse_contract(self, code: str) -> tuple[str, str, str, str] | None:
        """解析合约代码 (W6.3.3 Step 1: 迁移为 contracts 统一入口)。

        注意: 为保持 100% 向后兼容, 返回的第 4 项是原始后缀的大写 (如 "SHF" 而非 "SHFE"),
        与旧本地正则 group(4) 取值行为完全一致。规范化交易所代码请使用
        parse_symbol(code).exchange (如 SHFE/CZCE/CFFEX 等)。

        Args:
            code: 如 "IF2507.CFFEX" / "CU2508.SHF" (旧写法) / "SC2509.INE"

        Returns:
            (product, year_str, month_str, raw_exchange_suffix) 或 None
        """
        try:
            info = parse_symbol(code, hint_asset="future", strict=True)
        except SymbolParseError:
            return None
        # 非期货 (hint_asset mismatch 但 parse 成功的降级情况)
        if info.futures_year is None or info.futures_month is None:
            return None
        product = info.product
        year_str = f"{info.futures_year % 100:02d}"
        month_str = f"{info.futures_month:02d}"
        # 100% 兼容旧行为: 第 4 项取原始输入中的后缀大写, 而非规范化值
        raw_suffix = code.rsplit(".", 1)[-1].upper() if "." in code else ""
        return (product, year_str, month_str, raw_suffix)

    def is_tradable(self, code: str) -> bool:
        """判断是否为可交易合约 (非 .IDX 或连续合约)"""
        # 拦截常见的不可交易代码
        if code.endswith(".IDX"):
            return False
        # 去掉交易所后缀后检查连续合约/指数合约 (修复: 原 endswith 对带后缀代码如 IF00.CFFEX 失效)
        base_code = code.split(".")[0]
        if base_code.endswith("00"):
            # 连续合约 (如 IF2 IF00.CFFEX)
            return False
        if base_code.endswith("0001"):
            # 指数合约
            return False
        return self._parse_contract(code) is not None

    def get_days_to_expiry(self, contract_code: str) -> int | None:
        """获取合约距到期天数"""
        parsed = self._parse_contract(contract_code)
        if parsed is None:
            return None
        product, year_str, month_str, _ = parsed
        year = 2000 + int(year_str)
        expiry = _get_futures_expiry(product, month_str, year)
        if expiry is None:
            return None
        return (expiry - date.today()).days

    # ------------------------------------------------------------
    # 对冲合约名解析 (P0-5)
    # ------------------------------------------------------------

    # 通用对冲合约名 → 产品代码映射
    _HEDGE_NAME_MAP: dict[str, str] = {
        # 股指期货
        "IF_futures": "IF",
        "IF.CFFEX": "IF",
        "IF": "IF",
        "IC_futures": "IC",
        "IC.CFFEX": "IC",
        "IC": "IC",
        "IM_futures": "IM",
        "IM.CFFEX": "IM",
        "IM": "IM",
        "IH_futures": "IH",
        "IH.CFFEX": "IH",
        "IH": "IH",
        # 商品期货
        "RB_futures": "RB",
        "RB.SHF": "RB",
        "AU_futures": "AU",
        "AU.SHF": "AU",
        "SC_futures": "SC",
        "SC.INE": "SC",
    }

    def resolve_hedge_contract(self, hedge_name: str) -> str | None:
        """将通用对冲合约名解析为具体月份的可交易合约

        例: "IF_futures" / "IF.CFFEX" / "IF" → "IF2507.CFFEX"

        Args:
            hedge_name: 通用对冲合约名 (如 "IF_futures", "IF.CFFEX")

        Returns:
            具体合约代码 (如 "IF2507.CFFEX") 或 None
        """
        # 1. 如果已经是具体合约 (如 "IF2507.CFFEX"), 直接返回
        if self.is_tradable(hedge_name):
            return hedge_name

        # 2. 映射到产品代码
        product = self._HEDGE_NAME_MAP.get(hedge_name)
        if product is None:
            # 尝试从合约名中提取产品代码
            match = re.match(r"^([A-Za-z]+)", hedge_name)
            if match:
                product = match.group(1).upper()
            else:
                logger.warning("无法解析对冲合约名: %s", hedge_name)
                return None

        # 3. 获取主力合约
        exchange = "CFFEX" if product in ("IF", "IC", "IM", "IH") else None
        active = self.get_active_contract(product, exchange)  # type: ignore
        if active is None:
            logger.warning("无法获取 %s 的主力合约", product)
            return None

        logger.info("对冲合约解析: %s → %s", hedge_name, active)
        return active


__all__ = [
    "ROLLOVER_DAYS_BEFORE_EXPIRY",
    "FuturesRolloverManager",
]
