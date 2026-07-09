"""自动交易运行能力检查"""
import sys, importlib.util
from pathlib import Path

base = Path(r"e:\各种PY程序\28-终极量化交易系统7.1")
sys.path.insert(0, str(base / "v7.5_institutional"))
sys.path.insert(0, str(base / "utils"))
sys.path.insert(0, str(base / "v7.5_institutional" / "src"))

spec = importlib.util.spec_from_file_location(
    "daily_workflow", base / "v7.5_institutional" / "daily_workflow.py"
)
mod = importlib.util.module_from_spec(spec)
sys.modules["daily_workflow"] = mod
spec.loader.exec_module(mod)

DailyWorkflow = mod.DailyWorkflow

w = DailyWorkflow(trade_date="2026-07-05", dry_run=False)

# 1) check
print("=== 1. Phase check ===")
check_ok = w.run(only_phase="check")
print("check:", check_ok)

# 2) signal
print("\n=== 2. Phase signal ===")
signal = w.run(only_phase="signal")
print("signal keys:", list(signal.keys())[:10])
print("signal status:", signal.get("status") if isinstance(signal, dict) else type(signal).__name__)

# 3) execute
print("\n=== 3. Phase execute ===")
fills = w.run(only_phase="execute")
print("execute fills:", len(fills) if isinstance(fills, list) else "N/A")
if isinstance(fills, list) and fills:
    print("first fill:", fills[0])

# 4) report
print("\n=== 4. Phase report ===")
report_result = w.run(only_phase="report")
print("report type:", type(report_result).__name__)
if isinstance(report_result, dict):
    print("report keys:", list(report_result.keys())[:10])
elif isinstance(report_result, Path):
    print("report path:", report_result)
    print("report exists:", report_result.exists())
else:
    print("report result:", report_result)

# 5) 交易计划内容
print("\n=== 5. Trade plan ===")
plan = w.trade_plan
print("phase:", plan.get("phase", {}).get("name"))
exec_plan = plan.get("execution_plan", {})
print("morning orders:", len(exec_plan.get("morning_orders", [])))
print("afternoon orders:", len(exec_plan.get("afternoon_orders", [])))
print("grand total:", exec_plan.get("grand_total"))
