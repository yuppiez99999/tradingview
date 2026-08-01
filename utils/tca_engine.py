"""
交易后成本分析引擎 (Post-Trade TCA Engine)

世界顶级对冲基金执行质量评估标准 (Citadel / Point72 / Renaissance):
- 决策价 (Decision Price) — 下单时刻的中价
- 到达价 (Arrival Price) — 订单到达市场的中价
- 执行价 (Execution Price) — 实际成交加权均价
- VWAP 偏离 — 执行价 vs 市场成交量加权均价
- 实施差额 (Implementation Shortfall, IS) — 决策价到最终成交的总成本
- 市场冲击 (Market Impact) — 订单本身造成的价格变动
- 时机成本 (Timing Cost) — 决策延迟产生的机会成本
- 择时能力 (Timing Skill) — 是否在有利时点执行

参考:
- Perold (1988) "Implementation Shortfall"
- Kissell (2013) "The Science of Algorithmic Trading"
- Bloomberg TOMS / ITG TCA 标准
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# ============================================================
# 数据结构
# ============================================================


@dataclass
class FillRecord:
    """单笔成交记录"""

    symbol: str
    side: str  # "BUY" / "SELL"
    shares: int
    price: float  # 成交价
    timestamp: str  # ISO8601
    broker: str = ""
    venue: str = ""  # 交易所/暗池
    order_id: str = ""


@dataclass
class BenchmarkPrices:
    """基准价数据"""

    decision_price: float  # 决策价 (下单时刻中价)
    arrival_price: float  # 到达价 (订单到达时价)
    vwap: float = 0.0  # 区间 VWAP
    close_price: float = 0.0  # 当日收盘价
    open_price: float = 0.0  # 当日开盘价
    twap: float = 0.0  # 区间 TWAP


@dataclass
class TCAReport:
    """TCA 单标的报告"""

    symbol: str
    side: str
    total_shares: int
    avg_exec_price: float
    vwap: float

    # 核心指标 (bps, 正数=成本, 负数=收益)
    is_cost_bps: float  # Implementation Shortfall (vs 决策价)
    arrival_cost_bps: float  # 到达价偏离
    vwap_deviation_bps: float  # VWAP 偏离
    close_deviation_bps: float  # 收盘价偏离

    # 分解
    market_impact_bps: float  # 市场冲击
    timing_cost_bps: float  # 时机成本
    opportunity_cost_bps: float  # 机会成本 (未成交部分)
    slippage_bps: float  # 滑点

    # 执行质量
    fill_rate: float  # 成交率 (成交/委托)
    participation_rate: float  # 参与率 (成交/区间成交量)
    timing_skill_score: float  # 择时能力评分 [-1, 1]

    # 成本细项
    commission: float
    fees: float
    total_cost: float  # 总成本 (含手续费)

    # 评级
    quality_grade: str  # A+ / A / B / C / D
    issues: list[str] = field(default_factory=list)


# ============================================================
# TCA 引擎
# ============================================================


class TCAManager:
    """交易后成本分析引擎

    用法:
        tca = TCAManager()
        report = tca.analyze(
            fills=fills_list,
            benchmark=BenchmarkPrices(
                decision_price=10.00,
                arrival_price=10.02,
                vwap=10.05,
                close_price=10.10,
            ),
            order_shares=10000,
            interval_volume=500000,
        )
    """

    # 评级阈值 (bps, 相对于决策价)
    GRADE_THRESHOLDS = {
        "A+": 2.0,  # ≤ 2 bps
        "A": 5.0,  # ≤ 5 bps
        "B": 10.0,  # ≤ 10 bps
        "C": 20.0,  # ≤ 20 bps
        "D": 50.0,  # ≤ 50 bps
        # > 50 bps = F
    }

    def __init__(
        self,
        commission_rate: float = 0.0003,  # 万三
        min_commission: float = 5.0,
        fee_rate: float = 0.000067,  # 过户费等
        stamp_duty_rate: float = 0.0005,  # 印花税 (卖出)
    ):
        self.commission_rate = float(commission_rate)
        self.min_commission = float(min_commission)
        self.fee_rate = float(fee_rate)
        self.stamp_duty_rate = float(stamp_duty_rate)

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------

    def analyze(
        self,
        fills: list[FillRecord],
        benchmark: BenchmarkPrices,
        order_shares: int | None = None,
        interval_volume: int | None = None,
    ) -> TCAReport:
        """分析单标的的执行质量

        Args:
            fills: 该标的的所有成交记录
            benchmark: 基准价数据
            order_shares: 委托数量 (计算 fill_rate, None=用成交总和)
            interval_volume: 执行区间市场成交量 (计算参与率)

        Returns:
            TCAReport 执行成本分析报告
        """
        if not fills:
            raise ValueError("fills 不能为空")

        symbol = fills[0].symbol
        side = fills[0].side.upper()

        # 1. 加权平均执行价
        total_shares = sum(f.shares for f in fills)
        if total_shares <= 0:
            raise ValueError(f"总成交量为 0: {symbol}")

        avg_exec_price = sum(f.shares * f.price for f in fills) / total_shares

        # 2. 核心指标 (bps)
        # IS = (执行价 - 决策价) / 决策价 × 10000 (买入)
        #    = (决策价 - 执行价) / 决策价 × 10000 (卖出)
        is_signed_return = self._signed_return(avg_exec_price, benchmark.decision_price, side)
        is_cost_bps = is_signed_return * 10000

        arrival_signed = self._signed_return(avg_exec_price, benchmark.arrival_price, side)
        arrival_cost_bps = arrival_signed * 10000

        # VWAP 偏离
        if benchmark.vwap > 0:
            vwap_signed = self._signed_return(avg_exec_price, benchmark.vwap, side)
            vwap_deviation_bps = vwap_signed * 10000
        else:
            vwap_deviation_bps = 0.0

        # 收盘价偏离
        if benchmark.close_price > 0:
            close_signed = self._signed_return(avg_exec_price, benchmark.close_price, side)
            close_deviation_bps = close_signed * 10000
        else:
            close_deviation_bps = 0.0

        # 3. 成本分解
        # 市场冲击 = 到达价 - 决策价 (订单造成的价格变动)
        impact_signed = self._signed_return(benchmark.arrival_price, benchmark.decision_price, side)
        market_impact_bps = impact_signed * 10000

        # 时机成本 = 执行价 - 到达价 (执行延迟)
        timing_signed = self._signed_return(avg_exec_price, benchmark.arrival_price, side)
        timing_cost_bps = timing_signed * 10000

        # 滑点 = 执行价 - VWAP (相对市场基准的滑点)
        slippage_bps = vwap_deviation_bps

        # 机会成本 (未成交部分)
        if order_shares is not None and order_shares > total_shares:
            unfilled = order_shares - total_shares
            # 假设未成交部分按收盘价计算损失
            if benchmark.close_price > 0:
                opp_signed = self._signed_return(benchmark.close_price, benchmark.decision_price, side)
                opportunity_cost_bps = opp_signed * 10000 * (unfilled / order_shares)
            else:
                opportunity_cost_bps = 0.0
        else:
            opportunity_cost_bps = 0.0

        # 4. 执行质量
        fill_rate = total_shares / order_shares if order_shares and order_shares > 0 else 1.0
        participation_rate = total_shares / interval_volume if interval_volume and interval_volume > 0 else 0.0

        # 择时能力评分 [-1, 1]
        # 正分 = 在低价买入/高价卖出 (优于决策价)
        # 负分 = 在高价买入/低价卖出 (差于决策价)
        if is_cost_bps != 0:
            timing_skill_score = -is_cost_bps / 50.0  # 50bps = 满分
            timing_skill_score = max(-1.0, min(1.0, timing_skill_score))
        else:
            timing_skill_score = 0.0

        # 5. 成本细项
        notional = total_shares * avg_exec_price
        commission = max(notional * self.commission_rate, self.min_commission)
        fees = notional * self.fee_rate
        if side == "SELL":
            fees += notional * self.stamp_duty_rate
        total_cost = commission + fees + abs(is_cost_bps / 10000) * notional

        # 6. 评级
        quality_grade = self._grade(is_cost_bps)

        # 7. 问题诊断
        issues = self._diagnose(
            is_cost_bps=is_cost_bps,
            vwap_deviation_bps=vwap_deviation_bps,
            market_impact_bps=market_impact_bps,
            timing_cost_bps=timing_cost_bps,
            fill_rate=fill_rate,
            participation_rate=participation_rate,
        )

        return TCAReport(
            symbol=symbol,
            side=side,
            total_shares=total_shares,
            avg_exec_price=avg_exec_price,
            vwap=benchmark.vwap,
            is_cost_bps=is_cost_bps,
            arrival_cost_bps=arrival_cost_bps,
            vwap_deviation_bps=vwap_deviation_bps,
            close_deviation_bps=close_deviation_bps,
            market_impact_bps=market_impact_bps,
            timing_cost_bps=timing_cost_bps,
            opportunity_cost_bps=opportunity_cost_bps,
            slippage_bps=slippage_bps,
            fill_rate=fill_rate,
            participation_rate=participation_rate,
            timing_skill_score=timing_skill_score,
            commission=commission,
            fees=fees,
            total_cost=total_cost,
            quality_grade=quality_grade,
            issues=issues,
        )

    # ------------------------------------------------------------
    # 批量分析
    # ------------------------------------------------------------

    def analyze_batch(
        self,
        fills_by_symbol: dict[str, list[FillRecord]],
        benchmarks: dict[str, BenchmarkPrices],
        orders: dict[str, int] | None = None,
        volumes: dict[str, int] | None = None,
    ) -> dict[str, TCAReport]:
        """批量分析多个标的"""
        reports: dict[str, TCAReport] = {}
        for symbol, fills in fills_by_symbol.items():
            if not fills:
                continue
            if symbol not in benchmarks:
                continue
            try:
                order_shares = (orders or {}).get(symbol)
                interval_volume = (volumes or {}).get(symbol)
                report = self.analyze(fills, benchmarks[symbol], order_shares, interval_volume)
                reports[symbol] = report
            except Exception:  # P2 模块 fail-safe, 待后续精确化
                # 单标失败不影响其他
                continue
        return reports

    # ------------------------------------------------------------
    # 组合汇总
    # ------------------------------------------------------------

    def summarize(self, reports: dict[str, TCAReport]) -> dict:
        """组合级 TCA 汇总"""
        if not reports:
            return {"total_notional": 0, "total_cost": 0, "avg_cost_bps": 0}

        total_notional = sum(r.total_shares * r.avg_exec_price for r in reports.values())
        total_cost = sum(r.total_cost for r in reports.values())
        avg_is_bps = (
            (sum(r.is_cost_bps * r.total_shares * r.avg_exec_price for r in reports.values()) / total_notional)
            if total_notional > 0
            else 0
        )
        avg_vwap_bps = (
            (sum(r.vwap_deviation_bps * r.total_shares * r.avg_exec_price for r in reports.values()) / total_notional)
            if total_notional > 0
            else 0
        )
        avg_impact_bps = (
            (sum(r.market_impact_bps * r.total_shares * r.avg_exec_price for r in reports.values()) / total_notional)
            if total_notional > 0
            else 0
        )
        avg_timing_bps = (
            (sum(r.timing_cost_bps * r.total_shares * r.avg_exec_price for r in reports.values()) / total_notional)
            if total_notional > 0
            else 0
        )
        avg_fill_rate = sum(r.fill_rate for r in reports.values()) / len(reports)

        grade_dist: dict[str, int] = {}
        for r in reports.values():
            grade_dist[r.quality_grade] = grade_dist.get(r.quality_grade, 0) + 1

        worst_symbol = max(reports.values(), key=lambda r: r.is_cost_bps).symbol if reports else ""
        best_symbol = min(reports.values(), key=lambda r: r.is_cost_bps).symbol if reports else ""

        return {
            "n_orders": len(reports),
            "total_notional": total_notional,
            "total_cost": total_cost,
            "avg_is_cost_bps": avg_is_bps,
            "avg_vwap_deviation_bps": avg_vwap_bps,
            "avg_market_impact_bps": avg_impact_bps,
            "avg_timing_cost_bps": avg_timing_bps,
            "avg_fill_rate": avg_fill_rate,
            "grade_distribution": grade_dist,
            "worst_symbol": worst_symbol,
            "best_symbol": best_symbol,
        }

    # ------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------

    def _signed_return(self, exec_price: float, ref_price: float, side: str) -> float:
        """计算带方向的收益率

        买入: 正数=成本 (执行价高于基准)
        卖出: 正数=成本 (执行价低于基准)
        """
        if ref_price <= 0:
            return 0.0
        raw = (exec_price - ref_price) / ref_price
        # 卖出时, 执行价低于基准 = 成本 (正)
        if side.upper() == "SELL":
            return -raw
        return raw

    def _grade(self, is_cost_bps: float) -> str:
        """根据 IS 成本评级"""
        cost = abs(is_cost_bps)
        for grade, threshold in self.GRADE_THRESHOLDS.items():
            if cost <= threshold:
                return grade
        return "F"

    def _diagnose(
        self,
        is_cost_bps: float,
        vwap_deviation_bps: float,
        market_impact_bps: float,
        timing_cost_bps: float,
        fill_rate: float,
        participation_rate: float,
    ) -> list[str]:
        """诊断执行问题"""
        issues: list[str] = []
        if abs(is_cost_bps) > 20:
            issues.append(f"IS 成本过高: {is_cost_bps:.1f}bps (>20)")
        if abs(vwap_deviation_bps) > 10:
            issues.append(f"VWAP 偏离大: {vwap_deviation_bps:.1f}bps (>10)")
        if abs(market_impact_bps) > 15:
            issues.append(f"市场冲击显著: {market_impact_bps:.1f}bps (>15)")
        if abs(timing_cost_bps) > 10:
            issues.append(f"时机成本高: {timing_cost_bps:.1f}bps (>10)")
        if fill_rate < 0.95:
            issues.append(f"成交率低: {fill_rate:.1%} (<95%)")
        if participation_rate > 0.20:
            issues.append(f"参与率过高: {participation_rate:.1%} (>20%)")
        if participation_rate < 0.01:
            issues.append(f"参与率过低: {participation_rate:.2%} (<1%)")
        return issues

    # ------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------

    def save_report(self, report: TCAReport, path: str | Path) -> Path:
        """保存 TCA 报告到 JSON"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "symbol": report.symbol,
            "side": report.side,
            "total_shares": report.total_shares,
            "avg_exec_price": report.avg_exec_price,
            "vwap": report.vwap,
            "is_cost_bps": report.is_cost_bps,
            "arrival_cost_bps": report.arrival_cost_bps,
            "vwap_deviation_bps": report.vwap_deviation_bps,
            "close_deviation_bps": report.close_deviation_bps,
            "market_impact_bps": report.market_impact_bps,
            "timing_cost_bps": report.timing_cost_bps,
            "opportunity_cost_bps": report.opportunity_cost_bps,
            "slippage_bps": report.slippage_bps,
            "fill_rate": report.fill_rate,
            "participation_rate": report.participation_rate,
            "timing_skill_score": report.timing_skill_score,
            "commission": report.commission,
            "fees": report.fees,
            "total_cost": report.total_cost,
            "quality_grade": report.quality_grade,
            "issues": report.issues,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    # ============================================================
    # T3.4: 执行前预估接口 (facade → PreTradeEstimator)
    # ============================================================
    def estimate(
        self,
        order: dict,
        market_data: dict | None = None,
        cost_threshold_bps: float = 30.0,
    ):
        """执行前成本预估 (T3.4)

        facade 方法: 委托给 PreTradeEstimator 实现.
        满足任务文档要求的 `tca_engine.estimate(order)` 接口.

        Args:
            order: 订单字典 (symbol/side/shares/price/notional/market_cap?)
            market_data: 市场数据 (adv/volatility)
            cost_threshold_bps: 否决阈值 (bps), 默认 30

        Returns:
            PreTradeEstimate 预估结果

        用法:
            from utils.tca_engine import TCAManager
            tca = TCAManager()
            est = tca.estimate(order, market_data)
            if not est.approved:
                skip_order(order)
        """
        from utils.tca_pre_trade_estimator import PreTradeEstimator

        # 单次调用创建独立 estimator (不缓存, 避免状态污染)
        # 如果需要复用, 上层应直接使用 PreTradeEstimator
        estimator = PreTradeEstimator(
            cost_threshold_bps=cost_threshold_bps,
            save_to_file=True,
        )
        return estimator.estimate(order, market_data)

    # ============================================================
    # T3.5: 执行后归因接口 (facade → PostTradeAttribution)
    # ============================================================
    def record(
        self,
        fill,
        estimate=None,
    ):
        """记录成交 + 预估对比 (T3.5)

        facade 方法: 委托给 PostTradeAttribution 实现.
        满足任务文档要求的 `tca_engine.record(fill)` 接口.

        Args:
            fill: FillRecord 或兼容字典
            estimate: T3.4 的 PreTradeEstimate (可选)
        """
        from utils.tca_post_trade_attribution import (
            FillRecord as PTAFillRecord,
        )
        from utils.tca_post_trade_attribution import (
            PostTradeAttribution,
        )

        # 延迟初始化 (单例缓存)
        if not hasattr(self, "_post_trade_attribution"):
            self._post_trade_attribution = PostTradeAttribution(save_to_file=True)

        # 字典转 FillRecord
        if isinstance(fill, dict):
            fill = PTAFillRecord(
                symbol=str(fill.get("symbol", "")),
                side=str(fill.get("side", "BUY")).upper(),
                shares=int(fill.get("shares", 0)),
                price=float(fill.get("price", 0.0)),
                timestamp=fill.get("timestamp", ""),
                broker=fill.get("broker", ""),
                venue=fill.get("venue", ""),
                order_id=fill.get("order_id", ""),
            )
        return self._post_trade_attribution.record(fill, estimate)

    def calibrate(self, pre_trade_estimator=None, **kwargs):
        """EOD 触发校准 (T3.5)

        facade 方法: 委托给 PostTradeAttribution.calibrate()

        Args:
            pre_trade_estimator: T3.4 的 PreTradeEstimator 实例
            **kwargs: 传递给 calibrate() 的参数 (percentile/min_threshold/max_threshold)

        Returns:
            新的否决阈值 (bps), 无数据则返回 None
        """
        from utils.tca_post_trade_attribution import PostTradeAttribution

        if not hasattr(self, "_post_trade_attribution"):
            self._post_trade_attribution = PostTradeAttribution(save_to_file=True)
        return self._post_trade_attribution.calibrate(pre_trade_estimator, **kwargs)
