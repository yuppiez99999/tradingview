# -*- coding: utf-8 -*-
"""
量化策略系统快速检查脚本

功能：
- 快速验证所有模块的导入
- 检查基本功能
- 生成系统状态报告
- 提供优化建议

使用方法：
python quick_check.py
"""

import sys
import os
import time
import json
from datetime import datetime
from typing import Dict, List, Any

# 添加路径
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

# 创建日志器
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('quick_check')


class SystemChecker:
    """系统检查器"""
    
    def __init__(self):
        self.results = {}
        self.issues = []
        self.suggestions = []
    
    def check_module_import(self, module_name: str) -> bool:
        """检查模块导入"""
        try:
            __import__(module_name)
            return True
        except Exception as e:
            self.issues.append(f"模块导入失败: {module_name} - {str(e)}")
            return False
    
    def check_system_integrity(self) -> Dict[str, Any]:
        """检查系统完整性"""
        print("检查系统完整性...")
        
        # 检查核心模块
        core_modules = [
            'enhanced_delta_hedge',
            'volatility_hedge',
            'tail_risk_hedge',
            'smart_hedge_trigger',
            'dynamic_capital_manager',
            'enhanced_risk_manager',
            'automated_execution_system',
            'strategy_optimizer',
            'quantitative_strategy_system'
        ]
        
        for module in core_modules:
            status = self.check_module_import(module)
            self.results[f'core_module_{module}'] = status
        
        # 检查工具模块
        utils_modules = [
            'utils.logger',
            'utils.data_provider',
            'utils.risk_metrics'
        ]
        
        for module in utils_modules:
            status = self.check_module_import(module)
            self.results[f'utils_module_{module}'] = status
        
        return self.results
    
    def check_basic_functionality(self) -> Dict[str, Any]:
        """检查基本功能"""
        print("检查基本功能...")
        
        try:
            # 测试数据获取
            from utils.data_provider import get_market_data, get_historical_data
            
            market_data = get_market_data()
            historical_data = get_historical_data('SPY', '1m')
            
            self.results['data_provider'] = {
                'market_data': bool(market_data),
                'historical_data': not historical_data.empty
            }
            
            # 测试风险指标
            from utils.risk_metrics import calculate_var, calculate_max_drawdown
            
            returns = [0.01, -0.02, 0.03, -0.01, 0.02]
            prices = [100, 101, 99, 102, 103]
            
            var_result = calculate_var(returns, 0.95)
            max_dd_result = calculate_max_drawdown(prices)
            
            self.results['risk_metrics'] = {
                'var_calculation': var_result > 0,
                'max_dd_calculation': max_dd_result > 0
            }
            
            return True, "基本功能检查通过"
            
        except Exception as e:
            return False, f"基本功能检查失败: {str(e)}"
    
    def check_system_capabilities(self) -> Dict[str, Any]:
        """检查系统功能"""
        print("检查系统功能...")
        
        try:
            # 测试主系统
            from quantitative_strategy_system import QuantitativeStrategySystem
            
            system = QuantitativeStrategySystem(5000000)
            summary = system.get_system_summary()
            
            if 'error' in summary:
                return False, f"主系统初始化失败: {summary['error']}"
            
            self.results['main_system'] = {
                'initialized': True,
                'status': summary.get('status', 'unknown'),
                'components_loaded': len(summary.get('components', {}))
            }
            
            # 测试对冲策略
            from enhanced_delta_hedge import EnhancedDeltaHedge
            from volatility_hedge import VolatilityHedge
            from tail_risk_hedge import TailRiskHedge
            
            delta_hedge = EnhancedDeltaHedge(1000000)
            volatility_hedge = VolatilityHedge(300000)
            tail_risk_hedge = TailRiskHedge(100000)
            
            self.results['hedge_strategies'] = {
                'delta_hedge': delta_hedge.get_status() == 'ready',
                'volatility_hedge': volatility_hedge.get_status() == 'ready',
                'tail_risk_hedge': tail_risk_hedge.get_status() == 'ready'
            }
            
            return True, "系统功能检查通过"
            
        except Exception as e:
            return False, f"系统功能检查失败: {str(e)}"
    
    def generate_report(self) -> Dict[str, Any]:
        """生成系统报告"""
        print("生成系统报告...")
        
        # 计算总体状态
        total_checks = len(self.results)
        passed_checks = sum(1 for v in self.results.values() if isinstance(v, bool) and v)
        
        if total_checks == 0:
            status = "未知"
            readiness = 0
        else:
            readiness = passed_checks / total_checks
            if readiness >= 0.9:
                status = "就绪"
            elif readiness >= 0.7:
                status = "部分就绪"
            elif readiness >= 0.5:
                status = "有问题"
            else:
                status = "严重问题"
        
        # 生成建议
        if self.issues:
            self.suggestions.append("修复以下模块导入问题:")
            self.suggestions.extend(self.issues)
        
        if readiness < 0.9:
            self.suggestions.append("系统尚有改进空间，建议:")
            self.suggestions.append("1. 检查所有依赖项是否正确安装")
            self.suggestions.append("2. 验证配置文件是否正确")
            self.suggestions.append("3. 运行完整测试检查所有功能")
        
        report = {
            'timestamp': datetime.now().isoformat(),
            'system_status': status,
            'readiness_score': readiness,
            'total_checks': total_checks,
            'passed_checks': passed_checks,
            'issues': self.issues,
            'suggestions': self.suggestions,
            'detailed_results': self.results
        }
        
        return report
    
    def print_report(self, report: Dict[str, Any]):
        """打印系统报告"""
        print("\n" + "="*60)
        print("量化策略系统状态报告")
        print("="*60)
        print(f"检查时间: {report['timestamp']}")
        print(f"系统状态: {report['system_status']}")
        print(f"就绪评分: {report['readiness_score']:.2%}")
        print(f"总检查项: {report['total_checks']}")
        print(f"通过检查: {report['passed_checks']}")
        
        print("\n详细检查结果:")
        for key, value in report['detailed_results'].items():
            if isinstance(value, bool):
                status = "✓ 通过" if value else "✗ 失败"
            else:
                status = str(value)
            print(f"  {key}: {status}")
        
        if report['issues']:
            print("\n发现的问题:")
            for issue in report['issues']:
                print(f"  ✗ {issue}")
        
        if report['suggestions']:
            print("\n优化建议:")
            for suggestion in report['suggestions']:
                print(f"  • {suggestion}")
        
        print("\n" + "="*60)
        
        # 给出最终建议
        if report['readiness_score'] >= 0.9:
            print("✓ 系统状态良好，可以正常运行")
            return True
        elif report['readiness_score'] >= 0.7:
            print("⚠ 系统基本正常，但有改进空间")
            return False
        else:
            print("✗ 系统需要修复才能使用")
            return False


def main():
    """主函数"""
    print("量化策略系统快速检查")
    print("=" * 60)
    
    # 创建检查器
    checker = SystemChecker()
    
    # 执行检查
    checker.check_system_integrity()
    
    # 检查基本功能
    func_ok, func_msg = checker.check_basic_functionality()
    checker.results['basic_functionality'] = func_ok
    
    # 检查系统功能
    system_ok, system_msg = checker.check_system_capabilities()
    checker.results['system_functionality'] = system_ok
    
    # 生成报告
    report = checker.generate_report()
    
    # 打印报告
    ready = checker.print_report(report)
    
    # 保存报告
    report_filename = f"system_check_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_filename, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    print(f"\n详细报告已保存: {report_filename}")
    
    return ready


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)