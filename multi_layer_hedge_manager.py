# -*- coding: utf-8 -*-
"""
多层次对冲管理器 - 世界级对冲基金的对冲系统
整合三层保护策略，实现全方位风险管理

系统架构：
- 第一层：动态Delta对冲 - 日常市场风险保护
- 第二层：波动率对冲 - 波动率异常保护
- 第三层：尾部风险对冲 - 极端市场事件保护

特点：
- 智能资金配置
- 动态风险预算
- 实时监控和预警
- 自动执行机制
- 压力测试和回测
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import json
import os

try:
    from utils.logger import get_logger
    from utils.data_provider import get_market_data
    from utils.risk_metrics import calculate_var, calculate_es, calculate_max_drawdown
    from utils.performance_metrics import calculate_sharpe_ratio, calculate_sortino_ratio
    logger = get_logger('multi_layer_hedge')
except ImportError:
    import logging
    logger = logging.getLogger('multi_layer_hedge')

from enhanced_delta_hedge import EnhancedDeltaHedge
from volatility_hedge import VolatilityHedge
from tail_risk_hedge import TailRiskHedge

class MultiLayerHedgeManager:
    """
    多层次对冲管理器 - 世界级对冲基金的核心系统
    
    整合三层保护策略，实现全方位风险管理：
    1. 第一层：动态Delta对冲 - 日常市场风险保护
    2. 第二层：波动率对冲 - 波动率异常保护
    3. 第三层：尾部风险对冲 - 极端市场事件保护
    """
    
    def __init__(self, total_capital: float = 5000000, hedge_ratio: float = 0.20):
        """
        初始化多层次对冲管理器
        
        Args:
            total_capital: 总资金（500万）
            hedge_ratio: 对冲资金比例（20%）
        """
        self.total_capital = total_capital
        self.hedge_capital = total_capital * hedge_ratio  # 100万对冲资金
        self.investment_capital = total_capital - self.hedge_capital  # 400万投资资金
        
        # 初始化三层对冲策略
        self.delta_hedge = EnhancedDeltaHedge(self.hedge_capital * 0.6)  # 60%用于Delta对冲
        self.volatility_hedge = VolatilityHedge(self.hedge_capital * 0.3)  # 30%用于波动率对冲
        self.tail_risk_hedge = TailRiskHedge(self.hedge_capital * 0.1)  # 10%用于尾部风险对冲
        
        # 资金分配配置
        self.capital_allocation = {
            'delta_hedge': 0.60,    # 60%用于Delta对冲
            'volatility_hedge': 0.30,  # 30%用于波动率对冲
            'tail_risk_hedge': 0.10,   # 10%用于尾部风险对冲
            'reserve': 0.05         # 5%作为应急储备
        }
        
        # 风险预算配置
        self.risk_budget = {
            'market_risk': 0.40,    # 市场风险预算40%
            'volatility_risk': 0.35,  # 波动率风险预算35%
            'tail_risk': 0.25      # 尾部风险预算25%
        }
        
        # 系统配置
        self.system_config = {
            'auto_execution': True,       # 自动执行
            'execution_time': '07:00',    # 执行时间
            'monitoring_frequency': '5min',  # 监控频率
            'rebalance_frequency': 'daily',  # 再平衡频率
            'early_warning_threshold': 0.7,  # 预警阈值
            'emergency_stop_loss': 0.15    # 紧急止损
        }
        
        # 状态监控
        self.current_state = {
            'market_regime': 'normal',
            'overall_risk_level': 0.0,
            'hedge_effectiveness': 0.0,
            'portfolio_performance': 0.0,
            'last_execution': None,
            'alerts': []
        }
        
        # 历史数据
        self.performance_history = []
        self.risk_history = []
        self.execution_history = []
        
        # 初始化完成
        logger.info(f"多层次对冲管理器初始化完成")
        logger.info(f"总资金: {total_capital:,.0f}元")
        logger.info(f"投资资金: {self.investment_capital:,.0f}元")
        logger.info(f"对冲资金: {self.hedge_capital:,.0f}元")
        logger.info(f"资金配置: {self.capital_allocation}")
        
    def analyze_market_regime(self, market_data: Dict) -> str:
        """
        综合分析市场状态
        
        Args:
            market_data: 市场数据
            
        Returns:
            市场状态：'normal', 'warning', 'crisis', 'recovery'
        """
        try:
            # 从各层策略获取市场状态
            delta_regime = self.delta_hedge.analyze_market_regime(market_data)
            volatility_regime = self.volatility_hedge.detect_market_regime(market_data)
            tail_regime = self.tail_risk_hedge.market_regime
            
            # 综合判断市场状态
            regime_scores = {
                'delta_regime': delta_regime,
                'volatility_regime': volatility_regime,
                'tail_regime': tail_regime
            }
            
            # 计算综合状态
            crisis_count = sum(1 for r in regime_scores.values() if r == 'crisis')
            warning_count = sum(1 for r in regime_scores.values() if r == 'warning')
            
            if crisis_count >= 2:
                overall_regime = 'crisis'
            elif crisis_count >= 1 or warning_count >= 2:
                overall_regime = 'warning'
            elif warning_count >= 1:
                overall_regime = 'recovery'
            else:
                overall_regime = 'normal'
            
            self.current_state['market_regime'] = overall_regime
            logger.info(f"综合市场状态分析: {overall_regime}")
            
            return overall_regime
            
        except Exception as e:
            logger.error(f"市场状态分析失败: {e}")
            return 'normal'
    
    def calculate_optimal_allocation(self, market_data: Dict) -> Dict:
        """
        计算最优资金分配
        
        Args:
            market_data: 市场数据
            
        Returns:
            最优资金分配方案
        """
        try:
            # 分析市场状态
            market_regime = self.analyze_market_regime(market_data)
            
            # 动态调整资金分配
            if market_regime == 'crisis':
                # 危机时期：增加尾部风险保护
                allocation = {
                    'delta_hedge': 0.40,    # 降低Delta对冲比例
                    'volatility_hedge': 0.30,  # 保持波动率对冲
                    'tail_risk_hedge': 0.25,   # 增加尾部风险保护
                    'reserve': 0.05          # 保持应急储备
                }
            elif market_regime == 'warning':
                # 警告时期：增加波动率对冲
                allocation = {
                    'delta_hedge': 0.50,    # 适度降低Delta对冲
                    'volatility_hedge': 0.40,  # 增加波动率对冲
                    'tail_risk_hedge': 0.05,   # 减少尾部风险保护
                    'reserve': 0.05
                }
            elif market_regime == 'recovery':
                # 恢复时期：正常配置，关注Delta对冲
                allocation = {
                    'delta_hedge': 0.60,    # 增加Delta对冲
                    'volatility_hedge': 0.25,  # 减少波动率对冲
                    'tail_risk_hedge': 0.10,   # 正常尾部风险保护
                    'reserve': 0.05
                }
            else:
                # 正常时期：均衡配置
                allocation = self.capital_allocation.copy()
            
            # 基于风险指标微调
            risk_adjustment = self._adjust_for_risk_factors(market_data, allocation)
            allocation.update(risk_adjustment)
            
            # 确保总和为1
            total = sum(allocation.values())
            allocation = {k: v / total for k, v in allocation.items()}
            
            logger.info(f"最优资金分配计算完成: {allocation}")
            return allocation
            
        except Exception as e:
            logger.error(f"资金分配计算失败: {e}")
            return self.capital_allocation.copy()
    
    def _adjust_for_risk_factors(self, market_data: Dict, base_allocation: Dict) -> Dict:
        """
        基于风险因素调整资金分配
        
        Args:
            market_data: 市场数据
            base_allocation: 基础分配方案
            
        Returns:
            调整后的分配方案
        """
        adjustments = {}
        
        # 基于波动率调整
        volatility = market_data.get('volatility', 0.15)
        if volatility > 0.25:  # 高波动率
            adjustments['volatility_hedge'] = base_allocation['volatility_hedge'] * 1.2
            adjustments['delta_hedge'] = base_allocation['delta_hedge'] * 0.9
        
        # 基于VaR调整
        var_95 = market_data.get('var_95', 0.02)
        if var_95 > 0.025:  # 高VaR
            adjustments['tail_risk_hedge'] = base_allocation['tail_risk_hedge'] * 1.5
            adjustments['volatility_hedge'] = base_allocation['volatility_hedge'] * 0.9
        
        # 基于流动性调整
        liquidity = market_data.get('liquidity', 1.0)
        if liquidity < 0.6:  # 低流动性
            adjustments['delta_hedge'] = base_allocation['delta_hedge'] * 0.8
            adjustments['reserve'] = base_allocation['reserve'] * 1.5
        
        return adjustments
    
    def execute_multi_layer_hedge(self, market_data: Dict) -> Dict:
        """
        执行多层次对冲策略
        
        Args:
            market_data: 市场数据
            
        Returns:
            执行结果
        """
        try:
            logger.info("开始执行多层次对冲策略")
            
            # 1. 分析市场状态
            market_regime = self.analyze_market_regime(market_data)
            
            # 2. 计算最优资金分配
            optimal_allocation = self.calculate_optimal_allocation(market_data)
            
            # 3. 执行第一层：Delta对冲
            delta_trades = self.delta_hedge.execute_strategy(market_data)
            delta_allocation = self.hedge_capital * optimal_allocation['delta_hedge']
            
            # 4. 执行第二层：波动率对冲
            volatility_trades = self.volatility_hedge.execute_strategy(market_data)
            volatility_allocation = self.hedge_capital * optimal_allocation['volatility_hedge']
            
            # 5. 执行第三层：尾部风险对冲
            tail_risk_needed = self.tail_risk_hedge.calculate_protection_needed(
                self.total_capital, market_data
            )
            tail_trades = self.tail_risk_hedge.execute_protection_strategy(
                tail_risk_needed, market_data
            )
            tail_allocation = self.hedge_capital * optimal_allocation['tail_risk_hedge']
            
            # 6. 整合交易指令
            all_trades = []
            
            # 添加Delta对冲交易
            if delta_trades:
                for trade in delta_trades:
                    trade['allocation'] = 'delta_hedge'
                    trade['allocated_capital'] = delta_allocation
                    all_trades.append(trade)
            
            # 添加波动率对冲交易
            if volatility_trades:
                for trade in volatility_trades:
                    trade['allocation'] = 'volatility_hedge'
                    trade['allocated_capital'] = volatility_allocation
                    all_trades.append(trade)
            
            # 添加尾部风险对冲交易
            if tail_trades:
                for trade in tail_trades:
                    trade['allocation'] = 'tail_risk_hedge'
                    trade['allocated_capital'] = tail_allocation
                    all_trades.append(trade)
            
            # 7. 计算执行效果
            execution_effectiveness = self._calculate_execution_effectiveness(
                all_trades, market_data
            )
            
            # 8. 更新系统状态
            self._update_system_state(market_data, execution_effectiveness)
            
            # 9. 记录执行历史
            execution_record = {
                'timestamp': datetime.now().isoformat(),
                'market_regime': market_regime,
                'optimal_allocation': optimal_allocation,
                'total_trades': len(all_trades),
                'capital_allocated': sum(t.get('allocated_capital', 0) for t in all_trades),
                'execution_effectiveness': execution_effectiveness,
                'trades_by_layer': {
                    'delta_hedge': len([t for t in all_trades if t['allocation'] == 'delta_hedge']),
                    'volatility_hedge': len([t for t in all_trades if t['allocation'] == 'volatility_hedge']),
                    'tail_risk_hedge': len([t for t in all_trades if t['allocation'] == 'tail_risk_hedge'])
                }
            }
            
            self.execution_history.append(execution_record)
            self.current_state['last_execution'] = execution_record
            
            logger.info(f"多层次对冲策略执行完成: {len(all_trades)} 个交易指令")
            
            return {
                'success': True,
                'market_regime': market_regime,
                'optimal_allocation': optimal_allocation,
                'trades': all_trades,
                'execution_effectiveness': execution_effectiveness,
                'execution_record': execution_record
            }
            
        except Exception as e:
            logger.error(f"多层次对冲策略执行失败: {e}")
            return {
                'success': False,
                'error': str(e),
                'market_regime': self.current_state['market_regime'],
                'trades': [],
                'execution_effectiveness': 0.0
            }
    
    def _calculate_execution_effectiveness(self, trades: List[Dict], market_data: Dict) -> float:
        """
        计算执行效果评分
        
        Args:
            trades: 交易指令列表
            market_data: 市场数据
            
        Returns:
            执行效果评分（0-1）
        """
        try:
            # 基础评分
            base_score = 0.5
            
            # 交易数量评分
            trade_count_score = min(len(trades) / 10, 1.0) * 0.2
            
            # 资金使用效率评分
            total_capital = sum(t.get('allocated_capital', 0) for t in trades)
            capital_efficiency = min(total_capital / self.hedge_capital, 1.0) * 0.3
            
            # 风险覆盖评分
            risk_coverage = self._calculate_risk_coverage(trades, market_data) * 0.3
            
            # 市场状态适应评分
            regime_score = self._calculate_regime_adaptation(trades, market_data) * 0.2
            
            # 综合评分
            effectiveness = base_score + trade_count_score + capital_efficiency + risk_coverage + regime_score
            
            return min(effectiveness, 1.0)
            
        except Exception as e:
            logger.error(f"执行效果计算失败: {e}")
            return 0.0
    
    def _calculate_risk_coverage(self, trades: List[Dict], market_data: Dict) -> float:
        """
        计算风险覆盖评分
        
        Args:
            trades: 交易指令列表
            market_data: 市场数据
            
        Returns:
            风险覆盖评分
        """
        try:
            # 计算各层对冲覆盖情况
            delta_coverage = 0.0
            volatility_coverage = 0.0
            tail_coverage = 0.0
            
            delta_trades = [t for t in trades if t['allocation'] == 'delta_hedge']
            volatility_trades = [t for t in trades if t['allocation'] == 'volatility_hedge']
            tail_trades = [t for t in trades if t['allocation'] == 'tail_risk_hedge']
            
            # Delta对冲覆盖
            if delta_trades:
                delta_positions = sum(abs(t.get('quantity', 0)) for t in delta_trades)
                delta_coverage = min(delta_positions / 10000, 1.0)  # 假设需要10000个单位
            
            # 波动率对冲覆盖
            if volatility_trades:
                vol_positions = sum(abs(t.get('quantity', 0)) for t in volatility_trades)
                volatility_coverage = min(vol_positions / 50, 1.0)  # 假设需要50个单位
            
            # 尾部风险覆盖
            if tail_trades:
                tail_capital = sum(t.get('capital_required', 0) for t in tail_trades)
                tail_coverage = min(abs(tail_capital) / (self.hedge_capital * 0.1), 1.0)  # 10%用于尾部保护
            
            # 综合风险覆盖
            total_coverage = (delta_coverage + volatility_coverage + tail_coverage) / 3
            
            return total_coverage
            
        except Exception as e:
            logger.error(f"风险覆盖计算失败: {e}")
            return 0.0
    
    def _calculate_regime_adaptation(self, trades: List[Dict], market_data: Dict) -> float:
        """
        计算市场状态适应评分
        
        Args:
            trades: 交易指令列表
            market_data: 市场数据
            
        Returns:
            市场状态适应评分
        """
        try:
            market_regime = self.analyze_market_regime(market_data)
            
            # 根据市场状态评估交易策略
            if market_regime == 'crisis':
                # 危机时期应优先尾部风险保护
                tail_trades = [t for t in trades if t['allocation'] == 'tail_risk_hedge']
                adaptation_score = min(len(tail_trades) / 5, 1.0)  # 至少5个尾部保护交易
            
            elif market_regime == 'warning':
                # 警告时期应增加波动率对冲
                vol_trades = [t for t in trades if t['allocation'] == 'volatility_hedge']
                adaptation_score = min(len(vol_trades) / 3, 1.0)  # 至少3个波动率对冲交易
            
            else:
                # 正常时期保持均衡
                total_trades = len(trades)
                adaptation_score = min(total_trades / 8, 1.0)  # 至少8个交易
            
            return adaptation_score
            
        except Exception as e:
            logger.error(f"市场状态适应计算失败: {e}")
            return 0.0
    
    def _update_system_state(self, market_data: Dict, execution_effectiveness: float):
        """
        更新系统状态
        
        Args:
            market_data: 市场数据
            execution_effectiveness: 执行效果评分
        """
        try:
            # 更新整体风险水平
            overall_risk = self._calculate_overall_risk(market_data)
            self.current_state['overall_risk_level'] = overall_risk
            
            # 更新对冲效果
            self.current_state['hedge_effectiveness'] = execution_effectiveness
            
            # 更新市场状态
            self.current_state['market_regime'] = self.analyze_market_regime(market_data)
            
            # 更新预警信息
            alerts = self._generate_alerts(market_data)
            self.current_state['alerts'] = alerts
            
            # 记录历史数据
            self._record_performance_data(market_data, execution_effectiveness)
            
        except Exception as e:
            logger.error(f"系统状态更新失败: {e}")
    
    def _calculate_overall_risk(self, market_data: Dict) -> float:
        """
        计算整体风险水平
        
        Args:
            market_data: 市场数据
            
        Returns:
            整体风险评分（0-1）
        """
        try:
            # 提取风险指标
            volatility = market_data.get('volatility', 0.15)
            var_95 = market_data.get('var_95', 0.02)
            es_95 = market_data.get('es_95', 0.03)
            liquidity = market_data.get('liquidity', 1.0)
            
            # 归一化风险指标
            vol_score = min(volatility / 0.30, 1.0)
            var_score = min(var_95 / 0.05, 1.0)
            es_score = min(es_95 / 0.08, 1.0)
            liquidity_score = max(0, (1.0 - liquidity) / 0.5)
            
            # 综合风险评分
            overall_risk = (
                vol_score * 0.3 +
                var_score * 0.3 +
                es_score * 0.3 +
                liquidity_score * 0.1
            )
            
            return min(overall_risk, 1.0)
            
        except Exception as e:
            logger.error(f"整体风险计算失败: {e}")
            return 0.0
    
    def _generate_alerts(self, market_data: Dict) -> List[Dict]:
        """
        生成预警信息
        
        Args:
            market_data: 市场数据
            
        Returns:
            预警信息列表
        """
        alerts = []
        
        # 高风险预警
        if self.current_state['overall_risk_level'] > 0.8:
            alerts.append({
                'type': 'HIGH_RISK',
                'severity': 'critical',
                'message': '系统整体风险过高',
                'value': self.current_state['overall_risk_level']
            })
        
        # 对冲效果预警
        if self.current_state['hedge_effectiveness'] < 0.5:
            alerts.append({
                'type': 'LOW_EFFECTIVENESS',
                'severity': 'high',
                'message': '对冲效果不佳',
                'value': self.current_state['hedge_effectiveness']
            })
        
        # 市场状态预警
        if self.current_state['market_regime'] == 'crisis':
            alerts.append({
                'type': 'CRISIS_MODE',
                'severity': 'critical',
                'message': '系统进入危机模式',
                'value': 1.0
            })
        
        return alerts
    
    def _record_performance_data(self, market_data: Dict, execution_effectiveness: float):
        """
        记录绩效数据
        
        Args:
            market_data: 市场数据
            execution_effectiveness: 执行效果
        """
        try:
            performance_record = {
                'timestamp': datetime.now().isoformat(),
                'overall_risk': self.current_state['overall_risk_level'],
                'hedge_effectiveness': execution_effectiveness,
                'market_regime': self.current_state['market_regime'],
                'portfolio_value': self.total_capital,  # 假设组合价值
                'risk_metrics': {
                    'volatility': market_data.get('volatility', 0.15),
                    'var_95': market_data.get('var_95', 0.02),
                    'es_95': market_data.get('es_95', 0.03),
                    'liquidity': market_data.get('liquidity', 1.0)
                }
            }
            
            self.performance_history.append(performance_record)
            
            # 保持最近100条记录
            if len(self.performance_history) > 100:
                self.performance_history = self.performance_history[-100:]
                
        except Exception as e:
            logger.error(f"绩效数据记录失败: {e}")
    
    def generate_system_report(self) -> Dict:
        """
        生成系统报告
        
        Returns:
            系统报告
        """
        try:
            # 计算历史统计数据
            if self.performance_history:
                avg_risk = np.mean([p['overall_risk'] for p in self.performance_history])
                avg_effectiveness = np.mean([p['hedge_effectiveness'] for p in self.performance_history])
                max_risk = np.max([p['overall_risk'] for p in self.performance_history])
            else:
                avg_risk = 0.0
                avg_effectiveness = 0.0
                max_risk = 0.0
            
            # 执行统计
            total_executions = len(self.execution_history)
            successful_executions = sum(1 for e in self.execution_history if e.get('success', False))
            
            # 当前状态
            current_state = self.current_state.copy()
            
            # 系统配置
            system_config = self.system_config.copy()
            
            report = {
                'system_name': '多层次对冲管理系统',
                'timestamp': datetime.now().isoformat(),
                'capital_summary': {
                    'total_capital': self.total_capital,
                    'investment_capital': self.investment_capital,
                    'hedge_capital': self.hedge_capital,
                    'allocation': self.capital_allocation
                },
                'current_status': current_state,
                'performance_statistics': {
                    'total_executions': total_executions,
                    'successful_executions': successful_executions,
                    'success_rate': successful_executions / total_executions if total_executions > 0 else 0,
                    'average_risk_level': avg_risk,
                    'maximum_risk_level': max_risk,
                    'average_effectiveness': avg_effectiveness
                },
                'system_configuration': system_config,
                'recent_executions': self.execution_history[-5:],  # 最近5次执行
                'historical_performance': self.performance_history[-10:],  # 最近10条绩效
                'alerts': current_state['alerts']
            }
            
            logger.info("系统报告生成完成")
            return report
            
        except Exception as e:
            logger.error(f"系统报告生成失败: {e}")
            return {}
    
    def run_simulation(self) -> Dict:
        """
        运行系统模拟
        
        Returns:
            模拟结果
        """
        try:
            logger.info("开始运行多层次对冲管理模拟")
            
            # 模拟市场数据
            simulation_data = {
                'index_price': 3000,
                'volatility': 0.15,
                'returns': [0.01, -0.02, 0.03, -0.01, 0.02],
                'liquidity': 1.0,
                'var_95': 0.02,
                'es_95': 0.03,
                'beta': 1.0,
                'vix_future_price': 20.0
            }
            
            # 执行多层次对冲
            result = self.execute_multi_layer_hedge(simulation_data)
            
            # 生成报告
            report = self.generate_system_report()
            
            logger.info("多层次对冲管理模拟完成")
            
            return {
                'simulation_result': result,
                'system_report': report,
                'conclusion': {
                    'success': result.get('success', False),
                    'trades_generated': len(result.get('trades', [])),
                    'execution_effectiveness': result.get('execution_effectiveness', 0.0)
                }
            }
            
        except Exception as e:
            logger.error(f"模拟运行失败: {e}")
            return {'error': str(e)}
    
    def emergency_stop(self) -> Dict:
        """
        紧急停止机制
        
        Returns:
            紧急停止结果
        """
        try:
            logger.warning("执行紧急停止机制")
            
            # 检查是否需要紧急停止
            if self.current_state['overall_risk_level'] > self.system_config['emergency_stop_loss']:
                # 执行紧急停止
                self._execute_emergency_stop()
                
                return {
                    'success': True,
                    'action': 'emergency_stop',
                    'reason': f"风险水平超限: {self.current_state['overall_risk_level']:.3f}",
                    'timestamp': datetime.now().isoformat()
                }
            else:
                return {
                    'success': False,
                    'action': 'no_action_needed',
                    'reason': '风险水平在可接受范围内',
                    'timestamp': datetime.now().isoformat()
                }
                
        except Exception as e:
            logger.error(f"紧急停止失败: {e}")
            return {'error': str(e)}
    
    def _execute_emergency_stop(self):
        """
        执行紧急停止操作
        """
        try:
            # 清空所有持仓
            self.delta_hedge.positions = []
            self.volatility_hedge.positions = []
            self.tail_risk_hedge.positions = []
            
            # 重置状态
            self.current_state['overall_risk_level'] = 0.0
            self.current_state['hedge_effectiveness'] = 0.0
            self.current_state['market_regime'] = 'normal'
            
            # 记录紧急停止
            emergency_record = {
                'timestamp': datetime.now().isoformat(),
                'action': 'emergency_stop',
                'trigger_reason': 'risk_level_exceeded',
                'risk_level': self.current_state['overall_risk_level']
            }
            
            self.execution_history.append(emergency_record)
            
            logger.warning("紧急停止操作完成")
            
        except Exception as e:
            logger.error(f"紧急停止操作失败: {e}")


# 主程序
if __name__ == "__main__":
    print("多层次对冲管理系统启动")
    print("=" * 50)
    
    # 创建多层次对冲管理器
    hedge_manager = MultiLayerHedgeManager(
        total_capital=5000000,
        hedge_ratio=0.20
    )
    
    # 运行模拟
    simulation_result = hedge_manager.run_simulation()
    
    print("\n模拟完成")
    print("=" * 50)
    
    # 输出结果
    print("模拟结果:")
    print(f"执行成功: {simulation_result['conclusion']['success']}")
    print(f"生成交易数量: {simulation_result['conclusion']['trades_generated']}")
    print(f"执行效果: {simulation_result['conclusion']['execution_effectiveness']:.3f}")
    
    # 生成系统报告
    system_report = hedge_manager.generate_system_report()
    print("\n系统报告:")
    print(f"总资金: {system_report['capital_summary']['total_capital']:,}元")
    print(f"投资资金: {system_report['capital_summary']['investment_capital']:,}元")
    print(f"对冲资金: {system_report['capital_summary']['hedge_capital']:,}元")
    print(f"平均风险水平: {system_report['performance_statistics']['average_risk_level']:.3f}")
    print(f"平均执行效果: {system_report['performance_statistics']['average_effectiveness']:.3f}")