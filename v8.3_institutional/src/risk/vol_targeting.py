# v7.6 波动率目标控制引擎 -- 桥水全天候 / AQR 标准
# 对应当前最大痛点: 实盘波动 62.5% vs 目标 12%, 差 5.2 倍
from __future__ import annotations
import math
import logging
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, List

import numpy as np

logger = logging.getLogger("v76.risk.vol_targeting")

@dataclass
class VolTargetConfig:
    target_ann_vol: float = 0.12
    max_leverage: float = 2.0
    min_leverage: float = 0.25
    ewma_window: int = 20
    ewma_lambda: float = 0.94
    garch_omega: float = 0.000002
    garch_alpha: float = 0.05
    garch_beta: float = 0.90
    smoothing_alpha: float = 0.30
    max_daily_scale_change: float = 0.15
    scale_floor: float = 0.25
    scale_ceiling: float = 2.00
    dd_max_scale_reduction: float = 0.50  # dd > 10% 时再打半折


class VolTargetingEngine:
    def __init__(self, config: Optional[VolTargetConfig] = None):
        self.cfg = config or VolTargetConfig()
        self._returns: Deque[float] = deque(maxlen=self.cfg.ewma_window)
        self._garch_sigma2: float = (self.cfg.target_ann_vol ** 2) / 252
        self.smoothed_scale: float = 1.0
        self.current_ann_vol: float = self.cfg.target_ann_vol
        logger.info("VolTargeting 就绪: target=%.0f%%", self.cfg.target_ann_vol * 100)

    def update(self, daily_return: float, drawdown_from_hwm: float = 0.0) -> float:
        self._returns.append(float(daily_return))
        if len(self._returns) < 5:
            return 1.0

        # 双模型融合: EWMA(稳) + GARCH(灵)
        ewma_vol = self._estimate_ewma()
        garch_vol = self._estimate_garch(float(daily_return))
        realized_vol = max(ewma_vol * 0.60 + garch_vol * 0.40, 0.01)
        self.current_ann_vol = realized_vol

        raw_scale = max(self.cfg.min_leverage,
                        min(self.cfg.max_leverage,
                            self.cfg.target_ann_vol / realized_vol))

        # 平滑 + 变动限制
        alpha = self.cfg.smoothing_alpha
        new_scale = alpha * raw_scale + (1 - alpha) * self.smoothed_scale
        if self.smoothed_scale > 0:
            chg = new_scale / self.smoothed_scale - 1
            cap = self.cfg.max_daily_scale_change
            if abs(chg) > cap:
                new_scale = self.smoothed_scale * (1 + math.copysign(cap, chg))

        # 回撤保护: dd > 10% → 额外收缩 50%
        dd = abs(drawdown_from_hwm)
        if dd > 0.10:
            new_scale = min(new_scale, new_scale * self.cfg.dd_max_scale_reduction)

        self.smoothed_scale = max(self.cfg.scale_floor,
                                  min(self.cfg.scale_ceiling, new_scale))
        return self.smoothed_scale

    def _estimate_ewma(self) -> float:
        if len(self._returns) < 2:
            return self.cfg.target_ann_vol
        returns = np.array(list(self._returns))
        w = np.array([self.cfg.ewma_lambda ** i for i in range(len(returns) - 1, -1, -1)])
        w /= w.sum()
        daily_var = np.sum(w * (returns - returns.mean()) ** 2)
        return math.sqrt(daily_var * 252)

    def _estimate_garch(self, ret: float) -> float:
        omega, alpha, beta = self.cfg.garch_omega, self.cfg.garch_alpha, self.cfg.garch_beta
        self._garch_sigma2 = omega + alpha * ret**2 + beta * self._garch_sigma2
        return math.sqrt(self._garch_sigma2 * 252)

    def report(self) -> dict:
        return {
            'current_scale': round(self.smoothed_scale, 4),
            'realized_ann_vol': round(self.current_ann_vol, 4),
            'vol_ratio': round(self.current_ann_vol / self.cfg.target_ann_vol, 2),
            'status': ('减仓' if self.smoothed_scale < 0.80
                       else '正常' if self.smoothed_scale < 1.20 else '加仓'),
        }
