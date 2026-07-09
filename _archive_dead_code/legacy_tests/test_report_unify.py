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
# 先运行 check、signal、execute 阶段
w.run(only_phase="check")
w.run(only_phase="signal")
w.run(only_phase="execute")
# 生成报告
result = w.run(only_phase="report")
report_dir = Path(r"e:\各种PY程序\28-终极量化交易系统7.1\每日报告归档\2026\07\05")
report_path = report_dir / "v75_daily_workflow_20260705.md"
print("报告路径:", report_path)

# 读取报告和 JSON，检查字段一致性
import json
json_path = report_path.with_suffix(".json")
state = json.loads(json_path.read_text(encoding="utf-8"))
order_summary = state.get("phases", {}).get("execute", {}).get("order_summary", [])
fills = state.get("phases", {}).get("execute", {}).get("fills", [])

print("order_summary 笔数:", len(order_summary))
print("fills 笔数:", len(fills))
print("顶层 orders 笔数:", len(state.get("orders", [])))

# 检查关键字段
if order_summary:
    print("\n=== order_summary 字段 ===")
    item = order_summary[0]
    for k in sorted(item.keys()):
        print(f"  {k}: {item[k]}")

if fills:
    print("\n=== fills 字段 ===")
    item = fills[0]
    for k in sorted(item.keys()):
        print(f"  {k}: {item[k]}")
