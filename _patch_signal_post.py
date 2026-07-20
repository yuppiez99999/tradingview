from pathlib import Path

path = Path(r"e:\各种PY程序\28-终极量化交易系统7.1\signal_post_processing.py")
text = path.read_text(encoding="utf-8")

old_get = '''def get_latest_qlib_report():
    reports = sorted(REPORTS_DIR.glob("qlib_v3_5_fix_train_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    if reports:
        return reports[0]
    reports = sorted(REPORTS_DIR.glob("qlib_v3_5_train_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    if reports:
        return reports[0]
    reports = sorted(REPORTS_DIR.glob("qlib_v3_train_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    if reports:
        return reports[0]
    reports = sorted(REPORTS_DIR.glob("qlib_improved_train_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    if reports:
        return reports[0]
    return None'''

new_get = '''def get_latest_qlib_report():
    patterns = [
        "qlib_v9_train_*.json",
        "qlib_v8_train_*.json",
        "qlib_v7_train_*.json",
        "qlib_v6_train_*.json",
        "qlib_v5_train_*.json",
        "qlib_v4_train_*.json",
        "qlib_v3_5_fix_train_*.json",
        "qlib_v3_5_train_*.json",
        "qlib_v3_train_*.json",
        "qlib_improved_train_*.json",
    ]
    for pattern in patterns:
        reports = sorted(REPORTS_DIR.glob(pattern), key=lambda x: x.stat().st_mtime, reverse=True)
        if reports:
            return reports[0]
    return None'''

if old_get in text:
    text = text.replace(old_get, new_get, 1)
else:
    print("old get_latest_qlib_report not found")

old_parse = '''    stock_signals = {}
    for sig in report.get("stock_signals", []):
        stock_signals[sig["code"]] = {
            "raw_signal": sig["latest_signal"],
            "direction": sig["direction"],
            "name": sig["name"],
        }'''

new_parse = '''    stock_signals = {}
    if "stock_signals" in report:
        for sig in report["stock_signals"]:
            stock_signals[sig["code"]] = {
                "raw_signal": sig["latest_signal"],
                "direction": sig["direction"],
                "name": sig["name"],
            }
    elif "signals" in report:
        for symbol, signal in report["signals"].items():
            direction = "看多" if signal > 0 else "看空"
            stock_signals[symbol] = {
                "raw_signal": float(signal),
                "direction": direction,
                "name": "",
            }'''

if old_parse in text:
    text = text.replace(old_parse, new_parse, 1)
else:
    print("old stock_signals parse not found")

path.write_text(text, encoding="utf-8")
print("patched signal_post_processing.py")
