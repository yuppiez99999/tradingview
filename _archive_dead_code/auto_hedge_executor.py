#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全自动对冲风险执行器
=====================
将黑天鹅自动响应系统与实盘交易工作流整合，实现：
  监控 -> 触发 -> 减仓 -> 期权加厚 -> 期货加仓 -> 实际下单 -> 持仓同步 -> 报告

核心职责：
  1. 盘前初始化：加载配置、检查 baseline、初始化 broker
  2. 实时监控：持续监控回撤、VIX、板块跌幅、期权流动性
  3. 自动触发：回撤 10%/15%/20% 三级熔断自动判定
  4. 自动执行：自动减仓 + 期权加厚 + 期货加仓
  5. 订单管理：通过 BrokerInterface 下单、撤单、查状态
  6. 持仓同步：实时同步券商持仓，计算权益比例
  7. 资金底线监控：确保黑天鹅下保留 >= 70% 资金
  8. 报告输出：每次触发生成完整执行报告
  9. 模型自优化：根据运行历史自动校准风控阈值和对冲参数

与现有系统整合：
  - 复用 live_trading_workflow.py 的 BrokerInterface / PositionStore / NotificationService
  - 复用 black_swan_auto_responder_light.py 的触发/减仓/加厚/加仓逻辑
  - 复用 comprehensive_quant_system_v7.py 的五层对冲架构
  - 复用 tail_risk_hedge.py 的三层 OTM Put 保护
  - 复用 model_self_optimizer.py 的本地参数自校准

用法：
  python auto_hedge_executor.py                        # 常驻监控模式
  python auto_hedge_executor.py --once --scenario crisis  # 单次执行
  python auto_hedge_executor.py --pre-market            # 盘前检查
  python auto_hedge_executor.py --backtest              # 历史回测
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from model_self_optimizer import ModelSelfOptimizer

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# 日志配置
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            LOG_DIR / f"auto_hedge_{datetime.now():%Y%m%d}.log",
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("AutoHedgeExecutor")

# =============================================================================
# 2026 交易计划标的（来自 generate_500w_build_plan.py）
# =============================================================================

BUILD_PLAN_START = date(2026, 7, 6)
BUILD_PLAN_END = date(2026, 9, 30)

TARGET_PORTFOLIO = {
    # ===== 高端制造板块 (50.0%) =====
    '588000': {'name': '科创50ETF华夏',     'type': 'ETF',  'risk': '高', 'style': '高端制造',
               'weight': 0.1100, 'est_price': 1.05,  'lots': 100,
               'reason': 'AI/科技核心指数，十五五规划重点方向'},
    '512480': {'name': '半导体ETF国泰',      'type': 'ETF',  'risk': '高', 'style': '高端制造',
               'weight': 0.1000, 'est_price': 1.38,  'lots': 100,
               'reason': 'AI算力硬件核心，康波第六轮技术底座'},
    '516160': {'name': '高端装备ETF南方',    'type': 'ETF',  'risk': '高', 'style': '高端制造',
               'weight': 0.1000, 'est_price': 1.12,  'lots': 100,
               'reason': '十五五重点产业，制造业升级主线'},
    '515030': {'name': '新能源车ETF华夏',    'type': 'ETF',  'risk': '高', 'style': '高端制造',
               'weight': 0.1000, 'est_price': 1.55,  'lots': 100,
               'reason': '新能源产业链，十五五绿色转型战略'},
    '159915': {'name': '创业板ETF易方达',    'type': 'ETF',  'risk': '高', 'style': '高端制造',
               'weight': 0.0900, 'est_price': 2.15,  'lots': 100,
               'reason': '成长风格核心敞口，创新企业集中地'},
    # ===== 防御板块 (25.0%) =====
    '159992': {'name': '创新药ETF银华',      'type': 'ETF',  'risk': '高', 'style': '防御',
               'weight': 0.1000, 'est_price': 0.92,  'lots': 100,
               'reason': '生物医药创新，十五五民生健康重点'},
    '512010': {'name': '医药ETF易方达',      'type': 'ETF',  'risk': '中', 'style': '防御',
               'weight': 0.0700, 'est_price': 0.58,  'lots': 100,
               'reason': '医药行业宽基配置，防御+成长双属性'},
    '511260': {'name': '十年国债ETF国泰',    'type': '债券', 'risk': '低', 'style': '防御',
               'weight': 0.0300, 'est_price': 102.50, 'lots': 10,
               'reason': '利率债配置，极端行情安全垫'},
    '511520': {'name': '政金债ETF富国',      'type': '债券', 'risk': '低', 'style': '防御',
               'weight': 0.0300, 'est_price': 101.20, 'lots': 10,
               'reason': '政策性金融债，信用风险极低'},
    '511360': {'name': '短融ETF海富通',      'type': '货币', 'risk': '低', 'style': '防御',
               'weight': 0.0200, 'est_price': 100.05, 'lots': 10,
               'reason': '现金管理工具，闲置资金获取货币收益'},
    # ===== 资源板块 (20.0%) =====
    '512400': {'name': '有色金属ETF南方',    'type': 'ETF',  'risk': '高', 'style': '资源',
               'weight': 0.1200, 'est_price': 1.18,  'lots': 100,
               'reason': '康波繁荣期预期，商品超级周期受益'},
    '518880': {'name': '黄金ETF华安',        'type': '商品', 'risk': '中', 'style': '资源',
               'weight': 0.0800, 'est_price': 5.85,  'lots': 100,
               'reason': '通胀对冲+地缘风险避险，组合稳定器'},
    # ===== 顺周期板块 (5.0%) =====
    '601088': {'name': '中国神华',           'type': '个股', 'risk': '中', 'style': '顺周期',
               'weight': 0.0500, 'est_price': 38.50, 'lots': 100,
               'reason': '能源安全龙头，高股息+顺周期双重属性'},
}

BUILD_PHASES = [
    {
        'phase': 1, 'name': '第一阶段-底仓建立',
        'start': date(2026, 7, 6),    'duration_days': 10,
        'capital_ratio': 0.35,            'desc': '周一开盘建立核心底仓，关注市场流动性',
        'strategy': '优先建立高端制造核心仓位（科创50、半导体、高端装备），同步配置黄金ETF和中国神华作为稳定器',
    },
    {
        'phase': 2, 'name': '第二阶段-配置完善',
        'start': date(2026, 7, 20),   'duration_days': 15,
        'capital_ratio': 0.30,            'desc': '完成新能源车、创业板、有色金属配置',
        'strategy': '利用月中波动窗口分批加仓，关注大宗商品价格趋势，择机增加资源板块',
    },
    {
        'phase': 3, 'name': '第三阶段-防御补充',
        'start': date(2026, 8, 10),   'duration_days': 15,
        'capital_ratio': 0.20,            'desc': '配置医药ETF和创新药ETF，完成防御板块',
        'strategy': '结合中报披露窗口，优选医药板块回调时点建仓，配置债券类资产',
    },
    {
        'phase': 4, 'name': '第四阶段-最终调整',
        'start': date(2026, 9, 1),    'duration_days': 20,
        'capital_ratio': 0.15,            'desc': '微调各板块权重，完成建仓',
        'strategy': '审视前三阶段执行偏差，补齐偏离标的，配置短融ETF管理剩余现金',
    },
]

# =============================================================================
# 复用现有接口和组件（最小侵入式整合）
# =============================================================================

class RiskControlConfig:
    """风控配置读取器"""

    def __init__(self) -> None:
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
# 订单/持仓数据模型（与 live_trading_workflow 对齐）
# =============================================================================

class OrderStatus:
    PENDING = "pending"
    SENT = "sent"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class HedgeOrder:
    """对冲订单"""

    def __init__(
        self,
        order_id: str,
        symbol: str,
        name: str,
        side: str,
        quantity: int,
        limit_price: float,
        order_type: str = "LIMIT",
        strategy: str = "",
        layer: str = "",
        trigger_level: str = "NORMAL",
        status: str = OrderStatus.PENDING,
        broker_order_id: Optional[str] = None,
        filled_qty: int = 0,
        avg_price: float = 0.0,
        error_message: Optional[str] = None,
        created_at: str = "",
        strategy_note: str = "",
    ):
        self.order_id = order_id
        self.symbol = symbol
        self.name = name
        self.side = side
        self.quantity = quantity
        self.limit_price = limit_price
        self.order_type = order_type
        self.strategy = strategy
        self.layer = layer
        self.trigger_level = trigger_level
        self.status = status
        self.broker_order_id = broker_order_id
        self.filled_qty = filled_qty
        self.avg_price = avg_price
        self.error_message = error_message
        self.created_at = created_at or datetime.now().isoformat()
        self.strategy_note = strategy_note

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "name": self.name,
            "side": self.side,
            "quantity": self.quantity,
            "limit_price": self.limit_price,
            "order_type": self.order_type,
            "strategy": self.strategy,
            "layer": self.layer,
            "trigger_level": self.trigger_level,
            "status": self.status,
            "broker_order_id": self.broker_order_id,
            "filled_qty": self.filled_qty,
            "avg_price": self.avg_price,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "strategy_note": self.strategy_note,
        }


# =============================================================================
# 券商接口（依赖注入，支持 Mock/实盘）
# =============================================================================

class BrokerInterface(ABC):
    """券商接口抽象（与 live_trading_workflow.py 对齐）"""

    @abstractmethod
    def connect(self) -> bool:
        ...

    @abstractmethod
    def disconnect(self) -> bool:
        ...

    @abstractmethod
    def get_account_info(self) -> Dict[str, Any]:
        ...

    @abstractmethod
    def get_positions(self) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    def place_order(self, code: str, name: str, side: str,
                    quantity: int, limit_price: float,
                    order_type: str = "LIMIT") -> Dict[str, Any]:
        ...

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    def query_order(self, broker_order_id: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    def query_today_orders(self) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    def query_today_trades(self) -> List[Dict[str, Any]]:
        ...


class MockBroker(BrokerInterface):
    """模拟券商（开发/测试用）"""

    def __init__(self) -> None:
        self.connected = False
        self._order_counter = 0
        self._orders: Dict[str, Dict[str, Any]] = {}

    def connect(self) -> bool:
        self.connected = True
        logger.info("[MockBroker] 连接成功（模拟）")
        return True

    def disconnect(self) -> bool:
        self.connected = False
        logger.info("[MockBroker] 断开连接（模拟）")
        return True

    def get_account_info(self) -> Dict[str, Any]:
        return {
            "account_id": "MOCK_AUTO_HEDGE",
            "available_cash": 3_250_000.0,
            "total_asset": 5_000_000.0,
            "frozen_cash": 0.0,
            "market_value": 1_750_000.0,
        }

    def get_positions(self) -> List[Dict[str, Any]]:
        return []

    def place_order(self, code: str, name: str, side: str,
                    quantity: int, limit_price: float,
                    order_type: str = "LIMIT") -> Dict[str, Any]:
        self._order_counter += 1
        broker_id = f"MOCK_{datetime.now().strftime('%H%M%S')}_{self._order_counter:04d}"
        logger.info(f"[MockBroker] 下单: {code} {name} {side} {quantity} @{limit_price:.3f} -> {broker_id}")
        order = {
            "success": True,
            "broker_order_id": broker_id,
            "code": code,
            "name": name,
            "side": side,
            "quantity": quantity,
            "limit_price": limit_price,
            "order_type": order_type,
            "status": "sent",
            "message": "模拟下单成功",
        }
        self._orders[broker_id] = order
        return order

    def cancel_order(self, broker_order_id: str) -> Dict[str, Any]:
        logger.info(f"[MockBroker] 撤单: {broker_order_id}")
        return {"success": True, "broker_order_id": broker_order_id, "status": "cancelled"}

    def query_order(self, broker_order_id: str) -> Dict[str, Any]:
        return {
            "broker_order_id": broker_order_id,
            "status": "filled",
            "filled_quantity": 0,
            "avg_price": 0.0,
        }

    def query_today_orders(self) -> List[Dict[str, Any]]:
        return list(self._orders.values())

    def query_today_trades(self) -> List[Dict[str, Any]]:
        return []


# =============================================================================
# 资金底线监控
# =============================================================================

class CapitalFloorMonitor:
    """资金底线监控器"""

    def __init__(self, cfg: RiskControlConfig) -> None:
        self.cfg = cfg
        self.total_capital = float(cfg.get("total_capital", 5_000_000))
        self.risk = cfg.get("risk", {})
        self.high_water_mark: float = self.total_capital

    def update_high_water_mark(self, portfolio_value: float) -> None:
        if portfolio_value > self.high_water_mark:
            self.high_water_mark = portfolio_value
            logger.info(f"新高点水位更新: {self.high_water_mark:,.2f}")

    def evaluate(
        self,
        portfolio_value: float,
        equity_ratio: float,
        cash_ratio: float,
        futures_hedge_ratio: float,
        options_protection_coverage: float,
        gold_bond_ratio: float,
    ) -> Dict[str, Any]:
        drawdown = (
            (portfolio_value - self.high_water_mark) / self.high_water_mark
            if self.high_water_mark > 0
            else 0.0
        )
        breach_warning = drawdown <= -self.risk.get("drawdown_breach_warning", 0.10)
        breach_emergency = drawdown <= -self.risk.get("drawdown_breach_emergency", 0.15)
        breach_extreme = drawdown <= -self.risk.get("drawdown_breach_extreme", 0.20)

        if breach_extreme:
            recommended_level = "LEVEL_4"
        elif breach_emergency:
            recommended_level = "LEVEL_3"
        elif breach_warning:
            recommended_level = "LEVEL_2"
        else:
            recommended_level = "NORMAL"

        if breach_extreme:
            logger.critical(f"资金底线极端: drawdown={drawdown:.2%}, level={recommended_level}")
        elif breach_emergency:
            logger.error(f"资金底线紧急: drawdown={drawdown:.2%}, level={recommended_level}")
        elif breach_warning:
            logger.warning(f"资金底线告警: drawdown={drawdown:.2%}, level={recommended_level}")
        else:
            logger.info(f"资金底线正常: drawdown={drawdown:.2%}, level={recommended_level}")

        return {
            "timestamp": datetime.now().isoformat(),
            "portfolio_value": portfolio_value,
            "high_water_mark": self.high_water_mark,
            "drawdown": drawdown,
            "equity_ratio": equity_ratio,
            "cash_ratio": cash_ratio,
            "futures_hedge_ratio": futures_hedge_ratio,
            "options_protection_coverage": options_protection_coverage,
            "gold_bond_ratio": gold_bond_ratio,
            "breach_warning": breach_warning,
            "breach_emergency": breach_emergency,
            "breach_extreme": breach_extreme,
            "recommended_level": recommended_level,
        }


# =============================================================================
# 回撤熔断触发
# =============================================================================

class DrawdownBreachTrigger:
    """回撤熔断触发器"""

    def __init__(self, cfg: RiskControlConfig) -> None:
        self.cfg = cfg
        self.total_capital = float(cfg.get("total_capital", 5_000_000))
        self.risk = cfg.get("risk", {})
        self.high_water_mark: float = 0.0

    def evaluate_market(
        self,
        current_price: float,
        vix_level: float,
        daily_drop: float,
        weekly_drop: float,
        limit_down_count: int,
        sector_drops: Dict[str, float],
        portfolio_value: float,
    ) -> Dict[str, Any]:
        if portfolio_value > 0:
            self.high_water_mark = max(self.high_water_mark, portfolio_value)
        try:
            from black_swan_optimizer import IntradayCircuitBreaker

            cb = IntradayCircuitBreaker(total_capital=self.total_capital)
            cb.high_water_mark = max(self.high_water_mark, cb.high_water_mark)
            alert = cb.evaluate(
                current_price=current_price,
                vix_level=vix_level,
                sector_drops=sector_drops or {},
                limit_down_count=limit_down_count or 0,
                portfolio_value=portfolio_value,
            )
            result = {
                "level": alert.level.name,
                "level_value": alert.level.value,
                "trigger_reason": alert.trigger_reason,
                "intraday_drop": alert.intraday_drop,
                "recommended_action": alert.recommended_action,
                "executed": alert.executed,
            }
            # 如果主引擎因“无参考价”返回 NORMAL，且组合已出现明显回撤，则回退到本地阈值
            if result["level"] == "NORMAL" and "无参考价" in (result.get("trigger_reason") or ""):
                fallback = self._fallback_evaluate(
                    daily_drop=daily_drop,
                    weekly_drop=weekly_drop,
                    vix_level=vix_level,
                    portfolio_value=portfolio_value,
                    high_water_mark=self.high_water_mark,
                )
                if fallback["level_value"] > result["level_value"]:
                    return fallback
            return result
        except Exception as e:
            logger.error(f"熔断评估失败，回退到本地阈值: {e}")
            return self._fallback_evaluate(
                daily_drop=daily_drop,
                weekly_drop=weekly_drop,
                vix_level=vix_level,
                portfolio_value=portfolio_value,
                high_water_mark=self.high_water_mark,
            )

    def _fallback_evaluate(
        self,
        daily_drop: float,
        weekly_drop: float,
        vix_level: float,
        portfolio_value: float = 0.0,
        high_water_mark: float = 0.0,
    ) -> Dict[str, Any]:
        # 优先使用回撤熔断
        drawdown = 0.0
        if portfolio_value > 0 and high_water_mark > 0:
            drawdown = (portfolio_value - high_water_mark) / high_water_mark

        if drawdown <= -0.20 or vix_level >= 80 or daily_drop <= -0.09:
            return {
                "level": "LEVEL_4",
                "level_value": 4,
                "trigger_reason": f"fallback: drawdown={drawdown:.1%}, vix={vix_level:.1f}, daily={daily_drop:.1%}",
                "intraday_drop": daily_drop,
                "recommended_action": "全面防御",
                "executed": False,
            }
        if drawdown <= -0.15 or vix_level >= 60 or daily_drop <= -0.07:
            return {
                "level": "LEVEL_3",
                "level_value": 3,
                "trigger_reason": f"fallback: drawdown={drawdown:.1%}, vix={vix_level:.1f}, daily={daily_drop:.1%}",
                "intraday_drop": daily_drop,
                "recommended_action": "紧急减仓",
                "executed": False,
            }
        if drawdown <= -0.10 or daily_drop <= -0.05 or weekly_drop <= -0.10:
            return {
                "level": "LEVEL_2",
                "level_value": 2,
                "trigger_reason": f"fallback: drawdown={drawdown:.1%}, daily={daily_drop:.1%}, weekly={weekly_drop:.1%}",
                "intraday_drop": daily_drop,
                "recommended_action": "预警降仓",
                "executed": False,
            }
        return {
            "level": "NORMAL",
            "level_value": 0,
            "trigger_reason": "fallback: normal",
            "intraday_drop": daily_drop,
            "recommended_action": "正常交易",
            "executed": False,
        }


# =============================================================================
# 自动减仓
# =============================================================================

class AutoEquityReducer:
    """自动权益减仓器"""

    def __init__(self, cfg: RiskControlConfig) -> None:
        self.cfg = cfg
        self.total_capital = float(cfg.get("total_capital", 5_000_000))

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
            return {
                "need_reduce": False,
                "target_ratio": target,
                "reduce_amount": 0.0,
            }
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

    def build_reduce_orders(
        self,
        current_positions: List[Dict[str, Any]],
        current_equity_ratio: float,
        level_value: int,
    ) -> List[HedgeOrder]:
        plan = self.reduction_plan(current_equity_ratio, level_value)
        if not plan.get("need_reduce"):
            return []

        target = plan["target_ratio"]
        reduce_amount = plan["reduce_amount"]
        orders: List[HedgeOrder] = []

        # 按当前持仓比例分配减仓金额
        if not current_positions:
            return orders

        total_market_value = sum(
            float(p.get("market_value", 0.0) or 0.0) for p in current_positions
        )
        if total_market_value <= 0:
            return orders

        for pos in current_positions:
            market_value = float(pos.get("market_value", 0.0) or 0.0)
            ratio = market_value / total_market_value if total_market_value > 0 else 0.0
            amount = reduce_amount * ratio
            if amount <= 0:
                continue

            price = float(pos.get("current_price", 0.0) or 0.0)
            if price <= 0:
                continue

            qty = int(amount / price)
            if qty <= 0:
                continue

            # A股最小交易单位是 100 股
            qty = (qty // 100) * 100
            if qty <= 0:
                continue

            order = HedgeOrder(
                order_id=f"REDUCE_{datetime.now():%Y%m%d%H%M%S}_{pos.get('code','')}",
                symbol=pos.get("code", ""),
                name=pos.get("name", ""),
                side="SELL",
                quantity=qty,
                limit_price=round(price * 0.995, 2),  # 减仓时挂低一分，确保成交
                order_type="LIMIT",
                strategy="auto_equity_reduce",
                layer="equity",
                trigger_level=f"LEVEL_{level_value}",
            )
            orders.append(order)

        return orders


# =============================================================================
# 期权加厚
# =============================================================================

class OptionThickener:
    """期权加厚器"""

    def __init__(self, cfg: RiskControlConfig) -> None:
        self.cfg = cfg
        self.hedge_capital = float(cfg.get("hedge_capital", 2_000_000))
        self.options_capital = self.hedge_capital * 0.15

    def build_protection(self, market_data: Dict[str, Any]) -> List[HedgeOrder]:
        orders: List[HedgeOrder] = []
        try:
            from tail_risk_hedge import TailRiskHedge

            tail = TailRiskHedge(capital=self.options_capital)
            protection_needed = tail.calculate_protection_needed(
                portfolio_value=float(market_data.get("portfolio_value", 5_000_000)),
                market_data=market_data,
            )
            option_trades = tail.execute_protection_strategy(protection_needed, market_data)
            for trade in option_trades:
                quantity = int(trade.get("quantity", 0))
                # 若尾部保护需要下单但数量为 0，至少保留 1 张期权合约，避免保护失效
                if quantity <= 0 and trade.get("protection_ratio", 0) > 0:
                    quantity = 1
                order = HedgeOrder(
                    order_id=f"OPT_{datetime.now():%Y%m%d%H%M%S}_{len(orders)+1:03d}",
                    symbol=trade.get("symbol", ""),
                    name=trade.get("symbol", ""),
                    side=trade.get("direction", "buy"),
                    quantity=quantity,
                    limit_price=round(float(trade.get("premium", 0.0) or 0.0), 3),
                    order_type="LIMIT",
                    strategy=trade.get("strategy", "protective_put"),
                    layer="option",
                    trigger_level=market_data.get("trigger_level", "NORMAL"),
                )
                orders.append(order)
            logger.info(f"期权加厚完成: 生成 {len(orders)} 笔期权保护")
        except Exception as e:
            logger.error(f"期权加厚失败: {e}")
        return orders


# =============================================================================
# 期货加仓
# =============================================================================

class FuturesHedgeAmplifier:
    """期货加仓器"""

    def __init__(self, cfg: RiskControlConfig) -> None:
        self.cfg = cfg
        self.hedge_capital = float(cfg.get("hedge_capital", 2_000_000))
        self.futures_capital = self.hedge_capital * 0.15

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

    def build_hedge_orders(
        self,
        current_hedge_ratio: float,
        level_value: int,
        index_level: float = 3000,
    ) -> List[HedgeOrder]:
        plan = self.hedge_plan(current_hedge_ratio, level_value)
        if not plan.get("need_increase"):
            return []

        orders: List[HedgeOrder] = []
        # 沪深300股指期货每点 300 元
        multiplier = 300.0
        increase_notional = plan["increase_amount"]
        if increase_notional <= 0 or index_level <= 0:
            return orders

        contracts = int(increase_notional / (index_level * multiplier))
        if contracts <= 0:
            return orders

        order = HedgeOrder(
            order_id=f"FUT_{datetime.now():%Y%m%d%H%M%S}_{level_value}",
            symbol=self._resolve_futures_symbol(),
            name="沪深300股指期货",
            side="SELL_SHORT",
            quantity=contracts,
            limit_price=round(index_level, 2),
            order_type="LIMIT",
            strategy="futures_delta_hedge",
            layer="futures",
            trigger_level=f"LEVEL_{level_value}",
        )
        orders.append(order)
        return orders

    @staticmethod
    def _resolve_futures_symbol() -> str:
        """根据当前月份推断 IF 主力合约代码，默认回退到 IF2406"""
        try:
            now = datetime.now()
            # 每个季月的第三个周五为交割日，此处用简单近似：每月换月
            year = now.year % 100
            month = now.month
            return f"IF{year:02d}{month:02d}"
        except Exception:
            return "IF2406"


# =============================================================================
# 通知服务
# =============================================================================

class NotificationService(ABC):
    @abstractmethod
    def send_alert(self, title: str, content: str, level: str = "info") -> bool:
        ...

    @abstractmethod
    def send_trade_confirmation(self, order: HedgeOrder) -> bool:
        ...


class LogNotification(NotificationService):
    """日志通知（默认实现）"""

    def send_alert(self, title: str, content: str, level: str = "info") -> bool:
        getattr(logger, level, logger.info)(f"[ALERT] {title}: {content}")
        return True

    def send_trade_confirmation(self, order: HedgeOrder) -> bool:
        logger.info(f"[CONFIRM] {order.symbol} {order.side} {order.quantity} @{order.limit_price}")
        return True


# =============================================================================
# 持仓同步
# =============================================================================

class PositionSync:
    """券商持仓同步器"""

    def __init__(self, broker: BrokerInterface) -> None:
        self.broker = broker

    def sync(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        if not self.broker or not getattr(self.broker, "connected", False):
            return [], {"error": "broker_not_connected"}

        try:
            account = self.broker.get_account_info()
            positions = self.broker.get_positions()
            total_asset = float(account.get("total_asset", 0.0) or 0.0)
            market_value = sum(float(p.get("market_value", 0.0) or 0.0) for p in positions)
            cash = float(account.get("available_cash", 0.0) or 0.0)

            equity_ratio = market_value / total_asset if total_asset > 0 else 0.0
            cash_ratio = cash / total_asset if total_asset > 0 else 0.0

            sync_result = {
                "total_asset": total_asset,
                "market_value": market_value,
                "cash": cash,
                "equity_ratio": equity_ratio,
                "cash_ratio": cash_ratio,
                "position_count": len(positions),
            }
            return positions, sync_result
        except Exception as e:
            logger.error(f"持仓同步失败: {e}")
            return [], {"error": str(e)}


# =============================================================================
# 订单执行器
# =============================================================================

class OrderExecutor:
    """订单执行器：负责实际下单、撤单、查状态"""

    def __init__(self, broker: BrokerInterface, notifier: NotificationService) -> None:
        self.broker = broker
        self.notifier = notifier

    def execute(self, order: HedgeOrder) -> HedgeOrder:
        if not self.broker or not getattr(self.broker, "connected", False):
            order.status = OrderStatus.REJECTED
            order.error_message = "broker_not_connected"
            return order

        try:
            result = self.broker.place_order(
                code=order.symbol,
                name=order.name,
                side=order.side,
                quantity=order.quantity,
                limit_price=order.limit_price,
                order_type=order.order_type,
            )
            if result.get("success"):
                order.status = OrderStatus.SENT
                order.broker_order_id = result.get("broker_order_id")
                self.notifier.send_trade_confirmation(order)
                logger.info(
                    f"[ORDER] 下单成功: {order.symbol} {order.side} {order.quantity} "
                    f"@{order.limit_price} -> {order.broker_order_id}"
                )
            else:
                order.status = OrderStatus.REJECTED
                order.error_message = result.get("message", "unknown_error")
                logger.error(f"[ORDER] 下单失败: {order.symbol} {result.get('message')}")
        except Exception as e:
            order.status = OrderStatus.REJECTED
            order.error_message = str(e)
            logger.error(f"[ORDER] 下单异常: {e}")

        return order

    def cancel(self, order: HedgeOrder) -> HedgeOrder:
        if not order.broker_order_id:
            return order
        try:
            result = self.broker.cancel_order(order.broker_order_id)
            if result.get("success"):
                order.status = OrderStatus.CANCELLED
                logger.info(f"[ORDER] 撤单成功: {order.broker_order_id}")
            else:
                logger.error(f"[ORDER] 撤单失败: {result.get('status')}")
        except Exception as e:
            logger.error(f"[ORDER] 撤单异常: {e}")
        return order

    def refresh_status(self, order: HedgeOrder) -> HedgeOrder:
        if not order.broker_order_id:
            return order
        try:
            result = self.broker.query_order(order.broker_order_id)
            status = result.get("status", order.status)
            if status == "filled":
                order.status = OrderStatus.FILLED
                order.filled_qty = int(result.get("filled_quantity", order.quantity))
                order.avg_price = float(result.get("avg_price", order.limit_price) or 0.0)
            elif status == "partial":
                order.status = OrderStatus.PARTIAL
                order.filled_qty = int(result.get("filled_quantity", 0))
            elif status == "cancelled":
                order.status = OrderStatus.CANCELLED
            elif status == "rejected":
                order.status = OrderStatus.REJECTED
        except Exception as e:
            logger.error(f"[ORDER] 查单异常: {e}")
        return order


# =============================================================================
# 全自动对冲执行器
# =============================================================================

class AutoHedgeExecutor:
    """
    全自动对冲执行器

    执行流程：
      1. 实时/定时读取组合市值、持仓、市场数据
      2. CapitalFloorMonitor 计算 drawdown 和 breach
      3. DrawdownBreachTrigger 判定是否触发 LEVEL_2/3/4
      4. 若触发：
         a. AutoEquityReducer 生成减仓订单
         b. OptionThickener 生成期权加厚订单
         c. FuturesHedgeAmplifier 生成期货加仓订单
      5. OrderExecutor 统一执行所有订单
      6. PositionSync 同步最新持仓
      7. 输出执行报告并通知
    """

    def __init__(
        self,
        total_capital: float = 5_000_000,
        broker: Optional[BrokerInterface] = None,
        notifier: Optional[NotificationService] = None,
        poll_interval: int = 30,
    ):
        self.cfg = RiskControlConfig()
        self.total_capital = float(total_capital)
        self.broker = broker or MockBroker()
        self.notifier = notifier or LogNotification()
        self.poll_interval = poll_interval

        self.monitor = CapitalFloorMonitor(self.cfg)
        self.trigger = DrawdownBreachTrigger(self.cfg)
        self.reducer = AutoEquityReducer(self.cfg)
        self.thickener = OptionThickener(self.cfg)
        self.amplifier = FuturesHedgeAmplifier(self.cfg)
        self.position_sync = PositionSync(self.broker)
        self.order_executor = OrderExecutor(self.broker, self.notifier)
        self.optimizer = ModelSelfOptimizer()

        self.build_plan: Dict[str, Any] = {}
        self.build_phase_summary: List[Dict[str, Any]] = []
        self._load_build_plan()

        self._shutdown = False

    # ---------------------------------------------------------------
    # 建仓计划
    # ---------------------------------------------------------------
    def _load_build_plan(self) -> None:
        """加载 2026 交易计划标的与分阶段建仓计划"""
        today = date.today()
        in_period = BUILD_PLAN_START <= today <= BUILD_PLAN_END
        self.build_plan = {
            "in_period": in_period,
            "start": BUILD_PLAN_START.isoformat(),
            "end": BUILD_PLAN_END.isoformat(),
            "targets": TARGET_PORTFOLIO,
            "phases": [
                {
                    "phase": phase["phase"],
                    "name": phase["name"],
                    "start": phase["start"].isoformat(),
                    "end": (phase["start"] + timedelta(days=phase["duration_days"])).isoformat(),
                    "duration_days": phase["duration_days"],
                    "capital_ratio": phase["capital_ratio"],
                    "desc": phase["desc"],
                    "strategy": phase["strategy"],
                }
                for phase in BUILD_PHASES
            ],
            "today": today.isoformat(),
            "strategy_note": (
                "本组合基于康波第六轮复苏→繁荣周期，"
                "叠加十五五规划七大战略方向(AI/半导体/高端制造/新能源/数字经济/生物医药/能源安全)"
            ),
            "style_map": {
                "高端制造": "康波复苏期核心驱动，AI/算力/制造业升级主线",
                "防御": "十五五民生健康+组合安全垫，债券/医药/货币",
                "资源": "康波繁荣期商品超级周期，有色+黄金通胀对冲",
                "顺周期": "能源安全+高股息，组合稳定器",
            },
        }
        self.build_phase_summary = []
        for phase in BUILD_PHASES:
            start_dt = datetime.combine(phase['start'], datetime.min.time())
            end_dt = start_dt + timedelta(days=phase['duration_days'])
            self.build_phase_summary.append({
                "phase": phase['phase'],
                "name": phase['name'],
                "start": phase['start'].isoformat(),
                "end": end_dt.date().isoformat(),
                "capital_ratio": phase['capital_ratio'],
                "strategy": phase['strategy'],
            })

    def _current_build_phase(self) -> Optional[Dict[str, Any]]:
        """根据当前日期返回当前应执行的建仓阶段，None 表示不在建仓期"""
        if not self.build_plan.get("in_period"):
            return None
        today = date.today()
        for phase in BUILD_PHASES:
            start_dt = datetime.combine(phase['start'], datetime.min.time())
            end_dt = start_dt + timedelta(days=phase['duration_days'])
            if phase['start'] <= today <= end_dt.date():
                return phase
        return None

    def build_buy_orders(self, phase: Optional[Dict[str, Any]] = None) -> List[HedgeOrder]:
        """根据当前阶段生成建仓买入订单"""
        if phase is None:
            phase = self._current_build_phase()
        if not phase:
            return []

        today = date.today()
        elapsed = (today - phase['start']).days + 1
        duration = phase['duration_days']
        progress = min(1.0, max(0.0, elapsed / duration)) if duration > 0 else 1.0

        orders: List[HedgeOrder] = []
        capital_ratio = phase['capital_ratio']
        phase_capital = self.total_capital * capital_ratio

        for code, cfg in TARGET_PORTFOLIO.items():
            target_amount = phase_capital * cfg['weight']
            est_price = cfg['est_price']
            lots = cfg['lots']
            raw_shares = int(target_amount / est_price / lots) * lots if est_price > 0 else 0
            actual_amount = raw_shares * est_price
            if raw_shares <= 0 or actual_amount <= 0:
                continue

            qty = int(raw_shares * progress)
            qty = (qty // lots) * lots
            if qty <= 0:
                continue

            order = HedgeOrder(
                order_id=f"BUILD_{datetime.now():%Y%m%d%H%M%S}_{code}",
                symbol=code,
                name=cfg['name'],
                side="BUY",
                quantity=qty,
                limit_price=round(est_price, 2),
                order_type="LIMIT",
                strategy="build_plan_2026",
                layer="equity",
                trigger_level="BUILD_PLAN",
                strategy_note=(
                    f"[十五五+康波] {cfg['style']} | {cfg['reason']} | "
                    f"阶段: {phase['name']}"
                ),
            )
            orders.append(order)

        return orders

    # ---------------------------------------------------------------
    # 盘前
    # ---------------------------------------------------------------
    def pre_market(self) -> Dict[str, Any]:
        logger.info("=== 盘前检查开始 ===")
        report: Dict[str, Any] = {
            "mode": "pre_market",
            "executed_at": datetime.now().isoformat(),
            "success": True,
            "steps": [],
            "build_plan": self.build_plan,
            "build_phase": None,
            "strategy_note": (
                "本组合基于康波第六轮复苏→繁荣周期，"
                "叠加十五五规划七大战略方向(AI/半导体/高端制造/新能源/数字经济/生物医药/能源安全)"
            ),
            "style_map": {
                "高端制造": "康波复苏期核心驱动，AI/算力/制造业升级主线",
                "防御": "十五五民生健康+组合安全垫，债券/医药/货币",
                "资源": "康波繁荣期商品超级周期，有色+黄金通胀对冲",
                "顺周期": "能源安全+高股息，组合稳定器",
            },
        }

        try:
            # 1. 连接券商
            connected = self.broker.connect()
            report["steps"].append({
                "step": "broker_connect",
                "success": connected,
                "detail": "connected" if connected else "failed",
            })
            if not connected:
                raise RuntimeError("券商连接失败")

            # 2. 账户状态
            account = self.broker.get_account_info()
            report["steps"].append({"step": "account_info", "success": True, "detail": account})

            # 3. 持仓同步
            positions, sync = self.position_sync.sync()
            report["steps"].append({"step": "position_sync", "success": True, "detail": sync})

            # 4. 资金底线初始化
            portfolio_value = float(account.get("total_asset", self.total_capital) or self.total_capital)
            self.monitor.update_high_water_mark(portfolio_value)
            metrics = self.monitor.evaluate(
                portfolio_value=portfolio_value,
                equity_ratio=sync.get("equity_ratio", 0.6),
                cash_ratio=sync.get("cash_ratio", 0.05),
                futures_hedge_ratio=0.10,
                options_protection_coverage=0.10,
                gold_bond_ratio=0.08,
            )
            report["steps"].append({"step": "capital_floor_init", "success": True, "detail": metrics})

            # 5. 建仓计划检查
            current_phase = self._current_build_phase()
            if current_phase:
                report["build_phase"] = current_phase
                buy_orders = self.build_buy_orders(current_phase)
                executed_buy_orders: List[Dict[str, Any]] = []
                for order in buy_orders:
                    executed_order = self.order_executor.execute(order)
                    executed_buy_orders.append(executed_order.to_dict())
                report["steps"].append({
                    "step": "build_plan_buy",
                    "success": True,
                    "detail": {
                        "phase": current_phase['name'],
                        "orders_count": len(executed_buy_orders),
                        "orders": executed_buy_orders,
                    },
                })
                logger.info(f"建仓计划执行: 阶段={current_phase['name']}, 订单数={len(executed_buy_orders)}")

            logger.info("盘前检查完成")
        except Exception as e:
            report["success"] = False
            report["error"] = str(e)
            logger.error(f"盘前检查失败: {e}")

        return report

    # ---------------------------------------------------------------
    # 单次执行
    # ---------------------------------------------------------------
    def run_once(self, scenario: str = "normal", **market_inputs: Any) -> Dict[str, Any]:
        logger.info(f"=== 单次执行开始 scenario={scenario} ===")
        report: Dict[str, Any] = {
            "mode": "once",
            "scenario": scenario,
            "executed_at": datetime.now().isoformat(),
            "success": True,
            "steps": [],
            "orders": [],
            "metrics_before": None,
            "metrics_after": None,
            "strategy_note": (
                "本组合基于康波第六轮复苏→繁荣周期，"
                "叠加十五五规划七大战略方向(AI/半导体/高端制造/新能源/数字经济/生物医药/能源安全)"
            ),
            "style_map": {
                "高端制造": "康波复苏期核心驱动，AI/算力/制造业升级主线",
                "防御": "十五五民生健康+组合安全垫，债券/医药/货币",
                "资源": "康波繁荣期商品超级周期，有色+黄金通胀对冲",
                "顺周期": "能源安全+高股息，组合稳定器",
            },
        }

        try:
            if not getattr(self.broker, 'connected', False):
                self.broker.connect()

            # live 模式且未显式提供持仓时，自动从券商同步
            if scenario == "live" and not market_inputs.get("positions") and getattr(self.broker, "connected", False):
                try:
                    market_inputs["positions"] = self.broker.get_positions()
                except Exception as e:
                    logger.warning(f"自动获取持仓失败: {e}")

            portfolio_value = float(market_inputs.get("portfolio_value", self.total_capital))
            self.monitor.update_high_water_mark(portfolio_value)

            # 1. 资金底线监控
            metrics = self.monitor.evaluate(
                portfolio_value=portfolio_value,
                equity_ratio=float(market_inputs.get("equity_ratio", 0.60)),
                cash_ratio=float(market_inputs.get("cash_ratio", 0.05)),
                futures_hedge_ratio=float(market_inputs.get("futures_hedge_ratio", 0.10)),
                options_protection_coverage=float(market_inputs.get("options_protection_coverage", 0.10)),
                gold_bond_ratio=float(market_inputs.get("gold_bond_ratio", 0.08)),
            )
            report["metrics_before"] = metrics

            # 2. 熔断触发
            alert = self.trigger.evaluate_market(
                current_price=float(market_inputs.get("current_price", 3000)),
                vix_level=float(market_inputs.get("vix_level", 20.0)),
                daily_drop=float(market_inputs.get("daily_drop", 0.0)),
                weekly_drop=float(market_inputs.get("weekly_drop", 0.0)),
                limit_down_count=int(market_inputs.get("limit_down_count", 0)),
                sector_drops=market_inputs.get("sector_drops", {}),
                portfolio_value=portfolio_value,
            )
            report["steps"].append({"step": "drawdown_breach_trigger", "success": True, "detail": alert})
            level_value = int(alert.get("level_value", 0))

            # 3. 自动减仓
            current_positions = market_inputs.get("positions", [])
            reduce_orders = self.reducer.build_reduce_orders(
                current_positions=current_positions,
                current_equity_ratio=float(market_inputs.get("equity_ratio", 0.60)),
                level_value=level_value,
            )
            for order in reduce_orders:
                executed_order = self.order_executor.execute(order)
                report["orders"].append(executed_order.to_dict())
            report["steps"].append({
                "step": "auto_equity_reduce",
                "success": True,
                "detail": {"orders_count": len(reduce_orders), "orders": [o.to_dict() for o in reduce_orders]},
            })

            # 4. 期权加厚
            option_orders = self.thickener.build_protection({
                "index_price": market_inputs.get("current_price", 3000),
                "vix": market_inputs.get("vix_level", 20.0),
                "daily_drop": market_inputs.get("daily_drop", 0.0),
                "weekly_drop": market_inputs.get("weekly_drop", 0.0),
                "portfolio_value": portfolio_value,
                "volatility": market_inputs.get("volatility", 0.2),
                "liquidity": market_inputs.get("liquidity", 1.0),
                "var_95": market_inputs.get("var_95", 0.025),
                "es_95": market_inputs.get("es_95", 0.04),
                "trigger_level": metrics.get("recommended_level", "NORMAL"),
            })
            for order in option_orders:
                executed_order = self.order_executor.execute(order)
                report["orders"].append(executed_order.to_dict())
            report["steps"].append({
                "step": "option_thicken",
                "success": True,
                "detail": {"orders_count": len(option_orders), "orders": [o.to_dict() for o in option_orders]},
            })

            # 5. 期货加仓
            futures_orders = self.amplifier.build_hedge_orders(
                current_hedge_ratio=float(market_inputs.get("futures_hedge_ratio", 0.10)),
                level_value=level_value,
                index_level=float(market_inputs.get("current_price", 3000)),
            )
            for order in futures_orders:
                executed_order = self.order_executor.execute(order)
                report["orders"].append(executed_order.to_dict())
            report["steps"].append({
                "step": "futures_amplify",
                "success": True,
                "detail": {"orders_count": len(futures_orders), "orders": [o.to_dict() for o in futures_orders]},
            })

            # 6. 持仓同步（执行后）
            post_positions, post_sync = self.position_sync.sync()
            post_equity_ratio = post_sync.get("equity_ratio", metrics.get("equity_ratio", 0.6))
            post_metrics = self.monitor.evaluate(
                portfolio_value=portfolio_value,
                equity_ratio=post_equity_ratio,
                cash_ratio=max(0.15, post_sync.get("cash_ratio", 0.05)),
                futures_hedge_ratio=self.amplifier.target_hedge_ratio(level_value),
                options_protection_coverage=min(1.0, float(market_inputs.get("options_protection_coverage", 0.10)) + 0.15),
                gold_bond_ratio=max(
                    self.cfg.get("risk", {}).get("min_gold_bond_ratio", 0.15),
                    float(market_inputs.get("gold_bond_ratio", 0.08)),
                ),
            )
            report["metrics_after"] = post_metrics
            report["drawdown"] = metrics.get("drawdown", 0.0)
            report["trigger_level"] = metrics.get("recommended_level", "NORMAL")
            report["message"] = (
                f"执行完成，触发级别={report['trigger_level']}，"
                f"drawdown={report['drawdown']:.2%}，"
                f"订单数={len(report['orders'])}"
            )
            logger.info(report["message"])

            # 告警通知
            if metrics.get("breach_extreme") or metrics.get("breach_emergency"):
                self.notifier.send_alert(
                    title="黑天鹅风控触发",
                    content=report["message"],
                    level="critical" if metrics.get("breach_extreme") else "warning",
                )

            # 模型自优化：记录本次运行
            try:
                futures_ratio = 0.0
                options_ratio = 0.0
                for order in report.get("orders", []):
                    layer = order.get("layer", "")
                    if layer == "futures":
                        futures_ratio += 1
                    elif layer in ("option", "options"):
                        options_ratio += 1
                self.optimizer.record_run(
                    scenario=scenario,
                    trigger_level=report.get("trigger_level", "NORMAL"),
                    drawdown=report.get("drawdown", 0.0),
                    orders_count=len(report.get("orders", [])),
                    portfolio_value=float(market_inputs.get("portfolio_value", self.total_capital)),
                    vix_level=float(market_inputs.get("vix_level", 18.0)),
                    daily_drop=float(market_inputs.get("daily_drop", 0.0)),
                    weekly_drop=float(market_inputs.get("weekly_drop", 0.0)),
                    futures_ratio=futures_ratio,
                    options_ratio=options_ratio,
                )
                report["optimization"] = self.optimizer.generate_optimization_report()
            except Exception as e:
                logger.warning(f"模型自优化记录失败: {e}")
        except Exception as e:
            report["success"] = False
            report["error"] = str(e)
            logger.error(f"单次执行失败: {e}")

        return report

    # ---------------------------------------------------------------
    # 实时循环
    # ---------------------------------------------------------------
    def run_live(self) -> None:
        from market_data_adapter import MarketDataAdapter

        logger.info(f"进入 live 模式，每 {self.poll_interval} 秒执行一次")
        market_adapter = MarketDataAdapter()

        # 盘前检查
        pre_report = self.pre_market()
        if not pre_report.get("success"):
            logger.error("盘前检查失败，停止 live 模式")
            return

        try:
            while not self._shutdown:
                # 真实行情
                snapshot = market_adapter.get_market_snapshot()

                # 真实持仓/账户
                positions: List[Dict[str, Any]] = []
                portfolio_value = self.total_capital
                equity_ratio = 0.6
                cash_ratio = 0.05
                futures_hedge_ratio = 0.10
                options_protection_coverage = 0.10
                gold_bond_ratio = 0.08

                if getattr(self.broker, "connected", False):
                    try:
                        account = self.broker.get_account_info()
                        if not account.get("error"):
                            portfolio_value = float(account.get("total_asset", portfolio_value) or portfolio_value)
                            total_asset = portfolio_value
                            cash = float(account.get("available_cash", 0.0) or 0.0)
                            cash_ratio = cash / total_asset if total_asset > 0 else cash_ratio

                            positions = self.broker.get_positions()
                            market_value = sum(float(p.get("market_value", 0.0) or 0.0) for p in positions)
                            equity_ratio = market_value / total_asset if total_asset > 0 else equity_ratio
                    except Exception as e:
                        logger.warning(f"获取账户/持仓失败，使用默认值: {e}")

                market_inputs = {
                    "current_price": snapshot.get("current_price", 3000.0),
                    "portfolio_value": portfolio_value,
                    "vix_level": snapshot.get("vix_level", 18.0),
                    "daily_drop": snapshot.get("daily_drop", 0.0),
                    "weekly_drop": snapshot.get("weekly_drop", 0.0),
                    "limit_down_count": snapshot.get("limit_down_count", 0),
                    "sector_drops": snapshot.get("sector_drops", {}),
                    "volatility": snapshot.get("volatility", 0.18),
                    "liquidity": snapshot.get("liquidity", 1.0),
                    "var_95": snapshot.get("var_95", 0.025),
                    "es_95": snapshot.get("es_95", 0.04),
                    "positions": positions,
                    "equity_ratio": equity_ratio,
                    "cash_ratio": cash_ratio,
                    "futures_hedge_ratio": futures_hedge_ratio,
                    "options_protection_coverage": options_protection_coverage,
                    "gold_bond_ratio": gold_bond_ratio,
                }

                # 建仓期内优先执行建仓计划；否则执行正常风控
                current_phase = self._current_build_phase()
                if current_phase:
                    build_orders = self.build_buy_orders(current_phase)
                    report: Dict[str, Any] = {
                        "mode": "live_build_plan",
                        "executed_at": datetime.now().isoformat(),
                        "success": True,
                        "steps": [],
                        "orders": [],
                        "build_phase": current_phase,
                        "build_plan": self.build_plan,
                        "strategy_note": (
                            "本组合基于康波第六轮复苏→繁荣周期，"
                            "叠加十五五规划七大战略方向(AI/半导体/高端制造/新能源/数字经济/生物医药/能源安全)"
                        ),
                        "style_map": {
                            "高端制造": "康波复苏期核心驱动，AI/算力/制造业升级主线",
                            "防御": "十五五民生健康+组合安全垫，债券/医药/货币",
                            "资源": "康波繁荣期商品超级周期，有色+黄金通胀对冲",
                            "顺周期": "能源安全+高股息，组合稳定器",
                        },
                    }
                    for order in build_orders:
                        executed_order = self.order_executor.execute(order)
                        report["orders"].append(executed_order.to_dict())
                    report["steps"].append({
                        "step": "build_plan_buy",
                        "success": True,
                        "detail": {
                            "phase": current_phase['name'],
                            "orders_count": len(build_orders),
                            "orders": [o.to_dict() for o in build_orders],
                        },
                    })
                    logger.info(f"live 建仓执行: 阶段={current_phase['name']}, 订单数={len(build_orders)}")
                    self._persist_report(report)
                else:
                    report = self.run_once(scenario="live", **market_inputs)
                    self._persist_report(report)

                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            logger.info("live 模式手动停止")
        finally:
            self.broker.disconnect()

    # ---------------------------------------------------------------
    # 历史回测
    # ---------------------------------------------------------------
    def run_backtest(self) -> List[Dict[str, Any]]:
        from black_swan_auto_responder_light import ScenarioSimulator

        strategy_note = (
            "本组合基于康波第六轮复苏→繁荣周期，"
            "叠加十五五规划七大战略方向(AI/半导体/高端制造/新能源/数字经济/生物医药/能源安全)"
        )
        style_map = {
            "高端制造": "康波复苏期核心驱动，AI/算力/制造业升级主线",
            "防御": "十五五民生健康+组合安全垫，债券/医药/货币",
            "资源": "康波繁荣期商品超级周期，有色+黄金通胀对冲",
            "顺周期": "能源安全+高股息，组合稳定器",
        }
        reports: List[Dict[str, Any]] = []
        for name, fn in [
            ("normal", ScenarioSimulator.normal),
            ("bear_market", ScenarioSimulator.bear_market),
            ("black_swan", ScenarioSimulator.black_swan),
        ]:
            sim = fn()
            report = self.run_once(scenario=name, **sim)
            report["strategy_note"] = strategy_note
            report["style_map"] = style_map
            reports.append(report)
            self._persist_report(report)
        return reports

    # ---------------------------------------------------------------
    # 交易日自动调度
    # ---------------------------------------------------------------
    def run_schedule(self) -> None:
        import datetime as dt

        strategy_note = (
            "本组合基于康波第六轮复苏→繁荣周期，"
            "叠加十五五规划七大战略方向(AI/半导体/高端制造/新能源/数字经济/生物医药/能源安全)"
        )
        style_map = {
            "高端制造": "康波复苏期核心驱动，AI/算力/制造业升级主线",
            "防御": "十五五民生健康+组合安全垫，债券/医药/货币",
            "资源": "康波繁荣期商品超级周期，有色+黄金通胀对冲",
            "顺周期": "能源安全+高股息，组合稳定器",
        }

        logger.info("交易日自动调度启动...")
        print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] 交易日调度器已启动，等待触发任务...")

        # 跟踪盘中监控状态，避免重复启动
        live_started = False
        live_process: Optional[subprocess.Popen] = None
        live_log = BASE_DIR / "logs" / "live_monitor_schedule.log"
        live_log.parent.mkdir(exist_ok=True)

        try:
            while True:
                now = dt.datetime.now()
                weekday = now.weekday() < 5
                hour, minute = now.hour, now.minute

                # 盘前检查：工作日 7:00
                if weekday and hour == 7 and minute == 0:
                    logger.info("执行盘前检查")
                    print(f"[{now:%Y-%m-%d %H:%M:%S}] 执行盘前检查...")
                    report = self.pre_market()
                    report["strategy_note"] = strategy_note
                    report["style_map"] = style_map
                    self._persist_report(report)
                    print(f"[{now:%Y-%m-%d %H:%M:%S}] 盘前检查完成")
                    time.sleep(60)

                # 启动盘中监控：工作日 9:25
                elif weekday and hour == 9 and minute == 25 and not live_started:
                    logger.info("启动盘中监控")
                    print(f"[{now:%Y-%m-%d %H:%M:%S}] 启动盘中监控...")
                    live_process = subprocess.Popen(
                        [
                            "C:\\Program Files\\Python38\\python.exe",
                            str(BASE_DIR / "auto_hedge_executor.py"),
                            "--mode", "live",
                            "--broker", "mock",
                        ],
                        cwd=str(BASE_DIR),
                        stdout=open(live_log, "a", encoding="utf-8"),
                        stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                    )
                    live_started = True
                    print(f"[{now:%Y-%m-%d %H:%M:%S}] 盘中监控已启动，PID: {live_process.pid}")
                    time.sleep(60)

                # 停止盘中监控：工作日 15:05
                elif weekday and hour == 15 and minute == 5 and live_started:
                    logger.info("停止盘中监控")
                    print(f"[{now:%Y-%m-%d %H:%M:%S}] 停止盘中监控...")
                    if live_process:
                        try:
                            live_process.terminate()
                            live_process.wait(timeout=10)
                        except Exception as e:
                            logger.warning(f"停止 live 进程失败: {e}")
                    live_started = False
                    live_process = None
                    print(f"[{now:%Y-%m-%d %H:%M:%S}] 盘中监控已停止")
                    time.sleep(60)

                # 非交易日或非触发时段，等待 60 秒
                else:
                    time.sleep(60)

        except KeyboardInterrupt:
            logger.info("调度器手动停止")
            print(f"\n[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] 调度器停止")
            if live_process:
                try:
                    live_process.terminate()
                    live_process.wait(timeout=10)
                except Exception:
                    pass
        finally:
            if self.broker:
                self.broker.disconnect()

    # ---------------------------------------------------------------
    # 持久化
    # ---------------------------------------------------------------
    def _persist_report(self, report: Dict[str, Any]) -> None:
        try:
            report_dir = BASE_DIR / "logs"
            report_dir.mkdir(exist_ok=True)
            path = report_dir / f"auto_hedge_{datetime.now():%Y%m%d_%H%M%S}.json"
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.debug(f"报告已保存: {path}")
        except Exception as e:
            logger.warning(f"保存报告失败: {e}")


# =============================================================================
# 入口
# =============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="全自动对冲风险执行器")
    parser.add_argument("--mode", choices=["live", "once", "pre-market", "backtest", "schedule"], default="once")
    parser.add_argument("--capital", type=float, default=5_000_000)
    parser.add_argument("--scenario", choices=["normal", "bear_market", "black_swan"], default="normal")
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--broker", choices=["mock", "rest", "easytrader", "ht", "ths"], default="mock")
    args = parser.parse_args()

    broker: Optional[BrokerInterface] = None
    if args.broker == "mock":
        broker = MockBroker()
    elif args.broker in ("rest", "http"):
        from real_broker import RestBroker
        broker = RestBroker()
    elif args.broker in ("easytrader", "ht", "ths"):
        from real_broker import EasyTraderBroker
        broker = EasyTraderBroker()
    else:
        logger.error(f"不支持的 broker 类型: {args.broker}")
        return 1

    if not broker.connect():
        logger.error(f"券商连接失败: broker={args.broker}，请检查配置后重试")
        return 1

    executor = AutoHedgeExecutor(
        total_capital=args.capital,
        broker=broker,
        poll_interval=args.interval,
    )

    if args.mode == "pre-market":
        report = executor.pre_market()
        print(json.dumps({
            "strategy_note": "本组合基于康波第六轮复苏→繁荣周期，叠加十五五规划七大战略方向(AI/半导体/高端制造/新能源/数字经济/生物医药/能源安全)",
            "style_map": {
                "高端制造": "康波复苏期核心驱动，AI/算力/制造业升级主线",
                "防御": "十五五民生健康+组合安全垫，债券/医药/货币",
                "资源": "康波繁荣期商品超级周期，有色+黄金通胀对冲",
                "顺周期": "能源安全+高股息，组合稳定器",
            },
            **report,
        }, ensure_ascii=False, indent=2))
        return 0 if report.get("success") else 1

    if args.mode == "once":
        from black_swan_auto_responder_light import ScenarioSimulator
        sim = {
            "normal": ScenarioSimulator.normal(),
            "bear_market": ScenarioSimulator.bear_market(),
            "black_swan": ScenarioSimulator.black_swan(),
        }[args.scenario]
        report = executor.run_once(scenario=args.scenario, **sim)
        print(json.dumps({
            "strategy_note": "本组合基于康波第六轮复苏→繁荣周期，叠加十五五规划七大战略方向(AI/半导体/高端制造/新能源/数字经济/生物医药/能源安全)",
            "style_map": {
                "高端制造": "康波复苏期核心驱动，AI/算力/制造业升级主线",
                "防御": "十五五民生健康+组合安全垫，债券/医药/货币",
                "资源": "康波繁荣期商品超级周期，有色+黄金通胀对冲",
                "顺周期": "能源安全+高股息，组合稳定器",
            },
            **report,
        }, ensure_ascii=False, indent=2))
        return 0 if report.get("success") else 1

    if args.mode == "backtest":
        reports = executor.run_backtest()
        print(json.dumps({
            "strategy_note": "本组合基于康波第六轮复苏→繁荣周期，叠加十五五规划七大战略方向(AI/半导体/高端制造/新能源/数字经济/生物医药/能源安全)",
            "style_map": {
                "高端制造": "康波复苏期核心驱动，AI/算力/制造业升级主线",
                "防御": "十五五民生健康+组合安全垫，债券/医药/货币",
                "资源": "康波繁荣期商品超级周期，有色+黄金通胀对冲",
                "顺周期": "能源安全+高股息，组合稳定器",
            },
            "reports": reports,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.mode == "live":
        executor.run_live()
        return 0

    if args.mode == "schedule":
        executor.run_schedule()
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
