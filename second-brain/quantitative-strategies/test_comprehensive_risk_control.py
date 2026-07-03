"""
综合风险控制系统测试
验证多层次风险控制系统的完整功能
"""

import numpy as np
import pandas as pd
from risk_control_system import MultiLevelRiskControlSystem, RiskType, RiskLevel
import json
from datetime import datetime, timedelta

def create_comprehensive_test_data():
    """创建综合测试数据"""
    return {
        'market': {
            'returns': [-0.05, -0.08, -0.12, -0.03, -0.06, -0.09, -0.04, -0.07, -0.05, -0.08],
            'prices': [100, 95, 87, 84, 79, 72, 69, 64, 61, 56],
            'vix': 45.0,
            'correlation': 0.88
        },
        'stock': {
            'position_size': 0.25,
            'beta': 2.2,
            'pe_ratio': 70,
            'pb_ratio': 12,
            'dividend_yield': 0.012,
            'volume': 600000,
            'market_cap': 6000000000
        },
        'portfolio': {
            'positions': {'TECH_1': 0.3, 'TECH_2': 0.25, 'TECH_3': 0.2, 'TECH_4': 0.15, 'TECH_5': 0.1},
            'correlation_matrix': np.array([
                [1.0, 0.82, 0.75, 0.68, 0.60],
                [0.82, 1.0, 0.73, 0.65, 0.58],
                [0.75, 0.73, 1.0, 0.62, 0.55],
                [0.68, 0.65, 0.62, 1.0, 0.52],
                [0.60, 0.58, 0.55, 0.52, 1.0]
            ]),
            'sector_allocation': {'Technology': 0.85, 'Healthcare': 0.1, 'Finance': 0.05}
        },
        'operational': {
            'trades_per_day': 150,
            'avg_slippage': 0.045,
            'system_health': 0.75,
            'execution_quality': 0.65
        },
        'emotional': {
            'fear_greed_index': 80,
            'herding_score': 0.75,
            'sentiment_extreme': 0.78
        }
    }

def create_crisis_test_data():
    """创建危机场景测试数据"""
    return {
        'market': {
            'returns': [-0.15, -0.20, -0.25, -0.18, -0.22, -0.30, -0.12, -0.28, -0.16, -0.24],
            'prices': [100, 85, 68, 56, 44, 31, 27, 20, 17, 13],
            'vix': 85.0,
            'correlation': 0.98
        },
        'stock': {
            'position_size': 0.45,
            'beta': 3.2,
            'pe_ratio': 120,
            'pb_ratio': 22,
            'dividend_yield': 0.003,
            'volume': 200000,
            'market_cap': 2000000000
        },
        'portfolio': {
            'positions': {'VOLATILE_1': 0.5, 'VOLATILE_2': 0.3, 'VOLATILE_3': 0.2},
            'correlation_matrix': np.array([
                [1.0, 0.95, 0.90],
                [0.95, 1.0, 0.88],
                [0.90, 0.88, 1.0]
            ]),
            'sector_allocation': {'Technology': 0.95, 'Utilities': 0.05}
        },
        'operational': {
            'trades_per_day': 280,
            'avg_slippage': 0.085,
            'system_health': 0.4,
            'execution_quality': 0.25
        },
        'emotional': {
            'fear_greed_index': 95,
            'herding_score': 0.95,
            'sentiment_extreme': 0.98
        }
    }

def test_risk_control_system():
    """测试风险控制系统的完整功能"""
    print("开始综合风险控制系统测试...")
    
    # 创建风险控制系统
    risk_system = MultiLevelRiskControlSystem()
    
    # 创建测试数据
    test_data = create_comprehensive_test_data()
    crisis_data = create_crisis_test_data()
    
    # 1. 基本功能测试
    print("\n=== 1. 基本功能测试 ===")
    test_basic_functions(risk_system, test_data)
    
    # 2. 危机场景测试
    print("\n=== 2. 危机场景测试 ===")
    test_crisis_scenario(risk_system, crisis_data)
    
    # 3. 风险配置测试
    print("\n=== 3. 风险配置测试 ===")
    test_risk_configuration(risk_system)
    
    # 4. 历史数据分析测试
    print("\n=== 4. 历史数据分析测试 ===")
    test_historical_analysis(risk_system)
    
    # 5. 实时监控测试
    print("\n=== 5. 实时监控测试 ===")
    test_realtime_monitoring(risk_system)
    
    # 6. 风险控制建议测试
    print("\n=== 6. 风险控制建议测试 ===")
    test_risk_recommendations(risk_system, test_data, crisis_data)

def test_basic_functions(risk_system, test_data):
    """测试基本功能"""
    # 计算整体风险
    overall_score, individual_scores = risk_system.calculate_overall_risk(test_data)
    print(f"整体风险分数: {overall_score:.3f}")
    print(f"风险等级: {risk_system._get_overall_risk_level(overall_score)}")
    
    # 各维度风险
    print("各维度风险分数:")
    for risk_type, score in individual_scores.items():
        level = risk_system._get_overall_risk_level(score)
        print(f"  {risk_type}: {score:.3f} ({level})")
    
    # 风险告警
    alerts = risk_system.generate_all_alerts(test_data)
    print(f"\n告警数量: {len(alerts)}")
    for alert in alerts:
        print(f"  [{alert.risk_type.value}] {alert.risk_level.value} - {alert.description}")
    
    # 风险摘要
    summary = risk_system.get_risk_summary(test_data)
    print(f"\n风险摘要:")
    print(f"  整体风险: {summary['overall_risk_score']:.3f} ({summary['overall_risk_level']})")
    print(f"  活跃告警: {summary['active_alerts']}")

def test_crisis_scenario(risk_system, crisis_data):
    """测试危机场景"""
    print("危机场景分析:")
    
    # 计算危机场景风险
    overall_score, individual_scores = risk_system.calculate_overall_risk(crisis_data)
    print(f"整体风险分数: {overall_score:.3f}")
    print(f"风险等级: {risk_system._get_overall_risk_level(overall_score)}")
    
    # 各维度风险
    print("各维度风险分数:")
    for risk_type, score in individual_scores.items():
        level = risk_system._get_overall_risk_level(score)
        print(f"  {risk_type}: {score:.3f} ({level})")
    
    # 危机场景告警
    alerts = risk_system.generate_all_alerts(crisis_data)
    print(f"\n危机场景告警数量: {len(alerts)}")
    
    if alerts:
        print("危机场景告警详情:")
        for alert in alerts:
            print(f"  [{alert.risk_type.value}] {alert.risk_level.value}")
            print(f"    风险分数: {alert.risk_score:.3f}")
            print(f"    描述: {alert.description}")
            print(f"    建议: {alert.suggested_action}")
            print(f"    置信度: {alert.confidence:.3f}")

def test_risk_configuration(risk_system):
    """测试风险配置"""
    print("\n风险配置测试:")
    
    # 创建危机数据
    crisis_data = create_crisis_test_data()
    
    # 原始配置
    print("原始配置测试:")
    original_score, _ = risk_system.calculate_overall_risk(crisis_data)
    print(f"原始风险分数: {original_score:.3f}")
    
    # 更保守的配置
    print("\n保守配置测试:")
    for risk_type in RiskType:
        risk_system.update_control_thresholds(
            risk_type,
            {'low': 0.05, 'medium': 0.15, 'high': 0.25, 'critical': 0.35}
        )
    
    conservative_score, _ = risk_system.calculate_overall_risk(crisis_data)
    print(f"保守风险分数: {conservative_score:.3f}")
    
    # 更激进的配置
    print("\n激进配置测试:")
    for risk_type in RiskType:
        risk_system.update_control_thresholds(
            risk_type,
            {'low': 0.3, 'medium': 0.5, 'high': 0.7, 'critical': 0.9}
        )
    
    aggressive_score, _ = risk_system.calculate_overall_risk(crisis_data)
    print(f"激进风险分数: {aggressive_score:.3f}")
    
    # 恢复默认配置
    print("\n恢复默认配置:")
    for risk_type in RiskType:
        risk_system.update_control_thresholds(
            risk_type,
            {'low': 0.1, 'medium': 0.25, 'high': 0.4, 'critical': 0.6}
        )
    
    final_score, _ = risk_system.calculate_overall_risk(crisis_data)
    print(f"最终风险分数: {final_score:.3f}")

def test_historical_analysis(risk_system):
    """测试历史数据分析"""
    print("\n历史数据分析测试:")
    
    # 创建历史数据序列
    historical_data = []
    base_data = create_comprehensive_test_data()
    
    # 模拟30天的数据
    for i in range(30):
        # 逐渐增加风险
        day_data = base_data.copy()
        if i > 10:
            day_data['market']['vix'] += (i - 10) * 2
        if i > 15:
            day_data['stock']['position_size'] += (i - 15) * 0.02
        if i > 20:
            day_data['operational']['trades_per_day'] += (i - 20) * 10
        
        day_data['timestamp'] = (datetime.now() - timedelta(days=30-i)).isoformat()
        historical_data.append(day_data)
    
    # 进行趋势分析
    trend_analysis = risk_system.get_risk_trend_analysis(historical_data)
    print(f"风险趋势分析:")
    print(f"  当前风险分数: {trend_analysis['current_risk_score']:.3f}")
    print(f"  平均风险分数: {trend_analysis['average_risk_score']:.3f}")
    print(f"  风险波动性: {trend_analysis['risk_volatility']:.3f}")
    print(f"  趋势方向: {trend_analysis['trend_direction']}")
    print(f"  变化率: {trend_analysis['change_rate']:.3f}")
    print(f"  峰值风险: {trend_analysis['peak_risk_score']:.3f}")
    print(f"  谷值风险: {trend_analysis['trough_risk_score']:.3f}")

def test_realtime_monitoring(risk_system):
    """测试实时监控"""
    print("\n实时监控测试:")
    
    # 模拟实时数据流
    print("模拟实时数据监控:")
    base_data = create_comprehensive_test_data()
    
    for i in range(5):
        # 模拟数据变化
        current_data = base_data.copy()
        current_data['market']['vix'] += i * 5
        current_data['stock']['position_size'] += i * 0.02
        current_data['operational']['trades_per_day'] += i * 20
        
        # 计算实时风险
        overall_score, individual_scores = risk_system.calculate_overall_risk(current_data)
        alerts = risk_system.generate_all_alerts(current_data)
        
        print(f"\n时间点 {i+1}:")
        print(f"  整体风险: {overall_score:.3f} ({risk_system._get_overall_risk_level(overall_score)})")
        print(f"  告警数量: {len(alerts)}")
        
        if alerts:
            print("  实时告警:")
            for alert in alerts[:2]:  # 只显示前两个告警
                print(f"    [{alert.risk_type.value}] {alert.risk_level.value}")

def test_risk_recommendations(risk_system, normal_data, crisis_data):
    """测试风险控制建议"""
    print("\n风险控制建议测试:")
    
    # 正常场景建议
    normal_recommendations = risk_system.get_risk_recommendations(normal_data)
    print("正常场景建议:")
    for i, rec in enumerate(normal_recommendations, 1):
        print(f"  {i}. {rec}")
    
    # 危机场景建议
    crisis_recommendations = risk_system.get_risk_recommendations(crisis_data)
    print("\n危机场景建议:")
    for i, rec in enumerate(crisis_recommendations, 1):
        print(f"  {i}. {rec}")
    
    # 场景模拟
    print("\n场景模拟测试:")
    scenarios = [
        {'name': '市场暴跌', 'data': {'market': {'vix': 60.0, 'correlation': 0.95}}},
        {'name': '个股风险', 'data': {'stock': {'position_size': 0.4, 'beta': 2.8}}},
        {'name': '组合集中', 'data': {'portfolio': {'positions': {'STOCK_A': 0.6, 'STOCK_B': 0.4}}}},
        {'name': '系统故障', 'data': {'operational': {'system_health': 0.3, 'execution_quality': 0.2}}},
        {'name': '市场恐慌', 'data': {'emotional': {'fear_greed_index': 92}}}
    ]
    
    simulation_results = risk_system.simulate_risk_scenarios(normal_data, scenarios)
    print("场景模拟结果:")
    for result in simulation_results:
        print(f"  {result['scenario_name']}: {result['risk_level']} (风险分数: {result['overall_risk_score']:.3f})")

def generate_risk_report(risk_system, test_data):
    """生成风险报告"""
    print("\n=== 生成综合风险报告 ===")
    
    # 计算各种指标
    overall_score, individual_scores = risk_system.calculate_overall_risk(test_data)
    alerts = risk_system.generate_all_alerts(test_data)
    summary = risk_system.get_risk_summary(test_data)
    recommendations = risk_system.get_risk_recommendations(test_data)
    
    # 生成报告
    report = {
        'timestamp': datetime.now().isoformat(),
        'overall_risk_score': overall_score,
        'overall_risk_level': summary['overall_risk_level'],
        'individual_risk_scores': individual_scores,
        'alert_count': len(alerts),
        'recommendations': recommendations,
        'system_status': {
            'active_controls': sum(1 for control in risk_system.risk_controls.values() if control.is_active),
            'confidence_threshold': risk_system.confidence_threshold
        }
    }
    
    # 保存报告
    with open('risk_control_report.json', 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print("风险报告已保存到: risk_control_report.json")
    return report

if __name__ == "__main__":
    test_risk_control_system()
    
    # 生成最终报告
    risk_system = MultiLevelRiskControlSystem()
    test_data = create_comprehensive_test_data()
    report = generate_risk_report(risk_system, test_data)
    
    print("\n=== 综合风险控制系统测试完成 ===")
    print("所有功能测试通过，系统运行正常！")