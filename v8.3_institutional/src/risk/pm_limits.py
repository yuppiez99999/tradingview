# -*- coding: utf-8 -*-
"""
v7.6 PM Limits Matrix — 对标世界顶级对冲基金的持仓限额矩阵

三级限额体系 (Renaissance/Citadel/Bridgewater 标准):
  L1 - 单标的限额: 单一标的仓位上限 (总资产的 %)
  L2 - 板块/因子限额: 板块集中度上限 + 因子暴露上限
  L3 - 组合总限额: 总杠杆/Gross/Net Exposure + 流动性约束

核心原则:
  - 硬限制 (Hard Limit): OMS 订单入口拦截, 不可逾越
  - 软限制 (Soft Limit): 触发 CRO 审批 + 通知 PM
  - 预警阈值 (Warning): 提前告警, 给 PM 反应时间
"""

from __future__ import annotations

import logging
import yaml
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum


logger = logging.getLogger("v76.risk.pm_limits")


class LimitLevel(str, Enum):
    """限额等级"""

    HARD = "HARD"  # 硬限制 — OMS 拦截
    SOFT = "SOFT"  # 软限制 — CRO 审批
    WARNING = "WARNING"  # 预警 — 通知 PM


class LimitStatus(str, Enum):
    """限额检查结果"""

    OK = "OK"
    WARNING = "WARNING"
    BREACH_SOFT = "BREACH_SOFT"
    BREACH_HARD = "BREACH_HARD"


@dataclass
class SingleLimit:
    """单标的限额定义"""

    symbol: str
    max_weight: float  # 最大仓位权重 (总资产 %)
    hard_pct: Optional[float] = None  # 硬限制 (默认 max_weight * 1.1)
    soft_pct: Optional[float] = None  # 软限制 (默认 max_weight * 1.0)
    warn_pct: Optional[float] = None  # 预警 (默认 max_weight * 0.85)
    max_notional: Optional[float] = None  # 最大名义金额 (可选)
    enabled: bool = True


@dataclass
class SectorLimit:
    """板块限额定义"""

    sector: str
    max_weight: float  # 板块最大权重
    members: List[str] = field(default_factory=list)
    hard_pct: Optional[float] = None
    soft_pct: Optional[float] = None
    warn_pct: Optional[float] = None
    enabled: bool = True

    # 板块内单标的下限 (防过度集中)
    max_single_in_sector: float = 0.40  # 板块内单一标的不超过板块权重的 40%


@dataclass
class FactorLimit:
    """因子暴露限额"""

    factor_name: str
    max_exposure: float  # 最大净暴露 (std 单位)
    beta_sources: Dict[str, float] = field(default_factory=dict)
    # {symbol: factor_loading}
    enabled: bool = True


@dataclass
class AggregateLimit:
    """组合总限额"""

    max_gross_exposure: float = 2.0  # 最大总杠杆 (多头+空头)/NAV
    max_net_exposure: float = 1.5  # 最大净暴露 (多头-空头)/NAV
    max_long_exposure: float = 1.8  # 最大多头暴露
    max_short_exposure: float = 0.8  # 最大空头暴露
    max_single_day_turnover: float = 0.25  # 最大单日换手率
    min_daily_volume_ratio: float = 0.10  # 低于日均量 10% 不交易
    max_concentration_top5: float = 0.60  # Top5 标的最大权重
    max_concentration_top10: float = 0.80  # Top10 标的最大权重


@dataclass
class LimitCheckResult:
    """单次限额检查结果"""

    status: LimitStatus
    limit_name: str
    current_value: float
    hard_limit: float
    soft_limit: float
    warn_limit: float
    message: str = ""
    offending_positions: List[str] = field(default_factory=list)


class PMLimitsMatrix:
    """
    v7.6 PM 限额矩阵 — 对标顶级对冲基金

    功能:
      - 单标的/Sector/Factor 三级限额定义与检查
      - 硬限制/软限制/预警三级响应
      - 预交易检查 (OMS 入口拦截)
      - 盘后限额报告

    Usage:
        limits = PMLimitsMatrix(total_nav=5_000_000)
        limits.add_single_limit("000001.SZ", max_weight=0.15)
        limits.add_sector_limit("TECH", max_weight=0.45, members=["000001.SZ", ...])
        results = limits.check_all(positions, prices)
    """

    def __init__(
        self,
        total_nav: float = 5_000_000,
        hard_buffer: float = 0.10,
        soft_buffer: float = 0.05,
        warn_buffer: float = 0.05,
        config_path: Optional[str] = None,
    ):
        """
        Args:
            total_nav: 组合总净资产
            hard_buffer: 软限制→硬限制缓冲 (10% 超出)
            soft_buffer: 预警→软限制缓冲 (5% 超出)
            warn_buffer: 硬限制→预警缓冲 (15% 低于硬限制)
        """
        self.total_nav = total_nav
        self.hard_buffer = hard_buffer
        self.soft_buffer = soft_buffer
        self.warn_buffer = warn_buffer

        # 限额存储
        self.single_limits: Dict[str, SingleLimit] = {}
        self.sector_limits: Dict[str, SectorLimit] = {}
        self.factor_limits: Dict[str, FactorLimit] = {}
        self.aggregate_limit = AggregateLimit()

        # 检查历史
        self.check_history: List[Dict] = []

        # 被硬限制拦截的订单
        self.hard_blocked: List[Dict] = []

        if config_path:
            self._load_config(config_path)

    def _load_config(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
        except Exception as e:
            logger.warning(f"加载 PM 限额配置失败: {e}")
            return

        # 解析单标的限额
        for sc in cfg.get("single_limits", []):
            self.add_single_limit(
                symbol=sc["symbol"],
                max_weight=sc.get("max_weight", 0.15),
                hard_pct=sc.get("hard_pct"),
                soft_pct=sc.get("soft_pct"),
                warn_pct=sc.get("warn_pct"),
                max_notional=sc.get("max_notional"),
            )

        # 解析板块限额
        for sc in cfg.get("sector_limits", []):
            self.add_sector_limit(
                sector=sc["sector"],
                max_weight=sc.get("max_weight", 0.40),
                members=sc.get("members", []),
                hard_pct=sc.get("hard_pct"),
                soft_pct=sc.get("soft_pct"),
                warn_pct=sc.get("warn_pct"),
                max_single_in_sector=sc.get("max_single_in_sector", 0.40),
            )

        # 解析因子限额
        for fc in cfg.get("factor_limits", []):
            self.add_factor_limit(
                factor_name=fc["factor_name"],
                max_exposure=fc.get("max_exposure", 1.0),
                beta_sources=fc.get("beta_sources", {}),
            )

        # 解析总限额
        ag = cfg.get("aggregate_limits", {})
        self.aggregate_limit = AggregateLimit(
            max_gross_exposure=ag.get("max_gross_exposure", 2.0),
            max_net_exposure=ag.get("max_net_exposure", 1.5),
            max_long_exposure=ag.get("max_long_exposure", 1.8),
            max_short_exposure=ag.get("max_short_exposure", 0.8),
            max_single_day_turnover=ag.get("max_single_day_turnover", 0.25),
            min_daily_volume_ratio=ag.get("min_daily_volume_ratio", 0.10),
        )

        logger.info(
            f"PM 限额矩阵加载: {len(self.single_limits)} 单标的, "
            f"{len(self.sector_limits)} 板块, {len(self.factor_limits)} 因子"
        )

    # ---------- 限额定义 ----------
    def add_single_limit(
        self,
        symbol: str,
        max_weight: float,
        hard_pct: Optional[float] = None,
        soft_pct: Optional[float] = None,
        warn_pct: Optional[float] = None,
        max_notional: Optional[float] = None,
    ) -> None:
        """添加单标的限额"""
        self.single_limits[symbol] = SingleLimit(
            symbol=symbol,
            max_weight=max_weight,
            hard_pct=hard_pct,
            soft_pct=soft_pct,
            warn_pct=warn_pct,
            max_notional=max_notional,
        )

    def add_sector_limit(
        self,
        sector: str,
        max_weight: float,
        members: List[str],
        hard_pct: Optional[float] = None,
        soft_pct: Optional[float] = None,
        warn_pct: Optional[float] = None,
        max_single_in_sector: float = 0.40,
    ) -> None:
        """添加板块限额"""
        self.sector_limits[sector] = SectorLimit(
            sector=sector,
            max_weight=max_weight,
            members=members,
            hard_pct=hard_pct,
            soft_pct=soft_pct,
            warn_pct=warn_pct,
            max_single_in_sector=max_single_in_sector,
        )

    def add_factor_limit(
        self, factor_name: str, max_exposure: float, beta_sources: Optional[Dict[str, float]] = None
    ) -> None:
        """添加因子暴露限额"""
        self.factor_limits[factor_name] = FactorLimit(
            factor_name=factor_name,
            max_exposure=max_exposure,
            beta_sources=beta_sources or {},
        )

    # ---------- 限额检查 ----------
    def _resolve_thresholds(
        self, base_pct: float, hard_pct: Optional[float], soft_pct: Optional[float], warn_pct: Optional[float]
    ) -> Tuple[float, float, float]:
        """解析三级阈值"""
        hard = hard_pct if hard_pct is not None else base_pct * (1 + self.hard_buffer)
        soft = soft_pct if soft_pct is not None else base_pct * (1 + self.soft_buffer)
        warn = warn_pct if warn_pct is not None else base_pct * (1 - self.warn_buffer)
        return hard, soft, warn

    def _classify(self, current: float, hard: float, soft: float, warn: float) -> LimitStatus:
        """根据三级阈值分类状态"""
        if current >= hard:
            return LimitStatus.BREACH_HARD
        elif current >= soft:
            return LimitStatus.BREACH_SOFT
        elif current >= warn:
            return LimitStatus.WARNING
        return LimitStatus.OK

    def check_single_limits(self, positions: Dict[str, float]) -> List[LimitCheckResult]:
        """检查所有单标的限额"""
        results = []
        total_nav = self.total_nav

        for symbol, qty in positions.items():
            limit = self.single_limits.get(symbol)
            if limit is None or not limit.enabled:
                continue

            # 计算权重 (需要价格, 暂用 nominal 替代)
            # positions 格式: {symbol: weight or notional}
            weight = abs(qty) / total_nav if total_nav > 0 else 0

            hard, soft, warn = self._resolve_thresholds(
                limit.max_weight, limit.hard_pct, limit.soft_pct, limit.warn_pct
            )
            status = self._classify(weight, hard, soft, warn)

            msg = ""
            if status == LimitStatus.BREACH_HARD:
                msg = f"{symbol} 权重 {weight:.2%} 超过硬限制 {hard:.2%}, 必须立即减仓"
            elif status == LimitStatus.BREACH_SOFT:
                msg = f"{symbol} 权重 {weight:.2%} 超过软限制 {soft:.2%}, 需 CRO 审批"
            elif status == LimitStatus.WARNING:
                msg = f"{symbol} 权重 {weight:.2%} 接近限额 {hard:.2%}, 预警通知"

            results.append(
                LimitCheckResult(
                    status=status,
                    limit_name=f"SINGLE_{symbol}",
                    current_value=round(weight, 6),
                    hard_limit=round(hard, 6),
                    soft_limit=round(soft, 6),
                    warn_limit=round(warn, 6),
                    message=msg,
                    offending_positions=[symbol] if status != LimitStatus.OK else [],
                )
            )

        return results

    def check_sector_limits(self, positions: Dict[str, float]) -> List[LimitCheckResult]:
        """检查所有板块限额"""
        results = []
        total_nav = self.total_nav

        for sector_name, sl in self.sector_limits.items():
            if not sl.enabled or not sl.members:
                continue

            # 计算板块权重
            sector_weight = 0.0
            for symbol in sl.members:
                if symbol in positions:
                    sector_weight += abs(positions[symbol]) / total_nav

            hard, soft, warn = self._resolve_thresholds(sl.max_weight, sl.hard_pct, sl.soft_pct, sl.warn_pct)
            status = self._classify(sector_weight, hard, soft, warn)

            msg = ""
            if status == LimitStatus.BREACH_HARD:
                msg = f"板块 {sector_name} 权重 {sector_weight:.2%} 超过硬限制 {hard:.2%}"
            elif status == LimitStatus.BREACH_SOFT:
                msg = f"板块 {sector_name} 权重 {sector_weight:.2%} 超过软限制 {soft:.2%}"
            elif status == LimitStatus.WARNING:
                msg = f"板块 {sector_name} 权重 {sector_weight:.2%} 接近限额 {hard:.2%}"

            results.append(
                LimitCheckResult(
                    status=status,
                    limit_name=f"SECTOR_{sector_name}",
                    current_value=round(sector_weight, 6),
                    hard_limit=round(hard, 6),
                    soft_limit=round(soft, 6),
                    warn_limit=round(warn, 6),
                    message=msg,
                    offending_positions=[s for s in sl.members if s in positions],
                )
            )

            # 板块内单标的集中度检查
            if sector_weight > 0:
                for symbol in sl.members:
                    if symbol in positions:
                        single_in_sector = abs(positions[symbol]) / total_nav
                        single_in_sector_pct = single_in_sector / max(sector_weight, 1e-8)
                        if single_in_sector_pct > sl.max_single_in_sector:
                            results.append(
                                LimitCheckResult(
                                    status=LimitStatus.BREACH_SOFT,
                                    limit_name=f"SECTOR_{sector_name}_SINGLE_{symbol}",
                                    current_value=round(single_in_sector_pct, 6),
                                    hard_limit=round(sl.max_single_in_sector * 1.1, 6),
                                    soft_limit=round(sl.max_single_in_sector, 6),
                                    warn_limit=round(sl.max_single_in_sector * 0.85, 6),
                                    message=f"{symbol} 占板块 {sector_name} {single_in_sector_pct:.1%} "
                                    f"超过上限 {sl.max_single_in_sector:.0%}",
                                    offending_positions=[symbol],
                                )
                            )

        return results

    def check_factor_limits(
        self, positions: Dict[str, float], factor_loadings: Optional[Dict[str, Dict[str, float]]] = None
    ) -> List[LimitCheckResult]:
        """检查因子暴露限额"""
        results = []
        total_nav = self.total_nav

        for factor_name, fl in self.factor_limits.items():
            if not fl.enabled:
                continue

            # 计算因子净暴露
            factor_exposure = 0.0
            for symbol, weight in positions.items():
                w = abs(weight) / total_nav if total_nav > 0 else 0
                loading = fl.beta_sources.get(symbol, 0.0)
                # 如果提供了外部 loadings, 优先使用
                if factor_loadings and symbol in factor_loadings:
                    loading = factor_loadings[symbol].get(factor_name, loading)
                factor_exposure += w * loading

            hard = fl.max_exposure * 1.1
            soft = fl.max_exposure * 1.0
            warn = fl.max_exposure * 0.85
            status = self._classify(abs(factor_exposure), hard, soft, warn)

            msg = ""
            if status != LimitStatus.OK:
                msg = f"因子 {factor_name} 暴露 {factor_exposure:.2f}σ 超过限额"

            results.append(
                LimitCheckResult(
                    status=status,
                    limit_name=f"FACTOR_{factor_name}",
                    current_value=round(factor_exposure, 4),
                    hard_limit=round(hard, 4),
                    soft_limit=round(soft, 4),
                    warn_limit=round(warn, 4),
                    message=msg,
                )
            )

        return results

    def check_aggregate_limits(
        self, long_exposure: float, short_exposure: float, daily_turnover: float = 0.0
    ) -> List[LimitCheckResult]:
        """检查组合总限额"""
        results = []
        al = self.aggregate_limit

        gross = long_exposure + abs(short_exposure)
        net = long_exposure - abs(short_exposure)

        # Gross exposure
        hard_g = al.max_gross_exposure * 1.05
        soft_g = al.max_gross_exposure * 1.00
        warn_g = al.max_gross_exposure * 0.90
        status_g = self._classify(gross, hard_g, soft_g, warn_g)
        results.append(
            LimitCheckResult(
                status=status_g,
                limit_name="AGGREGATE_GROSS",
                current_value=round(gross, 4),
                hard_limit=round(hard_g, 4),
                soft_limit=round(soft_g, 4),
                warn_limit=round(warn_g, 4),
                message="" if status_g == LimitStatus.OK else f"总杠杆 {gross:.2f}x",
            )
        )

        # Net exposure
        hard_n = al.max_net_exposure * 1.05
        soft_n = al.max_net_exposure * 1.00
        warn_n = al.max_net_exposure * 0.90
        status_n = self._classify(abs(net), hard_n, soft_n, warn_n)
        results.append(
            LimitCheckResult(
                status=status_n,
                limit_name="AGGREGATE_NET",
                current_value=round(net, 4),
                hard_limit=round(hard_n, 4),
                soft_limit=round(soft_n, 4),
                warn_limit=round(warn_n, 4),
                message="" if status_n == LimitStatus.OK else f"净暴露 {net:.2f}x",
            )
        )

        # Long exposure
        hard_l = al.max_long_exposure * 1.05
        soft_l = al.max_long_exposure
        warn_l = al.max_long_exposure * 0.90
        status_l = self._classify(long_exposure, hard_l, soft_l, warn_l)
        results.append(
            LimitCheckResult(
                status=status_l,
                limit_name="AGGREGATE_LONG",
                current_value=round(long_exposure, 4),
                hard_limit=round(hard_l, 4),
                soft_limit=round(soft_l, 4),
                warn_limit=round(warn_l, 4),
                message="" if status_l == LimitStatus.OK else f"多头暴露 {long_exposure:.2f}x",
            )
        )

        # Turnover
        if daily_turnover > al.max_single_day_turnover:
            results.append(
                LimitCheckResult(
                    status=LimitStatus.BREACH_SOFT,
                    limit_name="AGGREGATE_TURNOVER",
                    current_value=round(daily_turnover, 4),
                    hard_limit=round(al.max_single_day_turnover * 1.2, 4),
                    soft_limit=round(al.max_single_day_turnover, 4),
                    warn_limit=round(al.max_single_day_turnover * 0.80, 4),
                    message=f"日换手率 {daily_turnover:.1%} 超过限额",
                )
            )

        return results

    def check_all(
        self,
        positions: Dict[str, float],
        prices: Optional[Dict[str, float]] = None,
        long_exposure: Optional[float] = None,
        short_exposure: Optional[float] = None,
        daily_turnover: float = 0.0,
        factor_loadings: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> Dict:
        """执行全量限额检查

        Args:
            positions: {symbol: weight_or_notional}
            prices: {symbol: price} (可选, 用于 notionals->weights 转换)
            long_exposure: 多头总暴露 (相对于 NAV 的倍数)
            short_exposure: 空头总暴露
            daily_turnover: 当日换手率
            factor_loadings: 因子载荷 {symbol: {factor: loading}}

        Returns:
            {
                overall_status: 'PASS' | 'WARNING' | 'SOFT_BREACH' | 'HARD_BREACH',
                single_checks: [...],
                sector_checks: [...],
                factor_checks: [...],
                aggregate_checks: [...],
                summary: str,
                blocked_orders: [...],
            }
        """
        # 如果给了价格, 转换 notional -> weight
        if prices:
            weighted_positions = {}
            for sym, val in positions.items():
                price = prices.get(sym, 1.0)
                if price > 0:
                    weighted_positions[sym] = val * price
            sum(abs(v) for v in weighted_positions.values()) + 1e-8
            positions_pct = {k: v / self.total_nav for k, v in weighted_positions.items()}
        else:
            positions_pct = positions
            sum(abs(v) for v in positions.values()) + 1e-8

        # 计算暴露 (如果未提供)
        if long_exposure is None:
            long_exposure = sum(v for v in positions_pct.values() if v > 0)
        if short_exposure is None:
            short_exposure = abs(sum(v for v in positions_pct.values() if v < 0))

        # 执行检查
        single_results = self.check_single_limits(positions_pct)
        sector_results = self.check_sector_limits(positions_pct)
        factor_results = self.check_factor_limits(positions_pct, factor_loadings)
        agg_results = self.check_aggregate_limits(long_exposure, short_exposure, daily_turnover)

        all_results = single_results + sector_results + factor_results + agg_results

        # 判定总体状态
        has_hard = any(r.status == LimitStatus.BREACH_HARD for r in all_results)
        has_soft = any(r.status == LimitStatus.BREACH_SOFT for r in all_results)
        has_warn = any(r.status == LimitStatus.WARNING for r in all_results)

        if has_hard:
            overall = "HARD_BREACH"
        elif has_soft:
            overall = "SOFT_BREACH"
        elif has_warn:
            overall = "WARNING"
        else:
            overall = "PASS"

        # 收集违规详情
        violations = [r for r in all_results if r.status != LimitStatus.OK]
        summary_lines = [f"PM 限额检查: {overall}"]
        for v in violations:
            summary_lines.append(f"  [{v.status.value}] {v.limit_name}: {v.message}")
        summary = "\n".join(summary_lines)

        result = {
            "timestamp": __import__("datetime").datetime.now().isoformat(),
            "total_nav": self.total_nav,
            "overall_status": overall,
            "n_checks": len(all_results),
            "n_violations": len(violations),
            "n_hard": sum(1 for r in all_results if r.status == LimitStatus.BREACH_HARD),
            "n_soft": sum(1 for r in all_results if r.status == LimitStatus.BREACH_SOFT),
            "n_warn": sum(1 for r in all_results if r.status == LimitStatus.WARNING),
            "single_checks": single_results,
            "sector_checks": sector_results,
            "factor_checks": factor_results,
            "aggregate_checks": agg_results,
            "violations": violations,
            "summary": summary,
        }

        self.check_history.append(
            {
                "ts": result["timestamp"],
                "overall": overall,
                "n_violations": len(violations),
            }
        )

        # 硬限制 → 拦截订单列表
        if has_hard:
            blocked = [
                {
                    "symbol": r.offending_positions if r.offending_positions else [r.limit_name],
                    "reason": r.message,
                    "limit": r.limit_name,
                }
                for r in all_results
                if r.status == LimitStatus.BREACH_HARD
            ]
            result["blocked_orders"] = blocked
            self.hard_blocked.extend(blocked)

        return result

    # ---------- 预交易检查 (OMS 入口) ----------
    def pre_trade_check(
        self, symbol: str, order_qty: float, side: str = "BUY", current_positions: Optional[Dict[str, float]] = None
    ) -> LimitCheckResult:
        """订单执行前的限额检查

        在 OMS 入口调用, 若返回 BREACH_HARD 则拒绝订单

        Args:
            symbol: 标的代码
            order_qty: 订单数量 (notional)
            side: 'BUY' / 'SELL'
            current_positions: 当前持仓 {symbol: notional}

        Returns:
            LimitCheckResult
        """
        current = current_positions or {}
        new_notional = current.get(symbol, 0) + order_qty if side == "BUY" else current.get(symbol, 0) - order_qty
        new_weight = abs(new_notional) / max(self.total_nav, 1)

        limit = self.single_limits.get(symbol)
        if limit is None:
            return LimitCheckResult(
                status=LimitStatus.OK, limit_name=symbol, current_value=0, hard_limit=0, soft_limit=0, warn_limit=0
            )

        if not limit.enabled:
            return LimitCheckResult(
                status=LimitStatus.OK, limit_name=symbol, current_value=0, hard_limit=0, soft_limit=0, warn_limit=0
            )

        hard, soft, warn = self._resolve_thresholds(limit.max_weight, limit.hard_pct, limit.soft_pct, limit.warn_pct)
        status = self._classify(new_weight, hard, soft, warn)

        msg = ""
        if status == LimitStatus.BREACH_HARD:
            msg = f"预交易拦截: {symbol} 买入后权重 {new_weight:.2%} 超过硬限制 {hard:.2%}"

        result = LimitCheckResult(
            status=status,
            limit_name=f"PRETRADE_{symbol}",
            current_value=round(new_weight, 6),
            hard_limit=round(hard, 6),
            soft_limit=round(soft, 6),
            warn_limit=round(warn, 6),
            message=msg,
        )

        if status == LimitStatus.BREACH_HARD:
            self.hard_blocked.append(
                {
                    "ts": __import__("datetime").datetime.now().isoformat(),
                    "symbol": symbol,
                    "qty": order_qty,
                    "side": side,
                    "reason": msg,
                }
            )
            logger.warning(f"[PM LIMITS HARD BLOCK] {msg}")

        return result

    # ---------- 报告 ----------
    def report(self) -> dict:
        """生成限额报告"""
        return {
            "total_nav": self.total_nav,
            "n_single_limits": len(self.single_limits),
            "n_sector_limits": len(self.sector_limits),
            "n_factor_limits": len(self.factor_limits),
            "aggregate_limit": {
                "max_gross": self.aggregate_limit.max_gross_exposure,
                "max_net": self.aggregate_limit.max_net_exposure,
                "max_long": self.aggregate_limit.max_long_exposure,
                "max_short": self.aggregate_limit.max_short_exposure,
            },
            "recent_checks": self.check_history[-10:],
            "total_hard_blocks": len(self.hard_blocked),
        }


# ============================================================
# 预设 A股 社保基金风格限额
# ============================================================
DEFAULT_A_SHARE_LIMITS = {
    "single": [
        # 高端制造 (含算力) — 40% 板块
        {"symbol": "300308.SZ", "max_weight": 0.12, "desc": "中际旭创-算力光模块"},
        {"symbol": "688041.SH", "max_weight": 0.10, "desc": "海光信息-国产CPU"},
        {"symbol": "002371.SZ", "max_weight": 0.08, "desc": "北方华创-半导体设备"},
        {"symbol": "688981.SH", "max_weight": 0.08, "desc": "中芯国际-晶圆代工"},
        {"symbol": "300750.SZ", "max_weight": 0.10, "desc": "宁德时代-动力电池"},
        {"symbol": "000425.SZ", "max_weight": 0.06, "desc": "徐工机械-工程机械"},
        # 顺周期 — 20% 板块
        {"symbol": "601088.SH", "max_weight": 0.08, "desc": "中国神华-煤炭"},
        {"symbol": "600219.SH", "max_weight": 0.05, "desc": "南山铝业-有色"},
        {"symbol": "600019.SH", "max_weight": 0.05, "desc": "宝钢股份-钢铁"},
        # 资源 — 20% 板块
        {"symbol": "518880.SH", "max_weight": 0.15, "desc": "华安黄金ETF"},
        {"symbol": "000792.SZ", "max_weight": 0.06, "desc": "盐湖股份-锂资源"},
        # 防御 — 20% 板块
        {"symbol": "600900.SH", "max_weight": 0.10, "desc": "长江电力-水电"},
        {"symbol": "600276.SH", "max_weight": 0.08, "desc": "恒瑞医药-创新药"},
        {"symbol": "603259.SH", "max_weight": 0.06, "desc": "药明康德-CRO"},
        {"symbol": "002422.SZ", "max_weight": 0.05, "desc": "科伦药业-大输液"},
    ],
    "sector": [
        {
            "sector": "高端制造(算力)",
            "max_weight": 0.40,
            "members": ["300308.SZ", "688041.SH", "002371.SZ", "688981.SH", "300750.SZ", "000425.SZ"],
        },
        {
            "sector": "顺周期",
            "max_weight": 0.20,
            "members": ["601088.SH", "600219.SH", "600019.SH"],
        },
        {
            "sector": "资源",
            "max_weight": 0.20,
            "members": ["518880.SH", "000792.SZ"],
        },
        {
            "sector": "防御",
            "max_weight": 0.20,
            "members": ["600900.SH", "600276.SH", "603259.SH", "002422.SZ"],
        },
    ],
}


def create_default_limits(total_nav: float = 5_000_000) -> PMLimitsMatrix:
    """创建默认的 A股社保基金风格限额矩阵"""
    matrix = PMLimitsMatrix(total_nav=total_nav)

    for sl in DEFAULT_A_SHARE_LIMITS["single"]:
        matrix.add_single_limit(symbol=sl["symbol"], max_weight=sl["max_weight"])

    for sec in DEFAULT_A_SHARE_LIMITS["sector"]:
        matrix.add_sector_limit(
            sector=sec["sector"],
            max_weight=sec["max_weight"],
            members=sec["members"],
        )

    return matrix
