# T1 Chaos 灾难演练补齐 —— 真实风控机制覆盖 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐 Chaos 六场景对真实风控机制（T11 熔断 / T12 KillSwitch / T16 孤儿单检测）的演练覆盖，兑现《v8.7 Production Edition 架构升级方案》T1 验收标准。

**Architecture:** 纯测试资产（零生产链路侵入）。现有 `tests/chaos/test_chaos_trading.py`（30 用例全绿）已覆盖六场景探针级路径（PreTradeGuard / 降级审计 / get_emergency_protocol 契约），但未演练真实风控机制。本计划新增一个测试文件，直接驱动真实 `OrderLifecycleTracker` / `IntradayCircuitBreaker` / `KillSwitchManager`，并加一个与既有 FaultInjector 的联动场景。T15 四道门已有专属单测（`tests/unit/test_t15_live_order_executor.py`），Chaos 层不重复覆盖。

**Tech Stack:** pytest（项目 pytest.ini 已配置，timeout 300s）、unittest.mock 不需要（真实类 + 桩 broker）、`RiskAuditLogger` 用 tmp_path 隔离审计落盘。

**上游设计:** `docs/v8.7_Production_Edition_架构升级方案_200万实盘版_20260902.md` §7.1 T1 + `docs/v8.7.1_稳定性增强实施计划_20260902.md` §三 G2

**验收对照（方案 T1 验收标准）:**
- 六场景用例全过 ✓（现有 30 用例）+ 真实机制演练（本计划）
- 单点故障注入后 fail-closed + 审计留痕（本计划断言）
- 门禁三件套无回归（Task 6 验证）

---

### Task 1: 测试文件骨架 + SpyAudit 桩 + T16 QMT 断开轮询不崩溃

**Files:**
- Create: `tests/chaos/test_chaos_risk_mechanisms.py`

- [ ] **Step 1: 写测试骨架与第一个用例（应通过——机制已存在，验证的是安全不变量）**

```python
"""Chaos 灾难演练 — 真实风控机制覆盖 (T1 补齐, 2026-09-02).

现有 test_chaos_trading.py 覆盖六场景探针级路径; 本文件直接驱动真实机制:
  - T16 OrderLifecycleTracker: QMT 断开轮询不崩溃 + 超时孤儿单检测
  - T11 IntradayCircuitBreaker: 连续失败熔断 + 冷却恢复 + 再熔断
  - T12 KillSwitchManager: 三级熔断开平仓语义
  - 联动: FaultInjector qmt_down + T11 熔断吸收

全部零侵入 (不改生产模块), 可被 CI 稳定执行 (无真实网络/账户)。
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
```

- [ ] **Step 2: 运行验证通过**

Run: `.venv\Scripts\python.exe -m pytest tests\chaos\test_chaos_risk_mechanisms.py::TestT16QmtDown::test_poll_with_broker_down_no_crash -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/chaos/test_chaos_risk_mechanisms.py
git commit -m "test(chaos): T16 QMT 断开轮询不崩溃用例 (T1 补齐)"
```

---

### Task 2: T16 超时孤儿单检测（fail-closed 核心断言）

**Files:**
- Modify: `tests/chaos/test_chaos_risk_mechanisms.py`（TestT16QmtDown 类内追加）

- [ ] **Step 1: 写孤儿单检测用例 + 负控制用例**

```python
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
```

- [ ] **Step 2: 运行验证通过**

Run: `.venv\Scripts\python.exe -m pytest tests\chaos\test_chaos_risk_mechanisms.py::TestT16QmtDown -v`
Expected: 4 passed

- [ ] **Step 3: Commit**

```bash
git add tests/chaos/test_chaos_risk_mechanisms.py
git commit -m "test(chaos): T16 超时孤儿单检测 + 审计留痕 + 负控制"
```

---

### Task 3: T11 连续失败熔断（trip → 冷却 HALF_OPEN → 恢复/再熔断）

**Files:**
- Modify: `tests/chaos/test_chaos_risk_mechanisms.py`（追加 TestT11CircuitBreaker 类）

- [ ] **Step 1: 写 T11 三用例**

```python
# ============================================================
# T11: 盘中断路器 — 连续失败熔断 / 冷却恢复 / 再熔断
# ============================================================
class TestT11CircuitBreaker:
    def test_consecutive_failures_trip_open(self):
        """连续失败达阈值 → OPEN, 禁止交易 (模型异常/下单连败场景)."""
        cb = IntradayCircuitBreaker(consecutive_fail_threshold=3, cooloff_seconds=60)
        assert cb.allow_trading is True
        for i in range(3):
            cb.record_failure(f"chaos-fail-{i}")
        assert cb.state == CBState.OPEN
        assert cb.allow_trading is False, "熔断 OPEN 态不得放行新交易"
        snap = cb.snapshot()
        assert snap["trip_count"] >= 1

    def test_cooldown_halfopen_then_recover(self):
        """冷却期过 → HALF_OPEN; 成功 → CLOSED 恢复交易."""
        cb = IntradayCircuitBreaker(consecutive_fail_threshold=2, cooloff_seconds=1)
        cb.record_failure("a")
        cb.record_failure("b")
        assert cb.state == CBState.OPEN
        time.sleep(1.1)  # 过冷却期 (state 属性惰性迁移)
        assert cb.state == CBState.HALF_OPEN
        cb.record_success()
        assert cb.state == CBState.CLOSED
        assert cb.allow_trading is True

    def test_halfopen_failure_retrips(self):
        """HALF_OPEN 下再失败 → 立即再 OPEN (不无限放行试探单)."""
        cb = IntradayCircuitBreaker(consecutive_fail_threshold=2, cooloff_seconds=1)
        cb.record_failure("a")
        cb.record_failure("b")
        time.sleep(1.1)
        assert cb.state == CBState.HALF_OPEN
        cb.record_failure("half-open-probe-fail")
        assert cb.state == CBState.OPEN
        assert cb.allow_trading is False
```

- [ ] **Step 2: 运行验证通过**

Run: `.venv\Scripts\python.exe -m pytest tests\chaos\test_chaos_risk_mechanisms.py::TestT11CircuitBreaker -v`
Expected: 3 passed

- [ ] **Step 3: Commit**

```bash
git add tests/chaos/test_chaos_risk_mechanisms.py
git commit -m "test(chaos): T11 连续失败熔断三态迁移用例"
```

---

### Task 4: T12 三级熔断开平仓语义

**Files:**
- Modify: `tests/chaos/test_chaos_risk_mechanisms.py`（追加 TestT12KillSwitch 类）

- [ ] **Step 1: 写 T12 两用例**

```python
# ============================================================
# T12: KillSwitch — 三级熔断的开平仓语义
# ============================================================
class TestT12KillSwitch:
    def test_caution_blocks_new_allows_reduce(self):
        """L1 (保证金≥50%): 禁开新仓, 放行减仓 — 降风险方向不阻塞."""
        ksm = KillSwitchManager()
        ksm.update_margin_usage(0.60)
        assert ksm.current_level() == KillLevel.CAUTION
        open_dec = ksm.evaluate_trade("510300.SH", "buy", 10_000, is_open_new=True)
        assert open_dec.allowed is False, "L1 不得开新仓"
        reduce_dec = ksm.evaluate_trade("510300.SH", "sell", 10_000, is_open_new=False)
        assert reduce_dec.allowed is True, "L1 必须放行减仓"

    def test_liquidate_only_sell_with_audit(self):
        """L3 (保证金≥95%): 仅允许变现类指令, 且触发计数留痕."""
        ksm = KillSwitchManager()
        ksm.update_margin_usage(0.97)
        assert ksm.current_level() == KillLevel.LIQUIDATE
        assert ksm.evaluate_trade("510300.SH", "buy", 10_000).allowed is False
        assert ksm.evaluate_trade("510300.SH", "sell", 10_000).allowed is True
        audit = ksm.audit()
        assert audit.total_triggered_L3 >= 1, "L3 触发必须有审计计数"
        assert audit.blocked_orders >= 1, "拦截必须有计数"
```

- [ ] **Step 2: 运行验证通过**

Run: `.venv\Scripts\python.exe -m pytest tests\chaos\test_chaos_risk_mechanisms.py::TestT12KillSwitch -v`
Expected: 2 passed

- [ ] **Step 3: Commit**

```bash
git add tests/chaos/test_chaos_risk_mechanisms.py
git commit -m "test(chaos): T12 三级熔断开平仓语义用例"
```

---

### Task 5: 联动场景 —— FaultInjector qmt_down + T11 熔断吸收

**Files:**
- Modify: `tests/chaos\test_chaos_risk_mechanisms.py`（追加 TestLinkage 类，需在文件头部追加 import）

- [ ] **Step 1: 文件头部追加 import**

```python
from utils.chaos.fault_injector import FaultInjector  # noqa: E402
```

- [ ] **Step 2: 写联动用例**

```python
# ============================================================
# 联动: QMT 断开 (FaultInjector) + T11 熔断吸收
# ============================================================
class TestLinkageQmtDownCircuitBreaker:
    def test_qmt_down_failures_trip_breaker_then_no_orders(self):
        """QMT 断开的执行降级喂给 T11 → 熔断 → 后续无新订单 (fail-closed 闭环)."""
        r = FaultInjector().run("qmt_down")
        # 探针层不变量
        assert not r.crashed
        assert r.orders_submitted == []
        assert r.degradations, "断开必须有降级留痕"

        # 执行层连败喂给熔断器 (模拟主链路把每次下单失败记入 T11)
        cb = IntradayCircuitBreaker(consecutive_fail_threshold=3, cooloff_seconds=60)
        for d in r.degradations:
            cb.record_failure(d)
        assert cb.state == CBState.OPEN, "执行连败必须触发熔断"
        assert cb.allow_trading is False
```

- [ ] **Step 3: 运行验证通过**

Run: `.venv\Scripts\python.exe -m pytest tests\chaos\test_chaos_risk_mechanisms.py::TestLinkageQmtDownCircuitBreaker -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/chaos/test_chaos_risk_mechanisms.py
git commit -m "test(chaos): qmt_down 与 T11 熔断联动闭环用例"
```

---

### Task 6: 全量回归 + ruff + LOG 登记

**Files:**
- Modify: `cairn/LOG.md`（顶部追加条目）

- [ ] **Step 1: 全 chaos 套件回归**

Run: `.venv\Scripts\python.exe -m pytest tests\chaos\ -v`
Expected: 既有 30 + 新增 10 = 40 passed

- [ ] **Step 2: ruff 检查**

Run: `.venv\Scripts\python.exe -m ruff check tests\chaos\test_chaos_risk_mechanisms.py`
Expected: All checks passed

- [ ] **Step 3: 相邻回归（T11/T12/T16 既有单测无冲突）**

Run: `.venv\Scripts\python.exe -m pytest tests\unit\ -k "circuit_breaker or kill_switch or lifecycle" -q`
Expected: 0 failed

- [ ] **Step 4: LOG.md 顶部追加条目（置于"本文件按反向时间顺序…"说明行下方）**

```markdown
## 2026-09-02 · T1 Chaos 灾难演练补齐：真实风控机制覆盖（零侵入测试资产）

- **背景**: Production Edition 方案 T1 验收要求六场景 fail-closed 演练；已有 `tests/chaos/test_chaos_trading.py` 30 用例覆盖探针级路径，但未演练真实 T11/T12/T16 机制（G2 设计表断言目标未完全兑现）
- **交付**: 新增 `tests/chaos/test_chaos_risk_mechanisms.py` 10 用例——① T16 QMT 断开：轮询吞异常不崩溃 + 超时标 ORPHANED 无活跃残留 + 审计留痕 + 负控制 ② T11 连续失败熔断三态迁移（OPEN→冷却 HALF_OPEN→恢复 CLOSED / 再熔断）③ T12 三级熔断开平仓语义（L1 禁开放减 / L3 仅变现 + 审计计数）④ 联动：qmt_down 执行降级喂 T11 → 熔断后无新订单
- **验证**: tests/chaos 全套 40 passed；ruff 0 error；相邻单测无回归
- **指针**: `tests/chaos/test_chaos_risk_mechanisms.py`；`docs/v8.7_Production_Edition_架构升级方案_200万实盘版_20260902.md` §7.1 T1
```

- [ ] **Step 5: Commit**

```bash
git add cairn/LOG.md
git commit -m "docs(cairn): T1 Chaos 机制覆盖完成 LOG 登记"
```

---

## Self-Review 记录

- **Spec 覆盖**: 方案 T1 验收"六场景全过"（既有 30 用例）+ "fail-closed + 审计留痕"（本计划 10 用例）+ "门禁三件套无回归"（Task 6 Step 3 相邻回归）；T15 已有专属单测不重复 ✓
- **占位符扫描**: 无 TBD/TODO ✓
- **类型一致性**: `CBState`/`KillLevel`/`OrderState` 枚举名、`evaluate_trade(symbol, side, notional, is_open_new)` 签名、`register(order_id, broker_order_id, symbol, side, planned_qty)` 签名均与源码核对一致 ✓
- **已知风险**: `test_orphan_has_audit_trace` 依赖 `RiskAuditLogger.log` 为关键字参数调用（源码 L388 `module=.../action=.../severity=.../symbol=.../reason=...` 已核实）；若 SpyAudit override 签名不匹配，pytest 会立即暴露，届时按实际签名调整
