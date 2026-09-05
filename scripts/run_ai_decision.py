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
from pathlib import Path

# 项目根入 sys.path (2026-09-05 审计修复): 直接执行时 sys.path[0]=scripts/,
# `from ai_decision.cli import main` 会 No module named 'ai_decision'
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_decision.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
