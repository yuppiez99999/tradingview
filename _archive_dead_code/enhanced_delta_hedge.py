# -*- coding: utf-8 -*-
"""
增强版动态Delta对冲策略 - 第一层保护
基于投资组合Beta值调整对冲比例，使用沪深300股指期货对冲系统性风险

功能特点：
1. 动态Beta计算和监控
2. 自适应对冲比例调整
3. 市场环境感知的对冲策略
4. 多层级风险控制

作者：量化策略系统 v5.10优化版
"""

import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import logging

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

try:
    from utils.logging_manager import get_logger
    logger = get_logger('enhanced_delta_hedge')
except ImportError:
    import logging
    logger = logging.getLogger('enhanced_delta_hedge')


class EnhancedDeltaHedge:
    """增强版动态Delta对冲策略 - 第一层保护"""
    
    def __init__(self, 
                 initial_capital: float = 5000000,
                 hedge_ratio: float = 0.20,  # 默认对冲比例20%
                 beta_target: float = 0.0,   # 目标Beta值
                 min_hedge_ratio: float = 0.05,  # 最小对冲比例
                 max_hedge_ratio: float = 0.60,  # 最大对冲比例
                 rebalance_threshold: float = 0.05,  # 再平衡阈值
                 market_sentiment_threshold: float = 0.7  # 市场情绪阈值
                ):
        """
        初始化增强版Delta对冲策略
        
        Args:
            initial_capital: 初始资金（500万）
            hedge_ratio: 初始对冲比例
            beta_target: 目标Beta值
            min_hedge_ratio: 最小对冲比例
            max_hedge_ratio: 最大对冲比例
            rebalance_threshold: 再平衡阈值
            market_sentiment_threshold: 市场情绪阈值
        """
        self.initial_capital = initial_capital
        self.total_capital = initial_capital
        
        # 对冲参数
        self.hedge_ratio = hedge_ratio
        self.beta_target = beta_target
        self.min_hedge_ratio = min_hedge_ratio
        self.max_hedge_ratio = max_hedge_ratio
        self.rebalance_threshold = rebalance_threshold
        self.market_sentiment_threshold = market_sentiment_threshold
        
        # 风险限制
        self.risk_limits = {
            'max_drawdown': 0.08,  # 最大回撤8%
            'max_single_loss': 0.03,  # 单日最大损失3%
            'max_position_concentration': 0.15,  # 单一标的最大权重15%
            'max_sector_concentration': 0.50,  # 板块最大权重50%
        }
        
        # 市场环境感知
        self.market_regime = 'normal'  # normal, warning, crisis
        self.market_volatility = 0.0
        self.market_sentiment = 0.0
        
        # 对冲历史
        self.hedge_history = []
        self.beta_history = []
        self.performance_history = []
        
        # 沪深300股指期货合约规格
        self.index_future_spec = {
            'multiplier': 300,  # 合约乘数
            'margin_ratio': 0.12,  # 保证金比例
            'tick_size': 0.2,  # 最小变动单位
        }
        
        logger.info(f"增强版Delta对冲策略初始化完成")
        logger.info(f"初始资金: {initial_capital:,.0f}元")
        logger.info(f"初始对冲比例: {hedge_ratio:.1%}")
        logger.info(f"目标Beta: {beta_target:.2f}")
        
    def calculate_portfolio_beta(self, 
                               portfolio_returns: pd.Series, 
                               benchmark_returns: pd.Series,
                               lookback_days: int = 60) -> float:
        """
        计算投资组合Beta值
        
        Args:
            portfolio_returns: 组合收益率序列
            benchmark_returns: 基准收益率序列
            lookback_days: 回溯天数
            
        Returns:
            Beta值
        """
        if len(portfolio_returns) < 30 or len(benchmark_returns) < 30:
            return 1.0  # 默认值
            
        # 对齐长度
        min_length = min(len(portfolio_returns), len(benchmark_returns), lookback_days)
        if min_length < 30:
            return 1.0
            
        port_ret = portfolio_returns.tail(min_length)
        bench_ret = benchmark_returns.tail(min_length)
        
        # 计算协方差和方差
        covariance = np.cov(port_ret, bench_ret)[0, 1]
        benchmark_variance = np.var(bench_ret)
        
        if benchmark_variance == 0:
            return 0.0
            
        beta = covariance / benchmark_variance
        return float(beta)
        
    def calculate_dynamic_hedge_ratio(self, 
                                  current_beta: float,
                                  market_volatility: float,
                                  market_sentiment: float,
                                  portfolio_drawdown: float) -> float:
        """
        计算动态对冲比例
        
        Args:
            current_beta: 当前组合Beta
            market_volatility: 市场波动率
            market_sentiment: 市场情绪
            portfolio_drawdown: 组合回撤
            
        Returns:
            动态对冲比例
        """
        # 基础对冲比例
        base_hedge = abs(current_beta - self.beta_target)
        
        # 市场波动率调整
        if market_volatility > 0.25:  # 高波动环境
            volatility_factor = 1.5
        elif market_volatility > 0.15:  # 中等波动
            volatility_factor = 1.2
        else:  # 低波动环境
            volatility_factor = 1.0
            
        # 市场情绪调整
        if market_sentiment < 0.3:  # 恐慌情绪
            sentiment_factor = 1.3
        elif market_sentiment < 0.5:  # 悲观情绪
            sentiment_factor = 1.1
        else:  # 乐观情绪
            sentiment_factor = 1.0
            
        # 组合回撤调整
        if portfolio_drawdown > 0.05:  # 大回撤
            drawdown_factor = 1.4
        elif portfolio_drawdown > 0.03:  # 中等回撤
            drawdown_factor = 1.2
        else:  # 小回撤
            drawdown_factor = 1.0
            
        # 计算动态对冲比例
        dynamic_ratio = base_hedge * volatility_factor * sentiment_factor * drawdown_factor
        
        # 应用限制
        dynamic_ratio = np.clip(dynamic_ratio, self.min_hedge_ratio, self.max_hedge_ratio)
        
        return float(dynamic_ratio)
        
    def determine_market_regime(self, 
                             market_volatility: float,
                             market_sentiment: float,
                             credit_spread: float = 0.02) -> str:
        """
        确定市场环境
        
        Args:
            market_volatility: 市场波动率
            market_sentiment: 市场情绪
            credit_spread: 信用利差
            
        Returns:
            市场环境：normal, warning, crisis
        """
        # 计算市场压力指数
        pressure_index = (
            (market_volatility - 0.15) / 0.15 * 0.4 +  # 波动率贡献40%
            (1 - market_sentiment) * 0.3 +  # 情绪贡献30%
            min(credit_spread / 0.05, 1.0) * 0.3  # 信用利差贡献30%
        )
        
        if pressure_index > 0.7:
            return 'crisis'
        elif pressure_index > 0.4:
            return 'warning'
        else:
            return 'normal'
            
    def calculate_hedge_position_size(self, 
                                    portfolio_value: float,
                                    hedge_ratio: float,
                                    future_price: float = 3200.0) -> int:
        """
        计算对冲头寸规模
        
        Args:
            portfolio_value: 组合价值
            hedge_ratio: 对冲比例
            future_price: 股指期货价格
            
        Returns:
            股指期货合约数量
        """
        # 对冲价值
        hedge_value = portfolio_value * hedge_ratio
        
        # 计算合约数量
        contract_value = future_price * self.index_future_spec['multiplier']
        position_size = hedge_value / contract_value
        
        # 向上取整
        return int(np.ceil(position_size))
        
    def execute_delta_hedge(self, 
                          portfolio_value: float,
                          current_beta: float,
                          market_volatility: float,
                          market_sentiment: float,
                          portfolio_drawdown: float = 0.0,
                          future_price: float = 3200.0) -> Dict:
        """
        执行Delta对冲
        
        Args:
            portfolio_value: 组合价值
            current_beta: 当前组合Beta
            market_volatility: 市场波动率
            market_sentiment: 市场情绪
            portfolio_drawdown: 组合回撤
            future_price: 股指期货价格
            
        Returns:
            对冲操作结果
        """
        # 更新市场环境
        self.market_volatility = market_volatility
        self.market_sentiment = market_sentiment
        self.market_regime = self.determine_market_regime(market_volatility, market_sentiment)
        
        # 计算动态对冲比例
        dynamic_ratio = self.calculate_dynamic_hedge_ratio(
            current_beta, market_volatility, market_sentiment, portfolio_drawdown
        )
        
        # 计算对冲头寸
        target_position = self.calculate_hedge_position_size(portfolio_value, dynamic_ratio, future_price)
        
        # 当前对冲头寸（简化计算）
        current_position = len(self.hedge_history) * 0.1  # 简化表示
        
        # 计算需要调整的数量
        adjustment = target_position - current_position
        
        # 生成对冲指令
        hedge_instruction = {
            'timestamp': datetime.now().isoformat(),
            'market_regime': self.market_regime,
            'current_beta': current_beta,
            'target_beta': self.beta_target,
            'current_hedge_ratio': self.hedge_ratio,
            'dynamic_hedge_ratio': dynamic_ratio,
            'market_volatility': market_volatility,
            'market_sentiment': market_sentiment,
            'portfolio_drawdown': portfolio_drawdown,
            'current_position': int(current_position),
            'target_position': target_position,
            'adjustment': int(adjustment),
            'hedge_value': portfolio_value * dynamic_ratio,
            'decision': self._generate_hedge_decision(adjustment)
        }
        
        # 记录对冲历史
        self.hedge_history.append(hedge_instruction)
        
        logger.info(f"Delta对冲执行完成 - 市场环境: {self.market_regime}")
        logger.info(f"动态对冲比例: {dynamic_ratio:.1%} (Beta: {current_beta:.2f})")
        logger.info(f"对冲头寸: {target_position}张合约")
        
        return hedge_instruction
        
    def _generate_hedge_decision(self, adjustment: float) -> str:
        """生成对冲决策"""
        if abs(adjustment) < 1:
            return "hold"
        elif adjustment > 0:
            return "buy_futures"
        else:
            return "sell_futures"
            
    def monitor_risk_limits(self, portfolio_value: float, daily_return: float) -> Dict:
        """
        监控风险限制
        
        Args:
            portfolio_value: 组合价值
            daily_return: 单日收益率
            
        Returns:
            风险监控结果
        """
        # 计算最大回撤
        if self.performance_history:
            peak_value = max([p['value'] for p in self.performance_history])
            current_drawdown = (peak_value - portfolio_value) / peak_value
        else:
            current_drawdown = 0.0
            
        # 风险检查
        violations = []
        
        if abs(daily_return) > self.risk_limits['max_single_loss']:
            violations.append(f"单日损失超限: {daily_return:.2%} > {self.risk_limits['max_single_loss']:.2%}")
            
        if current_drawdown > self.risk_limits['max_drawdown']:
            violations.append(f"最大回撤超限: {current_drawdown:.2%} > {self.risk_limits['max_drawdown']:.2%}")
            
        # 生成风险报告
        risk_report = {
            'timestamp': datetime.now().isoformat(),
            'portfolio_value': portfolio_value,
            'daily_return': daily_return,
            'current_drawdown': current_drawdown,
            'risk_regime': self.determine_market_regime(self.market_volatility, self.market_sentiment),
            'violations': violations,
            'risk_level': 'high' if violations else 'medium' if current_drawdown > 0.05 else 'low'
        }
        
        if violations:
            logger.warning(f"风险限制违规: {violations}")
        else:
            logger.info("风险限制检查通过")
            
        return risk_report
        
    def generate_hedge_report(self) -> str:
        """
        生成对冲策略报告
        
        Returns:
            对冲策略报告
        """
        if not self.hedge_history:
            return "暂无对冲历史数据"
            
        latest_hedge = self.hedge_history[-1]
        total_hedge_operations = len(self.hedge_history)
        
        # 计算对冲效率
        beta_reduction = abs(latest_hedge['current_beta'] - latest_hedge['target_beta'])
        
        report = f"""
=== 增强版Delta对冲策略报告 ===

基本信息:
- 初始资金: {self.initial_capital:,.0f}元
- 总对冲次数: {total_hedge_operations}
- 当前市场环境: {latest_hedge['market_regime']}
- 当前对冲比例: {latest_hedge['dynamic_hedge_ratio']:.1%}

风险指标:
- 当前Beta: {latest_hedge['current_beta']:.2f}
- 目标Beta: {latest_hedge['target_beta']:.2f}
- Beta减少程度: {beta_reduction:.2f}
- 市场波动率: {latest_hedge['market_volatility']:.1%}
- 市场情绪: {latest_hedge['market_sentiment']:.1%}

对冲操作:
- 当前对冲头寸: {latest_hedge['target_position']}张合约
- 对冲价值: {latest_hedge['hedge_value']:,.0f}元
- 调整方向: {latest_hedge['decision']}

风险控制:
- 最大回撤限制: {self.risk_limits['max_drawdown']:.1%}
- 单日最大损失: {self.risk_limits['max_single_loss']:.1%}
- 最大对冲比例: {self.max_hedge_ratio:.1%}
- 最小对冲比例: {self.min_hedge_ratio:.1%}

优化建议:
"""
        
        # 根据市场环境提供建议
        if latest_hedge['market_regime'] == 'crisis':
            report += "- 当前处于危机环境，建议维持高对冲比例\n"
            report += "- 关注流动性风险，适当减少交易频率\n"
        elif latest_hedge['market_regime'] == 'warning':
            report += "- 当前处于预警环境，建议适度增加对冲比例\n"
            report += "- 密切关注市场信号，准备应对极端情况\n"
        else:
            report += "- 当前市场环境正常，可适度调整对冲比例\n"
            report += "- 关注市场基本面变化，及时调整策略\n"
            
        return report
        
    def run_simulation(self):
        """运行对冲策略模拟"""
        print("开始增强版Delta对冲策略模拟...")
        print("=" * 60)
        
        # 模拟市场数据
        simulation_days = 60
        base_price = 3200.0
        
        for day in range(simulation_days):
            # 生成模拟数据
            date = datetime.now() - timedelta(days=simulation_days - day)
            
            # 市场波动率（模拟）
            market_volatility = np.random.normal(0.15, 0.05)
            market_volatility = np.clip(market_volatility, 0.05, 0.35)
            
            # 市场情绪（模拟）
            market_sentiment = np.random.uniform(0.3, 0.8)
            
            # 组合Beta（模拟）
            portfolio_beta = np.random.normal(1.2, 0.2)
            portfolio_beta = np.clip(portfolio_beta, 0.5, 2.0)
            
            # 组合价值（模拟）
            portfolio_value = self.total_capital * (1 + np.random.normal(0.001, 0.02))
            
            # 组合回撤（模拟）
            drawdown = np.random.uniform(0.0, 0.08)
            
            # 执行对冲
            hedge_result = self.execute_delta_hedge(
                portfolio_value=portfolio_value,
                current_beta=portfolio_beta,
                market_volatility=market_volatility,
                market_sentiment=market_sentiment,
                portfolio_drawdown=drawdown,
                future_price=base_price
            )
            
            # 监控风险
            daily_return = np.random.normal(0.001, 0.02)
            risk_report = self.monitor_risk_limits(portfolio_value, daily_return)
            
            # 记录性能
            performance_record = {
                'date': date.isoformat(),
                'portfolio_value': portfolio_value,
                'daily_return': daily_return,
                'market_volatility': market_volatility,
                'market_sentiment': market_sentiment,
                'portfolio_beta': portfolio_beta,
                'hedge_ratio': hedge_result['dynamic_hedge_ratio'],
                'risk_level': risk_report['risk_level']
            }
            self.performance_history.append(performance_record)
            
            # 每10天输出一次报告
            if day % 10 == 9:
                print(f"\n第{day+1}天状态:")
                print(f"  市场环境: {hedge_result['market_regime']}")
                print(f"  对冲比例: {hedge_result['dynamic_hedge_ratio']:.1%}")
                print(f"  组合Beta: {portfolio_beta:.2f}")
                print(f"  风险等级: {risk_report['risk_level']}")
        
        # 生成最终报告
        print("\n" + "=" * 60)
        print("模拟完成，生成最终报告:")
        print(self.generate_hedge_report())


def main():
    """主函数"""
    print("增强版Delta对冲策略启动")
    print("=" * 60)
    
    # 创建对冲策略实例
    hedge_strategy = EnhancedDeltaHedge(
        initial_capital=5000000,
        hedge_ratio=0.20,
        beta_target=0.0
    )
    
    # 运行模拟
    hedge_strategy.run_simulation()
    
    print("\n增强版Delta对冲策略模拟完成")
    print("=" * 60)


if __name__ == "__main__":
    main()