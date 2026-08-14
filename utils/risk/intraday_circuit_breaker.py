"""T11 盘中断路器 — 盘中异常计数/回撤/波动率触发的链路熔断, 避免系统雪崩.

属于「不崩风控六件套」第 3 位, 核心目的: **当连续异常发生时, 主动暂停新下单而不是让错误放大**.

三个独立触发条件 (任一达到即触发 OPEN 状态):
    1. CONSECUTIVE_FAIL : 连续 N 次下单/执行失败 (默认 5 次) — 对 QMT/券商接口错误链的自我保护
    2. INTRADAY_DRAWDOWN: 组合日内最大回撤 > X% (默认 3%) — 防止极端日内行情持续下错单
    3. REALIZED_VOL_BURST: 近 N 笔已实现波动率年化 > Y% (默认 50%) — 行情跳变时段主动降速

状态机 (CLOSED → OPEN → HALF_OPEN → CLOSED), 支持冷却期自动 HALF_OPEN,
也支持手动 close() / trip() 控制. 与 kill_switch 三级熔断互补:
    - kill_switch (T12): 基于保证金/风控硬指标, 一旦触发强平. L1/L2/L3 分级
    - circuit_breaker (T11): 基于失败计数/日内波动, 只阻断「新下单」, 不主动平仓

用法:
    from utils.risk.intraday_circuit_breaker import IntradayCircuitBreaker, CBState
    cb = IntradayCircuitBreaker(consecutive_fail_threshold=5)
    # 每次下单失败调用 record_failure(), 成功调用 record_success()
    cb.record_failure("broker_timeout")
    if cb.state == CBState.OPEN:
        logger.warning("[T11] 断路器已打开, 跳过新订单")
        return
    # 每次交易后更新 PnL 回撤 (可选, 用于条件 2/3)
    cb.update_intraday_pnl(current_pnl_pct=-0.032)
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger("intraday_cb")


class CBState(str, Enum):
    CLOSED = "CLOSED"          # 正常, 允许交易
    OPEN = "OPEN"              # 熔断, 阻断新交易
    HALF_OPEN = "HALF_OPEN"    # 半开, 试探恢复, 若再失败立即回到 OPEN


@dataclass
class CBMetrics:
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    last_failure_reason: str = ""
    last_failure_at: str = ""
    intraday_max_drawdown_pct: float = 0.0
    current_drawdown_pct: float = 0.0
    realized_vol_annualized: float = 0.0
    trip_count: int = 0
    last_trip_at: str = ""
    last_trip_reasons: list[str] = field(default_factory=list)


class IntradayCircuitBreaker:
    """T11 盘中断路器 — 失败计数 + 日内回撤 + 波动率 三重触发."""

    def __init__(
        self,
        consecutive_fail_threshold: int = 5,
        intraday_dd_pct: float = 0.03,
        realized_vol_annual_pct: float = 0.50,
        vol_window: int = 20,
        cooloff_seconds: int = 120,
        half_open_max_failures: int = 1,
    ) -> None:
        if consecutive_fail_threshold < 1:
            raise ValueError("consecutive_fail_threshold ≥ 1")
        if not (0 < intraday_dd_pct <= 1.0):
            raise ValueError("intraday_dd_pct ∈ (0, 1]")
        if not (0 < realized_vol_annual_pct):
            raise ValueError("realized_vol_annual_pct > 0")
        if vol_window < 2:
            raise ValueError("vol_window ≥ 2")
        if cooloff_seconds < 1:
            raise ValueError("cooloff_seconds ≥ 1")

        self._state: CBState = CBState.CLOSED
        self.consecutive_fail_threshold = consecutive_fail_threshold
        self.intraday_dd_pct = intraday_dd_pct
        self.realized_vol_annual_pct = realized_vol_annual_pct
        self.vol_window = vol_window
        self.cooloff = timedelta(seconds=cooloff_seconds)
        self.half_open_max_failures = half_open_max_failures

        self._metrics = CBMetrics()
        self._opened_at: datetime | None = None
        self._peak_pnl_pct: float = 0.0
        self._recent_returns: deque[float] = deque(maxlen=vol_window)
        self._half_open_failures: int = 0
        self._opened_reasons: list[str] = []

    # ------------------------------------------------------------
    # 公共属性 / 主检查
    # ------------------------------------------------------------

    @property
    def state(self) -> CBState:
        """读取状态前检查冷却期是否已到期 (CLOSED/OPEN → HALF_OPEN)."""
        if self._state == CBState.OPEN and self._opened_at is not None:
            if datetime.now() - self._opened_at >= self.cooloff:
                self._state = CBState.HALF_OPEN
                self._half_open_failures = 0
                logger.info(
                    f"[T11] 冷却期 {int(self.cooloff.total_seconds())}s 已过, "
                    f"状态 OPEN → HALF_OPEN"
                )
        return self._state

    @property
    def allow_trading(self) -> bool:
        """是否允许交易 (CLOSED/HALF_OPEN = True, OPEN = False)."""
        return self.state in (CBState.CLOSED, CBState.HALF_OPEN)

    @property
    def metrics(self) -> CBMetrics:
        return self._metrics

    def snapshot(self) -> dict:
        m = self._metrics
        return {
            "state": self.state.value,
            "consecutive_failures": m.consecutive_failures,
            "consecutive_fail_threshold": self.consecutive_fail_threshold,
            "total_failures": m.total_failures,
            "total_successes": m.total_successes,
            "last_failure_reason": m.last_failure_reason,
            "last_failure_at": m.last_failure_at,
            "intraday_max_drawdown_pct": round(m.intraday_max_drawdown_pct, 6),
            "drawdown_threshold_pct": round(self.intraday_dd_pct, 6),
            "realized_vol_annualized": round(m.realized_vol_annualized, 6),
            "vol_threshold_annualized": round(self.realized_vol_annual_pct, 6),
            "trip_count": m.trip_count,
            "last_trip_at": m.last_trip_at,
            "last_trip_reasons": list(m.last_trip_reasons),
        }

    # ------------------------------------------------------------
    # 记录接口 (调用方在主链路嵌入)
    # ------------------------------------------------------------

    def record_success(self) -> None:
        m = self._metrics
        m.total_successes += 1
        m.consecutive_failures = 0
        if self._state == CBState.HALF_OPEN:
            # HALF_OPEN 下成功 → 恢复 CLOSED
            self._state = CBState.CLOSED
            self._opened_at = None
            self._half_open_failures = 0
            logger.info("[T11] HALF_OPEN 成功, 状态 → CLOSED")

    def record_failure(self, reason: str = "unknown") -> None:
        m = self._metrics
        now = datetime.now()
        m.total_failures += 1
        m.consecutive_failures += 1
        m.last_failure_reason = reason
        m.last_failure_at = now.isoformat(timespec="seconds")

        if self._state == CBState.HALF_OPEN:
            self._half_open_failures += 1
            if self._half_open_failures >= self.half_open_max_failures:
                self._trip(reason_prefix="HALF_OPEN_FAIL")
            return

        # 条件 1: 连续失败
        if m.consecutive_failures >= self.consecutive_fail_threshold:
            self._trip(
                reason_prefix=(
                    f"CONSECUTIVE_FAIL={m.consecutive_failures}/"
                    f"{self.consecutive_fail_threshold}"
                )
            )

    def update_intraday_pnl(self, current_pnl_pct: float) -> None:
        """更新日内收益, 用于回撤/波动率触发.

        Args:
            current_pnl_pct: 当日收益率, 小数 (如 0.01 = +1%).
        """
        m = self._metrics
        self._peak_pnl_pct = max(self._peak_pnl_pct, current_pnl_pct)
        dd = self._peak_pnl_pct - current_pnl_pct
        m.current_drawdown_pct = dd
        if dd > m.intraday_max_drawdown_pct:
            m.intraday_max_drawdown_pct = dd

        # 记录回报 (用于波动率计算)
        self._recent_returns.append(current_pnl_pct)
        if len(self._recent_returns) >= 2:
            m.realized_vol_annualized = self._compute_annualized_vol()

        triggered: list[str] = []
        if dd > self.intraday_dd_pct:
            triggered.append(
                f"DRAWDOWN={dd:.4%} > {self.intraday_dd_pct:.4%}"
            )
        if m.realized_vol_annualized > self.realized_vol_annual_pct:
            triggered.append(
                f"VOL_BURST={m.realized_vol_annualized:.4%} > "
                f"{self.realized_vol_annual_pct:.4%}"
            )
        if triggered:
            self._trip(reason_prefix=" | ".join(triggered))

    # ------------------------------------------------------------
    # 手动控制
    # ------------------------------------------------------------

    def trip(self, reason: str = "manual") -> None:
        """手动强制打开断路器."""
        self._trip(reason_prefix=f"MANUAL({reason})")

    def close(self) -> None:
        """手动复位 (仅运维场景使用, 会清零计数 + 回撤峰值)."""
        self._state = CBState.CLOSED
        self._opened_at = None
        self._half_open_failures = 0
        self._metrics = CBMetrics()
        self._peak_pnl_pct = 0.0
        self._recent_returns.clear()
        logger.warning("[T11] 断路器已手动复位 → CLOSED")

    # ------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------

    def _trip(self, reason_prefix: str) -> None:
        if self._state == CBState.OPEN:
            return  # 已打开, 避免重复刷屏
        self._state = CBState.OPEN
        self._opened_at = datetime.now()
        m = self._metrics
        m.trip_count += 1
        m.last_trip_at = self._opened_at.isoformat(timespec="seconds")
        m.last_trip_reasons = [reason_prefix]
        self._opened_reasons = [reason_prefix]
        logger.error(
            f"[T11] 🔴 断路器 OPEN — 新交易已阻断. 触发原因: {reason_prefix}. "
            f"冷却 {int(self.cooloff.total_seconds())}s 后进入 HALF_OPEN."
        )

    def _compute_annualized_vol(self) -> float:
        """简单 realized vol, 假设样本为日频 (252 交易日年化)."""
        rets = list(self._recent_returns)
        if len(rets) < 2:
            return 0.0
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var) * math.sqrt(252)
