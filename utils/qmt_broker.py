"""
qmt_broker re-export shim
=========================
创建日期: 2026-07-26
创建原因: 顶级对冲基金审计 P1-C 发现 — qmt_broker.py 实际位于
         ms_strategy/src/execution/qmt_broker.py，但 README 多处引用
         utils/qmt_broker.py 路径，导致 import 路径不一致。

本文件作为 re-export shim，将 ms_strategy/src/execution/qmt_broker.py 的
所有公共 API 重新导出，使 `from utils.qmt_broker import XXX` 与
`from ms_strategy.src.execution.qmt_broker import XXX` 等价。

修复依据: docs/HEDGE_FUND_AUDIT_2026-07-26_SYSTEM_BUGS_AND_AUTOMATION.md P1-C

注意: 若 ms_strategy 模块结构发生变化，需同步更新本 shim。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 将 ms_strategy 路径加入 sys.path 以便 import
_BASE_DIR = Path(__file__).resolve().parent.parent
_MS_STRATEGY_DIR = _BASE_DIR / "ms_strategy"
if str(_MS_STRATEGY_DIR) not in sys.path:
    sys.path.insert(0, str(_MS_STRATEGY_DIR))

try:
    from ms_strategy.src.execution.qmt_broker import *  # noqa: F403
    from ms_strategy.src.execution.qmt_broker import (
        __name__ as _upstream_name,  # noqa: F401
    )

    # 显式重新导出常见 API（基于实际 qmt_broker.py 内容）
    # 注意：使用 module 级 __all__ 已通过 * 导出，此处显式列出便于 IDE 提示
except ImportError as _e:
    import logging

    logging.getLogger("utils.qmt_broker").warning(
        "qmt_broker re-export shim: 无法从 ms_strategy.src.execution.qmt_broker 导入 — %s",
        _e,
    )
    raise

# 标记为 shim 文件
__file_shim__ = True
__upstream_path__ = str(
    _BASE_DIR / "ms_strategy" / "src" / "execution" / "qmt_broker.py"
)
