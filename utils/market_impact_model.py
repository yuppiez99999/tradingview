"""市场冲击模型 v1.0

Almgren-Chriss 框架 + Square-Root 模型 + 临时/永久冲击分解

参考:
- Almgren & Chriss (2000) "Optimal Execution of Portfolio Transactions"
- Almgren (2003) "Optimal Execution with Nonlinear Impact Functions"
- Kissell & Malamut (2005) "Algorithmic Decision Making Framework"

核心公式:
- 临时冲击: h(v) = η × sign(v) × |v|^α  (α ∈ [0.5, 1.0], 通常 0.6)
- 永久冲击: g(v) = γ × v  (线性永久冲击)
- Square-Root: Δp = σ × c × (X/ADV)^0.5  (Bouchaud et al. 2004)
- AC 成本: C(x) = (γ/2) × X² + (η/α+1) × X^(α+1) / T^α
- AC 风险: V(x) = σ² × T × Σ x_i²

最优轨迹 (闭式解):
- 线性冲击 (α=1): x(t) = X × sinh(λ(T-t)) / sinh(λT)
- 非线性 (α≠1): 数值解
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ============================================================
# 数据结构
# ============================================================


@dataclass
class ImpactParams:
    """冲击成本参数"""

    eta: float = 0.142  # 临时冲击系数 (Almgren 2003 估计)
    gamma: float = 0.314  # 永久冲击系数
    alpha: float = 0.6  # 非线性冲击指数 (0.5 平方根, 1.0 线性)
    # Square-Root 模型
    sr_coefficient: float = 0.5  # σ × c 的乘子
    # 波动率调整
    volatility_scaling: bool = True
    daily_volatility: float = 0.02  # 日波动率


@dataclass
class ImpactEstimate:
    """冲击成本估计"""

    symbol: str
    order_shares: float
    adv: float  # 日均成交量
    participation_rate: float  # 参与度 = order_shares / adv
    # 临时冲击 (bps)
    temporary_impact_bps: float
    # 永久冲击 (bps)
    permanent_impact_bps: float
    # 总冲击 (bps)
    total_impact_bps: float
    # 价格变动 (绝对值)
    price_impact: float
    # 决策价
    decision_price: float
    # 预期执行价
    expected_exec_price: float
    # 模型
    model_used: str  # AC / SQRT / LINEAR
    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OptimalTrajectory:
    """最优执行轨迹 (Almgren-Chriss)"""

    times: List[float]  # 时间点 [0, T]
    holdings: List[float]  # 持仓轨迹 x(t)
    trades: List[float]  # 交易轨迹 Δx(t)
    speeds: List[float]  # 交易速度 v(t)
    # 成本与风险
    expected_cost: float  # 预期成本
    cost_variance: float  # 成本方差
    # AC 参数
    efficient_frontier_lam: float  # 风险厌恶系数
    half_life: float  # 半衰期 (调仓强度)


# ============================================================
# 市场冲击模型
# ============================================================


class MarketImpactModel:
    """市场冲击模型

    用法:
        model = MarketImpactModel()
        est = model.estimate(symbol="600519", order_shares=10000, adv=500000,
                             decision_price=1800.0)
        logger.info(est.total_impact_bps)
    """

    def __init__(
        self,
        params: Optional[ImpactParams] = None,
        # 默认参数 (A股调优)
        default_adv: float = 1_000_000,
        default_tick_size: float = 0.01,
    ):
        self.params = params or ImpactParams()
        self.default_adv = float(default_adv)
        self.default_tick = float(default_tick_size)

    # ------------------------------------------------------------
    # 主入口: 估计冲击成本
    # ------------------------------------------------------------

    def estimate(
        self,
        symbol: str,
        order_shares: float,
        adv: Optional[float] = None,
        decision_price: float = 0.0,
        volatility: Optional[float] = None,
        execution_time_days: float = 1.0,
    ) -> ImpactEstimate:
        """估计单笔订单的市场冲击

        Args:
            symbol: 标的代码
            order_shares: 订单股数 (绝对值)
            adv: 日均成交量 (None 用默认值)
            decision_price: 决策价
            volatility: 日波动率 (None 用默认)
            execution_time_days: 执行时间 (天)

        Returns:
            ImpactEstimate
        """
        adv = float(adv) if adv and adv > 0 else self.default_adv
        order_shares = abs(order_shares)
        participation = order_shares / adv

        # 波动率
        vol = float(volatility) if volatility else self.params.daily_volatility
        if self.params.volatility_scaling:
            vol_scale = max(vol / 0.02, 0.5)  # 以 2% 为基准
        else:
            vol_scale = 1.0

        # === Square-Root 模型 (主) ===
        # Δp_bps = σ × c × sqrt(participation)
        # 这里用 sr_coefficient × vol_scale × sqrt(participation) × 10000
        sqrt_impact_bps = self.params.sr_coefficient * vol_scale * math.sqrt(max(participation, 1e-10)) * 10000

        # === Almgren-Chriss 分解 ===
        # 临时冲击 (非线性): h(v) = η × v^α
        # 永久冲击 (线性): g(v) = γ × v
        # 在 T 时间内匀速执行: v = X / T
        v = order_shares / max(execution_time_days, 1e-6) / adv  # 标准化速度
        temp_bps = self.params.eta * (v**self.params.alpha) * 10000 * vol_scale
        perm_bps = (
            self.params.gamma * participation * 10000 * 0.5  # 永久冲击平均影响一半
        )

        # 综合: Square-Root 为主, AC 分解修正
        total_impact_bps = sqrt_impact_bps
        temp_impact_bps = max(temp_bps, sqrt_impact_bps * 0.6)
        perm_impact_bps = max(perm_bps, sqrt_impact_bps * 0.4)

        # 价格变动
        price_impact = decision_price * total_impact_bps / 10000.0
        expected_exec_price = decision_price * (1 + total_impact_bps / 10000.0)

        return ImpactEstimate(
            symbol=symbol,
            order_shares=order_shares,
            adv=adv,
            participation_rate=participation,
            temporary_impact_bps=temp_impact_bps,
            permanent_impact_bps=perm_impact_bps,
            total_impact_bps=total_impact_bps,
            price_impact=price_impact,
            decision_price=decision_price,
            expected_exec_price=expected_exec_price,
            model_used="SQRT+AC",
            metadata={
                "volatility": vol,
                "vol_scale": vol_scale,
                "execution_time_days": execution_time_days,
                "v_normalized": v,
            },
        )

    # ------------------------------------------------------------
    # Almgren-Chriss 最优执行轨迹
    # ------------------------------------------------------------

    def optimal_trajectory(
        self,
        total_shares: float,
        time_horizon: float = 1.0,  # T (天)
        volatility: float = 0.02,
        risk_aversion: float = 1.0,  # λ
        n_steps: int = 10,
    ) -> OptimalTrajectory:
        """Almgren-Chriss 最优执行轨迹 (线性冲击闭式解)

        最优解:
            x(t) = X × sinh(λ(T-t)) / sinh(λT)
            其中 λ = sqrt(γ × λ / η)

        Args:
            total_shares: 总股数 X
            time_horizon: 执行时间 T (天)
            volatility: 日波动率 σ
            risk_aversion: 风险厌恶 λ
            n_steps: 离散步数

        Returns:
            OptimalTrajectory
        """
        eta = self.params.eta
        gamma = self.params.gamma
        sigma = float(volatility)
        lam_user = float(risk_aversion)

        # AC 系数 κ = sqrt(λ × σ² / η)
        # 当 η → 0 或 λ × σ² → 0 时, 退化为匀速
        if eta <= 1e-10 or lam_user <= 1e-10:
            # 匀速 (TWAP)
            times = np.linspace(0, time_horizon, n_steps + 1).tolist()
            holdings = np.full(n_steps + 1, total_shares).tolist()
            holdings[-1] = 0.0
            # 线性递减
            for i in range(n_steps + 1):
                holdings[i] = total_shares * (1 - i / n_steps)
            trades = [
                -holdings[i] + holdings[i - 1] if i > 0 else total_shares - holdings[0] for i in range(n_steps + 1)
            ]
            speeds = [t / (time_horizon / n_steps) for t in trades]
            return OptimalTrajectory(
                times=times,
                holdings=holdings,
                trades=trades,
                speeds=speeds,
                expected_cost=0.0,
                cost_variance=0.0,
                efficient_frontier_lam=0.0,
                half_life=0.0,
            )

        # κ = sqrt(λσ² / η)
        kappa = math.sqrt(lam_user * sigma * sigma / eta)
        T = float(time_horizon)

        # 时间点
        t_array = np.linspace(0, T, n_steps + 1)
        # x(t) = X × sinh(κ(T-t)) / sinh(κT)
        # 边界: x(0) = X, x(T) = 0
        sin_kT = math.sinh(kappa * T)
        if abs(sin_kT) < 1e-10:
            # 退化: 匀速
            holdings = total_shares * (1 - t_array / T)  # type: ignore
        else:
            holdings = total_shares * np.sinh(kappa * (T - t_array)) / sin_kT

        # 交易 = -Δx
        trades = np.diff(-holdings)  # type: ignore
        # 第一个交易把持仓从 0 拉到 x(0)? 实际 AC 模型: 初始持仓 = X, 逐步卖到 0
        # 所以 holdings[0] = X (初始), holdings[-1] = 0 (终止)
        trades_full = np.concatenate([[total_shares - holdings[0]], trades])
        # 修正: holdings[0] = total_shares
        # 这里 holdings[0] 已经 = X * sinh(kT)/sinh(kT) = X, OK
        trades_full = np.diff(np.concatenate([[total_shares], -holdings]))  # type: ignore
        # 简化: trades[i] = holdings[i-1] - holdings[i]
        trades_full = np.concatenate(
            [
                [total_shares - holdings[0]],
                [holdings[i] - holdings[i + 1] for i in range(n_steps)],
            ]
        )
        speeds = trades_full / (T / n_steps)

        # 成本 = (γ/2) × X² + (η/(α+1)) × Σ v_i^(α+1) × Δt
        alpha = self.params.alpha
        dt = T / n_steps
        perm_cost = (gamma / 2) * total_shares * total_shares
        temp_cost = (eta / (alpha + 1)) * np.sum(np.abs(trades_full / dt) ** (alpha + 1)) * dt
        total_cost = perm_cost + temp_cost

        # 风险 = σ² × Σ x_i² × Δt
        cost_var = sigma * sigma * np.sum(holdings[:-1] ** 2) * dt  # type: ignore

        # 半衰期: x(t) 减半的时间
        # X/2 = X × sinh(κ(T-t_h)) / sinh(κT)
        # => sinh(κ(T-t_h)) = 0.5 × sinh(κT)
        # => κ(T-t_h) = asinh(0.5 × sinh(κT))
        try:
            half_life = T - math.asinh(0.5 * sin_kT) / kappa
            half_life = max(0.0, half_life)
        except (ValueError, OverflowError):
            half_life = T / 2

        return OptimalTrajectory(
            times=t_array.tolist(),
            holdings=holdings.tolist(),  # type: ignore
            trades=trades_full.tolist(),
            speeds=speeds.tolist(),
            expected_cost=float(total_cost),
            cost_variance=float(cost_var),
            efficient_frontier_lam=lam_user,
            half_life=half_life,
        )

    # ------------------------------------------------------------
    # 批量估计
    # ------------------------------------------------------------

    def estimate_basket(
        self,
        orders: List[Dict[str, Any]],
    ) -> List[ImpactEstimate]:
        """批量估计多个订单的冲击成本

        Args:
            orders: 订单列表, 每项含 symbol/order_shares/adv/decision_price/volatility

        Returns:
            List[ImpactEstimate]
        """
        results: List[ImpactEstimate] = []
        for o in orders:
            est = self.estimate(
                symbol=str(o.get("symbol", "")),
                order_shares=float(o.get("order_shares", 0)),
                adv=o.get("adv"),
                decision_price=float(o.get("decision_price", 0.0)),
                volatility=o.get("volatility"),
                execution_time_days=float(o.get("execution_time_days", 1.0)),
            )
            results.append(est)
        return results

    # ------------------------------------------------------------
    # 有效前沿 (Efficient Frontier of Execution)
    # ------------------------------------------------------------

    def efficient_frontier(
        self,
        total_shares: float,
        time_horizon: float = 1.0,
        volatility: float = 0.02,
        lam_range: Optional[Sequence[float]] = None,
    ) -> List[Tuple[float, float, float]]:
        """生成执行有效前沿

        Returns:
            List of (lambda, expected_cost, cost_std)
        """
        if lam_range is None:
            lam_range = [0.1, 0.5, 1.0, 2.0, 5.0, 10.0]

        frontier: List[Tuple[float, float, float]] = []
        for lam in lam_range:
            traj = self.optimal_trajectory(
                total_shares=total_shares,
                time_horizon=time_horizon,
                volatility=volatility,
                risk_aversion=lam,
                n_steps=10,
            )
            cost_std = math.sqrt(traj.cost_variance) if traj.cost_variance > 0 else 0.0
            frontier.append((lam, traj.expected_cost, cost_std))
        return frontier


# ============================================================
# 工具函数
# ============================================================


def classify_order_urgency(
    order_shares: float,
    adv: float,
    alpha_signal_strength: float = 0.0,
    market_volatility: float = 0.02,
) -> str:
    """订单紧迫度分类

    Args:
        order_shares: 订单股数
        adv: 日均成交量
        alpha_signal_strength: Alpha 信号强度 [-1, 1]
        market_volatility: 市场波动率

    Returns:
        LOW / MEDIUM / HIGH
    """
    participation = order_shares / max(adv, 1.0)

    # 大单 + 强信号 → 高紧迫 (信号衰减快)
    if participation > 0.10 and abs(alpha_signal_strength) > 0.5:
        return "HIGH"
    # 波动率高 + 大单 → 高紧迫
    if participation > 0.15 and market_volatility > 0.03:
        return "HIGH"
    # 小单 → 低紧迫
    if participation < 0.02:
        return "LOW"
    return "MEDIUM"
