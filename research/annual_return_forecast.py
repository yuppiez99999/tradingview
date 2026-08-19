"""
年化收益测算器 (Annual Return Forecast) — 兼容转发层
=====================================================

⚠️ 2026-08-09 G5 物理隔离: 本模块已迁移至 utils/annual_return_forecast.py
（原仅依赖 utils.risk_params, 无 research 内部依赖, 属纯生产级模块）。

本文件保留为兼容转发壳, 仅供已归档脚本 (如 _archive/one_time_scripts/_verify_b13.py)
通过 `from research.annual_return_forecast import ...` 引用时使用, 避免破坏历史可追溯性。

生产代码 (如 system_health_check.py) 应直接 `from utils.annual_return_forecast import ...`。
"""
from utils.annual_return_forecast import *  # noqa: F401,F403 — 向后兼容转发
from utils.annual_return_forecast import (
    forecast_annual_return,
    main,
    print_forecast,
    write_to_trade_plan,
)

__all__ = [
    "forecast_annual_return",
    "write_to_trade_plan",
    "print_forecast",
    "main",
]
