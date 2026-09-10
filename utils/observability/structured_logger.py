"""Phase B 结构化日志配置.

基于 structlog 实现 JSON 结构化输出, 带 structlog 回退到标准 logging.

用法:
    from utils.observability.structured_logger import get_structured_logger
    logger = get_structured_logger(__name__)
    logger.info("order_submitted", order_id="123", symbol="510300", qty=100)
"""

import json
import logging
from typing import Any

from utils.datetime_utils import now_bj

try:
    import structlog

    _HAS_STRUCTLOG = True
except ImportError:
    _HAS_STRUCTLOG = False

_STRUCTLOG_CONFIGURED = False


def _configure_structlog() -> None:
    """配置 structlog 全局处理器链."""
    global _STRUCTLOG_CONFIGURED
    if _STRUCTLOG_CONFIGURED or not _HAS_STRUCTLOG:
        return
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _STRUCTLOG_CONFIGURED = True


class StructuredLogger:
    """结构化日志包装器.

    structlog 可用时使用 structlog 后端, 否则回退到标准 logging JSON 输出.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        if _HAS_STRUCTLOG:
            _configure_structlog()
            self._structlog = structlog.get_logger(name)
        else:
            self._structlog = None
        self._logger = logging.getLogger(name)

    def _log_fallback(self, level: int, event: str, **kwargs: Any) -> None:
        record = {
            "timestamp": now_bj().isoformat(),
            "event": event,
            **kwargs,
        }
        self._logger.log(level, json.dumps(record, ensure_ascii=False, default=str))

    def debug(self, event: str, **kwargs: Any) -> None:
        if self._structlog:
            self._structlog.debug(event, **kwargs)
        else:
            self._log_fallback(logging.DEBUG, event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        if self._structlog:
            self._structlog.info(event, **kwargs)
        else:
            self._log_fallback(logging.INFO, event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        if self._structlog:
            self._structlog.warning(event, **kwargs)
        else:
            self._log_fallback(logging.WARNING, event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        if self._structlog:
            self._structlog.error(event, **kwargs)
        else:
            self._log_fallback(logging.ERROR, event, **kwargs)

    def critical(self, event: str, **kwargs: Any) -> None:
        if self._structlog:
            self._structlog.critical(event, **kwargs)
        else:
            self._log_fallback(logging.CRITICAL, event, **kwargs)

    def bind(self, **kwargs: Any) -> "StructuredLogger":
        """绑定上下文变量 (structlog contextvars)."""
        if self._structlog:
            bound = self._structlog.bind(**kwargs)
            wrapper = StructuredLogger(self._name)
            wrapper._structlog = bound
            return wrapper
        return self


def get_structured_logger(name: str) -> StructuredLogger:
    """获取结构化日志实例."""
    return StructuredLogger(name)
