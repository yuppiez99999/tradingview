"""
敏感度风险测试
测试调整后的风险控制系统对高风险场景的识别能力
"""

import numpy as np
import pandas as pd
from risk_control_system import MultiLevelRiskControlSystem, RiskType

def create_sensitive_risk_data():
    """创建敏感风险测试数据"""
    return {
        'market': {
            'returns': [-0.08, -0.12, -0.15, -0.06, -0.18, -0.22, -0.09, -0.16, -0.11, -0.19],
            'prices': [100, 92, 81, 76, 63, 49, 45, 38, 34, 28],
            'vix': 55.0,
            'correlation': 0.92
        },
        'stock': {
            'position_size': 0.25,
            'beta': 2.8,
            'pe_ratio': 85,
            'pb_ratio': 18,
            'dividend_yield': 0.008,
            'volume': 300000,
            'market_cap': 3000000000
        },
        'portfolio': {
            'positions': {'TECH_1': 0.35, 'TECH_2': 0.25, 'TECH_3': 0.20, 'TECH_4': 0.15, 'OTHER': 0.05},
            'correlation_matrix': np.array([
                [1.0, 0.85, 0.80, 0.75, 0.60],
                [0.85, 1.0, 0.78, 0.72, 0.58],
                [0.80, 0.78, 1.0, 0.70, 0.55],
                [0.75, 0.72, 0.70, 1.0, 0.52],
                [0.60, 0.58, 0.55, 0.52, 1.0]
            ]),
            'sector_allocation': {'Technology': 0.9, 'Healthcare': 0.08, 'Finance': 0.02}
        },
        'operational': {
            'trades_per_day': 180,
            'avg_slippage': 0.055,
            'system_health': 0.65,
            'execution_quality': 0.45
        },
        'emotional': {
            'fear_greed_index': 85,
            'herding_score': 0.85,
            'sentiment_extreme': 0.88
        }
    }

def test_sensitive_risk_detection():
    """测试敏感风险检测"""
    print("开始测试敏感风险检测...")
    
    # 创建风险控制系统
    risk_system = MultiLevelRiskControlSystem()
    
    # 创建敏感风险数据
    sensitive_data = create_sensitive_risk_data()
    
    # 测试敏感风险场景
    print("\n=== 敏感风险场景测试 ===")
    overall_score, individual_scores = risk_system.calculate_overall_risk(sensitive_data)
    print(f"整体风险分数: {overall_score:.3f}")
    print(f"风险等级: {risk_system._get_overall_risk_level(overall_score)}")
    
    print("\n各维度风险分数:")
    for risk_type, score in individual_scores.items():
        level = risk_system._get_overall_risk_level(score)
        print(f"  {risk_type}: {score:.3f} ({level})")
    
    # 测试风险告警
    print("\n=== 风险告警测试 ===")
    alerts = risk_system.generate_all_alerts(sensitive_data)
    print(f"告警数量: {len(alerts)}")
    
    for alert in alerts:
        print(f"\n  [{alert.risk_type.value}] {alert.risk_level.value}")
        print(f"    风险分数: {alert.risk_score:.3f}")
        print(f"    描述: {alert.description}")
        print(f"    建议: {alert.suggested_action}")
        print(f"    置信度: {alert.confidence:.3f}")
    
    # 测试风险建议
    print("\n=== 风险建议测试 ===")
    recommendations = risk_system.get_risk_recommendations(sensitive_data)
    print("风险控制建议:")
    for i, rec in enumerate(recommendations, 1):
        print(f"  {i}. {rec}")
    
    # 测试阈值调整效果
    print("\n=== 阈值调整效果测试 ===")
    
    # 原始阈值
    print("原始阈值:")
    for risk_type, control in risk_system.risk_controls.items():
        thresholds = control.thresholds
        print(f"  {risk_type.value}: {thresholds}")
    
    # 调整为更敏感的阈值
    print("\n调整阈值后:")
    for risk_type in RiskType:
        risk_system.update_control_thresholds(
            risk_type,
            {'low': 0.1, 'medium': 0.3, 'high': 0.5, 'critical': 0.7}
        )
    
    overall_score, individual_scores = risk_system.calculate_overall_risk(sensitive_data)
    print(f"调整后整体风险分数: {overall_score:.3f}")
    print(f"调整后风险等级: {risk_system._get_overall_risk_level(overall_score)}")
    
    # 测试调整后的告警
    alerts = risk_system.generate_all_alerts(sensitive_data)
    print(f"调整后告警数量: {len(alerts)}")

def compare_risk_sensitivity():
    """比较不同敏感度设置"""
    print("\n=== 风险敏感度比较 ===")
    
    # 创建不同敏感度设置
    low_sensitivity = MultiLevelRiskControlSystem()
    medium_sensitivity = MultiLevelRiskControlSystem()
    high_sensitivity = MultiLevelRiskControlSystem()
    
    # 设置不同敏感度
    low_sensitivity.update_control_thresholds(RiskType.MARKET, {'low': 0.4, 'medium': 0.6, 'high': 0.8, 'critical': 0.9})
    medium_sensitivity.update_control_thresholds(RiskType.MARKET, {'low': 0.2, 'medium': 0.4, 'high': 0.6, 'critical': 0.8})
    high_sensitivity.update_control_thresholds(RiskType.MARKET, {'low': 0.1, 'medium': 0.2, 'high': 0.3, 'critical': 0.5})
    
    # 创建测试数据
    test_data = create_sensitive_risk_data()
    
    # 比较结果
    systems = [
        ("低敏感度", low_sensitivity),
        ("中等敏感度", medium_sensitivity),
        ("高敏感度", high_sensitivity)
    ]
    
    for name, system in systems:
        overall_score, individual_scores = system.calculate_overall_risk(test_data)
        alerts = system.generate_all_alerts(test_data)
        
        print(f"\n{name}:")
        print(f"  整体风险分数: {overall_score:.3f}")
        print(f"  风险等级: {system._get_overall_risk_level(overall_score)}")
        print(f"  告警数量: {len(alerts)}")
        
        for alert in alerts[:2]:  # 只显示前两个告警
            print(f"  告警: [{alert.risk_type.value}] {alert.risk_level.value} ({alert.risk_score:.3f})")

def test_risk_trend_comparison():
    """测试风险趋势对比"""
    print("\n=== 风险趋势对比 ===")
    
    # 创建风险控制系统
    risk_system = MultiLevelRiskControlSystem()
    
    # 创建不同严重程度的风险数据
    mild_risk = create_sensitive_risk_data()
    moderate_risk = create_sensitive_risk_data()
    severe_risk = create_sensitive_risk_data()
    
    # 调整风险级别
    # 中等风险
    moderate_risk['market']['vix'] = 60.0
    moderate_risk['stock']['position_size'] = 0.35
    moderate_risk['operational']['trades_per_day'] = 220
    moderate_risk['emotional']['fear_greed_index'] = 90
    
    # 严重风险
    severe_risk['market']['vix'] = 70.0
    severe_risk['stock']['position_size'] = 0.45
    severe_risk['operational']['trades_per_day'] = 280
    severe_risk['emotional']['fear_greed_index'] = 95
    
    # 测试不同风险级别
    risk_levels = ["轻度", "中度", "重度"]
    risk_datasets = [mild_risk, moderate_risk, severe_risk]
    
    print("风险级别对比:")
    for i, (level, data) in enumerate(zip(risk_levels, risk_datasets)):
        overall_score, individual_scores = risk_system.calculate_overall_risk(data)
        alerts = risk_system.generate_all_alerts(data)
        
        print(f"\n{level}风险:")
        print(f"  整体风险分数: {overall_score:.3f}")
        print(f"  风险等级: {risk_system._get_overall_risk_level(overall_score)}")
        print(f"  告警数量: {len(alerts)}")
        
        # 各维度风险
        print("  各维度风险:")
        for risk_type, score in individual_scores.items():
            print(f"    {risk_type}: {score:.3f}")

def test_operational_emotional_risks():
    """测试操作和情绪风险"""
    print("\n=== 操作和情绪风险专项测试 ===")
    
    # 创建风险控制系统
    risk_system = MultiLevelRiskControlSystem()
    
    # 创建高风险数据
    high_risk_data = create_sensitive_risk_data()
    
    # 极高操作风险
    high_risk_data['operational'] = {
        'trades_per_day': 250,
        'avg_slippage': 0.075,
        'system_health': 0.45,
        'execution_quality': 0.35
    }
    
    # 极高情绪风险
    high_risk_data['emotional'] = {
        'fear_greed_index': 92,
        'herding_score': 0.92,
        'sentiment_extreme': 0.95
    }
    
    # 测试操作风险
    print("\n操作风险测试:")
    operational_score = risk_system.risk_controls[RiskType.OPERATIONAL].calculate_risk_score(high_risk_data)
    print(f"操作风险分数: {operational_score:.3f}")
    print(f"操作风险等级: {risk_system.risk_controls[RiskType.OPERATIONAL].get_risk_level(operational_score)}")
    
    # 测试情绪风险
    print("\n情绪风险测试:")
    emotional_score = risk_system.risk_controls[RiskType.EMOTIONAL].calculate_risk_score(high_risk_data)
    print(f"情绪风险分数: {emotional_score:.3f}")
    print(f"情绪风险等级: {risk_system.risk_controls[RiskType.EMOTIONAL].get_risk_level(emotional_score)}")
    
    # 测试整体影响
    print("\n整体影响测试:")
    overall_score, individual_scores = risk_system.calculate_overall_risk(high_risk_data)
    print(f"整体风险分数: {overall_score:.3f}")
    print(f"整体风险等级: {risk_system._get_overall_risk_level(overall_score)}")

if __name__ == "__main__":
    test_sensitive_risk_detection()
    compare_risk_sensitivity()
    test_risk_trend_comparison()
    test_operational_emotional_risks()
    print("\n=== 敏感度风险测试完成 ===")