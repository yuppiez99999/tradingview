# 简化版2026年交易计划模拟器
# 避免复杂依赖，直接基于交易计划进行模拟

import json
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

class SimpleTradingPlan:
    """简化版交易计划实现"""
    
    def __init__(self):
        # 总资金配置
        self.total_capital = 43_000_000  # 4300万
        self.equity_portfolio = 3_000_000  # 权益组合300万
        self.low_risk_portfolio = 40_000_000  # 低风险理财4000万
        self.cash_buffer = 240_000  # 现金缓冲24万
        
        # 权益组合配置
        self.portfolio_config = {
            # 核心ETF (84万，28%)
            '510300': {'name': '沪深300ETF华泰柏瑞', 'weight': 0.08, 'amount': 240_000, 'stop_loss': -0.08},
            '510500': {'name': '中证500ETF南方', 'weight': 0.06, 'amount': 180_000, 'stop_loss': -0.08},
            '512100': {'name': '中证1000ETF南方', 'weight': 0.05, 'amount': 150_000, 'stop_loss': -0.10},
            '588000': {'name': '科创50ETF华夏', 'weight': 0.05, 'amount': 150_000, 'stop_loss': -0.12},
            '159915': {'name': '创业板ETF易方达', 'weight': 0.04, 'amount': 120_000, 'stop_loss': -0.12},
            
            # 科技成长个股 (60万，20%)
            '688041': {'name': '海光信息', 'weight': 0.03, 'amount': 90_000, 'stop_loss': -0.10},
            '300308': {'name': '中际旭创', 'weight': 0.03, 'amount': 90_000, 'stop_loss': -0.12},
            '300274': {'name': '阳光电源', 'weight': 0.04, 'amount': 120_000, 'stop_loss': -0.12},
            '002371': {'name': '北方华创', 'weight': 0.03, 'amount': 90_000, 'stop_loss': -0.12},
            '688017': {'name': '绿的谐波', 'weight': 0.03, 'amount': 90_000, 'stop_loss': -0.15},
            '600276': {'name': '恒瑞医药', 'weight': 0.04, 'amount': 120_000, 'stop_loss': -0.10},
            
            # 高端制造/基建 (60万，20%)
            '600089': {'name': '特变电工', 'weight': 0.05, 'amount': 150_000, 'stop_loss': -0.10},
            '600875': {'name': '东方电气', 'weight': 0.04, 'amount': 120_000, 'stop_loss': -0.10},
            '000425': {'name': '徐工机械', 'weight': 0.04, 'amount': 120_000, 'stop_loss': -0.10},
            '600406': {'name': '国电南瑞', 'weight': 0.04, 'amount': 120_000, 'stop_loss': -0.10},
            '600989': {'name': '宝丰能源', 'weight': 0.03, 'amount': 90_000, 'stop_loss': -0.12},
            
            # 防御/红利 (45万，15%)
            '515180': {'name': '易方达中证红利ETF', 'weight': 0.06, 'amount': 180_000, 'stop_loss': -0.08},
            '600036': {'name': '招商银行', 'weight': 0.04, 'amount': 120_000, 'stop_loss': -0.08},
            '600900': {'name': '长江电力', 'weight': 0.03, 'amount': 90_000, 'stop_loss': -0.08},
            '601088': {'name': '中国神华', 'weight': 0.02, 'amount': 60_000, 'stop_loss': -0.08},
            
            # 黄金ETF (15万，5%)
            '518880': {'name': '黄金ETF华安', 'weight': 0.05, 'amount': 150_000, 'stop_loss': -0.12},
        }
        
        # 建仓时间表
        self.build_schedule = {
            '2026-06-22': {'phase': 'Day 1', 'stocks': ['510300', '510500', '512100', '515180', '600036', '600900', '601088'], 'amount': 960_000},
            '2026-06-23': {'phase': 'Day 2', 'stocks': ['688041', '300308', '300274', '002371'], 'amount': 450_000},
            '2026-06-24': {'phase': 'Day 3', 'stocks': ['688017', '600276', '600089', '600875'], 'amount': 390_000},
            '2026-06-25': {'phase': 'Day 4', 'stocks': ['000425', '600406', '600989', '588000', '159915'], 'amount': 480_000},
            '2026-06-26': {'phase': 'Day 5', 'stocks': ['518880'], 'amount': 480_000},
            '2026-06-29': {'phase': 'Day 6', 'stocks': ['rebalance'], 'amount': 0},
        }
        
        # 月度目标
        self.monthly_targets = {
            '6': {'nav_range': (0.98, 1.02), 'max_drawdown': 0.02, 'cash_target': 240_000, 'description': '建仓阶段'},
            '7': {'nav_range': (0.98, 1.05), 'max_drawdown': 0.03, 'cash_target': 200_000, 'description': '观察与微调'},
            '8': {'nav_range': (1.00, 1.08), 'max_drawdown': 0.05, 'cash_target': 150_000, 'description': 'Q2财报季应对'},
            '9': {'nav_range': (1.03, 1.12), 'max_drawdown': 0.06, 'cash_target': 150_000, 'description': '半年度深度评估'},
            '10': {'nav_range': (1.05, 1.18), 'max_drawdown': 0.08, 'cash_target': 100_000, 'description': '四季度冲锋准备'},
            '11': {'nav_range': (1.06, 1.22), 'max_drawdown': 0.06, 'cash_target': 200_000, 'description': '年终行情追击'},
            '12': {'nav_range': (1.08, 1.25), 'max_drawdown': 0.15, 'cash_target': 600_000, 'description': '年度收官'},
        }
        
        # 初始化状态
        self.reset_simulation()
    
    def reset_simulation(self):
        """重置模拟状态"""
        self.current_cash = self.cash_buffer
        self.portfolio_positions = {}
        self.trading_history = []
        self.daily_records = []
        self.current_date = None
        self.start_nav = 1.0
        self.peak_nav = 1.0
        self.build_progress = 0
        
    def get_monthly_target(self, month: str) -> Dict:
        """获取月度目标"""
        return self.monthly_targets.get(month, {})
    
    def execute_build_schedule(self, date: str) -> List[Dict]:
        """执行建仓计划"""
        schedule = self.build_schedule.get(date)
        if not schedule:
            return []
        
        executed_trades = []
        
        if schedule['stocks'] == ['rebalance']:
            # 执行再平衡
            rebalance_trades = self.execute_rebalance()
            executed_trades.extend(rebalance_trades)
        else:
            # 执行建仓
            for stock_code in schedule['stocks']:
                if stock_code in self.portfolio_config:
                    stock_info = self.portfolio_config[stock_code]
                    
                    # 检查是否已经建仓
                    current_position = self.portfolio_positions.get(stock_code, 0)
                    target_amount = stock_info['amount']
                    
                    if current_position < target_amount:
                        # 计算需要建仓的金额（简化：一次性建仓）
                        trade_amount = min(target_amount - current_position, self.current_cash)
                        
                        if trade_amount > 0:
                            self.portfolio_positions[stock_code] = current_position + trade_amount
                            self.current_cash -= trade_amount
                            
                            executed_trades.append({
                                'date': date,
                                'stock_code': stock_code,
                                'stock_name': stock_info['name'],
                                'trade_type': 'BUY',
                                'amount': trade_amount,
                                'reason': '建仓'
                            })
        
        self.build_progress += 1
        return executed_trades
    
    def execute_rebalance(self) -> List[Dict]:
        """执行再平衡"""
        executed_trades = []
        
        for stock_code, stock_info in self.portfolio_config.items():
            target_amount = stock_info['amount']
            current_amount = self.portfolio_positions.get(stock_code, 0)
            
            # 如果偏离超过5%，进行调整
            if target_amount > 0:
                deviation = (current_amount - target_amount) / target_amount
                if abs(deviation) > 0.05:
                    if current_amount > target_amount:  # 超配，卖出
                        sell_amount = current_amount - target_amount
                        self.portfolio_positions[stock_code] = target_amount
                        self.current_cash += sell_amount
                        
                        executed_trades.append({
                            'date': self.current_date,
                            'stock_code': stock_code,
                            'stock_name': stock_info['name'],
                            'trade_type': 'SELL',
                            'amount': sell_amount,
                            'reason': '再平衡-卖出超配'
                        })
                    elif current_amount < target_amount:  # 低配，买入
                        buy_amount = min(target_amount - current_amount, self.current_cash)
                        if buy_amount > 0:
                            self.portfolio_positions[stock_code] = current_amount + buy_amount
                            self.current_cash -= buy_amount
                            
                            executed_trades.append({
                                'date': self.current_date,
                                'stock_code': stock_code,
                                'stock_name': stock_info['name'],
                                'trade_type': 'BUY',
                                'amount': buy_amount,
                                'reason': '再平衡-买入低配'
                            })
        
        return executed_trades
    
    def calculate_portfolio_value(self) -> float:
        """计算组合价值"""
        portfolio_value = sum(self.portfolio_positions.values())
        return portfolio_value + self.current_cash
    
    def calculate_nav(self) -> float:
        """计算净值"""
        total_value = self.calculate_portfolio_value()
        return total_value / self.equity_portfolio  # 基于权益组合计算
    
    def check_constraints(self) -> Dict[str, bool]:
        """检查组合约束"""
        portfolio_value = sum(self.portfolio_positions.values()) if self.portfolio_positions else 0
        
        # 单只标的上限（10%）
        single_stock_max = True
        for amount in self.portfolio_positions.values():
            if amount > 300_000:  # 30万 = 300万的10%
                single_stock_max = False
                break
        
        # ETF合计上限（40%）
        etf_total = sum(self.portfolio_positions.get(code, 0) 
                        for code in ['510300', '510500', '512100', '588000', '159915'])
        etf_max = etf_total <= 1_200_000  # 120万 = 300万的40%
        
        # 科技板块上限（35%）
        tech_total = sum(self.portfolio_positions.get(code, 0) 
                        for code in ['688041', '300308', '300274', '002371', '688017', '600276'])
        tech_max = tech_total <= 1_050_000  # 105万 = 300万的35%
        
        # 现金下限
        cash_min = self.current_cash >= 240_000
        
        return {
            'single_stock_max': single_stock_max,
            'etf_max': etf_max,
            'tech_max': tech_max,
            'cash_min': cash_min,
        }
    
    def simulate_month(self, month: str, nav_data: List[float]) -> Dict:
        """模拟一个月的交易"""
        target = self.get_monthly_target(month)
        month_records = []
        
        for i, nav in enumerate(nav_data):
            date = f"2026-{month.zfill(2)}-{(i+1):02d}"
            
            # 记录日数据
            record = {
                'date': date,
                'nav': nav,
                'portfolio_value': sum(self.portfolio_positions.values()),
                'cash': self.current_cash,
                'constraints': self.check_constraints(),
            }
            
            month_records.append(record)
        
        return {
            'month': month,
            'target': target,
            'records': month_records,
            'final_nav': nav_data[-1] if nav_data else 1.0,
            'avg_nav': sum(nav_data) / len(nav_data) if nav_data else 1.0,
            'min_nav': min(nav_data) if nav_data else 1.0,
            'max_nav': max(nav_data) if nav_data else 1.0,
        }
    
    def generate_report(self, monthly_data: Dict) -> Dict:
        """生成性能报告"""
        total_return = 0
        max_drawdown = 0
        violations = 0
        
        for month, data in monthly_data.items():
            monthly_return = data['final_nav'] - 1.0
            total_return += monthly_return
            
            # 计算月度最大回撤
            nav_data = [record['nav'] for record in data['records']]
            peak = nav_data[0]
            for nav in nav_data:
                if nav > peak:
                    peak = nav
                drawdown = (nav - peak) / peak
                max_drawdown = min(max_drawdown, drawdown)
            
            # 计算约束违规
            for record in data['records']:
                if not record['constraints']['single_stock_max']:
                    violations += 1
                if not record['constraints']['etf_max']:
                    violations += 1
                if not record['constraints']['tech_max']:
                    violations += 1
                if not record['constraints']['cash_min']:
                    violations += 1
        
        # 计算年化收益率
        annual_return = (1 + total_return) ** (12/7) - 1  # 7个月数据
        
        return {
            'summary': {
                'total_return': total_return,
                'annual_return': annual_return,
                'max_drawdown': max_drawdown,
                'violations': violations,
                'final_portfolio': self.portfolio_positions,
                'final_cash': self.current_cash,
            },
            'monthly_performance': monthly_data,
        }

def generate_test_data() -> Dict[str, List[float]]:
    """生成测试数据 - 模拟市场波动"""
    test_data = {}
    
    # 基于交易计划的预期生成数据
    base_scenarios = {
        '6': [0.985, 0.990, 0.995, 1.000, 1.005, 1.010, 1.015, 1.020, 1.018, 1.022],
        '7': [1.020, 1.025, 1.030, 1.028, 1.035, 1.040, 1.045, 1.042, 1.048, 1.055],
        '8': [1.055, 1.060, 1.055, 1.050, 1.058, 1.065, 1.070, 1.068, 1.075, 1.080],
        '9': [1.080, 1.090, 1.095, 1.100, 1.105, 1.110, 1.115, 1.120, 1.118, 1.125],
        '10': [1.125, 1.135, 1.140, 1.145, 1.150, 1.155, 1.160, 1.165, 1.170, 1.180],
        '11': [1.180, 1.190, 1.195, 1.200, 1.205, 1.210, 1.215, 1.220, 1.218, 1.225],
        '12': [1.225, 1.235, 1.240, 1.245, 1.250, 1.255, 1.260, 1.265, 1.270, 1.275],
    }
    
    # 添加一些波动
    for month, base_data in base_scenarios.items():
        noisy_data = []
        for i, base_nav in enumerate(base_data):
            # 添加随机波动
            volatility = 0.02  # 2%的波动
            random_factor = 1 + (i % 3 - 1) * volatility * 0.5
            noisy_nav = base_nav * random_factor
            noisy_data.append(max(0.95, min(1.30, noisy_nav)))  # 限制在合理范围内
        
        test_data[month] = noisy_data
    
    return test_data

def main():
    """主函数 - 运行交易计划模拟"""
    print("=== 2026年交易计划模拟器 ===")
    print("总资金: 4,300万元")
    print("权益组合: 300万元")
    print("低风险理财: 4,000万元")
    print()
    
    # 创建交易计划
    plan = SimpleTradingPlan()
    
    # 生成测试数据
    test_data = generate_test_data()
    
    # 逐月模拟
    monthly_results = {}
    
    for month in ['6', '7', '8', '9', '10', '11', '12']:
        print(f"\n=== {month}月模拟 ===")
        target = plan.get_monthly_target(month)
        print(f"月度目标: {target['description']}")
        print(f"净值目标: {target['nav_range'][0]:.3f} - {target['nav_range'][1]:.3f}")
        
        # 执行建仓（仅6月）
        if month == '6':
            build_trades = plan.execute_build_schedule('2026-06-22')
            if build_trades:
                print("建仓交易:")
                for trade in build_trades:
                    print(f"  {trade['stock_name']} {trade['trade_type']} ¥{trade['amount']:,}")
        
        # 模拟该月
        monthly_result = plan.simulate_month(month, test_data[month])
        monthly_results[month] = monthly_result
        
        print(f"最终净值: {monthly_result['final_nav']:.3f}")
        print(f"平均净值: {monthly_result['avg_nav']:.3f}")
        print(f"月度收益: {(monthly_result['final_nav'] - 1.0) * 100:.2f}%")
        print(f"约束违规: {sum(1 for r in monthly_result['records'] if not r['constraints']['cash_min'])}")
        
        # 月末再平衡
        if month != '12':
            rebalance_trades = plan.execute_rebalance()
            if rebalance_trades:
                print("再平衡交易:")
                for trade in rebalance_trades:
                    print(f"  {trade['stock_name']} {trade['trade_type']} ¥{trade['amount']:,}")
    
    # 生成最终报告
    print("\n=== 最终性能报告 ===")
    report = plan.generate_report(monthly_results)
    
    summary = report['summary']
    print(f"总收益: {summary['total_return'] * 100:.2f}%")
    print(f"年化收益: {summary['annual_return'] * 100:.2f}%")
    print(f"最大回撤: {summary['max_drawdown'] * 100:.2f}%")
    print(f"约束违规次数: {summary['violations']}")
    print(f"最终现金: ¥{summary['final_cash']:,}")
    
    # 显示最终持仓
    print(f"\n=== 最终持仓 ===")
    for stock_code, amount in summary['final_portfolio'].items():
        if amount > 0:
            stock_info = plan.portfolio_config[stock_code]
            print(f"{stock_info['name']}: ¥{amount:,.0f}")
    
    # 保存报告
    report_file = 'trading_plan_report.json'
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(f"\n详细报告已保存至: {report_file}")
    
    # 达标情况
    print("\n=== 达标情况检查 ===")
    annual_return = summary['annual_return']
    max_drawdown = abs(summary['max_drawdown'])
    
    target_return = 0.08  # 8%
    target_drawdown = 0.15  # 15%
    
    return_ok = annual_return >= target_return
    drawdown_ok = max_drawdown <= target_drawdown
    
    print(f"年化收益目标: {target_return:.2%}，实际: {annual_return:.2%} - {'✓' if return_ok else '✗'}")
    print(f"最大回撤目标: {target_drawdown:.2%}，实际: {max_drawdown:.2%} - {'✓' if drawdown_ok else '✗'}")
    print(f"整体达标: {'✓' if return_ok and drawdown_ok else '✗'}")

if __name__ == "__main__":
    main()