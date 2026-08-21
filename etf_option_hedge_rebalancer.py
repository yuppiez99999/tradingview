"""A股ETF + 期权对冲 + 自我再平衡子模型编排器
================================================

独立200万子组合，纯ETF仓位 + ETF期权对冲(认沽保护) + 自我再平衡
目标: 年化收益 >= 8%, 最大回撤 < 15%

复用现有模块:
  - utils/protective_put_engine.py  (认沽期权保护)
  - utils/broad_based_etf_policy.py (宽基ETF资金流加减仓)
  - utils/portfolio_optimizer.py    (因子信号Alpha增强)
  - utils/drawdown_breaker.py       (回撤分级熔断)
  - utils/kill_switch.py            (三级保证金熔断)

设计依据:
  - V9 Regime-LGB 实测年化19.62%/回撤9.95% (cairn/ROADMAP.md:24)
  - BL+MVSK(378) 跨周期4/4段跑赢BL+MV (cairn/ROADMAP.md:39)
  - protective_put_engine 年化成本<2.5% (utils/protective_put_engine.py:84)

用法:
    from etf_option_hedge_rebalancer import ETFOptionHedgeRebalancer
    rebalancer = ETFOptionHedgeRebalancer()
    plan = rebalancer.run_daily_rebalance(positions, prices, "2026-08-20")
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import yaml

logger = logging.getLogger("etf_option_hedge_rebalancer")

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from utils.drawdown_breaker import DrawdownCircuitBreaker, DrawdownDecision, DrawdownLevel
    _DD_OK = True
except ImportError as e:
    logger.warning("DrawdownCircuitBreaker 加载失败, 降级防护: %s", e)
    _DD_OK = False

try:
    from utils.kill_switch import KillSwitch
    _KS_OK = True
except ImportError as e:
    logger.warning("KillSwitch 加载失败, 降级防护: %s", e)
    _KS_OK = False

try:
    from utils.protective_put_engine import ProtectivePutEngine
    _PP_OK = True
except ImportError as e:
    logger.warning("ProtectivePutEngine 加载失败, 期权对冲不可用: %s", e)
    _PP_OK = False

try:
    from utils.portfolio_optimizer import PortfolioOptimizer
    _PO_OK = True
except ImportError as e:
    logger.warning("PortfolioOptimizer 加载失败, Alpha增强降级: %s", e)
    _PO_OK = False

try:
    from utils.broad_based_etf_policy import adjust_plan_with_national_team_flow
    _ETF_FLOW_OK = True
except ImportError as e:
    logger.warning("broad_based_etf_policy 加载失败, ETF资金流加减仓降级: %s", e)
    _ETF_FLOW_OK = False

try:
    from utils.signal_fusion import SignalFusionEngine
    _SF_OK = True
except ImportError as e:
    logger.warning("SignalFusionEngine 加载失败, 动态权重降级: %s", e)
    _SF_OK = False

try:
    from utils.alpha.vol_regime_weighter import VolRegimeWeighter
    _VRW_OK = True
except ImportError as e:
    logger.warning("VolRegimeWeighter 加载失败, Regime适应降级: %s", e)
    _VRW_OK = False

try:
    from utils.alpha.drift_monitor import DriftMonitor
    _DM_OK = True
except ImportError as e:
    logger.warning("DriftMonitor 加载失败, 漂移检测降级: %s", e)
    _DM_OK = False

try:
    from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection
    _MR_OK = True
except ImportError as e:
    logger.warning("MemoryReflection 加载失败, 决策记忆降级: %s", e)
    _MR_OK = False

try:
    from utils.alpha.evolution_orchestrator import EvolutionOrchestrator
    _EO_OK = True
except ImportError as e:
    logger.warning("EvolutionOrchestrator 加载失败, 进化编排降级: %s", e)
    _EO_OK = False


@dataclass
class RiskState:
    portfolio_value: float = 0.0
    total_exposure: float = 0.0
    portfolio_beta: float = 1.0
    portfolio_volatility: float = 0.0
    current_drawdown: float = 0.0
    var_95: float = 0.0
    max_single_weight: float = 0.0
    max_category_weight: float = 0.0


@dataclass
class DailyPlan:
    trade_date: str = ""
    risk_state: Optional[RiskState] = None
    drawdown_decision: Optional[DrawdownDecision] = None
    option_hedge: dict[str, Any] = field(default_factory=dict)
    etf_flow_adjustment: dict[str, Any] = field(default_factory=dict)
    alpha_enhancement: dict[str, Any] = field(default_factory=dict)
    rebalance_orders: list[dict] = field(default_factory=list)
    execution_summary: str = ""
    warning_flags: list[str] = field(default_factory=list)
    estimated_annual_return: float = 0.0
    estimated_max_drawdown: float = 0.0
    regime: dict[str, Any] = field(default_factory=dict)
    fused_signals: dict[str, Any] = field(default_factory=dict)
    drift_status: dict[str, Any] = field(default_factory=dict)
    reflection_context: dict[str, Any] = field(default_factory=dict)
    evolution_action: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "trade_date": self.trade_date,
            "risk_state": self.risk_state.__dict__ if self.risk_state else None,
            "drawdown_decision": self.drawdown_decision.to_dict() if self.drawdown_decision else None,
            "option_hedge": self.option_hedge,
            "etf_flow_adjustment": self.etf_flow_adjustment,
            "alpha_enhancement": self.alpha_enhancement,
            "rebalance_orders": self.rebalance_orders,
            "execution_summary": self.execution_summary,
            "warning_flags": self.warning_flags,
            "estimated_annual_return": self.estimated_annual_return,
            "estimated_max_drawdown": self.estimated_max_drawdown,
            "regime": self.regime,
            "fused_signals": self.fused_signals,
            "drift_status": self.drift_status,
            "reflection_context": self.reflection_context,
            "evolution_action": self.evolution_action,
        }


class ETFOptionHedgeRebalancer:
    """A股ETF + 期权对冲 + 自我再平衡子模型

    独立200万子组合，纯ETF仓位(100%) + ETF期权对冲 + 自我再平衡
    目标: 年化收益 >= 8%, 最大回撤 < 15%

    五阶段日度流程:
      Phase 1: 风险评估 (组合Beta/VaR/波动率/回撤)
      Phase 2: 回撤熔断检查 (L0/L1/L2/L3分级)
      Phase 3: ETF资金流加减仓 + Alpha增强调权
      Phase 4: 期权对冲决策 (认沽保护/滚仓)
      Phase 5: 阈值再平衡 + 生成执行计划
    """

    DEFAULT_CONFIG = "config/etf_option_subportfolio.yaml"

    def __init__(
        self,
        config_path: str | None = None,
        portfolio_value: float | None = None,
    ) -> None:
        self.config_path = str(
            Path(config_path) if config_path else _PROJECT_ROOT / self.DEFAULT_CONFIG
        )
        self.config = self._load_config()
        sub_cfg = self.config.get("subportfolio", {})
        self.portfolio_value = portfolio_value or float(sub_cfg.get("total_capital", 2_000_000))
        self.target_annual_return = float(sub_cfg.get("target_annual_return", 0.08))
        self.target_max_drawdown = float(sub_cfg.get("target_max_drawdown", 0.15))

        self._init_drawdown_breaker()
        self._init_kill_switch()
        self._init_protective_put_engine()
        self._init_portfolio_optimizer()
        self._init_signal_fusion()
        self._init_vol_regime_weighter()
        self._init_drift_monitor()
        self._init_memory_reflection()
        self._init_evolution_orchestrator()

        logger.info(
            "ETF期权对冲再平衡子模型初始化完成 | 总资本=%.0f | 目标年化=%.0f%% | 目标回撤<%.0f%%",
            self.portfolio_value,
            self.target_annual_return * 100,
            self.target_max_drawdown * 100,
        )

    def _load_config(self) -> dict:
        with open(self.config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _init_drawdown_breaker(self) -> None:
        if not _DD_OK:
            self.drawdown_breaker = None
            return
        rc = self.config.get("risk_control", {})
        self.drawdown_breaker = DrawdownCircuitBreaker(
            max_drawdown=rc.get("max_drawdown_limit", 0.15),
        )

    def _init_kill_switch(self) -> None:
        if not _KS_OK:
            self.kill_switch = None
            return
        rc = self.config.get("risk_control", {})
        self.kill_switch = KillSwitch(margin_limit=rc.get("margin_limit", 0.20))

    def _init_protective_put_engine(self) -> None:
        if not _PP_OK:
            self.put_engine = None
            return
        oh = self.config.get("options_hedge", {})
        self.put_engine = ProtectivePutEngine()
        cap = float(oh.get("total_capital", self.portfolio_value))
        self.put_engine.TOTAL_CAPITAL = cap
        self.put_engine.MAX_ANNUAL_COST_PCT = float(oh.get("max_annual_cost_pct", 0.025))
        self.put_engine.OTM_PCT = float(oh.get("otm_pct", 0.05))
        targets = oh.get("protection_targets")
        if targets:
            self.put_engine.PROTECTION_TARGETS = targets

    def _init_portfolio_optimizer(self) -> None:
        if not _PO_OK:
            self.portfolio_optimizer = None
            return
        ae = self.config.get("alpha_enhancement", {})
        if not ae.get("enabled", True):
            self.portfolio_optimizer = None
            return
        try:
            self.portfolio_optimizer = PortfolioOptimizer()
        except (ValueError, TypeError, OSError) as e:
            logger.warning("PortfolioOptimizer 初始化失败, Alpha增强降级: %s", e)
            self.portfolio_optimizer = None

    def _init_signal_fusion(self) -> None:
        if not _SF_OK:
            self.signal_fusion = None
            return
        try:
            self.signal_fusion = SignalFusionEngine()
            logger.info("SignalFusionEngine 已接入 (动态权重)")
        except (ValueError, TypeError, OSError) as e:
            logger.warning("SignalFusionEngine 初始化失败: %s", e)
            self.signal_fusion = None

    def _init_vol_regime_weighter(self) -> None:
        if not _VRW_OK:
            self.vol_regime_weighter = None
            return
        try:
            self.vol_regime_weighter = VolRegimeWeighter()
            logger.info("VolRegimeWeighter 已接入 (Regime适应)")
        except (ValueError, TypeError, OSError) as e:
            logger.warning("VolRegimeWeighter 初始化失败: %s", e)
            self.vol_regime_weighter = None

    def _init_drift_monitor(self) -> None:
        if not _DM_OK:
            self.drift_monitor = None
            return
        try:
            self.drift_monitor = DriftMonitor(model_name="etf_option_subportfolio")
            logger.info("DriftMonitor 已接入 (漂移检测)")
        except (ValueError, TypeError, OSError) as e:
            logger.warning("DriftMonitor 初始化失败: %s", e)
            self.drift_monitor = None

    def _init_memory_reflection(self) -> None:
        if not _MR_OK:
            self.memory_reflection = None
            return
        try:
            _mem_dir = str(Path(__file__).parent / "reports" / "ai_hedge_fund" / "memory")
            self.memory_reflection = MemoryReflection(memory_dir=_mem_dir)
            logger.info("MemoryReflection 已接入 (决策记忆)")
        except (ValueError, TypeError, OSError) as e:
            logger.warning("MemoryReflection 初始化失败: %s", e)
            self.memory_reflection = None

    def _init_evolution_orchestrator(self) -> None:
        if not _EO_OK:
            self.evolution_orchestrator = None
            return
        try:
            self.evolution_orchestrator = EvolutionOrchestrator()
            logger.info("EvolutionOrchestrator 已接入 (进化编排)")
        except (ValueError, TypeError, OSError) as e:
            logger.warning("EvolutionOrchestrator 初始化失败: %s", e)
            self.evolution_orchestrator = None

    def _sense_regime(self, risk: RiskState) -> dict[str, Any]:
        if not self.vol_regime_weighter or not self.vol_regime_weighter.enabled:
            return {}
        try:
            regime = self.vol_regime_weighter.sense_regime(
                daily_returns=None,
                current_drawdown=risk.current_drawdown,
            )
            return {
                "label": regime.label,
                "confidence": regime.confidence,
                "hedge_ratio": regime.aligned_hedge_ratio,
                "hedge_policy": regime.hedge_policy_key,
            }
        except (ValueError, TypeError, OSError, AttributeError) as e:
            logger.warning("Regime感知失败: %s", e)
            return {}

    def _fuse_signals(self, target_weights: dict[str, float]) -> dict[str, Any]:
        if not self.signal_fusion:
            return {}
        try:
            fused = self.signal_fusion.fuse()
            if not fused:
                return {}
            adjusted = {}
            for fs in fused:
                code = getattr(fs, "symbol", "")
                strength = getattr(fs, "strength", 0.0)
                if code in target_weights:
                    adjusted[code] = target_weights[code] * (1.0 + strength * 0.05)
            total = sum(adjusted.values())
            if total > 0:
                adjusted = {k: v / total for k, v in adjusted.items()}
            return {"n_signals": len(fused), "adjusted_weights": adjusted}
        except (ValueError, TypeError, OSError) as e:
            logger.warning("信号融合失败: %s", e)
            return {}

    def _record_decision(self, plan: DailyPlan) -> None:
        if not self.memory_reflection:
            return
        try:
            debate_results = {}
            for order in plan.rebalance_orders:
                code = order.get("code", "")
                action = order.get("action", "HOLD")
                debate_results[code] = {
                    "final_signal": "bullish" if action == "BUY" else ("bearish" if action == "SELL" else "neutral"),
                    "final_confidence": min(100, int(abs(order.get("adjust_value", 0)) / 10000)),
                    "winner": "bull" if action == "BUY" else ("bear" if action == "SELL" else "tie"),
                    "net_confidence": min(100, int(abs(order.get("adjust_value", 0)) / 10000)),
                    "reasoning": f"{action} {order.get('adjust_value', 0):.0f}",
                }
            session = {
                "session_id": f"etf_rebalance_{plan.trade_date.replace('-', '')}",
                "timestamp": plan.trade_date,
                "trade_date": plan.trade_date,
                "debate_results": debate_results,
                "analyst_signals_snapshot": {},
                "rebalance_orders": plan.rebalance_orders,
                "option_hedge": plan.option_hedge,
                "risk_state": plan.risk_state.__dict__ if plan.risk_state else {},
            }
            self.memory_reflection.record_decisions(session)
        except (ValueError, TypeError, OSError) as e:
            logger.warning("决策记录失败: %s", e)

    def _run_evolution_cycle(self) -> dict[str, Any]:
        if not self.evolution_orchestrator or not self.evolution_orchestrator.enabled:
            return {}
        try:
            return self.evolution_orchestrator.run_observation_cycle()
        except (ValueError, TypeError, OSError) as e:
            logger.warning("进化编排失败: %s", e)
            return {}

    def get_target_weights(self) -> dict[str, float]:
        positions = self.config.get("positions", {})
        return {
            code: float(info.get("target_weight", 0.0))
            for code, info in positions.items()
            if info.get("asset_type") == "etf"
        }

    def assess_risk(
        self,
        positions: dict[str, dict],
        prices: dict[str, float],
        current_drawdown: float = 0.0,
    ) -> RiskState:
        total_value = 0.0
        max_w = 0.0
        cat_values: dict[str, float] = {}
        for code, pos in positions.items():
            px = prices.get(code, 0.0)
            shares = float(pos.get("shares", 0))
            val = shares * px
            total_value += val
            cat = pos.get("category", "其他")
            cat_values[cat] = cat_values.get(cat, 0.0) + val

        weights = {}
        for code, pos in positions.items():
            px = prices.get(code, 0.0)
            shares = float(pos.get("shares", 0))
            w = (shares * px / total_value) if total_value > 0 else 0.0
            weights[code] = w
            if w > max_w:
                max_w = w

        max_cat_w = max(v / total_value for v in cat_values.values()) if total_value > 0 and cat_values else 0.0

        returns_arr = np.array([
            float(pos.get("daily_return", 0.0)) for pos in positions.values()
        ])
        port_ret = sum(
            weights.get(code, 0.0) * float(pos.get("daily_return", 0.0))
            for code, pos in positions.items()
        )
        port_vol = float(np.std(returns_arr) * np.sqrt(252)) if len(returns_arr) > 1 else 0.0
        var_95 = abs(port_ret) + 1.65 * port_vol / np.sqrt(252) if port_vol > 0 else 0.0

        return RiskState(
            portfolio_value=total_value,
            total_exposure=total_value,
            portfolio_beta=1.0,
            portfolio_volatility=port_vol,
            current_drawdown=current_drawdown,
            var_95=var_95,
            max_single_weight=max_w,
            max_category_weight=float(max_cat_w),
        )

    def check_drawdown_circuit(self, current_drawdown: float) -> Optional[DrawdownDecision]:
        if self.drawdown_breaker is None:
            return None
        dd = -abs(float(current_drawdown))
        decision = self.drawdown_breaker.evaluate(dd)
        if decision.level in (DrawdownLevel.FORCE_HEDGE, DrawdownLevel.HALT):
            logger.error("【回撤熔断】回撤%.2f%% 级别%s: %s", dd * 100, decision.level.value, decision.action)
        elif decision.level == DrawdownLevel.REDUCE:
            logger.warning("【回撤减仓】回撤%.2f%% 级别%s: %s", dd * 100, decision.level.value, decision.action)
        return decision

    def decide_option_hedge(self, drawdown_level: int = 0) -> dict[str, Any]:
        if self.put_engine is None:
            return {"enabled": False, "reason": "ProtectivePutEngine 不可用"}
        orders = self.put_engine.generate_put_orders(drawdown_level=drawdown_level)
        roll = self.put_engine.check_and_roll()
        result = {
            "enabled": True,
            "put_orders": orders,
            "roll_check": roll,
            "total_premium_est": orders.get("total_premium_est", 0),
            "annual_cost_pct": orders.get("annual_cost_pct", 0),
        }
        if orders.get("should_execute"):
            n = len(orders.get("orders", []))
            logger.info("【期权对冲】生成认沽保护订单 %d 张, 估算权利金 %.0f", n, result["total_premium_est"])
        if roll.get("needs_roll"):
            logger.info("【期权滚仓】%d 张认沽临近到期需滚仓", len(roll.get("expiring_puts", [])))
        return result

    def apply_etf_flow_adjustment(self, target_weights: dict[str, float]) -> dict[str, Any]:
        if not _ETF_FLOW_OK:
            return {"enabled": False, "reason": "broad_based_etf_policy 不可用"}
        cfg = self.config.get("etf_flow_adjustment", {})
        if not cfg.get("enabled", True):
            return {"enabled": False, "reason": "ETF资金流加减仓未启用"}
        plan = {
            "target_weights": target_weights,
            "positions": {
                code: {"target_weight": w, "category": "宽基"}
                for code, w in target_weights.items()
            },
        }
        try:
            adjusted = adjust_plan_with_national_team_flow(plan)
            logger.info("【ETF资金流】已应用社保国家队流向加减仓调整")
            return {"enabled": True, "adjusted_plan": adjusted}
        except (ValueError, KeyError, TypeError, AttributeError, OSError) as e:
            logger.warning("ETF资金流加减仓失败, 保持原权重: %s", e)
            return {"enabled": True, "error": str(e), "adjusted_plan": plan}

    def apply_alpha_enhancement(
        self,
        target_weights: dict[str, float],
        trade_date: str,
    ) -> dict[str, Any]:
        if self.portfolio_optimizer is None:
            return {"enabled": False, "reason": "PortfolioOptimizer 不可用"}
        ae = self.config.get("alpha_enhancement", {})
        alpha = float(ae.get("conservative_alpha", 0.05))
        try:
            signals = self.portfolio_optimizer.load_factor_signals(trade_date)
            if not signals:
                return {"enabled": True, "signals_loaded": False, "adjusted_weights": target_weights}
            adjusted = self.portfolio_optimizer.adjust_target_weights(target_weights, signals, alpha=alpha)
            logger.info("【Alpha增强】加载%d个因子信号, 混合权重alpha=%.2f", len(signals), alpha)
            return {
                "enabled": True,
                "signals_loaded": True,
                "signal_count": len(signals),
                "adjusted_weights": adjusted,
            }
        except (ValueError, KeyError, TypeError, AttributeError, OSError) as e:
            logger.warning("Alpha增强失败, 保持原权重: %s", e)
            return {"enabled": True, "error": str(e), "adjusted_weights": target_weights}

    def check_rebalance(
        self,
        positions: dict[str, dict],
        target_weights: dict[str, float],
        prices: dict[str, float],
    ) -> list[dict]:
        rb = self.config.get("rebalance", {})
        threshold = float(rb.get("threshold", 0.06))
        rc = rb.get("risk_control", {})
        max_single = float(rc.get("max_single_weight", 0.20))
        # TODO: max_category_weight 未实现, 待补类别权重上限检查

        total_value = sum(
            float(pos.get("shares", 0)) * prices.get(code, 0.0)
            for code, pos in positions.items()
        )
        if total_value <= 0:
            return []

        orders = []
        for code, pos in positions.items():
            px = prices.get(code, 0.0)
            if px <= 0:
                continue
            shares = float(pos.get("shares", 0))
            current_w = shares * px / total_value
            target_w = target_weights.get(code, 0.0)
            deviation = current_w - target_w
            if abs(deviation) < threshold:
                continue
            target_value = target_w * total_value
            current_value = shares * px
            adj_value = target_value - current_value
            adj_shares = int(adj_value / px / 100) * 100
            if adj_shares == 0:
                continue
            action = "BUY" if adj_shares > 0 else "SELL"
            new_w = (shares + adj_shares) * px / total_value
            if new_w > max_single:
                cap_shares = int(max_single * total_value / px / 100) * 100
                adj_shares = cap_shares - int(shares)
                if adj_shares == 0:
                    continue
            orders.append({
                "code": code,
                "name": pos.get("name", code),
                "action": action,
                "current_weight": round(current_w, 4),
                "target_weight": round(target_w, 4),
                "deviation": round(deviation, 4),
                "adjust_shares": adj_shares,
                "adjust_value": round(adj_shares * px, 2),
                "price": px,
            })
        if orders:
            logger.info("【阈值再平衡】触发%d笔调整 (阈值%.0f%%)", len(orders), threshold * 100)
        return orders

    def run_daily_rebalance(
        self,
        positions: dict[str, dict],
        prices: dict[str, float],
        trade_date: str,
        current_drawdown: float = 0.0,
    ) -> DailyPlan:
        logger.info("=" * 60)
        logger.info("ETF期权对冲再平衡 | 交易日=%s", trade_date)
        logger.info("=" * 60)

        plan = DailyPlan(trade_date=trade_date)

        logger.info("[Phase 1/5] 风险评估 + Regime感知...")
        risk = self.assess_risk(positions, prices, current_drawdown)
        plan.risk_state = risk
        regime_info = self._sense_regime(risk)
        plan.regime = regime_info
        logger.info(
            "  组合市值=%.0f | 波动率=%.1f%% | 回撤=%.2f%% | Regime=%s",
            risk.portfolio_value,
            risk.portfolio_volatility * 100,
            risk.current_drawdown * 100,
            regime_info.get("label", "N/A"),
        )

        logger.info("[Phase 2/5] 回撤熔断检查...")
        dd_decision = self.check_drawdown_circuit(current_drawdown)
        plan.drawdown_decision = dd_decision
        drawdown_level = 0
        if dd_decision:
            level_map = {
                DrawdownLevel.NORMAL: 0,
                DrawdownLevel.WATCH: 1,
                DrawdownLevel.REDUCE: 2,
                DrawdownLevel.FORCE_HEDGE: 3,
                DrawdownLevel.HALT: 4,
            }
            drawdown_level = level_map.get(dd_decision.level, 0)
            if not dd_decision.allow_new_buy:
                plan.warning_flags.append(f"回撤熔断{dd_decision.level.value}: 禁止新建多头")

        target_weights = self.get_target_weights()

        logger.info("[Phase 3/5] ETF资金流 + 信号融合动态权重 + Alpha增强...")
        flow_result = self.apply_etf_flow_adjustment(target_weights)
        plan.etf_flow_adjustment = flow_result
        if flow_result.get("adjusted_plan"):
            ap = flow_result["adjusted_plan"]
            if isinstance(ap, dict) and "target_weights" in ap:
                target_weights = ap["target_weights"]

        fused = self._fuse_signals(target_weights)
        plan.fused_signals = fused
        if fused.get("adjusted_weights"):
            target_weights = fused["adjusted_weights"]

        alpha_result = self.apply_alpha_enhancement(target_weights, trade_date)
        plan.alpha_enhancement = alpha_result
        if alpha_result.get("adjusted_weights"):
            target_weights = alpha_result["adjusted_weights"]

        logger.info("[Phase 4/5] 期权对冲决策 (Regime自适应)...")
        option_hedge = self.decide_option_hedge(drawdown_level=drawdown_level)
        if regime_info and regime_info.get("hedge_ratio"):
            option_hedge["regime_hedge_ratio"] = regime_info["hedge_ratio"]
            option_hedge["regime_label"] = regime_info["label"]
        plan.option_hedge = option_hedge

        logger.info("[Phase 5/5] 阈值再平衡 + 生成执行计划...")
        if dd_decision and not dd_decision.allow_new_buy:
            plan.rebalance_orders = []
            plan.execution_summary = f"回撤熔断{dd_decision.level.value}触发, 跳过再平衡"
            logger.warning("【再平衡跳过】回撤熔断触发, 仅保留对冲操作")
        else:
            rebalance_orders = self.check_rebalance(positions, target_weights, prices)
            plan.rebalance_orders = rebalance_orders
            buy_amt = sum(o["adjust_value"] for o in rebalance_orders if o["action"] == "BUY")
            sell_amt = sum(abs(o["adjust_value"]) for o in rebalance_orders if o["action"] == "SELL")
            plan.execution_summary = (
                f"再平衡{len(rebalance_orders)}笔 | 买入{buy_amt:.0f} | 卖出{sell_amt:.0f} | "
                f"期权对冲{'启用' if option_hedge.get('enabled') else '禁用'} | "
                f"Regime={regime_info.get('label', 'N/A')}"
            )

        logger.info("[Phase 6/6] 自我进化闭环 (漂移检测+决策记忆+进化编排)...")
        if self.drift_monitor:
            try:
                self.drift_monitor.update_ic(trade_date, risk.portfolio_volatility)
                plan.drift_status = self.drift_monitor.get_status()
            except (ValueError, TypeError, OSError) as e:
                logger.warning("漂移检测失败: %s", e)

        self._record_decision(plan)
        if self.memory_reflection:
            try:
                plan.reflection_context = self.memory_reflection.get_reflection_context(days=30)
            except (ValueError, TypeError, OSError) as e:
                logger.warning("反思上下文获取失败: %s", e)

        plan.evolution_action = self._run_evolution_cycle()

        plan.estimated_annual_return = self.target_annual_return
        plan.estimated_max_drawdown = self.target_max_drawdown

        logger.info("=" * 60)
        logger.info("日度再平衡完成 | %s", plan.execution_summary)
        logger.info("=" * 60)
        return plan

    def run_stress_tests(
        self,
        positions: dict[str, dict],
        prices: dict[str, float],
    ) -> dict[str, Any]:
        scenarios = self.config.get("risk_control", {}).get("stress_test_scenarios", [])
        scenario_shocks = {
            "2015股灾": -0.45,
            "2016熔断": -0.25,
            "2018贸易战": -0.32,
            "2020疫情闪崩": -0.16,
            "2022俄乌冲突": -0.18,
            "2024地产危机": -0.20,
        }
        total_value = sum(
            float(pos.get("shares", 0)) * prices.get(code, 0.0)
            for code, pos in positions.items()
        )
        oh = self.config.get("options_hedge", {})
        otm_pct = float(oh.get("otm_pct", 0.05))
        targets = oh.get("protection_targets", [])
        hedge_coverage = sum(
            float(t.get("contracts", 0)) * 10000 * prices.get(t["code"] + ".SH", prices.get(t["code"] + ".SZ", 4.0))
            for t in targets
        ) / total_value if total_value > 0 and targets else 0.0
        hedge_coverage = min(hedge_coverage, 0.6)
        annual_cost = float(oh.get("max_annual_cost_pct", 0.025))

        results = {}
        breach_count = 0
        breach_count_hedged = 0
        for scenario in scenarios:
            shock = scenario_shocks.get(scenario, -0.20)
            loss = total_value * shock
            dd_pct = abs(shock)
            breaches = dd_pct > self.target_max_drawdown
            if breaches:
                breach_count += 1
            excess_dd = max(0.0, dd_pct - otm_pct)
            hedge_benefit = excess_dd * hedge_coverage
            dd_hedged = max(0.0, dd_pct - hedge_benefit + annual_cost)
            breaches_hedged = dd_hedged > self.target_max_drawdown
            if breaches_hedged:
                breach_count_hedged += 1
            results[scenario] = {
                "market_shock": shock,
                "estimated_loss": round(loss, 2),
                "drawdown_pct": round(dd_pct * 100, 1),
                "drawdown_hedged_pct": round(dd_hedged * 100, 1),
                "hedge_coverage": round(hedge_coverage, 3),
                "breaches_limit": breaches,
                "breaches_limit_hedged": breaches_hedged,
                "action": "启动尾部对冲+减仓" if breaches_hedged else "期权对冲已覆盖",
            }
        logger.info(
            "【压力测试】%d场景完成 | 裸敞口%d场景突破15%% | 对冲后%d场景突破15%%",
            len(scenarios), breach_count, breach_count_hedged,
        )
        return {
            "scenarios": results,
            "breach_count": breach_count,
            "breach_count_hedged": breach_count_hedged,
            "total_scenarios": len(scenarios),
            "all_safe": breach_count_hedged == 0,
            "hedge_coverage": round(hedge_coverage, 3),
        }

    def get_protection_status(self) -> dict[str, Any]:
        if self.put_engine is None:
            return {"enabled": False}
        return self.put_engine.get_protection_status()


def run_etf_option_hedge_rebalance(
    config_path: str | None = None,
    trade_date: str | None = None,
) -> tuple[DailyPlan, ETFOptionHedgeRebalancer]:
    rebalancer = ETFOptionHedgeRebalancer(config_path=config_path)
    target_weights = rebalancer.get_target_weights()
    positions = {}
    for code, w in target_weights.items():
        positions[code] = {
            "shares": int(w * rebalancer.portfolio_value / 4.0 / 100) * 100,
            "name": code,
            "category": "宽基",
        }
    prices = {code: 4.0 for code in target_weights}
    td = trade_date or datetime.now().strftime("%Y-%m-%d")
    plan = rebalancer.run_daily_rebalance(positions, prices, td)
    return plan, rebalancer


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    plan, rebalancer = run_etf_option_hedge_rebalance()
    print(f"\n执行摘要: {plan.execution_summary}")  # noqa: T201
    print(f"警告: {plan.warning_flags}")  # noqa: T201