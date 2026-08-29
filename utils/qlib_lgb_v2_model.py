"""qlib_lgb_v2 真实模型信号加载器 (替代原随机数模拟)。

W7.2.9 补齐真实模型: 训练脚本 ms_strategy/cloud_train/modelscope_train.py
产出 reports/qlib_model_*.pkl + reports/predictions_*.csv。
本模块加载 predictions CSV (模型对 test 段的 OOS 预测) 作为 shadow 信号源,
避免运行时逐股重算 (qlib Alpha158 handler 初始化慢 ~80s)。

信号语义: score 为模型预测的未来 1 日收益方向, 范围约 [-0.5, 0.35]。
正看多, 负看空。数据时效 = CSV 内最新交易日 (当前 qlib_data 到 2026-07-08)。
"""

from __future__ import annotations

import glob
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REPORTS_DIR = _PROJECT_ROOT / "reports"

# 缓存: {qlib_instrument: latest_score}
_SIGNAL_CACHE: dict[str, float] = {}
_CACHE_LOADED = False


def _latest_predictions_csv() -> str | None:
    csvs = sorted(glob.glob(str(_REPORTS_DIR / "predictions_*.csv")))
    return csvs[-1] if csvs else None


def _latest_model_pkl() -> str | None:
    pkls = sorted(glob.glob(str(_REPORTS_DIR / "qlib_model_*.pkl")))
    return pkls[-1] if pkls else None


def _qlib_instrument_to_bridge(qlib_inst: str) -> str:
    """qlib 输出格式 (SZ000001) -> qlib_data_bridge 格式 (000001.SZ)。

    to_qlib_symbol() 返回 000001.SZ (点分隔), 而 predictions CSV 的
    instrument 列为 qlib 内部前缀格式 (SZ000001), 需统一才能查缓存。
    """
    if "." in qlib_inst:
        return qlib_inst  # 已是桥接格式
    prefix = qlib_inst[:2]
    code = qlib_inst[2:]
    suffix = {"SH": ".SH", "SZ": ".SZ", "BJ": ".BJ"}.get(prefix, "")
    return code + suffix if suffix else qlib_inst


def load_lgb_v2_signals(force: bool = False) -> dict[str, float]:
    """加载 qlib_lgb_v2 模型信号缓存。

    优先从 predictions CSV 读取 (模型 OOS 预测, 已含特征工程),
    失败则返回空 dict (调用方降级到随机数 fallback)。

    Returns:
        dict: {qlib_instrument(如 SH600519): score}
    """
    global _SIGNAL_CACHE, _CACHE_LOADED
    if _CACHE_LOADED and not force:
        return _SIGNAL_CACHE

    _SIGNAL_CACHE = {}
    csv_path = _latest_predictions_csv()
    if not csv_path:
        logger.warning("qlib_lgb_v2: 未找到 predictions CSV, 信号将降级")
        _CACHE_LOADED = True
        return _SIGNAL_CACHE

    try:
        import pandas as pd

        df = pd.read_csv(csv_path)
        # 每个 instrument 取最新可用交易日的 score
        latest = df.sort_values("datetime").groupby("instrument").tail(1)
        for _, row in latest.iterrows():
            bridge_code = _qlib_instrument_to_bridge(row["instrument"])
            _SIGNAL_CACHE[bridge_code] = float(row["score"])
        _CACHE_LOADED = True
        logger.info(
            "qlib_lgb_v2: 已加载 %d 只信号 (源=%s, 模型=%s)",
            len(_SIGNAL_CACHE),
            os.path.basename(csv_path),
            os.path.basename(_latest_model_pkl() or "NA"),
        )
    except (ImportError, ValueError, TypeError, KeyError, AttributeError, OSError) as e:
        logger.warning("qlib_lgb_v2: 信号加载失败 (%s), 降级到随机数", e)
        _CACHE_LOADED = True
    return _SIGNAL_CACHE


def get_lgb_v2_signal(qlib_code: str) -> float | None:
    """查询单只股票的 qlib_lgb_v2 信号。

    Args:
        qlib_code: qlib 格式代码 (如 SH600519)

    Returns:
        float | None: 信号强度, 未命中返回 None (调用方 fallback)
    """
    if not _CACHE_LOADED:
        load_lgb_v2_signals()
    return _SIGNAL_CACHE.get(qlib_code)


def model_meta() -> dict:
    """返回模型元信息 (供 shadow 报告记录真实状态)。"""
    pkl = _latest_model_pkl()
    csv = _latest_predictions_csv()
    return {
        "model_path": os.path.basename(pkl) if pkl else None,
        "predictions_path": os.path.basename(csv) if csv else None,
        "n_signals": len(_SIGNAL_CACHE) if _CACHE_LOADED else 0,
        "data_cutoff": "2026-07-08",
    }
