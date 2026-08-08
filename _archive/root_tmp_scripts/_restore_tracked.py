"""从 git HEAD 恢复被破坏的 tracked 文件列表。"""
import subprocess
import sys
from pathlib import Path

# 仅恢复 git tracked 文件 (排除 untracked)
TRACKED_FILES = [
    "daily_build_and_hedge.py",
    "rebalance_execution_orders.py",
    "utils/ai_report_agent.py",
    "utils/alpha/auto_retrain_scheduler.py",
    "utils/alpha/drift_monitor.py",
    "utils/alpha/mlops_pipeline.py",
    "utils/alpha/strategy_evaluator.py",
    "utils/attribution/daily_panel.py",
    "utils/attribution/factor_attribution.py",
    "utils/attribution/managers.py",
    "utils/black_litterman_optimizer.py",
    "utils/data_provider.py",
    "utils/etf_flow_decision.py",
    "utils/etf_flow_monitor.py",
    "utils/execution/automated_execution_system.py",
    "utils/greek_hedge_manager.py",
    "utils/hedge_execution_engine.py",
    "utils/ifind_client.py",
    "utils/ledoit_wolf_covariance.py",
    "utils/liquidation_scheduler.py",
    "utils/market_impact_model.py",
    "utils/multi_strategy_coordinator.py",
    "utils/phase_manager.py",
    "utils/protective_put_engine.py",
    "utils/reporting/daily_report_generator.py",
    "utils/risk_attribution.py",
    "utils/risk_guard_integrator.py",
    "utils/supply_chain_graph.py",
    "utils/tf_price_predictor.py",
    "utils/var_monitor.py",
    "utils/vol_target_controller.py",
    "utils/web_scraper.py",
    "utils/wt_backtest_engine.py",
    "utils/wt_spread_strategy.py",
]

restored = 0
failed = []
for f in TRACKED_FILES:
    if not Path(f).exists():
        print("SKIP (not exist):", f)
        continue
    r = subprocess.run(["git", "checkout", "HEAD", "--", f], capture_output=True, text=True)
    if r.returncode == 0:
        restored += 1
        print("OK:", f)
    else:
        failed.append((f, r.stderr.strip()))
        print("FAIL:", f, r.stderr.strip())

print()
print("Restored:", restored)
print("Failed:", len(failed))
for f, err in failed:
    print(" ", f, ":", err)
