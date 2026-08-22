"""TimesFM 2.5 时序基础模型封装 (v8.6+ 接入 Google Research TimesFM)


零样本时序预测: 价格/收益率/任意单变量序列
TimesFM 2.5: 200M 参数, ~800MB 磁盘, ~1.5GB RAM(CPU)/~1GB VRAM(GPU)
支持 1-16384 上下文点, 返回点预测 + 分位区间(10/20/50/80/90百分位)

安装: pip install -e .[timesfm]
模型权重首次使用时从 HuggingFace 自动下载 (~800MB) 缓存到 ~/.cache/huggingface/

参考: https://github.com/google-research/timesfm (ICML 2024)
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_MODEL: Any = None


def load_timesfm_model(
    batch_size: int = 32,
    max_context: int = 1024,
    max_horizon: int = 256,
) -> Any:
    """懒加载 TimesFM 2.5 模型 (首次调用时加载, 后续返回单例).

    Args:
        batch_size: per_core_batch_size
        max_context: 最大上下文长度 (≤16384)
        max_horizon: 最大预测步数 (≤1024)

    Returns:
        编译好的 TimesFM 2.5 模型实例
    """
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    try:
        import timesfm
        import torch
    except ImportError as e:
        raise ImportError(
            "timesfm/torch 未安装. 请运行: pip install -e .[timesfm] "
            "(或 pip install timesfm[torch]>=2.0.0)"
        ) from e

    torch.set_float32_matmul_precision("high")
    logger.info("加载 TimesFM 2.5 from HuggingFace (google/timesfm-2.5-200m-pytorch)...")
    _MODEL = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
        "google/timesfm-2.5-200m-pytorch"
    )
    _MODEL.compile(
        timesfm.ForecastConfig(
            max_context=max_context,
            max_horizon=max_horizon,
            normalize_inputs=True,
            use_continuous_quantile_head=True,
            force_flip_invariance=True,
            infer_is_positive=True,
            fix_quantile_crossing=True,
            per_core_batch_size=batch_size,
        )
    )
    logger.info("TimesFM 2.5 加载完成 (200M params)")
    return _MODEL


def forecast_series(
    values: Sequence[float],
    horizon: int = 20,
) -> dict[str, list[float]]:
    """零样本预测单序列.

    Args:
        values: 1D 序列 (价格/收益率/任意单变量)
        horizon: 预测步数

    Returns:
        {forecast, lower_90, lower_80, median, upper_80, upper_90}
    """
    model = load_timesfm_model()
    inputs = [np.asarray(values, dtype=np.float32)]
    point, quantiles = model.forecast(horizon=horizon, inputs=inputs)
    return {
        "forecast": point[0].tolist(),
        "lower_90": quantiles[0, :, 1].tolist(),
        "lower_80": quantiles[0, :, 2].tolist(),
        "median": quantiles[0, :, 5].tolist(),
        "upper_80": quantiles[0, :, 8].tolist(),
        "upper_90": quantiles[0, :, 9].tolist(),
    }


def forecast_returns(
    prices: Sequence[float],
    horizon: int = 20,
) -> dict[str, list[float]]:
    """预测收益率 (从价格序列).

    将价格转为对数收益率, 预测, 返回收益率预测 + 累计收益.
    适用于 A 股每日收盘价 → 未来 N 日收益率预测.

    Args:
        prices: 价格序列 (正数)
        horizon: 预测步数

    Returns:
        {forecast, lower_90, median, upper_90, cumulative_return}
    """
    prices_arr = np.asarray(prices, dtype=np.float32)
    if np.any(prices_arr <= 0):
        raise ValueError("prices 必须为正数 (对数收益率要求)")
    returns = np.diff(np.log(prices_arr))
    result = forecast_series(returns, horizon=horizon)
    result["cumulative_return"] = np.cumsum(result["forecast"]).tolist()
    return result


def forecast_batch(
    series_dict: dict[str, Sequence[float]],
    horizon: int = 20,
) -> dict[str, dict[str, list[float]]]:
    """批量预测多序列.

    Args:
        series_dict: {name: values}
        horizon: 预测步数

    Returns:
        {name: {forecast, lower_90, median, upper_90}}
    """
    model = load_timesfm_model()
    names = list(series_dict.keys())
    inputs = [np.asarray(series_dict[n], dtype=np.float32) for n in names]
    point, quantiles = model.forecast(horizon=horizon, inputs=inputs)
    results: dict[str, dict[str, list[float]]] = {}
    for i, name in enumerate(names):
        results[name] = {
            "forecast": point[i].tolist(),
            "lower_90": quantiles[i, :, 1].tolist(),
            "median": quantiles[i, :, 5].tolist(),
            "upper_90": quantiles[i, :, 9].tolist(),
        }
    return results


def is_available() -> bool:
    """检查 timesfm 是否可用 (已安装)."""
    try:
        import timesfm  # noqa: F401
        return True
    except ImportError:
        return False
