# -*- coding: utf-8 -*-
"""
快速规则引擎 - 可配置的交易策略和决策逻辑

提供灵活的交易规则系统，支持多种投资大师策略，
集成到快速响应层实现毫秒级交易决策。

主要功能：
- 多种投资大师策略模板
- 可配置的交易规则
- 动态权重调整
- 风险管理集成
- 策略切换
"""

import os
import sys
import json
import time
import numpy as np
from typing import Dict, List, Any, Optional, Tuple, Union
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import threading
from abc import ABC, abstractmethod
from enum import Enum

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from .logging_manager import get_logger
    logger = get_logger('rule_engine')
except ImportError:
    import logging
    logger = logging.getLogger('rule_engine')


class StrategyType(Enum):
    """策略类型枚举"""
    CONSERVATIVE = "conservative"      # 保守型
    AGGRESSIVE = "aggressive"        # 激进型
    BALANCED = "balanced"            # 平衡型
    MOMENTUM = "momentum"            # 动量型
    MEAN_REVERSION = "mean_reversion"  # 均值回归
    TREND_FOLLOWING = "trend_following"  # 趋势跟踪
    VALUE_INVESTING = "value_investing"  # 价值投资


class Action(Enum):
    """交易动作枚举"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    INCREASE = "INCREASE"


class MarketCondition(Enum):
    """市场状态枚举"""
    BULLISH = "bullish"
    BEARISH = "bearish"
    SIDEWAYS = "sideways"
    VOLATILE = "volatile"
    TRENDING = "trending"


@dataclass
class TradingRule:
    """交易规则"""
    name: str
    condition: str  # 条件表达式
    action: Action
    weight: float = 1.0  # 规则权重
    threshold: float = 0.5  # 触发阈值
    description: str = ""
    enabled: bool = True


@dataclass
class StrategyConfig:
    """策略配置"""
    name: str
    strategy_type: StrategyType
    description: str
    rules: List[TradingRule] = field(default_factory=list)
    risk_management: Dict[str, Any] = field(default_factory=dict)
    performance_metrics: Dict[str, float] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class DecisionContext:
    """决策上下文"""
    code: str
    price: float
    indicators: Dict[str, float]
    market_condition: MarketCondition
    portfolio_position: float = 0.0  # 当前仓位 (-1到1)
    risk_tolerance: float = 0.5  # 风险容忍度 (0-1)
    time_horizon: str = "short"  # 时间维度: short/medium/long
    volatility: float = 0.0  # 波动率


class BaseStrategy(ABC):
    """策略基类"""
    
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.rules: List[TradingRule] = []
        self.performance_history = []
        self.created_at = datetime.now()
    
    @abstractmethod
    def generate_rules(self) -> List[TradingRule]:
        """生成策略规则"""
        pass
    
    @abstractmethod
    def evaluate_market_condition(self, indicators: Dict[str, float]) -> MarketCondition:
        """评估市场状态"""
        pass
    
    def add_rule(self, rule: TradingRule):
        """添加规则"""
        self.rules.append(rule)
    
    def remove_rule(self, rule_name: str):
        """移除规则"""
        self.rules = [r for r in self.rules if r.name != rule_name]
    
    def get_enabled_rules(self) -> List[TradingRule]:
        """获取启用的规则"""
        return [r for r in self.rules if r.enabled]


class ConservativeStrategy(BaseStrategy):
    """保守型策略 - 基于格雷厄姆价值投资理念"""
    
    def __init__(self):
        super().__init__(
            "保守型策略",
            "基于价值投资理念，追求稳健收益，适合风险厌恶型投资者"
        )
        self.generate_rules()
    
    def generate_rules(self) -> List[TradingRule]:
        """生成保守型规则"""
        rules = [
            TradingRule(
                name="rsi超卖买入",
                condition="rsi < 30",
                action=Action.BUY,
                weight=1.0,
                threshold=0.3,
                description="RSI低于30超卖时买入"
            ),
            TradingRule(
                name="rsi超卖卖出",
                condition="rsi > 70",
                action=Action.SELL,
                weight=1.0,
                threshold=0.7,
                description="RSI高于70超买时卖出"
            ),
            TradingRule(
                name="MACD金叉买入",
                condition="macd > macd_signal and macd > 0",
                action=Action.BUY,
                weight=0.8,
                threshold=0.01,
                description="MACD金叉且MACD线为正时买入"
            ),
            TradingRule(
                name="MACD死叉卖出",
                condition="macd < macd_signal and macd < 0",
                action=Action.SELL,
                weight=0.8,
                threshold=-0.01,
                description="MACD死叉且MACD线为负时卖出"
            ),
            TradingRule(
                name="移动平均支撑买入",
                condition="ma5 > ma20 and price > ma20",
                action=Action.BUY,
                weight=0.6,
                threshold=0,
                description="短期均线上穿长期均线且价格在均线上方"
            ),
            TradingRule(
                name="移动平均压力卖出",
                condition="ma5 < ma20 and price < ma20",
                action=Action.SELL,
                weight=0.6,
                threshold=0,
                description="短期均线下穿长期均线且价格在均线下方"
            ),
            TradingRule(
                name="动量确认买入",
                condition="momentum > 0.02",
                action=Action.BUY,
                weight=0.7,
                threshold=0.02,
                description="正动量确认上涨趋势"
            ),
            TradingRule(
                name="动量确认卖出",
                condition="momentum < -0.02",
                action=Action.SELL,
                weight=0.7,
                threshold=-0.02,
                description="负动量确认下跌趋势"
            )
        ]
        self.rules = rules
        return rules
    
    def evaluate_market_condition(self, indicators: Dict[str, float]) -> MarketCondition:
        """评估市场状态"""
        rsi = indicators.get('rsi', 50)
        macd = indicators.get('macd', 0)
        momentum = indicators.get('momentum', 0)
        
        # 保守型策略的市场状态评估
        if rsi < 30 and momentum > 0:
            return MarketCondition.BULLISH
        elif rsi > 70 and momentum < 0:
            return MarketCondition.BEARISH
        elif 30 <= rsi <= 70 and abs(macd) < 0.01:
            return MarketCondition.SIDEWAYS
        elif abs(momentum) > 0.03:
            return MarketCondition.VOLATILE
        else:
            return MarketCondition.TRENDING


class AggressiveStrategy(BaseStrategy):
    """激进型策略 - 基于趋势跟踪理念"""
    
    def __init__(self):
        super().__init__(
            "激进型策略",
            "基于趋势跟踪理念，追求最大化收益，适合风险偏好型投资者"
        )
        self.generate_rules()
    
    def generate_rules(self) -> List[TradingRule]:
        """生成激进型规则"""
        rules = [
            TradingRule(
                name="强势买入",
                condition="rsi < 25 and macd > 0.005",
                action=Action.BUY,
                weight=1.2,
                threshold=0.4,
                description="RSI超卖且MACD强势向上"
            ),
            TradingRule(
                name="强势卖出",
                condition="rsi > 75 and macd < -0.005",
                action=Action.SELL,
                weight=1.2,
                threshold=0.4,
                description="RSI超买且MACD强势向下"
            ),
            TradingRule(
                name="趋势跟踪买入",
                condition="ma5 > ma20 and ma20 > ma60",
                action=Action.BUY,
                weight=1.0,
                threshold=0,
                description="短期均线上穿长期均线且长期均线向上"
            ),
            TradingRule(
                name="趋势跟踪卖出",
                condition="ma5 < ma20 and ma20 < ma60",
                action=Action.SELL,
                weight=1.0,
                threshold=0,
                description="短期均线下穿长期均线且长期均线向下"
            ),
            TradingRule(
                name="高动量买入",
                condition="momentum > 0.03",
                action=Action.BUY,
                weight=1.5,
                threshold=0.03,
                description="高动量时积极买入"
            ),
            TradingRule(
                name="高动量卖出",
                condition="momentum < -0.03",
                action=Action.SELL,
                weight=1.5,
                threshold=-0.03,
                description="高动量时积极卖出"
            ),
            TradingRule(
                name="突破买入",
                condition="price > ma20 + 0.02 * ma20",
                action=Action.BUY,
                weight=0.8,
                threshold=0.02,
                description="价格突破布林带上轨"
            ),
            TradingRule(
                name="跌破卖出",
                condition="price < ma20 - 0.02 * ma20",
                action=Action.SELL,
                weight=0.8,
                threshold=-0.02,
                description="价格跌破布林带下轨"
            )
        ]
        self.rules = rules
        return rules
    
    def evaluate_market_condition(self, indicators: Dict[str, float]) -> MarketCondition:
        """评估市场状态"""
        rsi = indicators.get('rsi', 50)
        macd = indicators.get('macd', 0)
        momentum = indicators.get('momentum', 0)
        
        # 激进型策略的市场状态评估
        if rsi < 25 and macd > 0.005:
            return MarketCondition.BULLISH
        elif rsi > 75 and macd < -0.005:
            return MarketCondition.BEARISH
        elif abs(momentum) > 0.05:
            return MarketCondition.VOLATILE
        elif macd > 0:
            return MarketCondition.TRENDING
        else:
            return MarketCondition.SIDEWAYS


class BalancedStrategy(BaseStrategy):
    """平衡型策略 - 结合价值与成长"""
    
    def __init__(self):
        super().__init__(
            "平衡型策略",
            "结合价值投资和成长投资，平衡风险与收益，适合大多数投资者"
        )
        self.generate_rules()
    
    def generate_rules(self) -> List[TradingRule]:
        """生成平衡型规则"""
        rules = [
            TradingRule(
                name="价值买入",
                condition="rsi < 35 and macd > 0",
                action=Action.BUY,
                weight=1.0,
                threshold=0.35,
                description="价值投资理念：RSI适中且MACD向上"
            ),
            TradingRule(
                name="成长卖出",
                condition="rsi > 65 and macd < 0",
                action=Action.SELL,
                weight=1.0,
                threshold=0.65,
                description="成长投资理念：RSI过高且MACD向下"
            ),
            TradingRule(
                name="趋势确认买入",
                condition="ma5 > ma20 and momentum > 0.015",
                action=Action.BUY,
                weight=0.8,
                threshold=0.015,
                description="趋势确认：均线上穿且动量为正"
            ),
            TradingRule(
                name="趋势确认卖出",
                condition="ma5 < ma20 and momentum < -0.015",
                action=Action.SELL,
                weight=0.8,
                threshold=-0.015,
                description="趋势确认：均线下穿且动量为负"
            ),
            TradingRule(
                name="波动率买入",
                condition="volatility < 0.2 and momentum > 0.01",
                action=Action.BUY,
                weight=0.6,
                threshold=0.01,
                description="低波动率下的稳定买入"
            ),
            TradingRule(
                name="波动率卖出",
                condition="volatility > 0.4 and momentum < -0.01",
                action=Action.SELL,
                weight=0.6,
                threshold=-0.01,
                description="高波动率下的风险卖出"
            ),
            TradingRule(
                name="成交量确认买入",
                condition="volume_ratio > 1.2 and momentum > 0.01",
                action=Action.BUY,
                weight=0.7,
                threshold=0.01,
                description="成交量放大确认买入"
            ),
            TradingRule(
                name="成交量确认卖出",
                condition="volume_ratio < 0.8 and momentum < -0.01",
                action=Action.SELL,
                weight=0.7,
                threshold=-0.01,
                description="成交量萎缩确认卖出"
            )
        ]
        self.rules = rules
        return rules
    
    def evaluate_market_condition(self, indicators: Dict[str, float]) -> MarketCondition:
        """评估市场状态"""
        rsi = indicators.get('rsi', 50)
        macd = indicators.get('macd', 0)
        momentum = indicators.get('momentum', 0)
        
        # 平衡型策略的市场状态评估
        if rsi < 35 and macd > 0.005:
            return MarketCondition.BULLISH
        elif rsi > 65 and macd < -0.005:
            return MarketCondition.BEARISH
        elif 35 <= rsi <= 65 and abs(momentum) < 0.02:
            return MarketCondition.SIDEWAYS
        elif abs(momentum) > 0.03:
            return MarketCondition.VOLATILE
        else:
            return MarketCondition.TRENDING


class RuleEngine:
    """规则引擎主类"""
    
    def __init__(self):
        """初始化规则引擎"""
        self.strategies: Dict[str, BaseStrategy] = {}
        self.active_strategy: Optional[str] = None
        self.performance_metrics = {}
        self.decision_history = []
        self.lock = threading.RLock()
        
        # 初始化内置策略
        self._init_builtin_strategies()
        
        logger.info("规则引擎初始化完成")
    
    def _init_builtin_strategies(self):
        """初始化内置策略"""
        strategies = {
            "conservative": ConservativeStrategy(),
            "aggressive": AggressiveStrategy(),
            "balanced": BalancedStrategy(),
        }
        
        for name, strategy in strategies.items():
            self.strategies[name] = strategy
        
        # 设置默认策略
        self.active_strategy = "balanced"
        
        logger.info(f"已加载内置策略: {list(strategies.keys())}")
    
    def register_strategy(self, name: str, strategy: BaseStrategy):
        """注册自定义策略"""
        with self.lock:
            self.strategies[name] = strategy
            logger.info(f"已注册策略: {name}")
    
    def set_active_strategy(self, strategy_name: str):
        """设置活跃策略"""
        with self.lock:
            if strategy_name in self.strategies:
                self.active_strategy = strategy_name
                logger.info(f"已设置活跃策略: {strategy_name}")
            else:
                logger.error(f"策略不存在: {strategy_name}")
    
    def get_available_strategies(self) -> List[str]:
        """获取可用策略列表"""
        return list(self.strategies.keys())
    
    def evaluate_decision(self, context: DecisionContext) -> Dict[str, Any]:
        """
        评估交易决策
        
        Args:
            context: 决策上下文
            
        Returns:
            决策结果
        """
        start_time = time.perf_counter()
        
        if self.active_strategy not in self.strategies:
            return {
                "action": Action.HOLD,
                "confidence": 0.0,
                "reason": "无可用策略",
                "timestamp": datetime.now().isoformat()
            }
        
        strategy = self.strategies[self.active_strategy]
        market_condition = strategy.evaluate_market_condition(context.indicators)
        
        # 获取启用的规则
        enabled_rules = strategy.get_enabled_rules()
        
        # 评估每个规则
        rule_results = []
        for rule in enabled_rules:
            try:
                if self._evaluate_rule_condition(rule, context):
                    score = self._calculate_rule_score(rule, context)
                    rule_results.append({
                        "rule": rule.name,
                        "action": rule.action,
                        "score": score,
                        "weight": rule.weight
                    })
            except Exception as e:
                logger.warning(f"评估规则 {rule.name} 时出错: {e}")
        
        if not rule_results:
            return {
                "action": Action.HOLD,
                "confidence": 0.0,
                "reason": "无规则被触发",
                "timestamp": datetime.now().isoformat()
            }
        
        # 加权计算最终决策
        final_decision = self._calculate_final_decision(rule_results, context)
        
        # 应用风险管理
        final_decision = self._apply_risk_management(final_decision, context)
        
        # 记录决策历史
        decision_record = {
            "timestamp": datetime.now().isoformat(),
            "code": context.code,
            "strategy": self.active_strategy,
            "market_condition": market_condition.value,
            "context": context.__dict__,
            "rule_results": rule_results,
            "final_decision": final_decision,
            "latency_ms": (time.perf_counter() - start_time) * 1000
        }
        
        with self.lock:
            self.decision_history.append(decision_record)
            # 保持历史记录在合理范围内
            if len(self.decision_history) > 1000:
                self.decision_history = self.decision_history[-500:]
        
        return final_decision
    
    def _evaluate_rule_condition(self, rule: TradingRule, context: DecisionContext) -> bool:
        """评估规则条件"""
        try:
            # 简单的条件解析（实际项目中可以使用更复杂的表达式解析器）
            condition = rule.condition
            
            # 替换条件中的变量
            for indicator_name, value in context.indicators.items():
                condition = condition.replace(indicator_name, str(value))
            
            # 评估条件
            return eval(condition) if condition else False
            
        except Exception as e:
            logger.warning(f"评估规则条件时出错: {e}")
            return False
    
    def _calculate_rule_score(self, rule: TradingRule, context: DecisionContext) -> float:
        """计算规则得分"""
        score = rule.weight
        
        # 根据市场状态调整得分
        if context.market_condition == MarketCondition.BULLISH and rule.action == Action.BUY:
            score *= 1.2
        elif context.market_condition == MarketCondition.BEARISH and rule.action == Action.SELL:
            score *= 1.2
        
        # 根据风险容忍度调整得分
        if rule.action in [Action.BUY, Action.INCREASE] and context.risk_tolerance < 0.3:
            score *= 0.7
        elif rule.action in [Action.SELL, Action.REDUCE] and context.risk_tolerance > 0.7:
            score *= 0.7
        
        # 根据时间维度调整得分
        if context.time_horizon == "short" and rule.action == Action.BUY:
            score *= 1.1
        elif context.time_horizon == "long" and rule.action == Action.SELL:
            score *= 1.1
        
        return min(score, 2.0)  # 限制最大得分为2.0
    
    def _calculate_final_decision(self, rule_results: List[Dict], context: DecisionContext) -> Dict[str, Any]:
        """计算最终决策"""
        # 按动作分组
        buy_score = 0.0
        sell_score = 0.0
        hold_score = 0.0
        
        for result in rule_results:
            action = result["action"]
            score = result["score"] * result["weight"]
            
            if action == Action.BUY:
                buy_score += score
            elif action == Action.SELL:
                sell_score += score
            else:
                hold_score += score
        
        # 计算总得分
        total_score = buy_score + sell_score + hold_score
        
        # 确定最终动作
        if total_score == 0:
            return {
                "action": Action.HOLD,
                "confidence": 0.0,
                "reason": "无有效规则触发",
                "timestamp": datetime.now().isoformat()
            }
        
        # 计算置信度
        max_score = max(buy_score, sell_score, hold_score)
        confidence = max_score / total_score
        
        # 确定动作
        if buy_score > sell_score and buy_score > hold_score and confidence > 0.3:
            action = Action.BUY
            reason = f"买入信号 (得分: {buy_score:.2f})"
        elif sell_score > buy_score and sell_score > hold_score and confidence > 0.3:
            action = Action.SELL
            reason = f"卖出信号 (得分: {sell_score:.2f})"
        else:
            action = Action.HOLD
            reason = f"观望信号 (最高得分: {max_score:.2f})"
        
        return {
            "action": action.value,
            "confidence": confidence,
            "reason": reason,
            "buy_score": buy_score,
            "sell_score": sell_score,
            "hold_score": hold_score,
            "total_score": total_score,
            "timestamp": datetime.now().isoformat()
        }
    
    def _apply_risk_management(self, decision: Dict[str, Any], context: DecisionContext) -> Dict[str, Any]:
        """应用风险管理"""
        # 基本风险管理规则
        if decision["confidence"] < 0.3:
            decision["action"] = Action.HOLD.value
            decision["reason"] = f"低置信度决策，改为观望 (置信度: {decision['confidence']:.2f})"
            decision["confidence"] *= 0.5
        
        # 基于仓位的风险管理
        if context.portfolio_position > 0.8 and decision["action"] == Action.BUY.value:
            decision["action"] = Action.HOLD.value
            decision["reason"] = "仓位过高，暂不买入"
            decision["confidence"] *= 0.7
        
        elif context.portfolio_position < -0.8 and decision["action"] == Action.SELL.value:
            decision["action"] = Action.HOLD.value
            decision["reason"] = "仓位过低，暂不卖出"
            decision["confidence"] *= 0.7
        
        # 基于波动率的风险管理
        if context.volatility > 0.5 and decision["confidence"] < 0.5:
            decision["action"] = Action.HOLD.value
            decision["reason"] = "高波动率环境下观望"
            decision["confidence"] *= 0.6
        
        return decision
    
    def get_strategy_info(self, strategy_name: str) -> Optional[Dict[str, Any]]:
        """获取策略信息"""
        if strategy_name not in self.strategies:
            return None
        
        strategy = self.strategies[strategy_name]
        
        return {
            "name": strategy.name,
            "description": strategy.description,
            "rule_count": len(strategy.rules),
            "enabled_rule_count": len(strategy.get_enabled_rules()),
            "created_at": strategy.created_at.isoformat(),
            "rules": [
                {
                    "name": rule.name,
                    "condition": rule.condition,
                    "action": rule.action.value,
                    "weight": rule.weight,
                    "threshold": rule.threshold,
                    "description": rule.description,
                    "enabled": rule.enabled
                }
                for rule in strategy.rules
            ]
        }
    
    def get_decision_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取决策历史"""
        with self.lock:
            return self.decision_history[-limit:]
    
    def get_performance_metrics(self) -> Dict[str, Any]:
        """获取性能指标"""
        with self.lock:
            if not self.decision_history:
                return {"message": "暂无决策历史"}
            
            # 计算基本统计
            total_decisions = len(self.decision_history)
            buy_decisions = sum(1 for d in self.decision_history if d["final_decision"]["action"] == "BUY")
            sell_decisions = sum(1 for d in self.decision_history if d["final_decision"]["action"] == "SELL")
            hold_decisions = sum(1 for d in self.decision_history if d["final_decision"]["action"] == "HOLD")
            
            # 计算平均置信度
            avg_confidence = sum(d["final_decision"]["confidence"] for d in self.decision_history) / total_decisions
            
            # 计算平均延迟
            avg_latency = sum(d["latency_ms"] for d in self.decision_history) / total_decisions
            
            return {
                "total_decisions": total_decisions,
                "buy_decisions": buy_decisions,
                "sell_decisions": sell_decisions,
                "hold_decisions": hold_decisions,
                "buy_ratio": buy_decisions / total_decisions if total_decisions > 0 else 0,
                "sell_ratio": sell_decisions / total_decisions if total_decisions > 0 else 0,
                "hold_ratio": hold_decisions / total_decisions if total_decisions > 0 else 0,
                "average_confidence": avg_confidence,
                "average_latency_ms": avg_latency,
                "active_strategy": self.active_strategy,
                "available_strategies": list(self.strategies.keys())
            }


# 全局单例
_rule_engine: Optional[RuleEngine] = None


def get_rule_engine() -> RuleEngine:
    """获取全局规则引擎单例"""
    global _rule_engine
    if _rule_engine is None:
        _rule_engine = RuleEngine()
    return _rule_engine


# 便捷函数
def evaluate_trading_decision(indicators: Dict[str, float], code: str, strategy: str = "balanced") -> Dict[str, Any]:
    """便捷函数：评估交易决策"""
    engine = get_rule_engine()
    engine.set_active_strategy(strategy)
    
    context = DecisionContext(
        code=code,
        price=indicators.get('close', 0),
        indicators=indicators,
        market_condition=MarketCondition.TRENDING,
        risk_tolerance=0.5
    )
    
    return engine.evaluate_decision(context)


def get_rule_engine_metrics() -> Dict[str, Any]:
    """便捷函数：获取规则引擎指标"""
    engine = get_rule_engine()
    return engine.get_performance_metrics()


if __name__ == '__main__':
    # 测试示例
    print("测试规则引擎...")
    
    engine = get_rule_engine()
    
    # 测试数据
    test_indicators = {
        'close': 100.0,
        'rsi': 25.0,
        'macd': 0.02,
        'ma5': 99.0,
        'ma20': 98.0,
        'momentum': 0.03,
        'volatility': 0.15,
        'volume_ratio': 1.5
    }
    
    # 测试不同策略
    for strategy in ['conservative', 'aggressive', 'balanced']:
        print(f"\n=== 测试 {strategy} 策略 ===")
        engine.set_active_strategy(strategy)
        
        result = evaluate_trading_decision(test_indicators, '600519', strategy)
        print(f"决策: {result['action']}")
        print(f"置信度: {result['confidence']:.3f}")
        print(f"原因: {result['reason']}")
    
    # 显示性能指标
    print(f"\n性能指标:")
    metrics = get_rule_engine_metrics()
    for key, value in metrics.items():
        print(f"  {key}: {value}")
    
    # 显示策略信息
    print(f"\n策略信息:")
    for strategy_name in engine.get_available_strategies():
        info = engine.get_strategy_info(strategy_name)
        print(f"  {info['name']}: {info['description']}")
        print(f"    规则数量: {info['rule_count']} (启用: {info['enabled_rule_count']})")