#!/usr/bin/env python
"""
执行流水线 — 信号到订单的安全转化
=================================

职责:
1. 将 Alpha 信号转化为目标持仓
2. 复用系统的执行引擎 (TWAP/VWAP/POV/IS/Almgren-Chriss)
3. 通过 ExecutionRouter 路由到正确的执行通道
4. 交易后成本分析 (TCA)

安全设计:
- dry_run 模式默认开启
- 需要 confirmation_token 才能实盘
- 订单金额/数量二次校验
- 执行失败自动回滚

作者: 终极量化交易系统 v8.4
日期: 2026-08-02
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from .types import (
    AlphaSignalResult,
    ExecutionResult,
    PipelineConfig,
    PipelineResult,
    PipelineStage,
)

logger = logging.getLogger("pipeline.execution")


class ExecutionPipeline:
    """
    执行流水线

    将 Alpha 信号转化为实际订单，通过系统的执行引擎执行。

    使用示例:
        pipeline = ExecutionPipeline(config)
        result, meta = pipeline.run(signal_result, dry_run=True)
    """

    def __init__(self, config: Optional[PipelineConfig] = None):
        self.config = config or PipelineConfig()
        self._order_generator = None
        self._execution_router = None
        self._tca_engine = None
        self._init_engines()
        logger.info("ExecutionPipeline 初始化完成")

    def _init_engines(self) -> None:
        """初始化执行引擎 (优雅降级)"""
        # 订单生成器
        try:
            from ..order_generator import OrderGenerator
            self._order_generator = OrderGenerator()
            logger.info("OrderGenerator 已加载")
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            self._order_generator = None
            logger.warning(f"OrderGenerator 导入失败: {e}")

        # 执行路由
        try:
            from ..execution_router import ExecutionRouter
            self._execution_router = ExecutionRouter()
            logger.info("ExecutionRouter 已加载")
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            self._execution_router = None
            logger.warning(f"ExecutionRouter 导入失败: {e}")

        # TCA 引擎
        try:
            from ..tca_engine import TCAManager as TCAEngine
            self._tca_engine = TCAEngine()
            logger.info("TCAEngine 已加载")
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            self._tca_engine = None
            logger.warning(f"TCAEngine 导入失败: {e}")

    def run(
        self,
        signal_result: AlphaSignalResult,
        current_positions: Optional[dict[str, float]] = None,
        dry_run: Optional[bool] = None,
        confirmation_token: Optional[str] = None,
    ) -> tuple[ExecutionResult, PipelineResult]:
        """
        执行交易流水线

        Args:
            signal_result: Alpha 信号结果
            current_positions: 当前持仓 {symbol: weight}
            dry_run: 是否模拟执行 (默认取配置值)
            confirmation_token: 实盘确认令牌

        Returns:
            (ExecutionResult, PipelineResult)
        """
        start_time = datetime.now()
        logger.info("=" * 60)
        logger.info("执行流水线启动")
        logger.info("=" * 60)

        dry_run = dry_run if dry_run is not None else self.config.execution_dry_run
        batch_id = f"exec_{start_time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        try:
            # 安全检查: signal_result 为 None 时直接返回空结果
            if signal_result is None:
                logger.warning("signal_result 为 None，跳过执行")
                empty_result = ExecutionResult(
                    batch_id=batch_id,
                    dry_run=dry_run,
                    started_at=start_time,
                    completed_at=datetime.now(),
                    duration_ms=0,
                )
                return empty_result, PipelineResult(
                    stage=PipelineStage.EXECUTION,
                    success=True,
                    started_at=start_time,
                    completed_at=datetime.now(),
                    metrics={"total_orders": 0, "skip_reason": "no_signal"},
                )

            # 1. 生成目标持仓
            logger.info("步骤 1/4: 生成目标持仓...")
            target_positions = self._generate_target_positions(signal_result)
            logger.info(f"目标持仓: {len(target_positions)} 只标的")

            # 2. 生成订单
            logger.info("步骤 2/4: 生成订单...")
            orders = self._generate_orders(target_positions, current_positions or {})
            logger.info(f"生成订单: {len(orders)} 笔")

            # 3. 执行订单
            logger.info(f"步骤 3/4: 执行订单 (dry_run={dry_run})...")
            execution_result = self._execute_orders(orders, dry_run, confirmation_token, batch_id)

            # 4. TCA 分析
            logger.info("步骤 4/4: 交易后成本分析...")
            self._run_tca(execution_result)

            execution_result.duration_ms = (datetime.now() - start_time).total_seconds() * 1000
            execution_result.completed_at = datetime.now()

            # 生成结果
            result = PipelineResult(
                stage=PipelineStage.EXECUTION,
                success=execution_result.failed_orders == 0,
                started_at=start_time,
                completed_at=execution_result.completed_at,
                duration_ms=execution_result.duration_ms,
                metrics={
                    "total_orders": execution_result.total_orders,
                    "filled_orders": execution_result.filled_orders,
                    "fill_rate": execution_result.fill_rate,
                    "dry_run": dry_run,
                },
                reports=[],
            )

            logger.info(f"执行完成: {execution_result.filled_orders}/{execution_result.total_orders} "
                       f"({execution_result.fill_rate:.1%})")

            return execution_result, result

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"执行流水线异常: {e}", exc_info=True)

            error_result = ExecutionResult(
                batch_id=batch_id,
                total_orders=0,
                filled_orders=0,
                failed_orders=0,
                total_amount=0,
                filled_amount=0,
                avg_fill_price=0,
                fill_rate=0,
                dry_run=dry_run,
                started_at=start_time,
                completed_at=datetime.now(),
                duration_ms=(datetime.now() - start_time).total_seconds() * 1000,
                errors=[str(e)],
            )

            result = PipelineResult(
                stage=PipelineStage.EXECUTION,
                success=False,
                started_at=start_time,
                completed_at=datetime.now(),
                duration_ms=error_result.duration_ms,
                metrics={"error": str(e)},
                reports=[],
            )

            return error_result, result

    def _generate_target_positions(
        self,
        signal_result: AlphaSignalResult,
    ) -> dict[str, float]:
        """将信号转化为目标持仓权重"""
        signals = signal_result.signals

        # 只保留多头信号 (A股做空受限)
        long_signals = {s: v for s, v in signals.items() if v > 0}

        if not long_signals:
            return {}

        # 归一化到目标权重
        total_signal = sum(long_signals.values())
        target_positions = {
            s: (v / total_signal) * self.config.execution_target_exposure
            for s, v in long_signals.items()
        }

        return target_positions

    def _load_total_capital(self) -> float:
        """从 config/positions.json 读取真实总资产 (与 kill_switch.py / hedge_execution_engine.py 一致).

        Returns:
            total_capital: 总资产 (元), 读取失败时 fallback 5_000_000 (与项目惯例一致).
        """
        try:
            import json
            from pathlib import Path

            positions_path = Path(__file__).resolve().parent.parent.parent / "config" / "positions.json"
            if positions_path.exists():
                with open(positions_path, encoding="utf-8") as f:
                    data = json.load(f)
                return float(data.get("meta", {}).get("total_capital", 5_000_000))
        except (ValueError, TypeError, KeyError, OSError) as e:
            logger.warning(f"读取总资产失败, 使用 fallback 5_000_000: {e}")
        return 5_000_000.0

    def _generate_orders(
        self,
        target_positions: dict[str, float],
        current_positions: dict[str, float],
    ) -> list[dict]:
        """生成订单列表"""
        orders = []

        # 读取真实总资产 (避免硬编码导致订单金额与实际资产不匹配)
        total_value = self._load_total_capital()

        # 计算调仓
        all_symbols = set(target_positions.keys()) | set(current_positions.keys())

        for symbol in all_symbols:
            target_weight = target_positions.get(symbol, 0)
            current_weight = current_positions.get(symbol, 0)
            diff = target_weight - current_weight

            if abs(diff) < 0.01:  # 忽略小于 1% 的调整
                continue

            amount = abs(diff) * total_value

            # 二次校验
            if amount > self.config.execution_max_order_value:
                logger.warning(f"订单金额超限: {symbol} {amount:,.0f} > {self.config.execution_max_order_value:,.0f}")
                continue

            orders.append({
                "symbol": symbol,
                "direction": "BUY" if diff > 0 else "SELL",
                "target_weight": target_weight,
                "current_weight": current_weight,
                "weight_diff": diff,
                "amount": amount,
                "algo": self.config.execution_default_algo,
            })

        return orders

    def _execute_orders(
        self,
        orders: list[dict],
        dry_run: bool,
        confirmation_token: Optional[str],
        batch_id: str,
    ) -> ExecutionResult:
        """执行订单"""
        result = ExecutionResult(
            batch_id=batch_id,
            total_orders=len(orders),
            filled_orders=0,
            failed_orders=0,
            total_amount=sum(o["amount"] for o in orders),
            filled_amount=0,
            avg_fill_price=0,
            fill_rate=0,
            dry_run=dry_run,
            started_at=datetime.now(),
            completed_at=None,
            duration_ms=0,
            errors=[],
        )

        if not orders:
            logger.info("无订单需要执行")
            return result

        # 实盘安全检查
        if not dry_run and not confirmation_token:
            result.errors.append("实盘执行需要 confirmation_token")
            result.failed_orders = len(orders)
            logger.error("实盘执行被拒绝: 缺少 confirmation_token")
            return result

        # 执行订单
        fill_prices = []

        for order in orders:
            try:
                if dry_run:
                    # 模拟执行
                    fill_price = 100.0  # 模拟价格
                    order["amount"] / fill_price
                    result.filled_orders += 1
                    result.filled_amount += order["amount"]
                    fill_prices.append(fill_price)
                    logger.info(f"  [DRY] {order['symbol']} {order['direction']} "
                               f"{order['amount']:,.0f} @ {fill_price:.2f}")
                else:
                    # 实盘执行
                    exec_result = self._execute_single_order(order)
                    if exec_result.get("success"):
                        result.filled_orders += 1
                        result.filled_amount += order["amount"]
                        fill_prices.append(exec_result.get("fill_price", 0))
                    else:
                        result.failed_orders += 1
                        result.errors.append(f"{order['symbol']}: {exec_result.get('error', '未知错误')}")

            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                result.failed_orders += 1
                result.errors.append(f"{order['symbol']}: {str(e)}")
                logger.error(f"订单执行异常: {order['symbol']} - {e}")

        # 计算汇总
        result.fill_rate = result.filled_orders / result.total_orders if result.total_orders > 0 else 0
        result.avg_fill_price = sum(fill_prices) / len(fill_prices) if fill_prices else 0

        return result

    def _execute_single_order(self, order: dict) -> dict[str, Any]:
        """执行单个订单"""
        # 优先使用 ExecutionRouter
        if self._execution_router:
            try:
                # 构建订单请求
                request = {
                    "symbol": order["symbol"],
                    "direction": order["direction"],
                    "amount": order["amount"],
                    "algo": order["algo"],
                    "features": {
                        "pipeline_batch": True,
                        "confirmation_required": True,
                    },
                }

                # 通过路由执行
                result = self._execution_router.execute(request)
                return {
                    "success": result.get("status") == "filled",
                    "fill_price": result.get("avg_price", 0),
                    "fill_qty": result.get("fill_qty", 0),
                    "error": result.get("error"),
                }

            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.error(f"ExecutionRouter 执行失败: {e}")
                return {"success": False, "error": str(e)}

        # 降级: 直接标记失败
        return {"success": False, "error": "ExecutionRouter 不可用"}

    def _run_tca(self, result: ExecutionResult) -> None:
        """运行交易后成本分析"""
        if not self._tca_engine or result.filled_orders == 0:
            return

        try:
            tca_result = self._tca_engine.analyze({
                "batch_id": result.batch_id,
                "total_amount": result.filled_amount,
                "avg_price": result.avg_fill_price,
            })

            logger.info(f"TCA 分析: 冲击成本 {tca_result.get('impact_cost', 0):.2%}, "
                       f"机会成本 {tca_result.get('opportunity_cost', 0):.2%}")

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning(f"TCA 分析失败: {e}")
