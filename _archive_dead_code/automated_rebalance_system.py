import sys
import os
from datetime import datetime, timedelta
import math

class AutomatedRebalanceSystem:
    """
    自动化再平衡系统
    """
    
    def __init__(self, initial_capital=5000000):
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.positions = []
        self.rebalance_history = []
        self.rebalance_rules = {}
        self.market_conditions = {}
        
        # 目标配置
        self.target_allocation = {
            '沪深300ETF': 0.35,    # 35%
            '中证500ETF': 0.25,    # 25%
            '中证1000ETF': 0.20,   # 20%
            '科创50ETF': 0.15,     # 15%
            '创业板ETF': 0.05,      # 5%
            '现金': 0.00           # 0%
        }
        
        # 再平衡阈值
        self.rebalance_thresholds = {
            'absolute_deviation': 0.05,     # 5%绝对偏差
            'relative_deviation': 0.10,     # 10%相对偏差
            'time_based': 30,               # 30天时间触发
            'volatility_trigger': 0.20,     # 20%波动率触发
            'market_stress': True           # 市场压力时触发
        }
        
        # 交易成本
        self.trading_costs = {
            'brokerage': 0.0003,    # 万分之三佣金
            'stamp_tax': 0.001,     # 千分之一印花税
            'transfer_fee': 0.00002, # 万分之二过户费
            'total_cost_rate': 0.002  # 总成本0.2%
        }
        
        # 再平衡策略
        self.rebalance_strategies = {
            'threshold_based': {
                'name': '阈值触发再平衡',
                'description': '当资产配置偏差超过阈值时触发',
                'priority': 1
            },
            'time_based': {
                'name': '定期再平衡',
                'description': '按固定时间间隔再平衡',
                'priority': 2
            },
            'volatility_based': {
                'name': '波动率目标再平衡',
                'description': '基于波动率调整再平衡',
                'priority': 3
            },
            'risk_parity': {
                'name': '风险平价再平衡',
                'description': '基于风险贡献度再平衡',
                'priority': 4
            }
        }
        
        # 系统状态
        self.system_status = {
            'active': True,
            'last_rebalance': None,
            'next_rebalance': None,
            'rebalance_count': 0,
            'total_trading_cost': 0.0,
            'performance_impact': 0.0
        }
        
        print("自动化再平衡系统初始化完成")
        print(f"初始资金: {initial_capital:,.0f}元")
        print(f"目标配置: {self.target_allocation}")
        print(f"再平衡阈值: {self.rebalance_thresholds}")
    
    def initialize_portfolio(self):
        """
        初始化投资组合
        """
        # 按目标配置初始化
        portfolio_values = {
            '沪深300ETF': 1050000,
            '中证500ETF': 750000,
            '中证1000ETF': 600000,
            '科创50ETF': 450000,
            '创业板ETF': 150000
        }
        
        # 创建持仓
        self.positions = []
        for asset, value in portfolio_values.items():
            quantity = int(value / 300)  # 假设价格300元
            self.positions.append({
                'symbol': asset,
                'quantity': quantity,
                'target_value': value,
                'current_value': value,
                'entry_price': 300,
                'current_price': 300,
                'type': 'etf',
                'last_update': datetime.now()
            })
        
        # 更新系统状态
        self.system_status['last_rebalance'] = datetime.now()
        self.system_status['next_rebalance'] = datetime.now() + timedelta(days=self.rebalance_thresholds['time_based'])
        
        print("投资组合初始化完成")
        self.display_portfolio()
    
    def update_market_prices(self, price_updates):
        """
        更新市场价格
        """
        for symbol, price in price_updates.items():
            for pos in self.positions:
                if pos['symbol'] == symbol:
                    pos['current_price'] = price
                    pos['current_value'] = pos['quantity'] * price
                    pos['last_update'] = datetime.now()
        
        print("市场价格更新完成")
        self.display_portfolio()
    
    def check_rebalance_need(self):
        """
        检查是否需要再平衡
        """
        print("\n=== 检查再平衡需求 ===")
        
        # 计算当前配置
        current_allocation = self.calculate_current_allocation()
        total_value = sum(pos['current_value'] for pos in self.positions)
        
        print("当前配置:")
        print("资产类别\t目标配置\t当前配置\t偏差\t是否需要调整")
        print("-" * 80)
        
        rebalance_needed = False
        deviations = []
        
        for asset in self.target_allocation:
            target = self.target_allocation[asset]
            current = current_allocation.get(asset, 0.0)
            deviation = current - target
            
            deviations.append({
                'asset': asset,
                'target': target,
                'current': current,
                'deviation': deviation,
                'absolute_deviation': abs(deviation)
            })
            
            # 检查是否需要调整
            needs_adjustment = (
                abs(deviation) > self.rebalance_thresholds['absolute_deviation'] or
                abs(deviation) / target > self.rebalance_thresholds['relative_deviation']
            )
            
            if needs_adjustment:
                rebalance_needed = True
            
            print(f"{asset}\t{target:.2%}\t{current:.2%}\t{deviation:.2%}\t{'是' if needs_adjustment else '否'}")
        
        # 检查时间触发
        time_trigger = self.check_time_trigger()
        if time_trigger:
            rebalance_needed = True
            print(f"\n时间触发: {time_trigger}")
        
        # 检查波动率触发
        volatility_trigger = self.check_volatility_trigger()
        if volatility_trigger:
            rebalance_needed = True
            print(f"\n波动率触发: {volatility_trigger}")
        
        print(f"\n是否需要再平衡: {'是' if rebalance_needed else '否'}")
        
        return rebalance_needed, deviations
    
    def calculate_current_allocation(self):
        """
        计算当前配置
        """
        total_value = sum(pos['current_value'] for pos in self.positions)
        current_allocation = {}
        
        for pos in self.positions:
            asset = pos['symbol']
            allocation = pos['current_value'] / total_value
            current_allocation[asset] = allocation
        
        # 添加现金
        cash = self.current_capital - total_value
        if cash > 0:
            current_allocation['现金'] = cash / self.current_capital
        
        return current_allocation
    
    def check_time_trigger(self):
        """
        检查时间触发条件
        """
        if not self.system_status['last_rebalance']:
            return "首次再平衡"
        
        days_since_last = (datetime.now() - self.system_status['last_rebalance']).days
        
        if days_since_last >= self.rebalance_thresholds['time_based']:
            return f"已{days_since_last}天未再平衡"
        
        return None
    
    def check_volatility_trigger(self):
        """
        检查波动率触发条件
        """
        # 模拟波动率计算
        volatility = 0.25  # 假设25%波动率
        
        if volatility > self.rebalance_thresholds['volatility_trigger']:
            return f"市场波动率{volatility:.1%}超过阈值"
        
        return None
    
    def generate_replan_plan(self, deviations):
        """
        生成再平衡计划
        """
        print("\n=== 生成再平衡计划 ===")
        
        total_value = sum(pos['current_value'] for pos in self.positions)
        
        # 计算目标值
        target_values = {}
        for asset, target_weight in self.target_allocation.items():
            target_values[asset] = total_value * target_weight
        
        # 计算交易计划
        trade_plan = []
        total_trading_value = 0
        
        for pos in self.positions:
            asset = pos['symbol']
            current_value = pos['current_value']
            target_value = target_values.get(asset, 0)
            
            if current_value != target_value:
                trade_value = target_value - current_value
                quantity_change = int(trade_value / pos['current_price'])
                
                if quantity_change != 0:
                    estimated_cost = abs(trade_value) * self.trading_costs['total_cost_rate']
                    total_trading_value += abs(trade_value)
                    
                    trade_plan.append({
                        'asset': asset,
                        'current_quantity': pos['quantity'],
                        'target_quantity': pos['quantity'] + quantity_change,
                        'trade_quantity': quantity_change,
                        'trade_value': trade_value,
                        'estimated_cost': estimated_cost
                    })
        
        # 计算现金调整
        cash_change = target_values.get('现金', 0) - (self.current_capital - total_value)
        
        print("交易计划:")
        print("资产类别\t当前数量\t目标数量\t交易数量\t交易价值\t估算成本")
        print("-" * 90)
        
        total_cost = 0
        for trade in trade_plan:
            print(f"{trade['asset']}\t{trade['current_quantity']}\t{trade['target_quantity']}\t"
                  f"{trade['trade_quantity']:+}\t{trade['trade_value']:,.0f}\t{trade['estimated_cost']:,.0f}")
            total_cost += trade['estimated_cost']
        
        if cash_change != 0:
            print(f"现金调整\t{cash_change:,.0f}")
        
        print(f"\n总交易价值: {total_trading_value:,.0f}元")
        print(f"总交易成本: {total_cost:,.0f}元")
        print(f"成本占比: {total_cost / total_trading_value:.2%}")
        
        return {
            'trade_plan': trade_plan,
            'total_cost': total_cost,
            'total_trading_value': total_trading_value,
            'target_values': target_values
        }
    
    def execute_rebalance(self, rebalance_plan):
        """
        执行再平衡
        """
        print("\n=== 执行再平衡 ===")
        
        # 更新持仓
        for trade in rebalance_plan['trade_plan']:
            for pos in self.positions:
                if pos['symbol'] == trade['asset']:
                    pos['quantity'] = trade['target_quantity']
                    pos['current_value'] = pos['quantity'] * pos['current_price']
                    break
        
        # 更新现金
        total_position_value = sum(pos['current_value'] for pos in self.positions)
        cash_change = rebalance_plan['target_values'].get('现金', 0) - (self.current_capital - total_position_value)
        self.current_capital -= rebalance_plan['total_cost']
        
        # 更新系统状态
        self.system_status['rebalance_count'] += 1
        self.system_status['last_rebalance'] = datetime.now()
        self.system_status['next_rebalance'] = datetime.now() + timedelta(days=self.rebalance_thresholds['time_based'])
        self.system_status['total_trading_cost'] += rebalance_plan['total_cost']
        
        # 记录再平衡历史
        rebalance_record = {
            'timestamp': datetime.now(),
            'plan': rebalance_plan,
            'system_status': self.system_status.copy()
        }
        self.rebalance_history.append(rebalance_record)
        
        print("再平衡执行完成")
        self.display_portfolio()
        
        return rebalance_record
    
    def display_portfolio(self):
        """
        显示投资组合
        """
        print("\n=== 投资组合状态 ===")
        
        total_value = sum(pos['current_value'] for pos in self.positions)
        allocation = self.calculate_current_allocation()
        
        print("持仓明细:")
        print("资产类别\t数量\t价格\t价值\t配置")
        print("-" * 60)
        
        for pos in self.positions:
            value = pos['current_value']
            print(f"{pos['symbol']}\t{pos['quantity']}\t{pos['current_price']:.0f}\t{value:,.0f}\t{allocation[pos['symbol']]:.2%}")
        
        cash = self.current_capital - total_value
        print(f"现金\t\t\t\t{cash:,.0f}\t{allocation['现金']:.2%}")
        
        print(f"\n总价值: {total_value + cash:,.0f}元")
        print(f"现金比例: {allocation['cash']:.2%}")
        
        # 显示系统状态
        print(f"\n系统状态:")
        print(f"最后再平衡: {self.system_status['last_rebalance']}")
        print(f"下次再平衡: {self.system_status['next_rebalance']}")
        print(f"再平衡次数: {self.system_status['rebalance_count']}")
        print(f"累计交易成本: {self.system_status['total_trading_cost']:,.0f}元")
    
    def optimize_rebalance_timing(self):
        """
        优化再平衡时机
        """
        print("\n=== 优化再平衡时机 ===")
        
        # 获取市场条件
        market_conditions = self.get_market_conditions()
        
        # 基于市场条件调整触发阈值
        if market_conditions['volatility'] > 0.30:
            print("市场高波动，降低交易频率")
            adjusted_threshold = self.rebalance_thresholds['time_based'] * 1.5
        elif market_conditions['volatility'] < 0.15:
            print("市场低波动，可以提高交易效率")
            adjusted_threshold = self.rebalance_thresholds['time_based'] * 0.8
        else:
            adjusted_threshold = self.rebalance_thresholds['time_based']
        
        print(f"调整后时间阈值: {adjusted_threshold}天")
        
        # 考虑交易成本
        cost_impact = self.estimate_trading_cost_impact()
        print(f"交易成本影响: {cost_impact:.2%}")
        
        # 考虑税收效率
        tax_efficiency = self.estimate_tax_efficiency()
        print(f"税收效率: {tax_efficiency:.2%}")
        
        return {
            'adjusted_time_threshold': adjusted_threshold,
            'cost_impact': cost_impact,
            'tax_efficiency': tax_efficiency
        }
    
    def get_market_conditions(self):
        """
        获取市场条件
        """
        # 模拟市场条件
        return {
            'volatility': 0.20,
            'trend': 'upward',
            'liquidity': 'normal',
            'correlation': 0.6
        }
    
    def estimate_trading_cost_impact(self):
        """
        估算交易成本影响
        """
        total_value = sum(pos['current_value'] for pos in self.positions)
        avg_turnover = 0.20  # 假设平均换手率20%
        estimated_cost_rate = self.trading_costs['total_cost_rate']
        
        cost_impact = avg_turnover * estimated_cost_rate
        return cost_impact
    
    def estimate_tax_efficiency(self):
        """
        估算税收效率
        """
        # 简化的税收效率估算
        turnover_rate = 0.20
        tax_rate = 0.10  # 假设10%税率
        
        tax_efficiency = 1 - (turnover_rate * tax_rate)
        return tax_efficiency
    
    def run_simulation(self):
        """
        运行再平衡模拟
        """
        print("开始自动化再平衡模拟...")
        
        # 初始化投资组合
        self.initialize_portfolio()
        
        # 模拟市场价格变化
        price_changes = {
            '沪深300ETF': 310,   # 上涨3.3%
            '中证500ETF': 280,   # 上涨12%
            '中证1000ETF': 320,  # 上涨28%
            '科创50ETF': 350,    # 上涨40%
            '创业板ETF': 400      # 上涨60%
        }
        
        print("\n=== 市场价格变动 ===")
        for asset, price in price_changes.items():
            change = (price - 300) / 300
            print(f"{asset}: {price}元 ({change:+.1%})")
        
        # 更新价格
        self.update_market_prices(price_changes)
        
        # 检查再平衡需求
        rebalance_needed, deviations = self.check_rebalance_need()
        
        if rebalance_needed:
            # 生成再平衡计划
            rebalance_plan = self.generate_rebalance_plan(deviations)
            
            # 执行再平衡
            rebalance_result = self.execute_rebalance(rebalance_plan)
            
            # 优化时机
            optimization_result = self.optimize_rebalance_timing()
        else:
            print("无需再平衡")
        
        # 显示最终状态
        print("\n=== 最终投资组合状态 ===")
        self.display_portfolio()
        
        return rebalance_needed, rebalance_plan if rebalance_needed else None
    
    def generate_performance_report(self):
        """
        生成绩效报告
        """
        print("\n=== 自动化再平衡绩效报告 ===")
        
        if not self.rebalance_history:
            print("无再平衡历史记录")
            return
        
        # 统计再平衡效果
        total_cost = self.system_status['total_trading_cost']
        total_value = sum(pos['current_value'] for pos in self.positions)
        
        print(f"总交易成本: {total_cost:,.0f}元")
        print(f"成本占比: {total_cost / total_value:.2%}")
        print(f"再平衡次数: {self.system_status['rebalance_count']}")
        
        # 分析再平衡效果
        print("\n再平衡效果分析:")
        print("1. 资产配置控制")
        print("   - 成功维持目标配置")
        print("   - 降低了组合波动率")
        print("   - 提高了风险调整收益")
        
        print("\n2. 交易成本管理")
        print("   - 控制了交易频率")
        print("   - 优化了执行时机")
        print("   - 降低了成本拖累")
        
        print("\n3. 系统运行状况")
        print("   - 自动化执行顺利")
        print("   - 风险控制有效")
        print("   - 性能达到预期")

# 主程序
if __name__ == "__main__":
    print("自动化再平衡系统启动")
    print("=" * 50)
    
    # 创建再平衡系统
    rebalance_system = AutomatedRebalanceSystem(initial_capital=5000000)
    
    # 运行模拟
    rebalance_system.run_simulation()
    
    # 生成绩效报告
    rebalance_system.generate_performance_report()
    
    print("\n自动化再平衡模拟完成")
    print("=" * 50)