# -*- coding: utf-8 -*-
"""
量化策略系统启动器 v5.10

功能说明：
- 自动导入所有核心模块
- 系统完整性检查
- 系统启动和运行控制
- 实时监控和状态报告
- 错误处理和系统恢复

系统架构：
1. 主系统控制器 (QuantitativeStrategySystem)
2. 增强Delta对冲策略 (EnhancedDeltaHedge)
3. 波动率对冲策略 (VolatilityHedge) 
4. 尾部风险对冲策略 (TailRiskHedge)
5. 智能对冲触发器 (SmartHedgeTrigger)
6. 动态资金管理 (DynamicCapitalManager)
7. 增强风险管理 (EnhancedRiskManager)
8. 自动化执行系统 (AutomatedExecutionSystem)
9. 策略优化器 (StrategyOptimizer)
"""

import sys
import os
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

# 添加路径
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

def check_system_integrity():
    """检查系统完整性"""
    print("正在检查系统完整性...")
    
    required_files = [
        'enhanced_delta_hedge.py',
        'volatility_hedge.py', 
        'tail_risk_hedge.py',
        'smart_hedge_trigger.py',
        'dynamic_capital_manager.py',
        'enhanced_risk_manager.py',
        'automated_execution_system.py',
        'strategy_optimizer.py',
        'quantitative_strategy_system.py'
    ]
    
    missing_files = []
    
    for file in required_files:
        file_path = os.path.join(current_dir, file)
        if not os.path.exists(file_path):
            missing_files.append(file)
        else:
            print(f"  ✓ {file}")
    
    if missing_files:
        print(f"\n错误: 缺失以下文件:")
        for file in missing_files:
            print(f"  ✗ {file}")
        return False
    
    print("\n系统完整性检查通过")
    return True

def import_system_modules():
    """导入系统模块"""
    print("正在导入系统模块...")
    
    try:
        # 导入所有模块
        from enhanced_delta_hedge import EnhancedDeltaHedge
        from volatility_hedge import VolatilityHedge
        from tail_risk_hedge import TailRiskHedge
        from smart_hedge_trigger import SmartHedgeTrigger
        from dynamic_capital_manager import DynamicCapitalManager
        from enhanced_risk_manager import EnhancedRiskManager
        from automated_execution_system import AutomatedExecutionSystem
        from strategy_optimizer import StrategyOptimizer
        from quantitative_strategy_system import QuantitativeStrategySystem
        
        # 验证导入
        modules = {
            'EnhancedDeltaHedge': EnhancedDeltaHedge,
            'VolatilityHedge': VolatilityHedge,
            'TailRiskHedge': TailRiskHedge,
            'SmartHedgeTrigger': SmartHedgeTrigger,
            'DynamicCapitalManager': DynamicCapitalManager,
            'EnhancedRiskManager': EnhancedRiskManager,
            'AutomatedExecutionSystem': AutomatedExecutionSystem,
            'StrategyOptimizer': StrategyOptimizer,
            'QuantitativeStrategySystem': QuantitativeStrategySystem
        }
        
        print("模块导入完成:")
        for name, module in modules.items():
            print(f"  ✓ {name}")
        
        return modules
        
    except Exception as e:
        print(f"\n模块导入失败: {e}")
        return None

def display_system_info():
    """显示系统信息"""
    print("\n" + "="*60)
    print("量化策略系统 v5.10 - 世界级对冲基金系统")
    print("="*60)
    
    print("\n系统特点:")
    print("  ✓ 多层对冲策略：Delta对冲、波动率对冲、尾部风险保护")
    print("  ✓ 智能触发机制：基于市场情绪、技术指标、ML预测")
    print("  ✓ 动态风险管理：实时监控、压力测试、自动预警")
    print("  ✓ 自适应资金管理：动态配置、效率优化、风险调整")
    print("  ✓ 自动化执行：7:00AM定时执行、智能订单路由")
    print("  ✓ 策略优化整合：权重优化、回测验证、实时优化")
    
    print("\n系统配置:")
    print("  ✓ 总资本：500万元人民币")
    print("  ✓ 股票ETF资金：400万元")
    print("  ✓ 对冲资金：100万元")
    print("  ✓ 年化收益目标：≥8%")
    print("  ✓ 最大回撤限制：≤15%")
    print("  ✓ 执行时间：每日7:00 AM")
    
    print("\n性能要求:")
    print("  ✓ 实时风险监控：风险预警延迟 < 1分钟")
    print("  ✓ 执行精度：订单执行延迟 < 100ms")
    print("  ✓ 数据质量：数据完整性 > 99.9%")
    print("  ✓ 系统可用性：运行时间 > 99.5%")

class SystemRunner:
    """系统运行器"""
    
    def __init__(self):
        self.system = None
        self.running = False
        self.monitor_thread = None
        
    def initialize_system(self):
        """初始化系统"""
        try:
            print("\n正在初始化量化策略系统...")
            
            # 创建主系统实例
            self.system = QuantitativeStrategySystem(
                total_capital=5000000,
                stock_etf_capital=4000000,
                hedge_capital=1000000
            )
            
            print("系统初始化完成")
            return True
            
        except Exception as e:
            print(f"系统初始化失败: {e}")
            return False
    
    def start_system(self):
        """启动系统"""
        try:
            if not self.system:
                if not self.initialize_system():
                    return False
            
            print("\n正在启动量化策略系统...")
            self.system.start_system()
            self.running = True
            
            # 启动监控线程
            self.monitor_thread = threading.Thread(target=self._monitor_system)
            self.monitor_thread.daemon = True
            self.monitor_thread.start()
            
            print("系统启动成功")
            print("\n系统运行中...按 Ctrl+C 查看状态报告")
            
            return True
            
        except Exception as e:
            print(f"系统启动失败: {e}")
            return False
    
    def stop_system(self):
        """停止系统"""
        try:
            if self.running and self.system:
                print("\n正在停止系统...")
                self.system.stop_system()
                self.running = False
                
                # 等待监控线程结束
                if self.monitor_thread:
                    self.monitor_thread.join(timeout=5)
                
                print("系统已停止")
                return True
            
            return False
            
        except Exception as e:
            print(f"系统停止失败: {e}")
            return False
    
    def _monitor_system(self):
        """监控系统运行状态"""
        while self.running:
            try:
                # 每60秒输出一次状态
                time.sleep(60)
                
                if self.system:
                    summary = self.system.get_system_summary()
                    
                    # 更新控制台标题
                    status_text = (
                        f"系统运行中 | "
                        f"运行时间: {summary['system_status']['uptime_hours']:.1f}h | "
                        f"总收益: {summary['performance_summary']['annual_return']:.2%} | "
                        f"最大回撤: {summary['performance_summary']['max_drawdown']:.2%} | "
                        f"胜率: {summary['performance_summary']['win_rate']:.2%} | "
                        f"执行次数: {summary['execution_statistics']['completed_tasks']}"
                    )
                    
                    # 更新控制台标题
                    print(f"\r{status_text}", end='', flush=True)
                
            except Exception as e:
                print(f"\n监控错误: {e}")
                time.sleep(60)
    
    def show_status(self):
        """显示系统状态"""
        try:
            if self.system:
                print("\n" + "="*60)
                print("系统状态报告")
                print("="*60)
                
                summary = self.system.get_system_summary()
                
                # 系统基本信息
                print("\n系统基本信息:")
                print(f"  系统状态: {'运行中' if summary['system_status']['is_running'] else '已停止'}")
                print(f"  启用状态: {'启用' if summary['system_status']['system_enabled'] else '禁用'}")
                print(f"  运行时间: {summary['system_status']['uptime_hours']:.1f} 小时")
                print(f"  最后更新: {summary['system_status']['last_update']}")
                
                # 绩效指标
                print("\n绩效指标:")
                print(f"  年化收益: {summary['performance_summary']['annual_return']:.2%}")
                print(f"  最大回撤: {summary['performance_summary']['max_drawdown']:.2%}")
                print(f"  夏普比率: {summary['performance_summary']['sharpe_ratio']:.2f}")
                print(f"  胜率: {summary['performance_summary']['win_rate']:.2%}")
                print(f"  利润因子: {summary['performance_summary']['profit_factor']:.2f}")
                print(f"  总交易数: {summary['performance_summary']['total_trades']}")
                print(f"  成功交易: {summary['performance_summary']['successful_trades']}")
                
                # 执行统计
                print("\n执行统计:")
                print(f"  今日已执行: {'是' if summary['execution_statistics']['today_executed'] else '否'}")
                print(f"  已完成任务: {summary['execution_statistics']['completed_tasks']}")
                print(f"  待完成任务: {summary['execution_statistics']['pending_tasks']}")
                
                if summary['execution_statistics']['latest_execution']:
                    print(f"  最后执行时间: {summary['execution_statistics']['latest_execution']}")
                
                # 模块状态
                print("\n模块状态:")
                for module, status in summary['module_status'].items():
                    status_text = "正常" if status != 'error' else "错误"
                    print(f"  {module}: {status_text}")
                
                # 预警信息
                if summary['recent_alerts']:
                    print("\n近期预警:")
                    for alert in summary['recent_alerts']:
                        timestamp = alert['timestamp'][:19]  # 简化时间格式
                        print(f"  [{timestamp}] {alert['message']}")
                else:
                    print("\n近期预警: 无")
                
                print("\n" + "="*60)
                
                return True
            else:
                print("系统未初始化")
                return False
                
        except Exception as e:
            print(f"状态查询失败: {e}")
            return False
    
    def run_manual_optimization(self):
        """手动运行优化"""
        try:
            if self.system:
                print("\n正在运行手动优化...")
                self.system.run_manual_optimization()
                print("优化任务已添加到队列")
                return True
            else:
                print("系统未初始化")
                return False
        except Exception as e:
            print(f"手动优化失败: {e}")
            return False
    
    def export_report(self, filename=None):
        """导出系统报告"""
        try:
            if not filename:
                filename = f"system_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            
            if self.system:
                success = self.system.export_system_report(filename)
                if success:
                    print(f"\n系统报告已导出: {filename}")
                    return True
                else:
                    print("\n报告导出失败")
                    return False
            else:
                print("系统未初始化")
                return False
        except Exception as e:
            print(f"报告导出失败: {e}")
            return False

def main():
    """主函数"""
    # 显示系统信息
    display_system_info()
    
    # 检查系统完整性
    if not check_system_integrity():
        print("\n系统不完整，请检查缺失的模块")
        return
    
    # 导入模块
    modules = import_system_modules()
    if not modules:
        print("\n模块导入失败，请检查Python环境和依赖")
        return
    
    # 创建系统运行器
    runner = SystemRunner()
    
    try:
        # 启动系统
        if not runner.start_system():
            print("系统启动失败")
            return
        
        # 主循环
        print("\n输入命令控制系统:")
        print("  status   - 查看系统状态")
        print("  optimize - 手动运行优化")
        print("  report   - 导出系统报告")
        print("  quit     - 退出系统")
        
        while True:
            try:
                cmd = input("\n命令> ").strip().lower()
                
                if cmd == 'status':
                    runner.show_status()
                
                elif cmd == 'optimize':
                    runner.run_manual_optimization()
                
                elif cmd == 'report':
                    runner.export_report()
                
                elif cmd in ['quit', 'exit', 'q']:
                    print("\n正在退出系统...")
                    runner.stop_system()
                    print("系统已退出")
                    break
                
                elif cmd == 'help':
                    print("\n可用命令:")
                    print("  status   - 查看系统状态")
                    print("  optimize - 手动运行优化")
                    print("  report   - 导出系统报告")
                    print("  quit     - 退出系统")
                
                else:
                    print("未知命令，输入 'help' 查看帮助")
                
            except KeyboardInterrupt:
                print("\n检测到中断信号...")
                runner.stop_system()
                print("系统已停止")
                break
            except Exception as e:
                print(f"命令执行错误: {e}")
                continue
    
    except KeyboardInterrupt:
        print("\n检测到中断信号...")
        runner.stop_system()
        print("系统已停止")
    except Exception as e:
        print(f"\n系统运行错误: {e}")
        runner.stop_system()

if __name__ == "__main__":
    main()