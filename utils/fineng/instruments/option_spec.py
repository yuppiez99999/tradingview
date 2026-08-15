"""
期权合约规格定义 (Option Contract Specification)

定义了统一的期权合约数据结构, 作为所有定价/风险模块的输入标准。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class OptionType(Enum):
    """期权类型"""
    CALL = "CALL"
    PUT = "PUT"


class OptionSide(Enum):
    """持仓方向"""
    LONG = "LONG"
    SHORT = "SHORT"


class ExerciseStyle(Enum):
    """行权方式"""
    EUROPEAN = "EUROPEAN"   # 欧式 (仅到期日行权)
    AMERICAN = "AMERICAN"   # 美式 (到期前任一天可行权)


@dataclass(frozen=True)
class OptionSpec:
    """期权合约规格

    不可变数据类, 一旦创建不可修改, 保证定价函数的引用透明性。

    Attributes:
        underlying: 标的代码 (如 "510050.SH")
        option_type: CALL 或 PUT
        strike: 行权价
        expiry: 到期日
        exercise_style: 欧式/美式
        multiplier: 合约乘数 (ETF期权=10000, 股指期权=100)
        exchange: 交易所代码
    """

    underlying: str
    option_type: OptionType
    strike: float
    expiry: date
    exercise_style: ExerciseStyle = ExerciseStyle.EUROPEAN
    multiplier: float = 10000.0  # ETF 期权默认乘数
    exchange: str = ""

    @property
    def is_call(self) -> bool:
        return self.option_type == OptionType.CALL

    @property
    def is_put(self) -> bool:
        return self.option_type == OptionType.PUT

    def days_to_expiry(self, valuation_date: date | None = None) -> int:
        """距到期日的自然日天数"""
        ref = valuation_date or date.today()
        return (self.expiry - ref).days

    def years_to_expiry(self, valuation_date: date | None = None) -> float:
        """距到期日的年化时间 (ACT/365)"""
        return max(self.days_to_expiry(valuation_date), 0) / 365.0

    def payoff(self, spot: float) -> float:
        """到期收益 (不考虑权利金)"""
        if self.is_call:
            return max(spot - self.strike, 0.0)
        else:
            return max(self.strike - spot, 0.0)

    def moneyness(self, spot: float) -> float:
        """计算实值程度 (S/K for call, K/S for put)"""
        if spot <= 0 or self.strike <= 0:
            return 0.0
        if self.is_call:
            return spot / self.strike
        else:
            return self.strike / spot

    @property
    def is_itm(self, spot: float | None = None) -> bool:
        """是否实值 (需要 spot 参数)"""
        if spot is None:
            raise ValueError("spot price required to determine ITM status")
        return self.moneyness(spot) > 1.0
