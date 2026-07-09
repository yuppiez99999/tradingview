# -*- coding: utf-8 -*-
"""
动态对冲资金管理器 - 世界级对冲基金的智能资金配置系统

系统特点：
- 分层配置管理：动态配置各层对冲策略的资金比例
- 风险预算分配：基于风险贡献度的动态资金分配
- 市场适应调整：根据市场状态自动调整资金配置
- 多目标优化：同时考虑收益、风险、对冲效果的多目标优化
- 实时监控调整：实时监控资金使用效果并动态调整
- 压力测试机制：在各种市场环境下测试资金配置效果

核心功能：
1. 静态配置管理：基于策略的默认资金配置
2. 动态调整机制：基于市场状态的实时调整
3. 风险预算分配：基于风险贡献度的智能分配
4. 资金使用效率监控：监控资金使用效率和效果
5. 极端情况处理：在极端市场情况下的资金保护机制
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Union
from collections import deque
import json

try:
    from utils.logger import get_logger
    from utils.risk_metrics import calculate_var, calculate_es, calculate_max_drawdown
    from utils.data_provider import get_market_data
    logger = get_logger('dynamic_capital_manager')
except ImportError:
    import logging
    logger = logging.getLogger('dynamic_capital_manager')

class RiskBudgetAllocator:
    """
    风险预算分配器
    """
    
    def __init__(self, total_capital: float):
        self.total_capital = total_capital
        self.risk_budgets = {
            'market_risk': 0.40,
            'volatility_risk': 0.35,
            'tail_risk': 0.25
        }
        
        # 风险预算调整参数
        self.adjustment_params = {
            'volatility_multiplier': 2.0,      # 波动率调整系数
            'var_multiplier': 1.5,             # VaR调整系数
            'correlation_buffer': 0.1,         # 相关性缓冲
            'min_budget_ratio': 0.05,           # 最小预算比例
            'max_budget_ratio': 0.50           # 最大预算比例
        }
        
        # 历史风险贡献度
        self.risk_contribution_history = deque(maxlen=100)
        
    def allocate_risk_budget(self, market_data: Dict) -> Dict:
        """
        分配风险预算
        
        Args:
            market_data: 市场数据
            
        Returns:
            风险预算分配方案
        """
        try:
            # 计算当前风险指标
            current_risks = self._calculate_current_risks(market_data)
            
            # 计算风险贡献度
            risk_contributions = self._calculate_risk_contributions(current_risks)
            
            # 动态调整风险预算
            adjusted_budgets = self._adjust_risk_budgets(risk_contributions, current_risks)
            
            # 确保预算总和为1
            total_budget = sum(adjusted_budgets.values())
            if total_budget > 0:
                adjusted_budgets = {k: v / total_budget for k, v in adjusted_budgets.items()}
            
            # 记录历史
            self.risk_contribution_history.append({
                'timestamp': datetime.now().isoformat(),
                'risk_budgets': adjusted_budgets,
                'risk_contributions': risk_contributions,
                'current_risks': current_risks
            })
            
            logger.info(f"风险预算分配完成: {adjusted_budgets}")
            return adjusted_budgets
            
        except Exception as e:
            logger.error(f"风险预算分配失败: {e}")
            return self.risk_budgets.copy()
    
    def _calculate_current_risks(self, market_data: Dict) -> Dict:
        """计算当前风险指标"""
        risks = {
            'market_risk': {
                'var_95': market_data.get('var_95', 0.02),
                'beta': market_data.get('beta', 1.0),
                'volatility': market_data.get('volatility', 0.15),
                'max_drawdown': market_data.get('max_drawdown', 0.0)
            },
            'volatility_risk': {
                'volatility': market_data.get('volatility', 0.15),
                'vix_level': market_data.get('vix_future_price', 20.0) / 50.0,
                'volatility_skew': market_data.get('volatility_skew', 0.0),
                'volatility_term': market_data.get('volatility_term', 0.0)
            },
            'tail_risk': {
                'var_99': market_data.get('var_99', 0.035),
                'es_95': market_data.get('es_95', 0.03),
                'kurtosis': market_data.get('kurtosis', 3.0),
                'skewness': market_data.get('skewness', 0.0),
                'liquidity': market_data.get('liquidity', 1.0)
            }
        }
        
        return risks
    
    def _calculate_risk_contributions(self, risks: Dict) -> Dict:
        """计算风险贡献度"""
        contributions = {}
        
        # 市场风险贡献度
        market_risk_score = (
            risks['market_risk']['var_95'] * 0.3 +
            risks['market_risk']['volatility'] * 0.3 +
            risks['market_risk']['beta'] * 0.2 +
            risks['market_risk']['max_drawdown'] * 0.2
        )
        contributions['market_risk'] = market_risk_score
        
        # 波动率风险贡献度
        volatility_risk_score = (
            risks['volatility_risk']['volatility'] * 0.4 +
            risks['volatility_risk']['vix_level'] * 0.3 +
            risks['volatility_risk']['volatility_skew'] * 0.2 +
            risks['volatility_risk']['volatility_term'] * 0.1
        )
        contributions['volatility_risk'] = volatility_risk_score
        
        # 尾部风险贡献度
        tail_risk_score = (
            risks['tail_risk']['var_99'] * 0.3 +
            risks['tail_risk']['es_95'] * 0.3 +
            risks['tail_risk']['kurtosis'] * 0.2 +
            abs(risks['tail_risk']['skewness']) * 0.1 +
            (1.0 - risks['tail_risk']['liquidity']) * 0.1
        )
        contributions['tail_risk'] = tail_risk_score
        
        # 归一化贡献度
        total_contribution = sum(contributions.values())
        if total_contribution > 0:
            contributions = {k: v / total_contribution for k, v in contributions.items()}
        
        return contributions
    
    def _adjust_risk_budgets(self, contributions: Dict, risks: Dict) -> Dict:
        """动态调整风险预算"""
        adjusted_budgets = {}
        
        for risk_type, contribution in contributions.items():
            base_budget = self.risk_budgets[risk_type]
            
            # 基于贡献度调整
            contribution_adjustment = (contribution - base_budget) * 0.5
            
            # 基于风险水平调整
            risk_level = self._get_risk_level(risks, risk_type)
            risk_adjustment = (risk_level - 0.5) * self.adjustment_params['volatility_multiplier']
            
            # 综合调整
            adjusted_budget = base_budget + contribution_adjustment + risk_adjustment
            
            # 应用上下限约束
            adjusted_budget = max(
                self.adjustment_params['min_budget_ratio'],
                min(adjusted_budget, self.adjustment_params['max_budget_ratio'])
            )
            
            adjusted_budgets[risk_type] = adjusted_budget
        
        # 处理相关性缓冲
        adjusted_budgets = self._apply_correlation_buffer(adjusted_budgets)
        
        return adjusted_budgets
    
    def _get_risk_level(self, risks: Dict, risk_type: str) -> float:
        """获取风险水平评分"""
        risk_metrics = risks[risk_type]
        
        if risk_type == 'market_risk':
            # 市场风险水平
            return np.mean([
                min(risk_metrics['var_95'] / 0.05, 1.0),
                min(risk_metrics['volatility'] / 0.3, 1.0),
                min(risk_metrics['beta'], 1.5) / 1.5,
                min(risk_metrics['max_drawdown'] / 0.15, 1.0)
            ])
        
        elif risk_type == 'volatility_risk':
            # 波动率风险水平
            return np.mean([
                min(risk_metrics['volatility'] / 0.4, 1.0),
                min(risk_metrics['vix_level'], 1.0),
                min(abs(risk_metrics['volatility_skew']) * 2, 1.0),
                min(abs(risk_metrics['volatility_term']) * 5, 1.0)
            ])
        
        elif risk_type == 'tail_risk':
            # 尾部风险水平
            return np.mean([
                min(risk_metrics['var_99'] / 0.08, 1.0),
                min(risk_metrics['es_95'] / 0.1, 1.0),
                min((risk_metrics['kurtosis'] - 3.0) * 2, 1.0),
                min(abs(risk_metrics['skewness']) * 3, 1.0),
                1.0 - risk_metrics['liquidity']
            ])
        
        return 0.5
    
    def _apply_correlation_buffer(self, budgets: Dict) -> Dict:
        """应用相关性缓冲"""
        # 简化的相关性缓冲处理
        # 确保预算之间不会过度集中
        max_budget = max(budgets.values())
        min_budget = min(budgets.values())
        
        if max_budget - min_budget > 0.5:  # 差距过大时进行平衡
            excess = (max_budget - 0.4) * 0.3  # 将超出40%的部分进行再分配
            for risk_type, budget in budgets.items():
                if budget > 0.4:
                    budgets[risk_type] -= excess
                elif budget < 0.2:
                    budgets[risk_type] += excess / 2
        
        return budgets

class MarketAdaptiveAdjuster:
    """
    市场适应调整器
    """
    
    def __init__(self, base_allocation: Dict):
        self.base_allocation = base_allocation
        self.market_regime_history = deque(maxlen=50)
        
        # 市场状态映射
        self.regime_mappings = {
            'normal': {
                'delta_hedge': 0.60,
                'volatility_hedge': 0.30,
                'tail_risk_hedge': 0.10
            },
            'warning': {
                'delta_hedge': 0.50,
                'volatility_hedge': 0.40,
                'tail_risk_hedge': 0.10
            },
            'crisis': {
                'delta_hedge': 0.40,
                'volatility_hedge': 0.30,
                'tail_risk_hedge': 0.30
            },
            'recovery': {
                'delta_hedge': 0.65,
                'volatility_hedge': 0.25,
                'tail_risk_hedge': 0.10
            }
        }
        
        # 调整参数
        self.adjustment_params = {
            'regime_switch_threshold': 0.7,     # 市场状态切换阈值
            'adaptation_speed': 0.3,            # 适应速度
            'memory_factor': 0.8,               # 记忆因子
            'volatility_impact': 0.4,           # 波动率影响
            'liquidity_impact': 0.3             # 流动性影响
        }
        
        # 当前市场状态
        self.current_regime = 'normal'
        self.current_allocation = base_allocation.copy()
        
    def adjust_allocation(self, market_data: Dict, risk_budgets: Dict) -> Dict:
        """
        基于市场数据调整资金配置
        
        Args:
            market_data: 市场数据
            risk_budgets: 风险预算分配
            
        Returns:
            调整后的资金配置
        """
        try:
            # 分析市场状态
            market_regime = self._analyze_market_regime(market_data)
            self.current_regime = market_regime
            
            # 基于市场状态获取初始配置
            regime_allocation = self.regime_mappings[market_regime].copy()
            
            # 基于风险预算调整
            risk_adjusted_allocation = self._adjust_by_risk_budgets(
                regime_allocation, risk_budgets
            )
            
            # 基于市场指标微调
            market_adjusted_allocation = self._adjust_by_market_indicators(
                risk_adjusted_allocation, market_data
            )
            
            # 应用平滑过渡
            smooth_allocation = self._apply_smoothing(
                market_adjusted_allocation
            )
            
            # 更新当前配置
            self.current_allocation = smooth_allocation.copy()
            
            # 记录历史
            self.market_regime_history.append({
                'timestamp': datetime.now().isoformat(),
                'market_regime': market_regime,
                'allocation': smooth_allocation,
                'risk_budgets': risk_budgets
            })
            
            logger.info(f"市场适应调整完成: {market_regime} -> {smooth_allocation}")
            return smooth_allocation
            
        except Exception as e:
            logger.error(f"市场适应调整失败: {e}")
            return self.base_allocation.copy()
    
    def _analyze_market_regime(self, market_data: Dict) -> str:
        """分析市场状态"""
        try:
            # 提取关键指标
            volatility = market_data.get('volatility', 0.15)
            var_95 = market_data.get('var_95', 0.02)
            liquidity = market_data.get('liquidity', 1.0)
            sentiment = market_data.get('sentiment_score', 0.0)
            
            # 计算风险评分
            risk_score = (
                min(volatility / 0.3, 1.0) * 0.3 +
                min(var_95 / 0.05, 1.0) * 0.3 +
                (1.0 - liquidity) * 0.2 +
                (1.0 - abs(sentiment)) * 0.2
            )
            
            # 确定市场状态
            if risk_score > 0.8:
                regime = 'crisis'
            elif risk_score > 0.6:
                regime = 'warning'
            elif risk_score > 0.4:
                regime = 'recovery'
            else:
                regime = 'normal'
            
            return regime
            
        except Exception as e:
            logger.error(f"市场状态分析失败: {e}")
            return 'normal'
    
    def _adjust_by_risk_budgets(self, base_allocation: Dict, risk_budgets: Dict) -> Dict:
        """基于风险预算调整配置"""
        adjusted_allocation = {}
        
        total_base = sum(base_allocation.values())
        
        for strategy, base_ratio in base_allocation.items():
            # 获取对应的风险预算
            risk_budget = risk_budgets.get(strategy, 0.0)
            
            # 计算调整比例
            if total_base > 0:
                base_weight = base_ratio / total_base
            else:
                base_weight = 1.0 / len(base_allocation)
            
            # 融合基础配置和风险预算
            adjustment_factor = 0.7  # 保留70%的基础配置，30%来自风险预算
            adjusted_ratio = (
                base_weight * adjustment_factor +
                risk_budget * (1 - adjustment_factor)
            )
            
            adjusted_allocation[strategy] = adjusted_ratio
        
        # 确保总和为1
        total_adjusted = sum(adjusted_allocation.values())
        if total_adjusted > 0:
            adjusted_allocation = {k: v / total_adjusted for k, v in adjusted_allocation.items()}
        
        return adjusted_allocation
    
    def _adjust_by_market_indicators(self, allocation: Dict, market_data: Dict) -> Dict:
        """基于市场指标调整配置"""
        adjusted_allocation = allocation.copy()
        
        # 获取市场指标
        volatility = market_data.get('volatility', 0.15)
        liquidity = market_data.get('liquidity', 1.0)
        vix_level = market_data.get('vix_future_price', 20.0) / 50.0
        
        # 波动率调整
        if volatility > 0.25:  # 高波动率
            # 增加波动率对冲，减少Delta对冲
            volatility_boost = (volatility - 0.25) * self.adjustment_params['volatility_impact']
            adjusted_allocation['volatility_hedge'] = min(
                adjusted_allocation['volatility_hedge'] + volatility_boost,
                0.6  # 最大限制
            )
            adjusted_allocation['delta_hedge'] = max(
                adjusted_allocation['delta_hedge'] - volatility_boost * 0.5,
                0.2  # 最小限制
            )
        
        # 流动性调整
        if liquidity < 0.7:  # 低流动性
            liquidity_impact = (1.0 - liquidity) * self.adjustment_params['liquidity_impact']
            adjusted_allocation['delta_hedge'] = max(
                adjusted_allocation['delta_hedge'] - liquidity_impact * 0.5,
                0.3  # 最小限制
            )
            adjusted_allocation['tail_risk_hedge'] = min(
                adjusted_allocation['tail_risk_hedge'] + liquidity_impact,
                0.4  # 最大限制
            )
        
        # VIX水平调整
        if vix_level > 0.6:  # 高VIX
            vix_adjustment = (vix_level - 0.6) * 0.2
            adjusted_allocation['volatility_hedge'] = min(
                adjusted_allocation['volatility_hedge'] + vix_adjustment,
                0.5
            )
            adjusted_allocation['tail_risk_hedge'] = min(
                adjusted_allocation['tail_risk_hedge'] + vix_adjustment * 0.5,
                0.35
            )
        
        # 确保总和为1
        total = sum(adjusted_allocation.values())
        if total > 0:
            adjusted_allocation = {k: v / total for k, v in adjusted_allocation.items()}
        
        return adjusted_allocation
    
    def _apply_smoothing(self, target_allocation: Dict) -> Dict:
        """应用平滑过渡"""
        # 基于记忆因子计算平滑配置
        memory_factor = self.adjustment_params['memory_factor']
        
        current_weights = np.array([
            self.current_allocation.get('delta_hedge', 0.0),
            self.current_allocation.get('volatility_hedge', 0.0),
            self.current_allocation.get('tail_risk_hedge', 0.0)
        ])
        
        target_weights = np.array([
            target_allocation.get('delta_hedge', 0.0),
            target_allocation.get('volatility_hedge', 0.0),
            target_allocation.get('tail_risk_hedge', 0.0)
        ])
        
        # 应用平滑
        smoothed_weights = (
            current_weights * memory_factor +
            target_weights * (1 - memory_factor)
        )
        
        # 确保总和为1
        smoothed_weights = smoothed_weights / np.sum(smoothed_weights)
        
        # 构建平滑配置
        smoothed_allocation = {
            'delta_hedge': smoothed_weights[0],
            'volatility_hedge': smoothed_weights[1],
            'tail_risk_hedge': smoothed_weights[2]
        }
        
        return smoothed_allocation

class CapitalEfficiencyMonitor:
    """
    资金使用效率监控器
    """
    
    def __init__(self):
        self.efficiency_history = deque(maxlen=100)
        self.performance_metrics = {
            'sharpe_ratio': 0.0,
            'sortino_ratio': 0.0,
            'max_drawdown': 0.0,
            'calmar_ratio': 0.0,
            'win_rate': 0.0,
            'profit_factor': 0.0
        }
        
        # 效率阈值
        self.efficiency_thresholds = {
            'excellent': 0.8,
            'good': 0.6,
            'acceptable': 0.4,
            'poor': 0.2
        }
        
    def monitor_efficiency(self, allocation: Dict, market_data: Dict, 
                          performance_data: Dict) -> Dict:
        """
        监控资金使用效率
        
        Args:
            allocation: 资金配置
            market_data: 市场数据
            performance_data: 绩效数据
            
        Returns:
            效率评估结果
        """
        try:
            # 计算效率指标
            efficiency_metrics = self._calculate_efficiency_metrics(
                allocation, market_data, performance_data
            )
            
            # 评估效率等级
            efficiency_grade = self._evaluate_efficiency(efficiency_metrics)
            
            # 生成改进建议
            improvement_suggestions = self._generate_improvement_suggestions(
                efficiency_metrics, allocation
            )
            
            # 记录历史
            self.efficiency_history.append({
                'timestamp': datetime.now().isoformat(),
                'efficiency_metrics': efficiency_metrics,
                'efficiency_grade': efficiency_grade,
                'allocation': allocation,
                'performance_data': performance_data
            })
            
            logger.info(f"资金效率监控完成: {efficiency_grade}")
            
            return {
                'efficiency_metrics': efficiency_metrics,
                'efficiency_grade': efficiency_grade,
                'improvement_suggestions': improvement_suggestions,
                'is_efficient': efficiency_grade >= 'good'
            }
            
        except Exception as e:
            logger.error(f"资金效率监控失败: {e}")
            return {
                'efficiency_metrics': {},
                'efficiency_grade': 'unknown',
                'improvement_suggestions': [],
                'is_efficient': False,
                'error': str(e)
            }
    
    def _calculate_efficiency_metrics(self, allocation: Dict, 
                                   market_data: Dict, performance_data: Dict) -> Dict:
        """计算效率指标"""
        metrics = {}
        
        # 计算风险调整收益
        returns = performance_data.get('returns', [])
        if returns:
            # 夏普比率
            if len(returns) > 1:
                excess_returns = [r - 0.03/252 for r in returns]  # 假设无风险利率3%
                sharpe_ratio = np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(252)
                metrics['sharpe_ratio'] = max(sharpe_ratio, -5.0)  # 限制最小值
            else:
                metrics['sharpe_ratio'] = 0.0
            
            # 索提诺比率
            negative_returns = [r for r in returns if r < 0]
            if negative_returns:
                downside_deviation = np.std(negative_returns)
                if downside_deviation > 0:
                    sortino_ratio = np.mean(returns) / downside_deviation * np.sqrt(252)
                    metrics['sortino_ratio'] = max(sortino_ratio, -5.0)
                else:
                    metrics['sortino_ratio'] = metrics['sharpe_ratio']
            else:
                metrics['sortino_ratio'] = metrics['sharpe_ratio']
        
        # 最大回撤
        max_drawdown = performance_data.get('max_drawdown', 0.0)
        metrics['max_drawdown'] = max_drawdown
        
        # 卡玛比率
        if max_drawdown > 0:
            metrics['calmar_ratio'] = performance_data.get('total_return', 0.0) / max_drawdown
        else:
            metrics['calmar_ratio'] = 0.0
        
        # 胜率
        winning_trades = performance_data.get('winning_trades', 0)
        total_trades = performance_data.get('total_trades', 0)
        if total_trades > 0:
            metrics['win_rate'] = winning_trades / total_trades
        else:
            metrics['win_rate'] = 0.0
        
        # 盈亏比
        gross_profit = performance_data.get('gross_profit', 0.0)
        gross_loss = performance_data.get('gross_loss', 0.0)
        if gross_loss > 0:
            metrics['profit_factor'] = gross_profit / gross_loss
        else:
            metrics['profit_factor'] = float('inf') if gross_profit > 0 else 0.0
        
        # 资金使用率
        allocated_capital = sum(allocation.values())
        metrics['capital_utilization'] = allocated_capital
        
        # 风险覆盖度
        volatility = market_data.get('volatility', 0.15)
        risk_coverage = min(metrics.get('sortino_ratio', 0.0) / 2.0, 1.0)
        metrics['risk_coverage'] = risk_coverage
        
        return metrics
    
    def _evaluate_efficiency(self, metrics: Dict) -> str:
        """评估效率等级"""
        # 综合评分
        score = 0.0
        weight = 0.0
        
        # 夏普比率 (30%)
        if 'sharpe_ratio' in metrics:
            score += min(max(metrics['sharpe_ratio'] / 2.0, 0.0), 1.0) * 0.3
            weight += 0.3
        
        # 索提诺比率 (25%)
        if 'sortino_ratio' in metrics:
            score += min(max(metrics['sortino_ratio'] / 3.0, 0.0), 1.0) * 0.25
            weight += 0.25
        
        # 胜率 (20%)
        if 'win_rate' in metrics:
            score += metrics['win_rate'] * 0.2
            weight += 0.2
        
        # 盈亏比 (15%)
        if 'profit_factor' in metrics:
            if metrics['profit_factor'] >= 2.0:
                score += 1.0 * 0.15
            elif metrics['profit_factor'] >= 1.0:
                score += metrics['profit_factor'] * 0.075
            weight += 0.15
        
        # 风险覆盖度 (10%)
        if 'risk_coverage' in metrics:
            score += metrics['risk_coverage'] * 0.1
            weight += 0.1
        
        # 归一化评分
        if weight > 0:
            score = score / weight
        
        # 确定等级
        if score >= self.efficiency_thresholds['excellent']:
            return 'excellent'
        elif score >= self.efficiency_thresholds['good']:
            return 'good'
        elif score >= self.efficiency_thresholds['acceptable']:
            return 'acceptable'
        else:
            return 'poor'
    
    def _generate_improvement_suggestions(self, metrics: Dict, 
                                       allocation: Dict) -> List[str]:
        """生成改进建议"""
        suggestions = []
        
        # 夏普比率改进
        if 'sharpe_ratio' in metrics and metrics['sharpe_ratio'] < 1.0:
            suggestions.append("提高夏普比率：考虑优化资产配置或增加低相关性资产")
        
        # 索提诺比率改进
        if 'sortino_ratio' in metrics and metrics['sortino_ratio'] < 2.0:
            suggestions.append("改善索提诺比率：加强下行保护，减少负收益交易")
        
        # 胜率改进
        if 'win_rate' in metrics and metrics['win_rate'] < 0.6:
            suggestions.append("提高胜率：优化入场时机，加强信号过滤")
        
        # 盈亏比改进
        if 'profit_factor' in metrics and metrics['profit_factor'] < 2.0:
            if metrics['profit_factor'] < 1.0:
                suggestions.append("改善盈亏比：减少亏损交易，提高止损纪律")
            else:
                suggestions.append("提高盈亏比：优化止盈策略，增加盈利交易的平均收益")
        
        # 最大回撤控制
        if 'max_drawdown' in metrics and metrics['max_drawdown'] > 0.1:
            suggestions.append("控制最大回撤：加强风险管理，考虑增加对冲比例")
        
        # 资金使用优化
        total_allocation = sum(allocation.values())
        if abs(total_allocation - 1.0) > 0.1:
            suggestions.append("优化资金使用：确保资金配置比例总和为100%")
        
        # 对策建议
        if 'risk_coverage' in metrics and metrics['risk_coverage'] < 0.7:
            suggestions.append("加强风险覆盖：增加尾部风险对冲比例")
        
        return suggestions

class DynamicCapitalManager:
    """
    动态对冲资金管理器 - 主控制器
    """
    
    def __init__(self, total_capital: float = 1000000):
        self.total_capital = total_capital
        
        # 初始化组件
        self.risk_allocator = RiskBudgetAllocator(total_capital)
        self.market_adjuster = MarketAdaptiveAdjuster({
            'delta_hedge': 0.60,
            'volatility_hedge': 0.30,
            'tail_risk_hedge': 0.10
        })
        self.efficiency_monitor = CapitalEfficiencyMonitor()
        
        # 管理历史
        self.allocation_history = deque(maxlen=100)
        self.performance_history = deque(maxlen=100)
        
        # 当前状态
        self.current_allocation = {
            'delta_hedge': 0.60,
            'volatility_hedge': 0.30,
            'tail_risk_hedge': 0.10
        }
        
        self.current_capital = {
            'delta_hedge': total_capital * 0.60,
            'volatility_hedge': total_capital * 0.30,
            'tail_risk_hedge': total_capital * 0.10,
            'reserve': total_capital * 0.05
        }
        
        logger.info(f"动态资金管理器初始化完成，总资本: {total_capital:,.0f}元")
    
    def optimize_capital_allocation(self, market_data: Dict, 
                                 performance_data: Dict) -> Dict:
        """
        优化资金配置
        
        Args:
            market_data: 市场数据
            performance_data: 绩效数据
            
        Returns:
            优化结果
        """
        try:
            logger.info("开始资金配置优化")
            
            # 1. 风险预算分配
            risk_budgets = self.risk_allocator.allocate_risk_budget(market_data)
            logger.info(f"风险预算分配: {risk_budgets}")
            
            # 2. 市场适应调整
            adjusted_allocation = self.market_adjuster.adjust_allocation(
                market_data, risk_budgets
            )
            logger.info(f"市场适应调整: {adjusted_allocation}")
            
            # 3. 资金效率监控
            efficiency_result = self.efficiency_monitor.monitor_efficiency(
                adjusted_allocation, market_data, performance_data
            )
            logger.info(f"资金效率评估: {efficiency_result['efficiency_grade']}")
            
            # 4. 综合优化决策
            optimized_allocation = self._make_optimization_decision(
                adjusted_allocation, efficiency_result
            )
            
            # 5. 更新资本配置
            self._update_capital_allocation(optimized_allocation)
            
            # 6. 记录历史
            optimization_record = {
                'timestamp': datetime.now().isoformat(),
                'market_regime': self.market_adjuster.current_regime,
                'risk_budgets': risk_budgets,
                'adjusted_allocation': adjusted_allocation,
                'optimized_allocation': optimized_allocation,
                'efficiency_result': efficiency_result,
                'performance_data': performance_data
            }
            
            self.allocation_history.append(optimization_record)
            
            logger.info("资金配置优化完成")
            
            return {
                'success': True,
                'optimized_allocation': optimized_allocation,
                'capital_allocation': self.current_capital,
                'efficiency_result': efficiency_result,
                'optimization_record': optimization_record
            }
            
        except Exception as e:
            logger.error(f"资金配置优化失败: {e}")
            return {
                'success': False,
                'error': str(e),
                'current_allocation': self.current_allocation,
                'capital_allocation': self.current_capital
            }
    
    def _make_optimization_decision(self, adjusted_allocation: Dict, 
                                   efficiency_result: Dict) -> Dict:
        """做出优化决策"""
        optimized_allocation = adjusted_allocation.copy()
        
        # 如果效率较差，进行优化调整
        if efficiency_result['efficiency_grade'] in ['poor', 'acceptable']:
            suggestions = efficiency_result['improvement_suggestions']
            
            for suggestion in suggestions:
                # 根据建议调整配置
                if '夏普比率' in suggestion and '增加低相关性资产' in suggestion:
                    # 增加尾部风险对冲（低相关性）
                    optimized_allocation['tail_risk_hedge'] = min(
                        optimized_allocation['tail_risk_hedge'] + 0.05,
                        0.20
                    )
                    optimized_allocation['delta_hedge'] = max(
                        optimized_allocation['delta_hedge'] - 0.05,
                        0.40
                    )
                
                elif '下行保护' in suggestion:
                    # 增加波动率对冲
                    optimized_allocation['volatility_hedge'] = min(
                        optimized_allocation['volatility_hedge'] + 0.05,
                        0.40
                    )
                    optimized_allocation['delta_hedge'] = max(
                        optimized_allocation['delta_hedge'] - 0.05,
                        0.40
                    )
                
                elif '最大回撤' in suggestion:
                    # 增加尾部风险对冲
                    optimized_allocation['tail_risk_hedge'] = min(
                        optimized_allocation['tail_risk_hedge'] + 0.10,
                        0.30
                    )
                    # 减少Delta对冲
                    optimized_allocation['delta_hedge'] = max(
                        optimized_allocation['delta_hedge'] - 0.10,
                        0.30
                    )
        
        # 确保总和为1
        total = sum(optimized_allocation.values())
        if total > 0:
            optimized_allocation = {k: v / total for k, v in optimized_allocation.items()}
        
        return optimized_allocation
    
    def _update_capital_allocation(self, allocation: Dict):
        """更新资本配置"""
        self.current_allocation = allocation.copy()
        
        # 计算各策略资本
        allocated_total = sum(allocation.values())
        reserve_ratio = 1.0 - allocated_total
        
        for strategy, ratio in allocation.items():
            self.current_capital[strategy] = self.total_capital * ratio / allocated_total
        
        self.current_capital['reserve'] = self.total_capital * reserve_ratio
        
        logger.info(f"资本配置更新完成: {self.current_capital}")
    
    def get_allocation_summary(self) -> Dict:
        """获取配置总结"""
        try:
            # 最近配置分析
            recent_allocations = list(self.allocation_history)[-10:]
            
            # 平均配置
            if recent_allocations:
                avg_allocation = {}
                for strategy in self.current_allocation.keys():
                    values = [a['optimized_allocation'].get(strategy, 0) for a in recent_allocations]
                    avg_allocation[strategy] = np.mean(values)
            else:
                avg_allocation = self.current_allocation.copy()
            
            # 配置稳定性
            if len(recent_allocations) > 1:
                allocation_changes = []
                for i in range(1, len(recent_allocations)):
                    change = sum(
                        abs(recent_allocations[i]['optimized_allocation'].get(k, 0) - 
                            recent_allocations[i-1]['optimized_allocation'].get(k, 0))
                        for k in self.current_allocation.keys()
                    )
                    allocation_changes.append(change)
                
                avg_change = np.mean(allocation_changes)
                stability = 1.0 - min(avg_change, 1.0)
            else:
                stability = 1.0
            
            # 效率趋势
            efficiency_trend = []
            for record in recent_allocations:
                if 'efficiency_result' in record:
                    grade = record['efficiency_result']['efficiency_grade']
                    score = {'excellent': 1.0, 'good': 0.8, 'acceptable': 0.6, 'poor': 0.4}.get(grade, 0.0)
                    efficiency_trend.append(score)
            
            avg_efficiency = np.mean(efficiency_trend) if efficiency_trend else 0.0
            
            return {
                'current_allocation': self.current_allocation,
                'current_capital': self.current_capital,
                'average_allocation': avg_allocation,
                'allocation_stability': stability,
                'average_efficiency': avg_efficiency,
                'total_allocations': len(self.allocation_history),
                'market_regime': self.market_adjuster.current_regime,
                'last_update': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"配置总结生成失败: {e}")
            return {'error': str(e)}
    
    def run_simulation(self) -> Dict:
        """运行资金管理模拟"""
        try:
            logger.info("开始动态资金管理模拟")
            
            # 模拟市场数据
            market_data = {
                'index_price': 3000,
                'volatility': 0.15,
                'var_95': 0.02,
                'var_99': 0.035,
                'es_95': 0.03,
                'beta': 1.0,
                'liquidity': 1.0,
                'vix_future_price': 20.0,
                'sentiment_score': 0.2,
                'kurtosis': 3.0,
                'skewness': 0.1,
                'volatility_skew': 0.0,
                'volatility_term': 0.0
            }
            
            # 模拟绩效数据
            performance_data = {
                'returns': [0.001, -0.002, 0.003, -0.001, 0.002, 0.001, -0.003, 0.004, -0.002, 0.005],
                'total_return': 0.05,
                'max_drawdown': 0.08,
                'winning_trades': 6,
                'total_trades': 10,
                'gross_profit': 10000,
                'gross_loss': 6000
            }
            
            # 测试不同市场情况
            test_scenarios = [
                {'name': '正常市场', 'data': market_data.copy()},
                {'name': '高波动市场', 'data': self._create_high_volatility_scenario(market_data)},
                {'name': '危机市场', 'data': self._create_crisis_scenario(market_data)},
                {'name': '流动性危机', 'data': self._create_liquidity_crisis_scenario(market_data)}
            ]
            
            results = []
            
            for scenario in test_scenarios:
                logger.info(f"测试场景: {scenario['name']}")
                
                # 执行资金优化
                optimization_result = self.optimize_capital_allocation(
                    scenario['data'], performance_data
                )
                
                result = {
                    'scenario': scenario['name'],
                    'optimization_result': optimization_result,
                    'market_data': scenario['data']
                }
                results.append(result)
            
            # 生成总结报告
            summary = self.get_allocation_summary()
            
            logger.info("动态资金管理模拟完成")
            
            return {
                'success': True,
                'simulation_results': results,
                'allocation_summary': summary,
                'conclusions': self._generate_simulation_conclusions(results)
            }
            
        except Exception as e:
            logger.error(f"资金管理模拟失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def _create_high_volatility_scenario(self, base_data: Dict) -> Dict:
        """创建高波动率场景"""
        high_vol_data = base_data.copy()
        high_vol_data.update({
            'volatility': 0.35,
            'var_95': 0.08,
            'var_99': 0.15,
            'es_95': 0.12,
            'vix_future_price': 40.0
        })
        return high_vol_data
    
    def _create_crisis_scenario(self, base_data: Dict) -> Dict:
        """创建危机场景"""
        crisis_data = base_data.copy()
        crisis_data.update({
            'volatility': 0.50,
            'var_95': 0.15,
            'var_99': 0.25,
            'es_95': 0.20,
            'beta': 1.5,
            'liquidity': 0.3,
            'vix_future_price': 60.0,
            'sentiment_score': -0.8,
            'kurtosis': 8.0,
            'skewness': -0.5
        })
        return crisis_data
    
    def _create_liquidity_crisis_scenario(self, base_data: Dict) -> Dict:
        """创建流动性危机场景"""
        liquidity_data = base_data.copy()
        liquidity_data.update({
            'volatility': 0.40,
            'var_95': 0.12,
            'var_99': 0.20,
            'beta': 1.2,
            'liquidity': 0.2,
            'vix_future_price': 45.0,
            'sentiment_score': -0.6
        })
        return liquidity_data
    
    def _generate_simulation_conclusions(self, results: List[Dict]) -> List[str]:
        """生成模拟结论"""
        conclusions = []
        
        # 分析各场景的优化效果
        for result in results:
            scenario = result['scenario']
            optimization_result = result['optimization_result']
            
            if optimization_result['success']:
                allocation = optimization_result['optimized_allocation']
                efficiency = optimization_result['efficiency_result']['efficiency_grade']
                
                conclusions.append(
                    f"{scenario}: 配置优化成功，效率评级={efficiency}, "
                    f"Delta={allocation['delta_hedge']:.2%}, "
                    f"Volatility={allocation['volatility_hedge']:.2%}, "
                    f"Tail={allocation['tail_risk_hedge']:.2%}"
                )
            else:
                conclusions.append(f"{scenario}: 优化失败 - {optimization_result['error']}")
        
        # 分析配置适应性
        allocations = [r['optimization_result']['optimized_allocation'] 
                     for r in results if r['optimization_result']['success']]
        
        if allocations:
            # 计算配置变化
            delta_changes = [a['delta_hedge'] for a in allocations]
            vol_changes = [a['volatility_hedge'] for a in allocations]
            tail_changes = [a['tail_risk_hedge'] for a in allocations]
            
            avg_delta = np.mean(delta_changes)
            avg_vol = np.mean(vol_changes)
            avg_tail = np.mean(tail_changes)
            
            conclusions.append(f"平均配置: Delta={avg_delta:.2%}, Volatility={avg_vol:.2%}, Tail={avg_tail:.2%}")
            
            # 评估配置变化幅度
            delta_std = np.std(delta_changes)
            vol_std = np.std(vol_changes)
            tail_std = np.std(tail_changes)
            
            avg_std = (delta_std + vol_std + tail_std) / 3
            
            if avg_std > 0.15:
                conclusions.append("配置变化较大，适应性强")
            else:
                conclusions.append("配置变化较小，配置相对稳定")
        
        return conclusions


# 主程序
if __name__ == "__main__":
    print("动态对冲资金管理器启动")
    print("=" * 50)
    
    # 创建动态资金管理器
    capital_manager = DynamicCapitalManager(total_capital=1000000)
    
    # 运行模拟
    simulation_result = capital_manager.run_simulation()
    
    print("\n模拟完成")
    print("=" * 50)
    
    # 输出结果
    if simulation_result['success']:
        print("模拟结果:")
        for conclusion in simulation_result['conclusions']:
            print(f"  - {conclusion}")
        
        print("\n配置总结:")
        summary = simulation_result['allocation_summary']
        print(f"  当前配置: {summary['current_allocation']}")
        print(f"  平均效率: {summary['average_efficiency']:.3f}")
        print(f"  配置稳定性: {summary['allocation_stability']:.3f}")
        print(f"  市场状态: {summary['market_regime']}")
    else:
        print(f"模拟失败: {simulation_result['error']}")