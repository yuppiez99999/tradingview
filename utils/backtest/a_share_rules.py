"""W6.4.2 SimTradeLab A 股 T+1 模拟借鉴模块。

职责:
    - 在 G15 事件驱动回测中实现 A 股 T+1 交易限制
    - 借鉴 SimTradeLab (https://github.com/kay-ou/SimTradeLab) 的 lot-based 持仓管理
    - 整合 utils/trading_rules.py (T+0/T+1 判定) + utils/market_rules.py (20cm 涨跌停)

SimTradeLab 核心设计借鉴:
    1. Lot-based 持仓: 每个 BUY 成交产生一个 PositionLot (code, volume, date)
    2. T+1 强制: SELL 时仅可卖出 acquisition_date < current_date 的 lot
    3. FIFO 消费: SELL 成交按 lot 时间顺序消费
    4. 当日可卖量 = sum(lot.volume for lot in lots if lot.date < today)

与现有模块整合:
    - utils/trading_rules.py: is_t0_eligible() 判断 T+0/T+1 → 本模块用之跳过 T+0 标的
    - utils/market_rules.py: is_20cm_symbol() 判断涨跌停板 → 本模块用之设置 price_limit_pct
    - utils/backtest/matching_engine.py: 本模块提供 pre-match filter, 不修改撮合引擎本身

设计原则 (AGENTS.md):
    - 不可变性 (§5.1): PositionLot 为 dataclass(frozen=True)
    - 单一职责: 只做 T+1 + 涨跌停规则, 不做撮合
    - 多小文件 (§5.3): 本模块 < 350 行
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from utils.market_rules import is_20cm_symbol
from utils.trading_rules import is_t0_eligible

# A 股涨跌停交易限制 (交易所规则, 非 market_rules 的数据验证阈值)
PRICE_LIMIT_10CM = 0.10  # 主板: ±10%
PRICE_LIMIT_20CM = 0.20  # 科创板/创业板注册制: ±20%


# ============================================================
# 1. 持仓批次 (Lot) — SimTradeLab 核心数据结构
# ============================================================


@dataclass(frozen=True)
class PositionLot:
    """持仓批次 (不可变)。

    每次买入成交产生一个 lot, 记录 acquisition_date 用于 T+1 检查。

    Attributes:
        code: 标的代码
        volume: 批次手数
        acquisition_date: 买入日期 (交易日)
        avg_price: 买入均价
    """

    code: str
    volume: float
    acquisition_date: date
    avg_price: float


# ============================================================
# 2. T+1 持仓追踪器
# ============================================================


class T1PositionTracker:
    """T+1 持仓追踪器 — 按 code 分组管理 lots。

    核心操作:
        - add_lot(code, volume, date, price): 买入成交后追加 lot
        - consume(code, volume, current_date): 卖出时 FIFO 消费可卖 lots
        - available_volume(code, current_date): 查询当日可卖量
        - total_volume(code): 查询总持仓量

    T+1 规则:
        - available_volume(code, today) = sum(lot.volume for lot in lots
                                               if lot.acquisition_date < today)
        - 若 is_t0_eligible(code) → available_volume = total_volume (无 T+1 限制)
    """

    def __init__(self) -> None:
        self._lots: dict[str, list[PositionLot]] = {}

    def add_lot(
        self,
        code: str,
        volume: float,
        acquisition_date: date,
        avg_price: float,
    ) -> None:
        """买入成交后追加 lot。"""
        if volume <= 0:
            return
        lot = PositionLot(
            code=code,
            volume=volume,
            acquisition_date=acquisition_date,
            avg_price=avg_price,
        )
        self._lots.setdefault(code, []).append(lot)

    def consume(
        self,
        code: str,
        volume: float,
        current_date: date,
    ) -> tuple[float, float]:
        """卖出时 FIFO 消费可卖 lots。

        仅消费 acquisition_date < current_date 的 lot (T+1 规则)。
        若为 T+0 标的 (is_t0_eligible=True), 可消费所有 lot。

        Args:
            code: 标的代码
            volume: 请求卖出量
            current_date: 当前日期

        Returns:
            (consumed_volume, consumed_value) — 实际消费量 + 加权成本
            consumed_volume ≤ volume, 若可卖不足则为可卖量
        """
        lots = self._lots.get(code, [])
        if not lots:
            return 0.0, 0.0

        is_t0 = is_t0_eligible(code)
        remaining = volume
        consumed_vol = 0.0
        consumed_val = 0.0

        new_lots: list[PositionLot] = []
        for lot in lots:
            if remaining <= 0:
                new_lots.append(lot)
                continue

            # T+1 检查: 非 T+0 标的的 lot 必须满足 acquisition_date < current_date
            if not is_t0 and lot.acquisition_date >= current_date:
                new_lots.append(lot)
                continue

            # FIFO 消费
            consume = min(remaining, lot.volume)
            consumed_vol += consume
            consumed_val += consume * lot.avg_price
            remaining -= consume

            if lot.volume - consume > 0.0001:
                # 剩余部分保留 (创建新 lot, 不可变)
                new_lots.append(
                    PositionLot(
                        code=lot.code,
                        volume=lot.volume - consume,
                        acquisition_date=lot.acquisition_date,
                        avg_price=lot.avg_price,
                    )
                )

        self._lots[code] = new_lots
        return consumed_vol, consumed_val

    def available_volume(self, code: str, current_date: date) -> float:
        """查询当日可卖量 (T+1 规则)。"""
        lots = self._lots.get(code, [])
        if not lots:
            return 0.0

        # T+0 标的: 无限制
        if is_t0_eligible(code):
            return sum(lot.volume for lot in lots)

        # T+1 标的: 仅 acquisition_date < current_date 的 lot 可卖
        return sum(lot.volume for lot in lots if lot.acquisition_date < current_date)

    def total_volume(self, code: str) -> float:
        """查询总持仓量 (含 T+1 不可卖部分)。"""
        return sum(lot.volume for lot in self._lots.get(code, []))

    def get_lots(self, code: str) -> list[PositionLot]:
        """获取 code 的所有 lot 列表 (副本)。"""
        return list(self._lots.get(code, []))


# ============================================================
# 3. A 股交易规则
# ============================================================


@dataclass
class AShareTradingRules:
    """A 股交易规则集合 (T+1 + 涨跌停)。

    用于 G15 回测引擎的 pre-match 规则检查。

    Attributes:
        enable_t1: 是否启用 T+1 检查 (默认 True)
        enable_price_limit: 是否启用涨跌停检查 (默认 True)
        t1_tracker: T+1 持仓追踪器 (若 enable_t1=True)
    """

    enable_t1: bool = True
    enable_price_limit: bool = True
    t1_tracker: T1PositionTracker = field(default_factory=T1PositionTracker)

    def check_sell_t1(
        self,
        code: str,
        sell_volume: float,
        current_date: date,
    ) -> tuple[bool, str]:
        """检查卖出订单是否符合 T+1 规则。

        Returns:
            (compliant, reason) — (True, "") 或 (False, "T+1: 可卖量不足")
        """
        if not self.enable_t1:
            return True, ""

        # T+0 标的跳过
        if is_t0_eligible(code):
            return True, ""

        available = self.t1_tracker.available_volume(code, current_date)
        if available >= sell_volume:
            return True, ""

        shortage = sell_volume - available
        return False, (
            f"T+1限制: {code} 请求卖出 {sell_volume:.0f} 股, "
            f"当日可卖 {available:.0f} 股, 不足 {shortage:.0f} 股"
        )

    def get_price_limit_pct(self, code: str) -> float:
        """获取涨跌停限制百分比。

        Returns:
            20cm 板: 0.20 (±20%)
            10cm 板: 0.10 (±10%)
        """
        if not self.enable_price_limit:
            return 1.0  # 无限制

        if is_20cm_symbol(code):
            return PRICE_LIMIT_20CM  # 0.20
        return PRICE_LIMIT_10CM  # 0.10

    def check_price_limit(
        self,
        code: str,
        order_price: float,
        pre_close: float,
    ) -> tuple[bool, str]:
        """检查委托价是否在涨跌停范围内。

        Args:
            code: 标的代码
            order_price: 委托价
            pre_close: 昨收价

        Returns:
            (compliant, reason)
        """
        if not self.enable_price_limit or pre_close <= 0:
            return True, ""

        limit_pct = self.get_price_limit_pct(code)
        upper = pre_close * (1 + limit_pct)
        lower = pre_close * (1 - limit_pct)

        if order_price > upper:
            return (
                False,
                f"涨停限制: {code} 委托价 {order_price:.2f} > 涨停价 {upper:.2f}",
            )
        if order_price < lower:
            return (
                False,
                f"跌停限制: {code} 委托价 {order_price:.2f} < 跌停价 {lower:.2f}",
            )
        return True, ""

    def on_buy_fill(
        self,
        code: str,
        volume: float,
        fill_price: float,
        acquisition_date: date,
    ) -> None:
        """买入成交回调 — 追加 lot。"""
        self.t1_tracker.add_lot(code, volume, acquisition_date, fill_price)

    def on_sell_fill(
        self,
        code: str,
        volume: float,
        current_date: date,
    ) -> tuple[float, float]:
        """卖出成交回调 — FIFO 消费 lot。

        Returns:
            (consumed_volume, consumed_value) — 实际消费量 + 加权成本
        """
        return self.t1_tracker.consume(code, volume, current_date)


# ============================================================
# 4. 日期工具
# ============================================================


def bar_to_date(bar) -> date:  # type: ignore[no-untyped-def]
    """从 BarData 提取交易日日期。

    BarData.date 格式: YYYYMMDD (int), 需转为 date 对象。
    """
    d = int(getattr(bar, "date", 0))
    if d <= 0:
        # fallback: 从 ts_event 纳秒时间戳提取
        ts = int(getattr(bar, "ts_event", 0))
        if ts > 0:
            return datetime.fromtimestamp(ts / 1e9).date()
        return date.today()

    year = d // 10000
    month = (d % 10000) // 100
    day = d % 100
    return date(year, month, day)


# ============================================================
# 5. 订单过滤结果
# ============================================================


@dataclass
class T1FilterResult:
    """T+1 订单过滤结果。

    Attributes:
        passed: 是否通过 T+1 检查
        reason: 拒绝原因 (passed=True 时为空)
        adjusted_volume: 通过后的可成交量 (≤ order.volume)
    """

    passed: bool
    reason: str = ""
    adjusted_volume: float = 0.0


def filter_order_t1(
    rules: AShareTradingRules,
    order,  # type: ignore[no-untyped-def]  OrderData
    current_date: date,
) -> T1FilterResult:
    """对单个订单执行 T+1 过滤。

    用途: 在 G15 引擎 _match_ready_orders 之前调用,
          拦截或调整 T+1 违规的 SELL 订单。

    规则:
        - BUY: 直接通过 (T+1 不限制买入)
        - SELL CLOSE / SELL CLOSETODAY:
            - 检查 available_volume >= order.volume
            - 若不足: 返回 passed=False (拒绝) 或 adjusted_volume (部分成交)
    """
    if order.direction == "BUY":
        return T1FilterResult(passed=True, adjusted_volume=order.volume)

    # SELL
    compliant, reason = rules.check_sell_t1(order.code, order.volume, current_date)
    if compliant:
        return T1FilterResult(passed=True, adjusted_volume=order.volume)

    # 部分放行: 若有部分可卖量, 调整为可卖量
    available = rules.t1_tracker.available_volume(order.code, current_date)
    if available > 0:
        return T1FilterResult(
            passed=True,
            adjusted_volume=available,
            reason=f"T+1部分放行: 请求{order.volume:.0f}, 可卖{available:.0f}",
        )

    return T1FilterResult(passed=False, reason=reason, adjusted_volume=0.0)


__all__ = [
    "AShareTradingRules",
    "PositionLot",
    "T1FilterResult",
    "T1PositionTracker",
    "bar_to_date",
    "filter_order_t1",
]
