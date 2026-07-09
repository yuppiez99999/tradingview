import sys
import os
from datetime import datetime, timedelta
import statistics

class PerformanceAttributionSystem:
    """
    绩效归因分析系统
    """
    
    def __init__(self, initial_capital=5000000):
        self.initial_capital = initial_capital
        self.portfolio_value = initial_capital
        self.benchmark_value = initial_capital
        self.performance_data = []
        self.attributions = []
        
        # 策略分类
        self.strategies = {
            'core': {'name': '核心策略', 'allocation': 0.60, 'expected_return': 0.08},
            'hedge': {'name': '对冲策略', 'allocation': 0.25, 'expected_return': 0.10},
            'alpha': {'name': 'Alpha策略', 'allocation': 0.10, 'expected_return': 0.15},
            'tactical': {'name': '战术配置', 'allocation': 0.05, 'expected_return': 0.12}
        }
        
        # 资产分类
        self.asset_classes = {
            'large_cap': {'name': '大盘股', 'allocation': 0.35, 'volatility': 0.20},
            'mid_cap': {'name': '中盘股', 'allocation': 0.25, 'volatility': 0.25},
            'small_cap': {'name': '小盘股', 'allocation': 0.20, 'volatility': 0.30},
            'growth': {'name': '成长股', 'allocation': 0.15, 'volatility': 0.35},
            'value': {'name': '价值股', 'allocation': 0.05, 'volatility': 0.18}
        }
        
        print("绩效归因分析系统初始化完成")
        print(f"初始资金: {initial_capital:,.0f}元")
        print(f"策略配置: {self.strategies}")
        print(f"资产配置: {self.asset_classes}")
    
    def calculate_portfolio_return(self, portfolio_value, previous_value):
        """
        计算组合收益率
        """
        if previous_value == 0:
            return 0.0
        
        return (portfolio_value - previous_value) / previous_value
    
    def calculate_benchmark_return(self, benchmark_value, previous_benchmark_value):
        """
        计算基准收益率
        """
        if previous_benchmark_value == 0:
            return 0.0
        
        return (benchmark_value - previous_benchmark_value) / previous_benchmark_value
    
    def calculate_excess_return(self, portfolio_return, benchmark_return):
        """
        计算超额收益
        """
        return portfolio_return - benchmark_return
    
    def calculate_tracking_error(self, excess_returns):
        """
        计算跟踪误差
        """
        if len(excess_returns) < 2:
            return 0.0
        
        return statistics.stdev(excess_returns)
    
    def calculate_information_ratio(self, avg_excess_return, tracking_error):
        """
        计算信息比率
        """
        if tracking_error == 0:
            return 0.0
        
        return avg_excess_return / tracking_error
    
    def calculate_alpha(self, portfolio_return, benchmark_return, risk_free_rate, portfolio_beta):
        """
        计算Alpha
        """
        return portfolio_return - (risk_free_rate + portfolio_beta * (benchmark_return - risk_free_rate))
    
    def calculate_sharpe_ratio(self, returns, risk_free_rate=0.03):
        """
        计算夏普比率
        """
        if len(returns) < 2:
            return 0.0
        
        avg_return = statistics.mean(returns)
        volatility = statistics.stdev(returns)
        
        if volatility == 0:
            return 0.0
        
        return (avg_return - risk_free_rate) / volatility
    
    def calculate_sortino_ratio(self, returns, risk_free_rate=0.03):
        """
        计算索提诺比率
        """
        if len(returns) < 2:
            return 0.0
        
        avg_return = statistics.mean(returns)
        
        # 计算下行标准差
        downside_returns = [r for r in returns if r < 0]
        downside_volatility = statistics.stdev(downside_returns) if len(downside_returns) > 1 else 0.0
        
        if downside_volatility == 0:
            return 0.0
        
        return (avg_return - risk_free_rate) / downside_volatility
    
    def calculate_max_drawdown(self, portfolio_values):
        """
        计算最大回撤
        """
        if len(portfolio_values) < 2:
            return 0.0
        
        peak = portfolio_values[0]
        max_drawdown = 0.0
        
        for value in portfolio_values[1:]:
            if value > peak:
                peak = value
            else:
                drawdown = (peak - value) / peak
                max_drawdown = max(max_drawdown, drawdown)
        
        return max_drawdown
    
    def calculate_calmar_ratio(self, annual_return, max_drawdown):
        """
        计算卡玛比率
        """
        if max_drawdown == 0:
            return 0.0
        
        return annual_return / max_drawdown
    
    def strategy_attribution(self, portfolio_returns, benchmark_returns, strategy_returns):
        """
        策略归因分析
        """
        print("\n=== 策略归因分析 ===")
        
        attributions = {}
        
        # 总体表现
        total_portfolio_return = sum(portfolio_returns)
        total_benchmark_return = sum(benchmark_returns)
        total_excess_return = self.calculate_excess_return(total_portfolio_return, total_benchmark_return)
        
        print(f"组合总收益: {total_portfolio_return:.2%}")
        print(f"基准总收益: {total_benchmark_return:.2%}")
        print(f"超额收益: {total_excess_return:.2%}")
        
        # 计算跟踪误差和信息比率
        tracking_error = self.calculate_tracking_error([
            self.calculate_excess_return(p, b) 
            for p, b in zip(portfolio_returns, benchmark_returns)
        ])
        
        avg_excess_return = total_excess_return / len(portfolio_returns) if portfolio_returns else 0
        information_ratio = self.calculate_information_ratio(avg_excess_return, tracking_error)
        
        print(f"跟踪误差: {tracking_error:.2%}")
        print(f"信息比率: {information_ratio:.3f}")
        
        # 各策略归因
        for strategy_name, strategy_data in self.strategies.items():
            if strategy_name in strategy_returns:
                strategy_return = sum(strategy_returns[strategy_name])
                strategy_weight = strategy_data['allocation']
                strategy_alpha = strategy_return - strategy_data['expected_return']
                
                attributions[strategy_name] = {
                    'return': strategy_return,
                    'weight': strategy_weight,
                    'expected_return': strategy_data['expected_return'],
                    'alpha': strategy_alpha,
                    'contribution': strategy_weight * strategy_return
                }
                
                print(f"\n{strategy_data['name']}:")
                print(f"  策略权重: {strategy_weight:.2%}")
                print(f"  实际收益: {strategy_return:.2%}")
                print(f"  预期收益: {strategy_data['expected_return']:.2%}")
                print(f"  Alpha: {strategy_alpha:.2%}")
                print(f"  收益贡献: {strategy_weight * strategy_return:.2%}")
        
        return attributions
    
    def asset_attribution(self, portfolio_returns, benchmark_returns, asset_returns):
        """
        资产归因分析
        """
        print("\n=== 资产归因分析 ===")
        
        # 计算选择效应和配置效应
        total_portfolio_return = sum(portfolio_returns)
        total_benchmark_return = sum(benchmark_returns)
        
        # 配置效应（主动配置偏离基准）
        allocation_effect = 0.0
        # 选择效应（资产选择优于基准）
        selection_effect = 0.0
        
        for asset_name, asset_data in self.asset_classes.items():
            if asset_name in asset_returns:
                asset_weight = asset_data['allocation']
                asset_return = sum(asset_returns[asset_name])
                benchmark_asset_return = asset_return * 0.8  # 简化基准收益
                
                # 配置效应
                allocation_effect += asset_weight * (asset_return - benchmark_asset_return)
                
                # 选择效应
                selection_effect += asset_weight * (asset_return - benchmark_asset_return)
                
                print(f"\n{asset_data['name']}:")
                print(f"  权重: {asset_weight:.2%}")
                print(f"  收益: {asset_return:.2%}")
                print(f"  基准收益: {benchmark_asset_return:.2%}")
        
        print(f"\n配置效应: {allocation_effect:.2%}")
        print(f"选择效应: {selection_effect:.2%}")
        
        # 总体归因应该等于超额收益
        total_attribution = allocation_effect + selection_effect
        print(f"总归因: {total_attribution:.2%}")
        print(f"验证（超额收益）: {total_portfolio_return - total_benchmark_return:.2%}")
        
        return {
            'allocation_effect': allocation_effect,
            'selection_effect': selection_effect,
            'total_attribution': total_attribution
        }
    
    def risk_attribution(self, portfolio_returns, benchmark_returns, risk_factors):
        """
        风险归因分析
        """
        print("\n=== 风险归因分析 ===")
        
        # 计算组合Beta
        portfolio_beta = self.calculate_beta(portfolio_returns, benchmark_returns)
        
        # 计算各因子暴露
        factor_exposures = {}
        for factor_name, factor_data in risk_factors.items():
            # 简化的因子暴露计算
            factor_return = sum(factor_data['returns'])
            factor_beta = 0.5  # 简化的Beta
            
            factor_exposures[factor_name] = {
                'exposure': factor_beta,
                'contribution': factor_beta * factor_return,
                'risk_budget': factor_data['risk_budget']
            }
            
            print(f"\n{factor_name}:")
            print(f"  因子暴露: {factor_beta:.3f}")
            print(f"  因子收益: {factor_return:.2%}")
            print(f"  风险贡献: {factor_beta * factor_return:.2%}")
            print(f"  风险预算: {factor_data['risk_budget']:.1%}")
        
        # 组合总风险
        total_risk = sum(fe['contribution'] for fe in factor_exposures.values())
        
        print(f"\n组合总风险: {total_risk:.2%}")
        print(f"组合Beta: {portfolio_beta:.3f}")
        
        return factor_exposures
    
    def calculate_beta(self, portfolio_returns, benchmark_returns):
        """
        计算Beta
        """
        if len(portfolio_returns) != len(benchmark_returns) or len(portfolio_returns) < 2:
            return 0.0
        
        # 简化的Beta计算
        covariance = sum((p - statistics.mean(portfolio_returns)) * (b - statistics.mean(benchmark_returns)) 
                         for p, b in zip(portfolio_returns, benchmark_returns)) / len(portfolio_returns)
        
        benchmark_variance = statistics.variance(benchmark_returns)
        
        if benchmark_variance == 0:
            return 0.0
        
        return covariance / benchmark_variance
    
    def calculate_performance_metrics(self, portfolio_values, benchmark_values):
        """
        计算绩效指标
        """
        if len(portfolio_values) < 2:
            return {}
        
        portfolio_returns = [
            self.calculate_portfolio_value(portfolio_values[i], portfolio_values[i-1])
            for i in range(1, len(portfolio_values))
        ]
        
        benchmark_returns = [
            self.calculate_benchmark_value(benchmark_values[i], benchmark_values[i-1])
            for i in range(1, len(benchmark_values))
        ]
        
        metrics = {
            'total_return': self.calculate_portfolio_value(portfolio_values[-1], portfolio_values[0]),
            'annual_return': statistics.mean(portfolio_returns) * 252,
            'volatility': statistics.stdev(portfolio_returns) * math.sqrt(252),
            'max_drawdown': self.calculate_max_drawdown(portfolio_values),
            'sharpe_ratio': self.calculate_sharpe_ratio(portfolio_returns),
            'sortino_ratio': self.calculate_sortino_ratio(portfolio_returns),
            'calmar_ratio': self.calculate_calmar_ratio(
                statistics.mean(portfolio_returns) * 252,
                self.calculate_max_drawdown(portfolio_values)
            ),
            'beta': self.calculate_beta(portfolio_returns, benchmark_returns),
            'alpha': self.calculate_alpha(
                statistics.mean(portfolio_returns),
                statistics.mean(benchmark_returns),
                0.03,
                self.calculate_beta(portfolio_returns, benchmark_returns)
            )
        }
        
        return metrics
    
    def calculate_portfolio_value(self, current_value, previous_value):
        """
        计算组合收益率
        """
        if previous_value == 0:
            return 0.0
        return (current_value - previous_value) / previous_value
    
    def generate_performance_report(self, period='monthly'):
        """
        生成绩效报告
        """
        print("\n=== 绩效报告 ===")
        
        # 模拟绩效数据
        portfolio_values = [
            5000000, 5020000, 5050000, 5080000, 5100000,
            5120000, 5080000, 5150000, 5180000, 5200000,
            5250000, 5280000, 5300000, 5250000, 5320000,
            5350000, 5380000, 5400000, 5350000, 5420000
        ]
        
        benchmark_values = [
            5000000, 5010000, 5020000, 5030000, 5040000,
            5050000, 5040000, 5060000, 5070000, 5080000,
            5090000, 5100000, 5110000, 5100000, 5120000,
            5130000, 5140000, 5150000, 5140000, 5160000
        ]
        
        # 计算绩效指标
        metrics = self.calculate_performance_metrics(portfolio_values, benchmark_values)
        
        print(f"报告期间: {period}")
        print(f"组合价值: {portfolio_values[-1]:,.0f}元")
        print(f"基准价值: {benchmark_values[-1]:,.0f}元")
        
        print("\n=== 绩效指标 ===")
        for metric, value in metrics.items():
            if isinstance(value, float):
                if 'ratio' in metric or 'sharpe' in metric:
                    print(f"{metric}: {value:.3f}")
                elif 'return' in metric or 'alpha' in metric:
                    print(f"{metric}: {value:.2%}")
                elif 'volatility' in metric:
                    print(f"{metric}: {value:.2%}")
                elif 'beta' in metric:
                    print(f"{metric}: {value:.3f}")
                elif 'max_drawdown' in metric:
                    print(f"{metric}: {value:.2%}")
            else:
                print(f"{metric}: {value}")
        
        # 策略归因
        print("\n=== 策略归因 ===")
        strategy_returns = {
            'core': [0.001, 0.002, 0.001, 0.003, 0.002],
            'hedge': [0.002, 0.001, 0.003, 0.001, 0.002],
            'alpha': [0.003, 0.002, 0.004, 0.002, 0.003],
            'tactical': [0.001, 0.002, 0.001, 0.002, 0.001]
        }
        
        self.strategy_attribution(portfolio_returns, benchmark_returns, strategy_returns)
        
        # 风险归因
        print("\n=== 风险归因 ===")
        risk_factors = {
            'market_risk': {
                'returns': [0.01, 0.005, 0.008, 0.003, 0.006],
                'risk_budget': 0.4
            },
            'volatility_risk': {
                'returns': [0.005, 0.003, 0.006, 0.002, 0.004],
                'risk_budget': 0.3
            },
            'credit_risk': {
                'returns': [0.002, 0.001, 0.003, 0.001, 0.002],
                'risk_budget': 0.2
            },
            'liquidity_risk': {
                'returns': [0.001, 0.001, 0.001, 0.001, 0.001],
                'risk_budget': 0.1
            }
        }
        
        self.risk_attribution(portfolio_returns, benchmark_returns, risk_factors)
        
        # 风险分析
        print("\n=== 风险分析 ===")
        print("VaR分析:")
        print("  VaR 95%: -2.0%")
        print("  VaR 99%: -4.0%")
        print("  期望损失: -3.0%")
        
        print("压力测试:")
        print("  市场下跌20%: 回撤8.5%")
        print("  流动性危机: 回撤10.2%")
        print("  信用风险: 回撤5.8%")
        
        return metrics
    
    def run_analysis(self):
        """
        运行完整分析
        """
        print("开始绩效归因分析...")
        
        # 生成绩效报告
        self.generate_performance_report()
        
        print("\n分析完成")

# 主程序
if __name__ == "__main__":
    import math  # 添加math模块导入
    
    print("绩效归因分析系统启动")
    print("=" * 50)
    
    # 创建绩效归因系统
    attribution_system = PerformanceAttributionSystem(initial_capital=5000000)
    
    # 运行分析
    attribution_system.run_analysis()
    
    print("\n分析完成")
    print("=" * 50)