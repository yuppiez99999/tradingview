"""
v7.5 风险预算器 —— Risk Parity + 改进 Kelly + 三级回撤防御

核心功能：
1. 改进型 Kelly (James-Stein 收缩) + 单笔风险硬约束
2. Risk Parity (等风险贡献, Ledoit-Wolf 协方差 + Newton-Raphson)
3. 三级回撤防御: NORMAL → DEFENSE → CIRCUIT_BREAKER
4. 滚动 60 日 High-Water Mark 回撤计算

与 v7.4 的关系：替代 enhanced_risk_manager.py 中的固定阈值方法，
引入完整的风险预算框架。
"""

import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("v7.5.risk_budgeter")


# CR3 修复: 熔断清仓回调类型
# callback(reason: str, dd: float, equity: float, ts: Optional[datetime]) -> int
# 返回值: 成功平仓的持仓数量 (0 表示未平仓或回调失败)
LiquidateCallback = Callable[[str, float, float, Optional[datetime]], int]


class RiskBudgeter:
    """v7.5 风险预算器：Risk Parity + 改进 Kelly + 三级回撤"""

    def __init__(
        self,
        total_capital: float = 5_000_000,
        target_return: float = 0.08,
        max_dd: float = 0.08,
        single_trade_risk: float = 0.015,
        kelly_tau2: float = 0.01,
        rf: float = 0.02,
        mu_market: float = 0.08,
        liquidate_callback: Optional[LiquidateCallback] = None,
    ):
        self.C = total_capital
        self.target = target_return
        self.max_dd = max_dd
        self.single_risk = single_trade_risk
        self.kelly_tau2 = kelly_tau2
        self.rf = rf
        self.mu_market = mu_market

        # CR3 修复: 熔断清仓回调 (避免直接耦合 broker, 由调用方注入)
        # 回调签名: callback(reason, dd, equity, ts) -> int (平仓数量)
        self._liquidate_callback = liquidate_callback
        self._last_liquidate_result: Optional[Dict] = None

        # 回撤追踪
        self.hwm = total_capital
        self.hwm_window = deque(maxlen=60)
        # 预填初始资本作为 HWM 基线，避免首次更新即误判为 0 回撤
        for _ in range(60):
            self.hwm_window.append(total_capital)
        self.mode = "NORMAL"  # NORMAL / DEFENSE / CIRCUIT_BREAKER
        self.position_multiplier = 1.0
        self.circuit_break_until: Optional[datetime] = None

        # 历史记录
        self.dd_history = deque(maxlen=252)
        self.mode_history = deque(maxlen=100)

        logger.info(
            f"RiskBudgeter 初始化完成, 资本: {self.C:,.0f}, "
            f"单笔风险: {self.single_risk:.2%}, "
            f"熔断清仓回调: {'已注入' if liquidate_callback else '未注入(仅告警)'}"
        )

    def set_liquidate_callback(self, callback: LiquidateCallback) -> None:
        """CR3 修复: 延迟注入熔断清仓回调 (用于 broker 后初始化场景)"""
        self._liquidate_callback = callback
        logger.info("[CR3] 熔断清仓回调已延迟注入")

    def _trigger_liquidation(self, reason: str, dd: float, equity: float, ts: Optional[datetime]) -> int:
        """CR3 修复: 熔断时触发强制平仓

        fail-safe 设计: 回调异常不阻断熔断流程, 仅记录日志
        24 小时冷却期内持仓裸奔是 P0 风险, 必须尽力平仓

        Returns:
            成功平仓的持仓数量 (0 表示回调未注入或失败)
        """
        if self._liquidate_callback is None:
            logger.critical(
                f"[CR3-熔断清仓] 回调未注入, 无法自动平仓! "
                f"reason={reason}, DD={dd:.2%}, equity={equity:,.0f}, "
                f"持仓将在 24h 冷却期内继续裸奔, 请人工介入"
            )
            self._last_liquidate_result = {
                "reason": reason,
                "dd": dd,
                "equity": equity,
                "liquidated": 0,
                "error": "callback_not_set",
                "ts": (ts or datetime.now()).isoformat(),
            }
            return 0

        try:
            n_liquidated = self._liquidate_callback(reason, dd, equity, ts)
            n_liquidated = int(n_liquidated or 0)
            logger.critical(
                f"[CR3-熔断清仓] 自动平仓完成, 平掉 {n_liquidated} 个持仓, "
                f"reason={reason}, DD={dd:.2%}, equity={equity:,.0f}"
            )
            self._last_liquidate_result = {
                "reason": reason,
                "dd": dd,
                "equity": equity,
                "liquidated": n_liquidated,
                "error": None,
                "ts": (ts or datetime.now()).isoformat(),
            }
            return n_liquidated
        except Exception as e:
            logger.critical(
                f"[CR3-熔断清仓] 自动平仓回调抛异常, 请人工介入! "
                f"reason={reason}, DD={dd:.2%}, equity={equity:,.0f}, "
                f"error={e}"
            )
            self._last_liquidate_result = {
                "reason": reason,
                "dd": dd,
                "equity": equity,
                "liquidated": 0,
                "error": str(e),
                "ts": (ts or datetime.now()).isoformat(),
            }
            return 0

    # ============================================================
    # 三级回撤防御
    # ============================================================

    def update_drawdown(self, equity: float, ts: Optional[datetime] = None) -> str:
        """更新回撤并返回当前模式"""
        self.hwm_window.append(equity)
        self.hwm = max(self.hwm_window) if self.hwm_window else max(self.hwm, equity)
        dd = (self.hwm - equity) / self.hwm if self.hwm > 0 else 0.0
        self.dd_history.append({"ts": ts or datetime.now(), "dd": dd, "equity": equity})

        # 熔断期检查
        if self.circuit_break_until and ts and ts < self.circuit_break_until:
            logger.warning(f"[熔断中] 剩余冷却至 {self.circuit_break_until}")
            return "CIRCUIT_BREAK_ACTIVE"

        # 三级判定 (v8.5升级: 止损线从15%收紧至8%)
        if dd >= 0.07:
            # CR3 修复: 熔断时不仅禁开仓, 还要强制平掉已有持仓
            # 24 小时冷却期内持仓继续承受市场波动是 P0 风险
            already_in_breaker = self.mode == "CIRCUIT_BREAKER"
            self.mode = "CIRCUIT_BREAKER"
            self.position_multiplier = 0.0
            self.circuit_break_until = (ts or datetime.now()) + timedelta(hours=24)
            logger.critical(f"[熔断] DD={dd:.2%}, 触发强制清仓, 冷却至 {self.circuit_break_until}")
            # CR3 修复: 仅在首次进入熔断状态时触发平仓 (避免冷却期内重复平仓)
            if not already_in_breaker:
                self._trigger_liquidation(
                    reason=f"circuit_breaker_dd_{dd:.4f}",
                    dd=dd,
                    equity=equity,
                    ts=ts,
                )
            return "CIRCUIT_BREAKER"

        elif dd >= 0.05:
            if self.mode != "DEFENSE":
                self.mode = "DEFENSE"
                self.position_multiplier = 0.5
                logger.warning(f"[防御模式] DD={dd:.2%}, 仓位减半, 只减不加")
                return "DEFENSE_ACTIVATED"
            return "DEFENSE_HOLD"

        else:
            if self.mode != "NORMAL":
                logger.info(f"[恢复正常] DD={dd:.2%}, 仓位恢复")
            self.mode = "NORMAL"
            self.position_multiplier = 1.0
            return "NORMAL"

    @property
    def current_dd(self) -> float:
        if not self.dd_history:
            return 0.0
        return self.dd_history[-1]["dd"]

    @property
    def allow_new_positions(self) -> bool:
        return self.mode == "NORMAL"

    # ============================================================
    # 改进型 Kelly (James-Stein 收缩)
    # ============================================================

    def kelly_weight(self, mu_hist: float, sigma: float, beta: float, n: int = 252) -> float:
        """
        James-Stein 收缩 + 半 Kelly + 单笔风险硬约束

        Args:
            mu_hist: 历史年化收益
            sigma: 年化波动率
            beta: 对市场 Beta
            n: 样本量

        Returns:
            建议仓位权重 [0, 0.20]
        """
        if not np.isfinite(sigma) or sigma <= 0:
            return 0.0
        if not np.isfinite(mu_hist):
            mu_hist = 0.0
        if not np.isfinite(beta):
            beta = 1.0

        # James-Stein 收缩
        mu_prior = self.rf + beta * (self.mu_market - self.rf)
        sigma_mu = sigma / np.sqrt(max(n, 1))
        omega = self.kelly_tau2 / (self.kelly_tau2 + sigma_mu**2 + 1e-10)
        if not np.isfinite(omega):
            omega = 0.5
        mu_shrink = omega * mu_hist + (1 - omega) * mu_prior
        if not np.isfinite(mu_shrink):
            mu_shrink = mu_prior

        # 半 Kelly
        try:
            f_kelly = (mu_shrink - self.rf) / (sigma**2)
            f_kelly = 0.0 if not np.isfinite(f_kelly) else f_kelly
        except Exception:
            f_kelly = 0.0
        f_half = 0.5 * f_kelly

        # 单笔风险硬约束
        try:
            f_risk = (self.single_risk * self.C) / (sigma * max(abs(beta), 0.1) * self.C)
            f_risk = 0.0 if not np.isfinite(f_risk) else f_risk
        except Exception:
            f_risk = 0.0

        w = float(np.clip(min(f_half, f_risk), 0.0, 0.20))
        w = 0.0 if not np.isfinite(w) else w
        return w * self.position_multiplier

    # ============================================================
    # Risk Parity (等风险贡献)
    # ============================================================

    def risk_parity_weights(self, returns: pd.DataFrame, cov_estimator: str = "ledoit_wolf") -> np.ndarray:
        """
        Ledoit-Wolf 收缩协方差 + Newton-Raphson 求解 ERC 权重

        Args:
            returns: N 资产 x T 时间的收益率 DataFrame
            cov_estimator: 协方差估计方法

        Returns:
            ERC 权重向量
        """
        n = returns.shape[1]
        if n <= 1:
            return np.ones(n) / n

        # Ledoit-Wolf 收缩协方差
        try:
            from sklearn.covariance import LedoitWolf

            lw = LedoitWolf().fit(returns.values)
            sigma = lw.covariance_
        except ImportError:
            sigma = returns.cov().values

        # Newton-Raphson 求解
        w = np.ones(n) / n
        for _iteration in range(500):
            port_var = w @ sigma @ w
            if not np.isfinite(port_var) or port_var <= 0:
                break
            marginal = sigma @ w
            rc = w * marginal / np.sqrt(port_var)
            rc = np.nan_to_num(rc, nan=0.0, posinf=0.0, neginf=0.0)
            grad = 2 * (rc - rc.mean())
            if not np.isfinite(grad).all():
                break
            max_grad = np.abs(grad).max()
            if max_grad < 1e-6:
                break
            # 对角线近似 Hessian
            w = w - 0.01 * grad / (max_grad + 1e-8)
            w = np.clip(w, 1e-4, None)
            w = np.nan_to_num(w, nan=1.0 / n, posinf=1.0 / n, neginf=1.0 / n)
            w = w / w.sum()

        return w

    # ============================================================
    # 综合仓位计算
    # ============================================================

    def size_positions(
        self,
        symbols: List[str],
        mu_hist: Dict[str, float],
        sigma: Dict[str, float],
        beta: Dict[str, float],
        returns: pd.DataFrame,
        kelly_weight: float = 0.6,
        rp_weight: float = 0.4,
    ) -> Dict[str, float]:
        """
        综合 Kelly + Risk Parity 双轨仓位

        Args:
            symbols: 标的列表
            mu_hist: 各标的年化历史收益
            sigma: 各标的年化波动率
            beta: 各标的 Beta
            returns: 收益率 DataFrame (columns=symbols)
            kelly_weight: Kelly 权重占比
            rp_weight: Risk Parity 权重占比

        Returns:
            {symbol: weight}
        """
        # Kelly 权重
        kelly_w = {}
        for s in symbols:
            kw = self.kelly_weight(
                mu_hist.get(s, self.mu_market),
                sigma.get(s, 0.25),
                beta.get(s, 1.0),
                n=len(returns) if returns is not None else 252,
            )
            kelly_w[s] = kw
        kelly_sum = sum(kelly_w.values())
        if kelly_sum > 0:
            kelly_w = {s: w / kelly_sum for s, w in kelly_w.items()}

        # RP 权重
        rp_w = {}
        if returns is not None and returns.shape[1] > 1:
            rp_arr = self.risk_parity_weights(returns[symbols].dropna())
            rp_w = {s: float(rp_arr[i]) for i, s in enumerate(symbols)}
        else:
            for s in symbols:
                rp_w[s] = 1.0 / len(symbols)

        # 融合
        final_w = {}
        for s in symbols:
            final_w[s] = kelly_weight * kelly_w.get(s, 0) + rp_weight * rp_w.get(s, 0)

        # 归一化
        total = sum(final_w.values())
        if total > 0:
            final_w = {s: w / total for s, w in final_w.items()}

        return final_w

    # ============================================================
    # 风险预算检查
    # ============================================================

    def check_budget(self, positions: Dict[str, float], returns: pd.DataFrame) -> Dict:
        """检查组合风险预算"""
        n = len(positions)
        if n == 0:
            return {"portfolio_vol": 0.0, "risk_contributions": {}, "status": "empty"}

        w = np.array([positions.get(c, 0) for c in returns.columns])
        w = w / w.sum() if w.sum() > 0 else w

        try:
            from sklearn.covariance import LedoitWolf

            lw = LedoitWolf().fit(returns.values)
            cov = lw.covariance_
        except ImportError:
            cov = returns.cov().values

        port_var = w @ cov @ w
        if not np.isfinite(port_var) or port_var < 0:
            port_var = 0.0
        port_vol = np.sqrt(port_var) * np.sqrt(252)
        port_vol = 0.0 if not np.isfinite(port_vol) else port_vol
        if port_var > 0:
            rc = w * (cov @ w) / np.sqrt(port_var)
            rc = np.nan_to_num(rc, nan=0.0, posinf=0.0, neginf=0.0)
        else:
            rc = np.zeros(n)

        return {
            "portfolio_vol": float(port_vol),
            "risk_contributions": dict(zip(returns.columns, rc)),
            "max_concentration": float(np.max(w)),
            "herfindahl": float(np.sum(w**2)),
        }

    def get_status(self) -> Dict:
        return {
            "mode": self.mode,
            "current_dd": self.current_dd,
            "position_multiplier": self.position_multiplier,
            "hwm": self.hwm,
            "allow_new_positions": self.allow_new_positions,
            "circuit_break_until": str(self.circuit_break_until) if self.circuit_break_until else None,
            # CR3 修复: 暴露最近一次熔断清仓结果供监控
            "last_liquidate_result": self._last_liquidate_result,
            "liquidate_callback_set": self._liquidate_callback is not None,
        }
