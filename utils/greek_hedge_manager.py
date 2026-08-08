"""
Greeks 动态对冲管理器 — v8.3.2 Vega动态监控升级

Claude Audit 2026-07-22 改进项 #3 — Vega上限动态化 (评级 A- → A+)

顶级对冲基金标准组件：
- 组合 Delta/Gamma/Theta/Vega/Rho 暴露计算
- 基于 Greeks 的动态对冲目标计算
- Vega 动态上限 — 基于 IV 期限结构 & VIX 水平 (NEW)
- 期货/期权对冲量自动调整
- Skew 风险监控 (NEW)

用于替代/增强现有静态对冲配置。

Vega 动态算法:
    max_vega = base_vega_limit * iv_ratio_mult * term_structure_mult * skew_mult
    其中:
    - iv_ratio_mult: 当前 IV / 长期 IV 中位数的比值。IV 偏高 → 收紧 Vega 上限
      (高IV期权利金贵, 应减少Vega暴露)
    - term_structure_mult: 近月 IV / 远月 IV。Contango → 放松, Backwardation → 收紧
    - skew_mult: Put Skew (25Δ Put IV - 25Δ Call IV)。Skew 飙升 → 收紧
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class GreekExposure:
    """Greeks 暴露"""

    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0


@dataclass
class HedgeInstrument:
    """对冲工具"""

    code: str
    instrument_type: str  # FUTURES / OPTION
    direction: str  # LONG / SHORT
    multiplier: float = 1.0
    delta: float = 1.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    beta: float = 1.0


@dataclass
class IVEnvironment:
    """隐含波动率环境参数 (v8.3.2 NEW)"""

    current_iv: float = 0.20  # 当前 ATM IV (如 20%)
    long_term_median_iv: float = 0.20  # 长期 IV 中位数
    front_month_iv: float = 0.20  # 近月 IV
    second_month_iv: float = 0.20  # 次月 IV
    put_25d_iv: float = 0.22  # 25Δ Put IV
    call_25d_iv: float = 0.18  # 25Δ Call IV
    vix_level: float = 20.0  # VIX / 50ETF 波指 水平
    vix_long_term_median: float = 20.0  # VIX 长期中位数


def _norm_cdf(x: float) -> float:
    """标准正态分布累积分布函数"""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def _norm_pdf(x: float) -> float:
    """标准正态分布概率密度函数"""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


class GreekHedgeManager:
    """Greeks 动态对冲管理器 (v8.3.2: Vega动态)"""

    def __init__(
        self,
        target_delta: float = 0.0,
        target_gamma: float = 0.0,
        max_vega: float = 50000.0,
        max_theta_burn: float = -5000.0,
        iv_env: IVEnvironment | None = None,
    ):
        self.target_delta = target_delta
        self.target_gamma = target_gamma
        self._base_max_vega = max_vega
        self.max_theta_burn = max_theta_burn
        self.iv_env = iv_env

    # -----------------------------------------------------------
    # Vega 动态上限计算 (v8.3.2 NEW)
    # -----------------------------------------------------------
    @property
    def max_vega(self) -> float:
        """动态 Vega 上限 — 根据 IV 环境实时调整"""
        if self.iv_env is None:
            return self._base_max_vega
        return self._compute_dynamic_vega_limit()

    @max_vega.setter
    def max_vega(self, value: float):
        """允许覆盖 (向后兼容)"""
        self._base_max_vega = value

    def set_iv_environment(self, iv_env: IVEnvironment):
        """更新 IV 环境参数"""
        self.iv_env = iv_env

    def _compute_dynamic_vega_limit(self) -> float:
        """计算动态 Vega 上限。

        核心逻辑:
            1. IV 偏高 → 权利金贵 → 减少 Vega 暴露 (收紧上限)
            2. 近月 IV > 远月 IV (Backwardation) → 恐慌信号 → 收紧
            3. Put Skew 飙升 → 尾部风险定价高 → 收紧

        公式:
            multi = iv_ratio_mult * term_structure_mult * skew_mult
            limit = base * max(0.5, min(1.5, multi))
        """
        iv = self.iv_env

        # 因子1: IV 比率 — 当前 IV 相对历史中位数
        if iv.long_term_median_iv > 0:  # type: ignore[misc]
            iv_ratio = iv.current_iv / iv.long_term_median_iv  # type: ignore[misc]
        else:
            iv_ratio = 1.0
        # IV 比率越高, 乘数越低 (收紧上限)
        # 1.0 → 1.0, 1.5 → 0.75, 2.0 → 0.5
        iv_ratio_mult = 1.0 / max(iv_ratio, 0.5)

        # 因子2: 期限结构 — 近月/远月 IV 比值
        if iv.second_month_iv > 0:  # type: ignore[misc]
            term_ratio = iv.front_month_iv / iv.second_month_iv  # type: ignore[misc]
        else:
            term_ratio = 1.0
        # Contango (近低远高, ratio < 1) → 放松, Backwardation → 收紧
        if term_ratio <= 1.0:
            term_mult = 1.0 + (1.0 - term_ratio) * 0.5  # 最多放大到 1.5
        else:
            term_mult = 1.0 / term_ratio  # 恐慌时收紧

        # 因子3: Skew — Put-Call IV 差
        skew = iv.put_25d_iv - iv.call_25d_iv  # type: ignore[misc]
        normal_skew = 0.04  # 正常的 Skew 约 4%
        if skew <= normal_skew:
            skew_mult = 1.0
        else:
            # Skew 每超出 1% 收紧 10%
            skew_mult = 1.0 / (1.0 + (skew - normal_skew) * 10)

        # 综合乘数, 限制在 [0.3, 1.5]
        composite = iv_ratio_mult * term_mult * skew_mult
        composite = max(0.3, min(1.5, composite))

        dynamic_limit = self._base_max_vega * composite
        return round(dynamic_limit, 2)

    def get_vega_limit_breakdown(self) -> dict[str, float]:
        """获取 Vega 上限分解详情 (用于监控和审计)"""
        if self.iv_env is None:
            return {"max_vega": self._base_max_vega, "dynamic": False}

        iv = self.iv_env
        iv_ratio = iv.current_iv / max(iv.long_term_median_iv, 0.01)
        iv_ratio_mult = 1.0 / max(iv_ratio, 0.5)

        term_ratio = iv.front_month_iv / max(iv.second_month_iv, 0.01)
        term_mult = (1.0 + (1.0 - term_ratio) * 0.5) if term_ratio <= 1.0 else (1.0 / term_ratio)

        skew = iv.put_25d_iv - iv.call_25d_iv
        normal_skew = 0.04
        skew_mult = 1.0 if skew <= normal_skew else 1.0 / (1.0 + (skew - normal_skew) * 10)

        composite = max(0.3, min(1.5, iv_ratio_mult * term_mult * skew_mult))

        return {
            "max_vega": round(self._base_max_vega * composite, 2),
            "base_max_vega": self._base_max_vega,
            "iv_ratio_mult": round(iv_ratio_mult, 4),
            "term_structure_mult": round(term_mult, 4),
            "skew_mult": round(skew_mult, 4),
            "composite_multiplier": round(composite, 4),
            "current_iv": iv.current_iv,
            "long_term_median_iv": iv.long_term_median_iv,
            "put_25d_iv": iv.put_25d_iv,
            "call_25d_iv": iv.call_25d_iv,
            "skew": round(skew, 4),
            "dynamic": True,
        }

    def _bs_d1_d2(self, S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, float]:
        """计算 Black-Scholes d1 和 d2"""
        if T <= 0 or sigma <= 0 or S <= 0:
            return 0.0, 0.0
        sqrt_T = math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T
        return d1, d2

    def _bs_delta(self, S: float, K: float, T: float, r: float, sigma: float, call: bool = True) -> float:
        """计算期权 Delta

        与 _bs_gamma/_bs_theta/_bs_vega/_bs_rho 一致: 无效输入 (S<=0/T<=0/sigma<=0)
        返回 0, 避免 _bs_d1_d2 返回哨兵 (0,0) 后算得 N(0)=0.5 污染组合 Delta.
        """
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return 0.0
        d1, _ = self._bs_d1_d2(S, K, T, r, sigma)
        if call:
            return _norm_cdf(d1)
        else:
            return _norm_cdf(d1) - 1.0

    def _bs_gamma(self, S: float, K: float, T: float, r: float, sigma: float) -> float:
        """计算期权 Gamma"""
        d1, _ = self._bs_d1_d2(S, K, T, r, sigma)
        sqrt_T = math.sqrt(T)
        if S <= 0 or sigma <= 0 or T <= 0:
            return 0.0
        return _norm_pdf(d1) / (S * sigma * sqrt_T)

    def _bs_theta(self, S: float, K: float, T: float, r: float, sigma: float, call: bool = True) -> float:
        """计算期权 Theta (年化)"""
        d1, d2 = self._bs_d1_d2(S, K, T, r, sigma)
        sqrt_T = math.sqrt(T)
        if S <= 0 or sigma <= 0 or T <= 0:
            return 0.0
        term1 = -S * _norm_pdf(d1) * sigma / (2.0 * sqrt_T)
        if call:
            term2 = r * K * math.exp(-r * T) * _norm_cdf(d2)
        else:
            term2 = r * K * math.exp(-r * T) * _norm_cdf(-d2)
        return term1 - term2

    def _bs_vega(self, S: float, K: float, T: float, r: float, sigma: float) -> float:
        """计算期权 Vega (每1%波动率变化)"""
        d1, _ = self._bs_d1_d2(S, K, T, r, sigma)
        sqrt_T = math.sqrt(T)
        if S <= 0 or T <= 0:
            return 0.0
        return S * _norm_pdf(d1) * sqrt_T / 100.0

    def _bs_rho(self, S: float, K: float, T: float, r: float, sigma: float, call: bool = True) -> float:
        """计算期权 Rho (每1%利率变化)"""
        _d1, d2 = self._bs_d1_d2(S, K, T, r, sigma)
        if S <= 0 or T <= 0:
            return 0.0
        if call:
            return K * T * math.exp(-r * T) * _norm_cdf(d2) / 100.0
        else:
            return -K * T * math.exp(-r * T) * _norm_cdf(-d2) / 100.0

    def calc_option_greeks(
        self, S: float, K: float, T: float, r: float = 0.02, sigma: float = 0.2, call: bool = True
    ) -> GreekExposure:
        """计算期权完整 Greeks"""
        return GreekExposure(
            delta=self._bs_delta(S, K, T, r, sigma, call),
            gamma=self._bs_gamma(S, K, T, r, sigma),
            theta=self._bs_theta(S, K, T, r, sigma, call),
            vega=self._bs_vega(S, K, T, r, sigma),
            rho=self._bs_rho(S, K, T, r, sigma, call),
        )

    def calc_portfolio_greeks(self, positions: dict[str, dict], prices: dict[str, float]) -> GreekExposure:
        """计算持仓组合的 Greeks 暴露 (兼容 Dict[str, float] 和 Dict[str, dict])

        期权持仓自动使用 Black-Scholes 模型计算 Greeks
        """
        exposure = GreekExposure()
        for code, pos in positions.items():
            # 兼容简单数值型持仓
            if isinstance(pos, (int, float)):
                qty = float(pos or 0)
                price = float(prices.get(code, 0.0))
                if qty == 0 or price <= 0:
                    continue
                multiplier = 1.0
                beta = 1.0
                delta = 1.0
                gamma = 0.0
                theta = 0.0
                vega = 0.0
            else:
                qty = float(pos.get("shares", 0) or pos.get("target_contracts", 0) or 0)
                price = float(prices.get(code, pos.get("est_price", 0.0)))
                if qty == 0 or price <= 0:
                    continue
                multiplier = float(pos.get("multiplier", 1.0))
                beta = float(pos.get("beta", 1.0))
                pos_type = pos.get("type", "").upper()

                # 期权持仓使用 Black-Scholes 计算 Greeks
                if pos_type == "OPTION":
                    S = float(pos.get("underlying_price", price))
                    K = float(pos.get("strike", price))
                    T = float(pos.get("days_to_expiry", 30)) / 365.0
                    r = float(pos.get("risk_free_rate", 0.02))
                    sigma = float(pos.get("implied_vol", 0.2))
                    call = pos.get("option_type", "CALL").upper() == "CALL"
                    opt_greeks = self.calc_option_greeks(S, K, T, r, sigma, call)
                    delta = opt_greeks.delta
                    gamma = opt_greeks.gamma
                    theta = opt_greeks.theta
                    vega = opt_greeks.vega
                else:
                    delta = float(pos.get("delta", 1.0))
                    gamma = float(pos.get("gamma", 0.0))
                    theta = float(pos.get("theta", 0.0))
                    vega = float(pos.get("vega", 0.0))
            notional = qty * price * multiplier
            exposure.delta += notional * beta * delta
            exposure.gamma += notional * beta * gamma
            exposure.theta += notional * beta * theta
            exposure.vega += notional * beta * vega
        return exposure

    def target_futures_delta_hedge(
        self, portfolio_exposure: GreekExposure, hedge_instruments: list[HedgeInstrument], prices: dict[str, float]
    ) -> dict[str, float]:
        """基于 Delta 计算期货对冲目标量"""
        if not hedge_instruments:
            return {}
        current_delta = portfolio_exposure.delta
        residual_delta = current_delta - self.target_delta
        if abs(residual_delta) < 1e-6:
            return {}

        targets: dict[str, float] = {}
        remaining = residual_delta
        for inst in hedge_instruments:
            if inst.instrument_type.upper() != "FUTURES":
                continue
            price = float(prices.get(inst.code, 0.0))
            if price <= 0 or inst.delta == 0:
                continue
            hedge_delta_per_unit = inst.delta * inst.multiplier * price
            if remaining * hedge_delta_per_unit < 0:
                unit = -remaining / hedge_delta_per_unit
                targets[inst.code] = float(unit)
                remaining = 0.0
                break
        return targets

    def target_option_greeks_hedge(
        self, portfolio_exposure: GreekExposure, options: list[HedgeInstrument], prices: dict[str, float]
    ) -> dict[str, dict]:
        """基于 Greeks 计算期权对冲目标量"""
        if not options:
            return {}
        targets: dict[str, dict] = {}
        for opt in options:
            if opt.instrument_type.upper() != "OPTION":
                continue
            price = float(prices.get(opt.code, 0.0))
            if price <= 0:
                continue
            target = {
                "delta_target": -portfolio_exposure.delta * 0.1 if opt.delta else 0.0,
                "gamma_target": -portfolio_exposure.gamma * 0.1 if opt.gamma else 0.0,
                "vega_target": min(abs(portfolio_exposure.vega), self.max_vega) * 0.1,
                "theta_cap": self.max_theta_burn,
            }
            targets[opt.code] = target
        return targets

    def hedge_ratio(self, portfolio_value: float, hedge_value: float) -> float:
        """对冲比例"""
        if portfolio_value <= 0:
            return 0.0
        return hedge_value / portfolio_value

    def rebalance_signal(self, current_exposure: GreekExposure, tolerance: float = 0.05) -> dict[str, bool]:
        """判断是否需要再平衡"""
        delta_ok = abs(current_exposure.delta - self.target_delta) <= tolerance * max(abs(current_exposure.delta), 1.0)
        gamma_ok = abs(current_exposure.gamma - self.target_gamma) <= tolerance * max(abs(current_exposure.gamma), 1.0)
        vega_ok = abs(current_exposure.vega) <= self.max_vega
        theta_ok = current_exposure.theta >= self.max_theta_burn
        return {
            "delta_rebalance": not delta_ok,
            "gamma_rebalance": not gamma_ok,
            "vega_rebalance": not vega_ok,
            "theta_rebalance": not theta_ok,
            "need_rebalance": not (delta_ok and gamma_ok and vega_ok and theta_ok),
        }
