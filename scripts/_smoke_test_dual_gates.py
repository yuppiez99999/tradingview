# -*- coding: utf-8 -*-
"""v8.4 双门禁端到端冒烟测试.

验证完整链路:
    1. predict_annual_return_struct() 返回结构化预测
    2. ReturnExpectationGate (sim_mode) 告警不阻断
    3. ReturnExpectationGate (live_mode) 不达标时 fail-closed
    4. HedgeCompletenessGate (sim_mode, 完全对冲) 通过
    5. HedgeCompletenessGate (live_mode, 对冲不足) fail-closed
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_V83_DIR = _PROJECT_ROOT / "v8.3_institutional"
sys.path.insert(0, str(_V83_DIR))
sys.path.insert(0, str(_PROJECT_ROOT))


class MockSimEngine:
    """模拟 SimExecutionEngine"""

    def get_greek_exposure(self):
        return {"delta": -1_300_000, "gamma": 0, "theta": 0, "vega": 0}


def main() -> int:
    """运行冒烟测试, 返回 0=成功 / 1=失败"""
    failures = []

    # === 1. predict_annual_return_struct ===
    try:
        from predict_annual_return import predict_annual_return_struct
        pred = predict_annual_return_struct()
        assert "expected_return" in pred
        assert "phase_adjusted_return" in pred
        assert "sharpe_estimate" in pred
        print("[1] predict_annual_return_struct OK")
        print(f"    expected_return={pred['expected_return']:.4f}")
        print(f"    phase_adjusted_return={pred['phase_adjusted_return']:.4f}")
        print(f"    sharpe_estimate={pred['sharpe_estimate']:.4f}")
    except Exception as e:
        failures.append(f"[1] predict_annual_return_struct FAIL: {e}")
        print(f"[1] FAIL: {e}")

    # === 2. ReturnExpectationGate (sim_mode) ===
    try:
        from gate_manager import ReturnExpectationGate
        gate = ReturnExpectationGate()
        result = gate.evaluate(mode="sim")
        # sim_mode: 无论是否达标都不阻断
        assert gate.enforce(result, live_mode=False) is True
        print(f"[2] ReturnExpectationGate(sim) OK, passed={result.passed}, action={result.action}")
        print(f"    blockers={result.blockers}")
        print(f"    promoters={result.promoters}")
    except Exception as e:
        failures.append(f"[2] ReturnExpectationGate(sim) FAIL: {e}")
        print(f"[2] FAIL: {e}")

    # === 3. ReturnExpectationGate (live_mode) ===
    try:
        from gate_manager import ReturnExpectationGate
        gate = ReturnExpectationGate()
        result = gate.evaluate(mode="live")
        # live_mode: 若不达标必须 fail-closed
        if not result.passed:
            assert gate.enforce(result, live_mode=True) is False, "live_mode 不达标应阻断"
            print("[3] ReturnExpectationGate(live) OK, fail-closed 触发")
        else:
            assert gate.enforce(result, live_mode=True) is True
            print("[3] ReturnExpectationGate(live) OK, 通过")
        print(f"    blockers={result.blockers}")
    except Exception as e:
        failures.append(f"[3] ReturnExpectationGate(live) FAIL: {e}")
        print(f"[3] FAIL: {e}")

    # === 4. HedgeCompletenessGate (sim_mode, 完全对冲) ===
    try:
        from gate_manager import HedgeCompletenessGate
        gate = HedgeCompletenessGate()
        result = gate.evaluate(
            portfolio_beta_before=1.05,
            portfolio_value=5_000_000,
            hedge_orders_executed=[
                {"action": "SHORT_FUTURES", "contracts": 3, "price": 4200},
                {"action": "BUY_PUT_SPREAD", "budget": 100_000},
            ],
            sim_engine=MockSimEngine(),
        )
        assert result.passed is True, f"完全对冲应通过: blockers={result.blockers}"
        print(f"[4] HedgeCompletenessGate(sim, hedged) OK, passed={result.passed}")
        print(f"    beta_after={result.metrics['portfolio_beta_after']:.4f}")
        print(f"    net_delta={result.metrics['net_delta']:.4f}")
    except Exception as e:
        failures.append(f"[4] HedgeCompletenessGate(sim, hedged) FAIL: {e}")
        print(f"[4] FAIL: {e}")

    # === 5. HedgeCompletenessGate (live_mode, 对冲不足) ===
    try:
        from gate_manager import HedgeCompletenessGate
        gate = HedgeCompletenessGate()
        result = gate.evaluate(
            portfolio_beta_before=1.05,
            portfolio_value=5_000_000,
            hedge_orders_executed=[
                {"action": "SHORT_FUTURES", "contracts": 1, "price": 4200},
            ],
            sim_engine=None,
        )
        assert result.passed is False, "对冲不足应不通过"
        assert gate.enforce(result, live_mode=True) is False, "live_mode 对冲不足应 fail-closed"
        print("[5] HedgeCompletenessGate(live, under-hedged) OK, fail-closed 触发")
        print(f"    beta_after={result.metrics['portfolio_beta_after']:.4f}")
        print(f"    net_delta={result.metrics['net_delta']:.4f}")
        print(f"    blockers={result.blockers}")
    except Exception as e:
        failures.append(f"[5] HedgeCompletenessGate(live, under-hedged) FAIL: {e}")
        print(f"[5] FAIL: {e}")

    # === 总结 ===
    print()
    if failures:
        print(f"=== SMOKE TEST FAILED ({len(failures)} failures) ===")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("=== ALL SMOKE TESTS PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
