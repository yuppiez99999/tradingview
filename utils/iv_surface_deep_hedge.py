"""
隐含波动率曲面深度对冲
======================

文献依据: #38 IV Surface Deep Hedging (2025.04)
任务: LIT-3.5 隐含波动率曲面深度对冲（可选）

核心增强
--------
超越 delta-gamma 对冲:
1. 方差风险溢价 (VRP = IV² - RV²) 信号
2. 多对冲工具 (不同 strike/maturity 期权 + 期货)
3. vega + vanna + volga (二阶波动率希腊字母)
4. IV 面动态校准

使用示例
--------
    from utils.iv_surface_deep_hedge import IVSurfaceDeepHedgeEngine

    engine = IVSurfaceDeepHedgeEngine()
    result = engine.hedge(portfolio_vega, option_chain, realized_vol=0.15)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger("iv_surface_deep_hedge")


# ============================================================
# 枚举
# ============================================================


class HedgeToolType(str, Enum):
    """对冲工具类型。"""

    OPTION = "option"
    FUTURE = "future"
    ETF = "etf"


class VolatilitySignal(str, Enum):
    """波动率信号。"""

    SHORT_VOL = "short_vol"  # 做空波动率 (IV > RV)
    LONG_VOL = "long_vol"  # 做多波动率 (IV < RV)
    NEUTRAL = "neutral"  # 中性 (IV ≈ RV)


# ============================================================
# 方差风险溢价
# ============================================================


@dataclass
class VarianceRiskPremium:
    """方差风险溢价计算结果。

    Attributes:
        implied_var: 隐含方差 (IV²)
        realized_var: 已实现方差 (RV²)
        vrp: 方差风险溢价 (IV² - RV²)
        signal: 波动率信号
    """

    implied_var: float
    realized_var: float
    vrp: float = 0.0
    signal: VolatilitySignal = VolatilitySignal.NEUTRAL

    def __post_init__(self) -> None:
        self.vrp = self.implied_var - self.realized_var
        if self.vrp > 0.001:
            self.signal = VolatilitySignal.SHORT_VOL
        elif self.vrp < -0.001:
            self.signal = VolatilitySignal.LONG_VOL


class VRPCalculator:
    """方差风险溢价计算器。"""

    @staticmethod
    def compute(implied_vol: float, realized_vol: float) -> VarianceRiskPremium:
        """计算 VRP。"""
        return VarianceRiskPremium(
            implied_var=implied_vol**2,
            realized_var=realized_vol**2,
        )

    @staticmethod
    def compute_series(
        implied_vols: np.ndarray, realized_vols: np.ndarray
    ) -> list[VarianceRiskPremium]:
        """批量计算。"""
        return [
            VRPCalculator.compute(float(iv), float(rv))
            for iv, rv in zip(implied_vols, realized_vols, strict=True)
        ]

    @staticmethod
    def rolling_vrp(
        implied_vols: np.ndarray, realized_vols: np.ndarray, window: int = 20
    ) -> np.ndarray:
        """滚动 VRP。"""
        n = len(implied_vols)
        vrp = np.zeros(n)
        for i in range(window, n):
            iv_mean = np.mean(implied_vols[i - window : i] ** 2)
            rv_mean = np.mean(realized_vols[i - window : i] ** 2)
            vrp[i] = iv_mean - rv_mean
        return vrp


# ============================================================
# 对冲工具
# ============================================================


@dataclass
class HedgeTool:
    """对冲工具。

    Attributes:
        tool_type: 工具类型
        strike: 行权价 (期权)
        maturity: 到期时间
        iv: 隐含波动率
        vega: vega 暴露
        vanna: vanna 暴露 (∂delta/∂vol)
        volga: volga 暴露 (∂vega/∂vol)
        price: 价格
    """

    tool_type: HedgeToolType
    strike: float = 0.0
    maturity: float = 0.0
    iv: float = 0.0
    vega: float = 0.0
    vanna: float = 0.0
    volga: float = 0.0
    price: float = 0.0


# ============================================================
# 二阶希腊字母计算
# ============================================================


class SecondOrderGreeks:
    """二阶波动率希腊字母计算 (vega/vanna/volga)。"""

    @staticmethod
    def vanna(
        spot: float, strike: float, maturity: float, vol: float, rate: float = 0.03
    ) -> float:
        """vanna = ∂delta/∂vol = -N'(d1) * d2 / vol。

        衡量 delta 对波动率的敏感度。
        """
        if maturity <= 0 or vol <= 0:
            return 0.0
        d1 = (np.log(spot / strike) + (rate + 0.5 * vol**2) * maturity) / (
            vol * np.sqrt(maturity)
        )
        d2 = d1 - vol * np.sqrt(maturity)
        n_prime_d1 = np.exp(-0.5 * d1**2) / np.sqrt(2 * np.pi)
        return -n_prime_d1 * d2 / vol

    @staticmethod
    def volga(
        spot: float, strike: float, maturity: float, vol: float, rate: float = 0.03
    ) -> float:
        """volga = ∂vega/∂vol = vega * d1 * d2 / vol。

        衡量 vega 对波动率的敏感度。
        """
        if maturity <= 0 or vol <= 0:
            return 0.0
        d1 = (np.log(spot / strike) + (rate + 0.5 * vol**2) * maturity) / (
            vol * np.sqrt(maturity)
        )
        d2 = d1 - vol * np.sqrt(maturity)
        n_prime_d1 = np.exp(-0.5 * d1**2) / np.sqrt(2 * np.pi)
        vega = spot * n_prime_d1 * np.sqrt(maturity)
        return vega * d1 * d2 / vol

    @staticmethod
    def all_vol_greeks(
        spot: float, strike: float, maturity: float, vol: float, rate: float = 0.03
    ) -> dict[str, float]:
        """所有波动率希腊字母。"""
        if maturity <= 0 or vol <= 0:
            return {"vega": 0.0, "vanna": 0.0, "volga": 0.0}
        d1 = (np.log(spot / strike) + (rate + 0.5 * vol**2) * maturity) / (
            vol * np.sqrt(maturity)
        )
        d2 = d1 - vol * np.sqrt(maturity)
        n_prime_d1 = np.exp(-0.5 * d1**2) / np.sqrt(2 * np.pi)
        vega = spot * n_prime_d1 * np.sqrt(maturity)
        vanna = -n_prime_d1 * d2 / vol
        volga = vega * d1 * d2 / vol
        return {"vega": vega, "vanna": vanna, "volga": volga}


# ============================================================
# 多工具对冲器
# ============================================================


@dataclass
class DeepHedgeResult:
    """深度对冲结果。"""

    hedge_quantities: np.ndarray = field(default_factory=lambda: np.array([]))
    residual_vega: float = 0.0
    residual_vanna: float = 0.0
    residual_volga: float = 0.0
    vrp_signal: VolatilitySignal = VolatilitySignal.NEUTRAL
    total_cost: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "quantities": self.hedge_quantities.tolist(),
            "residual_vega": self.residual_vega,
            "residual_vanna": self.residual_vanna,
            "residual_volga": self.residual_volga,
            "vrp0_signal": self.vrp_signal.value,
            "total_cost": self.total_cost,
        }


class MultiToolHedger:
    """多工具对冲器 (vega + vanna + volga 中和)."""

    def hedge(
        self,
        portfolio_vega: float,
        portfolio_vanna: float,
        portfolio_volga: float,
        tools: list[HedgeTool],
    ) -> DeepHedgeResult:
        """执行多工具对冲.

        最小二乘求解: min ||A x - b||²
        A = [[vega_1, vanna_1, volga_1], ...]
        b = [portfolio_vega, portfolio_vanna, portfolio_volga]
        """
        result = DeepHedgeResult()

        if not tools:
            result.residual_vega = portfolio_vega
            result.residual_vanna = portfolio_vanna
            result.residual_volga = portfolio_volga
            return result

        n = len(tools)
        A = np.zeros((3, n))
        for i, tool in enumerate(tools):
            A[0, i] = tool.vega
            A[1, i] = tool.vanna
            A[2, i] = tool.volga

        b = np.array([portfolio_vega, portfolio_vanna, portfolio_volga])

        if n >= 3:
            quantities, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
        elif n == 1:
            if abs(A[0, 0]) > 1e-10:
                quantities = np.array([b[0] / A[0, 0]])
            else:
                quantities = np.array([0.0])
        else:
            quantities, _, _, _ = np.linalg.lstsq(A, b, rcond=None)

        residual = A @ quantities - b
        result.hedge_quantities = quantities
        result.residual_vega = float(residual[0])
        result.residual_vanna = float(residual[1])
        result.residual_volga = float(residual[2])
        result.total_cost = float(np.sum(np.abs(quantities)))

        return result


# ============================================================
# IV 面深度对冲引擎
# ============================================================


class IVSurfaceDeepHedgeEngine:
    """IV 面深度对冲引擎.

    使用示例:
        engine = IVSurfaceDeepHedgeEngine()
        result = engine.hedge(portfolio_vega, option_chain, realized_vol=0.15)
    """

    def __init__(self) -> None:
        self.hedger = MultiToolHedger()
        self._stats: dict[str, int] = {"total": 0}

    def hedge(
        self,
        portfolio_vega: float,
        portfolio_vanna: float,
        portfolio_volga: float,
        tools: list[HedgeTool],
        implied_vol: float = 0.2,
        realized_vol: float = 0.15,
    ) -> DeepHedgeResult:
        """执行深度对冲。"""
        self._stats["total"] += 1

        vrp = VRPCalculator.compute(implied_vol, realized_vol)

        vanna_adj = portfolio_vanna
        volga_adj = portfolio_volga
        if vrp.signal == VolatilitySignal.SHORT_VOL:
            vanna_adj *= 1.2
            volga_adj *= 1.5
        elif vrp.signal == VolatilitySignal.LONG_VOL:
            vanna_adj *= 0.8
            volga_adj *= 0.5

        result = self.hedger.hedge(portfolio_vega, vanna_adj, volga_adj, tools)
        result.vrp_signal = vrp.signal

        logger.debug(
            "深度对冲: vega=%.4f, vanna=%.4f, volga=%.4f, signal=%s",
            result.residual_vega,
            result.residual_vanna,
            result.residual_volga,
            vrp.signal.value,
        )

        return result

    def build_option_tools(
        self,
        spot: float,
        strikes: list[float],
        maturities: list[float],
        ivs: list[float],
    ) -> list[HedgeTool]:
        """构建期权对冲工具集。"""
        tools: list[HedgeTool] = []
        for k, t, iv in zip(strikes, maturities, ivs, strict=True):
            greeks = SecondOrderGreeks.all_vol_greeks(spot, k, t, iv)
            tools.append(
                HedgeTool(
                    tool_type=HedgeToolType.OPTION,
                    strike=k,
                    maturity=t,
                    iv=iv,
                    vega=greeks["vega"],
                    vanna=greeks["vanna"],
                    volga=greeks["volga"],
                )
            )
        return tools

    def get_stats(self) -> dict[str, Any]:
        return {"total": self._stats["total"]}


# ============================================================
# CLI 入口
# ============================================================


def main() -> None:
    """CLI 入口: 演示 IV 面深度对冲。"""
    print("=" * 60)
    print("隐含波动率曲面深度对冲")
    print("文献: #38 IV Surface Deep Hedging 2025.04")
    print("=" * 60)

    engine = IVSurfaceDeepHedgeEngine()

    print("\n--- 方差风险溢价 ---")
    for iv, rv in [(0.25, 0.15), (0.15, 0.20), (0.20, 0.20)]:
        vrp = VRPCalculator.compute(iv, rv)
        print(
            f"  IV={iv:.2f}, RV={rv:.2f}: VRP={vrp.vrp:.4f}, signal={vrp.signal.value}"
        )

    spot = 100.0
    strikes = [90, 95, 100, 105, 110]
    maturities = [30 / 365] * 5
    ivs = [0.25, 0.22, 0.20, 0.19, 0.18]

    tools = engine.build_option_tools(spot, strikes, maturities, ivs)

    print(f"\n--- 对冲工具 ({len(tools)} 个期权) ---")
    for t in tools:
        print(
            f"  K={t.strike}: vega={t.vega:.3f}, vanna={t.vanna:.4f}, volga={t.volga:.3f}"
        )

    print("\n--- 深度对冲 (vega=100, vanna=10, volga=50) ---")
    result = engine.hedge(
        portfolio_vega=100,
        portfolio_vanna=10,
        portfolio_volga=50,
        tools=tools,
        implied_vol=0.22,
        realized_vol=0.15,
    )
    print(f"  对冲量: {result.hedge_quantities}")
    print(f"  剩余 vega: {result.residual_vega:.4f}")
    print(f"  剩余 vanna: {result.residual_vanna:.4f}")
    print(f"  剩余 volga: {result.residual_volga:.4f}")
    print(f"  VRP 信号: {result.vrp_signal.value}")
    print(f"  总成本: {result.total_cost:.4f}")

    print("\n--- 统计 ---")
    print(f"  总计: {engine.get_stats()['total']}")


if __name__ == "__main__":
    main()
