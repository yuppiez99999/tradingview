"""
TCA 执行前预估器 (Pre-Trade TCA Estimator)
==========================================
任务: T3.4 — 实现 TCA 执行前预估
责任层: L4 执行
依赖: T1.4 (TransactionCostModel 已有)

设计原则 (顶级对冲基金标准):
    - 执行即 alpha: 高成本订单应在执行前被否决, 而非事后复盘
    - 阈值否决: 预估成本 > 阈值时, 拒绝订单并记录原因
    - 延迟预算 <50ms: 通过简单的成本模型计算, 不调用外部数据源
    - Feature Flag 透传 (HC-1): USE_TCA_PRE_TRADE_ESTIMATE 默认 False,
      关闭时降级为不预估 (兼容模式)
    - ConfigManager 4 级优先级 (HC-5): 配置走 multi_factor_signal.yaml 或
      v8.3_institutional/config/ 下的 tca_pre_trade.yaml

预估模型 (基于 TransactionCostModel):
    1. 滑点 (Slippage): 按市值分层 (大/中/小/微盘) × 波动率调整
    2. 佣金 (Commission): 双边万2.5 + 印花税 (卖出万5) + 过户费
    3. 市场冲击 (Impact): Square-Root Law × 分层系数 × 参与率^0.75
    4. 机会成本 (Opportunity): 日度延迟成本
    5. 延迟成本 (Delay): 小时级延迟成本

输出接口:
    - PreTradeEstimate 数据类: 含 cost_bps, cost_amount, approved, reason
    - PreTradeEstimator.estimate(order, market_data) → PreTradeEstimate
    - PreTradeEstimator.save_estimate(estimate) → 写入 reports/tca/estimate_{date}.jsonl

接入点:
    - tca_engine.TCAManager.estimate() (facade)
    - execution_router.ExecutionRouter.route_with_tca() (HC-1 透传)

用法:
    from utils.tca_pre_trade_estimator import PreTradeEstimator

    estimator = PreTradeEstimator(
        cost_threshold_bps=30.0,  # 30bps 阈值
    )
    estimate = estimator.estimate(
        order={
            "symbol": "600276",
            "side": "BUY",
            "shares": 10000,
            "price": 50.0,
            "notional": 500_000,
            "market_cap": 800e8,  # 800亿
        },
        market_data={
            "adv": 100_000_000,  # 日均成交额 1亿
            "volatility": 0.025,
        },
    )
    if not estimate.approved:
        logger.info(f"订单否决: {estimate.rejection_reason}")
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.transaction_cost_model import (
    CostParameters,
    TransactionCostModel,
)

logger = logging.getLogger("tca_pre_trade_estimator")

# ============================================================
# 路径常量
# ============================================================
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_ESTIMATE_DIR = _PROJECT_ROOT / "reports" / "tca"

# ============================================================
# 默认配置
# ============================================================
DEFAULT_COST_THRESHOLD_BPS = 30.0  # 30 bps = 0.3% 否决阈值
DEFAULT_LATENCY_LIMIT_MS = 50.0  # 预估延迟上限 50ms
DEFAULT_VOLATILITY_FALLBACK = 0.02  # 波动率缺省 2% (年化)
DEFAULT_ADV_FALLBACK = 5_000_000  # ADV 缺省 500万 (避免除0)


class PreTradeEstimateError(Exception):
    """TCA 预估异常"""


# ============================================================
# 数据结构
# ============================================================
@dataclass
class PreTradeEstimate:
    """执行前预估结果

    Attributes:
        symbol: 标的代码
        side: 买卖方向 (BUY/SELL)
        shares: 委托数量
        notional: 名义金额 (元)
        price: 委托价
        tier: 市值分层 (large/mid/small/micro)
        estimated_cost_bps: 预估总成本 (bps)
        estimated_cost_amount: 预估总成本 (元)
        cost_breakdown: 成本分解 (slippage/commission/impact/opportunity/delay)
        approved: 是否通过 (cost_bps <= threshold)
        rejection_reason: 否决原因 (approved=False 时填充)
        threshold_bps: 否决阈值 (bps)
        latency_ms: 预估耗时 (ms)
        timestamp: ISO 时间戳
    """

    symbol: str
    side: str
    shares: int
    notional: float
    price: float
    tier: str
    estimated_cost_bps: float
    estimated_cost_amount: float
    cost_breakdown: dict[str, float]
    approved: bool
    rejection_reason: str
    threshold_bps: float
    latency_ms: float
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, Any]:
        """转为字典 (用于持久化)"""
        return asdict(self)

    def to_jsonl(self) -> str:
        """转为 JSONL 行 (单行 JSON)"""
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ============================================================
# 预估器
# ============================================================
class PreTradeEstimator:
    """执行前 TCA 预估器

    用法:
        estimator = PreTradeEstimator(cost_threshold_bps=30.0)
        estimate = estimator.estimate(order, market_data)
        if not estimate.approved:
            logger.warning("订单被 TCA 否决: %s", estimate.rejection_reason)
    """

    def __init__(
        self,
        cost_threshold_bps: float = DEFAULT_COST_THRESHOLD_BPS,
        latency_limit_ms: float = DEFAULT_LATENCY_LIMIT_MS,
        cost_model: TransactionCostModel | None = None,
        cost_params: CostParameters | None = None,
        estimate_dir: Path | None = None,
        save_to_file: bool = True,
    ) -> None:
        """
        Args:
            cost_threshold_bps: 成本否决阈值 (bps), 超过则 approved=False
            latency_limit_ms: 延迟上限 (ms), 用于自检
            cost_model: 自定义 TransactionCostModel (None 则用 cost_params 创建)
            cost_params: 自定义 CostParameters (当 cost_model=None 时生效)
            estimate_dir: 预估记录目录, 默认 reports/tca/
            save_to_file: 是否写入 JSONL 文件
        """
        self.cost_threshold_bps = float(cost_threshold_bps)
        self.latency_limit_ms = float(latency_limit_ms)
        self.cost_model = cost_model or TransactionCostModel(cost_params)
        self.estimate_dir = Path(estimate_dir) if estimate_dir else _DEFAULT_ESTIMATE_DIR
        self.save_to_file = bool(save_to_file)

        # 确保目录存在
        if self.save_to_file:
            self.estimate_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------
    def estimate(
        self,
        order: dict[str, Any],
        market_data: dict[str, Any] | None = None,
    ) -> PreTradeEstimate:
        """执行前成本预估

        Args:
            order: 订单字典, 必须包含:
                - symbol: 标的代码
                - side: BUY/SELL
                - shares: 委托数量
                - price: 委托价
                - notional: 名义金额 (= shares * price, 若缺省则自动计算)
                - market_cap: 市值 (可选, 用于分层)
            market_data: 市场数据字典, 可选:
                - adv: 日均成交额 (默认 500万)
                - volatility: 年化波动率 (默认 2%)

        Returns:
            PreTradeEstimate 预估结果
        """
        t_start = time.perf_counter()

        # 参数解析与缺省值填充
        market_data = market_data or {}
        symbol = str(order.get("symbol", ""))
        side = str(order.get("side", "BUY")).upper()
        shares = int(order.get("shares", 0))
        price = float(order.get("price", 0.0))
        notional = float(order.get("notional", 0.0))
        if notional <= 0 and shares > 0 and price > 0:
            notional = shares * price
        market_cap = order.get("market_cap")
        market_cap = float(market_cap) if market_cap is not None else None

        adv = float(market_data.get("adv", DEFAULT_ADV_FALLBACK))
        volatility = float(market_data.get("volatility", DEFAULT_VOLATILITY_FALLBACK))

        # 参数校验
        if not symbol:
            raise PreTradeEstimateError("order.symbol 不能为空")
        if shares <= 0:
            raise PreTradeEstimateError(f"order.shares 必须 > 0, 实际={shares}")
        if price <= 0:
            raise PreTradeEstimateError(f"order.price 必须 > 0, 实际={price}")
        if notional <= 0:
            raise PreTradeEstimateError(f"order.notional 必须 > 0, 实际={notional}")
        if side not in ("BUY", "SELL"):
            raise PreTradeEstimateError(f"order.side 必须 BUY/SELL, 实际={side}")

        # 调用 TransactionCostModel 计算成本
        cost_detail = self.cost_model.estimate_total_cost(
            notional=notional,
            adv=adv,
            volatility=volatility,
            days_delayed=1.0,  # 默认 1 天延迟
            hours_delayed=0.0,
            market_cap=market_cap,
            side=side,
        )

        # 市值分层 (从 cost_detail 取)
        tier = cost_detail.get("tier", "micro")
        total_cost = float(cost_detail.get("total", 0.0))
        cost_bps = float(cost_detail.get("cost_bps", 0.0))

        # 延迟统计
        latency_ms = (time.perf_counter() - t_start) * 1000.0

        # 阈值否决判断
        approved = cost_bps <= self.cost_threshold_bps
        rejection_reason = ""
        if not approved:
            rejection_reason = (
                f"cost_bps={cost_bps:.2f} > threshold={self.cost_threshold_bps:.2f} "
                f"(symbol={symbol}, tier={tier}, notional={notional:.0f})"
            )

        # 构建结果
        estimate = PreTradeEstimate(
            symbol=symbol,
            side=side,
            shares=shares,
            notional=notional,
            price=price,
            tier=tier,  # type: ignore
            estimated_cost_bps=cost_bps,
            estimated_cost_amount=total_cost,
            cost_breakdown={
                "slippage": float(cost_detail.get("slippage", 0.0)),
                "commission": float(cost_detail.get("commission", 0.0)),
                "impact": float(cost_detail.get("impact", 0.0)),
                "opportunity_cost": float(cost_detail.get("opportunity_cost", 0.0)),
                "delay_cost": float(cost_detail.get("delay_cost", 0.0)),
            },
            approved=approved,
            rejection_reason=rejection_reason,
            threshold_bps=self.cost_threshold_bps,
            latency_ms=latency_ms,
        )

        # 延迟自检 (仅日志, 不阻断)
        if latency_ms > self.latency_limit_ms:
            logger.warning(
                "[TCA-PreTrade] 延迟超限: %.2fms > %.1fms (symbol=%s)",
                latency_ms,
                self.latency_limit_ms,
                symbol,
            )

        # 持久化
        if self.save_to_file:
            try:
                self._save_estimate(estimate)
            except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
                logger.error("[TCA-PreTrade] 保存预估记录失败: %s", e)

        # 否决日志
        if not approved:
            logger.warning(
                "[TCA-PreTrade] 订单被否决: %s (cost=%.2f bps, threshold=%.2f bps)",
                symbol,
                cost_bps,
                self.cost_threshold_bps,
            )
        else:
            logger.info(
                "[TCA-PreTrade] 订单通过: %s (cost=%.2f bps, tier=%s, latency=%.2fms)",
                symbol,
                cost_bps,
                tier,
                latency_ms,
            )

        return estimate

    # ------------------------------------------------------------
    # 批量预估
    # ------------------------------------------------------------
    def estimate_batch(
        self,
        orders: dict[str, dict[str, Any]],
        market_data_by_symbol: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, PreTradeEstimate]:
        """批量预估多个订单

        Args:
            orders: {symbol: order_dict}
            market_data_by_symbol: {symbol: market_data_dict}

        Returns:
            {symbol: PreTradeEstimate}
        """
        market_data_by_symbol = market_data_by_symbol or {}
        results: dict[str, PreTradeEstimate] = {}
        for symbol, order in orders.items():
            market_data = market_data_by_symbol.get(symbol, {})
            try:
                results[symbol] = self.estimate(order, market_data)
            except PreTradeEstimateError as e:
                logger.error("[TCA-PreTrade] 批量预估失败 %s: %s", symbol, e)
            except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
                # 单标的失败不影响其他
                logger.error("[TCA-PreTrade] 批量预估异常 %s: %s", symbol, e)
        return results

    # ------------------------------------------------------------
    # 决策汇总 (用于上层路由器判断)
    # ------------------------------------------------------------
    def filter_approved(
        self,
        orders: dict[str, dict[str, Any]],
        market_data_by_symbol: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, PreTradeEstimate]:
        """返回通过 TCA 预估的订单 (过滤被否决的)

        Args:
            orders: {symbol: order_dict}
            market_data_by_symbol: {symbol: market_data_dict}

        Returns:
            {symbol: PreTradeEstimate} 仅包含 approved=True 的
        """
        all_estimates = self.estimate_batch(orders, market_data_by_symbol)
        return {symbol: est for symbol, est in all_estimates.items() if est.approved}

    # ------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------
    def _save_estimate(self, estimate: PreTradeEstimate) -> Path:
        """保存预估记录到 JSONL 文件

        文件路径: reports/tca/estimate_{YYYY-MM-DD}.jsonl
        每行一条 JSON 记录
        """
        date_str = datetime.now().strftime("%Y-%m-%d")
        path = self.estimate_dir / f"estimate_{date_str}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(estimate.to_jsonl() + "\n")
        return path

    # ------------------------------------------------------------
    # 历史查询
    # ------------------------------------------------------------
    def get_history(
        self,
        date_str: str | None = None,
        symbol: str | None = None,
    ) -> list:
        """查询预估历史

        Args:
            date_str: 日期 (YYYY-MM-DD), 默认今天
            symbol: 标的过滤 (None=全部)

        Returns:
            预估记录列表
        """
        date_str = date_str or datetime.now().strftime("%Y-%m-%d")
        path = self.estimate_dir / f"estimate_{date_str}.jsonl"
        if not path.exists():
            return []

        records: list = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        if symbol is None or record.get("symbol") == symbol:
                            records.append(record)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.error("[TCA-PreTrade] 读取历史失败: %s", e)
        return records

    # ------------------------------------------------------------
    # 校准 (提供给 T3.5 调用)
    # ------------------------------------------------------------
    def calibrate_threshold(
        self,
        actual_costs_bps: list,
        percentile: float = 0.95,
        min_threshold: float = 10.0,
        max_threshold: float = 100.0,
    ) -> float:
        """根据实际成本校准否决阈值

        策略: 取实际成本的 percentile 分位数作为新阈值
        (T3.5 执行后归因会调用此方法, 形成 TCA 闭环)

        Args:
            actual_costs_bps: 最近 N 笔订单的实际成本 (bps)
            percentile: 分位数 (0-1), 默认 95% 分位
            min_threshold: 阈值下限 (避免过度否决)
            max_threshold: 阈值上限 (避免过度宽松)

        Returns:
            新的否决阈值 (bps)
        """
        if not actual_costs_bps:
            return self.cost_threshold_bps

        sorted_costs = sorted(actual_costs_bps)
        n = len(sorted_costs)
        idx = max(0, min(n - 1, int(n * percentile)))
        new_threshold = float(sorted_costs[idx])

        # 上下限约束
        new_threshold = max(min_threshold, min(max_threshold, new_threshold))

        old_threshold = self.cost_threshold_bps
        self.cost_threshold_bps = new_threshold
        logger.info(
            "[TCA-PreTrade] 阈值校准: %.2f → %.2f (基于 %d 笔订单, p%.0f)",
            old_threshold,
            new_threshold,
            n,
            percentile * 100,
        )
        return new_threshold


# ============================================================
# 便捷工厂函数
# ============================================================
def create_default_estimator(
    cost_threshold_bps: float = DEFAULT_COST_THRESHOLD_BPS,
) -> PreTradeEstimator:
    """创建默认配置的预估器 (便捷函数)

    Args:
        cost_threshold_bps: 否决阈值 (bps)

    Returns:
        PreTradeEstimator 实例
    """
    return PreTradeEstimator(
        cost_threshold_bps=cost_threshold_bps,
        save_to_file=True,
    )


def create_no_save_estimator(
    cost_threshold_bps: float = DEFAULT_COST_THRESHOLD_BPS,
) -> PreTradeEstimator:
    """创建不写文件的预估器 (用于测试或内存场景)

    Args:
        cost_threshold_bps: 否决阈值 (bps)

    Returns:
        PreTradeEstimator 实例 (save_to_file=False)
    """
    return PreTradeEstimator(
        cost_threshold_bps=cost_threshold_bps,
        save_to_file=False,
    )


# ============================================================
# CLI 入口 (手动测试用)
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="TCA 执行前预估器")
    parser.add_argument("--symbol", default="600276", help="标的代码")
    parser.add_argument("--side", default="BUY", choices=["BUY", "SELL"])
    parser.add_argument("--shares", type=int, default=10000)
    parser.add_argument("--price", type=float, default=50.0)
    parser.add_argument("--market-cap", type=float, default=800e8, help="市值 (元)")
    parser.add_argument("--adv", type=float, default=1e8, help="日均成交额")
    parser.add_argument("--vol", type=float, default=0.025, help="年化波动率")
    parser.add_argument("--threshold", type=float, default=30.0, help="否决阈值 (bps)")
    args = parser.parse_args()

    estimator = PreTradeEstimator(cost_threshold_bps=args.threshold)
    notional = args.shares * args.price
    est = estimator.estimate(
        order={
            "symbol": args.symbol,
            "side": args.side,
            "shares": args.shares,
            "price": args.price,
            "notional": notional,
            "market_cap": args.market_cap,
        },
        market_data={
            "adv": args.adv,
            "volatility": args.vol,
        },
    )
    logger.info(json.dumps(est.to_dict(), ensure_ascii=False, indent=2))
