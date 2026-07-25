# -*- coding: utf-8 -*-
"""FactorKillSwitch 单元测试 - CIO v1.0"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.safety.factor_kill_switch import (
    FactorKillSwitch, FactorStatus, quick_check,
)


def test_normal_ic_stays_active():
    print("\n[Test 1] 正常 IC 保持 ACTIVE...")
    ks = FactorKillSwitch()
    ks.init("VT_NORMAL")
    for _ in range(10):
        s = ks.update("VT_NORMAL", ic=0.05, daily_pnl=0.001)
    assert s.status == FactorStatus.ACTIVE.value
    assert s.current_position_ratio == 1.0
    print(f"  ✓ status={s.status} pos={s.current_position_ratio}")


def test_consecutive_low_ic_degraded():
    print("\n[Test 2] 连续 5 日 IC<0.02 触发 DEGRADED...")
    ks = FactorKillSwitch()
    ks.init("VT_LOW_IC")
    for _ in range(5):
        s = ks.update("VT_LOW_IC", ic=0.01, daily_pnl=0.0)
    assert s.status == FactorStatus.DEGRADED.value, f"应 DEGRADED, 实际 {s.status}"
    assert s.current_position_ratio == 0.5
    print(f"  ✓ status={s.status} pos={s.current_position_ratio}")


def test_consecutive_neg_ic_disabled():
    print("\n[Test 3] 连续 10 日 IC<0 触发 DISABLED...")
    ks = FactorKillSwitch()
    ks.init("VT_NEG_IC")
    for _ in range(10):
        s = ks.update("VT_NEG_IC", ic=-0.01, daily_pnl=-0.001)
    assert s.status == FactorStatus.DISABLED.value, f"应 DISABLED, 实际 {s.status}"
    assert s.current_position_ratio == 0.0
    print(f"  ✓ status={s.status} pos={s.current_position_ratio}")


def test_consecutive_neg_ic_retired():
    print("\n[Test 4] 连续 20 日 IC<0 触发 RETIRED（终态）...")
    ks = FactorKillSwitch()
    ks.init("VT_RETIRE")
    for _ in range(20):
        s = ks.update("VT_RETIRE", ic=-0.01, daily_pnl=-0.001)
    assert s.status == FactorStatus.RETIRED.value, f"应 RETIRED, 实际 {s.status}"
    assert s.is_terminal
    # 终态不可恢复
    s = ks.update("VT_RETIRE", ic=0.5, daily_pnl=0.01)  # 即使 IC 恢复
    assert s.status == FactorStatus.RETIRED.value
    print(f"  ✓ status={s.status} 终态不可恢复")


def test_cumulative_drawdown_emergency_exit():
    print("\n[Test 5] 累计回撤 > 12% 触发 EMERGENCY_EXIT...")
    ks = FactorKillSwitch()
    ks.init("VT_BIG_DD")
    # 先建立 peak
    for _ in range(5):
        ks.update("VT_BIG_DD", ic=0.05, daily_pnl=0.01)
    # 然后大跌
    for _ in range(5):
        s = ks.update("VT_BIG_DD", ic=0.05, daily_pnl=-0.03)
    assert s.cumulative_drawdown >= 0.12, f"cum_dd={s.cumulative_drawdown:.3f}"
    assert s.status == FactorStatus.EMERGENCY_EXIT.value, f"应 EMERGENCY_EXIT, 实际 {s.status}"
    assert s.current_position_ratio == 0.0
    print(f"  ✓ status={s.status} cum_dd={s.cumulative_drawdown:.3f}")


def test_daily_drawdown_half_position():
    print("\n[Test 6] 单日回撤 > 3% 仓位减半...")
    ks = FactorKillSwitch()
    ks.init("VT_DAILY_DD")
    # 正常 1 天
    ks.update("VT_DAILY_DD", ic=0.05, daily_pnl=0.001)
    # 大跌 1 天
    s = ks.update("VT_DAILY_DD", ic=0.05, daily_pnl=-0.04)  # -4% < -3%
    assert s.current_position_ratio <= 0.5, f"应减半 pos={s.current_position_ratio}"
    print(f"  ✓ pos={s.current_position_ratio}（已减半）")


def test_force_retire():
    print("\n[Test 7] 人工强制退役...")
    ks = FactorKillSwitch()
    ks.init("VT_FORCE")
    ks.update("VT_FORCE", ic=0.05, daily_pnl=0.001)
    ok = ks.force_retire("VT_FORCE", "人工干预退役")
    assert ok
    s = ks.get_status("VT_FORCE")
    assert s.status == FactorStatus.RETIRED.value
    assert s.is_terminal
    print(f"  ✓ 强制退役成功")


def test_recovery_to_active():
    print("\n[Test 8] 恢复到 ACTIVE...")
    ks = FactorKillSwitch()
    ks.init("VT_RECOVER")
    # 5 天低 IC -> DEGRADED
    for _ in range(5):
        s = ks.update("VT_RECOVER", ic=0.01, daily_pnl=0.0)
    assert s.status == FactorStatus.DEGRADED.value
    # 5 天正常 IC -> 恢复 ACTIVE
    for _ in range(5):
        s = ks.update("VT_RECOVER", ic=0.05, daily_pnl=0.001)
    assert s.status == FactorStatus.ACTIVE.value, f"应恢复 ACTIVE, 实际 {s.status}"
    assert s.current_position_ratio == 1.0
    print(f"  ✓ 恢复 ACTIVE")


def test_quick_check_helper():
    print("\n[Test 9] quick_check 辅助函数...")
    ic_series = [-0.01] * 20
    pnl_series = [-0.001] * 20
    s = quick_check("VT_QUICK", ic_series, pnl_series)
    assert s.status == FactorStatus.RETIRED.value
    print(f"  ✓ status={s.status}")


def main():
    print("=" * 60)
    print("FactorKillSwitch 单元测试 - CIO v1.0")
    print("=" * 60)
    tests = [
        test_normal_ic_stays_active,
        test_consecutive_low_ic_degraded,
        test_consecutive_neg_ic_disabled,
        test_consecutive_neg_ic_retired,
        test_cumulative_drawdown_emergency_exit,
        test_daily_drawdown_half_position,
        test_force_retire,
        test_recovery_to_active,
        test_quick_check_helper,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t(); passed += 1
        except AssertionError as e:
            failed += 1; print(f"  ✗ FAIL: {e}")
        except Exception as e:
            failed += 1; print(f"  ✗ ERROR: {type(e).__name__}: {e}")
    print("\n" + "=" * 60)
    print(f"总计: {passed} 通过, {failed} 失败 (共 {len(tests)} 项)")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
