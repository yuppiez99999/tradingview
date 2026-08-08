#!/usr/bin/env python3
"""Run the phase extraction script."""

import sys
from pathlib import Path

# Wave 3 第三阶段: 相对路径 "." 改为绝对路径 bootstrap (避免从其他目录运行时失败)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_phases import main

if __name__ == '__main__':
    exit_code = main()
    sys.exit(exit_code)
