"""
多层次风险控制系统演示
展示风险控制系统的核心功能和实际应用
"""

import numpy as np
import pandas as pd
from risk_control_system import MultiLevelRiskControlSystem, RiskType, RiskLevel

def demo_basic_risk_assessment():
    """演示基本风险评估"""
    print("=== 基本风险评估演示 ===")
    
    # 创建风险控制系统
    risk_system = MultiLevelRiskControlSystem()
    
    # 创建正常市场数据
    normal_data = {
        'market': {
            'returns': [0.01, -0.02, 0.03, -0.01, 0.02, -0.03, 0.01, 0.02, -0.01, 0.01],
            'prices': [100, 101, 99, 102, 101, 98, 99, 101, 100, 101],
            'vix': 18.0,
            'correlation': 0.65
        },
        'stock': {
            'position_size': 0.08,
            'beta': 1.2,
            'pe_ratio': 25,
            'pb_ratio': 5,
            'dividend_yield': 0.03,
            'volume': 2000000,
            'market_cap': 20000000000
        },
        'portfolio': {
            'positions': {'AAPL': 0.08, 'MSFT': 0.07, 'GOOGL': 0.06, 'AMZN': 0.05, 'TSLA': 0.04, 'META': 0.04, 'NVDA': 0.03, 'JPM': 0.03},
            'correlation_matrix': np.array([
                [1.0, 0.6, 0.5, 0.4, 0.3, 0.4, 0.5, 0.2],
                [0.6, 1.0, 0.4, 0.5, 0.4, 0.3, 0.4, 0.3],
                [0.5, 0.4, 1.0, 0.3, 0.2, 0.5, 0.3, 0.2],
                [0.4, 0.5, 0.3, 1.0, 0.6, 0.4, 0.3, 0.2],
                [0.3, 0.4, 0.2, 0.6, 1.0, 0.3, 0.2, 0.1],
                [0.4, 0.3, 0.5, 0.4, 0.3, 1.0, 0.4, 0.2],
                [0.5, 0.4, 0.3, 0.3, 0.2, 0.4, 1.0, 0.1],
                [0.2, 0.3, 0.2, 0.2, 0.1, 0.2, 0.1, 1.0]
            ]),
            'sector_allocation': {'Technology': 0.3, 'Healthcare': 0.25, 'Finance': 0.2, 'Energy': 0.15, 'Consumer': 0.1}
        },
        'operational': {
            'trades_per_day': 50,
            'avg_slippage': 0.008,
            'system_health': 0.95,
            'execution_quality': 0.92
        },
        'emotional': {
            'fear_greed_index': 50,
            'herding_score': 0.4,
            'sentiment_extreme': 0.3
        }
    }
    
    # 计算风险评估
    overall_score, individual_scores = risk_system.calculate_overall_risk(normal_data)
    
    print(f"整体风险分数: {overall_score:.3f}")
    print(f"风险等级: {risk_system._get_overall_risk_level(overall_score)}")
    print("\n各维度风险分析:")
    for risk_type, score in individual_scores.items():
        level = risk_system._get_overall_risk_level(score)
        print(f"  {risk_type}: {score:.3f} ({level})")
    
    # 生成风险告警
    alerts = risk_system.generate_all_alerts(normal_data)
    print(f"\n风险告警数量: {len(alerts)}")
    
    if alerts:
        print("风险告警详情:")
        for alert in alerts:
            print(f"  [{alert.risk_type.value}] {alert.risk_level.value}")
            print(f"    风险分数: {alert.risk_score:.3f}")
            print(f"    建议: {alert.suggested_action}")

def demo_risk_scenarios():
    """演示不同风险场景"""
    print("\n=== 风险场景演示 ===")
    
    risk_system = MultiLevelRiskControlSystem()
    
    # 创建基础数据
    base_data = {
        'market': {
            'returns': [0.01, -0.01, 0.02, -0.02, 0.01, -0.01, 0.02, -0.02, 0.01, -0.01],
            'prices': [100, 101, 99, 101, 99, 100, 102, 100, 101, 100],
            'vix': 20.0,
            'correlation': 0.6
        },
        'stock': {
            'position_size': 0.1,
            'beta': 1.5,
            'pe_ratio': 30,
            'pb_ratio': 6,
            'dividend_yield': 0.025,
            'volume': 1500000,
            'market_cap': 15000000000
        },
        'portfolio': {
            'positions': {'STOCK_A': 0.1, 'STOCK_B': 0.09, 'STOCK_C': 0.08, 'STOCK_D': 0.07, 'STOCK_E': 0.06},
            'correlation_matrix': np.array([
                [1.0, 0.5, 0.4, 0.3, 0.2],
                [0.5, 1.0, 0.3, 0.4, 0.3],
                [0.4, 0.3, 1.0, 0.2, 0.3],
                [0.3, 0.4, 0.2, 1.0, 0.2],
                [0.2, 0.3, 0.3, 0.2, 1.0]
            ]),
            'sector_allocation': {'Tech': 0.3, 'Finance': 0.3, 'Healthcare': 0.2, 'Energy': 0.2}
        },
        'operational': {
            'trades_per_day': 80,
            'avg_slippage': 0.012,
            'system_health': 0.9,
            'execution_quality': 0.85
        },
        'emotional': {
            'fear_greed_index': 55,
            'herding_score': 0.5,
            'sentiment_extreme': 0.4
        }
    }
    
    # 定义不同风险场景
    scenarios = [
        {
            'name': '市场正常',
            'data': base_data.copy()
        },
        {
            'name': '市场恐慌',
            'data': {
                **base_data,
                'market': {
                    **base_data['market'],
                    'vix': 45.0,
                    'correlation': 0.9,
                    'returns': [-0.05, -0.08, -0.12, -0.03, -0.06, -0.09, -0.04, -0.07, -0.05, -0.08]
                }
            }
        },
        {
            'name': '个股风险',
            'data': {
                **base_data,
                'stock': {
                    **base_data['stock'],
                    'position_size': 0.35,
                    'beta': 2.8,
                    'pe_ratio': 80,
                    'pb_ratio': 15
                }
            }
        },
        {
            'name': '组合集中',
            'data': {
                **base_data,
                'portfolio': {
                    **base_data['portfolio'],
                    'positions': {'STOCK_A': 0.5, 'STOCK_B': 0.3, 'STOCK_C': 0.2},
                    'sector_allocation': {'Tech': 0.8, 'Finance': 0.2}
                }
            }
        },
        {
            'name': '操作风险',
            'data': {
                **base_data,
                'operational': {
                    **base_data['operational'],
                    'trades_per_day': 200,
                    'avg_slippage': 0.05,
                    'system_health': 0.7
                }
            }
        },
        {
            'name': '情绪风险',
            'data': {
                **base_data,
                'emotional': {
                    **base_data['emotional'],
                    'fear_greed_index': 90,
                    'herding_score': 0.85,
                    'sentiment_extreme': 0.9
                }
            }
        }
    ]
    
    # 分析每个场景
    print("风险场景对比分析:")
    print("-" * 80)
    print(f"{'场景名称':<12} {'整体风险':<10} {'风险等级':<8} {'市场':<8} {'个股':<8} {'组合':<8} {'操作':<8} {'情绪':<8}")
    print("-" * 80)
    
    for scenario in scenarios:
        data = scenario['data']
        overall_score, individual_scores = risk_system.calculate_overall_risk(data)
        level = risk_system._get_overall_risk_level(overall_score)
        alerts = risk_system.generate_all_alerts(data)
        
        market_score = individual_scores.get('market', 0)
        stock_score = individual_scores.get('single_stock', 0)
        portfolio_score = individual_scores.get('portfolio', 0)
        operational_score = individual_scores.get('operational', 0)
        emotional_score = individual_scores.get('emotional', 0)
        
        print(f"{scenario['name']:<12} {overall_score:<10.3f} {level:<8} {market_score:<8.3f} {stock_score:<8.3f} {portfolio_score:<8.3f} {operational_score:<8.3f} {emotional_score:<8.3f}")
    
    print("-" * 80)

def demo_risk_management_strategies():
    """演示风险管理策略"""
    print("\n=== 风险管理策略演示 ===")
    
    risk_system = MultiLevelRiskControlSystem()
    
    # 创建危机场景数据
    crisis_data = {
        'market': {
            'returns': [-0.1, -0.15, -0.2, -0.08, -0.12, -0.18, -0.06, -0.14, -0.09, -0.16],
            'prices': [100, 90, 72, 66, 58, 48, 45, 39, 35, 29],
            'vix': 65.0,
            'correlation': 0.95
        },
        'stock': {
            'position_size': 0.4,
            'beta': 3.0,
            'pe_ratio': 100,
            'pb_ratio': 20,
            'dividend_yield': 0.01,
            'volume': 500000,
            'market_cap': 5000000000
        },
        'portfolio': {
            'positions': {'TECH_1': 0.4, 'TECH_2': 0.35, 'TECH_3': 0.25},
            'correlation_matrix': np.array([
                [1.0, 0.9, 0.85],
                [0.9, 1.0, 0.8],
                [0.85, 0.8, 1.0]
            ]),
            'sector_allocation': {'Technology': 0.9, 'Finance': 0.1}
        },
        'operational': {
            'trades_per_day': 220,
            'avg_slippage': 0.06,
            'system_health': 0.6,
            'execution_quality': 0.5
        },
        'emotional': {
            'fear_greed_index': 95,
            'herding_score': 0.9,
            'sentiment_extreme': 0.95
        }
    }
    
    print("危机场景分析:")
    overall_score, individual_scores = risk_system.calculate_overall_risk(crisis_data)
    level = risk_system._get_overall_risk_level(overall_score)
    alerts = risk_system.generate_all_alerts(crisis_data)
    
    print(f"整体风险分数: {overall_score:.3f}")
    print(f"风险等级: {level}")
    print(f"告警数量: {len(alerts)}")
    
    # 生成风险管理建议
    recommendations = risk_system.get_risk_recommendations(crisis_data)
    print("\n风险管理建议:")
    for i, rec in enumerate(recommendations, 1):
        print(f"  {i}. {rec}")
    
    # 演示风险控制措施
    print("\n风险控制措施演示:")
    
    # 1. 调整风险阈值
    print("1. 调整风险阈值（更严格）:")
    for risk_type in RiskType:
        risk_system.update_control_thresholds(
            risk_type,
            {'low': 0.05, 'medium': 0.15, 'high': 0.25, 'critical': 0.35}
        )
    
    new_score, _ = risk_system.calculate_overall_risk(crisis_data)
    print(f"调整后风险分数: {new_score:.3f}")
    
    # 2. 禁用特定风险控制
    print("\n2. 禁用情绪风险控制:")
    risk_system.disable_control(RiskType.EMOTIONAL)
    
    disabled_score, _ = risk_system.calculate_overall_risk(crisis_data)
    print(f"禁用情绪风险后: {disabled_score:.3f}")
    
    # 3. 启用所有风险控制
    print("\n3. 启用所有风险控制:")
    for risk_type in RiskType:
        risk_system.enable_control(risk_type)
    
    enabled_score, _ = risk_system.calculate_overall_risk(crisis_data)
    print(f"启用所有风险后: {enabled_score:.3f}")
    
    # 4. 设置置信度阈值
    print("\n4. 设置置信度阈值:")
    for threshold in [0.5, 0.7, 0.9]:
        risk_system.set_confidence_threshold(threshold)
        alerts = risk_system.generate_all_alerts(crisis_data)
        print(f"置信度阈值 {threshold}: {len(alerts)} 个告警")

def demo_real_time_monitoring():
    """演示实时监控"""
    print("\n=== 实时监控演示 ===")
    
    risk_system = MultiLevelRiskControlSystem()
    
    # 模拟实时数据流
    base_data = {
        'market': {'vix': 20.0, 'correlation': 0.6, 'returns': [0.01, -0.01, 0.02, -0.02, 0.01], 'prices': [100, 101, 99, 101, 100]},
        'stock': {'position_size': 0.1, 'beta': 1.5, 'pe_ratio': 30, 'pb_ratio': 6, 'dividend_yield': 0.025, 'volume': 1500000, 'market_cap': 15000000000},
        'portfolio': {'positions': {'STOCK_A': 0.1, 'STOCK_B': 0.09, 'STOCK_C': 0.08}, 'correlation_matrix': np.eye(3), 'sector_allocation': {'Tech': 0.4, 'Finance': 0.3, 'Healthcare': 0.3}},
        'operational': {'trades_per_day': 80, 'avg_slippage': 0.012, 'system_health': 0.9, 'execution_quality': 0.85},
        'emotional': {'fear_greed_index': 55, 'herding_score': 0.5, 'sentiment_extreme': 0.4}
    }
    
    print("实时风险监控（模拟10个时间点）:")
    print("-" * 80)
    print(f"{'时间点':<6} {'整体风险':<10} {'等级':<6} {'告警':<6} {'市场':<8} {'个股':<8} {'组合':<8} {'操作':<8} {'情绪':<8}")
    print("-" * 80)
    
    for i in range(10):
        # 模拟数据变化
        current_data = base_data.copy()
        
        # 逐渐恶化市场状况
        if i >= 3:
            current_data['market']['vix'] += (i - 2) * 5
        if i >= 5:
            current_data['market']['correlation'] += (i - 4) * 0.1
        
        # 恶化个股状况
        if i >= 4:
            current_data['stock']['position_size'] += (i - 3) * 0.05
        if i >= 6:
            current_data['stock']['beta'] += (i - 5) * 0.3
        
        # 恶化操作状况
        if i >= 7:
            current_data['operational']['trades_per_day'] += (i - 6) * 30
        
        # 恶化情绪状况
        if i >= 8:
            current_data['emotional']['fear_greed_index'] += (i - 7) * 10
        
        # 计算风险
        overall_score, individual_scores = risk_system.calculate_overall_risk(current_data)
        level = risk_system._get_overall_risk_level(overall_score)
        alerts = risk_system.generate_all_alerts(current_data)
        
        market_score = individual_scores.get('market', 0)
        stock_score = individual_scores.get('single_stock', 0)
        portfolio_score = individual_scores.get('portfolio', 0)
        operational_score = individual_scores.get('operational', 0)
        emotional_score = individual_scores.get('emotional', 0)
        
        print(f"{i+1:<6} {overall_score:<10.3f} {level:<6} {len(alerts):<6} {market_score:<8.3f} {stock_score:<8.3f} {portfolio_score:<8.3f} {operational_score:<8.3f} {emotional_score:<8.3f}")
        
        # 如果出现高风险，显示告警信息
        if len(alerts) > 0:
            print(f"    ⚠️  告警: {', '.join([f'{alert.risk_type.value}' for alert in alerts])}")
    
    print("-" * 80)

def main():
    """主演示函数"""
    print("🛡️  多层次风险控制系统演示")
    print("=" * 50)
    
    demo_basic_risk_assessment()
    demo_risk_scenarios()
    demo_risk_management_strategies()
    demo_real_time_monitoring()
    
    print("\n" + "=" * 50)
    print("演示完成！")
    print("多层次风险控制系统具备以下核心功能：")
    print("✅ 5个维度的全面风险评估")
    print("✅ 实时风险监控和告警")
    print("✅ 灵活的风险阈值配置")
    print("✅ 多种风险管理策略")
    print("✅ 历史风险趋势分析")
    print("✅ 实时风险场景模拟")

if __name__ == "__main__":
    main()