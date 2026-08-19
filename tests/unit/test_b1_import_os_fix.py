"""B1 smoke test: daily_trading_workflow._resolve_path 不再 NameError (缺 import os).

验证: B1 修复 — daily_trading_workflow.py L19 新增 import os
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def test_import_does_not_raise():
    """模块级 import 不抛 NameError"""
    import daily_trading_workflow  # noqa: F401


def test_resolve_path_no_name_error():
    """_resolve_path 正常使用 os.sep, 不抛 NameError"""
    import daily_trading_workflow as dtw

    # 传入一个已知存在的相对路径 (正斜杠 + 反斜杠混用)
    result = dtw._resolve_path("config\\positions.json")
    assert result is not None
    # 无论文件是否存在, 返回值应为 Path (不抛异常)
    assert isinstance(result, Path)
