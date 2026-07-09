import sys, importlib.util
from pathlib import Path

base = Path(r"e:\各种PY程序\28-终极量化交易系统7.1")
mod_path = base / "v7.5_institutional" / "daily_workflow.py"
spec = importlib.util.spec_from_file_location("daily_workflow", mod_path)
mod = importlib.util.module_from_spec(spec)
sys.modules["daily_workflow"] = mod
spec.loader.exec_module(mod)

DailyWorkflow = mod.DailyWorkflow

w = DailyWorkflow(trade_date="2026-07-05", dry_run=False)
ok = w.run(only_phase="check")
print("\n=== CHECK 结果 ===")
print("phase_check 返回:", ok)
check_state = w.state.get("phases", {}).get("check", {})
print("状态:", check_state.get("status"))
for k, v in check_state.get("checks", {}).items():
    print(f"  {k}: {v}")
