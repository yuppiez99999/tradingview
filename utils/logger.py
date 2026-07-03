# -*- coding: utf-8 -*-
"""
日志工具

功能：
- 统一的日志记录
- 多级别日志管理
- 文件和控制台输出
- 日志轮转和管理
"""

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler


class Logger:
    """日志管理器"""
    
    def __init__(self, name: str, level: str = 'INFO', 
                 log_file: str = None, console_output: bool = True,
                 max_file_size: int = 10*1024*1024, backup_count: int = 5):
        """
        初始化日志管理器
        
        Args:
            name: 日志器名称
            level: 日志级别
            log_file: 日志文件路径
            console_output: 是否输出到控制台
            max_file_size: 最大文件大小(字节)
            backup_count: 备份文件数量
        """
        self.name = name
        self.level = getattr(logging, level.upper())
        self.log_file = log_file
        self.console_output = console_output
        self.max_file_size = max_file_size
        self.backup_count = backup_count
        
        # 创建日志器
        self.logger = logging.getLogger(name)
        self.logger.setLevel(self.level)
        
        # 避免重复添加处理器
        if not self.logger.handlers:
            self._setup_handlers()
    
    def _setup_handlers(self):
        """设置日志处理器"""
        # 格式化器
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # 文件处理器
        if self.log_file:
            # 确保日志目录存在
            log_dir = os.path.dirname(self.log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir)
            
            # 使用轮转文件处理器
            file_handler = RotatingFileHandler(
                self.log_file,
                maxBytes=self.max_file_size,
                backupCount=self.backup_count,
                encoding='utf-8'
            )
            file_handler.setLevel(self.level)
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
        
        # 控制台处理器
        if self.console_output:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(self.level)
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)
    
    def debug(self, message: str, *args, **kwargs):
        """调试日志"""
        self.logger.debug(message, *args, **kwargs)
    
    def info(self, message: str, *args, **kwargs):
        """信息日志"""
        self.logger.info(message, *args, **kwargs)
    
    def warning(self, message: str, *args, **kwargs):
        """警告日志"""
        self.logger.warning(message, *args, **kwargs)
    
    def error(self, message: str, *args, **kwargs):
        """错误日志"""
        self.logger.error(message, *args, **kwargs)
    
    def critical(self, message: str, *args, **kwargs):
        """严重错误日志"""
        self.logger.critical(message, *args, **kwargs)


def get_logger(name: str, log_dir: str = 'logs') -> Logger:
    """
    获取日志器实例
    
    Args:
        name: 日志器名称
        log_dir: 日志目录
        
    Returns:
        Logger实例
    """
    # 创建日志目录
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # 生成日志文件路径
    log_file = os.path.join(log_dir, f'{name}.log')
    
    # 创建日志器
    logger = Logger(
        name=name,
        level='INFO',
        log_file=log_file,
        console_output=True,
        max_file_size=10*1024*1024,  # 10MB
        backup_count=5
    )
    
    return logger


def setup_loggers():
    """设置所有日志器"""
    # 确保日志目录存在
    log_dir = 'logs'
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # 设置系统日志
    system_logger = get_logger('quantitative_strategy_system')
    
    # 设置模块日志
    modules = [
        'enhanced_delta_hedge',
        'volatility_hedge', 
        'tail_risk_hedge',
        'smart_hedge_trigger',
        'dynamic_capital_manager',
        'enhanced_risk_manager',
        'automated_execution_system',
        'strategy_optimizer'
    ]
    
    module_loggers = {}
    for module in modules:
        module_loggers[module] = get_logger(module)
    
    return {
        'system': system_logger,
        'modules': module_loggers
    }


if __name__ == "__main__":
    # 测试日志功能
    logger = get_logger('test')
    
    logger.debug("这是一条调试信息")
    logger.info("这是一条信息")
    logger.warning("这是一条警告信息")
    logger.error("这是一条错误信息")
    logger.critical("这是一条严重错误信息")
    
    print("日志测试完成")