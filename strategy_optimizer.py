# -*- coding: utf-8 -*-
"""
策略优化器 - 世界级对冲基金的策略整合与优化系统

系统特点：
- 多策略整合：Delta、Volatility、Tail Risk三大对冲策略的智能整合
- 动态权重分配：基于市场环境和策略表现的动态权重调整
- 风险预算管理：智能的风险预算分配和管理
- 回测验证：全面的策略回测和验证机制
- 实时优化：基于实时数据的策略参数优化
- 性能监控：全方位的策略性能监控和分析

核心功能：
1. 策略整合框架：统一的多策略管理和协调机制
2. 权重优化算法：基于现代投资理论的权重优化算法
3. 风险预算分配：基于风险贡献度的智能分配
4. 回测引擎：多场景的回测验证
5. 实时优化：自适应的参数优化
6. 绩效分析：详细的策略绩效分析
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Union
from collections import deque
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from scipy.optimize import minimize, differential_evolution
from scipy.stats import norm

try:
    from utils.logger import get_logger
    from utils.risk_metrics import calculate_var, calculate_es, calculate_max_drawdown, calculate_sharpe_ratio
    from utils.data_provider import get_market_data, get_historical_data
    logger = get_logger('strategy_optimizer')
except ImportError:
    import logging
    logger = logging.getLogger('strategy_optimizer')

class StrategyIntegrator:
    """
    策略整合器
    """
    
    def __init__(self, total_capital: float = 1000000):
        self.total_capital = total_capital
        
        # 策略定义
        self.strategies = {
            'delta_hedge': {
                'description': 'Delta对冲策略',
                'target_risk': 0.02,
                'max_position': 0.4,
                'min_position': 0.1,
                'correlation_target': 0.3,
                'performance_metrics': {
                    'sharpe_ratio': 0.0,
                    'max_drawdown': 0.0,
                    'win_rate': 0.0,
                    'annual_return': 0.0
                }
            },
            'volatility_hedge': {
                'description': '波动率对冲策略',
                'target_risk': 0.015,
                'max_position': 0.35,
                'min_position': 0.05,
                'correlation_target': 0.4,
                'performance_metrics': {
                    'sharpe_ratio': 0.0,
                    'max_drawdown': 0.0,
                    'win_rate': 0.0,
                    'annual_return': 0.0
                }
            },
            'tail_risk_hedge': {
                'description': '尾部风险对冲策略',
                'target_risk': 0.01,
                'max_position': 0.25,
                'min_position': 0.05,
                'correlation_target': 0.5,
                'performance_metrics': {
                    'sharpe_ratio': 0.0,
                    'max_drawdown': 0.0,
                    'win_rate': 0.0,
                    'annual_return': 0.0
                }
            }
        }
        
        # 当前权重
        self.current_weights = {
            'delta_hedge': 0.60,
            'volatility_hedge': 0.30,
            'tail_risk_hedge': 0.10
        }
        
        # 整合历史
        self.integration_history = deque(maxlen=100)
        
        # 优化参数
        self.optimization_params = {
            'max_iterations': 1000,
            'convergence_threshold': 0.001,
            'population_size': 50,
            'mutation_rate': 0.1,
            'crossover_rate': 0.8,
            'max_weight_change': 0.1  # 单次权重变化最大限制
        }
        
        logger.info("策略整合器初始化完成")
    
    def integrate_strategies(self, market_data: Dict, performance_data: Dict) -> Dict:
        """
        整合策略
        
        Args:
            market_data: 市场数据
            performance_data: 绩效数据
            
        Returns:
            整合结果
        """
        try:
            logger.info("开始策略整合")
            
            # 1. 更新策略绩效
            self._update_strategy_performance(performance_data)
            
            # 2. 计算策略相关性
            strategy_correlations = self._calculate_strategy_correlations(performance_data)
            
            # 3. 计算风险贡献度
            risk_contributions = self._calculate_risk_contributions(market_data, strategy_correlations)
            
            # 4. 权重优化
            optimized_weights = self._optimize_weights(
                strategy_correlations, risk_contributions
            )
            
            # 5. 风险预算分配
            risk_budgets = self._allocate_risk_budgets(
                optimized_weights, market_data
            )
            
            # 6. 组合构建
            portfolio = self._construct_portfolio(
                optimized_weights, risk_budgets, market_data
            )
            
            # 7. 整合验证
            validation = self._validate_integration(portfolio, market_data)
            
            # 8. 记录历史
            integration_record = {
                'timestamp': datetime.now().isoformat(),
                'market_data': market_data,
                'performance_data': performance_data,
                'original_weights': self.current_weights.copy(),
                'optimized_weights': optimized_weights,
                'risk_budgets': risk_budgets,
                'strategy_correlations': strategy_correlations,
                'risk_contributions': risk_contributions,
                'portfolio': portfolio,
                'validation': validation
            }
            
            self.integration_history.append(integration_record)
            
            # 更新当前权重
            self.current_weights = optimized_weights.copy()
            
            logger.info("策略整合完成")
            
            return {
                'success': True,
                'original_weights': self.current_weights,
                'optimized_weights': optimized_weights,
                'risk_budgets': risk_budgets,
                'portfolio': portfolio,
                'validation': validation,
                'integration_record': integration_record
            }
            
        except Exception as e:
            logger.error(f"策略整合失败: {e}")
            return {
                'success': False,
                'error': str(e),
                'current_weights': self.current_weights
            }
    
    def _update_strategy_performance(self, performance_data: Dict):
        """更新策略绩效"""
        # 模拟各策略的绩效更新
        strategy_returns = {
            'delta_hedge': np.random.normal(0.08, 0.12, 252),
            'volatility_hedge': np.random.normal(0.06, 0.15, 252),
            'tail_risk_hedge': np.random.normal(0.04, 0.20, 252)
        }
        
        for strategy_name, returns in strategy_returns.items():
            if strategy_name in self.strategies:
                # 计算绩效指标
                annual_return = np.mean(returns) * 252
                volatility = np.std(returns) * np.sqrt(252)
                sharpe_ratio = annual_return / volatility if volatility > 0 else 0.0
                max_drawdown = calculate_max_drawdown(returns)
                
                # 更新绩效
                self.strategies[strategy_name]['performance_metrics'].update({
                    'annual_return': annual_return,
                    'volatility': volatility,
                    'sharpe_ratio': sharpe_ratio,
                    'max_drawdown': max_drawdown,
                    'win_rate': np.mean(returns > 0)
                })
    
    def _calculate_strategy_correlations(self, performance_data: Dict) -> Dict:
        """计算策略相关性"""
        try:
            # 模拟策略相关性计算
            n_strategies = len(self.strategies)
            correlation_matrix = np.eye(n_strategies)  # 初始化为单位矩阵
            
            # 添加一些相关性
            correlation_matrix[0, 1] = 0.3  # Delta与Volatility相关性
            correlation_matrix[0, 2] = 0.2  # Delta与Tail相关性
            correlation_matrix[1, 2] = 0.4  # Volatility与Tail相关性
            
            # 确保矩阵对称
            correlation_matrix = (correlation_matrix + correlation_matrix.T) / 2
            
            # 计算相关系数矩阵
            strategy_names = list(self.strategies.keys())
            correlation_dict = {}
            
            for i, name1 in enumerate(strategy_names):
                correlation_dict[name1] = {}
                for j, name2 in enumerate(strategy_names):
                    correlation_dict[name1][name2] = correlation_matrix[i, j]
            
            logger.info("策略相关性计算完成")
            return correlation_dict
            
        except Exception as e:
            logger.error(f"策略相关性计算失败: {e}")
            return {s: {s2: 0.0 for s2 in self.strategies.keys()} 
                   for s in self.strategies.keys()}
    
    def _calculate_risk_contributions(self, market_data: Dict, 
                                   correlations: Dict) -> Dict:
        """计算风险贡献度"""
        try:
            risk_contributions = {}
            
            for strategy_name, strategy_info in self.strategies.items():
                # 计算各策略的风险贡献
                target_risk = strategy_info['target_risk']
                weight = self.current_weights[strategy_name]
                
                # 基于市场条件调整风险
                volatility = market_data.get('volatility', 0.15)
                risk_adjustment = 1.0 + (volatility - 0.15) * 2.0
                
                adjusted_risk = target_risk * risk_adjustment
                
                # 计算边际风险贡献
                marginal_risk_contribution = weight * adjusted_risk
                
                # 考虑相关性调整
                correlation_penalty = 0.0
                for other_strategy, correlation in correlations[strategy_name].items():
                    if other_strategy != strategy_name:
                        correlation_penalty += correlation * 0.1
                
                total_risk_contribution = marginal_risk_contribution * (1 + correlation_penalty)
                
                risk_contributions[strategy_name] = total_risk_contribution
            
            # 归一化风险贡献
            total_risk = sum(risk_contributions.values())
            if total_risk > 0:
                risk_contributions = {k: v / total_risk for k, v in risk_contributions.items()}
            
            logger.info("风险贡献度计算完成")
            return risk_contributions
            
        except Exception as e:
            logger.error(f"风险贡献度计算失败: {e}")
            return {s: 1.0 / len(self.strategies) for s in self.strategies.keys()}
    
    def _optimize_weights(self, correlations: Dict, 
                         risk_contributions: Dict) -> Dict:
        """优化权重分配"""
        try:
            # 定义优化目标函数
            def objective_function(weights):
                # 计算组合风险
                portfolio_risk = self._calculate_portfolio_risk(weights, correlations)
                
                # 计算组合收益
                portfolio_return = self._calculate_portfolio_return(weights)
                
                # 计算夏普比率
                if portfolio_risk > 0:
                    sharpe_ratio = portfolio_return / portfolio_risk
                else:
                    sharpe_ratio = 0
                
                # 惩罚过度集中
                concentration_penalty = np.sum(weights ** 2) * 10
                
                # 最小化负夏普比率和惩罚项
                return -sharpe_ratio + concentration_penalty
            
            # 约束条件
            constraints = (
                {'type': 'eq', 'fun': lambda w: np.sum(w) - 1},  # 权重和为1
                {'type': 'ineq', 'fun': lambda w: w - 0.05},  # 每个策略至少5%
                {'type': 'ineq', 'fun': lambda w: 0.6 - w}   # 每个策略最多60%
            )
            
            # 边界条件
            bounds = [(0.05, 0.6) for _ in range(len(self.strategies))]
            
            # 初始权重
            initial_weights = np.array(list(self.current_weights.values()))
            
            # 使用差分进化算法优化
            result = differential_evolution(
                objective_function,
                bounds,
                constraints=constraints,
                maxiter=self.optimization_params['max_iterations'],
                popsize=self.optimization_params['population_size'],
                mutation=self.optimization_params['mutation_rate'],
                recombination=self.optimization_params['crossover_rate'],
                seed=42
            )
            
            optimized_weights = dict(zip(
                self.strategies.keys(),
                result.x.tolist()
            ))
            
            # 应用权重变化限制
            optimized_weights = self._apply_weight_change_constraints(
                self.current_weights, optimized_weights
            )
            
            logger.info(f"权重优化完成: {optimized_weights}")
            return optimized_weights
            
        except Exception as e:
            logger.error(f"权重优化失败: {e}")
            return self.current_weights.copy()
    
    def _calculate_portfolio_risk(self, weights: np.ndarray, 
                                 correlations: Dict) -> float:
        """计算组合风险"""
        try:
            # 构建协方差矩阵
            n_strategies = len(self.strategies)
            cov_matrix = np.eye(n_strategies)
            
            strategy_names = list(self.strategies.keys())
            for i in range(n_strategies):
                for j in range(i+1, n_strategies):
                    correlation = correlations[strategy_names[i]][strategy_names[j]]
                    # 假设波动率
                    vol_i = 0.15
                    vol_j = 0.15
                    cov_matrix[i, j] = correlation * vol_i * vol_j
                    cov_matrix[j, i] = cov_matrix[i, j]
            
            # 计算组合方差
            portfolio_variance = np.dot(weights.T, np.dot(cov_matrix, weights))
            portfolio_volatility = np.sqrt(portfolio_variance)
            
            return portfolio_volatility
            
        except Exception as e:
            logger.error(f"组合风险计算失败: {e}")
            return 0.15
    
    def _calculate_portfolio_return(self, weights: np.ndarray) -> float:
        """计算组合收益"""
        try:
            total_return = 0.0
            strategy_names = list(self.strategies.keys())
            
            for i, strategy_name in enumerate(strategy_names):
                annual_return = self.strategies[strategy_name]['performance_metrics']['annual_return']
                total_return += weights[i] * annual_return
            
            return total_return
            
        except Exception as e:
            logger.error(f"组合收益计算失败: {e}")
            return 0.08
    
    def _apply_weight_change_constraints(self, old_weights: Dict, 
                                       new_weights: Dict) -> Dict:
        """应用权重变化约束"""
        constrained_weights = {}
        
        for strategy, old_weight in old_weights.items():
            new_weight = new_weights[strategy]
            
            # 计算最大允许变化
            max_change = self.optimization_params['max_weight_change']
            min_weight = max(0.05, old_weight - max_change)
            max_weight = min(0.6, old_weight + max_change)
            
            # 应用约束
            constrained_weights[strategy] = max(min_weight, min(max_weight, new_weight))
        
        # 归一化权重
        total_weight = sum(constrained_weights.values())
        if total_weight > 0:
            constrained_weights = {k: v / total_weight for k, v in constrained_weights.items()}
        
        return constrained_weights
    
    def _allocate_risk_budgets(self, weights: Dict, market_data: Dict) -> Dict:
        """分配风险预算"""
        try:
            risk_budgets = {}
            total_risk = 0.08  # 总风险预算8%
            
            for strategy_name, weight in weights.items():
                strategy_info = self.strategies[strategy_name]
                target_risk = strategy_info['target_risk']
                
                # 基于市场条件调整风险预算
                volatility = market_data.get('volatility', 0.15)
                adjustment_factor = 1.0 + (volatility - 0.15) * 0.5
                
                # 计算分配的风险预算
                strategy_risk = total_risk * weight * adjustment_factor
                strategy_risk = min(strategy_risk, total_risk * 0.6)  # 限制最大占比
                
                risk_budgets[strategy_name] = strategy_risk
            
            # 确保总风险预算
            total_allocated = sum(risk_budgets.values())
            if total_allocated > 0:
                scaling_factor = total_risk / total_allocated
                risk_budgets = {k: v * scaling_factor for k, v in risk_budgets.items()}
            
            logger.info("风险预算分配完成")
            return risk_budgets
            
        except Exception as e:
            logger.error(f"风险预算分配失败: {e}")
            return {s: total_risk / len(weights) for s in weights.keys()}
    
    def _construct_portfolio(self, weights: Dict, risk_budgets: Dict, 
                           market_data: Dict) -> Dict:
        """构建组合"""
        try:
            portfolio = {
                'total_value': self.total_capital,
                'weights': weights,
                'risk_budgets': risk_budgets,
                'positions': {},
                'risk_metrics': {}
            }
            
            # 构建各策略仓位
            for strategy_name, weight in weights.items():
                strategy_info = self.strategies[strategy_name]
                strategy_capital = self.total_capital * weight
                
                # 生成模拟仓位
                if strategy_name == 'delta_hedge':
                    positions = [
                        {'symbol': 'SPY', 'quantity': int(strategy_capital / 150), 'weight': 0.6},
                        {'symbol': 'QQQ', 'quantity': int(strategy_capital / 300), 'weight': 0.4}
                    ]
                elif strategy_name == 'volatility_hedge':
                    positions = [
                        {'symbol': 'VXX', 'quantity': int(strategy_capital / 20), 'weight': 0.7},
                        {'symbol': 'UVXY', 'quantity': int(strategy_capital / 15), 'weight': 0.3}
                    ]
                else:  # tail_risk_hedge
                    positions = [
                        {'symbol': 'SPXW231015P00280000', 'quantity': 10, 'weight': 0.5},
                        {'symbol': 'UVXY', 'quantity': int(strategy_capital / 15), 'weight': 0.5}
                    ]
                
                portfolio['positions'][strategy_name] = {
                    'capital': strategy_capital,
                    'positions': positions,
                    'target_risk': strategy_info['target_risk'],
                    'risk_budget': risk_budgets[strategy_name]
                }
            
            # 计算组合风险指标
            portfolio['risk_metrics'] = self._calculate_portfolio_risk_metrics(
                portfolio, market_data
            )
            
            logger.info("组合构建完成")
            return portfolio
            
        except Exception as e:
            logger.error(f"组合构建失败: {e}")
            return {'error': str(e)}
    
    def _calculate_portfolio_risk_metrics(self, portfolio: Dict, 
                                        market_data: Dict) -> Dict:
        """计算组合风险指标"""
        try:
            # 模拟风险指标计算
            portfolio_var = calculate_var(np.random.normal(0, 0.02, 1000), 0.95)
            portfolio_es = calculate_es(np.random.normal(0, 0.02, 1000), 0.95)
            portfolio_volatility = market_data.get('volatility', 0.15)
            portfolio_beta = market_data.get('beta', 1.0)
            
            risk_metrics = {
                'var_95': portfolio_var,
                'es_95': portfolio_es,
                'volatility': portfolio_volatility,
                'beta': portfolio_beta,
                'max_drawdown': 0.05,
                'diversification_ratio': 0.8
            }
            
            return risk_metrics
            
        except Exception as e:
            logger.error(f"风险指标计算失败: {e}")
            return {}
    
    def _validate_integration(self, portfolio: Dict, market_data: Dict) -> Dict:
        """验证整合结果"""
        try:
            validation = {
                'success': True,
                'checks': {},
                'recommendations': []
            }
            
            # 检查风险控制
            if 'risk_metrics' in portfolio:
                risk_metrics = portfolio['risk_metrics']
                if risk_metrics.get('var_95', 0) > 0.05:
                    validation['checks']['risk_control'] = 'warning'
                    validation['recommendations'].append("VaR超过阈值，建议增加对冲")
                else:
                    validation['checks']['risk_control'] = 'pass'
            
            # 检查分散度
            if 'diversification_ratio' in portfolio.get('risk_metrics', {}):
                diversification = portfolio['risk_metrics']['diversification_ratio']
                if diversification < 0.6:
                    validation['checks']['diversification'] = 'warning'
                    validation['recommendations'].append("分散度不足，建议调整权重")
                else:
                    validation['checks']['diversification'] = 'pass'
            
            # 检查收益目标
            total_return = 0.0
            weights = portfolio.get('weights', {})
            for strategy_name, weight in weights.items():
                if strategy_name in self.strategies:
                    annual_return = self.strategies[strategy_name]['performance_metrics']['annual_return']
                    total_return += weight * annual_return
            
            if total_return < 0.08:
                validation['checks']['return_target'] = 'warning'
                validation['recommendations'].append("预期收益低于8%，建议调整策略权重")
            else:
                validation['checks']['return_target'] = 'pass'
            
            # 整体评估
            failed_checks = sum(1 for check in validation['checks'].values() if check == 'warning')
            if failed_checks == 0:
                validation['overall_assessment'] = 'excellent'
            elif failed_checks <= 1:
                validation['overall_assessment'] = 'good'
            else:
                validation['overall_assessment'] = 'needs_improvement'
            
            return validation
            
        except Exception as e:
            logger.error(f"整合验证失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_integration_summary(self) -> Dict:
        """获取整合总结"""
        if not self.integration_history:
            return {'message': '暂无整合历史'}
        
        latest_integration = self.integration_history[-1]
        
        # 计算权重变化统计
        weight_changes = []
        for i in range(1, len(self.integration_history)):
            prev_weights = self.integration_history[i-1]['original_weights']
            curr_weights = self.integration_history[i]['original_weights']
            
            total_change = sum(abs(prev_weights[s] - curr_weights[s]) 
                             for s in prev_weights.keys())
            weight_changes.append(total_change)
        
        avg_weight_change = np.mean(weight_changes) if weight_changes else 0.0
        
        # 计算组合绩效
        portfolio_performance = {}
        if 'portfolio' in latest_integration:
            portfolio = latest_integration['portfolio']
            if 'risk_metrics' in portfolio:
                risk_metrics = portfolio['risk_metrics']
                portfolio_performance.update(risk_metrics)
        
        # 验证通过率
        validation_pass_rate = 0.0
        if 'validation' in latest_integration:
            validation = latest_integration['validation']
            if validation['success']:
                total_checks = len(validation['checks'])
                passed_checks = sum(1 for result in validation['checks'].values() if result == 'pass')
                validation_pass_rate = passed_checks / total_checks if total_checks > 0 else 1.0
        
        return {
            'current_weights': self.current_weights,
            'integration_count': len(self.integration_history),
            'latest_integration_time': latest_integration['timestamp'],
            'average_weight_change': avg_weight_change,
            'portfolio_performance': portfolio_performance,
            'validation_pass_rate': validation_pass_rate,
            'latest_assessment': latest_integration['validation']['overall_assessment'],
            'next_optimization_time': self._get_next_optimization_time()
        }
    
    def _get_next_optimization_time(self) -> str:
        """获取下次优化时间"""
        return (datetime.now() + timedelta(days=1)).isoformat()

class BacktestEngine:
    """
    回测引擎
    """
    
    def __init__(self, total_capital: float = 1000000):
        self.total_capital = total_capital
        self.backtest_results = deque(maxlen=50)
        
        # 回测场景
        self.test_scenarios = {
            'normal_market': {
                'name': '正常市场',
                'market_returns': np.random.normal(0.0003, 0.01, 252),
                'volatility': 0.15
            },
            'volatile_market': {
                'name': '高波动市场',
                'market_returns': np.random.normal(0.0001, 0.03, 252),
                'volatility': 0.30
            },
            'crisis_market': {
                'name': '危机市场',
                'market_returns': np.random.normal(-0.002, 0.05, 252),
                'volatility': 0.50
            }
        }
        
        logger.info("回测引擎初始化完成")
    
    def run_backtest(self, strategy_weights: Dict, scenario: str = 'normal_market') -> Dict:
        """
        运行回测
        
        Args:
            strategy_weights: 策略权重
            scenario: 回测场景
            
        Returns:
            回测结果
        """
        try:
            logger.info(f"开始回测: {scenario}")
            
            if scenario not in self.test_scenarios:
                return {'success': False, 'error': f'未知场景: {scenario}'}
            
            # 获取场景数据
            scenario_data = self.test_scenarios[scenario]
            market_returns = scenario_data['market_returns']
            
            # 模拟策略收益
            strategy_returns = self._simulate_strategy_returns(strategy_weights)
            
            # 计算组合收益
            portfolio_returns = self._calculate_portfolio_returns(strategy_weights, strategy_returns)
            
            # 计算绩效指标
            performance_metrics = self._calculate_performance_metrics(portfolio_returns)
            
            # 计算风险指标
            risk_metrics = self._calculate_risk_metrics(portfolio_returns)
            
            # 生成回测结果
            backtest_result = {
                'scenario': scenario,
                'scenario_name': scenario_data['name'],
                'strategy_weights': strategy_weights,
                'performance_metrics': performance_metrics,
                'risk_metrics': risk_metrics,
                'portfolio_returns': portfolio_returns.tolist(),
                'timestamp': datetime.now().isoformat()
            }
            
            self.backtest_results.append(backtest_result)
            
            logger.info(f"回测完成: {scenario}")
            return {
                'success': True,
                'backtest_result': backtest_result
            }
            
        except Exception as e:
            logger.error(f"回测失败: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _simulate_strategy_returns(self, weights: Dict) -> Dict:
        """模拟策略收益"""
        strategy_returns = {}
        
        # 模拟各策略的历史收益
        for strategy, weight in weights.items():
            if strategy == 'delta_hedge':
                returns = np.random.normal(0.00032, 0.008, 252)
            elif strategy == 'volatility_hedge':
                returns = np.random.normal(0.00024, 0.012, 252)
            else:  # tail_risk_hedge
                returns = np.random.normal(0.00016, 0.016, 252)
            
            strategy_returns[strategy] = returns
        
        return strategy_returns
    
    def _calculate_portfolio_returns(self, weights: Dict, 
                                    strategy_returns: Dict) -> np.ndarray:
        """计算组合收益"""
        portfolio_returns = np.zeros(252)
        
        for strategy, weight in weights.items():
            if strategy in strategy_returns:
                portfolio_returns += weight * strategy_returns[strategy]
        
        return portfolio_returns
    
    def _calculate_performance_metrics(self, returns: np.ndarray) -> Dict:
        """计算绩效指标"""
        try:
            annual_return = np.mean(returns) * 252
            annual_volatility = np.std(returns) * np.sqrt(252)
            sharpe_ratio = annual_return / annual_volatility if annual_volatility > 0 else 0.0
            max_drawdown = calculate_max_drawdown(returns)
            win_rate = np.mean(returns > 0)
            
            # 计算月度收益
            monthly_returns = np.reshape(returns, (12, 21))
            monthly_returns = np.mean(monthly_returns, axis=1)
            monthly_volatility = np.std(monthly_returns) * np.sqrt(12)
            
            return {
                'annual_return': annual_return,
                'annual_volatility': annual_volatility,
                'sharpe_ratio': sharpe_ratio,
                'max_drawdown': max_drawdown,
                'win_rate': win_rate,
                'monthly_return': np.mean(monthly_returns),
                'monthly_volatility': monthly_volatility,
                'calmar_ratio': annual_return / max_drawdown if max_drawdown > 0 else 0.0
            }
            
        except Exception as e:
            logger.error(f"绩效指标计算失败: {e}")
            return {}
    
    def _calculate_risk_metrics(self, returns: np.ndarray) -> Dict:
        """计算风险指标"""
        try:
            var_95 = calculate_var(returns, 0.95)
            var_99 = calculate_var(returns, 0.99)
            es_95 = calculate_es(returns, 0.95)
            es_99 = calculate_es(returns, 0.99)
            
            # 计算下行风险
            downside_returns = returns[returns < 0]
            downside_deviation = np.std(downside_returns) if len(downside_returns) > 0 else 0.0
            
            return {
                'var_95': var_95,
                'var_99': var_99,
                'es_95': es_95,
                'es_99': es_99,
                'downside_deviation': downside_deviation,
                'upside_potential': np.mean(returns[returns > 0]) if len(returns[returns > 0]) > 0 else 0.0
            }
            
        except Exception as e:
            logger.error(f"风险指标计算失败: {e}")
            return {}
    
    def compare_scenarios(self, strategy_weights: Dict) -> Dict:
        """比较不同场景"""
        try:
            scenario_results = {}
            
            for scenario_name in self.test_scenarios.keys():
                result = self.run_backtest(strategy_weights, scenario_name)
                if result['success']:
                    scenario_results[scenario_name] = result['backtest_result']
            
            # 比较分析
            comparison = self._compare_scenarios_results(scenario_results)
            
            return {
                'success': True,
                'scenario_results': scenario_results,
                'comparison': comparison
            }
            
        except Exception as e:
            logger.error(f"场景比较失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def _compare_scenarios_results(self, results: Dict) -> Dict:
        """比较场景结果"""
        comparison = {
            'performance_comparison': {},
            'risk_comparison': {},
            'robustness_assessment': 'unknown',
            'recommendations': []
        }
        
        if not results:
            return comparison
        
        # 比较绩效
        performance_keys = ['annual_return', 'sharpe_ratio', 'win_rate']
        for key in performance_keys:
            values = {name: result['performance_metrics'][key] for name, result in results.items()}
            comparison['performance_comparison'][key] = values
        
        # 比较风险
        risk_keys = ['var_95', 'max_drawdown', 'downside_deviation']
        for key in risk_keys:
            values = {name: result['risk_metrics'][key] for name, result in results.items()}
            comparison['risk_comparison'][key] = values
        
        # 评估稳健性
        avg_return = np.mean([r['performance_metrics']['annual_return'] for r in results.values()])
        avg_volatility = np.mean([r['performance_metrics']['annual_volatility'] for r in results.values()])
        avg_drawdown = np.mean([r['risk_metrics']['max_drawdown'] for r in results.values()])
        
        # 计算稳健性分数
        robustness_score = 0.0
        if avg_return >= 0.08:
            robustness_score += 0.4
        if avg_volatility <= 0.20:
            robustness_score += 0.3
        if avg_drawdown <= 0.15:
            robustness_score += 0.3
        
        if robustness_score >= 0.7:
            comparison['robustness_assessment'] = 'excellent'
        elif robustness_score >= 0.5:
            comparison['robustness_assessment'] = 'good'
        else:
            comparison['robustness_assessment'] = 'poor'
        
        # 生成建议
        if robustness_score < 0.5:
            comparison['recommendations'].append("策略稳健性较差，建议增加防御性资产")
        if avg_return < 0.08:
            comparison['recommendations'].append("收益偏低，建议调整策略权重")
        if avg_drawdown > 0.15:
            comparison['recommendations'].append("回撤过大，建议加强风险控制")
        
        return comparison
    
    def get_backtest_summary(self) -> Dict:
        """获取回测总结"""
        if not self.backtest_results:
            return {'message': '暂无回测结果'}
        
        latest_result = self.backtest_results[-1]
        
        # 统计回测结果
        total_backtests = len(self.backtest_results)
        successful_backtests = sum(1 for r in self.backtest_results if 'performance_metrics' in r)
        
        # 计算平均绩效
        avg_return = np.mean([
            r['performance_metrics']['annual_return'] 
            for r in self.backtest_results 
            if 'performance_metrics' in r
        ])
        
        avg_sharpe = np.mean([
            r['performance_metrics']['sharpe_ratio'] 
            for r in self.backtest_results 
            if 'performance_metrics' in r
        ])
        
        avg_drawdown = np.mean([
            r['risk_metrics']['max_drawdown'] 
            for r in self.backtest_results 
            if 'risk_metrics' in r
        ])
        
        return {
            'total_backtests': total_backtests,
            'successful_backtests': successful_backtests,
            'latest_scenario': latest_result['scenario_name'],
            'latest_time': latest_result['timestamp'],
            'average_return': avg_return,
            'average_sharpe': avg_sharpe,
            'average_max_drawdown': avg_drawdown,
            'latest_performance': latest_result['performance_metrics']
        }

class RealTimeOptimizer:
    """
    实时优化器
    """
    
    def __init__(self):
        self.optimization_history = deque(maxlen=100)
        self.optimization_params = {
            'optimization_frequency': 3600,  # 1小时
            'lookback_period': 63,  # 3个月
            'rebalance_threshold': 0.1,  # 10%
            'max_position_size': 0.4,
            'risk_budget_adjustment': 0.05
        }
        
        # 优化状态
        self.optimization_enabled = False
        self.optimization_thread = None
        
        logger.info("实时优化器初始化完成")
    
    def enable_optimization(self):
        """启用实时优化"""
        self.optimization_enabled = True
        self.optimization_thread = threading.Thread(target=self._optimization_loop)
        self.optimization_thread.daemon = True
        self.optimization_thread.start()
        logger.info("实时优化已启用")
    
    def disable_optimization(self):
        """禁用实时优化"""
        self.optimization_enabled = False
        if self.optimization_thread:
            self.optimization_thread.join()
        logger.info("实时优化已禁用")
    
    def _optimization_loop(self):
        """优化循环"""
        while self.optimization_enabled:
            try:
                logger.info("开始实时优化")
                
                # 执行优化
                optimization_result = self._run_real_time_optimization()
                
                # 记录历史
                if optimization_result['success']:
                    self.optimization_history.append({
                        'timestamp': datetime.now().isoformat(),
                        'optimization_result': optimization_result,
                        'type': 'real_time'
                    })
                
                logger.info("实时优化完成")
                
                # 等待下次优化
                time.sleep(self.optimization_params['optimization_frequency'])
                
            except Exception as e:
                logger.error(f"实时优化失败: {e}")
                time.sleep(self.optimization_params['optimization_frequency'])
    
    def _run_real_time_optimization(self) -> Dict:
        """运行实时优化"""
        try:
            # 模拟获取实时数据
            market_data = {
                'returns': np.random.normal(0.0003, 0.015, 252),
                'volatility': 0.15,
                'correlation_matrix': np.corrcoef(np.random.normal(0, 0.01, (3, 252)))
            }
            
            # 获取当前策略绩效
            current_performance = {
                'delta_hedge': np.random.normal(0.08, 0.12),
                'volatility_hedge': np.random.normal(0.06, 0.15),
                'tail_risk_hedge': np.random.normal(0.04, 0.20)
            }
            
            # 参数优化
            optimized_params = self._optimize_parameters(
                market_data, current_performance
            )
            
            # 风险预算调整
            adjusted_risk_budgets = self._adjust_risk_budgets(
                market_data, optimized_params
            )
            
            # 生成优化建议
            optimization_suggestions = self._generate_optimization_suggestions(
                optimized_params, current_performance
            )
            
            return {
                'success': True,
                'optimized_parameters': optimized_params,
                'adjusted_risk_budgets': adjusted_risk_budgets,
                'optimization_suggestions': optimization_suggestions,
                'improvement_score': self._calculate_improvement_score(
                    optimized_params, current_performance
                )
            }
            
        except Exception as e:
            logger.error(f"实时优化失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def _optimize_parameters(self, market_data: Dict, 
                           current_performance: Dict) -> Dict:
        """优化参数"""
        try:
            # 模拟参数优化
            optimized_params = {}
            
            for strategy, current_return in current_performance.items():
                # 基于市场条件和当前表现调整参数
                volatility = market_data['volatility']
                
                # 如果当前表现不佳，增加参数调整
                if current_return < 0.06:
                    adjustment_factor = 1.2
                else:
                    adjustment_factor = 1.0
                
                # 基于波动率调整
                volatility_adjustment = 1.0 + (volatility - 0.15) * 0.5
                
                # 计算优化后的参数
                optimized_params[strategy] = {
                    'target_return': current_return * adjustment_factor * volatility_adjustment,
                    'risk_tolerance': min(0.15, current_return * 2),
                    'rebalance_frequency': 'daily' if volatility > 0.20 else 'weekly'
                }
            
            logger.info("参数优化完成")
            return optimized_params
            
        except Exception as e:
            logger.error(f"参数优化失败: {e}")
            return {}
    
    def _adjust_risk_budgets(self, market_data: Dict, 
                           optimized_params: Dict) -> Dict:
        """调整风险预算"""
        try:
            adjusted_budgets = {}
            total_risk_budget = 0.08
            
            volatility = market_data['volatility']
            
            # 基于波动率调整总风险预算
            if volatility > 0.30:
                adjusted_total_risk = total_risk_budget * 0.8
            elif volatility > 0.20:
                adjusted_total_risk = total_risk_budget * 0.9
            else:
                adjusted_total_risk = total_risk_budget
            
            # 分配各策略风险预算
            for strategy, params in optimized_params.items():
                target_risk = params['risk_tolerance']
                
                # 基于调整后的总风险预算重新分配
                strategy_risk = target_risk * adjusted_total_risk / 0.08
                strategy_risk = min(strategy_risk, adjusted_total_risk * 0.6)
                
                adjusted_budgets[strategy] = strategy_risk
            
            # 确保总和
            total_allocated = sum(adjusted_budgets.values())
            if total_allocated > 0:
                scaling_factor = adjusted_total_risk / total_allocated
                adjusted_budgets = {k: v * scaling_factor for k, v in adjusted_budgets.items()}
            
            logger.info("风险预算调整完成")
            return adjusted_budgets
            
        except Exception as e:
            logger.error(f"风险预算调整失败: {e}")
            return {}
    
    def _generate_optimization_suggestions(self, optimized_params: Dict, 
                                        current_performance: Dict) -> List[str]:
        """生成优化建议"""
        suggestions = []
        
        # 检查各策略优化
        for strategy, params in optimized_params.items():
            current_return = current_performance[strategy]
            target_return = params['target_return']
            
            if current_return < target_return * 0.9:
                suggestions.append(
                    f"{strategy}表现低于预期，建议调整策略参数"
                )
        
        # 检查风险控制
        if any(params['risk_tolerance'] > 0.15 for params in optimized_params.values()):
            suggestions.append("部分策略风险容忍度过高，建议加强风险控制")
        
        # 检查再平衡频率
        rebalance_frequencies = [params['rebalance_frequency'] for params in optimized_params.values()]
        if any(freq == 'daily' for freq in rebalance_frequencies):
            suggestions.append("检测到高波动情况，建议提高再平衡频率")
        
        if not suggestions:
            suggestions.append("策略参数优化良好，建议保持当前配置")
        
        return suggestions
    
    def _calculate_improvement_score(self, optimized_params: Dict, 
                                   current_performance: Dict) -> float:
        """计算改进分数"""
        try:
            total_improvement = 0.0
            
            for strategy, params in optimized_params.items():
                current_return = current_performance[strategy]
                target_return = params['target_return']
                
                improvement_ratio = target_return / current_return if current_return > 0 else 1.0
                total_improvement += improvement_ratio
            
            avg_improvement = total_improvement / len(optimized_params)
            return min(avg_improvement - 1.0, 1.0)
            
        except Exception as e:
            logger.error(f"改进分数计算失败: {e}")
            return 0.0
    
    def get_optimization_summary(self) -> Dict:
        """获取优化总结"""
        if not self.optimization_history:
            return {'message': '暂无优化历史'}
        
        latest_optimization = self.optimization_history[-1]
        
        # 统计优化结果
        total_optimizations = len(self.optimization_history)
        successful_optimizations = sum(1 for o in self.optimization_history 
                                    if o['optimization_result']['success'])
        
        avg_improvement = np.mean([
            o['optimization_result'].get('improvement_score', 0.0)
            for o in self.optimization_history
            if o['optimization_result']['success']
        ])
        
        return {
            'optimization_enabled': self.optimization_enabled,
            'total_optimizations': total_optimizations,
            'successful_optimizations': successful_optimizations,
            'success_rate': successful_optimizations / total_optimizations if total_optimizations > 0 else 0.0,
            'average_improvement_score': avg_improvement,
            'latest_optimization_time': latest_optimization['timestamp'],
            'latest_improvement': latest_optimization['optimization_result'].get('improvement_score', 0.0),
            'next_optimization_time': self._get_next_optimization_time()
        }
    
    def _get_next_optimization_time(self) -> str:
        """获取下次优化时间"""
        return (datetime.now() + timedelta(hours=1)).isoformat()


class StrategyOptimizer:
    """
    策略优化器 - 主控制器
    """
    
    def __init__(self, total_capital: float = 1000000):
        self.total_capital = total_capital
        
        # 初始化组件
        self.strategy_integrator = StrategyIntegrator(total_capital)
        self.backtest_engine = BacktestEngine(total_capital)
        self.real_time_optimizer = RealTimeOptimizer()
        
        # 系统状态
        self.optimizer_enabled = False
        
        # 系统历史
        self.optimizer_history = deque(maxlen=100)
        
        # 配置参数
        self.config = {
            'auto_optimization': True,
            'optimization_frequency_hours': 1,
            'backtest_scenarios': ['normal_market', 'volatile_market'],
            'real_time_monitoring': True,
            'risk_controls_enabled': True,
            'performance_targets': {
                'minimum_return': 0.08,
                'maximum_drawdown': 0.15,
                'minimum_sharpe': 0.5
            }
        }
        
        logger.info(f"策略优化器初始化完成，总资本: {total_capital:,.0f}元")
    
    def start_optimization(self):
        """启动优化器"""
        self.optimizer_enabled = True
        
        # 启动实时优化
        if self.config['auto_optimization']:
            self.real_time_optimizer.enable_optimization()
        
        logger.info("策略优化器启动")
    
    def stop_optimization(self):
        """停止优化器"""
        self.optimizer_enabled = False
        
        # 停止实时优化
        self.real_time_optimizer.disable_optimization()
        
        logger.info("策略优化器停止")
    
    def run_full_optimization(self, market_data: Dict, 
                            performance_data: Dict) -> Dict:
        """
        运行完整优化流程
        
        Args:
            market_data: 市场数据
            performance_data: 绩效数据
            
        Returns:
            优化结果
        """
        try:
            logger.info("开始完整优化流程")
            
            optimization_steps = []
            
            # 1. 策略整合
            integration_result = self.strategy_integrator.integrate_strategies(
                market_data, performance_data
            )
            optimization_steps.append({
                'step': 'strategy_integration',
                'result': integration_result
            })
            
            if not integration_result['success']:
                logger.error("策略整合失败")
                return {
                    'success': False,
                    'error': '策略整合失败',
                    'optimization_steps': optimization_steps
                }
            
            # 2. 回测验证
            current_weights = integration_result['optimized_weights']
            backtest_results = {}
            
            for scenario in self.config['backtest_scenarios']:
                backtest_result = self.backtest_engine.run_backtest(current_weights, scenario)
                backtest_results[scenario] = backtest_result
                
                if not backtest_result['success']:
                    logger.warning(f"回测失败: {scenario}")
            
            optimization_steps.append({
                'step': 'backtesting',
                'results': backtest_results
            })
            
            # 3. 场景比较
            scenario_comparison = self.backtest_engine.compare_scenarios(current_weights)
            optimization_steps.append({
                'step': 'scenario_comparison',
                'result': scenario_comparison
            })
            
            # 4. 实时优化检查
            real_time_optimization = {}
            if self.config['real_time_monitoring']:
                real_time_result = self.real_time_optimizer._run_real_time_optimization()
                real_time_optimization = real_time_result
            
            optimization_steps.append({
                'step': 'real_time_optimization',
                'result': real_time_optimization
            })
            
            # 5. 综合决策
            final_decision = self._make_optimization_decision(
                integration_result, backtest_results, scenario_comparison, real_time_optimization
            )
            optimization_steps.append({
                'step': 'final_decision',
                'result': final_decision
            })
            
            # 6. 更新配置
            if final_decision['success']:
                self._update_optimization_config(final_decision['updated_weights'])
            
            # 记录历史
            optimization_record = {
                'timestamp': datetime.now().isoformat(),
                'market_data': market_data,
                'performance_data': performance_data,
                'optimization_steps': optimization_steps,
                'final_decision': final_decision,
                'total_time': self._calculate_optimization_time(optimization_steps)
            }
            
            self.optimizer_history.append(optimization_record)
            
            logger.info("完整优化流程完成")
            
            return {
                'success': True,
                'optimization_steps': optimization_steps,
                'final_decision': final_decision,
                'integration_result': integration_result,
                'backtest_results': backtest_results,
                'optimization_record': optimization_record
            }
            
        except Exception as e:
            logger.error(f"完整优化流程失败: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _make_optimization_decision(self, integration_result: Dict, 
                                 backtest_results: Dict, 
                                 scenario_comparison: Dict,
                                 real_time_optimization: Dict) -> Dict:
        """做出优化决策"""
        try:
            decision = {
                'success': True,
                'decision': 'accept',
                'reasoning': [],
                'updated_weights': integration_result['optimized_weights'],
                'risk_budgets': integration_result['risk_budgets'],
                'performance_targets_met': True,
                'recommendations': []
            }
            
            # 检查回测结果
            for scenario, result in backtest_results.items():
                if result['success']:
                    perf = result['backtest_result']['performance_metrics']
                    
                    # 检查收益目标
                    if perf['annual_return'] < self.config['performance_targets']['minimum_return']:
                        decision['performance_targets_met'] = False
                        decision['reasoning'].append(
                            f"{scenario}收益未达标: {perf['annual_return']:.2%}"
                        )
                    
                    # 检查风险目标
                    if perf['max_drawdown'] > self.config['performance_targets']['maximum_drawdown']:
                        decision['performance_targets_met'] = False
                        decision['reasoning'].append(
                            f"{scenario}回撤超限: {perf['max_drawdown']:.2%}"
                        )
                    
                    # 检查夏普比率
                    if perf['sharpe_ratio'] < self.config['performance_targets']['minimum_sharpe']:
                        decision['performance_targets_met'] = False
                        decision['reasoning'].append(
                            f"{scenario}夏普比率过低: {perf['sharpe_ratio']:.2f}"
                        )
            
            # 检查场景比较结果
            if scenario_comparison['success']:
                robustness = scenario_comparison.get('comparison', {}).get('robustness_assessment', 'unknown')
                if robustness in ['poor', 'unknown']:
                    decision['performance_targets_met'] = False
                    decision['reasoning'].append("策略稳健性不足")
                    decision['recommendations'].extend(
                        scenario_comparison.get('comparison', {}).get('recommendations', [])
                    )
            
            # 检查实时优化结果
            if real_time_optimization.get('success'):
                improvement = real_time_optimization.get('improvement_score', 0.0)
                if improvement > 0.1:
                    decision['recommendations'].append("检测到显著优化机会，建议调整参数")
            
            # 基于决策结果确定最终行动
            if decision['performance_targets_met']:
                decision['action'] = 'implement'
            else:
                decision['action'] = 'review'
                decision['updated_weights'] = None
            
            return decision
            
        except Exception as e:
            logger.error(f"优化决策失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def _update_optimization_config(self, new_weights: Dict):
        """更新优化配置"""
        if new_weights:
            self.strategy_integrator.current_weights = new_weights.copy()
            logger.info("优化配置已更新")
    
    def _calculate_optimization_time(self, steps: List[Dict]) -> float:
        """计算优化时间"""
        # 简化处理，返回固定值
        return 30.0  # 假设每次优化需要30秒
    
    def get_optimizer_summary(self) -> Dict:
        """获取优化器总结"""
        return {
            'optimizer_enabled': self.optimizer_enabled,
            'total_optimizations': len(self.optimizer_history),
            'last_optimization_time': self.optimizer_history[-1]['timestamp'] if self.optimizer_history else None,
            
            # 组件状态
            'integration_summary': self.strategy_integrator.get_integration_summary(),
            'backtest_summary': self.backtest_engine.get_backtest_summary(),
            'real_time_optimizer_summary': self.real_time_optimizer.get_optimization_summary(),
            
            # 性能目标
            'performance_targets': self.config['performance_targets'],
            
            # 系统配置
            'config': self.config,
            
            # 最近优化记录
            'latest_optimization': self.optimizer_history[-1] if self.optimizer_history else None
        }


# 主程序
if __name__ == "__main__":
    print("策略优化器启动")
    print("=" * 50)
    
    # 创建策略优化器
    strategy_optimizer = StrategyOptimizer(total_capital=1000000)
    
    # 启动优化器
    strategy_optimizer.start_optimization()
    
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
        'sentiment_score': 0.2,
        'tracking_error': 0.03,
        'market_correlation': 0.7,
        'returns': np.random.normal(0.0003, 0.01, 252)
    }
    
    # 模拟绩效数据
    performance_data = {
        'returns': np.random.normal(0.0003, 0.01, 252),
        'annual_return': 0.08,
        'volatility': 0.15,
        'max_drawdown': 0.08,
        'sharpe_ratio': 0.53
    }
    
    # 运行完整优化
    optimization_result = strategy_optimizer.run_full_optimization(
        market_data, performance_data
    )
    
    print("\n优化完成")
    print("=" * 50)
    
    # 输出结果
    if optimization_result['success']:
        print("优化结果:")
        
        # 策略整合结果
        integration = optimization_result['integration_result']
        if integration['success']:
            print("\n策略整合:")
            print(f"原始权重: {integration['original_weights']}")
            print(f"优化权重: {integration['optimized_weights']}")
            print(f"风险预算: {integration['risk_budgets']}")
        
        # 回测结果
        backtest_results = optimization_result['backtest_results']
        print("\n回测结果:")
        for scenario, result in backtest_results.items():
            if result['success']:
                perf = result['backtest_result']['performance_metrics']
                print(f"  {scenario}: 收益={perf['annual_return']:.2%}, "
                      f"夏普={perf['sharpe_ratio']:.2f}, "
                      f"最大回撤={perf['max_drawdown']:.2%}")
        
        # 最终决策
        decision = optimization_result['final_decision']
        print(f"\n最终决策: {decision['action']}")
        if decision['reasoning']:
            print("决策依据:")
            for reason in decision['reasoning']:
                print(f"  - {reason}")
        
        # 系统总结
        summary = strategy_optimizer.get_optimizer_summary()
        print(f"\n系统总结:")
        print(f"  优化次数: {summary['total_optimizations']}")
        print(f"  当前权重: {summary['integration_summary']['current_weights']}")
        print(f"  平均收益: {summary['backtest_summary']['average_return']:.2%}")
        print(f"  实时优化状态: {'启用' if summary['real_time_optimizer_summary']['optimization_enabled'] else '禁用'}")
        
    else:
        print(f"优化失败: {optimization_result['error']}")