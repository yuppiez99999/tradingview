"""
RegimeFolio 制度感知组合优化
============================

文献依据: #41 RegimeFolio (2025.10)
任务: LIT-3.4 RegimeFolio 制度感知组合优化

核心范式
--------
不同市场制度下最优组合不同:
- 低波动 (VIX<15): 增加进攻性资产 (成长股/周期股)
- 正常 (VIX 15-20): 均衡配置
- 高波动 (VIX 20-30): 增加防御性资产
- 危机 (VIX>30): 最大防御 + 降仓位

关键技术
--------
1. VIX 制度分类 (4级)
2. Ledoit-Wolf 收缩协方差 (减少估计误差)
3. 制度感知权重调整
4. 行业集成 (不同制度下行业权重不同)

使用示例
--------
    from utils.regime_aware_allocator import RegimeAwareAllocator

    allocator = RegimeAwareAllocator()
    weights = allocator.allocate(returns, vix=25.0)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger("regime_aware_allocator")


# ============================================================
# 制度枚举
# ============================================================


class Regime(str, Enum):
    """市场制度。"""

    LOW_VOL = "low_vol"
    NORMAL = "normal"
    HIGH_VOL = "high_vol"
    CRISIS = "crisis"


# ============================================================
# VIX 制度分类器
# ============================================================


@dataclass
class RegimeThresholds:
    """制度阈值。

    Attributes:
        low_vol: 低波动上限
        normal: 正常上限
        high_vol: 高波动上限
    """

    low_vol: float = 15.0
    normal: float = 20.0
    high_vol: float = 30.0


class RegimeClassifier:
    """VIX 制度分类器。"""

    def __init__(self, thresholds: RegimeThresholds | None = None) -> None:
        self.thresholds = thresholds or RegimeThresholds()
        self.history: list[Regime] = []

    def classify(self, vix: float) -> Regime:
        """根据 VIX 分类制度。"""
        if vix < self.thresholds.low_vol:
            regime = Regime.LOW_VOL
        elif vix < self.thresholds.normal:
            regime = Regime.NORMAL
        elif vix < self.thresholds.high_vol:
            regime = Regime.HIGH_VOL
        else:
            regime = Regime.CRISIS
        self.history.append(regime)
        return regime

    def classify_batch(self, vix_series: np.ndarray) -> list[Regime]:
        """批量分类。"""
        return [self.classify(float(v)) for v in vix_series]

    def regime_distribution(self) -> dict[str, float]:
        """制度分布统计。"""
        if not self.history:
            return {}
        total = len(self.history)
        return {r.value: self.history.count(r) / total for r in Regime}


# ============================================================
# Ledoit-Wolf 收缩协方差
# ============================================================


class CovarianceShrinkage:
    """Ledoit-Wolf 收缩协方差估计.

    Σ_shrunk = δ * F + (1-δ) * S
    F = 单位矩阵 * trace(S)/n (目标)
    δ = 最优收缩强度
    """

    @staticmethod
    def ledoit_wolf(returns: np.ndarray) -> tuple[np.ndarray, float]:
        """Ledoit-Wolf 收缩协方差.

        Returns:
            (收缩后协方差, 收缩强度)
        """
        n_samples, n_assets = returns.shape
        s = np.cov(returns, rowvar=False)

        mu = np.trace(s) / n_assets
        f = mu * np.eye(n_assets)

        d_sq = np.sum((s - f) ** 2) / n_assets

        mean_returns = np.mean(returns, axis=0)
        centered = returns - mean_returns
        pi_mat = np.zeros((n_assets, n_assets))
        for t in range(n_samples):
            x_t = centered[t].reshape(-1, 1)
            pi_mat += (x_t @ x_t.T - s) ** 2
        pi_mat = pi_mat / n_samples
        pi_hat = np.sum(pi_mat) / n_assets

        rho_hat = 0.0

        gamma_hat = d_sq

        kappa = (pi_hat - rho_hat) / gamma_hat
        delta = max(0.0, min(1.0, kappa / n_samples))

        shrunk = delta * f + (1 - delta) * s
        return shrunk, float(delta)

    @staticmethod
    def shrink_to_identity(returns: np.ndarray, intensity: float = 0.1) -> np.ndarray:
        """向单位矩阵收缩 (简化版)."""
        s = np.cov(returns, rowvar=False)
        n = s.shape[0]
        mu = np.trace(s) / n
        return np.asarray(intensity * mu * np.eye(n) + (1 - intensity) * s)


# ============================================================
# 制度感知配置
# ============================================================


@dataclass
class RegimeConfig:
    """制度感知配置。

    Attributes:
        risk_aversion: 各制度的风险厌恶系数
        max_weight: 各制度的最大权重
        defense_boost: 防御资产加成
        offense_boost: 进攻资产加成
    """

    risk_aversion: dict[Regime, float] = field(
        default_factory=lambda: {
            Regime.LOW_VOL: 0.5,
            Regime.NORMAL: 1.0,
            Regime.HIGH_VOL: 2.0,
            Regime.CRISIS: 5.0,
        }
    )
    max_weight: dict[Regime, float] = field(
        default_factory=lambda: {
            Regime.LOW_VOL: 0.40,
            Regime.NORMAL: 0.30,
            Regime.HIGH_VOL: 0.25,
            Regime.CRISIS: 0.15,
        }
    )
    defense_boost: dict[Regime, float] = field(
        default_factory=lambda: {
            Regime.LOW_VOL: 0.8,
            Regime.NORMAL: 1.0,
            Regime.HIGH_VOL: 1.5,
            Regime.CRISIS: 2.0,
        }
    )
    offense_boost: dict[Regime, float] = field(
        default_factory=lambda: {
            Regime.LOW_VOL: 1.3,
            Regime.NORMAL: 1.0,
            Regime.HIGH_VOL: 0.7,
            Regime.CRISIS: 0.3,
        }
    )


# ============================================================
# 制度感知分配器
# ============================================================


@dataclass
class AllocationResult:
    """分配结果。

    Attributes:
        weights: 最终权重
        regime: 当前制度
        base_weights: 基础权重 (优化器)
        adjusted_weights: 制度调整后权重
        shrinkage_intensity: 收缩强度
        expected_return: 期望收益
        expected_risk: 期望风险
    """

    weights: np.ndarray
    regime: Regime
    base_weights: np.ndarray = field(default_factory=lambda: np.array([]))
    adjusted_weights: np.ndarray = field(default_factory=lambda: np.array([]))
    shrinkage_intensity: float = 0.0
    expected_return: float = 0.0
    expected_risk: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "weights": self.weights.tolist(),
            "regime": self.regime.value,
            "shrinkage": self.shrinkage_intensity,
            "expected_return": self.expected_return,
            "expected_risk": self.expected_risk,
        }


class RegimeAwareAllocator:
    """制度感知组合分配器.

    使用示例:
        allocator = RegimeAwareAllocator()
        result = allocator.allocate(returns, vix=25.0)
    """

    def __init__(
        self,
        config: RegimeConfig | None = None,
        thresholds: RegimeThresholds | None = None,
        use_shrinkage: bool = True,
    ) -> None:
        self.config = config or RegimeConfig()
        self.classifier = RegimeClassifier(thresholds)
        self.use_shrinkage = use_shrinkage
        self._stats: dict[str, int] = {"total": 0}

    def allocate(
        self,
        returns: np.ndarray,
        vix: float,
        defense_mask: np.ndarray | None = None,
        offense_mask: np.ndarray | None = None,
    ) -> AllocationResult:
        """制度感知分配.

        Args:
            returns: 收益矩阵 (n_samples, n_assets)
            vix: 当前 VIX 值
            defense_mask: 防御资产掩码 (1=防御, 0=其他)
            offense_mask: 进攻资产掩码 (1=进攻, 0=其他)
        """
        self._stats["total"] += 1

        regime = self.classifier.classify(vix)

        if self.use_shrinkage:
            cov_shrunk, shrinkage = CovarianceShrinkage.ledoit_wolf(returns)
        else:
            cov_shrunk = np.cov(returns, rowvar=False)
            shrinkage = 0.0

        base_weights = self._base_allocate(returns, cov_shrunk, regime)

        adjusted = base_weights.copy()
        if defense_mask is not None:
            boost = self.config.defense_boost[regime]
            adjusted = adjusted * (defense_mask * boost + (1 - defense_mask))
        if offense_mask is not None:
            boost = self.config.offense_boost[regime]
            adjusted = adjusted * (offense_mask * boost + (1 - offense_mask))

        adjusted = np.maximum(adjusted, 0)
        total = adjusted.sum()
        if total > 0:
            adjusted = adjusted / total

        max_w = self.config.max_weight[regime]
        adjusted = self._apply_max_weight(adjusted, max_w)

        mu = np.mean(returns, axis=0)
        exp_ret = float(mu @ adjusted)
        exp_risk = float(np.sqrt(adjusted @ cov_shrunk @ adjusted))

        return AllocationResult(
            weights=adjusted,
            regime=regime,
            base_weights=base_weights,
            adjusted_weights=adjusted,
            shrinkage_intensity=shrinkage,
            expected_return=exp_ret,
            expected_risk=exp_risk,
        )

    def _base_allocate(
        self,
        returns: np.ndarray,
        cov: np.ndarray,
        regime: Regime,
    ) -> np.ndarray:
        """基础权重分配 (制度感知 Markowitz)."""
        mu = np.mean(returns, axis=0)
        risk_aversion = self.config.risk_aversion[regime]

        n = cov.shape[0]
        cov_reg = cov + np.eye(n) * 1e-8

        weights = np.linalg.solve(cov_reg, mu) / risk_aversion
        weights = np.maximum(weights, 0)
        total = weights.sum()
        if total < 1e-10:
            return np.asarray(np.ones(n) / n)
        return np.asarray(weights / total)

    @staticmethod
    def _apply_max_weight(weights: np.ndarray, max_w: float) -> np.ndarray:
        """应用最大权重约束 (迭代裁剪).

        修复 (2026-09-11, Issue #13 巡检续批): 原实现存在两处失效, 均无告警:

        1. ``max_w * n < 1`` 时直接 ``return np.ones(n) / n`` —— 等权 = ``1/n`` 本身
           **就超过** ``max_w``, 即在数学上不可行的情形下静默返回一个违规解。
           实测 CRISIS(n=8, max_w=0.15) 单资产权重 0.347 > 上限 0.15 (2.3×)。
        2. ``max_w >= 1.0 / n`` 时直接 ``return weights`` —— 该早退是错的:
           ``max_w >= 1/n`` 只说明"等权可行", 并不意味着**给定权重**满足上限。
           实测 n=5、max_w=0.25 时 ``[0.9, 0.025, ...]`` 原样通过 (0.9 > 0.25, 3.6×)。
           注意原注释即把 "等权可行" 误当 "约束不生效"。

        现语义: 只要约束可行 (``max_w * n_active >= 1``) 就必须真实裁剪到上限;
        不可行时返回最接近的可行解 (等权) **并显式 WARNING**, 不静默伪装成已满足。
        """
        n = len(weights)
        if n == 0:
            return np.asarray(weights)

        if not np.isfinite(max_w):
            logger.warning("[RegimeAware] max_weight 非有限值 %r, 跳过上限裁剪", max_w)
            return np.asarray(weights)

        result = np.maximum(np.asarray(weights, dtype=float).copy(), 0)
        total = result.sum()
        if total < 1e-10:
            result = np.ones(n) / n
            total = 1.0
        result = result / total

        # 可行性必须按**实际持仓资产数**判定, 而不是资产总数 n。
        # 因为权重为 0 的资产无需满足上限, 只有正权重资产参与"瓜分 1.0"。
        n_active = int(np.sum(result > 1e-12))
        if n_active == 0:
            return np.ones(n) / n

        if max_w * n_active < 1.0 - 1e-10:
            # 不可行: 活跃资产数不足以在上限内合计到 1.0。
            # 返回活跃资产等权 (最接近的可行近似) 并显式告警 —— 不静默伪装成已满足。
            logger.warning(
                "[RegimeAware] max_weight=%.4f 对 %d 个活跃资产不可行 (上限合计 %.4f < 1), "
                "返回活跃资产等权近似; 单资产权重 %.4f 将超过上限",
                max_w,
                n_active,
                max_w * n_active,
                1.0 / n_active,
            )
            out = np.zeros(n, dtype=float)
            out[result > 1e-12] = 1.0 / n_active
            return out

        # 裁剪 + 再分配迭代。每轮用**当前活跃集合**判定可行性:
        # 当活跃数已降到不足以支撑 1.0 时提前收敛 (返回活跃等权), 避免
        # "钉在上限后重归一化"再次越限 (原实现 n=8/max_w=0.15/4 个活跃资产 → 0.2333)。
        for _ in range(200):
            active = result > 1e-12
            n_active = int(np.sum(active))
            if n_active == 0:
                return np.ones(n) / n

            excess = result > max_w + 1e-10
            if not np.any(excess):
                break

            if max_w * n_active < 1.0 - 1e-10:
                # 剩余活跃集合已无法在上限内合计到 1.0 → 活跃等权收尾
                out = np.zeros(n, dtype=float)
                out[active] = 1.0 / n_active
                logger.warning(
                    "[RegimeAware] max_weight=%.4f 与剩余 %d 个活跃资产不可行, "
                    "返回活跃等权近似 (单资产 %.4f)",
                    max_w,
                    n_active,
                    1.0 / n_active,
                )
                return out

            pool = float(np.sum(result[excess] - max_w))
            result[excess] = max_w
            free_mask = (~excess) & active
            n_free = int(np.sum(free_mask))
            if n_free > 0:
                result[free_mask] += pool / n_free
            else:
                break

        result = np.maximum(result, 0)
        total = result.sum()
        return result / total if total > 0 else np.ones(n) / n

    def allocate_batch(
        self,
        returns: np.ndarray,
        vix_series: np.ndarray,
    ) -> list[AllocationResult]:
        """批量分配。"""
        return [self.allocate(returns, float(v)) for v in vix_series]

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息。"""
        return {
            "total": self._stats["total"],
            "regime_distribution": self.classifier.regime_distribution(),
        }

    def compare_regimes(
        self,
        returns: np.ndarray,
        vix_values: dict[str, float] | None = None,
    ) -> dict[str, AllocationResult]:
        """对比不同制度下的分配。"""
        if vix_values is None:
            vix_values = {
                "low_vol": 12.0,
                "normal": 18.0,
                "high_vol": 25.0,
                "crisis": 35.0,
            }
        return {name: self.allocate(returns, vix) for name, vix in vix_values.items()}


# ============================================================
# CLI 入口
# ============================================================


def main() -> None:
    """CLI 入口: 演示 RegimeFolio 制度感知组合优化。"""
    print("=" * 60)
    print("RegimeFolio 制度感知组合优化")
    print("文献: #41 RegimeFolio 2025.10")
    print("=" * 60)

    rng = np.random.default_rng(42)
    n_assets = 6
    n_samples = 252
    true_mu = np.array([0.15, 0.10, 0.08, 0.05, 0.12, 0.03]) / 252
    returns = rng.standard_normal((n_samples, n_assets)) * 0.02 + true_mu

    allocator = RegimeAwareAllocator()

    defense_mask = np.array([0, 0, 0, 1, 0, 1])
    offense_mask = np.array([1, 1, 0, 0, 1, 0])

    print(f"\n--- 数据: {n_samples} 天 x {n_assets} 资产 ---")
    print(f"  防御资产: {np.where(defense_mask)[0].tolist()}")
    print(f"  进攻资产: {np.where(offense_mask)[0].tolist()}")

    vix_levels = [
        ("低波动 (VIX=12)", 12.0),
        ("正常 (VIX=18)", 18.0),
        ("高波动 (VIX=25)", 25.0),
        ("危机 (VIX=35)", 35.0),
    ]

    print("\n--- 制度对比 ---")
    for name, vix in vix_levels:
        result = allocator.allocate(returns, vix, defense_mask, offense_mask)
        print(f"\n  {name}:")
        print(f"    制度: {result.regime.value}")
        print(f"    权重: {[f'{w:.3f}' for w in result.weights]}")
        print(f"    年化收益: {result.expected_return * 252:.4f}")
        print(f"    年化风险: {result.expected_risk * np.sqrt(252):.4f}")
        print(f"    收缩强度: {result.shrinkage_intensity:.4f}")
        sharpe = result.expected_return / max(result.expected_risk, 1e-8) * np.sqrt(252)
        print(f"    夏普比率: {sharpe:.4f}")

    stats = allocator.get_stats()
    print("\n--- 统计 ---")
    print(f"  总计: {stats['total']}")
    print(f"  制度分布: {stats['regime_distribution']}")


if __name__ == "__main__":
    main()
