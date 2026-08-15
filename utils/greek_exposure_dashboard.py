"""
Greeks 暴露监控面板 (Greek Exposure Dashboard)
=============================================

实现 README v8.1 中的「Greeks 监控面板」:
  - 实时 Greeks 暴露监控 (Delta / Gamma / Theta / Vega / Rho)
  - 与目标对比 (target_delta / target_gamma / max_vega / max_theta_burn)
  - 再平衡信号强度 (OK / WARN / CRITICAL)
  - 行动建议 (期货对冲量 / 期权调整量)

数据来源:
  - config/positions.json  (持仓 + 期权 Greeks + 对冲工具)
  - utils/greek_hedge_manager.py (Black-Scholes + 再平衡信号)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ============================================================
# 默认路径
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POSITIONS_PATH = PROJECT_ROOT / "config" / "positions.json"


# ============================================================
# 数据结构
# ============================================================
@dataclass
class GreekSnapshot:
    """Greeks 快照"""

    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0


@dataclass
class GreekDashboard:
    """Greeks 监控面板结果"""

    snapshot: GreekSnapshot = field(default_factory=GreekSnapshot)
    targets: dict[str, float] = field(default_factory=dict)
    rebalance_signals: dict[str, bool] = field(default_factory=dict)
    signal_levels: dict[str, str] = field(default_factory=dict)  # OK / WARN / CRITICAL
    recommendations: list[str] = field(default_factory=list)
    per_position: list[dict[str, Any]] = field(default_factory=list)


# ============================================================
# 数据加载 (兼容旧版接口)
# ============================================================
def load_positions(path: str | Path = DEFAULT_POSITIONS_PATH):
    """加载持仓和价格 (兼容旧版接口)"""
    path = Path(path)
    if not path.exists():
        return {}, {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # P2 模块 fail-safe, 待后续精确化
        return {}, {}

    positions = {}
    prices = {}
    for item in data.get("positions", {}).values():
        code = item.get("code")
        qty = item.get("phase1_shares") or item.get("total_shares") or item.get("shares", 0)
        price = item.get("est_price", 0.0)
        if code and qty:
            positions[code] = {
                "shares": float(qty),
                "est_price": float(price),
                "name": item.get("name", code),
                "beta": float(item.get("beta", 1.0) or 1.0),
                "delta": float(item.get("delta", 1.0) or 1.0),
                "gamma": float(item.get("gamma", 0.0) or 0.0),
                "theta": float(item.get("theta", 0.0) or 0.0),
                "vega": float(item.get("vega", 0.0) or 0.0),
                "type": item.get("type", "STOCK"),
            }
            prices[code] = float(price)
    return positions, prices


# ============================================================
# 信号强度判定
# ============================================================
def _signal_level(value: float, target: float, tolerance: float = 0.05) -> str:
    """判定单维度信号强度

    Returns:
        "OK" / "WARN" / "CRITICAL"
    """
    abs_value = abs(value)
    abs_target = abs(target)
    diff = abs(value - target)
    threshold = tolerance * max(abs_value, abs_target, 1.0)
    if diff <= threshold:
        return "OK"
    if diff <= 2 * threshold:
        return "WARN"
    return "CRITICAL"


def _build_recommendations(exposure: Any, signals: dict[str, bool], levels: dict[str, str]) -> list[str]:
    """基于 Greeks 暴露生成行动建议"""
    recs: list[str] = []

    if levels.get("delta") in ("WARN", "CRITICAL") and signals.get("delta_rebalance"):
        # 期货对冲量建议: 残余 Delta / IF 期货 Delta (300 × 价格)
        # 简化: 假设 IF 期货价格 4500, 乘数 300
        if_price = 4500.0
        if_multiplier = 300
        per_contract_delta = if_price * if_multiplier
        if per_contract_delta > 0:
            target_contracts = abs(exposure.delta) / per_contract_delta
            direction = "卖出" if exposure.delta > 0 else "买入"
            recs.append(
                f"Delta 再平衡: 建议 {direction} IF 期货 {target_contracts:.2f} 手 "
                f"(当前 Delta {exposure.delta:,.0f}, 单手 Delta {per_contract_delta:,.0f})"
            )

    if levels.get("gamma") in ("WARN", "CRITICAL") and signals.get("gamma_rebalance"):
        recs.append(f"Gamma 再平衡: 当前 Gamma {exposure.gamma:,.2f}, 建议调整期权头寸以平抑二阶导风险")

    if levels.get("vega") in ("WARN", "CRITICAL") and signals.get("vega_rebalance"):
        recs.append(f"Vega 再平衡: 当前 Vega {exposure.vega:,.2f}, 建议买入/卖出跨式期权降低波动率敞口")

    if levels.get("theta") in ("WARN", "CRITICAL") and signals.get("theta_rebalance"):
        recs.append(
            f"Theta 再平衡: 当前 Theta {exposure.theta:,.2f}, 时间衰减过快, "
            "建议平仓部分近月期权或卖出远月 Call 减少时间价值流失"
        )

    if not recs:
        recs.append("所有 Greeks 在容忍范围内, 无需再平衡")

    return recs


# ============================================================
# 主面板函数
# ============================================================
def compute_dashboard(
    positions_path: str | Path = DEFAULT_POSITIONS_PATH,
    target_delta: float = 0.0,
    target_gamma: float = 0.0,
    max_vega: float = 50_000.0,
    max_theta_burn: float = -5_000.0,
    tolerance: float = 0.05,
) -> GreekDashboard:
    """计算 Greeks 监控面板

    Args:
        positions_path: positions.json 路径
        target_delta: 目标 Delta
        target_gamma: 目标 Gamma
        max_vega: Vega 最大容忍
        max_theta_burn: Theta 最大损耗 (负值)
        tolerance: 再平衡容忍度 (5%)

    Returns:
        GreekDashboard 数据对象
    """
    dashboard = GreekDashboard()
    try:
        from utils.greek_hedge_manager import GreekHedgeManager
    except ImportError:
        dashboard.recommendations.append("GreekHedgeManager 模块不可用")
        return dashboard

    positions, prices = load_positions(positions_path)
    if not positions:
        dashboard.recommendations.append("无持仓数据")
        return dashboard

    manager = GreekHedgeManager(
        target_delta=target_delta,
        target_gamma=target_gamma,
        max_vega=max_vega,
        max_theta_burn=max_theta_burn,
    )

    exposure = manager.calc_portfolio_greeks(positions, prices)
    dashboard.snapshot = GreekSnapshot(
        delta=exposure.delta,
        gamma=exposure.gamma,
        theta=exposure.theta,
        vega=exposure.vega,
        rho=exposure.rho,
    )
    dashboard.targets = {
        "target_delta": target_delta,
        "target_gamma": target_gamma,
        "max_vega": max_vega,
        "max_theta_burn": max_theta_burn,
    }

    # 再平衡信号
    signals = manager.rebalance_signal(exposure, tolerance=tolerance)
    dashboard.rebalance_signals = signals

    # 信号强度
    dashboard.signal_levels = {
        "delta": _signal_level(exposure.delta, target_delta, tolerance),
        "gamma": _signal_level(exposure.gamma, target_gamma, tolerance),
        "vega": "OK"
        if abs(exposure.vega) <= max_vega
        else ("WARN" if abs(exposure.vega) <= 1.5 * max_vega else "CRITICAL"),
        "theta": "OK"
        if exposure.theta >= max_theta_burn
        else ("WARN" if exposure.theta >= 1.5 * max_theta_burn else "CRITICAL"),
    }

    # 每标的 Greeks 贡献 (Top 10)
    per_position = []
    for code, pos in positions.items():
        qty = float(pos.get("shares", 0))
        price = float(prices.get(code, 0))
        beta = float(pos.get("beta", 1.0))
        if qty <= 0 or price <= 0:
            continue
        notional = qty * price
        delta_contrib = notional * beta * float(pos.get("delta", 1.0))
        gamma_contrib = notional * beta * float(pos.get("gamma", 0.0))
        theta_contrib = notional * beta * float(pos.get("theta", 0.0))
        vega_contrib = notional * beta * float(pos.get("vega", 0.0))
        per_position.append(
            {
                "code": code,
                "name": pos.get("name", code),
                "notional": round(notional, 2),
                "delta_contrib": round(delta_contrib, 2),
                "gamma_contrib": round(gamma_contrib, 2),
                "theta_contrib": round(theta_contrib, 2),
                "vega_contrib": round(vega_contrib, 2),
            }
        )
    per_position.sort(key=lambda x: abs(x["delta_contrib"]), reverse=True)
    dashboard.per_position = per_position[:10]

    # 行动建议
    dashboard.recommendations = _build_recommendations(exposure, signals, dashboard.signal_levels)

    return dashboard


def dashboard_to_dict(dashboard: GreekDashboard) -> dict[str, Any]:
    """转字典"""
    return {
        "snapshot": {
            "delta": round(dashboard.snapshot.delta, 2),
            "gamma": round(dashboard.snapshot.gamma, 2),
            "theta": round(dashboard.snapshot.theta, 2),
            "vega": round(dashboard.snapshot.vega, 2),
            "rho": round(dashboard.snapshot.rho, 2),
        },
        "targets": dashboard.targets,
        "rebalance_signals": dashboard.rebalance_signals,
        "signal_levels": dashboard.signal_levels,
        "recommendations": dashboard.recommendations,
        "per_position_top10": dashboard.per_position,
    }


# ============================================================
# 终端输出
# ============================================================
def print_dashboard(positions_path: str | Path = DEFAULT_POSITIONS_PATH) -> None:
    """打印 Greeks 监控面板 (兼容旧版接口)"""
    dashboard = compute_dashboard(positions_path)
    if not dashboard.snapshot and not dashboard.recommendations:
        logger.info("无持仓数据")
        return

    logger.info("=" * 70)
    logger.info("组合 Greeks 暴露监控面板 (Greek Exposure Dashboard)")
    logger.info("=" * 70)

    snap = dashboard.snapshot
    targets = dashboard.targets
    logger.info("\n[当前 Greeks 暴露]")
    logger.info(f"  Delta: {snap.delta:>15,.2f}  (目标: {targets.get('target_delta', 0):,.2f})")
    logger.info(f"  Gamma: {snap.gamma:>15,.2f}  (目标: {targets.get('target_gamma', 0):,.2f})")
    logger.info(f"  Theta: {snap.theta:>15,.2f}  (下限: {targets.get('max_theta_burn', 0):,.2f})")
    logger.info(f"  Vega:  {snap.vega:>15,.2f}  (上限: {targets.get('max_vega', 0):,.2f})")
    logger.info(f"  Rho:   {snap.rho:>15,.2f}")

    logger.info("\n[再平衡信号强度]")
    levels = dashboard.signal_levels
    signals = dashboard.rebalance_signals
    for greek in ("delta", "gamma", "vega", "theta"):
        level = levels.get(greek, "OK")
        signal = signals.get(f"{greek}_rebalance", False)
        icon = {"OK": "✅", "WARN": "⚠️ ", "CRITICAL": "🚨"}.get(level, "?")
        logger.info(f"  {icon} {greek.upper():<6} {level:<10} 再平衡: {'需要' if signal else '正常'}")
    overall = signals.get("need_rebalance", False)
    logger.info(f"  {'🚨' if overall else '✅'} 综合   {'需要再平衡' if overall else '正常'}")

    if dashboard.per_position:
        logger.info("\n[Top 10 标的 Delta 贡献]")
        logger.info(f"  {'代码':<12} {'名称':<12} {'名义价值':>14} {'Delta':>14} {'Gamma':>10} {'Theta':>10} {'Vega':>10}")
        for p in dashboard.per_position:
            logger.info(
                f"  {p['code']:<12} {p['name'][:10]:<12} "
                f"{p['notional']:>14,.0f} {p['delta_contrib']:>+14,.0f} "
                f"{p['gamma_contrib']:>+10,.0f} {p['theta_contrib']:>+10,.0f} {p['vega_contrib']:>+10,.0f}"
            )

    if dashboard.recommendations:
        logger.info("\n[行动建议]")
        for i, r in enumerate(dashboard.recommendations, 1):
            logger.info(f"  {i}. {r}")
    logger.info("=" * 70)


if __name__ == "__main__":
    print_dashboard()
