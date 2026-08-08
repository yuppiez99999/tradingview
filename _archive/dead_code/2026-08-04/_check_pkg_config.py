"""Check existing package config files."""
import pathlib

files = [
    "pyproject.toml", "setup.py", "setup.cfg",
    "conftest.py", "sitecustomize.py",
    "utils/__init__.py", "ms_strategy/__init__.py",
    "ai_decision/__init__.py", "scripts/__init__.py",
    "v8.3_institutional/__init__.py",
]

for f in files:
    p = pathlib.Path(f)
    if p.exists():
        size = p.stat().st_size
        print(f"{f}: EXISTS ({size} bytes)")
    else:
        print(f"{f}: missing")
