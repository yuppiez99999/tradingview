"""
将 QLib 训练信号更新到 positions.json
支持v3/v3.5/v4/v5版本报告
"""
import json
import re
from pathlib import Path

PROJECT_ROOT = Path(r"e:\各种PY程序\28-终极量化交易系统7.1")
POSITIONS_FILE = PROJECT_ROOT / "config" / "positions.json"
REPORTS_DIR = PROJECT_ROOT / "reports"


def get_latest_qlib_report():
    candidates = []
    for p in REPORTS_DIR.glob("qlib_*_train_*.json"):
        m = re.search(r"qlib_(v\d+)_train_(\d+_\d+)\.json", p.name)
        if m:
            candidates.append((m.group(1), m.group(2), p.stat().st_mtime, p))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    return candidates[0][3]


def update_positions_with_signals():
    report_path = get_latest_qlib_report()
    if not report_path:
        print("[错误] 未找到 QLib 训练报告")
        return

    print(f"[读取] {report_path}")
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    with open(POSITIONS_FILE, encoding="utf-8") as f:
        positions_data = json.load(f)

    signal_map = {}

    if "portfolio_signals" in report:
        for _code, info in report["portfolio_signals"].items():
            symbol = info["symbol"]
            exchange = symbol[:2]
            number = symbol[2:]
            positions_key = f"{number}.{exchange}"
            signal_map[positions_key] = {
                "qlib_signal": info["signal"],
                "qlib_direction": info["direction"],
                "qlib_rank": None,
                "qlib_total": report.get("n_stocks", 0),
                "qlib_avg_signal": info["signal"],
            }
    elif "signals" in report:
        signals_dict = report["signals"]
        signal_values = list(signals_dict.values())
        avg_signal = sum(signal_values) / len(signal_values) if signal_values else 0
        for symbol, signal in signals_dict.items():
            exchange = symbol[:2]
            number = symbol[2:]
            positions_key = f"{number}.{exchange}"
            signal_map[positions_key] = {
                "qlib_signal": signal,
                "qlib_direction": "看多" if signal > 0 else "看空",
                "qlib_rank": None,
                "qlib_total": len(signal_values),
                "qlib_avg_signal": avg_signal,
            }
    else:
        signals = report.get("stock_signals", [])
        for sig in signals:
            code = sig["code"]
            exchange = code[:2]
            number = code[2:]
            positions_key = f"{number}.{exchange}"
            signal_map[positions_key] = {
                "qlib_signal": sig["latest_signal"],
                "qlib_direction": sig["direction"],
                "qlib_rank": sig["rank"],
                "qlib_total": sig["total"],
                "qlib_avg_signal": sig.get("qlib_avg_signal", sig.get("avg_signal", 0)),
            }

    updated_count = 0
    for key, pos in positions_data["positions"].items():
        if key in signal_map:
            sig = signal_map[key]
            pos["qlib_signal"] = sig["qlib_signal"]
            pos["qlib_direction"] = sig["qlib_direction"]
            pos["qlib_rank"] = sig["qlib_rank"]
            pos["qlib_total"] = sig["qlib_total"]
            pos["qlib_avg_signal"] = sig["qlib_avg_signal"]
            updated_count += 1

    positions_data["meta"]["last_qlib_update"] = report.get("timestamp", "")
    positions_data["meta"]["qlib_version"] = report.get("version", "unknown")
    positions_data["meta"]["qlib_model"] = report.get("model", "unknown")

    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(positions_data, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*70}")
    print("QLib 信号更新完成")
    print(f"{'='*70}")
    print(f"训练报告: {report_path.name}")
    print(f"模型版本: {report.get('version', 'unknown')} ({report.get('model', 'unknown')})")
    print(f"更新标的: {updated_count} / {len(positions_data['positions'])}")
    print(f"训练池规模: {report.get('n_stocks', 0)} 只股票")
    print(f"日均 IC: {report.get('mean_daily_ic', 0):.4f}")
    print(f"IC IR: {report.get('ic_ir', 0):.4f}")
    print(f"{'='*70}\n")

    for _key, pos in positions_data["positions"].items():
        if "qlib_signal" in pos:
            signal_str = f"{pos['qlib_signal']:.4f}" if pos['qlib_signal'] is not None else "None"
            print(f"{pos['code']} {pos['name']:<10} 信号: {signal_str} 方向: {pos['qlib_direction']}")


if __name__ == "__main__":
    update_positions_with_signals()
