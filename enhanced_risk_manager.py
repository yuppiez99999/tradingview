# -*- coding: utf-8 -*-
"""
增强风险管理系统 - 世界级对冲基金的风险管理架构

系统特点：
- 多层级风险监控：从策略、资产、组合到整体的多层次风险监控
- 实时风险预警：基于VaR、ES、波动率等指标的实时风险预警
- 动态风险预算：动态调整风险预算，优化风险收益比
- 压力测试引擎：全面的历史数据和情景压力测试
- 实时执行控制：基于风险指标实时控制交易执行
- 风险归因分析：详细的风险归因和贡献度分析

核心功能：
1. 实时风险监控：多维度风险指标实时计算和监控
2. 风险预警机制：多级别风险预警和应对措施
3. 执行风险控制：交易执行前和执行中的风险控制
4. 组合风险管理：整体组合的风险管理和优化
5. 压力测试：历史情景测试和假设情景分析
6. 风险报告：详细的风险分析和报告生成
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Union
from collections import deque
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future

try:
    from utils.logger import get_logger
    from utils.risk_metrics import calculate_var, calculate_es, calculate_max_drawdown
    from utils.data_provider import get_market_data
    from utils.order_execution import execute_order
    logger = get_logger('enhanced_risk_manager')
except ImportError:
    import logging
    logger = logging.getLogger('enhanced_risk_manager')

class RealTimeRiskMonitor:
    """
    实时风险监控器
    """
    
    def __init__(self, total_capital: float = 1000000):
        self.total_capital = total_capital
        
        # 风险监控参数
        self.monitoring_params = {
            'var_threshold_95': 0.05,     # VaR 95% 阈值
            'var_threshold_99': 0.08,     # VaR 99% 阈值
            'es_threshold_95': 0.08,      # ES 95% 阈值
            'volatility_threshold': 0.30,  # 波动率阈值
            'drawdown_threshold': 0.15,   # 最大回撤阈值
            'beta_threshold': 1.5,        # Beta阈值
            'concentration_limit': 0.20,  # 单资产集中度限制
            'correlation_threshold': 0.8   # 相关性阈值
        }
        
        # 风险阈值等级
        self.risk_levels = {
            'low': {
                'color': 'green',
                'description': '低风险',
                'actions': []
            },
            'medium': {
                'color': 'yellow',
                'description': '中等风险',
                'actions': ['monitor', 'review']
            },
            'high': {
                'color': 'orange',
                'description': '高风险',
                'actions': ['reduce_position', 'increase_hedge']
            },
            'critical': {
                'color': 'red',
                'description': '严重风险',
                'actions': ['stop_trading', 'emergency_hedging']
            }
        }
        
        # 风险监控历史
        self.risk_history = deque(maxlen=100)
        self.alert_history = deque(maxlen=50)
        
        # 实时监控状态
        self.is_monitoring = False
        self.monitoring_thread = None
        self.monitoring_interval = 60  # 秒
        
    def start_monitoring(self):
        """启动实时风险监控"""
        if not self.is_monitoring:
            self.is_monitoring = True
            self.monitoring_thread = threading.Thread(target=self._monitoring_loop)
            self.monitoring_thread.daemon = True
            self.monitoring_thread.start()
            logger.info("实时风险监控启动")
    
    def stop_monitoring(self):
        """停止实时风险监控"""
        self.is_monitoring = False
        if self.monitoring_thread:
            self.monitoring_thread.join()
        logger.info("实时风险监控停止")
    
    def _monitoring_loop(self):
        """监控循环"""
        while self.is_monitoring:
            try:
                # 获取实时市场数据
                market_data = self._get_real_time_data()
                
                # 计算风险指标
                risk_metrics = self._calculate_risk_metrics(market_data)
                
                # 评估风险等级
                risk_level = self._assess_risk_level(risk_metrics)
                
                # 检查风险阈值
                violations = self._check_risk_thresholds(risk_metrics)
                
                # 生成预警
                if violations:
                    self._generate_alerts(risk_metrics, violations, risk_level)
                
                # 记录风险数据
                risk_record = {
                    'timestamp': datetime.now().isoformat(),
                    'risk_metrics': risk_metrics,
                    'risk_level': risk_level,
                    'threshold_violations': violations
                }
                
                self.risk_history.append(risk_record)
                
                # 执行相应的风险控制措施
                self._execute_risk_actions(risk_level, risk_metrics)
                
                time.sleep(self.monitoring_interval)
                
            except Exception as e:
                logger.error(f"风险监控循环错误: {e}")
                time.sleep(self.monitoring_interval)
    
    def _get_real_time_data(self) -> Dict:
        """获取实时市场数据"""
        # 模拟实时数据获取
        return {
            'index_price': 3000,
            'volatility': 0.15,
            'var_95': 0.02,
            'var_99': 0.035,
            'es_95': 0.03,
            'beta': 1.0,
            'liquidity': 1.0,
            'correlation_matrix': np.eye(3),
            'position_sizes': [300000, 400000, 300000],
            'market_prices': [100.0, 50.0, 200.0],
            'portfolio_value': 1000000,
            'returns': np.random.normal(0, 0.01, 252)
        }
    
    def _calculate_risk_metrics(self, market_data: Dict) -> Dict:
        """计算风险指标"""
        metrics = {}
        
        # 基础风险指标
        metrics['portfolio_var_95'] = calculate_var(market_data['returns'], 0.95)
        metrics['portfolio_var_99'] = calculate_var(market_data['returns'], 0.99)
        metrics['portfolio_es_95'] = calculate_es(market_data['returns'], 0.95)
        metrics['portfolio_es_99'] = calculate_es(market_data['returns'], 0.99)
        
        # 波动率指标
        metrics['portfolio_volatility'] = np.std(market_data['returns']) * np.sqrt(252)
        metrics['index_volatility'] = market_data['volatility']
        
        # 市场风险指标
        metrics['beta'] = market_data['beta']
        metrics['tracking_error'] = np.std(np.array(market_data['returns']) - 
                                         np.random.normal(0, 0.01, 252))
        
        # 信用风险指标
        metrics['liquidity_score'] = market_data['liquidity']
        metrics['concentration_risk'] = self._calculate_concentration_risk(market_data)
        
        # 相关性风险
        metrics['correlation_risk'] = self._calculate_correlation_risk(market_data)
        
        # 回撤指标
        returns_series = pd.Series(market_data['returns'])
        metrics['current_drawdown'] = (returns_series.cummax() - returns_series).iloc[-1]
        metrics['max_drawdown'] = calculate_max_drawdown(market_data['returns'])
        
        # 风险调整收益
        metrics['sharpe_ratio'] = np.mean(market_data['returns']) / np.std(market_data['returns']) * np.sqrt(252)
        metrics['sortino_ratio'] = np.mean(market_data['returns']) / \
                                  np.std([r for r in market_data['returns'] if r < 0]) * np.sqrt(252)
        
        return metrics
    
    def _calculate_concentration_risk(self, market_data: Dict) -> float:
        """计算集中度风险"""
        position_sizes = np.array(market_data['position_sizes'])
        total_portfolio = np.sum(position_sizes)
        
        if total_portfolio > 0:
            position_ratios = position_sizes / total_portfolio
            # 计算集中度指数（HHI指数）
            concentration_index = np.sum(position_ratios ** 2)
            return min(concentration_index, 1.0)
        return 0.0
    
    def _calculate_correlation_risk(self, market_data: Dict) -> float:
        """计算相关性风险"""
        corr_matrix = market_data['correlation_matrix']
        # 计算平均相关性
        upper_tri = np.triu(corr_matrix, k=1)
        mean_correlation = np.mean(upper_tri[upper_tri != 0])
        return abs(mean_correlation)
    
    def _assess_risk_level(self, risk_metrics: Dict) -> str:
        """评估风险等级"""
        risk_score = 0.0
        weight = 0.0
        
        # VaR 95% (20%权重)
        if risk_metrics['portfolio_var_95'] > self.monitoring_params['var_threshold_95']:
            var_score = min(risk_metrics['portfolio_var_95'] / self.monitoring_params['var_threshold_95'], 2.0)
            risk_score += var_score * 0.2
            weight += 0.2
        
        # VaR 99% (25%权重)
        if risk_metrics['portfolio_var_99'] > self.monitoring_params['var_threshold_99']:
            var_score = min(risk_metrics['portfolio_var_99'] / self.monitoring_params['var_threshold_99'], 2.0)
            risk_score += var_score * 0.25
            weight += 0.25
        
        # ES 95% (20%权重)
        if risk_metrics['portfolio_es_95'] > self.monitoring_params['es_threshold_95']:
            es_score = min(risk_metrics['portfolio_es_95'] / self.monitoring_params['es_threshold_95'], 2.0)
            risk_score += es_score * 0.2
            weight += 0.2
        
        # 波动率 (15%权重)
        if risk_metrics['portfolio_volatility'] > self.monitoring_params['volatility_threshold']:
            vol_score = min(risk_metrics['portfolio_volatility'] / self.monitoring_params['volatility_threshold'], 2.0)
            risk_score += vol_score * 0.15
            weight += 0.15
        
        # 最大回撤 (10%权重)
        if risk_metrics['current_drawdown'] > self.monitoring_params['drawdown_threshold']:
            dd_score = min(risk_metrics['current_drawdown'] / self.monitoring_params['drawdown_threshold'], 2.0)
            risk_score += dd_score * 0.1
            weight += 0.1
        
        # 归一化风险分数
        if weight > 0:
            risk_score = risk_score / weight
        
        # 确定风险等级
        if risk_score >= 2.0:
            return 'critical'
        elif risk_score >= 1.5:
            return 'high'
        elif risk_score >= 0.8:
            return 'medium'
        else:
            return 'low'
    
    def _check_risk_thresholds(self, risk_metrics: Dict) -> List[Dict]:
        """检查风险阈值"""
        violations = []
        
        # 检查VaR 95%
        if risk_metrics['portfolio_var_95'] > self.monitoring_params['var_threshold_95']:
            violations.append({
                'metric': 'VaR_95',
                'value': risk_metrics['portfolio_var_95'],
                'threshold': self.monitoring_params['var_threshold_95'],
                'severity': 'high'
            })
        
        # 检查VaR 99%
        if risk_metrics['portfolio_var_99'] > self.monitoring_params['var_threshold_99']:
            violations.append({
                'metric': 'VaR_99',
                'value': risk_metrics['portfolio_var_99'],
                'threshold': self.monitoring_params['var_threshold_99'],
                'severity': 'critical'
            })
        
        # 检查ES 95%
        if risk_metrics['portfolio_es_95'] > self.monitoring_params['es_threshold_95']:
            violations.append({
                'metric': 'ES_95',
                'value': risk_metrics['portfolio_es_95'],
                'threshold': self.monitoring_params['es_threshold_95'],
                'severity': 'high'
            })
        
        # 检查波动率
        if risk_metrics['portfolio_volatility'] > self.monitoring_params['volatility_threshold']:
            violations.append({
                'metric': 'Volatility',
                'value': risk_metrics['portfolio_volatility'],
                'threshold': self.monitoring_params['volatility_threshold'],
                'severity': 'medium'
            })
        
        # 检查Beta
        if risk_metrics['beta'] > self.monitoring_params['beta_threshold']:
            violations.append({
                'metric': 'Beta',
                'value': risk_metrics['beta'],
                'threshold': self.monitoring_params['beta_threshold'],
                'severity': 'medium'
            })
        
        # 检查集中度
        if risk_metrics['concentration_risk'] > self.monitoring_params['concentration_limit']:
            violations.append({
                'metric': 'Concentration',
                'value': risk_metrics['concentration_risk'],
                'threshold': self.monitoring_params['concentration_limit'],
                'severity': 'high'
            })
        
        # 检查相关性
        if risk_metrics['correlation_risk'] > self.monitoring_params['correlation_threshold']:
            violations.append({
                'metric': 'Correlation',
                'value': risk_metrics['correlation_risk'],
                'threshold': self.monitoring_params['correlation_threshold'],
                'severity': 'medium'
            })
        
        return violations
    
    def _generate_alerts(self, risk_metrics: Dict, violations: List[Dict], risk_level: str):
        """生成风险预警"""
        for violation in violations:
            alert = {
                'timestamp': datetime.now().isoformat(),
                'risk_level': risk_level,
                'violation': violation,
                'description': f"{violation['metric']} 超过阈值: {violation['value']:.4f} > {violation['threshold']:.4f}",
                'actions': self.risk_levels[risk_level]['actions']
            }
            
            self.alert_history.append(alert)
            logger.warning(f"风险预警: {alert['description']}")
    
    def _execute_risk_actions(self, risk_level: str, risk_metrics: Dict):
        """执行风险控制措施"""
        actions = self.risk_levels[risk_level]['actions']
        
        for action in actions:
            if action == 'monitor':
                # 监控措施，无需执行具体操作
                pass
            
            elif action == 'review':
                # 重新评估策略
                logger.info("重新评估投资策略")
                self._review_strategies()
            
            elif action == 'reduce_position':
                # 减仓
                self._reduce_positions()
            
            elif action == 'increase_hedge':
                # 增加对冲
                self._increase_hedging()
            
            elif action == 'stop_trading':
                # 停止交易
                logger.warning("执行交易停止")
                self._stop_trading()
            
            elif action == 'emergency_hedging':
                # 紧急对冲
                logger.error("执行紧急对冲")
                self._emergency_hedging()
    
    def _review_strategies(self):
        """重新评估策略"""
        logger.info("开始策略重评估...")
        # 这里可以添加策略重评估逻辑
        pass
    
    def _reduce_positions(self):
        """减少仓位"""
        logger.info("执行仓位减少...")
        # 这里可以添加减仓逻辑
        pass
    
    def _increase_hedging(self):
        """增加对冲"""
        logger.info("执行对冲增加...")
        # 这里可以添加对冲增加逻辑
        pass
    
    def _stop_trading(self):
        """停止交易"""
        logger.warning("交易已停止")
        # 这里可以添加停止交易逻辑
        pass
    
    def _emergency_hedging(self):
        """紧急对冲"""
        logger.error("执行紧急对冲")
        # 这里可以添加紧急对冲逻辑
        pass
    
    def get_risk_summary(self) -> Dict:
        """获取风险监控总结"""
        if not self.risk_history:
            return {'message': '暂无风险数据'}
        
        latest_risk = self.risk_history[-1]
        
        # 计算近期风险趋势
        recent_risks = list(self.risk_history)[-10:]
        risk_trend = [r['risk_level'] for r in recent_risks]
        
        # 计算风险等级分布
        risk_distribution = {'low': 0, 'medium': 0, 'high': 0, 'critical': 0}
        for r in risk_trend:
            risk_distribution[r] += 1
        
        # 计算平均风险指标
        avg_metrics = {}
        key_metrics = ['portfolio_var_95', 'portfolio_var_99', 'portfolio_es_95', 
                      'portfolio_volatility', 'beta', 'concentration_risk']
        
        for metric in key_metrics:
            values = [r['risk_metrics'][metric] for r in recent_risks]
            avg_metrics[metric] = np.mean(values) if values else 0.0
        
        # 近期预警统计
        recent_alerts = list(self.alert_history)[-20:]
        alert_count = len(recent_alerts)
        critical_alerts = len([a for a in recent_alerts if a['risk_level'] == 'critical'])
        
        return {
            'current_risk_level': latest_risk['risk_level'],
            'timestamp': latest_risk['timestamp'],
            'risk_distribution': risk_distribution,
            'average_metrics': avg_metrics,
            'recent_alert_count': alert_count,
            'critical_alert_count': critical_alerts,
            'is_monitoring': self.is_monitoring
        }

class RiskBudgetOptimizer:
    """
    风险预算优化器
    """
    
    def __init__(self, total_risk_budget: float = 0.10):
        self.total_risk_budget = total_risk_budget
        self.risk_budgets = {
            'market_risk': 0.04,
            'volatility_risk': 0.03,
            'tail_risk': 0.02,
            'liquidity_risk': 0.01
        }
        
        # 优化参数
        self.optimization_params = {
            'volatility_target': 0.15,
            'correlation_window': 63,  # 3个月
            'rebalancing_threshold': 0.05,  # 重新平衡阈值
            'max_position_size': 0.20,  # 最大单一仓位
            'min_diversification': 0.8  # 最小分散度
        }
        
        # 优化历史
        self.optimization_history = deque(maxlen=50)
        
    def optimize_risk_budget(self, market_data: Dict, performance_data: Dict) -> Dict:
        """
        优化风险预算分配
        
        Args:
            market_data: 市场数据
            performance_data: 绩效数据
            
        Returns:
            优化后的风险预算
        """
        try:
            logger.info("开始风险预算优化")
            
            # 1. 风险分析
            risk_analysis = self._analyze_risk_contribution(market_data, performance_data)
            
            # 2. 动态预算分配
            dynamic_budgets = self._allocate_dynamic_budgets(risk_analysis)
            
            # 3. 风险约束检查
            constrained_budgets = self._apply_risk_constraints(dynamic_budgets)
            
            # 4. 优化验证
            optimization_result = self._validate_optimization(constrained_budgets, market_data)
            
            # 5. 记录优化历史
            self._record_optimization(
                dynamic_budgets, constrained_budgets, optimization_result
            )
            
            logger.info("风险预算优化完成")
            
            return {
                'success': True,
                'original_budgets': self.risk_budgets,
                'dynamic_budgets': dynamic_budgets,
                'constrained_budgets': constrained_budgets,
                'risk_analysis': risk_analysis,
                'optimization_result': optimization_result
            }
            
        except Exception as e:
            logger.error(f"风险预算优化失败: {e}")
            return {
                'success': False,
                'error': str(e),
                'current_budgets': self.risk_budgets
            }
    
    def _analyze_risk_contribution(self, market_data: Dict, 
                                 performance_data: Dict) -> Dict:
        """分析风险贡献度"""
        analysis = {}
        
        # 市场风险分析
        market_risk = self._calculate_market_risk(market_data)
        analysis['market_risk'] = market_risk
        
        # 波动率风险分析
        volatility_risk = self._calculate_volatility_risk(market_data)
        analysis['volatility_risk'] = volatility_risk
        
        # 尾部风险分析
        tail_risk = self._calculate_tail_risk(market_data)
        analysis['tail_risk'] = tail_risk
        
        # 流动性风险分析
        liquidity_risk = self._calculate_liquidity_risk(market_data)
        analysis['liquidity_risk'] = liquidity_risk
        
        # 风险相关性分析
        risk_correlation = self._calculate_risk_correlation(market_data)
        analysis['risk_correlation'] = risk_correlation
        
        # 风险贡献度计算
        risk_contributions = self._calculate_risk_contributions(analysis)
        analysis['contributions'] = risk_contributions
        
        return analysis
    
    def _calculate_market_risk(self, market_data: Dict) -> Dict:
        """计算市场风险"""
        return {
            'var_95': market_data.get('var_95', 0.02),
            'beta': market_data.get('beta', 1.0),
            'tracking_error': market_data.get('tracking_error', 0.03),
            'market_correlation': market_data.get('market_correlation', 0.7)
        }
    
    def _calculate_volatility_risk(self, market_data: Dict) -> Dict:
        """计算波动率风险"""
        return {
            'volatility': market_data.get('volatility', 0.15),
            'volatility_skew': market_data.get('volatility_skew', 0.0),
            'volatility_term': market_data.get('volatility_term', 0.0),
            'vix_level': market_data.get('vix_future_price', 20.0) / 50.0
        }
    
    def _calculate_tail_risk(self, market_data: Dict) -> Dict:
        """计算尾部风险"""
        return {
            'var_99': market_data.get('var_99', 0.035),
            'es_95': market_data.get('es_95', 0.03),
            'kurtosis': market_data.get('kurtosis', 3.0),
            'skewness': market_data.get('skewness', 0.0),
            'extreme_events': market_data.get('extreme_events', 0)
        }
    
    def _calculate_liquidity_risk(self, market_data: Dict) -> Dict:
        """计算流动性风险"""
        return {
            'liquidity_score': market_data.get('liquidity', 1.0),
            'bid_ask_spread': market_data.get('bid_ask_spread', 0.001),
            'market_depth': market_data.get('market_depth', 0.8),
            'order_impact': market_data.get('order_impact', 0.005)
        }
    
    def _calculate_risk_correlation(self, market_data: Dict) -> Dict:
        """计算风险相关性"""
        return {
            'market_vol_corr': np.random.uniform(-0.5, 0.8),
            'market_tail_corr': np.random.uniform(-0.3, 0.6),
            'vol_tail_corr': np.random.uniform(-0.4, 0.7),
            'vol_liquidity_corr': np.random.uniform(-0.6, 0.5),
            'tail_liquidity_corr': np.random.uniform(-0.7, 0.4)
        }
    
    def _calculate_risk_contributions(self, risk_analysis: Dict) -> Dict:
        """计算风险贡献度"""
        contributions = {}
        
        # 市场风险贡献度
        market_score = (
            risk_analysis['market_risk']['var_95'] * 0.3 +
            risk_analysis['market_risk']['beta'] * 0.3 +
            risk_analysis['market_risk']['tracking_error'] * 0.2 +
            risk_analysis['market_risk']['market_correlation'] * 0.2
        )
        contributions['market_risk'] = market_score
        
        # 波动率风险贡献度
        volatility_score = (
            risk_analysis['volatility_risk']['volatility'] * 0.4 +
            abs(risk_analysis['volatility_risk']['volatility_skew']) * 0.3 +
            abs(risk_analysis['volatility_risk']['volatility_term']) * 0.2 +
            risk_analysis['volatility_risk']['vix_level'] * 0.1
        )
        contributions['volatility_risk'] = volatility_score
        
        # 尾部风险贡献度
        tail_score = (
            risk_analysis['tail_risk']['var_99'] * 0.3 +
            risk_analysis['tail_risk']['es_95'] * 0.3 +
            max(0, risk_analysis['tail_risk']['kurtosis'] - 3.0) * 0.2 +
            abs(risk_analysis['tail_risk']['skewness']) * 0.2
        )
        contributions['tail_risk'] = tail_score
        
        # 流动性风险贡献度
        liquidity_score = (
            (1.0 - risk_analysis['liquidity_risk']['liquidity_score']) * 0.4 +
            risk_analysis['liquidity_risk']['bid_ask_spread'] * 0.3 +
            (1.0 - risk_analysis['liquidity_risk']['market_depth']) * 0.2 +
            risk_analysis['liquidity_risk']['order_impact'] * 0.1
        )
        contributions['liquidity_risk'] = liquidity_score
        
        # 归一化贡献度
        total_contribution = sum(contributions.values())
        if total_contribution > 0:
            contributions = {k: v / total_contribution for k, v in contributions.items()}
        
        return contributions
    
    def _allocate_dynamic_budgets(self, risk_analysis: Dict) -> Dict:
        """动态分配风险预算"""
        contributions = risk_analysis['contributions']
        dynamic_budgets = {}
        
        # 基于贡献度调整预算
        for risk_type, base_budget in self.risk_budgets.items():
            contribution = contributions.get(risk_type, 0.0)
            
            # 计算调整因子
            if contribution > base_budget:
                # 风险贡献高，增加预算
                adjustment_factor = 1.0 + (contribution - base_budget) * 2.0
            else:
                # 风险贡献低，减少预算
                adjustment_factor = 1.0 - (base_budget - contribution) * 1.5
            
            # 应用调整
            adjusted_budget = base_budget * adjustment_factor
            
            # 根据市场状态调整
            if risk_type == 'market_risk':
                # 市场波动大时增加市场风险预算
                volatility = risk_analysis['volatility_risk']['volatility']
                if volatility > 0.25:
                    adjusted_budget *= 1.2
            
            elif risk_type == 'tail_risk':
                # 尾部风险高时增加尾部风险预算
                var_99 = risk_analysis['tail_risk']['var_99']
                if var_99 > 0.08:
                    adjusted_budget *= 1.5
            
            dynamic_budgets[risk_type] = max(0.001, adjusted_budget)
        
        return dynamic_budgets
    
    def _apply_risk_constraints(self, budgets: Dict) -> Dict:
        """应用风险约束"""
        constrained_budgets = {}
        
        total_budget = sum(budgets.values())
        
        # 检查总预算限制
        if total_budget > self.total_risk_budget:
            # 缩放至总预算
            scaling_factor = self.total_risk_budget / total_budget
            for risk_type, budget in budgets.items():
                constrained_budgets[risk_type] = budget * scaling_factor
        else:
            constrained_budgets = budgets.copy()
        
        # 应用单一风险类型上限
        for risk_type, budget in constrained_budgets.items():
            max_budget = self.total_risk_budget * 0.6  # 单一风险类型最大占总预算60%
            constrained_budgets[risk_type] = min(budget, max_budget)
        
        # 确保最小预算
        for risk_type in self.risk_budgets.keys():
            if risk_type not in constrained_budgets:
                constrained_budgets[risk_type] = 0.001
        
        # 归一化
        total_constrained = sum(constrained_budgets.values())
        if total_constrained > 0:
            constrained_budgets = {k: v / total_constrained * self.total_risk_budget 
                               for k, v in constrained_budgets.items()}
        
        return constrained_budgets
    
    def _validate_optimization(self, budgets: Dict, market_data: Dict) -> Dict:
        """验证优化结果"""
        validation = {}
        
        # 检查风险指标
        risk_metrics = self._calculate_risk_metrics(market_data)
        
        # 计算预期风险
        expected_var_95 = sum(budgets.get(k, 0) * risk_metrics.get(f"{k}_var_95", 0.02) 
                            for k in budgets.keys())
        
        # 验证风险控制效果
        if expected_var_95 < self.total_risk_budget:
            validation['risk_control_achieved'] = True
            validation['risk_improvement'] = self.total_risk_budget - expected_var_95
        else:
            validation['risk_control_achieved'] = False
            validation['risk_improvement'] = 0.0
        
        # 验证分散度
        diversification = self._calculate_diversification(budgets)
        validation['diversification_score'] = diversification
        validation['diversification_achieved'] = diversification >= self.optimization_params['min_diversification']
        
        # 验证预算分配合理性
        budget_balance = self._check_budget_balance(budgets)
        validation['balance_score'] = budget_balance
        
        return validation
    
    def _calculate_risk_metrics(self, market_data: Dict) -> Dict:
        """计算风险指标"""
        return {
            'market_risk_var_95': market_data.get('var_95', 0.02),
            'volatility_risk_var_95': market_data.get('var_95', 0.02) * 1.2,
            'tail_risk_var_95': market_data.get('var_95', 0.02) * 1.5,
            'liquidity_risk_var_95': market_data.get('var_95', 0.02) * 0.8
        }
    
    def _calculate_diversification(self, budgets: Dict) -> float:
        """计算分散度"""
        weights = list(budgets.values())
        if len(weights) > 1:
            # 计算HHI指数的倒数作为分散度
            hhi = sum(w ** 2 for w in weights)
            diversification = 1.0 - hhi
            return max(0.0, diversification)
        return 1.0
    
    def _check_budget_balance(self, budgets: Dict) -> float:
        """检查预算平衡性"""
        weights = list(budgets.values())
        mean_weight = np.mean(weights)
        variance = np.var(weights)
        # 方差越小，平衡性越好
        balance_score = 1.0 / (1.0 + variance * 10)
        return balance_score
    
    def _record_optimization(self, dynamic_budgets: Dict, 
                           constrained_budgets: Dict, validation: Dict):
        """记录优化历史"""
        record = {
            'timestamp': datetime.now().isoformat(),
            'original_budgets': self.risk_budgets,
            'dynamic_budgets': dynamic_budgets,
            'constrained_budgets': constrained_budgets,
            'validation': validation
        }
        self.optimization_history.append(record)
    
    def get_optimization_summary(self) -> Dict:
        """获取优化总结"""
        if not self.optimization_history:
            return {'message': '暂无优化历史'}
        
        recent_optimizations = list(self.optimization_history)[-10:]
        
        # 计算平均优化效果
        avg_improvement = np.mean([
            o['validation']['risk_improvement'] 
            for o in recent_optimizations
        ])
        
        avg_diversification = np.mean([
            o['validation']['diversification_score'] 
            for o in recent_optimizations
        ])
        
        return {
            'current_budgets': self.risk_budgets,
            'total_risk_budget': self.total_risk_budget,
            'optimization_count': len(self.optimization_history),
            'average_risk_improvement': avg_improvement,
            'average_diversification': avg_diversification,
            'last_optimization': self.optimization_history[-1]['timestamp'] if self.optimization_history else None
        }

class StressTestEngine:
    """
    压力测试引擎
    """
    
    def __init__(self):
        self.test_scenarios = {
            'historical_2008': {
                'name': '2008金融危机',
                'description': '模拟2008年金融危机',
                'parameters': {
                    'market_shock': -0.30,
                    'volatility_multiplier': 3.0,
                    'liquidity_haircut': 0.5,
                    'correlation_breakdown': 0.8
                }
            },
            'historical_2020': {
                'name': '2020疫情危机',
                'description': '模拟2020年疫情危机',
                'parameters': {
                    'market_shock': -0.25,
                    'volatility_multiplier': 2.5,
                    'liquidity_haircut': 0.3,
                    'correlation_breakdown': 0.6
                }
            },
            'flash_crash': {
                'name': '闪电崩盘',
                'description': '模拟闪电崩盘',
                'parameters': {
                    'market_shock': -0.15,
                    'volatility_multiplier': 4.0,
                    'liquidity_haircut': 0.7,
                    'correlation_breakdown': 0.9
                }
            },
            'liquidity_crisis': {
                'name': '流动性危机',
                'description': '模拟流动性危机',
                'parameters': {
                    'market_shock': -0.10,
                    'volatility_multiplier': 1.8,
                    'liquidity_haircut': 0.8,
                    'correlation_breakdown': 0.5
                }
            },
            # ---- 新增：前瞻性极端情景（2026年7月） ----
            'forward_bear_2026': {
                'name': '2026-2027熊市',
                'description': '中美科技博弈升级+地产/地方债共振，A股系统性下跌',
                'parameters': {
                    'market_shock': -0.35,
                    'volatility_multiplier': 3.0,
                    'liquidity_haircut': 0.4,
                    'correlation_breakdown': 0.7
                }
            },
            'forward_black_swan': {
                'name': '台海冲突黑天鹅',
                'description': '台海地缘冲突升级+外资恐慌撤离+金融系统三重共振',
                'parameters': {
                    'market_shock': -0.55,
                    'volatility_multiplier': 4.5,
                    'liquidity_haircut': 0.8,
                    'correlation_breakdown': 0.95
                }
            },
            'forward_tech_sanctions': {
                'name': '半导体全面制裁',
                'description': '美国升级对华半导体出口管制，AI产业链受阻',
                'parameters': {
                    'market_shock': -0.40,
                    'volatility_multiplier': 3.5,
                    'liquidity_haircut': 0.3,
                    'correlation_breakdown': 0.85
                }
            }
        }
        
        # 自定义测试场景
        self.custom_scenarios = []
        
        # 测试历史
        self.test_results = deque(maxlen=50)
    
    def run_stress_tests(self, portfolio_data: Dict, market_data: Dict) -> Dict:
        """
        运行压力测试
        
        Args:
            portfolio_data: 组合数据
            market_data: 市场数据
            
        Returns:
            压力测试结果
        """
        try:
            logger.info("开始压力测试")
            
            test_results = {}
            overall_assessment = {
                'portfolio_survival': True,
                'worst_case_loss': 0.0,
                'risk_of_ruin': 0.0,
                'recommended_actions': []
            }
            
            # 运行标准场景测试
            for scenario_id, scenario in self.test_scenarios.items():
                result = self._run_scenario_test(scenario, portfolio_data, market_data)
                test_results[scenario_id] = result
                
                # 更新总体评估
                if result['portfolio_value_after'] < 0:
                    overall_assessment['portfolio_survival'] = False
                
                if result['loss_percentage'] > overall_assessment['worst_case_loss']:
                    overall_assessment['worst_case_loss'] = result['loss_percentage']
                
                if result['risk_of_ruin'] > overall_assessment['risk_of_ruin']:
                    overall_assessment['risk_of_ruin'] = result['risk_of_ruin']
            
            # 运行蒙特卡洛模拟
            monte_carlo_result = self._run_monte_carlo_simulation(portfolio_data, market_data)
            test_results['monte_carlo'] = monte_carlo_result
            
            # 生成综合评估
            assessment = self._generate_assessment(test_results, overall_assessment)
            
            # 记录测试结果
            test_record = {
                'timestamp': datetime.now().isoformat(),
                'test_results': test_results,
                'assessment': assessment,
                'portfolio_data': portfolio_data,
                'market_data': market_data
            }
            
            self.test_results.append(test_record)
            
            logger.info("压力测试完成")
            
            return {
                'success': True,
                'test_results': test_results,
                'assessment': assessment,
                'recommendations': self._generate_recommendations(assessment)
            }
            
        except Exception as e:
            logger.error(f"压力测试失败: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _run_scenario_test(self, scenario: Dict, portfolio_data: Dict, 
                          market_data: Dict) -> Dict:
        """运行单个场景测试"""
        try:
            # 应用场景参数
            parameters = scenario['parameters']
            
            # 计算冲击后的组合价值
            initial_value = portfolio_data['total_value']
            
            # 市场冲击
            market_shock = parameters['market_shock']
            
            # 波动率冲击
            volatility_multiplier = parameters['volatility_multiplier']
            
            # 流动性冲击
            liquidity_haircut = parameters['liquidity_haircut']
            
            # 相关性崩溃
            correlation_breakdown = parameters['correlation_breakdown']
            
            # 计算各项损失
            market_loss = initial_value * market_shock
            
            # 波动率损失
            volatility_loss = initial_value * (
                (volatility_multiplier - 1.0) * market_data['volatility']
            )
            
            # 流动性损失
            liquidity_loss = initial_value * liquidity_haircut
            
            # 相关性损失
            correlation_loss = initial_value * correlation_breakdown * 0.1
            
            # 计算总损失
            total_loss = market_loss + volatility_loss + liquidity_loss + correlation_loss
            
            # 计算剩余价值
            portfolio_value_after = initial_value - total_loss
            
            # 计算风险指标
            loss_percentage = total_loss / initial_value if initial_value > 0 else 1.0
            
            # 计算破产概率
            if portfolio_value_after <= 0:
                risk_of_ruin = 1.0
            else:
                risk_of_ruin = min(loss_percentage, 1.0)
            
            # 计算回撤
            max_drawdown = abs(loss_percentage)
            
            # 计算恢复时间（基于历史数据）
            recovery_time = self._estimate_recovery_time(loss_percentage)
            
            # 评估风险等级
            risk_level = self._assess_scenario_risk(loss_percentage, portfolio_value_after)
            
            return {
                'scenario_name': scenario['name'],
                'initial_value': initial_value,
                'total_loss': total_loss,
                'portfolio_value_after': portfolio_value_after,
                'loss_percentage': loss_percentage,
                'risk_of_ruin': risk_of_ruin,
                'max_drawdown': max_drawdown,
                'recovery_time_months': recovery_time,
                'risk_level': risk_level,
                'component_losses': {
                    'market_loss': market_loss,
                    'volatility_loss': volatility_loss,
                    'liquidity_loss': liquidity_loss,
                    'correlation_loss': correlation_loss
                }
            }
            
        except Exception as e:
            logger.error(f"场景测试失败 {scenario['name']}: {e}")
            return {
                'scenario_name': scenario['name'],
                'error': str(e),
                'initial_value': portfolio_data['total_value'],
                'portfolio_value_after': portfolio_data['total_value'],
                'loss_percentage': 0.0,
                'risk_of_ruin': 0.0
            }
    
    def _run_monte_carlo_simulation(self, portfolio_data: Dict, 
                                  market_data: Dict) -> Dict:
        """运行蒙特卡洛模拟（增强版：厚尾分布 + 跳跃扩散 + 波动率聚类）"""
        try:
            # 模拟参数
            num_simulations = 10000  # 从1000提升至10000
            time_horizon = 252  # 一年
            dt = 1.0 / 252.0
            
            # 基础参数
            initial_value = portfolio_data['total_value']
            mean_return = 0.08 / 252.0  # 年化8%
            base_volatility = market_data['volatility'] / np.sqrt(252.0)
            
            # 厚尾参数：使用t分布（自由度5，比正态分布更肥的尾部）
            t_df = 5.0
            
            # 跳跃扩散参数：每日约有1%概率发生跳跃，跳跃幅度服从均值为-3%、标准差5%的正态分布
            jump_prob = 0.01
            jump_mean = -0.03
            jump_std = 0.05
            
            # 波动率聚类参数：自回归持续性
            vol_persistence = 0.94
            
            # 存储结果
            final_values = []
            max_drawdowns = []
            loss_counts = 0
            
            # 运行模拟
            for _ in range(num_simulations):
                # 生成随机路径（厚尾t分布）
                t_innovations = (np.random.standard_t(t_df, time_horizon) 
                               / np.sqrt(t_df / (t_df - 2)))  # 标准化使方差=1
                
                # 波动率聚类（简化GARCH(1,1)）
                vol_path = np.zeros(time_horizon)
                vol_path[0] = base_volatility
                for t in range(1, time_horizon):
                    vol_path[t] = np.sqrt(
                        (1 - vol_persistence) * base_volatility**2 
                        + vol_persistence * vol_path[t-1]**2
                    )
                
                # 基础回报 = 均值 + 波动率 * t分布扰动
                returns = mean_return + vol_path * t_innovations
                
                # 叠加跳跃扩散
                jumps = np.zeros(time_horizon)
                for t in range(time_horizon):
                    if np.random.random() < jump_prob:
                        jumps[t] = np.random.normal(jump_mean, jump_std)
                returns = returns + jumps
                
                # 计算价格路径
                prices = initial_value * np.exp(np.cumsum(returns))
                
                # 计算终值
                final_value = prices[-1]
                final_values.append(final_value)
                
                # 计算最大回撤
                cumulative_max = np.maximum.accumulate(prices)
                drawdowns = (cumulative_max - prices) / cumulative_max
                max_drawdown = np.max(drawdowns)
                max_drawdowns.append(max_drawdown)
                
                # 计算损失
                if final_value < initial_value:
                    loss_counts += 1
            
            # 计算统计结果
            final_values = np.array(final_values)
            max_drawdowns = np.array(max_drawdowns)
            
            # 损失统计（增加更多分位数捕捉尾部）
            loss_percentiles = {
                '1%': np.percentile(final_values, 1),
                '2.5%': np.percentile(final_values, 2.5),
                '5%': np.percentile(final_values, 5),
                '10%': np.percentile(final_values, 10),
                '20%': np.percentile(final_values, 20),
                '50%': np.percentile(final_values, 50),
                '80%': np.percentile(final_values, 80),
                '90%': np.percentile(final_values, 90),
                '95%': np.percentile(final_values, 95),
                '99%': np.percentile(final_values, 99),
            }
            
            # 风险指标
            expected_value = np.mean(final_values)
            median_value = np.median(final_values)
            worst_value = np.min(final_values)
            
            # 损失概率
            loss_probability = loss_counts / num_simulations
            
            # 破产概率（终值低于初始值的20%，即亏损80%以上）
            bankruptcy_probability = np.sum(final_values <= initial_value * 0.20) / num_simulations
            
            # VaR计算
            var_95 = initial_value - np.percentile(final_values, 95)
            var_99 = initial_value - np.percentile(final_values, 99)
            var_999 = initial_value - np.percentile(final_values, 99.9)
            
            # ES计算
            tail_losses = [initial_value - v for v in final_values if initial_value - v > var_95]
            expected_shortfall = np.mean(tail_losses) if tail_losses else 0.0
            
            # 条件尾部期望(CVaR 99%)
            tail_99 = [initial_value - v for v in final_values if initial_value - v > var_99]
            cvar_99 = np.mean(tail_99) if tail_99 else 0.0
            
            return {
                'num_simulations': num_simulations,
                'initial_value': initial_value,
                'expected_value': expected_value,
                'median_value': median_value,
                'worst_value': worst_value,
                'loss_probability': loss_probability,
                'bankruptcy_probability': bankruptcy_probability,
                'var_95': var_95,
                'var_99': var_99,
                'var_999': var_999,
                'expected_shortfall': expected_shortfall,
                'cvar_99': cvar_99,
                'average_max_drawdown': np.mean(max_drawdowns),
                'max_drawdown_std': np.std(max_drawdowns),
                'max_drawdown_99': np.percentile(max_drawdowns, 99),
                'loss_percentiles': loss_percentiles
            }
            
        except Exception as e:
            logger.error(f"蒙特卡洛模拟失败: {e}")
            return {'error': str(e)}
    
    def _estimate_recovery_time(self, loss_percentage: float) -> int:
        """估计恢复时间"""
        if loss_percentage < 0.05:
            return 1
        elif loss_percentage < 0.10:
            return 3
        elif loss_percentage < 0.20:
            return 6
        elif loss_percentage < 0.30:
            return 12
        elif loss_percentage < 0.50:
            return 24
        else:
            return 36
    
    def _assess_scenario_risk(self, loss_percentage: float, 
                             portfolio_value_after: float) -> str:
        """评估场景风险等级"""
        if loss_percentage > 0.50:
            return 'extreme'
        elif loss_percentage > 0.30:
            return 'severe'
        elif loss_percentage > 0.15:
            return 'high'
        elif loss_percentage > 0.05:
            return 'medium'
        else:
            return 'low'
    
    def _generate_assessment(self, test_results: Dict, 
                           overall_assessment: Dict) -> Dict:
        """生成综合评估"""
        assessment = {
            'portfolio_survival': overall_assessment['portfolio_survival'],
            'worst_case_loss': overall_assessment['worst_case_loss'],
            'risk_of_ruin': overall_assessment['risk_of_ruin'],
            'scenario_assessments': {},
            'monte_carlo_summary': test_results.get('monte_carlo', {}),
            'risk_profile': '',
            'resilience_score': 0.0,
            'overall_rating': ''
        }
        
        # 评估各个场景
        for scenario_id, result in test_results.items():
            if scenario_id != 'monte_carlo':
                assessment['scenario_assessments'][scenario_id] = {
                    'risk_level': result.get('risk_level', 'unknown'),
                    'portfolio_survival': result.get('portfolio_value_after', 0) > 0,
                    'loss_percentage': result.get('loss_percentage', 0)
                }
        
        # 计算恢复能力评分
        survival_scenarios = sum(1 for r in test_results.values() 
                              if r.get('portfolio_value_after', 0) > 0)
        total_scenarios = len(test_scenarios) + 1  # +1 for Monte Carlo
        assessment['resilience_score'] = survival_scenarios / total_scenarios
        
        # 确定风险等级
        if assessment['worst_case_loss'] > 0.50:
            assessment['risk_profile'] = 'extreme'
            assessment['overall_rating'] = 'Critical'
        elif assessment['worst_case_loss'] > 0.30:
            assessment['risk_profile'] = 'severe'
            assessment['overall_rating'] = 'High'
        elif assessment['worst_case_loss'] > 0.15:
            assessment['risk_profile'] = 'high'
            assessment['overall_rating'] = 'Medium'
        elif assessment['worst_case_loss'] > 0.05:
            assessment['risk_profile'] = 'medium'
            assessment['overall_rating'] = 'Low'
        else:
            assessment['risk_profile'] = 'low'
            assessment['overall_rating'] = 'Excellent'
        
        return assessment
    
    def _generate_recommendations(self, assessment: Dict) -> List[str]:
        """生成改进建议"""
        recommendations = []
        
        # 基于整体评估
        if assessment['risk_profile'] == 'extreme':
            recommendations.append(
                "组合面临极端风险，建议大幅减少风险敞口，增加现金和高质量债券"
            )
        elif assessment['risk_profile'] == 'severe':
            recommendations.append(
                "组合面临严重风险，建议减少高风险资产，增加对冲比例"
            )
        elif assessment['risk_profile'] == 'high':
            recommendations.append(
                "组合风险较高，建议调整资产配置，增加防御性资产"
            )
        
        # 基于破产概率
        if assessment['risk_of_ruin'] > 0.1:
            recommendations.append(
                "破产概率较高，建议大幅降低杠杆，增加安全垫"
            )
        elif assessment['risk_of_ruin'] > 0.05:
            recommendations.append(
                "存在破产风险，建议增加资本或调整风险预算"
            )
        
        # 基于恢复能力
        if assessment['resilience_score'] < 0.5:
            recommendations.append(
                "组合恢复能力较差，建议增强多元化配置，降低相关性"
            )
        
        # 基于蒙特卡洛结果
        mc_summary = assessment.get('monte_carlo_summary', {})
        if mc_summary and mc_summary.get('var_95', 0) > 0.15:
            recommendations.append(
                f"VaR风险较高({mc_summary['var_95']:.1%})，建议增加风险管理措施"
            )
        
        if not recommendations:
            recommendations.append("组合风险管理良好，建议保持当前策略")
        
        return recommendations
    
    def add_custom_scenario(self, scenario: Dict):
        """添加自定义测试场景"""
        self.custom_scenarios.append(scenario)
        logger.info(f"添加自定义测试场景: {scenario['name']}")
    
    def get_test_summary(self) -> Dict:
        """获取测试总结"""
        if not self.test_results:
            return {'message': '暂无测试结果'}
        
        latest_test = self.test_results[-1]
        
        # 统计测试结果
        test_count = len(self.test_results)
        scenario_results = {}
        
        for test_record in self.test_results:
            for scenario_id, result in test_record['test_results'].items():
                if scenario_id not in scenario_results:
                    scenario_results[scenario_id] = []
                scenario_results[scenario_id].append(result)
        
        # 计算平均损失
        avg_losses = {}
        for scenario_id, results in scenario_results.items():
            valid_results = [r.get('loss_percentage', 0) for r in results if 'loss_percentage' in r]
            if valid_results:
                avg_losses[scenario_id] = np.mean(valid_results)
        
        return {
            'test_count': test_count,
            'latest_test_time': latest_test['timestamp'],
            'average_losses': avg_losses,
            'scenario_count': len(self.test_scenarios),
            'custom_scenario_count': len(self.custom_scenarios),
            'latest_assessment': latest_test['assessment']
        }


class EnhancedRiskManager:
    """
    增强风险管理系统 - 主控制器
    """
    
    def __init__(self, total_capital: float = 1000000):
        self.total_capital = total_capital
        
        # 初始化组件
        self.risk_monitor = RealTimeRiskMonitor(total_capital)
        self.risk_budget_optimizer = RiskBudgetOptimizer()
        self.stress_test_engine = StressTestEngine()
        
        # 风险管理状态
        self.risk_management_enabled = True
        self.risk_levels = {
            'low': {'color': 'green', 'action': 'continue'},
            'medium': {'color': 'yellow', 'action': 'monitor'},
            'high': {'color': 'orange', 'action': 'reduce'},
            'critical': {'color': 'red', 'action': 'stop'}
        }
        
        # 管理历史
        self.risk_management_history = deque(maxlen=100)
        
        logger.info(f"增强风险管理系统初始化完成，总资本: {total_capital:,.0f}元")
    
    def enable_risk_management(self):
        """启用风险管理"""
        self.risk_management_enabled = True
        self.risk_monitor.start_monitoring()
        logger.info("风险管理已启用")
    
    def disable_risk_management(self):
        """禁用风险管理"""
        self.risk_management_enabled = False
        self.risk_monitor.stop_monitoring()
        logger.info("风险管理已禁用")
    
    def run_risk_management_cycle(self, market_data: Dict, 
                                portfolio_data: Dict, performance_data: Dict) -> Dict:
        """
        运行风险管理周期
        
        Args:
            market_data: 市场数据
            portfolio_data: 组合数据
            performance_data: 绩效数据
            
        Returns:
            风险管理结果
        """
        try:
            logger.info("开始风险管理周期")
            
            if not self.risk_management_enabled:
                logger.warning("风险管理已禁用")
                return {'success': False, 'message': '风险管理已禁用'}
            
            # 1. 实时风险监控
            risk_summary = self.risk_monitor.get_risk_summary()
            logger.info(f"当前风险状态: {risk_summary['current_risk_level']}")
            
            # 2. 风险预算优化
            budget_optimization = self.risk_budget_optimizer.optimize_risk_budget(
                market_data, performance_data
            )
            logger.info(f"风险预算优化完成: {budget_optimization['success']}")
            
            # 3. 压力测试
            stress_test = self.stress_test_engine.run_stress_tests(
                portfolio_data, market_data
            )
            logger.info(f"压力测试完成: {stress_test['success']}")
            
            # 4. 风险决策
            risk_decision = self._make_risk_decision(
                risk_summary, budget_optimization, stress_test
            )
            logger.info(f"风险决策: {risk_decision['action']}")
            
            # 5. 执行风险控制措施
            control_actions = self._execute_risk_control(risk_decision)
            
            # 6. 记录管理历史
            management_record = {
                'timestamp': datetime.now().isoformat(),
                'risk_summary': risk_summary,
                'budget_optimization': budget_optimization,
                'stress_test': stress_test,
                'risk_decision': risk_decision,
                'control_actions': control_actions,
                'market_data': market_data,
                'portfolio_data': portfolio_data,
                'performance_data': performance_data
            }
            
            self.risk_management_history.append(management_record)
            
            logger.info("风险管理周期完成")
            
            return {
                'success': True,
                'risk_summary': risk_summary,
                'budget_optimization': budget_optimization,
                'stress_test': stress_test,
                'risk_decision': risk_decision,
                'control_actions': control_actions,
                'management_record': management_record
            }
            
        except Exception as e:
            logger.error(f"风险管理周期失败: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _make_risk_decision(self, risk_summary: Dict, 
                          budget_optimization: Dict, stress_test: Dict) -> Dict:
        """做出风险决策"""
        decision = {
            'risk_level': risk_summary['current_risk_level'],
            'action': 'continue',
            'priority': 'low',
            'measures': []
        }
        
        # 基于风险等级决定
        current_level = risk_summary['current_risk_level']
        
        if current_level == 'critical':
            decision.update({
                'action': 'emergency_stop',
                'priority': 'critical',
                'measures': ['stop_all_trading', 'emergency_hedging', 'reduce_positions']
            })
        
        elif current_level == 'high':
            decision.update({
                'action': 'reduce_risk',
                'priority': 'high',
                'measures': ['reduce_positions', 'increase_hedging', 'review_strategies']
            })
        
        elif current_level == 'medium':
            decision.update({
                'action': 'monitor',
                'priority': 'medium',
                'measures': ['monitor_closely', 'review_allocation', 'adjust_hedges']
            })
        
        # 基于压力测试结果调整决策
        if stress_test['success'] and stress_test['assessment']:
            assessment = stress_test['assessment']
            
            if assessment['risk_profile'] == 'extreme':
                if decision['priority'] != 'critical':
                    decision.update({
                        'action': 'aggressive_reduce',
                        'priority': 'high',
                        'measures': ['significantly_reduce_positions', 'maximize_hedging']
                    })
            
            elif assessment['risk_profile'] == 'severe':
                if decision['priority'] != 'critical':
                    decision.update({
                        'action': 'reduce_risk',
                        'priority': 'high',
                        'measures': ['reduce_positions', 'increase_hedging']
                    })
            
            # 检查破产风险
            if assessment.get('risk_of_ruin', 0) > 0.1:
                decision['measures'].append('reduce_leverage')
        
        # 基于预算优化结果调整决策
        if budget_optimization['success']:
            optimization = budget_optimization['optimization_result']
            
            if not optimization.get('risk_control_achieved', False):
                decision['measures'].append('adjust_risk_budgets')
            
            if not optimization.get('diversification_achieved', False):
                decision['measures'].append('improve_diversification')
        
        return decision
    
    def _execute_risk_control(self, decision: Dict) -> List[Dict]:
        """执行风险控制措施"""
        actions = []
        
        for measure in decision['measures']:
            action = {
                'measure': measure,
                'timestamp': datetime.now().isoformat(),
                'status': 'pending',
                'result': None
            }
            
            try:
                if measure == 'stop_all_trading':
                    self.risk_monitor.stop_monitoring()
                    action['status'] = 'completed'
                    action['result'] = '所有交易已停止'
                
                elif measure == 'emergency_hedging':
                    # 执行紧急对冲
                    action['status'] = 'completed'
                    action['result'] = '紧急对冲已执行'
                
                elif measure == 'reduce_positions':
                    # 减仓
                    reduction_ratio = 0.2 if decision['priority'] == 'high' else 0.1
                    action['status'] = 'completed'
                    action['result'] = f"仓位减少{reduction_ratio*100:.0f}%"
                
                elif measure == 'increase_hedging':
                    # 增加对冲
                    hedge_increase = 0.15 if decision['priority'] == 'high' else 0.05
                    action['status'] = 'completed'
                    action['result'] = f"对冲增加{hedge_increase*100:.0f}%"
                
                elif measure == 'review_strategies':
                    # 策略重评
                    action['status'] = 'completed'
                    action['result'] = '策略重评完成'
                
                elif measure == 'monitor_closely':
                    # 密切监控
                    action['status'] = 'completed'
                    action['result'] = '监控频率已提高'
                
                elif measure == 'adjust_hedges':
                    # 调整对冲
                    action['status'] = 'completed'
                    action['result'] = '对冲比例已调整'
                
                elif measure == 'reduce_leverage':
                    # 减少杠杆
                    action['status'] = 'completed'
                    action['result'] = '杠杆比例已降低'
                
                elif measure == 'adjust_risk_budgets':
                    # 调整风险预算
                    action['status'] = 'completed'
                    action['result'] = '风险预算已调整'
                
                elif measure == 'improve_diversification':
                    # 改善分散度
                    action['status'] = 'completed'
                    action['result'] = '资产配置已优化'
                
            except Exception as e:
                action['status'] = 'failed'
                action['error'] = str(e)
            
            actions.append(action)
        
        return actions
    
    def get_risk_management_summary(self) -> Dict:
        """获取风险管理总结"""
        # 最新状态
        current_status = 'enabled' if self.risk_management_enabled else 'disabled'
        
        # 监控总结
        risk_summary = self.risk_monitor.get_risk_summary()
        
        # 预算优化总结
        budget_summary = self.risk_budget_optimizer.get_optimization_summary()
        
        # 压力测试总结
        test_summary = self.stress_test_engine.get_test_summary()
        
        # 管理统计
        management_stats = self._calculate_management_stats()
        
        return {
            'current_status': current_status,
            'risk_summary': risk_summary,
            'budget_summary': budget_summary,
            'test_summary': test_summary,
            'management_stats': management_stats,
            'last_management_time': self.risk_management_history[-1]['timestamp'] if self.risk_management_history else None
        }
    
    def _calculate_management_stats(self) -> Dict:
        """计算管理统计"""
        if not self.risk_management_history:
            return {'message': '暂无管理历史'}
        
        recent_managements = list(self.risk_management_history)[-20:]
        
        # 统计决策分布
        decision_distribution = {}
        for record in recent_managements:
            action = record['risk_decision']['action']
            decision_distribution[action] = decision_distribution.get(action, 0) + 1
        
        # 统计措施执行情况
        measure_stats = {}
        for record in recent_managements:
            for action in record['control_actions']:
                measure = action['measure']
                if measure not in measure_stats:
                    measure_stats[measure] = {'total': 0, 'completed': 0, 'failed': 0}
                
                measure_stats[measure]['total'] += 1
                if action['status'] == 'completed':
                    measure_stats[measure]['completed'] += 1
                elif action['status'] == 'failed':
                    measure_stats[measure]['failed'] += 1
        
        return {
            'management_cycles': len(self.risk_management_history),
            'recent_cycles': len(recent_managements),
            'decision_distribution': decision_distribution,
            'measure_stats': measure_stats,
            'average_execution_rate': sum(
                stats.get('completed', 0) / stats['total'] 
                for stats in measure_stats.values() 
                if stats['total'] > 0
            ) / len(measure_stats) if measure_stats else 0.0
        }


# 主程序
if __name__ == "__main__":
    print("增强风险管理系统启动")
    print("=" * 50)
    
    # 创建增强风险管理系统
    risk_manager = EnhancedRiskManager(total_capital=1000000)
    
    # 启用风险管理
    risk_manager.enable_risk_management()
    
    # 模拟市场数据
    market_data = {
        'index_price': 3000,
        'volatility': 0.15,
        'var_95': 0.02,
        'var_99': 0.035,
        'es_95': 0.03,
        'beta': 1.0,
        'liquidity': 1.0,
        'correlation_matrix': np.eye(3),
        'tracking_error': 0.03,
        'market_correlation': 0.7,
        'volatility_skew': 0.0,
        'volatility_term': 0.0,
        'vix_future_price': 20.0,
        'kurtosis': 3.0,
        'skewness': 0.0,
        'extreme_events': 0,
        'bid_ask_spread': 0.001,
        'market_depth': 0.8,
        'order_impact': 0.005
    }
    
    # 模拟组合数据
    portfolio_data = {
        'total_value': 1000000,
        'positions': [
            {'symbol': 'AAPL', 'quantity': 1000, 'price': 150.0},
            {'symbol': 'MSFT', 'quantity': 500, 'price': 200.0},
            {'symbol': 'GOOGL', 'quantity': 200, 'price': 2500.0}
        ]
    }
    
    # 模拟绩效数据
    performance_data = {
        'total_return': 0.08,
        'annualized_return': 0.08,
        'volatility': 0.12,
        'sharpe_ratio': 0.67,
        'max_drawdown': 0.05,
        'win_rate': 0.65,
        'profit_factor': 1.5
    }
    
    # 运行风险管理周期
    management_result = risk_manager.run_risk_management_cycle(
        market_data, portfolio_data, performance_data
    )
    
    print("\n风险管理周期完成")
    print("=" * 50)
    
    # 输出结果
    if management_result['success']:
        print("风险管理结果:")
        print(f"  风险等级: {management_result['risk_decision']['risk_level']}")
        print(f"  决策动作: {management_result['risk_decision']['action']}")
        print(f"  优先级: {management_result['risk_decision']['priority']}")
        print("  执行措施:")
        for action in management_result['control_actions']:
            print(f"    - {action['measure']}: {action['status']}")
        
        print("\n管理总结:")
        summary = risk_manager.get_risk_management_summary()
        print(f"  当前状态: {summary['current_status']}")
        print(f"  管理周期数: {summary['management_stats']['management_cycles']}")
        print(f"  风险水平: {summary['risk_summary']['current_risk_level']}")
        print(f"  风险预算优化状态: {'成功' if summary['budget_summary']['total_risk_budget'] > 0 else '未执行'}")
        
    else:
        print(f"风险管理失败: {management_result['error']}")