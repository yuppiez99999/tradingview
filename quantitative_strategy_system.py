# -*- coding: utf-8 -*-
"""
量化策略系统 v5.10 - 世界级对冲基金的综合管理系统

系统特点：
- 多层对冲策略：Delta对冲、波动率对冲、尾部风险保护三层架构
- 动态风险管理：实时风险监控、预算分配、压力测试
- 智能资金管理：分层配置、动态调整、效率监控
- 自动化执行：7:00AM定时执行、智能订单路由、异常处理
- 策略优化整合：多策略权重优化、回测验证、实时优化
- 全景监控系统：实时监控、性能分析、预警报告

系统架构：
1. 核心策略模块：Delta对冲、波动率对冲、尾部风险保护
2. 风险管理模块：实时监控、预算分配、压力测试
3. 资金管理模块：动态配置、效率监控、优化调整
4. 自动化执行模块：定时执行、订单路由、异常处理
5. 策略优化模块：权重优化、回测验证、实时优化
6. 监控分析模块：性能监控、预警报告、数据分析

核心功能：
1. 策略运行：三层对冲策略的并行运行和协同
2. 风险控制：全方位的风险管理和预警
3. 资金配置：智能的资产配置和动态调整
4. 自动执行：精确的定时交易和订单管理
5. 优化决策：基于数据的策略参数优化
6. 监控分析：全面的系统监控和性能分析
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
import os
import sys

try:
    from utils.logger import get_logger
    from utils.data_provider import get_market_data, get_historical_data
    from utils.risk_metrics import calculate_var, calculate_es, calculate_max_drawdown
    from utils.order_execution import execute_order, cancel_order
    logger = get_logger('quantitative_strategy_system')
except ImportError:
    import logging
    logger = logging.getLogger('quantitative_strategy_system')

class QuantitativeStrategySystem:
    """
    量化策略系统 - 主控制器
    """
    
    def __init__(self, total_capital: float = 5000000, 
                 stock_etf_capital: float = 4000000,
                 hedge_capital: float = 1000000):
        """
        初始化量化策略系统
        
        Args:
            total_capital: 总资金（500万）
            stock_etf_capital: 股票ETF资金（400万）
            hedge_capital: 对冲资金（100万）
        """
        self.total_capital = total_capital
        self.stock_etf_capital = stock_etf_capital
        self.hedge_capital = hedge_capital
        
        # 系统状态
        self.system_enabled = False
        self.is_running = False
        self.main_thread = None
        
        # 系统配置
        self.config = {
            'daily_execution_time': time(7, 0),  # 7:00 AM
            'automatic_execution': True,
            'risk_management_enabled': True,
            'performance_monitoring': True,
            'logging_level': 'INFO',
            'data_source': 'real_time',
            'execution_mode': 'auto'
        }
        
        # 初始化核心模块
        self._initialize_modules()
        
        # 系统历史
        self.system_history = deque(maxlen=1000)
        
        # 性能指标
        self.performance_metrics = {
            'total_return': 0.0,
            'annual_return': 0.0,
            'max_drawdown': 0.0,
            'sharpe_ratio': 0.0,
            'win_rate': 0.0,
            'profit_factor': 0.0,
            'total_trades': 0,
            'successful_trades': 0,
            'avg_trade_return': 0.0,
            'system_uptime': 0.0,
            'last_update_time': datetime.now().isoformat()
        }
        
        # 任务队列
        self.task_queue = deque()
        self.completed_tasks = deque(maxlen=500)
        
        logger.info(f"量化策略系统 v5.10 初始化完成")
        logger.info(f"总资本: {total_capital:,.0f}元 (股票ETF: {stock_etf_capital:,.0f}元, 对冲: {hedge_capital:,.0f}元)")
    
    def _initialize_modules(self):
        """初始化核心模块"""
        try:
            # 策略模块
            from enhanced_delta_hedge import EnhancedDeltaHedge
            from volatility_hedge import VolatilityHedge
            from tail_risk_hedge import TailRiskHedge
            
            self.delta_hedge = EnhancedDeltaHedge(self.hedge_capital * 0.6)
            self.volatility_hedge = VolatilityHedge(self.hedge_capital * 0.3)
            self.tail_risk_hedge = TailRiskHedge(self.hedge_capital * 0.1)
            
            # 资金管理模块
            from dynamic_capital_manager import DynamicCapitalManager
            self.capital_manager = DynamicCapitalManager(self.hedge_capital)
            
            # 风险管理模块
            from enhanced_risk_manager import EnhancedRiskManager
            self.risk_manager = EnhancedRiskManager(self.total_capital)
            
            # 自动执行模块
            from automated_execution_system import AutomatedExecutionSystem
            self.execution_system = AutomatedExecutionSystem(self.total_capital)
            
            # 策略优化模块
            from strategy_optimizer import StrategyOptimizer
            self.strategy_optimizer = StrategyOptimizer(self.total_capital)
            
            # 智能触发模块
            from smart_hedge_trigger import SmartHedgeTrigger
            self.smart_trigger = SmartHedgeTrigger()
            
            logger.info("所有核心模块初始化完成")
            
        except Exception as e:
            logger.error(f"模块初始化失败: {e}")
            raise
    
    def start_system(self):
        """启动系统"""
        try:
            if not self.system_enabled:
                self.system_enabled = True
                self.is_running = True
                
                # 启动主线程
                self.main_thread = threading.Thread(target=self._main_loop)
                self.main_thread.daemon = True
                self.main_thread.start()
                
                # 启动自动化执行
                if self.config['automatic_execution']:
                    self.execution_system.start_system()
                
                # 启动风险管理
                if self.config['risk_management_enabled']:
                    self.risk_manager.enable_risk_management()
                
                # 启动实时优化
                if self.config['performance_monitoring']:
                    self.strategy_optimizer.start_optimization()
                
                logger.info("量化策略系统 v5.10 启动成功")
                
                # 记录启动日志
                self._log_system_event('system_start', {
                    'total_capital': self.total_capital,
                    'stock_etf_capital': self.stock_etf_capital,
                    'hedge_capital': self.hedge_capital,
                    'config': self.config
                })
                
        except Exception as e:
            logger.error(f"系统启动失败: {e}")
            self.system_enabled = False
            raise
    
    def stop_system(self):
        """停止系统"""
        try:
            if self.system_enabled:
                self.system_enabled = False
                self.is_running = False
                
                # 停止各子系统
                self.execution_system.stop_system()
                self.risk_manager.disable_risk_management()
                self.strategy_optimizer.stop_optimization()
                
                # 记录停止日志
                self._log_system_event('system_stop', {
                    'performance_metrics': self.performance_metrics,
                    'system_uptime': datetime.now() - datetime.fromisoformat(
                        self.performance_metrics['last_update_time']
                    )
                })
                
                logger.info("量化策略系统已停止")
                
        except Exception as e:
            logger.error(f"系统停止失败: {e}")
    
    def _main_loop(self):
        """主循环"""
        logger.info("系统主循环启动")
        
        while self.is_running:
            try:
                # 更新系统状态
                self._update_system_status()
                
                # 检查执行时间
                self._check_execution_time()
                
                # 处理任务队列
                self._process_task_queue()
                
                # 更新性能指标
                self._update_performance_metrics()
                
                # 检查预警
                self._check_alerts()
                
                # 每分钟检查一次
                time.sleep(60)
                
            except Exception as e:
                logger.error(f"主循环错误: {e}")
                time.sleep(60)
    
    def _update_system_status(self):
        """更新系统状态"""
        try:
            # 获取各模块状态
            status = {
                'timestamp': datetime.now().isoformat(),
                'system_enabled': self.system_enabled,
                'is_running': self.is_running,
                'performance_metrics': self.performance_metrics.copy(),
                'module_status': {}
            }
            
            # 检查各模块状态
            try:
                status['module_status']['delta_hedge'] = {
                    'enabled': self.delta_hedge.enabled,
                    'status': self.delta_hedge.get_status()
                }
            except:
                status['module_status']['delta_hedge'] = {'error': '模块未初始化'}
            
            try:
                status['module_status']['volatility_hedge'] = {
                    'enabled': self.volatility_hedge.enabled,
                    'status': self.volatility_hedge.get_status()
                }
            except:
                status['module_status']['volatility_hedge'] = {'error': '模块未初始化'}
            
            try:
                status['module_status']['tail_risk_hedge'] = {
                    'enabled': self.tail_risk_hedge.enabled,
                    'status': self.tail_risk_hedge.get_status()
                }
            except:
                status['module_status']['tail_risk_hedge'] = {'error': '模块未初始化'}
            
            # 记录状态
            self.system_history.append(status)
            
            # 更新系统运行时间
            if status['is_running']:
                last_update = datetime.fromisoformat(self.performance_metrics['last_update_time'])
                self.performance_metrics['system_uptime'] = (
                    datetime.now() - last_update
                ).total_seconds() / 3600.0
            
        except Exception as e:
            logger.error(f"系统状态更新失败: {e}")
    
    def _check_execution_time(self):
        """检查执行时间"""
        try:
            current_time = datetime.now().time()
            exec_time = self.config['daily_execution_time']
            
            # 检查是否为执行时间（允许5分钟误差）
            time_diff = abs((datetime.combine(datetime.min, current_time) - 
                            datetime.combine(datetime.min, exec_time)).total_seconds())
            
            if time_diff <= 300:  # 5分钟内
                if not self._has_executed_today():
                    self._execute_daily_strategy()
                    self._mark_execution_today()
        
        except Exception as e:
            logger.error(f"执行时间检查失败: {e}")
    
    def _has_executed_today(self):
        """检查今天是否已经执行"""
        today = datetime.now().date()
        for record in self.completed_tasks:
            if record.get('task_type') == 'daily_execution':
                task_date = datetime.fromisoformat(record['execution_time']).date()
                if task_date == today:
                    return True
        return False
    
    def _mark_execution_today(self):
        """标记今日已执行"""
        today_record = {
            'task_type': 'daily_execution',
            'execution_time': datetime.now().isoformat(),
            'status': 'completed'
        }
        self.completed_tasks.append(today_record)
    
    def _execute_daily_strategy(self):
        """执行每日策略"""
        try:
            logger.info("开始执行每日策略")
            
            # 1. 市场状态评估
            market_data = self._get_market_data()
            market_state = self.smart_trigger.evaluate_market_state(market_data)
            
            # 2. 智能触发决策
            trigger_decision = self.smart_trigger.make_hedge_decision(
                market_data, self.performance_metrics
            )
            
            # 3. 风险管理
            risk_management = self.risk_manager.run_risk_management_cycle(
                market_data, self._get_portfolio_data(), self.performance_metrics
            )
            
            # 4. 执行各策略
            strategies_results = {}
            
            # 执行Delta对冲
            if trigger_decision['delta_hedge_enabled']:
                delta_result = self.delta_hedge.execute(market_data, trigger_decision['signals'])
                strategies_results['delta_hedge'] = delta_result
            
            # 执行波动率对冲
            if trigger_decision['volatility_hedge_enabled']:
                volatility_result = self.volatility_hedge.execute(market_data, trigger_decision['signals'])
                strategies_results['volatility_hedge'] = volatility_result
            
            # 执行尾部风险对冲
            if trigger_decision['tail_risk_hedge_enabled']:
                tail_result = self.tail_risk_hedge.execute(market_data, trigger_decision['signals'])
                strategies_results['tail_risk_hedge'] = tail_result
            
            # 5. 资金管理优化
            capital_optimization = self.capital_manager.optimize_capital_allocation(
                market_data, self.performance_metrics
            )
            
            # 6. 自动化执行
            if self.config['execution_mode'] == 'auto':
                execution_result = self.execution_system.run_risk_management_cycle(
                    market_data, self._get_portfolio_data(), self.performance_metrics
                )
            
            # 7. 记录执行结果
            execution_record = {
                'execution_time': datetime.now().isoformat(),
                'market_state': market_state,
                'trigger_decision': trigger_decision,
                'risk_management': risk_management,
                'strategies_results': strategies_results,
                'capital_optimization': capital_optimization,
                'execution_result': execution_result if self.config['execution_mode'] == 'auto' else None,
                'performance_update': self._calculate_strategy_performance(strategies_results)
            }
            
            self.completed_tasks.append(execution_record)
            
            # 更新系统历史
            self._log_system_event('daily_execution', execution_record)
            
            logger.info("每日策略执行完成")
            
        except Exception as e:
            logger.error(f"每日策略执行失败: {e}")
            error_record = {
                'execution_time': datetime.now().isoformat(),
                'error': str(e),
                'status': 'failed'
            }
            self._log_system_event('execution_error', error_record)
    
    def _get_market_data(self):
        """获取市场数据"""
        try:
            # 模拟市场数据获取
            return {
                'timestamp': datetime.now().isoformat(),
                'index_price': 3000,
                'volatility': 0.15,
                'var_95': 0.02,
                'var_99': 0.035,
                'es_95': 0.03,
                'beta': 1.0,
                'liquidity': 1.0,
                'sentiment_score': 0.2,
                'correlation_matrix': np.eye(3),
                'tracking_error': 0.03,
                'market_correlation': 0.7,
                'returns': np.random.normal(0.0003, 0.01, 252),
                'vix_future_price': 20.0,
                'kurtosis': 3.0,
                'skewness': 0.0,
                'extreme_events': 0
            }
        except Exception as e:
            logger.error(f"市场数据获取失败: {e}")
            return {}
    
    def _get_portfolio_data(self):
        """获取组合数据"""
        try:
            return {
                'total_value': self.total_capital,
                'stock_etf_value': self.stock_etf_capital,
                'hedge_value': self.hedge_capital,
                'positions': [
                    {'symbol': 'AAPL', 'quantity': 1000, 'price': 150.0},
                    {'symbol': 'MSFT', 'quantity': 500, 'price': 200.0},
                    {'symbol': 'SPY', 'quantity': 2000, 'price': 300.0}
                ]
            }
        except Exception as e:
            logger.error(f"组合数据获取失败: {e}")
            return {}
    
    def _process_task_queue(self):
        """处理任务队列"""
        try:
            while self.task_queue:
                task = self.task_queue.popleft()
                
                try:
                    task_result = self._execute_task(task)
                    task['result'] = task_result
                    task['status'] = 'completed'
                    task['completed_time'] = datetime.now().isoformat()
                    
                except Exception as e:
                    task['error'] = str(e)
                    task['status'] = 'failed'
                
                self.completed_tasks.append(task)
                
        except Exception as e:
            logger.error(f"任务队列处理失败: {e}")
    
    def _execute_task(self, task):
        """执行单个任务"""
        task_type = task.get('task_type')
        
        if task_type == 'strategy_optimization':
            return self.strategy_optimizer.run_full_optimization(
                task['market_data'], task['performance_data']
            )
        
        elif task_type == 'risk_assessment':
            return self.risk_manager.run_risk_management_cycle(
                task['market_data'], task['portfolio_data'], task['performance_data']
            )
        
        elif task_type == 'capital_allocation':
            return self.capital_manager.optimize_capital_allocation(
                task['market_data'], task['performance_data']
            )
        
        else:
            return {'success': False, 'error': f'未知任务类型: {task_type}'}
    
    def _update_performance_metrics(self):
        """更新性能指标"""
        try:
            # 计算最新绩效
            if self.completed_tasks:
                latest_tasks = list(self.completed_tasks)[-10:]  # 最近10个任务
                
                # 计算交易统计
                total_trades = sum(1 for t in latest_tasks 
                                 if t.get('status') == 'completed' and 
                                 'performance_update' in t)
                successful_trades = sum(1 for t in latest_tasks 
                                     if t.get('status') == 'completed' and
                                     t.get('performance_update', {}).get('success', False))
                
                # 计算平均收益
                trade_returns = [t.get('performance_update', {}).get('trade_return', 0)
                               for t in latest_tasks 
                               if t.get('status') == 'completed' and 
                               'performance_update' in t]
                avg_return = np.mean(trade_returns) if trade_returns else 0.0
                
                # 更新指标
                self.performance_metrics.update({
                    'total_trades': total_trades,
                    'successful_trades': successful_trades,
                    'win_rate': successful_trades / total_trades if total_trades > 0 else 0.0,
                    'avg_trade_return': avg_return,
                    'last_update_time': datetime.now().isoformat()
                })
        
        except Exception as e:
            logger.error(f"性能指标更新失败: {e}")
    
    def _check_alerts(self):
        """检查预警"""
        try:
            # 检查性能预警
            if self.performance_metrics['max_drawdown'] > 0.15:
                self._generate_alert('high_drawdown', 
                    f"最大回撤超过15%: {self.performance_metrics['max_drawdown']:.2%}")
            
            if self.performance_metrics['win_rate'] < 0.6:
                self._generate_alert('low_win_rate', 
                    f"胜率低于60%: {self.performance_metrics['win_rate']:.2%}")
            
            # 检查系统预警
            if not self.system_enabled:
                self._generate_alert('system_disabled', '系统已禁用')
            
            # 检查模块预警
            for module_name, module in [
                ('delta_hedge', self.delta_hedge),
                ('volatility_hedge', self.volatility_hedge),
                ('tail_risk_hedge', self.tail_risk_hedge)
            ]:
                if hasattr(module, 'enabled') and not module.enabled:
                    self._generate_alert(f'module_disabled_{module_name}', 
                        f"{module_name}模块已禁用")
        
        except Exception as e:
            logger.error(f"预警检查失败: {e}")
    
    def _generate_alert(self, alert_type, message):
        """生成预警"""
        alert = {
            'timestamp': datetime.now().isoformat(),
            'alert_type': alert_type,
            'message': message,
            'severity': 'high' if alert_type in ['high_drawdown', 'system_disabled'] else 'medium'
        }
        
        logger.warning(f"预警: {alert['message']}")
        
        # 记录预警历史
        self.system_history.append({
            'event': 'alert',
            'alert': alert
        })
    
    def _log_system_event(self, event_type, event_data):
        """记录系统事件"""
        event = {
            'timestamp': datetime.now().isoformat(),
            'event_type': event_type,
            'event_data': event_data
        }
        
        self.system_history.append(event)
        logger.info(f"系统事件: {event_type}")
    
    def _calculate_strategy_performance(self, strategies_results):
        """计算策略绩效"""
        try:
            if not strategies_results:
                return {'success': False, 'error': '无策略结果'}
            
            total_return = 0.0
            total_risk = 0.0
            successful_strategies = 0
            
            for strategy_name, result in strategies_results.items():
                if result.get('success', False):
                    total_return += result.get('return', 0.0)
                    total_risk += result.get('risk', 0.0)
                    successful_strategies += 1
            
            # 计算综合绩效
            avg_return = total_return / len(strategies_results) if strategies_results else 0.0
            avg_risk = total_risk / len(strategies_results) if strategies_results else 0.0
            success_rate = successful_strategies / len(strategies_results) if strategies_results else 0.0
            
            return {
                'success': True,
                'total_return': total_return,
                'average_return': avg_return,
                'average_risk': avg_risk,
                'success_rate': success_rate,
                'sharpe_ratio': avg_return / avg_risk if avg_risk > 0 else 0.0,
                'trade_return': avg_return
            }
        
        except Exception as e:
            logger.error(f"策略绩效计算失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_system_summary(self):
        """获取系统总结"""
        try:
            # 系统状态
            system_status = {
                'system_enabled': self.system_enabled,
                'is_running': self.is_running,
                'total_capital': self.total_capital,
                'stock_etf_capital': self.stock_etf_capital,
                'hedge_capital': self.hedge_capital,
                'uptime_hours': self.performance_metrics['system_uptime'],
                'last_update': self.performance_metrics['last_update_time']
            }
            
            # 绩效指标
            performance_summary = {
                'annual_return': self.performance_metrics['annual_return'],
                'max_drawdown': self.performance_metrics['max_drawdown'],
                'sharpe_ratio': self.performance_metrics['sharpe_ratio'],
                'win_rate': self.performance_metrics['win_rate'],
                'profit_factor': self.performance_metrics['profit_factor'],
                'total_trades': self.performance_metrics['total_trades'],
                'successful_trades': self.performance_metrics['successful_trades']
            }
            
            # 模块状态
            module_status = {}
            for module_name, module in [
                ('delta_hedge', self.delta_hedge),
                ('volatility_hedge', self.volatility_hedge),
                ('tail_risk_hedge', self.tail_risk_hedge),
                ('capital_manager', self.capital_manager),
                ('risk_manager', self.risk_manager),
                ('execution_system', self.execution_system),
                ('strategy_optimizer', self.strategy_optimizer),
                ('smart_trigger', self.smart_trigger)
            ]:
                try:
                    if hasattr(module, 'get_status'):
                        module_status[module_name] = module.get_status()
                    else:
                        module_status[module_name] = 'available'
                except:
                    module_status[module_name] = 'error'
            
            # 预警状态
            alerts = [entry for entry in self.system_history 
                     if entry.get('event') == 'alert']
            recent_alerts = alerts[-5:] if alerts else []
            
            # 执行统计
            execution_stats = {
                'today_executed': self._has_executed_today(),
                'completed_tasks': len(self.completed_tasks),
                'pending_tasks': len(self.task_queue),
                'latest_execution': self.completed_tasks[-1]['execution_time'] 
                    if self.completed_tasks else None
            }
            
            return {
                'system_status': system_status,
                'performance_summary': performance_summary,
                'module_status': module_status,
                'recent_alerts': recent_alerts,
                'execution_statistics': execution_stats,
                'configuration': self.config
            }
        
        except Exception as e:
            logger.error(f"系统总结生成失败: {e}")
            return {'error': str(e)}
    
    def add_task(self, task_type: str, task_data: Dict):
        """添加任务到队列"""
        task = {
            'task_id': f"TASK_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.task_queue)}",
            'task_type': task_type,
            'task_data': task_data,
            'created_time': datetime.now().isoformat(),
            'status': 'pending'
        }
        
        self.task_queue.append(task)
        logger.info(f"任务已添加: {task_type} - {task['task_id']}")
    
    def run_manual_optimization(self):
        """手动运行优化"""
        try:
            # 准备数据
            market_data = self._get_market_data()
            performance_data = {
                'returns': np.random.normal(0.0003, 0.01, 252),
                'annual_return': self.performance_metrics['annual_return'],
                'max_drawdown': self.performance_metrics['max_drawdown'],
                'win_rate': self.performance_metrics['win_rate']
            }
            
            # 添加优化任务
            self.add_task('strategy_optimization', {
                'market_data': market_data,
                'performance_data': performance_data
            })
            
            logger.info("手动优化任务已添加")
            
        except Exception as e:
            logger.error(f"手动优化添加失败: {e}")
    
    def export_system_report(self, file_path: str):
        """导出系统报告"""
        try:
            summary = self.get_system_summary()
            
            report = {
                'report_type': 'system_summary',
                'generated_time': datetime.now().isoformat(),
                'system_config': self.config,
                'system_status': summary['system_status'],
                'performance_summary': summary['performance_summary'],
                'module_status': summary['module_status'],
                'execution_statistics': summary['execution_statistics'],
                'recent_alerts': summary['recent_alerts'],
                'performance_metrics': self.performance_metrics,
                'historical_data': list(self.system_history)[-100:]  # 最近100条记录
            }
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            
            logger.info(f"系统报告已导出: {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"系统报告导出失败: {e}")
            return False


def main():
    """主函数"""
    print("量化策略系统 v5.10 - 世界级对冲基金系统")
    print("=" * 60)
    
    # 创建系统实例
    try:
        system = QuantitativeStrategySystem(
            total_capital=5000000,
            stock_etf_capital=4000000,
            hedge_capital=1000000
        )
        
        # 启动系统
        print("正在启动系统...")
        system.start_system()
        print("系统启动成功！")
        
        # 系统运行状态
        print("\n系统运行中...按Ctrl+C查看状态报告")
        
        # 定期输出状态
        last_output = time.time()
        while True:
            time.sleep(10)
            
            # 每60秒输出一次状态
            current_time = time.time()
            if current_time - last_output >= 60:
                summary = system.get_system_summary()
                print(f"\r[{datetime.now().strftime('%H:%M:%S')}] "
                      f"运行时间: {summary['system_status']['uptime_hours']:.1f}小时 | "
                      f"总收益: {summary['performance_summary']['annual_return']:.2%} | "
                      f"最大回撤: {summary['performance_summary']['max_drawdown']:.2%}", end='')
                last_output = current_time
        
    except KeyboardInterrupt:
        print("\n\n正在生成系统报告...")
        
        # 导出系统报告
        report_path = f"system_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        if system.export_system_report(report_path):
            print(f"系统报告已保存: {report_path}")
        
        # 显示最终状态
        print("\n最终状态:")
        final_summary = system.get_system_summary()
        print(f"  系统运行时间: {final_summary['system_status']['uptime_hours']:.1f}小时")
        print(f"  年化收益: {final_summary['performance_summary']['annual_return']:.2%}")
        print(f"  最大回撤: {final_summary['performance_summary']['max_drawdown']:.2%}")
        print(f"  胜率: {final_summary['performance_summary']['win_rate']:.2%}")
        print(f"  总交易数: {final_summary['performance_summary']['total_trades']}")
        
        # 停止系统
        print("\n正在停止系统...")
        system.stop_system()
        print("系统已停止")
        
    except Exception as e:
        print(f"系统运行错误: {e}")
        logger.error(f"系统运行失败: {e}")


if __name__ == "__main__":
    main()