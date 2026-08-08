"""LLM 调用审计日志 (JSONL 按日切分).

从原 `utils/alpha/llm_router.py:LLMRouter._write_audit_log` 拆出 (B3.4.3)。

设计:
    - 每次调用 (成功/失败) 均记录一行 JSON
    - 按日期切分: calls_YYYY-MM-DD.jsonl
    - 写入失败不影响主流程 (silent fail)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from utils.alpha.llm.base import CallRecord

logger = logging.getLogger("llm_router")


def write_audit_log(
    record: CallRecord,
    audit_log_dir: Path,
    enabled: bool = True,
) -> None:
    """写入审计日志 (JSONL 格式, 按日切分).

    Args:
        record: 调用记录
        audit_log_dir: 审计日志目录
        enabled: 是否启用审计日志 (False 时不写入)
    """
    if not enabled:
        return

    try:
        audit_log_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_file = audit_log_dir / f"calls_{date_str}.jsonl"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
        logger.warning("审计日志写入失败: %s", e)


__all__ = ["write_audit_log"]
