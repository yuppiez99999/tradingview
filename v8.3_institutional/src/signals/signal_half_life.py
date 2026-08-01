"""
v7.6 Signal Half-Life Manager — 信号半衰期管理

对标顶级量化基金的信号衰减管理:
  - 每一类 Alpha 信号都有独特的衰减周期
  - 高频信号 (minute-level): T_half = 几分钟~几小时
  - 低频信号 (daily-level): T_half = 几天~几周
  - 基本面信号 (quarterly): T_half = 几个月

核心功能:
  1. 信号自相关分析: 估计每路信号的半衰期
  2. 衰减模型: 按半衰期指数衰减权重
  3. 时效性检查: 过期信号自动降权或剔除
  4. 信号新鲜度评分: 综合评估当前信号状态
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

logger = logging.getLogger("v76.signals.half_life")


@dataclass
class SignalHalfLife:
    """信号半衰期信息"""

    signal_name: str
    half_life: float  # 半衰期 (天)
    estimated_at: str  # 估计时间
    method: str  # 'autocorr' | 'variance_ratio' | 'fixed'
    decay_rate: float  # λ = ln(2) / half_life
    r2: float = 0.0  # 拟合 R²
    is_stable: bool = True  # 半衰期是否稳定

    # 信号类别
    category: str = "unknown"  # 'technical' | 'fundamental' | 'sentiment' | 'macro' | 'alternative'

    # 推荐使用策略
    max_valid_days: int = 10  # 信号最大有效天数 (3 × half_life)
    weight_decay_method: str = "exponential"
    min_retain_weight: float = 0.05  # 低于此权重丢弃


class SignalHalfLifeManager:
    """
    v7.6 信号半衰期管理器

    对标 Two Sigma / WorldQuant / AQR 信号管理流程:
      1. 信号生成 → 自动记录时间戳和初始强度
      2. 半衰期估计 → 基于自相关/方差比估计
      3. 衰减应用 → 权重 = exp(-λ × age)
      4. 过期清理 → 超过 3×T_half 的信号剔除
      5. 新鲜度评分 → 综合所有信号的时效性

    Usage:
        mgr = SignalHalfLifeManager()
        mgr.register_signal("momentum_20d", category="technical", half_life=5.0)
        decayed_weight = mgr.decay_weight("momentum_20d", 0.8, days_ago=3)
    """

    def __init__(self, default_max_signals: int = 50):
        self.default_max_signals = default_max_signals
        self.signals: dict[str, SignalHalfLife] = {}
        self.signal_snapshots: dict[str, list[dict]] = {}
        self.decay_log: list[dict] = []
        self.active_signals: dict[str, dict] = {}

    # ---------- 注册与估计 ----------
    def register_signal(
        self,
        signal_name: str,
        category: str,
        half_life: float | None = None,
        max_valid_days: int | None = None,
        min_retain_weight: float = 0.05,
    ) -> SignalHalfLife:
        """注册新信号

        如果未提供 half_life, 将标记为通过数据估计
        """
        if half_life is None:
            decay_rate = 0.0
            max_valid = 3
        else:
            decay_rate = np.log(2) / max(half_life, 0.01)
            max_valid = max_valid_days or int(3 * half_life)

        meta = SignalHalfLife(
            signal_name=signal_name,
            half_life=half_life or 0,
            estimated_at=datetime.now().isoformat(),
            method="fixed" if half_life else "unknown",
            decay_rate=round(decay_rate, 6),
            category=category,
            max_valid_days=max_valid,
            min_retain_weight=min_retain_weight,
        )
        self.signals[signal_name] = meta
        self.signal_snapshots[signal_name] = []
        return meta

    def estimate_half_life(
        self, signal_name: str, signal_values: pd.Series, method: str = "autocorr", max_lag: int = 60
    ) -> SignalHalfLife:
        """从历史数据估计半衰期

        Args:
            signal_name: 信号名称
            signal_values: 信号时间序列 (按时间排序)
            method: 'autocorr' (自相关) 或 'variance_ratio' (方差比)
            max_lag: 最大滞后天数

        Returns:
            SignalHalfLife 对象
        """
        if len(signal_values) < max_lag:
            logger.warning(f"{signal_name}: 数据不足 {len(signal_values)} < {max_lag}")
            return self.register_signal(signal_name, "unknown", half_life=5.0)

        values = signal_values.dropna().values

        if method == "autocorr":
            half_life, decay_rate, r2 = self._estimate_via_autocorr(values, max_lag)
        elif method == "variance_ratio":
            half_life, decay_rate, r2 = self._estimate_via_variance_ratio(values, max_lag)
        else:
            half_life, decay_rate, r2 = 5.0, np.log(2) / 5.0, 0

        half_life = max(0.5, min(half_life, 252))  # 0.5~252 天
        decay_rate = np.log(2) / half_life

        meta = SignalHalfLife(
            signal_name=signal_name,
            half_life=round(half_life, 2),
            estimated_at=datetime.now().isoformat(),
            method=method,
            decay_rate=round(decay_rate, 6),
            r2=round(r2, 4),
            category=getattr(self.signals.get(signal_name), "category", "unknown"),
            max_valid_days=int(3 * half_life),
        )
        self.signals[signal_name] = meta
        logger.info(f"{signal_name}: T_half={half_life:.1f}d, λ={decay_rate:.4f}, R²={r2:.3f}")
        return meta

    def _estimate_via_autocorr(self, values: np.ndarray, max_lag: int) -> tuple[float, float, float]:
        """自相关法估计半衰期

        方法: 计算 seq=values 的一阶自回归系数 ρ
             然后求 AR(1) 过程半衰期 = -ln(2)/ln(|ρ|)
        """
        if len(values) < 2:
            return 5.0, np.log(2) / 5.0, 0.0

        # 计算不同滞后的自相关
        acf = []
        lags = list(range(1, min(max_lag + 1, len(values) - 1)))
        for lag in lags:
            x = values[:-lag]
            y = values[lag:]
            if len(x) > 0:
                corr = np.corrcoef(x, y)[0, 1]
                if not np.isnan(corr):
                    acf.append((lag, abs(corr)))

        if not acf:
            return 5.0, np.log(2) / 5.0, 0.0

        # 取最前 20 个 lags 估计 ρ
        acf_arr = np.array(acf)
        rho = np.mean(acf_arr[: min(20, len(acf_arr)), 1])

        if rho <= 0 or rho >= 1:
            return 5.0, np.log(2) / 5.0, 0.0

        half_life = -np.log(2) / np.log(rho)

        # R²: 用 AR(1) 拟合的残差
        # 简化: 用 (1-rho)^2 近似
        r2 = rho**2

        return float(half_life), float(np.log(2) / half_life), float(r2)

    def _estimate_via_variance_ratio(self, values: np.ndarray, max_lag: int) -> tuple[float, float, float]:
        """方差比法估计半衰期

        方差比 = var(k-period returns) / (k * var(1-period returns))
        均值回复速度 = 方差比偏离 1 的速度
        """
        if len(values) < 3:
            return 5.0, np.log(2) / 5.0, 0.0

        changes = np.diff(values)

        if len(changes) < 2:
            return 5.0, np.log(2) / 5.0, 0.0

        var_1 = np.var(changes) + 1e-10

        vr_list = []
        for k in range(2, min(max_lag + 1, len(changes))):
            k_changes = values[k:] - values[:-k]
            if len(k_changes) > 1:
                var_k = np.var(k_changes) + 1e-10
                vr = var_k / (k * var_1)
                vr_list.append((k, vr))

        if not vr_list:
            return 5.0, np.log(2) / 5.0, 0.0

        # 找方差比降至 0.5 的 lag
        target_lag = None
        for k, vr in vr_list:
            if vr < 0.5:
                target_lag = k
                break

        if target_lag is None:
            target_lag = vr_list[-1][0]

        # 半衰期 ~ 目标 lag / 2 (启发式)
        half_life = target_lag / 2.0

        return float(half_life), float(np.log(2) / max(half_life, 0.1)), float(max(0, 1 - target_lag / max_lag))

    # ---------- 衰减应用 ----------
    def decay_weight(
        self, signal_name: str, original_weight: float, days_ago: float = 0.0, custom_half_life: float | None = None
    ) -> float:
        """按半衰期衰减信号权重

        w(t) = w0 * exp(-λ * t)
        其中 λ = ln(2) / T_half

        Args:
            signal_name: 信号名称
            original_weight: 初始权重 [0, 1]
            days_ago: 信号产生距今多少天
            custom_half_life: 自定义半衰期 (覆盖默认值)

        Returns:
            衰减后的权重
        """
        if signal_name not in self.signals:
            # 未知信号, 用 5 天默认半衰期
            hl = custom_half_life or 5.0
        else:
            meta = self.signals[signal_name]
            hl = custom_half_life or meta.half_life
            if hl <= 0:
                hl = 5.0

        if hl <= 0:
            decayed = original_weight
        else:
            lam = np.log(2) / hl
            decayed = original_weight * np.exp(-lam * days_ago)

        # 最小保留阈值
        default_meta = SignalHalfLife(
            signal_name=signal_name,
            half_life=0,
            estimated_at=datetime.now().isoformat(),
            method="unknown",
            decay_rate=0.0,
            min_retain_weight=0.05,
        )
        min_retain = self.signals.get(signal_name, default_meta).min_retain_weight

        if decayed < min_retain * original_weight:
            decayed = 0.0

        decayed = round(max(0.0, min(decayed, 1.0)), 6)

        self.decay_log.append(
            {
                "ts": datetime.now().isoformat(),
                "signal": signal_name,
                "original": original_weight,
                "decayed": decayed,
                "days_ago": days_ago,
            }
        )

        return decayed

    def apply_decay_to_signals(
        self, signals: dict[str, dict], current_time: datetime | None = None
    ) -> dict[str, float]:
        """对活跃信号批量应用衰减

        Args:
            signals: {signal_name: {'weight': float, 'generated_at': datetime}}
            current_time: 当前时间 (默认 now)

        Returns:
            {signal_name: decayed_weight}
        """
        now = current_time or datetime.now()
        decayed = {}

        for name, info in signals.items():
            w = info.get("weight", 1.0)
            gen_at = info.get("generated_at")

            if gen_at is None:
                days_ago = 0.0
            elif isinstance(gen_at, str):
                gen_dt = datetime.fromisoformat(gen_at)
                days_ago = (now - gen_dt).total_seconds() / 86400
            else:
                days_ago = (now - gen_at).total_seconds() / 86400

            dw = self.decay_weight(name, w, max(0, days_ago))
            if dw > 0:
                decayed[name] = dw

        self.active_signals = {name: {"weight": w, "ts": now.isoformat()} for name, w in decayed.items()}

        return decayed

    # ---------- 新鲜度评分 ----------
    def freshness_score(self) -> dict:
        """计算当前信号新鲜度评分

        Returns:
            {
                'overall_freshness': 0-100,
                'signal_count': n,
                'stale_signals': [...],
                'fresh_signals': [...],
            }
        """
        if not self.active_signals:
            return {"overall_freshness": 0, "signal_count": 0, "stale_signals": [], "fresh_signals": []}

        n_total = len(self.active_signals)
        fresh = []
        stale = []

        for name, info in self.active_signals.items():
            w = info["weight"]
            meta = self.signals.get(name)
            if meta and meta.half_life > 0:
                # 估算 age
                lam = meta.decay_rate
                if lam > 0 and w > 0:
                    age = -np.log(max(w, 0.001)) / lam
                    if age > 2 * meta.half_life:
                        stale.append(name)
                    else:
                        fresh.append(name)
            else:
                fresh.append(name)

        # 新鲜度 = 鲜活信号 / 总信号
        freshness = round(len(fresh) / max(n_total, 1) * 100, 1)

        return {
            "overall_freshness": freshness,
            "signal_count": n_total,
            "n_fresh": len(fresh),
            "n_stale": len(stale),
            "stale_signals": stale,
            "fresh_signals": fresh,
        }

    # ---------- 批量管理与报告 ----------
    def prune_stale_signals(self, max_age_multiplier: float = 3.0) -> list[str]:
        """剔除超过 max_age_multiplier × T_half 的过期信号"""
        removed = []
        for name in list(self.active_signals.keys()):
            meta = self.signals.get(name)
            if meta and meta.half_life > 0:
                info = self.active_signals[name]
                gen_at = datetime.fromisoformat(info.get("ts", datetime.now().isoformat()))
                age = (datetime.now() - gen_at).total_seconds() / 86400
                if age > max_age_multiplier * meta.half_life:
                    removed.append(name)
                    del self.active_signals[name]
                    logger.info(f"剔除过期信号: {name} (age={age:.1f}d > {max_age_multiplier * meta.half_life:.1f}d)")

        return removed

    def report(self) -> dict:
        """生成信号半衰期管理报告"""
        freshness = self.freshness_score()

        signal_details = {}
        for name, meta in self.signals.items():
            signal_details[name] = {
                "half_life_days": meta.half_life,
                "decay_rate": meta.decay_rate,
                "category": meta.category,
                "max_valid_days": meta.max_valid_days,
                "method": meta.method,
                "r2": meta.r2,
                "is_stable": meta.is_stable,
            }

        return {
            "total_registered": len(self.signals),
            "active_signals": freshness["signal_count"],
            "freshness_score": freshness["overall_freshness"],
            "signal_details": signal_details,
            "stale_signals": freshness["stale_signals"],
            "recommended_prune": freshness["stale_signals"],
            "recent_decays": self.decay_log[-20:] if self.decay_log else [],
        }


# ============================================================
# 预设信号半衰期 (行业标准)
# ============================================================
PRESET_HALF_LIVES = {
    # 技术面信号 (衰减快)
    "momentum_5d": {"hl": 2.0, "cat": "technical"},
    "momentum_20d": {"hl": 5.0, "cat": "technical"},
    "momentum_60d": {"hl": 10.0, "cat": "technical"},
    "rsi_14": {"hl": 2.0, "cat": "technical"},
    "macd_signal": {"hl": 3.0, "cat": "technical"},
    "volume_break": {"hl": 1.0, "cat": "technical"},
    "bollinger_band": {"hl": 1.5, "cat": "technical"},
    # 基本面信号 (衰减慢)
    "pe_ratio": {"hl": 30.0, "cat": "fundamental"},
    "roe": {"hl": 60.0, "cat": "fundamental"},
    "profit_growth": {"hl": 45.0, "cat": "fundamental"},
    "dividend_yield": {"hl": 40.0, "cat": "fundamental"},
    # 情绪/舆情信号
    "news_sentiment": {"hl": 1.0, "cat": "sentiment"},
    "social_media": {"hl": 0.5, "cat": "sentiment"},
    "analyst_rating": {"hl": 15.0, "cat": "sentiment"},
    # 宏观信号
    "macro_pmi": {"hl": 25.0, "cat": "macro"},
    "macro_cpi": {"hl": 25.0, "cat": "macro"},
    "macro_rate": {"hl": 20.0, "cat": "macro"},
    # 另类数据
    "satellite_image": {"hl": 7.0, "cat": "alternative"},
    "supply_chain": {"hl": 10.0, "cat": "alternative"},
    "credit_card": {"hl": 14.0, "cat": "alternative"},
}
