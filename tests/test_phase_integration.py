"""集成测试: phase_v10_risk + PhaseManager 季度评估"""
import sys
from datetime import date
from pathlib import Path

# 将项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 切换到 v7.5_institutional 目录
V75_DIR = PROJECT_ROOT / "v7.5_institutional"
sys.path.insert(0, str(V75_DIR))

import os

os.chdir(V75_DIR)

from daily_workflow import DailyWorkflow


def main():
    print("\n" + "=" * 60)
    print("集成测试: phase_v10_risk + PhaseManager 季度评估")
    print("=" * 60)

    # === 测试 1: 季度末 (2026-09-29) ===
    print("\n[测试 1] 2026-09-29 季度末")
    eng = DailyWorkflow(trade_date="2026-09-29", dry_run=True)

    pi = eng.current_phase_info
    print(f"  Phase info: year={pi.year}, phase={pi.phase_name}, quarter={pi.current_quarter}")
    print(f"  target={pi.target_return:.0%}, leverage={pi.leverage_target}x")

    # 季度末判断
    sim_date = date(2026, 9, 29)
    is_qe = eng.phase_manager.is_quarter_end(sim_date)
    print(f"  is_quarter_end={is_qe}")

    # 执行 phase_check
    eng.phase_check()

    # 执行 phase_v10_risk (含季度评估)
    print("\n--- 执行 phase_v10_risk ---")
    result = eng.phase_v10_risk()

    print(f"\n  drawdown level: {result.get('drawdown', {}).get('level', 'N/A')}")
    print(f"  var: {result.get('var', {}).get('status', 'N/A')}")
    print(f"  stress_test executed: {result.get('stress_test', {}).get('executed', False)}")

    qr = result.get("quarterly_review", {})
    print("\n  quarterly_review:")
    print(f"    executed: {qr.get('executed', False)}")
    print(f"    quarter: {qr.get('quarter', 'N/A')}")
    print(f"    is_quarter_end: {qr.get('is_quarter_end', False)}")
    print(f"    stress_test_triggered: {qr.get('stress_test_triggered', False)}")
    print(f"    rebalance_needed: {qr.get('rebalance_needed', False)}")
    print(f"    actions count: {len(qr.get('actions', []))}")
    for action in qr.get("actions", []):
        print(f"      - {action}")

    # === 测试 2: 非季度末 (2026-07-14) ===
    print("\n\n[测试 2] 2026-07-14 非季度末")
    eng2 = DailyWorkflow(trade_date="2026-07-14", dry_run=True)
    eng2.phase_check()
    result2 = eng2.phase_v10_risk()
    qr2 = result2.get("quarterly_review", {})
    print(f"  executed: {qr2.get('executed', False)}")
    print(f"  reason: {qr2.get('reason', 'N/A')}")

    # === 测试 3: 2030 清仓年 (2030-05-15 Q2) ===
    print("\n\n[测试 3] 2030-05-15 清仓年 Q2")
    eng3 = DailyWorkflow(trade_date="2030-05-15", dry_run=True)
    pi3 = eng3.current_phase_info
    print(f"  Phase info: year={pi3.year}, phase={pi3.phase_name}, quarter={pi3.current_quarter}")
    print(f"  is_liquidation_year: {pi3.is_liquidation_year}")
    if pi3.liquidation_actions:
        print(f"  liquidation action: {pi3.liquidation_actions.get('name', '')}")
    eng3.phase_check()

    print("\n" + "=" * 60)
    print("集成测试完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
