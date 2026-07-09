"""
高风险场景测试脚本
测试多层次风险控制系统在高风险场景下的表现
"""

import numpy as np
import pandas as pd
from risk_control_system import MultiLevelRiskControlSystem, RiskType

def create_high_risk_data():
    """创建高风险测试数据"""
    return {
        'market': {
            'returns': [-0.05, -0.08, -0.12, -0.03, -0.06, -0.09, -0.04, -0.07, -0.05, -0.08],
            'prices': [100, 95, 87, 84, 81, 75, 72, 67, 64, 59],
            'vix': 45.0,  # VIX处于高位
            'correlation': 0.95  # 相关性极高
        },
        'stock': {
            'position_size': 0.35,  # 单一股票仓位过高
            'beta': 2.5,  # Beta过高
            'pe_ratio': 80,  # PE比率过高
            'pb_ratio': 15,  # PB比率过高
            'dividend_yield': 0.005,  # 股息率过低
            'volume': 500000,  # 交易量低
            'market_cap': 5000000000  # 市值较小
        },
        'portfolio': {
            'positions': {'AAPL': 0.4, 'MSFT': 0.3, 'GOOGL': 0.2, 'AMZN': 0.1},  # 集中度极高
            'correlation_matrix': np.array([
                [1.0, 0.95, 0.92, 0.88],
                [0.95, 1.0, 0.90, 0.85],
                [0.92, 0.90, 1.0, 0.87],
                [0.88, 0.85, 0.87, 1.0]
            ]),  # 相关性极高
            'sector_allocation': {'Technology': 0.8, 'Healthcare': 0.15, 'Finance': 0.05}  # 行业集中度过高
        },
        'operational': {
            'trades_per_day': 150,  # 交易频率过高
            'avg_slippage': 0.035,  # 滑点过大
            'system_health': 0.75,  # 系统健康度低
            'execution_quality': 0.65  # 执行质量低
        },
        'emotional': {
            'fear_greed_index': 90,  # 极度贪婪
            'herding_score': 0.9,  # 严重跟风
            'sentiment_extreme': 0.95  # 极端情绪
        }
    }

def create_medium_risk_data():
    """创建中等风险测试数据"""
    return {
        'market': {
            'returns': [0.02, -0.03, 0.01, -0.04, 0.02, -0.01, 0.03, -0.02, 0.01, -0.03],
            'prices': [100, 102, 99, 100, 96, 98, 101, 99, 100, 97],
            'vix': 30.0,
            'correlation': 0.75
        },
        'stock': {
            'position_size': 0.18,
            'beta': 1.8,
            'pe_ratio': 40,
            'pb_ratio': 8,
            'dividend_yield': 0.015,
            'volume': 800000,
            'market_cap': 8000000000
        },
        'portfolio': {
            'positions': {'AAPL': 0.2, 'MSFT': 0.15, 'GOOGL': 0.12, 'AMZN': 0.1, 'TSLA': 0.08, 'META': 0.07},
            'correlation_matrix': np.array([
                [1.0, 0.7, 0.6, 0.8, 0.5, 0.6],
                [0.7, 1.0, 0.5, 0.6, 0.4, 0.5],
                [0.6, 0.5, 1.0, 0.7, 0.3, 0.4],
                [0.8, 0.6, 0.7, 1.0, 0.5, 0.6],
                [0.5, 0.4, 0.3, 0.5, 1.0, 0.4],
                [0.6, 0.5, 0.4, 0.6, 0.4, 1.0]
            ]),
            'sector_allocation': {'Technology': 0.5, 'Healthcare': 0.2, 'Finance': 0.15, 'Energy': 0.15}
        },
        'operational': {
            'trades_per_day': 120,
            'avg_slippage': 0.025,
            'system_health': 0.85,
            'execution_quality': 0.75
        },
        'emotional': {
            'fear_greed_index': 60,
            'herding_score': 0.6,
            'sentiment_extreme': 0.6
        }
    }

def test_risk_scenarios():
    """测试不同风险场景"""
    print("开始测试高风险场景...")
    
    # 创建风险控制系统
    risk_system = MultiLevelRiskControlSystem()
    
    # 创建测试数据
    high_risk_data = create_high_risk_data()
    medium_risk_data = create_medium_risk_data()
    
    # 测试高风险场景
    print("\n=== 高风险场景测试 ===")
    overall_score, individual_scores = risk_system.calculate_overall_risk(high_risk_data)
    print(f"整体风险分数: {overall_score:.3f}")
    print(f"风险等级: {risk_system._get_overall_risk_level(overall_score)}")
    print("各维度风险分数:")
    for risk_type, score in individual_scores.items():
        print(f"  {risk_type}: {score:.3f}")
    
    # 测试风险告警
    alerts = risk_system.generate_all_alerts(high_risk_data)
    print(f"\n告警数量: {len(alerts)}")
    for alert in alerts:
        print(f"  {alert.risk_type.value}: {alert.risk_level.value} ({alert.risk_score:.3f})")
        print(f"    描述: {alert.description}")
        print(f"    建议: {alert.suggested_action}")
    
    # 测试中等风险场景
    print("\n=== 中等风险场景测试 ===")
    overall_score_medium, individual_scores_medium = risk_system.calculate_overall_risk(medium_risk_data)
    print(f"整体风险分数: {overall_score_medium:.3f}")
    print(f"风险等级: {risk_system._get_overall_risk_level(overall_score_medium)}")
    print("各维度风险分数:")
    for risk_type, score in individual_scores_medium.items():
        print(f"  {risk_type}: {score:.3f}")
    
    # 测试场景对比
    print("\n=== 场景对比分析 ===")
    print("高风险 vs 中等风险:")
    for risk_type in individual_scores.keys():
        diff = individual_scores[risk_type] - individual_scores_medium[risk_type]
        print(f"  {risk_type}: 高风险比中等风险高 {diff:.3f}")
    
    # 测试风险趋势分析
    print("\n=== 风险趋势分析 ===")
    historical_data = [high_risk_data, medium_risk_data, high_risk_data, medium_risk_data]
    trend_analysis = risk_system.get_risk_trend_analysis(historical_data)
    print(f"当前风险分数: {trend_analysis['current_risk_score']:.3f}")
    print(f"平均风险分数: {trend_analysis['average_risk_score']:.3f}")
    print(f"风险波动性: {trend_analysis['risk_volatility']:.3f}")
    print(f"趋势方向: {trend_analysis['trend_direction']}")
    print(f"变化率: {trend_analysis['change_rate']:.3f}")
    print(f"峰值风险: {trend_analysis['peak_risk_score']:.3f}")
    print(f"谷值风险: {trend_analysis['trough_risk_score']:.3f}")
    
    # 测试风险建议
    print("\n=== 风险建议 ===")
    high_risk_recommendations = risk_system.get_risk_recommendations(high_risk_data)
    medium_risk_recommendations = risk_system.get_risk_recommendations(medium_risk_data)
    
    print("高风险场景建议:")
    for i, rec in enumerate(high_risk_recommendations, 1):
        print(f"  {i}. {rec}")
    
    print("\n中等风险场景建议:")
    for i, rec in enumerate(medium_risk_recommendations, 1):
        print(f"  {i}. {rec}")

def test_control_thresholds():
    """测试风险控制阈值调整"""
    print("\n=== 测试风险控制阈值调整 ===")
    
    # 创建风险控制系统
    risk_system = MultiLevelRiskControlSystem()
    
    # 创建中等风险数据
    data = create_medium_risk_data()
    
    # 初始风险计算
    overall_score, _ = risk_system.calculate_overall_risk(data)
    print(f"初始风险分数: {overall_score:.3f}")
    
    # 调整市场风险阈值
    risk_system.update_control_thresholds(
        RiskType.MARKET, 
        {'medium': 0.4, 'high': 0.7, 'critical': 0.85}
    )
    
    # 重新计算风险
    overall_score, _ = risk_system.calculate_overall_risk(data)
    print(f"调整阈值后风险分数: {overall_score:.3f}")
    print(f"风险等级: {risk_system._get_overall_risk_level(overall_score)}")
    
    # 测试启用/禁用控制
    print("\n测试启用/禁用风险控制模块...")
    
    # 禁用情绪风险控制
    risk_system.disable_control(RiskType.EMOTIONAL)
    overall_score, individual_scores = risk_system.calculate_overall_risk(data)
    print(f"禁用情绪风险后整体风险分数: {overall_score:.3f}")
    
    # 启用情绪风险控制
    risk_system.enable_control(RiskType.EMOTIONAL)
    overall_score, individual_scores = risk_system.calculate_overall_risk(data)
    print(f"重新启用情绪风险后整体风险分数: {overall_score:.3f}")

if __name__ == "__main__":
    test_risk_scenarios()
    test_control_thresholds()
    print("\n=== 高风险场景测试完成 ===")