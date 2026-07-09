import sys
import os
from datetime import datetime, timedelta
import random
import statistics

class StressTestingSystem:
    """
    压力测试与情景分析系统
    """
    
    def __init__(self, initial_capital=5000000):
        self.initial_capital = initial_capital
        self.portfolio_value = initial_capital
        self.positions = []
        self.scenarios = []
        self.test_results = []
        
        # 预设压力情景
        self.predefined_scenarios = {
            'market_crash': {
                'name': '市场崩盘',
                'description': '市场大幅下跌，流动性枯竭',
                'market_return': -0.30,  # 市场下跌30%
                'volatility_multiplier': 2.0,  # 波动率加倍
                'liquidity_penalty': -0.05,  # 流动性惩罚
                'correlation_breakdown': True,  # 相关性崩溃
                'duration': 5  # 持续天数
            },
            'financial_crisis': {
                'name': '金融危机',
                'description': '系统性金融风险爆发',
                'market_return': -0.25,  # 市场下跌25%
                'volatility_multiplier': 3.0,  # 波动率3倍
                'credit_spread_widening': 0.05,  # 信用利差扩大
                'counterparty_risk': -0.08,  # 交易对手风险
                'duration': 10  # 持续天数
            },
            'liquidity_crisis': {
                'name': '流动性危机',
                'description': '市场流动性严重不足',
                'market_return': -0.15,  # 市场下跌15%
                'liquidity_penalty': -0.10,  # 流动性惩罚
                'bid_ask_spread_widening': 0.03,  # 买卖价差扩大
                'forced_selling_pressure': -0.05,  # 强制抛售压力
                'duration': 7  # 持续天数
            },
            'black_swan': {
                'name': '黑天鹅事件',
                'description': '极端市场事件',
                'market_return': -0.40,  # 市场下跌40%
                'volatility_multiplier': 4.0,  # 波动率4倍
                'correlation_spike': 0.95,  # 相关性飙升
                'tail_risk_realized': -0.12,  # 尾部风险实现
                'duration': 3  # 持续天数
            },
            'interest_rate_shock': {
                'name': '利率冲击',
                'description': '利率大幅波动',
                'interest_rate_change': 0.02,  # 利率上升200bp
                'bond_yield_change': 0.03,  # 债券收益率上升300bp
                'currency_depreciation': -0.05,  # 货币贬值
                'duration': 5  # 持续天数
            }
        }
        
        # 自定义情景模板
        self.custom_scenario_template = {
            'name': '',
            'description': '',
            'market_return': 0.0,
            'volatility_multiplier': 1.0,
            'liquidity_penalty': 0.0,
            'correlation_breakdown': False,
            'credit_spread_widening': 0.0,
            'counterparty_risk': 0.0,
            'forced_selling_pressure': 0.0,
            'tail_risk_realized': 0.0,
            'interest_rate_change': 0.0,
            'bond_yield_change': 0.0,
            'currency_depreciation': 0.0,
            'duration': 1
        }
        
        print("压力测试与情景分析系统初始化完成")
        print(f"初始资金: {initial_capital:,.0f}元")
        print(f"预设情景数量: {len(self.predefined_scenarios)}")
    
    def setup_portfolio(self):
        """
        设置投资组合
        """
        # 模拟投资组合
        self.positions = [
            {'symbol': '沪深300ETF', 'quantity': 3500, 'price': 300, 'type': 'etf', 'value': 1050000},
            {'symbol': '中证500ETF', 'quantity': 3000, 'price': 250, 'type': 'etf', 'value': 750000},
            {'symbol': '中证1000ETF', 'quantity': 2400, 'price': 250, 'type': 'etf', 'value': 600000},
            {'symbol': '科创50ETF', 'quantity': 1800, 'price': 250, 'type': 'etf', 'value': 450000},
            {'symbol': '创业板ETF', 'quantity': 600, 'price': 250, 'type': 'etf', 'value': 150000},
            {'symbol': '50ETF购7月2800', 'quantity': 10, 'price': 0.05, 'type': 'option', 'value': 500},
            {'symbol': '300ETF沽7月3200', 'quantity': 15, 'price': 0.08, 'type': 'option', 'value': 1200},
            {'symbol': '沪深300股指', 'quantity': -2, 'price': 3200, 'type': 'future', 'value': -6400}
        ]
        
        total_value = sum(pos['value'] for pos in self.positions)
        cash = self.initial_capital - total_value
        
        self.portfolio_value = self.initial_capital
        
        print(f"投资组合设置完成:")
        print(f"总价值: {total_value:,.0f}元")
        print(f"现金: {cash:,.0f}元")
        print(f"持仓数量: {len(self.positions)}")
    
    def run_predefined_scenarios(self):
        """
        运行预设压力情景
        """
        print("\n=== 预设压力情景测试 ===")
        
        for scenario_name, scenario_data in self.predefined_scenarios.items():
            print(f"\n--- {scenario_data['name']} ({scenario_name}) ---")
            print(f"描述: {scenario_data['description']}")
            
            result = self.execute_scenario(scenario_data)
            self.scenarios.append({
                'name': scenario_name,
                'data': scenario_data,
                'result': result
            })
            self.test_results.append(result)
    
    def execute_scenario(self, scenario):
        """
        执行单个压力情景
        """
        initial_value = self.portfolio_value
        
        print(f"初始价值: {initial_value:,.0f}元")
        
        # 模拟压力期间的每日变化
        daily_returns = []
        portfolio_values = [initial_value]
        
        for day in range(scenario['duration']):
            # 生成每日收益率
            daily_return = self.generate_daily_return(scenario)
            daily_returns.append(daily_return)
            
            # 计算当日价值
            previous_value = portfolio_values[-1]
            current_value = previous_value * (1 + daily_return)
            portfolio_values.append(current_value)
            
            print(f"第{day+1}天: {current_value:,.0f}元 ({daily_return:.2%})")
        
        # 计算结果
        final_value = portfolio_values[-1]
        total_return = (final_value - initial_value) / initial_value
        max_drawdown = self.calculate_max_drawdown(portfolio_values)
        volatility = statistics.stdev(daily_returns) if len(daily_returns) > 1 else 0
        
        result = {
            'scenario_name': scenario['name'],
            'initial_value': initial_value,
            'final_value': final_value,
            'total_return': total_return,
            'max_drawdown': max_drawdown,
            'volatility': volatility,
            'duration': scenario['duration'],
            'portfolio_values': portfolio_values
        }
        
        print(f"\n结果总结:")
        print(f"总收益: {total_return:.2%}")
        print(f"最大回撤: {max_drawdown:.2%}")
        print(f"最终价值: {final_value:,.0f}元")
        print(f"波动率: {volatility:.2%}")
        
        return result
    
    def generate_daily_return(self, scenario):
        """
        生成每日收益率
        """
        # 基础市场收益
        base_return = scenario['market_return'] / scenario['duration']
        
        # 增加随机性
        random_factor = random.uniform(-0.02, 0.02)
        
        # 波动率调整
        volatility_adjustment = random.uniform(-1, 1) * scenario['volatility_multiplier'] * 0.01
        
        # 流动性惩罚
        liquidity_penalty = scenario['liquidity_penalty'] / scenario['duration']
        
        # 相关性崩溃
        correlation_breakdown = 0.0
        if scenario['correlation_breakdown']:
            correlation_breakdown = random.uniform(-0.01, 0.01)
        
        # 信用利差扩大
        credit_penalty = scenario['credit_spread_widening'] / scenario['duration']
        
        # 交易对手风险
        counterparty_penalty = scenario['counterparty_risk'] / scenario['duration']
        
        # 强制抛售压力
        forced_penalty = scenario['forced_selling_pressure'] / scenario['duration']
        
        # 尾部风险
        tail_penalty = scenario['tail_risk_realized'] / scenario['duration']
        
        # 利率冲击
        interest_penalty = scenario['interest_rate_change'] / scenario['duration']
        
        # 债券收益率变化
        bond_penalty = scenario['bond_yield_change'] / scenario['duration']
        
        # 货币贬值
        currency_penalty = scenario['currency_depreciation'] / scenario['duration']
        
        # 综合收益率
        daily_return = (base_return + random_factor + volatility_adjustment + 
                       liquidity_penalty + correlation_breakdown + credit_penalty +
                       counterparty_penalty + forced_penalty + tail_penalty +
                       interest_penalty + bond_penalty + currency_penalty)
        
        # 限制收益率范围
        daily_return = max(-0.15, min(0.15, daily_return))
        
        return daily_return
    
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
    
    def monte_carlo_simulation(self, num_simulations=1000):
        """
        蒙特卡洛模拟
        """
        print("\n=== 蒙特卡洛模拟 ===")
        print(f"模拟次数: {num_simulations}")
        
        simulation_results = []
        
        for i in range(num_simulations):
            # 随机生成市场参数
            market_return = random.uniform(-0.3, 0.3)
            volatility = random.uniform(0.1, 0.4)
            correlation = random.uniform(0.2, 0.8)
            
            # 模拟1年表现
            daily_returns = [
                random.gauss(market_return/252, volatility/252) 
                for _ in range(252)
            ]
            
            # 计算累积收益
            cumulative_return = 1.0
            portfolio_values = [self.initial_capital]
            
            for daily_return in daily_returns:
                cumulative_return *= (1 + daily_return)
                portfolio_values.append(self.initial_capital * cumulative_return)
            
            # 计算结果指标
            total_return = cumulative_return - 1.0
            max_drawdown = self.calculate_max_drawdown(portfolio_values)
            volatility = statistics.stdev(daily_returns) * math.sqrt(252)
            
            simulation_results.append({
                'total_return': total_return,
                'max_drawdown': max_drawdown,
                'volatility': volatility,
                'final_value': portfolio_values[-1]
            })
        
        # 统计结果
        total_returns = [r['total_return'] for r in simulation_results]
        max_drawdowns = [r['max_drawdown'] for r in simulation_results]
        volatilities = [r['volatility'] for r in simulation_results]
        
        print("\n蒙特卡洛模拟结果:")
        print(f"平均收益: {statistics.mean(total_returns):.2%}")
        print(f"收益标准差: {statistics.stdev(total_returns):.2%}")
        print(f"最大回撤 - 均值: {statistics.mean(max_drawdowns):.2%}")
        print(f"最大回撤 - 95%分位: {sorted(max_drawdowns)[int(0.95 * len(max_drawdowns))]:.2%}")
        print(f"波动率 - 均值: {statistics.mean(volatilities):.2%}")
        
        # VaR计算
        var_95 = sorted(total_returns)[int(0.05 * len(total_returns))]
        var_99 = sorted(total_returns)[int(0.01 * len(total_returns))]
        
        print(f"VaR 95%: {var_95:.2%}")
        print(f"VaR 99%: {var_99:.2%}")
        
        return simulation_results
    
    def backtesting_scenarios(self, historical_scenarios):
        """
        历史情景回测
        """
        print("\n=== 历史情景回测 ===")
        
        for i, scenario_data in enumerate(historical_scenarios):
            print(f"\n--- 历史情景 {i+1}: {scenario_data['name']} ---")
            
            result = self.execute_scenario(scenario_data)
            self.test_results.append(result)
    
    def create_custom_scenario(self, name, description, parameters):
        """
        创建自定义情景
        """
        custom_scenario = self.custom_scenario_template.copy()
        custom_scenario.update({
            'name': name,
            'description': description,
            **parameters
        })
        
        print(f"\n--- 自定义情景: {name} ---")
        print(f"描述: {description}")
        
        result = self.execute_scenario(custom_scenario)
        self.test_results.append(result)
        
        return custom_scenario
    
    def generate_stress_test_report(self):
        """
        生成压力测试报告
        """
        print("\n=== 压力测试报告 ===")
        
        if not self.test_results:
            print("无测试结果")
            return
        
        print("\n1. 预设情景测试结果:")
        print("情景名称\t\t总收益\t\t最大回撤\t波动率")
        print("-" * 70)
        
        for scenario in self.scenarios:
            result = scenario['result']
            print(f"{result['scenario_name']}\t\t{result['total_return']:.2%}\t{result['max_drawdown']:.2%}\t{result['volatility']:.2%}")
        
        print("\n2. 统计摘要:")
        total_returns = [r['total_return'] for r in self.test_results]
        max_drawdowns = [r['max_drawdown'] for r in self.test_results]
        volatilities = [r['volatility'] for r in self.test_results]
        
        print(f"平均收益: {statistics.mean(total_returns):.2%}")
        print(f"收益范围: {min(total_returns):.2%} ~ {max(total_returns):.2%}")
        print(f"平均最大回撤: {statistics.mean(max_drawdowns):.2%}")
        print(f"平均波动率: {statistics.mean(volatilities):.2%}")
        
        print("\n3. 风险评估:")
        # 计算压力测试下的风险指标
        worst_case = min(total_returns)
        worst_drawdown = max(max_drawdowns)
        
        print(f"最坏情况收益: {worst_case:.2%}")
        print(f"最大回撤: {worst_drawdown:.2%}")
        
        # 评估风险等级
        if worst_drawdown > 0.20:
            risk_level = "极高风险"
        elif worst_drawdown > 0.15:
            risk_level = "高风险"
        elif worst_drawdown > 0.10:
            risk_level = "中等风险"
        else:
            risk_level = "低风险"
        
        print(f"风险等级: {risk_level}")
        
        print("\n4. 建议措施:")
        if worst_drawdown > 0.15:
            print("- 建议降低组合杠杆")
            print("- 增加对冲比例")
            print("- 优化流动性配置")
        
        if worst_case < -0.20:
            print("- 考虑调整资产配置")
            print("- 加强尾部风险管理")
            print("- 建立应急资金储备")
        
        print("\n5. 压力测试完成")
    
    def run_comprehensive_stress_test(self):
        """
        运行全面压力测试
        """
        print("开始全面压力测试...")
        
        # 设置投资组合
        self.setup_portfolio()
        
        # 运行预设情景
        self.run_predefined_scenarios()
        
        # 运行蒙特卡洛模拟
        self.monte_carlo_simulation()
        
        # 创建自定义情景
        custom_params = {
            'market_return': -0.20,
            'volatility_multiplier': 2.5,
            'liquidity_penalty': -0.08,
            'duration': 7
        }
        self.create_custom_scenario("市场波动加剧", "市场大幅波动，流动性紧张", custom_params)
        
        # 生成报告
        self.generate_stress_test_report()
        
        print("\n全面压力测试完成")

# 主程序
if __name__ == "__main__":
    import math  # 添加math模块导入
    
    print("压力测试与情景分析系统启动")
    print("=" * 50)
    
    # 创建压力测试系统
    stress_system = StressTestingSystem(initial_capital=5000000)
    
    # 运行全面压力测试
    stress_system.run_comprehensive_stress_test()
    
    print("\n压力测试完成")
    print("=" * 50)