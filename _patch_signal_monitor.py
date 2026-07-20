from pathlib import Path

path = Path(r"e:\各种PY程序\28-终极量化交易系统7.1\signal_monitor.py")
text = path.read_text(encoding="utf-8")

old = '''def get_latest_qlib_report():
    reports = sorted(REPORTS_DIR.glob("qlib_v6_train_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    if reports:
        return reports[0]
    reports = sorted(REPORTS_DIR.glob("qlib_v5_train_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    if reports:
        return reports[0]
    reports = sorted(REPORTS_DIR.glob("qlib_v4_train_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    if reports:
        return reports[0]
    reports = sorted(REPORTS_DIR.glob("qlib_v3_5_fix_train_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    if reports:
        return reports[0]
    reports = sorted(REPORTS_DIR.glob("qlib_v3_train_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    if reports:
        return reports[0]
    return None'''

new = '''def get_latest_qlib_report():
    preferred = [
        "qlib_v7_train_*.json",
        "qlib_v9_train_*.json",
        "qlib_v8_train_*.json",
        "qlib_v6_train_*.json",
        "qlib_v5_train_*.json",
        "qlib_v4_train_*.json",
        "qlib_v3_5_fix_train_*.json",
        "qlib_v3_5_train_*.json",
        "qlib_v3_train_*.json",
    ]
    for pattern in preferred:
        reports = sorted(REPORTS_DIR.glob(pattern), key=lambda x: x.stat().st_mtime, reverse=True)
        if reports:
            return reports[0]
    return None'''

if old in text:
    text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")
    print("patched signal_monitor.py: prefer v7")
else:
    print("target block not found")
