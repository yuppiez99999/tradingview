# -*- coding: utf-8 -*-
"""T3.6 re-export 兼容层 — 已迁移到 utils/execution/rebalance_execution_orders.py.

原根目录文件已迁移到 ``utils/execution/`` 子目录 (2026-07-27).
本文件保留以兼容历史 ``from rebalance_execution_orders import ...`` 调用,
实际实现请参考 ``utils/execution/rebalance_execution_orders.py``.

HC-1 透传: 历史调用方无需修改 import 路径, 行为完全等价.
"""

from __future__ import annotations

# ============================================================
# T3.6 re-export 全部顶层符号 (常量 + 函数)
# ============================================================
try:
    from utils.execution.rebalance_execution_orders import (
        TARGET_ALLOCATION,
        MIN_TRADE_AMOUNT,
        MAX_SINGLE_ORDER_AMOUNT,
        MIN_LOT_SIZE,
        TARGET_TOTAL,
        load_positions,
        classify_style,
        calc_current_allocation,
        validate_order,
        generate_rebalance_orders,
        build_report,
        main,
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
        "rebalance_execution_orders.py",
    )
    if os.path.exists(_SRC):
        _spec = importlib.util.spec_from_file_location(
            "utils.execution.rebalance_execution_orders",
            _SRC,
        )
        _mod = importlib.util.module_from_spec(_spec)  # type: ignore
        _project_root = os.path.dirname(os.path.abspath(__file__))
        if _project_root not in sys.path:
            sys.path.insert(0, _project_root)
        _spec.loader.exec_module(_mod)  # type: ignore
        TARGET_ALLOCATION = _mod.TARGET_ALLOCATION  # type: ignore
        MIN_TRADE_AMOUNT = _mod.MIN_TRADE_AMOUNT  # type: ignore
        MAX_SINGLE_ORDER_AMOUNT = _mod.MAX_SINGLE_ORDER_AMOUNT  # type: ignore
        MIN_LOT_SIZE = _mod.MIN_LOT_SIZE  # type: ignore
        TARGET_TOTAL = _mod.TARGET_TOTAL  # type: ignore
        load_positions = _mod.load_positions  # type: ignore
        classify_style = _mod.classify_style  # type: ignore
        calc_current_allocation = _mod.calc_current_allocation  # type: ignore
        validate_order = _mod.validate_order  # type: ignore
        generate_rebalance_orders = _mod.generate_rebalance_orders  # type: ignore
        build_report = _mod.build_report  # type: ignore
        main = _mod.main  # type: ignore
    else:
        raise ImportError(
            f"T3.6 re-export 失败: 找不到 utils/execution/rebalance_execution_orders.py (expected at {_SRC}): {_e}"
        ) from _e

__all__ = [
    "MAX_SINGLE_ORDER_AMOUNT",
    "MIN_LOT_SIZE",
    "MIN_TRADE_AMOUNT",
    "TARGET_ALLOCATION",
    "TARGET_TOTAL",
    "build_report",
    "calc_current_allocation",
    "classify_style",
    "generate_rebalance_orders",
    "load_positions",
    "main",
    "validate_order",
]
