"""
期权行权/指派风险管理 (Option Exercise & Assignment Risk)

QMT 关键规则:
1. 中国 ETF 期权为欧式期权, 仅在到期日行权
2. 期权买方: 虚值期权到期作废, 实值期权自动行权
3. 期权卖方: 实值期权到期被指派概率极高, 虚值期权也可能被指派(尾行权风险)
4. 到期前 3 天: 进入"行权预警期", 卖方应提前平仓或准备资金/标的
5. 被指派后果:
   - 认购期权卖方: 需交付标的 ETF (需持有足够份额)
   - 认沽期权卖方: 需以行权价买入标的 ETF (需准备足够资金)
6. 临近到期日, 虚值期权时间价值加速衰减 (Theta 风险)

核心功能:
- detect_near_expiry(): 检测临近到期期权
- assess_assignment_risk(): 评估被指派风险
- suggest_close_orders(): 生成平仓建议
- auto_close_deep_itm(): 深度实值自动平仓 (避免被指派)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta

logger = logging.getLogger(__name__)


# ============================================================
# 数据结构
# ============================================================


@dataclass
class ExerciseRiskResult:
    """行权风险评估结果"""

    symbol: str  # 期权代码
    side: str  # "BUY" / "SELL"
    option_type: str  # "CALL" / "PUT"
    strike: float  # 行权价
    underlying_price: float  # 标的最新价
    days_to_expiry: int  # 距到期天数
    moneyness: float  # 实值程度 (call: price/strike, put: strike/price)
    is_itm: bool  # 是否实值
    assignment_probability: str  # "LOW" / "MEDIUM" / "HIGH" / "CERTAIN"
    recommended_action: str  # 建议操作
    potential_loss: float  # 潜在损失 (卖方被指派)
    message: str = ""


# ============================================================
# 期权代码解析 (与 option_margin_monitor 共享逻辑)
# ============================================================

OPTION_CODE_PATTERN = re.compile(r"^(\d{6})([CP])(\d{2})(\d{2})(M\d{5})\.(SH|SZ)$")


def _parse_option_code(code: str) -> dict | None:
    """解析期权代码"""
    match = OPTION_CODE_PATTERN.match(code)
    if not match:
        return None
    underlying = match.group(1)
    opt_type = "CALL" if match.group(2) == "C" else "PUT"
    year = 2000 + int(match.group(3))
    month = int(match.group(4))
    strike_str = match.group(5)[1:]  # 去掉 M
    strike = float(strike_str) / 1000.0
    exchange = match.group(6)
    return {
        "underlying": f"{underlying}.{exchange}",
        "option_type": opt_type,
        "year": year,
        "month": month,
        "strike": strike,
        "exchange": exchange,
    }


def _get_expiry_date(year: int, month: int) -> date:
    """获取期权到期日 (当月第四个周三)"""
    first_day = date(year, month, 1)
    # 第一个周三
    first_wed = first_day + timedelta(days=(2 - first_day.weekday()) % 7)
    # 第四个周三
    return first_wed + timedelta(weeks=3)


# ============================================================
# 行权风险管理器
# ============================================================


class OptionExerciseRiskManager:
    """期权行权/指派风险管理器"""

    # 预警天数
    WARNING_DAYS = 3  # 到期前 3 天预警
    DANGER_DAYS = 1  # 到期前 1 天危险

    def __init__(self):
        self.today = date.today()

    # ----------------------------------------------------------
    # 风险评估
    # ----------------------------------------------------------

    def assess_risk(
        self,
        symbol: str,
        side: str,
        quantity: int,
        underlying_price: float,
        premium: float = 0.0,
    ) -> ExerciseRiskResult | None:
        """评估单只期权的行权/指派风险

        Args:
            symbol: 期权代码 (如 "510050C2507M03000.SH")
            side: "BUY" (买方) / "SELL" (卖方)
            quantity: 持仓数量
            underlying_price: 标的最新价
            premium: 权利金单价

        Returns:
            ExerciseRiskResult 或 None (解析失败)
        """
        parsed = _parse_option_code(symbol)
        if parsed is None:
            logger.warning("无法解析期权代码: %s", symbol)
            return None

        opt_type = parsed["option_type"]
        strike = parsed["strike"]
        expiry = _get_expiry_date(parsed["year"], parsed["month"])
        days_to_expiry = (expiry - self.today).days

        # 计算实值程度
        if opt_type == "CALL":
            moneyness = underlying_price / strike if strike > 0 else 0
            is_itm = underlying_price > strike
        else:
            moneyness = strike / underlying_price if underlying_price > 0 else 0
            is_itm = strike > underlying_price

        # 买方风险
        if side == "BUY":
            return self._assess_buyer_risk(
                symbol,
                opt_type,
                strike,
                underlying_price,
                days_to_expiry,
                is_itm,
                moneyness,
                quantity,
                premium,
            )

        # 卖方风险
        return self._assess_seller_risk(
            symbol,
            opt_type,
            strike,
            underlying_price,
            days_to_expiry,
            is_itm,
            moneyness,
            quantity,
            premium,
            parsed,
        )

    def _assess_buyer_risk(
        self,
        symbol: str,
        opt_type: str,
        strike: float,
        underlying_price: float,
        days_to_expiry: int,
        is_itm: bool,
        moneyness: float,
        quantity: int,
        premium: float,
    ) -> ExerciseRiskResult:
        """买方风险评估"""
        if days_to_expiry < 0:
            return ExerciseRiskResult(
                symbol=symbol,
                side="BUY",
                option_type=opt_type,
                strike=strike,
                underlying_price=underlying_price,
                days_to_expiry=days_to_expiry,
                moneyness=round(moneyness, 4),
                is_itm=is_itm,
                assignment_probability="EXPIRED",
                recommended_action="期权已到期",
                potential_loss=premium * quantity * 10000,  # 最大亏损=权利金
                message="期权已到期, 虚值作废/实值自动行权",
            )

        if is_itm and days_to_expiry <= self.DANGER_DAYS:
            prob = "CERTAIN"
            action = "实值期权将自动行权, 准备资金/标的"
            msg = f"距到期{days_to_expiry}天, 实值期权, 将自动行权"
            loss = premium * quantity * 10000
        elif is_itm:
            prob = "LOW"
            action = "实值期权, 到期将自动行权, 无需操作"
            msg = f"距到期{days_to_expiry}天, 买方持有实值期权"
            loss = premium * quantity * 10000
        elif days_to_expiry <= self.WARNING_DAYS:
            prob = "MEDIUM"
            action = "虚值期权即将到期作废, 建议平仓止损"
            msg = f"距到期{days_to_expiry}天, 虚值期权时间价值加速衰减"
            loss = premium * quantity * 10000
        else:
            prob = "LOW"
            action = "买方最大亏损为权利金, 风险可控"
            msg = f"距到期{days_to_expiry}天"
            loss = premium * quantity * 10000

        return ExerciseRiskResult(
            symbol=symbol,
            side="BUY",
            option_type=opt_type,
            strike=strike,
            underlying_price=underlying_price,
            days_to_expiry=days_to_expiry,
            moneyness=round(moneyness, 4),
            is_itm=is_itm,
            assignment_probability=prob,
            recommended_action=action,
            potential_loss=loss,
            message=msg,
        )

    def _assess_seller_risk(
        self,
        symbol: str,
        opt_type: str,
        strike: float,
        underlying_price: float,
        days_to_expiry: int,
        is_itm: bool,
        moneyness: float,
        quantity: int,
        premium: float,
        parsed: dict,
    ) -> ExerciseRiskResult:
        """卖方风险评估 (关键! 卖方有被指派风险)"""
        multiplier = 10000  # ETF 期权合约单位

        if days_to_expiry < 0:
            return ExerciseRiskResult(
                symbol=symbol,
                side="SELL",
                option_type=opt_type,
                strike=strike,
                underlying_price=underlying_price,
                days_to_expiry=days_to_expiry,
                moneyness=round(moneyness, 4),
                is_itm=is_itm,
                assignment_probability="EXPIRED",
                recommended_action="期权已到期",
                potential_loss=0,
                message="期权已到期",
            )

        # 计算被指派后的潜在损失
        if opt_type == "CALL":
            # 认购卖方被指派: 需以行权价卖出标的, 亏损 = (市价-行权价) × 数量
            potential_loss = max(0, (underlying_price - strike) * quantity * multiplier)
        else:
            # 认沽卖方被指派: 需以行权价买入标的, 亏损 = (行权价-市价) × 数量
            potential_loss = max(0, (strike - underlying_price) * quantity * multiplier)

        # 判断指派概率
        if days_to_expiry <= self.DANGER_DAYS:
            if is_itm and moneyness > 1.05:
                prob = "CERTAIN"
                action = "【紧急】深度实值+到期前1天, 几乎必然被指派!"
                msg = f"深度实值(程度{moneyness:.2%}), 距到期{days_to_expiry}天, 强烈建议立即平仓"
            elif is_itm:
                prob = "HIGH"
                action = "实值期权+临近到期, 被指派概率很高, 建议平仓"
                msg = f"实值期权, 距到期{days_to_expiry}天, 被指派风险高"
            else:
                prob = "MEDIUM"
                action = "虚值期权+临近到期, 仍有尾行权风险, 建议平仓"
                msg = f"虚值期权距到期{days_to_expiry}天, 尾行权风险"
        elif days_to_expiry <= self.WARNING_DAYS:
            prob = "HIGH" if is_itm else "MEDIUM"
            action = "进入行权预警期, 实值期权被指派概率高, 建议平仓"
            msg = f"距到期{days_to_expiry}天, 进入行权预警期"
        elif is_itm and moneyness > 1.10:
            prob = "MEDIUM"
            action = "深度实值期权, 建议提前平仓避险"
            msg = f"深度实值(程度{moneyness:.2%}), 注意到期指派风险"
        else:
            prob = "LOW"
            action = "风险可控, 继续监控"
            msg = f"距到期{days_to_expiry}天, 风险可控"

        return ExerciseRiskResult(
            symbol=symbol,
            side="SELL",
            option_type=opt_type,
            strike=strike,
            underlying_price=underlying_price,
            days_to_expiry=days_to_expiry,
            moneyness=round(moneyness, 4),
            is_itm=is_itm,
            assignment_probability=prob,
            recommended_action=action,
            potential_loss=potential_loss,
            message=msg,
        )

    # ----------------------------------------------------------
    # 批量检测
    # ----------------------------------------------------------

    def check_all(
        self,
        positions: list[dict],
        underlying_prices: dict[str, float],
    ) -> list[ExerciseRiskResult]:
        """批量检测所有期权持仓

        Args:
            positions: [{"symbol": "510050C2507M03000.SH", "side": "SELL",
                         "quantity": 10, "premium": 0.05}, ...]
            underlying_prices: {"510050.SH": 3.200, ...}

        Returns:
            风险评估结果列表 (按风险等级排序)
        """
        results = []
        for pos in positions:
            symbol = pos.get("symbol", "")
            side = pos.get("side", "BUY")
            quantity = int(pos.get("quantity", 0))
            premium = float(pos.get("premium", 0.0))

            parsed = _parse_option_code(symbol)
            if parsed is None:
                continue

            underlying = parsed["underlying"]
            underlying_price = underlying_prices.get(underlying, 0)
            if underlying_price <= 0:
                logger.warning("缺少标的 %s 价格, 跳过 %s", underlying, symbol)
                continue

            result = self.assess_risk(
                symbol,
                side,
                quantity,
                underlying_price,
                premium,
            )
            if result:
                results.append(result)

        # 按风险排序: CERTAIN > HIGH > MEDIUM > LOW
        risk_order = {"CERTAIN": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "EXPIRED": 4}
        results.sort(key=lambda r: risk_order.get(r.assignment_probability, 99))

        return results

    # ----------------------------------------------------------
    # 平仓建议
    # ----------------------------------------------------------

    def generate_close_orders(
        self,
        risk_results: list[ExerciseRiskResult],
        close_threshold: str = "HIGH",
    ) -> list[dict]:
        """为高风险期权生成平仓建议

        Args:
            risk_results: assess_risk / check_all 的结果
            close_threshold: 触发平仓的最低风险等级
                            ("CERTAIN" / "HIGH" / "MEDIUM")

        Returns:
            平仓订单列表
        """
        risk_order = {"CERTAIN": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        threshold_value = risk_order.get(close_threshold, 1)

        orders = []
        for r in risk_results:
            if risk_order.get(r.assignment_probability, 99) > threshold_value:
                continue

            # 卖方: 买入平仓
            if r.side == "SELL":
                orders.append(
                    {
                        "symbol": r.symbol,
                        "side": "BUY",
                        "offset": "CLOSE",  # 平仓
                        "reason": f"行权风险管理: {r.assignment_probability}",
                        "days_to_expiry": r.days_to_expiry,
                        "potential_loss": r.potential_loss,
                        "urgency": (
                            "high"
                            if r.assignment_probability == "CERTAIN"
                            else "medium"
                        ),
                    }
                )
            # 买方: 卖出平仓 (虚值期权止损)
            elif r.side == "BUY" and not r.is_itm:
                orders.append(
                    {
                        "symbol": r.symbol,
                        "side": "SELL",
                        "offset": "CLOSE",
                        "reason": "虚值期权到期前平仓止损",
                        "days_to_expiry": r.days_to_expiry,
                        "potential_loss": r.potential_loss,
                        "urgency": "medium",
                    }
                )

        return orders

    # ----------------------------------------------------------
    # 深度实值自动平仓 (避免被指派)
    # ----------------------------------------------------------

    def auto_close_deep_itm(
        self,
        positions: list[dict],
        underlying_prices: dict[str, float],
        moneyness_threshold: float = 1.05,
        max_days: int = 5,
    ) -> list[dict]:
        """深度实值期权自动平仓 (避免到期被指派)

        条件: 实值程度 > moneyness_threshold + 距到期 < max_days + 卖方持仓

        Args:
            positions: 期权持仓列表
            underlying_prices: 标的价格
            moneyness_threshold: 实值程度阈值 (默认 1.05 = 5%)
            max_days: 最大距到期天数

        Returns:
            平仓订单列表
        """
        results = self.check_all(positions, underlying_prices)

        close_candidates = []
        for r in results:
            if (
                r.side == "SELL"
                and r.is_itm
                and r.moneyness > moneyness_threshold
                and 0 <= r.days_to_expiry <= max_days
            ):
                close_candidates.append(r)

        if close_candidates:
            logger.warning("深度实值期权自动平仓: %d 个持仓", len(close_candidates))

        return self.generate_close_orders(close_candidates, "HIGH")


__all__ = [
    "ExerciseRiskResult",
    "OptionExerciseRiskManager",
    "_get_expiry_date",
    "_parse_option_code",
]
