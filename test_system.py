# -*- coding: utf-8 -*-
"""
量化策略系统测试脚本

功能：
- 测试所有模块导入
- 验证基本功能
- 检查系统完整性
- 生成测试报告

测试内容：
1. 模块导入测试
2. 基本功能测试
3. 系统集成测试
4. 性能测试
"""

import sys
import os
import time
import traceback
from datetime import datetime
from typing import Dict, List, Tuple

# 添加路径
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

# 导入日志器
from utils.logger import get_logger
logger = get_logger('test_system')

class TestResult:
    """测试结果类"""
    
    def __init__(self, test_name: str, success: bool = False, 
                 message: str = "", execution_time: float = 0.0):
        self.test_name = test_name
        self.success = success
        self.message = message
        self.execution_time = execution_time
        self.timestamp = datetime.now()
    
    def __str__(self):
        status = "✓ 通过" if self.success else "✗ 失败"
        return f"{self.test_name}: {status} ({self.execution_time:.3f}s) - {self.message}"


class SystemTester:
    """系统测试器"""
    
    def __init__(self):
        self.results = []
        self.start_time = time.time()
    
    def run_test(self, test_name: str, test_func) -> TestResult:
        """运行单个测试"""
        start_time = time.time()
        try:
            result = test_func()
            execution_time = time.time() - start_time
            
            if isinstance(result, tuple):
                success, message = result
            else:
                success = result
                message = "测试执行成功"
            
            test_result = TestResult(test_name, success, message, execution_time)
            self.results.append(test_result)
            
            return test_result
            
        except Exception as e:
            execution_time = time.time() - start_time
            message = f"测试异常: {str(e)}"
            logger.error(f"测试 {test_name} 失败: {message}")
            
            test_result = TestResult(test_name, False, message, execution_time)
            self.results.append(test_result)
            
            return test_result
    
    def run_tests(self, test_functions: Dict[str, callable]) -> List[TestResult]:
        """运行所有测试"""
        logger.info("开始系统测试")
        
        for test_name, test_func in test_functions.items():
            logger.info(f"运行测试: {test_name}")
            self.run_test(test_name, test_func)
        
        total_time = time.time() - self.start_time
        logger.info(f"所有测试完成，总耗时: {total_time:.3f}s")
        
        return self.results
    
    def get_summary(self) -> Dict:
        """获取测试总结"""
        if not self.results:
            return {}
        
        total_tests = len(self.results)
        passed_tests = sum(1 for r in self.results if r.success)
        failed_tests = total_tests - passed_tests
        
        success_rate = passed_tests / total_tests if total_tests > 0 else 0
        
        # 统计各模块测试结果
        module_results = {}
        for result in self.results:
            module_name = result.test_name.split('.')[0]
            if module_name not in module_results:
                module_results[module_name] = {'total': 0, 'passed': 0}
            
            module_results[module_name]['total'] += 1
            if result.success:
                module_results[module_name]['passed'] += 1
        
        return {
            'total_tests': total_tests,
            'passed_tests': passed_tests,
            'failed_tests': failed_tests,
            'success_rate': success_rate,
            'module_results': module_results,
            'total_time': time.time() - self.start_time
        }
    
    def print_results(self):
        """打印测试结果"""
        print("\n" + "="*60)
        print("量化策略系统测试报告")
        print("="*60)
        
        # 打印每个测试结果
        for result in self.results:
            print(f"\n{result}")
        
        # 打印总结
        summary = self.get_summary()
        print(f"\n测试总结:")
        print(f"  总测试数: {summary['total_tests']}")
        print(f"  通过测试: {summary['passed_tests']}")
        print(f"  失败测试: {summary['failed_tests']}")
        print(f"  成功率: {summary['success_rate']:.2%}")
        print(f"  总耗时: {summary['total_time']:.3f}s")
        
        # 打印模块结果
        print("\n各模块测试结果:")
        for module, result in summary['module_results'].items():
            pass_rate = result['passed'] / result['total'] if result['total'] > 0 else 0
            print(f"  {module}: {result['passed']}/{result['total']} ({pass_rate:.2%})")
        
        # 判断系统是否就绪
        if summary['success_rate'] >= 0.9:
            print("\n✓ 系统测试通过，可以正常运行")
        else:
            print("\n✗ 系统测试失败，需要修复后才能运行")


def test_imports():
    """测试模块导入"""
    try:
        # 测试所有核心模块导入
        from enhanced_delta_hedge import EnhancedDeltaHedge
        from volatility_hedge import VolatilityHedge
        from tail_risk_hedge import TailRiskHedge
        from smart_hedge_trigger import SmartHedgeTrigger
        from dynamic_capital_manager import DynamicCapitalManager
        from enhanced_risk_manager import EnhancedRiskManager
        from automated_execution_system import AutomatedExecutionSystem
        from strategy_optimizer import StrategyOptimizer
        from quantitative_strategy_system import QuantitativeStrategySystem
        
        # 测试工具模块导入
        from utils.logger import get_logger
        from utils.data_provider import get_market_data, get_historical_data, get_sentiment_data
        from utils.risk_metrics import calculate_var, calculate_es, calculate_max_drawdown
        
        return True, "所有模块导入成功"
        
    except Exception as e:
        return False, f"模块导入失败: {str(e)}"


def test_basic_functions():
    """测试基本功能"""
    try:
        # 测试数据获取
        from utils.data_provider import get_market_data, get_historical_data, get_sentiment_data
        
        market_data = get_market_data()
        if not market_data:
            return False, "市场数据获取失败"
        
        historical_data = get_historical_data('SPY', '1m')
        if historical_data.empty:
            return False, "历史数据获取失败"
        
        sentiment_data = get_sentiment_data()
        if not sentiment_data:
            return False, "情绪数据获取失败"
        
        # 测试风险指标计算
        from utils.risk_metrics import calculate_var, calculate_es, calculate_max_drawdown, calculate_sharpe_ratio
        
        returns = [0.01, -0.02, 0.03, -0.01, 0.02]
        prices = [100, 101, 99, 102, 103]
        
        var_95 = calculate_var(returns, 0.95)
        es_95 = calculate_es(returns, 0.95)
        max_dd, _, _ = calculate_max_drawdown(prices)
        sharpe = calculate_sharpe_ratio(returns)
        
        if var_95 == 0 or es_95 == 0 or max_dd == 0 or sharpe == 0:
            return False, "风险指标计算失败"
        
        return True, "基本功能测试通过"
        
    except Exception as e:
        return False, f"基本功能测试失败: {str(e)}"


def test_hedge_strategies():
    """测试对冲策略"""
    try:
        from enhanced_delta_hedge import EnhancedDeltaHedge
        from volatility_hedge import VolatilityHedge
        from tail_risk_hedge import TailRiskHedge
        
        # 测试Delta对冲
        delta_hedge = EnhancedDeltaHedge(1000000)
        delta_status = delta_hedge.get_status()
        if delta_status == 'error':
            return False, "Delta对冲策略初始化失败"
        
        # 测试波动率对冲
        volatility_hedge = VolatilityHedge(300000)
        volatility_status = volatility_hedge.get_status()
        if volatility_status == 'error':
            return False, "波动率对冲策略初始化失败"
        
        # 测试尾部风险对冲
        tail_risk_hedge = TailRiskHedge(100000)
        tail_risk_status = tail_risk_hedge.get_status()
        if tail_risk_status == 'error':
            return False, "尾部风险对冲策略初始化失败"
        
        return True, "对冲策略测试通过"
        
    except Exception as e:
        return False, f"对冲策略测试失败: {str(e)}"


def test_smart_trigger():
    """测试智能触发器"""
    try:
        from smart_hedge_trigger import SmartHedgeTrigger
        
        trigger = SmartHedgeTrigger()
        trigger_status = trigger.get_trigger_status()
        
        if 'error' in trigger_status:
            return False, "智能触发器初始化失败"
        
        # 运行模拟
        simulation_result = trigger.run_simulation()
        if not simulation_result['success']:
            return False, "智能触发器模拟失败"
        
        return True, "智能触发器测试通过"
        
    except Exception as e:
        return False, f"智能触发器测试失败: {str(e)}"


def test_capital_manager():
    """测试资金管理器"""
    try:
        from dynamic_capital_manager import DynamicCapitalManager
        
        capital_manager = DynamicCapitalManager(1000000)
        capital_status = capital_manager.get_status()
        
        if capital_status == 'error':
            return False, "资金管理器初始化失败"
        
        return True, "资金管理器测试通过"
        
    except Exception as e:
        return False, f"资金管理器测试失败: {str(e)}"


def test_risk_manager():
    """测试风险管理器"""
    try:
        from enhanced_risk_manager import EnhancedRiskManager
        
        risk_manager = EnhancedRiskManager(5000000)
        risk_status = risk_manager.get_status()
        
        if risk_status == 'error':
            return False, "风险管理器初始化失败"
        
        return True, "风险管理器测试通过"
        
    except Exception as e:
        return False, f"风险管理器测试失败: {str(e)}"


def test_execution_system():
    """测试执行系统"""
    try:
        from automated_execution_system import AutomatedExecutionSystem
        
        execution_system = AutomatedExecutionSystem(5000000)
        execution_status = execution_system.get_status()
        
        if execution_status == 'error':
            return False, "执行系统初始化失败"
        
        return True, "执行系统测试通过"
        
    except Exception as e:
        return False, f"执行系统测试失败: {str(e)}"


def test_strategy_optimizer():
    """测试策略优化器"""
    try:
        from strategy_optimizer import StrategyOptimizer
        
        optimizer = StrategyOptimizer(5000000)
        optimizer_status = optimizer.get_status()
        
        if optimizer_status == 'error':
            return False, "策略优化器初始化失败"
        
        return True, "策略优化器测试通过"
        
    except Exception as e:
        return False, f"策略优化器测试失败: {str(e)}"


def test_main_system():
    """测试主系统"""
    try:
        from quantitative_strategy_system import QuantitativeStrategySystem
        
        system = QuantitativeStrategySystem(
            total_capital=5000000,
            stock_etf_capital=4000000,
            hedge_capital=1000000
        )
        
        system_status = system.get_system_summary()
        
        if 'error' in system_status:
            return False, "主系统初始化失败"
        
        # 测试手动优化
        system.run_manual_optimization()
        
        return True, "主系统测试通过"
        
    except Exception as e:
        return False, f"主系统测试失败: {str(e)}"


def test_performance():
    """测试性能"""
    try:
        import time
        from quantitative_strategy_system import QuantitativeStrategySystem
        
        # 测试系统初始化性能
        start_time = time.time()
        system = QuantitativeStrategySystem(5000000)
        init_time = time.time() - start_time
        
        if init_time > 5.0:  # 初始化时间超过5秒
            return False, f"系统初始化时间过长: {init_time:.3f}s"
        
        # 测试性能指标计算
        from utils.risk_metrics import calculate_performance_metrics
        import numpy as np
        
        start_time = time.time()
        returns = np.random.normal(0.001, 0.02, 252)
        prices = 3000 * np.exp(np.cumsum(returns))
        metrics = calculate_performance_metrics(returns, prices)
        calc_time = time.time() - start_time
        
        if calc_time > 1.0:  # 计算时间超过1秒
            return False, f"性能指标计算时间过长: {calc_time:.3f}s"
        
        return True, f"性能测试通过 (初始化: {init_time:.3f}s, 计算: {calc_time:.3f}s)"
        
    except Exception as e:
        return False, f"性能测试失败: {str(e)}"


def main():
    """主函数"""
    print("量化策略系统测试")
    print("=" * 60)
    
    # 创建测试器
    tester = SystemTester()
    
    # 定义测试函数
    test_functions = {
        "模块导入": test_imports,
        "基本功能": test_basic_functions,
        "对冲策略": test_hedge_strategies,
        "智能触发器": test_smart_trigger,
        "资金管理器": test_capital_manager,
        "风险管理器": test_risk_manager,
        "执行系统": test_execution_system,
        "策略优化器": test_strategy_optimizer,
        "主系统": test_main_system,
        "性能测试": test_performance
    }
    
    # 运行所有测试
    results = tester.run_tests(test_functions)
    
    # 打印结果
    tester.print_results()
    
    # 生成测试报告
    test_report = {
        'timestamp': datetime.now().isoformat(),
        'test_results': [str(r) for r in results],
        'summary': tester.get_summary(),
        'system_ready': tester.get_summary().get('success_rate', 0) >= 0.9
    }
    
    # 保存测试报告
    report_filename = f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    import json
    with open(report_filename, 'w', encoding='utf-8') as f:
        json.dump(test_report, f, indent=2, ensure_ascii=False)
    
    logger.info(f"测试报告已保存: {report_filename}")
    
    # 返回测试结果
    return test_report['system_ready']


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)