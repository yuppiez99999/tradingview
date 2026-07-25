# -*- coding: utf-8 -*-
"""
⚠ v8.3_institutional/tests/ 目录已与根 tests/ 合并
统一测试入口: pytest tests/
共享 fixture: tests/conftest.py

本文件保留以确保向后兼容: pytest v8.3_institutional/tests/ 仍可运行
"""
import sys
import os

# 转发到项目统一 conftest
_UNIFIED_CONFTEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "tests", "conftest.py"
)

if os.path.exists(_UNIFIED_CONFTEST):
    _dir = os.path.dirname(_UNIFIED_CONFTEST)
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

    # 导入并暴露所有 fixture
    import importlib.util
    _spec = importlib.util.spec_from_file_location("conftest", _UNIFIED_CONFTEST)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    # 桥接 fixtures 到当前模块命名空间
    for _name in dir(_mod):
        if _name.startswith(("sample_", "mock_", "project_")):
            globals()[_name] = getattr(_mod, _name)
