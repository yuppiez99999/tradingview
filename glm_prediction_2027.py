# GLM模型预测2027年投资收益
# 基于2026年交易计划配置进行深度分析

class GLMPredictionModel:
    """GLM投资收益预测模型"""
    
    def __init__(self):
        # 2026年交易计划基础配置
        self.equity_portfolio = 3_000_000  # 权益组合300万
        self.low_risk_portfolio = 40_000_000  # 低风险理财4000万
        self.total_capital = 43_000_000  # 总资金4300万
        
        # 权益组合配置权重
        self.sector_weights = {
            'core_etf': 0.28,      # 核心ETF 28%
            'tech_growth': 0.20,   # 科技成长 20%
            'high_end': 0.20,      # 高端制造 20%
            'defense': 0.15,       # 防御/红利 15%
            'gold': 0.05,          # 黄金ETF 5%
            'cash': 0.08,          # 现金缓冲 8%
        }
        
        # 历史表现基准（基于市场数据）
        self.historical_returns = {
            'core_etf': {'mean': 0.08, 'std': 0.15, 'sharpe': 0.53},
            'tech_growth': {'mean': 0.12, 'std': 0.25, 'sharpe': 0.48},
            'high_end': {'mean': 0.10, 'std': 0.20, 'sharpe': 0.50},
            'defense': {'mean': 0.06, 'std': 0.12, 'sharpe': 0.50},
            'gold': {'mean': 0.05, 'std': 0.18, 'sharpe': 0.28},
            'cash': {'mean': 0.015, 'std': 0.01, 'sharpe': 1.50},
        }
        
        # 经济情景概率
        self.scenario_probabilities = {
            'recession': 0.15,      # 经济衰退
            'slow_growth': 0.35,   # 缓慢增长
            'normal': 0.35,        # 正常增长
            'fast_growth': 0.15,   # 快速增长
        }
        
        # 政策环境因子
        self.policy_factors = {
            'monetary': '宽松',    # 货币政策
            'fiscal': '积极',      # 财政政策
            'industry': '支持',    # 产业政策
            'regulatory': '正常',  # 监管环境
        }
    
    def calculate_portfolio_metrics(self):
        """计算组合核心指标"""
        portfolio_return = 0
        portfolio_risk = 0
        portfolio_weights = []
        
        for sector, weight in self.sector_weights.items():
            if sector != 'cash':  # 现金单独计算
                sector_return = self.historical_returns[sector]['mean']
                sector_risk = self.historical_returns[sector]['std']
                
                portfolio_return += weight * sector_return
                portfolio_risk += weight * sector_risk
                portfolio_weights.append({
                    'sector': sector,
                    'weight': weight,
                    'expected_return': sector_return,
                    'risk': sector_risk
                })
        
        # 添加现金收益
        portfolio_return += self.sector_weights['cash'] * self.historical_returns['cash']['mean']
        
        return {
            'expected_return': portfolio_return,
            'expected_risk': portfolio_risk,
            'sharpe_ratio': portfolio_return / portfolio_risk,
            'sector_weights': portfolio_weights
        }
    
    def analyze_economic_scenarios(self, base_return):
        """分析不同经济情景下的收益"""
        scenarios = {}
        
        for scenario, prob in self.scenario_probabilities.items():
            # 根据经济环境调整收益
            if scenario == 'recession':
                adjusted_return = base_return * 0.6  # 衰退期收益下降40%
                max_drawdown = -0.25
            elif scenario == 'slow_growth':
                adjusted_return = base_return * 0.8  # 缓慢增长期收益下降20%
                max_drawdown = -0.15
            elif scenario == 'normal':
                adjusted_return = base_return * 1.0  # 正常增长期基准
                max_drawdown = -0.12
            elif scenario == 'fast_growth':
                adjusted_return = base_return * 1.3  # 快速增长期收益提升30%
                max_drawdown = -0.10
            
            scenarios[scenario] = {
                'probability': prob,
                'annual_return': adjusted_return,
                'max_drawdown': max_drawdown,
                '2027_return': adjusted_return,
                '2027_portfolio_value': self.equity_portfolio * (1 + adjusted_return)
            }
        
        return scenarios
    
    def calculate_yearly_projection(self, base_return, years=5):
        """计算逐年收益预测"""
        projection = []
        current_value = self.equity_portfolio
        
        for year in range(1, years + 1):
            # 加入逐年衰减因子（避免过度乐观）
            decay_factor = 1 - (year - 1) * 0.02  # 每年衰减2%
            yearly_return = base_return * decay_factor
            
            current_value = current_value * (1 + yearly_return)
            
            projection.append({
                'year': 2026 + year,
                'portfolio_value': current_value,
                'annual_return': yearly_return,
                'cumulative_return': (current_value - self.equity_portfolio) / self.equity_portfolio
            })
        
        return projection
    
    def assess_risk_adjusted_returns(self, scenarios):
        """评估风险调整后收益"""
        # 计算期望收益
        expected_return = sum(scen['probability'] * scen['2027_return'] 
                           for scen in scenarios.values())
        
        # 计算风险指标
        risk_metrics = {
            'expected_return': expected_return,
            'worst_case': min(scen['2027_return'] for scen in scenarios.values()),
            'best_case': max(scen['2027_return'] for scen in scenarios.values()),
            'prob_positive_return': sum(1 for scen in scenarios.values() 
                                      if scen['2027_return'] > 0) / len(scenarios),
            'max_drawdown': max(abs(scen['max_drawdown']) for scen in scenarios.values())
        }
        
        return risk_metrics
    
    def generate_ai_insights(self, analysis_result):
        """生成AI投资见解"""
        insights = []
        
        # 基于配置结构的分析
        portfolio_metrics = analysis_result['portfolio_metrics']
        scenarios = analysis_result['scenarios']
        risk_metrics = analysis_result['risk_metrics']
        
        # 优势分析
        if portfolio_metrics['sharpe_ratio'] > 0.5:
            insights.append("✓ 投资组合夏普比率良好(>0.5)，风险收益比合理")
        
        # 风险提示
        if risk_metrics['max_drawdown'] > 0.20:
            insights.append("⚠ 最大回撤风险较高，建议加强防守配置")
        
        # 配置建议
        tech_weight = next((w['weight'] for w in portfolio_metrics['sector_weights'] 
                          if w['sector'] == 'tech_growth'), 0)
        if tech_weight > 0.25:
            insights.append("⚠ 科技成长板块权重偏高，注意波动风险")
        
        # 市场判断
        if risk_metrics['expected_return'] > 0.10:
            insights.append("📈 AI判断：2027年市场环境偏向积极，权益配置合理")
        else:
            insights.append("📊 AI判断：2027年市场存在不确定性，建议谨慎配置")
        
        # 时间维度建议
        current_month = 7  # 假设当前是7月
        if current_month >= 10:
            insights.append("🎯 建议逐步增加防守配置，为次年布局做准备")
        else:
            insights.append("🚀 建议维持当前配置，捕捉年内机会")
        
        return insights
    
    def run_prediction(self):
        """运行完整预测"""
        print("GLM模型预测2027年投资收益")
        print("=" * 60)
        
        # 1. 计算组合指标
        portfolio_metrics = self.calculate_portfolio_metrics()
        print(f"组合预期年化收益: {portfolio_metrics['expected_return']:.2%}")
        print(f"组合预期风险: {portfolio_metrics['expected_risk']:.2%}")
        print(f"组合夏普比率: {portfolio_metrics['sharpe_ratio']:.3f}")
        print()
        
        # 2. 分析经济情景
        scenarios = self.analyze_economic_scenarios(portfolio_metrics['expected_return'])
        print("经济情景分析:")
        for scenario, data in scenarios.items():
            print(f"  {scenario}: {data['probability']:.0%}概率, "
                  f"收益{data['annual_return']:.2%}, "
                  f"组合价值¥{data['2027_portfolio_value']:,.0f}")
        print()
        
        # 3. 风险调整收益
        risk_metrics = self.assess_risk_adjusted_returns(scenarios)
        print("风险调整收益:")
        print(f"  期望收益: {risk_metrics['expected_return']:.2%}")
        print(f"  最坏情况: {risk_metrics['worst_case']:.2%}")
        print(f"  最好情况: {risk_metrics['best_case']:.2%}")
        print(f"  正收益概率: {risk_metrics['prob_positive_return']:.0%}")
        print(f"  最大回撤: {risk_metrics['max_drawdown']:.2%}")
        print()
        
        # 4. 逐年预测
        yearly_projection = self.calculate_yearly_projection(
            risk_metrics['expected_return'], years=5)
        print("逐年收益预测:")
        for year_data in yearly_projection:
            print(f"  {year_data['year']}年: ¥{year_data['portfolio_value']:,.0f} "
                  f"({year_data['cumulative_return']:.2%})")
        print()
        
        # 5. AI分析见解
        analysis_result = {
            'portfolio_metrics': portfolio_metrics,
            'scenarios': scenarios,
            'risk_metrics': risk_metrics
        }
        
        insights = self.generate_ai_insights(analysis_result)
        print("AI投资见解:")
        for insight in insights:
            print(f"  {insight}")
        print()
        
        # 6. 生成预测摘要
        print("预测摘要:")
        print("-" * 40)
        expected_value = risk_metrics['expected_return']
        expected_portfolio = self.equity_portfolio * (1 + expected_value)
        expected_profit = expected_portfolio - self.equity_portfolio
        
        print(f"2027年预期组合价值: ¥{expected_portfolio:,.0f}")
        print(f"预期收益: ¥{expected_profit:,.0f} ({expected_value:.2%})")
        print(f"达成8%目标概率: {'✓' if expected_value >= 0.08 else '✗'}")
        print(f"回撤控制: {'✓' if risk_metrics['max_drawdown'] <= 0.15 else '✗'}")
        
        return {
            'expected_portfolio_value': expected_portfolio,
            'expected_profit': expected_profit,
            'expected_return_rate': expected_value,
            'scenarios': scenarios,
            'risk_metrics': risk_metrics,
            'yearly_projection': yearly_projection,
            'insights': insights
        }

def main():
    """主函数"""
    model = GLMPredictionModel()
    result = model.run_prediction()
    
    # 保存预测结果
    result_file = 'glm_prediction_result.txt'
    with open(result_file, 'w', encoding='utf-8') as f:
        f.write("GLM模型2027年投资收益预测\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"2027年预期组合价值: ¥{result['expected_portfolio_value']:,.0f}\n")
        f.write(f"预期收益: ¥{result['expected_profit']:,.0f}\n")
        f.write(f"预期收益率: {result['expected_return_rate']:.2%}\n\n")
        
        f.write("经济情景分析:\n")
        for scenario, data in result['scenarios'].items():
            f.write(f"{scenario}: {data['probability']:.0%}概率, "
                   f"收益{data['annual_return']:.2%}, "
                   f"组合价值¥{data['2027_portfolio_value']:,.0f}\n")
        
        f.write(f"\n逐年预测:\n")
        for year_data in result['yearly_projection']:
            f.write(f"{year_data['year']}年: ¥{year_data['portfolio_value']:,.0f} "
                   f"({year_data['cumulative_return']:.2%})\n")
        
        f.write(f"\nAI见解:\n")
        for insight in result['insights']:
            f.write(f"{insight}\n")
    
    print(f"\n详细预测结果已保存至: {result_file}")

if __name__ == "__main__":
    main()