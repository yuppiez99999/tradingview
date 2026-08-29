"""
TCA 执行后归因引擎 (Post-Trade TCA Attribution)
================================================
任务: T3.5 — 实现 TCA 执行后归因
责任层: L4 执行 + L7 归因
依赖: T3.4 (PreTradeEstimator)

设计原则 (顶级对冲基金标准):
    - 实际成交 vs 预估对比: 记录预估成本与实际成本的偏差
    - PnL 归因拆分: Alpha / Execution / Risk 三大维度
        * Alpha: 决策产生的 PnL (信号选择能力)
        * Execution: 执行产生的 PnL (滑点/冲击/延迟)
        * Risk: 风险管理产生的 PnL (对冲/止损/仓位调整)
    - 每日 EOD 触发 calibrate(): 根据实际成本反馈校准 T3.4 预估阈值
    - Feature Flag 透传 (HC-1): USE_TCA_POST_TRADE_ATTRIBUTION 默认 False,
      关闭时不进行归因 (兼容模式)
    - ConfigManager 4 级优先级 (HC-5)

归因方法学:
    - Alpha PnL = (decision_price - entry_price) * position * direction
        即信号生成时的决策价与持仓入市价的差, 反映信号 alpha 能力
    - Execution PnL = (entry_price - avg_exec_price) * shares * direction
        即预估入市价与实际成交均价的差, 反映执行质量
    - Risk PnL = hedge_pnl + stop_loss_pnl + position_adjust_pnl
        即风险管理动作产生的 PnL (对冲盈亏、止损、仓位调整)

参考:
    - Perold (1988) Implementation Shortfall
    - Brinson-Fachler (1985) Performance Attribution
    - Kissell (2013) The Science of Algorithmic Trading

用法:
    from utils.tca_post_trade_attribution import PostTradeAttribution

    attribution = PostTradeAttribution()
    # 1. 记录成交
    attribution.record(fill=fill_record, estimate=pre_trade_estimate)
    # 2. 对比预估 vs 实际
    comparison = attribution.compare_estimate_vs_actual(symbol="600276")
    # 3. PnL 归因
    pnl_attribution = attribution.attribute_pnl(
        symbol="600276",
        decision_price=10.0,
        avg_exec_price=10.05,
        shares=10000,
        side="BUY",
        hedge_pnl=-500,
        stop_loss_pnl=0,
        position_adjust_pnl=0,
    )
    # 4. EOD 触发校准
    attribution.calibrate(pre_trade_estimator)
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("tca_post_trade_attribution")

# ============================================================
# 路径常量
# ============================================================
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_ATTRIBUTION_DIR = _PROJECT_ROOT / "reports" / "tca"

# ============================================================
# 默认配置
# ============================================================
DEFAULT_ESTIMATE_VS_ACTUAL_TOLERANCE_BPS = 5.0  # 预估 vs 实际偏差容忍度 (bps)


class PostTradeAttributionError(Exception):
    """TCA 执行后归因异常"""


# ============================================================
# 数据结构
# ============================================================
@dataclass
class FillRecord:
    """成交记录 (复用 tca_engine.FillRecord 字段, 简化版)"""

    symbol: str
    side: str  # "BUY" / "SELL"
    shares: int
    price: float  # 实际成交价
    timestamp: str = ""
    broker: str = ""
    venue: str = ""
    order_id: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat(timespec="seconds")


@dataclass
class EstimateVsActual:
    """预估 vs 实际对比"""

    symbol: str
    side: str
    estimated_cost_bps: float
    actual_cost_bps: float
    deviation_bps: float  # actual - estimate (正=超预估, 负=优于预估)
    estimated_amount: float
    actual_amount: float
    deviation_amount: float
    within_tolerance: bool
    tolerance_bps: float
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PnLAttribution:
    """PnL 归因拆分

    总 PnL = Alpha PnL + Execution PnL + Risk PnL

    Attributes:
        symbol: 标的代码
        side: 买卖方向
        shares: 成交数量
        decision_price: 决策价 (信号生成时的中价)
        avg_exec_price: 实际成交均价
        alpha_pnl: Alpha PnL (决策价 vs 入市价) × 数量 × 方向
        execution_pnl: Execution PnL (预估入市价 vs 实际成交价) × 数量 × 方向
        risk_pnl: Risk PnL (对冲 + 止损 + 仓位调整)
        total_pnl: 总 PnL (alpha + execution + risk)
        alpha_bps: Alpha 贡献 (bps)
        execution_bps: Execution 贡献 (bps)
        risk_bps: Risk 贡献 (bps)
        notional: 名义金额
        timestamp: ISO 时间戳
    """

    symbol: str
    side: str
    shares: int
    decision_price: float
    avg_exec_price: float
    alpha_pnl: float
    execution_pnl: float
    risk_pnl: float
    total_pnl: float
    alpha_bps: float
    execution_bps: float
    risk_bps: float
    notional: float
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================
# 执行后归因引擎
# ============================================================
class PostTradeAttribution:
    """TCA 执行后归因引擎

    用法:
        attribution = PostTradeAttribution()
        attribution.record(fill=fill, estimate=pre_trade_estimate)
        comparison = attribution.compare_estimate_vs_actual("600276")
        pnl_attr = attribution.attribute_pnl(
            symbol="600276", decision_price=10.0,
            avg_exec_price=10.05, shares=10000, side="BUY",
            hedge_pnl=-500,
        )
        attribution.calibrate(pre_trade_estimator)
    """

    def __init__(
        self,
        tolerance_bps: float = DEFAULT_ESTIMATE_VS_ACTUAL_TOLERANCE_BPS,
        attribution_dir: Path | None = None,
        save_to_file: bool = True,
    ) -> None:
        """
        Args:
            tolerance_bps: 预估 vs 实际偏差容忍度 (bps)
            attribution_dir: 归因记录目录, 默认 reports/tca/
            save_to_file: 是否写入 JSONL 文件
        """
        self.tolerance_bps = float(tolerance_bps)
        self.attribution_dir = (
            Path(attribution_dir) if attribution_dir else _DEFAULT_ATTRIBUTION_DIR
        )
        self.save_to_file = bool(save_to_file)

        # 内存缓存: {symbol: [(fill, estimate), ...]}
        self._records: dict[str, list[tuple[FillRecord, Any | None]]] = defaultdict(
            list
        )
        # PnL 归因历史: {symbol: [PnLAttribution, ...]}
        self._pnl_history: dict[str, list[PnLAttribution]] = defaultdict(list)
        # 对比历史: {symbol: [EstimateVsActual, ...]}
        self._comparison_history: dict[str, list[EstimateVsActual]] = defaultdict(list)

        if self.save_to_file:
            self.attribution_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------
    # 1. 记录成交 (任务文档要求 tca_engine.record(fill))
    # ------------------------------------------------------------
    def record(
        self,
        fill: FillRecord,
        estimate: Any | None = None,
    ) -> None:
        """记录一笔成交及其对应的预估

        Args:
            fill: 成交记录
            estimate: T3.4 的 PreTradeEstimate (可选, 用于预估 vs 实际对比)
        """
        if not isinstance(fill, FillRecord):
            raise PostTradeAttributionError(
                f"fill 必须是 FillRecord 类型, 实际={type(fill).__name__}"
            )
        self._records[fill.symbol].append((fill, estimate))

        # 持久化
        if self.save_to_file:
            try:
                self._save_fill_record(fill, estimate)
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ) as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
                logger.error("[TCA-PostTrade] 保存成交记录失败: %s", e)

        logger.info(
            "[TCA-PostTrade] 记录成交: %s %s %d@%.4f (estimate=%s)",
            fill.symbol,
            fill.side,
            fill.shares,
            fill.price,
            "yes" if estimate is not None else "no",
        )

    # ------------------------------------------------------------
    # 2. 预估 vs 实际对比
    # ------------------------------------------------------------
    def compare_estimate_vs_actual(
        self,
        symbol: str,
        decision_price: float | None = None,
    ) -> EstimateVsActual | None:
        """对比某标的的预估成本与实际成本

        Args:
            symbol: 标的代码
            decision_price: 决策价 (用于计算实际成本), 若 None 则用最近 fill.price

        Returns:
            EstimateVsActual 对比结果, 若无记录则返回 None
        """
        records = self._records.get(symbol, [])
        if not records:
            return None

        # 取最近一笔 (fill, estimate)
        fill, estimate = records[-1]
        if estimate is None:
            logger.warning("[TCA-PostTrade] %s 无预估记录, 无法对比", symbol)
            return None

        # 实际成本计算: 基于 decision_price 与 fill.price 的偏差
        ref_price = (
            decision_price
            if decision_price is not None and decision_price > 0
            else fill.price
        )
        if ref_price <= 0:
            return None

        # 实际成本 (bps) = |fill.price - ref_price| / ref_price * 10000
        direction = 1 if fill.side.upper() == "BUY" else -1
        actual_cost_bps = direction * (fill.price - ref_price) / ref_price * 10000
        # 实际成本为正数 (买入价高 = 成本, 卖出价低 = 成本)
        actual_cost_bps = abs(actual_cost_bps)

        estimated_cost_bps = float(getattr(estimate, "estimated_cost_bps", 0.0))
        estimated_amount = float(getattr(estimate, "estimated_cost_amount", 0.0))

        # 实际金额 = actual_cost_bps * notional / 10000
        notional = float(getattr(estimate, "notional", fill.shares * fill.price))
        actual_amount = actual_cost_bps * notional / 10000.0

        deviation_bps = actual_cost_bps - estimated_cost_bps
        deviation_amount = actual_amount - estimated_amount
        within_tolerance = abs(deviation_bps) <= self.tolerance_bps

        comparison = EstimateVsActual(
            symbol=symbol,
            side=fill.side,
            estimated_cost_bps=estimated_cost_bps,
            actual_cost_bps=actual_cost_bps,
            deviation_bps=deviation_bps,
            estimated_amount=estimated_amount,
            actual_amount=actual_amount,
            deviation_amount=deviation_amount,
            within_tolerance=within_tolerance,
            tolerance_bps=self.tolerance_bps,
        )

        self._comparison_history[symbol].append(comparison)

        # 持久化
        if self.save_to_file:
            try:
                self._save_comparison(comparison)
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ) as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
                logger.error("[TCA-PostTrade] 保存对比记录失败: %s", e)

        # 偏差超容忍度时告警
        if not within_tolerance:
            logger.warning(
                "[TCA-PostTrade] %s 预估偏差超限: %.2f bps (容忍 %.1f bps)",
                symbol,
                deviation_bps,
                self.tolerance_bps,
            )

        return comparison

    # ------------------------------------------------------------
    # 3. PnL 归因拆分
    # ------------------------------------------------------------
    def attribute_pnl(
        self,
        symbol: str,
        decision_price: float,
        avg_exec_price: float,
        shares: int,
        side: str,
        hedge_pnl: float = 0.0,
        stop_loss_pnl: float = 0.0,
        position_adjust_pnl: float = 0.0,
        estimated_entry_price: float | None = None,
    ) -> PnLAttribution:
        """PnL 归因拆分 (Alpha / Execution / Risk)

        公式:
            Alpha PnL = (decision_price - entry_price) * shares * direction
                entry_price = 估计入市价 (即预估 T0 价, 无预估时 = decision_price)
            Execution PnL = (entry_price - avg_exec_price) * shares * direction
            Risk PnL = hedge_pnl + stop_loss_pnl + position_adjust_pnl
            Total = Alpha + Execution + Risk

        Args:
            symbol: 标的代码
            decision_price: 决策价 (信号生成时中价)
            avg_exec_price: 实际成交均价
            shares: 成交数量
            side: BUY/SELL
            hedge_pnl: 对冲盈亏 (负数=对冲成本)
            stop_loss_pnl: 止损盈亏 (负数=止损损失)
            position_adjust_pnl: 仓位调整盈亏
            estimated_entry_price: 预估入市价 (None 则用 decision_price)

        Returns:
            PnLAttribution 归因结果
        """
        if decision_price <= 0:
            raise PostTradeAttributionError(
                f"decision_price 必须 > 0, 实际={decision_price}"
            )
        if avg_exec_price <= 0:
            raise PostTradeAttributionError(
                f"avg_exec_price 必须 > 0, 实际={avg_exec_price}"
            )
        if shares <= 0:
            raise PostTradeAttributionError(f"shares 必须 > 0, 实际={shares}")

        side = side.upper()
        if side not in ("BUY", "SELL"):
            raise PostTradeAttributionError(f"side 必须 BUY/SELL, 实际={side}")

        # 方向系数: BUY=+1 (价格涨=盈利), SELL=-1 (价格跌=盈利)
        direction = 1 if side == "BUY" else -1

        # 入市价: 优先用预估入市价, 否则用决策价
        entry_price = (
            estimated_entry_price
            if estimated_entry_price and estimated_entry_price > 0
            else decision_price
        )

        # Alpha PnL = (decision_price - entry_price) * shares * direction
        # 当 entry_price = decision_price 时, Alpha PnL = 0 (无预估延迟)
        alpha_pnl = (decision_price - entry_price) * shares * direction

        # Execution PnL = (entry_price - avg_exec_price) * shares * direction
        # 买入: 实际价低于预估 = 执行优于预估 (正)
        # 卖出: 实际价高于预估 = 执行优于预估 (正)
        execution_pnl = (entry_price - avg_exec_price) * shares * direction

        # Risk PnL = 对冲 + 止损 + 仓位调整
        risk_pnl = float(hedge_pnl) + float(stop_loss_pnl) + float(position_adjust_pnl)

        # 总 PnL
        total_pnl = alpha_pnl + execution_pnl + risk_pnl

        # bps 计算 (相对于名义金额)
        notional = shares * avg_exec_price
        alpha_bps = alpha_pnl / notional * 10000 if notional > 0 else 0.0
        execution_bps = execution_pnl / notional * 10000 if notional > 0 else 0.0
        risk_bps = risk_pnl / notional * 10000 if notional > 0 else 0.0

        attribution = PnLAttribution(
            symbol=symbol,
            side=side,
            shares=shares,
            decision_price=decision_price,
            avg_exec_price=avg_exec_price,
            alpha_pnl=float(alpha_pnl),
            execution_pnl=float(execution_pnl),
            risk_pnl=float(risk_pnl),
            total_pnl=float(total_pnl),
            alpha_bps=float(alpha_bps),
            execution_bps=float(execution_bps),
            risk_bps=float(risk_bps),
            notional=float(notional),
        )

        self._pnl_history[symbol].append(attribution)

        # 持久化
        if self.save_to_file:
            try:
                self._save_pnl_attribution(attribution)
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ) as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
                logger.error("[TCA-PostTrade] 保存 PnL 归因失败: %s", e)

        logger.info(
            "[TCA-PostTrade] %s PnL 归因: Alpha=%.2f / Exec=%.2f / Risk=%.2f / Total=%.2f (bps: A=%.2f E=%.2f R=%.2f)",
            symbol,
            alpha_pnl,
            execution_pnl,
            risk_pnl,
            total_pnl,
            alpha_bps,
            execution_bps,
            risk_bps,
        )

        return attribution

    # ------------------------------------------------------------
    # 4. EOD 校准 (任务文档要求 tca_engine.calibrate())
    # ------------------------------------------------------------
    def calibrate(
        self,
        pre_trade_estimator: Any | None = None,
        percentile: float = 0.95,
        min_threshold: float = 10.0,
        max_threshold: float = 100.0,
    ) -> float | None:
        """EOD 触发校准: 根据当日实际成本反馈调整 T3.4 预估阈值

        策略:
            1. 收集当日所有 EstimateVsActual.actual_cost_bps
            2. 调用 PreTradeEstimator.calibrate_threshold() 调整阈值
            3. 写入校准日志

        Args:
            pre_trade_estimator: T3.4 的 PreTradeEstimator 实例 (None 则不调整)
            percentile: 分位数 (0-1)
            min_threshold: 阈值下限
            max_threshold: 阈值上限

        Returns:
            新的否决阈值 (bps), 无数据则返回 None
        """
        # 收集当日实际成本
        actual_costs: list[float] = []
        for _symbol, comparisons in self._comparison_history.items():
            for c in comparisons:
                if c.actual_cost_bps > 0:
                    actual_costs.append(c.actual_cost_bps)

        if not actual_costs:
            logger.info("[TCA-PostTrade] EOD 校准: 无实际成本数据, 跳过")
            return None

        if pre_trade_estimator is None:
            # 仅记录实际成本统计, 不调整阈值
            avg_cost = sum(actual_costs) / len(actual_costs)
            max_cost = max(actual_costs)
            logger.info(
                "[TCA-PostTrade] EOD 校准统计: n=%d, avg=%.2f bps, max=%.2f bps (未调整阈值)",
                len(actual_costs),
                avg_cost,
                max_cost,
            )
            return None

        # 调用 T3.4 PreTradeEstimator.calibrate_threshold()
        if not hasattr(pre_trade_estimator, "calibrate_threshold"):
            raise PostTradeAttributionError(
                "pre_trade_estimator 缺少 calibrate_threshold 方法"
            )

        old_threshold = float(getattr(pre_trade_estimator, "cost_threshold_bps", 0.0))
        new_threshold = pre_trade_estimator.calibrate_threshold(
            actual_costs_bps=actual_costs,
            percentile=percentile,
            min_threshold=min_threshold,
            max_threshold=max_threshold,
        )

        # 持久化校准日志
        if self.save_to_file:
            try:
                self._save_calibration_log(
                    actual_costs=actual_costs,
                    old_threshold=old_threshold,
                    new_threshold=new_threshold,
                    percentile=percentile,
                )
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ) as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
                logger.error("[TCA-PostTrade] 保存校准日志失败: %s", e)

        logger.info(
            "[TCA-PostTrade] EOD 校准完成: %.2f → %.2f (n=%d, p%.0f)",
            old_threshold,
            new_threshold,
            len(actual_costs),
            percentile * 100,
        )
        return new_threshold  # type: ignore

    # ------------------------------------------------------------
    # 5. 汇总报告
    # ------------------------------------------------------------
    def summarize(self) -> dict[str, Any]:
        """生成全组合归因汇总"""
        # PnL 归因汇总
        total_alpha = sum(
            a.alpha_pnl for attr_list in self._pnl_history.values() for a in attr_list
        )
        total_execution = sum(
            a.execution_pnl
            for attr_list in self._pnl_history.values()
            for a in attr_list
        )
        total_risk = sum(
            a.risk_pnl for attr_list in self._pnl_history.values() for a in attr_list
        )
        total_pnl = total_alpha + total_execution + total_risk

        # 预估 vs 实际汇总
        all_comparisons = [
            c for cmp_list in self._comparison_history.values() for c in cmp_list
        ]
        n_within = sum(1 for c in all_comparisons if c.within_tolerance)
        n_total = len(all_comparisons)
        avg_deviation = (
            sum(c.deviation_bps for c in all_comparisons) / n_total
            if n_total > 0
            else 0.0
        )

        # 按标的汇总
        per_symbol: dict[str, dict[str, float]] = {}
        for symbol, attr_list in self._pnl_history.items():
            sym_alpha = sum(a.alpha_pnl for a in attr_list)
            sym_exec = sum(a.execution_pnl for a in attr_list)
            sym_risk = sum(a.risk_pnl for a in attr_list)
            per_symbol[symbol] = {
                "alpha_pnl": sym_alpha,
                "execution_pnl": sym_exec,
                "risk_pnl": sym_risk,
                "total_pnl": sym_alpha + sym_exec + sym_risk,
            }

        return {
            "total_pnl": float(total_pnl),
            "alpha_pnl": float(total_alpha),
            "execution_pnl": float(total_execution),
            "risk_pnl": float(total_risk),
            "alpha_pct": (
                float(total_alpha / total_pnl * 100) if total_pnl != 0 else 0.0
            ),
            "execution_pct": (
                float(total_execution / total_pnl * 100) if total_pnl != 0 else 0.0
            ),
            "risk_pct": float(total_risk / total_pnl * 100) if total_pnl != 0 else 0.0,
            "n_fills": sum(len(v) for v in self._records.values()),
            "n_comparisons": n_total,
            "n_within_tolerance": n_within,
            "tolerance_rate": float(n_within / n_total) if n_total > 0 else 0.0,
            "avg_deviation_bps": float(avg_deviation),
            "per_symbol": per_symbol,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }

    # ------------------------------------------------------------
    # 5.5 G4: 从 FillsStore 成交回报事实源批量归因
    # ------------------------------------------------------------
    def ingest_fills_from_store(self, date: str | None = None) -> int:
        """G4 补齐: 从 `FillsStore` 读当日成交回报, 作为单一事实源批量归因。

        设计铁律:
            - 有成交走成交 (读 fills_store), 无成交走行情/跳过, 互补不互斥
            - 观测路径 fail-open: 读取或归因失败只记日志, 不阻断
            - 前置校验: symbol 非空 / filled_qty>0 / avg_price>0, 防"成功但零成交"污染

        Args:
            date: 交易日 YYYY-MM-DD, None 用今日

        Returns:
            实际 ingest 进入归因的成交笔数
        """
        try:
            from utils.execution.fills_store import FillsStore

            fills = FillsStore().load_day(date)
        except (ImportError, AttributeError) as e:
            logger.warning("[TCA-PostTrade] 读取 FillsStore 失败, 跳过归因: %s", e)
            return 0

        ingested = 0
        for rec in fills:
            symbol = str(rec.get("symbol", "") or "").strip()
            side = str(rec.get("side", "") or "").strip().upper()
            try:
                filled_qty = float(rec.get("filled_qty", 0) or 0)
                avg_price = float(rec.get("avg_price", 0) or 0)
            except (TypeError, ValueError):
                continue
            if not symbol or filled_qty <= 0 or avg_price <= 0:
                continue
            meta = rec.get("meta") or {}
            fill = FillRecord(
                symbol=symbol,
                side=side,
                shares=int(filled_qty),
                price=avg_price,
                timestamp=str(rec.get("ts", "") or ""),
                broker=str(rec.get("broker", "") or ""),
                order_id=str(meta.get("order_id", "") or ""),
            )
            try:
                self.record(fill=fill, estimate=None)
                ingested += 1
            except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
                logger.warning("[TCA-PostTrade] 归因单笔成交失败 %s: %s", symbol, e)

        logger.info(
            "[TCA-PostTrade] 从 FillsStore 归因 %d 笔成交 (date=%s)",
            ingested,
            date or datetime.now().strftime("%Y-%m-%d"),
        )
        return ingested

    # ------------------------------------------------------------
    # 6. 历史查询
    # ------------------------------------------------------------
    def get_pnl_history(self, symbol: str | None = None) -> list[PnLAttribution]:
        """获取 PnL 归因历史"""
        if symbol:
            return list(self._pnl_history.get(symbol, []))
        return [a for attr_list in self._pnl_history.values() for a in attr_list]

    def get_comparison_history(
        self, symbol: str | None = None
    ) -> list[EstimateVsActual]:
        """获取预估 vs 实际对比历史"""
        if symbol:
            return list(self._comparison_history.get(symbol, []))
        return [c for cmp_list in self._comparison_history.values() for c in cmp_list]

    def get_fills(
        self, symbol: str | None = None
    ) -> list[tuple[FillRecord, Any | None]]:
        """获取成交记录历史"""
        if symbol:
            return list(self._records.get(symbol, []))
        return [(f, e) for rec_list in self._records.values() for (f, e) in rec_list]

    # ============================================================
    # 持久化方法
    # ============================================================
    def _save_fill_record(self, fill: FillRecord, estimate: Any | None) -> Path:
        """保存成交记录到 JSONL"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        path = self.attribution_dir / f"fills_{date_str}.jsonl"
        record = {
            "type": "fill",
            "fill": asdict(fill),
            "estimate": (
                estimate.to_dict()
                if estimate and hasattr(estimate, "to_dict")
                else None
            ),
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path

    def _save_comparison(self, comparison: EstimateVsActual) -> Path:
        """保存对比记录到 JSONL"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        path = self.attribution_dir / f"estimate_vs_actual_{date_str}.jsonl"
        record = {"type": "comparison", **comparison.to_dict()}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path

    def _save_pnl_attribution(self, attribution: PnLAttribution) -> Path:
        """保存 PnL 归因到 JSONL"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        path = self.attribution_dir / f"pnl_attribution_{date_str}.jsonl"
        record = {"type": "pnl_attribution", **attribution.to_dict()}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path

    def _save_calibration_log(
        self,
        actual_costs: list[float],
        old_threshold: float,
        new_threshold: float,
        percentile: float,
    ) -> Path:
        """保存校准日志到 JSONL"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        path = self.attribution_dir / f"calibration_{date_str}.jsonl"
        record = {
            "type": "calibration",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "n_samples": len(actual_costs),
            "avg_actual_cost_bps": (
                float(sum(actual_costs) / len(actual_costs)) if actual_costs else 0.0
            ),
            "max_actual_cost_bps": float(max(actual_costs)) if actual_costs else 0.0,
            "min_actual_cost_bps": float(min(actual_costs)) if actual_costs else 0.0,
            "percentile": float(percentile),
            "old_threshold_bps": float(old_threshold),
            "new_threshold_bps": float(new_threshold),
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path


# ============================================================
# 便捷工厂函数
# ============================================================
def create_default_attribution() -> PostTradeAttribution:
    """创建默认配置的归因器"""
    return PostTradeAttribution(save_to_file=True)


def create_no_save_attribution() -> PostTradeAttribution:
    """创建不写文件的归因器 (用于测试)"""
    return PostTradeAttribution(save_to_file=False)
