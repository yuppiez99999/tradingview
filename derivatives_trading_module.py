import sys
import os
from datetime import datetime, timedelta
import math

class OptionPricing:
    """
    期权定价模型
    """
    
    def __init__(self):
        self.risk_free_rate = 0.03
        self.volatility = 0.25
        
    def black_scholes_price(self, S, K, T, r, sigma, option_type='call'):
        """
        Black-Scholes期权定价
        """
        if T <= 0:
            return 0.0
        
        d1 = (math.log(S/K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        
        if option_type == 'call':
            price = S * self.norm_cdf(d1) - K * math.exp(-r * T) * self.norm_cdf(d2)
        else:  # put
            price = K * math.exp(-r * T) * self.norm_cdf(-d2) - S * self.norm_cdf(-d1)
        
        return price
    
    def norm_cdf(self, x):
        """
        标准正态分布累积函数
        """
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))
    
    def calculate_greeks(self, S, K, T, r, sigma, option_type='call'):
        """
        计算期权Greeks
        """
        if T <= 0:
            return {'delta': 0.0, 'gamma': 0.0, 'theta': 0.0, 'vega': 0.0, 'rho': 0.0}
        
        d1 = (math.log(S/K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        
        # Delta
        if option_type == 'call':
            delta = self.norm_cdf(d1)
        else:  # put
            delta = self.norm_cdf(d1) - 1
        
        # Gamma
        gamma = self.norm_pdf(d1) / (S * sigma * math.sqrt(T))
        
        # Theta
        theta = -(S * sigma * self.norm_pdf(d1)) / (2 * math.sqrt(T))
        theta -= r * K * math.exp(-r * T) * self.norm_cdf(d2) if option_type == 'call' else -r * K * math.exp(-r * T) * self.norm_cdf(-d2)
        
        # Vega
        vega = S * math.sqrt(T) * self.norm_pdf(d1)
        
        # Rho
        if option_type == 'call':
            rho = K * T * math.exp(-r * T) * self.norm_cdf(d2)
        else:  # put
            rho = -K * T * math.exp(-r * T) * self.norm_cdf(-d2)
        
        return {
            'delta': delta,
            'gamma': gamma,
            'theta': theta,
            'vega': vega,
            'rho': rho
        }
    
    def norm_pdf(self, x):
        """
        标准正态分布概率密度函数
        """
        return math.exp(-0.5 * x**2) / math.sqrt(2 * math.pi)

class DerivativesTradingModule:
    """
    期权期货交易模块
    """
    
    def __init__(self, initial_capital=2000000):
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.positions = []
        self.orders = []
        self.risk_limits = self._setup_risk_limits()
        
        # 策略配置
        self.strategies = {
            'delta_hedge': {
                'name': 'Delta对冲策略',
                'target_delta': 0.0,
                'max_positions': 50,
                'capital_allocation': 500000
            },
            'volatility_target': {
                'name': '波动率目标策略',
                'target_volatility': 0.20,
                'max_vega': 10000,
                'capital_allocation': 400000
            },
            'straddle': {
                'name': '跨式策略',
                'capital_allocation': 300000
            },
            'spread': {
                'name': '价差策略',
                'capital_allocation': 300000
            },
            'covered_call': {
                'name': '备兑开仓',
                'capital_allocation': 200000
            }
        }
        
        # 期权定价
        self.pricing_model = OptionPricing()
        
        print("期权期货交易模块初始化完成")
        print(f"初始资金: {initial_capital:,.0f}元")
        print(f"策略配置: {self.strategies}")
    
    def _setup_risk_limits(self):
        """
        设置衍生品交易风险限制
        """
        return {
            'option_position_limit': 100,
            'max_delta': 0.30,
            'max_gamma': 0.15,
            'max_vega': 5000,
            'max_theta': -1000,
            'max_rho': 1000,
            'future_position_limit': 20,
            'max_leverage': 2.0,
            'min_margin_ratio': 0.15
        }
    
    def calculate_portfolio_greeks(self):
        """
        计算组合Greeks
        """
        total_greeks = {
            'delta': 0.0,
            'gamma': 0.0,
            'theta': 0.0,
            'vega': 0.0,
            'rho': 0.0
        }
        
        for pos in self.positions:
            if pos['type'] == 'option':
                greeks = pos.get('greeks', {})
                for greek, value in greeks.items():
                    total_greeks[greek] += value * pos['quantity']
        
        return total_greeks
    
    def execute_delta_hedge(self, underlying_price, target_delta=0.0):
        """
        执行Delta对冲
        """
        print("\n=== 执行Delta对冲策略 ===")
        
        # 计算当前组合Delta
        portfolio_greeks = self.calculate_portfolio_greeks()
        current_delta = portfolio_greeks['delta']
        
        print(f"当前组合Delta: {current_delta:.3f}")
        print(f"目标Delta: {target_delta:.3f}")
        
        delta_adjustment = target_delta - current_delta
        print(f"需要调整Delta: {delta_adjustment:.3f}")
        
        # 计算需要交易的期权数量
        # 假设使用50ETF期权，Delta约为0.5
        option_delta = 0.5
        option_quantity = delta_adjustment / option_delta
        
        print(f"建议交易 {option_quantity:.0f} 份期权")
        
        # 执行交易
        if abs(option_quantity) > 0:
            order = {
                'symbol': '50ETF购7月2800',
                'quantity': int(option_quantity),
                'action': 'buy' if option_quantity > 0 else 'sell',
                'strategy': 'delta_hedge',
                'timestamp': datetime.now()
            }
            self.place_order(order)
        
        return delta_adjustment
    
    def volatility_strategy(self, current_vol, target_vol=0.20):
        """
        波动率目标策略
        """
        print("\n=== 执行波动率目标策略 ===")
        
        # 计算组合Vega
        portfolio_greeks = self.calculate_portfolio_greeks()
        current_vega = portfolio_greeks['vega']
        print(f"当前组合Vega: {current_vega:.3f}")
        
        # 计算波动率偏差
        vol_deviation = current_vol - target_vol
        print(f"波动率偏差: {vol_deviation:.3f}")
        
        if abs(vol_deviation) > 0.05:  # 5%阈值
            # 调整期权组合
            print("执行波动率调整...")
            # 简化的调整逻辑
            adjustment = -vol_deviation * 1000  # 每单位波动率调整1000 Vega
            print(f"建议调整期权组合Vega {adjustment:.0f}")
        
        return vol_deviation
    
    def straddle_strategy(self, underlying_price, strike_price, time_to_expiry):
        """
        跨式策略
        """
        print("\n=== 执行跨式策略 ===")
        
        # 计算期权价格
        call_price = self.pricing_model.black_scholes_price(
            underlying_price, strike_price, time_to_expiry, 
            self.pricing_model.risk_free_rate, self.pricing_model.volatility, 'call'
        )
        
        put_price = self.pricing_model.black_scholes_price(
            underlying_price, strike_price, time_to_expiry, 
            self.pricing_model.risk_free_rate, self.pricing_model.volatility, 'put'
        )
        
        print(f"看涨期权价格: {call_price:.4f}")
        print(f"看跌期权价格: {put_price:.4f}")
        print(f"跨式组合成本: {call_price + put_price:.4f}")
        
        # 计算盈亏平衡点
        upper_break_even = strike_price + call_price + put_price
        lower_break_even = strike_price - call_price - put_price
        
        print(f"上盈亏平衡点: {upper_break_even:.2f}")
        print(f"下盈亏平衡点: {lower_break_even:.2f}")
        
        # 计算最大收益和最大损失
        max_profit = float('inf')  # 跨式策略理论上收益无限
        max_loss = call_price + put_price
        
        print(f"最大损失: {max_loss:.4f}")
        print(f"波动范围: {lower_break_even:.2f} - {upper_break_even:.2f}")
        
        # 执行跨式交易
        if len([p for p in self.positions if p['strategy'] == 'straddle']) == 0:
            # 买入跨式组合
            call_order = {
                'symbol': f'50ETF购7月{strike_price}',
                'quantity': 10,
                'action': 'buy',
                'strategy': 'straddle',
                'price': call_price,
                'timestamp': datetime.now()
            }
            
            put_order = {
                'symbol': f'50ETF沽7月{strike_price}',
                'quantity': 10,
                'action': 'buy',
                'strategy': 'straddle',
                'price': put_price,
                'timestamp': datetime.now()
            }
            
            self.place_order(call_order)
            self.place_order(put_order)
        
        return {
            'call_price': call_price,
            'put_price': put_price,
            'cost': call_price + put_price,
            'upper_break_even': upper_break_even,
            'lower_break_even': lower_break_even
        }
    
    def spread_strategy(self, underlying_price):
        """
        价差策略
        """
        print("\n=== 执行价差策略 ===")
        
        # 垂直价差示例
        strike1 = 2800
        strike2 = 2900
        
        # 计算期权价格
        call1_price = self.pricing_model.black_scholes_price(
            underlying_price, strike1, 0.1, 
            self.pricing_model.risk_free_rate, self.pricing_model.volatility, 'call'
        )
        
        call2_price = self.pricing_model.black_scholes_price(
            underlying_price, strike2, 0.1, 
            self.pricing_model.risk_free_rate, self.pricing_model.volatility, 'call'
        )
        
        print(f"看涨期权{strike1}: {call1_price:.4f}")
        print(f"看涨期权{strike2}: {call2_price:.4f}")
        print(f"价差成本: {call2_price - call1_price:.4f}")
        
        # 最大收益和最大损失
        max_profit = strike2 - strike1 - (call2_price - call1_price)
        max_loss = call2_price - call1_price
        
        print(f"最大收益: {max_profit:.4f}")
        print(f"最大损失: {max_loss:.4f}")
        
        return {
            'spread_cost': call2_price - call1_price,
            'max_profit': max_profit,
            'max_loss': max_loss
        }
    
    def covered_call_strategy(self, underlying_symbol, underlying_quantity, strike_price):
        """
        备兑开仓策略
        """
        print("\n=== 执行备兑开仓策略 ===")
        
        # 计算看涨期权价格
        call_price = self.pricing_model.black_scholes_price(
            300, strike_price, 0.1, 
            self.pricing_model.risk_free_rate, self.pricing_model.volatility, 'call'
        )
        
        print(f"股票: {underlying_symbol}, 数量: {underlying_quantity}")
        print(f"行权价: {strike_price}")
        print(f"期权价格: {call_price:.4f}")
        
        # 计算收益
        premium_income = underlying_quantity * call_price
        downside_protection = (strike_price - 300) / 300 * 100 if strike_price > 300 else 0
        
        print(f"权利金收入: {premium_income:.2f}")
        print(f"下行保护: {downside_protection:.1f}%")
        
        # 执行备兑开仓
        if len([p for p in self.positions if p['strategy'] == 'covered_call']) == 0:
            option_order = {
                'symbol': f'50ETF购7月{strike_price}',
                'quantity': underlying_quantity,
                'action': 'sell',
                'strategy': 'covered_call',
                'price': call_price,
                'timestamp': datetime.now()
            }
            self.place_order(option_order)
        
        return {
            'premium_income': premium_income,
            'downside_protection': downside_protection,
            'call_price': call_price
        }
    
    def place_order(self, order):
        """
        下单
        """
        print(f"\n下单: {order['action']} {order['quantity']}股 {order['symbol']} @ {order.get('price', '市场价')}")
        
        # 检查风险限制
        if self.check_risk_limits(order):
            # 模拟成交
            order['status'] = 'filled'
            order['fill_price'] = order.get('price', 300)
            order['fill_timestamp'] = datetime.now()
            
            self.orders.append(order)
            
            # 更新持仓
            self.update_position(order)
            
            print("订单成交成功")
        else:
            order['status'] = 'rejected'
            print("订单被拒绝：风险限制")
    
    def update_position(self, order):
        """
        更新持仓
        """
        # 查找现有持仓
        existing_pos = None
        for pos in self.positions:
            if pos['symbol'] == order['symbol'] and pos['strategy'] == order['strategy']:
                existing_pos = pos
                break
        
        if existing_pos:
            # 更新数量
            if order['action'] == 'buy':
                existing_pos['quantity'] += order['quantity']
            else:  # sell
                existing_pos['quantity'] -= order['quantity']
            
            if existing_pos['quantity'] == 0:
                self.positions.remove(existing_pos)
        else:
            # 创建新持仓
            new_pos = {
                'symbol': order['symbol'],
                'quantity': order['quantity'] if order['action'] == 'buy' else -order['quantity'],
                'strategy': order['strategy'],
                'entry_price': order.get('fill_price', 300),
                'timestamp': order['fill_timestamp']
            }
            
            # 计算Greeks
            if 'option' in order['symbol']:
                K = int(order['symbol'].split('月')[1][:-2]) if '月' in order['symbol'] else 2800
                T = 0.1
                underlying_price = 300
                sigma = 0.25
                
                greeks = self.pricing_model.calculate_greeks(underlying_price, K, T, 0.03, sigma, 'call')
                new_pos['greeks'] = greeks
            
            self.positions.append(new_pos)
    
    def check_risk_limits(self, order):
        """
        检查风险限制
        """
        # 模拟检查
        if order['quantity'] > self.risk_limits['option_position_limit']:
            return False
        
        # 检查Delta限制
        portfolio_greeks = self.calculate_portfolio_greeks()
        if 'option' in order['symbol']:
            # 简化的Delta检查
            estimated_delta = 0.5 * order['quantity']
            if abs(portfolio_greeks['delta'] + estimated_delta) > self.risk_limits['max_delta']:
                return False
        
        return True
    
    def monitor_positions(self):
        """
        监控持仓
        """
        print("\n=== 持仓监控 ===")
        
        if not self.positions:
            print("当前无持仓")
            return
        
        # 计算组合Greeks
        portfolio_greeks = self.calculate_portfolio_greeks()
        print("组合Greeks:")
        for greek, value in portfolio_greeks.items():
            print(f"  {greek}: {value:.3f}")
        
        # 检查风险限制
        print("\n风险限制检查:")
        violations = []
        
        if abs(portfolio_greeks['delta']) > self.risk_limits['max_delta']:
            violations.append(f"Delta超限: {portfolio_greeks['delta']:.3f} > {self.risk_limits['max_delta']}")
        
        if abs(portfolio_greeks['gamma']) > self.risk_limits['max_gamma']:
            violations.append(f"Gamma超限: {portfolio_greeks['gamma']:.3f} > {self.risk_limits['max_gamma']}")
        
        if abs(portfolio_greeks['vega']) > self.risk_limits['max_vega']:
            violations.append(f"Vega超限: {portfolio_greeks['vega']:.3f} > {self.risk_limits['max_vega']}")
        
        if violations:
            print("风险限制违规:")
            for violation in violations:
                print(f"  - {violation}")
        else:
            print("✓ 所有风险限制正常")
        
        # 显示持仓详情
        print("\n持仓详情:")
        for pos in self.positions:
            print(f"{pos['symbol']} ({pos['strategy']}): {pos['quantity']}股")
            if 'greeks' in pos:
                greeks = pos['greeks']
                print(f"  Delta: {greeks['delta']:.3f}")
                print(f"  Gamma: {greeks['gamma']:.3f}")
                print(f"  Vega: {greeks['vega']:.3f}")
    
    def run_simulation(self):
        """
        运行交易模拟
        """
        print("开始期权期货交易模拟...")
        
        # 模拟市场数据
        underlying_price = 300
        volatility = 0.25
        
        print("\n1. 执行Delta对冲策略")
        self.execute_delta_hedge(underlying_price)
        
        print("\n2. 执行波动率目标策略")
        self.volatility_strategy(volatility)
        
        print("\n3. 执行跨式策略")
        self.straddle_strategy(underlying_price, 2800, 0.1)
        
        print("\n4. 执行价差策略")
        self.spread_strategy(underlying_price)
        
        print("\n5. 执行备兑开仓策略")
        self.covered_call_strategy('50ETF', 1000, 2800)
        
        print("\n6. 监控持仓")
        self.monitor_positions()
        
        print("\n交易模拟完成")

class HedgeStrategiesManager:
    """对冲策略管理器 - 管理多策略对冲组合"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.total_capital = self.config.get('total_capital', 5000000)
        self.allocations = self.config.get('hedge_strategies', {
            'delta_hedge': {'allocation': 0.25, 'target_delta': 0.0},
            'volatility_hedge': {'allocation': 0.30, 'target_vega': 0.0},
            'absolute_return': {'allocation': 0.25, 'target_correlation': 0.0},
            'volatility_arbitrage': {'allocation': 0.15, 'max_position': 100000},
            'covered_write': {'allocation': 0.05, 'target_return': 0.08}
        })
        self.positions = {}
        self.performance = {
            'hedge_pnl': 0.0,
            'hedge_return': 0.0,
            'hedge_sharpe': 0.0,
            'active_strategies': 5
        }
        self.derivatives_module = DerivativesTradingModule(
            initial_capital=int(self.total_capital * 0.40)
        )

    def execute_hedge_strategy(self, strategy: str, params: dict = None) -> dict:
        """执行指定对冲策略"""
        params = params or {}
        if strategy == 'delta_hedge':
            result = self.derivatives_module.execute_delta_hedge(
                params.get('underlying_price', 300),
                params.get('target_delta', 0.0)
            )
        elif strategy == 'volatility_hedge':
            result = self.derivatives_module.volatility_strategy(
                params.get('current_vol', 0.25),
                params.get('target_vol', 0.20)
            )
        elif strategy == 'covered_write':
            result = self.derivatives_module.covered_call_strategy(
                params.get('symbol', '50ETF'),
                params.get('quantity', 1000),
                params.get('strike', 2800)
            )
        else:
            result = {'strategy': strategy, 'status': 'simulated'}

        return {
            'success': True,
            'strategy': strategy,
            'result': result,
            'allocation': self.allocations.get(strategy, {}).get('allocation', 0)
        }

    def execute_all_hedges(self) -> dict:
        """执行所有对冲策略"""
        results = {}
        for strategy in self.allocations:
            results[strategy] = self.execute_hedge_strategy(strategy)
        return {
            'success': True,
            'strategies_executed': len(results),
            'results': results,
            'performance': self.performance
        }

    def get_hedge_status(self) -> dict:
        """获取对冲状态"""
        return {
            'total_hedge_capital': int(self.total_capital * 0.40),
            'allocations': self.allocations,
            'positions': self.positions,
            'performance': self.performance
        }


# 主程序
if __name__ == "__main__":
    print("期权期货交易模块启动")
    print("=" * 50)
    
    # 创建交易模块
    derivatives_trading = DerivativesTradingModule(initial_capital=2000000)
    
    # 运行模拟
    derivatives_trading.run_simulation()
    
    print("\n交易模拟完成")
    print("=" * 50)