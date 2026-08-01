"""conftest.py — 单元测试层专用 fixture (tests/unit/)

pytest 自动加载规则: 仅识别名为 conftest.py 的文件
本文件为 tests/unit/ 目录的 conftest.py

注意: 以下 fixture 已提升到顶层 tests/conftest.py 供跨层共享:
    - tmp_kill_switch_log
    - clean_env
    - production_env

本文件只保留单元测试专用的 fixture:
    - fake_logger: 捕获日志输出用于断言
"""
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_logger():
    """捕获日志输出的 mock logger"""
    log = MagicMock(name="fake_logger")
    log.messages = []

    def _capture(level, msg, *args, **kwargs):
        full = msg % args if args else msg
        log.messages.append({"level": level, "msg": full})
        getattr(log, level)(full)

    return log
