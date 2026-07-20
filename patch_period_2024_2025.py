# -*- coding: utf-8 -*-
import pathlib

p = pathlib.Path(r"E:\各种PY程序\28-终极量化交易系统7.1\backtest_runner.py")
text = p.read_text(encoding="utf-8")

old = 'start="2026-01-01", end="2030-12-31"'
new = 'start="2024-01-01", end="2025-12-31"'

if old not in text:
    raise SystemExit("target not found")

text = text.replace(old, new)
p.write_text(text, encoding="utf-8")
print("patched backtest period to 2024-2025")
