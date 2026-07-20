# -*- coding: utf-8 -*-
"""
期权卖方保证金动态监控 (Option Margin Monitor)

QMT 关键规则:
1. 期权买方: 最大亏损 = 权利金 (已支付), 无保证金要求
2. 期权卖方: 需缴纳保证金, 保证金随标的价格波动动态变化
3. 上交所/深交所 期权保证金计算:
   - 认购期权卖方保证金 = 权利金 + max(标的市值×15% - 虚值额, 标的市值×7%)
   - 认沽期权卖方保证金 = 权利金 + max(标的市值×15% - 虚值额, 行权价×合约单位×7%)
4. 临近行权日 (到期前 3 天): 保证金要求可能提高
5. 卖方爆仓风险: 保证金不足时 QMT 会强制平仓

核心功能:
- calc_margin(): 计算期权卖方保证金
- check_margin_sufficient(): 检查保证金是否充足
- detect_expiry_risk(): 检测临近行权日风险
- monitor(): 批量监控所有期权卖方持仓
"""
from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import logging
import re

logger = logging.getLogger(__name__)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class OptionPosition:
    """期权持仓"""
    symbol: str                    # 如 "510050C2507M03000.SH"
    underlying: str                # 标的代码, 如 "510050.SH"
    option_type: str = "CALL"      # "CALL" / "PUT"
    side: str = "BUY"             # "BUY" (买方) / "SELL" (卖方)
    strike: float = 0.0           # 行权价
    quantity: int = 0             # 持仓数量
    premium: float = 0.0          # 权利金 (单价)
    entry_price: float = 0.0      # 开仓价
    expiry_date: str = ""         # 到期日 "YYYY-MM-DD"
    contract_multiplier: float = 10000.0  # 合约单位 (ETF期权=10000)


@dataclass
class MarginCheckResult:
    """保证金检查结果"""
    symbol: str
    required_margin: float        # 所需保证金
    available_funds: float        # 可用资金
    is_sufficient: bool           # 是否充足
    margin_ratio: float           # 保证金占可用资金比例
    days_to_expiry: int           # 距到期天数
    warning_level: str = "OK"     # "OK" / "WARNING" / "DANGER"
    message: str = ""


# ============================================================
# 期权代码解析
# ============================================================

# 上交所 ETF 期权代码格式: {标的代码前3位}{C/P}{YY}{M}{行权价×1000}{SH}
# 例: 510050C2507M03000.SH = 上证50ETF 认购期权, 2025年7月, 行权价3.000
OPTION_CODE_PATTERN = re.compile(
    r'^(\d{6})([CP])(\d{2})(\d{2})(M\d{5})\.(SH|SZ)$'
)


def parse_option_code(code: str) -> Optional[Dict]:
    """解析期权代码

    Args:
        code: 如 "510050C2507M03000.SH"

    Returns:
        {
            "underlying_prefix": "510",
            "option_type": "CALL",
            "year": 2025,
            "month": 7,
            "strike": 3.000,
            "exchange": "SH",
            "full_underlying": "510050.SH",
        }
    """
    match = OPTION_CODE_PATTERN.match(code)
    if not match:
        return None

    prefix = match.group(1)     # "510050"
    opt_type = match.group(2)   # "C" or "P"
    yy = int(match.group(3))    # "25"
    month = int(match.group(4)) # "7"
    strike_str = match.group(5) # "M03000"
    exchange = match.group(6)   # "SH" or "SZ"

    year = 2000 + yy
    strike = float(strike_str[1:]) / 1000.0  # "M03000" → 3.000

    return {
        "underlying_prefix": prefix[:3],
        "option_type": "CALL" if opt_type == "C" else "PUT",
        "year": year,
        "month": month,
        "strike": strike,
        "exchange": exchange,
        "full_underlying": f"{prefix}.{exchange}",
    }


# ============================================================
# 保证金计算
# ============================================================

def calc_call_margin(
    underlying_price: float,
    strike: float,
    premium: float,
    contract_multiplier: float = 10000.0,
) -> float:
    """计算认购期权卖方保证金 (上交所规则)

    公式:
       保证金 = 权利金 + max(标的市值×15% - 虚值额, 标的市值×7%)

    Args:
        underlying_price: 标的最新价
        strike: 行权价
        premium: 权利金单价
        contract_multiplier: 合约单位

    Returns:
        每张合约保证金
    """
    underlying_value = underlying_price * contract_multiplier
    otm_amount = max(strike - underlying_price, 0) * contract_multiplier
    risk_amount = max(underlying_value * 0.15 - otm_amount, underlying_value * 0.07)
    return (premium + risk_amount) * contract_multiplier / contract_multiplier  # 简化为每张


def calc_put_margin(
    underlying_price: float,
    strike: float,
    premium: float,
    contract_multiplier: float = 10000.0,
) -> float:
    """计算认沽期权卖方保证金 (上交所规则)

    公式:
       保证金 = 权利金 + max(标的市值×15% - 虚值额, 行权价×合约单位×7%)

    Args:
        underlying_price: 标的最新价
        strike: 行权价
        premium: 权利金单价
        contract_multiplier: 合约单位

    Returns:
        每张合约保证金
    """
    underlying_value = underlying_price * contract_multiplier
    otm_amount = max(underlying_price - strike, 0) * contract_multiplier
    risk_amount = max(underlying_value * 0.15 - otm_amount, strike * contract_multiplier * 0.07)
    return premium + risk_amount


def calc_margin(
    option_type: str,
    underlying_price: float,
    strike: float,
    premium: float,
    quantity: int,
    contract_multiplier: float = 10000.0,
    days_to_expiry: int = 30,
) -> float:
    """计算期权卖方总保证金

    Args:
        option_type: "CALL" / "PUT"
        underlying_price: 标的最新价
        strike: 行权价
        premium: 权利金单价
        quantity: 持仓数量 (张)
        contract_multiplier: 合约单位
        days_to_expiry: 距到期天数

    Returns:
        总保证金金额
    """
    if option_type == "CALL":
        per_contract = calc_call_margin(
            underlying_price, strike, premium, contract_multiplier
        )
    else:
        per_contract = calc_put_margin(
            underlying_price, strike, premium, contract_multiplier
        )

    # 临近到期日加收 20% 保证金
    if days_to_expiry <= 3:
        per_contract *= 1.2

    return per_contract * quantity


# ============================================================
# 期权卖方保证金监控器
# ============================================================

class OptionMarginMonitor:
    """期权卖方保证金动态监控

    用法:
        monitor = OptionMarginMonitor(warning_ratio=0.8, danger_ratio=0.95)
        monitor.add_position(OptionPosition(
            symbol="510050P2507M03000.SH",
            underlying="510050.SH",
            option_type="PUT",
            side="SELL",
            strike=3.000,
            quantity=10,
            premium=0.0500,
            expiry_date="2025-07-25",
        ))
        results = monitor.check_all(underlying_prices={"510050.SH": 3.200}, available_funds=500000)
        for r in results:
            if r.warning_level == "DANGER":
                logger.error("保证金危险: %s", r.message)
    """

    # 预警阈值
    WARNING_RATIO = 0.80   # 保证金占可用资金 > 80% → 预警
    DANGER_RATIO = 0.95    # 保证金占可用资金 > 95% → 危险
    EXPIRY_WARNING_DAYS = 3  # 到期前 3 天 → 预警

    def __init__(
        self,
        warning_ratio: float = WARNING_RATIO,
        danger_ratio: float = DANGER_RATIO,
        expiry_warning_days: int = EXPIRY_WARNING_DAYS,
    ):
        self.warning_ratio = float(warning_ratio)
        self.danger_ratio = float(danger_ratio)
        self.expiry_warning_days = int(expiry_warning_days)
        self._positions: Dict[str, OptionPosition] = {}

    # ------------------------------------------------------------
    # 持仓管理
    # ------------------------------------------------------------

    def add_position(self, pos: OptionPosition):
        """添加/更新期权卖方持仓"""
        self._positions[pos.symbol] = pos

    def remove_position(self, symbol: str):
        """移除期权持仓"""
        self._positions.pop(symbol, None)

    def get_positions(self) -> List[OptionPosition]:
        """获取所有卖方持仓"""
        return list(self._positions.values())

    # ------------------------------------------------------------
    # 保证金检查
    # ------------------------------------------------------------

    def check_all(
        self,
        underlying_prices: Dict[str, float],
        available_funds: float,
    ) -> List[MarginCheckResult]:
        """批量检查所有期权卖方持仓的保证金

        Args:
            underlying_prices: {underlying_code: price}
            available_funds: 账户可用资金

        Returns:
            保证金检查结果列表
        """
        results = []
        for symbol, pos in self._positions.items():
            if pos.side != "SELL":
                continue  # 买方无需保证金

            underlying_price = underlying_prices.get(pos.underlying, 0.0)
            if underlying_price <= 0:
                logger.warning("期权 %s 标的价格 %s 不可用, 跳过保证金检查",
                             symbol, pos.underlying)
                continue

            result = self._check_single(pos, underlying_price, available_funds)
            results.append(result)

        return results

    def _check_single(
        self,
        pos: OptionPosition,
        underlying_price: float,
        available_funds: float,
    ) -> MarginCheckResult:
        """检查单个期权持仓的保证金"""
        # 计算距到期天数
        days_to_expiry = 999
        if pos.expiry_date:
            try:
                expiry = datetime.strptime(pos.expiry_date, "%Y-%m-%d").date()
                days_to_expiry = (expiry - date.today()).days
            except ValueError:
                pass

        # 计算保证金
        required = calc_margin(
            option_type=pos.option_type,
            underlying_price=underlying_price,
            strike=pos.strike,
            premium=pos.premium,
            quantity=pos.quantity,
            contract_multiplier=pos.contract_multiplier,
            days_to_expiry=days_to_expiry,
        )

        ratio = required / available_funds if available_funds > 0 else float("inf")

        # 风险等级
        if ratio >= self.danger_ratio:
            level = "DANGER"
            msg = (
                f"保证金危险: {pos.symbol} 需要 ¥{required:,.0f}, "
                f"可用 ¥{available_funds:,.0f}, 占比 {ratio:.1%}"
            )
        elif ratio >= self.warning_ratio:
            level = "WARNING"
            msg = (
                f"保证金预警: {pos.symbol} 需要 ¥{required:,.0f}, "
                f"可用 ¥{available_funds:,.0f}, 占比 {ratio:.1%}"
            )
        elif days_to_expiry <= self.expiry_warning_days:
            level = "WARNING"
            msg = (
                f"临近行权日: {pos.symbol} 距到期 {days_to_expiry} 天, "
                f"保证金 ¥{required:,.0f}"
            )
        else:
            level = "OK"
            msg = ""

        return MarginCheckResult(
            symbol=pos.symbol,
            required_margin=required,
            available_funds=available_funds,
            is_sufficient=ratio < 1.0,
            margin_ratio=ratio,
            days_to_expiry=days_to_expiry,
            warning_level=level,
            message=msg,
        )

    # ------------------------------------------------------------
    # 强制平仓建议
    # ------------------------------------------------------------

    def get_liquidation_advice(
        self,
        results: List[MarginCheckResult],
    ) -> List[Dict]:
        """获取强制平仓建议

        Returns:
            [{"symbol": "510050P2507M03000.SH", "action": "FORCE_CLOSE", "reason": "..."}]
        """
        advice = []
        for r in results:
            if r.warning_level == "DANGER":
                advice.append({
                    "symbol": r.symbol,
                    "action": "FORCE_CLOSE",
                    "reason": f"保证金占比 {r.margin_ratio:.1%} >= {self.danger_ratio:.1%}",
                    "required_margin": r.required_margin,
                    "available_funds": r.available_funds,
                })
            elif r.days_to_expiry <= 0:
                advice.append({
                    "symbol": r.symbol,
                    "action": "FORCE_CLOSE",
                    "reason": f"已到期或今日到期 (days={r.days_to_expiry})",
                    "required_margin": r.required_margin,
                    "available_funds": r.available_funds,
                })
            elif r.days_to_expiry <= self.expiry_warning_days:
                advice.append({
                    "symbol": r.symbol,
                    "action": "WARN_CLOSE",
                    "reason": f"临近行权日 {r.days_to_expiry} 天",
                    "required_margin": r.required_margin,
                    "available_funds": r.available_funds,
                })
        return advice

    # ------------------------------------------------------------
    # 诊断
    # ------------------------------------------------------------

    def get_status(self) -> Dict:
        """获取监控状态"""
        return {
            "positions_count": len(self._positions),
            "warning_ratio": self.warning_ratio,
            "danger_ratio": self.danger_ratio,
            "expiry_warning_days": self.expiry_warning_days,
        }


__all__ = [
    "OptionMarginMonitor",
    "OptionPosition",
    "MarginCheckResult",
    "calc_margin",
    "calc_call_margin",
    "calc_put_margin",
    "parse_option_code",
]