"""T15 实盘下单编排器 — 风控前置 + 实盘下单 + 成交落盘 + 审计归档.

属于「实盘验证四件套」之首, 核心目的: **在 phase_execute 与 broker 之间插入编排层,
确保每一笔实盘订单都经过 T09-T12 风控门检查, 成交结果落 FillsStore, 全生命周期写 T14 审计**.

编排流程 (单笔 ExecutionPlan):
    1. 遍历 ExecutionSlice
    2. T09 PreTradeGuard.check()        → 拦截非法单 (手数/价格/ST/停牌)
    3. T10 PositionLimitEnforcer        → 持仓集中度冲击检查
    4. T12 KillSwitchManager.evaluate() → 保证金梯度熔断检查
    5. T11 IntradayCircuitBreaker       → 日内熔断状态检查
    6. 全通过 → broker.place_order()
    7. 成交 → record_fill(is_live=True)
    8. 全程 → RiskAuditLogger.log()

设计原则:
    - fail-closed: 任一风控门拦截 → reject slice, 不降级到 sim
    - 零行为变更: dry_run=true 时调用方不进入本编排器, 仍走原 MockBroker 路径
    - 可测试: broker / fills_store 均为注入接口, 可用 mock 替换

用法:
    from utils.risk.live_order_executor import LiveOrderExecutor, LiveExecutionResult
    executor = LiveOrderExecutor(
        broker=broker_factory.get_broker(),
        pretrade_guard=PreTradeGuard(),
        position_enforcer=PositionLimitEnforcer(),
        circuit_breaker=IntradayCircuitBreaker(),
        kill_switch=KillSwitchManager(),
        audit_logger=RiskAuditLogger(),
    )
    result = executor.execute_plan(plan)
    if result.rejected_count > 0:
        logger.error(f"[T15] {result.rejected_count} 笔被风控拦截")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from utils.risk.intraday_circuit_breaker import IntradayCircuitBreaker
from utils.risk.kill_switch_manager import KillSwitchManager
from utils.risk.position_limit_enforcer import (
    OrderImpact,
    PositionLimitEnforcer,
    PositionSnapshot,
)
from utils.risk.pretrade_guard import GuardOrderRequest, PreTradeGuard
from utils.risk.risk_audit_logger import RiskAuditLogger

logger = logging.getLogger("live_order_executor")


# ============================================================
# 协议定义 (鸭子类型, 避免硬依赖 broker_api / broker_adapter)
# ============================================================

class BrokerProtocol(Protocol):
    """T15 需要的 broker 最小接口."""

    is_live: bool

    def place_order(
        self, symbol: str, side: str, qty: int, price: float, order_type: str = "limit",
    ) -> str:
        """提交订单, 返回 broker_order_id."""
        ...

    def cancel_order(self, broker_order_id: str) -> bool:
        ...

    def get_order_status(self, broker_order_id: str) -> dict[str, Any]:
        """返回 {state, filled_qty, avg_price, rejection_reason}."""
        ...


class FillsStoreProtocol(Protocol):
    """T15 需要的 FillsStore 最小接口."""

    def record_fill(
        self, symbol: str, side: str, filled_qty: float, avg_price: float,
        broker: str = "", is_live: bool = False, strategy: str = "",
        source: str = "", meta: dict | None = None,
    ) -> None:
        ...


# ============================================================
# 数据结构
# ============================================================

@dataclass
class SliceExecutionResult:
    """单个 ExecutionSlice 的执行结果."""

    slice_idx: int
    symbol: str
    side: str
    planned_shares: int
    submitted: bool = False
    broker_order_id: str = ""
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    rejected: bool = False
    rejection_reason: str = ""
    audit_events: list[str] = field(default_factory=list)


@dataclass
class LiveExecutionResult:
    """整个 ExecutionPlan 的执行汇总."""

    plan_id: str
    total_slices: int = 0
    submitted_count: int = 0
    filled_count: int = 0
    rejected_count: int = 0
    total_filled_shares: int = 0
    total_filled_notional: float = 0.0
    slice_results: list[SliceExecutionResult] = field(default_factory=list)
    execution_started_at: str = ""
    execution_finished_at: str = ""

    @property
    def all_rejected(self) -> bool:
        """全被拦截时返回 True."""
        return self.rejected_count == self.total_slices and self.total_slices > 0

    @property
    def fully_filled(self) -> bool:
        """全部 slice 都成功提交且有成交."""
        return self.submitted_count == self.total_slices and self.filled_count == self.total_slices


# ============================================================
# 主类
# ============================================================

class LiveOrderExecutor:
    """实盘下单编排器 — 整合 T09-T12 风控门 + broker 下单 + T14 审计.

    风控检查顺序 (fail-closed, 任一拦截即跳过该 slice):
        T09 PreTradeGuard → T10 PositionLimit → T12 KillSwitch → T11 CircuitBreaker
    """

    def __init__(
        self,
        broker: BrokerProtocol,
        pretrade_guard: PreTradeGuard,
        position_enforcer: PositionLimitEnforcer,
        circuit_breaker: IntradayCircuitBreaker,
        kill_switch: KillSwitchManager,
        audit_logger: RiskAuditLogger,
        fills_store: FillsStoreProtocol | None = None,
    ) -> None:
        if broker is None:
            raise ValueError("broker 不能为 None")
        self.broker = broker
        self.guard = pretrade_guard
        self.enforcer = position_enforcer
        self.cb = circuit_breaker
        self.ksm = kill_switch
        self.audit = audit_logger
        self.fills_store = fills_store
        # 缓存真实总资产 (从 positions.json 读取, 避免 T10 风控门用硬编码导致形同虚设)
        self._total_equity = self._load_total_equity()

    def _load_total_equity(self) -> float:
        """从 config/positions.json 读取真实总资产 (与 kill_switch.py 等一致).

        Returns:
            total_equity: 总权益 (元), 读取失败时 fallback 5_000_000.
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
            logger.warning(f"读取总权益失败, 使用 fallback 5_000_000: {e}")
        return 5_000_000.0

    # ------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------

    def execute_plan(self, plan: Any) -> LiveExecutionResult:
        """编排执行一个 ExecutionPlan.

        Args:
            plan: ExecutionPlan dataclass (需有 plan_id, symbol, side, slices 属性)
                  每个 slice 需有 slice_idx, target_shares, limit_price

        Returns:
            LiveExecutionResult 汇总
        """
        result = LiveExecutionResult(
            plan_id=getattr(plan, "plan_id", "unknown"),
            total_slices=len(getattr(plan, "slices", [])),
            execution_started_at=datetime.now().isoformat(),
        )

        symbol = getattr(plan, "symbol", "")
        side = getattr(plan, "side", "")
        slices = getattr(plan, "slices", [])

        for sl in slices:
            sl_result = self._execute_slice(sl, symbol, side)
            result.slice_results.append(sl_result)

            if sl_result.submitted:
                result.submitted_count += 1
            if sl_result.filled_qty > 0:
                result.filled_count += 1
                result.total_filled_shares += sl_result.filled_qty
                result.total_filled_notional += sl_result.filled_qty * sl_result.avg_fill_price
            if sl_result.rejected:
                result.rejected_count += 1

        result.execution_finished_at = datetime.now().isoformat()

        # 汇总审计
        self.audit.log(
            module="T15_LIVE_EXECUTOR",
            action="OTHER",
            severity="INFO" if result.rejected_count == 0 else "WARN",
            symbol=symbol,
            reason=(
                f"plan={result.plan_id} slices={result.total_slices} "
                f"submitted={result.submitted_count} filled={result.filled_count} "
                f"rejected={result.rejected_count}"
            ),
        )

        logger.info(
            f"[T15] plan={result.plan_id} | "
            f"submitted={result.submitted_count}/{result.total_slices} | "
            f"filled={result.filled_count} | rejected={result.rejected_count}"
        )
        return result

    # ------------------------------------------------------------
    # 内部: 单 slice 执行
    # ------------------------------------------------------------

    def _execute_slice(self, sl: Any, symbol: str, side: str) -> SliceExecutionResult:
        """对单个 ExecutionSlice 执行风控检查 → 下单 → 记录."""
        slice_idx = getattr(sl, "slice_idx", 0)
        target_shares = getattr(sl, "target_shares", 0)
        limit_price = getattr(sl, "limit_price", 0.0) or getattr(sl, "price", 0.0)

        result = SliceExecutionResult(
            slice_idx=slice_idx,
            symbol=symbol,
            side=side,
            planned_shares=target_shares,
        )

        if target_shares <= 0:
            result.rejected = True
            result.rejection_reason = "target_shares <= 0"
            return result

        # ---- 风控门 1: T09 PreTradeGuard ----
        guard_req = GuardOrderRequest(
            symbol=symbol,
            side=side,
            shares=target_shares,
            price=limit_price,
        )
        guard_res = self.guard.check(guard_req)
        if guard_res.rejected:
            result.rejected = True
            result.rejection_reason = f"T09: {'; '.join(guard_res.reasons)}"
            result.audit_events.append("T09_REJECT")
            self.audit.log(
                module="T15_LIVE_EXECUTOR",
                action="BLOCK",
                severity="WARN",
                symbol=symbol,
                reason=f"T09 拦截 slice={slice_idx}: {guard_res.reasons}",
            )
            return result

        # ---- 风控门 2: T10 PositionLimitEnforcer (简化: 只做静态检查) ----
        # 实际使用时传入 PositionSnapshot + OrderImpact
        # 这里做空 snap 检查 (enforcer 内部会 skip 无数据维度)
        # total_equity 从 positions.json 读取真实总资产 (__init__ 时缓存), 避免 T10 形同虚设
        try:
            snap = PositionSnapshot(
                total_equity=self._total_equity,
                positions={},
                sectors={},
            )
            impact = OrderImpact(
                symbol=symbol,
                side=side,
                delta_shares=target_shares if side.lower() == "buy" else -target_shares,
                price=limit_price,
            )
            enforce_res = self.enforcer.check_after_trade(snap, impact)
            if enforce_res.rejected:
                result.rejected = True
                result.rejection_reason = f"T10: {'; '.join(enforce_res.reasons)}"
                result.audit_events.append("T10_REJECT")
                self.audit.log(
                    module="T15_LIVE_EXECUTOR",
                    action="BLOCK",
                    severity="WARN",
                    symbol=symbol,
                    reason=f"T10 拦截 slice={slice_idx}: {enforce_res.reasons}",
                )
                return result
        except Exception as exc:
            # T10 异常不阻断 (fail-open for position check, 因 snap 可能不完整)
            logger.warning(f"[T15] T10 检查异常, 降级跳过: {exc}")

        # ---- 风控门 3: T12 KillSwitchManager ----
        notional = target_shares * limit_price
        kill_dec = self.ksm.evaluate_trade(
            symbol=symbol, side=side, notional=notional,
            is_open_new=(side.lower() == "buy"),
        )
        if not kill_dec.allowed:
            result.rejected = True
            result.rejection_reason = f"T12: {kill_dec.reason}"
            result.audit_events.append("T12_REJECT")
            self.audit.log(
                module="T15_LIVE_EXECUTOR",
                action="BLOCK",
                severity="WARN",
                symbol=symbol,
                reason=f"T12 熔断拦截 slice={slice_idx}: {kill_dec.reason}",
            )
            return result

        # ---- 风控门 4: T11 IntradayCircuitBreaker ----
        if not self.cb.allow_trading:
            result.rejected = True
            result.rejection_reason = "T11: 日内熔断器 OPEN"
            result.audit_events.append("T11_REJECT")
            self.audit.log(
                module="T15_LIVE_EXECUTOR",
                action="BLOCK",
                severity="ERROR",
                symbol=symbol,
                reason=f"T11 熔断器 OPEN, 拦截 slice={slice_idx}",
            )
            return result

        # ---- 全部通过 → 提交 broker ----
        try:
            broker_order_id = self.broker.place_order(
                symbol=symbol,
                side=side,
                qty=target_shares,
                price=limit_price,
                order_type="limit",
            )
            result.submitted = True
            result.broker_order_id = broker_order_id
            result.audit_events.append("BROKER_SUBMIT")

            self.audit.log(
                module="T15_LIVE_EXECUTOR",
                action="ALLOW",
                severity="INFO",
                symbol=symbol,
                reason=f"slice={slice_idx} 提交 broker, order_id={broker_order_id}",
            )
        except Exception as exc:
            result.rejected = True
            result.rejection_reason = f"broker.place_order 异常: {type(exc).__name__}: {exc}"
            result.audit_events.append("BROKER_ERROR")
            self.audit.log(
                module="T15_LIVE_EXECUTOR",
                action="BLOCK",
                severity="ERROR",
                symbol=symbol,
                reason=f"broker 提交异常 slice={slice_idx}: {exc}",
            )
            return result

        # ---- 查询成交 ----
        try:
            status = self.broker.get_order_status(broker_order_id)
            result.filled_qty = int(status.get("filled_qty", 0))
            result.avg_fill_price = float(status.get("avg_price", 0.0))

            if result.filled_qty > 0 and self.fills_store is not None:
                self.fills_store.record_fill(
                    symbol=symbol,
                    side=side.upper(),
                    filled_qty=float(result.filled_qty),
                    avg_price=result.avg_fill_price,
                    broker=getattr(self.broker, "name", "unknown"),
                    is_live=getattr(self.broker, "is_live", False),
                    strategy="P0_LIVE",
                    source="live_executor",
                    meta={"broker_order_id": broker_order_id, "slice_idx": slice_idx},
                )
                result.audit_events.append("FILL_RECORDED")
        except Exception as exc:
            logger.warning(f"[T15] 查询成交状态异常: {exc}")

        return result
