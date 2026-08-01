# -*- coding: utf-8 -*-
"""T3.6 re-export 兼容层 — 已迁移到 utils/execution/automated_execution_system.py.

原根目录文件已迁移到 ``utils/execution/`` 子目录 (2026-07-27).
本文件保留以兼容历史 ``from automated_execution_system import ...`` 调用,
实际实现请参考 ``utils/execution/automated_execution_system.py``.

HC-1 透传: 历史调用方无需修改 import 路径, 行为完全等价.
"""

from __future__ import annotations

# ============================================================
# T3.6 re-export 全部顶层符号 (类 + 函数 + 模块级变量)
# ============================================================
try:
    from utils.execution.automated_execution_system import (
        AutomatedExecutionSystem,
        ExecutionStrategy,
        MarketStateEvaluator,
        OrderRouter,
        TradingCalendar,
        _to_wind_code,
        _HEDGE_AVAILABLE,  # noqa: F401
        _WIND_MCP_AVAILABLE,  # noqa: F401
        HedgeCoordinator,  # noqa: F401
        logger,  # noqa: F401
        wind_get_quote,  # noqa: F401
    )
except ImportError as _e:
    # 兜底: 当 utils 包不可导入时 (例如独立脚本运行), 尝试从同目录加载
    import importlib.util
    import os
    import sys

    _SRC = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "utils",
        "execution",
        "automated_execution_system.py",
    )
    if os.path.exists(_SRC):
        _spec = importlib.util.spec_from_file_location(
            "utils.execution.automated_execution_system",
            _SRC,
        )
        _mod = importlib.util.module_from_spec(_spec)  # type: ignore
        # 注入 sys.path 以便模块内的相对导入可用
        _project_root = os.path.dirname(os.path.abspath(__file__))
        if _project_root not in sys.path:
            sys.path.insert(0, _project_root)
        _spec.loader.exec_module(_mod)  # type: ignore
        AutomatedExecutionSystem = _mod.AutomatedExecutionSystem  # type: ignore
        ExecutionStrategy = _mod.ExecutionStrategy  # type: ignore
        MarketStateEvaluator = _mod.MarketStateEvaluator  # type: ignore
        OrderRouter = _mod.OrderRouter  # type: ignore
        TradingCalendar = _mod.TradingCalendar  # type: ignore
        _to_wind_code = _mod._to_wind_code  # type: ignore
    else:
        raise ImportError(
            f"T3.6 re-export 失败: 找不到 utils/execution/automated_execution_system.py (expected at {_SRC}): {_e}"
        ) from _e

__all__ = [
    "AutomatedExecutionSystem",
    "ExecutionStrategy",
    "MarketStateEvaluator",
    "OrderRouter",
    "TradingCalendar",
    "_to_wind_code",
]
