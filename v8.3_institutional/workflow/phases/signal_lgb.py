"""Phase 5 子模块: LightGBM 增强信号加载与置信度乘数 (从 daily_workflow.py 拆出, 零行为变更)。

原位置: daily_workflow.py
- _load_lgb_enhanced_signals (L1947-L1996)
- _lgb_confidence_multiplier (L1999-L2044, 静态)

搬移内容:
- load_lgb_enhanced_signals: 从 models/lgb_enhanced/lgb_enhanced_signals.json 加载当日信号
  (带新鲜度检查: trade_date 必须为今日)
- lgb_confidence_multiplier: LGB 信号 [-1,1] → 置信度乘数 [0.85, 1.08]
  (保守设计, 因 R² 普遍接近 0)

路径说明:
- 原代码 __file__ = v8.3_institutional/daily_workflow.py, 上溯 2 层到项目根
- 拆分后 __file__ = v8.3_institutional/workflow/phases/signal_lgb.py, 上溯 4 层到项目根
- signals_path = 项目根/models/lgb_enhanced/lgb_enhanced_signals.json
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger("v75.daily_workflow")


def load_lgb_enhanced_signals() -> dict[str, dict[str, Any]]:
    """加载 lgb_enhanced 增强模型信号文件

    从 models/lgb_enhanced/lgb_enhanced_signals.json 读取当日信号。
    若文件不存在或 trade_date 非今日, 返回空字典 (安全降级)。

    Returns:
        {code: {"signal": float, "quality_flag": str, "name": str}} 或 {}
    """
    try:
        # __file__ = v8.3_institutional/workflow/phases/signal_lgb.py
        # 上溯 4 层到项目根目录 (与原 daily_workflow.py 上溯 2 层等价)
        signals_path = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            ),
            "models",
            "lgb_enhanced",
            "lgb_enhanced_signals.json",
        )
        if not os.path.exists(signals_path):
            logger.debug("LGB增强信号文件不存在: %s", signals_path)
            return {}

        with open(signals_path, encoding="utf-8") as f:
            data = json.load(f)

        # 新鲜度检查: trade_date 必须是今日 (防止使用过期信号)
        file_date = data.get("trade_date", "")
        today_str = datetime.now().strftime("%Y-%m-%d")
        if file_date != today_str:
            logger.info(
                "LGB增强信号非今日 (文件: %s, 今日: %s), 跳过", file_date, today_str
            )
            return {}

        signals = data.get("signals", {})
        if not signals:
            return {}

        result = {}
        for code, info in signals.items():
            if not isinstance(info, dict):
                continue
            sig = info.get("signal")
            if sig is None:
                continue
            result[code] = {
                "signal": float(sig),
                "quality_flag": info.get("quality_flag", "OK"),
                "name": info.get("name", code),
            }
        logger.info(
            "LGB增强信号加载: %d 个标的 (trade_date=%s)", len(result), file_date
        )
        return result

    except Exception as e:  # fail-safe
        logger.warning("LGB增强信号加载失败: %s", e)
        return {}


def lgb_confidence_multiplier(
    signal_value: Optional[float],
    quality_flag: str = "OK",
) -> float:
    """将 lgb_enhanced 信号映射到置信度乘数

    信号为 tanh(pred*100), 范围 [-1, 1]:
    - 正值 = 看涨 (预测正收益)
    - 负值 = 看跌 (预测负收益)
    - |值| = 置信强度

    乘数设计 (保守, 因 R² 普遍接近 0):
    - 强看涨 (>=0.3) + OK → 1.08 (+8%)
    - 中看涨 (>=0.15) + OK → 1.04 (+4%)
    - 弱看涨 (>=0.05) + OK → 1.00 (中性)
    - 中性 (>=-0.05) → 0.98 (-2%)
    - 弱看跌 (>=-0.15) → 0.92 (-8%)
    - 强看跌 (<-0.15) → 0.85 (-15%, 风控)
    - LOW_QUALITY → 1.0 (忽略, 不影响)
    - None → 1.0 (无信号)

    Args:
        signal_value: 信号值 [-1, 1] 或 None
        quality_flag: OK / LOW_QUALITY

    Returns:
        置信度乘数 [0.85, 1.08]
    """
    if signal_value is None:
        return 1.0
    if quality_flag == "LOW_QUALITY":
        return 1.0
    try:
        sig = float(signal_value)
    except (TypeError, ValueError):
        return 1.0

    if sig >= 0.3:
        return 1.08
    if sig >= 0.15:
        return 1.04
    if sig >= 0.05:
        return 1.0
    if sig >= -0.05:
        return 0.98
    if sig >= -0.15:
        return 0.92
    return 0.85
