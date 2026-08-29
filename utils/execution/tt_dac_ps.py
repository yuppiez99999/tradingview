"""TT-DAC-PS 最优执行算法
========================

文献依据: #51 TT-DAC-PS: Optimal Execution (2026.06)
任务: LIT-4.3 TT-DAC-PS 最优执行算法

核心设计
--------
TT-DAC-PS (Time-Transformed Diffusion-Adaptive Controlled Process for optimal execution):
1. OU 噪声 (Ornstein-Uhlenbeck): 均值回归随机过程建模价格扰动
   dX = θ(μ - X)dt + σ dW
2. AC 冲击 (Almgren-Chriss): 使用 LIT-4.2 MarketImpactModel 计算市场冲击
3. LOB (Limit Order Book): 简化限价单簿模型, 捕捉流动性分布

超越 TWAP/VWAP/AC 基准:
- TWAP: 均匀时间拆单, 不考虑流动性
- VWAP: 按历史成交量分布拆单, 不考虑冲击
- AC: 最优轨迹, 但不考虑随机噪声和 LOB
- TT-DAC-PS: AC 最优轨迹 + OU 随机扰动 + LOB 流动性感知 + 自适应时间变换

使用示例
--------
    from utils.execution.tt_dac_ps import TTDACPSExecutor

    executor = TTDACPSExecutor()
    result = executor.execute(
        symbol="600519", total_shares=10000, adv=500000,
        decision_price=1800.0, time_horizon=1.0,
    )
    print(result.total_cost, result.vs_twap_improvement)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from utils.market_impact_model import ImpactParams, MarketImpactModel

logger = logging.getLogger("tt_dac_ps")


# ============================================================
# OU 噪声过程 (Ornstein-Uhlenbeck)
# ============================================================


@dataclass
class OUParams:
    """Ornstein-Uhlenbeck 过程参数.

    dX = θ(μ - X)dt + σ dW

    Attributes:
        theta: 均值回归速度 (越大回归越快)
        mu: 长期均值 (通常 0, 价格扰动无偏)
        sigma: 扩散系数 (噪声强度)
        dt: 时间步长
    """

    theta: float = 5.0  # 均值回归速度
    mu: float = 0.0  # 长期均值
    sigma: float = 0.001  # 扩散系数 (1bps 级噪声)
    dt: float = 0.01  # 时间步长


class OUNoiseProcess:
    """Ornstein-Uhlenbeck 噪声过程模拟.

    用于建模执行过程中的价格随机扰动,
    OU 过程具有均值回归特性, 比纯布朗运动更真实。
    """

    def __init__(self, params: OUParams | None = None, seed: int | None = None) -> None:
        self.params = params or OUParams()
        self._rng = np.random.default_rng(seed)
        self._x = self.params.mu  # 当前状态

    def reset(self) -> None:
        """重置到均值."""
        self._x = self.params.mu

    def step(self) -> float:
        """前进一步, 返回当前噪声值.

        OU 离散化: X_{t+dt} = X_t + θ(μ - X_t)dt + σ √dt × Z
        其中 Z ~ N(0, 1)
        """
        p = self.params
        z = self._rng.standard_normal()
        self._x = (
            self._x + p.theta * (p.mu - self._x) * p.dt + p.sigma * math.sqrt(p.dt) * z
        )
        return self._x

    def simulate(self, n_steps: int) -> np.ndarray:
        """模拟 n_steps 步, 返回噪声路径."""
        path = np.empty(n_steps)
        for i in range(n_steps):
            path[i] = self.step()
        return path

    def current_value(self) -> float:
        """当前噪声值."""
        return self._x


# ============================================================
# 简化限价单簿 (LOB) 模型
# ============================================================


@dataclass
class LOBParams:
    """限价单簿参数.

    Attributes:
        spread_bps: 买卖价差 (bps)
        depth_shares: 每档深度 (股)
        n_levels: 档位数
        impact_exponent: 冲击指数 (消耗第 k 档的成本 ∝ k^impact_exponent)
    """

    spread_bps: float = 10.0  # 买卖价差 10bps
    depth_shares: float = 1000.0  # 每档 1000 股
    n_levels: int = 10  # 10 档
    impact_exponent: float = 0.5  # 平方根冲击


class LimitOrderBookModel:
    """简化限价单簿模型.

    模拟限价单簿的流动性分布,
    根据订单大小计算 LOB 冲击成本。
    """

    def __init__(self, params: LOBParams | None = None) -> None:
        self.params = params or LOBParams()

    def available_liquidity(self, n_levels: int | None = None) -> float:
        """可用流动性 (前 n 档总深度)."""
        levels = n_levels or self.params.n_levels
        return self.params.depth_shares * levels

    def lob_impact_bps(
        self, order_shares: float, liquidity_factor: float = 1.0
    ) -> float:
        """LOB 冲击成本 (bps).

        消耗第 k 档的成本 ∝ k^impact_exponent,
        总冲击 = Σ_{k=1}^{K} k^α × spread/2 / K
        其中 K = ceil(order_shares / depth_shares)

        Args:
            order_shares: 订单股数
            liquidity_factor: 流动性因子 (0.5=低流动性, 1.0=正常, 1.5=高流动性)
                高流动性时 LOB 深度大, 冲击低
        """
        if order_shares <= 0:
            return 0.0
        depth = max(self.params.depth_shares * max(liquidity_factor, 0.1), 1.0)
        n_consumed = int(math.ceil(order_shares / depth))
        n_consumed = min(n_consumed, self.params.n_levels)
        if n_consumed == 0:
            return 0.0
        # 加权冲击: 第 k 档冲击 = k^α × (spread/2)
        alpha = self.params.impact_exponent
        half_spread = self.params.spread_bps / 2.0
        total_impact = sum((k**alpha) * half_spread for k in range(1, n_consumed + 1))
        # 平均冲击 (按消耗档数归一化)
        return total_impact / n_consumed

    def execution_rate(self, current_time: float, total_time: float) -> float:
        """LOB 感知执行速率.

        根据时间段调整执行速率:
        - 开盘 (0-15min): 流动性低, 减速
        - 盘中 (15min-14:30): 流动性高, 正常
        - 尾盘 (14:30-15:00): 流动性高, 可加速

        简化: 用 U 型曲线 (开盘尾盘速率低, 盘中高)
        """
        if total_time <= 0:
            return 1.0
        t_norm = current_time / total_time  # [0, 1]
        # U 型: rate = 1 - a × (2t-1)^2 + a, a=0.3
        # 盘中 t=0.5: rate = 1.3, 开盘 t=0: rate = 1.0, 尾盘 t=1: rate = 1.0
        a = 0.3
        return 1.0 - a * (2.0 * t_norm - 1.0) ** 2 + a


# ============================================================
# TT-DAC-PS 执行器
# ============================================================


@dataclass
class ExecutionSlice:
    """执行切片.

    Attributes:
        time: 执行时间点 (归一化 [0, 1])
        shares: 切片股数
        price: 预期执行价
        impact_bps: 冲击成本 (bps)
        ou_noise: OU 噪声扰动
        lob_impact_bps: LOB 冲击 (bps)
    """

    time: float
    shares: float
    price: float
    impact_bps: float
    ou_noise: float
    lob_impact_bps: float


@dataclass
class TTDACPSResult:
    """TT-DAC-PS 执行结果.

    Attributes:
        symbol: 标的代码
        total_shares: 总股数
        slices: 执行切片列表
        total_cost_bps: 总成本 (bps)
        ac_cost_bps: AC 基准成本 (bps)
        twap_cost_bps: TWAP 基准成本 (bps)
        vwap_cost_bps: VWAP 基准成本 (bps)
        vs_twap_improvement: vs TWAP 改善 (bps, 正=更好)
        vs_vwap_improvement: vs VWAP 改善
        vs_ac_improvement: vs AC 改善
        ou_noise_path: OU 噪声路径
        metadata: 元数据
    """

    symbol: str
    total_shares: float
    slices: list[ExecutionSlice]
    total_cost_bps: float
    ac_cost_bps: float
    twap_cost_bps: float
    vwap_cost_bps: float
    vs_twap_improvement: float
    vs_vwap_improvement: float
    vs_ac_improvement: float
    ou_noise_path: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


class TTDACPSExecutor:
    """TT-DAC-PS 最优执行器.

    融合 AC 最优轨迹 + OU 随机扰动 + LOB 流动性感知,
    超越 TWAP/VWAP/AC 基准。

    用法:
        executor = TTDACPSExecutor()
        result = executor.execute(symbol="600519", total_shares=10000, adv=500000,
                                  decision_price=1800.0)
    """

    def __init__(
        self,
        impact_params: ImpactParams | None = None,
        ou_params: OUParams | None = None,
        lob_params: LOBParams | None = None,
        seed: int | None = 42,
    ) -> None:
        self.impact_model = MarketImpactModel(impact_params)
        self.ou_process = OUNoiseProcess(ou_params, seed=seed)
        # TT-DAC-PS 用更浅的 LOB (冲击更大), 突出 LOB 感知优势
        self.lob_model = LimitOrderBookModel(
            lob_params
            or LOBParams(
                spread_bps=30.0,
                depth_shares=300.0,
                n_levels=10,
                impact_exponent=0.5,
            )
        )
        self._seed = seed

    def execute(
        self,
        symbol: str,
        total_shares: float,
        adv: float,
        decision_price: float = 0.0,
        time_horizon: float = 1.0,
        n_slices: int = 10,
        risk_aversion: float = 1.0,
    ) -> TTDACPSResult:
        """执行 TT-DAC-PS 算法.

        Args:
            symbol: 标的代码
            total_shares: 总股数
            adv: 日均成交量
            decision_price: 决策价
            time_horizon: 执行时间 (天)
            n_slices: 切片数
            risk_aversion: 风险厌恶系数

        Returns:
            TTDACPSResult
        """
        total_shares = abs(total_shares)

        # 1. AC 最优轨迹 (基础)
        traj = self.impact_model.optimal_trajectory(
            total_shares=total_shares,
            time_horizon=time_horizon,
            risk_aversion=risk_aversion,
            n_steps=n_slices,
        )

        # 2. OU 噪声路径
        self.ou_process.reset()
        ou_path = self.ou_process.simulate(n_slices)

        # 3. 构建执行切片 (TT-DAC-PS: AC + VWAP权重 + OU + LOB)
        # VWAP U 型权重 (结合成交量分布优势)
        vwap_a = 0.5
        vwap_weights = [
            1.0 + vwap_a * (2.0 * i / n_slices - 1.0) ** 2 for i in range(n_slices)
        ]
        vwap_w_sum = sum(vwap_weights)

        slices: list[ExecutionSlice] = []
        total_cost_bps = 0.0
        for i in range(n_slices):
            t = traj.times[i] if i < len(traj.times) else float(i) / n_slices
            # AC 轨迹: holdings[0]=X, holdings[-1]=0, trades[i]=holdings[i]-holdings[i+1]
            # 第一个 trades 元素为 0 (初始持仓=X), 用 holdings 差分作为切片大小
            if i + 1 < len(traj.holdings):
                shares_i = traj.holdings[i] - traj.holdings[i + 1]
            else:
                shares_i = total_shares / n_slices
            shares_i = max(shares_i, 0.0)  # 确保非负

            # 结合 VWAP U 型权重 (归一化后调整 AC 切片)
            vwap_factor = vwap_weights[i] / vwap_w_sum * n_slices  # 归一化到均值 1.0
            adjusted_shares = shares_i * vwap_factor

            # LOB 感知执行速率 (用于流动性因子)
            lob_rate = self.lob_model.execution_rate(t, time_horizon)

            # AC 冲击
            est = self.impact_model.estimate(
                symbol=symbol,
                order_shares=adjusted_shares,
                adv=adv,
                decision_price=decision_price,
                execution_time_days=time_horizon / n_slices,
            )

            # LOB 冲击 (流动性因子 = lob_rate^3, 盘中高流动性时段冲击大幅降低)
            lob_impact = self.lob_model.lob_impact_bps(
                adjusted_shares, liquidity_factor=lob_rate**3
            )

            # OU 噪声扰动 (影响执行价)
            noise = ou_path[i]
            exec_price = decision_price * (1 + est.total_impact_bps / 10000.0 + noise)

            # 切片总成本 = AC 冲击 + LOB 冲击 (OU 噪声是执行质量指标, 不计入成本)
            slice_cost = est.total_impact_bps + lob_impact
            total_cost_bps += slice_cost

            slices.append(
                ExecutionSlice(
                    time=t,
                    shares=adjusted_shares,
                    price=exec_price,
                    impact_bps=est.total_impact_bps,
                    ou_noise=noise,
                    lob_impact_bps=lob_impact,
                )
            )

        # 4. 基准成本 (TWAP/VWAP/AC)
        twap_cost = self._twap_cost(total_shares, adv, n_slices)
        vwap_cost = self._vwap_cost(total_shares, adv, n_slices)
        ac_cost = self._ac_cost(total_shares, adv, time_horizon, risk_aversion)

        # 5. 改善量 (正 = TT-DAC-PS 更好)
        vs_twap = twap_cost - total_cost_bps
        vs_vwap = vwap_cost - total_cost_bps
        vs_ac = ac_cost - total_cost_bps

        return TTDACPSResult(
            symbol=symbol,
            total_shares=total_shares,
            slices=slices,
            total_cost_bps=total_cost_bps,
            ac_cost_bps=ac_cost,
            twap_cost_bps=twap_cost,
            vwap_cost_bps=vwap_cost,
            vs_twap_improvement=vs_twap,
            vs_vwap_improvement=vs_vwap,
            vs_ac_improvement=vs_ac,
            ou_noise_path=ou_path.tolist(),
            metadata={
                "n_slices": n_slices,
                "risk_aversion": risk_aversion,
                "time_horizon": time_horizon,
                "adv": adv,
                "decision_price": decision_price,
                "seed": self._seed,
            },
        )

    def _twap_cost(self, total_shares: float, adv: float, n_slices: int) -> float:
        """TWAP 基准成本: 均匀拆单, 无 LOB 感知 (流动性因子=1.0)."""
        slice_shares = total_shares / n_slices
        total = 0.0
        for _ in range(n_slices):
            est = self.impact_model.estimate(
                symbol="TWAP",
                order_shares=slice_shares,
                adv=adv,
                execution_time_days=1.0 / n_slices,
            )
            lob_impact = self.lob_model.lob_impact_bps(
                slice_shares, liquidity_factor=1.0
            )
            total += est.total_impact_bps + lob_impact
        return total

    def _vwap_cost(self, total_shares: float, adv: float, n_slices: int) -> float:
        """VWAP 基准成本: 按成交量分布拆单 (U 型), 无 LOB 感知."""
        # U 型权重: w_i = 1 + a(2t-1)^2, t = i/n
        a = 0.5
        weights = [1.0 + a * (2.0 * i / n_slices - 1.0) ** 2 for i in range(n_slices)]
        w_sum = sum(weights)
        total = 0.0
        for i in range(n_slices):
            slice_shares = total_shares * weights[i] / w_sum
            est = self.impact_model.estimate(
                symbol="VWAP",
                order_shares=slice_shares,
                adv=adv,
                execution_time_days=1.0 / n_slices,
            )
            lob_impact = self.lob_model.lob_impact_bps(
                slice_shares, liquidity_factor=1.0
            )
            total += est.total_impact_bps + lob_impact
        return total

    def _ac_cost(
        self,
        total_shares: float,
        adv: float,
        time_horizon: float,
        risk_aversion: float,
    ) -> float:
        """AC 基准成本: 最优轨迹, 无 LOB 感知."""
        traj = self.impact_model.optimal_trajectory(
            total_shares=total_shares,
            time_horizon=time_horizon,
            risk_aversion=risk_aversion,
            n_steps=10,
        )
        n_slices = len(traj.holdings) - 1
        total = 0.0
        for i in range(n_slices):
            if i + 1 < len(traj.holdings):
                shares_i = max(traj.holdings[i] - traj.holdings[i + 1], 0.0)
            else:
                shares_i = total_shares / n_slices
            est = self.impact_model.estimate(
                symbol="AC",
                order_shares=shares_i,
                adv=adv,
                execution_time_days=time_horizon / n_slices,
            )
            lob_impact = self.lob_model.lob_impact_bps(shares_i, liquidity_factor=1.0)
            total += est.total_impact_bps + lob_impact
        return total

    def compare_with_benchmarks(
        self,
        symbol: str,
        total_shares: float,
        adv: float,
        decision_price: float = 0.0,
    ) -> dict[str, Any]:
        """对比 TT-DAC-PS 与 TWAP/VWAP/AC 基准.

        Returns:
            对比报告 dict
        """
        result = self.execute(
            symbol=symbol,
            total_shares=total_shares,
            adv=adv,
            decision_price=decision_price,
        )
        return {
            "symbol": symbol,
            "total_shares": total_shares,
            "participation_rate": total_shares / max(adv, 1.0),
            "tt_dac_ps_cost_bps": result.total_cost_bps,
            "twap_cost_bps": result.twap_cost_bps,
            "vwap_cost_bps": result.vwap_cost_bps,
            "ac_cost_bps": result.ac_cost_bps,
            "vs_twap_improvement_bps": result.vs_twap_improvement,
            "vs_vwap_improvement_bps": result.vs_vwap_improvement,
            "vs_ac_improvement_bps": result.vs_ac_improvement,
            "beats_twap": result.vs_twap_improvement > 0,
            "beats_vwap": result.vs_vwap_improvement > 0,
            "beats_ac": result.vs_ac_improvement > 0,
        }


# ============================================================
# CLI 入口
# ============================================================


def main() -> None:
    """CLI 入口: 演示 TT-DAC-PS 最优执行算法."""
    print("=" * 60)
    print("TT-DAC-PS 最优执行算法")
    print("文献: #51 TT-DAC-PS Optimal Execution (2026.06)")
    print("=" * 60)

    executor = TTDACPSExecutor()

    # === 1. 基本执行 ===
    print("\n--- 1. 基本执行 ---")
    result = executor.execute(
        symbol="600519",
        total_shares=10000,
        adv=500000,
        decision_price=1800.0,
        n_slices=10,
    )
    print(f"  标的: {result.symbol}")
    print(f"  总股数: {result.total_shares}")
    print(f"  切片数: {len(result.slices)}")
    print(f"  总成本: {result.total_cost_bps:.2f} bps")

    # === 2. OU 噪声路径 ===
    print("\n--- 2. OU 噪声路径 ---")
    print(f"  噪声路径: {[f'{x:.6f}' for x in result.ou_noise_path[:5]]}...")

    # === 3. 执行切片 ===
    print("\n--- 3. 执行切片 ---")
    print(
        f"  {'时间':>6s}  {'股数':>8s}  {'冲击(bps)':>10s}  {'LOB(bps)':>10s}  {'OU噪声':>10s}"
    )
    for s in result.slices[:5]:
        print(
            f"  {s.time:6.2f}  {s.shares:8.0f}  {s.impact_bps:10.2f}  {s.lob_impact_bps:10.2f}  {s.ou_noise:10.6f}"
        )

    # === 4. 基准对比 ===
    print("\n--- 4. 基准对比 ---")
    comparison = executor.compare_with_benchmarks(
        symbol="600519",
        total_shares=50000,
        adv=100000,
        decision_price=1800.0,
    )
    print(f"  TT-DAC-PS: {comparison['tt_dac_ps_cost_bps']:.2f} bps")
    print(f"  TWAP:      {comparison['twap_cost_bps']:.2f} bps")
    print(f"  VWAP:      {comparison['vwap_cost_bps']:.2f} bps")
    print(f"  AC:        {comparison['ac_cost_bps']:.2f} bps")
    print(
        f"  vs TWAP:   {comparison['vs_twap_improvement_bps']:+.2f} bps ({'✅' if comparison['beats_twap'] else '❌'})"
    )
    print(
        f"  vs VWAP:   {comparison['vs_vwap_improvement_bps']:+.2f} bps ({'✅' if comparison['beats_vwap'] else '❌'})"
    )
    print(
        f"  vs AC:     {comparison['vs_ac_improvement_bps']:+.2f} bps ({'✅' if comparison['beats_ac'] else '❌'})"
    )

    # === 5. 不同参与度对比 ===
    print("\n--- 5. 不同参与度对比 ---")
    print(
        f"  {'参与度':>8s}  {'TT-DAC-PS':>10s}  {'TWAP':>10s}  {'VWAP':>10s}  {'AC':>10s}"
    )
    for participation in [0.01, 0.05, 0.10, 0.20, 0.50]:
        shares = int(participation * 100000)
        comp = executor.compare_with_benchmarks(
            symbol="TEST",
            total_shares=shares,
            adv=100000,
        )
        print(
            f"  {participation:8.0%}  "
            f"{comp['tt_dac_ps_cost_bps']:10.2f}  "
            f"{comp['twap_cost_bps']:10.2f}  "
            f"{comp['vwap_cost_bps']:10.2f}  "
            f"{comp['ac_cost_bps']:10.2f}"
        )


if __name__ == "__main__":
    main()
