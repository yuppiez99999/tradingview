# -*- coding: utf-8 -*-
"""
v7.6 Implementation Shortfall — 实现缺口分解引擎

对标 D.E. Shaw / Two Sigma 标准:
  IS = Decision Price - Final Fill Price (方向调整)

缺口分解 (Perold 1988 框架):
  IS = Delay Cost    (决策→订单到达的市价变化)
     + Impact Cost   (订单对市场的冲击)
     + Timing Cost   (执行期间的市价不利移动)
     + Missed Cost   (未成交部分的 adverse move)
     + Fixed Cost    (佣金+印花税+交易所费用)

算法评分: 越低越好, 0 为完美执行
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List

import numpy as np

logger = logging.getLogger("v76.execution.is")


@dataclass
class ISDecomposition:
    """实现缺口完全分解"""

    symbol: str
    side: str
    order_type: str

    # 价格锚点
    decision_price: float
    arrival_price: float  # OMS 收到订单时的市价
    avg_fill_price: float
    close_price: float  # 执行完成后收盘
    vwap: float

    # 量
    order_qty: int
    fill_qty: int
    fill_rate: float

    # 实现缺口 (bps)
    total_is_bps: float  # 总实现缺口
    delay_cost_bps: float  # 延迟成本
    impact_cost_bps: float  # 市场冲击成本
    timing_cost_bps: float  # 时机成本
    missed_cost_bps: float  # 机会成本
    fixed_cost_bps: float  # 固定佣金/税费

    # 执行质量度量
    is_ratio: float = 0.0  # IS / 到达成本 (越低越好)
    benchmark_vs_is: float = 0.0  # VWAP 执行 vs IS 的对比

    # 元数据
    execution_time_s: float = 0
    num_fills: int = 0
    participation_pct: float = 0


@dataclass
class ISOptimizationSuggestion:
    """实现缺口优化建议"""

    category: str  # 'delay' | 'impact' | 'timing' | 'missed'
    current_bps: float
    target_bps: float
    potential_saving_bps: float
    recommendation: str
    priority: str  # 'HIGH' | 'MEDIUM' | 'LOW'


class ImplementationShortfall:
    """
    v7.6 实现缺口分析器

    对标机构执行分析标准:
      - 每笔大单 (>= 1% ADV) 自动触发 IS 分析
      - 滚动 30 天 IS 追踪 → 检测执行质量漂移
      - 按算法类型/交易台/时间窗口 归因
      - 生成优化建议 → 反馈至 Smart Order Router 参数调优

    目标: 将平均 IS 从 ~20bps (零售) 降至 ~8bps (机构水平)
    """

    def __init__(
        self,
        nav: float = 5_000_000,
        commission_rate: float = 0.00025,
        stamp_tax_rate: float = 0.001,
        exchange_fee_rate: float = 0.00005,
    ):
        self.nav = nav
        self.commission_rate = commission_rate
        self.stamp_tax_rate = stamp_tax_rate
        self.exchange_fee_rate = exchange_fee_rate

        self.decompositions: List[ISDecomposition] = []
        self.optimization_history: List[ISOptimizationSuggestion] = []

    def decompose(
        self,
        symbol: str,
        side: str,
        order_type: str,
        decision_price: float,
        arrival_price: float,
        avg_fill_price: float,
        close_price: float,
        vwap: float,
        order_qty: int,
        fill_qty: int,
        execution_time_s: float = 0,
        num_fills: int = 1,
        daily_volume: int = 0,
    ) -> ISDecomposition:
        """
        Perold 1988 框架: 五段分解

        Ratio 定义 (买入):
          Decision Price  ← 理论最优
          Arrival Price   ← 延迟后的实际可用价格
          VWAP           ← 执行期间的公允价格
          Fill Price     ← 实际成交价格
          Close Price    ← 执行后价格

        买入 IS: (Fill - Decision) / Decision * 10000 (正=买贵了)
        卖出 IS: (Decision - Fill) / Decision * 10000 (正=卖便宜了)
        """
        direction = 1 if side == "BUY" else -1

        def signed_diff(a, b):
            """方向调整的价格差 (bps)"""
            if a <= 0 or b <= 0:
                return 0.0
            return direction * (a - b) / b * 10000

        # 总实现缺口
        total_is = signed_diff(avg_fill_price, decision_price)

        # 1. 延迟成本 = decision → arrival
        delay_cost = signed_diff(arrival_price, decision_price)

        # 2. 冲击成本 = arrival → VWAP (执行期间市场的 adverse move)
        impact_cost = signed_diff(vwap, arrival_price)

        # 3. 时机成本 = VWAP → fill (执行策略 vs 被动 VWAP)
        timing_cost = signed_diff(avg_fill_price, vwap)

        # 4. 机会成本 = 未成交部分 × (close - decision)
        unfilled_ratio = (order_qty - fill_qty) / max(order_qty, 1)
        missed_cost = signed_diff(close_price, decision_price) * unfilled_ratio

        # 5. 固定成本
        fill_qty * avg_fill_price
        fixed_bps = self.commission_rate * 10000 + self.exchange_fee_rate * 10000
        if side == "SELL":
            fixed_bps += self.stamp_tax_rate * 10000

        # 执行质量度量
        is_ratio = abs(total_is) / max(abs(delay_cost) + abs(impact_cost) + abs(timing_cost) + 1e-8, 1e-8)

        benchmark_vs_is = signed_diff(avg_fill_price, vwap)

        # 参与率
        participation = fill_qty / max(daily_volume, 1) if daily_volume > 0 else 0

        decomp = ISDecomposition(
            symbol=symbol,
            side=side,
            order_type=order_type,
            decision_price=decision_price,
            arrival_price=arrival_price,
            avg_fill_price=avg_fill_price,
            close_price=close_price,
            vwap=vwap,
            order_qty=order_qty,
            fill_qty=fill_qty,
            fill_rate=round(fill_qty / max(order_qty, 1), 4),
            total_is_bps=round(total_is, 2),
            delay_cost_bps=round(delay_cost, 2),
            impact_cost_bps=round(impact_cost, 2),
            timing_cost_bps=round(timing_cost, 2),
            missed_cost_bps=round(missed_cost, 2),
            fixed_cost_bps=round(fixed_bps, 2),
            is_ratio=round(is_ratio, 4),
            benchmark_vs_is=round(benchmark_vs_is, 2),
            execution_time_s=execution_time_s,
            num_fills=num_fills,
            participation_pct=round(participation * 100, 4),
        )

        self.decompositions.append(decomp)
        logger.info(
            f"[IS] {symbol} {side}: total={total_is:.1f}bps, "
            f"delay={delay_cost:.1f}, impact={impact_cost:.1f}, "
            f"timing={timing_cost:.1f}, missed={missed_cost:.1f}"
        )

        return decomp

    # ---------- 优化建议生成 ----------
    def generate_suggestions(self, decomp: ISDecomposition) -> List[ISOptimizationSuggestion]:
        """基于分解结果生成针对性优化建议"""
        suggestions = []

        components = [
            (
                "delay",
                decomp.delay_cost_bps,
                2.0,
                "减少决策→执行延迟, 考虑使用 DMA/算法单直连交易所",
                "HIGH" if abs(decomp.delay_cost_bps) > 5 else "LOW",
            ),
            (
                "impact",
                decomp.impact_cost_bps,
                3.0,
                "降低市场冲击: TWAP/VWAP 分散执行, 降低 POV 参与率",
                "HIGH" if abs(decomp.impact_cost_bps) > 8 else "MEDIUM",
            ),
            (
                "timing",
                decomp.timing_cost_bps,
                2.0,
                "改进时机选择: 使用 IS 最小化算法, 流动性预测",
                "HIGH" if abs(decomp.timing_cost_bps) > 4 else "LOW",
            ),
            (
                "missed",
                decomp.missed_cost_bps,
                1.0,
                "减少未成交: 提高激进程度, 使用 IOC/FOK 品种",
                "MEDIUM" if decomp.missed_cost_bps > 3 else "LOW",
            ),
        ]

        for cat, current, target, rec, priority in components:
            saving = max(0, abs(current) - target)
            if saving > 0.5:
                suggestions.append(
                    ISOptimizationSuggestion(
                        category=cat,
                        current_bps=round(current, 2),
                        target_bps=target,
                        potential_saving_bps=round(saving, 2),
                        recommendation=rec,
                        priority=priority,
                    )
                )

        return suggestions

    # ---------- 汇总分析 ----------
    def summary(self, window_days: int = 30) -> Dict:
        """执行质量汇总"""
        if not self.decompositions:
            return {"n_trades": 0, "message": "无交易数据"}

        recent = self.decompositions[-window_days:] if len(self.decompositions) > window_days else self.decompositions

        def _avg(items, attr):
            if not items:
                return 0
            return round(np.mean([getattr(i, attr) for i in items]), 2)

        def _percentile(items, attr, p):
            vals = [getattr(i, attr) for i in items if getattr(i, attr) != 0]
            if not vals:
                return 0
            return round(np.percentile(vals, p), 2)

        buys = [d for d in recent if d.side == "BUY"]
        sells = [d for d in recent if d.side == "SELL"]

        return {
            "n_trades": len(recent),
            "avg_total_is_bps": _avg(recent, "total_is_bps"),
            "avg_delay_bps": _avg(recent, "delay_cost_bps"),
            "avg_impact_bps": _avg(recent, "impact_cost_bps"),
            "avg_timing_bps": _avg(recent, "timing_cost_bps"),
            "avg_missed_bps": _avg(recent, "missed_cost_bps"),
            "avg_fixed_bps": _avg(recent, "fixed_cost_bps"),
            "avg_fill_rate": _avg(recent, "fill_rate"),
            "p95_is_bps": _percentile(recent, "total_is_bps", 95),
            # 拆分
            "buy_is_bps": _avg(buys, "total_is_bps"),
            "sell_is_bps": _avg(sells, "total_is_bps"),
            "buy_impact_bps": _avg(buys, "impact_cost_bps"),
            "sell_impact_bps": _avg(sells, "impact_cost_bps"),
            # 目标 vs 实际
            "target_is_bps": 8.0,
            "gap_to_target": _avg(recent, "total_is_bps") - 8.0,
            "annual_savings_if_target": round((_avg(recent, "total_is_bps") - 8.0) / 10000 * self.nav * 2.0, 2),
        }

    def report(self) -> Dict:
        """完整 IS 报告"""
        s = self.summary()

        # 收集优化建议
        all_suggestions = []
        for d in self.decompositions[-50:]:
            all_suggestions.extend(self.generate_suggestions(d))

        # 按优先级排序
        priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        all_suggestions.sort(key=lambda x: priority_order.get(x.priority, 3))

        high_priority = [sg for sg in all_suggestions if sg.priority == "HIGH"]

        return {
            **s,
            "n_suggestions": len(all_suggestions),
            "high_priority_suggestions": high_priority,
            "total_potential_saving_bps": round(sum(sg.potential_saving_bps for sg in all_suggestions), 2),
            "benchmark_comparison": {
                "retail_average_is": 25,
                "institutional_average_is": 12,
                "top_quartile_is": 8,
                "current_is": s["avg_total_is_bps"],
                "vs_institutional": s["avg_total_is_bps"] - 12,
                "vs_top_quartile": s["avg_total_is_bps"] - 8,
            },
        }

    def reset(self):
        """重置分析状态"""
        self.decompositions.clear()
        self.optimization_history.clear()
