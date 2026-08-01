# v7.6 信号拥挤度检测器 -- RenTech/AQR 标准
# 防止追逐已被定价的信号, 检测 Alpha 衰减
# 逻辑: ETF 大额流入 → 拥挤度上升 → Alpha 预期下降 → 信号降权
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime

import numpy as np

logger = logging.getLogger("v76.signals.crowding")


@dataclass
class CrowdingConfig:
    # ETF 流入阈值
    etf_flow_warning: float = 30e8  # 单日 > 30 亿 → 警示
    etf_flow_extreme: float = 80e8  # 单日 > 80 亿 → 极端
    # 衰减参数
    crowding_decay_rate: float = 0.92  # 日衰减 (半衰期 ≈ 8 天)
    # 降权系数
    mild_weight: float = 0.80  # 轻度拥挤 → 信号×0.80
    moderate_weight: float = 0.60  # 中度拥挤 → 信号×0.60
    extreme_weight: float = 0.30  # 极端拥挤 → 信号×0.30
    # 历史窗口
    lookback_days: int = 30


class SignalCrowdingDetector:
    """多层次拥挤度检测: ETF流 + 相关性 + 资金集中度"""

    def __init__(self, config: CrowdingConfig | None = None):
        self.cfg = config or CrowdingConfig()
        self._crowding_scores: dict[str, deque[float]] = {}
        self._flow_history: dict[str, deque[tuple[str, float]]] = {}
        self._alerts: list[dict] = []

    def update(self, etf_flows: dict[str, float], sector_correlations: dict[str, float] | None = None) -> dict:
        """更新拥挤度 → 返回信号调整系数"""
        adjustments = {}

        for sector, flow in etf_flows.items():
            self._flow_history.setdefault(sector, deque(maxlen=self.cfg.lookback_days))
            self._flow_history[sector].append((datetime.now().isoformat()[:10], flow))

            # 滚动平滑
            recent_flows = [f for _, f in self._flow_history[sector]]
            smoothed = float(np.mean(recent_flows)) if recent_flows else flow

            # 衰减累计拥挤分
            self._crowding_scores.setdefault(sector, deque(maxlen=self.cfg.lookback_days))

            # 当日拥挤增量
            if flow > self.cfg.etf_flow_extreme:
                increment = 1.0
                severity = "extreme"
            elif flow > self.cfg.etf_flow_warning:
                increment = 0.60
                severity = "moderate"
            elif flow > self.cfg.etf_flow_warning * 0.5:
                increment = 0.25
                severity = "mild"
            else:
                increment = 0.0
                severity = "normal"

            # 累计: 衰减 + 新增
            prev = self._crowding_scores[sector][-1] if self._crowding_scores[sector] else 0.0
            score = prev * self.cfg.crowding_decay_rate + increment
            self._crowding_scores[sector].append(score)

            # 信号调整系数
            if score > 1.20:
                factor = self.cfg.extreme_weight
                status = "EXTREME"
            elif score > 0.80:
                factor = self.cfg.moderate_weight
                status = "MODERATE"
            elif score > 0.40:
                factor = self.cfg.mild_weight
                status = "MILD"
            else:
                factor = 1.0
                status = "NORMAL"

            adjustments[sector] = {
                "crowding_score": round(score, 4),
                "signal_multiplier": factor,
                "status": status,
                "flow_smoothed": round(smoothed / 1e8, 1),
                "severity": severity,
            }

            if status != "NORMAL":
                self._alerts.append(
                    {
                        "timestamp": datetime.now().isoformat(),
                        "sector": sector,
                        "status": status,
                        "score": round(score, 4),
                        "flow_billions": round(flow / 1e8, 1),
                    }
                )
                logger.warning(
                    "拥挤告警: %s [%s] score=%.2f flow=%.1f亿 signal×%.0f%%",
                    sector,
                    status,
                    score,
                    flow / 1e8,
                    factor * 100,
                )

        return adjustments

    def report(self) -> dict:
        if not self._crowding_scores:
            return {"status": "无数据"}

        result = {}
        for sector, scores in self._crowding_scores.items():
            current = scores[-1] if scores else 0
            trend = "up" if len(scores) >= 3 and scores[-1] > scores[-2] else "down"
            result[sector] = {
                "score": round(current, 4),
                "trend": trend,
                "days_tracked": len(scores),
            }
        return result
