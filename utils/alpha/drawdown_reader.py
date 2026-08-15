"""
回撤读取器 (Drawdown Reader)
==============================

从 output/shadow_account/shadow_state.json 读取 daily_nav 数据,
计算当前组合回撤, 供 VolRegimeWeighter.sense_regime 使用.

设计原则:
    - fail-safe: 数据不可用时返回 None, VolRegimeWeighter 降级到 neutral
    - 复用现有数据: 不重复造轮子, 直接读取 ShadowAccount 已维护的状态
    - 无副作用: 只读, 不修改 shadow_state.json

用法:
    from utils.alpha.drawdown_reader import DrawdownReader
    dd = DrawdownReader().get_current_drawdown()
    # dd = 0.0352 表示当前回撤 3.52% (正数); None 表示数据不可用
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ============================================================
# 路径常量
# ============================================================
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SHADOW_STATE_PATH = _PROJECT_ROOT / "output" / "shadow_account" / "shadow_state.json"


class DrawdownReader:
    """从 shadow_state.json 读取并计算当前组合回撤.

    属性:
        SHADOW_STATE_PATH: shadow_state.json 文件路径
    """

    SHADOW_STATE_PATH = _SHADOW_STATE_PATH

    def __init__(self, state_path: Optional[Path] = None) -> None:
        """初始化.

        Args:
            state_path: 自定义 shadow_state.json 路径 (测试用), None 时用默认路径
        """
        self._state_path = state_path or _SHADOW_STATE_PATH

    def get_current_drawdown(self) -> Optional[float]:
        """返回当前回撤百分比 (正数, 如 0.0352 表示 3.52%).

        计算逻辑:
            1. 读取 shadow_state.json 的 daily_nav 数组
            2. peak_nav = max(所有 nav 值)
            3. current_nav = 最后一条记录的 nav
            4. drawdown = (peak_nav - current_nav) / peak_nav
            5. 返回 abs(drawdown) (正数)

        Returns:
            回撤百分比 (正数, 0~1) 或 None (数据不可用时)
        """
        peak_current = self.get_peak_and_current()
        if peak_current is None:
            return None

        peak_nav, current_nav = peak_current
        if peak_nav <= 0:
            logger.warning("peak_nav <= 0: %s, 无法计算回撤", peak_nav)
            return None

        drawdown = (peak_nav - current_nav) / peak_nav
        # 处理浮点精度: 当前值略高于峰值时回撤为负, 取 0
        return abs(max(drawdown, 0.0))

    def get_peak_and_current(self) -> Optional[Tuple[float, float]]:
        """返回 (peak_nav, current_nav) 元组.

        Returns:
            (peak_nav, current_nav) 或 None (数据不可用时)
        """
        try:
            if not self._state_path.exists():
                logger.debug("shadow_state.json 不存在: %s", self._state_path)
                return None

            with self._state_path.open("r", encoding="utf-8") as f:
                state = json.load(f)

            daily_nav = state.get("daily_nav", [])

            # 提取所有 nav 值
            nav_values: list[float] = []
            for record in daily_nav:
                nav = record.get("nav")
                if nav is not None:
                    try:
                        nav_values.append(float(nav))
                    except (TypeError, ValueError):
                        continue

            if nav_values:
                peak_nav = max(nav_values)
                current_nav = nav_values[-1]
                logger.debug(
                    "回撤数据: peak=%.6f, current=%.6f, drawdown=%.4f%%",
                    peak_nav, current_nav,
                    (peak_nav - current_nav) / peak_nav * 100 if peak_nav > 0 else 0,
                )
                return (peak_nav, current_nav)

            # 备选: daily_nav 为空或 nav 全为 None 时, 从 current_nav 字段读取
            current_nav_field = state.get("current_nav")
            if current_nav_field is not None:
                try:
                    current = float(current_nav_field)
                    # 无历史 nav 时, peak = current (回撤 = 0)
                    logger.debug("daily_nav 为空, 从 current_nav 备选读取: %.6f", current)
                    return (current, current)
                except (TypeError, ValueError):
                    pass

            logger.debug("daily_nav 和 current_nav 均不可用")
            return None

        except (OSError, json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning("读取 shadow_state.json 失败: %s", e)
            return None

    def get_drawdown_details(self) -> Optional[dict]:
        """返回回撤详情 (调试用).

        Returns:
            {
                "peak_nav": float,
                "current_nav": float,
                "drawdown_pct": float,  # 正数
                "peak_date": str,       # peak 对应日期
                "current_date": str,    # 当前日期
                "total_days": int,      # daily_nav 记录数
            } 或 None
        """
        try:
            if not self._state_path.exists():
                return None

            with self._state_path.open("r", encoding="utf-8") as f:
                state = json.load(f)

            daily_nav = state.get("daily_nav", [])
            if not daily_nav:
                return None

            # 找到 peak 和 current
            peak_nav = -1.0
            peak_date = ""
            current_nav = 0.0
            current_date = ""

            for record in daily_nav:
                nav = record.get("nav")
                date = record.get("date", "")
                if nav is None:
                    continue
                try:
                    nav_float = float(nav)
                except (TypeError, ValueError):
                    continue

                if nav_float > peak_nav:
                    peak_nav = nav_float
                    peak_date = date
                current_nav = nav_float
                current_date = date

            if peak_nav <= 0:
                return None

            drawdown = (peak_nav - current_nav) / peak_nav

            return {
                "peak_nav": peak_nav,
                "current_nav": current_nav,
                "drawdown_pct": abs(max(drawdown, 0.0)),
                "peak_date": peak_date,
                "current_date": current_date,
                "total_days": len(daily_nav),
            }

        except (OSError, json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning("读取回撤详情失败: %s", e)
            return None


# ============================================================
# 便捷函数
# ============================================================
def get_current_drawdown() -> Optional[float]:
    """便捷函数: 获取当前回撤百分比.

    Returns:
        回撤百分比 (正数) 或 None

    Usage:
        >>> from utils.alpha.drawdown_reader import get_current_drawdown
        >>> dd = get_current_drawdown()
    """
    return DrawdownReader().get_current_drawdown()


__all__ = ["DrawdownReader", "get_current_drawdown"]
