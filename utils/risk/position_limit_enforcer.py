"""T10 持仓集中度执行器 — 下单前对组合层面的持仓/暴露进行硬约束.

属于「不崩风控六件套」第 2 位, 核心目的: **任何下单后组合都不会进入过度集中或过度杠杆状态**.

约束项 (4 项, 支持任意配置):
    1. SINGLE_NAME_CAP     : 单标的市值 / 总权益 ≤ threshold (默认 15%) — 防单票黑天鹅
    2. SECTOR_CAP          : 单行业/板块市值 / 总权益 ≤ threshold (默认 30%) — 防行业踩踏
    3. NET_EXPOSURE_CAP    : (多头市值 - 空头市值) / 总权益绝对值 ≤ threshold (默认 120%) — 防过度净敞口
    4. GROSS_LEVERAGE_CAP  : (多头市值 + 空头市值) / 总权益 ≤ threshold (默认 200%) — 防总杠杆失控

用法:
    from utils.risk.position_limit_enforcer import (
        PositionLimitEnforcer, PositionSnapshot, OrderImpact, EnforcementResult,
    )
    snap = PositionSnapshot(total_equity=10_000_000, positions={"sh600519": (100, 1700.0)},
                            sectors={"sh600519": "白酒"})
    enf = PositionLimitEnforcer()
    impact = OrderImpact(symbol="sh600519", side="buy", delta_shares=100, price=1700.0,
                         sector="白酒")
    result = enf.check_after_trade(snap, impact)
    if result.rejected:
        logger.error(f"[T10] 拦截: {result.reasons}")

零行为变更: 不改任何传入对象, 纯函数式检查.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger("position_limit")


@dataclass
class PositionSnapshot:
    """下单前组合快照.

    Args:
        total_equity: 组合总权益 (元). 做分母.
        positions:    {symbol: (shares, last_price)}, 多头 shares>0, 空头 shares<0
        sectors:      {symbol: sector_name} 可选, 用于 SECTOR_CAP 检查 (缺省时跳过该检查)
    """

    total_equity: float
    positions: Mapping[str, tuple[int, float]]
    sectors: Mapping[str, str] = field(default_factory=dict)

    def market_value(self, symbol: str) -> float:
        """返回某标的当前市值 (多头正, 空头负)."""
        if symbol not in self.positions:
            return 0.0
        shares, px = self.positions[symbol]
        return shares * px


@dataclass
class OrderImpact:
    """单笔订单对持仓的影响."""

    symbol: str
    side: str  # "buy" | "sell"
    delta_shares: int  # 正数 = 增加股数, 负数 = 减少股数 (调用方负责按 side 换算正负)
    price: float
    sector: str = ""  # 可选, 若未传则从 PositionSnapshot.sectors 取

    @property
    def delta_notional(self) -> float:
        return self.delta_shares * self.price


@dataclass
class EnforcementResult:
    rejected: bool = False
    reasons: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    projected_single: dict[str, float] = field(
        default_factory=dict
    )  # symbol → 下单后占比
    projected_sector: dict[str, float] = field(
        default_factory=dict
    )  # sector → 下单后占比
    projected_net_exp_pct: float = 0.0
    projected_gross_lv: float = 0.0
    timestamp: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )

    @property
    def is_pass(self) -> bool:
        return not self.rejected


class PositionLimitEnforcer:
    """T10 持仓集中度执行器 — 4 项硬约束."""

    def __init__(
        self,
        single_name_cap_pct: float = 0.15,
        sector_cap_pct: float = 0.30,
        net_exposure_cap_pct: float = 1.20,
        gross_leverage_cap: float = 2.00,
        mode: str = "BLOCK",
    ) -> None:
        if not (0 < single_name_cap_pct <= 1.0):
            raise ValueError(
                f"single_name_cap_pct 应在 (0, 1], 实际 {single_name_cap_pct}"
            )
        if not (0 < sector_cap_pct <= 1.0):
            raise ValueError(f"sector_cap_pct 应在 (0, 1], 实际 {sector_cap_pct}")
        if net_exposure_cap_pct <= 0:
            raise ValueError(f"net_exposure_cap_pct 应 >0, 实际 {net_exposure_cap_pct}")
        if gross_leverage_cap <= 0:
            raise ValueError(f"gross_leverage_cap 应 >0, 实际 {gross_leverage_cap}")
        if mode not in ("BLOCK", "WARN"):
            raise ValueError(f"mode 必须 BLOCK/WARN, 实际 {mode}")

        self.single_cap = single_name_cap_pct
        self.sector_cap = sector_cap_pct
        self.net_cap = net_exposure_cap_pct
        self.gross_cap = gross_leverage_cap
        self.mode = mode

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------

    def check_after_trade(
        self, snap: PositionSnapshot, impact: OrderImpact
    ) -> EnforcementResult:
        """模拟单笔订单执行后检查全部约束."""
        if snap.total_equity <= 0:
            # 无权益则所有比例检查无意义, 直接 fail-close
            r = EnforcementResult(rejected=(self.mode == "BLOCK"))
            r.reasons.append(
                f"[EQUITY_ZERO] total_equity={snap.total_equity} ≤ 0, 禁止任何下单"
            )
            return r

        result = EnforcementResult()

        # 先计算下单后的市值投影
        projected_mv: dict[str, float] = {}
        for sym, (sh, px) in snap.positions.items():
            projected_mv[sym] = sh * px
        cur_mv = projected_mv.get(impact.symbol, 0.0)
        projected_mv[impact.symbol] = cur_mv + impact.delta_notional

        long_mv = sum(max(v, 0.0) for v in projected_mv.values())
        short_mv = -sum(min(v, 0.0) for v in projected_mv.values())
        net_exp = long_mv - short_mv
        gross = long_mv + short_mv

        result.projected_net_exp_pct = net_exp / snap.total_equity
        result.projected_gross_lv = gross / snap.total_equity

        # 1. 单票集中度
        self._rule_single_name(projected_mv, snap.total_equity, result)
        # 2. 行业集中度
        sector_affecting = impact.sector or snap.sectors.get(impact.symbol, "")
        self._rule_sector(
            projected_mv, snap.sectors, sector_affecting, snap.total_equity, result
        )
        # 3. 净敞口
        self._rule_net_exposure(result)
        # 4. 总杠杆
        self._rule_gross_leverage(result)

        if result.reasons and self.mode == "BLOCK":
            result.rejected = True
            logger.warning(
                f"[T10] 拦截 {impact.symbol} {impact.side} {abs(impact.delta_shares)}@{impact.price:.2f} "
                f"原因={result.reasons}"
            )
        elif result.reasons and self.mode == "WARN":
            logger.warning(f"[T10] WARN (放行) 原因={result.reasons}")
        return result

    # ------------------------------------------------------------
    # 规则实现
    # ------------------------------------------------------------

    def _rule_single_name(
        self, mv: dict[str, float], equity: float, result: EnforcementResult
    ) -> None:
        rule = "SINGLE_NAME"
        result.checked.append(rule)
        worst_sym = ""
        worst_pct = 0.0
        for sym, v in mv.items():
            pct = abs(v) / equity
            result.projected_single[sym] = round(pct, 6)
            if pct > worst_pct:
                worst_pct = pct
                worst_sym = sym
        if worst_pct > self.single_cap:
            result.reasons.append(
                f"[{rule}] {worst_sym} 下单后占比 {worst_pct:.2%} > 阈值 {self.single_cap:.2%}"
            )

    def _rule_sector(
        self,
        mv: dict[str, float],
        sectors: Mapping[str, str],
        affected_sector: str,
        equity: float,
        result: EnforcementResult,
    ) -> None:
        rule = "SECTOR"
        if not sectors and not affected_sector:
            result.checked.append(rule + "_DISABLED(NO_SECTORS)")
            return
        # 合并 sectors + 本次 impact.sector
        merged_sectors = dict(sectors)
        if affected_sector:
            merged_sectors.setdefault(
                next(iter(mv.keys()), ""), ""
            )  # noop, 为了避免键遗漏
        # 聚合行业市值
        sector_mv: dict[str, float] = {}
        for sym, v in mv.items():
            sec = merged_sectors.get(sym, affected_sector if sym in mv else "")
            if not sec:
                continue
            sector_mv[sec] = sector_mv.get(sec, 0.0) + abs(v)
        for sec, v in sector_mv.items():
            pct = v / equity
            result.projected_sector[sec] = round(pct, 6)
            if pct > self.sector_cap:
                result.reasons.append(
                    f"[{rule}] {sec} 板块下单后占比 {pct:.2%} > 阈值 {self.sector_cap:.2%}"
                )
        result.checked.append(rule)

    def _rule_net_exposure(self, result: EnforcementResult) -> None:
        rule = "NET_EXPOSURE"
        result.checked.append(rule)
        if abs(result.projected_net_exp_pct) > self.net_cap:
            result.reasons.append(
                f"[{rule}] 净敞口 |{result.projected_net_exp_pct:.2%}| > 阈值 {self.net_cap:.2%}"
            )

    def _rule_gross_leverage(self, result: EnforcementResult) -> None:
        rule = "GROSS_LEVERAGE"
        result.checked.append(rule)
        if result.projected_gross_lv > self.gross_cap:
            result.reasons.append(
                f"[{rule}] 总杠杆 {result.projected_gross_lv:.2%} > 阈值 {self.gross_cap:.2%}"
            )
