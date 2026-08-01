#!/usr/bin/env python3
"""日志配置工具 — 统一日志初始化逻辑。"""

from __future__ import annotations
import logging
from datetime import datetime
from pathlib import Path

def setup_logger(level: int = logging.INFO) -> None:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_dir / f"daily_workflow_{datetime.now():%Y%m%d}.log", encoding="utf-8"), logging.StreamHandler()])
    logger = logging.getLogger("v75.workflow")
    logger.setLevel(level)
