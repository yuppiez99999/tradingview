# -*- coding: utf-8 -*-
"""
量化策略系统启动脚本

功能：
- 系统完整性检查
- 配置验证
- 系统初始化
- 自动执行管理
- 监控和日志管理

使用方法：
python start_system.py [mode]

mode参数：
- auto: 自动执行模式（默认）
- manual: 手动模式
- test: 测试模式
- check: 系统检查模式
"""

import sys
import os
import time
import signal
import threading
import argparse
import json
from datetime import datetime, time
from typing import Dict, List, Any, Optional
from enum import Enum

# 添加路径
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

# 导入系统组件
from utils.logger import get_logger
from utils.data_provider import get_market_data, get_historical_data
from utils.risk_metrics import calculate_var, calculate_max_drawdown
from config import Config
from quantitative_strategy_system import QuantitativeStrategySystem
from automated_execution_system import AutomatedExecutionSystem

# 导入必要的日志器
logger = get_logger('system_starter')


class ExecutionMode(Enum):
    """执行模式枚举"""
    AUTO = "auto"          # 自动执行
    MANUAL = "manual"      # 手动执行
    TEST = "test"          # 测试模式
    CHECK = "check"        # 检查模式


class SystemStarter:
    """系统启动器"""
    
    def __init__(self, mode: ExecutionMode = ExecutionMode.AUTO):
        self.mode = mode
        self.config = Config.get_instance()
        self.system = None
        self.execution_system = None
        self.running = False
        self.start_time = None
        self.last_execution_time = None
        self.execution_count = 0
        self.error_count = 0
        self.monitor_thread = None
        
        # 注册信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        logger.info(f"系统启动器初始化 - 模式: {mode.value}")
    
    def _signal_handler(self, signum, frame):
        """信号处理"""
        logger.info(f"接收到信号 {signum}，准备停止系统...")
        self.stop()
        sys.exit(0)
    
    def _log_system_info(self):
        """记录系统信息"""
        logger.info("=" * 60)
        logger.info("量化策略系统信息")
        logger.info("=" * 60)
        logger.info(f"启动时间: {self.start_time}")
        logger.info(f"当前模式: {self.mode.value}")
        logger.info(f"总资金: {self.config.total_capital:,.2f} 元")
        logger.info(f"股票ETF资金: {self.config.stock_etf_capital:,.2f} 元")
        logger.info(f"对冲资金: {self.config.hedge_capital:,.2f} 元")
        logger.info(f"执行时间: {self.config.execution_config.execution_time}")
        logger.info("=" * 60)
    
    def _check_system_requirements(self) -> bool:
        """检查系统要求"""
        logger.info("检查系统要求...")
        
        try:
            # 检查必要文件
            required_files = [
                "quantitative_strategy_system.py",
                "automated_execution_system.py",
                "config.py",
                "utils/logger.py",
                "utils/data_provider.py",
                "utils/risk_metrics.py"
            ]
            
            for file in required_files:
                if not os.path.exists(file):
                    logger.error(f"缺少必要文件: {file}")
                    return False
            
            # 检查数据获取
            logger.info("检查数据获取...")
            market_data = get_market_data()
            if not market_data:
                logger.warning("无法获取市场数据")
                return False
            
            # 检查历史数据
            historical_data = get_historical_data('SPY', '1m')
            if historical_data.empty:
                logger.warning("无法获取历史数据")
                return False
            
            # 检查风险指标计算
            logger.info("检查风险指标计算...")
            returns = [0.01, -0.02, 0.03, -0.01, 0.02]
            prices = [100, 101, 99, 102, 103]
            
            var_result = calculate_var(returns, 0.95)
            dd_result = calculate_max_drawdown(prices)
            
            if var_result == 0 or dd_result == 0:
                logger.warning("风险指标计算异常")
                return False
            
            logger.info("所有系统要求检查通过")
            return True
            
        except Exception as e:
            logger.error(f"系统要求检查失败: {e}")
            return False
    
    def _init_system(self) -> bool:
        """初始化系统"""
        logger.info("初始化量化策略系统...")
        
        try:
            # 初始化主系统
            self.system = QuantitativeStrategySystem(
                total_capital=self.config.total_capital,
                stock_etf_capital=self.config.stock_etf_capital,
                hedge_capital=self.config.hedge_capital
            )
            
            # 初始化自动执行系统
            self.execution_system = AutomatedExecutionSystem(
                total_capital=self.config.total_capital
            )
            
            # 获取系统状态
            system_status = self.system.get_system_summary()
            execution_status = self.execution_system.get_status()
            
            if 'error' in system_status or 'error' in execution_status:
                logger.error(f"系统初始化失败: {system_status.get('error', execution_status.get('error'))}")
                return False
            
            logger.info("系统初始化成功")
            return True
            
        except Exception as e:
            logger.error(f"系统初始化失败: {e}")
            return False
    
    def _monitor_system(self):
        """监控系统状态"""
        logger.info("启动系统监控线程...")
        
        while self.running:
            try:
                # 获取系统状态
                if self.system:
                    status = self.system.get_system_summary()
                    self._log_status(status)
                
                # 获取执行状态
                if self.execution_system:
                    exec_status = self.execution_system.get_execution_status()
                    self._log_execution_status(exec_status)
                
                # 每5分钟记录一次状态
                time.sleep(300)
                
            except Exception as e:
                logger.error(f"监控系统状态失败: {e}")
                self.error_count += 1
    
    def _log_status(self, status: Dict):
        """记录状态"""
        try:
            if 'portfolio_value' in status:
                logger.info(f"组合价值: {status['portfolio_value']:,.2f} 元")
            if 'total_return' in status:
                logger.info(f"总收益率: {status['total_return']:.4f}")
            if 'max_drawdown' in status:
                logger.info(f"最大回撤: {status['max_drawdown']:.4f}")
            if 'risk_metrics' in status:
                risk = status['risk_metrics']
                logger.info(f"风险指标 - VaR: {risk.get('var', 0):.4f}, ES: {risk.get('es', 0):.4f}")
        except Exception as e:
            logger.error(f"记录状态失败: {e}")
    
    def _log_execution_status(self, status: Dict):
        """记录执行状态"""
        try:
            if 'last_execution_time' in status:
                logger.info(f"上次执行时间: {status['last_execution_time']}")
            if 'next_execution_time' in status:
                logger.info(f"下次执行时间: {status['next_execution_time']}")
            if 'execution_count' in status:
                logger.info(f"执行次数: {status['execution_count']}")
            if 'last_error' in status:
                if status['last_error']:
                    logger.warning(f"上次错误: {status['last_error']}")
        except Exception as e:
            logger.error(f"记录执行状态失败: {e}")
    
    def _check_execution_time(self) -> bool:
        """检查是否到执行时间"""
        if self.mode == ExecutionMode.MANUAL:
            return True
        
        try:
            # 解析执行时间
            exec_time = time.strptime(self.config.execution_config.execution_time, "%H:%M")
            current_time = datetime.now().time()
            
            # 检查是否在执行时间窗口内
            if (current_time.hour == exec_time.hour and 
                current_time.minute == exec_time.minute):
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"检查执行时间失败: {e}")
            return False
    
    def _execute_strategy(self) -> bool:
        """执行策略"""
        logger.info("开始执行量化策略...")
        
        try:
            # 执行策略优化
            if self.mode != ExecutionMode.TEST:
                optimization_result = self.system.run_manual_optimization()
                if not optimization_result.get('success', False):
                    logger.warning("策略优化失败")
                    return False
            
            # 执行交易
            if self.mode != ExecutionMode.TEST:
                execution_result = self.execution_system.execute_strategy()
                if not execution_result.get('success', False):
                    logger.error("策略执行失败")
                    return False
            
            # 更新执行统计
            self.execution_count += 1
            self.last_execution_time = datetime.now()
            
            logger.info("策略执行成功")
            return True
            
        except Exception as e:
            logger.error(f"策略执行失败: {e}")
            self.error_count += 1
            return False
    
    def _test_system(self):
        """测试系统"""
        logger.info("开始系统测试...")
        
        try:
            # 测试数据获取
            market_data = get_market_data()
            if not market_data:
                logger.error("市场数据获取失败")
                return False
            
            historical_data = get_historical_data('SPY', '1m')
            if historical_data.empty:
                logger.error("历史数据获取失败")
                return False
            
            # 测试系统初始化
            system = QuantitativeStrategySystem(5000000)
            status = system.get_system_summary()
            if 'error' in status:
                logger.error(f"系统初始化失败: {status['error']}")
                return False
            
            # 测试策略执行
            result = system.run_manual_optimization()
            if not result.get('success', False):
                logger.error("策略优化失败")
                return False
            
            logger.info("系统测试通过")
            return True
            
        except Exception as e:
            logger.error(f"系统测试失败: {e}")
            return False
    
    def _check_system(self):
        """检查系统"""
        logger.info("开始系统检查...")
        
        try:
            # 检查系统要求
            if not self._check_system_requirements():
                logger.error("系统要求检查失败")
                return False
            
            # 检查系统初始化
            if not self._init_system():
                logger.error("系统初始化失败")
                return False
            
            # 获取系统状态
            status = self.system.get_system_summary()
            if 'error' in status:
                logger.error(f"系统状态异常: {status['error']}")
                return False
            
            logger.info("系统检查通过")
            return True
            
        except Exception as e:
            logger.error(f"系统检查失败: {e}")
            return False
    
    def start(self):
        """启动系统"""
        self.start_time = datetime.now()
        self.running = True
        
        logger.info(f"启动量化策略系统 - 模式: {self.mode.value}")
        
        try:
            # 检查系统要求
            if not self._check_system_requirements():
                logger.error("系统要求检查失败")
                return False
            
            # 初始化系统
            if not self._init_system():
                logger.error("系统初始化失败")
                return False
            
            # 记录系统信息
            self._log_system_info()
            
            # 启动监控线程
            if self.mode == ExecutionMode.AUTO:
                self.monitor_thread = threading.Thread(target=self._monitor_system)
                self.monitor_thread.daemon = True
                self.monitor_thread.start()
            
            # 根据模式执行不同操作
            if self.mode == ExecutionMode.CHECK:
                # 检查模式，只完成检查
                logger.info("系统检查完成")
                return True
            elif self.mode == ExecutionMode.TEST:
                # 测试模式
                return self._test_system()
            elif self.mode == ExecutionMode.MANUAL:
                # 手动模式，执行一次
                return self._execute_strategy()
            else:
                # 自动模式
                logger.info("系统进入自动执行模式")
                while self.running:
                    try:
                        # 检查执行时间
                        if self._check_execution_time():
                            # 执行策略
                            self._execute_strategy()
                        
                        # 等待1分钟
                        time.sleep(60)
                        
                    except KeyboardInterrupt:
                        logger.info("接收到中断信号，停止系统")
                        break
                    except Exception as e:
                        logger.error(f"自动执行循环出错: {e}")
                        self.error_count += 1
                        time.sleep(60)
                
                logger.info("自动执行模式结束")
                return True
            
        except Exception as e:
            logger.error(f"系统启动失败: {e}")
            return False
    
    def stop(self):
        """停止系统"""
        logger.info("停止量化策略系统...")
        
        self.running = False
        
        # 等待监控线程结束
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        
        # 执行清理
        if self.system:
            try:
                self.system.cleanup()
            except Exception as e:
                logger.error(f"系统清理失败: {e}")
        
        # 记录停止信息
        runtime = datetime.now() - self.start_time if self.start_time else None
        logger.info(f"系统已停止 - 运行时间: {runtime}")
        logger.info(f"执行次数: {self.execution_count}")
        logger.info(f"错误次数: {self.error_count}")
        
        logger.info("系统停止完成")


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="量化策略系统启动器")
    parser.add_argument('mode', nargs='?', choices=['auto', 'manual', 'test', 'check'],
                       default='auto', help='执行模式')
    return parser.parse_args()


def main():
    """主函数"""
    # 解析参数
    args = parse_args()
    mode = ExecutionMode(args.mode)
    
    # 创建启动器
    starter = SystemStarter(mode)
    
    try:
        # 启动系统
        success = starter.start()
        if success:
            logger.info("系统启动成功")
            sys.exit(0)
        else:
            logger.error("系统启动失败")
            sys.exit(1)
    except Exception as e:
        logger.error(f"系统启动失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()