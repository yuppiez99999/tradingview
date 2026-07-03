"""
智能投资代理主类
整合所有组件：组合管理、风控、数据提供者、策略引擎、AI引擎
"""

from typing import Dict, List, Optional, Tuple
from datetime import datetime, time
from enum import Enum
import pandas as pd
import logging

from .portfolio_manager import PortfolioManager
from .risk_monitor import RiskMonitor, RiskLevel
from ..config.config import (
    PORTFOLIO_CONFIG, CAPITAL_CONFIG, PERFORMANCE_TARGETS, 
    AUTOMATION_SCHEDULE, SYSTEM_CONFIG
)

class AgentStatus(Enum):
    """代理状态"""
    INITIALIZING = "INITIALIZING"       # 初始化中
    READY = "READY"                     # 就绪
    MONITORING = "MONITORING"           # 监控中
    TRADING = "TRADING"                 # 交易中
    PAUSED = "PAUSED"                   # 暂停
    ERROR = "ERROR"                     # 错误状态

class InvestmentAgent:
    """
    智能投资代理核心类
    
    整合所有投资决策组件，实现AI驱动的量化投资管理
    """
    
    def __init__(self):
        """初始化智能投资代理"""
        # 设置日志
        self._setup_logging()
        
        # 初始化状态
        self.status = AgentStatus.INITIALIZING
        self.start_time = datetime.now()
        self.last_trading_day = None
        
        # 核心组件初始化
        self.logger.info("初始化投资组合管理器...")
        self.portfolio_manager = PortfolioManager(total_capital=CAPITAL_CONFIG['total_capital'])
        
        self.logger.info("初始化风控监控系统...")
        self.risk_monitor = RiskMonitor(self.portfolio_manager)
        
        # 数据提供者（将在后续实现）
        self.data_provider = None  # WindDataProvider()
        self.backup_provider = None  # AkShareProvider()
        
        # 策略引擎（将在后续实现）
        self.strategies = []  # [MultiFactorStrategy(), MomentumStrategy()]
        
        # AI引擎（将在后续实现）
        self.ai_engine = None  # AIEngine()
        
        # 自动化任务（将在后续实现）
        self.scheduler = None  # TaskScheduler()
        
        # 性能追踪
        self.performance_history = []
        self.daily_returns = []
        
        self.logger.info("智能投资代理初始化完成")
        self.status = AgentStatus.READY
        
    def _setup_logging(self):
        """设置日志系统"""
        import os
        
        # 确保日志目录存在
        log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
        os.makedirs(log_dir, exist_ok=True)
        
        # 配置日志
        log_file = os.path.join(log_dir, f'investment_agent_{datetime.now().strftime("%Y%m%d")}.log')
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        
        self.logger = logging.getLogger('InvestmentAgent')
        self.logger.info(f"日志系统初始化完成，日志文件: {log_file}")
    
    def start_trading_day(self):
        """开始交易日"""
        self.logger.info("="*50)
        self.logger.info(f"开始交易日: {datetime.now().strftime('%Y-%m-%d')}")
        self.logger.info("="*50)
        
        # 启动风控日内追踪
        self.risk_monitor.start_trading_day()
        
        # 更新状态
        self.status = AgentStatus.TRADING
        self.last_trading_day = datetime.now()
        
        # 执行盘前任务
        self._execute_pre_market_tasks()
    
    def end_trading_day(self):
        """结束交易日"""
        self.logger.info("="*50)
        self.logger.info(f"结束交易日: {datetime.now().strftime('%Y-%m-%d')}")
        self.logger.info("="*50)
        
        # 计算当日收益
        portfolio_value = self.portfolio_manager.portfolio.total_value
        daily_return = (portfolio_value - CAPITAL_CONFIG['total_capital']) / CAPITAL_CONFIG['total_capital']
        
        # 记录每日收益
        self.daily_returns.append({
            'date': datetime.now(),
            'portfolio_value': portfolio_value,
            'daily_return': daily_return,
            'risk_level': self.risk_monitor.calculate_risk_metrics().risk_level.value
        })
        
        # 记录性能指标
        self.performance_history.append(self.risk_monitor.calculate_risk_metrics())
        
        # 执行盘后任务
        self._execute_post_market_tasks()
        
        # 更新状态
        self.status = AgentStatus.READY
        
        # 生成当日报告
        self._generate_daily_report()
    
    def _execute_pre_market_tasks(self):
        """执行盘前任务"""
        self.logger.info("执行盘前任务...")
        
        # 1. 数据采集（06:30）
        self.logger.info("6:30 - 盘前数据采集")
        if self.data_provider:
            market_data = self.data_provider.collect_pre_market_data()
            self.logger.info(f"采集到 {len(market_data)} 条市场数据")
        
        # 2. AI晨报生成（06:40）
        self.logger.info("6:40 - AI晨报生成")
        morning_report = self._generate_morning_report()
        self.logger.info("晨报生成完成")
        
        # 3. 盘前策略计算（08:30）
        self.logger.info("8:30 - 盘前策略计算")
        trading_recommendations = self._calculate_trading_recommendations()
        self.logger.info(f"生成 {len(trading_recommendations)} 条交易建议")
        
        # 4. 风险检查
        self.logger.info("执行风险检查...")
        risk_alerts = self._check_all_risk_levels()
        if risk_alerts:
            self.logger.warning(f"发现 {len(risk_alerts)} 个风险告警")
        
        return morning_report, trading_recommendations, risk_alerts
    
    def _execute_post_market_tasks(self):
        """执行盘后任务"""
        self.logger.info("执行盘后任务...")
        
        # 1. 数据归档（15:30）
        self.logger.info("15:30 - 盘后数据归档")
        if self.data_provider:
            self.data_provider.archive_market_data()
        
        # 2. 持仓更新
        self.logger.info("更新持仓状态...")
        self._update_positions_with_latest_prices()
        
        # 3. 性能计算
        self.logger.info("计算性能指标...")
        metrics = self.risk_monitor.calculate_risk_metrics()
        self.logger.info(f"组合市值: {metrics.portfolio_value:,.2f}")
        self.logger.info(f"组合回撤: {metrics.portfolio_drawdown:.2%}")
        self.logger.info(f"风险等级: {metrics.risk_level.value}")
    
    def _check_all_risk_levels(self) -> List:
        """
        检查所有风险级别
        
        Returns:
            风险告警列表
        """
        alerts = []
        
        # 第一级：个股止损
        level1_alerts = self.risk_monitor.check_level1_individual_stop_loss()
        alerts.extend(level1_alerts)
        
        # 第二级：组合回撤
        level2_alerts = self.risk_monitor.check_level2_portfolio_drawdown()
        alerts.extend(level2_alerts)
        
        # 第三级：日内断路器
        level3_alerts = self.risk_monitor.check_level3_circuit_breaker()
        alerts.extend(level3_alerts)
        
        return alerts
    
    def _generate_morning_report(self) -> Dict:
        """
        生成AI晨报
        
        Returns:
            晨报内容字典
        """
        self.logger.info("生成AI晨报...")
        
        # 基础晨报结构
        morning_report = {
            'report_id': f"MORNING_{datetime.now().strftime('%Y%m%d')}",
            'date': datetime.now().strftime('%Y-%m-%d'),
            'timestamp': datetime.now(),
            'market_summary': {},
            'portfolio_status': {},
            'trading_recommendations': [],
            'risk_alerts': [],
            'performance_targets': PERFORMANCE_TARGETS
        }
        
        # 市场概要
        morning_report['market_summary'] = {
            'pre_market_sentiment': 'neutral',
            'key_events': [],
            'market_focus': []
        }
        
        # 组合状态
        portfolio = self.portfolio_manager.portfolio
        morning_report['portfolio_status'] = {
            'total_value': portfolio.total_value,
            'cash_balance': portfolio.cash_balance,
            'stock_value': portfolio.stock_value,
            'total_pnl': portfolio.total_pnl,
            'total_pnl_percent': portfolio.total_pnl_percent,
            'position_count': len(portfolio.positions)
        }
        
        # 风险告警
        risk_alerts = self.risk_monitor.get_active_alerts()
        morning_report['risk_alerts'] = [
            {
                'risk_level': alert.risk_level.value,
                'message': alert.message,
                'recommended_action': alert.recommended_action
            }
            for alert in risk_alerts
        ]
        
        self.logger.info(f"晨报生成完成，共 {len(morning_report['risk_alerts'])} 个风险告警")
        return morning_report
    
    def _calculate_trading_recommendations(self) -> List[Dict]:
        """
        计算交易建议
        
        Returns:
            交易建议列表
        """
        self.logger.info("计算交易建议...")
        
        recommendations = []
        
        # 1. 风险控制建议
        risk_alerts = self.risk_monitor.get_active_alerts()
        for alert in risk_alerts:
            if alert.symbol:  # 个股级别的风控建议
                recommendations.append({
                    'type': 'RISK_CONTROL',
                    'symbol': alert.symbol,
                    'action': 'SELL' if '清仓' in alert.recommended_action else 'REDUCE',
                    'reason': alert.message,
                    'priority': 'HIGH' if alert.risk_level == RiskLevel.CRITICAL else 'MEDIUM',
                    'timestamp': datetime.now()
                })
        
        # 2. 再平衡建议
        rebalance_needed = self.portfolio_manager.calculate_rebalance_needed()
        for symbol, (current, target, diff) in rebalance_needed.items():
            if diff > 0.02:  # 超过2%偏差
                action = 'BUY' if diff > 0 else 'SELL'
                recommendations.append({
                    'type': 'REBALANCING',
                    'symbol': symbol,
                    'action': action,
                    'current_weight': current,
                    'target_weight': target,
                    'difference': diff,
                    'reason': f"权重偏差 {diff:.2%} 超过阈值",
                    'priority': 'MEDIUM',
                    'timestamp': datetime.now()
                })
        
        # 3. 策略信号（待实现）
        # if self.strategies:
        #     for strategy in self.strategies:
        #         strategy_signals = strategy.generate_signals()
        #         recommendations.extend(strategy_signals)
        
        self.logger.info(f"生成 {len(recommendations)} 条交易建议")
        return recommendations
    
    def _update_positions_with_latest_prices(self):
        """更新持仓的最新价格"""
        self.logger.info("更新持仓价格...")
        
        # 这里应该调用数据提供者获取最新价格
        # 临时使用配置中的成本价格模拟
        for symbol, position in self.portfolio_manager.portfolio.positions.items():
            if symbol in PORTFOLIO_CONFIG:
                # 模拟价格波动（实际应从API获取）
                simulated_price = position.cost_basis * (1.0 + np.random.normal(0, 0.01))
                self.portfolio_manager.update_position_price(symbol, simulated_price)
    
    def _generate_daily_report(self):
        """生成当日报告"""
        self.logger.info("生成当日报告...")
        
        # 组合摘要
        portfolio_summary = self.portfolio_manager.get_portfolio_summary()
        
        # 风险摘要
        risk_summary = self.risk_monitor.get_risk_summary()
        
        # 性能指标
        performance = self.risk_monitor.calculate_risk_metrics()
        
        # 报告内容
        report = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'portfolio_summary': portfolio_summary,
            'risk_summary': risk_summary,
            'performance': performance,
            'daily_return': self.daily_returns[-1] if self.daily_returns else None,
            'performance_vs_targets': self._compare_with_targets(performance)
        }
        
        self.logger.info("当日报告生成完成")
        return report
    
    def _compare_with_targets(self, performance) -> Dict:
        """比较当前性能与目标"""
        return {
            'annual_return': {
                'current': performance.sharpe_ratio * 0.15,  # 简化估算
                'target': PERFORMANCE_TARGETS['annual_return'],
                'achievement': '达标' if performance.sharpe_ratio * 0.15 >= PERFORMANCE_TARGETS['annual_return'] else '未达标'
            },
            'max_drawdown': {
                'current': abs(performance.portfolio_drawdown),
                'target': PERFORMANCE_TARGETS['max_drawdown'],
                'achievement': '达标' if abs(performance.portfolio_drawdown) <= PERFORMANCE_TARGETS['max_drawdown'] else '超限'
            },
            'sharpe_ratio': {
                'current': performance.sharpe_ratio,
                'target': PERFORMANCE_TARGETS['sharpe_ratio'],
                'achievement': '达标' if performance.sharpe_ratio >= PERFORMANCE_TARGETS['sharpe_ratio'] else '未达标'
            }
        }
    
    def execute_trade(self, symbol: str, action: str, quantity: float, price: float = None) -> bool:
        """
        执行交易
        
        Args:
            symbol: 股票代码
            action: 交易动作 ('BUY', 'SELL')
            quantity: 交易数量
            price: 交易价格（None时使用市价）
            
        Returns:
            交易是否成功
        """
        # 检查风控状态
        can_trade, reason = self.risk_monitor.should_block_trading()
        if not can_trade:
            self.logger.warning(f"交易被阻止: {reason}")
            return False
        
        try:
            if action == 'BUY':
                if price is None:
                    # 模拟市价
                    price = 10.0  # 临时值
                
                self.portfolio_manager.add_position(symbol, quantity, price)
                self.logger.info(f"买入 {symbol}: {quantity}股 @ {price:.2f}")
                
            elif action == 'SELL':
                if price is None:
                    # 模拟市价
                    price = 10.0  # 临时值
                
                self.portfolio_manager.remove_position(symbol, quantity, price)
                self.logger.info(f"卖出 {symbol}: {quantity}股 @ {price:.2f}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"交易执行失败: {str(e)}")
            return False
    
    def get_status(self) -> Dict:
        """获取代理状态"""
        portfolio = self.portfolio_manager.portfolio
        risk_metrics = self.risk_monitor.calculate_risk_metrics()
        
        return {
            'status': self.status.value,
            'start_time': self.start_time.strftime('%Y-%m-%d %H:%M:%S'),
            'last_trading_day': self.last_trading_day.strftime('%Y-%m-%d %H:%M:%S') if self.last_trading_day else None,
            'portfolio': {
                'total_value': portfolio.total_value,
                'cash_balance': portfolio.cash_balance,
                'total_pnl': portfolio.total_pnl,
                'total_pnl_percent': portfolio.total_pnl_percent,
                'position_count': len(portfolio.positions)
            },
            'risk': {
                'level': risk_metrics.risk_level.value,
                'drawdown': risk_metrics.portfolio_drawdown,
                'active_alerts': len(self.risk_monitor.get_active_alerts())
            },
            'performance': {
                'sharpe_ratio': risk_metrics.sharpe_ratio,
                'var_95': risk_metrics.var_95,
                'var_99': risk_metrics.var_99
            }
        }
    
    def get_initial_positions_plan(self) -> pd.DataFrame:
        """获取初始建仓计划"""
        return self.portfolio_manager.get_initial_positions_summary()
    
    def get_portfolio_positions(self) -> pd.DataFrame:
        """获取当前持仓"""
        return self.portfolio_manager.get_portfolio_summary()
    
    def get_risk_alerts(self) -> pd.DataFrame:
        """获取风险告警"""
        return self.risk_monitor.get_alerts_report()
    
    def pause(self):
        """暂停代理"""
        self.logger.info("暂停智能投资代理")
        self.status = AgentStatus.PAUSED
    
    def resume(self):
        """恢复代理"""
        self.logger.info("恢复智能投资代理")
        self.status = AgentStatus.READY
    
    def shutdown(self):
        """关闭代理"""
        self.logger.info("关闭智能投资代理")
        self.status = AgentStatus.INITIALIZING
        
        # 生成最终报告
        final_report = self._generate_daily_report()
        
        # 保存历史数据
        self._save_historical_data()
        
        self.logger.info("智能投资代理已安全关闭")
        return final_report
    
    def _save_historical_data(self):
        """保存历史数据"""
        self.logger.info("保存历史数据...")
        # 这里实现数据保存逻辑
        pass
    
    def __str__(self):
        return f"InvestmentAgent(status={self.status.value}, portfolio_value={self.portfolio_manager.portfolio.total_value:,.2f})"

# 为了代码兼容性
import numpy as np