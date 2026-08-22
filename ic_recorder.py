#!/usr/bin/env python3
"""IC 记录器 (N3): 每日计算并持久化 IC, 修复 Bug-B 数据流断裂.

背景: system_integration.py:_hook_drift_and_retrain 读取 reports/daily_ic_scores.json
      的 latest_ic 字段, 但全项目无任何代码写入该文件, 导致 IC 恒为 0, 漂移检测失效.

本模块职责:
  - 从信号历史计算当日 IC (Pearson 相关)
  - 写入 reports/daily_ic_scores.json (latest_ic + history)
  - 提供从 QLib 报告回退提取 IC 的能力

不重复造漂移检测轮子 — 漂移检测仍由 ModelDriftDetector 负责 (四要素齐全).
"""

import json
import logging
import os
from datetime import date, datetime
from typing import Optional

import numpy as np

_BASE = os.path.dirname(os.path.abspath(__file__))
IC_STORE_PATH = os.path.join(_BASE, "reports", "daily_ic_scores.json")

logger = logging.getLogger("ic_recorder")


def _ensure_store_dir() -> None:
    """确保存储目录存在"""
    os.makedirs(os.path.dirname(IC_STORE_PATH), exist_ok=True)


def compute_ic_from_signals(
    signal_history: list[dict],
    min_samples: int = 10,
) -> Optional[float]:
    """从信号历史计算当日 IC (Pearson 相关)

    Args:
        signal_history: [{'date': ..., 'predicted_return': ..., 'actual_return': ...}, ...]
        min_samples: 最少样本数, 不足返回 None

    Returns:
        IC 值 (float), 或 None 如果样本不足
    """
    pairs = [
        (float(s.get("predicted_return", 0)), float(s.get("actual_return", 0)))
        for s in signal_history
        if isinstance(s, dict) and "actual_return" in s
    ]
    if len(pairs) < min_samples:
        return None
    pred = np.array([p[0] for p in pairs], dtype=np.float64)
    actual = np.array([p[1] for p in pairs], dtype=np.float64)
    # 方差为 0 时相关无意义
    if pred.std() == 0 or actual.std() == 0:
        return 0.0
    ic = float(np.corrcoef(pred, actual)[0, 1])
    if np.isnan(ic):
        return 0.0
    return ic


def load_ic_store() -> dict:
    """加载 IC 存储

    Returns:
        {'latest_ic': float, 'latest_date': str, 'history': [{'date','ic','source'}]}
    """
    if not os.path.exists(IC_STORE_PATH):
        return {"latest_ic": 0.0, "history": []}
    try:
        with open(IC_STORE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, ValueError) as e:
        logger.warning(f"读取 IC 存储失败, 返回空: {e}")
        return {"latest_ic": 0.0, "history": []}


def record_daily_ic(
    ic_value: float,
    trade_date: Optional[date] = None,
    source: str = "signal_history",
) -> None:
    """记录当日 IC

    Args:
        ic_value: IC 值
        trade_date: 交易日期 (默认今天)
        source: IC 来源标记 (signal_history / qlib_report / test / ...)
    """
    _ensure_store_dir()
    trade_date = trade_date or date.today()
    store = load_ic_store()

    store["latest_ic"] = float(ic_value)
    store["latest_date"] = trade_date.isoformat()
    store["latest_source"] = source
    store["updated_at"] = datetime.now().isoformat()

    history: list[dict] = store.get("history", [])
    history.append(
        {
            "date": trade_date.isoformat(),
            "ic": float(ic_value),
            "source": source,
        }
    )
    # 保留最近 1 年 (252 交易日), 避免文件无限增长
    if len(history) > 252:
        history = history[-252:]
    store["history"] = history

    try:
        with open(IC_STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
        logger.info(f"IC 已记录: {ic_value:.4f} (date={trade_date}, source={source})")
    except (OSError, TypeError, ValueError) as e:
        # 不用 except: pass, 记录日志 (符合项目硬约束)
        logger.warning(f"写入 IC 存储失败: {e}")


def record_ic_from_qlib_report(report_path: str) -> Optional[float]:
    """从 QLib 训练报告提取 IC 并记录 (回退数据源)

    Args:
        report_path: QLib 报告 JSON 路径

    Returns:
        提取到的 IC 值, 或 None
    """
    if not os.path.exists(report_path):
        return None
    try:
        with open(report_path, encoding="utf-8") as f:
            report = json.load(f)
        mean_ic = float(report.get("mean_daily_ic", 0) or 0)
        record_daily_ic(mean_ic, source="qlib_report")
        return mean_ic
    except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as e:
        logger.warning(f"从 QLib 报告提取 IC 失败: {e}")
        return None


def get_latest_ic() -> float:
    """获取最新 IC 值 (便捷接口)"""
    return float(load_ic_store().get("latest_ic", 0.0) or 0.0)


if __name__ == "__main__":
    # 自检: 用 dummy 数据验证管道
    logger.info("=== ic_recorder 自检 ===")
    dummy_signals = [{"predicted_return": 0.01 * i, "actual_return": 0.012 * i} for i in range(15)]
    ic = compute_ic_from_signals(dummy_signals, min_samples=10)
    assert ic is not None and 0.0 <= ic <= 1.0, f"IC 计算异常: {ic}"
    logger.info(f"  compute_ic_from_signals: IC={ic:.4f} ✓")

    record_daily_ic(ic, trade_date=date(2026, 7, 29), source="self_test")
    store = load_ic_store()
    assert store["latest_ic"] == ic
    assert any(h["source"] == "self_test" for h in store["history"])
    logger.debug(f"  record/load: latest_ic={store['latest_ic']:.4f}, history_len={len(store['history'])} ✓")

    logger.info(f"  存储路径: {IC_STORE_PATH}")
    logger.info("=== 自检通过 ===")
