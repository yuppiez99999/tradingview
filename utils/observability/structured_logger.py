"""Phase B 结构化日志配置.

当前基于标准 logging 实现 JSON 结构化输出,
后续 structlog 安装后可无缝切换.

用法:
    from utils.observability.structured_logger import get_structured_logger
    logger = get_structured_logger(__name__)
    logger.info("order_submitted", order_id="123", symbol="510300", qty=100)
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict


class StructuredLogger:
    """结构化日志包装器.

    将 key-value 参数序列化为 JSON, 输出到标准 logging.
    后续 structlog 安装后可直接替换.
    """

    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger(name)

    def _log(self, level: int, event: str, **kwargs: Any) -> None:
        record = {
            "timestamp": datetime.now().isoformat(),
            "event": event,
            **kwargs,
        }
        self._logger.log(level, json.dumps(record, ensure_ascii=False, default=str))

    def debug(self, event: str, **kwargs: Any) -> None:
        self._log(logging.DEBUG, event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        self._log(logging.INFO, event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._log(logging.WARNING, event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, event, **kwargs)

    def critical(self, event: str, **kwargs: Any) -> None:
        self._log(logging.CRITICAL, event, **kwargs)


def get_structured_logger(name: str) -> StructuredLogger:
    """获取结构化日志实例."""
    return StructuredLogger(name)