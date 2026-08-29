"""
v7.5 Alpha 模块 — 多因子信号生成、因子库、信号融合

NOTE (2026-08-10 高价值资产集成专项):
    ``factor_library.FactorLibrary`` 已归档至 ``ms_strategy/_archive/factor_library_deprecated.py``
    (全仓库零生产消费, 功能由 ``utils/alpha_factor/`` 唯一真相源覆盖)。
    此处保留 fail-open 兼容导入, 避免破坏包初始化契约; 新代码应直接 import ``utils.alpha_factor``。
"""

try:
    from .factor_library import FactorLibrary  # noqa: F401
except Exception:  # noqa: BLE001
    # 归档后兼容占位: factor_library 已弃用, 不重新引入
    FactorLibrary = None  # type: ignore

from .signal_fusion import SignalFusion
from .signal_generator import SignalGenerator

__all__ = ["SignalFusion", "SignalGenerator"]
