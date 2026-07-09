"""
极端风险场景测试
测试多层次风险控制系统在极端情况下的表现
"""

import numpy as np
import pandas as pd
from risk_control_system import MultiLevelRiskControlSystem, RiskType, RiskLevel

def create_extreme_risk_data():
    """创建极端风险测试数据"""
    return {
        'market': {
            'returns': [-0.15, -0.20, -0.25, -0.18, -0.22, -0.30, -0.12, -0.28, -0.16, -0.24],
            'prices': [100, 85, 68, 56, 46, 32, 28, 20, 17, 13],
            'vix': 80.0,  # VIX极高，市场恐慌
            'correlation': 0.98  # 几乎完全相关
        },
        'stock': {
            'position_size': 0.5,  # 单一股票仓位过大
            'beta': 3.5,  # 极高的Beta
            'pe_ratio': 150,  # 极高的PE
            'pb_ratio': 25,  # 极高的PB
            'dividend_yield': 0.001,  # 几乎没有股息
            'volume': 100000,  # 极低的交易量
            'market_cap': 1000000000  # 很小的市值
        },
        'portfolio': {
            'positions': {'STOCK_A': 0.6, 'STOCK_B': 0.3, 'STOCK_C': 0.1},  # 极度集中
            'correlation_matrix': np.array([
                [1.0, 0.99, 0.98],
                [0.99, 1.0, 0.97],
                [0.98, 0.97, 1.0]
            ]),  # 极高的相关性
            'sector_allocation': {'Technology': 0.9, 'Healthcare': 0.1}  # 极端行业集中
        },
        'operational': {
            'trades_per_day': 300,  # 极高的交易频率
            'avg_slippage': 0.08,  # 极高的滑点
            'system_health': 0.3,  # 系统健康度极低
            'execution_quality': 0.2  # 极差的执行质量
        },
        'emotional': {
            'fear_greed_index': 95,  # 极度贪婪或恐慌
            'herding_score': 0.95,  # 极度跟风
            'sentiment_extreme': 0.99  # 极端情绪
        }
    }

def test_extreme_risk():
    """测试极端风险场景"""
    print("开始测试极端风险场景...")
    
    # 创建风险控制系统
    risk_system = MultiLevelRiskControlSystem()
    
    # 创建极端风险数据
    extreme_data = create_extreme_risk_data()
    
    # 测试极端风险场景
    print("\n=== 极端风险场景测试 ===")
    overall_score, individual_scores = risk_system.calculate_overall_risk(extreme_data)
    print(f"整体风险分数: {overall_score:.3f}")
    print(f"风险等级: {risk_system._get_overall_risk_level(overall_score)}")
    print("各维度风险分数:")
    for risk_type, score in individual_scores.items():
        print(f"  {risk_type}: {score:.3f}")
        level = risk_system._get_overall_risk_level(score)
        print(f"    风险等级: {level}")
    
    # 测试风险告警
    print("\n=== 风险告警测试 ===")
    alerts = risk_system.generate_all_alerts(extreme_data)
    print(f"告警数量: {len(alerts)}")
    for alert in alerts:
        print(f"\n  [{alert.risk_type.value}] {alert.risk_level.value}")
        print(f"    风险分数: {alert.risk_score:.3f}")
        print(f"    描述: {alert.description}")
        print(f"    建议: {alert.suggested_action}")
        print(f"    置信度: {alert.confidence:.3f}")
        print(f"    时间戳: {alert.timestamp}")
    
    # 测试风险摘要
    print("\n=== 风险摘要测试 ===")
    summary = risk_system.get_risk_summary(extreme_data)
    print(f"整体风险分数: {summary['overall_risk_score']:.3f}")
    print(f"整体风险等级: {summary['overall_risk_level']}")
    print(f"活跃告警数量: {summary['active_alerts']}")
    print("告警按类型统计:")
    for risk_type, count in summary['alert_count_by_type'].items():
        print(f"  {risk_type}: {count}个告警")
    
    # 测试风险建议
    print("\n=== 风险建议测试 ===")
    recommendations = risk_system.get_risk_recommendations(extreme_data)
    print("风险控制建议:")
    for i, rec in enumerate(recommendations, 1):
        print(f"  {i}. {rec}")
    
    # 测试风险场景模拟
    print("\n=== 风险场景模拟测试 ===")
    scenarios = [
        {'market': {'vix': 50.0, 'correlation': 0.95}},
        {'stock': {'position_size': 0.4, 'beta': 3.0}},
        {'portfolio': {'positions': {'STOCK_A': 0.7, 'STOCK_B': 0.2}}},
        {'emotional': {'fear_greed_index': 92}},
        {'operational': {'trades_per_day': 250}}
    ]
    
    simulation_results = risk_system.simulate_risk_scenarios(extreme_data, scenarios)
    print("\n场景模拟结果:")
    for result in simulation_results:
        print(f"\n场景: {result['scenario_name']}")
        print(f"  整体风险分数: {result['overall_risk_score']:.3f}")
        print(f"  风险等级: {result['risk_level']}")
        print(f"  告警数量: {result['alert_count']}")
    
    # 测试风险趋势分析
    print("\n=== 风险趋势分析测试 ===")
    historical_data = [extreme_data] * 10
    trend_analysis = risk_system.get_risk_trend_analysis(historical_data)
    print(f"当前风险分数: {trend_analysis['current_risk_score']:.3f}")
    print(f"平均风险分数: {trend_analysis['average_risk_score']:.3f}")
    print(f"风险波动性: {trend_analysis['risk_volatility']:.3f}")
    print(f"趋势方向: {trend_analysis['trend_direction']}")
    print(f"变化率: {trend_analysis['change_rate']:.3f}")
    print(f"峰值风险: {trend_analysis['peak_risk_score']:.3f}")
    print(f"谷值风险: {trend_analysis['trough_risk_score']:.3f}")
    
    # 测试不同置信度阈值
    print("\n=== 测试不同置信度阈值 ===")
    for threshold in [0.5, 0.7, 0.9]:
        risk_system.set_confidence_threshold(threshold)
        alerts = risk_system.generate_all_alerts(extreme_data)
        print(f"置信度阈值 {threshold}: 生成 {len(alerts)} 个告警")

def test_risk_control_configurations():
    """测试风险控制配置"""
    print("\n=== 测试风险控制配置 ===")
    
    # 创建风险控制系统
    risk_system = MultiLevelRiskControlSystem()
    
    # 创建极端风险数据
    extreme_data = create_extreme_risk_data()
    
    # 测试关闭所有风险控制
    print("\n测试关闭所有风险控制...")
    for risk_type in RiskType:
        risk_system.disable_control(risk_type)
    
    overall_score, _ = risk_system.calculate_overall_risk(extreme_data)
    print(f"关闭所有风险控制后: {overall_score:.3f}")
    
    # 测试只保留市场风险控制
    print("\n测试只保留市场风险控制...")
    for risk_type in RiskType:
        risk_system.disable_control(risk_type)
    risk_system.enable_control(RiskType.MARKET)
    
    overall_score, individual_scores = risk_system.calculate_overall_risk(extreme_data)
    print(f"只保留市场风险控制: {overall_score:.3f}")
    print(f"市场风险分数: {individual_scores.get('market', 0):.3f}")
    
    # 测试自定义阈值
    print("\n测试自定义阈值...")
    risk_system.enable_control(RiskType.SINGLE_STOCK)
    risk_system.update_control_thresholds(
        RiskType.SINGLE_STOCK,
        {'low': 0.1, 'medium': 0.2, 'high': 0.3, 'critical': 0.4}
    )
    
    overall_score, individual_scores = risk_system.calculate_overall_risk(extreme_data)
    print(f"自定义阈值后: {overall_score:.3f}")
    print(f"单股票风险分数: {individual_scores.get('single_stock', 0):.3f}")

if __name__ == "__main__":
    test_extreme_risk()
    test_risk_control_configurations()
    print("\n=== 极端风险测试完成 ===")