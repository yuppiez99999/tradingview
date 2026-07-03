# 2026年交易计划增强版 - 整合模拟交易系统
# 基于4300万总资金的权益组合 + 风险平价策略

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

# 可选可视化依赖
try:
    import matplotlib.pyplot as plt
    import seaborn as sns
    # 设置中文显示
    plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei']
    plt.rcParams['axes.unicode_minus'] = False
    _HAS_VIZ = True
except ImportError:
    _HAS_VIZ = False

@dataclass
class TradingPlanConfig:
    """2026年交易计划配置"""
    # 基础配置
    total_capital: float = 43_000_000  # 总资金4300万
    equity_portfolio: float = 3_000_000  # 权益组合300万
    low_risk_portfolio: float = 40_000_000  # 低风险理财4000万
    
    # 权益组合配置（300万）
    core_etf_allocation: Dict[str, Dict] = field(default_factory=lambda: {
        '510300': {'name': '沪深300ETF华泰柏瑞', 'weight': 0.08, 'amount': 240_000, 'stop_loss': -0.08},
        '510500': {'name': '中证500ETF南方', 'weight': 0.06, 'amount': 180_000, 'stop_loss': -0.08},
        '512100': {'name': '中证1000ETF南方', 'weight': 0.05, 'amount': 150_000, 'stop_loss': -0.10},
        '588000': {'name': '科创50ETF华夏', 'weight': 0.05, 'amount': 150_000, 'stop_loss': -0.12},
        '159915': {'name': '创业板ETF易方达', 'weight': 0.04, 'amount': 120_000, 'stop_loss': -0.12},
    })
    
    tech_growth_allocation: Dict[str, Dict] = field(default_factory=lambda: {
        '688041': {'name': '海光信息', 'weight': 0.03, 'amount': 90_000, 'stop_loss': -0.10},
        '300308': {'name': '中际旭创', 'weight': 0.03, 'amount': 90_000, 'stop_loss': -0.12},
        '300274': {'name': '阳光电源', 'weight': 0.04, 'amount': 120_000, 'stop_loss': -0.12},
        '002371': {'name': '北方华创', 'weight': 0.03, 'amount': 90_000, 'stop_loss': -0.12},
        '688017': {'name': '绿的谐波', 'weight': 0.03, 'amount': 90_000, 'stop_loss': -0.15},
        '600276': {'name': '恒瑞医药', 'weight': 0.04, 'amount': 120_000, 'stop_loss': -0.10},
    })
    
    high_end_allocation: Dict[str, Dict] = field(default_factory=lambda: {
        '600089': {'name': '特变电工', 'weight': 0.05, 'amount': 150_000, 'stop_loss': -0.10},
        '600875': {'name': '东方电气', 'weight': 0.04, 'amount': 120_000, 'stop_loss': -0.10},
        '000425': {'name': '徐工机械', 'weight': 0.04, 'amount': 120_000, 'stop_loss': -0.10},
        '600406': {'name': '国电南瑞', 'weight': 0.04, 'amount': 120_000, 'stop_loss': -0.10},
        '600989': {'name': '宝丰能源', 'weight': 0.03, 'amount': 90_000, 'stop_loss': -0.12},
    })
    
    defense_allocation: Dict[str, Dict] = field(default_factory=lambda: {
        '515180': {'name': '易方达中证红利ETF', 'weight': 0.06, 'amount': 180_000, 'stop_loss': -0.08},
        '600036': {'name': '招商银行', 'weight': 0.04, 'amount': 120_000, 'stop_loss': -0.08},
        '600900': {'name': '长江电力', 'weight': 0.03, 'amount': 90_000, 'stop_loss': -0.08},
        '601088': {'name': '中国神华', 'weight': 0.02, 'amount': 60_000, 'stop_loss': -0.08},
    })
    
    gold_allocation: Dict[str, Dict] = field(default_factory=lambda: {
        '518880': {'name': '黄金ETF华安', 'weight': 0.05, 'amount': 150_000, 'stop_loss': -0.12},
    })
    
    cash_buffer: float = 240_000  # 现金缓冲24万
    
    # 建仓时间表
    schedule: Dict[str, Dict] = field(default_factory=lambda: {
        '2026-06-22': {'phase': 'Day 1', 'stocks': ['510300', '510500', '512100', '515180', '600036', '600900', '601088'], 'amount': ~960_000},
        '2026-06-23': {'phase': 'Day 2', 'stocks': ['688041', '300308', '300274', '002371'], 'amount': ~450_000},
        '2026-06-24': {'phase': 'Day 3', 'stocks': ['688017', '600276', '600089', '600875'], 'amount': ~390_000},
        '2026-06-25': {'phase': 'Day 4', 'stocks': ['000425', '600406', '600989', '588000', '159915'], 'amount': ~480_000},
        '2026-06-26': {'phase': 'Day 5', 'stocks': ['518880'], 'amount': ~480_000},
        '2026-06-29': {'phase': 'Day 6', 'stocks': ['rebalance'], 'amount': 0},
    })

class RiskControlSystem:
    """三级风控系统"""
    
    def __init__(self):
        self.stop_loss_rules = {
            'ETF': -0.08,
            'TECH_GROWTH': -0.12,
            'HIGH_END': -0.10,
            'DEFENSE': -0.08,
            'GOLD': -0.12,
        }
    
    def check_position_stop_loss(self, stock_code: str, current_price: float, 
                               buy_price: float, position_type: str) -> Optional[bool]:
        """检查是否触发止损"""
        price_change = (current_price - buy_price) / buy_price
        stop_loss_threshold = self.stop_loss_rules.get(position_type, -0.10)
        
        if price_change <= stop_loss_threshold:
            return True
        return None
    
    def check_portfolio_drawdown(self, current_nav: float, peak_nav: float) -> float:
        """检查组合回撤"""
        if peak_nav == 0:
            return 0
        return (current_nav - peak_nav) / peak_nav
    
    def get_portfolio_risk_level(self, drawdown: float) -> str:
        """获取风险等级"""
        if drawdown <= -0.02:
            return "LOW"
        elif drawdown <= -0.05:
            return "MEDIUM"
        elif drawdown <= -0.08:
            return "HIGH"
        else:
            return "CRITICAL"

class EnhancedTradingSimulator:
    """增强版交易模拟器 - 整合2026年交易计划"""
    
    def __init__(self, config: TradingPlanConfig):
        self.config = config
        self.risk_control = RiskControlSystem()
        self.portfolio = {}
        self.cash = config.cash_buffer
        self.trading_history = []
        self.daily_records = []
        self.current_date = None
        self.peak_nav = 1.0
        self.start_nav = 1.0
        self.monthly_targets = {
            '6': {'nav_range': (0.98, 1.02), 'max_drawdown': 0.02, 'cash_target': 240_000},
            '7': {'nav_range': (0.98, 1.05), 'max_drawdown': 0.03, 'cash_target': 200_000},
            '8': {'nav_range': (1.00, 1.08), 'max_drawdown': 0.05, 'cash_target': 150_000},
            '9': {'nav_range': (1.03, 1.12), 'max_drawdown': 0.06, 'cash_target': 150_000},
            '10': {'nav_range': (1.05, 1.18), 'max_drawdown': 0.08, 'cash_target': 100_000},
            '11': {'nav_range': (1.06, 1.22), 'max_drawdown': 0.06, 'cash_target': 200_000},
            '12': {'nav_range': (1.08, 1.25), 'max_drawdown': 0.15, 'cash_target': 600_000},
        }
    
    def get_stock_category(self, stock_code: str) -> str:
        """获取股票分类"""
        if stock_code in self.config.core_etf_allocation:
            return 'ETF'
        elif stock_code in self.config.tech_growth_allocation:
            return 'TECH_GROWTH'
        elif stock_code in self.config.high_end_allocation:
            return 'HIGH_END'
        elif stock_code in self.config.defense_allocation:
            return 'DEFENSE'
        elif stock_code in self.config.gold_allocation:
            return 'GOLD'
        else:
            return 'OTHER'
    
    def check_portfolio_constraints(self) -> Dict[str, bool]:
        """检查组合约束"""
        total_value = sum(self.portfolio.values()) if self.portfolio else 0
        equity_value = total_value
        
        constraints = {
            'single_stock_max': True,  # 单只标的不超过10%
            'etf_max': True,  # ETF合计不超过40%
            'tech_sector_max': True,  # 科技板块不超过35%
            'cash_min': self.cash >= 240_000,  # 现金不低于24万
        }
        
        # 检查单只股票上限
        if self.portfolio:
            max_position = max(self.portfolio.values())
            if max_position > 300_000:  # 30万 = 总权益的10%
                constraints['single_stock_max'] = False
        
        # 检查ETF合计上限
        etf_value = sum(pos for code, pos in self.portfolio.items() 
                       if code in self.config.core_etf_allocation)
        if etf_value > 1_200_000:  # 120万 = 总权益的40%
            constraints['etf_max'] = False
        
        # 检查科技板块上限
        tech_value = sum(pos for code, pos in self.portfolio.items() 
                        if code in self.config.tech_growth_allocation)
        if tech_value > 1_050_000:  # 105万 = 总权益的35%
            constraints['tech_sector_max'] = False
        
        return constraints
    
    def execute_build_schedule(self, date: str) -> List[Dict]:
        """执行建仓计划"""
        schedule_date = self.config.schedule.get(date)
        if not schedule_date:
            return []
        
        executed_trades = []
        
        for stock_code in schedule_date['stocks']:
            if stock_code == 'rebalance':
                # 执行再平衡
                rebalance_result = self.execute_rebalance()
                executed_trades.extend(rebalance_result)
                continue
            
            # 获取目标配置
            stock_info = None
            allocation_dict = None
            
            # 查找股票在哪个配置中
            for config_dict in [self.config.core_etf_allocation,
                              self.config.tech_growth_allocation,
                              self.config.high_end_allocation,
                              self.config.defense_allocation,
                              self.config.gold_allocation]:
                if stock_code in config_dict:
                    stock_info = config_dict[stock_code]
                    allocation_dict = config_dict
                    break
            
            if not stock_info:
                continue
            
            # 建仓规则：首日建仓50%，确认趋势后补满
            current_position = self.portfolio.get(stock_code, 0)
            target_amount = stock_info['amount']
            
            if current_position == 0:  # 新建仓
                # 首日建仓50%
                trade_amount = target_amount * 0.5
                if self.cash >= trade_amount:
                    self.portfolio[stock_code] = trade_amount
                    self.cash -= trade_amount
                    executed_trades.append({
                        'date': date,
                        'stock_code': stock_code,
                        'stock_name': stock_info['name'],
                        'trade_type': 'BUY',
                        'amount': trade_amount,
                        'price': trade_amount / 1000,  # 假设1000股
                        'trade_value': trade_amount,
                        'reason': '建仓-首日50%'
                    })
            else:
                # 补仓到目标金额
                remaining_amount = target_amount - current_position
                if remaining_amount > 0 and self.cash >= remaining_amount:
                    self.portfolio[stock_code] += remaining_amount
                    self.cash -= remaining_amount
                    executed_trades.append({
                        'date': date,
                        'stock_code': stock_code,
                        'stock_name': stock_info['name'],
                        'trade_type': 'BUY',
                        'amount': remaining_amount,
                        'price': remaining_amount / 1000,
                        'trade_value': remaining_amount,
                        'reason': '补仓-完成目标'
                    })
        
        return executed_trades
    
    def execute_rebalance(self) -> List[Dict]:
        """执行再平衡"""
        executed_trades = []
        
        # 获取所有配置的股票
        all_configurations = {
            **self.config.core_etf_allocation,
            **self.config.tech_growth_allocation,
            **self.config.high_end_allocation,
            **self.config.defense_allocation,
            **self.config.gold_allocation,
        }
        
        total_equity = sum(self.portfolio.values()) if self.portfolio else 0
        
        for stock_code, stock_info in all_configurations.items():
            target_amount = stock_info['amount']
            current_amount = self.portfolio.get(stock_code, 0)
            
            deviation = (current_amount - target_amount) / target_amount if target_amount > 0 else 0
            
            if abs(deviation) > 0.05:  # 偏离超过5%
                if current_amount > target_amount:  # 超配，卖出
                    sell_amount = current_amount - target_amount
                    self.portfolio[stock_code] = target_amount
                    self.cash += sell_amount
                    executed_trades.append({
                        'date': self.current_date,
                        'stock_code': stock_code,
                        'stock_name': stock_info['name'],
                        'trade_type': 'SELL',
                        'amount': sell_amount,
                        'price': sell_amount / 1000,
                        'trade_value': sell_amount,
                        'reason': '再平衡-卖出超配'
                    })
                elif current_amount < target_amount:  # 低配，买入
                    buy_amount = target_amount - current_amount
                    if self.cash >= buy_amount:
                        self.portfolio[stock_code] = target_amount
                        self.cash -= buy_amount
                        executed_trades.append({
                            'date': self.current_date,
                            'stock_code': stock_code,
                            'stock_name': stock_info['name'],
                            'trade_type': 'BUY',
                            'amount': buy_amount,
                            'price': buy_amount / 1000,
                            'trade_value': buy_amount,
                            'reason': '再平衡-买入低配'
                        })
        
        return executed_trades
    
    def stop_loss_check(self, stock_prices: Dict[str, float]) -> List[Dict]:
        """执行止损检查"""
        triggered_stops = []
        
        for stock_code, position_amount in list(self.portfolio.items()):
            if stock_code in stock_prices:
                buy_price = self.get_average_buy_price(stock_code)
                if buy_price > 0:
                    current_price = stock_prices[stock_code]
                    position_type = self.get_stock_category(stock_code)
                    
                    should_stop = self.risk_control.check_position_stop_loss(
                        stock_code, current_price, buy_price, position_type
                    )
                    
                    if should_stop:
                        # 执行止损
                        sell_amount = position_amount
                        self.cash += sell_amount
                        del self.portfolio[stock_code]
                        
                        triggered_stops.append({
                            'date': self.current_date,
                            'stock_code': stock_code,
                            'stock_name': self.get_stock_name(stock_code),
                            'trade_type': 'SELL',
                            'amount': sell_amount,
                            'price': current_price,
                            'trade_value': sell_amount,
                            'reason': '止损触发'
                        })
        
        return triggered_stops
    
    def get_average_buy_price(self, stock_code: str) -> float:
        """获取平均买入价格（简化版）"""
        # 在实际系统中，这里应该根据历史交易记录计算
        # 这里使用一个简化的估算方法
        return 100.0  # 假设平均买入价100元
    
    def get_stock_name(self, stock_code: str) -> str:
        """获取股票名称"""
        for config_dict in [self.config.core_etf_allocation,
                          self.config.tech_growth_allocation,
                          self.config.high_end_allocation,
                          self.config.defense_allocation,
                          self.config.gold_allocation]:
            if stock_code in config_dict:
                return config_dict[stock_code]['name']
        return stock_code
    
    def calculate_nav(self) -> float:
        """计算组合净值"""
        total_value = sum(self.portfolio.values()) if self.portfolio else 0
        total_equity = total_value + self.cash
        return total_value / 3_000_000  # 基于权益组合计算
    
    def get_monthly_target(self, month: str) -> Dict:
        """获取月度目标"""
        return self.monthly_targets.get(month, {})
    
    def record_daily_performance(self):
        """记录每日表现"""
        nav = self.calculate_nav()
        drawdown = self.risk_control.check_portfolio_drawdown(nav, self.peak_nav)
        risk_level = self.risk_control.get_portfolio_risk_level(drawdown)
        
        # 检查月度目标
        month = self.current_date.split('-')[1] if self.current_date else '6'
        monthly_target = self.get_monthly_target(month)
        
        daily_record = {
            'date': self.current_date,
            'nav': nav,
            'drawdown': drawdown,
            'risk_level': risk_level,
            'cash': self.cash,
            'total_equity': sum(self.portfolio.values()) + self.cash,
            'portfolio_value': sum(self.portfolio.values()),
            'monthly_target': monthly_target,
            'constraint_check': self.check_portfolio_constraints(),
            'position_count': len(self.portfolio),
        }
        
        self.daily_records.append(daily_record)
        
        # 更新峰值净值
        if nav > self.peak_nav:
            self.peak_nav = nav
    
    def generate_performance_report(self) -> Dict:
        """生成性能报告"""
        if not self.daily_records:
            return {}
        
        df = pd.DataFrame(self.daily_records)
        
        # 计算关键指标
        annual_return = (df['nav'].iloc[-1] / df['nav'].iloc[0]) ** (252 / len(df)) - 1
        max_drawdown = df['drawdown'].min()
        sharpe_ratio = self.calculate_sharpe_ratio(df['nav'])
        win_rate = (df['nav'].pct_change() > 0).mean()
        
        # 月度表现分析
        monthly_performance = self.analyze_monthly_performance()
        
        report = {
            'summary': {
                'annual_return': annual_return,
                'max_drawdown': max_drawdown,
                'sharpe_ratio': sharpe_ratio,
                'win_rate': win_rate,
                'final_nav': df['nav'].iloc[-1],
                'peak_nav': self.peak_nav,
                'trading_days': len(df),
            },
            'monthly_performance': monthly_performance,
            'final_portfolio': self.portfolio.copy(),
            'constraint_violations': self.count_constraint_violations(),
            'risk_events': self.count_risk_events(),
        }
        
        return report
    
    def calculate_sharpe_ratio(self, nav_series: pd.Series, risk_free_rate: float = 0.02) -> float:
        """计算夏普比率"""
        returns = nav_series.pct_change().dropna()
        if len(returns) == 0:
            return 0
        excess_returns = returns - risk_free_rate / 252
        return np.sqrt(252) * excess_returns.mean() / excess_returns.std()
    
    def analyze_monthly_performance(self) -> Dict:
        """分析月度表现"""
        monthly_performance = {}
        
        df = pd.DataFrame(self.daily_records)
        df['month'] = pd.to_datetime(df['date']).dt.month
        
        for month in df['month'].unique():
            month_data = df[df['month'] == month]
            monthly_nav = month_data['nav'].iloc[-1] / month_data['nav'].iloc[0] - 1
            
            monthly_performance[f'month_{month}'] = {
                'return': monthly_nav,
                'max_drawdown': month_data['drawdown'].min(),
                'days': len(month_data),
                'final_cash': month_data['cash'].iloc[-1],
            }
        
        return monthly_performance
    
    def count_constraint_violations(self) -> int:
        """计算约束违规次数"""
        violations = 0
        for record in self.daily_records:
            if not record['constraint_check']['single_stock_max']:
                violations += 1
            if not record['constraint_check']['etf_max']:
                violations += 1
            if not record['constraint_check']['tech_sector_max']:
                violations += 1
            if not record['constraint_check']['cash_min']:
                violations += 1
        return violations
    
    def count_risk_events(self) -> int:
        """计算风险事件次数"""
        risk_events = 0
        for record in self.daily_records:
            if record['drawdown'] <= -0.08:  # 高风险或 critical
                risk_events += 1
            if record['risk_level'] == 'CRITICAL':
                risk_events += 2
        return risk_events

def run_enhanced_simulation():
    """运行增强版模拟交易"""
    # 创建配置
    config = TradingPlanConfig()
    
    # 创建模拟器
    simulator = EnhancedTradingSimulator(config)
    
    # 交易日历（2026年6-12月）
    trading_days = pd.date_range('2026-06-22', '2026-12-31', freq='B')  # 工作日
    
    print("=== 2026年交易计划模拟开始 ===")
    print(f"总资金: ¥{config.total_capital:,}")
    print(f"权益组合: ¥{config.equity_portfolio:,}")
    print(f"低风险理财: ¥{config.low_risk_portfolio:,}")
    print()
    
    # 模拟交易
    for date in trading_days:
        simulator.current_date = date.strftime('%Y-%m-%d')
        
        # 检查是否为建仓日
        date_str = date.strftime('%Y-%m-%d')
        if date_str in config.schedule:
            build_trades = simulator.execute_build_schedule(date_str)
            if build_trades:
                print(f"{date_str} 建仓交易:")
                for trade in build_trades:
                    print(f"  {trade['stock_name']} {trade['trade_type']} ¥{trade['amount']:,} ({trade['reason']})")
        
        # 每月最后一个交易日执行再平衡
        if date.day >= 28 and date.weekday() < 5:  # 月末且为工作日
            rebalance_trades = simulator.execute_rebalance()
            if rebalance_trades:
                print(f"{date_str} 月末再平衡:")
                for trade in rebalance_trades:
                    print(f"  {trade['stock_name']} {trade['trade_type']} ¥{trade['amount']:,} ({trade['reason']})")
        
        # 记录每日表现
        simulator.record_daily_performance()
        
        # 每周输出一次状态
        if date.weekday() == 4:  # 周五
            nav = simulator.calculate_nav()
            cash = simulator.cash
            portfolio_value = sum(simulator.portfolio.values())
            print(f"{date_str} 周五状态: NAV={nav:.3f}, 现金=¥{cash:,.0f}, 组合价值=¥{portfolio_value:,.0f}")
    
    # 生成最终报告
    print("\n=== 模拟完成，生成性能报告 ===")
    report = simulator.generate_performance_report()
    
    if report:
        summary = report['summary']
        print(f"\n=== 性能总结 ===")
        print(f"年化收益率: {summary['annual_return']:.2%}")
        print(f"最大回撤: {summary['max_drawdown']:.2%}")
        print(f"夏普比率: {summary['sharpe_ratio']:.3f}")
        print(f"胜率: {summary['win_rate']:.2%}")
        print(f"最终净值: {summary['final_nav']:.3f}")
        print(f"交易天数: {summary['trading_days']}")
        print(f"约束违规次数: {report['constraint_violations']}")
        print(f"风险事件次数: {report['risk_events']}")
        
        # 月度表现
        print(f"\n=== 月度表现 ===")
        monthly_performance = report['monthly_performance']
        for month, perf in monthly_performance.items():
            print(f"{month}: {perf['return']:.2%}, 最大回撤: {perf['max_drawdown']:.2%}")
        
        # 最终持仓
        print(f"\n=== 最终持仓 ===")
        final_portfolio = report['final_portfolio']
        for stock_code, amount in final_portfolio.items():
            print(f"{stock_code}: ¥{amount:,.0f}")
        
        # 保存报告
        report_filename = 'enhanced_trading_report.json'
        with open(report_filename, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        print(f"\n详细报告已保存至: {report_filename}")
    
    return simulator

class EnhancedTradingPlan:
    """增强交易计划 - 为综合量化系统提供统一接口"""

    def __init__(self):
        self.config = TradingPlanConfig()
        self.simulator = EnhancedTradingSimulator(self.config)
        self.risk_control = RiskControlSystem()

    def enhanced_analysis(self) -> dict:
        """执行增强分析 - 返回综合分析结果"""
        # 执行模拟交易
        trading_days = pd.date_range('2026-06-22', '2026-12-31', freq='B')
        for date in trading_days:
            self.simulator.current_date = date.strftime('%Y-%m-%d')
            date_str = date.strftime('%Y-%m-%d')
            if date_str in self.config.schedule:
                self.simulator.execute_build_schedule(date_str)
            if date.day >= 28 and date.weekday() < 5:
                self.simulator.execute_rebalance()
            self.simulator.record_daily_performance()

        report = self.simulator.generate_performance_report()
        summary = report.get('summary', {})

        return {
            'portfolio_value': self.config.total_capital,
            'equity_allocation': self.config.equity_portfolio,
            'low_risk_allocation': self.config.low_risk_portfolio,
            'positions_count': (
                len(self.config.core_etf_allocation) +
                len(self.config.tech_growth_allocation) +
                len(self.config.high_end_allocation) +
                len(self.config.defense_allocation) +
                len(self.config.gold_allocation)
            ),
            'risk_level': 'medium',
            'max_drawdown': summary.get('max_drawdown', 0),
            'sharpe_ratio': summary.get('sharpe_ratio', 0),
            'annual_return': summary.get('annual_return', 0),
            'win_rate': summary.get('win_rate', 0),
            'final_nav': summary.get('final_nav', 0),
            'trading_days': summary.get('trading_days', 0),
            'monthly_performance': report.get('monthly_performance', {}),
            'constraint_violations': report.get('constraint_violations', 0),
            'risk_events': report.get('risk_events', 0),
            'final_positions': report.get('final_portfolio', {}),
            'auto_rebalance': True,
            'performance_enabled': True,
            'execution_summary': '2026年6-12月交易计划模拟完成'
        }


if __name__ == "__main__":
    # 运行增强版模拟
    simulator = run_enhanced_simulation()