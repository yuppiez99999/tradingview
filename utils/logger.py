"""
日志工具

功能：
- 统一的日志记录
- 多级别日志管理
- 控制台 + 常规文件 + 调试文件三层日志输出
- 相对路径输出
- 第三方库日志降噪
"""

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional, Union

LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | %(pathname)s:%(lineno)d | %(message)s"
)
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_LOG_DIR = "logs"
_DEFAULT_LITELLM_LOG_LEVEL = "WARNING"

_QUIET_LOGGERS = [
    "urllib3",
    "sqlalchemy",
    "google",
    "httpx",
    "requests",
    "matplotlib",
    "seaborn",
    "scikit-learn",
    "numba",
    "plotly",
    "dash",
    "asyncio",
    "aiohttp",
    "cryptography",
    "tqdm",
]

_LITELLM_LOGGERS = [
    "LiteLLM",
    "LiteLLM Router",
    "LiteLLM Proxy",
    "litellm",
]


class RelativePathFormatter(logging.Formatter):
    """将日志中的绝对路径转换为相对路径"""

    def __init__(
        self,
        fmt: Optional[str] = None,
        datefmt: Optional[str] = None,
        relative_to: Optional[Union[str, Path]] = None,
    ) -> None:
        super().__init__(fmt, datefmt)
        self.relative_to = Path(relative_to) if relative_to else Path.cwd()

    def format(self, record: logging.LogRecord) -> str:
        try:
            record.pathname = str(Path(record.pathname).relative_to(self.relative_to))
        except ValueError:
            pass
        return super().format(record)


class Logger:
    """兼容旧接口的日志门面，底层委托给根日志器统一管理"""

    def __init__(
        self,
        name: str,
        level: str = "INFO",
        log_file: Optional[str] = None,  # type: ignore
        console_output: bool = True,
        max_file_size: int = 10 * 1024 * 1024,
        backup_count: int = 5,
    ):
        self.name = name
        self.level = getattr(logging, level.upper(), logging.INFO)
        self.log_file = log_file
        self.console_output = console_output
        self.max_file_size = max_file_size
        self.backup_count = backup_count

        self.logger = logging.getLogger(name)
        self.logger.setLevel(self.level)
        self.logger.propagate = False

        if not self.logger.handlers:
            self._setup_handlers()

    def _setup_handlers(self) -> None:
        """兼容旧行为：控制台 + 单文件轮转"""
        formatter = RelativePathFormatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            relative_to=Path.cwd(),
        )

        if self.log_file:
            log_dir = os.path.dirname(self.log_file)
            try:
                if log_dir and not os.path.exists(log_dir):
                    os.makedirs(log_dir)

                file_handler = RotatingFileHandler(
                    self.log_file,
                    maxBytes=self.max_file_size,
                    backupCount=self.backup_count,
                    encoding="utf-8",
                )
                file_handler.setLevel(self.level)
                file_handler.setFormatter(formatter)
                self.logger.addHandler(file_handler)
            except (OSError, ValueError) as exc:
                # 日志文件目录不可写时降级为仅控制台输出，不阻断应用/测试
                self.logger.warning(
                    "file handler disabled for '%s' (%s); console-only logging",
                    self.name,
                    exc,
                )

        if self.console_output:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(self.level)
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.debug(message, *args, **kwargs)

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.info(message, *args, **kwargs)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.warning(message, *args, **kwargs)

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.error(message, *args, **kwargs)

    def critical(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.critical(message, *args, **kwargs)


def _resolve_log_level(raw_level: Optional[str], default: int = logging.INFO) -> int:
    if not raw_level:
        return default
    return getattr(logging, str(raw_level).upper(), default)


def _init_root_logging(
    log_prefix: str = "quant_strategy_system",
    log_dir: str = DEFAULT_LOG_DIR,
    console_level: int = logging.INFO,
) -> None:
    """
    统一初始化根日志器，包含三层 handler：
    1. 控制台
    2. INFO 级别常规日志文件，10MB 轮转，保留 5 个备份
    3. DEBUG 级别调试日志文件，50MB 轮转，保留 3 个备份
    """
    log_path = Path(log_dir)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    if root_logger.handlers:
        root_logger.handlers.clear()

    project_root = Path.cwd()
    formatter = RelativePathFormatter(
        LOG_FORMAT, LOG_DATE_FORMAT, relative_to=project_root
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 日志目录缺失/不可写时降级为仅控制台输出，绝不阻断应用启动与测试
    # (2026-08-24 修复: 沙箱 logs/ 无写权限曾导致 utils 导入链整体崩溃)
    try:
        log_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        root_logger.warning(
            "log dir %r not writable (%s); file handlers disabled, console-only",
            str(log_path),
            exc,
        )
        _apply_quiet_loggers()
        _apply_litellm_log_level()
        return

    today_str = datetime.now().strftime("%Y%m%d")
    info_log_file = log_path / f"{log_prefix}_{today_str}.log"
    debug_log_file = log_path / f"{log_prefix}_debug_{today_str}.log"

    for level, log_file, max_bytes, backups in (
        (logging.INFO, info_log_file, 10 * 1024 * 1024, 5),
        (logging.DEBUG, debug_log_file, 50 * 1024 * 1024, 3),
    ):
        try:
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backups,
                encoding="utf-8",
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
        except (OSError, ValueError) as exc:
            root_logger.warning(
                "file handler for %s disabled (%s); console-only", log_file, exc
            )

    _apply_quiet_loggers()
    _apply_litellm_log_level()


def _apply_quiet_loggers() -> None:
    for logger_name in _QUIET_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def _apply_litellm_log_level() -> None:
    raw_level = os.getenv("LITELLM_LOG_LEVEL", "").strip().upper()
    allowed = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    level = allowed.get(raw_level, logging.WARNING)
    for logger_name in _LITELLM_LOGGERS:
        logging.getLogger(logger_name).setLevel(level)


def get_logger(name: str, log_dir: str = DEFAULT_LOG_DIR) -> Logger:
    """
    获取日志器实例

    Args:
        name: 日志器名称
        log_dir: 日志目录

    Returns:
        Logger 实例
    """
    log_file: Optional[str] = None
    if log_dir:
        try:
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
        except OSError as exc:
            # 日志目录不可创建时降级为仅控制台输出，不阻断应用/测试
            logging.getLogger(name).warning(
                "log dir %r not writable (%s); console-only logging", log_dir, exc
            )
            log_dir = ""
        if log_dir:
            log_file = os.path.join(log_dir, f"{name}.log")
    return Logger(
        name=name,
        level="INFO",
        log_file=log_file,
        console_output=True,
        max_file_size=10 * 1024 * 1024,
        backup_count=5,
    )


def setup_loggers(log_prefix: str = "quantitative_strategy_system") -> dict:
    """
    设置并返回统一日志体系下的系统日志与模块日志映射
    """
    _init_root_logging(log_prefix=log_prefix)

    system_logger = get_logger("system", log_dir=DEFAULT_LOG_DIR)

    modules = [
        "enhanced_delta_hedge",
        "volatility_hedge",
        "tail_risk_hedge",
        "smart_hedge_trigger",
        "dynamic_capital_manager",
        "enhanced_risk_manager",
        "automated_execution_system",
        "strategy_optimizer",
    ]

    module_loggers = {
        module: get_logger(module, log_dir=DEFAULT_LOG_DIR) for module in modules
    }

    logging.info("日志系统初始化完成")
    return {"system": system_logger, "modules": module_loggers}


if __name__ == "__main__":
    _init_root_logging(console_level=logging.DEBUG)

    logger = logging.getLogger("logger_test")
    logger.debug("这是一条调试信息")
    logger.info("这是一条信息")
    logger.warning("这是一条警告信息")
    logger.error("这是一条错误信息")
    logger.critical("这是一条严重错误信息")

    logger.info("日志测试完成")
