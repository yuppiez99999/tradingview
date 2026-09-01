"""TimesFM 时序预测头 (S2 集成 Google TimesFM 2.5)

零样本时序基础模型, 200M 参数, 16k 上下文, 连续分位预测。
降级安全: timesfm 未安装/预检不满足/forecast 异常时 available=False, 调用方降级到 LightGBM。

详见 docs/集成记录/S2/spec_20260821.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

logger = logging.getLogger(__name__)


@dataclass
class ForecastResult:
    point: np.ndarray
    p10: np.ndarray | None = None
    p50: np.ndarray | None = None
    p90: np.ndarray | None = None
    available: bool = True
    model: str = "timesfm-2.5"
    horizon: int = 0


class TimesFMPredictor:
    """TimesFM 零样本时序预测器, 降级安全。"""

    def __init__(self, config_path: str = "config/timesfm_predictor.yaml"):
        self.config: dict[str, Any] = {}
        self.enabled = False
        self.available = False
        self._tfm: Any = None
        self._device: str = "cpu"

        try:
            cfg_path = Path(config_path)
            if not cfg_path.is_absolute():
                cfg_path = Path(__file__).parent.parent / config_path
            with open(cfg_path, encoding="utf-8") as f:
                self.config = yaml.safe_load(f) or {}
            self.enabled = bool(self.config.get("enabled", False))
            self._device = str(self.config.get("device", "auto"))
            if self.enabled:
                self._init_model()
            logger.info(
                "TimesFM 预测器 enabled=%s available=%s", self.enabled, self.available
            )
        except FileNotFoundError:
            logger.warning("TimesFM 配置不存在 %s, 旁路", config_path)
        except Exception as exc:
            logger.warning("TimesFM 初始化失败, 旁路: %s", exc)

    def _init_model(self) -> None:
        if not self.preflight_check():
            logger.warning("TimesFM 预检不通过, available=False")
            return
        try:
            import timesfm

            if self._device == "auto":
                self._device = "gpu" if self._has_gpu() else "cpu"
            version = str(self.config.get("model_version", "2.5"))
            if version == "2.5" and hasattr(timesfm, "TimesFM_2p5_200M_torch"):
                self._tfm = timesfm.TimesFM_2p5_200M_torch(torch_compile=False)
            else:
                self._tfm = timesfm.TimesFm(
                    hparams=timesfm.TimesFmHparams(
                        backend="gpu" if self._device == "gpu" else "torch",
                        per_core_batch_size=32,
                        horizon_len=0,
                        num_layers=50,
                        model_dims=2048,
                        context_len=int(self.config.get("context_length", 16384)),
                    ),
                    checkpoint=timesfm.TimesFmCheckpoint(
                        huggingface_repo_id="google/timesfm-1.0-200m-pytorch-torch"
                    ),
                )
                self._tfm.load_from_checkpoint()
            self.available = True
            logger.info(
                "TimesFM 模型加载成功 device=%s version=%s", self._device, version
            )
        except ImportError:
            logger.warning(
                "timesfm 包未安装, available=False (pip install timesfm[torch])"
            )
        except Exception as exc:
            logger.warning("TimesFM 模型加载失败, available=False: %s", exc)

    def preflight_check(self) -> bool:
        """检查 RAM/磁盘/GPU 是否满足加载条件。"""
        pf = self.config.get("preflight", {})
        min_ram = float(pf.get("min_ram_gb", 2.0))
        min_disk = float(pf.get("min_disk_gb", 1.0))

        try:
            import psutil

            ram_gb = psutil.virtual_memory().total / 1e9
            if ram_gb < min_ram:
                logger.warning("RAM %.1fGB < %.1fGB, TimesFM 预检失败", ram_gb, min_ram)
                return False
            ckpt_dir = Path(self.config.get("checkpoint", "models/timesfm/"))
            if not ckpt_dir.is_absolute():
                ckpt_dir = Path(__file__).parent.parent / ckpt_dir
            if ckpt_dir.exists():
                free_disk = psutil.disk_usage(str(ckpt_dir.parent)).free / 1e9
                if free_disk < min_disk:
                    logger.warning("磁盘 %.1fGB < %.1fGB", free_disk, min_disk)
                    return False
        except ImportError:
            logger.warning("psutil 未安装, 跳过 RAM/磁盘预检")
        return True

    @staticmethod
    def _has_gpu() -> bool:
        try:
            import torch

            return torch.cuda.is_available()
        except ImportError:
            return False

    def forecast(self, series: np.ndarray, horizon: int = 0) -> ForecastResult:
        """零样本时序预测, 返回点预测 + 分位区间。

        Args:
            series: 单变量时序 (价格/收益率), 1D array
            horizon: 预测步数, 0 用配置默认

        Returns:
            ForecastResult, available=False 时为降级占位
        """
        if horizon <= 0:
            horizon = int(self.config.get("horizon_default", 5))
        if not self.enabled or not self.available or self._tfm is None:
            return ForecastResult(
                point=np.zeros(horizon), available=False, horizon=horizon
            )

        arr = np.asarray(series, dtype=np.float64)
        if arr.ndim != 1 or len(arr) < 2:
            return ForecastResult(
                point=np.zeros(horizon), available=False, horizon=horizon
            )

        ctx_len = int(self.config.get("context_length", 16384))
        ctx = arr[-ctx_len:].copy()

        try:
            import timesfm

            if isinstance(self._tfm, getattr(timesfm, "TimesFM_2p5_200M_torch", type)):
                point_forecast, quantile_forecast = self._tfm.forecast(horizon, [ctx])
                point = (
                    np.asarray(point_forecast[0], dtype=np.float64)
                    if point_forecast.ndim > 1
                    else np.asarray(point_forecast, dtype=np.float64)
                )
                p10 = p50 = p90 = None
                if quantile_forecast is not None and quantile_forecast.size > 0:
                    q = (
                        np.asarray(quantile_forecast[0], dtype=np.float64)
                        if quantile_forecast.ndim > 2
                        else np.asarray(quantile_forecast, dtype=np.float64)
                    )
                    if q.ndim == 2 and q.shape[0] >= 3:
                        p10, p50, p90 = q[0], q[1], q[2]
            else:
                point_forecast, quantile_forecast = self._tfm.forecast([ctx], [horizon])
                point = np.asarray(point_forecast[0], dtype=np.float64)
                p10 = p50 = p90 = None
                if quantile_forecast is not None and len(quantile_forecast) > 0:
                    q = np.asarray(quantile_forecast[0], dtype=np.float64)
                    if q.ndim == 2 and q.shape[0] >= 3:
                        p10, p50, p90 = q[0], q[1], q[2]
            return ForecastResult(
                point=point,
                p10=p10,
                p50=p50,
                p90=p90,
                available=True,
                horizon=horizon,
            )
        except Exception as exc:
            logger.warning("TimesFM forecast 异常, 降级: %s", exc)
            return ForecastResult(
                point=np.zeros(horizon), available=False, horizon=horizon
            )

    def forecast_with_covariates(
        self,
        series: np.ndarray,
        xreg_dynamic: np.ndarray | None = None,
        xreg_static: dict[str, float] | None = None,
        horizon: int = 0,
    ) -> ForecastResult:
        """协变量预测 (XReg, 需 timesfm[xreg])。降级到普通 forecast。"""
        if horizon <= 0:
            horizon = int(self.config.get("horizon_default", 5))
        if xreg_dynamic is None and xreg_static is None:
            return self.forecast(series, horizon)
        if not self.available or self._tfm is None:
            return ForecastResult(
                point=np.zeros(horizon), available=False, horizon=horizon
            )
        try:
            import timesfm  # noqa: F401

            if isinstance(self._tfm, getattr(timesfm, "TimesFM_2p5_200M_torch", type)):
                point, _quantiles = self._tfm.forecast_with_covariates(
                    horizon,
                    [np.asarray(series, dtype=np.float64)],
                    dynamic_covariates=(
                        [np.asarray(xreg_dynamic, dtype=np.float64)]
                        if xreg_dynamic is not None
                        else None
                    ),
                    static_covariates=(
                        [xreg_static] if xreg_static is not None else None
                    ),
                )
            else:
                xreg = timesfm.TimesFmxreg()
                if xreg_dynamic is not None:
                    xreg.add_dynamic_covariate(
                        np.asarray(xreg_dynamic, dtype=np.float64)
                    )
                if xreg_static is not None:
                    for k, v in xreg_static.items():
                        xreg.add_static_covariate(k, float(v))
                point, _quantiles = self._tfm.forecast_with_xreg(
                    [np.asarray(series, dtype=np.float64)], [horizon], xreg
                )
            return ForecastResult(
                point=np.asarray(
                    (
                        point[0]
                        if hasattr(point, "__getitem__") and point.ndim > 1
                        else point
                    ),
                    dtype=np.float64,
                ),
                available=True,
                horizon=horizon,
            )
        except Exception as exc:
            logger.warning("TimesFM XReg 异常, 降级到普通 forecast: %s", exc)
            return self.forecast(series, horizon)

    def hybrid_blend(
        self, lgb_pred: float, timesfm_result: ForecastResult, alpha: float = 0.0
    ) -> float:
        """hybrid 融合: final = alpha * lgb + (1-alpha) * timesfm。

        Args:
            lgb_pred: LightGBM 点预测
            timesfm_result: TimesFM 预测结果
            alpha: LightGBM 权重, 0 用配置默认

        Returns:
            融合预测值; timesfm 不可用时返回 lgb_pred
        """
        if alpha <= 0:
            alpha = float(self.config.get("hybrid", {}).get("alpha", 0.6))
        if not timesfm_result.available or len(timesfm_result.point) == 0:
            return float(lgb_pred)
        tfm_pred = float(timesfm_result.point[0])
        return alpha * float(lgb_pred) + (1.0 - alpha) * tfm_pred
