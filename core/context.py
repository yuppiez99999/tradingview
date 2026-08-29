"""core.context — 统一上下文聚合层

为 cli/modes/ 下的所有模式提供统一的导入入口。
从各模块聚合: logger, BASE_DIR, ProgressIndicator, strategy_registry, data_provider 等。

修复日期: 2026-08-04
修复原因: core/ 目录缺失, cli/modes/__init__.py 导入 hypothesis.py 时
          from core.context import 失败 (ModuleNotFoundError: No module named 'core'),
          导致整个 CLI 入口不可用。

设计原则:
    1. 容错: 每个组件用 try/except 导入, 失败则提供降级默认值
    2. 不自动实例化重量级组件 (auto_trading/daily_report/stop_loss 仅导出类或 None)
    3. 优先从 quant_modules.core 导入, 降级到 utils/ 各模块
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

# ============================================================
# 基础: BASE_DIR, logger
# ============================================================
BASE_DIR = Path(__file__).resolve().parent.parent
logger = logging.getLogger("quant_system")


# ============================================================
# ProgressIndicator
# ============================================================
try:
    from quant_modules.core import ProgressIndicator
except (ImportError, ModuleNotFoundError):
    # 降级: 简单的进度指示器
    class ProgressIndicator:
        """简易进度指示器 (降级版)"""

        def __init__(self, title: str, total_steps: int) -> None:
            self.title = title
            self.total = total_steps
            self.current = 0

        def update(self, step: int, message: str) -> None:
            self.current = step

        def complete(self, message: str = "完成") -> None:
            pass


# ============================================================
# StrategyRegistry + strategy_registry 单例
# ============================================================
try:
    from quant_modules.core import StrategyRegistry

    strategy_registry = StrategyRegistry()
except (ImportError, ModuleNotFoundError):
    # 降级: 从 utils.infra.core 导入
    try:
        from utils.infra.core import registry as strategy_registry  # type: ignore[assignment]
    except (ImportError, ModuleNotFoundError):
        # 最终降级: 空的注册表 (hypothesis.py 等模式仍可导入, 但功能受限)
        class _DummyRegistry:
            """降级策略注册表 (无实际功能, 仅保证导入不报错)"""

            hypotheses: dict = {}

            def list_hypotheses(self) -> list:
                return []

            def register_hypothesis(self, hyp_id: str, data: dict) -> None:
                pass

            def get_hypothesis(self, hyp_id: str) -> Any:
                return None

            def register_strategy(self, name: str, strategy: Any) -> None:
                pass

            def get_strategy(self, name: str) -> Any:
                return None

            def get(self, name: str) -> Any:
                raise KeyError(name)

            def list(self) -> list:
                return []

        strategy_registry = _DummyRegistry()  # type: ignore[assignment]


# ============================================================
# get_archive_dir
# ============================================================
try:
    from utils.report_archiver import get_archive_dir
except (ImportError, ModuleNotFoundError):

    def get_archive_dir() -> Path:
        """降级: 返回默认归档目录"""
        return BASE_DIR / "reports" / "archive"


# ============================================================
# get_ai_coordinator
# ============================================================
try:
    from utils.ai_coordinator import get_ai_coordinator
except (ImportError, ModuleNotFoundError):

    def get_ai_coordinator() -> Any:
        """降级: AI 协调器不可用"""
        return None


# ============================================================
# data_provider
# ============================================================
data_provider: Any = None
try:
    from quant_modules.core import ModuleLoader

    _loader = ModuleLoader()
    data_provider = _loader.load("wind_data_provider", {})
except (ImportError, ModuleNotFoundError, Exception):
    pass


# ============================================================
# config_manager
# ============================================================
config_manager: Any = None
try:
    from quant_modules.core import ConfigManager

    config_manager = ConfigManager()
except (ImportError, ModuleNotFoundError):
    pass


# ============================================================
# rebalance_engine
# ============================================================
rebalance_engine: Any = None
try:
    from quant_modules.core import rebalance_engine  # type: ignore[assignment]
except (ImportError, ModuleNotFoundError):
    pass


# ============================================================
# connector_manager
# ============================================================
connector_manager: Any = None
try:
    from quant_modules.core import connector_manager  # type: ignore[assignment]
except (ImportError, ModuleNotFoundError):
    pass


# ============================================================
# auto_trading (仅导出类, 不自动实例化)
# ============================================================
auto_trading: Any = None


# ============================================================
# daily_report (仅导出类, 不自动实例化)
# ============================================================
daily_report: Any = None


# ============================================================
# stop_loss (仅导出类, 不自动实例化)
# ============================================================
stop_loss: Any = None


# ============================================================
# ETFFundFlowMonitor
# ============================================================
try:
    from utils.etf_flow_monitor import ETFFundFlowMonitor  # type: ignore[assignment]
except (ImportError, ModuleNotFoundError):
    try:
        # 备选路径: ui/pages 下的 Streamlit 页面
        import importlib

        _mod = importlib.import_module("ui.pages.06_💰_ETF资金流向")
        ETFFundFlowMonitor = getattr(_mod, "ETFFundFlowMonitor", None)  # type: ignore[assignment]
    except (ImportError, ModuleNotFoundError):
        ETFFundFlowMonitor = None  # type: ignore[assignment]


# ============================================================
# ML_ENHANCED_TRAINER_AVAILABLE
# ============================================================
ML_ENHANCED_TRAINER_AVAILABLE: bool = False
try:
    from quant_modules.core import ML_ENHANCED_TRAINER_AVAILABLE  # type: ignore[assignment]
except (ImportError, ModuleNotFoundError):
    pass


# ============================================================
# 公共 API
# ============================================================
__all__ = [
    "BASE_DIR",
    "logger",
    "ProgressIndicator",
    "StrategyRegistry",
    "strategy_registry",
    "get_archive_dir",
    "get_ai_coordinator",
    "data_provider",
    "config_manager",
    "rebalance_engine",
    "connector_manager",
    "auto_trading",
    "daily_report",
    "stop_loss",
    "ETFFundFlowMonitor",
    "ML_ENHANCED_TRAINER_AVAILABLE",
]
