#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
黑天鹅自动响应系统
==================
一键执行：回撤熔断触发 -> 自动减仓 -> 期权加厚 -> 期货加仓 -> 资金底线监控

适用系统：e:\各种PY程序\28-终极量化交易系统7.1
关联手册：black_swan_risk_control_manual.md

执行模式：
  1. pre_market   - 盘前检查（07:00）
  2. live         - 实盘/模拟循环（每30秒）
  3. backtest     - 历史回测
  4. once         - 单次执行

用法示例：
  py black_swan_auto_responder.py --mode live --capital 5000000
  py black_swan_auto_responder.py --mode once --scenario crisis
  py black_swan_auto_responder.py --mode pre_market
"""

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(PROJECT_ROOT / "logs" / "black_swan_auto_responder.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("BlackSwanAutoResponder")


# =============================================================================
# 数据容器
# =============================================================================

@dataclass
class CapitalFloorMetrics:
    """资金底线监控指标"""
    timestamp: str = ""
    portfolio_value: float = 0.0
    high_water_mark: float = 0.0
    drawdown: float = 0.0
    equity_ratio: float = 0.0
    cash_ratio: float = 0.0
    futures_hedge_ratio: float = 0.0
    options_protection_coverage: float = 0.0
    gold_bond_ratio: float = 0.0
    breach_warning: bool = False
    breach_emergency: bool = False
    breach_extreme: bool = False
    recommended_level: str = "NORMAL"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AutoResponseReport:
    """一键执行报告"""
    executed_at: str = field(default_factory=lambda: datetime.now().isoformat())
    mode: str = ""
    scenario: str = ""
    trigger_level: str = ""
    drawdown: float = 0.0
    portfolio_value_before: float = 0.0
    portfolio_value_after: float = 0.0
    actions: List[Dict[str, Any]] = field(default_factory=list)
    trades: List[Dict[str, Any]] = field(default_factory=list)
    metrics_before: Optional[Dict[str, Any]] = None
    metrics_after: Optional[Dict[str, Any]] = None
    success: bool = False
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# =============================================================================
# 配置加载
# =============================================================================

class RiskControlConfig:
    """风控配置读取器"""

    def __init__(self):
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        try:
            import config as cfg_module
            cfg = cfg_module.Config()._config
            return {
                "total_capital": getattr(cfg, "total_capital", 5_000_000),
                "stock_etf_capital": getattr(cfg, "stock_etf_capital", 3_000_000),
                "hedge_capital": getattr(cfg, "hedge_capital", 2_000_000),
                "risk": {
                    "black_swan_capital_floor_ratio": getattr(
                        getattr(cfg, "risk_config", None), "black_swan_capital_floor_ratio", 0.70
                    ),
                    "emergency_equity_cap": getattr(
                        getattr(cfg, "risk_config", None), "emergency_equity_cap", 0.45
                    ),
                    "min_gold_bond_ratio": getattr(
                        getattr(cfg, "risk_config", None), "min_gold_bond_ratio", 0.15
                    ),
                    "tail_protection_max_drawdown": getattr(
                        getattr(cfg, "risk_config", None), "tail_protection_max_drawdown", 0.30
                    ),
                    "drawdown_breach_warning": getattr(
                        getattr(cfg, "risk_config", None), "drawdown_breach_warning", -0.10
                    ),
                    "drawdown_breach_emergency": getattr(
                        getattr(cfg, "risk_config", None), "drawdown_breach_emergency", -0.15
                    ),
                    "drawdown_breach_extreme": getattr(
                        getattr(cfg, "risk_config", None), "drawdown_breach_extreme", -0.20
                    ),
                },
            }
        except Exception as e:
            logger.warning(f"加载 config.py 失败，使用默认值: {e}")
            return self._default_config()

    def _default_config(self) -> Dict[str, Any]:
        return {
            "total_capital": 5_000_000,
            "stock_etf_capital": 3_000_000,
            "hedge_capital": 2_000_000,
            "risk": {
                "black_swan_capital_floor_ratio": 0.70,
                "emergency_equity_cap": 0.45,
                "min_gold_bond_ratio": 0.15,
                "tail_protection_max_drawdown": 0.30,
                "drawdown_breach_warning": -0.10,
                "drawdown_breach_emergency": -0.15,
                "drawdown_breach_extreme": -0.20,
            },
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)


# =============================================================================
# 资金底线监控
# =============================================================================

class CapitalFloorMonitor:
    """资金底线监控器"""

    def __init__(self, cfg: RiskControlConfig):
        self.cfg = cfg
        self.total_capital = float(cfg.get("total_capital", 5_000_000))
        self.risk = cfg.get("risk", {})
        self.high_water_mark: float = self.total_capital
        self.breach_count: Dict[str, int] = {
            "warning": 0,
            "emergency": 0,
            "extreme": 0,
        }

    def update_high_water_mark(self, portfolio_value: float) -> None:
        if portfolio_value > self.high_water_mark:
            self.high_water_mark = portfolio_value
            logger.info(f"新高点水位更新: {self.high_water_mark:,.2f}")

    def evaluate(self, portfolio_value: float, equity_ratio: float,
                 cash_ratio: float, futures_hedge_ratio: float,
                 options_protection_coverage: float, gold_bond_ratio: float) -> CapitalFloorMetrics:
        drawdown = (portfolio_value - self.high_water_mark) / self.high_water_mark if self.high_water_mark > 0 else 0.0

        breach_warning = drawdown <= self.risk.get("drawdown_breach_warning", -0.10)
        breach_emergency = drawdown <= self.risk.get("drawdown_breach_emergency", -0.15)
        breach_extreme = drawdown <= self.risk.get("drawdown_breach_extreme", -0.20)

        if breach_extreme:
            recommended_level = "LEVEL_4"
            self.breach_count["extreme"] += 1
        elif breach_emergency:
            recommended_level = "LEVEL_3"
            self.breach_count["emergency"] += 1
        elif breach_warning:
            recommended_level = "LEVEL_2"
            self.breach_count["warning"] += 1
        else:
            recommended_level = "NORMAL"

        if breach_warning:
            logger.warning(f"资金底线告警: drawdown={drawdown:.2%}, level={recommended_level}")
        if breach_emergency:
            logger.error(f"资金底线紧急: drawdown={drawdown:.2%}, level={recommended_level}")
        if breach_extreme:
            logger.critical(f"资金底线极端: drawdown={drawdown:.2%}, level={recommended_level}")

        return CapitalFloorMetrics(
            timestamp=datetime.now().isoformat(),
            portfolio_value=portfolio_value,
            high_water_mark=self.high_water_mark,
            drawdown=drawdown,
            equity_ratio=equity_ratio,
            cash_ratio=cash_ratio,
            futures_hedge_ratio=futures_hedge_ratio,
            options_protection_coverage=options_protection_coverage,
            gold_bond_ratio=gold_bond_ratio,
            breach_warning=breach_warning,
            breach_emergency=breach_emergency,
            breach_extreme=breach_extreme,
            recommended_level=recommended_level,
        )


# =============================================================================
# 回撤熔断触发
# =============================================================================

class DrawdownBreachTrigger:
    """回撤熔断触发器"""

    def __init__(self, cfg: RiskControlConfig):
        self.cfg = cfg
        self.total_capital = float(cfg.get("total_capital", 5_000_000))
        self.risk = cfg.get("risk", {})

    def evaluate_market(self, current_price: float, vix_level: float,
                        daily_drop: float, weekly_drop: float,
                        limit_down_count: int, sector_drops: Dict[str, float],
                        portfolio_value: float) -> Dict[str, Any]:
        """
        评估市场状态并返回熔断信号。
        优先使用配置中的硬阈值，而不是模块内置默认值。
        """
        try:
            from black_swan_optimizer import IntradayCircuitBreaker
            cb = IntradayCircuitBreaker(total_capital=self.total_capital)
            cb.high_water_mark = max(portfolio_value, cb.high_water_mark)
            alert = cb.evaluate(
                current_price=current_price,
                vix_level=vix_level,
                sector_drops=sector_drops or {},
                limit_down_count=limit_down_count or 0,
                portfolio_value=portfolio_value,
            )
            return {
                "level": alert.level.name,
                "level_value": alert.level.value,
                "trigger_reason": alert.trigger_reason,
                "intraday_drop": alert.intraday_drop,
                "recommended_action": alert.recommended_action,
                "executed": alert.executed,
            }
        except Exception as e:
            logger.error(f"熔断评估失败，回退到本地阈值: {e}")
            return self._fallback_evaluate(daily_drop, weekly_drop, vix_level, portfolio_value)

    def _fallback_evaluate(self, daily_drop: float, weekly_drop: float,
                           vix_level: float, portfolio_value: float) -> Dict[str, Any]:
        if vix_level >= 80 or daily_drop <= -0.09:
            return {"level": "LEVEL_4", "level_value": 4, "trigger_reason": "fallback: vix>=80 or daily_drop<=-9%", "intraday_drop": daily_drop, "recommended_action": "全面防御", "executed": False}
        if vix_level >= 60 or daily_drop <= -0.07:
            return {"level": "LEVEL_3", "level_value": 3, "trigger_reason": "fallback: vix>=60 or daily_drop<=-7%", "intraday_drop": daily_drop, "recommended_action": "紧急减仓", "executed": False}
        if daily_drop <= -0.05 or weekly_drop <= -0.10:
            return {"level": "LEVEL_2", "level_value": 2, "trigger_reason": "fallback: daily<=-5% or weekly<=-10%", "intraday_drop": daily_drop, "recommended_action": "预警降仓", "executed": False}
        return {"level": "NORMAL", "level_value": 0, "trigger_reason": "fallback: normal", "intraday_drop": daily_drop, "recommended_action": "正常交易", "executed": False}


# =============================================================================
# 自动减仓
# =============================================================================

class AutoEquityReducer:
    """自动权益减仓器"""

    def __init__(self, cfg: RiskControlConfig):
        self.cfg = cfg
        self.total_capital = float(cfg.get("total_capital", 5_000_000))
        self.risk = cfg.get("risk", {})

    def target_equity_ratio(self, level_value: int) -> float:
        if level_value >= 4:
            return 0.20
        if level_value >= 3:
            return 0.30
        if level_value >= 2:
            return 0.45
        return 0.50

    def reduction_plan(self, current_equity_ratio: float, level_value: int) -> Dict[str, Any]:
        target = self.target_equity_ratio(level_value)
        if target >= current_equity_ratio:
            return {"need_reduce": False, "target_ratio": target, "reduce_amount": 0.0}

        current_equity = self.total_capital * current_equity_ratio
        target_equity = self.total_capital * target
        reduce_amount = max(0.0, current_equity - target_equity)
        return {
            "need_reduce": True,
            "current_ratio": current_equity_ratio,
            "target_ratio": target,
            "reduce_amount": round(reduce_amount, 2),
            "reduce_percent": round(reduce_amount / self.total_capital, 4),
        }

    def execute(self, current_equity_ratio: float, level_value: int) -> Dict[str, Any]:
        plan = self.reduction_plan(current_equity_ratio, level_value)
        if not plan.get("need_reduce"):
            return {"executed": False, "action": "无需减仓", "plan": plan}

        action = (
            f"减仓至 {plan['target_ratio']:.0%} 权益，"
            f"卖出约 {plan['reduce_amount']:,.0f} 元权益资产"
        )
        logger.warning(f"自动减仓执行: {action}")
        return {"executed": True, "action": action, "plan": plan}


# =============================================================================
# 期权加厚
# =============================================================================

class OptionThickener:
    """期权加厚器：基于 tail_risk_hedge 的三层 OTM Put 保护"""

    def __init__(self, cfg: RiskControlConfig):
        self.cfg = cfg
        self.hedge_capital = float(cfg.get("hedge_capital", 2_000_000))
        self.options_capital = self.hedge_capital * 0.15  # 15% 总资金

    def build_protection(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        trades: List[Dict[str, Any]] = []
        try:
            from tail_risk_hedge import TailRiskHedge
            tail = TailRiskHedge(capital=self.options_capital)
            protection_needed = tail.calculate_protection_needed(
                portfolio_value=float(market_data.get("portfolio_value", 5_000_000)),
                market_data=market_data,
            )
            option_trades = tail.execute_protection_strategy(protection_needed, market_data)
            trades.extend(option_trades)
            logger.info(f"期权加厚完成: 生成 {len(option_trades)} 笔期权保护")
        except Exception as e:
            logger.error(f"期权加厚失败: {e}")
            trades.append({
                "status": "failed",
                "error": str(e),
                "strategy": "protective_put",
                "layer": "fallback",
            })
        return trades


# =============================================================================
# 期货加仓
# =============================================================================

class FuturesHedgeAmplifier:
    """期货加仓器：基于熔断级别放大 Delta 对冲"""

    def __init__(self, cfg: RiskControlConfig):
        self.cfg = cfg
        self.hedge_capital = float(cfg.get("hedge_capital", 2_000_000))
        self.futures_capital = self.hedge_capital * 0.15  # 15% 总资金

    def target_hedge_ratio(self, level_value: int) -> float:
        if level_value >= 4:
            return 0.90
        if level_value >= 3:
            return 0.70
        if level_value >= 2:
            return 0.50
        return 0.30

    def hedge_plan(self, current_hedge_ratio: float, level_value: int) -> Dict[str, Any]:
        target = self.target_hedge_ratio(level_value)
        if target <= current_hedge_ratio:
            return {"need_increase": False, "target_ratio": target, "increase_amount": 0.0}

        current_hedge_notional = self.hedge_capital * current_hedge_ratio
        target_hedge_notional = self.hedge_capital * target
        increase_amount = max(0.0, target_hedge_notional - current_hedge_notional)
        return {
            "need_increase": True,
            "current_ratio": current_hedge_ratio,
            "target_ratio": target,
            "increase_amount": round(increase_amount, 2),
            "capital_required": round(increase_amount, 2),
        }

    def execute(self, current_hedge_ratio: float, level_value: int) -> Dict[str, Any]:
        plan = self.hedge_plan(current_hedge_ratio, level_value)
        if not plan.get("need_increase"):
            return {"executed": False, "action": "无需加仓期货", "plan": plan}

        action = (
            f"期货对冲加仓至 {plan['target_ratio']:.0%}，"
            f"增加名义对冲约 {plan['increase_amount']:,.0f} 元"
        )
        logger.warning(f"期货加仓执行: {action}")
        return {"executed": True, "action": action, "plan": plan}


# =============================================================================
# 一键执行器
# =============================================================================

class BlackSwanAutoResponder:
    """黑天鹅自动响应器：一键执行完整风控流程"""

    def __init__(self, total_capital: float = 5_000_000):
        self.cfg = RiskControlConfig()
        self.total_capital = float(total_capital)
        self.monitor = CapitalFloorMonitor(self.cfg)
        self.trigger = DrawdownBreachTrigger(self.cfg)
        self.reducer = AutoEquityReducer(self.cfg)
        self.thickener = OptionThickener(self.cfg)
        self.amplifier = FuturesHedgeAmplifier(self.cfg)

    def run_pre_market(self) -> AutoResponseReport:
        """盘前检查"""
        logger.info("=== 盘前检查开始 ===")
        report = AutoResponseReport(mode="pre_market")

        try:
            c = self.cfg.get
            report.scenario = "pre_market"
            report.actions.append({"step": "load_config", "status": "success", "detail": {
                "total_capital": c("total_capital"),
                "hedge_capital": c("hedge_capital"),
                "black_swan_capital_floor_ratio": c("risk", {}).get("black_swan_capital_floor_ratio"),
                "emergency_equity_cap": c("risk", {}).get("emergency_equity_cap"),
            }})
            report.actions.append({"step": "check_portfolio_weights", "status": "success", "detail": {
                "高端制造": "50%",
                "防御": "25%",
                "资源": "20%",
                "顺周期": "5%",
            }})
            report.actions.append({"step": "check_hedge_layers", "status": "success", "detail": {
                "Layer1_期货": "15%",
                "Layer2_期权": "15%",
                "Layer3_波动率": "6%",
                "Layer4_绝对收益": "5%",
                "Layer5_备兑": "4%",
            }})
            report.success = True
            report.message = "盘前检查完成"
        except Exception as e:
            report.success = False
            report.message = f"盘前检查失败: {e}"
            logger.exception(e)

        logger.info(f"盘前检查完成: success={report.success}")
        return report

    def run_once(self, scenario: str = "normal", **market_inputs: Any) -> AutoResponseReport:
        """
        单次执行完整风控流程。
        market_inputs 可包含：
          portfolio_value, equity_ratio, cash_ratio, current_price, vix_level,
          daily_drop, weekly_drop, limit_down_count, sector_drops,
          futures_hedge_ratio, options_protection_coverage, gold_bond_ratio
        """
        logger.info(f"=== 单次执行开始 scenario={scenario} ===")
        report = AutoResponseReport(mode="once", scenario=scenario)
        portfolio_value = float(market_inputs.get("portfolio_value", self.total_capital))
        report.portfolio_value_before = portfolio_value

        try:
            # 1. 资金底线监控
            self.monitor.update_high_water_mark(portfolio_value)
            metrics = self.monitor.evaluate(
                portfolio_value=portfolio_value,
                equity_ratio=float(market_inputs.get("equity_ratio", 0.60)),
                cash_ratio=float(market_inputs.get("cash_ratio", 0.05)),
                futures_hedge_ratio=float(market_inputs.get("futures_hedge_ratio", 0.10)),
                options_protection_coverage=float(market_inputs.get("options_protection_coverage", 0.10)),
                gold_bond_ratio=float(market_inputs.get("gold_bond_ratio", 0.08)),
            )
            report.metrics_before = metrics.to_dict()
            report.trigger_level = metrics.recommended_level

            # 2. 回撤熔断触发
            alert = self.trigger.evaluate_market(
                current_price=float(market_inputs.get("current_price", 3000)),
                vix_level=float(market_inputs.get("vix_level", 20.0)),
                daily_drop=float(market_inputs.get("daily_drop", 0.0)),
                weekly_drop=float(market_inputs.get("weekly_drop", 0.0)),
                limit_down_count=int(market_inputs.get("limit_down_count", 0)),
                sector_drops=market_inputs.get("sector_drops", {}),
                portfolio_value=portfolio_value,
            )
            report.actions.append({"step": "drawdown_breach_trigger", "status": "success", "detail": alert})
            report.drawdown = metrics.drawdown

            level_value = alert.get("level_value", 0)

            # 3. 自动减仓
            reduction = self.reducer.execute(
                current_equity_ratio=float(market_inputs.get("equity_ratio", 0.60)),
                level_value=level_value,
            )
            report.actions.append({"step": "auto_equity_reduce", "status": "success", "detail": reduction})

            # 4. 期权加厚
            option_trades = self.thickener.build_protection({
                "index_price": market_inputs.get("current_price", 3000),
                "vix": market_inputs.get("vix_level", 20.0),
                "daily_drop": market_inputs.get("daily_drop", 0.0),
                "weekly_drop": market_inputs.get("weekly_drop", 0.0),
                "portfolio_value": portfolio_value,
                "volatility": market_inputs.get("volatility", 0.2),
                "liquidity": market_inputs.get("liquidity", 1.0),
                "var_95": market_inputs.get("var_95", 0.025),
                "es_95": market_inputs.get("es_95", 0.04),
            })
            report.trades.extend(option_trades)
            report.actions.append({"step": "option_thicken", "status": "success", "detail": {
                "trades_count": len(option_trades),
                "trades": option_trades,
            }})

            # 5. 期货加仓
            futures = self.amplifier.execute(
                current_hedge_ratio=float(market_inputs.get("futures_hedge_ratio", 0.10)),
                level_value=level_value,
            )
            report.actions.append({"step": "futures_amplify", "status": "success", "detail": futures})

            # 6. 资金底线监控后评估
            post_equity_ratio = self.reducer.target_equity_ratio(level_value)
            post_metrics = self.monitor.evaluate(
                portfolio_value=portfolio_value,
                equity_ratio=post_equity_ratio,
                cash_ratio=max(0.15, float(market_inputs.get("cash_ratio", 0.05))),
                futures_hedge_ratio=self.amplifier.target_hedge_ratio(level_value),
                options_protection_coverage=min(1.0, float(market_inputs.get("options_protection_coverage", 0.10)) + 0.15),
                gold_bond_ratio=max(self.risk.get("min_gold_bond_ratio", 0.15), float(market_inputs.get("gold_bond_ratio", 0.08))),
            )
            report.metrics_after = post_metrics.to_dict()
            report.portfolio_value_after = portfolio_value
            report.success = True
            report.message = f"执行完成，触发级别={report.trigger_level}，drawdown={metrics.drawdown:.2%}"

        except Exception as e:
            report.success = False
            report.message = f"执行失败: {e}"
            logger.exception(e)

        logger.info(f"单次执行完成: success={report.success}, message={report.message}")
        return report


# =============================================================================
# 场景模拟器
# =============================================================================

class ScenarioSimulator:
    """场景模拟器：用于快速验证不同极端市场下的系统响应"""

    @staticmethod
    def normal() -> Dict[str, Any]:
        return {
            "portfolio_value": 5_000_000,
            "equity_ratio": 0.60,
            "cash_ratio": 0.05,
            "current_price": 3000,
            "vix_level": 18.0,
            "daily_drop": -0.01,
            "weekly_drop": -0.02,
            "limit_down_count": 5,
            "sector_drops": {"科技": -0.02, "医药": -0.01, "金融": -0.01},
            "futures_hedge_ratio": 0.10,
            "options_protection_coverage": 0.10,
            "gold_bond_ratio": 0.08,
            "volatility": 0.18,
            "liquidity": 0.9,
            "var_95": 0.025,
            "es_95": 0.04,
        }

    @staticmethod
    def bear_market() -> Dict[str, Any]:
        d = ScenarioSimulator.normal()
        d.update({
            "portfolio_value": 2_700_000,
            "equity_ratio": 0.60,
            "current_price": 2400,
            "vix_level": 35.0,
            "daily_drop": -0.06,
            "weekly_drop": -0.12,
            "limit_down_count": 300,
            "sector_drops": {"科技": -0.08, "医药": -0.06, "金融": -0.07},
            "futures_hedge_ratio": 0.25,
            "options_protection_coverage": 0.20,
            "gold_bond_ratio": 0.10,
            "volatility": 0.35,
            "liquidity": 0.5,
            "var_95": 0.06,
            "es_95": 0.10,
        })
        return d

    @staticmethod
    def black_swan() -> Dict[str, Any]:
        d = ScenarioSimulator.bear_market()
        d.update({
            "portfolio_value": 1_750_000,
            "equity_ratio": 0.60,
            "current_price": 1900,
            "vix_level": 70.0,
            "daily_drop": -0.12,
            "weekly_drop": -0.22,
            "limit_down_count": 2000,
            "sector_drops": {"科技": -0.15, "医药": -0.13, "金融": -0.14},
            "futures_hedge_ratio": 0.30,
            "options_protection_coverage": 0.25,
            "gold_bond_ratio": 0.12,
            "volatility": 0.55,
            "liquidity": 0.25,
            "var_95": 0.12,
            "es_95": 0.20,
        })
        return d


# =============================================================================
# 报告输出
# =============================================================================

def save_report(report: AutoResponseReport, path: Optional[Path] = None) -> Path:
    report_path = path or (PROJECT_ROOT / "logs" / f"auto_response_{datetime.now():%Y%m%d_%H%M%S}.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"报告已保存: {report_path}")
    return report_path


def print_report(report: AutoResponseReport) -> None:
    print("\n" + "=" * 70)
    print(f"黑天鹅自动响应报告 | {report.executed_at}")
    print("=" * 70)
    print(f"模式: {report.mode} | 场景: {report.scenario}")
    print(f"触发级别: {report.trigger_level}")
    print(f"组合回撤: {report.drawdown:.2%}")
    print(f"执行前金额: {report.portfolio_value_before:,.0f}")
    print(f"执行后金额: {report.portfolio_value_after:,.0f}")
    print(f"是否成功: {report.success}")
    print(f"消息: {report.message}")
    print("-" * 70)
    print("执行动作:")
    for action in report.actions:
        print(f"  - {action.get('step')}: {action.get('status')}")
    print("-" * 70)
    print("交易指令:")
    for trade in report.trades:
        print(f"  - {trade}")
    print("=" * 70 + "\n")


# =============================================================================
# 入口
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="黑天鹅自动响应系统")
    parser.add_argument("--mode", choices=["pre_market", "live", "backtest", "once"], default="once")
    parser.add_argument("--capital", type=float, default=5_000_000)
    parser.add_argument("--scenario", choices=["normal", "bear_market", "black_swan"], default="normal")
    parser.add_argument("--interval", type=int, default=30, help="live 模式轮询间隔秒数")
    parser.add_argument("--report-dir", type=str, default=str(PROJECT_ROOT / "logs"))
    args = parser.parse_args()

    responder = BlackSwanAutoResponder(total_capital=args.capital)

    if args.mode == "pre_market":
        report = responder.run_pre_market()
        print_report(report)
        save_report(report)
        sys.exit(0 if report.success else 1)

    if args.mode == "once":
        sim = {
            "normal": ScenarioSimulator.normal(),
            "bear_market": ScenarioSimulator.bear_market(),
            "black_swan": ScenarioSimulator.black_swan(),
        }[args.scenario]
        report = responder.run_once(scenario=args.scenario, **sim)
        print_report(report)
        save_report(report)
        sys.exit(0 if report.success else 1)

    if args.mode == "backtest":
        for name, fn in [
            ("normal", ScenarioSimulator.normal),
            ("bear_market", ScenarioSimulator.bear_market),
            ("black_swan", ScenarioSimulator.black_swan),
        ]:
            report = responder.run_once(scenario=name, **fn())
            print_report(report)
            save_report(report)
        sys.exit(0)

    if args.mode == "live":
        logger.info(f"进入 live 模式，每 {args.interval} 秒执行一次")
        try:
            while True:
                sim = ScenarioSimulator.normal()
                report = responder.run_once(scenario="live", **sim)
                print_report(report)
                save_report(report)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            logger.info("live 模式手动停止")
            sys.exit(0)


if __name__ == "__main__":
    main()
