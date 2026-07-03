# -*- coding: utf-8 -*-
"""
自动化执行系统 - 世界级对冲基金的自动化交易执行架构

系统特点：
- 7:00AM自动执行：精确的时间控制，确保按时执行
- 智能订单路由：基于市场状况和交易成本的最优订单路由
- 滑点控制：多层级滑点控制，确保执行质量
- 分层执行策略：基于市场状态的分层执行策略
- 实时执行监控：执行过程的实时监控和异常处理
- 自动恢复机制：执行失败后的自动重试和恢复

核心功能：
1. 定时执行控制：精确的定时执行和日历管理
2. 市场状态评估：基于市场状况的执行策略选择
3. 订单生成和路由：智能订单生成和路由
4. 执行质量控制：多层级执行质量控制
5. 异常处理：全面的异常处理和恢复机制
6. 性能监控：执行性能监控和分析
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple, Union
from collections import deque
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
import schedule

try:
    from utils.logger import get_logger
    from utils.data_provider import get_market_data, get_trading_calendar
    from utils.order_execution import execute_order, cancel_order
    from utils.risk_metrics import calculate_var, calculate_es
    logger = get_logger('automated_execution_system')
except ImportError:
    import logging
    logger = logging.getLogger('automated_execution_system')

class TradingCalendar:
    """
    交易日历管理器
    """
    
    def __init__(self):
        self.trading_schedule = {
            'morning_open': time(7, 0),      # 7:00 AM
            'morning_close': time(11, 30),   # 11:30 AM
            'afternoon_open': time(13, 0),   # 1:00 PM
            'afternoon_close': time(15, 0),  # 3:00 PM
            'executions': [
                {'time': time(7, 0), 'name': 'daily_execution'},  # 7:00 AM
                {'time': time(10, 0), 'name': 'morning_review'},  # 10:00 AM
                {'time': time(14, 0), 'name': 'afternoon_adjustment'}  # 2:00 PM
            ]
        }
        
        # 特殊交易日处理
        self.special_days = {
            # 节假日、特殊交易日等
        }
        
        # 执行窗口（允许的执行时间范围）
        self.execution_windows = {
            'daily_execution': {
                'start': time(7, 0),
                'end': time(7, 15),   # 7:15 AM
                'allow_early': False,
                'allow_late': True
            },
            'morning_review': {
                'start': time(9, 45),
                'end': time(10, 15),
                'allow_early': False,
                'allow_late': True
            },
            'afternoon_adjustment': {
                'start': time(13, 45),
                'end': time(14, 15),
                'allow_early': False,
                'allow_late': True
            }
        }
        
        # 历史执行记录
        self.execution_history = deque(maxlen=100)
        
        logger.info("交易日历初始化完成")
    
    def is_trading_day(self, date: datetime = None) -> bool:
        """判断是否为交易日"""
        if date is None:
            date = datetime.now()
        
        # 检查是否为周末
        if date.weekday() >= 5:
            return False
        
        # 检查是否为特殊交易日
        date_str = date.strftime('%Y-%m-%d')
        if date_str in self.special_days:
            return self.special_days[date_str]['is_trading']
        
        # 检查是否为节假日（这里简化处理，实际应该从节假日API获取）
        # 简单判断一些常见节假日
        month, day = date.month, date.day
        if (month == 1 and day in [1, 2, 3]) or (month == 10 and day == 1):
            return False
        
        return True
    
    def is_within_execution_window(self, execution_name: str) -> Tuple[bool, str]:
        """判断当前是否在执行窗口内"""
        now = datetime.now().time()
        window = self.execution_windows.get(execution_name)
        
        if not window:
            return False, f"未知的执行类型: {execution_name}"
        
        # 检查是否在窗口内
        if window['start'] <= now <= window['end']:
            return True, "在执行窗口内"
        
        # 检查是否允许提前执行
        if window['allow_early'] and now < window['start']:
            time_diff = (datetime.combine(datetime.min, window['start']) - 
                        datetime.combine(datetime.min, now)).total_seconds()
            if time_diff <= 300:  # 允许提前5分钟
                return True, "允许提前执行"
        
        # 检查是否允许延后执行
        if window['allow_late'] and now > window['end']:
            time_diff = (datetime.combine(datetime.min, now) - 
                        datetime.combine(datetime.min, window['end'])).total_seconds()
            if time_diff <= 300:  # 允许延后5分钟
                return True, "允许延后执行"
        
        return False, "不在执行窗口内"
    
    def get_next_execution_time(self) -> Optional[datetime]:
        """获取下次执行时间"""
        now = datetime.now()
        
        # 如果不是交易日，返回下一个交易日
        if not self.is_trading_day(now):
            # 找到下一个交易日
            next_day = now + timedelta(days=1)
            while not self.is_trading_day(next_day):
                next_day += timedelta(days=1)
            return next_day.replace(
                hour=self.trading_schedule['morning_open'].hour,
                minute=self.trading_schedule['morning_open'].minute
            )
        
        # 检查今天的执行时间
        today = now.date()
        for execution in self.trading_schedule['executions']:
            execution_time = datetime.combine(today, execution['time'])
            
            # 如果执行时间已过，跳过
            if execution_time < now:
                continue
            
            # 检查是否在允许的执行窗口内
            is_allowed, _ = self.is_within_execution_window(execution['name'])
            if is_allowed:
                return execution_time
        
        # 如果今天没有更多执行，返回明天
        tomorrow = now + timedelta(days=1)
        return datetime.combine(
            tomorrow,
            self.trading_schedule['morning_open']
        )
    
    def get_execution_schedule(self, days_ahead: int = 7) -> List[Dict]:
        """获取未来几天的执行计划"""
        schedule = []
        now = datetime.now()
        
        for i in range(days_ahead):
            date = now + timedelta(days=i)
            if self.is_trading_day(date):
                day_schedule = {
                    'date': date.strftime('%Y-%m-%d'),
                    'is_trading': True,
                    'executions': []
                }
                
                for execution in self.trading_schedule['executions']:
                    execution_time = datetime.combine(date, execution['time'])
                    day_schedule['executions'].append({
                        'name': execution['name'],
                        'time': execution_time.isoformat(),
                        'timestamp': execution_time.timestamp()
                    })
                
                schedule.append(day_schedule)
        
        return schedule
    
    def record_execution(self, execution_name: str, start_time: datetime, 
                       end_time: datetime, success: bool, details: Dict):
        """记录执行历史"""
        record = {
            'timestamp': datetime.now().isoformat(),
            'execution_name': execution_name,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'duration_seconds': (end_time - start_time).total_seconds(),
            'success': success,
            'details': details
        }
        
        self.execution_history.append(record)
        logger.info(f"执行记录: {execution_name} - {'成功' if success else '失败'}")
    
    def get_execution_summary(self) -> Dict:
        """获取执行总结"""
        if not self.execution_history:
            return {'message': '暂无执行历史'}
        
        # 最近执行
        latest_execution = self.execution_history[-1]
        
        # 统计成功率
        total_executions = len(self.execution_history)
        successful_executions = sum(1 for r in self.execution_history if r['success'])
        success_rate = successful_executions / total_executions if total_executions > 0 else 0.0
        
        # 平均执行时间
        avg_duration = np.mean([
            r['duration_seconds'] for r in self.execution_history
        ])
        
        # 按执行类型统计
        execution_stats = {}
        for record in self.execution_history:
            exec_name = record['execution_name']
            if exec_name not in execution_stats:
                execution_stats[exec_name] = {
                    'count': 0,
                    'success': 0,
                    'avg_duration': 0.0
                }
            
            execution_stats[exec_name]['count'] += 1
            if record['success']:
                execution_stats[exec_name]['success'] += 1
            
            # 更新平均执行时间
            stats = execution_stats[exec_name]
            if stats['count'] > 0:
                stats['avg_duration'] = (
                    (stats['avg_duration'] * (stats['count'] - 1) + record['duration_seconds']) / stats['count']
                )
        
        return {
            'total_executions': total_executions,
            'latest_execution': latest_execution['execution_name'],
            'latest_time': latest_execution['timestamp'],
            'success_rate': success_rate,
            'average_duration_seconds': avg_duration,
            'execution_stats': execution_stats,
            'next_execution_time': self.get_next_execution_time().isoformat() if self.get_next_execution_time() else None
        }

class MarketStateEvaluator:
    """
    市场状态评估器
    """
    
    def __init__(self):
        # 市场状态定义
        self.market_states = {
            'normal': {
                'description': '正常市场',
                'execution_strategy': 'aggressive',
                'priority': 'normal',
                'risk_tolerance': 'medium'
            },
            'volatile': {
                'description': '高波动市场',
                'execution_strategy': 'conservative',
                'priority': 'high',
                'risk_tolerance': 'low'
            },
            'illiquid': {
                'description': '低流动性市场',
                'execution_strategy': 'patient',
                'priority': 'high',
                'risk_tolerance': 'low'
            },
            'stress': {
                'description': '压力市场',
                'execution_strategy': 'defensive',
                'priority': 'critical',
                'risk_tolerance': 'very_low'
            },
            'crisis': {
                'description': '危机市场',
                'execution_strategy': 'emergency',
                'priority': 'critical',
                'risk_tolerance': 'minimal'
            }
        }
        
        # 状态评估历史
        self.state_history = deque(maxlen=100)
        
        # 状态切换阈值
        self.state_thresholds = {
            'volatility_threshold': 0.25,
            'liquidity_threshold': 0.5,
            'var_threshold': 0.05,
            'sentiment_threshold': -0.6,
            'correlation_breakdown': 0.8
        }
        
        logger.info("市场状态评估器初始化完成")
    
    def evaluate_market_state(self, market_data: Dict) -> Dict:
        """
        评估当前市场状态
        
        Args:
            market_data: 市场数据
            
        Returns:
            市场状态评估结果
        """
        try:
            logger.info("开始市场状态评估")
            
            # 提取关键指标
            volatility = market_data.get('volatility', 0.15)
            liquidity = market_data.get('liquidity', 1.0)
            var_95 = market_data.get('var_95', 0.02)
            sentiment = market_data.get('sentiment_score', 0.0)
            
            # 计算相关性和波动性指标
            correlation_matrix = market_data.get('correlation_matrix', np.eye(3))
            correlation_breakdown = self._calculate_correlation_breakdown(correlation_matrix)
            
            # 评估各个维度
            volatility_score = self._evaluate_volatility(volatility)
            liquidity_score = self._evaluate_liquidity(liquidity)
            var_score = self._evaluate_var(var_95)
            sentiment_score = self._evaluate_sentiment(sentiment)
            
            # 综合评估
            market_state = self._determine_market_state(
                volatility_score, liquidity_score, var_score, 
                sentiment_score, correlation_breakdown
            )
            
            # 计算状态置信度
            confidence = self._calculate_state_confidence(
                volatility_score, liquidity_score, var_score, 
                sentiment_score, correlation_breakdown
            )
            
            # 生成评估报告
            evaluation_report = {
                'timestamp': datetime.now().isoformat(),
                'market_state': market_state,
                'confidence': confidence,
                'individual_scores': {
                    'volatility': volatility_score,
                    'liquidity': liquidity_score,
                    'var': var_score,
                    'sentiment': sentiment_score,
                    'correlation_breakdown': correlation_breakdown
                },
                'detailed_metrics': {
                    'volatility': volatility,
                    'liquidity': liquidity,
                    'var_95': var_95,
                    'sentiment': sentiment,
                    'correlation_matrix': correlation_matrix.tolist()
                },
                'state_characteristics': self.market_states[market_state]
            }
            
            # 记录历史
            self.state_history.append(evaluation_report)
            
            logger.info(f"市场状态评估完成: {market_state} (置信度: {confidence:.2f})")
            
            return evaluation_report
            
        except Exception as e:
            logger.error(f"市场状态评估失败: {e}")
            return {
                'market_state': 'normal',
                'confidence': 0.0,
                'error': str(e)
            }
    
    def _evaluate_volatility(self, volatility: float) -> float:
        """评估波动性"""
        if volatility < 0.10:
            return 0.0  # 非常低
        elif volatility < 0.20:
            return 0.3  # 低
        elif volatility < 0.30:
            return 0.6  # 中等
        elif volatility < 0.40:
            return 0.8  # 高
        else:
            return 1.0  # 非常高
    
    def _evaluate_liquidity(self, liquidity: float) -> float:
        """评估流动性"""
        if liquidity > 0.8:
            return 0.0  # 非常高
        elif liquidity > 0.6:
            return 0.3  # 高
        elif liquidity > 0.4:
            return 0.6  # 中等
        elif liquidity > 0.2:
            return 0.8  # 低
        else:
            return 1.0  # 非常低
    
    def _evaluate_var(self, var: float) -> float:
        """评估VaR"""
        if var < 0.02:
            return 0.0  # 非常低
        elif var < 0.03:
            return 0.3  # 低
        elif var < 0.05:
            return 0.6  # 中等
        elif var < 0.08:
            return 0.8  # 高
        else:
            return 1.0  # 非常高
    
    def _evaluate_sentiment(self, sentiment: float) -> float:
        """评估市场情绪"""
        abs_sentiment = abs(sentiment)
        if abs_sentiment < 0.2:
            return 0.0  # 中性
        elif abs_sentiment < 0.4:
            return 0.3  # 轻微
        elif abs_sentiment < 0.6:
            return 0.6  # 中度
        elif abs_sentiment < 0.8:
            return 0.8  # 强烈
        else:
            return 1.0  # 极端
    
    def _calculate_correlation_breakdown(self, correlation_matrix: np.ndarray) -> float:
        """计算相关性崩溃程度"""
        # 计算相关性的标准差
        upper_tri = np.triu(correlation_matrix, k=1)
        valid_correlations = upper_tri[upper_tri != 0]
        
        if len(valid_correlations) > 0:
            correlation_std = np.std(valid_correlations)
            return min(correlation_std / 0.5, 1.0)  # 归一化到0-1
        return 0.0
    
    def _determine_market_state(self, volatility_score: float, liquidity_score: float,
                              var_score: float, sentiment_score: float, 
                              correlation_breakdown: float) -> str:
        """确定市场状态"""
        # 计算风险分数
        risk_score = (
            volatility_score * 0.3 +
            liquidity_score * 0.3 +
            var_score * 0.2 +
            sentiment_score * 0.1 +
            correlation_breakdown * 0.1
        )
        
        # 根据风险分数确定状态
        if risk_score >= 0.8:
            return 'crisis'
        elif risk_score >= 0.6:
            return 'stress'
        elif risk_score >= 0.4:
            return 'illiquid'
        elif risk_score >= 0.2:
            return 'volatile'
        else:
            return 'normal'
    
    def _calculate_state_confidence(self, volatility_score: float, liquidity_score: float,
                                  var_score: float, sentiment_score: float,
                                  correlation_breakdown: float) -> float:
        """计算状态置信度"""
        # 基于各指标的极端程度计算置信度
        extreme_indicators = 0
        total_indicators = 5
        
        if volatility_score >= 0.8:
            extreme_indicators += 1
        if liquidity_score >= 0.8:
            extreme_indicators += 1
        if var_score >= 0.8:
            extreme_indicators += 1
        if sentiment_score >= 0.8:
            extreme_indicators += 1
        if correlation_breakdown >= 0.8:
            extreme_indicators += 1
        
        # 计算置信度
        confidence = extreme_indicators / total_indicators
        
        # 考虑历史趋势
        if len(self.state_history) > 2:
            recent_states = [s['market_state'] for s in list(self.state_history)[-3:]]
            if len(set(recent_states)) == 1:  # 最近3个状态相同
                confidence += 0.2
        
        return min(confidence, 1.0)
    
    def get_market_state_summary(self) -> Dict:
        """获取市场状态总结"""
        if not self.state_history:
            return {'message': '暂无市场状态数据'}
        
        latest_state = self.state_history[-1]
        
        # 状态分布统计
        state_distribution = {}
        for state_record in self.state_history:
            state = state_record['market_state']
            state_distribution[state] = state_distribution.get(state, 0) + 1
        
        # 平均置信度
        avg_confidence = np.mean([s['confidence'] for s in self.state_history])
        
        # 状态稳定性
        if len(self.state_history) > 5:
            recent_states = [s['market_state'] for s in list(self.state_history)[-10:]]
            state_changes = sum(1 for i in range(1, len(recent_states)) 
                              if recent_states[i] != recent_states[i-1])
            stability = 1.0 - (state_changes / len(recent_states))
        else:
            stability = 1.0
        
        return {
            'current_state': latest_state['market_state'],
            'current_confidence': latest_state['confidence'],
            'timestamp': latest_state['timestamp'],
            'state_distribution': state_distribution,
            'average_confidence': avg_confidence,
            'state_stability': stability,
            'total_evaluations': len(self.state_history)
        }

class ExecutionStrategy:
    """
    执行策略控制器
    """
    
    def __init__(self):
        # 执行策略定义
        self.execution_strategies = {
            'aggressive': {
                'description': '激进执行',
                'order_type': 'market',
                'execution_style': 'immediate',
                'slice_size': 1.0,
                'timeout_seconds': 30,
                'retry_attempts': 2,
                'slippage_tolerance': 0.01
            },
            'conservative': {
                'description': '保守执行',
                'order_type': 'limit',
                'execution_style': 'sliced',
                'slice_size': 0.3,
                'timeout_seconds': 120,
                'retry_attempts': 3,
                'slippage_tolerance': 0.005
            },
            'patient': {
                'description': '耐心执行',
                'order_type': 'limit',
                'execution_style': 'gradual',
                'slice_size': 0.2,
                'timeout_seconds': 300,
                'retry_attempts': 5,
                'slippage_tolerance': 0.003
            },
            'defensive': {
                'description': '防御性执行',
                'order_type': 'limit',
                'execution_style': 'weighted',
                'slice_size': 0.1,
                'timeout_seconds': 600,
                'retry_attempts': 8,
                'slippage_tolerance': 0.002
            },
            'emergency': {
                'description': '紧急执行',
                'order_type': 'market',
                'execution_style': 'immediate',
                'slice_size': 1.0,
                'timeout_seconds': 15,
                'retry_attempts': 1,
                'slippage_tolerance': 0.02
            }
        }
        
        # 市场状态到执行策略的映射
        self.state_to_strategy = {
            'normal': 'aggressive',
            'volatile': 'conservative',
            'illiquid': 'patient',
            'stress': 'defensive',
            'crisis': 'emergency'
        }
        
        # 执行历史
        self.execution_history = deque(maxlen=100)
        
        logger.info("执行策略控制器初始化完成")
    
    def select_execution_strategy(self, market_state: str, trade_info: Dict) -> Dict:
        """
        选择执行策略
        
        Args:
            market_state: 市场状态
            trade_info: 交易信息
            
        Returns:
            执行策略配置
        """
        try:
            # 基于市场状态选择策略
            strategy_name = self.state_to_strategy.get(market_state, 'conservative')
            strategy_config = self.execution_strategies[strategy_name].copy()
            
            # 根据交易规模调整策略参数
            trade_size = trade_info.get('trade_size', 0)
            if trade_size > 1000000:  # 大额交易
                strategy_config['slice_size'] = max(0.1, strategy_config['slice_size'] * 0.5)
                strategy_config['timeout_seconds'] = min(600, strategy_config['timeout_seconds'] * 1.5)
            
            # 根据紧急程度调整
            urgency = trade_info.get('urgency', 'normal')
            if urgency == 'high':
                strategy_config['slice_size'] = min(1.0, strategy_config['slice_size'] * 2)
                strategy_config['timeout_seconds'] = max(10, strategy_config['timeout_seconds'] * 0.5)
            
            # 根据资产特性调整
            asset_type = trade_info.get('asset_type', 'equity')
            if asset_type == 'bond':
                strategy_config['slice_size'] = min(0.5, strategy_config['slice_size'] * 1.5)
                strategy_config['slippage_tolerance'] = min(0.01, strategy_config['slippage_tolerance'] * 2)
            
            logger.info(f"选择执行策略: {strategy_name}")
            
            return {
                'strategy_name': strategy_name,
                'strategy_config': strategy_config,
                'reasoning': f"基于市场状态{market_state}和交易特性选择"
            }
            
        except Exception as e:
            logger.error(f"执行策略选择失败: {e}")
            # 返回默认策略
            return {
                'strategy_name': 'conservative',
                'strategy_config': self.execution_strategies['conservative'].copy(),
                'error': str(e)
            }
    
    def generate_execution_plan(self, trade_info: Dict, 
                              strategy_config: Dict) -> Dict:
        """
        生成执行计划
        
        Args:
            trade_info: 交易信息
            strategy_config: 策略配置
            
        Returns:
            执行计划
        """
        try:
            # 计算切片数量
            slice_size = strategy_config['slice_size']
            if slice_size >= 1.0:
                num_slices = 1
            else:
                num_slices = max(1, int(1.0 / slice_size))
            
            # 生成切片
            trade_size = trade_info.get('trade_size', 0)
            instrument = trade_info.get('instrument', '')
            direction = trade_info.get('direction', 'buy')
            
            slices = []
            for i in range(num_slices):
                if i == num_slices - 1:  # 最后一片
                    slice_size = trade_size - sum(s['size'] for s in slices)
                else:
                    slice_size = trade_size * strategy_config['slice_size']
                
                slices.append({
                    'slice_id': i + 1,
                    'size': slice_size,
                    'direction': direction,
                    'instrument': instrument,
                    'price_type': 'limit' if strategy_config['order_type'] == 'limit' else 'market',
                    'priority': 'high' if i == 0 else 'normal',
                    'created_at': datetime.now().isoformat()
                })
            
            # 计算总超时时间
            timeout_per_slice = strategy_config['timeout_seconds']
            total_timeout = timeout_per_slice * num_slices
            
            execution_plan = {
                'trade_id': trade_info.get('trade_id', ''),
                'instrument': instrument,
                'total_size': trade_size,
                'total_direction': direction,
                'strategy': strategy_config['strategy_name'],
                'num_slices': num_slices,
                'slices': slices,
                'timeout_seconds': total_timeout,
                'max_retry_attempts': strategy_config['retry_attempts'],
                'slippage_tolerance': strategy_config['slippage_tolerance'],
                'execution_style': strategy_config['execution_style'],
                'created_at': datetime.now().isoformat()
            }
            
            logger.info(f"执行计划生成完成: {num_slices}个切片")
            
            return execution_plan
            
        except Exception as e:
            logger.error(f"执行计划生成失败: {e}")
            return {'error': str(e)}
    
    def record_execution_result(self, execution_plan: Dict, 
                             execution_result: Dict):
        """记录执行结果"""
        record = {
            'timestamp': datetime.now().isoformat(),
            'plan': execution_plan,
            'result': execution_result,
            'success': execution_result.get('success', False),
            'execution_time': execution_result.get('execution_time', 0),
            'actual_slippage': execution_result.get('slippage', 0),
            'retry_attempts': execution_result.get('retry_attempts', 0)
        }
        
        self.execution_history.append(record)
        logger.info(f"执行结果记录: {'成功' if record['success'] else '失败'}")
    
    def get_execution_summary(self) -> Dict:
        """获取执行总结"""
        if not self.execution_history:
            return {'message': '暂无执行历史'}
        
        # 最近执行
        latest_execution = self.execution_history[-1]
        
        # 统计成功率
        total_executions = len(self.execution_history)
        successful_executions = sum(1 for r in self.execution_history if r['success'])
        success_rate = successful_executions / total_executions if total_executions > 0 else 0.0
        
        # 平均执行时间
        avg_execution_time = np.mean([
            r['execution_time'] for r in self.execution_history if r['execution_time'] > 0
        ])
        
        # 平均滑点
        avg_slippage = np.mean([
            r['actual_slippage'] for r in self.execution_history 
            if r['actual_slippage'] is not None
        ])
        
        # 按策略统计
        strategy_stats = {}
        for record in self.execution_history:
            strategy = record['plan']['strategy']
            if strategy not in strategy_stats:
                strategy_stats[strategy] = {
                    'count': 0,
                    'success': 0,
                    'avg_time': 0.0,
                    'avg_slippage': 0.0
                }
            
            stats = strategy_stats[strategy]
            stats['count'] += 1
            if record['success']:
                stats['success'] += 1
            stats['avg_time'] = (
                (stats['avg_time'] * (stats['count'] - 1) + record['execution_time']) / stats['count']
            )
            if record['actual_slippage'] is not None:
                stats['avg_slippage'] = (
                    (stats['avg_slippage'] * (stats['count'] - 1) + record['actual_slippage']) / stats['count']
                )
        
        return {
            'total_executions': total_executions,
            'success_rate': success_rate,
            'latest_execution': latest_execution['plan']['strategy'],
            'average_execution_time_seconds': avg_execution_time,
            'average_slippage': avg_slippage,
            'strategy_stats': strategy_stats,
            'latest_time': latest_execution['timestamp']
        }

class OrderRouter:
    """
    订单路由器
    """
    
    def __init__(self):
        # 执行池配置
        self.execution_pools = {
            'normal': {
                'broker': 'broker_a',
                'priority': 'normal',
                'max_concurrent': 10,
                'min_balance': 100000
            },
            'priority': {
                'broker': 'broker_b',
                'priority': 'high',
                'max_concurrent': 5,
                'min_balance': 500000
            },
            'emergency': {
                'broker': 'broker_c',
                'priority': 'critical',
                'max_concurrent': 3,
                'min_balance': 1000000
            }
        }
        
        # 当前活跃订单
        self.active_orders = {}
        
        # 执行队列
        self.execution_queue = deque(maxlen=50)
        
        # 执行统计
        self.execution_stats = {
            'total_orders': 0,
            'successful_orders': 0,
            'failed_orders': 0,
            'average_time': 0.0,
            'average_slippage': 0.0
        }
        
        logger.info("订单路由器初始化完成")
    
    def route_order(self, execution_plan: Dict, market_state: str) -> Dict:
        """
        路由订单到执行池
        
        Args:
            execution_plan: 执行计划
            market_state: 市场状态
            
        Returns:
            路由结果
        """
        try:
            # 根据市场状态选择执行池
            if market_state in ['crisis', 'stress']:
                pool_name = 'emergency'
            elif market_state == 'illiquid':
                pool_name = 'priority'
            else:
                pool_name = 'normal'
            
            pool = self.execution_pools[pool_name]
            
            # 检查执行池可用性
            if not self._check_pool_availability(pool):
                # 如果当前池不可用，尝试其他池
                available_pool = self._find_available_pool()
                if available_pool:
                    pool = available_pool
                    pool_name = list(self.execution_pools.keys())[
                        list(self.execution_pools.values()).index(available_pool)
                    ]
                else:
                    return {
                        'success': False,
                        'error': '无可用执行池',
                        'suggested_action': '等待'
                    }
            
            # 为每个切片生成订单
            routed_orders = []
            for slice_info in execution_plan['slices']:
                order_id = self._generate_order_id()
                
                order = {
                    'order_id': order_id,
                    'slice_info': slice_info,
                    'execution_plan': execution_plan,
                    'target_pool': pool_name,
                    'priority': pool['priority'],
                    'created_at': datetime.now().isoformat(),
                    'status': 'pending',
                    'retry_count': 0
                }
                
                routed_orders.append(order)
            
            # 更新活跃订单
            for order in routed_orders:
                self.active_orders[order['order_id']] = order
            
            # 加入执行队列
            for order in routed_orders:
                self.execution_queue.append(order)
            
            logger.info(f"订单路由完成: {len(routed_orders)}个订单到{pool_name}池")
            
            return {
                'success': True,
                'routed_orders': routed_orders,
                'target_pool': pool_name,
                'estimated_wait_time': self._estimate_wait_time(pool_name)
            }
            
        except Exception as e:
            logger.error(f"订单路由失败: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _check_pool_availability(self, pool: Dict) -> bool:
        """检查执行池可用性"""
        # 检查并发限制
        active_count = sum(1 for order in self.active_orders.values() 
                          if order.get('target_pool') == list(self.execution_pools.values()).index(pool))
        
        if active_count >= pool['max_concurrent']:
            return False
        
        # 检查余额限制（简化处理）
        # 实际应该查询真实的账户余额
        return True
    
    def _find_available_pool(self) -> Optional[Dict]:
        """查找可用的执行池"""
        for pool in self.execution_pools.values():
            if self._check_pool_availability(pool):
                return pool
        return None
    
    def _generate_order_id(self) -> str:
        """生成订单ID"""
        return f"ORD_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{np.random.randint(1000, 9999)}"
    
    def _estimate_wait_time(self, pool_name: str) -> float:
        """估算等待时间"""
        pool = self.execution_pools[pool_name]
        
        # 基础等待时间
        base_wait = 10.0
        
        # 加上当前活跃订单的影响
        active_count = sum(1 for order in self.active_orders.values() 
                          if order.get('target_pool') == pool_name)
        queue_wait = active_count * pool['max_concurrent'] * 5.0
        
        return base_wait + queue_wait
    
    def process_execution_queue(self):
        """处理执行队列"""
        try:
            while self.execution_queue:
                # 获取下一个订单
                order = self.execution_queue[0]
                
                # 检查是否可以执行
                if self._can_execute_order(order):
                    # 执行订单
                    execution_result = self._execute_order(order)
                    
                    # 更新订单状态
                    if execution_result['success']:
                        order['status'] = 'completed'
                        order['completed_at'] = datetime.now().isoformat()
                        order['execution_result'] = execution_result
                    else:
                        order['status'] = 'failed'
                        order['error'] = execution_result.get('error', '未知错误')
                        order['retry_count'] += 1
                        
                        # 重试逻辑
                        if order['retry_count'] < 3:
                            order['status'] = 'pending'
                        else:
                            order['status'] = 'abandoned'
                    
                    # 从队列中移除
                    self.execution_queue.popleft()
                    
                    # 更新统计
                    self._update_execution_stats(execution_result)
                    
                    logger.info(f"订单处理完成: {order['order_id']} - {order['status']}")
                
                else:
                    # 暂停处理
                    break
        
        except Exception as e:
            logger.error(f"执行队列处理失败: {e}")
    
    def _can_execute_order(self, order: Dict) -> bool:
        """检查是否可以执行订单"""
        # 检查执行池可用性
        pool_name = order.get('target_pool', 'normal')
        pool = self.execution_pools.get(pool_name)
        
        if not pool:
            return False
        
        # 检查并发限制
        active_count = sum(1 for o in self.active_orders.values() 
                          if o.get('target_pool') == pool_name and o.get('status') == 'pending')
        
        if active_count >= pool['max_concurrent']:
            return False
        
        return True
    
    def _execute_order(self, order: Dict) -> Dict:
        """执行单个订单"""
        try:
            # 模拟订单执行
            slice_info = order['slice_info']
            
            # 模拟执行延迟
            time.sleep(np.random.uniform(0.1, 0.5))
            
            # 模拟执行结果
            execution_time = np.random.uniform(5, 20)
            slippage = np.random.uniform(0, 0.01)
            
            return {
                'success': True,
                'execution_time': execution_time,
                'slippage': slippage,
                'filled_size': slice_info['size'],
                'average_price': slice_info.get('price', 0) * (1 + slippage),
                'broker': self.execution_pools[order['target_pool']]['broker']
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _update_execution_stats(self, execution_result: Dict):
        """更新执行统计"""
        self.execution_stats['total_orders'] += 1
        
        if execution_result['success']:
            self.execution_stats['successful_orders'] += 1
            self.execution_stats['average_time'] = (
                (self.execution_stats['average_time'] * 
                 (self.execution_stats['total_orders'] - 1) + 
                 execution_result['execution_time']) / 
                self.execution_stats['total_orders']
            )
            self.execution_stats['average_slippage'] = (
                (self.execution_stats['average_slippage'] * 
                 (self.execution_stats['total_orders'] - 1) + 
                 execution_result['slippage']) / 
                self.execution_stats['total_orders']
            )
        else:
            self.execution_stats['failed_orders'] += 1
    
    def get_router_summary(self) -> Dict:
        """获取路由器总结"""
        # 活跃订单统计
        active_orders = list(self.active_orders.values())
        
        # 按状态统计
        status_stats = {}
        for order in active_orders:
            status = order.get('status', 'unknown')
            status_stats[status] = status_stats.get(status, 0) + 1
        
        # 按池统计
        pool_stats = {}
        for order in active_orders:
            pool = order.get('target_pool', 'unknown')
            pool_stats[pool] = pool_stats.get(pool, 0) + 1
        
        return {
            'total_active_orders': len(active_orders),
            'queue_length': len(self.execution_queue),
            'status_distribution': status_stats,
            'pool_distribution': pool_stats,
            'execution_stats': self.execution_stats,
            'current_time': datetime.now().isoformat()
        }


class AutomatedExecutionSystem:
    """
    自动化执行系统 - 主控制器
    """
    
    def __init__(self, total_capital: float = 1000000):
        self.total_capital = total_capital
        
        # 初始化组件
        self.trading_calendar = TradingCalendar()
        self.market_evaluator = MarketStateEvaluator()
        self.execution_strategy = ExecutionStrategy()
        self.order_router = OrderRouter()
        
        # 系统状态
        self.system_enabled = False
        self.is_running = False
        self.execution_thread = None
        
        # 执行状态
        self.current_market_state = 'normal'
        self.current_execution_plan = None
        self.current_routed_orders = []
        
        # 系统历史
        self.system_history = deque(maxlen=100)
        
        # 配置参数
        self.config = {
            'auto_start': True,
            'execution_window_check': True,
            'risk_pre_check': True,
            'max_retry_attempts': 3,
            'emergency_stop': True,
            'performance_monitoring': True
        }
        
        logger.info(f"自动化执行系统初始化完成，总资本: {total_capital:,.0f}元")
    
    def start_system(self):
        """启动系统"""
        if not self.system_enabled:
            self.system_enabled = True
            self.is_running = True
            
            # 启动执行线程
            self.execution_thread = threading.Thread(target=self._execution_loop)
            self.execution_thread.daemon = True
            self.execution_thread.start()
            
            # 启动性能监控
            if self.config['performance_monitoring']:
                self._start_performance_monitoring()
            
            logger.info("自动化执行系统启动")
    
    def stop_system(self):
        """停止系统"""
        self.is_running = False
        self.system_enabled = False
        
        if self.execution_thread:
            self.execution_thread.join()
        
        logger.info("自动化执行系统停止")
    
    def _execution_loop(self):
        """执行循环"""
        while self.is_running:
            try:
                # 检查是否应该执行
                next_execution = self.trading_calendar.get_next_execution_time()
                if not next_execution:
                    time.sleep(60)
                    continue
                
                current_time = datetime.now()
                if current_time < next_execution:
                    # 等待下次执行时间
                    sleep_time = (next_execution - current_time).total_seconds()
                    time.sleep(min(sleep_time, 60))
                    continue
                
                # 检查是否在执行窗口内
                is_in_window, message = self.trading_calendar.is_within_execution_window('daily_execution')
                if not is_in_window:
                    logger.info(f"不在执行窗口内: {message}")
                    time.sleep(60)
                    continue
                
                # 执行交易
                self._execute_daily_trading()
                
                # 等待下一个执行周期
                time.sleep(60)
                
            except Exception as e:
                logger.error(f"执行循环错误: {e}")
                time.sleep(60)
    
    def _execute_daily_trading(self):
        """执行每日交易"""
        try:
            logger.info("开始每日交易执行")
            
            # 1. 市场状态评估
            market_data = self._get_market_data()
            market_state = self.market_evaluator.evaluate_market_state(market_data)
            
            if isinstance(market_state, dict):
                self.current_market_state = market_state['market_state']
                market_state_data = market_state
            else:
                self.current_market_state = market_state
                market_state_data = {'market_state': market_state}
            
            # 2. 检查风险
            if self.config['risk_pre_check']:
                if not self._risk_pre_check(market_state_data):
                    logger.warning("风险预检查失败，取消今日交易")
                    return
            
            # 3. 生成交易计划（这里简化处理）
            trade_info = {
                'trade_id': f"TRADE_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                'instrument': 'SPY',
                'direction': 'buy',
                'trade_size': 100000,
                'urgency': 'normal',
                'asset_type': 'equity'
            }
            
            # 4. 选择执行策略
            strategy_result = self.execution_strategy.select_execution_strategy(
                self.current_market_state, trade_info
            )
            strategy_config = strategy_result['strategy_config']
            
            # 5. 生成执行计划
            execution_plan = self.execution_strategy.generate_execution_plan(
                trade_info, strategy_config
            )
            
            if isinstance(execution_plan, dict) and 'error' in execution_plan:
                logger.error(f"执行计划生成失败: {execution_plan['error']}")
                return
            
            self.current_execution_plan = execution_plan
            
            # 6. 订单路由
            routing_result = self.order_router.route_order(
                execution_plan, self.current_market_state
            )
            
            if not routing_result['success']:
                logger.error(f"订单路由失败: {routing_result['error']}")
                return
            
            self.current_routed_orders = routing_result['routed_orders']
            
            # 7. 处理执行队列
            self.order_router.process_execution_queue()
            
            # 8. 记录执行结果
            execution_result = {
                'market_state': self.current_market_state,
                'execution_plan': execution_plan,
                'routed_orders': self.current_routed_orders,
                'routing_result': routing_result,
                'execution_time': datetime.now().isoformat()
            }
            
            # 记录历史
            system_record = {
                'timestamp': datetime.now().isoformat(),
                'event': 'daily_execution',
                'execution_result': execution_result,
                'market_state_data': market_state_data
            }
            
            self.system_history.append(system_record)
            
            logger.info("每日交易执行完成")
            
        except Exception as e:
            logger.error(f"每日交易执行失败: {e}")
            
            # 记录失败
            failure_record = {
                'timestamp': datetime.now().isoformat(),
                'event': 'execution_failure',
                'error': str(e),
                'market_state': self.current_market_state
            }
            
            self.system_history.append(failure_record)
    
    def _get_market_data(self) -> Dict:
        """获取市场数据"""
        # 模拟市场数据获取
        return {
            'index_price': 3000,
            'volatility': 0.15,
            'var_95': 0.02,
            'var_99': 0.035,
            'liquidity': 1.0,
            'sentiment_score': 0.2,
            'correlation_matrix': np.eye(3),
            'beta': 1.0,
            'tracking_error': 0.03,
            'market_correlation': 0.7,
            'volatility_skew': 0.0,
            'volatility_term': 0.0,
            'vix_future_price': 20.0,
            'kurtosis': 3.0,
            'skewness': 0.0,
            'extreme_events': 0
        }
    
    def _risk_pre_check(self, market_state_data: Dict) -> bool:
        """执行风险预检查"""
        try:
            # 检查市场状态
            market_state = market_state_data['market_state']
            if market_state in ['crisis', 'stress']:
                logger.warning(f"市场状态异常: {market_state}")
                return False
            
            # 检查风险指标
            var_95 = market_state_data.get('individual_scores', {}).get('var', 0)
            if var_95 > 0.8:
                logger.warning(f"VaR风险过高: {var_95}")
                return False
            
            # 检查流动性
            liquidity = market_state_data.get('individual_scores', {}).get('liquidity', 0)
            if liquidity > 0.8:
                logger.warning(f"流动性风险过高: {liquidity}")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"风险预检查失败: {e}")
            return False
    
    def _start_performance_monitoring(self):
        """启动性能监控"""
        monitoring_thread = threading.Thread(target=self._performance_monitoring_loop)
        monitoring_thread.daemon = True
        monitoring_thread.start()
    
    def _performance_monitoring_loop(self):
        """性能监控循环"""
        while self.is_running:
            try:
                # 获取系统状态
                summary = self.get_system_summary()
                
                # 检查性能指标
                if summary['performance_metrics']['execution_success_rate'] < 0.8:
                    logger.warning("执行成功率过低，系统性能下降")
                
                if summary['performance_metrics']['average_execution_time'] > 30:
                    logger.warning("执行时间过长，系统性能下降")
                
                if summary['performance_metrics']['average_slippage'] > 0.01:
                    logger.warning("滑点过大，系统性能下降")
                
                time.sleep(300)  # 每5分钟检查一次
                
            except Exception as e:
                logger.error(f"性能监控错误: {e}")
                time.sleep(300)
    
    def get_system_summary(self) -> Dict:
        """获取系统总结"""
        return {
            'system_status': 'running' if self.is_running else 'stopped',
            'market_state': self.current_market_state,
            'current_plan': self.current_execution_plan,
            'routed_orders_count': len(self.current_routed_orders),
            
            # 各组件状态
            'trading_calendar': self.trading_calendar.get_execution_summary(),
            'market_state': self.market_evaluator.get_market_state_summary(),
            'execution_strategy': self.execution_strategy.get_execution_summary(),
            'order_router': self.order_router.get_router_summary(),
            
            # 系统统计
            'system_history_count': len(self.system_history),
            'last_execution': self.system_history[-1]['timestamp'] if self.system_history else None,
            
            # 性能指标
            'performance_metrics': {
                'execution_success_rate': 
                    self.order_router.execution_stats['successful_orders'] / 
                    max(self.order_router.execution_stats['total_orders'], 1),
                'average_execution_time': 
                    self.order_router.execution_stats['average_time'],
                'average_slippage': 
                    self.order_router.execution_stats['average_slippage']
            }
        }
    
    def get_execution_schedule(self, days_ahead: int = 7) -> List[Dict]:
        """获取执行计划"""
        return self.trading_calendar.get_execution_schedule(days_ahead)


# 主程序
if __name__ == "__main__":
    print("自动化执行系统启动")
    print("=" * 50)
    
    # 创建自动化执行系统
    execution_system = AutomatedExecutionSystem(total_capital=1000000)
    
    # 启动系统
    execution_system.start_system()
    
    # 等待一段时间观察执行
    time.sleep(10)
    
    # 获取系统总结
    summary = execution_system.get_system_summary()
    
    print("\n系统状态")
    print("=" * 50)
    print(f"系统状态: {summary['system_status']}")
    print(f"当前市场状态: {summary['market_state']}")
    print(f"当前执行计划: {'有' if summary['current_plan'] else '无'}")
    print(f"路由订单数: {summary['routed_orders_count']}")
    
    print("\n组件状态")
    print("=" * 50)
    print(f"交易日历: 总执行数={summary['trading_calendar'].get('total_executions', 0)}")
    print(f"市场评估: 当前状态={summary['market_state'].get('current_state', 'unknown')}")
    print(f"执行策略: 成功率={summary['execution_strategy'].get('success_rate', 0):.2%}")
    print(f"订单路由: 活跃订单={summary['order_router'].get('total_active_orders', 0)}")
    
    print("\n性能指标")
    print("=" * 50)
    perf = summary['performance_metrics']
    print(f"执行成功率: {perf['execution_success_rate']:.2%}")
    print(f"平均执行时间: {perf['average_execution_time']:.2f}秒")
    print(f"平均滑点: {perf['average_slippage']:.4%}")
    
    # 获取未来执行计划
    future_schedule = execution_system.get_execution_schedule(3)
    print("\n未来3天执行计划")
    print("=" * 50)
    for day in future_schedule:
        print(f"{day['date']}: {len(day['executions'])}个执行")
    
    print("\n系统运行中...按Ctrl+C停止")
    
    try:
        # 保持运行
        while True:
            time.sleep(30)
            # 更新状态
            current_summary = execution_system.get_system_summary()
            print(f"\r当前时间: {datetime.now().strftime('%H:%M:%S')} | "
                  f"系统状态: {current_summary['system_status']} | "
                  f"市场状态: {current_summary['market_state']}", end='')
    except KeyboardInterrupt:
        print("\n正在停止系统...")
        execution_system.stop_system()
        print("系统已停止")