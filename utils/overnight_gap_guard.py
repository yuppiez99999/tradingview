"""隔夜跳空 Guard (Overnight Gap Guard)
====================================
T11 (2026-07-28): 监控开盘隔夜跳空, 跳空 > 3% 自动降仓.

设计背景:
    A股开盘 09:25 集合竞价, 若开盘价相对前收盘价跳空超过 3%,
    说明隔夜发生了重大事件 (政策/外盘/财报), 需要主动降仓控制风险.

触发阈值:
    L1 警戒: 跳空 >= 2% → 记录告警, 收紧单票集中度
    L2 降仓: 跳空 >= 3% → 自动降仓 30% (仅对跳空方向不利的持仓)
    L3 清仓: 跳空 >= 5% → 自动降仓 50% + 禁止加仓

降仓规则:
    - 跳空上涨 (gap > 0): 只降空头持仓 (不利方向)
    - 跳空下跌 (gap < 0): 只降多头持仓 (不利方向)
    - 降仓数量 = 持仓 × 降仓比例 (向下取整到 100 股)

用法:
    from utils.overnight_gap_guard import OvernightGapGuard
    guard = OvernightGapGuard()
    status = guard.check_gap(opening_price=10.30, prev_close=10.00)
    if status["level"] >= 2:
        plan = guard.apply_to_plan(plan, status, positions)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from utils.datetime_utils import now_bj

logger = logging.getLogger("overnight_gap_guard")


@dataclass
class GapStatus:
    """跳空状态."""

    symbol: str
    opening_price: float
    prev_close: float
    gap_pct: float  # 跳空幅度 (小数, 如 0.03 = 3%)
    gap_direction: str  # "UP" / "DOWN" / "NONE"
    level: int  # 0/1/2/3
    level_name: str  # "正常"/"L1警戒"/"L2降仓"/"L3清仓"
    reduce_pct: float  # 降仓比例 (0.0/0.0/0.30/0.50)
    actions: list[str] = field(default_factory=list)
    timestamp: str = ""


class OvernightGapGuard:
    """隔夜跳空 Guard — 开盘跳空超过阈值时自动降仓.

    阈值:
        L1 警戒: |gap| >= 2% → 记录告警, 收紧集中度
        L2 降仓: |gap| >= 3% → 降仓 30% (仅不利方向持仓)
        L3 清仓: |gap| >= 5% → 降仓 50% + 禁止加仓
    """

    # 触发阈值 (跳空幅度, 正值)
    L1_THRESHOLD = 0.02  # 2%
    L2_THRESHOLD = 0.03  # 3%
    L3_THRESHOLD = 0.05  # 5%

    # 降仓比例
    L2_REDUCE_PCT = 0.30  # L2 降仓 30%
    L3_REDUCE_PCT = 0.50  # L3 降仓 50%

    # A股最小交易单位
    MIN_LOT_SIZE = 100

    def __init__(
        self,
        l1_threshold: float | None = None,
        l2_threshold: float | None = None,
        l3_threshold: float | None = None,
        l2_reduce_pct: float | None = None,
        l3_reduce_pct: float | None = None,
    ):
        """初始化隔夜跳空 Guard.

        Args:
            l1_threshold: L1 警戒阈值 (跳空幅度), 默认 0.02
            l2_threshold: L2 降仓阈值, 默认 0.03
            l3_threshold: L3 清仓阈值, 默认 0.05
            l2_reduce_pct: L2 降仓比例, 默认 0.30
            l3_reduce_pct: L3 降仓比例, 默认 0.50
        """
        self.l1_threshold = (
            float(l1_threshold) if l1_threshold is not None else self.L1_THRESHOLD
        )
        self.l2_threshold = (
            float(l2_threshold) if l2_threshold is not None else self.L2_THRESHOLD
        )
        self.l3_threshold = (
            float(l3_threshold) if l3_threshold is not None else self.L3_THRESHOLD
        )
        self.l2_reduce_pct = (
            float(l2_reduce_pct) if l2_reduce_pct is not None else self.L2_REDUCE_PCT
        )
        self.l3_reduce_pct = (
            float(l3_reduce_pct) if l3_reduce_pct is not None else self.L3_REDUCE_PCT
        )

    def check_gap(
        self,
        opening_price: float,
        prev_close: float,
        symbol: str = "UNKNOWN",
    ) -> GapStatus:
        """检查单个标的的隔夜跳空状态.

        Args:
            opening_price: 开盘价
            prev_close: 前收盘价
            symbol: 标的代码

        Returns:
            GapStatus 对象
        """
        if prev_close <= 0:
            return GapStatus(
                symbol=symbol,
                opening_price=opening_price,
                prev_close=prev_close,
                gap_pct=0.0,
                gap_direction="NONE",
                level=0,
                level_name="数据异常",
                reduce_pct=0.0,
                timestamp=now_bj().isoformat(),
            )

        gap_pct = (opening_price - prev_close) / prev_close
        abs_gap = abs(gap_pct)

        # 确定方向
        if gap_pct > 0.001:  # 0.1% 以上视为上涨
            direction = "UP"
        elif gap_pct < -0.001:
            direction = "DOWN"
        else:
            direction = "NONE"

        # 确定级别
        level = 0
        reduce_pct = 0.0
        actions: list[str] = []
        level_name = "正常"

        if abs_gap >= self.l3_threshold:
            level = 3
            reduce_pct = self.l3_reduce_pct
            level_name = "L3清仓"
            actions.append(f"降仓 {reduce_pct * 100:.0f}% (仅不利方向持仓)")
            actions.append("禁止加仓")
        elif abs_gap >= self.l2_threshold:
            level = 2
            reduce_pct = self.l2_reduce_pct
            level_name = "L2降仓"
            actions.append(f"降仓 {reduce_pct * 100:.0f}% (仅不利方向持仓)")
        elif abs_gap >= self.l1_threshold:
            level = 1
            level_name = "L1警戒"
            actions.append("记录告警, 收紧单票集中度")

        status = GapStatus(
            symbol=symbol,
            opening_price=opening_price,
            prev_close=prev_close,
            gap_pct=gap_pct,
            gap_direction=direction,
            level=level,
            level_name=level_name,
            reduce_pct=reduce_pct,
            actions=actions,
            timestamp=now_bj().isoformat(),
        )

        if level >= 2:
            logger.warning(
                "[OvernightGapGuard] %s 触发 %s: 跳空 %.2f%% (%s), 降仓比例 %.0f%%",
                symbol,
                level_name,
                gap_pct * 100,
                direction,
                reduce_pct * 100,
            )
        elif level == 1:
            logger.info(
                "[OvernightGapGuard] %s L1 警戒: 跳空 %.2f%% (%s)",
                symbol,
                gap_pct * 100,
                direction,
            )

        return status

    def apply_to_plan(
        self,
        plan: dict,
        status: GapStatus,
        positions: dict[str, dict],
    ) -> dict:
        """将跳空状态应用到交易计划, 生成降仓订单.

        Args:
            plan: 交易计划字典
            status: check_gap() 返回的状态
            positions: 持仓字典 {symbol: {side/quantity/actual_shares}}

        Returns:
            修改后的 plan (新增 gap_guard_reduce_orders)
        """
        plan.setdefault("execution_plan", {})
        plan.setdefault("market_state", {})
        plan.setdefault("risk_guard", {})

        # 记录 Guard 元数据
        plan["risk_guard"]["overnight_gap_guard"] = {
            "symbol": status.symbol,
            "gap_pct": status.gap_pct,
            "gap_direction": status.gap_direction,
            "level": status.level,
            "level_name": status.level_name,
            "reduce_pct": status.reduce_pct,
            "actions": status.actions,
        }

        if status.level == 0:
            return plan

        # L1: 只收紧集中度, 不降仓
        if status.level == 1:
            existing_level = plan["market_state"].get("circuit_level")
            if existing_level not in ("WARNING", "CRITICAL"):
                plan["market_state"]["circuit_level"] = "ALERT"
            return plan

        # L2/L3: 生成降仓订单 (仅不利方向持仓)
        reduce_orders: list[dict] = []
        target_symbol = status.symbol

        for symbol, pos in positions.items():
            if not isinstance(pos, dict):
                continue
            # 只处理跳空标的本身的持仓
            if target_symbol != "UNKNOWN" and symbol != target_symbol:
                continue

            side = str(pos.get("side", "")).upper()
            quantity = int(pos.get("actual_shares", pos.get("quantity", 0)))

            if quantity <= 0:
                continue

            # 判断是否为不利方向
            # 跳空上涨 (UP): 不利方向 = 空头 (SELL/SHORT)
            # 跳空下跌 (DOWN): 不利方向 = 多头 (BUY/LONG)
            is_adverse = False
            if (
                status.gap_direction == "UP"
                and side in ("SELL", "SHORT")
                or status.gap_direction == "DOWN"
                and side in ("BUY", "LONG")
            ):
                is_adverse = True

            if not is_adverse:
                continue

            # 计算降仓数量
            reduce_qty = int(quantity * status.reduce_pct)
            # 向下取整到 100 股
            reduce_qty = (reduce_qty // self.MIN_LOT_SIZE) * self.MIN_LOT_SIZE

            if reduce_qty < self.MIN_LOT_SIZE:
                continue

            # 生成降仓订单
            if side in ("SELL", "SHORT"):
                # 空头降仓 = BUY_TO_CLOSE
                reduce_orders.append(
                    {
                        "symbol": symbol,
                        "direction": "BUY_TO_CLOSE",
                        "shares": reduce_qty,
                        "order_type": "MARKET",
                        "rationale": f"overnight_gap_{status.level_name}_close_short",
                        "gap_pct": status.gap_pct,
                    }
                )
            else:
                # 多头降仓 = SELL
                reduce_orders.append(
                    {
                        "symbol": symbol,
                        "direction": "SELL",
                        "shares": reduce_qty,
                        "order_type": "MARKET",
                        "rationale": f"overnight_gap_{status.level_name}_reduce_long",
                        "gap_pct": status.gap_pct,
                    }
                )

        # 将降仓订单加入执行计划
        plan["execution_plan"].setdefault("gap_guard_reduce_orders", [])
        plan["execution_plan"]["gap_guard_reduce_orders"].extend(reduce_orders)

        # 更新 market_state
        if status.level >= 3:
            plan["market_state"]["halt_add_positions"] = True
            existing_level = plan["market_state"].get("circuit_level")
            if existing_level != "CRITICAL":
                plan["market_state"]["circuit_level"] = "CRITICAL"
        else:
            existing_level = plan["market_state"].get("circuit_level")
            if existing_level not in ("CRITICAL",):
                plan["market_state"]["circuit_level"] = "WARNING"

        if reduce_orders:
            logger.warning(
                "[OvernightGapGuard] %s 生成 %d 笔降仓订单, 总降仓股数=%d",
                status.symbol,
                len(reduce_orders),
                sum(o["shares"] for o in reduce_orders),
            )

        return plan


__all__ = ["GapStatus", "OvernightGapGuard"]
