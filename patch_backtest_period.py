# -*- coding: utf-8 -*-
import pathlib

p = pathlib.Path(r"E:\各种PY程序\28-终极量化交易系统7.1\backtest_runner.py")
text = p.read_text(encoding="utf-8")

old = """if __name__ == "__main__":
    result = run_backtest(["600519", "000858", "601318", "000001", "600036", "601398", "600276", "000063"], start="2024-01-01", end="2024-12-31")
    print(json.dumps(result, ensure_ascii=False, indent=2))"""

new = """if __name__ == "__main__":
    result = run_backtest(["600519", "000858", "601318", "000001", "600036", "601398", "600276", "000063"], start="2024-01-01", end="2030-12-31")
    print(json.dumps(result, ensure_ascii=False, indent=2))"""

if old not in text:
    raise SystemExit("backtest runner target not found")
text = text.replace(old, new)
p.write_text(text, encoding="utf-8")
print("patched backtest period to 2024-2030")
