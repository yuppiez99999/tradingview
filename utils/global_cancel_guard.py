"""全局撤单 Guard (Global Cancel Guard)
====================================
T12 (2026-07-28): 一键撤销所有待执行订单.

设计背景:
    极端行情或系统故障时, 需要快速撤销所有待执行订单, 防止错误订单成交.
    现有 KillSwitch 处理 L1/L2/L3 熔断, 但没有独立的"一键撤单"功能.
    GlobalCancelGuard 提供独立的撤单能力, 可被 daily_workflow 或人工触发.

撤单范围:
    - 所有 status=PENDING 的订单
    - 所有 status=PARTIALLY_FILLED 的订单 (撤销未成交部分)
    - 不撤销 status=FILLED / status=CANCELLED / status=REJECTED 的订单

触发方式:
    1. 人工触发: daily_workflow 调用 guard.cancel_all_orders(broker)
    2. 自动触发: 监控到异常信号 (如数据源中断、行情异常) 时自动触发

用法:
    from utils.global_cancel_guard import GlobalCancelGuard
    guard = GlobalCancelGuard()
    result = guard.cancel_all_orders(broker)
    if result["success"]:
        logger.info(f"已撤销 {result['cancelled_count']} 笔订单")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger("global_cancel_guard")


@dataclass
class CancelResult:
    """撤单结果."""

    success: bool
    total_orders: int
    cancelled_count: int
    failed_count: int
    skipped_count: int
    cancelled_order_ids: list[str] = field(default_factory=list)
    failed_order_ids: list[str] = field(default_factory=list)
    skipped_order_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    timestamp: str = ""
    trigger_reason: str = ""


class GlobalCancelGuard:
    """全局撤单 Guard — 一键撤销所有待执行订单.

    撤单逻辑:
        1. 从 broker 获取所有订单
        2. 过滤出可撤销的订单 (PENDING / PARTIALLY_FILLED)
        3. 逐笔调用 broker.cancel(order)
        4. 记录撤单结果, 返回汇总
    """

    # 可撤销的订单状态
    CANCELABLE_STATUSES = {"PENDING", "PARTIALLY_FILLED", "NEW", "ACCEPTED"}

    # 不可撤销的订单状态
    NON_CANCELABLE_STATUSES = {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}

    def __init__(self, max_retries: int = 2, retry_delay_sec: float = 0.5):
        """初始化全局撤单 Guard.

        Args:
            max_retries: 单笔撤单失败后最大重试次数, 默认 2
            retry_delay_sec: 重试间隔 (秒), 默认 0.5
        """
        self.max_retries = max_retries
        self.retry_delay_sec = retry_delay_sec

    def cancel_all_orders(
        self,
        broker: Any,
        trigger_reason: str = "manual",
        symbols: list[str] | None = None,
    ) -> CancelResult:
        """撤销所有待执行订单.

        Args:
            broker: 券商接口实例 (必须有 orders 属性和 cancel 方法)
            trigger_reason: 触发原因 (manual/auto_data_source_down/auto_market_crash)
            symbols: 只撤销指定标的的订单, None 表示撤销所有

        Returns:
            CancelResult 汇总
        """
        timestamp = datetime.now().isoformat()
        errors: list[str] = []

        # 1. 检查 broker 连接状态
        if not hasattr(broker, "is_connected") or not broker.is_connected:
            logger.error("[GlobalCancelGuard] broker 未连接, 无法撤单")
            return CancelResult(
                success=False,
                total_orders=0,
                cancelled_count=0,
                failed_count=0,
                skipped_count=0,
                errors=["broker_not_connected"],
                timestamp=timestamp,
                trigger_reason=trigger_reason,
            )

        # 2. 获取所有订单
        orders = getattr(broker, "orders", {})
        if isinstance(orders, dict):
            order_list = list(orders.values())
        elif isinstance(orders, list):
            order_list = orders
        else:
            order_list = []

        total = len(order_list)
        if total == 0:
            logger.info("[GlobalCancelGuard] 无待撤销订单")
            return CancelResult(
                success=True,
                total_orders=0,
                cancelled_count=0,
                failed_count=0,
                skipped_count=0,
                timestamp=timestamp,
                trigger_reason=trigger_reason,
            )

        # 3. 分类订单
        cancelable: list[Any] = []
        skipped: list[str] = []
        for order in order_list:
            order_id = self._get_order_id(order)
            status = self._get_order_status(order)

            # 按 symbols 过滤
            if symbols is not None:
                order_symbol = self._get_order_symbol(order)
                if order_symbol not in symbols:
                    skipped.append(order_id)
                    continue

            if status in self.CANCELABLE_STATUSES:
                cancelable.append(order)
            else:
                skipped.append(order_id)

        # 4. 逐笔撤销
        cancelled: list[str] = []
        failed: list[str] = []

        for order in cancelable:
            order_id = self._get_order_id(order)
            success = self._cancel_with_retry(broker, order)
            if success:
                cancelled.append(order_id)
            else:
                failed.append(order_id)
                errors.append(f"cancel_failed: {order_id}")

        # 5. 汇总
        result = CancelResult(
            success=len(failed) == 0,
            total_orders=total,
            cancelled_count=len(cancelled),
            failed_count=len(failed),
            skipped_count=len(skipped),
            cancelled_order_ids=cancelled,
            failed_order_ids=failed,
            skipped_order_ids=skipped,
            errors=errors,
            timestamp=timestamp,
            trigger_reason=trigger_reason,
        )

        logger.warning(
            "[GlobalCancelGuard] 撤单完成: 总计=%d, 撤销=%d, 失败=%d, 跳过=%d, 原因=%s",
            total,
            len(cancelled),
            len(failed),
            len(skipped),
            trigger_reason,
        )

        return result

    def _cancel_with_retry(self, broker: Any, order: Any) -> bool:
        """带重试的撤单.

        Args:
            broker: 券商接口
            order: 订单对象

        Returns:
            是否撤销成功
        """
        import time

        for attempt in range(self.max_retries + 1):
            try:
                result = broker.cancel(order)
                if result is True or (isinstance(result, dict) and result.get("success")):
                    return True
                # cancel 返回 False, 重试
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay_sec)
                    continue
                return False
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001  # broker API 异常类型不可预知
                logger.debug(
                    "[GlobalCancelGuard] 撤单失败 attempt=%d: %s, error=%s",
                    attempt + 1,
                    self._get_order_id(order),
                    e,
                )
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay_sec)
                    continue
                return False

        return False

    def _get_order_id(self, order: Any) -> str:
        """从订单对象获取 order_id."""
        if isinstance(order, dict):
            return str(order.get("order_id", order.get("id", "unknown")))
        return str(getattr(order, "order_id", getattr(order, "id", "unknown")))

    def _get_order_status(self, order: Any) -> str:
        """从订单对象获取 status."""
        if isinstance(order, dict):
            return str(order.get("status", "UNKNOWN")).upper()
        return str(getattr(order, "status", "UNKNOWN")).upper()

    def _get_order_symbol(self, order: Any) -> str:
        """从订单对象获取 symbol."""
        if isinstance(order, dict):
            return str(order.get("symbol", ""))
        return str(getattr(order, "symbol", ""))


__all__ = ["CancelResult", "GlobalCancelGuard"]
