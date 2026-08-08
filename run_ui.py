# -*- coding: utf-8 -*-
"""量化策略系统 v5.2 — Streamlit UI 启动脚本"""
import subprocess
import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UI_ENTRY = os.path.join(BASE_DIR, "ui", "app.py")

print("=" * 60)
print("  量化策略系统 v5.2 — Streamlit 可视化面板")
print("=" * 60)
print(f"  入口: {UI_ENTRY}")
print(f"  地址: http://localhost:8501")
print("=" * 60)

try:
    from utils.paths import get_python_exe
    python_exe = get_python_exe()
except ImportError:
    python_exe = os.environ.get("STREAMLIT_PYTHON", sys.executable)

result = subprocess.run(
    [python_exe, "-m", "streamlit", "run", UI_ENTRY, "--server.port", "8501"],
    cwd=BASE_DIR,
)