import json
from pathlib import Path

project = Path(r"e:\各种PY程序\28-终极量化交易系统7.1")
report_path = project / "reports" / "qlib_v7_train_20260712_104718.json"
positions_path = project / "config" / "positions.json"

with open(report_path, "r", encoding="utf-8") as f:
    report = json.load(f)

with open(positions_path, "r", encoding="utf-8") as f:
    positions = json.load(f)

signals = report.get("signals", {})
updated = 0
for symbol, signal in signals.items():
    exchange = symbol[:2]
    number = symbol[2:]
    key = f"{number}.{exchange}"
    if key in positions["positions"]:
        pos = positions["positions"][key]
        pos["qlib_signal"] = float(signal)
        pos["qlib_direction"] = "看多" if signal > 0 else "看空"
        pos["qlib_rank"] = None
        pos["qlib_total"] = len(signals)
        pos["qlib_avg_signal"] = float(sum(signals.values()) / len(signals)) if signals else 0.0
        updated += 1

positions["meta"]["last_qlib_update"] = report.get("timestamp", "")
positions["meta"]["qlib_version"] = report.get("version", "v7")
positions["meta"]["qlib_model"] = report.get("model", "XGBoost_Top40_Reg")

with open(positions_path, "w", encoding="utf-8") as f:
    json.dump(positions, f, indent=2, ensure_ascii=False)

print(f"updated {updated} positions from v7 report")
