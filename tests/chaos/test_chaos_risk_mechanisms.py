"""Chaos 灾难演练 — 真实风控机制覆盖 (T1 补齐, 2026-09-02).

现有 test_chaos_trading.py 覆盖六场景探针级路径; 本文件直接驱动真实机制:
  - T16 OrderLifecycleTracker: QMT 断开轮询不崩溃 + 超时孤儿单检测
  - T11 IntradayCircuitBreaker: 连续失败熔断 + 冷却恢复 + 再熔断
  - T12 KillSwitchManager: 三级熔断开平仓语义
  - 联动: FaultInjector qmt_down + T11 熔断吸收

全部零侵入 (不改生产模块), 可被 CI 稳定执行 (无真实网络/账户).
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.risk.intraday_circuit_breaker import (  # noqa: E402
    CBState,
    IntradayCircuitBreaker,
)
from utils.risk.kill_switch_manager import (  # noqa: E402
    KillLevel,
    KillSwitchManager,
)
from utils.risk.order_lifecycle_tracker import (  # noqa: E402
    OrderLifecycleTracker,
    OrderState,
)
from utils.risk.risk_audit_logger import RiskAuditLogger  # noqa: E402


# ============================================================
# 桩
# ============================================================
class SpyAudit(RiskAuditLogger):
    """审计桩: 记录 log 调用供断言, 落盘走 tmp_path 隔离."""

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(audit_dir=tmp_path / "audit")
        self.calls: list[dict] = []

    def log(self, **kwargs) -> None:  # noqa: D102
        self.calls.append(kwargs)
        super().log(**kwargs)


class QmtDownBroker:
    """QMT 断开 broker: 状态查询与撤单均抛 ConnectionError."""

    def get_order_status(self, broker_order_id: str) -> dict:
        raise ConnectionError("QMT 连接断开, 无法查询委托状态")

    def cancel_order(self, broker_order_id: str) -> bool:
        raise ConnectionError("QMT 连接断开, 无法撤单")


def _make_tracker(tmp_path: Path, broker, timeout_sec: int = 30) -> OrderLifecycleTracker:
    return OrderLifecycleTracker(
        broker=broker, audit_logger=SpyAudit(tmp_path), timeout_sec=timeout_sec
    )


# ============================================================
# T16: QMT 断开 — 轮询不崩溃, 订单保持受跟踪
# ============================================================
class TestT16QmtDown:
    def test_poll_with_broker_down_no_crash(self, tmp_path):
        """断开时 poll_once 必须吞异常不崩溃, 订单仍在跟踪 (不静默丢失)."""
        tracker = _make_tracker(tmp_path, QmtDownBroker())
        tracker.register("o1", "b1", "510300.SH", "buy", 100)
        tracker.poll_once()  # 不应抛出
        assert tracker.get_state("o1") == OrderState.SUBMITTED

    def test_timeout_marks_orphaned_no_active_leftover(self, tmp_path):
        """QMT 断开 + 超时: 订单必须被标记 ORPHANED, 不得残留活跃孤儿单."""
        tracker = _make_tracker(tmp_path, QmtDownBroker(), timeout_sec=30)
        tracker.register("o2", "b2", "510500.SH", "buy", 200)
        # 快进超时 (不真实 sleep): 直接把 deadline 置于过去
        tracked = tracker.get_all()[0]
        tracked.timeout_deadline = datetime.now() - timedelta(seconds=1)
        tracker.poll_once()
        assert tracker.get_state("o2") == OrderState.ORPHANED
        assert tracker.get_all_active() == [], "不得残留活跃孤儿单"

    def test_negative_control_no_timeout_not_orphaned(self, tmp_path):
        """负控制: 未超时的订单不得被误标 ORPHANED (验证用例敏感性)."""
        tracker = _make_tracker(tmp_path, QmtDownBroker(), timeout_sec=30)
        tracker.register("o3", "b3", "588000.SH", "buy", 100)
        tracker.poll_once()
        assert tracker.get_state("o3") == OrderState.SUBMITTED

    def test_orphan_has_audit_trace(self, tmp_path):
        """孤儿单转移必须留审计 (T16_LIFECYCLE 模块留痕)."""
        tracker = _make_tracker(tmp_path, QmtDownBroker(), timeout_sec=30)
        tracker.register("o4", "b4", "159915.SZ", "buy", 100)
        tracked = tracker.get_all()[0]
        tracked.timeout_deadline = datetime.now() - timedelta(seconds=1)
        tracker.poll_once()
        assert tracker.get_state("o4") == OrderState.ORPHANED
        assert any(c.get("module") == "T16_LIFECYCLE" for c in tracker.audit.calls)
