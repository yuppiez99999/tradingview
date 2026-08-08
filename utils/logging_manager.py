# -*- coding: utf-8 -*-
"""
统一日志管理器 — 借鉴 TradingAgents-CN 架构模式
支持：彩色控制台输出、JSON结构化文件日志、RotatingFileHandler、多级别配置、性能追踪
"""

import logging
import logging.handlers
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional


class ColoredFormatter(logging.Formatter):
    """彩色日志格式化器 — 借鉴 TradingAgents-CN ColoredFormatter"""

    COLORS = {
        'DEBUG': '\033[36m',     # 青色
        'INFO': '\033[32m',      # 绿色
        'WARNING': '\033[33m',   # 黄色
        'ERROR': '\033[31m',     # 红色
        'CRITICAL': '\033[35m',  # 紫色
        'RESET': '\033[0m',
    }

    def format(self, record):
        if record.levelname in self.COLORS:
            record.levelname = f"{self.COLORS[record.levelname]}{record.levelname}{self.COLORS['RESET']}"
        return super().format(record)


class StructuredFormatter(logging.Formatter):
    """JSON结构化日志格式化器 — 借鉴 TradingAgents-CN StructuredFormatter"""

    def format(self, record):
        import json
        log_entry = {
            'timestamp': datetime.fromtimestamp(record.created).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
        }
        # 附加性能追踪字段
        for attr in ('duration_ms', 'operation', 'target', 'session_id',
                     'event_type', 'stock_code', 'cost', 'tokens'):
            if hasattr(record, attr):
                log_entry[attr] = getattr(record, attr)
        return json.dumps(log_entry, ensure_ascii=False)


class QuantSystemLogger:
    """量化系统统一日志管理器 — 借鉴 TradingAgents-CN TradingAgentsLogger"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or self._load_default_config()
        self._loggers: Dict[str, logging.Logger] = {}
        self._setup_logging()

    def _load_default_config(self) -> Dict[str, Any]:
        log_level = os.getenv('QUANT_LOG_LEVEL', 'INFO').upper()
        log_dir = os.getenv('QUANT_LOG_DIR', './logs')

        return {
            'level': log_level,
            'format': {
                'console': '%(asctime)s | %(name)-18s | %(levelname)-8s | %(message)s',
                'file': '%(asctime)s | %(name)-18s | %(levelname)-8s | %(module)s:%(funcName)s:%(lineno)d | %(message)s',
            },
            'handlers': {
                'console': {'enabled': True, 'colored': True, 'level': log_level},
                'file': {'enabled': True, 'level': 'DEBUG', 'max_size': '10MB', 'backup_count': 5, 'directory': log_dir},
                'structured': {'enabled': False, 'level': 'INFO', 'directory': log_dir},
            },
            'loggers': {
                'quant': {'level': log_level},
                'trading': {'level': log_level},
                'data_source': {'level': log_level},
                'urllib3': {'level': 'WARNING'},
                'requests': {'level': 'WARNING'},
                'matplotlib': {'level': 'WARNING'},
            },
        }

    def _setup_logging(self):
        log_dir = Path(self.config['handlers']['file']['directory'])
        log_dir.mkdir(parents=True, exist_ok=True)

        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, self.config['level']))
        root_logger.handlers.clear()

        self._add_console_handler(root_logger)
        self._add_file_handler(root_logger)

        if self.config['handlers']['structured']['enabled']:
            self._add_structured_handler(root_logger)

        self._configure_specific_loggers()

    def _add_console_handler(self, logger: logging.Logger):
        if not self.config['handlers']['console']['enabled']:
            return
        stream = sys.stderr
        handler = logging.StreamHandler(stream)
        handler.setLevel(getattr(logging, self.config['handlers']['console']['level']))

        if self.config['handlers']['console']['colored'] and stream.isatty():
            formatter = ColoredFormatter(self.config['format']['console'])
        else:
            formatter = logging.Formatter(self.config['format']['console'])

        handler.setFormatter(formatter)
        logger.addHandler(handler)

    def _add_file_handler(self, logger: logging.Logger):
        if not self.config['handlers']['file']['enabled']:
            return
        log_dir = Path(self.config['handlers']['file']['directory'])
        log_file = log_dir / 'quant_system.log'

        max_size = self._parse_size(self.config['handlers']['file']['max_size'])
        backup_count = self.config['handlers']['file']['backup_count']

        handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=max_size, backupCount=backup_count, encoding='utf-8')
        handler.setLevel(getattr(logging, self.config['handlers']['file']['level']))
        handler.setFormatter(logging.Formatter(self.config['format']['file']))
        logger.addHandler(handler)

    def _add_structured_handler(self, logger: logging.Logger):
        log_dir = Path(self.config['handlers']['structured']['directory'])
        log_file = log_dir / 'quant_structured.log'
        handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=3, encoding='utf-8')
        handler.setLevel(getattr(logging, self.config['handlers']['structured']['level']))
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)

    def _configure_specific_loggers(self):
        for logger_name, logger_config in self.config['loggers'].items():
            lg = logging.getLogger(logger_name)
            lg.setLevel(getattr(logging, logger_config['level']))

    def _parse_size(self, size_str: str) -> int:
        size_str = size_str.upper()
        if size_str.endswith('KB'):
            return int(size_str[:-2]) * 1024
        elif size_str.endswith('MB'):
            return int(size_str[:-2]) * 1024 * 1024
        elif size_str.endswith('GB'):
            return int(size_str[:-2]) * 1024 * 1024 * 1024
        return int(size_str)

    def get_logger(self, name: str) -> logging.Logger:
        if name not in self._loggers:
            self._loggers[name] = logging.getLogger(name)
        return self._loggers[name]


# 全局单例
_logger_manager: Optional[QuantSystemLogger] = None


def get_logger_manager() -> QuantSystemLogger:
    global _logger_manager
    if _logger_manager is None:
        _logger_manager = QuantSystemLogger()
    return _logger_manager


def get_logger(name: str) -> logging.Logger:
    return get_logger_manager().get_logger(name)


def setup_logging(config: Optional[Dict[str, Any]] = None):
    global _logger_manager
    _logger_manager = QuantSystemLogger(config)
    return _logger_manager