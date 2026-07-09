import sys
import os

def calculate_portfolio_metrics(positions, prices, benchmark_prices):
    """
    计算投资组合风险指标
    """
    # 组合价值计算
    total_value = sum(pos['quantity'] * prices.get(pos['symbol'], 0) for pos in positions)
    
    # 组合风险指标
    portfolio_metrics = {
        'total_value': total_value,
        'positions_count': len(positions),
        'beta': calculate_portfolio_beta(positions, prices, benchmark_prices),
        'volatility': calculate_portfolio_volatility(positions, prices),
        'concentration': calculate_concentration(positions, prices),
        'liquidity_score': calculate_liquidity_score(positions, prices)
    }
    
    return portfolio_metrics

def calculate_portfolio_beta(positions, prices, benchmark_prices):
    """计算组合Beta"""
    if not positions or not benchmark_prices:
        return 0.0
    
    portfolio_returns = []
    benchmark_returns = []
    
    # 简化的Beta计算（实际需要历史数据）
    return 1.0  # 默认值

def calculate_portfolio_volatility(positions, prices):
    """计算组合波动率"""
    if not positions:
        return 0.0
    
    # 基于持仓权重计算组合波动率
    weights = []
    volatilities = []
    
    for pos in positions:
        price = prices.get(pos['symbol'], 0)
        if price > 0:
            weight = pos['quantity'] * price
            weights.append(weight)
            
            # 不同资产的波动率
            if pos['type'] == 'stock':
                vol = 0.25  # 股票25%波动率
            elif pos['type'] == 'etf':
                vol = 0.20  # ETF 20%波动率
            elif pos['type'] == 'option':
                vol = 0.30  # 期权30%波动率
            elif pos['type'] == 'future':
                vol = 0.15  # 期货15%波动率
            else:
                vol = 0.20
            
            volatilities.append(vol)
    
    if not weights:
        return 0.0
    
    # 加权平均波动率
    total_weight = sum(weights)
    weighted_vol = sum(w * v for w, v in zip(weights, volatilities)) / total_weight
    
    return weighted_vol

def calculate_concentration(positions, prices):
    """计算组合集中度"""
    if not positions:
        return 0.0
    
    position_values = []
    for pos in positions:
        price = prices.get(pos['symbol'], 0)
        if price > 0:
            value = pos['quantity'] * price
            position_values.append(value)
    
    if not position_values:
        return 0.0
    
    total_value = sum(position_values)
    max_position = max(position_values)
    
    return max_position / total_value if total_value > 0 else 0.0

def calculate_liquidity_score(positions, prices):
    """计算流动性评分"""
    if not positions:
        return 0.0
    
    liquidity_scores = []
    
    for pos in positions:
        # 根据资产类型和规模评估流动性
        if pos['type'] == 'stock':
            if pos['quantity'] < 10000:
                score = 0.8  # 小盘股流动性较低
            else:
                score = 0.9
        elif pos['type'] == 'etf':
            score = 0.95  # ETF流动性好
        elif pos['type'] == 'option':
            score = 0.7  # 期权流动性中等
        elif pos['type'] == 'future':
            score = 0.85  # 期货流动性好
        else:
            score = 0.6
        
        liquidity_scores.append(score)
    
    return sum(liquidity_scores) / len(liquidity_scores) if liquidity_scores else 0.0

class HedgePortfolioManager:
    """
    对冲头寸管理系统
    """
    
    def __init__(self, initial_capital=5000000):
        self.initial_capital = initial_capital
        self.positions = []
        self.cash = initial_capital
        self.total_value = initial_capital
        self.performance_history = []
        
        # 风险限制
        self.risk_limits = {
            'max_concentration': 0.20,  # 单一资产最大占比
            'max_portfolio_volatility': 0.15,  # 组合最大波动率
            'max_beta': 1.0,  # 最大Beta值
            'min_liquidity_score': 0.75,  # 最小流动性评分
            'max_drawdown': 0.08,  # 最大回撤
        }
        
        # 对冲策略参数
        self.hedge_strategies = {
            'delta_hedge': {
                'target_delta': 0.0,
                'delta_threshold': 0.05,
                'rebalance_frequency': 'daily'
            },
            'volatility_target': {
                'target_volatility': 0.12,
                'volatility_threshold': 0.05,
                'rebalance_frequency': 'weekly'
            },
            'correlation_target': {
                'target_correlation': 0.0,
                'correlation_threshold': 0.1,
                'rebalance_frequency': 'monthly'
            }
        }
        
        print(f"对冲头寸管理系统初始化完成")
        print(f"初始资金: {initial_capital:,.0f}元")
        print(f"风险限制: {self.risk_limits}")
    
    def add_position(self, symbol, quantity, price, asset_type, strategy=None):
        """
        添加持仓
        """
        position = {
            'symbol': symbol,
            'quantity': quantity,
            'price': price,
            'type': asset_type,
            'strategy': strategy,
            'entry_date': '2026-07-02',
            'cost_basis': quantity * price
        }
        
        self.positions.append(position)
        
        # 更新现金
        self.cash -= quantity * price
        
        # 更新组合价值
        self.update_portfolio_value()
        
        print(f"添加持仓: {symbol} {quantity}股 @{price:.2f} (类型:{asset_type})")
        print(f"现金剩余: {self.cash:,.0f}元")
        
        # 检查风险限制
        self.check_risk_limits()
    
    def remove_position(self, symbol, quantity):
        """
        减少持仓
        """
        for pos in self.positions:
            if pos['symbol'] == symbol:
                if pos['quantity'] >= quantity:
                    pos['quantity'] -= quantity
                    self.cash += quantity * pos['price']
                    
                    if pos['quantity'] == 0:
                        self.positions.remove(pos)
                    
                    self.update_portfolio_value()
                    print(f"减少持仓: {symbol} {quantity}股")
                    return True
        
        print(f"持仓 {symbol} 数量不足")
        return False
    
    def update_portfolio_value(self):
        """
        更新组合价值
        """
        # 简化计算，实际需要实时价格
        total_positions_value = sum(pos['quantity'] * pos['price'] for pos in self.positions)
        self.total_value = self.cash + total_positions_value
        
        # 记录绩效
        performance = {
            'date': '2026-07-02',
            'total_value': self.total_value,
            'cash': self.cash,
            'positions_value': total_positions_value,
            'return': (self.total_value - self.initial_capital) / self.initial_capital
        }
        
        self.performance_history.append(performance)
    
    def check_risk_limits(self):
        """
        检查风险限制
        """
        print("\n=== 风险限制检查 ===")
        
        # 获取当前价格（简化计算）
        prices = {pos['symbol']: pos['price'] for pos in self.positions}
        
        # 计算风险指标
        metrics = calculate_portfolio_metrics(self.positions, prices, {})
        
        print(f"组合价值: {metrics['total_value']:,.0f}元")
        print(f"组合Beta: {metrics['beta']:.3f} (限制: {self.risk_limits['max_beta']})")
        print(f"组合波动率: {metrics['volatility']:.3f} (限制: {self.risk_limits['max_portfolio_volatility']})")
        print(f"集中度: {metrics['concentration']:.3f} (限制: {self.risk_limits['max_concentration']})")
        print(f"流动性评分: {metrics['liquidity_score']:.3f} (限制: {self.risk_limits['min_liquidity_score']})")
        
        # 检查是否超限
        violations = []
        if metrics['beta'] > self.risk_limits['max_beta']:
            violations.append(f"Beta超限: {metrics['beta']:.3f} > {self.risk_limits['max_beta']}")
        
        if metrics['volatility'] > self.risk_limits['max_portfolio_volatility']:
            violations.append(f"波动率超限: {metrics['volatility']:.3f} > {self.risk_limits['max_portfolio_volatility']}")
        
        if metrics['concentration'] > self.risk_limits['max_concentration']:
            violations.append(f"集中度超限: {metrics['concentration']:.3f} > {self.risk_limits['max_concentration']}")
        
        if metrics['liquidity_score'] < self.risk_limits['min_liquidity_score']:
            violations.append(f"流动性不足: {metrics['liquidity_score']:.3f} < {self.risk_limits['min_liquidity_score']}")
        
        if violations:
            print("\n风险限制违规:")
            for violation in violations:
                print(f"  - {violation}")
        else:
            print("✓ 所有风险限制正常")
    
    def execute_delta_hedge(self):
        """
        执行Delta对冲
        """
        print("\n=== 执行Delta对冲 ===")
        
        # 计算当前组合Delta
        current_delta = self.calculate_portfolio_delta()
        target_delta = self.hedge_strategies['delta_hedge']['target_delta']
        
        print(f"当前Delta: {current_delta:.3f}")
        print(f"目标Delta: {target_delta:.3f}")
        print(f"Delta偏差: {abs(current_delta - target_delta):.3f}")
        
        # 检查是否需要调整
        threshold = self.hedge_strategies['delta_hedge']['delta_threshold']
        if abs(current_delta - target_delta) > threshold:
            print("执行Delta对冲调整...")
            self.adjust_delta_hedge(current_delta, target_delta)
        else:
            print("Delta在可接受范围内，无需调整")
    
    def calculate_portfolio_delta(self):
        """
        计算组合Delta
        """
        if not self.positions:
            return 0.0
        
        total_delta = 0.0
        for pos in self.positions:
            if pos['type'] == 'option':
                # 简化的Delta计算
                if pos['strategy'] == 'call':
                    delta = 0.5  # 看涨期权Delta
                elif pos['strategy'] == 'put':
                    delta = -0.5  # 看跌期权Delta
                else:
                    delta = 0.0
            elif pos['type'] == 'future':
                delta = 1.0  # 期货Delta
            else:
                delta = 1.0  # 股票/ETF Delta
            
            total_delta += pos['quantity'] * delta
        
        return total_delta
    
    def adjust_delta_hedge(self, current_delta, target_delta):
        """
        调整Delta对冲
        """
        delta_to_adjust = target_delta - current_delta
        print(f"需要调整Delta: {delta_to_adjust:.3f}")
        
        # 简化的调整逻辑
        if abs(delta_to_adjust) > 0.1:
            print(f"建议交易 {abs(delta_to_adjust):.3f} 股沪深300ETF以调整Delta")
    
    def rebalance_portfolio(self):
        """
        投资组合再平衡
        """
        print("\n=== 执行投资组合再平衡 ===")
        
        # 检查是否需要再平衡
        self.check_rebalance_need()
        
        # 执行再平衡
        self.execute_rebalance()
    
    def check_rebalance_need(self):
        """
        检查是否需要再平衡
        """
        # 计算目标配置
        target_allocation = {
            '沪深300ETF': 0.35,
            '中证500ETF': 0.25,
            '中证1000ETF': 0.20,
            '科创50ETF': 0.15,
            '创业板ETF': 0.05
        }
        
        # 计算当前配置
        current_allocation = {}
        total_value = self.total_value
        
        for asset in target_allocation:
            current_allocation[asset] = 0.0
        
        # 模拟当前持仓
        positions_info = [
            {'symbol': '沪深300ETF', 'value': 1050000, 'type': 'etf'},
            {'symbol': '中证500ETF', 'value': 750000, 'type': 'etf'},
            {'symbol': '中证1000ETF', 'value': 600000, 'type': 'etf'},
            {'symbol': '科创50ETF', 'value': 450000, 'type': 'etf'},
            {'symbol': '创业板ETF', 'value': 150000, 'type': 'etf'}
        ]
        
        for pos in positions_info:
            if pos['symbol'] in current_allocation:
                current_allocation[pos['symbol']] = pos['value'] / total_value
        
        print("\n资产配置对比:")
        print("资产类别\t目标配置\t当前配置\t偏差")
        print("-" * 60)
        
        rebalance_needed = False
        for asset in target_allocation:
            target = target_allocation[asset]
            current = current_allocation.get(asset, 0.0)
            deviation = current - target
            
            print(f"{asset}\t{target:.2%}\t{current:.2%}\t{deviation:.2%}")
            
            if abs(deviation) > 0.05:  # 5%偏差阈值
                rebalance_needed = True
        
        if rebalance_needed:
            print("\n需要再平衡")
        else:
            print("\n无需再平衡")
    
    def execute_rebalance(self):
        """
        执行再平衡
        """
        print("执行再平衡操作...")
        
        # 基于institutional_trading_plan中的配置
        target_values = {
            '沪深300ETF': 1050000,
            '中证500ETF': 750000,
            '中证1000ETF': 600000,
            '科创50ETF': 450000,
            '创业板ETF': 150000
        }
        
        # 模拟交易调整
        print("交易调整建议:")
        for asset, target_value in target_values.items():
            print(f"- {asset}: 目标价值 {target_value:,.0f}元")
    
    def generate_report(self):
        """
        生成投资组合报告
        """
        print("\n=== 投资组合报告 ===")
        
        print(f"总价值: {self.total_value:,.0f}元")
        print(f"现金: {self.cash:,.0f}元")
        print(f"持仓价值: {self.total_value - self.cash:,.0f}元")
        print(f"持仓数量: {len(self.positions)}")
        
        if self.performance_history:
            latest = self.performance_history[-1]
            print(f"收益率: {latest['return']:.2%}")
        
        print("\n持仓明细:")
        print("资产类型\t数量\t价格\t价值\t策略")
        print("-" * 60)
        
        for pos in self.positions:
            value = pos['quantity'] * pos['price']
            print(f"{pos['type']}\t{pos['quantity']}\t{pos['price']:.2f}\t{value:,.0f}\t{pos['strategy'] or 'N/A'}")
    
    def run_simulation(self):
        """
        运行模拟交易
        """
        print("开始对冲头寸管理模拟...")
        
        # 初始化投资组合
        print("\n1. 初始化投资组合")
        target_positions = [
            {'symbol': '沪深300ETF', 'quantity': 3500, 'price': 300, 'type': 'etf', 'strategy': 'core'},
            {'symbol': '中证500ETF', 'quantity': 3000, 'price': 250, 'type': 'etf', 'strategy': 'core'},
            {'symbol': '中证1000ETF', 'quantity': 2400, 'price': 250, 'type': 'etf', 'strategy': 'core'},
            {'symbol': '科创50ETF', 'quantity': 1800, 'price': 250, 'type': 'etf', 'strategy': 'growth'},
            {'symbol': '创业板ETF', 'quantity': 600, 'price': 250, 'type': 'etf', 'strategy': 'growth'}
        ]
        
        for pos in target_positions:
            self.add_position(**pos)
        
        print("\n2. 添加对冲策略")
        # 添加期权对冲
        hedge_positions = [
            {'symbol': '50ETF购7月2800', 'quantity': 10, 'price': 0.05, 'type': 'option', 'strategy': 'delta_hedge'},
            {'symbol': '300ETF沽7月3200', 'quantity': 15, 'price': 0.08, 'type': 'option', 'strategy': 'delta_hedge'},
            {'symbol': '沪深300股指', 'quantity': -2, 'price': 3200, 'type': 'future', 'strategy': 'hedging'}
        ]
        
        for pos in hedge_positions:
            self.add_position(**pos)
        
        print("\n3. 执行Delta对冲")
        self.execute_delta_hedge()
        
        print("\n4. 执行再平衡")
        self.rebalance_portfolio()
        
        print("\n5. 生成最终报告")
        self.generate_report()

# 主程序
if __name__ == "__main__":
    print("对冲头寸管理系统启动")
    print("=" * 50)
    
    # 创建对冲头寸管理器
    hedge_manager = HedgePortfolioManager(initial_capital=5000000)
    
    # 运行模拟
    hedge_manager.run_simulation()
    
    print("\n模拟完成")
    print("=" * 50)