# -*- coding: utf-8 -*-
"""
scripts/run_ai_decision.py — 便捷 CLI 包装
=========================================

直接调用 ai_decision.cli.main, 保持与项目 scripts/ 其他入口一致.

用法:
  python scripts/run_ai_decision.py --symbol 600519 --mode shadow
  python scripts/run_ai_decision.py --symbol 600519 --mock-force
"""

from __future__ import annotations

import sys

from ai_decision.cli import main

if __name__ == "__main__":
    sys.exit(main())
