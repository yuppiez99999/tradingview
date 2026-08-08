"""T3.6 re-export 兼容层 — 已迁移到 utils/execution/daily_build_and_hedge.py.

原根目录文件已迁移到 ``utils/execution/`` 子目录 (2026-07-27).
本文件保留以兼容历史 ``from daily_build_and_hedge import ...`` 调用,
实际实现请参考 ``utils/execution/daily_build_and_hedge.py``.

HC-1 透传: 历史调用方无需修改 import 路径, 行为完全等价.
"""

from __future__ import annotations

# ============================================================
# T3.6 re-export 全部顶层符号
# ============================================================
try:
    from utils.execution.daily_build_and_hedge import (
        BASE_DIR,
        LOG_DIR,
        DailyBuildHedgeSystem,
        logger,
    )
except ImportError as _e:
    # 兜底: 当 utils 包不可导入时, 尝试从同目录加载
    import importlib.util
    import os
    import sys

    _SRC = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "utils",
        "execution",
        "daily_build_and_hedge.py",
    )
    if os.path.exists(_SRC):
        _spec = importlib.util.spec_from_file_location(
            "utils.execution.daily_build_and_hedge",
            _SRC,
        )
        _mod = importlib.util.module_from_spec(_spec)  # type: ignore[misc]
        _project_root = os.path.dirname(os.path.abspath(__file__))
        if _project_root not in sys.path:
            sys.path.insert(0, _project_root)
        _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
        DailyBuildHedgeSystem = _mod.DailyBuildHedgeSystem  # type: ignore[attr-defined]
        BASE_DIR = _mod.BASE_DIR  # type: ignore[attr-defined]
        LOG_DIR = _mod.LOG_DIR  # type: ignore[attr-defined]
        logger = _mod.logger  # type: ignore[attr-defined]
    else:
        raise ImportError(
            f"T3.6 re-export 失败: 找不到 utils/execution/daily_build_and_hedge.py (expected at {_SRC}): {_e}"
        ) from _e

__all__ = ["BASE_DIR", "LOG_DIR", "DailyBuildHedgeSystem", "logger"]
