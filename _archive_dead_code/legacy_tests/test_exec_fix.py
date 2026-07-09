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
# 先运行 check 和 signal 阶段，生成订单
w.run(only_phase="check")
w.run(only_phase="signal")
# 再运行 execute 阶段
result = w.run(only_phase="execute")
print("状态:", result.get("status"))
print("阶段:", result.get("phases", {}).get("execute", {}).get("status"))
fills = result.get("phases", {}).get("execute", {}).get("fills", [])
print("成交数:", len(fills))
for f in fills[:8]:
    print(f)
