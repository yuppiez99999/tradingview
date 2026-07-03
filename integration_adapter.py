import sys
import os
from datetime import datetime, timedelta
import threading
import time
from queue import Queue, Empty
import json

class TradingAdapter:
    """
    交易适配器基类
    """
    
    def __init__(self):
        self.order_history = []
        self.position_history = []
        self.account_info = {}
        
    def place_order(self, order):
        """
        下单接口
        """
        raise NotImplementedError("请实现具体的下单方法")
    
    def cancel_order(self, order_id):
        """
        撤单接口
        """
        raise NotImplementedError("请实现具体的撤单方法")
    
    def get_order_status(self, order_id):
        """
        查询订单状态
        """
        raise NotImplementedError("请实现具体的订单查询方法")
    
    def get_positions(self):
        """
        获取持仓
        """
        raise NotImplementedError("请实现具体的持仓查询方法")
    
    def get_account_balance(self):
        """
        获取账户余额
        """
        raise NotImplementedError("请实现具体的余额查询方法")
    
    def get_transaction_history(self, start_date=None, end_date=None):
        """
        获取交易历史
        """
        raise NotImplementedError("请实现具体的交易历史查询方法")

class ExistingTradingAdapter(TradingAdapter):
    """
    现有交易系统适配器
    """
    
    def __init__(self, existing_system_config):
        super().__init__()
        self.config = existing_system_config
        self.connected = False
        
    def connect(self):
        """
        连接现有系统
        """
        try:
            # 模拟连接现有系统
            self.connected = True
            print("成功连接到现有交易系统")
            return True
        except Exception as e:
            print(f"连接失败: {e}")
            return False
    
    def place_order(self, order):
        """
        下单实现
        """
        if not self.connected:
            raise Exception("未连接到交易系统")
        
        # 转换订单格式
        converted_order = self.convert_order_format(order)
        
        # 模拟下单过程
        print(f"下单: {converted_order}")
        
        # 生成订单ID
        order_id = f"ORDER_{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(self.order_history) + 1}"
        
        # 保存订单
        order_data = {
            'order_id': order_id,
            'order': converted_order,
            'status': 'pending',
            'create_time': datetime.now()
        }
        
        self.order_history.append(order_data)
        
        # 模拟订单处理
        threading.Thread(target=self.process_order, args=(order_id,)).start()
        
        return order_id
    
    def cancel_order(self, order_id):
        """
        撤单实现
        """
        # 查找订单
        order_data = self.find_order(order_id)
        if not order_data:
            raise Exception(f"订单 {order_id} 不存在")
        
        if order_data['status'] == 'filled':
            raise Exception("已成交订单无法撤销")
        
        # 更新状态
        order_data['status'] = 'cancelled'
        order_data['cancel_time'] = datetime.now()
        
        print(f"撤销订单: {order_id}")
        return True
    
    def get_order_status(self, order_id):
        """
        查询订单状态
        """
        order_data = self.find_order(order_id)
        if not order_data:
            return None
        
        return {
            'order_id': order_id,
            'status': order_data['status'],
            'create_time': order_data['create_time'],
            'update_time': order_data.get('update_time', datetime.now())
        }
    
    def get_positions(self):
        """
        获取持仓
        """
        # 模拟持仓数据
        positions = [
            {
                'symbol': '沪深300ETF',
                'quantity': 3500,
                'price': 300.0,
                'value': 1050000.0,
                'type': 'etf'
            },
            {
                'symbol': '中证500ETF',
                'quantity': 3000,
                'price': 250.0,
                'value': 750000.0,
                'type': 'etf'
            }
        ]
        
        return positions
    
    def get_account_balance(self):
        """
        获取账户余额
        """
        return {
            'cash': 2000000.0,
            'total_value': 5000000.0,
            'available_balance': 1800000.0,
            'frozen_balance': 200000.0
        }
    
    def get_transaction_history(self, start_date=None, end_date=None):
        """
        获取交易历史
        """
        transactions = [
            {
                'trade_id': 'T001',
                'symbol': '沪深300ETF',
                'quantity': 1000,
                'price': 300.0,
                'amount': 300000.0,
                'action': 'buy',
                'time': datetime.now() - timedelta(days=1)
            },
            {
                'trade_id': 'T002',
                'symbol': '中证500ETF',
                'quantity': 1000,
                'price': 250.0,
                'amount': 250000.0,
                'action': 'buy',
                'time': datetime.now() - timedelta(days=2)
            }
        ]
        
        return transactions
    
    def convert_order_format(self, order):
        """
        转换订单格式
        """
        return {
            'symbol': order['symbol'],
            'quantity': order['quantity'],
            'price': order['price'],
            'action': order['action'],
            'order_type': order.get('order_type', 'limit'),
            'valid_time': order.get('valid_time', 'day'),
            'strategy': order.get('strategy', 'unknown')
        }
    
    def find_order(self, order_id):
        """
        查找订单
        """
        for order_data in self.order_history:
            if order_data['order_id'] == order_id:
                return order_data
        return None
    
    def process_order(self, order_id):
        """
        处理订单
        """
        order_data = self.find_order(order_id)
        if not order_data:
            return
        
        # 模拟订单处理时间
        time.sleep(1)
        
        # 模拟成交
        order_data['status'] = 'filled'
        order_data['fill_time'] = datetime.now()
        order_data['fill_price'] = order_data['order']['price']
        order_data['fill_quantity'] = order_data['order']['quantity']
        
        # 更新持仓
        self.update_position(order_data['order'])
        
        print(f"订单 {order_id} 成交完成")
    
    def update_position(self, order):
        """
        更新持仓
        """
        # 模拟持仓更新
        pass

class RiskControlAdapter:
    """
    风控适配器
    """
    
    def __init__(self):
        self.risk_limits = {
            'max_position_size': 1000000,
            'max_daily_loss': 50000,
            'max_leverage': 2.0,
            'max_concentration': 0.30,
            'var_limit': 0.02
        }
        
        self.current_risk_metrics = {
            'daily_pnl': 0,
            'position_size': 0,
            'leverage': 1.0,
            'concentration': 0.20,
            'var': 0.015
        }
        
        self.alerts = []
        
    def check_risk_limits(self, order):
        """
        检查风险限制
        """
        violations = []
        
        # 检查持仓大小限制
        if order.get('quantity', 0) * order.get('price', 0) > self.risk_limits['max_position_size']:
            violations.append(f"持仓大小限制: {order['quantity']}股 * {order['price']} > {self.risk_limits['max_position_size']}")
        
        # 检查日亏损限制
        if abs(self.current_risk_metrics['daily_pnl']) > self.risk_limits['max_daily_loss']:
            violations.append(f"日亏损限制: 当前{self.current_risk_metrics['daily_pnl']:.0f} > 限制{self.risk_limits['max_daily_loss']}")
        
        # 检查杠杆限制
        if self.current_risk_metrics['leverage'] > self.risk_limits['max_leverage']:
            violations.append(f"杠杆限制: 当前{self.current_risk_metrics['leverage']:.2f} > 限制{self.risk_limits['max_leverage']}")
        
        # 检查集中度限制
        if self.current_risk_metrics['concentration'] > self.risk_limits['max_concentration']:
            violations.append(f"集中度限制: 当前{self.current_risk_metrics['concentration']:.2f} > 限制{self.risk_limits['max_concentration']}")
        
        # 检查VaR限制
        if self.current_risk_metrics['var'] > self.risk_limits['var_limit']:
            violations.append(f"VaR限制: 当前{self.current_risk_metrics['var']:.3f} > 限制{self.risk_limits['var_limit']}")
        
        if violations:
            # 生成预警
            alert = {
                'type': 'risk_violation',
                'message': '风险限制违规',
                'details': violations,
                'timestamp': datetime.now(),
                'level': 'high'
            }
            self.alerts.append(alert)
            
            print("风险限制违规:")
            for violation in violations:
                print(f"  - {violation}")
        
        return len(violations) == 0
    
    def update_risk_metrics(self, order):
        """
        更新风险指标
        """
        # 更新当前指标
        self.current_risk_metrics['position_size'] += order.get('quantity', 0) * order.get('price', 0)
        self.current_risk_metrics['concentration'] = self.current_risk_metrics['position_size'] / 5000000
        self.current_risk_metrics['var'] = 0.015  # 模拟VaR
        
        # 模拟盈亏变化
        if order.get('action') == 'buy':
            self.current_risk_metrics['daily_pnl'] -= order.get('quantity', 0) * order.get('price', 0) * 0.001
    
    def generate_alert(self, level, message, details=None):
        """
        生成预警
        """
        alert = {
            'type': 'alert',
            'level': level,
            'message': message,
            'details': details or [],
            'timestamp': datetime.now()
        }
        
        self.alerts.append(alert)
        
        print(f"[{level.upper()}] {message}")
        if details:
            for detail in details:
                print(f"  - {detail}")
        
        return alert
    
    def get_risk_report(self):
        """
        获取风险报告
        """
        return {
            'risk_limits': self.risk_limits,
            'current_metrics': self.current_risk_metrics,
            'alerts': self.alerts[-10:] if len(self.alerts) > 10 else self.alerts,
            'risk_level': self.assess_risk_level()
        }
    
    def assess_risk_level(self):
        """
        评估风险等级
        """
        violations = 0
        
        if abs(self.current_risk_metrics['daily_pnl']) > self.risk_limits['max_daily_loss']:
            violations += 1
        
        if self.current_risk_metrics['leverage'] > self.risk_limits['max_leverage']:
            violations += 1
        
        if self.current_risk_metrics['concentration'] > self.risk_limits['max_concentration']:
            violations += 1
        
        if self.current_risk_metrics['var'] > self.risk_limits['var_limit']:
            violations += 1
        
        if violations >= 3:
            return 'critical'
        elif violations >= 2:
            return 'high'
        elif violations >= 1:
            return 'medium'
        else:
            return 'low'

class PortfolioManagementAdapter:
    """
    投资组合管理适配器
    """
    
    def __init__(self, initial_capital=5000000):
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.positions = []
        self.performance_history = []
        self.rebalance_history = []
        
        # 目标配置
        self.target_allocation = {
            '沪深300ETF': 0.35,
            '中证500ETF': 0.25,
            '中证1000ETF': 0.20,
            '科创50ETF': 0.15,
            '创业板ETF': 0.05
        }
        
        self.initialize_portfolio()
    
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
        for asset, value in portfolio_values.items():
            quantity = int(value / 300)  # 假设价格300元
            self.positions.append({
                'symbol': asset,
                'quantity': quantity,
                'price': 300.0,
                'value': value,
                'type': 'etf',
                'entry_time': datetime.now()
            })
    
    def update_position(self, order):
        """
        更新持仓
        """
        symbol = order['symbol']
        action = order['action']
        quantity = order['quantity']
        price = order['price']
        
        # 查找持仓
        position = None
        for pos in self.positions:
            if pos['symbol'] == symbol:
                position = pos
                break
        
        if position:
            # 更新持仓
            if action == 'buy':
                position['quantity'] += quantity
            else:  # sell
                position['quantity'] -= quantity
            
            # 更新价值
            position['value'] = position['quantity'] * price
        else:
            # 新持仓
            if action == 'buy':
                self.positions.append({
                    'symbol': symbol,
                    'quantity': quantity,
                    'price': price,
                    'value': quantity * price,
                    'type': order.get('type', 'etf'),
                    'entry_time': datetime.now()
                })
        
        # 更新资本
        if action == 'buy':
            self.current_capital -= quantity * price
        else:
            self.current_capital += quantity * price
        
        # 记录交易
        self.record_trade(order)
    
    def record_trade(self, order):
        """
        记录交易
        """
        trade = {
            'trade_id': f"TRADE_{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(self.performance_history) + 1}",
            'symbol': order['symbol'],
            'quantity': order['quantity'],
            'price': order['price'],
            'amount': order['quantity'] * order['price'],
            'action': order['action'],
            'time': datetime.now(),
            'strategy': order.get('strategy', 'unknown')
        }
        
        self.performance_history.append(trade)
    
    def get_portfolio_status(self):
        """
        获取投资组合状态
        """
        total_value = sum(pos['value'] for pos in self.positions)
        allocation = self.calculate_current_allocation()
        
        # 计算绩效指标
        performance = self.calculate_performance_metrics()
        
        return {
            'total_value': total_value + self.current_capital,
            'cash': self.current_capital,
            'positions': self.positions,
            'allocation': allocation,
            'performance': performance,
            'target_allocation': self.target_allocation
        }
    
    def calculate_current_allocation(self):
        """
        计算当前配置
        """
        total_value = sum(pos['value'] for pos in self.positions)
        allocation = {}
        
        for pos in self.positions:
            asset = pos['symbol']
            allocation[asset] = pos['value'] / total_value if total_value > 0 else 0
        
        # 添加现金
        if self.current_capital > 0:
            allocation['现金'] = self.current_capital / (total_value + self.current_capital)
        
        return allocation
    
    def calculate_performance_metrics(self):
        """
        计算绩效指标
        """
        total_value = sum(pos['value'] for pos in self.positions) + self.current_capital
        
        if len(self.performance_history) < 2:
            return {
                'total_return': 0.0,
                'daily_return': 0.0,
                'max_drawdown': 0.0,
                'sharpe_ratio': 0.0
            }
        
        # 计算收益
        total_return = (total_value - self.initial_capital) / self.initial_capital
        
        # 简化的夏普比率计算
        sharpe_ratio = 1.5 if total_return > 0 else -1.5
        
        # 计算回撤
        max_drawdown = self.calculate_max_drawdown()
        
        return {
            'total_return': total_return,
            'daily_return': total_return / 252,
            'max_drawdown': max_drawdown,
            'sharpe_ratio': sharpe_ratio
        }
    
    def calculate_max_drawdown(self):
        """
        计算最大回撤
        """
        if len(self.performance_history) < 2:
            return 0.0
        
        # 模拟净值曲线
        values = []
        cumulative_value = self.initial_capital
        
        for i, trade in enumerate(self.performance_history):
            if trade['action'] == 'buy':
                cumulative_value -= trade['amount']
            else:
                cumulative_value += trade['amount']
            
            values.append(cumulative_value)
        
        # 计算最大回撤
        peak = values[0]
        max_drawdown = 0.0
        
        for value in values[1:]:
            if value > peak:
                peak = value
            else:
                drawdown = (peak - value) / peak
                max_drawdown = max(max_drawdown, drawdown)
        
        return max_drawdown
    
    def check_rebalance_need(self):
        """
        检查是否需要再平衡
        """
        current_allocation = self.calculate_current_allocation()
        deviations = []
        
        for asset, target_weight in self.target_allocation.items():
            current_weight = current_allocation.get(asset, 0.0)
            deviation = current_weight - target_weight
            deviations.append({
                'asset': asset,
                'target': target_weight,
                'current': current_weight,
                'deviation': deviation
            })
        
        # 检查偏差阈值
        needs_rebalance = any(abs(dev['deviation']) > 0.05 for dev in deviations)
        
        return needs_rebalance, deviations
    
    def execute_rebalance(self):
        """
        执行再平衡
        """
        needs_rebalance, deviations = self.check_rebalance_need()
        
        if not needs_rebalance:
            return False
        
        print("执行再平衡...")
        
        # 生成再平衡计划
        rebalance_plan = self.generate_rebalance_plan(deviations)
        
        # 执行再平衡
        for trade in rebalance_plan['trades']:
            order = {
                'symbol': trade['symbol'],
                'quantity': trade['quantity'],
                'price': trade['price'],
                'action': trade['action'],
                'strategy': 'rebalance'
            }
            
            self.update_position(order)
        
        # 记录再平衡
        self.rebalance_history.append({
            'time': datetime.now(),
            'plan': rebalance_plan,
            'positions': self.positions.copy()
        })
        
        return True
    
    def generate_rebalance_plan(self, deviations):
        """
        生成再平衡计划
        """
        trades = []
        total_value = sum(pos['value'] for pos in self.positions)
        
        for dev in deviations:
            asset = dev['asset']
            target_value = total_value * dev['target']
            current_value = sum(pos['value'] for pos in self.positions if pos['symbol'] == asset)
            
            if abs(target_value - current_value) > 1000:  # 超过1000元才调整
                trade_quantity = int((target_value - current_value) / 300)  # 假设价格300元
                
                if trade_quantity != 0:
                    action = 'buy' if trade_quantity > 0 else 'sell'
                    trades.append({
                        'symbol': asset,
                        'quantity': abs(trade_quantity),
                        'price': 300.0,
                        'action': action
                    })
        
        return {
            'trades': trades,
            'total_adjustment': sum(t['quantity'] * t['price'] for t in trades),
            'deviations': deviations
        }

class IntegratedTradingSystem:
    """
    集成交易系统
    """
    
    def __init__(self, config):
        self.config = config
        self.running = False
        
        # 初始化适配器
        self.trading_adapter = TradingAdapter()
        self.risk_control = RiskControlAdapter()
        self.portfolio_manager = PortfolioManagementAdapter(config.get('initial_capital', 5000000))
        
        # 任务队列
        self.task_queue = Queue()
        self.result_queue = Queue()
        
        # 线程
        self.worker_thread = None
        
    def initialize(self):
        """
        初始化系统
        """
        # 连接交易系统
        if not self.trading_adapter.connect():
            raise Exception("无法连接到交易系统")
        
        # 启动工作线程
        self.worker_thread = threading.Thread(target=self.process_tasks)
        self.worker_thread.daemon = True
        self.worker_thread.start()
        
        self.running = True
        print("集成交易系统初始化完成")
    
    def place_order(self, order):
        """
        下单
        """
        # 风险检查
        if not self.risk_control.check_risk_limits(order):
            return {'success': False, 'error': 'Risk limit exceeded'}
        
        # 更新风险指标
        self.risk_control.update_risk_metrics(order)
        
        # 添加任务
        task = {
            'type': 'place_order',
            'order': order,
            'callback': self.handle_order_result
        }
        
        self.task_queue.put(task)
        
        return {'success': True, 'order_id': None}
    
    def cancel_order(self, order_id):
        """
        撤单
        """
        task = {
            'type': 'cancel_order',
            'order_id': order_id,
            'callback': self.handle_order_result
        }
        
        self.task_queue.put(task)
        
        return {'success': True}
    
    def get_order_status(self, order_id):
        """
        查询订单状态
        """
        return self.trading_adapter.get_order_status(order_id)
    
    def get_positions(self):
        """
        获取持仓
        """
        return self.trading_adapter.get_positions()
    
    def get_portfolio_status(self):
        """
        获取投资组合状态
        """
        return self.portfolio_manager.get_portfolio_status()
    
    def get_risk_report(self):
        """
        获取风险报告
        """
        return self.risk_control.get_risk_report()
    
    def check_rebalance(self):
        """
        检查再平衡
        """
        return self.portfolio_manager.check_rebalance_need()
    
    def execute_rebalance(self):
        """
        执行再平衡
        """
        return self.portfolio_manager.execute_rebalance()
    
    def process_tasks(self):
        """
        处理任务
        """
        while self.running:
            try:
                # 获取任务
                task = self.task_queue.get(timeout=1)
                
                # 处理任务
                result = None
                try:
                    if task['type'] == 'place_order':
                        result = self.trading_adapter.place_order(task['order'])
                    elif task['type'] == 'cancel_order':
                        result = self.trading_adapter.cancel_order(task['order_id'])
                    
                    # 更新投资组合
                    if task['type'] == 'place_order':
                        self.portfolio_manager.update_position(task['order'])
                
                except Exception as e:
                    result = {'success': False, 'error': str(e)}
                
                # 回调
                if 'callback' in task:
                    task['callback'](result)
                
            except Empty:
                continue
    
    def handle_order_result(self, result):
        """
        处理订单结果
        """
        self.result_queue.put(result)
    
    def get_results(self):
        """
        获取处理结果
        """
        results = []
        while not self.result_queue.empty():
            results.append(self.result_queue.get())
        return results
    
    def stop(self):
        """
        停止系统
        """
        self.running = False
        if self.worker_thread:
            self.worker_thread.join()

# 使用示例
def create_integration_example():
    """
    创建集成示例
    """
    print("\n集成示例代码：")
    print("=" * 50)
    
    # 创建配置
    config = {
        'initial_capital': 5000000,
        'trading_system': {
            'type': 'existing',
            'config': {}
        }
    }
    
    # 创建集成系统
    integrated_system = IntegratedTradingSystem(config)
    
    # 初始化
    try:
        integrated_system.initialize()
        print("集成系统初始化成功")
        
        # 示例1: 下单
        print("\n=== 示例1: 下单 ===")
        order = {
            'symbol': '沪深300ETF',
            'quantity': 100,
            'price': 300.0,
            'action': 'buy',
            'strategy': 'momentum'
        }
        
        result = integrated_system.place_order(order)
        print(f"下单结果: {result}")
        
        # 示例2: 查询投资组合
        print("\n=== 示例2: 查询投资组合 ===")
        portfolio = integrated_system.get_portfolio_status()
        print(f"投资组合价值: {portfolio['total_value']:,.0f}元")
        print("资产配置:")
        for asset, weight in portfolio['allocation'].items():
            print(f"  {asset}: {weight:.2%}")
        
        # 示例3: 查询风险报告
        print("\n=== 示例3: 查询风险报告 ===")
        risk_report = integrated_system.get_risk_report()
        print(f"风险等级: {risk_report['risk_level']}")
        print(f"当前VaR: {risk_report['current_metrics']['var']:.3f}")
        
        # 示例4: 检查再平衡
        print("\n=== 示例4: 检查再平衡 ===")
        needs_rebalance, deviations = integrated_system.check_rebalance()
        print(f"是否需要再平衡: {needs_rebalance}")
        
        if needs_rebalance:
            print("配置偏差:")
            for dev in deviations:
                print(f"  {dev['asset']}: {dev['deviation']:.2%}")
        
        # 示例5: 执行再平衡
        if needs_rebalance:
            print("\n=== 示例5: 执行再平衡 ===")
            rebalance_result = integrated_system.execute_rebalance()
            print(f"再平衡结果: {rebalance_result}")
        
        # 停止系统
        integrated_system.stop()
        print("\n集成系统已停止")
        
    except Exception as e:
        print(f"集成系统错误: {e}")

# 主程序
if __name__ == "__main__":
    print("集成适配器示例")
    print("=" * 50)
    
    # 显示示例
    create_integration_example()
    
    print("\n集成适配器示例完成")