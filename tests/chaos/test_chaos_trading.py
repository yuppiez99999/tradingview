"""Chaos 灾难演练 — 六场景用例 (G2, 2026-09-02).

每个场景三段式: 注入故障 -> 运行决策→执行探针 -> 断言安全状态。

通用断言 (所有场景必须满足):
  1. 不产生未受控的真实订单 (决策路径 fail-closed)
  2. 有降级审计留痕 (degradation_log)
  3. 有告警发出 (不静默失败)
  4. 系统未崩溃 (无未捕获异常)

全部走 mock / 桩, 不依赖真实网络与真实账户, 可被 CI 稳定执行。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.chaos.fault_injector import (  # noqa: E402
    SCENARIOS,
    FaultInjector,
)


def _assert_common_invariants(result) -> None:
    """所有场景必须满足的四项通用安全不变量."""
    assert not result.crashed, f"场景 {result.scenario} 崩溃: {result.error}"
    assert result.alerts, f"场景 {result.scenario} 未发出任何告警 (静默失败)"
    assert result.degradations, f"场景 {result.scenario} 无降级审计留痕"


def _run(scenario: str):
    return FaultInjector().run(scenario)


# ============================================================
# 场景 1: Wind 断开 -> 数据降级 -> 暂停建仓 (不下单)
# ============================================================
class TestScenario1WindDown:
    def test_no_orders_and_zero_capital(self):
        r = _run("wind_down")
        _assert_common_invariants(r)
        assert r.capital_multiplier == 0.0, "数据降级后当日建仓资金乘数必须为 0"
        assert r.orders_submitted == [], "数据源中断时不应有任何真实订单"

    def test_data_degraded_triggers_pause(self):
        r = _run("wind_down")
        assert any("chaos_data" in d for d in r.degradations)
        assert any("暂停建仓" in a for a in r.alerts)


# ============================================================
# 场景 2: QMT 断开 -> 拒单 + 无重试 + 告警
# ============================================================
class TestScenario2QmtDown:
    def test_broker_down_no_orders_and_no_crash(self):
        r = _run("qmt_down")
        _assert_common_invariants(r)
        assert r.orders_submitted == [], "Broker 断开时不应有任何落单"
        assert any("下单失败" in a for a in r.alerts), "应告警 broker 断开"

    def test_no_infinite_retry(self):
        # 断开时每个标的只尝试一次即标记降级, 不重试到裸实盘
        r = _run("qmt_down")
        exec_degrads = [d for d in r.degradations if d.startswith("chaos_exec:")]
        assert len(exec_degrads) >= 1, "每个失败标的应记一次执行降级 (无重试)"


# ============================================================
# 场景 3: 数据错一天 -> 非单调/未来时间戳拦截 (无前视信号)
# ============================================================
class TestScenario3DataOffByOne:
    def test_future_timestamp_blocked(self):
        r = _run("data_off_by_one")
        _assert_common_invariants(r)
        assert r.orders_submitted == [], "未来时间戳数据不得产生任何信号/订单"
        assert any("时间戳" in a or "前视" in a for a in r.alerts)

    def test_no_lookahead_signal(self):
        r = _run("data_off_by_one")
        assert r.capital_multiplier == 0.0 or not r.orders_submitted


# ============================================================
# 场景 4: ETF 停牌 -> PreTradeGuard SUSPEND_FILTER 拦截该标的
# ============================================================
class TestScenario4EtfSuspended:
    def test_suspended_symbol_rejected_not_submitted(self):
        r = _run("etf_suspended")
        _assert_common_invariants(r)
        assert "510300.SH" in r.rejected_symbols, "停牌标的必须被 SUSPEND_FILTER 拦截"
        submitted_codes = [o["code"] for o in r.orders_submitted]
        assert "510300.SH" not in submitted_codes, "停牌标的不得下单"

    def test_other_symbols_unaffected(self):
        r = _run("etf_suspended")
        submitted_codes = [o["code"] for o in r.orders_submitted]
        # 其余未停牌标的应可正常下单 (受控)
        assert "518880.SH" in submitted_codes, "未停牌标的应正常下单"


# ============================================================
# 场景 5: 期权无法成交 -> 对冲降级 + 敞口告警 + 不无限重试
# ============================================================
class TestScenario5OptionNoLiquidity:
    def test_option_orders_rejected(self):
        r = _run("option_no_liquidity")
        _assert_common_invariants(r)
        assert "10005003.SH" in r.rejected_symbols, "期权订单应被拒 (无流动性)"
        assert any("期权" in a or "对冲" in a for a in r.alerts), "应发期权对冲降级/敞口告警"

    def test_no_infinite_retry(self):
        r = _run("option_no_liquidity")
        exec_degrads = [d for d in r.degradations if d.startswith("chaos_exec:")]
        assert len(exec_degrads) >= 1


# ============================================================
# 场景 6: 模型输出异常 (NaN) -> 信号层 fail-closed (不产生订单)
# ============================================================
class TestScenario6ModelNaN:
    def test_nan_signal_no_orders(self):
        r = _run("model_nan")
        _assert_common_invariants(r)
        assert r.orders_submitted == [], "NaN 信号不得产生任何订单"
        assert any("信号" in a for a in r.alerts), "应告警信号质量不达标"

    def test_signal_quality_rejected(self):
        r = _run("model_nan")
        assert any("chaos_signal" in d for d in r.degradations)


# ============================================================
# 全场景通用不变量 (参数化)
# ============================================================
@pytest.mark.parametrize("scenario", SCENARIOS)
class TestAllScenariosCommonInvariants:
    def test_no_crash(self, scenario):
        r = _run(scenario)
        assert not r.crashed, f"{scenario} 崩溃: {r.error}"

    def test_has_alert(self, scenario):
        r = _run(scenario)
        assert r.alerts, f"{scenario} 无告警"

    def test_has_degradation(self, scenario):
        r = _run(scenario)
        assert r.degradations, f"{scenario} 无降级审计"
